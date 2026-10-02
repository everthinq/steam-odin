"""Team Fortress 2 case drops — be playing the minute a case is added, sell the
drops the minute they arrive.

A freshly added Team Fortress 2 case drops through the item drop system to
anyone playing (ASF's "playing" status counts — tested 2026-10-02), and it is
worth the most in its first hours: the Haunted Hoard Case was $0.05 a day after
release, with 14,500 listings. Drops are weekly-capped, so idling all the time
would spend the week's drops before the case exists. Hence three parts:

* **Release watcher** — polls Team Fortress 2's official news feed (the Steam
  news API for app 440, public) every few minutes. An update that says
  "Added the <name> Case" (or Crate) is a release: Team Fortress 2 mode starts
  on the chosen accounts at once (``AsfService.start_team_fortress``), the case
  joins the auto-sell list, then Telegram gets the news and the phone rings.
* **Play** — lives in ``asf_service.py`` (Team Fortress 2 mode and the license
  sweep that gives every account the free game).
* **Auto-sell** — while Team Fortress 2 mode is on (and for a while after), each
  playing account's Team Fortress 2 inventory is read in turn, paced, and every
  marketable item named on the sell list is listed on the Steam Community
  Market one cent under the lowest listing, priced in that account's wallet
  currency, then confirmed with the account's own identity secret (only those
  listings: their confirmation names the item).

Steam calls are serial with a gap between them (429s are the enemy); the
news feed is on api.steampowered.com and costs nothing.
"""
import html
import json
import logging
import re
import threading
import time

import requests

from gjallarhorn_news_service import _bbcode_to_lines
from jsonio import atomic_write_json, read_json
from notifications import send_notification

log = logging.getLogger(__name__)

TEAM_FORTRESS_2 = 440
CONTEXT_ID = 2
# Valve's own feeds only (not press sites that retell an update weeks later).
OFFICIAL_FEEDS = ('tf2_blog', 'steam_updates', 'steam_community_announcements')
NEWS_URL = ('https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/'
            '?appid=440&count=20&maxlength=0&format=json&feeds=' + ','.join(OFFICIAL_FEEDS))
INVENTORY_URL = 'https://steamcommunity.com/inventory/{steamid}/440/2'
PRICE_URL = 'https://steamcommunity.com/market/priceoverview/'
SELL_URL = 'https://steamcommunity.com/market/sellitem/'
MARKET_URL = 'https://steamcommunity.com/market/'   # carries g_rgWalletInfo: the fee rules per currency
STATE_PATH = 'cache/team_fortress.json'
USER_AGENT = 'Mozilla/5.0 (Heimdall Team Fortress 2 watcher)'
HTTP_TIMEOUT_SECONDS = 25

COMMUNITY_GAP_SECONDS = 4          # between any two steamcommunity.com calls from here
INVENTORY_INTERVAL_SECONDS = 5 * 60  # one account's inventory at most this often
SELL_AFTER_STOP_SECONDS = 2 * 3600   # keep selling this long after the mode stops
PRICE_TIME_TO_LIVE_SECONDS = 120
SELL_LOOP_SECONDS = 15
MINIMUM_LISTING_MINOR_UNITS = 3
# Steam's fee rules for a US dollar wallet; other currencies are read from the
# Market page once (their minimum fee is larger: 10 øre per fee in kroner).
US_DOLLAR_WALLET = {'fee_minimum': 1, 'fee_base': 0, 'fee_percent': 0.05, 'publisher_percent': 0.10}
WALLET_INFO_TIME_TO_LIVE_SECONDS = 30 * 86400
SALES_HISTORY_CAP = 300
CASES_HISTORY_CAP = 50
ATTEMPT_MEMORY_SECONDS = 7 * 86400
MAX_SELL_TRIES = 3                 # per item: a listing Steam keeps refusing is left alone after this
RATE_LIMIT_COOLDOWN_SECONDS = 15 * 60   # after an HTTP 429, no inventory or price reads this long
OWN_PRICE_MEMORY_SECONDS = 6 * 3600     # a lowest listing at our own last price is ours: not undercut

# A release line STARTS with one of these verbs ("Added the Haunted Hoard Case",
# "Introducing the Summer 2024 Cosmetic Case and the Summer 2024 War Paint Case"),
# so "Fixed ... added to the Gargoyle Case" never counts. After the verb, every
# capitalised name ending in Case or Crate is a new case, except a name followed
# by "Key" (the key, not the case) or after "to the"/"for the"/... (an old case
# that gained something: "Added effects to the Scream Fortress XVII War Paint Case").
_RELEASE_VERB_RE = re.compile(r"^\s*(?:we(?:'ve|\s+have)\s+)?(?:added|introducing|introduced)\b", re.IGNORECASE)
_CASE_NAME_RE = re.compile(r"\b((?:[A-Z0-9][\w'’.&!:-]*\s+){1,6}(?:Case|Crate)(s?))\b")
_KEY_AFTER_RE = re.compile(r"^\s+Keys?\b")
_EXISTING_BEFORE_RE = re.compile(r"\b(?:to|for|from|in|into|on|of|with|inside)\s+(?:the\s+|a\s+|an\s+|every\s+|all\s+)?$",
                                 re.IGNORECASE)
_HTML_BREAK_RE = re.compile(r'<\s*(?:br|/?li|/?ul|/?ol|/?p|/?h\d|/?div)\b[^>]*>', re.IGNORECASE)
_HTML_TAG_RE = re.compile(r'<[^>]+>')


def news_lines(contents):
    """A news post body (HTML from the Team Fortress 2 blog, BBCode from Steam
    announcements) as clean text lines."""
    text = _HTML_BREAK_RE.sub('\n', contents or '')
    text = html.unescape(_HTML_TAG_RE.sub('', text))
    return _bbcode_to_lines(text)


def detect_release(contents):
    """(case names, release lines) a post announces. A line that adds cases in
    the plural ("Added the Summer 2027 Cosmetic and War Paint Cases") is a
    release line without exact names: it still starts playing, but nothing
    joins the sell list without a name."""
    names, lines = [], []
    for line in news_lines(contents):
        verb = _RELEASE_VERB_RE.search(line)
        if not verb:
            continue
        rest = line[verb.end():]
        released = False
        for match in _CASE_NAME_RE.finditer(rest):
            if _KEY_AFTER_RE.match(rest[match.end():]) or _EXISTING_BEFORE_RE.search(rest[:match.start()]):
                continue
            released = True
            if match.group(2):           # plural: no single Market name
                continue
            name = re.sub(r'\s+', ' ', match.group(1)).strip()
            name = re.sub(r'^The\s+', '', name)
            if name.lower() not in {known.lower() for known in names}:
                names.append(name)
        if released:
            lines.append(line.strip())
    return names, lines


def detect_new_cases(contents):
    """Names of the cases (or crates) a post says were added, in order, once each."""
    return detect_release(contents)[0]


def parse_price_minor_units(text):
    """A Steam Market price in the account's currency format -> minor units
    ('$0.05' -> 5, '0,49 kr' -> 49, '₩ 1,000' -> 100000, 'Rp 1 000' -> 100000).
    Steam counts every currency in hundredths, even those shown without decimals.
    None when unparseable."""
    text = str(text or '')
    match = re.search(r'\d[\d\s.,  \']*', text)
    if not match:
        return None
    number = re.sub(r'[\s  \']', '', match.group(0)).rstrip('.,')
    decimal = re.search(r'[.,](\d{1,2})$', number)
    if decimal:
        whole = re.sub(r'\D', '', number[:decimal.start()]) or '0'
        fraction = decimal.group(1).ljust(2, '0')
        return int(whole) * 100 + int(fraction)
    digits = re.sub(r'\D', '', number)
    return int(digits) * 100 if digits else None


def buyer_pays_for(receives, wallet):
    """What a buyer pays for a listing that gives the seller *receives*, by
    Steam's own formula: the Steam fee and the publisher fee are each a share of
    the amount, but at least the wallet currency's minimum fee."""
    minimum = int(wallet['fee_minimum'])
    steam_fee = int(max(receives * float(wallet['fee_percent']), minimum) + int(wallet['fee_base']))
    publisher_fee = int(max(receives * float(wallet['publisher_percent']), minimum))
    return receives + steam_fee + publisher_fee


def seller_receives(buyer_pays, wallet):
    """The most the seller can receive while the buyer pays at most *buyer_pays*
    (0 when even one minor unit costs more)."""
    rate = 1 + float(wallet['fee_percent']) + float(wallet['publisher_percent'])
    receives = int(buyer_pays / rate) + 2
    while receives > 0 and buyer_pays_for(receives, wallet) > buyer_pays:
        receives -= 1
    return receives


def parse_wallet_info(page):
    """g_rgWalletInfo from a Steam Community page -> {currency, fee rules}, or None."""
    match = re.search(r'g_rgWalletInfo\s*=\s*(\{.*?\});', page or '')
    if not match:
        return None
    try:
        info = json.loads(match.group(1))
        return {'currency': int(info['wallet_currency']),
                'fee_minimum': int(info.get('wallet_fee_minimum') or 1),
                'fee_base': int(info.get('wallet_fee_base') or 0),
                'fee_percent': float(info.get('wallet_fee_percent') or 0.05),
                'publisher_percent': float(info.get('wallet_publisher_fee_percent_default') or 0.10)}
    except (KeyError, TypeError, ValueError):
        return None


def listing_price(lowest_minor_units, wallet=US_DOLLAR_WALLET, undercut=True):
    """(buyer pays, seller receives) for listing one minor unit under the lowest
    listing (at it when *undercut* is False: the lowest listing is our own). The
    buyer price Steam shows is recomputed from what the seller receives, so it
    never ends above the target. None without a lowest listing or when the fees
    would eat the whole price."""
    if not lowest_minor_units:
        return None
    target = max(MINIMUM_LISTING_MINOR_UNITS, int(lowest_minor_units) - (1 if undercut else 0))
    receives = seller_receives(target, wallet)
    return (buyer_pays_for(receives, wallet), receives) if receives > 0 else None


def sell_list_items(inventory, names):
    """[(assetid, market hash name, marketable)] in an inventory answer whose
    name is on the sell list (case-insensitive)."""
    wanted = {name.strip().lower() for name in names or [] if name and name.strip()}
    descriptions = {(item.get('classid'), item.get('instanceid')): item
                    for item in (inventory or {}).get('descriptions') or []}
    found = []
    for asset in (inventory or {}).get('assets') or []:
        item = descriptions.get((asset.get('classid'), asset.get('instanceid'))) or {}
        name = item.get('market_hash_name') or ''
        if name.lower() in wanted:
            found.append((str(asset.get('assetid')), name, int(item.get('marketable') or 0) == 1))
    return found


def sellable_items(inventory, names):
    """[(assetid, market hash name)] on the sell list that Steam lets you sell now.
    (A Team Fortress 2 free-to-play account's drops say "Not Tradable or
    Marketable" for good: only Premium accounts' drops can be sold.)"""
    return [(assetid, name) for assetid, name, marketable in sell_list_items(inventory, names) if marketable]


def confirmation_names(confirmation):
    """Every text field of a mobile confirmation that could name the item."""
    parts = [confirmation.get('headline'), confirmation.get('type_name')]
    summary = confirmation.get('summary')
    parts += summary if isinstance(summary, list) else [summary]
    return ' '.join(str(part) for part in parts if part)


class RateLimited(RuntimeError):
    """Steam answered HTTP 429."""


class TeamFortressService:
    def __init__(self, settings_manager, steam_service, asf_service, telegram_caller=None,
                 state_path=STATE_PATH, http=None, sleep=time.sleep):
        self.settings_manager = settings_manager
        self.steam = steam_service
        self.asf = asf_service
        self.telegram_caller = telegram_caller
        self.state_path = state_path
        self._http = http or requests.Session()
        self._sleep = sleep
        self._lock = threading.RLock()
        self._news_lock = threading.Lock()
        self._sell_lock = threading.Lock()
        self._last_community_call = 0.0
        self._prices = {}                # {(name, currency): (minor units, fetched_at)}
        saved = read_json(state_path, default={}) or {}
        self._state = {
            'news': dict(saved.get('news') or {}),           # last_gid, last_date
            'cases': list(saved.get('cases') or []),         # detected releases, newest last
            'sales': list(saved.get('sales') or []),         # listings made, newest last
            # {assetid: {at, tries, listed}}: a listed item is never listed twice
            'attempted': {key: value for key, value in (saved.get('attempted') or {}).items()
                          if isinstance(value, dict)},
            'inventories': dict(saved.get('inventories') or {}),  # {steamid: {at, sellable, error}}
            'wallets': dict(saved.get('wallets') or {}),     # {currency id: fee rules + fetched_at}
            # Team Fortress 2 item ids rise globally, so "dropped since the mode started"
            # = an id above the highest id seen in any inventory read before the mode:
            # {mode start: that id}. The case often drops at the first launch, before
            # the account's own first read, so a per-account baseline would miss it.
            'mode_floors': dict(saved.get('mode_floors') or {}),
            'max_assetid_seen': int(saved.get('max_assetid_seen') or 0),
            'own_prices': dict(saved.get('own_prices') or {}),  # {"name|currency": {buyer_pays, at}}
        }
        old_baselines = [entry for entry in (saved.get('baselines') or {}).values()
                         if isinstance(entry, dict) and entry.get('since') is not None]
        for entry in old_baselines:    # saves from before mode_floors: the lowest non-empty start
            key = str(entry['since'])
            values = [int(other.get('max_assetid') or 0) for other in old_baselines
                      if str(other.get('since')) == key and int(other.get('max_assetid') or 0) > 0]
            if values and key not in self._state['mode_floors']:
                self._state['mode_floors'][key] = min(values)
        self._cooldown_until = 0.0           # after an HTTP 429: no Steam Community reads until then
        self._news_error = None
        self._news_checked_at = None
        self._stop = threading.Event()

    # ---- state -------------------------------------------------------------------

    def _save(self):
        with self._lock:
            snapshot = json.loads(json.dumps(self._state))
        try:
            atomic_write_json(self.state_path, snapshot)
        except Exception as e:
            log.error('[TEAM-FORTRESS] could not save %s: %s', self.state_path, e)

    def _settings(self):
        return self.settings_manager.get_settings()

    def _alert_settings(self, settings):
        chat = str(settings.get('team_fortress_chat_id') or '').strip()
        return {**settings, 'telegram_chat_id': chat} if chat else settings

    def _notify(self, text, ring=False):
        settings = self._settings()
        try:
            send_notification(self._alert_settings(settings), text)
        except Exception as e:
            log.warning('[TEAM-FORTRESS] Telegram message failed: %s', e)
        if ring and self.telegram_caller is not None:
            try:
                self.telegram_caller.ring(message=text)
            except Exception as e:
                log.warning('[TEAM-FORTRESS] ring failed: %s', e)

    # ---- release watcher -----------------------------------------------------------

    def _fetch_news(self):
        response = self._http.get(NEWS_URL, headers={'User-Agent': USER_AGENT}, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        return ((response.json() or {}).get('appnews') or {}).get('newsitems') or []

    def check_news(self):
        """Poll the news feed once. On the first run the current posts become the
        baseline (no alert for old releases). Returns a summary."""
        if not self._news_lock.acquire(blocking=False):
            return {'ok': False, 'error': 'already running'}
        try:
            return self._check_news_locked()
        finally:
            self._news_lock.release()

    def _check_news_locked(self):
        self._news_checked_at = time.time()
        try:
            posts = self._fetch_news()
        except Exception as e:
            self._news_error = str(e)
            log.warning('[TEAM-FORTRESS] news fetch failed: %s', e)
            return {'ok': False, 'error': str(e)}
        self._news_error = None
        if not posts:
            return {'ok': True, 'released': []}
        with self._lock:
            news = dict(self._state['news'])
            known = {case['name'].lower() for case in self._state['cases']}
        newest = max(int(post.get('date') or 0) for post in posts)
        if not news.get('last_date'):
            with self._lock:
                self._state['news'] = {'last_date': newest}
            self._save()
            return {'ok': True, 'released': [], 'baseline': True}
        fresh = sorted((post for post in posts if int(post.get('date') or 0) > news['last_date']),
                       key=lambda post: int(post.get('date') or 0))
        released, unnamed, source = [], [], None
        for post in fresh:
            if post.get('feedname') and post['feedname'] not in OFFICIAL_FEEDS:
                continue
            names, lines = detect_release(post.get('contents'))
            new_names = [name for name in names if name.lower() not in known]
            known.update(name.lower() for name in new_names)
            released += new_names
            if lines and not names:
                unnamed += [line for line in lines if line not in unnamed]
            if new_names or (lines and not names):
                source = source or post
        with self._lock:
            self._state['news']['last_date'] = max(newest, news['last_date'])
        self._save()
        if released or unnamed:
            self._on_release(released, source, unnamed)
        return {'ok': True, 'released': released, 'unnamed': unnamed, 'checked': len(fresh)}

    def _on_release(self, names, post, unnamed=()):
        """A case was just added: play first (every minute counts), then record,
        add it to the sell list, and tell Ivan. *unnamed* are release lines whose
        case names could not be read exactly (plural): they play and alert, and
        the names are left for Ivan to add to the sell list."""
        settings = self._settings()
        label = ', '.join(names) or 'new cases'
        names = list(names)
        play_result = None
        if settings.get('team_fortress_play_on_release', True) and self.asf is not None and self.asf.enabled:
            try:
                accounts = settings.get('team_fortress_accounts') or None
                play_result = self.asf.start_team_fortress(accounts, reason='release: ' + label)
            except Exception as e:
                play_result = {'error': str(e)}
                log.error('[TEAM-FORTRESS] could not start playing for the release: %s', e)
        now = time.time()
        with self._lock:
            for name in names:
                self._state['cases'].append({'name': name, 'detected_at': now,
                                             'title': post.get('title'), 'url': post.get('url'),
                                             'posted_at': int(post.get('date') or 0)})
            self._state['cases'] = self._state['cases'][-CASES_HISTORY_CAP:]
        self._save()
        if settings.get('team_fortress_auto_sell_enabled', True):
            current = list(settings.get('team_fortress_sell_items') or [])
            additions = [name for name in names if name.lower() not in {item.lower() for item in current}]
            if additions:
                self.settings_manager.save_settings({'team_fortress_sell_items': current + additions})
        log.warning('[TEAM-FORTRESS] new case released: %s %s', label, list(unnamed))
        lines = ['🎃 Team Fortress 2: new case added — ' + label, post.get('title') or '', post.get('url') or '']
        lines += [f'• {line}' for line in unnamed]
        if unnamed:
            lines.append('⚠️ Add the exact case names to the sell list on the Team Fortress 2 page')
        if play_result and play_result.get('success'):
            lines.append(f"▶️ Playing Team Fortress 2 on {play_result.get('accounts')} accounts now")
        elif play_result:
            lines.append(f"⚠️ Could not start playing: {play_result.get('error')}")
        else:
            lines.append('Playing was not started (switched off in settings or ASF not set up)')
        if settings.get('team_fortress_auto_sell_enabled', True) and names:
            lines.append('💰 Drops of it will be listed on the Market as they arrive')
        self._notify('\n'.join(line for line in lines if line),
                     ring=bool(settings.get('team_fortress_ring_on_release', True)))

    # ---- auto-sell ----------------------------------------------------------------

    def _community_get(self, url, params=None, cookies=None):
        self._pace()
        return self._http.get(url, params=params, cookies=cookies, timeout=HTTP_TIMEOUT_SECONDS,
                              headers={'User-Agent': USER_AGENT})

    def _pace(self):
        wait = COMMUNITY_GAP_SECONDS - (time.time() - self._last_community_call)
        if wait > 0:
            self._sleep(wait)
        self._last_community_call = time.time()

    def _cookies(self, steamid):
        cookies = self.steam.web_session_cookie_for(steamid)
        if cookies:
            return cookies
        self.steam.ensure_fresh_session(steamid)
        return self.steam.web_session_cookie_for(steamid)

    def _lowest_price(self, name, currency):
        key = (name, int(currency))
        cached = self._prices.get(key)
        if cached and time.time() - cached[1] < PRICE_TIME_TO_LIVE_SECONDS:
            return cached[0]
        response = self._community_get(PRICE_URL, {'appid': TEAM_FORTRESS_2, 'market_hash_name': name,
                                                   'currency': int(currency)})
        if response.status_code == 429:
            raise RateLimited('Steam Market price check rate-limited (HTTP 429)')
        payload = response.json() if response.ok else {}
        lowest = parse_price_minor_units(payload.get('lowest_price')) if payload.get('success') else None
        if lowest:                       # a failed or empty answer is asked again next time
            self._prices[key] = (lowest, time.time())
        return lowest

    def _wallet(self, currency, steamid, cookies):
        """The fee rules for this wallet currency: US dollars are known, any other
        is read once from the Market page (logged in as an account using it)."""
        if int(currency) == 1:
            return US_DOLLAR_WALLET
        with self._lock:
            known = self._state['wallets'].get(str(int(currency)))
        if known and time.time() - known.get('fetched_at', 0) < WALLET_INFO_TIME_TO_LIVE_SECONDS:
            return known
        response = self._community_get(MARKET_URL, None, cookies)
        info = parse_wallet_info(response.text) if response.ok else None
        if not info or info['currency'] != int(currency):
            raise RuntimeError('could not read the Market fee rules for this wallet currency')
        info['fetched_at'] = time.time()
        with self._lock:
            self._state['wallets'][str(info['currency'])] = info
        self._save()
        return info

    def _sell_one(self, steamid, cookies, assetid, receives):
        self._pace()
        response = self._http.post(SELL_URL, cookies=cookies, timeout=HTTP_TIMEOUT_SECONDS, data={
            'sessionid': cookies.get('sessionid'), 'appid': TEAM_FORTRESS_2, 'contextid': CONTEXT_ID,
            'assetid': assetid, 'amount': 1, 'price': int(receives)},
            headers={'User-Agent': USER_AGENT, 'Origin': 'https://steamcommunity.com',
                     'Referer': f'https://steamcommunity.com/profiles/{steamid}/inventory/'})
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.ok and payload.get('success'):
            return True, None
        return False, (payload.get('message') or f'HTTP {response.status_code}')

    def _confirm_listings(self, steamid, names):
        """Accept the account's pending Market listing confirmations that name one
        of the items just listed. Returns how many were accepted."""
        result = self.steam.get_confirmations(steamid)
        if not result.get('success'):
            raise RuntimeError(result.get('message') or 'could not read confirmations')
        lowered = [name.lower() for name in names]
        chosen = []
        for confirmation in result.get('confirmations') or []:
            if int(confirmation.get('type') or 0) != 3:      # 3 = Market listing
                continue
            text = confirmation_names(confirmation).lower()
            if any(name in text for name in lowered):
                chosen.append((str(confirmation.get('id')),
                               str(confirmation.get('nonce') or confirmation.get('key'))))
        if not chosen:
            return 0
        outcome = self.steam.act_on_confirmations_batch(steamid, chosen, 'allow')
        if not outcome.get('success'):
            raise RuntimeError(outcome.get('message') or 'confirming failed')
        return len(chosen)

    def sell_account(self, steamid, account_name=None, force=False, mode_since=None):
        """Read one account's Team Fortress 2 inventory and list every sellable
        item on the sell list that dropped since Team Fortress 2 mode started
        (*mode_since*; the first read of a mode only records where the inventory
        stood). *force* (the API's relist) lists every copy, held before or not.
        Returns a summary (never raises)."""
        settings = self._settings()
        names = settings.get('team_fortress_sell_items') or []
        now = time.time()
        summary = {'steamid': steamid, 'at': now, 'sellable': 0, 'listed': 0, 'confirmed': 0,
                   'not_tradable': 0, 'error': None}
        if not names:
            summary['error'] = 'sell list is empty'
            return summary
        currency = self.asf.wallet_currency(steamid) if self.asf is not None else None
        try:
            cookies = self._cookies(steamid)
            if not cookies:
                raise RuntimeError('no Steam web session for this account')
            response = self._community_get(INVENTORY_URL.format(steamid=steamid),
                                           {'l': 'english', 'count': 2000}, cookies)
            if response.status_code == 429:
                raise RateLimited('inventory read rate-limited (HTTP 429)')
            if not response.ok:
                raise RuntimeError(f'inventory answered HTTP {response.status_code}')
            answer = response.json()
            newest = max((int(asset.get('assetid') or 0) for asset in answer.get('assets') or []), default=0)
            with self._lock:
                attempted = dict(self._state['attempted'])
                floors = self._state['mode_floors']
                if mode_since is not None and str(mode_since) not in floors:
                    floors[str(mode_since)] = self._state['max_assetid_seen']
                    for key in sorted(floors, key=float)[:-5]:
                        floors.pop(key)  # keep the last few modes
                floor = int(floors.get(str(mode_since), 0)) if mode_since is not None else 0
                self._state['max_assetid_seen'] = max(self._state['max_assetid_seen'], newest)
            if force:
                floor = -1
            on_list = [item for item in sell_list_items(answer, names) if int(item[0]) > floor]
            summary['not_tradable'] = sum(1 for _, _, marketable in on_list if not marketable)
            items = [(assetid, name) for assetid, name, marketable in on_list if marketable
                     and (force or not ((attempted.get(assetid) or {}).get('listed')
                                        or (attempted.get(assetid) or {}).get('tries', 0) >= MAX_SELL_TRIES))]
            summary['sellable'] = len(items)
            if items and not currency:
                raise RuntimeError('wallet currency unknown (the ASF bot has not logged in yet)')
            listed_names = []
            for assetid, name in items:
                lowest = self._lowest_price(name, currency)
                if not lowest:
                    summary['error'] = f'waiting for a Market price for {name}'
                    continue             # a brand-new case may have no listing yet: not a try
                own_key = f'{name}|{int(currency)}'
                with self._lock:
                    own = dict(self._state['own_prices'].get(own_key) or {})
                ours = own.get('buyer_pays') == lowest and now - own.get('at', 0) < OWN_PRICE_MEMORY_SECONDS
                price = listing_price(lowest, self._wallet(currency, steamid, cookies), undercut=not ours)
                if price is None:
                    summary['error'] = f'{name}: the Market fees would take the whole price'
                    continue
                buyer_pays, receives = price
                ok, error = self._sell_one(steamid, cookies, assetid, receives)
                with self._lock:
                    tries = (self._state['attempted'].get(assetid) or {}).get('tries', 0) + 1
                    self._state['attempted'][assetid] = {'at': now, 'tries': tries, 'listed': ok}
                    if ok:
                        self._state['own_prices'][own_key] = {'buyer_pays': buyer_pays, 'at': now}
                self._record_sale(steamid, account_name, assetid, name, currency, buyer_pays, receives, error)
                if ok:
                    summary['listed'] += 1
                    listed_names.append(name)
            if listed_names:
                summary['confirmed'] = self._confirm_listings(steamid, sorted(set(listed_names)))
                self._notify(f"💰 Team Fortress 2: listed {summary['listed']} × "
                             f"{', '.join(sorted(set(listed_names)))} on {account_name or steamid} "
                             f"({summary['confirmed']} confirmed)")
        except RateLimited as e:
            summary['error'] = str(e)
            self._cooldown_until = time.time() + RATE_LIMIT_COOLDOWN_SECONDS
            log.warning('[TEAM-FORTRESS] %s: pausing Steam reads for %d minutes',
                        e, RATE_LIMIT_COOLDOWN_SECONDS // 60)
        except Exception as e:
            summary['error'] = str(e)
            log.warning('[TEAM-FORTRESS] selling on %s: %s', account_name or steamid, e)
        with self._lock:
            self._state['inventories'][steamid] = {'at': now, 'sellable': summary['sellable'],
                                                   'listed': summary['listed'], 'error': summary['error'],
                                                   'not_tradable': summary['not_tradable']}
            cutoff = now - ATTEMPT_MEMORY_SECONDS
            self._state['attempted'] = {key: value for key, value in self._state['attempted'].items()
                                        if value.get('at', 0) >= cutoff}
        self._save()
        return summary

    def _record_sale(self, steamid, account_name, assetid, name, currency, buyer_pays, receives, error):
        with self._lock:
            self._state['sales'].append({'at': time.time(), 'steamid': steamid, 'account_name': account_name,
                                         'assetid': assetid, 'name': name, 'currency': currency,
                                         'buyer_pays': buyer_pays, 'receives': receives,
                                         'ok': error is None, 'error': error})
            self._state['sales'] = self._state['sales'][-SALES_HISTORY_CAP:]

    def _accounts_to_sell(self):
        """([(steamid, account name)], mode start) of the inventories watched right
        now: the accounts in Team Fortress 2 mode, and for a while after it stopped
        the accounts that were in it."""
        if self.asf is None or not self.asf.enabled:
            return [], None
        status = self.asf.team_fortress_status()
        recently = status.get('stopped_at') and time.time() - status['stopped_at'] < SELL_AFTER_STOP_SECONDS
        if not status['active'] and not recently:
            return [], status.get('since')
        return ([(row['steamid'], row['account_name']) for row in status['accounts']
                 if row['in_mode'] or (recently and row.get('chosen'))], status.get('since'))

    def sell_step(self):
        """Read the most overdue watched inventory (at most one per call)."""
        if not self._settings().get('team_fortress_auto_sell_enabled', True):
            return None
        if time.time() < self._cooldown_until or not self._sell_lock.acquire(blocking=False):
            return None
        try:
            now = time.time()
            with self._lock:
                seen = {steamid: (entry or {}).get('at', 0) for steamid, entry in self._state['inventories'].items()}
            accounts, since = self._accounts_to_sell()
            due = [(seen.get(steamid, 0), steamid, name) for steamid, name in accounts
                   if now - seen.get(steamid, 0) >= INVENTORY_INTERVAL_SECONDS]
            if not due:
                return None
            _, steamid, name = min(due)
            return self.sell_account(steamid, name, mode_since=since)
        finally:
            self._sell_lock.release()

    def sell_all_now(self, force=False):
        """Read every watched inventory now (the "Sell now" button). *force* also
        lists copies held before the mode started or listed before. Stops at the
        first HTTP 429."""
        with self._sell_lock:
            accounts, since = self._accounts_to_sell()
            results = []
            for steamid, name in accounts:
                if time.time() < self._cooldown_until:
                    break
                results.append(self.sell_account(steamid, name, force=force, mode_since=since))
            return results

    def start_sell_now(self, force=False):
        """Run sell_all_now in the background, unless a sweep runs or Steam
        rate-limited us: {"started": bool, "error"?}."""
        if time.time() < self._cooldown_until:
            return {'started': False, 'error': 'Steam rate-limited the last read: waiting '
                                               f'{int((self._cooldown_until - time.time()) // 60) + 1} more minutes'}
        if self._sell_lock.locked():
            return {'started': False, 'error': 'a sweep is already running'}
        threading.Thread(target=self.sell_all_now, kwargs={'force': force}, daemon=True,
                         name='team-fortress-sell-now').start()
        return {'started': True}

    def _auto_stop(self):
        """Stop Team Fortress 2 mode after team_fortress_auto_stop_hours (0 = never):
        a release at night must not pause card farming on every account for days."""
        hours = float(self._settings().get('team_fortress_auto_stop_hours') or 0)
        if hours <= 0 or self.asf is None or not self.asf.enabled:
            return False
        status = self.asf.team_fortress_status()
        if not status['active'] or time.time() - (status.get('since') or time.time()) < hours * 3600:
            return False
        self.asf.stop_team_fortress()
        self._notify(f'⏹️ Team Fortress 2: stopped playing after {hours:g} hours (card farming resumes)')
        return True

    # ---- background -----------------------------------------------------------------

    def start(self):
        threading.Thread(target=self._news_loop, daemon=True, name='team-fortress-news').start()
        threading.Thread(target=self._sell_loop, daemon=True, name='team-fortress-sell').start()
        log.info('[TEAM-FORTRESS] release watcher and auto-sell started')

    def _news_loop(self):
        while not self._stop.is_set():
            settings = self._settings()
            if settings.get('team_fortress_watch_enabled', True):
                try:
                    self.check_news()
                except Exception as e:
                    log.warning('[TEAM-FORTRESS] news poll error: %s', e)
            minutes = max(1, int(settings.get('team_fortress_poll_minutes') or 2))
            self._stop.wait(minutes * 60)

    def _sell_loop(self):
        self._stop.wait(30)
        while not self._stop.is_set():
            try:
                self._auto_stop()
                self.sell_step()
            except Exception as e:
                log.warning('[TEAM-FORTRESS] sell loop error: %s', e)
            self._stop.wait(SELL_LOOP_SECONDS)

    def stop(self):
        self._stop.set()

    def status(self):
        settings = self._settings()
        with self._lock:
            state = json.loads(json.dumps(self._state))
        play = self.asf.team_fortress_status() if self.asf is not None and self.asf.enabled else None
        return {
            'watch': {'enabled': bool(settings.get('team_fortress_watch_enabled', True)),
                      'poll_minutes': max(1, int(settings.get('team_fortress_poll_minutes') or 2)),
                      'checked_at': self._news_checked_at, 'error': self._news_error,
                      'baseline_date': state['news'].get('last_date'),
                      'cases': state['cases'][::-1],
                      'can_ring': bool(self.telegram_caller and self.telegram_caller.status().get('configured'))},
            'play': play,
            'sell': {'enabled': bool(settings.get('team_fortress_auto_sell_enabled', True)),
                     'rate_limited_until': self._cooldown_until if self._cooldown_until > time.time() else None,
                     'items': list(settings.get('team_fortress_sell_items') or []),
                     'sales': state['sales'][-50:][::-1],
                     'inventories': state['inventories']},
            'settings': {key: settings.get(key) for key in (
                'team_fortress_watch_enabled', 'team_fortress_play_on_release', 'team_fortress_ring_on_release',
                'team_fortress_auto_sell_enabled', 'team_fortress_sell_items', 'team_fortress_accounts',
                'team_fortress_poll_minutes', 'team_fortress_chat_id', 'team_fortress_auto_stop_hours')},
        }
