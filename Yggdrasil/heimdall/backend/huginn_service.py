import copy
import email.utils
import json
import logging
import os
import socket
import ssl
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from jsonio import atomic_write_json
from notifications import (TELEGRAM_MESSAGE_LIMIT, delete_notification, edit_notification,
                           notification_channel, send_notification, telegram_visible_length)

logger = logging.getLogger(__name__)  # 'log' is used locally for the auction log

# Only the inventory scan is cached to disk (it's expensive to produce). Arbitrage
# prices are fetched live on demand and held in the browser session, never cached.
CACHE_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_scan.json')

# CSFloat buy-order (autobuy) prices for owned items ARE cached to disk — fetching
# them is a slow, throttled sweep (~2 API calls per item), so it runs as a background
# job and the result is reused by every "=> CSFloat (autobuy)" profile until refreshed.
CSFLOAT_BUYORDERS_CACHE = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_csfloat_buyorders.json')
# The items you hold, keyed by market_hash_name (the name every market shares), each
# with the CSFloat listing whose buy orders the sweep reads:
# {name: {listing_id, link, checked_at}}. Rebuilt from every inventory scan (plus
# Draupnir holdings): items you no longer hold are dropped, new ones added. A listing
# is someone's copy for sale, so it goes stale when sold; the sweep then finds a new
# one and writes it back.
CSFLOAT_ITEM_LINKS_FILE = os.path.join(os.path.dirname(__file__), 'cache', 'csfloat_item_links.json')
_CSFLOAT_ITEM_URL = 'https://csfloat.com/item/{}'

# Bundled catalog of tradeable containers (cases + sticker/souvenir/autograph capsules)
# used by the "Case Arbitrage" tracker. Regenerated from the community CSGO-API.
CONTAINERS_FILE = os.path.join(os.path.dirname(__file__), 'cases_containers.json')
# Daily cheapest-price snapshots per container, for trend arrows / sparklines. Cached
# to disk (cheap to keep) and pruned to the most recent N days on every save.
CASE_HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'cache', 'case_price_history.json')
# Which buy-market-cheaper-than-CSFloat alerts are currently active, so we only
# notify on NEW crossings instead of every hourly run.
CASE_ALERT_STATE_FILE = os.path.join(os.path.dirname(__file__), 'cache', 'case_alert_state.json')
# LOOT.Farm auction tracker: per-lot trajectory (base, start/last price, bids, a Steam
# resale reference at capture, cleared-detection) accumulated over time for the backtest.
LOOTFARM_AUCTION_LOG = os.path.join(os.path.dirname(__file__), 'cache', 'lootfarm_auction_log.json')
# Last full container refresh (per-market snapshots + when the full pull ran), so a
# werkzeug reload does not trigger a fresh six-market pulse pull straight away.
CONTAINER_SNAPSHOT_FILE = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_container_snapshots.json')
# atomic_write_json leaves '.tmp-*.json' behind only when the process is killed
# mid-write; ones older than this are swept from cache/ on boot.
_STALE_TEMPORARY_FILE_AGE_SEC = 60 * 60

# A background warm (portfolio valuation map, container snapshot) that failed is
# not retried for this long; requests in that window get status 'error' so the
# frontend stops polling instead of restarting a doomed pulse pull every call.
_WARM_RETRY_AFTER_FAILURE_SEC = 120

_CSFLOAT_API_BASE = 'https://csfloat.com/api/v1'
_CSFLOAT_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')
# Human-editable pool of CSFloat API keys (labels + keys). Read fresh on every sweep,
# so you can add/remove keys without a restart. Gitignored (secrets).
CSFLOAT_KEYS_FILE = os.path.join(os.path.dirname(__file__), 'csfloat_keys.json')
# Per-key cooldown state (strikes + until-when benched). Lives in cache/ (gitignored)
# so it survives restarts and is separate from the user-owned keys file.
CSFLOAT_KEY_STATE_FILE = os.path.join(os.path.dirname(__file__), 'cache', 'csfloat_key_state.json')
# A rate-limited key is benched until CSFloat says it may retry (Retry-After /
# X-RateLimit-Reset on the 429), or else for this long. Measured 2026-09-28..30: the
# quota (about 400 requests an hour across the keys) comes back about an hour after it
# runs out; the old 10 → 20 → 30 minute steps only produced refused retries.
_CSFLOAT_LIMIT_WAIT_SECONDS = 60 * 60
_CSFLOAT_LIMIT_WAIT_MINIMUM_SECONDS = 60
_CSFLOAT_LIMIT_WAIT_MAXIMUM_SECONDS = 3 * 60 * 60
# After this many items in a row whose direct by-name lookup was blocked, the sweep
# stops trying it (direct access is blocked; listings can still go through the proxy).
_CSFLOAT_NAME_LOOKUP_BLOCKED_LIMIT = 3
# When EVERY key is cooling, the sweep waits out the soonest cooldown and auto-resumes.
# This caps how many such waits it will sit through before pausing for a manual resume.
_CSFLOAT_MAX_AUTO_WAITS = 12

# Seconds between CSFloat API calls. CSFloat rate-limits (429) — stay gentle.
_CSFLOAT_REQUEST_DELAY = 1.2
# If a sweep was interrupted (rate-limited) less than this ago, a new run resumes
# from where it stopped instead of starting over. Older than this → fresh sweep.
_CSFLOAT_RESUME_WINDOW_SEC = 2 * 60 * 60
# Checkpoint the partial cache to disk every N processed items so progress survives
# a crash / restart and can always be resumed.
_CSFLOAT_CHECKPOINT_EVERY = 25
# If the first N items in a row all fail to CONNECT (direct IP-blocked + proxy down)
# and nothing has priced yet, abort the sweep with a clear error instead of grinding
# through hundreds of unreachable items. A resume retries once the route is healthy.
_CSFLOAT_ABORT_AFTER_UNREACHABLE = 8


class _CSFloatRateLimited(Exception):
    """Raised when CSFloat rate-limits a KEY (HTTP 429). The sweep benches the key
    and retries the item on another key; if all keys are cooling it pauses (resumable).
    `retry_after` is the seconds CSFloat asked us to wait, when it said so."""

    def __init__(self, message='', retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after_seconds(headers):
    """Seconds to wait from a 429's Retry-After (seconds or an HTTP date) or
    X-RateLimit-Reset (an epoch time or seconds), or None when neither is usable."""
    if not headers:
        return None
    value = headers.get('Retry-After')
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(value)
                return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
    value = headers.get('X-RateLimit-Reset') or headers.get('x-ratelimit-reset')
    if value:
        try:
            number = float(value)
        except ValueError:
            return None
        # A large number is an epoch timestamp, a small one a number of seconds.
        return max(0.0, number - time.time()) if number > 1e9 else max(0.0, number)
    return None


class _CSFloatNameLookupUnsupported(Exception):
    """CSFloat's buy-orders-by-name endpoint answered in a way we do not understand
    (moved, changed, or refused): the sweep switches to the listing method."""


class _CSFloatUnavailable(Exception):
    """Raised when a request can't get through for a non-key reason (a proxy exit IP
    kept getting bot-challenged / 403 across retries). The sweep just skips this item
    and keeps going — it does NOT bench the key."""


def classify_proxy_error(text):
    """Turn a raw connection-error string into an explicit, actionable hint for the UI.

    Bright Data's most common failure is `407 Auth Failed (code: ip_forbidden)` — the
    machine's public IP is not on the zone's allowlist. That is a config problem the
    user must fix, so we surface it plainly rather than as a generic "connection
    failed". Returns {} when nothing specific is recognised.
    """
    t = (text or '').lower()
    if 'ip_forbidden' in t:
        return {
            'code': 'ip_forbidden',
            'hint': ("Bright Data rejected this server's IP (ip_forbidden). Add your current "
                     "public IP to the proxy zone's allowlist in the Bright Data dashboard, "
                     "or update the proxy in csfloat_keys.json."),
        }
    if '407' in t or 'auth failed' in t or 'proxy auth' in t:
        return {
            'code': 'proxy_auth_failed',
            'hint': ("Bright Data proxy authentication failed (HTTP 407). Check the proxy "
                     "username / password / zone in csfloat_keys.json."),
        }
    return {}


_PUBLIC_IP_CACHE = {'ip': None, 'at': 0.0}
_PUBLIC_IP_TTL_SEC = 300


def _looks_like_ip(s):
    if not s or len(s) > 45:
        return False
    if ':' in s:                      # crude IPv6 acceptance
        return all(c in '0123456789abcdefABCDEF:' for c in s)
    parts = s.split('.')              # IPv4
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def detect_public_ip(timeout=6, force=False):
    """Best-effort public egress IP of this server — the address Bright Data (and any
    remote) actually sees connecting, which is what must be whitelisted in the proxy
    zone. Cached for a few minutes so it is safe to call from status polls. Returns the
    IP string, or None if every echo service is unreachable."""
    now = time.time()
    if not force and _PUBLIC_IP_CACHE['ip'] and now - _PUBLIC_IP_CACHE['at'] < _PUBLIC_IP_TTL_SEC:
        return _PUBLIC_IP_CACHE['ip']
    for url in ('https://api.ipify.org', 'https://checkip.amazonaws.com', 'https://ifconfig.me/ip'):
        try:
            ip = urllib.request.urlopen(url, timeout=timeout).read().decode('utf-8', 'replace').strip()
            if _looks_like_ip(ip):
                _PUBLIC_IP_CACHE.update({'ip': ip, 'at': now})
                return ip
        except Exception:
            continue
    return None


def _proxy_with_session(proxy, sid):
    """Inject a Bright Data '-session-<sid>' into the proxy username so this request
    gets its own exit IP (rotating sessions = new IP per session id)."""
    if not proxy or '@' not in proxy or '://' not in proxy:
        return proxy
    scheme, rest = proxy.split('://', 1)
    creds, host = rest.rsplit('@', 1)
    if ':' in creds:
        user, pwd = creds.split(':', 1)
        return f'{scheme}://{user}-session-{sid}:{pwd}@{host}'
    return f'{scheme}://{creds}-session-{sid}@{host}'


def load_csfloat_keys():
    """Read the editable key pool. Returns a list of {'label','key'} (may be empty)."""
    if not os.path.exists(CSFLOAT_KEYS_FILE):
        return []
    try:
        with open(CSFLOAT_KEYS_FILE) as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f'[HUGINN] Could not read {CSFLOAT_KEYS_FILE}: {e}')
        return []
    out = []
    for entry in (data.get('keys') or []):
        key = (entry.get('key') or '').strip()
        if key:
            out.append({'label': entry.get('label') or key[:6], 'key': key})
    return out


def load_csfloat_proxy():
    """Optional proxy URL for CSFloat sweep requests (http://user:pass@host:port).

    Read from the same editable keys file; empty/missing means go direct. Only the
    CSFloat buy-order sweep uses it — pulse and everything else stay direct."""
    if not os.path.exists(CSFLOAT_KEYS_FILE):
        return ''
    try:
        with open(CSFLOAT_KEYS_FILE) as f:
            return ((json.load(f).get('proxy')) or '').strip()
    except Exception:
        return ''


class CSFloatKeyManager:
    """Rotates CSFloat API keys and benches ones that get rate-limited.

    A key that trips a rate limit is put on cooldown for 10 min × its strike count
    (10, 20, 30, … min for repeat offences), then auto-returns to the rotation. A
    clean call after its cooldown expired forgives the strikes. State is persisted
    to cache/ so it survives restarts and can be shown in the UI."""

    def __init__(self, state_path=CSFLOAT_KEY_STATE_FILE):
        self.state_path = state_path
        self._lock = threading.Lock()
        self._state = self._load()   # key -> {'strikes': int, 'cooldown_until': epoch}
        self._rr = 0

    def _load(self):
        if not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        try:
            atomic_write_json(self.state_path, self._state, indent=None)
        except Exception as e:
            logger.error(f'[HUGINN] Could not persist CSFloat key state: {e}')

    def _remaining(self, key):
        s = self._state.get(key)
        if not s:
            return 0
        return max(0, s.get('cooldown_until', 0) - time.time())

    def mark_limited(self, key, retry_after=None):
        """Bench a rate-limited key until CSFloat's own retry time, or for
        _CSFLOAT_LIMIT_WAIT_SECONDS when it gave none (clamped to a sane range)."""
        wait = retry_after if retry_after is not None else _CSFLOAT_LIMIT_WAIT_SECONDS
        wait = min(_CSFLOAT_LIMIT_WAIT_MAXIMUM_SECONDS, max(_CSFLOAT_LIMIT_WAIT_MINIMUM_SECONDS, wait))
        with self._lock:
            s = self._state.setdefault(key, {'strikes': 0, 'cooldown_until': 0})
            s['strikes'] += 1
            s['cooldown_until'] = time.time() + wait
            self._save()
            source = 'as CSFloat asked' if retry_after is not None else 'default'
            logger.info(f'[HUGINN] CSFloat key …{key[-6:]} benched {int(wait // 60)}m ({source}, '
                        f'strike {s["strikes"]})')

    def mark_ok(self, key):
        with self._lock:
            s = self._state.get(key)
            if s and s.get('strikes') and self._remaining(key) <= 0:
                s['strikes'] = 0            # forgiven after a clean, out-of-cooldown call
                s['cooldown_until'] = 0
                self._save()

    def next_key(self, keys):
        """Round-robin the next non-cooling key, or None if all are benched."""
        with self._lock:
            available = [k for k in keys if self._remaining(k) <= 0]
            if not available:
                return None
            self._rr = (self._rr + 1) % len(available)
            return available[self._rr]

    def min_cooldown_remaining(self, keys):
        """Seconds until the soonest-freeing key is available again (0 if any is now)."""
        with self._lock:
            remaining = [self._remaining(k) for k in keys]
            cooling = [r for r in remaining if r > 0]
            return min(cooling) if cooling else 0

    def status(self, key_pairs):
        with self._lock:
            out = []
            for kp in key_pairs:
                rem = self._remaining(kp['key'])
                out.append({
                    'label': kp['label'],
                    'cooling': rem > 0,
                    'cooldown_remaining': int(rem),
                    'strikes': (self._state.get(kp['key']) or {}).get('strikes', 0),
                })
            return out

_TRADEON_STEAM_URL    = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/Steam/all'
_TRADEON_BUFF_URL     = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/Buff/all'
_TRADEON_CSFLOAT_URL  = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/CsFloat/all'
_TRADEON_LISSKINS_URL = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/LisSkins/all'
_TRADEON_DMARKET_URL  = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/Dmarket/all'

# LOOT.Farm publishes its own price + limit feed (first-party, refreshed ~every
# minute). We read the LootFarm leg straight from here instead of pulse; only the
# buy side (TradeOnMarket min) still comes from pulse. Feed prices are INTEGER CENTS,
# gross — i.e. before LOOT.Farm's 3–5% acceptance fee — for the trade-locked variant
# of the item (unlocked items are worth +3%, which we don't add: base price only).
_LOOTFARM_FEEDS = {
    'cs':   'https://loot.farm/fullprice.json',
    'rust': 'https://loot.farm/fullpriceRUST.json',
    'tf2':  'https://loot.farm/fullpriceTF2.json',
}
_LOOTFARM_TTL = 60   # seconds; feed changes at most once a minute
_LOOTFARM_AUCTION_URL = 'https://loot.farm/botsInventory_Auctions.json'
_LOOTFARM_AUCTION_TTL = 45   # seconds

# Sell fees (net proceeds = price * (1 - fee)) live in ONE place: the market registry
# below (defaults) overridden by the Fees editor (settings huginn_market_fees), read
# through HuginnService.market_fee. Do not add per-feature fee constants.
_TRADEON_STEAM_BODY = {
    "templateId": None,
    "firstMarketOptions": {
        "firstMarketPriceType": "Sell",
        "firstMarketPriceFilter": {"minValue": 0.13, "maxValue": None},
        "firstMarketCountFilter": {"minValue": None, "maxValue": None},
        "updateTimeFilter": {"minTime": None, "maxTime": None},
    },
    "secondMarketOptions": {
        "secondMarketPriceType": "Buy",
        "secondMarketPriceFilter": {"minValue": None, "maxValue": None},
        "secondMarketCountFilter": {"minValue": None, "maxValue": None},
        "updateTimeFilter": {"minTime": None, "maxTime": None},
    },
    "marketHashNameFilter": None,
    "profitFilter": None,
    "profitPercentFilter": {"minValue": None, "maxValue": None},
    "counterStrikeItemTypeOptions": {
        "itemTypes": None, "itemQualities": None, "isStatTrack": None,
        "isSouvenir": None, "isSticker": None, "isGraffiti": None,
        "indicationOptions": {"isEnabled": True, "colorIndicators": [
            {"isEnabled": False, "profitPercent": 35, "color": "Green"},
            {"isEnabled": False, "profitPercent": 36, "color": "Blue"},
            {"isEnabled": False, "profitPercent": 37, "color": "Red"},
            {"isEnabled": False, "profitPercent": 38, "color": "Orange"},
            {"isEnabled": False, "profitPercent": 39, "color": "Purple"},
        ]},
        "isOverstock": None, "displaySoldOutItems": False,
        "displayOnlyOverridenItems": False, "firstMarketTime": None,
        "secondMarketTime": None, "holdOptions": None,
    },
    "dotaItemTypeOptions": {
        "itemTypes": None, "itemQualities": None, "isStatTrack": None,
        "isSouvenir": None, "isSticker": None, "isGraffiti": None,
        "indicationOptions": {"isEnabled": True, "colorIndicators": [
            {"isEnabled": False, "profitPercent": 35, "color": "Green"},
            {"isEnabled": False, "profitPercent": 36, "color": "Blue"},
            {"isEnabled": False, "profitPercent": 37, "color": "Red"},
            {"isEnabled": False, "profitPercent": 38, "color": "Orange"},
            {"isEnabled": False, "profitPercent": 39, "color": "Purple"},
        ]},
        "isOverstock": None, "displaySoldOutItems": False,
        "displayOnlyOverridenItems": False, "firstMarketTime": None,
        "secondMarketTime": None, "holdOptions": None,
    },
    "rustItemTypeOptions": {
        "itemTypes": None, "itemQualities": None, "isStatTrack": None,
        "isSouvenir": None, "isSticker": None, "isGraffiti": None,
        "indicationOptions": {"isEnabled": True, "colorIndicators": [
            {"isEnabled": False, "profitPercent": 35, "color": "Green"},
            {"isEnabled": False, "profitPercent": 36, "color": "Blue"},
            {"isEnabled": False, "profitPercent": 37, "color": "Red"},
            {"isEnabled": False, "profitPercent": 38, "color": "Orange"},
            {"isEnabled": False, "profitPercent": 39, "color": "Purple"},
        ]},
        "isOverstock": None, "displaySoldOutItems": False,
        "displayOnlyOverridenItems": False, "firstMarketTime": None,
        "secondMarketTime": None, "holdOptions": None,
    },
    "rarityFilter": None,
    "salesCountPeriod": "Week",
    "salesCountFilters": [],
    "holdFilter": None,
    "isOverstock": None,
    "displaySoldOutItems": False,
    "displayOnlyOverridenItems": False,
    "countFilterMode": "TotalOffersCount",
    "glowOldListItems": True,
    "paginationRequest": {
        "orderParameters": {"key": "profitPercent", "sortOrder": "Descending"},
        "skipCount": 0,
        "takeCount": 9999999,
    },
}

# Buy-side queries: same body, but the second market is priced as its own sell price
# (the min you'd pay to buy). These come back un-paywalled even for paid markets.
_TRADEON_LISSKINS_BODY = copy.deepcopy(_TRADEON_STEAM_BODY)
_TRADEON_LISSKINS_BODY["secondMarketOptions"]["secondMarketPriceType"] = "SellWithoutHold"

_TRADEON_BUFF_BUY_BODY = copy.deepcopy(_TRADEON_STEAM_BODY)
_TRADEON_BUFF_BUY_BODY["secondMarketOptions"]["secondMarketPriceType"] = "Sell"

# DMarket as a buy source: its second market is priced as its own lowest listing
# (Sell) — the min you'd pay to buy there.
_TRADEON_DMARKET_BUY_BODY = copy.deepcopy(_TRADEON_STEAM_BODY)
_TRADEON_DMARKET_BUY_BODY["secondMarketOptions"]["secondMarketPriceType"] = "Sell"

# Tradeon (min) => CSFloat (min): CSFloat has no autobuy, so the sell side is its
# lowest listing (Sell), not a buy order. Direct pulse query — pulse returns the
# profit itself, so this passes through like the other Tradeon-first profiles.
_TRADEON_CSFLOAT_BODY = copy.deepcopy(_TRADEON_STEAM_BODY)
_TRADEON_CSFLOAT_BODY["secondMarketOptions"]["secondMarketPriceType"] = "Sell"

# --- Generated arbitrage pairs (data-driven market registry) -----------------
# Every market is reached the free way: as the secondMarket under TradeOnMarket
# (TradeOnMarket/{id}). Direct {A}/{B} pulse tables are paywalled to items under
# ~$2, so we never use them — a pair A->B is synthesised by joining TradeOnMarket/A
# (A's min listing = what you'd pay) with TradeOnMarket/B (B's autobuy or min = what
# you'd get), exactly like the hand-written cross-pairs above. Each market carries:
#   id        pulse enum identifier (URL path segment)
#   display   name shown in the UI
#   buy_type  secondMarketPriceType for its MIN listing (the buy leg)
#   autobuy   secondMarketPriceType for its buy-order (instant-sell leg), or None
#   fee       default sell-side fee netted from proceeds; editable in settings, and
#             0.0 where unconfirmed (profit is then an upper bound — see feeKnown)
#   premium   pulse gates this market as a *direct* first-market (info only; we always
#             go through TradeOnMarket, so it never blocks a pair)
# Confirmed live against pulse on 2026-09-05 ('BuffMarket', Buff163's international
# site, on 2026-10-10). 'Youpine' is Youpin898's real id;
# C5Game's identifier could not be resolved and is intentionally left out.
_MARKET_REGISTRY = [
    {'id': 'TradeOnMarket',  'display': 'Tradeon',           'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'LisSkins',       'display': 'LisSkins',          'buy_type': 'SellWithoutHold', 'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'Buff',           'display': 'Buff163',           'buy_type': 'Sell',            'autobuy': 'Buy', 'fee': 0.015, 'premium': True},
    {'id': 'BuffMarket',     'display': 'Buff.market',       'buy_type': 'Sell',            'autobuy': 'Buy', 'fee': 0.0,   'premium': False},
    {'id': 'CsFloat',        'display': 'CSFloat',           'buy_type': 'Sell',            'autobuy': None,  'fee': 0.02,  'premium': True},
    {'id': 'Dmarket',        'display': 'DMarket',           'buy_type': 'Sell',            'autobuy': 'Buy', 'fee': 0.0,   'premium': False},
    {'id': 'Steam',          'display': 'Steam',             'buy_type': 'Sell',            'autobuy': 'Buy', 'fee': 0.13,  'premium': False},
    {'id': 'LootFarm',       'display': 'LOOT.Farm',         'buy_type': 'Sell',            'autobuy': 'Buy', 'fee': 0.05,  'premium': False},
    {'id': 'CsMoneyTrade',   'display': 'CSMoney (Trade)',   'buy_type': 'Sell',            'autobuy': 'Buy', 'fee': 0.0,   'premium': False},
    {'id': 'CsMoneyMarket',  'display': 'CSMoney (Market)',  'buy_type': 'Sell',            'autobuy': 'Buy', 'fee': 0.0,   'premium': False},
    {'id': 'TradeItStore',   'display': 'TradeIt (Store)',   'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'TradeItTrade',   'display': 'TradeIt (Trade)',   'buy_type': 'Sell',            'autobuy': 'Buy', 'fee': 0.0,   'premium': False},
    {'id': 'Tm',             'display': 'Market.CSGO',       'buy_type': 'Sell',            'autobuy': 'Buy', 'fee': 0.0,   'premium': False},
    {'id': 'WhiteMarket',    'display': 'WhiteMarket',       'buy_type': 'Sell',            'autobuy': 'Buy', 'fee': 0.0,   'premium': True},
    {'id': 'Skinport',       'display': 'Skinport',          'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'SkinVault',      'display': 'SkinVault',         'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'AimMarket',      'display': 'AimMarket',         'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': True},
    {'id': 'Haloskins',      'display': 'Haloskins',         'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': True},
    {'id': 'AvanMarket',     'display': 'AvanMarket',        'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': True},
    {'id': 'DupeFi',         'display': 'Dupe.fi',           'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'SkinPlace',      'display': 'SkinPlace',         'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'SkinsMonkey',    'display': 'SkinsMonkey',       'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'CsTradeTrade',   'display': 'CS.Trade (Trade)',  'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'CsTradeMarket',  'display': 'CS.Trade (Market)', 'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'CsDeals',        'display': 'CS.Deals',          'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'Skinout',        'display': 'Skinout',           'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': True},
    {'id': 'SkinSwapMarket', 'display': 'SkinSwap',          'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
    {'id': 'Youpine',        'display': 'Youpin898',         'buy_type': 'Sell',            'autobuy': None,  'fee': 0.0,   'premium': False},
]
_MARKET_BY_ID = {m['id']: m for m in _MARKET_REGISTRY}
# Markets whose sell-side fee is confirmed; the rest default to 0 (flagged in the UI).
_MARKET_FEE_CONFIRMED = {'Steam', 'Buff', 'CsFloat', 'Dmarket', 'LootFarm'}
# CSFloat has no pulse buy orders, but it DOES have an autobuy — its highest buy
# order, gathered by the CSFloat API sweep (see the buy-orders panel). So every buy
# market can also sell into CSFloat autobuy, sourced from that swept cache, not pulse.
_AUTOBUY_VIA_CSFLOAT_SWEEP = {'CsFloat'}
_TRADEON_TABLE_URL = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/{}/all'
_MARKET_PULL_TTL = 30   # seconds; reuse a raw TradeOnMarket/{id} pull across generated pairs


class HuginnService:
    def __init__(self, steam_service, ratatoskr_service):
        self.steam = steam_service
        self.rat = ratatoskr_service
        self.csfloat_keys = CSFloatKeyManager()
        self._proxy_seq = 0   # increments per proxied attempt → fresh Bright Data session/IP
        self._price_cache = {}   # market -> (fetched_at_epoch, {name: price}) for portfolio valuation
        self._price_state = {}   # market -> 'refreshing' | 'ok' | 'error'
        self._price_failed_at = {}   # market -> epoch of the last failed background refresh
        self._price_lock = threading.Lock()
        self._csfloat_links_lock = threading.Lock()   # guards csfloat_item_links.json writes
        # Returns the current settings; app.py points it at SettingsManager.get_settings,
        # so every fee below comes from the one Fees editor (huginn_market_fees).
        self.settings_provider = None
        self._market_pull_cache = {}   # (market_id, price_type) -> (fetched_at, items) for generated pairs
        self._market_pull_lock = threading.Lock()
        # Parsed-file caches keyed by (modification time, size): the 16 MB inventory
        # scan and the ~3 MB case price history are re-read only when the file changes.
        self._scan_file_cache = None      # ((mtime_ns, size), parsed scan)
        self._case_history_cache = None   # ((mtime_ns, size), parsed history)
        # Serialises case-history load/modify/save (loop + requests both record) and
        # the whole case-alert run (loop + "Check now" both read and write its state).
        self._case_history_lock = threading.RLock()
        self._case_alert_lock = threading.Lock()
        # Bumped on every _price_cache write so known_item_names() (hit per typeahead
        # keystroke) can memoize its unioned name set instead of rebuilding it each call.
        self._price_cache_gen = 0
        self._known_names_cache = None   # (gen, set) — non-empty results only
        # Container tracker snapshots — market -> (epoch, {name: {price, count}}). Kept
        # separate from _price_cache because it also carries listing counts (liquidity)
        # and is filtered to the container catalog only. Same non-blocking warm pattern.
        self._lootfarm_cache = {}   # game -> (fetched_at_epoch, {name: feed_row})
        self._auction_cache = None  # (fetched_at_epoch, feed_dict)
        self._buyorder_cache = {}   # market -> (epoch, {name: buy_order_price}) for the LF arb board
        self._auction_lock = threading.Lock()
        self._auction_tracker_started = False
        self._container_cache = {}
        self._container_state = {}
        self._container_failed_at = {}   # market -> epoch of the last failed snapshot refresh
        self._container_last_full = 0.0  # epoch of the last full refresh (restored from disk on start)
        self._container_refresh_started = False
        # Bundled container catalog is immutable at runtime → parse once, cache the
        # full list and the derived name set (both read-only to callers).
        self._containers_all = None
        self._container_names_set = None

    def _ensure_session(self, steam_id, account_data, opened=None):
        """True when the account has a Ratatoskr session. A session this call had to
        open is added to `opened` (so a scan can log it out again afterwards)."""
        account_name = account_data.get('account_name')
        if self.rat.get_status(steam_id).get('status') == 'connected':
            # A live session is itself proof the login works — stamp it green so a
            # scan over already-connected accounts still refreshes their health.
            self._record_login(account_name, True)
            return True
        password = self.steam.get_password(steam_id)
        if not password:
            self._record_login(account_name, False, 'no password in vault')
            return False
        result = self.rat.login(
            account_name=account_name,
            password=password,
            shared_secret=account_data.get('shared_secret'),
        )
        ok = 'error' not in result
        self._record_login(account_name, ok, result.get('error'))
        if ok and opened is not None and not result.get('reused'):
            opened.append(steam_id)
        return ok

    @staticmethod
    def _record_login(account_name, ok, error=None):
        """Best-effort stamp of a login outcome onto the Mimir credential."""
        if not account_name:
            return
        from context import ctx
        if ctx.mimir_service:
            ctx.mimir_service.record_login_result(account_name, ok, error)

    def _ingest(self, by_hash, items, account_name, steam_id, location, storage_unit=None):
        for item in items:
            mhn = item.get('item_name', '')
            if not mhn:
                continue
            if mhn not in by_hash:
                by_hash[mhn] = {'count': 0, 'instances': []}
            by_hash[mhn]['count'] += 1
            by_hash[mhn]['instances'].append({
                'account_name': account_name,
                'steam_id': steam_id,
                'item_id': str(item.get('item_id', '')),
                'float': item.get('item_paint_wear'),
                'location': location,
                'storage_unit': storage_unit,
                'on_trade_hold': item.get('trade_unlock') is not None,
                'stickers': [s['sticker_name'] for s in (item.get('stickers') or []) if s.get('sticker_name')],
                'collection': item.get('item_collection', ''),
            })

    def scan(self):
        """Read every account's inventory and Storage Units into the scan cache.
        Sessions the scan had to open are logged out at the end: a Ratatoskr
        session "plays" Counter-Strike 2, which pauses ASF card farming on that
        account; sessions that were already open are left as they were."""
        opened = []
        try:
            return self._scan_accounts(opened)
        finally:
            for steam_id in opened:
                try:
                    moves = self.rat.get_move_status(steam_id) or {}
                    if moves.get('running') or moves.get('pending'):
                        # Something started moving items on this session meanwhile.
                        logger.info(f'[HUGINN] {steam_id} left logged in after the scan: items are moving')
                        continue
                    result = self.rat.disconnect(steam_id) or {}
                    if result.get('error'):
                        logger.warning(f'[HUGINN] Could not log {steam_id} out after the scan: {result["error"]}')
                except Exception as e:
                    logger.warning(f'[HUGINN] Could not log {steam_id} out after the scan: {e}')

    def _scan_accounts(self, opened):
        accounts = self.steam.get_all_accounts_data()
        by_hash = {}
        scanned = 0

        for account in accounts:
            steam_id = str(account['steamid'])
            account_name = account.get('account_name', steam_id)
            account_data = self.steam.get_account(steam_id) or {}

            if not self._ensure_session(steam_id, account_data, opened):
                logger.warning(f'[HUGINN] Skipping {account_name} — no session')
                continue

            logger.info(f'[HUGINN] Scanning {account_name}…')
            scanned += 1

            inv = self.rat.get_inventory(steam_id)
            inv_items = [i for i in (inv.get('items') or []) if i.get('def_index') != 1201]
            self._ingest(by_hash, inv_items, account_name, steam_id, 'Inventory')

            caskets_resp = self.rat.get_caskets(steam_id)
            caskets = [c for c in (caskets_resp.get('caskets') or []) if (c.get('item_storage_total') or 0) > 0]

            for i, casket in enumerate(caskets):
                unit_name = casket.get('item_customname') or casket.get('item_name') or 'Storage Unit'
                contents = self.rat.get_casket_contents(steam_id, casket['item_id'])
                self._ingest(by_hash, contents.get('items') or [], account_name, steam_id, 'Storage Unit', unit_name)
                if i < len(caskets) - 1:
                    time.sleep(0.35)

        if accounts and not scanned:
            # Every login failed (no network after waking, Ratatoskr down, …): keep the
            # last good scan instead of replacing it with an empty inventory.
            raise RuntimeError(f'No account could be scanned ({len(accounts)} skipped); '
                               f'the previous scan is kept')
        result = {
            'scan_timestamp': datetime.now(timezone.utc).isoformat(),
            'total_items': sum(v['count'] for v in by_hash.values()),
            'by_hash': by_hash,
            'accounts_scanned': scanned,
            'accounts_skipped': len(accounts) - scanned,
        }

        atomic_write_json(CACHE_PATH, result, indent=None)

        return result

    def get_cache(self):
        """The last inventory scan, or None. The file is ~16 MB, so the parsed result
        is kept in memory and re-parsed only when the file's modification time or
        size changes. The returned dict is SHARED: callers must treat it as
        read-only (every current caller only reads it)."""
        try:
            stat = os.stat(CACHE_PATH)
        except OSError:
            return None
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = self._scan_file_cache
        if cached is not None and cached[0] == signature:
            return cached[1]
        with open(CACHE_PATH) as f:
            data = json.load(f)
        self._scan_file_cache = (signature, data)
        return data

    def _post_tradeon(self, url, token, body=None):
        """POST a tradeon table query and return the list of items (no caching)."""
        body_bytes = json.dumps(body or _TRADEON_STEAM_BODY).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=body_bytes,
            method='POST',
            headers={
                'Accept': '*/*',
                'Accept-Language': 'ru',
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Device-Id': '5c28d24a632e4c88f73c84a5e4aad23b',
                'Origin': 'https://pulse.tradeon.space',
                'Referer': 'https://pulse.tradeon.space/',
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
                'Usercurrency': 'USD',
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return next((data[k] for k in ('items', 'data', 'result', 'records') if isinstance(data.get(k), list)), data)
        return data

    def fetch_tradeon_steam(self, token):
        return self._post_tradeon(_TRADEON_STEAM_URL, token)

    def fetch_tradeon_buff(self, token):
        return self._post_tradeon(_TRADEON_BUFF_URL, token)

    def fetch_tradeon_csfloat(self, token):
        return self._post_tradeon(_TRADEON_CSFLOAT_URL, token, _TRADEON_CSFLOAT_BODY)

    def fetch_tradeon_dmarket(self, token):
        # Direct pulse profile: Tradeon min -> DMarket autobuy. DMarket takes no sales
        # fee, so pulse's profit already reflects the full autobuy price (no fee to net).
        return self._post_tradeon(_TRADEON_DMARKET_URL, token)

    def _fetch_lootfarm_feed(self, game='cs'):
        """{market_hash_name: feed_row} from LOOT.Farm's own price feed, cached for
        _LOOTFARM_TTL seconds. Each row: price (int cents, gross), have, max, rate,
        tr, res. Raises on network/parse failure so the route can report it."""
        now = time.time()
        cached = self._lootfarm_cache.get(game)
        if cached and now - cached[0] < _LOOTFARM_TTL:
            return cached[1]
        url = _LOOTFARM_FEEDS[game]
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        out = {}
        for row in (data or []):
            name = row.get('name')
            if name:
                out[name] = row
        self._lootfarm_cache[game] = (now, out)
        return out

    def fetch_tradeon_lootfarm(self, token, fee_pct=None):
        """Tradeon (min) buy -> LOOT.Farm sell, using LOOT.Farm's OWN feed for the
        sell side (price + overstock), first-party and fresh, rather than pulse.

        Buy side: TradeOnMarket lowest listing (the firstMarket of any pulse row).
        Sell side: LOOT.Farm feed price (cents -> USD) net of your acceptance fee
        (`fee_pct`; by default the LOOT.Farm fee set in the Fees editor). Overstock (have/max) comes straight from the feed,
        so the "Unstable"/Full column reflects LOOT.Farm's real limits. Items are
        joined on market_hash_name; only items present on both sides are returned."""
        if fee_pct is None:     # your LOOT.Farm fee from the Fees editor (percent)
            fee_pct = self.market_fee('LootFarm') * 100
        try:
            fee = float(fee_pct)
        except (TypeError, ValueError):
            fee = 5.0
        fee = min(max(fee, 0.0), 20.0)
        net_factor = 1.0 - fee / 100.0

        buy_items = self._post_tradeon(_TRADEON_STEAM_URL, token)   # firstMarket = TradeOnMarket min
        feed = self._fetch_lootfarm_feed('cs')

        combined = []
        for it in buy_items:
            name = (it.get('itemName') or {}).get('marketHashName')
            fm = it.get('firstMarket') or {}
            buy = fm.get('price')
            row = feed.get(name)
            if not name or not buy or row is None:
                continue
            gross = (row.get('price') or 0) / 100.0     # feed price is integer cents
            if not gross:
                continue
            sell_net = round(gross * net_factor, 4)
            profit = sell_net - buy
            have, mx = row.get('have'), row.get('max')
            scale = 100 if not mx else round((have or 0) / mx * 100)
            combined.append({
                'itemName': it.get('itemName'),
                'imageUrl': it.get('imageUrl'),
                'firstMarket': fm,                       # Buy @ TradeOnMarket
                'secondMarket': {                        # Sell @ LOOT.Farm (net of fee)
                    'price': sell_net,
                    'realPrice': sell_net,
                    'overstockInfo': {'limit': mx, 'currentCount': have, 'overstockScale': scale},
                    'rate': row.get('rate'),             # LOOT.Farm price as % of Steam (drives tier color)
                },
                'profit': round(profit, 3),
                'profitPercent': round(profit / buy * 100, 2) if buy else None,
            })

        combined.sort(key=lambda x: (x['profitPercent'] is not None, x['profitPercent'] or 0), reverse=True)
        return combined

    def fetch_lisskins_lootfarm(self, token, fee_pct=None):
        """LisSkins (min) buy → LOOT.Farm sell (autobuy), same shape as
        fetch_tradeon_lootfarm but sourcing the buy side from LisSkins' own listing
        (pulse secondMarket, un-paywalled) instead of TradeOnMarket. Sell side is the
        LOOT.Farm feed price net of your acceptance fee; overstock + tier come from the
        feed. Joined on market_hash_name."""
        if fee_pct is None:     # your LOOT.Farm fee from the Fees editor (percent)
            fee_pct = self.market_fee('LootFarm') * 100
        try:
            fee = float(fee_pct)
        except (TypeError, ValueError):
            fee = 5.0
        fee = min(max(fee, 0.0), 20.0)
        net_factor = 1.0 - fee / 100.0

        buy_items = self._post_tradeon(_TRADEON_LISSKINS_URL, token, _TRADEON_LISSKINS_BODY)
        feed = self._fetch_lootfarm_feed('cs')

        combined = []
        for it in buy_items:
            name = (it.get('itemName') or {}).get('marketHashName')
            buy_market = it.get('secondMarket') or {}      # LisSkins min listing
            buy = buy_market.get('price')
            row = feed.get(name)
            if not name or not buy or row is None:
                continue
            gross = (row.get('price') or 0) / 100.0
            if not gross:
                continue
            sell_net = round(gross * net_factor, 4)
            profit = sell_net - buy
            have, mx = row.get('have'), row.get('max')
            scale = 100 if not mx else round((have or 0) / mx * 100)
            combined.append({
                'itemName': it.get('itemName'),
                'imageUrl': it.get('imageUrl'),
                'firstMarket': buy_market,               # Buy @ LisSkins
                'secondMarket': {                        # Sell @ LOOT.Farm (net of fee)
                    'price': sell_net,
                    'realPrice': sell_net,
                    'overstockInfo': {'limit': mx, 'currentCount': have, 'overstockScale': scale},
                    'rate': row.get('rate'),
                },
                'profit': round(profit, 3),
                'profitPercent': round(profit / buy * 100, 2) if buy else None,
            })

        combined.sort(key=lambda x: (x['profitPercent'] is not None, x['profitPercent'] or 0), reverse=True)
        return combined

    # ---- LOOT.Farm auctions ------------------------------------------------

    def _fetch_auctions_raw(self):
        """Live LOOT.Farm CS2 auctions feed (public), cached _LOOTFARM_AUCTION_TTL s."""
        now = time.time()
        if self._auction_cache and now - self._auction_cache[0] < _LOOTFARM_AUCTION_TTL:
            return self._auction_cache[1]
        req = urllib.request.Request(_LOOTFARM_AUCTION_URL, headers={
            'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': 'https://loot.farm/',
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        self._auction_cache = (now, data)
        return data

    @staticmethod
    def _auction_lots(feed):
        """Flatten the auction feed into one dict per biddable lot (instance)."""
        lots = []
        for key, it in (feed.get('result') or {}).items():
            name, base, pg, ext = it.get('n'), it.get('p'), it.get('pg'), it.get('e')
            for grp in (it.get('u') or {}).values():
                for inst in grp:
                    lots.append({
                        'key': key, 'id': inst.get('id'), 'name': name, 'exterior': ext,
                        'base': base, 'pg': pg, 'current': inst.get('current'),
                        'bets': inst.get('bets') or 0, 'tradelock': bool(inst.get('tr')),
                    })
        return lots

    def _pulse_steam_tradeon(self, token):
        """{name: steam_autobuy_price}, {name: tradeon_min_price} from one pulse pull."""
        steam, tradeon = {}, {}
        try:
            for it in self._post_tradeon(_TRADEON_STEAM_URL, token):
                nm = (it.get('itemName') or {}).get('marketHashName')
                if not nm:
                    continue
                sm, fm = it.get('secondMarket') or {}, it.get('firstMarket') or {}
                if sm.get('price') is not None:
                    steam[nm] = sm['price']
                if fm.get('price') is not None:
                    tradeon[nm] = fm['price']
        except Exception as e:
            logger.error(f'[HUGINN] auction pulse prices failed: {e}')
        return steam, tradeon

    def fetch_auctions(self, token=''):
        """Live auction board: each auctioned item vs your buy sources. Per item we take
        the CHEAPEST current lot (what you'd snipe) and join pulse prices — Steam autobuy
        (resale target, net of 13% fee) and TradeOnMarket min (alt buy source) — to show
        the flip profit if you win the lot and sell on Steam. Priced by market_hash_name,
        so float/stickers aren't priced in (approximate)."""
        feed = self._fetch_auctions_raw()
        lots = self._auction_lots(feed)
        by_name = {}
        for lot in lots:
            name, cur = lot['name'], lot['current']
            if not name or cur is None:
                continue
            d = by_name.get(name)
            if d is None:
                d = by_name[name] = {'name': name, 'exterior': lot['exterior'], 'base': lot['base'],
                                     'pg': lot['pg'], 'min_current': cur, 'lots': 0, 'bids': 0,
                                     'tradelock': lot['tradelock']}
            d['lots'] += 1
            d['bids'] += lot['bets']
            d['min_current'] = min(d['min_current'], cur)

        steam, tradeon = self._pulse_steam_tradeon(token)
        rows = []
        steam_fee = self.market_fee('Steam')
        for d in by_name.values():
            cur = d['min_current'] / 100.0
            steam_sell = steam.get(d['name'])
            tradeon_buy = tradeon.get(d['name'])
            steam_net = round(steam_sell * (1 - steam_fee), 4) if steam_sell is not None else None
            profit = round(steam_net - cur, 3) if steam_net is not None else None
            rows.append({
                'itemName': {'marketHashName': d['name']},
                'exterior': d['exterior'],
                'base': round((d['base'] or 0) / 100.0, 2),
                'current': round(cur, 2),
                'bids': d['bids'], 'lots': d['lots'], 'tradelock': d['tradelock'], 'pg': d['pg'],
                'steam': round(steam_sell, 2) if steam_sell is not None else None,
                'steamNet': steam_net,
                'tradeon': round(tradeon_buy, 2) if tradeon_buy is not None else None,
                'profit': profit,
                'profitPercent': round(profit / cur * 100, 2) if (profit is not None and cur) else None,
            })
        rows.sort(key=lambda r: (r['profitPercent'] is not None, r['profitPercent'] or 0), reverse=True)
        return {'rows': rows, 'items': len(rows), 'lots': len(lots), 'timestamp': feed.get('timestamp')}

    # --- auction tracker (persistent per-lot log for the backtest) ---

    def _load_auction_log(self):
        try:
            with open(LOOTFARM_AUCTION_LOG, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {'lots': {}, 'updated': 0}

    def _save_auction_log(self, log):
        try:
            atomic_write_json(LOOTFARM_AUCTION_LOG, log, indent=None)
        except Exception as e:
            logger.error(f'[HUGINN] auction log save failed: {e}')

    _AUCTION_LOG_MAX = 20000   # cap on retained cleared lots

    def record_auction_snapshot(self, token=''):
        """Poll the auction feed once and fold it into the persistent per-lot log: track
        start/last price, max bids, a Steam resale reference at first sight, and mark lots
        cleared once they leave the feed. This is what the backtest reads."""
        feed = self._fetch_auctions_raw()
        lots = self._auction_lots(feed)
        now = int(time.time())
        steam, _ = self._pulse_steam_tradeon(token) if token else ({}, {})
        with self._auction_lock:
            log = self._load_auction_log()
            store = log.setdefault('lots', {})
            active = set()
            for lot in lots:
                lid = lot['id']
                if not lid:
                    continue
                active.add(lid)
                cur = round((lot['current'] or 0) / 100.0, 2)
                rec = store.get(lid)
                if rec is None:
                    store[lid] = {
                        'name': lot['name'], 'exterior': lot['exterior'],
                        'base': round((lot['base'] or 0) / 100.0, 2), 'pg': lot['pg'],
                        'tradelock': lot['tradelock'], 'first_ts': now, 'last_ts': now,
                        'start_current': cur, 'last_current': cur, 'max_bets': lot['bets'], 'polls': 1,
                        'steam_ref': round(steam[lot['name']], 2) if lot['name'] in steam else None,
                        'cleared': False,
                    }
                else:
                    rec['last_ts'] = now
                    rec['last_current'] = cur
                    rec['max_bets'] = max(rec.get('max_bets', 0), lot['bets'])
                    rec['polls'] = rec.get('polls', 1) + 1
                    if rec.get('steam_ref') is None and lot['name'] in steam:
                        rec['steam_ref'] = round(steam[lot['name']], 2)
            newly_cleared = 0
            for lid, rec in store.items():
                if not rec.get('cleared') and lid not in active:
                    rec['cleared'] = True
                    rec['cleared_ts'] = now
                    newly_cleared += 1
            cleared = [(lid, rec) for lid, rec in store.items() if rec.get('cleared')]
            if len(cleared) > self._AUCTION_LOG_MAX:
                cleared.sort(key=lambda x: x[1].get('cleared_ts', 0))
                for lid, _r in cleared[:len(cleared) - self._AUCTION_LOG_MAX]:
                    del store[lid]
            log['updated'] = now
            self._save_auction_log(log)
        return {'active': len(active), 'tracked': len(store), 'newly_cleared': newly_cleared, 'updated': now}

    def start_auction_tracker(self, settings_provider, interval=900):
        """Background loop that snapshots the auction feed every `interval` s."""
        if getattr(self, '_auction_tracker_started', False):
            return
        self._auction_tracker_started = True
        threading.Thread(target=self._auction_tracker_loop,
                         args=(settings_provider, interval), daemon=True).start()
        logger.info(f'[HUGINN] auction tracker started (every {interval}s)')

    def _auction_tracker_loop(self, settings_provider, interval):
        while True:
            try:
                settings = settings_provider() if callable(settings_provider) else (settings_provider or {})
                token = (settings or {}).get('tradeon_token') or ''
                res = self.record_auction_snapshot(token)
                logger.info(f"[HUGINN] auction snapshot: active={res['active']} tracked={res['tracked']} cleared+{res['newly_cleared']}")
            except Exception as e:
                logger.error(f'[HUGINN] auction tracker error: {e}')
            time.sleep(interval)

    def auction_backtest(self, token='', min_profit_pct=10.0):
        """Measure the auction edge from the tracker log, plus a live-snapshot proof.

        From CLEARED lots: did it attract bids, and how far above base did it clear
        (seller-side upside)? For 0-bid cleared lots you could have won at base — compare
        base to the Steam resale reference captured then (buyer snipe edge). Also runs the
        snipe test on the CURRENT snapshot so there's a number before the log fills."""
        log = self._load_auction_log()
        store = log.get('lots', {})
        cleared = [r for r in store.values() if r.get('cleared')]
        bid = [r for r in cleared if (r.get('max_bets') or 0) > 0]
        nobid = [r for r in cleared if not (r.get('max_bets') or 0)]

        def _pct(a, b):
            return round((a - b) / b * 100, 2) if b else None

        markups = [m for m in (_pct(r['last_current'], r['base']) for r in bid if r.get('base')) if m is not None]
        avg_markup = round(sum(markups) / len(markups), 2) if markups else None

        snipes = []
        steam_fee = self.market_fee('Steam')
        for r in nobid:
            ref, base = r.get('steam_ref'), r.get('base')
            if ref is None or not base:
                continue
            snipes.append(_pct(ref * (1 - steam_fee), base))
        snipes = [s for s in snipes if s is not None]
        good = sum(1 for s in snipes if s >= min_profit_pct)

        live = self.fetch_auctions(token)
        live_profitable = sum(1 for row in live['rows'] if (row.get('profitPercent') or -1) >= min_profit_pct)

        return {
            'log_updated': log.get('updated'),
            'cleared_lots': len(cleared), 'bid_lots': len(bid), 'nobid_lots': len(nobid),
            'bid_rate_pct': round(len(bid) / len(cleared) * 100, 1) if cleared else None,
            'avg_clear_markup_pct': avg_markup,
            'snipe_samples': len(snipes), 'profitable_snipes': good, 'min_profit_pct': min_profit_pct,
            'live_items': live['items'], 'live_profitable': live_profitable,
            'note': 'Backtest strengthens as the tracker accumulates cleared lots; live_* is the current snapshot.',
        }

    # ---- LOOT.Farm arbitrage board (buy balance cheap -> LF item -> sell buy order) ---

    _BUYORDER_MARKETS = {'steam': _TRADEON_STEAM_URL, 'buff': _TRADEON_BUFF_URL}
    _BUYORDER_TTL = 300   # seconds; buy-order maps cached and reused across requests

    def _buyorder_map(self, token, market):
        """{name: highest buy-order price} for a market, cached _BUYORDER_TTL s. These
        are instant-sell prices (secondMarketPriceType 'Buy'), unlike the valuation
        cache which holds listings ('Sell')."""
        now = time.time()
        cached = self._buyorder_cache.get(market)
        if cached and now - cached[0] < self._BUYORDER_TTL:
            return cached[1]
        body = copy.deepcopy(_TRADEON_STEAM_BODY)
        body['secondMarketOptions']['secondMarketPriceType'] = 'Buy'
        out = {}
        for it in self._post_tradeon(self._BUYORDER_MARKETS[market], token, body):
            n = (it.get('itemName') or {}).get('marketHashName')
            sm = it.get('secondMarket') or {}
            if n and sm.get('price') is not None:
                out[n] = sm['price']
        self._buyorder_cache[market] = (now, out)
        return out

    def lootfarm_arbitrage(self, token='', balance_rate=0.5208, unlocked=True, in_stock=True, settings=None):
        """The core play: buy LOOT.Farm balance cheap (from the USDT OTC trader) → acquire
        an item from LOOT.Farm → sell it instantly into a Steam/Buff/CSFloat BUY ORDER.

        For each LOOT.Farm item we compute the real USDT cost of acquiring it
          cost = LF_price × (1.03 if unlocked else 1.0) × balance_rate
        and the net proceeds of instant-selling into each market's buy order (Steam 13%,
        Buff 1.5%, CSFloat 2% fees). Steam pays into locked Steam wallet, so it's flagged
        real_cash=False. Ranked by the best real-cash exit's profit.

        `balance_rate` = USDT paid per $1 of LF balance (0.5208 = the trader's +92%).
        Steam/Buff buy orders come from pulse (cached); CSFloat from its buy-orders sweep
        cache (sparse — only swept items)."""
        try:
            balance_rate = float(balance_rate)
        except (TypeError, ValueError):
            balance_rate = 0.5208
        markup = 1.03 if unlocked else 1.0
        feed = self._fetch_lootfarm_feed('cs')
        steam_bo = self._buyorder_map(token, 'steam') if token else {}
        buff_bo = self._buyorder_map(token, 'buff') if token else {}
        csf_bo = (self.get_csfloat_buy_orders_cache() or {}).get('by_name', {})

        # Effective sell fees (the editable per-market overrides in settings win).
        fees = {'steam': self.market_fee('Steam', settings), 'buff': self.market_fee('Buff', settings),
                'csfloat': self.market_fee('CsFloat', settings)}
        real_cash = {'steam': False, 'buff': True, 'csfloat': True}   # Steam = locked wallet
        rows = []
        for name, r in feed.items():
            lf = (r.get('price') or 0) / 100.0
            if not lf:
                continue
            have, mx = r.get('have') or 0, r.get('max') or 0
            tr, res = r.get('tr') or 0, r.get('res') or 0
            avail = max(0, have - tr - res)   # buyable NOW: total minus trade-locked minus reserved
            if in_stock and avail <= 0:
                continue
            cost = round(lf * markup * balance_rate, 4)
            exits = {}
            raw = {'steam': steam_bo.get(name), 'buff': buff_bo.get(name),
                   'csfloat': (csf_bo.get(name) or {}).get('price')}
            for mk, price in raw.items():
                if price is not None:
                    exits[mk] = round(price * (1 - fees[mk]), 4)
            # best exit overall and best REAL-CASH exit (Buff/CSFloat)
            best_mk = max(exits, key=exits.get) if exits else None
            rc = {mk: v for mk, v in exits.items() if real_cash[mk]}
            best_rc = max(rc, key=rc.get) if rc else None
            profit = round(exits[best_rc] - cost, 3) if best_rc else None
            rows.append({
                'itemName': {'marketHashName': name},
                'lf': round(lf, 2), 'have': have, 'max': mx, 'tr': tr, 'res': res, 'avail': avail, 'rate': r.get('rate'),
                'cost': cost,
                'steam': exits.get('steam'), 'buff': exits.get('buff'), 'csfloat': exits.get('csfloat'),
                'bestMarket': best_mk, 'bestNet': exits.get(best_mk) if best_mk else None,
                'bestRealMarket': best_rc, 'bestRealNet': exits.get(best_rc) if best_rc else None,
                'profit': profit,
                'profitPercent': round(profit / cost * 100, 2) if (profit is not None and cost) else None,
            })
        # profitable real-cash flips first; unpriced sink
        rows.sort(key=lambda x: (x['profitPercent'] is not None, x['profitPercent'] or -1e9), reverse=True)
        return {
            'rows': rows, 'items': len(rows),
            'balance_rate': round(balance_rate, 4), 'unlocked': unlocked, 'in_stock': in_stock,
            'csfloat_coverage': len(csf_bo),
            'priced': bool(steam_bo or buff_bo or csf_bo),
        }

    def _combine_arbitrage(self, token, buy_url, buy_body, sell_url, sell_fee, sell_body=None):
        """Combine a buy-side market's min prices with a sell-side market's autobuy prices.

        The buy side is queried so its second market carries its own (un-paywalled) sell
        price — the min you'd pay to buy. The sell side is the standard autobuy query. We
        buy low there and sell into the target market's buy orders, netting that market's
        fee. Items are joined on market_hash_name; only items on both sides are returned.
        Shaped like the other profiles (firstMarket = buy, secondMarket = sell) so the UI
        renders it unchanged.

        `sell_body` overrides the sell-side query body — used for CSFloat, which has no
        autobuy, so its sell side is the lowest listing (Sell) rather than a buy order.
        """
        buy_items = self._post_tradeon(buy_url, token, buy_body)
        sell_items = self._post_tradeon(sell_url, token, sell_body or _TRADEON_STEAM_BODY)
        return self._join_arbitrage(buy_items, sell_items, sell_fee)

    def _join_arbitrage(self, buy_items, sell_items, sell_fee):
        """Join a buy-side pull (its secondMarket = the min you'd pay) with a
        sell-side pull (its secondMarket = what you'd get), on market_hash_name,
        netting the sell fee. Only items present on both sides are returned, shaped
        like the pulse rows (firstMarket = buy, secondMarket = sell) so the UI
        renders it unchanged."""
        # market_hash_name -> sell-side second-market
        sell_by_name = {}
        for it in sell_items:
            name = (it.get('itemName') or {}).get('marketHashName')
            sm = it.get('secondMarket') or {}
            if name and sm.get('price') is not None:
                sell_by_name[name] = sm

        combined = []
        for it in buy_items:
            name = (it.get('itemName') or {}).get('marketHashName')
            buy_market = it.get('secondMarket') or {}      # buy side
            buy = buy_market.get('price')
            sell_market = sell_by_name.get(name)           # target sell side
            if not name or buy is None or not buy or sell_market is None:
                continue
            net_sell = sell_market['price'] * (1 - sell_fee)
            profit = net_sell - buy
            combined.append({
                'itemName': it.get('itemName'),
                'imageUrl': it.get('imageUrl'),
                'firstMarket': buy_market,                 # Buy @ buy-side market
                'secondMarket': sell_market,               # Sell @ target market
                'profit': round(profit, 3),
                'profitPercent': round(profit / buy * 100, 2),
            })

        combined.sort(key=lambda x: x['profitPercent'], reverse=True)
        return combined

    # ---- Generated pairs (any registry market -> any registry market) -------

    def _body_for_type(self, price_type):
        """A standard Tradeon table body with the second market priced as `price_type`."""
        body = copy.deepcopy(_TRADEON_STEAM_BODY)
        body["secondMarketOptions"]["secondMarketPriceType"] = price_type
        return body

    def _pull_market(self, token, market_id, price_type):
        """Pull TradeOnMarket/{market_id} priced as `price_type`, cached briefly so
        generating several pairs that share a market doesn't re-hit pulse each time."""
        key = (market_id, price_type)
        now = time.time()
        with self._market_pull_lock:
            hit = self._market_pull_cache.get(key)
        if hit and now - hit[0] < _MARKET_PULL_TTL:
            return hit[1]
        items = self._post_tradeon(_TRADEON_TABLE_URL.format(market_id), token,
                                   self._body_for_type(price_type))
        with self._market_pull_lock:
            # A raw pull is tens of megabytes and only reused for _MARKET_PULL_TTL
            # seconds, so drop every expired pull on write instead of keeping one per
            # (market, price type) forever.
            fresh_cutoff = time.time() - _MARKET_PULL_TTL
            self._market_pull_cache = {k: v for k, v in self._market_pull_cache.items()
                                       if v[0] >= fresh_cutoff}
            self._market_pull_cache[key] = (now, items)
        return items

    def _join_direct(self, items, sell_fee):
        """Build rows from a single TradeOnMarket/{sell} pull, where the buy side IS
        TradeOnMarket (the pull's firstMarket) — used when Tradeon is the buy source."""
        out = []
        for it in items:
            buy_market = it.get('firstMarket') or {}
            sell_market = it.get('secondMarket') or {}
            buy = buy_market.get('price')
            sell = sell_market.get('price')
            if buy is None or not buy or sell is None:
                continue
            net_sell = sell * (1 - sell_fee)
            profit = net_sell - buy
            out.append({
                'itemName': it.get('itemName'),
                'imageUrl': it.get('imageUrl'),
                'firstMarket': buy_market,
                'secondMarket': sell_market,
                'profit': round(profit, 3),
                'profitPercent': round(profit / buy * 100, 2),
            })
        out.sort(key=lambda x: x['profitPercent'], reverse=True)
        return out

    def fetch_generated_pair(self, token, buy_id, sell_id, mode='autobuy', fee=None):
        """Synthesise the arbitrage table for any registry pair buy_id -> sell_id.

        `mode` 'autobuy' sells into the target's buy orders (falls back to its min
        listing when it has none); 'min' always uses the target's lowest listing.
        `fee` defaults to the sell market's registry fee when not provided. Both legs
        come from TradeOnMarket/{id} pulls (never the paywalled direct pair)."""
        buy = _MARKET_BY_ID[buy_id]      # KeyError -> route returns 400
        sell = _MARKET_BY_ID[sell_id]
        if fee is None:
            fee = self.market_fee(sell_id)
        if sell_id in _AUTOBUY_VIA_CSFLOAT_SWEEP and mode == 'autobuy':
            # Sell into CSFloat buy orders (swept cache), not a pulse price type —
            # netting the same (possibly edited) fee as every other pair.
            return self.fetch_generated_csfloat_autobuy(token, buy_id, fee)
        sell_type = sell['autobuy'] if (mode == 'autobuy' and sell['autobuy']) else 'Sell'
        if buy_id == 'TradeOnMarket':
            # Tradeon is the pull's own first market — one pull, buy = firstMarket.
            items = self._pull_market(token, sell_id, sell_type)
            return self._join_direct(items, fee)
        buy_items = self._pull_market(token, buy_id, buy['buy_type'])
        sell_items = self._pull_market(token, sell_id, sell_type)
        return self._join_arbitrage(buy_items, sell_items, fee)

    def fetch_generated_csfloat_autobuy(self, token, buy_id, fee=None):
        """Sell into CSFloat's buy orders (from the swept cache) from any buy market.
        Generalises the four hand-written *_csfloat_autobuy profiles: the buy price is
        the buy market's min listing — TradeOnMarket's own price when it's the buy side
        (buy_side='first'), otherwise the target market's second-market price. `fee`
        defaults to CSFloat's registry fee."""
        buy = _MARKET_BY_ID[buy_id]      # KeyError -> route returns 400
        if buy_id == 'TradeOnMarket':
            # firstMarket of any TradeOnMarket table is TradeOnMarket's min; use the
            # CsFloat table so only CSFloat-listed items are considered (as the curated one does).
            return self._combine_autobuy(token, _TRADEON_TABLE_URL.format('CsFloat'),
                                         self._body_for_type('Sell'), buy_side='first', sell_fee=fee)
        return self._combine_autobuy(token, _TRADEON_TABLE_URL.format(buy_id),
                                     self._body_for_type(buy['buy_type']), buy_side='second', sell_fee=fee)

    def market_ids(self):
        """Set of valid market identifiers (for request validation)."""
        return set(_MARKET_BY_ID)

    def market_fee(self, sell_id, settings=None):
        """Effective sell-side fee for a market: your edited fee from the Fees editor
        (settings, or the current settings when none are passed), else the registry
        default. The ONE place every profit calculation takes a fee from."""
        if settings is None and callable(self.settings_provider):
            try:
                settings = self.settings_provider()
            except Exception:
                settings = None
        overrides = (settings or {}).get('huginn_market_fees') or {}
        m = _MARKET_BY_ID.get(sell_id)
        return overrides.get(sell_id, m['fee'] if m else 0.0)

    def market_registry(self, settings=None):
        """Public registry for the UI: each market with its effective fee (settings
        override applied), whether that fee is confirmed, and whether it has autobuy."""
        overrides = (settings or {}).get('huginn_market_fees') or {}
        out = []
        for m in _MARKET_REGISTRY:
            out.append({
                'id': m['id'],
                'display': m['display'],
                'hasAutobuy': bool(m['autobuy']) or m['id'] in _AUTOBUY_VIA_CSFLOAT_SWEEP,
                'premium': m['premium'],
                'fee': overrides.get(m['id'], m['fee']),
                'feeDefault': m['fee'],
                'feeKnown': m['id'] in _MARKET_FEE_CONFIRMED,
            })
        return out

    @staticmethod
    def _index_from_pull(items, side='second'):
        """{market_hash_name: {price, count, image}} from a pulse pull, reading the
        chosen market side ('second' = the priced target market, 'first' = TradeOnMarket)."""
        key = 'firstMarket' if side == 'first' else 'secondMarket'
        out = {}
        for it in items:
            name = (it.get('itemName') or {}).get('marketHashName')
            mk = it.get(key) or {}
            price = mk.get('price')
            if name and price:
                out[name] = {'price': price,
                             'count': mk.get('totalOffersCount') or mk.get('count'),
                             'image': it.get('imageUrl'),
                             # pulse's own trend marks for this market's price
                             'rising': bool(mk.get('isRaising')), 'falling': bool(mk.get('isFalling'))}
        return out

    def market_buy_index(self, token, market_id):
        """{name: {price,count,image}} of a market's MIN listing (what you'd pay to
        buy). Empty for an unknown market id. Cached briefly via _pull_market."""
        m = _MARKET_BY_ID.get(market_id)
        if not m:
            return {}
        return self._index_from_pull(self._pull_market(token, market_id, m['buy_type']))

    def market_autobuy_index(self, token, market_id):
        """{name: {price,count,image}} of a market's autobuy / buy-order (what you'd
        get selling instantly). Empty if the market has no autobuy. CSFloat comes from
        the swept buy-orders cache (no pulse buy price); every other autobuy market
        comes from pulse's 'Buy' price type."""
        m = _MARKET_BY_ID.get(market_id)
        if not m:
            return {}
        if market_id in _AUTOBUY_VIA_CSFLOAT_SWEEP:
            cache = self.get_csfloat_buy_orders_cache() or {}
            return {n: {'price': o['price'], 'count': o.get('qty'), 'image': None}
                    for n, o in (cache.get('by_name') or {}).items() if o.get('price')}
        if not m['autobuy']:
            return {}
        return self._index_from_pull(self._pull_market(token, market_id, m['autobuy']))

    def market_display(self, market_id):
        """Human name for a market id (falls back to the id itself)."""
        m = _MARKET_BY_ID.get(market_id)
        return m['display'] if m else market_id

    def fetch_lisskins_steam(self, token, settings=None):
        return self._combine_arbitrage(token, _TRADEON_LISSKINS_URL, _TRADEON_LISSKINS_BODY,
                                       _TRADEON_STEAM_URL, self.market_fee('Steam', settings))

    def fetch_lisskins_buff(self, token, settings=None):
        return self._combine_arbitrage(token, _TRADEON_LISSKINS_URL, _TRADEON_LISSKINS_BODY,
                                       _TRADEON_BUFF_URL, self.market_fee('Buff', settings))

    def fetch_lisskins_csfloat(self, token, settings=None):
        return self._combine_arbitrage(token, _TRADEON_LISSKINS_URL, _TRADEON_LISSKINS_BODY,
                                       _TRADEON_CSFLOAT_URL, self.market_fee('CsFloat', settings),
                                       sell_body=_TRADEON_CSFLOAT_BODY)

    def fetch_buff_steam(self, token, settings=None):
        return self._combine_arbitrage(token, _TRADEON_BUFF_URL, _TRADEON_BUFF_BUY_BODY,
                                       _TRADEON_STEAM_URL, self.market_fee('Steam', settings))

    def fetch_buff_csfloat(self, token, settings=None):
        return self._combine_arbitrage(token, _TRADEON_BUFF_URL, _TRADEON_BUFF_BUY_BODY,
                                       _TRADEON_CSFLOAT_URL, self.market_fee('CsFloat', settings),
                                       sell_body=_TRADEON_CSFLOAT_BODY)

    def fetch_csfloat_steam(self, token, settings=None):
        # Buy at CSFloat's min listing, sell into Steam's autobuy (13% Steam fee).
        return self._combine_arbitrage(token, _TRADEON_CSFLOAT_URL, _TRADEON_CSFLOAT_BODY,
                                       _TRADEON_STEAM_URL, self.market_fee('Steam', settings))

    def fetch_csfloat_buff(self, token, settings=None):
        # Buy at CSFloat's min listing, sell into Buff163's autobuy (1.5% Buff fee).
        return self._combine_arbitrage(token, _TRADEON_CSFLOAT_URL, _TRADEON_CSFLOAT_BODY,
                                       _TRADEON_BUFF_URL, self.market_fee('Buff', settings))

    def fetch_lisskins_dmarket(self, token, settings=None):
        # Buy at LisSkins min, sell into DMarket autobuy (no DMarket fee).
        return self._combine_arbitrage(token, _TRADEON_LISSKINS_URL, _TRADEON_LISSKINS_BODY,
                                       _TRADEON_DMARKET_URL, self.market_fee('Dmarket', settings))

    def fetch_buff_dmarket(self, token, settings=None):
        # Buy at Buff163 min, sell into DMarket autobuy (no DMarket fee).
        return self._combine_arbitrage(token, _TRADEON_BUFF_URL, _TRADEON_BUFF_BUY_BODY,
                                       _TRADEON_DMARKET_URL, self.market_fee('Dmarket', settings))

    def fetch_csfloat_dmarket(self, token, settings=None):
        # Buy at CSFloat's min listing, sell into DMarket autobuy (no DMarket fee).
        return self._combine_arbitrage(token, _TRADEON_CSFLOAT_URL, _TRADEON_CSFLOAT_BODY,
                                       _TRADEON_DMARKET_URL, self.market_fee('Dmarket', settings))

    def fetch_dmarket_steam(self, token, settings=None):
        # Buy at DMarket's min listing, sell into Steam's autobuy (13% Steam fee).
        return self._combine_arbitrage(token, _TRADEON_DMARKET_URL, _TRADEON_DMARKET_BUY_BODY,
                                       _TRADEON_STEAM_URL, self.market_fee('Steam', settings))

    def fetch_dmarket_buff(self, token, settings=None):
        # Buy at DMarket's min listing, sell into Buff163's autobuy (1.5% Buff fee).
        return self._combine_arbitrage(token, _TRADEON_DMARKET_URL, _TRADEON_DMARKET_BUY_BODY,
                                       _TRADEON_BUFF_URL, self.market_fee('Buff', settings))

    def fetch_dmarket_csfloat(self, token, settings=None):
        # Buy at DMarket's min listing, sell at CSFloat's min listing (CSFloat has no
        # autobuy, so the sell side is its lowest listing — 2% CSFloat fee).
        return self._combine_arbitrage(token, _TRADEON_DMARKET_URL, _TRADEON_DMARKET_BUY_BODY,
                                       _TRADEON_CSFLOAT_URL, self.market_fee('CsFloat', settings),
                                       sell_body=_TRADEON_CSFLOAT_BODY)

    # ---- CSFloat buy orders (autobuy) --------------------------------------

    _CSFLOAT_PROXY_ATTEMPTS = 8   # datacenter IPs are ~75% Cloudflare-blocked; rotate through several

    def _csfloat_fetch_once(self, url, api_key, proxy=None, request_body=None):
        """One CSFloat GET. Returns parsed JSON, or raises _CSFloatRateLimited (429 —
        the key/us is throttled) or _CSFloatUnavailable (403 / non-JSON challenge — a
        blocked exit IP or edge block). Through a proxy, each call uses a fresh Bright
        Data session so it lands on a new exit IP."""
        if proxy:
            self._proxy_seq += 1
            per_ip = _proxy_with_session(proxy, self._proxy_seq)
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({'http': per_ip, 'https': per_ip}))
            do_open = lambda req: opener.open(req, timeout=30)
        else:
            do_open = lambda req: urllib.request.urlopen(req, timeout=30)

        headers = {
            'Authorization': api_key,
            'Accept': 'application/json',
            'User-Agent': _CSFLOAT_UA,
        }
        data = None
        if request_body is not None:   # a JSON POST (the buy-orders-by-name query)
            data = json.dumps(request_body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method='POST' if data is not None else 'GET')
        try:
            with do_open(req) as resp:
                body = resp.read().decode('utf-8', 'replace')
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise _CSFloatUnavailable('non-JSON page (bot challenge)')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise _CSFloatRateLimited('CSFloat rate limit (HTTP 429)',
                                          retry_after=_retry_after_seconds(e.headers))
            if e.code == 403:
                raise _CSFloatUnavailable('CSFloat forbidden (HTTP 403)')
            raise
        except (urllib.error.URLError, ssl.SSLError, socket.timeout, ConnectionError) as e:
            # Transport-level failure: TLS reset/EOF (CSFloat blocking the datacenter IP),
            # DNS/connection refused, proxy tunnel 407, or timeout. Surface it as
            # "unavailable" so _csfloat_get falls back to the proxy (direct is blocked),
            # and so the sweep counts it as "couldn't reach CSFloat" rather than
            # silently recording "this item has no buy order".
            detail = getattr(e, 'reason', None) or e
            raise _CSFloatUnavailable(f'connection failed: {detail}')

    def _csfloat_get(self, path, api_key, proxy=None):
        """GET a CSFloat API path, DIRECT first (reliable), falling back to the proxy
        only when direct is throttled — then rotating exit IPs to power through.

        Direct 429 → if a proxy is configured, bypass via rotating IPs; else back off
        and, if still limited, raise _CSFloatRateLimited so the key is benched. When the
        proxy is used, a 429 on a fresh IP means the key itself is throttled (bench it),
        whereas all-IPs-403 means the datacenter pool is blocked → _CSFloatUnavailable
        (skip this item, keep the sweep going)."""
        url = f'{_CSFLOAT_API_BASE}{path}'

        # --- direct first ---
        for attempt in range(3):
            try:
                return self._csfloat_fetch_once(url, api_key, None)
            except (_CSFloatRateLimited, _CSFloatUnavailable) as e:
                if proxy:
                    break                      # hand off to proxy fallback
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                if isinstance(e, _CSFloatUnavailable):
                    # The last direct failure was a block / connection failure, not a
                    # 429: keep it "unavailable" so the key is not benched for it and
                    # the sweep counts the item as unreachable (retried on Resume).
                    raise
                raise _CSFloatRateLimited('CSFloat throttled (direct, no proxy)',
                                          retry_after=getattr(e, 'retry_after', None))

        # --- proxy fallback: rotate exit IPs ---
        last = None
        for _ in range(self._CSFLOAT_PROXY_ATTEMPTS):
            try:
                return self._csfloat_fetch_once(url, api_key, proxy)
            except _CSFloatUnavailable as e:
                last = e                        # blocked IP → try another
                continue
            # _CSFloatRateLimited (429 on a fresh IP) propagates → key gets benched
        raise _CSFloatUnavailable(f'proxy exit IPs all blocked ({last})')

    def _csfloat_find_listing_id(self, api_key, market_hash_name, proxy=None):
        """Cheapest listing id for an exact market_hash_name, or None if unlisted."""
        q = urllib.parse.urlencode({
            'limit': 1, 'sort_by': 'lowest_price', 'market_hash_name': market_hash_name,
        })
        data = self._csfloat_get(f'/listings?{q}', api_key, proxy)
        rows = data.get('data') if isinstance(data, dict) else data
        if rows:
            return rows[0].get('id')
        return None

    def _csfloat_top_buy_order(self, api_key, listing_id, proxy=None):
        """Highest buy order for the item behind a listing, as {price(usd), qty} or None."""
        orders = self._csfloat_get(f'/listings/{listing_id}/buy-orders?limit=10', api_key, proxy)
        if not isinstance(orders, list) or not orders:
            return None
        top = max(orders, key=lambda o: o.get('price') or 0)
        price = top.get('price')
        if not price:
            return None
        return {'price': price / 100.0, 'qty': top.get('qty')}  # CSFloat prices are cents

    def load_csfloat_item_links(self):
        """{market_hash_name: {listing_id, link, checked_at}} for the items you hold."""
        try:
            with open(CSFLOAT_ITEM_LINKS_FILE) as f:
                links = json.load(f)
            return links if isinstance(links, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            logger.warning(f'[HUGINN] CSFloat item links unreadable, starting empty: {e}')
            return {}

    @staticmethod
    def _csfloat_link_entry(listing_id, checked_at):
        return {'listing_id': listing_id,
                'link': _CSFLOAT_ITEM_URL.format(listing_id) if listing_id else None,
                'checked_at': checked_at}

    def _update_csfloat_item_links(self, updates):
        """Write swept items' new listing / time into the dictionary as it is NOW on
        disk (a scan may have rebuilt it during a long sweep); items no longer in
        it (sold meanwhile) are not brought back."""
        with self._csfloat_links_lock:
            links = self.load_csfloat_item_links()
            for name, entry in updates.items():
                if name in links:
                    links[name] = entry
            atomic_write_json(CSFLOAT_ITEM_LINKS_FILE, links, indent=None)

    def sync_csfloat_item_links(self, extra_names=()):
        """Rebuild the dictionary from the latest inventory scan plus `extra_names`
        (Draupnir holdings): keep what is still held, add new items (no link yet),
        drop items you no longer hold. Returns (sorted names, added, removed)."""
        scanned = set(((self.get_cache() or {}).get('by_hash') or {}))
        names = scanned | {name for name in extra_names if name}
        with self._csfloat_links_lock:
            links = self.load_csfloat_item_links()
            if not scanned:
                # No usable scan (missing / unreadable): nothing is known to be sold,
                # so keep every entry and only add the extra names.
                names |= set(links)
            updated = {name: links.get(name) or self._csfloat_link_entry(None, None) for name in names}
            added = len(names - set(links))
            removed = len(set(links) - names)
            if updated != links:
                atomic_write_json(CSFLOAT_ITEM_LINKS_FILE, updated, indent=None)
        if added or removed:
            logger.info(f'[HUGINN] CSFloat item links: {len(updated)} items held '
                        f'({added} new, {removed} no longer held)')
        return sorted(names), added, removed

    def _csfloat_name_orders(self, api_key, name, retry_rate_limit=True):
        """An item's buy orders by name — one request, no listing needed:
        {price (USD), qty, depth: [[price, qty], ... highest first, up to 10]} or None.

        POST /buy-orders/similar-orders {market_hash_name} answers every buy order for
        that item, highest first (checked live 2026-09-30 against the listing method:
        same top prices). Orders with conditions (`hybrid_properties`: a float range,
        pattern or sticker) are left out: they would not take just any copy. DIRECT
        only: through the datacenter proxy CSFloat answers 429 "disable your VPN",
        which is about the proxy's address, not the key."""
        url = f'{_CSFLOAT_API_BASE}/buy-orders/similar-orders'
        for attempt in range(3):
            try:
                data = self._csfloat_fetch_once(url, api_key, None, request_body={'market_hash_name': name})
                break
            except _CSFloatRateLimited:
                if attempt == 2 or not retry_rate_limit:
                    raise
                time.sleep(5 * (attempt + 1))       # a brief 429: back off like _csfloat_get
            except urllib.error.HTTPError as e:
                if e.code in (405, 410):            # the endpoint itself is gone / changed
                    raise _CSFloatNameLookupUnsupported(f'HTTP {e.code}')
                # 5xx, or a 400/401 for this one name or key: this item only
                raise _CSFloatUnavailable(f'by-name lookup HTTP {e.code}')
        rows = data.get('data') if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise _CSFloatNameLookupUnsupported(f'unexpected answer ({type(data).__name__})')
        plain = []
        for row in rows:
            # Only plain orders for exactly this item: conditional ones (hybrid_properties,
            # or an advanced 'expression' order, which carries no item name) are skipped.
            if not isinstance(row, dict) or row.get('hybrid_properties') or row.get('expression'):
                continue
            if row.get('market_hash_name') != name:
                continue
            price, qty = row.get('price'), row.get('qty')
            if isinstance(price, (int, float)) and price > 0:
                plain.append((price / 100.0, qty))      # CSFloat prices are cents
        if not plain:
            return None
        plain.sort(key=lambda order: order[0], reverse=True)
        return {'price': plain[0][0], 'qty': plain[0][1],
                'depth': [[price, qty] for price, qty in plain[:10]]}

    def _csfloat_item_order(self, api_key, name, remembered_listing_id, proxy=None, by_name=True):
        """(highest buy order or None, listing id, method) for one item.

        With `by_name`: one request to the buy-orders-by-name endpoint (method
        'name'); the remembered listing id is kept as it is. If that endpoint is
        unsupported, _CSFloatNameLookupUnsupported goes up so the sweep stops trying it;
        if this one request could not get through (blocked / challenge), this item
        falls back to the listing method below.

        Listing method (method 'listing'): one request while the remembered listing is
        still up and has buy orders; otherwise the cheapest listing is looked up again
        and read (two requests)."""
        if by_name:
            try:
                # With a proxy, a direct 429 is handed to the listing method (which can
                # go through the proxy) instead of benching the key for an hour.
                return (self._csfloat_name_orders(api_key, name, retry_rate_limit=not proxy),
                        remembered_listing_id, 'name')
            except _CSFloatUnavailable as e:
                logger.info(f'[HUGINN] CSFloat by-name lookup blocked for {name!r} ({e}); using its listing')
            except _CSFloatRateLimited as e:
                if not proxy:
                    raise                           # no other route: bench the key
                logger.info(f'[HUGINN] CSFloat by-name lookup rate-limited for {name!r} ({e}); '
                            f'using its listing through the proxy')
            time.sleep(_CSFLOAT_REQUEST_DELAY)
        order, listing_id = self._csfloat_listing_order(api_key, name, remembered_listing_id, proxy)
        return order, listing_id, 'listing'

    def _csfloat_listing_order(self, api_key, name, remembered_listing_id, proxy=None):
        """(highest buy order or None, listing id) through a listing's buy-order book."""
        if remembered_listing_id:
            try:
                order = self._csfloat_top_buy_order(api_key, remembered_listing_id, proxy)
                if order:
                    return order, remembered_listing_id
            except urllib.error.HTTPError as e:
                if e.code not in (400, 404, 410):
                    raise                     # anything but "that listing is gone"
            time.sleep(_CSFLOAT_REQUEST_DELAY)  # pace every request, not just every item
        listing_id = self._csfloat_find_listing_id(api_key, name, proxy)
        if not listing_id:
            return None, None
        if listing_id == remembered_listing_id:
            return None, listing_id           # same listing, already read: no buy orders
        time.sleep(_CSFLOAT_REQUEST_DELAY)
        return self._csfloat_top_buy_order(api_key, listing_id, proxy), listing_id

    def _csfloat_sweep_order(self, todo, token, swept_at):
        """Every item is swept, in this order: most valuable holdings first (Buff163
        listing × units held), then the ones checked longest ago. Without prices (no
        token / pull failed) the order is simply oldest-swept first."""
        prices = {}
        if token:
            try:
                prices = self.price_map(token, 'buff') or {}
            except Exception as e:
                logger.warning(f'[HUGINN] CSFloat sweep: no Buff163 prices for ordering ({e})')
        held = {name: entry.get('count') or len(entry.get('instances') or []) or 1
                for name, entry in ((self.get_cache() or {}).get('by_hash') or {}).items()}
        now = datetime.now(timezone.utc)

        def age_seconds(name):
            try:
                return (now - datetime.fromisoformat(swept_at[name])).total_seconds()
            except (KeyError, TypeError, ValueError):
                return float('inf')           # never swept: oldest of all

        return sorted(todo, key=lambda name: (-(prices.get(name) or 0) * held.get(name, 1),
                                              -age_seconds(name), name))

    def _resumable_state(self, names):
        """Return (processed_set, by_name, started_at) — resuming a recent, unfinished
        prior sweep if one exists within the resume window, else a fresh start.

        A fresh start is SEEDED with the previous cache's prices (for the current
        candidates) but nothing marked processed: every item is swept again and its
        new result overwrites (or removes) the seeded one. Without the seed, the first
        checkpoint would replace a complete cache with ~25 items for the hours a
        sweep takes, and every CSFloat-autobuy view would go nearly empty meanwhile."""
        prev = self.get_csfloat_buy_orders_cache()
        candidate = set(names)
        if prev and not prev.get('complete'):
            stamp = prev.get('updated_at') or prev.get('fetched_at')
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
            except Exception:
                age = None
            if age is not None and age < _CSFLOAT_RESUME_WINDOW_SEC:
                # Only resume names still relevant to the current candidate set.
                processed = {n for n in (prev.get('processed') or []) if n in candidate}
                by_name = {k: v for k, v in (prev.get('by_name') or {}).items() if k in candidate}
                return processed, by_name, prev.get('started_at')
        seeded = {k: v for k, v in ((prev or {}).get('by_name') or {}).items() if k in candidate}
        return set(), seeded, None

    def _write_buyorders_cache(self, by_name, processed, total, started_at, complete, reason=None):
        result = {
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'started_at': started_at,
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'count': len(by_name),
            'candidates': total,
            'processed': sorted(processed),
            'by_name': by_name,
            'complete': complete,
            'interrupted': bool(reason),
            'reason': reason,
        }
        atomic_write_json(CSFLOAT_BUYORDERS_CACHE, result, indent=None)
        return result

    def fetch_csfloat_buy_orders(self, token=None, names=None, progress=None, key_pairs=None, wait_cb=None):
        """Sweep CSFloat buy orders for owned items and cache the result to disk.

        For each candidate market_hash_name we read its buy orders (by name, or
        through a listing) and keep the highest bid. Candidates default to the items
        in the latest inventory scan, and every one is asked: Tradeon's CSFloat feed is
        not used as a filter, because it joins Tradeon's own market with CSFloat and so
        misses items Tradeon does not list even when CSFloat has buy orders for them
        (64 of 102 such items did, checked 2026-10-01). `token` only orders the sweep.

        Requests rotate across the CSFloat key pool; a key that gets rate-limited is
        benched (see CSFloatKeyManager) and the item retries on the next key. The sweep
        only PAUSES when every key is cooling — then it saves progress and a new run
        within the resume window continues where it stopped. Progress is checkpointed.
        `progress(done, total, current, found)` is called after each item.
        """
        key_pairs = key_pairs if key_pairs is not None else load_csfloat_keys()
        keys = [kp['key'] for kp in key_pairs]
        if not keys:
            raise ValueError('No CSFloat API keys configured (edit csfloat_keys.json)')
        proxy = load_csfloat_proxy()
        if proxy:
            logger.info(f'[HUGINN] CSFloat sweep routing through proxy {proxy.split("@")[-1]}')

        if names is None:
            scan = self.get_cache()
            names = sorted((scan or {}).get('by_hash', {}).keys())

        total = len(names)
        processed, by_name, started_at = self._resumable_state(names)
        started_at = started_at or datetime.now(timezone.utc).isoformat()
        todo = [n for n in names if n not in processed]
        # Items priced BY THIS SWEEP (resumed part included). by_name may also hold
        # prices seeded from the previous cache that are not re-swept yet; those must
        # not count as "the route works" for the abort / total-failure checks.
        priced_now = sum(1 for n in processed if n in by_name)
        if processed:
            logger.info(f'[HUGINN] Resuming CSFloat sweep: {len(processed)}/{total} already done, '
                  f'{len(todo)} to go, {priced_now} priced so far')
        elif by_name:
            logger.info(f'[HUGINN] CSFloat sweep starting over {total} items, keeping '
                        f'{len(by_name)} previous prices until each is re-swept')
        links = self.load_csfloat_item_links()
        listing_ids = {name: entry.get('listing_id') for name, entry in links.items() if entry.get('listing_id')}
        swept_at = {name: entry.get('checked_at') for name, entry in links.items() if entry.get('checked_at')}
        todo = self._csfloat_sweep_order(todo, token, swept_at)

        def save(complete, reason=None):
            # Only the swept items get a new listing / time, merged into the dictionary
            # as it is on disk now.
            self._update_csfloat_item_links({
                name: self._csfloat_link_entry(listing_ids.get(name), swept_at.get(name))
                for name in set(listing_ids) | set(swept_at)})
            return self._write_buyorders_cache(by_name, processed, total, started_at, complete, reason)

        if progress:
            progress(len(processed), total, None, priced_now)

        reason = None
        use_name_lookup = True     # buy orders by name (1 request); listings if unsupported
        name_lookup_blocked_in_a_row = 0
        methods = {}               # 'name' / 'listing' -> items priced that way
        since_checkpoint = 0
        consecutive_waits = 0
        consecutive_unreachable = 0    # items in a row we couldn't even connect for
        last_unreachable = None
        for name in todo:
            order = None
            paused = False
            unreachable = False
            fetch_failed = False   # unexpected error: skip the item, keep any seeded price
            # Try this item across keys; a rate-limited key is benched and we try another.
            while True:
                key = self.csfloat_keys.next_key(keys)
                if key is None:
                    # Every key is cooling. Auto-resume: save progress, wait out the
                    # soonest cooldown, then retry — up to a cap, after which we pause.
                    consecutive_waits += 1
                    if consecutive_waits > _CSFLOAT_MAX_AUTO_WAITS:
                        paused = True
                        reason = 'all CSFloat keys still cooling after several auto-resumes — resume manually'
                        logger.info(f'[HUGINN] CSFloat sweep paused at {len(processed)}/{total}: {reason}')
                        break
                    wait_s = self.csfloat_keys.min_cooldown_remaining(keys) + 5
                    save(complete=False)
                    resume_at = time.time() + wait_s
                    if wait_cb:
                        wait_cb(resume_at)
                    logger.info(f'[HUGINN] all CSFloat keys cooling at {len(processed)}/{total}; '
                          f'auto-resuming in {int(wait_s // 60)}m{int(wait_s % 60)}s')
                    time.sleep(wait_s)
                    if wait_cb:
                        wait_cb(None)
                    continue        # keys should be free now → retry this item
                try:
                    try:
                        order, listing_id, method = self._csfloat_item_order(
                            key, name, listing_ids.get(name), proxy, by_name=use_name_lookup)
                    except _CSFloatNameLookupUnsupported as e:
                        use_name_lookup = False
                        logger.warning(f'[HUGINN] CSFloat buy-orders-by-name unavailable ({e}); '
                                       f'this sweep uses listings instead')
                        order, listing_id, method = self._csfloat_item_order(
                            key, name, listing_ids.get(name), proxy, by_name=False)
                    methods[method] = methods.get(method, 0) + 1
                    if use_name_lookup:
                        # A 'listing' result here means the direct by-name request was
                        # blocked; several in a row = direct is blocked, stop trying it.
                        name_lookup_blocked_in_a_row = name_lookup_blocked_in_a_row + 1 if method == 'listing' else 0
                        if name_lookup_blocked_in_a_row >= _CSFLOAT_NAME_LOOKUP_BLOCKED_LIMIT:
                            use_name_lookup = False
                            logger.warning(f'[HUGINN] CSFloat by-name lookup blocked {name_lookup_blocked_in_a_row} '
                                           f'times in a row (direct access?); this sweep uses listings instead')
                    if listing_id:
                        listing_ids[name] = listing_id
                    else:
                        listing_ids.pop(name, None)
                    swept_at[name] = datetime.now(timezone.utc).isoformat()
                    self.csfloat_keys.mark_ok(key)
                    break
                except _CSFloatRateLimited as e:
                    self.csfloat_keys.mark_limited(key, getattr(e, 'retry_after', None))
                    continue                      # key throttled → bench it, try next key
                except _CSFloatUnavailable as e:
                    self.csfloat_keys.mark_ok(key) # not the key's fault (proxy IP) — don't bench
                    if use_name_lookup:
                        # the by-name request (and its listing fallback) was blocked too
                        name_lookup_blocked_in_a_row += 1
                        if name_lookup_blocked_in_a_row >= _CSFLOAT_NAME_LOOKUP_BLOCKED_LIMIT:
                            use_name_lookup = False
                    unreachable = True             # couldn't reach CSFloat for this item
                    last_unreachable = str(e)
                    logger.info(f'[HUGINN] CSFloat item unreachable ({name!r}): {e}')
                    break
                except Exception as e:
                    logger.error(f'[HUGINN] CSFloat buy-order fetch failed for {name!r}: {e}')
                    fetch_failed = True
                    break                         # unexpected → skip this item permanently

            if paused:
                break

            if unreachable:
                # A transport failure, not a genuine "no buy order". Do NOT mark the item
                # processed, so a Resume retries it once the route is healthy. If we can't
                # reach CSFloat at all (direct IP-blocked AND proxy down), abort early with
                # a clear reason instead of grinding through every remaining item.
                consecutive_unreachable += 1
                if progress:
                    progress(len(processed), total, name, priced_now)
                if consecutive_unreachable >= _CSFLOAT_ABORT_AFTER_UNREACHABLE and not priced_now:
                    hint = classify_proxy_error(last_unreachable or '').get('hint')
                    reason = (f'CSFloat unreachable — {consecutive_unreachable} items in a row failed to '
                              f'connect. '
                              + (hint or (f'Direct requests are IP-blocked and the proxy is not working '
                                          f'({last_unreachable}); check the proxy / IP whitelist in '
                                          f'csfloat_keys.json.')))
                    logger.error(f'[HUGINN] CSFloat sweep aborted: {reason}')
                    break
                time.sleep(_CSFLOAT_REQUEST_DELAY)
                continue

            consecutive_unreachable = 0    # connected → reset the abort counter
            if order:
                by_name[name] = order
                priced_now += 1
            elif not fetch_failed:
                by_name.pop(name, None)    # re-swept with no buy order → drop the seeded price
            processed.add(name)
            consecutive_waits = 0          # made progress → reset the auto-resume cap
            since_checkpoint += 1
            if progress:
                progress(len(processed), total, name, priced_now)
            if since_checkpoint >= _CSFLOAT_CHECKPOINT_EVERY:
                save(complete=False)
                since_checkpoint = 0
            time.sleep(_CSFLOAT_REQUEST_DELAY)

        complete = reason is None and all(n in processed for n in names)
        result = save(complete, reason)
        if methods:
            logger.info(f'[HUGINN] CSFloat sweep read {methods.get("name", 0)} items by name, '
                        f'{methods.get("listing", 0)} through listings')
        if reason and not priced_now:
            # Total failure (nothing priced). Raise so the UI shows a loud error instead
            # of a silent, misleading "swept fine, no buy orders exist" / "No deals".
            raise RuntimeError(reason)
        return result

    def get_csfloat_buy_orders_cache(self):
        if not os.path.exists(CSFLOAT_BUYORDERS_CACHE):
            return None
        with open(CSFLOAT_BUYORDERS_CACHE) as f:
            return json.load(f)

    def check_csfloat_connectivity(self):
        """Probe CSFloat reachability WITHOUT running a full sweep: at most one direct
        and one proxied request. Reports which path works and, when the proxy rejects
        this server's IP (Bright Data ip_forbidden), an explicit hint for the UI.

        Shape: {proxy_enabled, direct:{ok,detail,...}, proxy:{ok,detail,code?,hint?},
        usable, checked_at}. `ok` is True/False, or None for the proxy when none is set.
        """
        keys = [kp['key'] for kp in load_csfloat_keys()]
        proxy = load_csfloat_proxy()
        result = {
            'proxy_enabled': bool(proxy),
            'checked_at': datetime.now(timezone.utc).isoformat(),
        }
        if not keys:
            no_keys = {'ok': False, 'detail': 'no CSFloat API keys configured (edit csfloat_keys.json)'}
            result['direct'] = dict(no_keys)
            result['proxy'] = dict(no_keys)
            result['usable'] = False
            return result

        key = keys[0]
        q = urllib.parse.urlencode({
            'limit': 1, 'sort_by': 'lowest_price',
            'market_hash_name': 'AK-47 | Redline (Field-Tested)',
        })
        url = f'{_CSFLOAT_API_BASE}/listings?{q}'

        def _probe(via_proxy):
            try:
                self._csfloat_fetch_once(url, key, proxy if via_proxy else None)
                return {'ok': True, 'detail': 'reachable'}
            except _CSFloatRateLimited as e:
                # Reached CSFloat, just throttled — the path itself works.
                return {'ok': True, 'detail': str(e), 'rate_limited': True}
            except _CSFloatUnavailable as e:
                return {'ok': False, 'detail': str(e), **classify_proxy_error(str(e))}
            except Exception as e:
                return {'ok': False, 'detail': str(e), **classify_proxy_error(str(e))}

        result['direct'] = _probe(via_proxy=False)
        result['proxy'] = _probe(via_proxy=True) if proxy else {'ok': None, 'detail': 'no proxy configured'}
        result['usable'] = bool(result['direct'].get('ok') or result['proxy'].get('ok'))
        # The IP the user must whitelist in the Bright Data zone. Detect it whenever the
        # proxy is not working, so the UI can name it explicitly.
        if proxy and not result['proxy'].get('ok'):
            result['public_ip'] = detect_public_ip()
        return result

    def _combine_autobuy(self, token, pulse_url, pulse_body, buy_side='second', sell_fee=None):
        """Combine a buy-side market's min price (from pulse) with CSFloat's highest
        buy order (from the cached sweep). `buy_side` selects which pulse market holds
        the buy price: 'first' = Tradeon (the query's first market), 'second' = the
        target market (LisSkins/Buff). Only owned items with a cached CSFloat buy order
        appear. Shaped like the other profiles so the UI renders it unchanged.
        `sell_fee` is the CSFloat seller fee to net (default: its registry fee).
        """
        if sell_fee is None:
            sell_fee = self.market_fee('CsFloat')
        cache = self.get_csfloat_buy_orders_cache() or {}
        by_name = cache.get('by_name', {})
        if not by_name:
            return []

        items = self._post_tradeon(pulse_url, token, pulse_body)
        key = 'firstMarket' if buy_side == 'first' else 'secondMarket'
        combined = []
        for it in items:
            name = (it.get('itemName') or {}).get('marketHashName')
            buy_market = it.get(key) or {}
            buy = buy_market.get('price')
            order = by_name.get(name)
            if not name or buy is None or not buy or not order:
                continue
            sell_price = order['price']
            net_sell = sell_price * (1 - sell_fee)
            profit = net_sell - buy
            combined.append({
                'itemName': it.get('itemName'),
                'imageUrl': it.get('imageUrl'),
                'firstMarket': buy_market,                              # Buy @ buy-side market
                'secondMarket': {'price': sell_price, 'count': order.get('qty')},  # Sell into CSFloat buy order
                'profit': round(profit, 3),
                'profitPercent': round(profit / buy * 100, 2),
            })

        combined.sort(key=lambda x: x['profitPercent'], reverse=True)
        return combined

    def fetch_tradeon_csfloat_autobuy(self, token, settings=None):
        return self._combine_autobuy(token, _TRADEON_CSFLOAT_URL, _TRADEON_CSFLOAT_BODY, buy_side='first',
                                     sell_fee=self.market_fee('CsFloat', settings))

    def fetch_lisskins_csfloat_autobuy(self, token, settings=None):
        return self._combine_autobuy(token, _TRADEON_LISSKINS_URL, _TRADEON_LISSKINS_BODY, buy_side='second',
                                     sell_fee=self.market_fee('CsFloat', settings))

    def fetch_buff_csfloat_autobuy(self, token, settings=None):
        return self._combine_autobuy(token, _TRADEON_BUFF_URL, _TRADEON_BUFF_BUY_BODY, buy_side='second',
                                     sell_fee=self.market_fee('CsFloat', settings))

    def fetch_dmarket_csfloat_autobuy(self, token, settings=None):
        return self._combine_autobuy(token, _TRADEON_DMARKET_URL, _TRADEON_DMARKET_BUY_BODY, buy_side='second',
                                     sell_fee=self.market_fee('CsFloat', settings))

    # ---- Live price map (portfolio valuation) ------------------------------

    # market slug -> (pulse url, second-market price type used as the "current price").
    # We use each market's lowest listing (Sell) — the standard "market price" a
    # portfolio is worth — matching what price-tracker exports call current price.
    _PRICE_MARKETS = {
        'steam':    (_TRADEON_STEAM_URL,    'Sell'),
        'buff':     (_TRADEON_BUFF_URL,     'Sell'),
        'csfloat':  (_TRADEON_CSFLOAT_URL,  'Sell'),
        'dmarket':  (_TRADEON_DMARKET_URL,  'Sell'),
        'lisskins': (_TRADEON_LISSKINS_URL, 'SellWithoutHold'),
    }

    def _single_price_map(self, token, market):
        url, sell_type = self._PRICE_MARKETS[market]
        body = copy.deepcopy(_TRADEON_STEAM_BODY)
        body['secondMarketOptions']['secondMarketPriceType'] = sell_type
        out = {}
        for it in self._post_tradeon(url, token, body):
            name = (it.get('itemName') or {}).get('marketHashName')
            sm = it.get('secondMarket') or {}
            price = sm.get('price')
            if name and price is not None and price:
                out[name] = price
        return out

    # Portfolio valuation prices are cached this long (per market). Skin prices
    # barely move minute-to-minute, so hour-stale is fine for tracking — and it
    # keeps every Draupnir page load from re-hitting pulse. Tweak freely.
    _PRICE_CACHE_TTL_SEC = 60 * 60   # 1 hour

    def _compute_price_map(self, token, market):
        if market == 'lowest':
            merged = {}
            for m in ('steam', 'buff', 'csfloat'):
                for name, price in self._single_price_map(token, m).items():
                    if name not in merged or price < merged[name]:
                        merged[name] = price
            return merged
        if market not in self._PRICE_MARKETS:
            market = 'steam'
        return self._single_price_map(token, market)

    def _normalize_price_market(self, market):
        """A known valuation market slug ('lowest' or a _PRICE_MARKETS key); anything
        else (a typo, a request-supplied junk value) becomes 'steam', which is what
        _compute_price_map already priced it as — so unknown strings never become
        cache keys or background warms of their own."""
        if market == 'lowest' or market in self._PRICE_MARKETS:
            return market
        return 'steam'

    def price_map(self, token, market='steam', force=False):
        """market_hash_name -> current unit price (USD) on the chosen reference market.

        BLOCKING: fetches from pulse if the cache is cold/stale. Prefer
        prices_for_valuation() for request paths — it never blocks. Result is
        cached per market for _PRICE_CACHE_TTL_SEC; pass force=True to refetch."""
        market = self._normalize_price_market(market)
        cached = self._price_cache.get(market)
        if not force and cached and (time.time() - cached[0]) < self._PRICE_CACHE_TTL_SEC:
            return cached[1]
        prices = self._compute_price_map(token, market)
        with self._price_lock:
            self._price_cache[market] = (time.time(), prices)
            self._price_state[market] = 'ok'
            self._price_cache_gen += 1
        return prices

    def known_item_names(self, token, block=True):
        """Set of every market_hash_name pulse knows about (the CS item universe),
        unioned across whatever price maps are cached. With block=True, falls back
        to a one-time blocking Steam fetch if nothing is cached yet (used for the
        one-off validate check); block=False stays non-blocking (used for typeahead
        on every keystroke)."""
        with self._price_lock:
            gen = self._price_cache_gen
            cache = self._known_names_cache
            if cache is not None and cache[0] == gen:
                # Cached non-empty union — callers only ever union it, never mutate.
                return cache[1]
            names = set()
            for _, m in self._price_cache.values():
                names.update(m.keys())
            if names:
                self._known_names_cache = (gen, names)
        if not names and block and token:
            try:
                names = set()  # fresh set; nothing non-empty was cached
                names.update(self.price_map(token, 'steam').keys())
            except Exception as e:
                logger.error(f'[DRAUPNIR] known_item_names steam fetch failed: {e}')
        return names

    def _refresh_prices_bg(self, token, market):
        try:
            prices = self._compute_price_map(token, market)
            with self._price_lock:
                self._price_cache[market] = (time.time(), prices)
                self._price_state[market] = 'ok'
                self._price_cache_gen += 1
        except Exception as e:
            logger.error(f'[DRAUPNIR] price refresh failed ({market}): {e}')
            with self._price_lock:
                self._price_state[market] = 'error'
                self._price_failed_at[market] = time.time()

    def prices_for_valuation(self, token, market='steam'):
        """NON-BLOCKING. Returns (prices_or_None, status) for portfolio valuation.

        Serves the cached price map instantly. When the cache is cold or stale,
        it kicks off a single background refresh (one per market at a time) and
        returns immediately with whatever we have (stale cache, or None on a cold
        start) so the page never waits on pulse. status is one of:
        'no_token', 'fresh', 'refreshing', 'error'.

        A refresh that failed is not retried for _WARM_RETRY_AFTER_FAILURE_SEC;
        within that window the status is 'error' (stale prices still served) so
        the page stops polling instead of restarting a doomed pull on every call."""
        market = self._normalize_price_market(market)
        with self._price_lock:
            cached = self._price_cache.get(market)
        if not token:
            return (cached[1] if cached else None), 'no_token'
        if cached and (time.time() - cached[0]) < self._PRICE_CACHE_TTL_SEC:
            return cached[1], 'fresh'
        # cold or stale → refresh in the background (single-flight per market)
        with self._price_lock:
            state = self._price_state.get(market)
            backing_off = (state == 'error' and time.time() - self._price_failed_at.get(market, 0)
                           < _WARM_RETRY_AFTER_FAILURE_SEC)
            if state != 'refreshing' and not backing_off:
                self._price_state[market] = 'refreshing'
                threading.Thread(target=self._refresh_prices_bg,
                                 args=(token, market), daemon=True).start()
                state = 'refreshing'
        if state == 'error':
            return (cached[1] if cached else None), 'error'
        if cached:
            return cached[1], 'refreshing'          # serve stale while warming
        return None, 'refreshing'

    # ---- Container price tracker ("Case Arbitrage") ------------------------

    # Markets a container is compared across (all shown as prices). 'tradeon' is the
    # TradeOnMarket lowest listing (the firstMarket side of every pulse row) — a
    # buy-only source here.
    _CONTAINER_MARKETS = ('steam', 'buff', 'csfloat', 'lisskins', 'dmarket', 'tradeon',
                          'csmoney_market', 'csmoney_trade', 'skinswap')
    # Buy-only price sources read from the generic pulse market table (their cheapest
    # listing). CS.MONEY Trade and SkinSwap price in their own trade balance.
    _CONTAINER_REGISTRY_MARKETS = {'csmoney_market': 'CsMoneyMarket', 'csmoney_trade': 'CsMoneyTrade',
                                   'skinswap': 'SkinSwapMarket'}
    # Markets you can realistically CASH OUT on (drives the "best flip" + profit
    # filters). DMarket is excluded on purpose: its pulse "Sell" price is often
    # unfillable ("unavailable"), so a flip that targets it is noise. LisSkins is a
    # buy-only source here (no seller fee modelled). Revisit if that changes.
    _CONTAINER_SELL_MARKETS = {'steam': 'Steam', 'buff': 'Buff', 'csfloat': 'CsFloat'}

    def _container_sell_fees(self):
        """{sell market: fee} for the markets flips cash out on, from the Fees editor."""
        return {key: self.market_fee(market_id) for key, market_id in self._CONTAINER_SELL_MARKETS.items()}
    # Markets whose pulse price is shown but excluded from the cheapest/dearest/spread
    # math because it's not actionable (DMarket "Sell" prices are often unfillable).
    # CS.MONEY Trade and SkinSwap price in their own trade balance, but they DO count
    # as places to buy (cheapest, flip): Ivan buys there with balance (2026-10-01).
    _CONTAINER_NOISE_MARKETS = frozenset({'dmarket'})
    # The long-horizon price history (lo/hi/f, trend, the "hot" baseline, the
    # bottom-call check) keeps the markets it was built from, so a newly added,
    # cheaper source (CS.MONEY Market, 2026-09-30) never shows up as a price drop.
    _CONTAINER_HISTORY_MARKETS = frozenset({'steam', 'buff', 'csfloat', 'lisskins', 'tradeon'})

    @staticmethod
    def _best_container_flip(buy_market, buy_price, prices, sell_fees):
        """Best buy-at-`buy_price` -> sell-net-of-fee flip over the sell markets, or None."""
        best = None
        for sell_market, fee in sell_fees.items():
            sell_price = prices.get(sell_market)
            if sell_price is None:
                continue
            net = sell_price * (1 - fee)
            profit = net - buy_price
            if best is None or profit > best['profit']:
                best = {'buy_market': buy_market, 'sell_market': sell_market,
                        'net_sell': round(net, 2), 'profit': round(profit, 2),
                        'profit_pct': round(profit / buy_price * 100, 2) if buy_price else None}
        return best
    # Price history is a long-horizon research asset (multi-year rotation
    # patterns, the 6-month bottom-call verification), so we keep it effectively
    # forever. To stay light, entries older than _CASE_HISTORY_FULL_DAYS are
    # compacted from the rich per-market dict down to just the day's low (a bare
    # float, which _hist_price already reads); a full recent year keeps full
    # detail. _CASE_HISTORY_MAX_DAYS is only a 10-year safety ceiling.
    _CASE_HISTORY_FULL_DAYS = 365
    _CASE_HISTORY_MAX_DAYS = 3650

    def _load_containers(self, categories=None):
        """Bundled container catalog ('case', 'sticker', 'souvenir', 'autograph').

        The file is immutable during a run, so it's parsed once and cached; an
        optional category filter returns a fresh filtered list off the cache."""
        if self._containers_all is None:
            try:
                with open(CONTAINERS_FILE, 'r', encoding='utf-8') as f:
                    self._containers_all = json.load(f).get('containers', [])
            except Exception as e:
                logger.error(f'[HUGINN] container catalog load failed: {e}')
                return []
        items = self._containers_all
        if categories:
            wanted = set(categories)
            items = [c for c in items if c.get('category') in wanted]
        return items

    def _container_names(self):
        if self._container_names_set is None:
            self._container_names_set = {c['name'] for c in self._load_containers(None)}
        return self._container_names_set

    # --- container market snapshots (price + listing count, non-blocking) ---

    def _single_container_map(self, token, market):
        """{name: {price, count}} for container names only, on one market. For
        'tradeon' we read the firstMarket side (TradeOnMarket's own lowest listing),
        which every pulse row already carries; for the rest we read secondMarket."""
        names = self._container_names()
        registry_id = self._CONTAINER_REGISTRY_MARKETS.get(market)
        if registry_id:
            # The market's cheapest listing (the registry's buy price type), read only
            # for tracked containers instead of indexing the whole ~17k-item table.
            out = {}
            for it in self._pull_market(token, registry_id, _MARKET_BY_ID[registry_id]['buy_type']):
                name = (it.get('itemName') or {}).get('marketHashName')
                market_prices = it.get('secondMarket') or {}
                if name in names and market_prices.get('price'):
                    out[name] = {'price': market_prices['price'],
                                 'count': market_prices.get('totalOffersCount') or market_prices.get('count')}
            return out
        body = copy.deepcopy(_TRADEON_STEAM_BODY)
        if market == 'tradeon':
            url = _TRADEON_STEAM_URL
            side = 'firstMarket'
        else:
            url, sell_type = self._PRICE_MARKETS[market]
            body['secondMarketOptions']['secondMarketPriceType'] = sell_type
            side = 'secondMarket'
        out = {}
        for it in self._post_tradeon(url, token, body):
            name = (it.get('itemName') or {}).get('marketHashName')
            if name not in names:
                continue
            mk = it.get(side) or {}
            price = mk.get('price')
            if not price:
                continue
            out[name] = {'price': price, 'count': mk.get('totalOffersCount')}
        return out

    def _refresh_container_bg(self, token, market):
        try:
            snap = self._single_container_map(token, market)
            with self._price_lock:
                self._container_cache[market] = (time.time(), snap)
                self._container_state[market] = 'ok'
        except Exception as e:
            logger.error(f'[HUGINN] container snapshot refresh failed ({market}): {e}')
            with self._price_lock:
                self._container_state[market] = 'error'
                self._container_failed_at[market] = time.time()

    def _container_snapshot(self, token, market):
        """NON-BLOCKING {name:{price,count}} for one market. Serves cache instantly,
        warms cold/stale in the background (single-flight per market), like
        prices_for_valuation. status: 'no_token'|'fresh'|'refreshing'|'error'.
        A failed refresh is not retried for _WARM_RETRY_AFTER_FAILURE_SEC; the
        status is 'error' in that window (stale snapshot still served)."""
        with self._price_lock:
            cached = self._container_cache.get(market)
        if not token:
            return (cached[1] if cached else {}), 'no_token'
        if cached and (time.time() - cached[0]) < self._PRICE_CACHE_TTL_SEC:
            return cached[1], 'fresh'
        with self._price_lock:
            state = self._container_state.get(market)
            backing_off = (state == 'error' and time.time() - self._container_failed_at.get(market, 0)
                           < _WARM_RETRY_AFTER_FAILURE_SEC)
            if state != 'refreshing' and not backing_off:
                self._container_state[market] = 'refreshing'
                threading.Thread(target=self._refresh_container_bg,
                                 args=(token, market), daemon=True).start()
                state = 'refreshing'
        if state == 'error':
            return (cached[1] if cached else {}), 'error'
        if cached:
            return cached[1], 'refreshing'
        return {}, 'refreshing'

    # --- history (daily cheapest price + spread) ---

    @staticmethod
    def _file_signature(path):
        """(modification time in nanoseconds, size) of *path*, or None if missing."""
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _load_case_history(self):
        """The parsed price history. Kept in memory and re-parsed only when the
        file changes (it is ~3 MB and every /api/huginn/cases call reads it). The
        returned dict is the live in-memory copy: only cases_prices mutates it, and
        only while holding _case_history_lock."""
        with self._case_history_lock:
            signature = self._file_signature(CASE_HISTORY_FILE)
            cached = self._case_history_cache
            if signature is not None and cached is not None and cached[0] == signature:
                return cached[1]
            try:
                with open(CASE_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except Exception:
                return {}
            self._case_history_cache = (signature, history)
            return history

    @classmethod
    def _compact_history(cls, history, full_cutoff):
        """Return *history* with each series capped at the 10-year ceiling and
        entries older than *full_cutoff* (an ISO date) collapsed from the rich
        per-market dict to the day's low float. Pure — no I/O — so it's testable
        and can't corrupt the live file."""
        out = {}
        for name, series in history.items():
            items = sorted(series.items())
            if len(items) > cls._CASE_HISTORY_MAX_DAYS:
                items = items[-cls._CASE_HISTORY_MAX_DAYS:]
            compacted = {}
            for day, entry in items:
                if day < full_cutoff and isinstance(entry, dict):
                    low = cls._hist_price(entry)  # dict -> day's low float
                    compacted[day] = low if low is not None else entry
                else:
                    compacted[day] = entry
            out[name] = compacted
        return out

    def _save_case_history(self, history):
        """Persist snapshots. Old entries are compacted (not dropped) to keep a
        long research history without unbounded file growth: beyond
        _CASE_HISTORY_FULL_DAYS the rich per-market dict is collapsed to the
        day's low; a 10-year ceiling is the only hard cap."""
        try:
            full_cutoff = (datetime.now(timezone.utc).date()
                           - timedelta(days=self._CASE_HISTORY_FULL_DAYS)
                           ).isoformat()
            history = self._compact_history(history, full_cutoff)
            with self._case_history_lock:
                atomic_write_json(CASE_HISTORY_FILE, history, indent=None)
                # What is on disk now IS this dict — keep it as the in-memory copy
                # so the next read does not re-parse the file just written.
                self._case_history_cache = (self._file_signature(CASE_HISTORY_FILE), history)
        except Exception as e:
            logger.error('case history save failed: %s', e)
            with self._case_history_lock:
                self._case_history_cache = None   # re-read what is really on disk

    @staticmethod
    def _hist_price(entry):
        """Representative daily price = the day's low. Handles new {'lo','hi',...},
        the interim {'p',...}, and the oldest bare-float form."""
        if isinstance(entry, dict):
            return entry.get('lo', entry.get('p'))
        return entry

    @staticmethod
    def _hist_profit(entry):
        """Net flip % stored in a snapshot entry ({'p':price,'f':profit_pct})."""
        return entry.get('f') if isinstance(entry, dict) else None

    @staticmethod
    def _median(vals):
        s = sorted(v for v in vals if v is not None)
        if not s:
            return None
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    @staticmethod
    def _percentile(vals, pct):
        s = sorted(v for v in vals if v is not None)
        if not s:
            return None
        k = (len(s) - 1) * pct / 100.0
        f = int(k)
        c = min(f + 1, len(s) - 1)
        return s[f] if f == c else s[f] + (s[c] - s[f]) * (k - f)

    def _history_trend(self, series, current, dates=None):
        """% change of cheapest price vs ~7 daily snapshots ago (nearest earlier).

        `dates` may be passed pre-sorted to avoid re-sorting the series."""
        if not series or current is None:
            return None
        dates = dates if dates is not None else sorted(series.keys())
        if len(dates) < 2:
            return None
        prior = self._hist_price(series[dates[-8] if len(dates) >= 8 else dates[0]])
        if not prior:
            return None
        return round((current - prior) / prior * 100, 2)

    def _history_sparkline(self, series, points=30, dates=None):
        if not series:
            return []
        dates = dates if dates is not None else sorted(series.keys())
        out = [self._hist_price(series[d]) for d in dates[-points:]]
        return [p for p in out if p is not None]

    def _price_rows_recording_history(self, containers, snaps, today):
        """Per-container rows for cases_prices, recording today's lo/hi into the
        price history as a side effect. Callers hold _case_history_lock."""
        history = self._load_case_history()
        sell_fees = self._container_sell_fees()
        dirty = False
        rows = []
        for c in containers:
            name = c['name']
            prices, counts = {}, {}
            for m in self._CONTAINER_MARKETS:
                entry = snaps[m].get(name)
                if entry is not None:
                    prices[m] = round(float(entry['price']), 2)
                    counts[m] = entry.get('count')
            row = {**c, 'prices': prices, 'counts': counts}
            # cheapest over actionable markets only (exclude noise like DMarket);
            # noise prices still ride along in `prices` for display.
            tradeable = {m: p for m, p in prices.items() if m not in self._CONTAINER_NOISE_MARKETS}
            if tradeable:
                cheapest_market = min(tradeable, key=tradeable.get)
                cheapest = tradeable[cheapest_market]
                row['cheapest_market'] = cheapest_market
                row['cheapest'] = cheapest
                # liquidity: listings available where you'd buy (cheapest market)
                row['liquidity'] = counts.get(cheapest_market)
                row['total_listings'] = sum(counts[m] for m in tradeable if counts.get(m)) or None
                # best flip over markets you can actually cash out on (net of fee)
                best = self._best_container_flip(cheapest_market, cheapest, prices, sell_fees)
                row['flip'] = best
                # The history (and trend / hot, which compare against it) is recorded
                # over its original markets only; see _CONTAINER_HISTORY_MARKETS.
                history_prices = {m: p for m, p in tradeable.items() if m in self._CONTAINER_HISTORY_MARKETS}
                if not history_prices:
                    # Priced only by a newly added source: record nothing today, so the
                    # history never mixes in a market it was not built from.
                    series = history.get(name) or {}
                    row['trend_pct'] = None
                    row['sparkline'] = self._history_sparkline(series) if series else []
                    row['profit_vs_norm'] = None
                    row['_hot_temporal'] = False
                    rows.append(row)
                    continue
                history_market = min(history_prices, key=history_prices.get)
                history_cheapest = history_prices[history_market]
                history_flip = self._best_container_flip(history_market, history_cheapest, prices, sell_fees)
                profit_pct = history_flip['profit_pct'] if history_flip else None
                # history: per-day min/max of the cheapest price + per-market min/max.
                # Updated every run (loop every ~10min, page views) so lo/hi capture the
                # true daily range, not just the first reading. Entry:
                #   {lo, hi: cheapest range, f: best flip %, mk:{market:[lo,hi]}}
                series = history.get(name)
                if series is None:
                    series = history[name] = {}
                e = series.get(today)
                if not isinstance(e, dict) or 'lo' not in e:
                    e = {'lo': history_cheapest, 'hi': history_cheapest, 'f': profit_pct, 'mk': {}}
                    series[today] = e
                    dirty = True
                if history_cheapest < e['lo']:
                    e['lo'] = history_cheapest; dirty = True
                if history_cheapest > e['hi']:
                    e['hi'] = history_cheapest; dirty = True
                if profit_pct is not None and (e.get('f') is None or profit_pct > e['f']):
                    e['f'] = profit_pct; dirty = True
                mk = e.setdefault('mk', {})
                for _m, _p in prices.items():
                    cur = mk.get(_m)
                    if cur is None:
                        mk[_m] = [_p, _p]; dirty = True
                    else:
                        if _p < cur[0]:
                            cur[0] = _p; dirty = True
                        if _p > cur[1]:
                            cur[1] = _p; dirty = True
                days = sorted(series.keys())  # sort once, reuse for all three reads
                row['trend_pct'] = self._history_trend(series, history_cheapest, dates=days)
                row['sparkline'] = self._history_sparkline(series, dates=days)
                # temporal "hot": today's net profit % vs this item's own median
                priors = [self._hist_profit(series[d]) for d in days[:-1]]
                priors = [f for f in priors if f is not None]
                if len(priors) >= 5 and profit_pct is not None:
                    med = self._median(priors)
                    row['profit_vs_norm'] = round(profit_pct / med, 2) if med else None
                    row['_hot_temporal'] = bool(med and profit_pct >= 1.3 * med and profit_pct > 0)
                else:
                    row['profit_vs_norm'] = None
                    row['_hot_temporal'] = False
            rows.append(row)
        if dirty:
            self._save_case_history(history)
        return rows

    def cases_prices(self, token, categories=None):
        """Price every tracked container across all markets. Per item: cheapest
        market to buy on, listing counts (liquidity), the best net-of-fee flip over
        *sellable* markets, a daily trend + sparkline, and a 'hot' flag for containers
        that are unusually profitable today. Non-blocking: serves cached snapshots and
        warms cold ones in the background, like portfolio valuation."""
        containers = self._load_containers(categories)
        snaps, status = {}, {}
        for m in self._CONTAINER_MARKETS:
            snap, st = self._container_snapshot(token, m)
            snaps[m] = snap or {}
            status[m] = st
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        # Load, update and save the history under one lock: the refresh loop, the
        # alert run and page requests all record into it concurrently.
        with self._case_history_lock:
            rows = self._price_rows_recording_history(containers, snaps, today)
        # cross-sectional "hot": today's most profitable across the priced set (works
        # from day one, before any history exists). 85th percentile, floored at 5%.
        profits = [(r.get('flip') or {}).get('profit_pct') for r in rows]
        profits = [p for p in profits if p is not None]
        thresh = max(self._percentile(profits, 85) or 0, 5.0) if profits else None
        for r in rows:
            pp = (r.get('flip') or {}).get('profit_pct')
            hot_x = thresh is not None and pp is not None and pp >= thresh
            r['hot_today'] = bool(r.pop('_hot_temporal', False) or hot_x)
        return {
            'containers': rows,
            'status': status,
            'markets': list(self._CONTAINER_MARKETS),
            'sell_markets': list(self._CONTAINER_SELL_MARKETS),
            'sell_fees': self._container_sell_fees(),
            'hot_threshold_pct': round(thresh, 2) if thresh is not None else None,
            'count': len(rows),
            'priced': sum(1 for r in rows if r.get('prices')),
            'updated': today,
        }

    # --- hourly background pull (keeps the cache warm without a page view) ---

    _CONTAINER_FULL_REFRESH_SEC = 3600   # full 6-market pull + history cadence

    def start_container_refresh(self, settings_provider, default_interval=600):
        """Background loop with two cadences: a FULL pull of every market + daily history
        every hour (keeps the UI warm without a page view), and a fast poll of the
        alert markets (CSFloat and the buy markets) every `case_poll_interval_sec` (default
        10min) that fires price alerts on new crossings. pulse reprices CSFloat ~1min
        / Steam ~5min, so hourly alone is too slow. `settings_provider` returns the
        current settings dict. Idempotent — one loop per service instance."""
        if getattr(self, '_container_refresh_started', False):
            return
        self._container_refresh_started = True
        self._sweep_stale_temporary_files()
        self._load_container_snapshots()
        threading.Thread(target=self._container_refresh_loop,
                         args=(settings_provider, default_interval), daemon=True).start()
        logger.info(f'[HUGINN] container refresh loop started (poll default {default_interval}s)')

    @staticmethod
    def _sweep_stale_temporary_files(directory=None, max_age=_STALE_TEMPORARY_FILE_AGE_SEC):
        """Delete leftover '.tmp-*.json' files in cache/ older than *max_age* — the
        partial writes atomic_write_json leaves only when the process is killed
        mid-write. Only that exact name pattern is touched. Returns the count removed."""
        directory = directory or os.path.dirname(CACHE_PATH)
        removed = 0
        cutoff = time.time() - max_age
        try:
            entries = os.listdir(directory)
        except OSError:
            return 0
        for entry in entries:
            if not (entry.startswith('.tmp-') and entry.endswith('.json')):
                continue
            path = os.path.join(directory, entry)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError as e:
                logger.warning(f'[HUGINN] could not remove stale temporary file {entry}: {e}')
        if removed:
            logger.info(f'[HUGINN] removed {removed} stale temporary file(s) from cache/')
        return removed

    def _load_container_snapshots(self, path=None):
        """Restore the last full refresh's per-market snapshots and its time, so a
        restart (every werkzeug reload) serves warm prices and does not re-run the
        six-market pull until the full-refresh interval has really passed."""
        path = path or CONTAINER_SNAPSHOT_FILE
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            logger.warning(f'[HUGINN] container snapshot file unreadable, ignoring: {e}')
            return
        with self._price_lock:
            for market, entry in (data.get('markets') or {}).items():
                if market not in self._CONTAINER_MARKETS or market in self._container_cache:
                    continue
                try:
                    fetched_at, snapshot = float(entry[0]), dict(entry[1])
                except (TypeError, ValueError, IndexError, KeyError):
                    continue
                self._container_cache[market] = (fetched_at, snapshot)
                self._container_state[market] = 'ok'
        try:
            self._container_last_full = float(data.get('last_full') or 0)
        except (TypeError, ValueError):
            self._container_last_full = 0.0

    def _save_container_snapshots(self, last_full, path=None):
        path = path or CONTAINER_SNAPSHOT_FILE
        with self._price_lock:
            markets = {m: [ts, snapshot] for m, (ts, snapshot) in self._container_cache.items()}
        try:
            atomic_write_json(path, {'last_full': last_full, 'markets': markets}, indent=None)
        except Exception as e:
            logger.error(f'[HUGINN] container snapshot save failed: {e}')

    def _refresh_one(self, token, market):
        try:
            snap = self._single_container_map(token, market)
            with self._price_lock:
                self._container_cache[market] = (time.time(), snap)
                self._container_state[market] = 'ok'
        except Exception as e:
            logger.error(f'[HUGINN] container refresh failed ({market}): {e}')
            with self._price_lock:
                self._container_state[market] = 'error'
                self._container_failed_at[market] = time.time()

    def _refresh_markets(self, token, markets, parallel=False):
        """Refresh each market's snapshot. parallel=True fetches them concurrently so
        they're captured within the same few seconds — important for the alert markets
        so a cross-market comparison isn't skewed by prices moving between fetches."""
        if parallel and len(markets) > 1:
            ts = [threading.Thread(target=self._refresh_one, args=(token, m), daemon=True) for m in markets]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        else:
            for m in markets:
                self._refresh_one(token, m)

    def _container_refresh_loop(self, settings_provider, default_interval):
        # Persisted by the previous process, so a reload does not force an
        # immediate full pull (0 when there is no snapshot file yet).
        last_full = self._container_last_full
        while True:
            interval = default_interval
            try:
                settings = settings_provider() if callable(settings_provider) else (settings_provider or {})
                settings = settings or {}
                token = settings.get('tradeon_token') or ''
                try:
                    interval = max(60, int(settings.get('case_poll_interval_sec') or default_interval))
                except (TypeError, ValueError):
                    interval = default_interval
                alerts_on = bool(settings.get('case_alerts_enabled')) and notification_channel(settings) is not None
                if token:
                    now = time.time()
                    full = (now - last_full) >= self._CONTAINER_FULL_REFRESH_SEC
                    if full:
                        self._refresh_markets(token, self._CONTAINER_MARKETS)
                        try:
                            self.cases_prices(token, None)   # daily history (own once/day guard)
                        except Exception as e:
                            logger.error(f'[HUGINN] history record failed: {e}')
                        last_full = now
                        self._save_container_snapshots(last_full)
                        logger.info('[HUGINN] full container refresh from pulse')
                    elif alerts_on:
                        self._refresh_markets(token, self._ALERT_MARKETS, parallel=True)
                    if alerts_on:
                        try:
                            res = self.run_case_alerts(settings)
                            if res.get('new'):
                                logger.info(f"[HUGINN] case alerts: {res['new']} new, sent={res.get('sent')}")
                            if res.get('send_error'):
                                logger.warning(f"[HUGINN] case alert send failed (retried next poll): "
                                               f"{res['send_error']}")
                        except Exception as e:
                            logger.error(f'[HUGINN] case alerts failed: {e}')
                else:
                    logger.info('[HUGINN] container refresh skipped — no tradeon_token yet')
            except Exception as e:
                logger.error(f'[HUGINN] container refresh loop error: {e}')
            time.sleep(interval)

    # --- Case Arbitrage price alerts (a buy market cheaper than CSFloat) ----

    # Where you buy: each one that is cheaper than CSFloat is an alert.
    _ALERT_BUY_MARKETS = ('lisskins', 'buff', 'tradeon', 'csmoney_market', 'csmoney_trade', 'skinswap')
    # Re-pulled every alert poll, together, so the comparison is near-simultaneous.
    _ALERT_MARKETS = ('csfloat',) + _ALERT_BUY_MARKETS
    _ALERT_MARKET_LABEL = {'lisskins': 'LisSkins', 'buff': 'Buff', 'tradeon': 'Tradeon',
                           'csmoney_market': 'CS.MONEY Market', 'csmoney_trade': 'CS.MONEY Trade',
                           'skinswap': 'SkinSwap'}
    # Don't re-PING the same (case,market) more often than this even if it flickers
    # out and back in (the board still edits silently). New deals still ping instantly.
    _ALERT_NOTIFY_COOLDOWN_SEC = 3600
    _MARKET_SLUG = {'steam': 'Steam', 'buff': 'Buff', 'csfloat': 'CsFloat',
                    'lisskins': 'LisSkins', 'dmarket': 'Dmarket', 'tradeon': 'TradeOnMarket',
                    'csmoney_market': 'CsMoneyMarket', 'csmoney_trade': 'CsMoneyTrade',
                    'skinswap': 'SkinSwapMarket'}

    def _pulse_link(self, market_key, name):
        """pulse short-link that 302-redirects to the item's page on a given market."""
        slug = self._MARKET_SLUG.get(market_key)
        if not slug:
            return None
        return f'https://short-pulse.tradeon.space/short-link/CsGo/{slug}/{urllib.parse.quote(name)}'

    @staticmethod
    def _esc(s):
        return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def _load_alert_state(self):
        try:
            with open(CASE_ALERT_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_alert_state(self, state):
        try:
            atomic_write_json(CASE_ALERT_STATE_FILE, state, indent=1)
        except Exception as e:
            logger.error(f'[HUGINN] alert state save failed: {e}')

    def _format_alert_messages(self, alerts, owned_map=None):
        """(plain, html) for the board, with fewer cases per section until it fits one
        Telegram message (4096 visible characters; link addresses do not count)."""
        per_section = 25
        while True:
            plain, html = self._render_alert_board(alerts, owned_map, per_section)
            if telegram_visible_length(html) <= TELEGRAM_MESSAGE_LIMIT or per_section <= 1:
                return plain, html
            per_section -= 1

    def _render_alert_board(self, alerts, owned_map, per_section):
        """Return (plain, html). Grouped one block per case (all its cheaper markets
        together), split into 'In your inventory' vs 'Not in your inventory' (from the
        Huginn scan), biggest discount first, a blank line between cases. html has <a>
        links for Telegram (parse_mode=HTML); plain has raw URLs for a webhook."""
        owned_map = owned_map or {}
        by_case = {}
        for a in alerts:
            g = by_case.get(a['name'])
            if g is None:
                info = owned_map.get(a['name'])
                g = by_case[a['name']] = {
                    'name': a['name'], 'csfloat': a['csfloat'], 'markets': [],
                    'owned': bool(info), 'owned_count': (info or {}).get('count', 0),
                }
            g['markets'].append(a)
        cases = list(by_case.values())
        for g in cases:
            g['best'] = max(g['markets'], key=lambda m: m['pct'])
        cases.sort(key=lambda g: g['best']['pct'], reverse=True)

        n = len(cases)
        head = f"\U0001F3AF Case Arbitrage — {n} case{'s' if n != 1 else ''} cheaper than CSFloat"
        plain, html = [head], [self._esc(head)]

        def block(g):
            mk = sorted(g['markets'], key=lambda m: m['price'])
            cf, best = g['csfloat'], g['best']
            cnt = f" ×{g['owned_count']}" if g['owned'] and g['owned_count'] else ""
            tail = f"(-${best['abs']:.2f}, -{best['pct']:.1f}%)"
            lbl = lambda m: self._ALERT_MARKET_LABEL.get(m['market'], m['market'])
            p_mk = ' · '.join(f"{lbl(m)} ${m['price']:.2f}" for m in mk)
            h_mk = ' · '.join(f"<a href=\"{self._pulse_link(m['market'], g['name'])}\">{lbl(m)}</a> ${m['price']:.2f}" for m in mk)
            cf_url = self._pulse_link('csfloat', g['name'])
            buy_url = self._pulse_link(mk[0]['market'], g['name'])
            p = [f"• {g['name']}{cnt}", f"   {p_mk}  vs CSFloat ${cf:.2f}  {tail}", f"   \U0001F517 {buy_url}"]
            h = [f"• {self._esc(g['name'])}{cnt}", f"   {h_mk}  vs <a href=\"{cf_url}\">CSFloat</a> ${cf:.2f}  {tail}"]
            return p, h

        def section(title, group):
            if not group:
                return
            plain.extend(['', title])
            html.extend(['', f"<b>{self._esc(title)}</b>"])
            for g in group[:per_section]:
                p, h = block(g)
                plain.append(''); plain.extend(p)
                html.append(''); html.extend(h)
            if len(group) > per_section:
                more = f"+{len(group) - per_section} more in Huginn → Case Arbitrage"
                plain.extend(['', more]); html.extend(['', f"<i>{self._esc(more)}</i>"])

        if owned_map:
            owned = [g for g in cases if g['owned']]
            notowned = [g for g in cases if not g['owned']]
            section(f"\U0001F4E6 In your inventory ({len(owned)})", owned)
            section(f"\U0001F195 Not in your inventory ({len(notowned)})", notowned)
        else:
            for g in cases[:per_section]:
                p, h = block(g)
                plain.append(''); plain.extend(p)
                html.append(''); html.extend(h)
            plain.extend(['', "(run 'Get all items' in Huginn to tag which you own)"])
            html.extend(['', "<i>(run 'Get all items' in Huginn to tag which you own)</i>"])
        # local (container TZ) time so it matches the Telegram bubble's clock; changing
        # every tick makes each silent edit visibly refresh — a "still flying" heartbeat.
        stamp = datetime.now().astimezone().strftime('%H:%M')
        foot = f"⏱ Updated {stamp} — prices move fast, tap a market to verify before buying."
        plain.extend(['', foot])
        html.extend(['', f"<i>{self._esc(foot)}</i>"])
        return '\n'.join(plain), '\n'.join(html)

    def case_alert_status(self, settings):
        """Config + current active alerts, for the UI (no side effects)."""
        state = self._load_alert_state()
        return {
            'enabled': bool(settings.get('case_alerts_enabled')),
            'channel': notification_channel(settings),
            'min_pct': settings.get('case_alert_min_pct', 3.0),
            'categories': settings.get('case_alert_categories') or ['case'],
            'active': list((state.get('details') or {}).values()),
            'updated': state.get('updated'),
        }

    def run_case_alerts(self, settings, force=False, refresh=False):
        """Evaluate buy markets cheaper than CSFloat and notify on NEW crossings.
        `force=True` re-sends all currently-active alerts (used by "Check now").
        `refresh=True` re-pulls the alert markets (in parallel, near-simultaneous)
        before comparing, so a manual check reflects live prices, not a stale cache.
        Returns a summary dict.

        One run at a time: the background loop and "Check now" both read the alert
        state, talk to Telegram (one board message) and write the state back, so
        two overlapping runs would double-post or lose the board id."""
        with self._case_alert_lock:
            return self._run_case_alerts_locked(settings, force=force, refresh=refresh)

    def _run_case_alerts_locked(self, settings, force=False, refresh=False):
        if not settings.get('case_alerts_enabled') and not force:
            return {'ran': False, 'reason': 'disabled'}
        if notification_channel(settings) is None:
            return {'ran': False, 'reason': 'no channel configured'}
        token = settings.get('tradeon_token', '')
        if not token:
            return {'ran': False, 'reason': 'no tradeon_token'}
        if refresh:
            self._refresh_markets(token, self._ALERT_MARKETS, parallel=True)
        try:
            min_pct = float(settings.get('case_alert_min_pct', 3) or 0)
        except (TypeError, ValueError):
            min_pct = 3.0
        cats = settings.get('case_alert_categories') or ['case']
        data = self.cases_prices(token, cats)
        active = {}
        for r in data['containers']:
            prices = r.get('prices') or {}
            cf = prices.get('csfloat')
            if not cf:
                continue
            for m in self._ALERT_BUY_MARKETS:
                p = prices.get(m)
                if not p or p >= cf:
                    continue
                pct = round((cf - p) / cf * 100, 2)
                if pct < min_pct:
                    continue
                active[f'{r["name"]}|{m}'] = {
                    'name': r['name'], 'market': m, 'price': p, 'csfloat': cf,
                    'pct': pct, 'abs': round(cf - p, 2),
                }
        channel = notification_channel(settings)
        state = self._load_alert_state()
        prev = set(state.get('active', []))
        board_id = state.get('board_message_id')
        notified = dict(state.get('notified') or {})
        now_keys = set(active.keys())
        now_ts = time.time()

        # A genuinely-new deal = active but not active last poll, AND not pinged for
        # this exact (case,market) within the cooldown (so cent-flicker doesn't re-ping).
        fresh = [k for k in active if k not in prev]
        notify = [k for k in fresh if (now_ts - notified.get(k, 0)) > self._ALERT_NOTIFY_COOLDOWN_SEC]
        should_ping = bool(notify) or force

        result = {'ran': True, 'active': len(now_keys), 'new': len(fresh), 'notify': len(notify),
                  'cleared': len(prev - now_keys), 'channel': channel,
                  'sent': False, 'edited': False}
        # Fresh deals whose notification did not go out. They are NOT saved as
        # 'active', so the next poll sees them as new again and retries the send.
        unsent = set()
        owned_map = (self.get_cache() or {}).get('by_hash') or {}

        if channel == 'telegram':
            if not now_keys:
                # nothing cheaper right now → reflect it in the single board (silent edit)
                if board_id:
                    empty = "✅ Case Arbitrage — no cases cheaper than CSFloat right now."
                    ed = edit_notification(settings, board_id, empty, empty)
                    result['edited'] = bool(ed.get('ok'))
                    if ed.get('not_found'):
                        board_id = None
            else:
                plain, html = self._format_alert_messages([active[k] for k in now_keys], owned_map)
                if should_ping or not board_id:
                    # push a fresh message (notifies), then remove the old board so the
                    # chat keeps exactly one up-to-date board.
                    snd = send_notification(settings, plain, html=html)
                    if snd.get('ok'):
                        new_id = snd.get('message_id')
                        if board_id and new_id and new_id != board_id:
                            delete_notification(settings, board_id)
                        board_id = new_id or board_id
                        result['sent'] = True
                        for k in now_keys:
                            notified[k] = now_ts
                    else:
                        unsent = set(fresh)
                    result['send_error'] = snd.get('error')
                else:
                    # no new deal — just keep the board current (silent, no push)
                    ed = edit_notification(settings, board_id, plain, html)
                    result['edited'] = bool(ed.get('ok'))
                    if ed.get('not_found'):
                        snd = send_notification(settings, plain, html=html)
                        if snd.get('ok'):
                            board_id = snd.get('message_id')
                            result['sent'] = True
                            for k in now_keys:
                                notified[k] = now_ts
                        else:
                            board_id = None    # the old board is gone; post a new one next poll
                            unsent = set(fresh)
                        result['send_error'] = snd.get('error')
        else:
            # webhook (Discord/Slack): no edit API here — only post on a real new deal
            if now_keys and should_ping:
                plain, _ = self._format_alert_messages([active[k] for k in now_keys], owned_map)
                snd = send_notification(settings, plain)
                result['sent'] = bool(snd.get('ok'))
                result['send_error'] = snd.get('error')
                if snd.get('ok'):
                    for k in now_keys:
                        notified[k] = now_ts
                else:
                    unsent = set(fresh)

        # keep only recent notified timestamps (bounded) and persist state
        cutoff = now_ts - self._ALERT_NOTIFY_COOLDOWN_SEC * 3
        notified = {k: t for k, t in notified.items() if t >= cutoff}
        self._save_alert_state({
            'active': sorted(now_keys - unsent), 'details': active, 'board_message_id': board_id,
            'notified': notified, 'updated': datetime.now(timezone.utc).isoformat(),
        })
        return result
