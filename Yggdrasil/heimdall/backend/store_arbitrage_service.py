"""Store Catalogue Arbitrage: which in-game store items resell for more than the store charges.

Read-only, a recommendation board: nothing here buys or sells. Each row is one item the
Counter-Strike 2 in-game store sells (the Store Catalogue's price sheet), priced against
what the chosen markets pay for it, net of each market's sell fee (``HuginnService.market_fee``,
the one Fees editor):

- **instant**: the market's buy orders / autobuy, what you get the moment you sell;
- **listing**: the market's lowest listing, what you get by listing at that price and waiting.

The store price is the US dollar one; the row also names the cheapest wallet currency among
your accounts (converted with Andvari's exchange rates). Store purchases cannot be traded for
7 days, so a profit has to survive a week: the daily history (``cache/store_arbitrage_history.json.gz``,
the best price seen per market each day, kept 180 days) shows whether a gap is a spike or
stays open, and your own Draupnir record shows what you sold the item for before.

Left out on purpose: case keys (keys bought from the store cannot be traded or sold, Valve's
2019 rule), passes and licenses, and every item whose own definition says "cannot trade"
(Storage Unit, Charm Detachment Pack). A gap that is wide (a market listing at 1.5 times the
store price or more) and stays open is marked **unconfirmed**: if store copies could be resold,
traders would have closed it, so it usually means they cannot (the Name Tag and the StatTrak
Swap Tool look like this). One purchase settles it: the web inventory shows "Tradable After"
for a 7-day hold, "Not Tradable" for good.

Prices come from Huginn's pulse pulls (one market and side at a time, in ONE worker thread
fed by a queue, cached 10 minutes; CSFloat's buy orders from its swept cache). An hourly watcher
warms the default markets so the history keeps growing while the page is closed.
"""
import datetime
import gzip
import json
import logging
import os
import threading
import time

from harvest_service import HarvestService
from storage_shop_service import GAME_STORE_CURRENCIES
from store_catalogue_service import CANNOT_TRADE, CATEGORY_KEYS, CATEGORY_PASSES
from store_purchase_service import to_usd

log = logging.getLogger(__name__)

HISTORY_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'store_arbitrage_history.json.gz')
HISTORY_DAYS = 180
HISTORY_STATISTICS_DAYS = 30
HISTORY_SAVE_MIN_INTERVAL = 60      # seconds between history writes

SIDE_INSTANT, SIDE_LISTING = 'instant', 'listing'
# The markets you sold store items on before, plus the two references (Buff163, Steam).
DEFAULT_MARKETS = ['Buff', 'Steam', 'CsFloat', 'LisSkins', 'AvanMarket', 'Tm']
REFERENCE_MARKET = 'Buff'           # its lowest listing is the sanity reference for every cash price
INDEX_TTL = 10 * 60
RETRY_AFTER_FAILURE = 120
WATCH_INTERVAL = 60 * 60
WATCH_FIRST_WAIT = 5 * 60

TRADE_HOLD_DAYS = 7
# A cash price far above Buff163's lowest listing is not a price you can sell at: a buy order
# for one pattern or float, not for any copy (Harvest's rule), or a listing on a thin market
# where one seller asks what nobody pays (live 2026-10-04: a $0.99 sticker listed at $8.37 on
# LisSkins while it sold for under $1 everywhere else). Balance markets (Steam) price in
# their own scale and are not checked.
SUSPICIOUS_RATIO = 1.3
# A market listing this many times the store price, for most of the days tracked, means the
# store's copies probably cannot be resold (see the module docstring).
UNCONFIRMED_RATIO = 1.5
UNCONFIRMED_SHARE_OF_DAYS = 0.8
UNCONFIRMED_MIN_DAYS = 3

EXCLUDED_REASONS = {
    'keys': 'case keys bought from the store cannot be traded or sold (since 2019)',
    'passes': 'a pass or license, not an item you can trade',
    'cannot_trade': 'the item itself cannot be traded',
    'no_price': 'none of the chosen markets has a price for it',
}
EXCLUDED_CATEGORIES = {CATEGORY_KEYS: 'keys', CATEGORY_PASSES: 'passes'}


# ---- pure helpers (unit-tested) ------------------------------------------------------------

def split_items(items):
    """(candidates, excluded) from the catalogue's rows: candidates can be bought and resold."""
    candidates, excluded = [], []
    for item in items or []:
        reason = EXCLUDED_CATEGORIES.get(item.get('category'))
        # Rows saved before the catalogue knew the flag: the known untradable entries.
        if not reason and item.get('cannot_trade', item.get('entry') in CANNOT_TRADE):
            reason = 'cannot_trade'
        if not reason and not item.get('usd'):
            continue                     # no US dollar price: nothing to compare
        if reason:
            excluded.append({'entry': item['entry'], 'name': item['name'], 'category': item.get('category'),
                             'reason': reason, 'reason_text': EXCLUDED_REASONS[reason]})
        else:
            candidates.append(item)
    return candidates, excluded


def history_key(market_id, side):
    return f'{market_id}:{side}'


def make_offer(market_id, display, side, gross, fee, cost, count=None, balance=None, suspicious=False,
               rising=False, falling=False):
    net = gross * (1 - fee)
    profit = net - cost
    return {'market': market_id, 'display': display, 'side': side, 'gross': round(gross, 3), 'fee': fee,
            'net': round(net, 3), 'profit': round(profit, 3),
            'profit_pct': round(profit / cost * 100, 1) if cost > 0 else None,
            'count': count, 'balance': balance, 'suspicious': bool(suspicious),
            'rising': bool(rising), 'falling': bool(falling)}


def best_offer(offers):
    """The highest net offer; a normal offer always beats a suspicious one."""
    return max(offers, key=lambda o: (not o['suspicious'], o['net']), default=None)


def cheapest_wallet(prices, wallets, rates):
    """The cheapest way your accounts can pay: {usd, currency, minor_units, accounts} for the wallet
    currency (one the game store sells in) with the lowest US dollar price, or None.
    *prices*: {ISO: minor units}; *wallets*: [{account_name, currency}]."""
    by_currency = {}
    for wallet in wallets or []:
        currency = wallet.get('currency')
        if currency and currency in GAME_STORE_CURRENCIES and prices.get(currency):
            by_currency.setdefault(currency, []).append(wallet.get('account_name'))
    best = None
    for currency, accounts in by_currency.items():
        usd = to_usd(prices[currency], currency, rates)
        if usd is not None and (best is None or usd < best['usd']):
            best = {'usd': round(usd, 3), 'currency': currency, 'minor_units': prices[currency],
                    'accounts': sorted(accounts, key=str.lower)}
    return best


def usable_prices(prices, balance_markets=()):
    """A day's {history key: price} without the cash prices the board would cross out: above
    SUSPICIOUS_RATIO × that day's Buff163 listing (else its cheapest cash listing)."""
    def market(key):
        return key.split(':', 1)[0]
    priced = {key: price for key, price in (prices or {}).items()
              if key != 'store' and isinstance(price, (int, float)) and price > 0}
    cash_listings = [price for key, price in priced.items()
                     if key.endswith(':' + SIDE_LISTING) and market(key) not in balance_markets]
    reference = priced.get(history_key(REFERENCE_MARKET, SIDE_LISTING)) or min(cash_listings, default=None)
    return {key: price for key, price in priced.items()
            if market(key) in balance_markets or not reference or price <= reference * SUSPICIOUS_RATIO}


def history_statistics(history, name, keys, fees, cost, balance_markets=(), days=HISTORY_STATISTICS_DAYS,
                       today=None):
    """What the last *days* days of history say about *name*: the best net price each day over
    *keys* ({history key: market id}), how many days it beat *cost*, and the best day. Prices
    the board would cross out are left out (``usable_prices``)."""
    today = today or datetime.date.today()
    first = (today - datetime.timedelta(days=days - 1)).isoformat()
    series = []
    for date in sorted(d for d in (history or {}) if d >= first):
        raw = ((history[date] or {}).get(name)) or {}
        prices = usable_prices(raw, balance_markets)
        nets = [prices[key] * (1 - fees.get(market_id, 0)) for key, market_id in keys.items() if prices.get(key)]
        if nets:
            series.append({'date': date, 'net': round(max(nets), 3),
                           'cost': raw.get('store') or cost})
    profitable = [point for point in series if point['net'] > point['cost']]
    best = max(series, key=lambda point: point['net'], default=None)
    return {'days': len(series), 'profitable_days': len(profitable),
            'best_net': best['net'] if best else None, 'best_date': best['date'] if best else None,
            'series': [point['net'] for point in series]}


def gap_days(history, name, listing_keys, cost, balance_markets=(), days=HISTORY_STATISTICS_DAYS, today=None):
    """(days tracked, days the cheapest usable market listing was at least UNCONFIRMED_RATIO ×
    *cost*) for *name*."""
    today = today or datetime.date.today()
    first = (today - datetime.timedelta(days=days - 1)).isoformat()
    tracked = wide = 0
    for date in (d for d in (history or {}) if d >= first):
        prices = usable_prices(((history[date] or {}).get(name)) or {}, balance_markets)
        listings = [prices[key] for key in listing_keys if prices.get(key)]
        if listings:
            tracked += 1
            wide += min(listings) >= cost * UNCONFIRMED_RATIO
    return tracked, wide


def is_unconfirmed(listing_prices, cost, tracked_days, wide_days, resold_before):
    """True when the item looks unsellable from the store: the cheapest market listing is far
    above the store price now and (when there is history) on most days tracked, and you never
    resold it yourself."""
    if resold_before or not listing_prices or not cost:
        return False
    if min(listing_prices) < cost * UNCONFIRMED_RATIO:
        return False
    if tracked_days < UNCONFIRMED_MIN_DAYS:
        return True
    return wide_days >= tracked_days * UNCONFIRMED_SHARE_OF_DAYS


def verdict_of(instant, listing, cost):
    if not instant and not listing:
        return 'no_price'
    if instant and not instant['suspicious'] and instant['net'] > cost:
        return 'instant'
    if listing and not listing['suspicious'] and listing['net'] > cost:
        return 'listing'
    return 'loss'


def reference_price(indexes, markets, name):
    """Buff163's lowest listing of *name*, else the lowest listing on the chosen cash markets."""
    reference = ((indexes.get((REFERENCE_MARKET, SIDE_LISTING)) or {}).get(name) or {}).get('price')
    if reference:
        return reference
    listings = [((indexes.get((m['id'], SIDE_LISTING)) or {}).get(name) or {}).get('price')
                for m in markets if not m.get('balance')]
    return min((price for price in listings if price), default=None)


def item_row(item, markets, indexes, fees, ledger, history, wallets, rates, sheet_prices, today=None):
    """One board row for a catalogue item. *markets*: [{id, display, balance}]; *indexes*:
    {(market id, side): {name: {price, count, rising, falling}}}."""
    name, cost = item['name'], item['usd']
    reference = reference_price(indexes, markets, name)
    offers = []
    for market in markets:
        for side in (SIDE_INSTANT, SIDE_LISTING):
            quote = (indexes.get((market['id'], side)) or {}).get(name)
            if not quote or not quote.get('price'):
                continue
            suspicious = bool(not market.get('balance') and reference
                              and quote['price'] > reference * SUSPICIOUS_RATIO)
            offers.append(make_offer(market['id'], market['display'], side, quote['price'], fees.get(market['id'], 0.0), cost,
                                     quote.get('count'), market.get('balance'), suspicious,
                                     quote.get('rising'), quote.get('falling')))
    instant = best_offer([o for o in offers if o['side'] == SIDE_INSTANT])
    listing = best_offer([o for o in offers if o['side'] == SIDE_LISTING])
    record = (ledger or {}).get(name)
    resold_before = bool(record and record.get('sold'))
    listing_keys = [history_key(m['id'], SIDE_LISTING) for m in markets]
    balance_markets = {m['id'] for m in markets if m.get('balance')}
    tracked, wide = gap_days(history, name, listing_keys, cost, balance_markets, today=today)
    keys = {history_key(m['id'], side): m['id'] for m in markets for side in (SIDE_INSTANT, SIDE_LISTING)}
    best = best_offer([o for o in (instant, listing) if o])
    # The best price paid out as money (not Steam wallet or a site balance), instant or listing.
    cash = best_offer([o for o in offers if not o['balance'] and not o['suspicious']])
    offers.sort(key=lambda o: o['net'], reverse=True)
    return {
        'entry': item['entry'], 'name': name, 'category': item.get('category'),
        'definition_index': item.get('definition_index'), 'market_url': item.get('market_url'),
        'on_store_front': item.get('on_store_front'),
        'cost': cost,
        'cheapest_wallet': cheapest_wallet(sheet_prices.get(item['entry']) or {}, wallets, rates),
        'instant': instant, 'listing': listing, 'best': best, 'cash': cash, 'offers': offers,
        # Only a price you can sell at ranks the row (both sides suspicious: no percentage).
        'profit_pct': best['profit_pct'] if best and not best['suspicious'] else None,
        'cash_profit_pct': cash['profit_pct'] if cash else None,
        'reference_listing': round(reference, 3) if reference else None,
        'verdict': verdict_of(instant, listing, cost),
        'unconfirmed': is_unconfirmed([o['gross'] for o in offers if o['side'] == SIDE_LISTING],
                                      cost, tracked, wide, resold_before),
        'resold_before': resold_before,
        'ledger': record,
        'history': history_statistics(history, name, keys, fees, cost, balance_markets, today=today),
    }


def board_summary(rows, excluded):
    sellable = [r for r in rows if not r['unconfirmed']]
    return {
        'items': len(rows),
        'instant_profit': sum(1 for r in sellable if r['verdict'] == 'instant'),
        'listing_profit': sum(1 for r in sellable if r['verdict'] == 'listing'),
        'cash_profit': sum(1 for r in sellable if r['cash'] and r['cash']['net'] > r['cost']),
        'unconfirmed': sum(1 for r in rows if r['unconfirmed']),
        'excluded': len(excluded),
        'best_profit_pct': max((r['profit_pct'] for r in sellable
                                if r['verdict'] in ('instant', 'listing') and r['profit_pct'] is not None), default=None),
    }


def record_prices(history, today, names, indexes, store_costs):
    """Fold fresh indexes into *history* ({date: {name: {key: best price that day}}}); the store
    price of the day goes under 'store'. Returns True when anything changed."""
    day = history.setdefault(today, {})
    changed = False
    for (market_id, side), index in indexes.items():
        key = history_key(market_id, side)
        for name in names:
            price = ((index or {}).get(name) or {}).get('price')
            if not price:
                continue
            prices = day.setdefault(name, {})
            if price > prices.get(key, 0):
                prices[key] = round(price, 3)
                changed = True
    for name in names:
        if name in day and store_costs.get(name) and day[name].get('store') != store_costs[name]:
            day[name]['store'] = store_costs[name]
            changed = True
    return changed


def clean_history(data):
    """Only the well-formed part of a history read from disk: {date: {name: {key: number}}}."""
    if not isinstance(data, dict):
        return {}
    clean = {}
    for date, names in data.items():
        if not isinstance(date, str) or not isinstance(names, dict):
            continue
        day = {name: {key: value for key, value in prices.items() if isinstance(value, (int, float))}
               for name, prices in names.items() if isinstance(name, str) and isinstance(prices, dict)}
        if day:
            clean[date] = day
    return clean


def prune_history(history, today, days=HISTORY_DAYS):
    first = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days - 1)).isoformat()
    for date in [d for d in history if d < first]:
        del history[date]


# ---- the service ---------------------------------------------------------------------------

class StoreArbitrageService:
    def __init__(self, huginn_service, catalogue_service, storage_shop_service, draupnir_service,
                 history_path=HISTORY_PATH, today=None):
        self.huginn = huginn_service
        self.catalogue = catalogue_service
        self.storage_shop = storage_shop_service
        self.draupnir = draupnir_service
        self.history_path = history_path
        self._today = today or datetime.date.today
        self._lock = threading.Lock()
        self._indexes = {}       # (market id, side) -> (fetched at, {name: quote}) for catalogue names only
        self._warming = set()   # pairs queued or being pulled
        self._queue = []        # pairs waiting for the one worker thread
        self._worker_running = False
        self._save_lock = threading.Lock()   # one history write at a time (temporary file + rename)
        self._failures = {}      # (market id, side) -> (failed at, error)
        self._history = self._load_history()
        self._history_saved_at = 0.0
        self._history_dirty = False
        self._watch_started = False

    # ---- markets -------------------------------------------------------------------------------

    def markets(self, settings=None):
        """Every market you can sell on: {id, display, fee, fee_known, instant, balance}."""
        return [{'id': m['id'], 'display': m['display'], 'fee': m['fee'], 'fee_known': m['feeKnown'],
                 'instant': m['hasAutobuy'], 'balance': HarvestService.BALANCE_PAYOUT.get(m['id'])}
                for m in self.huginn.market_registry(settings)]

    def clean_markets(self, ids, settings=None):
        known = [m['id'] for m in self.markets(settings)]
        chosen = [m for m in dict.fromkeys(ids or []) if m in known]
        return chosen or [m for m in DEFAULT_MARKETS if m in known]

    def _sides(self, market_ids, settings=None):
        """The (market id, side) pairs to price: every listing, the instant side where the market
        has one, and Buff163's listing as the reference."""
        instant = {m['id'] for m in self.markets(settings) if m['instant']}
        pairs = [(m, side) for m in market_ids for side in (SIDE_INSTANT, SIDE_LISTING)
                 if side == SIDE_LISTING or m in instant]
        if (REFERENCE_MARKET, SIDE_LISTING) not in pairs:
            pairs.append((REFERENCE_MARKET, SIDE_LISTING))
        return pairs

    # ---- price indexes (non-blocking, one background thread) -----------------------------------

    def _names(self):
        candidates, _ = split_items(self.catalogue.status().get('items'))
        return {item['name'] for item in candidates}

    def _fetch_index(self, token, market_id, side):
        if side == SIDE_INSTANT:
            index = self.huginn.market_autobuy_index(token, market_id) or {}
        else:
            index = self.huginn.market_buy_index(token, market_id) or {}
        names = self._names()
        return {name: quote for name, quote in index.items() if name in names}

    def _index_state(self, token, pairs):
        """({pair: index}, status, [failed pairs]). Serves what is cached and queues a warm for
        anything missing or stale; status: no_token | warming | refreshing | fresh | error."""
        now = time.time()
        found, missing, stale, failed = {}, [], [], []
        with self._lock:
            for pair in pairs:
                if pair == ('CsFloat', SIDE_INSTANT):
                    continue                 # read live below (the swept cache on disk)
                hit = self._indexes.get(pair)
                if hit:
                    found[pair] = hit[1]
                    if now - hit[0] < INDEX_TTL:
                        continue
                failure = self._failures.get(pair)
                if failure and now - failure[0] < RETRY_AFTER_FAILURE:
                    failed.append(pair)
                elif hit:
                    stale.append(pair)
                else:
                    missing.append(pair)
        if ('CsFloat', SIDE_INSTANT) in pairs:
            try:
                found[('CsFloat', SIDE_INSTANT)] = self._fetch_index(token, 'CsFloat', SIDE_INSTANT)
            except Exception as e:
                log.info('[STORE-ARBITRAGE] CSFloat buy orders unavailable: %s', e)
        if not token:
            return found, 'no_token', failed
        if missing or stale:
            self._queue_warm(token, missing + stale)
        if missing:                      # still loading others: keep polling fast
            return found, 'warming', failed
        if failed:
            return found, 'error', failed
        return found, ('refreshing' if stale else 'fresh'), failed

    def _queue_warm(self, token, pairs):
        """Queue pairs for the ONE worker thread (started when none runs): pulse throttles
        parallel pulls, and each pull is tens of megabytes."""
        with self._lock:
            new = [pair for pair in pairs if pair not in self._warming]
            self._warming.update(new)
            self._queue.extend((token, pair) for pair in new)
            if not new or self._worker_running:
                return
            self._worker_running = True
        threading.Thread(target=self._work, daemon=True, name='store-arbitrage-warm').start()

    def _work(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._worker_running = False
                    break
                token, pair = self._queue.pop(0)
            self._warm(token, [pair])
        self._save_history(force=True)

    def _warm(self, token, pairs):
        for pair in pairs:
            started = time.time()
            try:
                index = self._fetch_index(token, *pair)
                with self._lock:
                    self._indexes[pair] = (time.time(), index)
                    self._failures.pop(pair, None)
                self._record({pair: index})
                log.info('[STORE-ARBITRAGE] %s %s: %d store items priced in %.0fs',
                         pair[0], pair[1], len(index), time.time() - started)
            except Exception as e:
                log.warning('[STORE-ARBITRAGE] %s %s prices failed: %s', pair[0], pair[1], e)
                with self._lock:
                    self._failures[pair] = (time.time(), str(e)[:200] or type(e).__name__)
            finally:
                with self._lock:
                    self._warming.discard(pair)

    # ---- history -------------------------------------------------------------------------------

    def _load_history(self):
        try:
            with gzip.open(self.history_path, 'rt', encoding='utf-8') as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except Exception as e:
            # Kept aside, not overwritten by the next save.
            aside = f'{self.history_path}.unreadable-{int(time.time())}'
            log.warning('[STORE-ARBITRAGE] history unreadable (%s): moved to %s, starting a new one', e, aside)
            try:
                os.replace(self.history_path, aside)
            except OSError:
                pass
            return {}
        return clean_history(data)

    def _record(self, indexes):
        candidates, _ = split_items(self.catalogue.status().get('items'))
        costs = {item['name']: item['usd'] for item in candidates}
        today = self._today().isoformat()
        with self._lock:
            if record_prices(self._history, today, list(costs), indexes, costs):
                self._history_dirty = True
            prune_history(self._history, today)
        self._save_history()

    def _save_history(self, force=False):
        with self._save_lock:            # the snapshot and its rename stay in order
            self._write_history(force)

    def _write_history(self, force):
        with self._lock:
            if not self._history_dirty or (not force and time.time() - self._history_saved_at < HISTORY_SAVE_MIN_INTERVAL):
                return
            snapshot = json.dumps(self._history, separators=(',', ':'))
            self._history_dirty = False
            self._history_saved_at = time.time()
        temporary = f'{self.history_path}.{os.getpid()}.{threading.get_ident()}.tmp'
        try:
            os.makedirs(os.path.dirname(self.history_path) or '.', exist_ok=True)
            with gzip.open(temporary, 'wt', encoding='utf-8') as handle:
                handle.write(snapshot)
            os.replace(temporary, self.history_path)
        except OSError as e:
            log.error('[STORE-ARBITRAGE] could not save %s: %s', self.history_path, e)
            with self._lock:
                self._history_dirty = True

    # ---- the hourly watcher ----------------------------------------------------------------------

    def start_background(self, settings_provider):
        """Warm the default markets every hour, so the history grows while the page is closed."""
        if self._watch_started:
            return
        self._watch_started = True

        def loop():
            time.sleep(WATCH_FIRST_WAIT)
            while True:
                try:
                    settings = settings_provider() or {}
                    token = settings.get('tradeon_token', '')
                    if token and self.catalogue.status().get('items'):
                        self._queue_warm(token, self._sides(self.clean_markets(None, settings), settings))
                except Exception:
                    log.exception('[STORE-ARBITRAGE] watcher tick failed')
                time.sleep(WATCH_INTERVAL)
        threading.Thread(target=loop, daemon=True, name='store-arbitrage-watch').start()

    # ---- the board -------------------------------------------------------------------------------

    def _wallets(self):
        try:
            return self.storage_shop.wallets(), self.storage_shop.exchange_rates(), self.storage_shop.sheet_entries()
        except Exception as e:
            log.info('[STORE-ARBITRAGE] wallets unavailable: %s', e)
            return [], {}, {}

    def board(self, token, market_ids=None, settings=None):
        catalogue = self.catalogue.status()
        candidates, excluded = split_items(catalogue.get('items'))
        market_ids = self.clean_markets(market_ids, settings)
        listed = {m['id']: m for m in self.markets(settings)}
        markets = [listed[m] for m in market_ids]
        pairs = self._sides(market_ids, settings)
        indexes, status, failed = self._index_state(token, pairs)
        fees = {m: self.huginn.market_fee(m, settings) for m in {*market_ids, REFERENCE_MARKET}}
        ledger = self.draupnir.item_trades([item['name'] for item in candidates]) if self.draupnir else {}
        wallets, rates, sheet = self._wallets()
        with self._lock:
            history = json.loads(json.dumps(self._history))
        today = self._today()
        rows, unpriced = [], []
        for item in candidates:
            row = item_row(item, markets, indexes, fees, ledger, history, wallets, rates, sheet, today=today)
            if row['verdict'] == 'no_price' and status in ('fresh', 'refreshing'):
                unpriced.append({'entry': item['entry'], 'name': item['name'], 'category': item.get('category'),
                                 'reason': 'no_price', 'reason_text': EXCLUDED_REASONS['no_price']})
            elif row['verdict'] != 'no_price':
                rows.append(row)
        # Unconfirmed rows (probably not resellable) go below every confirmed one.
        rows.sort(key=lambda r: (not r['unconfirmed'], r['profit_pct'] is not None, r['profit_pct'] or 0), reverse=True)
        excluded = excluded + unpriced
        loaded = {pair for pair in indexes}
        return {
            'status': status,
            'error': ('Price pull failed for ' + ', '.join(f'{listed.get(m, {}).get("display", m)} ({side})'
                                                          for m, side in failed)
                      + ' — retrying in about two minutes') if failed else None,
            'markets': [{**m, 'fee': fees[m['id']],
                         'loaded': {side: (m['id'], side) in loaded for side in (SIDE_INSTANT, SIDE_LISTING)}}
                        for m in markets],
            'rows': rows,
            'excluded': excluded,
            'summary': board_summary(rows, excluded),
            'catalogue_read_at': catalogue.get('read_at'),
            'history_days': len(history),
            'trade_hold_days': TRADE_HOLD_DAYS,
            'unconfirmed_ratio': UNCONFIRMED_RATIO,
        }

    def options(self, settings=None):
        return {'markets': self.markets(settings), 'default_markets': list(DEFAULT_MARKETS)}
