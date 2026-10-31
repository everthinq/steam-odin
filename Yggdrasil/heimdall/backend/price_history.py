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
        self._lock = threading.Lock()
        self._today = {}        # (market, price type) -> {'day', 'items': {name: [sum, count, lowest, highest, last]}, 'flushed_at'}
        self._daily = {}        # (market, price type) -> (day read, {name: [daily averages]}): the window, once a day
        self._statistics = {}   # (market, price type, basis) -> (day computed, {name: value})
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
        prices = {name: quote['price'] for name, quote in table.index().items()
                  if quote.get('count') != 0 and not is_stale(quote.get('updated'), table.started_at)}
        with self._lock:
            today = self._today.get(key)
            if today is None:
                today = self._today[key] = self._load_today(key, day)
            if day < today['day']:
                # A table from an earlier day finishing late (a download that started before midnight):
                # added to that day's saved row, never replacing it.
                late = self._load_today(key, day)
                self._fold(late['items'], prices)
                self._write(key, late)
                return
            if today['day'] != day:
                self._flush_locked(key)
                today = self._today[key] = self._load_today(key, day)
            self._fold(today['items'], prices)
            if self._clock() - today['flushed_at'] >= FLUSH_SECONDS:
                self._flush_locked(key)

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

    def _flush_locked(self, key):
        today = self._today.get(key)
        if today:
            self._write(key, today)

    def _write(self, key, day_row):
        if not day_row['items']:
            return
        rows = {name: [round(total / count, 2), lowest, highest, last, count]
                for name, (total, count, lowest, highest, last) in day_row['items'].items()}
        try:
            _write_gzip_json(self._day_path(*key, day_row['day']), rows)
            day_row['flushed_at'] = self._clock()
        except OSError as error:
            logger.warning('[HISTORY] could not save %s %s %s: %s', *key, day_row['day'], error)

    def flush(self):
        """Save today's rows now (tests, shutdown)."""
        with self._lock:
            for key in list(self._today):
                self._flush_locked(key)

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
            self._statistics = {}       # the seed changes them (the daily averages stay)
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

    def statistic(self, market_id, price_type, basis='Median30d', names=None):
        """{name: 30-day median (or average) price} of a market's listing or buy-orders table, from
        the item's own daily averages once it has MIN_DAYS full days, else from the seed. Computed
        once a day and kept."""
        if basis not in BASES:
            raise ValueError(f'{basis}: use one of {", ".join(BASES)}')
        today = _day(self._clock())
        key = (market_id, price_type, basis)
        with self._lock:
            cached = self._statistics.get(key)
        if not cached or cached[0] != today:
            daily = self._daily_averages(market_id, price_type, today)
            summarise = statistics.median if basis == 'Median30d' else statistics.fmean
            with self._lock:
                values = dict(self._seed(market_id, basis))
            for name, means in daily.items():
                if len(means) >= MIN_DAYS:
                    values[name] = round(summarise(means), 2)
            cached = (today, values)
            with self._lock:
                self._statistics[key] = cached
        values = cached[1]
        return values if names is None else {name: values[name] for name in names if name in values}

    def _daily_averages(self, market_id, price_type, today):
        """{name: [daily averages]} of the window before *today*, read once a day per table (both
        bases share it), one reader at a time, without the main lock: the listener keeps recording."""
        key = (market_id, price_type)
        with self._lock:
            reading = self._reading.setdefault(key, threading.Lock())
        with reading:
            with self._lock:
                cached = self._daily.get(key)
            if cached and cached[0] == today:
                return cached[1]
            daily = {}
            for day in self._full_days(market_id, price_type, today):
                for name, row in (_read_gzip_json(self._day_path(market_id, price_type, day)) or {}).items():
                    daily.setdefault(name, []).append(row[MEAN])
            with self._lock:
                self._daily[key] = (today, daily)
            return daily

    def trade_statistic(self, market_id, listing_type, buy_type, basis='Median30d', names=None):
        """{name: 30-day price where a sale lands}: the middle of the lowest listing's and the buy
        orders' statistic (either alone when the other is missing). Before MIN_DAYS both are pulse's
        seed (its 30-day median of sales), so the middle is that seed."""
        listing = self.statistic(market_id, listing_type, basis, names)
        orders = self.statistic(market_id, buy_type, basis, names)
        return {name: round((listing[name] + orders[name]) / 2, 2) if name in listing and name in orders
                else listing.get(name, orders.get(name))
                for name in set(listing) | set(orders)}

    def coverage(self, market_id, price_type):
        """{days: full days saved in the window, seeded: {basis: bool}} of one table."""
        today = _day(self._clock())
        return {'days': len(self._full_days(market_id, price_type, today)),
                'seeded': {basis: os.path.exists(self._seed_path(market_id, basis)) for basis in BASES}}
