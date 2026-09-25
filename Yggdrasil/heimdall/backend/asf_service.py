"""ASF card farming — drives ArchiSteamFarm (ASF) so Andvari's games drop their cards.

ASF runs as its own container (see ``docker-compose.yml`` and
``Yggdrasil/asf/README.md``) and "plays" games over the Steam client protocol so
their trading cards drop. This service is Heimdall's thin side of it, over ASF's
HTTP API:

* **Provisioning** — one ASF bot per Heimdall account (bot name = Steam login),
  written through the API with a hardened config: offline status, no public
  listing or ASF Steam group, trading off, no gifts, no hour boosting, nobody can
  command the bot from Steam chat. The config holds **no password**.
* **Login assist** — ASF runs headless, so when a bot needs its password or a
  Steam Guard code it stops and reports which. Heimdall then supplies it (the
  password from the Mímir vault, a fresh code from the maFile) and restarts that
  one bot. ASF keeps the password in memory only and saves a login token, so
  this happens on the first login and after a token expires. The 2FA seeds
  (shared/identity secret) never leave Heimdall — so ASF cannot confirm a trade
  or a market listing, and a stolen ASF token cannot move items.
  Paced: one assisted login per tick, at most three tries per bot before it
  waits for a manual "retry", so a bad password can never hammer Steam.
* **Ratatoskr coordination** — Steam allows one "playing" session per account,
  and Ratatoskr must play Counter-Strike 2 to reach its Game Coordinator. Before
  a Ratatoskr login, the account's bot is paused; once Ratatoskr's session is
  gone the bot resumes. The paused set is persisted, because the backend reloads
  on every save and must never strand a bot paused.
* **Status** — per account: farming what, cards left, time left, or what it
  needs. No secrets ever appear in it.

Off (every method a no-op) until ``ASF_IPC_PASSWORD`` is set — ``make asf-setup``.
"""
import logging
import os
import re
import threading
import time

import requests

from jsonio import atomic_write_json, read_json

logger = logging.getLogger(__name__)

ASF_URL = os.environ.get('ASF_URL', 'http://asf:1242')
STATE_PATH = os.path.join('cache', 'asf_state.json')

# ASF.EUserInputType (ArchiSteamFarm/Core/ASF.cs)
INPUT_NONE, INPUT_LOGIN, INPUT_PASSWORD, INPUT_STEAM_GUARD = 0, 1, 2, 3
INPUT_PARENTAL_CODE, INPUT_TWO_FACTOR, INPUT_CRYPTKEY, INPUT_DEVICE_CONFIRMATION = 4, 5, 6, 7
INPUT_NAMES = {INPUT_LOGIN: 'login', INPUT_PASSWORD: 'password', INPUT_STEAM_GUARD: 'email code',
               INPUT_PARENTAL_CODE: 'family view code', INPUT_TWO_FACTOR: 'Steam Guard code',
               INPUT_CRYPTKEY: 'crypt key', INPUT_DEVICE_CONFIRMATION: 'device confirmation'}
ASSISTABLE_INPUTS = (INPUT_PASSWORD, INPUT_TWO_FACTOR)

TICK_SECONDS = 45                # > ASF's 30-second LoginLimiterDelay: one assisted login per tick
FIRST_TICK_DELAY_SECONDS = 20
MAX_LOGIN_ATTEMPTS = 3           # then wait for a manual retry
LOGIN_RETRY_SECONDS = 15 * 60
CODE_MIN_SECONDS_LEFT = 12       # never hand over a Steam Guard code about to expire
PAUSE_SETTLE_SECONDS = 3         # let Steam register "stopped playing" before Ratatoskr plays
REQUEST_TIMEOUT_SECONDS = 15

# The bot config Heimdall writes. Everything not listed stays at ASF's default
# (card farming on, HoursUntilCardDrops 3, login tokens kept).
HARDENED_BOT_CONFIG = {
    'Enabled': True,
    'OnlineStatus': 0,               # Offline: cards still drop; friends do not see 21 accounts "in game"
    'RemoteCommunication': 0,        # no ASF Steam group, no public bot listing (it would link the accounts)
    'TradingPreferences': 0,         # no trade matching or donations
    'AcceptGifts': False,
    'BotBehaviour': 0,
    'GamesPlayedWhileIdle': [],      # never boost play hours when farming is done
    'FarmingPreferences': 0,
    'SendTradePeriod': 0,            # never send items anywhere
    'SteamUserPermissions': {},      # nobody can command the bot from Steam chat
    'SteamMasterClanID': 0,
}


class AsfError(Exception):
    pass


def parse_time_span(text):
    """.NET TimeSpan JSON ('1.02:03:04', '02:03:04.5') -> seconds."""
    match = re.match(r'^(?:(\d+)\.)?(\d+):(\d+):(\d+)', str(text or ''))
    if not match:
        return 0
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def bot_view(bot):
    """ASF bot JSON -> the farming fields Heimdall shows (no config, no secrets)."""
    farmer = bot.get('CardsFarmer') or {}

    def games(key):
        return [{'app_id': game.get('AppID'), 'name': game.get('GameName'),
                 'cards_remaining': game.get('CardsRemaining') or 0,
                 'hours_played': round(float(game.get('HoursPlayed') or 0), 1)}
                for game in farmer.get(key) or []]

    to_farm = games('GamesToFarm')
    return {
        'connected': bool(bot.get('IsConnectedAndLoggedOn')),
        'running': bool(bot.get('KeepRunning')),
        'required_input': bot.get('RequiredInput') or INPUT_NONE,
        'playing_possible': bool(bot.get('IsPlayingPossible', True)),
        'now_farming': bool(farmer.get('NowFarming')),
        'paused': bool(farmer.get('Paused')),
        'current_games': games('CurrentGamesFarming'),
        'games_to_farm': len(to_farm),
        'cards_remaining': sum(game['cards_remaining'] for game in to_farm),
        'time_remaining_seconds': parse_time_span(farmer.get('TimeRemaining')),
    }


def farming_state(view, paused_for_ratatoskr, attempts):
    """One word for the account's farming state (the UI colours by it)."""
    if view is None:
        return 'not_added'
    if view['connected']:
        if paused_for_ratatoskr:
            return 'paused_for_ratatoskr'
        if view['paused']:
            return 'paused'
        if view['now_farming']:
            return 'farming'
        return 'done' if not view['games_to_farm'] else 'waiting'
    required = view['required_input']
    if required in ASSISTABLE_INPUTS:
        return 'needs_attention' if (attempts or {}).get('count', 0) >= MAX_LOGIN_ATTEMPTS else 'logging_in'
    if required != INPUT_NONE:
        return 'needs_attention'
    return 'connecting' if view['running'] else 'stopped'


class AsfService:
    def __init__(self, steam_service, ratatoskr_service, base_url=ASF_URL,
                 ipc_password=None, state_path=STATE_PATH, sleep=time.sleep):
        self.steam = steam_service
        self.ratatoskr = ratatoskr_service
        self.base_url = base_url.rstrip('/')
        self.ipc_password = os.environ.get('ASF_IPC_PASSWORD', '') if ipc_password is None else ipc_password
        self.state_path = state_path
        self._sleep = sleep
        self._lock = threading.RLock()
        self._http = requests.Session()
        saved = read_json(state_path, default={}) or {}
        # {bot_name: {steamid, since}} — bots Heimdall paused for a Ratatoskr session
        self._paused_for_ratatoskr = dict(saved.get('paused_for_ratatoskr') or {})
        # {bot_name: {count, last_at, last_error}} — login assists since the last success
        self._attempts = dict(saved.get('attempts') or {})
        self._last_bots = None
        self._last_error = None
        self._last_tick_at = None

    @property
    def enabled(self):
        return bool(self.ipc_password)

    # ---- ASF API ---------------------------------------------------------------

    def _call(self, method, path, body=None):
        try:
            response = self._http.request(method, f'{self.base_url}{path}', json=body,
                                          headers={'Authentication': self.ipc_password},
                                          timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            raise AsfError(f'ASF unreachable ({type(e).__name__})') from None
        if response.status_code == 401:
            raise AsfError('ASF refused the API password (check ASF_IPC_PASSWORD in .env)')
        try:
            payload = response.json()
        except ValueError:
            raise AsfError(f'ASF answered HTTP {response.status_code}') from None
        if not response.ok or not payload.get('Success', False):
            raise AsfError(payload.get('Message') or f'ASF answered HTTP {response.status_code}')
        return payload.get('Result')

    def _bots(self):
        return self._call('GET', '/Api/Bot/ASF') or {}

    def _command(self, bot_name, action, body=None):
        return self._call('POST', f'/Api/Bot/{bot_name}/{action}', body if body is not None else {})

    # ---- accounts --------------------------------------------------------------

    def _accounts(self):
        """{bot_name: {steamid, account_name}} for every Heimdall account."""
        accounts = {}
        for steamid in self.steam.storage.list_accounts():
            data = self.steam.storage.load_account(steamid) or {}
            login = (data.get('account_name') or '').strip()
            if login and not login.startswith('.') and login.upper() != 'ASF' \
                    and not re.search(r'[\s,]', login):
                accounts[login] = {'steamid': str(steamid), 'account_name': login}
        return accounts

    def _bot_name_for_login(self, account_name):
        login = (account_name or '').strip().lower()
        for name in self._accounts():
            if name.lower() == login:
                return name
        return None

    def _persist(self):
        with self._lock:
            state = {'paused_for_ratatoskr': self._paused_for_ratatoskr, 'attempts': self._attempts}
        try:
            atomic_write_json(self.state_path, state)
        except Exception as e:
            logger.error('[ASF] could not save %s: %s', self.state_path, e)

    # ---- provisioning ------------------------------------------------------------

    def provision(self, bots=None):
        """Create a hardened bot for every account ASF does not have yet, and
        rewrite any bot whose safety settings drifted. Returns the bot names written."""
        bots = self._bots() if bots is None else bots
        written = []
        for name, account in self._accounts().items():
            existing = (bots.get(name) or {}).get('BotConfig')
            desired = {**HARDENED_BOT_CONFIG, 'SteamLogin': account['account_name']}
            if existing is not None and all(existing.get(key) == value
                                            for key, value in desired.items() if key != 'SteamLogin'):
                continue
            self._call('POST', f'/Api/Bot/{name}', {'BotConfig': desired})
            written.append(name)
            logger.info('[ASF] %s bot config for %s', 'rewrote' if existing is not None else 'created', name)
        return written

    # ---- login assist ------------------------------------------------------------

    def _fresh_code(self, shared_secret):
        """A Steam Guard code with enough validity left for ASF to use it."""
        seconds_left = 30 - (self.steam._get_steam_time() % 30)
        if seconds_left < CODE_MIN_SECONDS_LEFT:
            self._sleep(seconds_left + 1)
        code = self.steam.generate_code(shared_secret)
        if not code or code in ('N/A', 'ERR'):
            raise AsfError('no usable shared secret in the maFile')
        return code

    def _assist_login(self, name, account, required_input):
        """Hand ASF what it asked for, then start the bot. Never logs a secret."""
        data = self.steam.storage.load_account(account['steamid']) or {}
        if required_input == INPUT_PASSWORD:
            password = self.steam.get_password(account['steamid'])
            if not password:
                raise AsfError('no password in Mímir for this login')
            self._command(name, 'Input', {'Type': INPUT_PASSWORD, 'Value': password})
        # A password prompt means there is no valid login token, so Steam will ask
        # for a code next: hand it over now and save a round trip.
        self._command(name, 'Input', {'Type': INPUT_TWO_FACTOR,
                                      'Value': self._fresh_code(data.get('shared_secret'))})
        self._command(name, 'Start')

    def _assist_one(self, bots, accounts, now):
        """At most one assisted login per tick, and only while ASF's login queue is
        empty: ASF paces logins 30 seconds apart, so a Steam Guard code handed to a
        bot stuck behind other logins would go stale and burn a failed login."""
        if any(bot.get('KeepRunning') and not bot.get('IsConnectedAndLoggedOn') for bot in bots.values()):
            return None
        for name, account in accounts.items():
            bot = bots.get(name)
            if not bot:
                continue
            with self._lock:
                attempts = dict(self._attempts.get(name) or {})
            if bot.get('IsConnectedAndLoggedOn'):
                if attempts:
                    with self._lock:
                        self._attempts.pop(name, None)
                    self._persist()
                continue
            required = bot.get('RequiredInput') or INPUT_NONE
            if required not in ASSISTABLE_INPUTS or bot.get('KeepRunning'):
                continue
            if attempts.get('count', 0) >= MAX_LOGIN_ATTEMPTS:
                continue                 # waits for a manual retry
            if attempts.get('count') and now - (attempts.get('last_at') or 0) < LOGIN_RETRY_SECONDS:
                continue
            error = None
            try:
                self._assist_login(name, account, required)
                logger.info('[ASF] supplied the %s for %s and started it', INPUT_NAMES[required], name)
            except AsfError as e:
                error = str(e)
                logger.warning('[ASF] login assist for %s failed: %s', name, error)
            with self._lock:
                self._attempts[name] = {'count': attempts.get('count', 0) + 1, 'last_at': now,
                                        'last_error': error}
            self._persist()
            return name
        return None

    def retry_login(self, steamid):
        """Clear the attempt counter so the next tick may assist this bot again."""
        name = self._name_for_steamid(steamid)
        with self._lock:
            self._attempts.pop(name, None)
        self._persist()
        return {'success': True}

    # ---- Ratatoskr coordination ----------------------------------------------------

    def pause_for_ratatoskr(self, account_name):
        """Called before every Ratatoskr login: free the account's "playing" slot.
        Best effort — never blocks or fails the Ratatoskr login."""
        if not self.enabled:
            return
        name = self._bot_name_for_login(account_name)
        if not name:
            return
        try:
            bot = (self._bots() or {}).get(name)
            if not bot or not bot.get('IsConnectedAndLoggedOn'):
                return
            farmer = bot.get('CardsFarmer') or {}
            with self._lock:
                already_ours = name in self._paused_for_ratatoskr
            if farmer.get('Paused') and not already_ours:
                return                   # paused by hand: leave it alone, and never resume it
            self._command(name, 'Pause', {'Permanent': True, 'ResumeInSeconds': 0})
            with self._lock:
                self._paused_for_ratatoskr[name] = {'steamid': self._accounts()[name]['steamid'],
                                                    'since': time.time()}
            self._persist()
            logger.info('[ASF] paused %s for a Ratatoskr session', name)
            if farmer.get('NowFarming'):
                self._sleep(PAUSE_SETTLE_SECONDS)
        except Exception as e:
            logger.warning('[ASF] could not pause %s for Ratatoskr: %s', name, e)

    def _resume_after_ratatoskr(self, bots):
        with self._lock:
            paused = dict(self._paused_for_ratatoskr)
        for name, info in paused.items():
            status = (self.ratatoskr.get_status(info['steamid']) or {}).get('status')
            if status in ('connected', 'gc_lost'):
                continue                 # Ratatoskr still holds the session
            if name in bots:
                self._command(name, 'Resume')
                logger.info('[ASF] resumed %s (Ratatoskr session over)', name)
            with self._lock:
                self._paused_for_ratatoskr.pop(name, None)
            self._persist()

    # ---- manual controls -----------------------------------------------------------

    def _name_for_steamid(self, steamid):
        for name, account in self._accounts().items():
            if account['steamid'] == str(steamid):
                return name
        raise AsfError('unknown account')

    def pause(self, steamid):
        name = self._name_for_steamid(steamid)
        self._command(name, 'Pause', {'Permanent': True, 'ResumeInSeconds': 0})
        with self._lock:
            self._paused_for_ratatoskr.pop(name, None)    # a manual pause is the user's, not ours
        self._persist()
        return {'success': True}

    def resume(self, steamid):
        name = self._name_for_steamid(steamid)
        self._command(name, 'Resume')
        with self._lock:
            self._paused_for_ratatoskr.pop(name, None)
        self._persist()
        return {'success': True}

    # ---- loop + status ---------------------------------------------------------------

    def tick(self):
        if not self.enabled:
            return
        now = time.time()
        try:
            bots = self._bots()
            if self.provision(bots):
                bots = self._bots()
            self._resume_after_ratatoskr(bots)
            self._assist_one(bots, self._accounts(), now)
            self._last_bots, self._last_error = self._bots(), None
        except AsfError as e:
            self._last_error = str(e)
            logger.warning('[ASF] %s', e)
        self._last_tick_at = now

    def start_background(self):
        if not self.enabled:
            logger.info('[ASF] off: ASF_IPC_PASSWORD not set (run `make asf-setup`)')
            return

        def loop():
            self._sleep(FIRST_TICK_DELAY_SECONDS)
            while True:
                try:
                    self.tick()
                except Exception as e:
                    logger.error('[ASF] loop error: %s', e)
                self._sleep(TICK_SECONDS)
        threading.Thread(target=loop, daemon=True).start()

    def status(self):
        """Per-account farming view for the UI. Never contains a secret."""
        if not self.enabled:
            return {'enabled': False, 'reachable': False, 'accounts': [],
                    'message': 'ASF is not set up. Run `make asf-setup`, then `make asf`.'}
        try:                             # fresh: one local call, and ASF adds new bots asynchronously
            bots, error = self._bots(), None
        except AsfError as e:
            bots, error = self._last_bots or {}, str(e)
        with self._lock:
            paused, attempts = dict(self._paused_for_ratatoskr), dict(self._attempts)
        rows = []
        for name, account in sorted(self._accounts().items(), key=lambda item: item[0].lower()):
            view = bot_view(bots[name]) if name in bots else None
            tries = attempts.get(name) or {}
            row = {'steamid': account['steamid'], 'account_name': name,
                   'state': farming_state(view, name in paused, tries),
                   'login_attempts': tries.get('count', 0), 'last_error': tries.get('last_error')}
            if view:
                row.update(view)
                row['required_input'] = INPUT_NAMES.get(view['required_input'])
            rows.append(row)
        return {
            'enabled': True, 'reachable': error is None, 'error': error,
            'checked_at': self._last_tick_at, 'accounts': rows,
            'totals': {
                'farming': sum(1 for row in rows if row['state'] == 'farming'),
                'needs_attention': sum(1 for row in rows if row['state'] == 'needs_attention'),
                'cards_remaining': sum(row.get('cards_remaining') or 0 for row in rows),
                'games_to_farm': sum(row.get('games_to_farm') or 0 for row in rows),
            },
        }
