"""The one place Heimdall asks Tradeon pulse for prices (the master); every feature reads from it.

Every pulse price table is ``POST /api/table/counter-strike/TradeOnMarket/{market}/all`` with one
body: the first market is always Tradeon's lowest listing ("Sell"), the second market is priced as
one price type (``Sell`` = lowest listing, ``SellWithoutHold``, ``Buy`` = buy orders, ``Median30d``…).
So a table is fully named by (market id, price type), and this module is the only code that builds
that request. Huginn's Arbitrage profiles and generated pairs, Harvest, Cross-Profile, Case
Arbitrage (refresh and alert polls), the portfolio valuation, Gjallarhorn, the LOOT.Farm boards and
Store Catalogue Arbitrage all call ``table()`` (through ``HuginnService``) and share its answers:

- **One download per table at a time** (single flight): a caller that asks while that table is
  being downloaded waits for the same download instead of starting another; at most
  ``MAX_PARALLEL_DOWNLOADS`` (4) tables download at once.
- **Freshness is the caller's choice**: ``max_age`` seconds (``FRESH`` = 10 minutes for background
  reads; a button the user pressed asks for ``ON_DEMAND`` = 1 minute). A table is kept
  ``KEEP_SECONDS`` after its download and then dropped.
- **Compact**: a raw table is ~32,000 rows, ~59 MB of JSON and ~155 MB as Python objects. Only the
  fields Heimdall reads are kept, as tuples, with each item's name and image stored once for all
  tables (~15 MB a table). ``rows()`` rebuilds pulse-shaped rows (fresh dicts) for the callers that
  want them.
- **Normalised request**: one body for every table (``BASE_BODY``): no price, count, profit or
  update-time filter, every item type, sold-out items shown (``displaySoldOutItems`` true; on
  2026-10-15 it changed no table's item count), sorted by profit percent, every row in one page.
- **Bonus-balance markets** are turned into real dollars once, on arrival (``scale``, SkinSwap
  (Trade) ÷ 1.4), keeping the page price as ``pagePrice``.
- **Listeners** (``add_listener``) hear about every downloaded table on one background thread, so
  a slave can keep what it needs (Store Catalogue Arbitrage keeps the store items).
- ``status()`` lists every table, its age, downloads and reads since boot.

Facts about the pulse API and ideas not used yet are in docs/internals/pulse-api.md.
"""
import copy
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

TABLE_URL = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/{}/all'
FRESH = 10 * 60                 # background reads (boards, Case Arbitrage refresh, warmers)
ON_DEMAND = 60                  # a button the user pressed (Fetch live data, Refresh)
KEEP_SECONDS = FRESH            # a table older than this is dropped (nobody asks for older)
# Each download peaks at ~200 MB (raw bytes, text, parsed rows), so four at most: the Case
# Arbitrage alert poll's 7 markets arrive in two waves, about 3 seconds apart.
MAX_PARALLEL_DOWNLOADS = 4
WAIT_FOR_DOWNLOAD = 600         # seconds a caller waits on another caller's download (queue included)

_COLOR_INDICATORS = [{"isEnabled": False, "profitPercent": percent, "color": color}
                     for percent, color in ((35, "Green"), (36, "Blue"), (37, "Red"), (38, "Orange"), (39, "Purple"))]


def _item_type_options():
    return {
        "itemTypes": None, "itemQualities": None, "isStatTrack": None,
        "isSouvenir": None, "isSticker": None, "isGraffiti": None,
        "indicationOptions": {"isEnabled": True, "colorIndicators": copy.deepcopy(_COLOR_INDICATORS)},
        "isOverstock": None, "displaySoldOutItems": True,
        "displayOnlyOverridenItems": False, "firstMarketTime": None,
        "secondMarketTime": None, "holdOptions": None,
    }


# The one request body. firstMarket is Tradeon's lowest listing; the second market's price type is
# set per table. No filter of any kind: the Arbitrage tabs filter on the page.
BASE_BODY = {
    "templateId": None,
    "firstMarketOptions": {
        "firstMarketPriceType": "Sell",
        "firstMarketPriceFilter": {"minValue": None, "maxValue": None},   # no price floor (Ivan, 2026-10-12)
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
    "counterStrikeItemTypeOptions": _item_type_options(),
    "dotaItemTypeOptions": _item_type_options(),
    "rustItemTypeOptions": _item_type_options(),
    "rarityFilter": None,
    "salesCountPeriod": "Week",
    "salesCountFilters": [],
    "holdFilter": None,
    "isOverstock": None,
    "displaySoldOutItems": True,
    "displayOnlyOverridenItems": False,
    "countFilterMode": "TotalOffersCount",
    "glowOldListItems": True,
    "paginationRequest": {
        "orderParameters": {"key": "profitPercent", "sortOrder": "Descending"},
        "skipCount": 0,
        "takeCount": 9999999,
    },
}


def body_for(price_type):
    """The request body for a table whose second market is priced as *price_type*."""
    body = copy.deepcopy(BASE_BODY)
    body["secondMarketOptions"]["secondMarketPriceType"] = price_type
    return body


# A market side of a row, compact: (price, count, rising, falling, overstock info, page price).
PRICE, COUNT, RISING, FALLING, OVERSTOCK, PAGE_PRICE = range(6)


def _quote(market, scale=None):
    if not isinstance(market, dict) or market.get('price') is None:
        return None
    price = market['price']
    page_price = None
    if scale and price:
        page_price, price = price, round(price * scale, 2)
    count = market.get('totalOffersCount')
    if count is None:
        count = market.get('count')
    return (price, count,
            bool(market.get('isRaising')), bool(market.get('isFalling')),
            market.get('overstockInfo'), page_price)


def _market_dict(quote):
    """A pulse-shaped market object (a fresh dict) from a compact quote."""
    if quote is None:
        return {}
    market = {'price': quote[PRICE], 'totalOffersCount': quote[COUNT],
              'isRaising': quote[RISING], 'isFalling': quote[FALLING]}
    if quote[OVERSTOCK] is not None:
        market['overstockInfo'] = copy.deepcopy(quote[OVERSTOCK])
    if quote[PAGE_PRICE] is not None:
        market['pagePrice'] = quote[PAGE_PRICE]
    return market


class PulseTable:
    """One downloaded table: (market id, price type), when its download started, and per item
    (first market quote, second market quote, pulse's profit, pulse's profit percent)."""

    def __init__(self, market_id, price_type, started_at, items, entries, seconds):
        self.market_id = market_id
        self.price_type = price_type
        self.started_at = started_at
        self.seconds = seconds
        self._items = items          # the feed's shared {name: (itemName, imageUrl)}
        self.entries = entries       # {name: (first, second, profit, profit percent)}, pulse's order

    def __len__(self):
        return len(self.entries)

    def prices(self, side='second'):
        """{name: price} of one side ('first' = Tradeon's lowest listing)."""
        position = 0 if side == 'first' else 1
        return {name: entry[position][PRICE] for name, entry in self.entries.items()
                if entry[position] and entry[position][PRICE]}

    def index(self, names=None, side='second'):
        """{name: {price, count, image, rising, falling}} of one side, for *names* (default every item)."""
        position = 0 if side == 'first' else 1
        wanted = self.entries if names is None else {n: self.entries[n] for n in names if n in self.entries}
        out = {}
        for name, entry in wanted.items():
            quote = entry[position]
            if quote and quote[PRICE]:
                out[name] = {'price': quote[PRICE], 'count': quote[COUNT],
                             'image': (self._items.get(name) or (None, None))[1],
                             'rising': quote[RISING], 'falling': quote[FALLING]}
        return out

    def rows(self, names=None):
        """Pulse-shaped rows (fresh dicts, safe to change), in pulse's order."""
        entries = self.entries if names is None else {n: self.entries[n] for n in names if n in self.entries}
        out = []
        for name, (first, second, profit, profit_percent) in entries.items():
            item_name, image = self._items.get(name) or ({'marketHashName': name}, None)
            out.append({'itemName': dict(item_name), 'imageUrl': image,
                        'firstMarket': _market_dict(first), 'secondMarket': _market_dict(second),
                        'profit': profit, 'profitPercent': profit_percent})
        return out


class _Flight:
    def __init__(self):
        self.done = threading.Event()
        self.table = None
        self.error = None


class PulseFeed:
    def __init__(self, post, scale=None, clock=time.time):
        """*post(url, token, body)* sends one request and returns the parsed rows; *scale*:
        {market id: factor} for bonus-balance markets."""
        self._post = post
        self._scale = dict(scale or {})
        self._clock = clock
        self._lock = threading.Lock()
        self._tables = {}            # (market id, price type) -> PulseTable
        self._flights = {}           # (market id, price type) -> _Flight while downloading
        self._items = {}             # name -> (itemName dict, imageUrl), shared by every table
        self._downloads = threading.BoundedSemaphore(MAX_PARALLEL_DOWNLOADS)
        self._listeners = []
        self._listener_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pulse-feed-listener')
        self._counts = {}            # key -> {'downloads', 'reads', 'failures', 'last_error'}
        self._started = clock()

    # ---- the one request ---------------------------------------------------------------------

    def table(self, token, market_id, price_type, max_age=FRESH):
        """The (market id, price type) table, downloaded at most *max_age* seconds ago."""
        key = (market_id, price_type)
        now = self._clock()
        with self._lock:
            counts = self._counts.setdefault(key, {'downloads': 0, 'reads': 0, 'failures': 0, 'last_error': None})
            counts['reads'] += 1
            self._drop_old_tables(now)
            cached = self._tables.get(key)
            if cached and now - cached.started_at < max_age:
                return cached
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight()
        if not leader:
            if not flight.done.wait(WAIT_FOR_DOWNLOAD):
                raise TimeoutError(f'pulse {market_id} {price_type}: still downloading after {WAIT_FOR_DOWNLOAD} s')
            if flight.error:
                raise flight.error
            if flight.table is None:
                raise RuntimeError(f'pulse {market_id} {price_type}: the download was interrupted')
            return flight.table
        try:
            table = self._download(token, market_id, price_type)
            flight.table = table
            return table
        except Exception as error:
            flight.error = error
            with self._lock:
                counts['failures'] += 1
                counts['last_error'] = str(error)[:200]
            raise
        finally:
            with self._lock:
                self._flights.pop(key, None)
            flight.done.set()

    def _download(self, token, market_id, price_type):
        with self._downloads:
            started = self._clock()
            rows = self._post(TABLE_URL.format(market_id), token, body_for(price_type))
            seconds = self._clock() - started
        if not isinstance(rows, list):
            raise ValueError(f'pulse {market_id} {price_type}: the answer is not a table')
        # Parsed outside the lock: callers of other (cached) tables never wait for it.
        scale = self._scale.get(market_id)
        entries, new_items, repeated = {}, {}, 0
        for row in rows:
            item_name = (row or {}).get('itemName') or {}
            name = item_name.get('marketHashName')
            if not name:
                continue
            if name in entries:
                repeated += 1            # never seen (2026-10-15); the first row is kept
                continue
            new_items.setdefault(name, (item_name, row.get('imageUrl')))
            entries[name] = (_quote(row.get('firstMarket')), _quote(row.get('secondMarket'), scale),
                             row.get('profit'), row.get('profitPercent'))
        if repeated:
            logger.warning('[PULSE] %s %s: %d rows repeat an item name; the first of each was kept',
                           market_id, price_type, repeated)
        with self._lock:
            for name, meta in new_items.items():
                self._items.setdefault(name, meta)
            table = PulseTable(market_id, price_type, started, self._items, entries, round(seconds, 1))
            self._tables[(market_id, price_type)] = table
            self._counts[(market_id, price_type)]['downloads'] += 1
            self._counts[(market_id, price_type)]['last_error'] = None
            self._drop_old_tables(self._clock())
        logger.info('[PULSE] %s %s: %d items in %.1f s', market_id, price_type, len(entries), seconds)
        self._announce(table)
        return table

    def _drop_old_tables(self, now):
        """Nobody asks for a table older than KEEP_SECONDS: drop those (caller holds the lock)."""
        for key in [key for key, table in self._tables.items() if now - table.started_at > KEEP_SECONDS]:
            del self._tables[key]

    # ---- slaves that want every table --------------------------------------------------------

    def add_listener(self, listener):
        """listener(table) after every download, on one background thread (never the caller's)."""
        self._listeners.append(listener)

    def _announce(self, table):
        if not self._listeners:
            return

        def tell():
            for listener in list(self._listeners):
                try:
                    listener(table)
                except Exception as error:
                    logger.warning('[PULSE] listener failed (%s %s): %s', table.market_id, table.price_type, error)
        self._listener_thread.submit(tell)

    def flush_listeners(self, timeout=30):
        """Wait until every download so far has been handed to the listeners (tests)."""
        self._listener_thread.submit(lambda: None).result(timeout=timeout)

    # ---- status ---------------------------------------------------------------------------------

    def status(self):
        now = self._clock()
        with self._lock:
            tables = []
            for key, counts in sorted(self._counts.items()):
                table = self._tables.get(key)
                tables.append({'market': key[0], 'price_type': key[1], **counts,
                               'items': len(table) if table else None,
                               'age_seconds': round(now - table.started_at) if table else None,
                               'download_seconds': table.seconds if table else None,
                               'downloading': key in self._flights})
            downloads = sum(counts['downloads'] for counts in self._counts.values())
            reads = sum(counts['reads'] for counts in self._counts.values())
        return {'since': self._started, 'downloads': downloads, 'reads': reads,
                'served_from_memory': reads - downloads, 'tables_in_memory': sum(1 for table in tables if table['items'] is not None),
                'fresh_seconds': FRESH, 'on_demand_seconds': ON_DEMAND, 'keep_seconds': KEEP_SECONDS,
                'tables': tables}
