"""Draupnir — portfolio tracker.

Portfolios hold buy/sell transactions for CS items. We track cost basis,
realized P/L (avg-cost method) and, using Huginn's live pulse prices,
current market value and unrealized P/L. State persists as a single JSON
file (gitignored — it's personal holdings data), guarded by a lock.
"""
import csv
import io
import json
import os
import threading
import uuid
from datetime import datetime, timezone

PORTFOLIOS_FILE = os.path.join(os.path.dirname(__file__), 'portfolios.json')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _new_id():
    return uuid.uuid4().hex[:12]


def _demojibake(s):
    """Best-effort repair of UTF-8 text that was decoded as latin-1/cp1252
    (e.g. 'StatTrakâ¢' -> 'StatTrak™'). Falls back to the original string.

    Only attempted when tell-tale mojibake characters are present, so clean
    names pass through untouched."""
    if not s or not any(c in s for c in ('Ã', 'â', 'Â', 'ð', 'Ä', '¢', '€', 'Ð', 'Ñ')):
        return s
    for enc in ('latin-1', 'cp1252'):
        try:
            fixed = s.encode(enc).decode('utf-8')
            if fixed != s:
                return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return s


def _cents_to_usd(v):
    """Price-tracker exports write money as integer cents with no decimal point
    (e.g. $1.58 -> '158'); convert to USD. Empty/'N/A' -> None.

    A value that already contains a decimal point is treated as real dollars —
    Pricempire never writes decimals, but our own export (export_csv) does, so
    this keeps an exported CSV round-trippable through import."""
    v = (v or '').strip()
    if v in ('', 'N/A', 'n/a'):
        return None
    try:
        if '.' in v:
            return round(float(v), 2)
        return round(float(v) / 100.0, 2)
    except ValueError:
        return None


class PortfolioService:
    def __init__(self, huginn_service=None, path=PORTFOLIOS_FILE):
        self.huginn = huginn_service
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()
        # Optional BackupService, wired by app.py. Every persisted change is
        # snapshotted for point-in-time restore (deduped by content hash).
        self._backup = None

    def set_backup(self, backup_service):
        self._backup = backup_service

    def reload(self):
        """Re-read the source file into memory — used after a backup restore
        overwrites portfolios.json underneath us."""
        with self._lock:
            self._data = self._load()

    # ---- persistence -------------------------------------------------------

    def _load(self):
        if not os.path.exists(self.path):
            return {'portfolios': {}}
        try:
            with open(self.path) as f:
                data = json.load(f)
            data.setdefault('portfolios', {})
            return data
        except Exception as e:
            print(f'[DRAUPNIR] Could not read {self.path}: {e}')
            return {'portfolios': {}}

    def _persist(self):
        """Caller must hold self._lock."""
        try:
            with open(self.path, 'w') as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            print(f'[DRAUPNIR] Could not persist portfolios: {e}')
            return
        # Snapshot the new state for point-in-time restore. Best-effort and
        # deduped by content hash — never lets a backup issue break the write.
        if self._backup is not None:
            self._backup.snapshot('change')

    # ---- transaction shaping ----------------------------------------------

    @staticmethod
    def _clean_txn(raw):
        """Normalize a transaction dict from the API/CSV into stored shape."""
        typ = str(raw.get('type') or 'buy').strip().lower()
        if typ not in ('buy', 'sell'):
            typ = 'buy'
        try:
            qty = int(float(raw.get('qty') or raw.get('quantity') or 1))
        except (ValueError, TypeError):
            qty = 1
        qty = max(1, qty)
        try:
            price = round(float(raw.get('price')), 4)
        except (ValueError, TypeError):
            price = 0.0
        try:
            fee_percent = float(raw.get('fee_percent') or 0) or 0.0
        except (ValueError, TypeError):
            fee_percent = 0.0
        return {
            'id': raw.get('id') or _new_id(),
            'item_name': (raw.get('item_name') or '').strip(),
            'type': typ,
            'qty': qty,
            'price': price,
            'platform': (raw.get('platform') or '').strip(),
            # Blank date (manual quick-add) defaults to today so the row sorts
            # to the top with the other recent entries instead of the bottom.
            'date': (raw.get('date') or '').strip() or _today(),
            'note': (raw.get('note') or '').strip(),
            'fee_percent': fee_percent,
            # Marks a leg of an inter-account move (buy on one of your accounts,
            # sell on another). Excluded from the combined ledger so moving items
            # between your own accounts doesn't distort overall profit.
            'is_arbitrage': bool(raw.get('is_arbitrage')),
            'created_at': raw.get('created_at') or _now(),
        }

    # ---- portfolio CRUD ----------------------------------------------------

    def create_portfolio(self, name):
        with self._lock:
            pid = _new_id()
            self._data['portfolios'][pid] = {
                'id': pid,
                'name': (name or 'Untitled').strip() or 'Untitled',
                'created_at': _now(),
                'updated_at': _now(),
                'transactions': [],
            }
            self._persist()
            return self._data['portfolios'][pid]

    def rename_portfolio(self, pid, name):
        with self._lock:
            p = self._data['portfolios'].get(pid)
            if not p:
                return None
            p['name'] = (name or p['name']).strip() or p['name']
            p['updated_at'] = _now()
            self._persist()
            return p

    def delete_portfolio(self, pid):
        with self._lock:
            if pid in self._data['portfolios']:
                del self._data['portfolios'][pid]
                self._persist()
                return True
            return False

    def _get(self, pid):
        return self._data['portfolios'].get(pid)

    def all_item_names(self):
        """Every distinct item_name across all portfolios — names the user has
        already used (e.g. via import) count as valid even if pulse no longer
        lists them."""
        names = set()
        with self._lock:
            for p in self._data['portfolios'].values():
                for t in p['transactions']:
                    if t.get('item_name'):
                        names.add(t['item_name'])
        return names

    # ---- transaction CRUD --------------------------------------------------

    def add_transaction(self, pid, raw):
        with self._lock:
            p = self._get(pid)
            if not p:
                return None
            txn = self._clean_txn(raw)
            p['transactions'].append(txn)
            p['updated_at'] = _now()
            self._persist()
            return txn

    def update_transaction(self, pid, tid, fields):
        with self._lock:
            p = self._get(pid)
            if not p:
                return None
            for i, txn in enumerate(p['transactions']):
                if txn['id'] == tid:
                    merged = {**txn, **fields, 'id': tid, 'created_at': txn['created_at']}
                    p['transactions'][i] = self._clean_txn(merged)
                    p['updated_at'] = _now()
                    self._persist()
                    return p['transactions'][i]
            return None

    def delete_transaction(self, pid, tid):
        with self._lock:
            p = self._get(pid)
            if not p:
                return None
            before = len(p['transactions'])
            p['transactions'] = [t for t in p['transactions'] if t['id'] != tid]
            if len(p['transactions']) == before:
                return False
            p['updated_at'] = _now()
            self._persist()
            return True

    # ---- CSV import --------------------------------------------------------

    @staticmethod
    def parse_csv(text):
        """Parse a price-tracker CSV export into a list of transaction dicts.

        Handles the integer-cents 'Unit Price' quirk (no decimal point) and
        mojibake item names. Unknown columns are ignored."""
        txns = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            name = _demojibake((row.get('Name') or '').strip())
            if not name:
                continue
            price = _cents_to_usd(row.get('Unit Price'))
            if price is None:
                # fall back to Total / Quantity if unit price is missing
                total = _cents_to_usd(row.get('Total Price'))
                try:
                    q = float(row.get('Quantity') or 1)
                except ValueError:
                    q = 1
                price = round(total / q, 4) if (total is not None and q) else 0.0
            platform = (row.get('Marketplace') or '').strip()
            note = _demojibake((row.get('Note') or '').strip())
            if platform in ('', 'N/A') and note:
                platform = note  # some rows put the real market in Note
            txns.append({
                'item_name': name,
                'type': row.get('Type'),
                'qty': row.get('Quantity'),
                'price': price,
                'platform': platform if platform != 'N/A' else '',
                'date': (row.get('Date') or '').strip(),
                'note': note if note != 'N/A' else '',
                'fee_percent': row.get('Fee Percentage'),
            })
        return txns

    def import_csv(self, text, name=None, pid=None):
        """Import a CSV into a new portfolio (default) or append to `pid`.
        Returns (portfolio, imported_count)."""
        txns = [self._clean_txn(t) for t in self.parse_csv(text)]
        with self._lock:
            if pid:
                p = self._get(pid)
                if not p:
                    return None, 0
            else:
                pid = _new_id()
                p = {
                    'id': pid, 'name': (name or 'Imported').strip() or 'Imported',
                    'created_at': _now(), 'updated_at': _now(), 'transactions': [],
                }
                self._data['portfolios'][pid] = p
            p['transactions'].extend(txns)
            p['updated_at'] = _now()
            self._persist()
            return p, len(txns)

    # ---- CSV export --------------------------------------------------------

    EXPORT_COLUMNS = ['Name', 'Type', 'Quantity', 'Unit Price', 'Total Price',
                      'Marketplace', 'Date', 'Note', 'Fee Percentage']

    def export_csv(self, pid):
        """Serialize one portfolio's transactions to CSV text (real dollars, not
        cents). Round-trips back through import_csv. Returns (name, csv) or None.
        Rows are ordered newest-date first to match the on-screen table."""
        with self._lock:
            p = self._get(pid)
            if not p:
                return None
            name = p['name']
            txns = sorted(p['transactions'],
                          key=lambda t: (t.get('date') or '', t.get('created_at') or ''),
                          reverse=True)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=self.EXPORT_COLUMNS)
        w.writeheader()
        for t in txns:
            qty, price = t.get('qty') or 0, t.get('price') or 0.0
            w.writerow({
                'Name': t.get('item_name', ''),
                'Type': t.get('type', 'buy'),
                'Quantity': qty,
                'Unit Price': f'{price:.2f}',
                'Total Price': f'{qty * price:.2f}',
                'Marketplace': t.get('platform', ''),
                'Date': t.get('date', ''),
                'Note': t.get('note', ''),
                'Fee Percentage': t.get('fee_percent', 0) or 0,
            })
        return name, buf.getvalue()

    # ---- valuation / aggregation ------------------------------------------

    @staticmethod
    def _holdings(txns, prices):
        """Aggregate transactions per item into holdings with cost basis, P/L
        and (if a price is known) current value. `prices` is {name: usd} or None."""
        prices = prices or {}
        by_item = {}
        for t in txns:
            h = by_item.setdefault(t['item_name'], {
                'item_name': t['item_name'], 'buy_qty': 0, 'buy_cost': 0.0,
                'sell_qty': 0, 'sell_proceeds': 0.0,
            })
            total = t['qty'] * t['price']
            if t['type'] == 'sell':
                h['sell_qty'] += t['qty']
                h['sell_proceeds'] += total
            else:
                h['buy_qty'] += t['qty']
                h['buy_cost'] += total

        holdings = []
        for h in by_item.values():
            avg_cost = (h['buy_cost'] / h['buy_qty']) if h['buy_qty'] else 0.0
            net_qty = h['buy_qty'] - h['sell_qty']
            realized = h['sell_proceeds'] - avg_cost * h['sell_qty']
            price = prices.get(h['item_name'])
            cost_basis = avg_cost * max(net_qty, 0)
            market_value = (price * net_qty) if (price is not None and net_qty > 0) else None
            unrealized = (market_value - cost_basis) if market_value is not None else None
            holdings.append({
                **h,
                'avg_cost': round(avg_cost, 4),
                'net_qty': net_qty,
                'cost_basis': round(cost_basis, 2),
                'current_price': price,
                'market_value': round(market_value, 2) if market_value is not None else None,
                'realized_pl': round(realized, 2),
                'unrealized_pl': round(unrealized, 2) if unrealized is not None else None,
            })
        holdings.sort(key=lambda x: (x['market_value'] or x['cost_basis'] or 0), reverse=True)
        return holdings

    @staticmethod
    def _summarize(p, holdings):
        invested = sum(h['buy_cost'] for h in holdings)
        cost_basis = sum(h['cost_basis'] for h in holdings)
        # Current value covers the WHOLE portfolio: live market value for holdings
        # we can price, and cost basis as a neutral fallback for the rest (items
        # not in the pulse feed). This keeps the headline comparable to Invested
        # instead of only counting the handful of priced items. Unrealized P/L
        # below still counts only priced holdings — we don't invent gains on items
        # we can't value.
        current_value = sum(
            h['market_value'] if h['market_value'] is not None else h['cost_basis']
            for h in holdings
        )
        realized = sum(h['realized_pl'] for h in holdings)
        unrealized = sum(h['unrealized_pl'] or 0 for h in holdings)
        priced = any(h['current_price'] is not None for h in holdings)
        held = [h for h in holdings if h['net_qty'] > 0]
        unpriced_count = sum(1 for h in held if h['current_price'] is None)
        return {
            'id': p['id'], 'name': p['name'],
            'created_at': p['created_at'], 'updated_at': p['updated_at'],
            'txn_count': len(p['transactions']),
            'holdings_count': len(held),
            'unpriced_count': unpriced_count,
            'invested': round(invested, 2),
            'cost_basis': round(cost_basis, 2),
            'current_value': round(current_value, 2) if priced else None,
            'realized_pl': round(realized, 2),
            'unrealized_pl': round(unrealized, 2) if priced else None,
            'total_pl': round(realized + unrealized, 2) if priced else round(realized, 2),
            'priced': priced,
        }

    def list_portfolios(self, prices=None):
        with self._lock:
            ps = list(self._data['portfolios'].values())
        out = []
        for p in ps:
            holdings = self._holdings(p['transactions'], prices)
            out.append(self._summarize(p, holdings))
        out.sort(key=lambda s: s['created_at'])
        return out

    def get_portfolio(self, pid, prices=None):
        with self._lock:
            p = self._get(pid)
            if not p:
                return None
            p = json.loads(json.dumps(p))  # snapshot under lock
        holdings = self._holdings(p['transactions'], prices)
        return {
            **self._summarize(p, holdings),
            # Newest first, with created_at as a tiebreaker so a just-added
            # transaction always appears at the top of its date.
            'transactions': sorted(p['transactions'],
                                   key=lambda t: (t.get('date') or '', t.get('created_at') or ''),
                                   reverse=True),
            'holdings': holdings,
        }

    def combined_ledger(self, prices=None):
        """One ledger across ALL accounts, so you can see whether the arbitrage is
        profitable overall. Inter-account moves (transactions flagged
        is_arbitrage) are dropped — both the sell leg on the source account and
        the buy leg on the destination — so shuffling items between your own
        accounts nets to nothing. Each transaction is tagged with its account."""
        with self._lock:
            ps = json.loads(json.dumps(list(self._data['portfolios'].values())))
        txns = []
        for p in ps:
            for t in p['transactions']:
                if t.get('is_arbitrage'):
                    continue
                txns.append({**t, 'account': p['name'], 'portfolio_id': p['id']})
        holdings = self._holdings(txns, prices)
        pseudo = {'id': 'combined', 'name': 'All accounts',
                  'created_at': '', 'updated_at': '', 'transactions': txns}
        arb = sum(1 for p in ps for t in p['transactions'] if t.get('is_arbitrage'))
        return {
            **self._summarize(pseudo, holdings),
            'transactions': sorted(txns,
                                   key=lambda t: (t.get('date') or '', t.get('created_at') or ''),
                                   reverse=True),
            'holdings': holdings,
            'account_count': len(ps),
            'arbitrage_excluded': arb,
        }

    def spread_board(self, buy_prices=None, sell_prices=None, sell_fee_pct=15.0):
        """Cross-market arbitrage board: buy on one market (cheap — e.g. Buff163),
        exit on another (e.g. Steam), fee-aware. Aggregates every account and drops
        inter-account moves (is_arbitrage), same as the combined ledger, because the
        arbitrage spans accounts.

        For each item you still hold it juxtaposes:
          - avg_cost   — what you actually paid (real cost basis)
          - buy_price  — the buy market's live price now (is it still cheap to source?)
          - sell_net   — the sell market's live price net of its fee (your true exit)
          - live_spread_pct = (sell_net - buy_price) / buy_price
                         → the arb still open in the market right now (source more?)
          - margin     = (sell_net - avg_cost) * qty
                         → your unrealized profit if you exit this holding now

        `buy_prices` / `sell_prices` are {item_name: usd} maps (or None). Returns a
        rows list (held items, biggest margin first) plus headline aggregates."""
        buy_prices = buy_prices or {}
        sell_prices = sell_prices or {}
        try:
            fee = float(sell_fee_pct)
        except (TypeError, ValueError):
            fee = 15.0
        fee = min(max(fee, 0.0), 99.0)
        net_factor = 1.0 - fee / 100.0

        with self._lock:
            ps = json.loads(json.dumps(list(self._data['portfolios'].values())))
        txns = []
        for p in ps:
            for t in p['transactions']:
                if t.get('is_arbitrage'):
                    continue
                txns.append(t)
        arb = sum(1 for p in ps for t in p['transactions'] if t.get('is_arbitrage'))

        holdings = self._holdings(txns, None)
        realized = round(sum(h['realized_pl'] for h in holdings), 2)

        rows = []
        capital_deployed = 0.0          # cost basis of ALL held items
        capital_priced = 0.0            # cost basis of held items we can price a sell for
        exit_value = 0.0                # net proceeds if we sold priced items now
        unpriced = 0
        spread_wsum = 0.0               # capital-weighted live market spread
        spread_w = 0.0
        for h in holdings:
            qty = h['net_qty']
            if qty <= 0:
                continue
            avg_cost = h['avg_cost']
            cost_basis = h['cost_basis']
            capital_deployed += cost_basis
            buy_price = buy_prices.get(h['item_name'])
            sell_price = sell_prices.get(h['item_name'])
            sell_net = round(sell_price * net_factor, 4) if sell_price is not None else None
            live_spread_pct = (
                round((sell_net - buy_price) / buy_price * 100, 2)
                if (sell_net is not None and buy_price) else None
            )
            if sell_net is not None:
                margin_unit = round(sell_net - avg_cost, 4)
                margin = round(margin_unit * qty, 2)
                margin_pct = round((margin_unit / avg_cost) * 100, 2) if avg_cost else None
                capital_priced += cost_basis
                exit_value += sell_net * qty
                if live_spread_pct is not None:
                    spread_wsum += live_spread_pct * cost_basis
                    spread_w += cost_basis
            else:
                margin_unit = margin = margin_pct = None
                unpriced += 1
            rows.append({
                'item_name': h['item_name'],
                'net_qty': qty,
                'avg_cost': avg_cost,
                'cost_basis': cost_basis,
                'buy_price': buy_price,
                'sell_price': sell_price,
                'sell_net': sell_net,
                'live_spread_pct': live_spread_pct,
                'margin_unit': margin_unit,
                'margin': margin,
                'margin_pct': margin_pct,
            })

        # Biggest exit profit on top; unpriced rows sink to the bottom.
        rows.sort(key=lambda r: (r['margin'] is not None, r['margin'] or 0), reverse=True)

        unrealized = round(exit_value - capital_priced, 2)
        priced = bool(sell_prices)
        return {
            'buy_market': None, 'sell_market': None,   # filled by the route
            'sell_fee_pct': fee,
            'account_count': len(ps),
            'arbitrage_excluded': arb,
            'holdings_count': len(rows),
            'unpriced_count': unpriced,
            'capital_deployed': round(capital_deployed, 2),
            'exit_value': round(exit_value, 2) if priced else None,
            'unrealized_spread': unrealized if priced else None,
            'unrealized_spread_pct': (round(unrealized / capital_priced * 100, 2)
                                      if (priced and capital_priced) else None),
            'realized_pl': realized,
            'avg_spread_pct': round(spread_wsum / spread_w, 2) if spread_w else None,
            'priced': priced,
            'rows': rows,
        }
