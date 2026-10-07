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
* **Only accounts with work run** — ASF's FAQ recommends at most 10 bots (Ivan
  chose 20, see ``MAX_RUNNING_BOTS``), and an idle logged-in bot is pure risk. So a bot
  is switched on only while its account has cards to farm (ASF's own queue,
  Andvari's badges scan, or a "farm now" request after buying a game), at most
  ``MAX_RUNNING_BOTS`` at once, and switched off once ASF has looked and found
  nothing. Its login token is kept, so switching back on needs no password.
* **Team Fortress 2 mode** — ``start_team_fortress`` makes the chosen bots
  play Team Fortress 2 (app 440) so its item drops (a freshly added case is
  worth the most in its first hours) land on every account. Those bots run
  outside the ``MAX_RUNNING_BOTS`` ceiling, get the free Team Fortress 2
  license once, and play it in ASF's manual mode (``play <bot> 440``), which
  pauses their card farming; ``stop_team_fortress`` resumes it. Drops are
  weekly-capped, so the mode is meant to start the minute a case is added
  (``team_fortress_service.py`` watches the news and starts it).
* **Team Fortress 2 license sweep** — every account should own Team Fortress
  2 (free), so it can join the mode above at once. Each tick, an account not
  yet confirmed gets ``addlicense <bot> app/440``: right away when its bot is
  logged in, otherwise its bot is started for it (one at a time, only while
  ASF's login queue is empty) and stopped again afterwards. New accounts are
  picked up the same way. Confirmed accounts are remembered in the state file.
* **Global config watch** — every tick reads ASF's global config (read only)
  and checks the safety settings ``make asf-setup`` wrote still hold: no
  ``SteamOwnerID`` (an owner could order loot or transfers from Steam chat, and
  Heimdall auto-confirms trades), Counter-Strike 2 blacklisted, no self-update.
  If one drifted, every bot is switched off and the status says why until the
  config is safe again. Heimdall never rewrites ``ASF.json`` itself.
* **Status** — per account: farming what, cards left, time left, or what it
  needs. No secrets ever appear in it.

Off (every method a no-op) until ``ASF_IPC_PASSWORD`` is set — ``make asf-setup``.
"""
import copy
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
RATATOSKR_LOGIN_GRACE_SECONDS = 120  # Ratatoskr reports "disconnected" while it is still logging in
STEAM_LOGIN = re.compile(r'^[A-Za-z0-9_]{1,64}$')   # Steam logins: letters, digits, underscore
REQUEST_TIMEOUT_SECONDS = 15
# Ivan's choice (2026-10-02): 20. ASF's FAQ: "ASF team suggests owning up to 10 Steam
# accounts in total, and therefore also running up to 10 bots in total. Anything above
# is not supported and done at your own risk"; technically "up to 100-200 bots with a
# single IP". ASF only warns above 10.
MAX_RUNNING_BOTS = 20
EMPTY_CHECK_SECONDS = 5 * 60     # connected this long with nothing queued = ASF checked the badges
FARM_NOW_SECONDS = 60 * 60       # a "farm now" request keeps a bot on at most this long
RECONNECT_GRACE_SECONDS = 6 * 60 * 60   # a bot seen with cards to farm stays on this long while it reconnects
ACCOUNTS_CACHE_SECONDS = 5 * 60  # status() reuses the account list (reading it decrypts every maFile)
COUNTER_STRIKE_2 = 730           # blacklisted in ASF.json by scripts/asf_setup.py
TEAM_FORTRESS_2 = 440            # free to play: ASF adds the license, then plays it for item drops
LICENSE_LOGIN_TIMEOUT_SECONDS = 4 * 60   # a bot started only to add the license gets this long to log in
LICENSE_RETRY_SECONDS = 60 * 60          # a failed license attempt is retried after this, doubling
LICENSE_RETRY_MAX_SECONDS = 24 * 3600    # ... up to once a day
PLAY_AGAIN_SECONDS = 30 * 60             # a bot in Team Fortress 2 mode is told to play again this often

# The bot config Heimdall writes. Everything not listed stays at ASF's default
# (card farming on, HoursUntilCardDrops 3, login tokens kept).
HARDENED_BOT_CONFIG = {
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


class UnknownAccount(AsfError):
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
        'enabled': (bot.get('BotConfig') or {}).get('Enabled', True) is not False,
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


def farming_state(view, paused_for_ratatoskr, attempts, queued=False):
    """One word for the account's farming state (the UI colours by it)."""
    if view is None:
        return 'queued' if queued else 'not_added'
    if not view['enabled']:
        return 'queued' if queued else 'off'
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
        # {bot_name: epoch} — "farm now" requests (switch on even without known drops)
        self._farm_requests = dict(saved.get('farm_requests') or {})
        # {bot_name: {at, drops}} — when ASF last looked and found nothing to farm, and
        # Andvari's drop count for that account then; only a different count later
        # switches the bot back on. (Older saves hold a bare epoch: {bot_name: epoch}.)
        self._checked_empty = dict(saved.get('checked_empty') or {})
        # {bot_name: epoch} — when ASF last reported cards to farm for a connected bot.
        # A disconnected bot reports nothing, so this keeps it on while it reconnects.
        self._last_work = dict(saved.get('last_work') or {})
        # Why ASF's global config is unsafe (None while it is safe): every bot stays off
        self._global_config_unsafe = saved.get('global_config_unsafe')
        # Team Fortress 2 mode: {active, names (None = every account), since, reason,
        # stopped_at, licensed: [bot names that hold the free license]}
        self._team_fortress = dict(saved.get('team_fortress') or {})
        # {bot_name: {at, error}} — failed license attempts (retried after LICENSE_RETRY_SECONDS)
        self._license_failures = dict(saved.get('license_failures') or {})
        # {bot_name: epoch} — bots started only to add the license (stopped once done)
        self._license_started = dict(saved.get('license_started') or {})
        # {bot_name: epoch} — bots Heimdall told to play Team Fortress 2 (ASF's manual
        # mode). Any of them no longer in the mode is resumed by the tick, so a stop
        # that failed half-way, a shrunk account list or a race never strands one.
        self._playing = dict(saved.get('team_fortress_playing') or {})
        self._global_config_shape_logged = False
        self._connected_since = {}       # {bot_name: epoch}, in memory only
        self._queued = set()             # wanted on, but over MAX_RUNNING_BOTS
        self.card_deals = None           # set by app.py: Andvari's per-account drop counts
        self._last_bots = None
        self._accounts_cache = {}
        self._accounts_cached_at = 0
        self._write_lock = threading.Lock()   # one state-file write at a time, newest snapshot last
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

    def _console(self, text):
        """One ASF console command (what the ASF UI terminal runs); returns ASF's answer."""
        return str(self._call('POST', '/Api/Command', {'Command': text}) or '')

    # ---- accounts --------------------------------------------------------------

    def _accounts(self):
        """{bot_name: {steamid, account_name}} for every Heimdall account."""
        accounts = {}
        for steamid in self.steam.storage.list_accounts():
            data = self.steam.storage.load_account(steamid) or {}
            login = (data.get('account_name') or '').strip()
            # The bot name goes into ASF API paths: only plain Steam logins qualify.
            if STEAM_LOGIN.match(login) and login.upper() != 'ASF':
                accounts[login] = {'steamid': str(steamid), 'account_name': login}
        return accounts

    def _refresh_accounts(self):
        accounts = self._accounts()
        self._accounts_cache, self._accounts_cached_at = accounts, time.time()
        return accounts

    def _cached_accounts(self):
        """The account list from the last tick, re-read when older than
        ACCOUNTS_CACHE_SECONDS: status() is polled by the UI, and reading the
        accounts decrypts every maFile."""
        if not self._accounts_cache or time.time() - self._accounts_cached_at >= ACCOUNTS_CACHE_SECONDS:
            return self._refresh_accounts()
        return self._accounts_cache

    def _bot_name_for_login(self, account_name):
        login = (account_name or '').strip().lower()
        for name in self._accounts():
            if name.lower() == login:
                return name
        return None

    def _persist(self):
        # Copies taken under the lock (request threads mutate these dicts while the
        # file is written), and one write at a time so an older snapshot never
        # lands after a newer one.
        with self._write_lock:
            with self._lock:
                state = copy.deepcopy({'paused_for_ratatoskr': self._paused_for_ratatoskr,
                                       'attempts': self._attempts, 'farm_requests': self._farm_requests,
                                       'checked_empty': self._checked_empty,
                                       'last_work': self._last_work,
                                       'global_config_unsafe': self._global_config_unsafe,
                                       'team_fortress': self._team_fortress,
                                       'license_failures': self._license_failures,
                                       'license_started': self._license_started,
                                       'team_fortress_playing': self._playing})
            try:
                atomic_write_json(self.state_path, state)
            except Exception as e:
                logger.error('[ASF] could not save %s: %s', self.state_path, e)

    # ---- provisioning ------------------------------------------------------------

    def provision(self, bots=None, wanted=()):
        """Create a hardened bot for every account ASF does not have yet, and
        re-apply the safety settings to any bot where they drifted (for example
        edited in the ASF UI). Other settings edited there are kept. ASF never
        returns the login or password in a config, so neither can be copied here.
        Returns the bot names written."""
        bots = self._bots() if bots is None else bots
        written = []
        for name, account in self._accounts().items():
            existing = (bots.get(name) or {}).get('BotConfig')
            if existing is not None and all(existing.get(key) == value
                                            for key, value in HARDENED_BOT_CONFIG.items()):
                continue
            kept = {key: value for key, value in (existing or {}).items()
                    if not key.startswith('s_')}   # 's_' keys are ASF's string copies of numbers
            if existing is None:
                kept = {'Enabled': name in wanted}
            desired = {**kept, **HARDENED_BOT_CONFIG, 'SteamLogin': account['account_name']}
            try:
                self._call('POST', f'/Api/Bot/{name}', {'BotConfig': desired})
            except AsfError as e:
                logger.warning('[ASF] could not write the bot config for %s: %s', name, e)
                continue
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
            with self._lock:
                license_check = name in self._license_started
            if (bot.get('BotConfig') or {}).get('Enabled') is False and not license_check:
                continue                 # switched off: nothing to log in for (unless started for the license)
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
                playing = self._playing.pop(name, None) is not None
            if playing:
                # Team Fortress 2 mode: stop playing it (ASF's farmer stays paused);
                # once Ratatoskr is gone the tick resumes the bot and, still in the
                # mode, tells it to play again.
                self._console(f'reset {name}')
            elif farmer.get('Paused') and not already_ours:
                return                   # paused by hand: leave it alone, and never resume it
            else:
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
            if time.time() - (info.get('since') or 0) < RATATOSKR_LOGIN_GRACE_SECONDS:
                continue                 # Ratatoskr may still be logging in
            status = (self.ratatoskr.get_status(info['steamid']) or {}).get('status')
            if status in ('connected', 'gc_lost'):
                continue                 # Ratatoskr still holds the session
            # Only a farmer that is still paused needs a Resume: after an ASF restart
            # it is not, and ASF answers a Resume then with Success false.
            if name in bots and bot_view(bots[name])['paused']:
                try:
                    self._command(name, 'Resume')
                    logger.info('[ASF] resumed %s (Ratatoskr session over)', name)
                except AsfError as e:
                    logger.warning('[ASF] could not resume %s after Ratatoskr: %s', name, e)
            # Forgotten either way, so one refused Resume never blocks every later tick.
            with self._lock:
                self._paused_for_ratatoskr.pop(name, None)
            self._persist()

    # ---- which bots run ------------------------------------------------------------

    def _andvari_drops(self):
        """{steamid: (drops left, fetched_at)} from Andvari's last account refresh."""
        try:
            return self.card_deals.account_drops() if self.card_deals else {}
        except Exception as e:
            logger.warning('[ASF] could not read Andvari drop counts: %s', e)
            return {}

    def _note_checks(self, bots, accounts, drops, now, skip=frozenset()):
        """Record which bots ASF has checked and found empty, with Andvari's drop
        count for the account at that moment (and close their "farm now" requests).
        Bots in *skip* (Team Fortress 2 mode, license checks) are not card checks."""
        changed = False
        for name, bot in bots.items():
            if name in skip or not bot.get('IsConnectedAndLoggedOn'):
                self._connected_since.pop(name, None)
                continue
            view = bot_view(bot)
            if not view['playing_possible']:
                # The account is being played elsewhere, so ASF reports nothing to
                # farm: that is no check. The clock starts once it can play.
                self._connected_since.pop(name, None)
                continue
            since = self._connected_since.setdefault(name, now)
            if view['now_farming'] or view['games_to_farm']:
                with self._lock:
                    stale = now - (self._last_work.get(name) or 0) >= EMPTY_CHECK_SECONDS
                    if stale:                # saved every few minutes, not every tick
                        self._last_work[name] = now
                changed |= stale
                continue
            if view['paused']:
                continue
            if now - since >= EMPTY_CHECK_SECONDS:
                steamid = (accounts.get(name) or {}).get('steamid')
                left = drops.get(steamid, (0, 0))[0] if steamid else 0
                with self._lock:
                    previous = self._checked_empty.get(name)
                    self._checked_empty[name] = {'at': now, 'drops': left}
                    changed |= self._farm_requests.pop(name, None) is not None
                changed = True
                if left > 0 and not (isinstance(previous, dict) and previous.get('drops') == left):
                    logger.warning('[ASF] %s: ASF found nothing to farm, but Andvari counts %s card '
                                   'drops left; keeping it off until that count changes', name, left)
        if changed:
            self._persist()

    @staticmethod
    def _drops_changed_since_check(check, left, fetched_at):
        """Whether Andvari's drop count for an account ASF found empty is news."""
        if not check:
            return True
        if isinstance(check, dict):
            return left != check.get('drops')
        return (fetched_at or 0) > check     # older save: only the check time is known

    def _wanted(self, bots, accounts, now, drops=None, team_fortress=frozenset()):
        """Bots that should run: ASF's own queue first, then "farm now" requests,
        then accounts Andvari saw with drops left — at most MAX_RUNNING_BOTS —
        plus every bot in Team Fortress 2 mode, outside that ceiling."""
        drops = self._andvari_drops() if drops is None else drops
        with self._lock:
            requests_, checked = dict(self._farm_requests), dict(self._checked_empty)
            last_work = dict(self._last_work)
            paused = set(self._paused_for_ratatoskr)
        ranked = []
        for name, account in accounts.items():
            if name in team_fortress:
                continue
            bot = bots.get(name)
            view = bot_view(bot) if bot else None
            if view and view['enabled'] and (view['now_farming'] or view['games_to_farm']
                                             or view['paused'] or name in paused):
                ranked.append((0, -view['cards_remaining'], name))
            elif (view and view['enabled'] and not view['connected']
                  and now - (last_work.get(name) or 0) < RECONNECT_GRACE_SECONDS):
                # Disconnected mid-farm: ASF reports nothing until it is back, so keep it on.
                ranked.append((0, 0, name))
            elif now - (requests_.get(name) or 0) < FARM_NOW_SECONDS:
                ranked.append((1, 0, name))
            else:
                left, fetched_at = drops.get(account['steamid'], (0, 0))
                if left > 0 and self._drops_changed_since_check(checked.get(name), left, fetched_at):
                    ranked.append((2, -left, name))
        ranked.sort()
        wanted = {name for _, _, name in ranked[:MAX_RUNNING_BOTS]} | set(team_fortress)
        return wanted, {name for _, _, name in ranked[MAX_RUNNING_BOTS:]}

    def _apply_enabled(self, bots, wanted, team_fortress=frozenset()):
        """Switch bots on/off to match *wanted*; returns the names changed."""
        changed = []
        for name, bot in bots.items():
            config = bot.get('BotConfig')
            if config is None or name not in self._accounts_cache:
                continue
            enabled = config.get('Enabled', True) is not False
            if enabled == (name in wanted):
                continue
            kept = {key: value for key, value in config.items() if not key.startswith('s_')}
            desired = {**kept, **HARDENED_BOT_CONFIG, 'Enabled': name in wanted,
                       'SteamLogin': self._accounts_cache[name]['account_name']}
            try:
                self._call('POST', f'/Api/Bot/{name}', {'BotConfig': desired})
            except AsfError as e:
                logger.warning('[ASF] could not switch %s %s: %s', name, 'on' if name in wanted else 'off', e)
                continue
            changed.append(name)
            reason = ('on (Team Fortress 2)' if name in team_fortress else 'on (cards to farm)'
                      if name in wanted else 'off (nothing to farm)')
            logger.info('[ASF] switched %s %s', name, reason)
        return changed

    # ---- Team Fortress 2 ---------------------------------------------------------------

    def _licensed(self):
        with self._lock:
            return set(self._team_fortress.get('licensed') or [])

    def _note_license(self, name, answer, now):
        """Read ASF's addlicense answer: owned (OK or AlreadyPurchased) or a failure."""
        owned = bool(re.search(r'Status:\s*(OK|Fail/AlreadyPurchased)\b', answer))
        with self._lock:
            if owned:
                licensed = set(self._team_fortress.get('licensed') or [])
                licensed.add(name)
                self._team_fortress['licensed'] = sorted(licensed)
                self._license_failures.pop(name, None)
            else:
                self._license_failure(name, now, answer.strip()[:200] or 'no answer')
        self._persist()
        if owned:
            logger.info('[ASF] %s owns Team Fortress 2', name)
        else:
            logger.warning('[ASF] could not add Team Fortress 2 to %s: %s', name, answer.strip()[:200])
        return owned

    def _license_failure(self, name, now, error):
        """Record a failed license attempt (call with the lock held or not: RLock)."""
        with self._lock:
            count = (self._license_failures.get(name) or {}).get('count', 0) + 1
            self._license_failures[name] = {'at': now, 'error': error, 'count': count}

    @staticmethod
    def _license_retry_due(failure, now):
        """Whether a failed license attempt may be tried again: after an hour,
        doubling with each failure, at most a day."""
        if not failure:
            return True
        wait = min(LICENSE_RETRY_MAX_SECONDS, LICENSE_RETRY_SECONDS * 2 ** max(0, failure.get('count', 1) - 1))
        return now - failure.get('at', 0) >= wait

    def _license_sweep(self, bots, accounts, now):
        """Give every account the free Team Fortress 2 license (see the module
        docstring). Returns the bot started this tick, if any."""
        licensed = self._licensed()
        with self._lock:
            failures, started = dict(self._license_failures), dict(self._license_started)
        # 1. logged-in bots that are not confirmed yet: ask now (no login needed)
        for name in accounts:
            bot = bots.get(name) or {}
            if name in licensed or not bot.get('IsConnectedAndLoggedOn'):
                continue
            if name not in started and not self._license_retry_due(failures.get(name), now):
                continue
            try:
                self._note_license(name, self._console(f'addlicense {name} app/{TEAM_FORTRESS_2}'), now)
            except AsfError as e:
                self._note_license(name, f'ASF error: {e}', now)
        # 2. bots started for the license: stop them once done, or when the login hangs
        licensed = self._licensed()
        for name, since in started.items():
            bot = bots.get(name) or {}
            done = name in licensed or name in self._license_failures_since(since)
            timed_out = not bot.get('IsConnectedAndLoggedOn') and now - since >= LICENSE_LOGIN_TIMEOUT_SECONDS
            if not (done or timed_out or name not in accounts):
                continue
            if timed_out and not done:
                self._license_failure(name, now, 'did not log in (ASF may need the password)')
            enabled = (bot.get('BotConfig') or {}).get('Enabled', True) is not False
            if bot.get('KeepRunning') and not enabled:    # switched on meanwhile for farming: leave it
                try:
                    self._command(name, 'Stop')
                except AsfError as e:
                    logger.warning('[ASF] could not stop %s after the license check: %s', name, e)
            with self._lock:
                self._license_started.pop(name, None)
            self._persist()
        # 3. start one stopped bot that still needs the license, only while no login is pending
        with self._lock:
            if self._license_started:
                return None
            failures = dict(self._license_failures)
        if any(bot.get('KeepRunning') and not bot.get('IsConnectedAndLoggedOn') for bot in bots.values()):
            return None
        for name in sorted(accounts):
            bot = bots.get(name)
            if not bot or name in licensed or bot.get('KeepRunning'):
                continue
            if not self._license_retry_due(failures.get(name), now):
                continue
            try:
                self._command(name, 'Start')
            except AsfError as e:
                self._license_failure(name, now, f'could not start: {e}')
                self._persist()
                continue
            with self._lock:
                self._license_started[name] = now
            self._persist()
            logger.info('[ASF] started %s to add Team Fortress 2 to its library', name)
            return name
        return None

    def _license_failures_since(self, since):
        with self._lock:
            return {name for name, failure in self._license_failures.items() if failure.get('at', 0) >= since}

    def _team_fortress_names(self, accounts):
        """Bot names in Team Fortress 2 mode right now (empty when it is off)."""
        with self._lock:
            mode = dict(self._team_fortress)
        if not mode.get('active'):
            return set()
        names = mode.get('names')
        return set(accounts) if names is None else {name for name in names if name in accounts}

    def start_team_fortress(self, steamids=None, reason='manual'):
        """Switch Team Fortress 2 mode on for these accounts (None = every account).
        The next tick switches their bots on, adds the license and plays it."""
        accounts = self._accounts()
        if steamids is None:
            names = None
        else:
            wanted = {str(steamid) for steamid in steamids}
            names = sorted(name for name, account in accounts.items() if account['steamid'] in wanted)
            if not names:
                raise UnknownAccount('none of these accounts is known')
        with self._lock:
            self._team_fortress.update({'active': True, 'names': names, 'since': time.time(),
                                        'reason': reason, 'stopped_at': None})
        self._persist()
        logger.info('[ASF] Team Fortress 2 mode on (%s) for %s', reason,
                    'every account' if names is None else ', '.join(names))
        return {'success': True, 'accounts': len(accounts) if names is None else len(names)}

    def stop_team_fortress(self):
        """Switch Team Fortress 2 mode off: every bot told to play is resumed (now,
        and by every later tick until it is), so card farming continues and bots
        with nothing to farm switch off on the following ticks."""
        with self._lock:
            self._team_fortress.update({'active': False, 'stopped_at': time.time()})
        self._persist()
        logger.info('[ASF] Team Fortress 2 mode off')
        try:
            self._release_players(self._bots(), set())
        except AsfError as e:
            logger.info('[ASF] resuming after Team Fortress 2 continues on the next tick: %s', e)
        return {'success': True}

    def _release_players(self, bots, team_fortress):
        """Resume every bot told to play Team Fortress 2 that is no longer in the
        mode. A bot not logged in right now is resumed once it is (ASF keeps the
        farmer paused across reconnects)."""
        with self._lock:
            playing = dict(self._playing)
            paused_for_ratatoskr = set(self._paused_for_ratatoskr)
        for name in sorted(set(playing) - set(team_fortress)):
            bot = bots.get(name)
            if bot is not None and not bot.get('KeepRunning'):
                pass                     # stopped: nothing plays any more
            elif bot is None or not bot.get('IsConnectedAndLoggedOn'):
                continue                 # try again once it is logged in
            elif name not in paused_for_ratatoskr and bot_view(bot)['paused']:
                try:
                    self._command(name, 'Resume')
                    logger.info('[ASF] %s stopped playing Team Fortress 2, card farming resumed', name)
                except AsfError as e:
                    logger.warning('[ASF] could not resume %s after Team Fortress 2: %s', name, e)
                    continue
            with self._lock:
                self._playing.pop(name, None)
            self._persist()

    def _play_team_fortress(self, bots, names, now=None):
        """Every logged-in bot in Team Fortress 2 mode plays it. A bot is told to
        play when it was not told yet, when it was seen logged out since (a
        reconnect may drop the manual game while the farmer stays paused), and
        again every PLAY_AGAIN_SECONDS as a safety net. Returns the names told."""
        now = time.time() if now is None else now
        with self._lock:
            paused_for_ratatoskr = set(self._paused_for_ratatoskr)
            playing = dict(self._playing)
        told = []
        licensed = self._licensed()
        for name in sorted(names - paused_for_ratatoskr):
            bot = bots.get(name) or {}
            if not bot.get('IsConnectedAndLoggedOn'):
                if name in playing:
                    with self._lock:
                        self._playing.pop(name, None)    # logged out: tell it again once back
                    self._persist()
                continue
            if not bot.get('IsPlayingPossible', True) or name not in licensed:
                continue                 # played elsewhere, or the license sweep adds it first
            if name in playing and bot_view(bot)['paused'] and now - playing[name] < PLAY_AGAIN_SECONDS:
                continue
            try:
                answer = self._console(f'play {name} {TEAM_FORTRESS_2}')
                logger.info('[ASF] %s plays Team Fortress 2: %s', name, answer.strip()[:120])
                told.append(name)
                with self._lock:
                    self._playing[name] = now
                self._persist()
            except AsfError as e:
                logger.warning('[ASF] could not make %s play Team Fortress 2: %s', name, e)
        return told

    def team_fortress_status(self):
        with self._lock:
            mode = copy.deepcopy(self._team_fortress)
            failures = copy.deepcopy(self._license_failures)
            started = dict(self._license_started)
            paused_for_ratatoskr = set(self._paused_for_ratatoskr)
            playing_told = set(self._playing)
        accounts = self._cached_accounts()
        names = self._team_fortress_names(accounts)
        chosen = set(accounts) if mode.get('names') is None else set(mode.get('names') or [])
        licensed = set(mode.get('licensed') or [])
        bots = self._last_bots or {}
        rows = []
        for name, account in sorted(accounts.items(), key=lambda item: item[0].lower()):
            bot = bots.get(name) or {}
            playing = (name in names and name in playing_told and bool(bot.get('IsConnectedAndLoggedOn'))
                       and name not in paused_for_ratatoskr)
            rows.append({'steamid': account['steamid'], 'account_name': name,
                         'licensed': name in licensed, 'license_error': (failures.get(name) or {}).get('error'),
                         'license_checking': name in started, 'in_mode': name in names,
                         'chosen': name in chosen,
                         'connected': bool(bot.get('IsConnectedAndLoggedOn')), 'playing': playing,
                         'wallet_currency': bot.get('WalletCurrency')})
        return {'active': bool(mode.get('active')), 'since': mode.get('since'), 'reason': mode.get('reason'),
                'stopped_at': mode.get('stopped_at'), 'all_accounts': mode.get('names') is None,
                'accounts': rows, 'licensed': sum(1 for row in rows if row['licensed']),
                'playing': sum(1 for row in rows if row['playing'])}

    def wallet_currency(self, steamid):
        """The account's Steam wallet currency id (1 = US dollar) from ASF, or None."""
        for name, account in self._cached_accounts().items():
            if account['steamid'] == str(steamid):
                return ((self._last_bots or {}).get(name) or {}).get('WalletCurrency') or None
        return None

    # ---- global config watch ---------------------------------------------------------

    def _global_config_problems(self):
        """What in ASF's global config breaks the safety settings asf_setup.py
        wrote: [] when safe, None when the answer's shape is unknown (then
        nothing is switched off — a shape change must not stop farming)."""
        result = self._call('GET', '/Api/ASF')
        config = result.get('GlobalConfig') if isinstance(result, dict) else None
        if not isinstance(config, dict) or 'SteamOwnerID' not in config:
            if not self._global_config_shape_logged:
                self._global_config_shape_logged = True
                logger.warning('[ASF] could not read the global config (unknown /Api/ASF answer): '
                               'owner and safety settings are not checked')
            return None
        problems = []
        try:
            if int(config.get('SteamOwnerID') or 0) != 0:
                problems.append('SteamOwnerID is set (an owner can command every bot from Steam chat)')
        except (TypeError, ValueError):
            problems.append('SteamOwnerID is not a number')
        blacklist = config.get('Blacklist')
        if isinstance(blacklist, list) and COUNTER_STRIKE_2 not in blacklist:
            problems.append('Counter-Strike 2 (730) is no longer in Blacklist')
        if 'UpdateChannel' in config and config.get('UpdateChannel') != 0:
            problems.append('UpdateChannel is not 0 (ASF would update itself)')
        return problems

    def _check_global_config(self):
        """Refresh the sticky "global config unsafe" flag. True while unsafe."""
        try:
            problems = self._global_config_problems()
        except AsfError as e:
            logger.warning('[ASF] could not read the global config: %s', e)
            problems = None
        with self._lock:
            before = self._global_config_unsafe
        if problems is None:
            return before is not None    # unknown: keep whatever was known last
        message = None
        if problems:
            message = ('ASF global config is unsafe: ' + '; '.join(problems)
                       + '. Every bot is switched off. Fix Yggdrasil/asf/config/ASF.json '
                         '(re-run `make asf-setup`) and restart ASF.')
        if message != before:
            with self._lock:
                self._global_config_unsafe = message
            self._persist()
            if message:
                logger.error('[ASF] %s', message)
            else:
                logger.info('[ASF] global config is safe again: bots may run')
        return message is not None

    def farm_now(self, steamid):
        """Switch this account's bot on so ASF checks it right away (after buying
        a game); it switches off again if ASF finds nothing."""
        name = self._name_for_steamid(steamid)
        with self._lock:
            self._farm_requests[name] = time.time()
            self._checked_empty.pop(name, None)
        self._persist()
        return {'success': True}

    # ---- manual controls -----------------------------------------------------------

    def _name_for_steamid(self, steamid):
        for name, account in self._accounts().items():
            if account['steamid'] == str(steamid):
                return name
        raise UnknownAccount('unknown account')

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
            accounts = self._refresh_accounts()
            bots = self._bots()
            unsafe = self._check_global_config()
            drops = self._andvari_drops()
            team_fortress = set() if unsafe else self._team_fortress_names(accounts)
            with self._lock:
                license_checks = set(self._license_started)
            self._note_checks(bots, accounts, drops, now, skip=team_fortress | license_checks)
            wanted, self._queued = self._wanted(bots, accounts, now, drops, team_fortress)
            if unsafe:
                wanted, self._queued = set(), set()   # every bot off until the config is safe
            if self.provision(bots, wanted) + self._apply_enabled(bots, wanted, team_fortress):
                bots = self._bots()
            self._resume_after_ratatoskr(bots)
            if not unsafe:
                self._assist_one(bots, accounts, now)
                if self._license_sweep(bots, accounts, now):
                    bots = self._bots()
                self._play_team_fortress(bots, team_fortress, now)
            self._release_players(bots, team_fortress)
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
            queued = set(self._queued)
            global_config_unsafe = self._global_config_unsafe
        rows = []
        for name, account in sorted(self._cached_accounts().items(), key=lambda item: item[0].lower()):
            view = bot_view(bots[name]) if name in bots else None
            tries = attempts.get(name) or {}
            row = {'steamid': account['steamid'], 'account_name': name,
                   'state': farming_state(view, name in paused, tries, name in queued),
                   'login_attempts': tries.get('count', 0), 'last_error': tries.get('last_error')}
            if view:
                row.update(view)
                row['required_input'] = INPUT_NAMES.get(view['required_input'])
            rows.append(row)
        return {
            'enabled': True, 'reachable': error is None, 'error': error,
            'global_config_unsafe': global_config_unsafe,
            'checked_at': self._last_tick_at, 'accounts': rows,
            'totals': {
                'farming': sum(1 for row in rows if row['state'] == 'farming'),
                'needs_attention': sum(1 for row in rows if row['state'] == 'needs_attention'),
                'running': sum(1 for row in rows if row.get('enabled')),
                'max_running': MAX_RUNNING_BOTS,
                'cards_remaining': sum(row.get('cards_remaining') or 0 for row in rows),
                'games_to_farm': sum(row.get('games_to_farm') or 0 for row in rows),
            },
        }
