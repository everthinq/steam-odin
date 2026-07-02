import copy
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

CACHE_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_scan.json')
TRADEON_STEAM_CACHE_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_tradeon_steam.json')
TRADEON_BUFF_CACHE_PATH  = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_tradeon_buff.json')
TRADEON_LISSKINS_STEAM_CACHE_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_tradeon_lisskins_steam.json')
TRADEON_LISSKINS_BUFF_CACHE_PATH  = os.path.join(os.path.dirname(__file__), 'cache', 'huginn_tradeon_lisskins_buff.json')

_TRADEON_STEAM_URL    = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/Steam/all'
_TRADEON_BUFF_URL     = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/Buff/all'
_TRADEON_LISSKINS_URL = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/LisSkins/all'

# Sales fee taken by the sell-side market: net proceeds = price * (1 - fee).
STEAM_SALES_FEE = 0.13
BUFF_SALES_FEE = 0.015
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

# LisSkins query: same body but secondMarket priced as "SellWithoutHold", which is the
# LisSkins side and (unlike most second markets) comes back un-paywalled.
_TRADEON_LISSKINS_BODY = copy.deepcopy(_TRADEON_STEAM_BODY)
_TRADEON_LISSKINS_BODY["secondMarketOptions"]["secondMarketPriceType"] = "SellWithoutHold"


class HuginnService:
    def __init__(self, steam_service, ratatoskr_service):
        self.steam = steam_service
        self.rat = ratatoskr_service

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

    def _write_cache(self, cache_path, items):
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w') as f:
            json.dump(items, f)

    def _fetch_tradeon(self, url, token, cache_path, body=None):
        items = self._post_tradeon(url, token, body)
        self._write_cache(cache_path, items)
        return items

    def _get_tradeon_cache(self, cache_path):
        if not os.path.exists(cache_path):
            return None
        with open(cache_path) as f:
            return json.load(f)

    def fetch_tradeon_steam(self, token):
        return self._fetch_tradeon(_TRADEON_STEAM_URL, token, TRADEON_STEAM_CACHE_PATH)

    def get_tradeon_steam_cache(self):
        return self._get_tradeon_cache(TRADEON_STEAM_CACHE_PATH)

    def fetch_tradeon_buff(self, token):
        return self._fetch_tradeon(_TRADEON_BUFF_URL, token, TRADEON_BUFF_CACHE_PATH)

    def get_tradeon_buff_cache(self):
        return self._get_tradeon_cache(TRADEON_BUFF_CACHE_PATH)

    def _combine_lisskins(self, token, sell_url, sell_fee, cache_path):
        """Combine LisSkins buy prices with a sell-side market's autobuy prices.

        LisSkins second-market prices come back un-paywalled, so we buy there (min) and
        sell into the target market's buy orders (autobuy), netting that market's fee.
        Items are joined on market_hash_name; only items on both sides are returned.
        Shaped like the other profiles (firstMarket = LisSkins buy, secondMarket = sell)
        so the UI renders it unchanged.
        """
        lisskins = self._post_tradeon(_TRADEON_LISSKINS_URL, token, _TRADEON_LISSKINS_BODY)
        sell_items = self._post_tradeon(sell_url, token, _TRADEON_STEAM_BODY)

        # market_hash_name -> sell-side second-market
        sell_by_name = {}
        for it in sell_items:
            name = (it.get('itemName') or {}).get('marketHashName')
            sm = it.get('secondMarket') or {}
            if name and sm.get('price') is not None:
                sell_by_name[name] = sm

        combined = []
        for it in lisskins:
            name = (it.get('itemName') or {}).get('marketHashName')
            buy_market = it.get('secondMarket') or {}      # LisSkins side
            buy = buy_market.get('price')
            sell_market = sell_by_name.get(name)           # target sell side
            if not name or buy is None or not buy or sell_market is None:
                continue
            net_sell = sell_market['price'] * (1 - sell_fee)
            profit = net_sell - buy
            combined.append({
                'itemName': it.get('itemName'),
                'imageUrl': it.get('imageUrl'),
                'firstMarket': buy_market,                 # Buy @ LisSkins
                'secondMarket': sell_market,               # Sell @ target market
                'profit': round(profit, 3),
                'profitPercent': round(profit / buy * 100, 2),
            })

        combined.sort(key=lambda x: x['profitPercent'], reverse=True)
        self._write_cache(cache_path, combined)
        return combined

    def fetch_lisskins_steam(self, token):
        return self._combine_lisskins(token, _TRADEON_STEAM_URL, STEAM_SALES_FEE, TRADEON_LISSKINS_STEAM_CACHE_PATH)

    def get_lisskins_steam_cache(self):
        return self._get_tradeon_cache(TRADEON_LISSKINS_STEAM_CACHE_PATH)

    def fetch_lisskins_buff(self, token):
        return self._combine_lisskins(token, _TRADEON_BUFF_URL, BUFF_SALES_FEE, TRADEON_LISSKINS_BUFF_CACHE_PATH)

    def get_lisskins_buff_cache(self):
        return self._get_tradeon_cache(TRADEON_LISSKINS_BUFF_CACHE_PATH)
