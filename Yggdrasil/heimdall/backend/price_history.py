"""Heimdall's own price statistics, built only from the pulse tables Huginn Arbitrage downloads (the
active profiles' tables, Buff163's daily reference, "Fetch live data"), never from extra requests.

- **Recorded**: every downloaded lowest-listing or buy-orders table (a listener on the master feed)
  adds one sample per item to that day's row (never a sold-out item's or a price its market has not
  refreshed for a day: those are frozen): average, lowest, highest, last price and the number
  of samples. Each day is a file, cache/price_history/<market>/<price type>/<YYYY-MM-DD>.json.gz
  (~300 KB), kept for good: the history Heimdall will need later (trends, backtests, charts).
  Today's file is rewritten every FLUSH_SECONDS and read back after a restart.
- **Statistics** (``statistic``): an item's 30-day median (or average) of its daily averages, once
  it has MIN_DAYS full days; before that, pulse's own 30-day value from a one-time seed.
  ``trade_statistic``: the middle of the lowest listing's and the buy orders' (where a sale lands,
  like pulse's median of sales), for Store Boosts and Gjallarhorn.
- **Seed** (``seeds_wanted`` → the keeper's ``download_once``): pulse's Median30d and Average30d
  tables, downloaded once per active market that has them (Steam, Buff163, CSMoney (Trade)...),
  cache/price_history/seed/<market>_<basis>.json.gz.

Price dips, Gjallarhorn's liquidity and Store Boosts read their 30-day prices here.
"""
import gzip
import json
import logging
import os
import statistics
import threading
import time

from jsonio import atomic_write_bytes
from pulse_feed import is_stale

logger = logging.getLogger(__name__)

HISTORY_DIRECTORY = os.path.join(os.path.dirname(__file__), 'cache', 'price_history')
RECORDED_TYPES = ('Sell', 'SellWithoutHold', 'Buy')
BASES = ('Median30d', 'Average30d')
# Markets with 30-day prices on pulse (market-info isSupportHistory, 2026-10-15): the seed comes
# from these. CSFloat answers a Median30d table but pulse does not list it, so it is left out.
STATISTICS_MARKETS = ('Steam', 'Buff', 'CsMoneyTrade', 'CsMoneyMarket', 'Dmarket', 'Tm', 'Waxpeer',
                      'Skinport', 'WhiteMarket', 'CsDeals', 'ShadowPay', 'CsTradeTrade', 'CsTradeMarket')
WINDOW_DAYS = 30
MIN_DAYS = 7                    # an item's own history replaces the seed from this many full days
FLUSH_SECONDS = 60              # often: a docker restart under the reloader kills the worker unsaved
SEED_MAX_AGE_DAYS = 30          # pulse's 30-day seed is downloaded again once a month while active
SEED_TRUSTED_DAYS = 45          # an older seed is no longer used (its market stopped being active)
SEED_UNAVAILABLE_DAYS = 30      # a seed pulse refused (its Table subscription) is not asked again for this long
MEAN, LOWEST, HIGHEST, LAST, SAMPLES = range(5)


def _day(epoch):
    return time.strftime('%Y-%m-%d', time.localtime(epoch))


def _read_gzip_json(path):
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        logger.warning('[HISTORY] unreadable %s: %s', path, error)
        return None


def _write_gzip_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_bytes(path, gzip.compress(json.dumps(data, separators=(',', ':')).encode('utf-8'), 6))


class PriceHistory:
    def __init__(self, directory=None, clock=time.time):
        self._directory = directory or HISTORY_DIRECTORY     # read now: tests point it at a temporary folder
        self._clock = clock
        self._lock = threading.Lock()       # the in-memory rows and caches (held briefly)
        self._writing = threading.Lock()    # recording and saving the day files (one writer at a time)
        self._today = {}        # (market, price type) -> {'day', 'items': {name: [sum, count, lowest, highest, last]}, 'flushed_at'}
        self._statistics = {}   # (market, price type, basis) -> (day computed, {name: value}, {name: own value only})
        self._seed_generation = 0   # changes with every new seed: statistics computed before it are dropped
        self._reading = {}      # (market, price type) -> lock: one read of the window at a time
        self._seeds = {}        # (market, basis) -> {name: price} (loaded on first use)

    # ---- paths -------------------------------------------------------------------------------

    def _day_path(self, market_id, price_type, day):
        return os.path.join(self._directory, market_id, price_type, f'{day}.json.gz')

    def _seed_path(self, market_id, basis):
        return os.path.join(self._directory, 'seed', f'{market_id}_{basis}.json.gz')

    # ---- recording (the master feed's listener) ----------------------------------------------

    def record(self, table):
        """Fold a downloaded table into today's row (a listing or buy-orders table), or save it as
        the seed (a 30-day table)."""
        if table.price_type in BASES:
            self._save_seed(table)
            return
        if table.price_type not in RECORDED_TYPES:
            return
        key = (table.market_id, table.price_type)
        day = _day(table.started_at)
        prices = {name: price for name, (price, count, updated) in table.quotes().items()
                  if count != 0 and not is_stale(updated, table.started_at)}
        with self._writing:             # one writer at a time; readers only wait for the short folds
            with self._lock:
                today = self._today.get(key)
            if today is None:
                loaded = self._load_today(key, day)
                with self._lock:
                    today = self._today.setdefault(key, loaded)
            if day < today['day']:
                # A table from an earlier day finishing late (a download that started before midnight):
                # added to that day's saved row, never replacing it.
                late = self._load_today(key, day)
                self._fold(late['items'], prices)
                self._write(key, self._rows(late), late)
                return
            if today['day'] != day:
                self._flush(key)
                loaded = self._load_today(key, day)
                with self._lock:
                    today = self._today[key] = loaded
            with self._lock:
                self._fold(today['items'], prices)
                due = self._clock() - today['flushed_at'] >= FLUSH_SECONDS
            if due:
                self._flush(key)

    @staticmethod
    def _fold(items, prices):
        """Add one sample per item to a day's row ({name: [sum, count, lowest, highest, last]})."""
        for name, price in prices.items():
            row = items.get(name)
            if row is None:
                items[name] = [price, 1, price, price, price]
            else:
                row[0] += price
                row[1] += 1
                row[2] = min(row[2], price)
                row[3] = max(row[3], price)
                row[4] = price

    def _load_today(self, key, day):
        """Today's row as saved before a restart (sums rebuilt from the averages)."""
        saved = _read_gzip_json(self._day_path(*key, day)) or {}
        items = {name: [row[MEAN] * row[SAMPLES], row[SAMPLES], row[LOWEST], row[HIGHEST], row[LAST]]
                 for name, row in saved.items() if isinstance(row, list) and len(row) == 5}
        return {'day': day, 'items': items, 'flushed_at': self._clock() if items else 0}   # a new day: saved at once

    @staticmethod
    def _rows(day_row):
        return {name: [round(total / count, 2), lowest, highest, last, count]
                for name, (total, count, lowest, highest, last) in day_row['items'].items()}

    def _flush(self, key):
        """Save one table's day row: copied under the lock, gzipped and written outside it (the
        caller holds _writing)."""
        with self._lock:
            today = self._today.get(key)
            rows = self._rows(today) if today else None
        if rows:
            self._write(key, rows, today)

    def _write(self, key, rows, day_row):
        if not rows:
            return
        try:
            _write_gzip_json(self._day_path(*key, day_row['day']), rows)
            day_row['flushed_at'] = self._clock()
        except OSError as error:
            logger.warning('[HISTORY] could not save %s %s %s: %s', *key, day_row['day'], error)

    def flush(self):
        """Save today's rows now (tests, shutdown)."""
        with self._writing:
            with self._lock:
                keys = list(self._today)
            for key in keys:
                self._flush(key)

    def _save_seed(self, table):
        path = self._seed_path(table.market_id, table.price_type)
        if self._seed_age_days(path) is not None and self._seed_age_days(path) < SEED_MAX_AGE_DAYS:
            return
        prices = table.prices()
        if not prices:
            return
        _write_gzip_json(path, {'downloaded_at': table.started_at, 'prices': prices})
        with self._lock:
            self._seeds[(table.market_id, table.price_type)] = prices
            self._statistics = {}       # the seed changes them
            self._seed_generation += 1
        logger.info('[HISTORY] seeded %s %s: %d items', table.market_id, table.price_type, len(prices))

    def _seed_age_days(self, path):
        try:
            return (self._clock() - os.path.getmtime(path)) / 86400
        except OSError:
            return None

    def seeds_wanted(self, market_ids):
        """[(market id, basis)] to download once: the 30-day tables of *market_ids* that pulse has,
        not saved yet or older than SEED_MAX_AGE_DAYS, and not refused lately (seed_unavailable)."""
        wanted = []
        for market_id in dict.fromkeys(market_ids):
            if market_id not in STATISTICS_MARKETS:
                continue
            for basis in BASES:
                age = self._seed_age_days(self._seed_path(market_id, basis))
                refused = self._seed_age_days(self._seed_path(market_id, basis) + '.unavailable')
                if (age is None or age >= SEED_MAX_AGE_DAYS) and (refused is None or refused >= SEED_UNAVAILABLE_DAYS):
                    wanted.append((market_id, basis))
        return wanted

    def seed_unavailable(self, key):
        """The keeper gave a seed up (pulse refused it, e.g. its Table subscription): not asked again
        for SEED_UNAVAILABLE_DAYS, restarts included."""
        market_id, basis = key
        path = self._seed_path(market_id, basis) + '.unavailable'
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('pulse refused this table; asked again after a month\n')

    # ---- statistics --------------------------------------------------------------------------

    def _seed(self, market_id, basis):
        """Pulse's 30-day prices, while the seed is younger than SEED_TRUSTED_DAYS (an older one is
        a month that no longer is: its market stopped being active)."""
        key = (market_id, basis)
        age = self._seed_age_days(self._seed_path(market_id, basis))
        if age is None or age >= SEED_TRUSTED_DAYS:
            return {}
        if key not in self._seeds:
            saved = _read_gzip_json(self._seed_path(market_id, basis)) or {}
            self._seeds[key] = saved.get('prices') or {}
        return self._seeds[key]

    def _full_days(self, market_id, price_type, today):
        """The saved days before *today* inside the window, oldest first."""
        folder = os.path.join(self._directory, market_id, price_type)
        try:
            names = sorted(name[:10] for name in os.listdir(folder) if name.endswith('.json.gz'))
        except FileNotFoundError:
            return []
        first = _day(self._clock() - WINDOW_DAYS * 86400)
        return [day for day in names if first <= day < today]

    def statistic(self, market_id, price_type, basis='Median30d', names=None, own_only=False):
        """{name: 30-day median (or average) price} of a market's listing or buy-orders table, from
        the item's own daily averages once it has MIN_DAYS full days, else from the seed (*own_only*:
        the items with MIN_DAYS of their own only). Computed once a day and kept."""
        if basis not in BASES:
            raise ValueError(f'{basis}: use one of {", ".join(BASES)}')
        today = _day(self._clock())
        with self._lock:
            cached = self._statistics.get((market_id, price_type, basis))
        if not (cached and cached[0] == today):
            cached = self._compute(market_id, price_type, basis, today)
        values = cached[2] if own_only else cached[1]
        return values if names is None else {name: values[name] for name in names if name in values}

    def _compute(self, market_id, price_type, basis, today):
        """Both bases of one table from the window before *today*, read once a day (one reader at a
        time, without the main lock: the listener keeps recording). Only the results are kept, never
        the window's daily averages (~30 numbers per item)."""
        with self._lock:
            reading = self._reading.setdefault((market_id, price_type), threading.Lock())
        with reading:
            with self._lock:
                cached = self._statistics.get((market_id, price_type, basis))
                generation = self._seed_generation
            if cached and cached[0] == today:
                return cached               # another reader computed it meanwhile
            daily = {}
            for day in self._full_days(market_id, price_type, today):
                for name, row in (_read_gzip_json(self._day_path(market_id, price_type, day)) or {}).items():
                    daily.setdefault(name, []).append(row[MEAN])
            own = {each: {} for each in BASES}
            for each, values in own.items():
                summarise = statistics.median if each == 'Median30d' else statistics.fmean
                for name, means in daily.items():
                    if len(means) >= MIN_DAYS:
                        values[name] = round(summarise(means), 2)
            with self._lock:
                computed = {each: (today, {**self._seed(market_id, each), **own[each]}, own[each]) for each in BASES}
                if generation == self._seed_generation:     # no new seed while reading
                    for each, entry in computed.items():
                        self._statistics[(market_id, price_type, each)] = entry
            return computed[basis]

    def trade_statistic(self, market_id, listing_type, buy_type, basis='Median30d', names=None):
        """{name: 30-day price where a sale lands}: the middle of the lowest listing's and the buy
        orders' own statistic (either alone when only one side has MIN_DAYS of its own: a side that is
        never recorded must not mix pulse's seed into it). Without either, pulse's seed (its 30-day
        median of sales, the same for both sides)."""
        listing = self.statistic(market_id, listing_type, basis, names, own_only=True)
        orders = self.statistic(market_id, buy_type, basis, names, own_only=True)
        seed = self.statistic(market_id, listing_type, basis, names)
        result = {name: price for name, price in seed.items()}
        for name in set(listing) | set(orders):
            result[name] = (round((listing[name] + orders[name]) / 2, 2) if name in listing and name in orders
                            else listing.get(name, orders.get(name)))
        return result

    def coverage(self, market_id, price_type):
        """{days: full days saved in the window, seeded: {basis: bool}} of one table."""
        today = _day(self._clock())
        return {'days': len(self._full_days(market_id, price_type, today)),
                'seeded': {basis: os.path.exists(self._seed_path(market_id, basis)) for basis in BASES}}
