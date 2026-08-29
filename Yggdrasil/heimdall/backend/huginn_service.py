import copy
import json
import os
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from notifications import send_notification, notification_channel, edit_notification, delete_notification

# Only the inventory scan is cached to disk (it's expensive to produce). Arbitrage
# prices are fetched live on demand and held in the browser session, never cached.
CACHE_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_scan.json')

# CSFloat buy-order (autobuy) prices for owned items ARE cached to disk — fetching
# them is a slow, throttled sweep (~2 API calls per item), so it runs as a background
# job and the result is reused by every "=> CSFloat (autobuy)" profile until refreshed.
CSFLOAT_BUYORDERS_CACHE = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_csfloat_buyorders.json')

# Bundled catalog of tradeable containers (cases + sticker/souvenir/autograph capsules)
# used by the "Case Arbitrage" tracker. Regenerated from the community CSGO-API.
CONTAINERS_FILE = os.path.join(os.path.dirname(__file__), 'cases_containers.json')
# Daily cheapest-price snapshots per container, for trend arrows / sparklines. Cached
# to disk (cheap to keep) and pruned to the most recent N days on every save.
CASE_HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'cache', 'case_price_history.json')
# Which LisSkins/Buff-cheaper-than-CSFloat alerts are currently active, so we only
# notify on NEW crossings instead of every hourly run.
CASE_ALERT_STATE_FILE = os.path.join(os.path.dirname(__file__), 'cache', 'case_alert_state.json')
# LOOT.Farm auction tracker: per-lot trajectory (base, start/last price, bids, a Steam
# resale reference at capture, cleared-detection) accumulated over time for the backtest.
LOOTFARM_AUCTION_LOG = os.path.join(os.path.dirname(__file__), 'cache', 'lootfarm_auction_log.json')

_CSFLOAT_API_BASE = 'https://csfloat.com/api/v1'
_CSFLOAT_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')
# Human-editable pool of CSFloat API keys (labels + keys). Read fresh on every sweep,
# so you can add/remove keys without a restart. Gitignored (secrets).
CSFLOAT_KEYS_FILE = os.path.join(os.path.dirname(__file__), 'csfloat_keys.json')
# Per-key cooldown state (strikes + until-when benched). Lives in cache/ (gitignored)
# so it survives restarts and is separate from the user-owned keys file.
CSFLOAT_KEY_STATE_FILE = os.path.join(os.path.dirname(__file__), 'cache', 'csfloat_key_state.json')
# A rate-limited key is benched for this base, growing by the same step each repeat strike
# (strike 1 → 10 min, strike 2 → 20 min, strike 3 → 30 min, …).
_CSFLOAT_COOLDOWN_STEP_SEC = 10 * 60
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


class _CSFloatRateLimited(Exception):
    """Raised when CSFloat rate-limits a KEY (HTTP 429). The sweep benches the key
    and retries the item on another key; if all keys are cooling it pauses (resumable)."""


class _CSFloatUnavailable(Exception):
    """Raised when a request can't get through for a non-key reason (a proxy exit IP
    kept getting bot-challenged / 403 across retries). The sweep just skips this item
    and keeps going — it does NOT bench the key."""


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
        print(f'[HUGINN] Could not read {CSFLOAT_KEYS_FILE}: {e}')
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
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, 'w') as f:
                json.dump(self._state, f)
        except Exception as e:
            print(f'[HUGINN] Could not persist CSFloat key state: {e}')

    def _remaining(self, key):
        s = self._state.get(key)
        if not s:
            return 0
        return max(0, s.get('cooldown_until', 0) - time.time())

    def mark_limited(self, key):
        with self._lock:
            s = self._state.setdefault(key, {'strikes': 0, 'cooldown_until': 0})
            s['strikes'] += 1
            s['cooldown_until'] = time.time() + _CSFLOAT_COOLDOWN_STEP_SEC * s['strikes']
            self._save()
            print(f'[HUGINN] CSFloat key …{key[-6:]} benched {int(_CSFLOAT_COOLDOWN_STEP_SEC * s["strikes"] / 60)}m '
                  f'(strike {s["strikes"]})')

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

# Sales fee taken by the sell-side market: net proceeds = price * (1 - fee).
STEAM_SALES_FEE = 0.13
BUFF_SALES_FEE = 0.015
CSFLOAT_SALES_FEE = 0.02
DMARKET_SALES_FEE = 0.0   # DMarket takes no seller fee
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


class HuginnService:
    def __init__(self, steam_service, ratatoskr_service):
        self.steam = steam_service
        self.rat = ratatoskr_service
        self.csfloat_keys = CSFloatKeyManager()
        self._proxy_seq = 0   # increments per proxied attempt → fresh Bright Data session/IP
        self._price_cache = {}   # market -> (fetched_at_epoch, {name: price}) for portfolio valuation
        self._price_state = {}   # market -> 'refreshing' | 'ok' | 'error'
        self._price_lock = threading.Lock()
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
        self._container_refresh_started = False

    def _ensure_session(self, steam_id, account_data):
        if self.rat.get_status(steam_id).get('status') == 'connected':
            return True
        password = self.steam.get_password(steam_id)
        if not password:
            return False
        result = self.rat.login(
            account_name=account_data.get('account_name'),
            password=password,
            shared_secret=account_data.get('shared_secret'),
        )
        return 'error' not in result

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
        accounts = self.steam.get_all_accounts_data()
        by_hash = {}

        for account in accounts:
            steam_id = str(account['steamid'])
            account_name = account.get('account_name', steam_id)
            account_data = self.steam.get_account(steam_id) or {}

            if not self._ensure_session(steam_id, account_data):
                print(f'[HUGINN] Skipping {account_name} — no session')
                continue

            print(f'[HUGINN] Scanning {account_name}…')

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

        result = {
            'scan_timestamp': datetime.now(timezone.utc).isoformat(),
            'total_items': sum(v['count'] for v in by_hash.values()),
            'by_hash': by_hash,
        }

        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, 'w') as f:
            json.dump(result, f)

        return result

    def get_cache(self):
        if not os.path.exists(CACHE_PATH):
            return None
        with open(CACHE_PATH) as f:
            return json.load(f)

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

    def fetch_tradeon_lootfarm(self, token, fee_pct=5.0):
        """Tradeon (min) buy -> LOOT.Farm sell, using LOOT.Farm's OWN feed for the
        sell side (price + overstock), first-party and fresh, rather than pulse.

        Buy side: TradeOnMarket lowest listing (the firstMarket of any pulse row).
        Sell side: LOOT.Farm feed price (cents -> USD) net of your acceptance fee
        (`fee_pct`, default 5%). Overstock (have/max) comes straight from the feed,
        so the "Unstable"/Full column reflects LOOT.Farm's real limits. Items are
        joined on market_hash_name; only items present on both sides are returned."""
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

    def fetch_lisskins_lootfarm(self, token, fee_pct=5.0):
        """LisSkins (min) buy → LOOT.Farm sell (autobuy), same shape as
        fetch_tradeon_lootfarm but sourcing the buy side from LisSkins' own listing
        (pulse secondMarket, un-paywalled) instead of TradeOnMarket. Sell side is the
        LOOT.Farm feed price net of your acceptance fee; overstock + tier come from the
        feed. Joined on market_hash_name."""
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
            print(f'[HUGINN] auction pulse prices failed: {e}')
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
        for d in by_name.values():
            cur = d['min_current'] / 100.0
            steam_sell = steam.get(d['name'])
            tradeon_buy = tradeon.get(d['name'])
            steam_net = round(steam_sell * (1 - STEAM_SALES_FEE), 4) if steam_sell is not None else None
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
            os.makedirs(os.path.dirname(LOOTFARM_AUCTION_LOG), exist_ok=True)
            tmp = LOOTFARM_AUCTION_LOG + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(log, f)
            os.replace(tmp, LOOTFARM_AUCTION_LOG)
        except Exception as e:
            print(f'[HUGINN] auction log save failed: {e}')

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
        print(f'[HUGINN] auction tracker started (every {interval}s)')

    def _auction_tracker_loop(self, settings_provider, interval):
        while True:
            try:
                settings = settings_provider() if callable(settings_provider) else (settings_provider or {})
                token = (settings or {}).get('tradeon_token') or ''
                res = self.record_auction_snapshot(token)
                print(f"[HUGINN] auction snapshot: active={res['active']} tracked={res['tracked']} cleared+{res['newly_cleared']}")
            except Exception as e:
                print(f'[HUGINN] auction tracker error: {e}')
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
        for r in nobid:
            ref, base = r.get('steam_ref'), r.get('base')
            if ref is None or not base:
                continue
            snipes.append(_pct(ref * (1 - STEAM_SALES_FEE), base))
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

    def lootfarm_arbitrage(self, token='', balance_rate=0.5208, unlocked=True, in_stock=True):
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

        fees = {'steam': STEAM_SALES_FEE, 'buff': BUFF_SALES_FEE, 'csfloat': CSFLOAT_SALES_FEE}
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

    def fetch_lisskins_steam(self, token):
        return self._combine_arbitrage(token, _TRADEON_LISSKINS_URL, _TRADEON_LISSKINS_BODY,
                                       _TRADEON_STEAM_URL, STEAM_SALES_FEE)

    def fetch_lisskins_buff(self, token):
        return self._combine_arbitrage(token, _TRADEON_LISSKINS_URL, _TRADEON_LISSKINS_BODY,
                                       _TRADEON_BUFF_URL, BUFF_SALES_FEE)

    def fetch_lisskins_csfloat(self, token):
        return self._combine_arbitrage(token, _TRADEON_LISSKINS_URL, _TRADEON_LISSKINS_BODY,
                                       _TRADEON_CSFLOAT_URL, CSFLOAT_SALES_FEE,
                                       sell_body=_TRADEON_CSFLOAT_BODY)

    def fetch_buff_steam(self, token):
        return self._combine_arbitrage(token, _TRADEON_BUFF_URL, _TRADEON_BUFF_BUY_BODY,
                                       _TRADEON_STEAM_URL, STEAM_SALES_FEE)

    def fetch_buff_csfloat(self, token):
        return self._combine_arbitrage(token, _TRADEON_BUFF_URL, _TRADEON_BUFF_BUY_BODY,
                                       _TRADEON_CSFLOAT_URL, CSFLOAT_SALES_FEE,
                                       sell_body=_TRADEON_CSFLOAT_BODY)

    def fetch_csfloat_steam(self, token):
        # Buy at CSFloat's min listing, sell into Steam's autobuy (13% Steam fee).
        return self._combine_arbitrage(token, _TRADEON_CSFLOAT_URL, _TRADEON_CSFLOAT_BODY,
                                       _TRADEON_STEAM_URL, STEAM_SALES_FEE)

    def fetch_csfloat_buff(self, token):
        # Buy at CSFloat's min listing, sell into Buff163's autobuy (1.5% Buff fee).
        return self._combine_arbitrage(token, _TRADEON_CSFLOAT_URL, _TRADEON_CSFLOAT_BODY,
                                       _TRADEON_BUFF_URL, BUFF_SALES_FEE)

    def fetch_lisskins_dmarket(self, token):
        # Buy at LisSkins min, sell into DMarket autobuy (no DMarket fee).
        return self._combine_arbitrage(token, _TRADEON_LISSKINS_URL, _TRADEON_LISSKINS_BODY,
                                       _TRADEON_DMARKET_URL, DMARKET_SALES_FEE)

    def fetch_buff_dmarket(self, token):
        # Buy at Buff163 min, sell into DMarket autobuy (no DMarket fee).
        return self._combine_arbitrage(token, _TRADEON_BUFF_URL, _TRADEON_BUFF_BUY_BODY,
                                       _TRADEON_DMARKET_URL, DMARKET_SALES_FEE)

    def fetch_csfloat_dmarket(self, token):
        # Buy at CSFloat's min listing, sell into DMarket autobuy (no DMarket fee).
        return self._combine_arbitrage(token, _TRADEON_CSFLOAT_URL, _TRADEON_CSFLOAT_BODY,
                                       _TRADEON_DMARKET_URL, DMARKET_SALES_FEE)

    def fetch_dmarket_steam(self, token):
        # Buy at DMarket's min listing, sell into Steam's autobuy (13% Steam fee).
        return self._combine_arbitrage(token, _TRADEON_DMARKET_URL, _TRADEON_DMARKET_BUY_BODY,
                                       _TRADEON_STEAM_URL, STEAM_SALES_FEE)

    def fetch_dmarket_buff(self, token):
        # Buy at DMarket's min listing, sell into Buff163's autobuy (1.5% Buff fee).
        return self._combine_arbitrage(token, _TRADEON_DMARKET_URL, _TRADEON_DMARKET_BUY_BODY,
                                       _TRADEON_BUFF_URL, BUFF_SALES_FEE)

    def fetch_dmarket_csfloat(self, token):
        # Buy at DMarket's min listing, sell at CSFloat's min listing (CSFloat has no
        # autobuy, so the sell side is its lowest listing — 2% CSFloat fee).
        return self._combine_arbitrage(token, _TRADEON_DMARKET_URL, _TRADEON_DMARKET_BUY_BODY,
                                       _TRADEON_CSFLOAT_URL, CSFLOAT_SALES_FEE,
                                       sell_body=_TRADEON_CSFLOAT_BODY)

    # ---- CSFloat buy orders (autobuy) --------------------------------------

    _CSFLOAT_PROXY_ATTEMPTS = 8   # datacenter IPs are ~75% Cloudflare-blocked; rotate through several

    def _csfloat_fetch_once(self, url, api_key, proxy=None):
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

        req = urllib.request.Request(url, headers={
            'Authorization': api_key,
            'Accept': 'application/json',
            'User-Agent': _CSFLOAT_UA,
        })
        try:
            with do_open(req) as resp:
                body = resp.read().decode('utf-8', 'replace')
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise _CSFloatUnavailable('non-JSON page (bot challenge)')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise _CSFloatRateLimited('CSFloat rate limit (HTTP 429)')
            if e.code == 403:
                raise _CSFloatUnavailable('CSFloat forbidden (HTTP 403)')
            raise

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
            except (_CSFloatRateLimited, _CSFloatUnavailable):
                if proxy:
                    break                      # hand off to proxy fallback
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise _CSFloatRateLimited('CSFloat throttled (direct, no proxy)')

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

    def _resumable_state(self, names):
        """Return (processed_set, by_name, started_at) — resuming a recent, unfinished
        prior sweep if one exists within the resume window, else a fresh start."""
        prev = self.get_csfloat_buy_orders_cache()
        if prev and not prev.get('complete'):
            stamp = prev.get('updated_at') or prev.get('fetched_at')
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
            except Exception:
                age = None
            if age is not None and age < _CSFLOAT_RESUME_WINDOW_SEC:
                # Only resume names still relevant to the current candidate set.
                candidate = set(names)
                processed = {n for n in (prev.get('processed') or []) if n in candidate}
                by_name = {k: v for k, v in (prev.get('by_name') or {}).items() if k in candidate}
                return processed, by_name, prev.get('started_at')
        return set(), {}, None

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
        os.makedirs(os.path.dirname(CSFLOAT_BUYORDERS_CACHE), exist_ok=True)
        with open(CSFLOAT_BUYORDERS_CACHE, 'w') as f:
            json.dump(result, f)
        return result

    def fetch_csfloat_buy_orders(self, token=None, names=None, progress=None, key_pairs=None, wait_cb=None):
        """Sweep CSFloat buy orders for owned items and cache the result to disk.

        For each candidate market_hash_name we resolve a listing id then read its
        buy-order book, keeping the highest bid. Candidates default to the items in
        the latest inventory scan; if `token` is given we first intersect them with
        the items CSFloat actually lists (via pulse) so we don't waste searches.

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
            print(f'[HUGINN] CSFloat sweep routing through proxy {proxy.split("@")[-1]}')

        if names is None:
            scan = self.get_cache()
            names = sorted((scan or {}).get('by_hash', {}).keys())

        if token:
            try:
                csfloat_listed = {
                    (it.get('itemName') or {}).get('marketHashName')
                    for it in self._post_tradeon(_TRADEON_CSFLOAT_URL, token, _TRADEON_CSFLOAT_BODY)
                }
                names = [n for n in names if n in csfloat_listed]
            except Exception as e:
                print(f'[HUGINN] CSFloat candidate pre-filter failed, using full owned set: {e}')

        total = len(names)
        processed, by_name, started_at = self._resumable_state(names)
        started_at = started_at or datetime.now(timezone.utc).isoformat()
        todo = [n for n in names if n not in processed]
        if processed:
            print(f'[HUGINN] Resuming CSFloat sweep: {len(processed)}/{total} already done, '
                  f'{len(todo)} to go, {len(by_name)} priced so far')
        if progress:
            progress(len(processed), total, None, len(by_name))

        reason = None
        since_checkpoint = 0
        consecutive_waits = 0
        for name in todo:
            order = None
            paused = False
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
                        print(f'[HUGINN] CSFloat sweep paused at {len(processed)}/{total}: {reason}')
                        break
                    wait_s = self.csfloat_keys.min_cooldown_remaining(keys) + 5
                    self._write_buyorders_cache(by_name, processed, total, started_at, complete=False)
                    resume_at = time.time() + wait_s
                    if wait_cb:
                        wait_cb(resume_at)
                    print(f'[HUGINN] all CSFloat keys cooling at {len(processed)}/{total}; '
                          f'auto-resuming in {int(wait_s // 60)}m{int(wait_s % 60)}s')
                    time.sleep(wait_s)
                    if wait_cb:
                        wait_cb(None)
                    continue        # keys should be free now → retry this item
                try:
                    listing_id = self._csfloat_find_listing_id(key, name, proxy)
                    if listing_id:
                        order = self._csfloat_top_buy_order(key, listing_id, proxy)
                    self.csfloat_keys.mark_ok(key)
                    break
                except _CSFloatRateLimited:
                    self.csfloat_keys.mark_limited(key)
                    continue                      # key throttled → bench it, try next key
                except _CSFloatUnavailable as e:
                    self.csfloat_keys.mark_ok(key) # not the key's fault (proxy IP) — don't bench
                    print(f'[HUGINN] CSFloat item skipped ({name!r}): {e}')
                    break                         # skip this item, keep the sweep going
                except Exception as e:
                    print(f'[HUGINN] CSFloat buy-order fetch failed for {name!r}: {e}')
                    break                         # skip this item, keep the sweep going

            if paused:
                break
            if order:
                by_name[name] = order
            processed.add(name)
            consecutive_waits = 0          # made progress → reset the auto-resume cap
            since_checkpoint += 1
            if progress:
                progress(len(processed), total, name, len(by_name))
            if since_checkpoint >= _CSFLOAT_CHECKPOINT_EVERY:
                self._write_buyorders_cache(by_name, processed, total, started_at, complete=False)
                since_checkpoint = 0
            time.sleep(_CSFLOAT_REQUEST_DELAY)

        complete = reason is None and all(n in processed for n in names)
        return self._write_buyorders_cache(by_name, processed, total, started_at, complete, reason)

    def get_csfloat_buy_orders_cache(self):
        if not os.path.exists(CSFLOAT_BUYORDERS_CACHE):
            return None
        with open(CSFLOAT_BUYORDERS_CACHE) as f:
            return json.load(f)

    def _combine_autobuy(self, token, pulse_url, pulse_body, buy_side='second'):
        """Combine a buy-side market's min price (from pulse) with CSFloat's highest
        buy order (from the cached sweep). `buy_side` selects which pulse market holds
        the buy price: 'first' = Tradeon (the query's first market), 'second' = the
        target market (LisSkins/Buff). Only owned items with a cached CSFloat buy order
        appear. Shaped like the other profiles so the UI renders it unchanged.
        """
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
            net_sell = sell_price * (1 - CSFLOAT_SALES_FEE)
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

    def fetch_tradeon_csfloat_autobuy(self, token):
        return self._combine_autobuy(token, _TRADEON_CSFLOAT_URL, _TRADEON_CSFLOAT_BODY, buy_side='first')

    def fetch_lisskins_csfloat_autobuy(self, token):
        return self._combine_autobuy(token, _TRADEON_LISSKINS_URL, _TRADEON_LISSKINS_BODY, buy_side='second')

    def fetch_buff_csfloat_autobuy(self, token):
        return self._combine_autobuy(token, _TRADEON_BUFF_URL, _TRADEON_BUFF_BUY_BODY, buy_side='second')

    def fetch_dmarket_csfloat_autobuy(self, token):
        return self._combine_autobuy(token, _TRADEON_DMARKET_URL, _TRADEON_DMARKET_BUY_BODY, buy_side='second')

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

    def price_map(self, token, market='steam', force=False):
        """market_hash_name -> current unit price (USD) on the chosen reference market.

        BLOCKING: fetches from pulse if the cache is cold/stale. Prefer
        prices_for_valuation() for request paths — it never blocks. Result is
        cached per market for _PRICE_CACHE_TTL_SEC; pass force=True to refetch."""
        cached = self._price_cache.get(market)
        if not force and cached and (time.time() - cached[0]) < self._PRICE_CACHE_TTL_SEC:
            return cached[1]
        prices = self._compute_price_map(token, market)
        with self._price_lock:
            self._price_cache[market] = (time.time(), prices)
            self._price_state[market] = 'ok'
        return prices

    def known_item_names(self, token, block=True):
        """Set of every market_hash_name pulse knows about (the CS item universe),
        unioned across whatever price maps are cached. With block=True, falls back
        to a one-time blocking Steam fetch if nothing is cached yet (used for the
        one-off validate check); block=False stays non-blocking (used for typeahead
        on every keystroke)."""
        names = set()
        with self._price_lock:
            for _, m in self._price_cache.values():
                names.update(m.keys())
        if not names and block and token:
            try:
                names.update(self.price_map(token, 'steam').keys())
            except Exception as e:
                print(f'[DRAUPNIR] known_item_names steam fetch failed: {e}')
        return names

    def _refresh_prices_bg(self, token, market):
        try:
            prices = self._compute_price_map(token, market)
            with self._price_lock:
                self._price_cache[market] = (time.time(), prices)
                self._price_state[market] = 'ok'
        except Exception as e:
            print(f'[DRAUPNIR] price refresh failed ({market}): {e}')
            with self._price_lock:
                self._price_state[market] = 'error'

    def prices_for_valuation(self, token, market='steam'):
        """NON-BLOCKING. Returns (prices_or_None, status) for portfolio valuation.

        Serves the cached price map instantly. When the cache is cold or stale,
        it kicks off a single background refresh (one per market at a time) and
        returns immediately with whatever we have (stale cache, or None on a cold
        start) so the page never waits on pulse. status is one of:
        'no_token', 'fresh', 'refreshing', 'error'."""
        with self._price_lock:
            cached = self._price_cache.get(market)
        if not token:
            return (cached[1] if cached else None), 'no_token'
        if cached and (time.time() - cached[0]) < self._PRICE_CACHE_TTL_SEC:
            return cached[1], 'fresh'
        # cold or stale → refresh in the background (single-flight per market)
        with self._price_lock:
            if self._price_state.get(market) != 'refreshing':
                self._price_state[market] = 'refreshing'
                threading.Thread(target=self._refresh_prices_bg,
                                 args=(token, market), daemon=True).start()
            state = self._price_state.get(market)
        if cached:
            return cached[1], 'refreshing'          # serve stale while warming
        return None, ('error' if state == 'error' else 'refreshing')

    # ---- Container price tracker ("Case Arbitrage") ------------------------

    # Markets a container is compared across (all shown as prices). 'tradeon' is the
    # TradeOnMarket lowest listing (the firstMarket side of every pulse row) — a
    # buy-only source here.
    _CONTAINER_MARKETS = ('steam', 'buff', 'csfloat', 'lisskins', 'dmarket', 'tradeon')
    # Markets you can realistically CASH OUT on (drives the "best flip" + profit
    # filters). DMarket is excluded on purpose: its pulse "Sell" price is often
    # unfillable ("unavailable"), so a flip that targets it is noise. LisSkins is a
    # buy-only source here (no seller fee modelled). Revisit if that changes.
    _CONTAINER_SELL_FEES = {
        'steam': STEAM_SALES_FEE, 'buff': BUFF_SALES_FEE, 'csfloat': CSFLOAT_SALES_FEE,
    }
    # Markets whose pulse price is shown but excluded from the cheapest/dearest/spread
    # math because it's not actionable (DMarket "Sell" prices are often unfillable).
    _CONTAINER_NOISE_MARKETS = frozenset({'dmarket'})
    _CASE_HISTORY_MAX_DAYS = 120

    def _load_containers(self, categories=None):
        """Load the bundled container catalog. Optionally filter to a set of
        category slugs ('case', 'sticker', 'souvenir', 'autograph')."""
        try:
            with open(CONTAINERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f'[HUGINN] container catalog load failed: {e}')
            return []
        items = data.get('containers', [])
        if categories:
            wanted = set(categories)
            items = [c for c in items if c.get('category') in wanted]
        return items

    def _container_names(self):
        return {c['name'] for c in self._load_containers(None)}

    # --- container market snapshots (price + listing count, non-blocking) ---

    def _single_container_map(self, token, market):
        """{name: {price, count}} for container names only, on one market. For
        'tradeon' we read the firstMarket side (TradeOnMarket's own lowest listing),
        which every pulse row already carries; for the rest we read secondMarket."""
        body = copy.deepcopy(_TRADEON_STEAM_BODY)
        if market == 'tradeon':
            url = _TRADEON_STEAM_URL
            side = 'firstMarket'
        else:
            url, sell_type = self._PRICE_MARKETS[market]
            body['secondMarketOptions']['secondMarketPriceType'] = sell_type
            side = 'secondMarket'
        names = self._container_names()
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
            print(f'[HUGINN] container snapshot refresh failed ({market}): {e}')
            with self._price_lock:
                self._container_state[market] = 'error'

    def _container_snapshot(self, token, market):
        """NON-BLOCKING {name:{price,count}} for one market. Serves cache instantly,
        warms cold/stale in the background (single-flight per market), like
        prices_for_valuation. status: 'no_token'|'fresh'|'refreshing'|'error'."""
        with self._price_lock:
            cached = self._container_cache.get(market)
        if not token:
            return (cached[1] if cached else {}), 'no_token'
        if cached and (time.time() - cached[0]) < self._PRICE_CACHE_TTL_SEC:
            return cached[1], 'fresh'
        with self._price_lock:
            if self._container_state.get(market) != 'refreshing':
                self._container_state[market] = 'refreshing'
                threading.Thread(target=self._refresh_container_bg,
                                 args=(token, market), daemon=True).start()
            state = self._container_state.get(market)
        if cached:
            return cached[1], 'refreshing'
        return {}, ('error' if state == 'error' else 'refreshing')

    # --- history (daily cheapest price + spread) ---

    def _load_case_history(self):
        try:
            with open(CASE_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_case_history(self, history):
        """Persist snapshots, pruning each series to the most recent N days."""
        try:
            for name, series in list(history.items()):
                if len(series) > self._CASE_HISTORY_MAX_DAYS:
                    history[name] = dict(sorted(series.items())[-self._CASE_HISTORY_MAX_DAYS:])
            os.makedirs(os.path.dirname(CASE_HISTORY_FILE), exist_ok=True)
            tmp = CASE_HISTORY_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(history, f)
            os.replace(tmp, CASE_HISTORY_FILE)
        except Exception as e:
            print(f'[HUGINN] case history save failed: {e}')

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

    def _history_trend(self, series, current):
        """% change of cheapest price vs ~7 daily snapshots ago (nearest earlier)."""
        if not series or current is None:
            return None
        dates = sorted(series.keys())
        if len(dates) < 2:
            return None
        prior = self._hist_price(series[dates[-8] if len(dates) >= 8 else dates[0]])
        if not prior:
            return None
        return round((current - prior) / prior * 100, 2)

    def _history_sparkline(self, series, points=30):
        if not series:
            return []
        out = [self._hist_price(series[d]) for d in sorted(series.keys())[-points:]]
        return [p for p in out if p is not None]

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
        history = self._load_case_history()
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
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
                best = None
                for sm, fee in self._CONTAINER_SELL_FEES.items():
                    sp = prices.get(sm)
                    if sp is None:
                        continue
                    net = sp * (1 - fee)
                    profit = net - cheapest
                    if best is None or profit > best['profit']:
                        best = {'buy_market': cheapest_market, 'sell_market': sm,
                                'net_sell': round(net, 2), 'profit': round(profit, 2),
                                'profit_pct': round(profit / cheapest * 100, 2) if cheapest else None}
                row['flip'] = best
                profit_pct = best['profit_pct'] if best else None
                # history: per-day min/max of the cheapest price + per-market min/max.
                # Updated every run (loop every ~10min, page views) so lo/hi capture the
                # true daily range, not just the first reading. Entry:
                #   {lo, hi: cheapest range, f: best flip %, mk:{market:[lo,hi]}}
                series = history.get(name)
                if series is None:
                    series = history[name] = {}
                e = series.get(today)
                if not isinstance(e, dict) or 'lo' not in e:
                    e = {'lo': cheapest, 'hi': cheapest, 'f': profit_pct, 'mk': {}}
                    series[today] = e
                    dirty = True
                if cheapest < e['lo']:
                    e['lo'] = cheapest; dirty = True
                if cheapest > e['hi']:
                    e['hi'] = cheapest; dirty = True
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
                row['trend_pct'] = self._history_trend(series, cheapest)
                row['sparkline'] = self._history_sparkline(series)
                # temporal "hot": today's net profit % vs this item's own median
                priors = [self._hist_profit(series[d]) for d in sorted(series.keys())[:-1]]
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
            'sell_markets': list(self._CONTAINER_SELL_FEES.keys()),
            'sell_fees': self._CONTAINER_SELL_FEES,
            'hot_threshold_pct': round(thresh, 2) if thresh is not None else None,
            'count': len(rows),
            'priced': sum(1 for r in rows if r.get('prices')),
            'updated': today,
        }

    # --- hourly background pull (keeps the cache warm without a page view) ---

    _CONTAINER_FULL_REFRESH_SEC = 3600   # full 6-market pull + history cadence

    def start_container_refresh(self, settings_provider, default_interval=600):
        """Background loop with two cadences: a FULL 6-market pull + daily history
        every hour (keeps the UI warm without a page view), and a fast poll of the
        alert markets (CSFloat/LisSkins/Buff) every `case_poll_interval_sec` (default
        10min) that fires price alerts on new crossings. pulse reprices CSFloat ~1min
        / Steam ~5min, so hourly alone is too slow. `settings_provider` returns the
        current settings dict. Idempotent — one loop per service instance."""
        if getattr(self, '_container_refresh_started', False):
            return
        self._container_refresh_started = True
        threading.Thread(target=self._container_refresh_loop,
                         args=(settings_provider, default_interval), daemon=True).start()
        print(f'[HUGINN] container refresh loop started (poll default {default_interval}s)')

    def _refresh_one(self, token, market):
        try:
            snap = self._single_container_map(token, market)
            with self._price_lock:
                self._container_cache[market] = (time.time(), snap)
                self._container_state[market] = 'ok'
        except Exception as e:
            print(f'[HUGINN] container refresh failed ({market}): {e}')
            with self._price_lock:
                self._container_state[market] = 'error'

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
        last_full = 0
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
                            print(f'[HUGINN] history record failed: {e}')
                        last_full = now
                        print('[HUGINN] full container refresh from pulse')
                    elif alerts_on:
                        self._refresh_markets(token, self._ALERT_MARKETS, parallel=True)
                    if alerts_on:
                        try:
                            res = self.run_case_alerts(settings)
                            if res.get('new'):
                                print(f"[HUGINN] case alerts: {res['new']} new, sent={res.get('sent')}")
                        except Exception as e:
                            print(f'[HUGINN] case alerts failed: {e}')
                else:
                    print('[HUGINN] container refresh skipped — no tradeon_token yet')
            except Exception as e:
                print(f'[HUGINN] container refresh loop error: {e}')
            time.sleep(interval)

    # --- Case Arbitrage price alerts (LisSkins/Buff cheaper than CSFloat) ----

    _ALERT_MARKET_LABEL = {'lisskins': 'LisSkins', 'buff': 'Buff'}
    _ALERT_MARKETS = ('csfloat', 'lisskins', 'buff')
    # Don't re-PING the same (case,market) more often than this even if it flickers
    # out and back in (the board still edits silently). New deals still ping instantly.
    _ALERT_NOTIFY_COOLDOWN_SEC = 3600
    _MARKET_SLUG = {'steam': 'Steam', 'buff': 'Buff', 'csfloat': 'CsFloat',
                    'lisskins': 'LisSkins', 'dmarket': 'Dmarket', 'tradeon': 'TradeOnMarket'}

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
            os.makedirs(os.path.dirname(CASE_ALERT_STATE_FILE), exist_ok=True)
            tmp = CASE_ALERT_STATE_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=1)
            os.replace(tmp, CASE_ALERT_STATE_FILE)
        except Exception as e:
            print(f'[HUGINN] alert state save failed: {e}')

    def _format_alert_messages(self, alerts, owned_map=None):
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
            for g in group[:25]:
                p, h = block(g)
                plain.append(''); plain.extend(p)
                html.append(''); html.extend(h)

        if owned_map:
            owned = [g for g in cases if g['owned']]
            notowned = [g for g in cases if not g['owned']]
            section(f"\U0001F4E6 In your inventory ({len(owned)})", owned)
            section(f"\U0001F195 Not in your inventory ({len(notowned)})", notowned)
        else:
            for g in cases[:40]:
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
        """Evaluate LisSkins/Buff-cheaper-than-CSFloat and notify on NEW crossings.
        `force=True` re-sends all currently-active alerts (used by "Check now").
        `refresh=True` re-pulls the alert markets (in parallel, near-simultaneous)
        before comparing, so a manual check reflects live prices, not a stale cache.
        Returns a summary dict; never raises into the caller."""
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
            for m in ('lisskins', 'buff'):
                p = prices.get(m)
                if p is None or p >= cf:
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
            # webhook (Discord/Slack): no edit API here — only post on a real new deal
            if now_keys and should_ping:
                plain, _ = self._format_alert_messages([active[k] for k in now_keys], owned_map)
                snd = send_notification(settings, plain)
                result['sent'] = bool(snd.get('ok'))
                result['send_error'] = snd.get('error')
                if snd.get('ok'):
                    for k in now_keys:
                        notified[k] = now_ts

        # keep only recent notified timestamps (bounded) and persist state
        cutoff = now_ts - self._ALERT_NOTIFY_COOLDOWN_SEC * 3
        notified = {k: t for k, t in notified.items() if t >= cutoff}
        self._save_alert_state({
            'active': sorted(now_keys), 'details': active, 'board_message_id': board_id,
            'notified': notified, 'updated': datetime.now(timezone.utc).isoformat(),
        })
        return result
