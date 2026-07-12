import copy
import json
import os
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

# Only the inventory scan is cached to disk (it's expensive to produce). Arbitrage
# prices are fetched live on demand and held in the browser session, never cached.
CACHE_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_scan.json')

# CSFloat buy-order (autobuy) prices for owned items ARE cached to disk — fetching
# them is a slow, throttled sweep (~2 API calls per item), so it runs as a background
# job and the result is reused by every "=> CSFloat (autobuy)" profile until refreshed.
CSFLOAT_BUYORDERS_CACHE = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_csfloat_buyorders.json')
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
