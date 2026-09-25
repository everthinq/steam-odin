"""Andvari — Steam trading-card deal finder.

The play: a game whose trading cards drop for more than the game costs. Buying a
game with trading cards earns half its card set (rounded up) as card drops from
playing it; each card sells on the Steam Community Market. When the cards'
fee-adjusted resale beats the game's price, the game pays for itself — and the
trick repeats on every account that does not own it yet.

Two scan scopes, scanned in this order:

* **On sale** — discounted games (the store's "specials"). Small (~1k games) and
  where most deals are, so it is always scanned first and refreshed often.
* **Full price** — every other game with cards (~10k). Full prices rarely move,
  so it is optional (a setting), scanned after the sale scope, refreshed less
  often, and given a smaller share of the rate-limited Market check.

Data sources, cheapest first, so Steam is asked as little as possible:

1. **SteamCardExchange badge-price feed** — ONE public request returns every game
   with cards (~14k): card count + full-set price (sum of each card's lowest
   Steam listing). Gives a first estimate for every game at zero Steam cost.
2. **Steam store search** — DISCOVERY, in one store country only (the one most
   accounts are in): games with trading cards, capped at the configured maximum
   price. Steam's ``maxprice`` filter only accepts its price-slider steps, so the
   scan asks for the nearest step above the maximum and filters exactly after.
   (The listing puts free games first and is only then price-ordered, so there
   is no "stop at the first expensive page" shortcut.)
3. **Steam store appdetails** — REGIONAL PRICES for the other account countries,
   100 games per request, only for plausible candidates. Accounts in different
   countries pay different prices, and not all in USD (Hong Kong dollars,
   Norwegian kroner...), so each region's price is converted to USD with a daily
   exchange-rate feed (exchangerate-api.com's free endpoint).
4. **Steam Community Market** — only for shortlisted (estimated-profitable)
   games: each card's lowest listing and listing count, for an exact expected
   value, a worst case (every drop is the cheapest card), and liquidity. This is
   the rate-limited endpoint (10 cards per request, harsh 429s), so it is
   serial, throttled, cached for a day, and capped per scan.
5. **Per account** (its own fresh web session): owned games, store country, and
   remaining card drops from the badges page — so a game is never bought twice
   and each account's regional price is used.

SteamDB (steamdb.info/sales) shows the same sales but blocks scripts behind
Cloudflare, so it is only linked from each row for manual checks.

Money is handled in integer cents internally (Steam's own unit) and exposed as
US dollars. It is Steam-wallet money: games are bought with wallet funds and
card sales pay back into the wallet. Read-only: it never buys anything.
"""
import gzip
import html
import http.client
import json
import logging
import math
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from notifications import send_notification

logger = logging.getLogger(__name__)

CARD_EXCHANGE_FEED_URL = 'https://www.steamcardexchange.net/api/request.php?GetBadgePrices_Guest'
STORE_SEARCH_URL = 'https://store.steampowered.com/search/results/'
STORE_APPDETAILS_URL = 'https://store.steampowered.com/api/appdetails'
STORE_ACCOUNT_URL = 'https://store.steampowered.com/account/'
STORE_USERDATA_URL = 'https://store.steampowered.com/dynamicstore/userdata/'
MARKET_SEARCH_URL = 'https://steamcommunity.com/market/search/render/'
# Order book (buy + sell orders with depth) by item NAME — the endpoint SteamDB's
# browser extension uses for its quick-sell buttons. Works anonymously.
MARKET_ORDERBOOK_URL = 'https://steamcommunity.com/market/orderbook'
# Steam's own store backend (the protobuf service documented in
# github.com/SteamTracking/Protobufs, webui/service_storebrowse.proto): batched
# items with each country's price, discount end date and feature categories.
STORE_BROWSE_ITEMS_URL = 'https://api.steampowered.com/IStoreBrowseService/GetItems/v1/'
EXCHANGE_RATES_URL = 'https://open.er-api.com/v6/latest/USD'

SCOPE_SALE = 'sale'
SCOPE_FULL = 'full'
SCOPE_ALL = 'all'
SCOPES = (SCOPE_SALE, SCOPE_FULL, SCOPE_ALL)

# How a dropped card is valued:
#   instant — sold right away into the highest buy orders (walking the order book
#             for the number of copies all accounts would sell).
#   listing — listed one cent under the lowest ask (outliers capped); more money,
#             but the card sits until somebody buys it.
#   both    — (default, Ivan's choice) every row carries both, and a game is a
#             deal when EITHER is profitable; "sell now" deals rank first.
VALUATION_INSTANT = 'instant'
VALUATION_LISTING = 'listing'
VALUATION_BOTH = 'both'
VALUATIONS = (VALUATION_BOTH, VALUATION_INSTANT, VALUATION_LISTING)
DEAL_SELL_NOW = 'sell_now'      # profitable selling the drops straight to buy orders
DEAL_LIST = 'list'              # profitable only when listed at the sell price

_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'cache', 'card_deals.json.gz')
_USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/128.0 Safari/537.36')

# Steam Community Market fees on Steam items (appid 753): a 5% Steam fee plus a
# 10% publisher fee, each computed on what the seller receives and each at least
# one cent. The lowest possible listing is 3 cents (seller gets 1 cent).
_STEAM_FEE_RATE = 0.05
_PUBLISHER_FEE_RATE = 0.10
_MINIMUM_LISTING_CENTS = 3

# The only `maxprice` values the store search honours (its price-slider steps,
# in the store's dollars); anything else is silently ignored. Above: no cap.
_STORE_PRICE_STEPS = (5, 10, 15, 20, 25, 30, 40, 50, 60)
# A paid game that is never free, used once per country to learn the currency.
_CURRENCY_PROBE_APP = '220'   # Half-Life 2
_TRADING_CARDS_CATEGORY = 29   # store feature category "Steam Trading Cards"
_USD_MARKET_CURRENCY = 1       # Steam's currency id for US dollars

# Cache lifetimes (seconds).
_FEED_TIME_TO_LIVE = 6 * 3600          # the feed itself refreshes a few times a day
_STORE_TIME_TO_LIVE = {SCOPE_SALE: 3 * 3600,     # sales change daily
                       SCOPE_FULL: 24 * 3600}    # full prices rarely move
_ACCOUNT_TIME_TO_LIVE = 6 * 3600       # owned games / drops / country
_CURRENCY_TIME_TO_LIVE = 7 * 86400     # a store country's currency practically never changes
_EXCHANGE_RATES_TIME_TO_LIVE = 12 * 3600
_VERIFIED_TIME_TO_LIVE = 24 * 3600     # per-card Market prices: re-check after a day
_VERIFIED_TRUSTED_FOR = 48 * 3600      # after this, fall back to the feed estimate
_ORDERBOOK_TIME_TO_LIVE = 12 * 3600    # buy orders move faster than asks
_ORDERBOOK_TRUSTED_FOR = 36 * 3600

# Which discovered games get priced in the other account countries: those whose
# expected cards net is at least this share of the discovery-country price.
# Regional prices differ a LOT (Turkey is sometimes 5x cheaper than Moldova), so
# the on-sale scope prices every game everywhere (only ~11 requests per country)
# and the big full-price scope keeps anything within 5x of break-even.
_REGIONAL_CANDIDATE_SHARE = {SCOPE_SALE: 0.0, SCOPE_FULL: 0.2}
_STORE_ITEMS_BATCH = 250             # measured: 250 items answer in under a second

# Rate-limit guards (minimum gap between calls to each host).
_HOST_GAP_SECONDS = {'store': 2.5, 'community': 4.0, 'orderbook': 2.5, 'api': 1.0,
                     'exchange': 1.0, 'rates': 1.0}
_RATE_LIMIT_BACKOFF_SECONDS = (60, 180)       # waits after consecutive 429s, then give up
_RATE_LIMIT_COOLDOWN_SECONDS = 20 * 60         # after giving up, skip that host for this long
_TRANSIENT_RETRY_SECONDS = (5, 20)            # waits after a timeout / 5xx / dropped connection

# Scan bounds.
_STORE_PAGE_SIZE = 100
_STORE_MAX_PAGES = {SCOPE_SALE: 40, SCOPE_FULL: 150}
_MARKET_PAGE_SIZE = 10                 # Steam caps market search at 10 results per request
_MARKET_MAX_PAGES_PER_GAME = 4
# Games checked on the Market per scan. The Market (and its priceoverview, which
# Gjallarhorn's liquidity warmer uses) shares one rate limit, so stay modest;
# checks are cached for a day, so successive scans cover more games.
_VERIFY_CAP_PER_SCAN = {SCOPE_SALE: 80, SCOPE_FULL: 40}
# A card's lowest ask is capped at this multiple of the set's median lowest ask.
# Every card drops with the same chance, and a lone $20 (or $1,150) ask with a
# handful of listings is not a price anyone pays — measured live, most sets have
# exactly such an outlier, and averaging it made fake deals.
_OUTLIER_CAP_MULTIPLE = 3
# Cards whose order book is fetched per scan (one request per card, anonymous,
# own throttle). Measured: 30 requests at a 2-second gap never hit a 429.
_ORDERBOOK_CAP_PER_SCAN = {SCOPE_SALE: 200, SCOPE_FULL: 120}
_BADGE_MAX_PAGES = 20
_ALERTED_MEMORY_SECONDS = 14 * 86400   # don't re-alert the same deal at the same price
_ALERT_MAX_LINES = 15
_UNPROFITABLE_ROW_LIMIT = 400
_BACKGROUND_FIRST_DELAY_SECONDS = 300  # let boot + session keep-alive settle first
_BACKGROUND_TICK_SECONDS = 300


class RateLimited(Exception):
    """Steam answered HTTP 429 Too Many Requests."""


# ---- pure money + parsing helpers (unit-tested) -----------------------------

def seller_receives_cents(buyer_pays_cents):
    """What the seller gets for a Steam Market listing the buyer pays
    `buyer_pays_cents` for, after Steam's 5% and the publisher's 10% fee (each
    at least one cent). Returns 0 below the 3-cent minimum listing."""
    buyer_pays_cents = int(buyer_pays_cents or 0)
    if buyer_pays_cents < _MINIMUM_LISTING_CENTS:
        return 0
    receives = int(buyer_pays_cents / 1.15) + 2
    while receives > 1 and (receives
                            + max(1, int(receives * _STEAM_FEE_RATE))
                            + max(1, int(receives * _PUBLISHER_FEE_RATE))) > buyer_pays_cents:
        receives -= 1
    return receives


def undercut_net_cents(lowest_listing_cents):
    """Net for selling one card by listing one cent under the current lowest
    listing (to sell first), floored at the minimum listing."""
    return seller_receives_cents(max(_MINIMUM_LISTING_CENTS, int(lowest_listing_cents) - 1))


def card_drops(card_count):
    """Card drops a purchased game gives: half the set, rounded up."""
    return math.ceil(int(card_count or 0) / 2)


def store_price_step(max_price):
    """The store search `maxprice` step to ask for: the smallest step at or
    above the maximum (in the store's currency units), or None (no cap) when
    there is no maximum or it is above the top step."""
    if not max_price or max_price <= 0:
        return None
    for step in _STORE_PRICE_STEPS:
        if max_price <= step:
            return step
    return None


def to_usd_cents(local_cents, currency, rates):
    """Convert an amount in a store currency's minor units to US cents with
    `rates` (units of each currency per 1 USD). None when it can't be converted."""
    if local_cents is None or not currency:
        return None                      # an unknown currency is never assumed to be USD
    if currency == 'USD':
        return float(local_cents)
    rate = (rates or {}).get(currency)
    return float(local_cents) / rate if rate else None


def _cents(dollar_text):
    """'$1,234.56' / '$0.44 USD' -> 123456 / 44. None if unparseable."""
    digits = ''.join(c for c in str(dollar_text or '') if c.isdigit() or c == '.')
    try:
        return int(round(float(digits) * 100)) if digits else None
    except ValueError:
        return None


def parse_card_exchange_feed(payload):
    """SteamCardExchange badge-price feed -> {app_id: {name, card_count,
    set_cents, updated_at}}. Rows look like
    [["1958600", "Barro F22"], 5, "$0.49", 1790278877, 1]."""
    games = {}
    for row in (payload or {}).get('data') or []:
        try:
            (app_id, name), card_count, set_price, updated_at = row[0], row[1], row[2], row[3]
            card_count = int(card_count)
        except (TypeError, ValueError, IndexError):
            continue
        set_cents = _cents(set_price)
        if not card_count or set_cents is None:
            continue
        games[str(app_id)] = {
            'name': _unescape(name),
            'card_count': card_count,
            'set_cents': set_cents,
            'updated_at': updated_at,
        }
    return games


_STORE_ROW = re.compile(r'<a\s([^>]*)>(.*?)</a>', re.S)


def parse_store_search(results_html):
    """Steam store search results HTML -> [{app_id, name, price_cents,
    original_cents, discount_percent}] in the store's currency minor units.
    Skips packages/bundles and free games (a free game never drops cards)."""
    games = []
    for tag, body in _STORE_ROW.findall(results_html or ''):
        # A package/bundle row carries its id anywhere in the tag (before the app
        # ids in practice) and often several comma-separated app ids: skip both.
        if 'data-ds-packageid' in tag or 'data-ds-bundleid' in tag:
            continue
        single_app = re.search(r'data-ds-appid="(\d+)"', tag)
        if not single_app:
            continue
        app_id = single_app.group(1)
        price = re.search(r'data-price-final="(\d+)"', body)
        if not price or int(price.group(1)) <= 0:
            continue
        discount = re.search(r'data-discount="(\d+)"', body)
        title = re.search(r'class="title">([^<]+)<', body)
        original = re.search(r'discount_original_price">([^<]+)<', body)
        games.append({
            'app_id': app_id,
            'name': _unescape(title.group(1)) if title else app_id,
            'price_cents': int(price.group(1)),
            'original_cents': _cents(original.group(1)) if original else None,
            'discount_percent': int(discount.group(1)) if discount else 0,
        })
    return games


def parse_appdetails_prices(payload):
    """appdetails?filters=price_overview JSON -> {app_id: {price_cents,
    original_cents, discount_percent, currency}} (store currency minor units).
    Games without a price (free, unavailable in that country) are left out."""
    prices = {}
    for app_id, entry in (payload or {}).items():
        data = (entry or {}).get('data') if (entry or {}).get('success') else None
        overview = data.get('price_overview') if isinstance(data, dict) else None
        if not overview or not overview.get('final'):
            continue
        prices[str(app_id)] = {
            'price_cents': int(overview['final']),
            'original_cents': int(overview.get('initial') or overview['final']),
            'discount_percent': int(overview.get('discount_percent') or 0),
            'currency': overview.get('currency'),
        }
    return prices


def parse_store_items(payload):
    """IStoreBrowseService/GetItems JSON -> {app_id: {price_cents, original_cents,
    discount_percent, discount_ends_at, has_trading_cards}} in the requested
    country's currency minor units. Free and free-to-keep games are left out
    (a free copy never drops cards)."""
    items = {}
    for item in ((payload or {}).get('response') or {}).get('store_items') or []:
        app_id = item.get('appid') or item.get('id')
        option = item.get('best_purchase_option') or {}
        final = option.get('final_price_in_cents')
        if not app_id or item.get('success') not in (1, True) or final in (None, ''):
            continue
        final = int(final)
        if final <= 0 or option.get('is_free_to_keep'):
            continue
        ends = [int(d['discount_end_date']) for d in option.get('active_discounts') or []
                if d.get('discount_end_date')]
        categories = (item.get('categories') or {}).get('feature_categoryids') or []
        items[str(app_id)] = {
            'price_cents': final,
            'original_cents': int(option.get('original_price_in_cents') or final),
            'discount_percent': int(option.get('discount_pct') or 0),
            'discount_ends_at': min(ends) if ends else None,
            'has_trading_cards': _TRADING_CARDS_CATEGORY in categories,
        }
    return items


def parse_orderbook(payload):
    """Market orderbook JSON -> {bids, asks, buy_orders, sell_orders, currency}
    or None. `bids`/`asks` are [(price_cents, quantity), ...] best first. The
    response comes wrapped in an extra {"data": ...}; both shapes are accepted."""
    wrapper = (payload or {}).get('data')
    body = wrapper if isinstance(wrapper, dict) and wrapper.get('success') else payload
    if not isinstance(body, dict) or not body.get('success') or not isinstance(body.get('data'), dict):
        return None
    book = body['data']

    def levels(flat):
        flat = [int(value) for value in (flat or [])]
        return [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2) if flat[i + 1] > 0]
    return {
        'bids': levels(book.get('rgCompactBuyOrders')),
        'asks': levels(book.get('rgCompactSellOrders')),
        'buy_orders': int(book.get('cBuyOrders') or 0),
        'sell_orders': int(book.get('cSellOrders') or 0),
        'currency': book.get('eCurrency'),
    }


def fill_net_cents(bids, units):
    """Average net per copy, after fees, for selling `units` copies straight into
    the buy orders, best first. Copies beyond the book's depth are valued at the
    lowest listed bid; no buy orders at all means the copies are worth nothing
    right now."""
    if not bids or units <= 0:
        return 0.0
    remaining, total = units, 0
    for price, quantity in bids:
        take = min(remaining, quantity)
        total += take * seller_receives_cents(price)
        remaining -= take
        if not remaining:
            break
    if remaining:
        total += remaining * seller_receives_cents(bids[-1][0])
    return total / units


def parse_market_cards(payload):
    """Steam market search JSON -> [{name, hash_name, price_cents, listings}]."""
    cards = []
    for row in (payload or {}).get('results') or []:
        cards.append({
            'name': row.get('name'),
            'hash_name': row.get('hash_name'),
            'price_cents': int(row.get('sell_price') or 0),
            'listings': int(row.get('sell_listings') or 0),
        })
    return cards


def parse_badges_page(html):
    """Badges page HTML -> ({app_id: card_drops_remaining}, highest page number
    linked). Rows without a remaining-drops line count as 0."""
    drops = {}
    for chunk in (html or '').split('class="badge_row is_link"')[1:]:
        app = re.search(r'/gamecards/(\d+)/', chunk)
        if not app:
            continue
        remaining = re.search(r'(\d+) card drops? remaining', chunk)
        drops[app.group(1)] = int(remaining.group(1)) if remaining else 0
    pages = [int(p) for p in re.findall(r'badges/\?p=(\d+)', html or '')]
    return drops, max(pages) if pages else 1


def parse_store_country(html):
    """The logged-in account's store country ('TR', 'MD', ...) from any store
    page's embedded config, or None."""
    found = re.search(r'(?:&quot;|")COUNTRY(?:&quot;|"):(?:&quot;|")([A-Z]{2})(?:&quot;|")', html or '')
    return found.group(1) if found else None


def _unescape(text):
    return html.unescape(str(text or '')).strip()


def _html_escape(text):
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def capped_card_prices(prices):
    """Card lowest asks with outliers capped at _OUTLIER_CAP_MULTIPLE x the
    set's median ask. Returns (capped prices, how many were capped)."""
    if not prices:
        return [], 0
    ordered = sorted(prices)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    cap = median * _OUTLIER_CAP_MULTIPLE
    capped = [min(price, cap) for price in prices]
    return capped, sum(1 for price in prices if price > cap)


def _dollars(cents):
    return None if cents is None else round(cents / 100, 2)


def _max_price_cents(settings):
    """The configured maximum game price, in US cents (None = no maximum)."""
    try:
        return int(round(float(settings.get('card_deals_max_price') or 0) * 100)) or None
    except (TypeError, ValueError):
        return None


CONFIG_KEYS = (
    'card_deals_auto_scan_enabled', 'card_deals_scan_interval_hours',
    'card_deals_include_full_price', 'card_deals_max_price', 'card_deals_min_discount',
    'card_deals_alerts_enabled', 'card_deals_alert_min_return_percent',
    'card_deals_alert_min_profit', 'card_deals_chat_id', 'card_deals_fallback_country',
    'card_deals_valuation',
)


def clean_config(body):
    """Validate a config update from the UI. Returns only the recognised keys
    that were supplied, coerced and clamped; raises ValueError on bad input."""
    def number(key, low, high, kind=float):
        try:
            value = kind(body[key])
        except (TypeError, ValueError):
            raise ValueError(f'{key} must be a number')
        if not low <= value <= high:
            raise ValueError(f'{key} must be between {low} and {high}')
        return value

    out = {}
    for key in ('card_deals_auto_scan_enabled', 'card_deals_include_full_price',
                'card_deals_alerts_enabled'):
        if key in body:
            out[key] = bool(body[key])
    if 'card_deals_scan_interval_hours' in body:
        out['card_deals_scan_interval_hours'] = number('card_deals_scan_interval_hours', 1, 168, int)
    if 'card_deals_max_price' in body:
        out['card_deals_max_price'] = round(number('card_deals_max_price', 0.1, 500), 2)
    if 'card_deals_min_discount' in body:
        out['card_deals_min_discount'] = number('card_deals_min_discount', 0, 100, int)
    if 'card_deals_alert_min_return_percent' in body:
        out['card_deals_alert_min_return_percent'] = number('card_deals_alert_min_return_percent', 0, 100000, int)
    if 'card_deals_alert_min_profit' in body:
        out['card_deals_alert_min_profit'] = round(number('card_deals_alert_min_profit', 0, 1000), 2)
    if 'card_deals_chat_id' in body:
        chat = str(body['card_deals_chat_id'] or '').strip()
        if chat and not re.fullmatch(r'-?\d{1,20}|@[A-Za-z0-9_]{4,64}', chat):
            raise ValueError('card_deals_chat_id must be a numeric chat id or @channel name')
        out['card_deals_chat_id'] = chat
    if 'card_deals_valuation' in body:
        valuation = str(body['card_deals_valuation'] or '').strip().lower()
        if valuation not in VALUATIONS:
            raise ValueError(f"card_deals_valuation must be one of: {', '.join(VALUATIONS)}")
        out['card_deals_valuation'] = valuation
    if 'card_deals_fallback_country' in body:
        country = str(body['card_deals_fallback_country'] or '').strip().upper()
        if not re.fullmatch(r'[A-Z]{2}', country):
            raise ValueError('card_deals_fallback_country must be a two-letter country code')
        out['card_deals_fallback_country'] = country
    return out


# ---- the service -------------------------------------------------------------

class CardDealsService:
    def __init__(self, steam_service, settings_manager, fetch=None, sleep=time.sleep,
                 notify=send_notification, cache_file=_CACHE_FILE):
        self.steam = steam_service
        self.settings_manager = settings_manager
        self._fetch = fetch or self._http_get
        self._sleep = sleep
        self._notify = notify
        self._cache_file = cache_file
        self._lock = threading.Lock()
        self._last_call = {}
        self._cooling_until = {}       # host -> epoch until which it is skipped after 429s
        self._job = self._idle_job()
        self._state = self._empty_state()
        self._load_cache()

    @staticmethod
    def _empty_state():
        return {
            'feed': None,
            # Discovery per scope, in ONE country:
            #   {scope: {country, currency, fetched_at, max_price_cents, total_count,
            #            games: {app_id: {name, price_cents, original_cents, discount_percent}}}}
            'store': {SCOPE_SALE: None, SCOPE_FULL: None},
            # Other countries' prices: {country: {app_id: {price_cents, original_cents,
            #                                              discount_percent, currency, fetched_at}}}
            'regional': {},
            'currencies': {},          # {country: {currency, fetched_at}}
            'exchange_rates': None,    # {fetched_at, rates: {currency: units per USD}}
            'verified': {},            # {app_id: {fetched_at, cards: [...]}}
            'orderbooks': {},          # {card hash name: {fetched_at, bids, asks, buy_orders, sell_orders}}
            'accounts': {},            # {steamid: {account_name, country, owned, drops, ...}}
            'alerted': {},             # {deal key: epoch sent}
            'last_scan': None,
        }

    @staticmethod
    def _idle_job():
        return {'running': False, 'phase': None, 'scope': None, 'done': 0, 'total': 0,
                'started_at': None, 'finished_at': None, 'error': None, 'message': None,
                'include_full_price': None}

    # ---- public API ----------------------------------------------------------

    def status(self):
        with self._lock:
            return dict(self._job)

    def start_scan(self, force=False, include_full_price=None):
        """Kick off a background scan unless one is running. The sale scope is
        always scanned first; the full-price scope follows when
        `include_full_price` (default: the setting) is true. Returns the status."""
        if include_full_price is None:
            include_full_price = bool(self.settings_manager.get_settings()
                                      .get('card_deals_include_full_price'))
        with self._lock:
            if self._job['running']:
                return dict(self._job)
            self._job = {**self._idle_job(), 'running': True, 'phase': 'starting',
                         'started_at': time.time(), 'include_full_price': include_full_price}
        threading.Thread(target=self._scan_safely, args=(force, include_full_price),
                         daemon=True).start()
        return self.status()

    def start_background(self):
        """Auto-scan loop: waits for boot to settle, then rescans whenever the
        configured interval has passed since the last finished scan."""
        def loop():
            self._sleep(_BACKGROUND_FIRST_DELAY_SECONDS)
            while True:
                try:
                    settings = self.settings_manager.get_settings()
                    if settings.get('card_deals_auto_scan_enabled'):
                        interval = max(1, int(settings.get('card_deals_scan_interval_hours') or 12)) * 3600
                        last = (self._state.get('last_scan') or {}).get('finished_at') or 0
                        if time.time() - last >= interval and not self.status()['running']:
                            self.start_scan()
                except Exception as e:
                    logger.error('[CARD DEALS] background loop error: %s', e)
                self._sleep(_BACKGROUND_TICK_SECONDS)
        threading.Thread(target=loop, daemon=True).start()

    def deals(self, settings, scope=SCOPE_SALE, include_unprofitable=False):
        """Ranked deals for one scope from the cached scan data, filtered by the
        current config (so changing a filter needs no rescan)."""
        scope = scope if scope in SCOPES else SCOPE_SALE
        snapshot = self._snapshot()
        every_row = self._rows(snapshot, settings)             # one pass, split below
        rows = every_row if scope == SCOPE_ALL else [r for r in every_row if r['scope'] == scope]
        profitable = [r for r in rows if r['is_deal']]
        scope_counts = {}
        for name in (SCOPE_SALE, SCOPE_FULL):
            scoped = [r for r in every_row if r['scope'] == name]
            discovery = snapshot['store'].get(name) or {}
            scope_counts[name] = {
                'games': len(scoped),
                'profitable': sum(1 for r in scoped if r['is_deal']),
                'awaiting_buy_orders': sum(1 for r in scoped if r['awaiting_buy_orders']),
                'scanned_at': discovery.get('fetched_at'),
                'discovery_country': discovery.get('country'),
            }
        return {
            'scope': scope,
            'deals': rows[:_UNPROFITABLE_ROW_LIMIT] if include_unprofitable else profitable,
            'summary': {
                'games': len(rows),
                'profitable': len(profitable),
                'verified': sum(1 for r in profitable if r['verified']),
                'sell_now_deals': sum(1 for r in profitable if r['deal_kind'] == DEAL_SELL_NOW),
                'awaiting_buy_orders': sum(1 for r in rows if r['awaiting_buy_orders']),
                'valuation': settings.get('card_deals_valuation') or VALUATION_BOTH,
                'total_profit_all_accounts': round(sum(r['total_profit_all_accounts'] for r in profitable), 2),
                'countries': self._account_countries_from(snapshot['accounts']),
                'currencies': {country: entry.get('currency')
                               for country, entry in snapshot['currencies'].items()},
                'scopes': scope_counts,
            },
            'accounts': self._accounts_view(snapshot['accounts']),
            'last_scan': snapshot['last_scan'],
            'feed_fetched_at': snapshot['feed_fetched_at'],
            'exchange_rates_fetched_at': (snapshot['exchange_rates'] or {}).get('fetched_at'),
            'job': self.status(),
        }

    def send_test_alert(self, settings):
        return self._notify(self._alert_settings(settings),
                            'Andvari card deals: test message. Alerts will land here.',
                            '🃏 <b>Andvari card deals</b>: test message. Alerts will land here.')

    # ---- scan ------------------------------------------------------------------

    def _scan_safely(self, force, include_full_price):
        try:
            self._scan(force, include_full_price)
            with self._lock:
                self._job.update(phase='done', scope=None)
        except Exception as e:
            logger.error('[CARD DEALS] scan failed: %s', e)
            with self._lock:
                self._job['error'] = str(e)
        finally:
            with self._lock:
                self._job['running'] = False
                self._job['finished_at'] = time.time()
                self._state['last_scan'] = {
                    'started_at': self._job['started_at'],
                    'finished_at': self._job['finished_at'],
                    'error': self._job['error'],
                    'include_full_price': include_full_price,
                }
            self._save_cache()

    def _set_phase(self, phase, scope=None, total=0):
        with self._lock:
            self._job.update(phase=phase, scope=scope, done=0, total=total)

    def _tick(self):
        with self._lock:
            self._job['done'] += 1

    def _note(self, message):
        with self._lock:
            self._job['message'] = message

    def _scan(self, force, include_full_price):
        self._refresh_feed(force)
        self._refresh_accounts(force)
        settings = self.settings_manager.get_settings()
        countries = self._account_countries(settings)
        self._refresh_currencies(countries, force)
        self._refresh_exchange_rates(force)
        discovery_country = self._discovery_country(settings)
        scopes = [SCOPE_SALE] + ([SCOPE_FULL] if include_full_price else [])
        for scope in scopes:
            # Re-read settings per scope: the user may tweak filters mid-scan.
            settings = self.settings_manager.get_settings()
            self._refresh_store(scope, discovery_country, settings, force)
            self._refresh_regional(scope, discovery_country, countries, settings)
            self._verify_shortlist(scope, settings)
            if (settings.get('card_deals_valuation') or VALUATION_BOTH) != VALUATION_LISTING:
                self._refresh_orderbooks(scope, settings)
            self._save_cache()
            self._send_alerts(scope, self.settings_manager.get_settings())

    def _refresh_feed(self, force):
        feed = self._state.get('feed') or {}
        if not force and feed and time.time() - (feed.get('fetched_at') or 0) < _FEED_TIME_TO_LIVE:
            return
        self._set_phase('card set prices (SteamCardExchange)', None, 1)
        games = parse_card_exchange_feed(json.loads(self._fetch(CARD_EXCHANGE_FEED_URL, None, None, 'exchange')))
        if not games:
            raise RuntimeError('SteamCardExchange feed came back empty')
        with self._lock:
            self._state['feed'] = {'fetched_at': time.time(), 'games': games}
        self._tick()

    def _refresh_accounts(self, force):
        steamids = list(self.steam.storage.list_accounts())
        self._set_phase('accounts (owned games, card drops, store country)', None, len(steamids))
        for steamid in steamids:
            with self._lock:
                cached = dict((self._state.get('accounts') or {}).get(steamid) or {})
            data = self.steam.storage.load_account(steamid) or {}
            name = data.get('account_name') or steamid
            if not force and cached.get('owned') is not None \
                    and time.time() - (cached.get('fetched_at') or 0) < _ACCOUNT_TIME_TO_LIVE:
                self._tick()
                continue
            cookies = self.steam.web_session_cookie_for(steamid)
            if not cookies:
                entry = {**cached, 'account_name': name, 'error': 'no fresh web session'}
            else:
                try:
                    entry = self._fetch_account(steamid, name, cookies)
                except Exception as e:           # includes RateLimited: keep the cached data
                    logger.warning('[CARD DEALS] account %s: %s', name, e)
                    entry = {**cached, 'account_name': name, 'error': str(e)}
            with self._lock:
                self._state['accounts'][steamid] = entry
            self._tick()

    def _fetch_account(self, steamid, name, cookies):
        country = parse_store_country(self._fetch(STORE_ACCOUNT_URL, {'l': 'english'}, cookies, 'store'))
        userdata = json.loads(self._fetch(STORE_USERDATA_URL, None, cookies, 'store'))
        owned = sorted({str(app) for app in (userdata.get('rgOwnedApps') or [])})
        drops = {}
        page, last_page = 1, 1
        while page <= min(last_page, _BADGE_MAX_PAGES):
            html = self._fetch(f'https://steamcommunity.com/profiles/{steamid}/badges/',
                               {'l': 'english', 'p': page}, cookies, 'community')
            page_drops, last_page = parse_badges_page(html)
            drops.update(page_drops)
            page += 1
        return {'account_name': name, 'fetched_at': time.time(), 'country': country,
                'owned': owned, 'drops': {app: n for app, n in drops.items() if n > 0},
                'error': None}

    def account_drops(self):
        """{steamid: (card drops left, fetched_at)} from the last account refresh
        (the ASF service switches farming bots on from it)."""
        with self._lock:
            accounts = dict(self._state.get('accounts') or {})
        return {steamid: (sum((entry.get('drops') or {}).values()), entry.get('fetched_at') or 0)
                for steamid, entry in accounts.items()}

    @staticmethod
    def _account_countries_from(accounts):
        return sorted({(a.get('country') or '').upper() for a in accounts.values() if a.get('country')})

    def _account_countries(self, settings):
        with self._lock:
            countries = self._account_countries_from(self._state.get('accounts') or {})
        return countries or [(settings.get('card_deals_fallback_country') or 'TR').upper()]

    def _discovery_country(self, settings):
        """The store country most accounts are in (ties: the fallback country,
        then alphabetical) — the one country whose store is scanned in full."""
        with self._lock:
            tally = Counter((a.get('country') or '').upper()
                            for a in (self._state.get('accounts') or {}).values() if a.get('country'))
        fallback = (settings.get('card_deals_fallback_country') or 'TR').upper()
        if not tally:
            return fallback
        return sorted(tally, key=lambda c: (-tally[c], c != fallback, c))[0]

    def _refresh_currencies(self, countries, force):
        """Learn each account country's store currency (one tiny call each)."""
        due = [c for c in countries
               if force or time.time() - ((self._state['currencies'].get(c) or {}).get('fetched_at') or 0)
               >= _CURRENCY_TIME_TO_LIVE]
        if not due:
            return
        self._set_phase('store currencies', None, len(due))
        for country in due:
            try:
                prices = parse_appdetails_prices(json.loads(self._fetch(STORE_APPDETAILS_URL, {
                    'appids': _CURRENCY_PROBE_APP, 'cc': country.lower(), 'filters': 'price_overview'},
                    None, 'store')))
                currency = (prices.get(_CURRENCY_PROBE_APP) or {}).get('currency')
                if currency:
                    with self._lock:
                        self._state['currencies'][country] = {'currency': currency, 'fetched_at': time.time()}
            except Exception as e:
                logger.warning('[CARD DEALS] currency for %s: %s', country, e)
            self._tick()

    def _refresh_exchange_rates(self, force):
        rates = self._state.get('exchange_rates') or {}
        needed = any((entry or {}).get('currency') not in (None, 'USD')
                     for entry in self._state['currencies'].values())
        if not needed:
            return
        if not force and rates and time.time() - (rates.get('fetched_at') or 0) < _EXCHANGE_RATES_TIME_TO_LIVE:
            return
        self._set_phase('exchange rates', None, 1)
        try:
            payload = json.loads(self._fetch(EXCHANGE_RATES_URL, None, None, 'rates'))
            if payload.get('result') == 'success' and isinstance(payload.get('rates'), dict):
                with self._lock:
                    self._state['exchange_rates'] = {'fetched_at': time.time(), 'rates': payload['rates']}
        except Exception as e:
            logger.warning('[CARD DEALS] exchange rates: %s', e)   # keep the older rates, if any
            self._note(f'Exchange rates unavailable ({e}); non-USD accounts use older rates or stay unpriced')
        self._tick()

    def _currency(self, country):
        return (self._state['currencies'].get(country) or {}).get('currency')

    def _rates(self):
        return (self._state.get('exchange_rates') or {}).get('rates') or {}

    def _refresh_store(self, scope, country, settings, force):
        """Discovery: every game with cards in this scope, in one store country."""
        with self._lock:
            snapshot = self._state['store'].get(scope) or {}
        max_usd_cents = _max_price_cents(settings)
        currency = self._currency(country)
        if currency is None:
            # Never guess: an unknown currency read as USD would mis-price everything.
            raise RuntimeError(f'could not learn the store currency for {country}; try again later')
        fresh = (snapshot and snapshot.get('country') == country
                 and snapshot.get('max_price_cents') == max_usd_cents
                 and time.time() - (snapshot.get('fetched_at') or 0) < _STORE_TIME_TO_LIVE[scope])
        if fresh and not force:
            return
        params = {'query': '', 'count': _STORE_PAGE_SIZE,
                  'category1': 998,      # games only (no DLC, soundtracks, software)
                  'category2': 29,       # has Steam trading cards
                  'sort_by': 'Price_ASC', 'cc': country.lower(), 'infinite': 1, 'l': 'english'}
        if scope == SCOPE_SALE:
            params['specials'] = 1
        # The slider steps are in store dollars: only use them for a USD store.
        step = store_price_step((max_usd_cents or 0) / 100) if currency in (None, 'USD') else None
        if step:
            params['maxprice'] = step
        label = 'on sale' if scope == SCOPE_SALE else 'full price'
        self._set_phase(f'store: {label} games ({country})', scope, 0)
        rates = self._rates()
        games, total = {}, 0
        for page in range(_STORE_MAX_PAGES[scope]):
            payload = json.loads(self._fetch(STORE_SEARCH_URL, {**params, 'start': page * _STORE_PAGE_SIZE},
                                             None, 'store'))
            total = int(payload.get('total_count') or 0)
            with self._lock:
                self._job['total'] = min(_STORE_MAX_PAGES[scope], math.ceil(total / _STORE_PAGE_SIZE))
            for game in parse_store_search(payload.get('results_html')):
                usd = to_usd_cents(game['price_cents'], currency, rates)
                if max_usd_cents and usd is not None and usd > max_usd_cents:
                    continue
                if scope == SCOPE_FULL and game['discount_percent'] > 0:
                    continue       # discounted games belong to the sale scope
                games[game['app_id']] = game
            self._tick()
            # A page can be all free games (skipped by the parser), so only the
            # raw listing ends the scan, never an empty parse.
            if (page + 1) * _STORE_PAGE_SIZE >= total \
                    or 'data-ds-appid' not in (payload.get('results_html') or ''):
                break
        with self._lock:
            self._state['store'][scope] = {
                'country': country, 'currency': currency, 'fetched_at': time.time(),
                'games': games, 'total_count': total, 'max_price_cents': max_usd_cents}

    def _regional_candidates(self, scope, settings):
        """This scope's games worth pricing in the other account countries (see
        _REGIONAL_CANDIDATE_SHARE)."""
        snapshot = self._snapshot()
        discovery = snapshot['store'].get(scope) or {}
        currency = discovery.get('currency')
        rates = snapshot['exchange_rates'].get('rates') if snapshot['exchange_rates'] else {}
        share = _REGIONAL_CANDIDATE_SHARE[scope]
        candidates = []
        for app_id, game in (discovery.get('games') or {}).items():
            if share <= 0:
                candidates.append(app_id)
                continue
            price = to_usd_cents(game['price_cents'], currency, rates)
            net = self._expected_net_cents(snapshot['feed'].get(app_id), snapshot['verified'].get(app_id))
            if price and net is not None and net >= share * price:
                candidates.append(app_id)
        return candidates

    def _refresh_regional(self, scope, discovery_country, countries, settings):
        """Price this scope's candidates in every account country via Steam's
        store backend (IStoreBrowseService/GetItems, 250 games per request). The
        discovery country is included too: its store search has the price but
        not the sale's end date."""
        candidates = self._regional_candidates(scope, settings)
        batches = [candidates[i:i + _STORE_ITEMS_BATCH] for i in range(0, len(candidates), _STORE_ITEMS_BATCH)]
        priceable = [c for c in countries if self._currency(c)]
        if len(priceable) < len(countries):
            self._note('Some account countries have no known store currency yet; they stay unpriced')
        self._set_phase('regional prices and sale end dates', scope, len(batches) * len(priceable))
        for country in priceable:
            currency = self._currency(country)
            for batch in batches:
                try:
                    prices = parse_store_items(json.loads(self._fetch(STORE_BROWSE_ITEMS_URL, {
                        'input_json': json.dumps({
                            'ids': [{'appid': int(app_id)} for app_id in batch],
                            'context': {'language': 'english', 'country_code': country},
                            'data_request': {'include_basic_info': False}}),
                    }, None, 'api')))
                except RateLimited as e:
                    self._note(f'Regional prices stopped early: {e}')
                    return
                except Exception as e:
                    logger.warning('[CARD DEALS] regional prices %s: %s', country, e)
                    self._tick()
                    continue
                now = time.time()
                with self._lock:
                    region = self._state['regional'].setdefault(country, {})
                    for app_id in batch:
                        if app_id in prices:
                            region[app_id] = {**prices[app_id], 'currency': currency, 'fetched_at': now}
                        else:
                            region.pop(app_id, None)     # not sold there (or free): unpriced
                self._tick()

    def _verify_shortlist(self, scope, settings):
        """Check this scope's estimated-profitable games card by card on the Market."""
        now = time.time()
        with self._lock:
            verified = dict(self._state.get('verified') or {})
        # Judged at listing prices (the optimistic side), so buy-order valuation
        # never hides a game from the checks that would confirm it.
        listing = {**settings, 'card_deals_valuation': VALUATION_LISTING}
        due = [r['app_id'] for r in self.deals(listing, scope)['deals']
               if now - ((verified.get(r['app_id']) or {}).get('fetched_at') or 0)
               >= _VERIFIED_TIME_TO_LIVE][:_VERIFY_CAP_PER_SCAN[scope]]
        self._set_phase('Steam Market card check', scope, len(due))
        cookies = self._market_cookies()
        for app_id in due:
            try:
                cards = self._market_cards(app_id, cookies)
            except RateLimited as e:
                self._note(f'Market check stopped early: {e}')
                return
            except Exception as e:
                logger.warning('[CARD DEALS] market check %s: %s', app_id, e)
                self._tick()
                continue
            with self._lock:
                self._state['verified'][app_id] = {'fetched_at': time.time(), 'cards': cards}
            self._tick()

    def _market_cards(self, app_id, cookies):
        cards = []
        for page in range(_MARKET_MAX_PAGES_PER_GAME):
            payload = json.loads(self._fetch(MARKET_SEARCH_URL, {
                'appid': 753, 'norender': 1, 'start': page * _MARKET_PAGE_SIZE,
                'count': _MARKET_PAGE_SIZE, 'currency': 1, 'l': 'english',
                'category_753_Game[]': f'tag_app_{app_id}',
                'category_753_item_class[]': 'item_class_2',          # trading cards
                'category_753_cardborder[]': 'tag_cardborder_0',      # normal (not foil)
            }, cookies, 'community'))
            if not payload.get('success', True):
                break
            page_cards = parse_market_cards(payload)
            cards.extend(page_cards)
            if not page_cards or len(cards) >= int(payload.get('total_count') or 0):
                break
        return cards

    def _market_cookies(self):
        got = self.steam.web_session_cookie()
        return got[1] if got else None

    def _refresh_orderbooks(self, scope, settings):
        """Fetch the buy orders of every card of this scope's Market-checked deals
        (judged at listing prices, the optimistic side, so no real deal is
        missed), most promising games first, capped per scan."""
        listing = {**settings, 'card_deals_valuation': VALUATION_LISTING}
        now = time.time()
        with self._lock:
            books = dict(self._state.get('orderbooks') or {})
            verified = dict(self._state.get('verified') or {})
        missing, stale = [], []
        for row in self.deals(listing, scope)['deals']:
            for card in self._trusted_cards(verified.get(row['app_id'])):
                name = card.get('hash_name')
                if not name or name in missing or name in stale:
                    continue
                if name not in books:
                    missing.append(name)
                elif now - (books[name].get('fetched_at') or 0) >= _ORDERBOOK_TIME_TO_LIVE:
                    stale.append(name)
        # Never-checked cards first, so a capped scan can't keep refreshing the
        # same top games while others are never looked at.
        due = (missing + stale)[:_ORDERBOOK_CAP_PER_SCAN[scope]]
        self._set_phase('buy orders (Steam Market order book)', scope, len(due))
        retried = set()
        while due:
            name = due.pop(0)
            try:
                book = parse_orderbook(json.loads(self._fetch(MARKET_ORDERBOOK_URL, {
                    'q': 'Load', 'qp': json.dumps([753, name])}, None, 'orderbook')))
            except RateLimited as e:
                self._note(f'Buy-order check stopped early: {e}')
                return
            except Exception as e:
                logger.warning('[CARD DEALS] order book %s: %s', name, e)
                self._tick()
                continue
            if book is not None and book.get('currency') not in (None, _USD_MARKET_CURRENCY):
                book = None          # not in US dollars: can't compare, treat as unknown
            if book is None and name not in retried:
                retried.add(name)    # seen live: an occasional empty answer; retry once at the end
                due.append(name)
                continue
            with self._lock:
                if book is None:
                    self._state['orderbooks'].pop(name, None)
                else:
                    self._state['orderbooks'][name] = {**book, 'fetched_at': time.time()}
            self._tick()

    # ---- deal math -------------------------------------------------------------

    def _snapshot(self):
        with self._lock:
            return {
                'feed': ((self._state.get('feed') or {}).get('games')) or {},
                'feed_fetched_at': (self._state.get('feed') or {}).get('fetched_at'),
                'store': {scope: (dict(snap, games=dict(snap.get('games') or {})) if snap else None)
                          for scope, snap in (self._state.get('store') or {}).items()},
                'regional': {country: dict(apps) for country, apps in (self._state.get('regional') or {}).items()},
                'currencies': dict(self._state.get('currencies') or {}),
                'exchange_rates': self._state.get('exchange_rates'),
                'verified': dict(self._state.get('verified') or {}),
                'orderbooks': dict(self._state.get('orderbooks') or {}),
                'accounts': {steamid: {**a, 'owned_set': set(a['owned']) if a.get('owned') is not None else None}
                             for steamid, a in (self._state.get('accounts') or {}).items()},
                'last_scan': self._state.get('last_scan'),
            }

    @staticmethod
    def _trusted_cards(verified):
        """Priced cards of a Market check that is still recent enough to trust."""
        if not verified or time.time() - (verified.get('fetched_at') or 0) >= _VERIFIED_TRUSTED_FOR:
            return []
        return [c for c in (verified.get('cards') or []) if c.get('price_cents')]

    def _expected_net_cents(self, feed_entry, verified):
        """Expected cards net per copy (US cents) at LISTING prices: from the
        Market check (outlier asks capped), else the SteamCardExchange feed."""
        cards = self._trusted_cards(verified)
        card_count = (feed_entry or {}).get('card_count') or len(cards)
        if not card_count:
            return None
        if cards:
            capped, _ = capped_card_prices([c['price_cents'] for c in cards])
            return card_drops(card_count) * sum(undercut_net_cents(p) for p in capped) / len(capped)
        if feed_entry:
            return card_drops(card_count) * undercut_net_cents(round(feed_entry['set_cents'] / card_count))
        return None

    @staticmethod
    def _card_books(cards, orderbooks):
        """The trusted order book of every card of the set, or None when any is
        missing or too old (a partial set can't be valued fairly)."""
        now = time.time()
        books = []
        for card in cards:
            book = orderbooks.get(card.get('hash_name'))
            if not book or now - (book.get('fetched_at') or 0) >= _ORDERBOOK_TRUSTED_FOR:
                return None
            books.append(book)
        return books or None

    def _rows(self, snapshot, settings):
        rates = (snapshot['exchange_rates'] or {}).get('rates') or {}
        stale_after = max(_STORE_TIME_TO_LIVE.values()) * 2
        max_usd_cents = _max_price_cents(settings)
        min_discount = int(settings.get('card_deals_min_discount') or 0)
        valuation = settings.get('card_deals_valuation') or VALUATION_BOTH
        sale = snapshot['store'].get(SCOPE_SALE) or {}
        full = snapshot['store'].get(SCOPE_FULL) or {}
        now = time.time()

        def fresh_regional(country, app_id):
            entry = (snapshot['regional'].get(country) or {}).get(app_id)
            return entry if entry and now - (entry.get('fetched_at') or 0) <= stale_after else None

        rows = []
        for scope, discovery in ((SCOPE_SALE, sale), (SCOPE_FULL, full)):
            for app_id, game in (discovery.get('games') or {}).items():
                if scope == SCOPE_FULL and app_id in (sale.get('games') or {}):
                    continue            # on sale now: shown in the sale scope only
                country = discovery['country']
                # The discovery country's price comes from the store search; its
                # sale end date + trading-card flag from the store backend.
                backend = fresh_regional(country, app_id) or {}
                if backend.get('has_trading_cards') is False:
                    continue            # Steam says it no longer has cards: nothing drops
                listings = {country: {
                    **game, 'currency': discovery.get('currency'),
                    'discount_ends_at': backend.get('discount_ends_at'),
                    'usd_cents': to_usd_cents(game['price_cents'], discovery.get('currency'), rates),
                    'original_usd_cents': to_usd_cents(game.get('original_cents'), discovery.get('currency'), rates),
                }}
                for other in snapshot['regional']:
                    entry = fresh_regional(other, app_id) if other != country else None
                    if entry:
                        listings[other] = {
                            **entry,
                            'usd_cents': to_usd_cents(entry['price_cents'], entry.get('currency'), rates),
                            'original_usd_cents': to_usd_cents(entry.get('original_cents'), entry.get('currency'), rates),
                        }
                listings = {c: listing for c, listing in listings.items() if listing['usd_cents'] is not None}
                row = self._row(app_id, snapshot['feed'].get(app_id), listings, country,
                                snapshot['verified'].get(app_id), snapshot['accounts'],
                                snapshot['orderbooks'], valuation)
                if row is None:
                    continue
                row['scope'] = scope
                if max_usd_cents and row['price_low'] * 100 > max_usd_cents:
                    continue
                if scope == SCOPE_SALE and row['best_discount_percent'] < min_discount:
                    continue
                rows.append(row)
        rows.sort(key=self._rank)
        return rows

    _SOURCE_RANK = {'buy_orders': 0, 'listings': 1, 'estimate': 2}

    @classmethod
    def _rank(cls, row):
        """Deals first — "sell now" deals (profitable at buy orders) ahead of
        "list" deals — then games awaiting their buy-order check; within each,
        the most trustworthy valuation first (buy orders, then Market listings,
        then the feed estimate — whose set prices include outlier asks and run
        optimistic); then the most money across all accounts, then the best
        single-account profit."""
        return (not row['is_deal'], row['deal_kind'] != DEAL_SELL_NOW, not row['awaiting_buy_orders'],
                cls._SOURCE_RANK[row['value_source']], -row['total_profit_all_accounts'],
                -(row['best_profit'] or 0))

    def _row(self, app_id, feed_entry, listings, discovery_country, verified, accounts,
             orderbooks=None, valuation=VALUATION_BOTH):
        """One game's deal math. `price`/`profit` are at the discovery country's
        price (the one most accounts pay); every account is also priced at its
        own country's price, and the game is a deal when ANY account profits —
        regional prices differ several-fold, so one expensive region (or one
        cheap one) must not decide for everybody.

        Cards are valued per `valuation`: "instant" sells into the highest buy
        orders (and, for the all-accounts total, walks the order book for the
        copies every account would sell together); "listing" undercuts the
        lowest ask. Instant falls back to listing prices until every card's
        order book is known; `value_source` says which one was used."""
        if not listings or discovery_country not in listings:
            return None
        any_listing = next(iter(listings.values()))
        verified_cards = self._trusted_cards(verified)
        card_count = (feed_entry or {}).get('card_count') or len(verified_cards)
        if not card_count:
            return None
        drops = card_drops(card_count)
        listing_net = self._expected_net_cents(feed_entry, verified)
        if listing_net is None:
            return None

        regional = {country: listing['usd_cents'] for country, listing in listings.items()}
        price = regional[discovery_country]
        price_low, price_high = min(regional.values()), max(regional.values())

        owners, candidates = [], []
        for steamid, account in accounts.items():
            name = account.get('account_name') or steamid
            owned = account.get('owned_set')
            if owned is not None and app_id in owned:
                owners.append({'account_name': name,
                               'drops_remaining': (account.get('drops') or {}).get(app_id, 0)})
                continue
            country = (account.get('country') or '').upper()
            candidates.append((name, country or None, regional.get(country), owned is None))

        books = self._card_books(verified_cards, orderbooks or {}) if verified_cards else None
        instant_net = None
        top_bid_nets = []
        if books:
            top_bid_nets = [seller_receives_cents(book['bids'][0][0]) if book['bids'] else 0 for book in books]
            instant_net = drops * sum(top_bid_nets) / len(top_bid_nets)
        copies_per_card = None
        sell_now_all_net = None
        if instant_net is not None:
            # Every account that doesn't own it sells its drops at the same time:
            # about this many copies of each card hit the buy orders together.
            copies_per_card = max(1, math.ceil(len(candidates) * drops / card_count))
            sell_now_all_net = drops * sum(fill_net_cents(book['bids'], copies_per_card)
                                           for book in books) / len(books)

        cheapest_card = fewest_listings = listing_worst_net = None
        capped_count, cards_view = 0, None
        if verified_cards:
            capped, capped_count = capped_card_prices([c['price_cents'] for c in verified_cards])
            listing_worst_net = drops * min(undercut_net_cents(p) for p in capped)
            cheapest_card = min(c['price_cents'] for c in verified_cards)
            fewest_listings = min(c['listings'] for c in verified_cards)
            average_card = sum(capped) / len(capped)
            cards_view = [{
                'name': c.get('name'), 'lowest_ask': _dollars(c['price_cents']),
                'valued_at': _dollars(value), 'listings': c['listings'], 'capped': value < c['price_cents'],
                'highest_bid': _dollars(books[i]['bids'][0][0]) if books and books[i]['bids'] else None,
                'buy_orders': books[i]['buy_orders'] if books else None,
            } for i, (c, value) in enumerate(zip(verified_cards, capped))]
        else:
            average_card = feed_entry['set_cents'] / card_count

        def measure(per_copy_net, all_accounts_net, worst_net):
            """Deal metrics for one way of valuing the cards (US cents in, dollars out)."""
            priced = [(price_cents, per_copy_net - price_cents) for _, _, price_cents, _ in candidates
                      if price_cents is not None]
            if accounts:
                best_price_cents = min((p for p, _ in priced), default=None)
                looks_profitable = any(gain > 0 for _, gain in priced)
            else:
                best_price_cents = price_low         # no account data yet: the cheapest region
                looks_profitable = per_copy_net - price_low > 0
            best = per_copy_net - best_price_cents if best_price_cents is not None else None
            total = sum(all_accounts_net - p for p, _ in priced if all_accounts_net - p > 0)
            return {
                'expected_net': _dollars(per_copy_net),
                'profit': _dollars(per_copy_net - price),
                'return_percent': round((per_copy_net - price) / price * 100, 1) if price else None,
                'best_profit': _dollars(best),
                'best_return_percent': round(best / best_price_cents * 100, 1) if best is not None and best_price_cents else None,
                'profitable_accounts': sum(1 for _, gain in priced if gain > 0),
                'total_profit_all_accounts': round(total / 100, 2),
                'worst_case_net': _dollars(worst_net),
                'worst_case_profit': _dollars(worst_net - price) if worst_net is not None else None,
                'is_profitable': looks_profitable,
            }

        list_metrics = measure(listing_net, listing_net, listing_worst_net)
        sell_now_metrics = (measure(instant_net, sell_now_all_net, drops * min(top_bid_nets))
                            if instant_net is not None else None)

        # Which metrics lead the row. Instant: buy orders, or (without them yet)
        # listing prices marked as only "awaiting" — measured live, listing prices
        # overstate what cards sell for right now by 5-50x. Listing: sell price.
        # Both: sell price leads, buy orders ride along; a deal either way.
        if valuation == VALUATION_INSTANT and sell_now_metrics:
            primary, value_source = sell_now_metrics, 'buy_orders'
        else:
            primary, value_source = list_metrics, ('listings' if verified_cards else 'estimate')
        if valuation == VALUATION_INSTANT:
            is_deal = bool(sell_now_metrics and sell_now_metrics['is_profitable'])
            awaiting_buy_orders = not sell_now_metrics and list_metrics['is_profitable']
        elif valuation == VALUATION_LISTING:
            is_deal, awaiting_buy_orders = list_metrics['is_profitable'], False
        else:
            is_deal = list_metrics['is_profitable'] or bool(sell_now_metrics and sell_now_metrics['is_profitable'])
            awaiting_buy_orders = False
        if sell_now_metrics and sell_now_metrics['is_profitable'] and valuation != VALUATION_LISTING:
            deal_kind = DEAL_SELL_NOW
        elif is_deal:
            deal_kind = DEAL_LIST
        else:
            deal_kind = None
        per_copy = (instant_net if primary is sell_now_metrics else listing_net)
        buyers = [{
            'account_name': name, 'country': country, 'price': _dollars(account_price),
            'profit': _dollars(per_copy - account_price) if account_price is not None else None,
            'profit_sell_now': (_dollars(instant_net - account_price)
                                if account_price is not None and instant_net is not None else None),
            'profit_list': _dollars(listing_net - account_price) if account_price is not None else None,
            'ownership_unknown': unknown,
        } for name, country, account_price, unknown in candidates]

        headline = listings[discovery_country]
        return {
            'app_id': app_id,
            'name': _unescape((feed_entry or {}).get('name') or any_listing.get('name') or app_id),
            'card_count': card_count,
            'card_drops': drops,
            'price': _dollars(price),
            'price_low': _dollars(price_low),
            'price_high': _dollars(price_high),
            'discovery_country': discovery_country,
            # Discount + original of the headline (discovery-country) price; the
            # deepest discount in any account country drives the minimum-discount filter.
            'original_price': (_dollars(headline.get('original_usd_cents'))
                               if headline.get('discount_percent') else None),
            'discount_percent': headline.get('discount_percent') or 0,
            'best_discount_percent': max(listing.get('discount_percent') or 0 for listing in listings.values()),
            'discount_ends_at': headline.get('discount_ends_at') if headline.get('discount_percent') else None,
            'regional_prices': {country: {
                'usd': _dollars(listing['usd_cents']),
                'local': _dollars(listing['price_cents']),
                'currency': listing.get('currency') or 'USD',
            } for country, listing in listings.items()},
            'set_price': _dollars((feed_entry or {}).get('set_cents')),
            'average_card_price': _dollars(average_card),
            'value_source': value_source,
            # The leading metrics (see above), then both valuations in full.
            **{key: primary[key] for key in ('expected_net', 'profit', 'return_percent', 'best_profit',
                                             'best_return_percent', 'profitable_accounts',
                                             'total_profit_all_accounts', 'worst_case_net',
                                             'worst_case_profit')},
            'expected_net_listing': _dollars(listing_net),
            'expected_net_instant': _dollars(instant_net),
            'sell_now': sell_now_metrics,
            'list': list_metrics,
            'deal_kind': deal_kind,
            'is_deal': is_deal,
            'awaiting_buy_orders': awaiting_buy_orders,
            'copies_per_card_all_accounts': copies_per_card,
            'verified': bool(verified_cards),
            'verified_at': (verified or {}).get('fetched_at') if verified_cards else None,
            'cheapest_card_price': _dollars(cheapest_card),
            'fewest_listings': fewest_listings,
            'capped_cards': capped_count,
            'cards': cards_view,
            'owners': owners,
            'buyers': buyers,
            'links': {
                'store': f'https://store.steampowered.com/app/{app_id}/',
                # SteamDB's per-game cards / emoticons / backgrounds page.
                'steamdb': f'https://steamdb.info/app/{app_id}/communityitems/',
                'market': ('https://steamcommunity.com/market/search?appid=753'
                           f'&category_753_Game%5B%5D=tag_app_{app_id}'
                           '&category_753_item_class%5B%5D=item_class_2'),
                'card_exchange': f'https://www.steamcardexchange.net/index.php?gamepage-appid-{app_id}',
            },
        }

    @staticmethod
    def _accounts_view(accounts):
        return sorted(({
            'account_name': a.get('account_name'),
            'country': a.get('country'),
            'owned_games': len(a.get('owned') or []) if a.get('owned') is not None else None,
            'games_with_drops': len(a.get('drops') or {}),
            'card_drops_remaining': sum((a.get('drops') or {}).values()),
            'fetched_at': a.get('fetched_at'),
            'error': a.get('error'),
        } for a in accounts.values()), key=lambda a: (a['account_name'] or '').lower())

    # ---- alerts ------------------------------------------------------------------

    @staticmethod
    def _alert_settings(settings):
        chat = str(settings.get('card_deals_chat_id') or '').strip()
        return {**settings, 'telegram_chat_id': chat} if chat else settings

    def _send_alerts(self, scope, settings):
        if not settings.get('card_deals_alerts_enabled'):
            return
        try:
            min_return = float(settings.get('card_deals_alert_min_return_percent') or 0)
            min_profit = float(settings.get('card_deals_alert_min_profit') or 0)
        except (TypeError, ValueError):
            min_return, min_profit = 50.0, 0.25
        now = time.time()
        with self._lock:
            alerted = {k: t for k, t in (self._state.get('alerted') or {}).items()
                       if now - t < _ALERTED_MEMORY_SECONDS}
        fresh = []
        for row in self.deals(settings, scope)['deals']:
            # Keyed on the cheapest AND dearest regional price, so a drop in any
            # one region counts as a new deal.
            key = f"{row['app_id']}:{int(round(row['price_low'] * 100))}:{int(round(row['price_high'] * 100))}"
            # Thresholds apply to the best account that can still buy it (the
            # deals list already excludes games no account profits from).
            if key in alerted or (row['best_profit'] or 0) < min_profit \
                    or (row['best_return_percent'] or 0) < min_return:
                continue
            fresh.append((key, row))
        if fresh:
            text, html = self._alert_message([row for _, row in fresh], scope)
            result = self._notify(self._alert_settings(settings), text, html)
            if result.get('ok'):
                for key, _ in fresh:
                    alerted[key] = now
            else:
                logger.warning('[CARD DEALS] alert not sent: %s', result.get('error'))
        with self._lock:
            self._state['alerted'] = alerted

    @staticmethod
    def _alert_message(rows, scope):
        sources = {'buy_orders': 'sells instantly to buy orders', 'listings': 'at Market listing prices',
                   'estimate': 'estimate, not yet checked'}

        def ends(row):
            left = (row.get('discount_ends_at') or 0) - time.time()
            if left <= 0:
                return ''
            return f", sale ends in {int(left // 3600)} h" if left >= 3600 else ', sale ends within the hour'

        kinds = {DEAL_SELL_NOW: 'sell now: profitable selling the drops straight to buy orders',
                 DEAL_LIST: 'list only: profitable only when the cards are listed at the sell price'}

        def line(row, html):
            check = sources[row['value_source']]
            if row.get('deal_kind') in kinds:
                check = f"{kinds[row['deal_kind']]}; {check}"
            name = row['name']
            if html:
                name = f"<a href=\"{row['links']['store']}\">{_html_escape(name)}</a>"
            discount = f" (−{row['discount_percent']}%)" if row['discount_percent'] else ''
            price = (f"${row['price_low']:.2f}–${row['price_high']:.2f}" if row['price_low'] != row['price_high']
                     else f"${row['price']:.2f}")
            nets = f"cards ${row['expected_net']:.2f} net"
            if row.get('sell_now') and row['value_source'] != 'buy_orders':
                nets = f"cards ${row['sell_now']['expected_net']:.2f} sold now / ${row['list']['expected_net']:.2f} listed"
            return (f"• {name}: {price}{discount}{ends(row)} → {nets}, "
                    f"best +${row['best_profit']:.2f} (+{row['best_return_percent']:.0f}%), "
                    f"{row['profitable_accounts']} accounts profit, +${row['total_profit_all_accounts']:.2f} "
                    f"on all of them [{check}]")
        label = 'on sale' if scope == SCOPE_SALE else 'full price'
        shown = rows[:_ALERT_MAX_LINES]
        more = len(rows) - len(shown)
        tail = f'\n…and {more} more in Heimdall → Huginn → Card deals' if more else ''
        text = '\n'.join([f'Andvari card deals ({label}): {len(rows)} new']
                         + [line(r, False) for r in shown]) + tail
        html = '\n'.join([f'🃏 <b>Andvari card deals ({label})</b>: {len(rows)} new']
                         + [line(r, True) for r in shown]) + tail
        return text, html

    # ---- HTTP ----------------------------------------------------------------------

    def _http_get(self, url, params, cookies, host):
        """GET with a per-host minimum gap, a 429 backoff ladder plus cooldown
        circuit breaker (so a rate-limited host is not hammered for the rest of
        the scan), and retries for transient failures (timeouts, 5xx, dropped
        connections) so one network hiccup never kills a long scan."""
        gap = _HOST_GAP_SECONDS.get(host, 1.0)
        full = f'{url}?{urllib.parse.urlencode(params)}' if params else url
        headers = {'User-Agent': _USER_AGENT}
        if host == 'exchange':
            headers['X-Requested-With'] = 'XMLHttpRequest'
        if host == 'orderbook':
            headers['X-Requested-With'] = 'XMLHttpRequest'
            headers['X-Valve-Request-Type'] = 'queryAction'
        if cookies:
            headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items()
                                          if k in ('steamLoginSecure', 'sessionid'))
        if time.time() < self._cooling_until.get(host, 0):
            raise RateLimited(f'Steam rate limit (429) on {host}; cooling down, try again later')
        rate_limited = transient = 0
        while True:
            wait = gap - (time.time() - self._last_call.get(host, 0))
            if wait > 0:
                self._sleep(wait)
            self._last_call[host] = time.time()
            try:
                with urllib.request.urlopen(urllib.request.Request(full, headers=headers),
                                            timeout=30) as response:
                    return response.read().decode('utf-8', 'replace')
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    if rate_limited >= len(_RATE_LIMIT_BACKOFF_SECONDS):
                        self._cooling_until[host] = time.time() + _RATE_LIMIT_COOLDOWN_SECONDS
                        raise RateLimited(f'Steam rate limit (429) on {host}; try again later')
                    pause = _RATE_LIMIT_BACKOFF_SECONDS[rate_limited]
                    rate_limited += 1
                    logger.warning('[CARD DEALS] 429 from %s, waiting %ss', host, pause)
                    self._note(f'Steam rate limit hit, waiting {pause} seconds')
                    self._sleep(pause)
                    self._note(None)
                    continue
                if e.code < 500 or transient >= len(_TRANSIENT_RETRY_SECONDS):
                    raise
                pause = _TRANSIENT_RETRY_SECONDS[transient]
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                    http.client.HTTPException) as e:
                if transient >= len(_TRANSIENT_RETRY_SECONDS):
                    raise
                pause = _TRANSIENT_RETRY_SECONDS[transient]
                logger.warning('[CARD DEALS] %s on %s, retrying in %ss', e, host, pause)
            transient += 1
            self._sleep(pause)

    # ---- disk cache ----------------------------------------------------------------

    def _load_cache(self):
        try:
            if not os.path.exists(self._cache_file):
                return
            with gzip.open(self._cache_file, 'rt', encoding='utf-8') as f:
                loaded = json.load(f) or {}
            store = loaded.get('store') or {}
            layout_ok = set(store) <= {SCOPE_SALE, SCOPE_FULL} and all(
                snap is None or (isinstance(snap, dict) and 'country' in snap) for snap in store.values())
            if not layout_ok:
                loaded.pop('store', None)          # older layout: discovery is simply rescanned
            for key, default in self._empty_state().items():
                self._state[key] = loaded.get(key, default) if loaded.get(key) is not None else default
            for scope in (SCOPE_SALE, SCOPE_FULL):
                self._state['store'].setdefault(scope, None)
        except Exception as e:
            logger.warning('[CARD DEALS] could not load cache: %s', e)

    def _save_cache(self):
        with self._lock:
            snapshot = json.dumps(self._state)
        try:
            os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)
            temporary = f'{self._cache_file}.tmp'
            with gzip.open(temporary, 'wt', encoding='utf-8') as f:
                f.write(snapshot)
            os.replace(temporary, self._cache_file)
        except Exception as e:
            logger.warning('[CARD DEALS] could not save cache: %s', e)
