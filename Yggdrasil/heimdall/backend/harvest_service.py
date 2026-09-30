"""Harvest: what can I sell right now for more than I paid?

For every purchase lot still held in Draupnir (per account, at the price you
actually paid, no averaging: "$3.84 x36, $4.00 x100") it looks up what the chosen AUTOBUY markets pay instantly — for example
everthinklol => CSMoney (Trade) — nets the sell fee, and ranks by profit. The buy
side is your real purchase price, not a market listing, so a row answers "sell
this now and I lock in +$X over what it cost me".

Only autobuy (instant-sell / buy-order) markets are offered as targets, so every
row is actionable. Prices come from Huginn's pulse 'Buy' price type (CSFloat from
its swept buy-orders cache). Each market's index is cached on its own for
_INDEX_TTL and warmed in ONE background thread, one market after another, so
switching accounts or markets never bursts pulse (which throttles hard).
"""

import datetime
import logging
import threading
import time

logger = logging.getLogger(__name__)


class HarvestService:
    DEFAULT_MARKETS = ['CsMoneyTrade']
    _INDEX_TTL = 10 * 60        # seconds a market's autobuy index is served before re-warming
    # A market whose warm failed is not retried for this long; the board reports
    # status 'error' (naming the market) in that window so the page stops polling.
    _RETRY_AFTER_FAILURE = 120
    # Steam puts a 7-day trade protection on items bought from the Steam market
    # and on items received in trades; a buy newer than this may not be sellable
    # (tradable) yet. A hint only: the ledger does not know the exact unlock time.
    TRADE_HOLD_DAYS = 8
    # Markets that pay in their own balance, not withdrawable money: the balance
    # only buys items there, usually at similarly inflated prices, so a "profit"
    # into these is not cash profit. Every other autobuy market pays out money.
    BALANCE_PAYOUT = {
        'CsMoneyTrade': 'cs.money trade balance',
        'LootFarm': 'LOOT.Farm balance',
        'TradeItTrade': 'TradeIt trade balance',
        'Steam': 'Steam wallet',
    }
    # Sanity check for cash offers: a buy order far above Buff163's cheapest
    # listing is almost always a buy order for one float or pattern (common on
    # DMarket and WhiteMarket), not for any copy, so it will not take yours.
    # Such an offer is flagged "check", never picked as best while a normal offer
    # exists, and kept out of the totals. Balance markets are priced in their own
    # (Steam-like) scale, so they are not checked against Buff.
    # CSFloat's autobuy is read from the swept buy-orders cache on disk (instant, no
    # pulse pull), so it is never held in the 10-minute cache: a finished sweep
    # shows up on the next request.
    LIVE_MARKETS = {'CsFloat'}
    REFERENCE = 'buff-listing'      # key of Draupnir's cached Buff163 min-listing map
    SUSPICIOUS_RATIO = 1.3

    def __init__(self, huginn_service, draupnir_service, today=None):
        self.huginn = huginn_service
        self.draupnir = draupnir_service
        self._today = today or datetime.date.today
        self._indexes = {}      # market id -> (fetched_at, {name: {price, count, image}})
        self._warming = set()   # market ids queued or being pulled
        self._failures = {}     # market id -> (failed_at, error message) of the last failed warm
        self._lock = threading.Lock()

    # ---- markets ----------------------------------------------------------

    def autobuy_markets(self, settings=None):
        """Markets you can sell into instantly, with their effective fee."""
        return [{'id': m['id'], 'display': m['display'], 'fee': m['fee'], 'fee_known': m['feeKnown'],
                 'balance': self.BALANCE_PAYOUT.get(m['id'])}
                for m in self.huginn.market_registry(settings) if m['hasAutobuy']]

    def clean_markets(self, ids, settings=None):
        """Known autobuy market ids from `ids`, de-duplicated, in order; the
        default (CSMoney Trade) when nothing valid is left."""
        allowed = {m['id'] for m in self.autobuy_markets(settings)}
        out = []
        for market_id in ids or []:
            if market_id in allowed and market_id not in out:
                out.append(market_id)
        return out or list(self.DEFAULT_MARKETS)

    # ---- price indexes (non-blocking) ---------------------------------------

    def recent_failures(self, market_ids):
        """[{id, error}] for the markets (the Buff163 reference included) whose warm
        failed within the last _RETRY_AFTER_FAILURE seconds."""
        now = time.time()
        with self._lock:
            return [{'id': market_id, 'error': self._failures[market_id][1]}
                    for market_id in [*market_ids, self.REFERENCE]
                    if market_id in self._failures
                    and now - self._failures[market_id][0] < self._RETRY_AFTER_FAILURE]

    def _index_state(self, token, market_ids):
        """({market id: index}, status). Serves what is cached; queues a background
        warm for anything missing or stale, except a market whose warm failed within
        _RETRY_AFTER_FAILURE: that one is not retried yet and makes the status
        'error' (see recent_failures for which market and why)."""
        now = time.time()
        found, missing, stale, failed = {}, [], [], []
        for market_id in market_ids:
            if market_id in self.LIVE_MARKETS:
                found[market_id] = self.huginn.market_autobuy_index(token, market_id) or {}
        with self._lock:
            for market_id in [*market_ids, self.REFERENCE]:
                if market_id in self.LIVE_MARKETS:
                    continue
                hit = self._indexes.get(market_id)
                if hit:
                    found[market_id] = hit[1]
                    if now - hit[0] < self._INDEX_TTL:
                        continue
                failure = self._failures.get(market_id)
                if failure and now - failure[0] < self._RETRY_AFTER_FAILURE:
                    failed.append(market_id)
                elif hit:
                    stale.append(market_id)
                else:
                    missing.append(market_id)
        if not token:
            return found, 'no_token'
        todo = missing + stale
        if todo:
            self._queue_warm(token, todo)
        if failed:
            return found, 'error'
        if missing:
            return found, 'warming'
        return found, ('refreshing' if stale else 'fresh')

    def _queue_warm(self, token, market_ids):
        with self._lock:
            new = [m for m in market_ids if m not in self._warming]
            self._warming.update(new)
        if new:
            threading.Thread(target=self._warm, args=(token, new), daemon=True).start()

    def _warm(self, token, market_ids):
        for market_id in market_ids:   # one after another: pulse throttles parallel pulls
            started = time.time()
            try:
                if market_id == self.REFERENCE:
                    # Shared with Draupnir's valuation (cached for an hour there).
                    index = {name: {'price': price}
                             for name, price in (self.huginn.price_map(token, 'buff') or {}).items() if price}
                else:
                    index = self.huginn.market_autobuy_index(token, market_id) or {}
                with self._lock:
                    self._indexes[market_id] = (time.time(), index)
                    self._failures.pop(market_id, None)
                logger.info('[HARVEST] %s autobuy index: %d items in %.0fs',
                            market_id, len(index), time.time() - started)
            except Exception as e:
                logger.warning('[HARVEST] %s autobuy index failed: %s', market_id, e)
                with self._lock:
                    self._failures[market_id] = (time.time(), str(e)[:200] or type(e).__name__)
            finally:
                with self._lock:
                    self._warming.discard(market_id)

    # ---- the board ----------------------------------------------------------

    def accounts(self):
        """[{id, name, lots}] for the account picker (accounts with holdings)."""
        counts = {}
        for lot in self.draupnir.open_lots():
            key = (lot['portfolio_id'], lot['account'])
            counts[key] = counts.get(key, 0) + 1
        return sorted(({'id': portfolio_id, 'name': name, 'lots': lot_count}
                       for (portfolio_id, name), lot_count in counts.items()),
                      key=lambda account: account['name'].lower())

    def board(self, token, account='all', market_ids=None, min_profit_pct=None, settings=None):
        """One row per purchase lot (account + item + price paid + platform) that at
        least one chosen market buys instantly: best net offer, profit per unit and
        for the whole lot."""
        market_ids = self.clean_markets(market_ids, settings)
        indexes, status = self._index_state(token, market_ids)
        failed = ([{**f, 'display': self._display(f['id'])} for f in self.recent_failures(market_ids)]
                  if status == 'error' else [])
        failed_ids = {f['id'] for f in failed}
        fees = {m: self.huginn.market_fee(m, settings) for m in market_ids}
        known = {m['id']: m['fee_known'] for m in self.autobuy_markets(settings)}

        lots = [p for p in self.draupnir.open_lots()
                     if account in (None, '', 'all') or p['portfolio_id'] == account]
        reference = indexes.get(self.REFERENCE) or {}
        rows = [self._row(p, market_ids, indexes, fees, known, reference) for p in lots]
        unpriced = sum(1 for r in rows if r is None)
        rows = [r for r in rows if r]
        if min_profit_pct is not None:
            rows = [r for r in rows if r['profit_pct'] is None or r['profit_pct'] >= min_profit_pct]
        rows.sort(key=lambda r: r['total_profit'], reverse=True)

        return {
            'rows': rows,
            'status': status,     # no_token | warming | refreshing | fresh | error
            # Markets whose price pull failed recently (ids), with their display name
            # and error in failed_market_details; retried after _RETRY_AFTER_FAILURE.
            'failed_markets': [f['id'] for f in failed],
            'failed_market_details': failed,
            'error': ('Price pull failed for ' + ', '.join(f['display'] for f in failed)
                      + ' — retrying in about two minutes') if failed else None,
            'account': account or 'all',
            'markets': [{'id': m, 'display': self.huginn.market_display(m), 'fee': fees[m],
                         'fee_known': known.get(m, False), 'balance': self.BALANCE_PAYOUT.get(m), 'loaded': m in indexes,
                         'failed': m in failed_ids,
                         'count': len(indexes.get(m) or {})} for m in market_ids],
            'summary': self._summary(rows, len(lots), unpriced),
            'reference_loaded': self.REFERENCE in indexes,
            'min_profit_pct': min_profit_pct,
        }

    def _display(self, market_id):
        if market_id == self.REFERENCE:
            return 'Buff163 listing (reference)'
        return self.huginn.market_display(market_id)

    def _row(self, lot, market_ids, indexes, fees, known, reference):
        name, cost, qty = lot['item_name'], lot['price'], lot['qty']
        listing = (reference.get(name) or {}).get('price')
        offers = []
        for market_id in market_ids:
            entry = (indexes.get(market_id) or {}).get(name)
            if not entry or not entry.get('price'):
                continue
            net = entry['price'] * (1 - fees[market_id])
            balance = self.BALANCE_PAYOUT.get(market_id)
            offers.append({
                'market': market_id, 'display': self.huginn.market_display(market_id),
                'gross': round(entry['price'], 2), 'fee': fees[market_id],
                'fee_known': known.get(market_id, False),
                'balance': balance,
                'net': round(net, 2), 'count': entry.get('count'), 'image': entry.get('image'),
                'profit': round(net - cost, 3),
                'suspicious': bool(not balance and listing
                                   and entry['price'] > listing * self.SUSPICIOUS_RATIO),
            })
        if not offers:
            return None
        # Best = highest net, but a normal offer always beats a suspicious one.
        offers.sort(key=lambda o: (not o['suspicious'], o['net']), reverse=True)
        best = offers[0]
        profit = best['net'] - cost
        days = self._days_since(lot['last_buy_date'])
        return {
            'account': lot['account'], 'portfolio_id': lot['portfolio_id'],
            'item_name': name, 'qty': qty, 'paid': round(cost, 4), 'platform': lot['platform'],
            'image': next((o['image'] for o in offers if o.get('image')), None),
            'best': best, 'offers': offers,
            'buff_listing': round(listing, 2) if listing else None,
            'profit': round(profit, 3),
            # A zero-cost lot (a drop or a gift) has no percentage: any sale is profit.
            'profit_pct': round(profit / cost * 100, 2) if cost > 0 else None,
            'total_profit': round(profit * qty, 2),
            'proceeds': round(best['net'] * qty, 2),
            'last_buy_date': lot['last_buy_date'],
            'days_since_buy': days,
            'maybe_trade_held': days is not None and days < self.TRADE_HOLD_DAYS,
        }

    def _days_since(self, date_text):
        try:
            return (self._today() - datetime.date.fromisoformat((date_text or '')[:10])).days
        except ValueError:
            return None

    @staticmethod
    def _summary(rows, lots, unpriced):
        to_check = [r for r in rows if r['best']['suspicious']]
        winners = [r for r in rows if r['total_profit'] > 0 and not r['best']['suspicious']]
        cash = [r for r in winners if not r['best']['balance']]
        return {
            'lots': lots,
            'priced': len(rows),
            'unpriced': unpriced,
            'profitable': len(winners),
            'total_profit': round(sum(r['total_profit'] for r in winners), 2),
            # The part paid out as money (best offer on a cash market), not site balance.
            'cash_profit': round(sum(r['total_profit'] for r in cash), 2),
            'proceeds': round(sum(r['proceeds'] for r in winners), 2),
            'cost': round(sum(r['paid'] * r['qty'] for r in winners), 2),
            'to_check': len(to_check),
            'best_profit_pct': max((r['profit_pct'] for r in winners if r['profit_pct'] is not None), default=None),
        }
