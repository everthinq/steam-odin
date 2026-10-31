"""The one place Heimdall asks Tradeon pulse for prices (the master); every feature reads from it.

**Only Huginn Arbitrage downloads** (Ivan's master-slave rule, 2026-10-31): the keeper keeps the
tables of the active Huginn profiles fresh (``set_active``), "Fetch live data" and a profile
opening by itself download through ``reading(download=True)``, and "Check live" asks pulse for one
item (``lookup``). Every other reader (Case Arbitrage and its alerts, Store Catalogue Arbitrage,
Store Boosts, Harvest, Cross-Profile, Price dips, Gjallarhorn, Draupnir's valuation, the LOOT.Farm
boards) is a slave: it gets the copy in memory, whatever its age, waits for a download already
under way, and otherwise gets ``NotActive``. It never causes a request.

Every pulse price table is ``POST /api/table/counter-strike/TradeOnMarket/{market}/all`` with one
body: the first market is always Tradeon's lowest listing ("Sell"), the second market is priced as
one price type (``Sell`` = lowest listing, ``SellWithoutHold``, ``Buy`` = buy orders, ``Median30d``…).
So a table is fully named by (market id, price type), and this module is the only code that builds
that request. Huginn's Arbitrage profiles and generated pairs, Harvest, Cross-Profile, Case
Arbitrage (refresh and alert polls), the portfolio valuation, Gjallarhorn, the LOOT.Farm boards,
Price dips and Store Catalogue Arbitrage all call ``table()`` (through ``HuginnService``) and share
its answers:

- **One download per table at a time** (single flight): a caller that asks while that table is
  being downloaded waits for the same download instead of starting another; at most
  ``MAX_PARALLEL_DOWNLOADS`` (4) tables download at once.
- **Pages first**: background jobs (``job()``, the keeper) take at most
  ``MAX_BACKGROUND_DOWNLOADS`` (3) of those slots, and a page or a button waiting for a slot goes
  before them, so a click never queues behind the jobs' downloads (on a slow connection a table
  takes minutes). A page that joins a job's queued download moves that download to the front.
- **Freshness is the caller's choice**: ``max_age`` seconds (``FRESH`` = 10 minutes for background
  reads; a button the user pressed asks for ``ON_DEMAND`` = 1 minute). A table is kept
  ``KEEP_SECONDS`` after its download and then dropped.
- **Active tables** (``set_active``): the active profiles' tables every few minutes, Buff163's
  lowest listing once a day (the off-Steam reference), and one-time downloads (``download_once``:
  the statistics seed). The keeper (``start_keeper``) downloads them one at a time, only inside
  the active hours (``set_active_hours``), and after the Mac slept it waits a tick for the network
  and starts again from a clean slate. An active table stays in memory until its next download.
- **Per request** (``reading``): Huginn Arbitrage's routes may download (*download*), a profile
  opening by itself accepts the active copy up to its interval (*accept_kept*), and every table
  the request read is recorded with its download time, so the page can say how old its prices are.
- **Compact**: a raw table is ~32,000 rows, ~59 MB of JSON (~4-5 MB gzipped on the wire, see
  ``HuginnService._post_tradeon``) and ~155 MB as Python objects. Only the
  fields Heimdall reads are kept, as tuples, with each item's name and image stored once for all
  tables (~15 MB a table). ``rows()`` rebuilds pulse-shaped rows (fresh dicts) for the callers that
  want them. Each quote keeps the market's own update time (``offersUpdateTime``), so a page can
  grey out a price the market has not refreshed for a day or more.
- **Weekly sales for free**: every row carries ``itemPopularity`` (sales in the last week on Steam,
  Market.CSGO, Tradeon and the table's own market); the feed keeps the latest count per item and
  market (``weekly_sales``). Gjallarhorn's liquidity and Price dips read it instead of Steam.
- **Single items** (``lookup``): pulse's name filter answers one item in ~0.5 s and ~5 KB, for a
  "check live" button on one row instead of a whole table.
- **Normalised request**: one body for every table (``BASE_BODY``): no price, count, profit or
  update-time filter, every item type, sold-out items shown (``displaySoldOutItems`` true; on
  2026-10-15 it changed no table's item count), sorted by profit percent, every row in one page.
- **Bonus-balance markets** are turned into real dollars once, on arrival (``scale``, SkinSwap
  (Trade) ÷ 1.4), keeping the page price as ``pagePrice``.
- **Listeners** (``add_listener``) hear about every downloaded table on one background thread, so
  a slave can keep what it needs (Store Catalogue Arbitrage keeps the store items).
- ``status()`` lists every table, its age, downloads, reads, its readers and whether it is active,
  and the keeper's state (active hours, last pass, the last sleep).

Facts about the pulse API and ideas not used yet are in docs/internals/pulse-api.md.
"""
import copy
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

logger = logging.getLogger(__name__)

TABLE_URL = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/{}/all'
FRESH = 10 * 60                 # background reads (boards, Case Arbitrage refresh, warmers)
ON_DEMAND = 60                  # a button the user pressed (Fetch live data, Refresh)
KEEP_SECONDS = FRESH            # a table older than this is dropped (nobody asks for older)
# Each download peaks at ~200 MB (raw bytes, text, parsed rows), so four at most: the Case
# Arbitrage alert poll's 7 markets arrive in two waves, about 3 seconds apart.
MAX_PARALLEL_DOWNLOADS = 4
MAX_BACKGROUND_DOWNLOADS = 3    # the last slot is kept for a page or a button
WAIT_FOR_DOWNLOAD = 600         # seconds a caller waits on another caller's download (queue included)
# Active profiles: every how many minutes their tables are downloaded again (Huginn Arbitrage).
ACTIVE_MINUTES = (5, 10, 15, 30, 60)
MAX_ACTIVE_TABLES = 12          # tables the active profiles may need together
# Read once a day: Buff163 is the main market, not tradeable yet, but its price belongs in the
# statistics and is the off-Steam reference (Case Arbitrage, Store Boosts, valuation).
REFERENCE_TABLES = {('Buff', 'Sell'): 24 * 60 * 60}
SLEEP_GAP = 3 * 30              # the keeper's tick took this long: the Mac (and Docker) slept
ONCE_TRIES = 3                  # a one-time download (the seed) is given up after this many failures
JOB_STATUS_SECONDS = 30         # how long the declared jobs' tables are cached for status()
KEEPER_TICK = 30                # seconds between the keeper's checks
KEEPER_RETRY_AFTER = 5 * 60     # a kept table that failed is tried again after this
MAX_PARALLEL_LOOKUPS = 2        # "Check live" has its own slots: never waits behind whole tables
# A price its market has not updated for a day or more is stale (pages grey it out; Store Catalogue
# Arbitrage never signals on it, Harvest never counts it). The one threshold (the frontend's
# utils/priceAge.js mirrors it).
STALE_SECONDS = 24 * 60 * 60
LOOKUP_ROWS = 500               # a name search returns every item whose name contains it (short names: many)

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


def body_for(price_type, name=None):
    """The request body for a table whose second market is priced as *price_type*; with *name*,
    only the items whose name contains it (pulse's name filter, one value per request)."""
    body = copy.deepcopy(BASE_BODY)
    body["secondMarketOptions"]["secondMarketPriceType"] = price_type
    if name is not None:
        body["marketHashNameFilter"] = {"value": name}
        body["paginationRequest"]["takeCount"] = LOOKUP_ROWS
    return body


# A market side of a row, compact:
# (price, count, rising, falling, overstock info, page price, the market's update time in epoch seconds).
PRICE, COUNT, RISING, FALLING, OVERSTOCK, PAGE_PRICE, UPDATED = range(7)


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
            market.get('overstockInfo'), page_price, market.get('offersUpdateTime'))


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
    if quote[UPDATED] is not None:
        market['offersUpdateTime'] = quote[UPDATED]
    return market


def is_stale(updated, now=None):
    """True when a market last updated a price (epoch seconds) STALE_SECONDS or more ago."""
    return bool(updated and (now if now is not None else time.time()) - updated >= STALE_SECONDS)


def _weekly_sales(row):
    """{market: sales in the last week} from a row's itemPopularity (pulse's salesCountPeriod Week)."""
    out = {}
    for entry in (row.get('itemPopularity') or []):
        market, count = (entry or {}).get('market'), (entry or {}).get('salesCount')
        if market and isinstance(count, (int, float)):
            out[market] = int(count)
    return out


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

    def __bool__(self):
        return True             # a downloaded table is there even when it has no rows (len 0)

    def prices(self, side='second'):
        """{name: price} of one side ('first' = Tradeon's lowest listing)."""
        position = 0 if side == 'first' else 1
        return {name: entry[position][PRICE] for name, entry in self.entries.items()
                if entry[position] and entry[position][PRICE]}

    def index(self, names=None, side='second'):
        """{name: {price, count, image, rising, falling, updated}} of one side, for *names* (default
        every item); 'updated' is the market's own update time of that price (epoch seconds)."""
        position = 0 if side == 'first' else 1
        wanted = self.entries if names is None else {n: self.entries[n] for n in names if n in self.entries}
        out = {}
        for name, entry in wanted.items():
            quote = entry[position]
            if quote and quote[PRICE]:
                out[name] = {'price': quote[PRICE], 'count': quote[COUNT],
                             'image': (self._items.get(name) or (None, None))[1],
                             'rising': quote[RISING], 'falling': quote[FALLING], 'updated': quote[UPDATED]}
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


class NotActive(RuntimeError):
    """A slave asked for a table that is not in memory: no active Huginn profile keeps it, and only
    Huginn Arbitrage downloads."""


class _Flight:
    def __init__(self, urgent=False):
        self.done = threading.Event()
        self.table = None
        self.error = None
        self.urgent = urgent         # a page or a button waits for it: it goes before the jobs


def _new_counts():
    # read_downloads: downloads a reader caused (not the keeper's); from_memory: reads answered with a
    # table already held (served_from_memory is their sum; a refused or failed read is neither).
    return {'downloads': 0, 'reads': 0, 'failures': 0, 'last_error': None,
            'last_read_at': None, 'last_download_at': None, 'read_downloads': 0, 'from_memory': 0, 'jobs': {}}


class PulseFeed:
    # Tests only (conftest.py): the older tests of the slaves' own logic let them download from a
    # fake pulse. Never set in the app; the gate's own tests switch it off again.
    slaves_may_download = False

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
        self._sales = {}             # name -> {market: (sales in the last week, download start)}, newest kept
        self._sales_at = None        # when the newest weekly sales arrived
        self._slots = threading.Condition()     # download slots (_download_slot)
        self._running = 0            # downloads running
        self._running_background = 0  # of them, background jobs' downloads
        self._urgent_waiting = 0     # pages and buttons waiting for a slot
        self._lookup_slots = threading.BoundedSemaphore(MAX_PARALLEL_LOOKUPS)
        self._listeners = []
        self._listener_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pulse-feed-listener')
        self._counts = {}            # key -> _new_counts()
        self._lookups = {'count': 0, 'failures': 0, 'last_error': None}
        self._active = {}            # (market id, price type) -> seconds between the keeper's downloads
        self._once = []              # [(market id, price type)] the keeper downloads once (statistics seed)
        self._active_hours = None    # (start, end) minutes of the local day the keeper downloads in
        self._keeper_failed = {}     # key -> when its last background download failed
        self._once_failures = {}     # key -> failed attempts of a one-time download (given up at ONCE_TRIES)
        self._keeper_started = False
        self._keeper_last_run = None
        self._keeper_last_pass = None  # {at, seconds, downloads} of the last pass that downloaded
        self._keeper_sleep = None    # {from, to}: the last time the Mac slept
        self._jobs = {}              # job id -> {label, schedule, tables(), active()}: declared
        self._local = threading.local()
        self._started = clock()

    # ---- the one request ---------------------------------------------------------------------

    def table(self, token, market_id, price_type, max_age=FRESH, count_read=True, job=None, keeper=False):
        """The (market id, price type) table. A slave gets the copy in memory whatever its age (or
        waits for a download under way), else NotActive. A downloader (the keeper, or a thread
        ``reading`` with *download*) gets it downloaded at most *max_age* seconds ago; with
        *accept_kept*, an active table up to its interval. The keeper passes *count_read* False."""
        table = self._read(token, market_id, price_type, max_age, count_read, job, keeper)
        reads = getattr(self._local, 'reads', None)
        if reads is not None:
            key = (market_id, price_type)
            reads[key] = min(reads.get(key, table.started_at), table.started_at)
        return table

    @contextmanager
    def reading(self, accept_kept=False, download=False):
        """This thread's reads as one request: yields {(market id, price type): download start} of
        every table read. *download*: it may download (Huginn Arbitrage's routes only); with
        *accept_kept*, an active table is accepted up to its interval (a profile opening by itself)."""
        previous = (getattr(self._local, 'reads', None), getattr(self._local, 'accept_kept', False),
                    getattr(self._local, 'download', False))
        self._local.reads, self._local.accept_kept, self._local.download = {}, accept_kept, download
        try:
            yield self._local.reads
        finally:
            self._local.reads, self._local.accept_kept, self._local.download = previous

    def _read(self, token, market_id, price_type, max_age, count_read, job, keeper):
        key = (market_id, price_type)
        now = self._clock()
        job = job or self.current_job()
        downloader = keeper or getattr(self._local, 'download', False) or self.slaves_may_download
        urgent = downloader and not keeper and not job   # Huginn Arbitrage's page goes before the keeper
        with self._lock:
            counts = self._counts.setdefault(key, _new_counts())
            if count_read:
                counts['reads'] += 1
                counts['last_read_at'] = now
            if job:
                counts['jobs'][job] = now
            self._drop_old_tables(now)
            cached = self._tables.get(key)
            if not downloader:
                # A slave: the copy in memory whatever its age, or a download under way; never a request.
                if cached:
                    if count_read:
                        counts['from_memory'] += 1
                    return cached
                flight = self._flights.get(key)
                if flight is None:
                    if key in self._active:
                        raise NotActive(f'{market_id} {price_type} is active but not downloaded yet '
                                        '(the keeper is on it, or outside the active hours)')
                    raise NotActive(f'{market_id} {price_type} is not kept by an active Huginn profile '
                                    '(only Huginn Arbitrage downloads prices)')
                leader = promoted = False
            else:
                every = self._active.get(key)
                if cached and every and not keeper and getattr(self._local, 'accept_kept', False):
                    # A profile opening by itself: the keeper's next download may be under way (it
                    # starts within KEEPER_TICK of the interval), so allow it two ticks.
                    max_age = max(max_age, every + 2 * KEEPER_TICK)
                if cached and now - cached.started_at < max_age:
                    if count_read:
                        counts['from_memory'] += 1
                    return cached
                flight = self._flights.get(key)
                leader = flight is None
                if leader:
                    flight = self._flights[key] = _Flight(urgent)
                promoted = urgent and not flight.urgent
                flight.urgent = flight.urgent or urgent
        if promoted:
            with self._slots:
                self._slots.notify_all()          # a job's queued download is now a page's
        if not leader:
            if not flight.done.wait(WAIT_FOR_DOWNLOAD):
                raise TimeoutError(f'pulse {market_id} {price_type}: still downloading after {WAIT_FOR_DOWNLOAD} s')
            if flight.error:
                raise flight.error
            if flight.table is None:
                raise RuntimeError(f'pulse {market_id} {price_type}: the download was interrupted')
            if count_read:
                with self._lock:
                    counts['from_memory'] += 1        # shared another caller's download
            return flight.table
        try:
            table = self._download(token, market_id, price_type, flight)
            flight.table = table
            if count_read:
                with self._lock:
                    counts['read_downloads'] += 1
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

    @contextmanager
    def _download_slot(self, flight):
        """One of the MAX_PARALLEL_DOWNLOADS slots. A background download waits while
        MAX_BACKGROUND_DOWNLOADS run or a page waits; a page's (*flight.urgent*, which may turn true
        while it waits) takes any free slot."""
        with self._slots:
            counted = False
            try:
                while True:
                    urgent = flight.urgent
                    if urgent and not counted:
                        self._urgent_waiting += 1
                        counted = True
                    if self._running < MAX_PARALLEL_DOWNLOADS and (
                            urgent or (self._running_background < MAX_BACKGROUND_DOWNLOADS
                                       and not self._urgent_waiting)):
                        break
                    self._slots.wait()
            finally:
                if counted:
                    self._urgent_waiting -= 1
            background = not urgent
            self._running += 1
            self._running_background += background
        try:
            yield
        finally:
            with self._slots:
                self._running -= 1
                self._running_background -= background
                self._slots.notify_all()

    def _download(self, token, market_id, price_type, flight=None):
        with self._download_slot(flight or _Flight(urgent=True)):
            started = self._clock()
            rows = self._post(TABLE_URL.format(market_id), token, body_for(price_type))
            seconds = self._clock() - started
        if not isinstance(rows, list):
            raise ValueError(f'pulse {market_id} {price_type}: the answer is not a table')
        # Parsed outside the lock: callers of other (cached) tables never wait for it.
        scale = self._scale.get(market_id)
        entries, new_items, sales, repeated = {}, {}, {}, 0
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
            weekly = _weekly_sales(row)
            if weekly:
                sales[name] = weekly
        if repeated:
            logger.warning('[PULSE] %s %s: %d rows repeat an item name; the first of each was kept',
                           market_id, price_type, repeated)
        with self._lock:
            for name, meta in new_items.items():
                self._items.setdefault(name, meta)
            self._merge_sales(sales, started)
            table = PulseTable(market_id, price_type, started, self._items, entries, round(seconds, 1))
            self._tables[(market_id, price_type)] = table
            counts = self._counts.setdefault((market_id, price_type), _new_counts())
            counts['downloads'] += 1
            counts['last_error'] = None
            counts['last_download_at'] = started
            self._drop_old_tables(self._clock())
        logger.info('[PULSE] %s %s: %d items in %.1f s', market_id, price_type, len(entries), seconds)
        self._announce(table)
        return table

    def _merge_sales(self, sales, at):
        """Keep the newest weekly count per item and market (caller holds the lock)."""
        if not sales:
            return
        for name, weekly in sales.items():
            known = self._sales.setdefault(name, {})
            for market, count in weekly.items():
                if market not in known or known[market][1] <= at:     # a slower, older download never wins
                    known[market] = (count, at)
        self._sales_at = max(at, self._sales_at or at)

    def _drop_old_tables(self, now):
        """A table nobody keeps is dropped KEEP_SECONDS after its download; an active one stays until
        its next download replaces it (caller holds the lock)."""
        for key in [key for key, table in self._tables.items()
                    if key not in self._active and now - table.started_at > KEEP_SECONDS]:
            del self._tables[key]

    # ---- background jobs (slaves): who reads what --------------------------------------------------

    @contextmanager
    def job(self, job_id):
        """Mark this thread's reads as a background job's (*job_id*, declared with declare_job), so
        the Pulse feed page can say which slave reads which table."""
        previous = getattr(self._local, 'job', None)
        self._local.job = job_id
        try:
            yield
        finally:
            self._local.job = previous

    def current_job(self):
        return getattr(self._local, 'job', None)

    def declare_job(self, job_id, label, schedule, tables, active=None, period=3600):
        """A recurring job and the tables it reads (*tables()* → [(market id, price type)];
        *active()* → whether it runs now; *period*: seconds between runs, or a callable), for the
        Pulse feed page."""
        self._jobs[job_id] = {'label': label, 'schedule': schedule, 'tables': tables, 'active': active,
                              'period': period}
        self._job_status_cache = None

    def _job_status(self):
        """Each declared job with its tables (computed outside the lock: tables() may read settings),
        cached JOB_STATUS_SECONDS: the Pulse feed page polls every 5 seconds."""
        cached = getattr(self, '_job_status_cache', None)
        if cached and self._clock() - cached[0] < JOB_STATUS_SECONDS:
            return cached[1]
        out = []
        for job_id, job in list(self._jobs.items()):
            try:
                tables = [list(key) for key in dict.fromkeys(tuple(table) for table in (job['tables']() or []))]
            except Exception as error:
                logger.warning('[PULSE] job %s tables: %s', job_id, error)
                tables = []
            try:
                active = bool(job['active']()) if job['active'] else True
            except Exception:
                active = False
            try:
                schedule = job['schedule']() if callable(job['schedule']) else job['schedule']
            except Exception:
                schedule = ''
            try:
                period = int(job['period']() if callable(job['period']) else job['period'])
            except Exception:
                period = 3600
            out.append({'id': job_id, 'label': job['label'], 'schedule': schedule, 'period_seconds': period,
                        'active': active, 'tables': tables})
        self._job_status_cache = (self._clock(), out)
        return out

    # ---- one item --------------------------------------------------------------------------------

    def lookup(self, token, market_id, price_type, name):
        """One item, fresh from pulse (its name filter, ~0.5 s): {'first', 'second'} pulse-shaped
        market dicts (Tradeon's lowest listing and *market_id* priced as *price_type*), 'image',
        'checked_at'; None when pulse has no row with exactly that name. Not cached."""
        started = self._clock()
        try:
            with self._lookup_slots:
                rows = self._post(TABLE_URL.format(market_id), token, body_for(price_type, name))
            if not isinstance(rows, list):
                raise ValueError(f'pulse {market_id} {price_type} "{name}": the answer is not a table')
        except Exception as error:
            with self._lock:
                self._lookups['failures'] += 1
                self._lookups['last_error'] = str(error)[:200]
            raise
        scale = self._scale.get(market_id)
        found = next((row for row in rows if ((row or {}).get('itemName') or {}).get('marketHashName') == name), None)
        with self._lock:
            self._lookups['count'] += 1
            if found:
                self._merge_sales({name: _weekly_sales(found)}, started)
        if not found:
            return None
        return {'first': _market_dict(_quote(found.get('firstMarket'))),
                'second': _market_dict(_quote(found.get('secondMarket'), scale)),
                'image': found.get('imageUrl'), 'checked_at': started}

    def peek(self, market_id, price_type, max_age=None):
        """The table if it is in memory (and younger than *max_age*, when given), else None. Never
        downloads and does not count as a read."""
        now = self._clock()
        with self._lock:
            cached = self._tables.get((market_id, price_type))
            return cached if cached and (max_age is None or now - cached.started_at < max_age) else None

    def now(self):
        """The feed's clock (epoch seconds): ages of its tables are measured with it."""
        return self._clock()

    def ages(self, keys):
        """{(market id, price type): (seconds since its download started or None, downloading now)}.
        Never downloads."""
        now = self._clock()
        with self._lock:
            return {key: (round(now - self._tables[key].started_at, 1) if key in self._tables else None,
                          key in self._flights) for key in keys}

    # ---- weekly sales ----------------------------------------------------------------------------

    def weekly_sales(self, names=None):
        """{name: {market: sales in the last week}} from every table downloaded since boot (pulse's
        itemPopularity: Steam, Market.CSGO ('Tm'), Tradeon, plus each table's own market)."""
        with self._lock:
            wanted = self._sales if names is None else {name: self._sales[name] for name in names if name in self._sales}
            return {name: {market: count for market, (count, _) in sales.items()} for name, sales in wanted.items()}

    # ---- active tables and the keeper ----------------------------------------------------------

    def set_active(self, keys, minutes):
        """Keep the active profiles' tables (*keys*) fresh, each downloaded again every *minutes*
        (one of ACTIVE_MINUTES), plus REFERENCE_TABLES at their own interval (the shorter wins)."""
        clean = list(dict.fromkeys((str(market_id), str(price_type)) for market_id, price_type in keys))
        if len(clean) > MAX_ACTIVE_TABLES:
            raise ValueError(f'at most {MAX_ACTIVE_TABLES} tables can be active ({len(clean)} needed)')
        active = dict(REFERENCE_TABLES)
        for key in clean:
            active[key] = min(active.get(key, int(minutes) * 60), int(minutes) * 60)
        with self._lock:
            self._active = active
            self._keeper_failed = {key: at for key, at in self._keeper_failed.items()
                                   if key in active or key in self._once}

    def active(self):
        """{(market id, price type): seconds between its downloads}."""
        with self._lock:
            return dict(self._active)

    def download_once(self, keys, give_up=None):
        """Tables the keeper downloads once, after the active ones (the statistics seed); a listener
        keeps what it needs. *give_up(key)* hears about one given up after ONCE_TRIES failures."""
        with self._lock:
            for key in keys:
                if tuple(key) not in self._once:
                    self._once.append(tuple(key))
            if give_up:
                self._once_give_up = give_up

    def will_download(self, key):
        """Whether the keeper is about to bring this table: active, inside the active hours, and
        not failing."""
        inside, _ = self._within_active_hours(self._clock())
        with self._lock:
            return inside and key in self._active and key not in self._keeper_failed

    def set_active_hours(self, hours):
        """(start, end) minutes of the local day the keeper downloads in (end before start: over
        midnight; equal: all day), or None: always."""
        with self._lock:
            self._active_hours = tuple(hours) if hours else None

    def _within_active_hours(self, now):
        """Whether *now* (epoch seconds) is inside the active hours, and if not, when they start."""
        with self._lock:
            hours = self._active_hours
        if not hours or hours[0] == hours[1]:
            return True, None
        start, end = hours
        local = time.localtime(now)
        minute = local.tm_hour * 60 + local.tm_min
        inside = start <= minute < end if start < end else (minute >= start or minute < end)
        if inside:
            return True, None
        wait = (start - minute) % (24 * 60)
        return False, now - local.tm_sec + wait * 60

    def keep_fresh_once(self, token):
        """One keeper pass inside the active hours: every active table older than its interval, then
        the one-time downloads, one at a time."""
        inside, _ = self._within_active_hours(self._clock())
        if not inside:
            with self._lock:
                self._keeper_last_run = self._clock()
            return 0
        with self._lock:
            due = [(key, seconds) for key, seconds in self._active.items()] + [(key, None) for key in self._once]
            failed = dict(self._keeper_failed)
        started, done = self._clock(), 0
        for key, seconds in due:
            now = self._clock()
            with self._lock:
                table = self._tables.get(key)
                downloading = key in self._flights
            if downloading or (seconds and table and now - table.started_at < seconds - KEEPER_TICK):
                continue
            if key in failed and now - failed[key] < KEEPER_RETRY_AFTER:
                continue
            try:
                self.table(token, key[0], key[1], max_age=seconds - KEEPER_TICK if seconds else 0,
                           count_read=False, keeper=True)
                done += 1
                with self._lock:
                    self._keeper_failed.pop(key, None)
                    if seconds is None and key in self._once:
                        self._once.remove(key)
            except Exception as error:
                logger.warning('[PULSE] keeper: %s %s failed: %s', key[0], key[1], error)
                with self._lock:
                    self._keeper_failed[key] = self._clock()
                    if seconds is None:
                        tries = self._once_failures[key] = self._once_failures.get(key, 0) + 1
                        if tries >= ONCE_TRIES and key in self._once:
                            self._once.remove(key)      # e.g. needs pulse's Table subscription
                            logger.warning('[PULSE] gave up the one-time download of %s %s', *key)
                            give_up = getattr(self, '_once_give_up', None)
                            if give_up:
                                try:
                                    give_up(key)
                                except Exception as callback_error:
                                    logger.warning('[PULSE] give-up callback: %s', callback_error)
        with self._lock:
            self._keeper_last_run = self._clock()
            if done:
                self._keeper_last_pass = {'at': started, 'seconds': round(self._clock() - started, 1), 'downloads': done}
        return done

    def note_sleep(self, slept_from, slept_to):
        """The Mac slept (the keeper's tick took far longer than a tick): forget the failures of the
        sleep (the network was gone), so every table is tried again at once."""
        with self._lock:
            self._keeper_sleep = {'from': slept_from, 'to': slept_to}
            self._keeper_failed = {}
        logger.info('[PULSE] the Mac slept for %d minutes; the keeper starts again after one tick',
                    round((slept_to - slept_from) / 60))

    def start_keeper(self, token_provider):
        """The background thread that keeps the active tables fresh (once; *token_provider()* gives
        the current tradeon_token, nothing is downloaded while it is empty). After the Mac slept it
        waits one more tick for the network before downloading."""
        with self._lock:
            if self._keeper_started:
                return
            self._keeper_started = True

        def loop():
            last = time.time()                  # the end of the previous tick (its pass included)
            while True:
                time.sleep(KEEPER_TICK)
                now = time.time()
                if now - last > SLEEP_GAP:      # only the sleep itself counts, never a long pass
                    self.note_sleep(last, now)
                    last = time.time()
                    continue
                try:
                    token = token_provider()
                    if token and (self._active or self._once):
                        self.keep_fresh_once(token)
                except Exception as error:      # never let the thread die
                    logger.warning('[PULSE] keeper: %s', error)
                last = time.time()
        threading.Thread(target=loop, name='pulse-keeper', daemon=True).start()

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

    def keeper_status(self):
        """The keeper's state alone (cheap: the Active profiles panel polls it)."""
        now = self._clock()
        inside, resumes_at = self._within_active_hours(now)
        with self._lock:
            return self._keeper_view(inside, resumes_at)

    def _keeper_view(self, inside, resumes_at):
        """The keeper's state (caller holds the lock)."""
        shortest = min((seconds for key, seconds in self._active.items() if key not in REFERENCE_TABLES), default=None)
        last_pass = dict(self._keeper_last_pass) if self._keeper_last_pass else None
        return {'running': self._keeper_started, 'last_run': self._keeper_last_run,
                'last_pass': last_pass, 'sleep': dict(self._keeper_sleep) if self._keeper_sleep else None,
                'active_hours': list(self._active_hours) if self._active_hours else None,
                'paused': not inside, 'resumes_at': resumes_at,
                # A pass that takes longer than the shortest interval cannot keep up.
                'behind': bool(last_pass and shortest and last_pass['seconds'] > shortest),
                'once_pending': [list(key) for key in self._once]}

    def status(self):
        jobs = self._job_status()
        readers = {}
        for job in jobs:
            for market_id, price_type in job['tables']:
                readers.setdefault((market_id, price_type), []).append(job['id'])
        now = self._clock()
        inside, resumes_at = self._within_active_hours(now)
        with self._lock:
            keys = set(self._counts) | set(self._active) | set(readers)
            tables = []
            for key in sorted(keys):
                counts = self._counts.get(key) or _new_counts()
                table = self._tables.get(key)
                tables.append({'market': key[0], 'price_type': key[1], **counts,
                               'jobs': dict(counts['jobs']), 'declared_jobs': readers.get(key, []),
                               'items': len(table) if table else None,
                               'age_seconds': round(now - table.started_at) if table else None,
                               'download_seconds': table.seconds if table else None,
                               'downloading': key in self._flights,
                               'active': key in self._active,
                               'keep_seconds': self._active.get(key),
                               'reference': key in REFERENCE_TABLES,
                               'once': key in self._once,
                               'keeper_failed_at': self._keeper_failed.get(key)})
            downloads = sum(counts['downloads'] for counts in self._counts.values())
            reads = sum(counts['reads'] for counts in self._counts.values())
            from_memory = sum(counts['from_memory'] for counts in self._counts.values())
            keeper = self._keeper_view(inside, resumes_at)
            return {'since': self._started, 'now': now, 'downloads': downloads, 'reads': reads,
                    'served_from_memory': from_memory,
                    'tables_in_memory': sum(1 for table in tables if table['items'] is not None),
                    'active_tables': len(self._active), 'keeper': keeper,
                    'lookups': dict(self._lookups), 'jobs': jobs,
                    'weekly_sales_items': len(self._sales), 'weekly_sales_at': self._sales_at,
                    'tables': tables}
