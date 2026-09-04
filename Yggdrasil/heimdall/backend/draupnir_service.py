"""Draupnir — portfolio tracker.

Portfolios hold buy/sell transactions for CS items. We track cost basis,
realized P/L (avg-cost method) and, using Huginn's live pulse prices,
current market value and unrealized P/L. State persists as a single JSON
file (gitignored — it's personal holdings data), guarded by a lock.
"""
import csv
import io
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone

from jsonio import atomic_write_json

log = logging.getLogger(__name__)

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


class DraupnirService:
    def __init__(self, huginn_service=None, path=PORTFOLIOS_FILE):
        self.huginn = huginn_service
        self.path = path
        self._lock = threading.Lock()
        # Bumped whenever the store changes, so all_item_names() (hit per typeahead
        # keystroke) can memoize its name set instead of rescanning every transaction.
        self._store_gen = 0
        self._names_cache = None   # (gen, set)
        # Optional BackupService, wired by app.py via set_backup(). Every
        # persisted change is snapshotted for point-in-time restore (deduped by
        # content hash), and it doubles as the corruption-recovery source in
        # _load(). Set before _load() so recovery is available on first read.
        self._backup = None
        self._data = self._load()

    def set_backup(self, backup_service):
        self._backup = backup_service
        # If the boot load hit a corrupt file before the backup was available,
        # retry recovery now that we can read snapshots.
        if getattr(self, '_last_load_corrupt', False):
            with self._lock:
                self._data = self._load()
                self._store_gen += 1

    def reload(self):
        """Re-read the source file into memory — used after a backup restore
        overwrites portfolios.json underneath us."""
        with self._lock:
            self._data = self._load()
            self._store_gen += 1   # store replaced → invalidate the memoized name set

    # ---- persistence -------------------------------------------------------

    def _load(self):
        """Read the store, self-healing from the newest good backup if the live
        file is corrupt (rather than silently starting empty).

        Sets ``self._last_load_corrupt`` so :meth:`set_backup` can retry the
        recovery once the backup service is wired (the boot load runs before
        it). A *missing* file is a legitimately-empty install, not corruption."""
        self._last_load_corrupt = False
        if not os.path.exists(self.path):
            return {'portfolios': {}}
        try:
            with open(self.path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault('portfolios', {})
                return data
            log.error('portfolios.json did not parse to an object')
        except (json.JSONDecodeError, OSError, ValueError) as e:
            log.error('portfolios.json unreadable: %s', e)

        # Existed but did not parse -> corruption. Try the newest good backup.
        recovered = self._recover_from_backup()
        if isinstance(recovered, dict):
            recovered.setdefault('portfolios', {})
            return recovered
        self._last_load_corrupt = True  # couldn't recover yet; retry after wiring
        return {'portfolios': {}}

    def _recover_from_backup(self):
        """Recovery hook for :func:`read_json`: parse the newest snapshot whose
        JSON is intact. Returns a dict, or None if no usable backup exists."""
        if self._backup is None:
            return None
        try:
            for entry in self._backup.list_backups():  # newest-first
                raw = self._backup.read_backup(entry['name'])
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(parsed, dict):
                    log.warning('recovered portfolios from backup %s',
                                entry['name'])
                    return parsed
        except Exception as e:
            log.error('backup recovery failed: %s', e)
        return None

    def _persist(self):
        """Caller must hold self._lock."""
        self._store_gen += 1   # invalidate the memoized name set
        try:
            atomic_write_json(self.path, self._data, indent=2)
        except Exception as e:
            log.error('could not persist portfolios: %s', e)
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
            # Marks a leg of an arbitrage deal: buy cheap on one market, sell dear
            # on another (may span your own accounts). Counted and valued by the
            # Arbitrage tab; included in the combined ledger as real profit.
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
        with self._lock:
            gen = self._store_gen
            if self._names_cache is not None and self._names_cache[0] == gen:
                # Callers only ever union this set, never mutate it.
                return self._names_cache[1]
            names = set()
            for p in self._data['portfolios'].values():
                for t in p['transactions']:
                    if t.get('item_name'):
                        names.add(t['item_name'])
            self._names_cache = (gen, names)
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

    @staticmethod
    def _non_arb(txns):
        """Transactions with arbitrage-tagged legs dropped. Arbitrage is a
        cross-account strategy tracked on its own tab, so it's kept out of a single
        account's holdings and P/L (which should reflect that account's own
        inventory and trading, not flips that merely passed through it)."""
        return [t for t in txns if not t.get('is_arbitrage')]

    def list_portfolios(self, prices=None):
        with self._lock:
            ps = list(self._data['portfolios'].values())
        out = []
        for p in ps:
            holdings = self._holdings(self._non_arb(p['transactions']), prices)
            s = self._summarize(p, holdings)
            s['arbitrage_count'] = sum(1 for t in p['transactions'] if t.get('is_arbitrage'))
            out.append(s)
        out.sort(key=lambda s: s['created_at'])
        return out

    def get_portfolio(self, pid, prices=None):
        with self._lock:
            p = self._get(pid)
            if not p:
                return None
            p = json.loads(json.dumps(p))  # snapshot under lock
        # P/L and holdings exclude arbitrage legs, but the transaction list below
        # still shows every leg (tagged ones carry the "arb" badge).
        holdings = self._holdings(self._non_arb(p['transactions']), prices)
        return {
            **self._summarize(p, holdings),
            'arbitrage_count': sum(1 for t in p['transactions'] if t.get('is_arbitrage')),
            # Newest first, with created_at as a tiebreaker so a just-added
            # transaction always appears at the top of its date.
            'transactions': sorted(p['transactions'],
                                   key=lambda t: (t.get('date') or '', t.get('created_at') or ''),
                                   reverse=True),
            'holdings': holdings,
        }

    def combined_ledger(self, prices=None):
        """One ledger across ALL accounts. Accounts are physically separate books —
        a non-arbitrage skin bought on one account is sold from that same account —
        so cost basis must NOT be blended across accounts. We therefore compute each
        account's holdings with its own avg cost (exactly as the per-account view
        does) and ADD them up. Combined P/L is thus the exact sum of the accounts.

        Arbitrage legs (is_arbitrage) are excluded from P/L and holdings — arbitrage
        is a separate strategy on its own tab — but the transaction list still shows
        every leg, tagged with its account (arbitrage legs badged)."""
        with self._lock:
            ps = json.loads(json.dumps(list(self._data['portfolios'].values())))

        txns = []            # all legs (incl. arbitrage) for the transaction list
        merged = {}          # item_name -> additive aggregate of per-account holdings
        for p in ps:
            for t in p['transactions']:
                txns.append({**t, 'account': p['name'], 'portfolio_id': p['id']})
            # Per-account holdings (own avg cost), then merge additively by item.
            for h in self._holdings(self._non_arb(p['transactions']), prices):
                m = merged.get(h['item_name'])
                if m is None:
                    m = merged[h['item_name']] = {
                        'item_name': h['item_name'], 'buy_qty': 0, 'buy_cost': 0.0,
                        'sell_qty': 0, 'sell_proceeds': 0.0, 'net_qty': 0,
                        'cost_basis': 0.0, 'realized_pl': 0.0,
                        'current_price': None, 'market_value': None, 'unrealized_pl': None,
                    }
                m['buy_qty'] += h['buy_qty']
                m['buy_cost'] += h['buy_cost']
                m['sell_qty'] += h['sell_qty']
                m['sell_proceeds'] += h['sell_proceeds']
                m['net_qty'] += h['net_qty']
                m['cost_basis'] += h['cost_basis']
                m['realized_pl'] += h['realized_pl']
                if h['current_price'] is not None:
                    m['current_price'] = h['current_price']
                if h['market_value'] is not None:
                    m['market_value'] = (m['market_value'] or 0.0) + h['market_value']
                if h['unrealized_pl'] is not None:
                    m['unrealized_pl'] = (m['unrealized_pl'] or 0.0) + h['unrealized_pl']

        holdings = []
        for m in merged.values():
            net, buy_qty = m['net_qty'], m['buy_qty']
            # Held avg cost keeps avg_cost × net_qty == cost_basis in the table;
            # for fully-sold items fall back to the pooled buy average.
            avg_cost = (m['cost_basis'] / net) if net > 0 else ((m['buy_cost'] / buy_qty) if buy_qty else 0.0)
            mv = m['market_value']
            holdings.append({
                'item_name': m['item_name'],
                'buy_qty': buy_qty, 'buy_cost': round(m['buy_cost'], 2),
                'sell_qty': m['sell_qty'], 'sell_proceeds': round(m['sell_proceeds'], 2),
                'avg_cost': round(avg_cost, 4),
                'net_qty': net,
                'cost_basis': round(m['cost_basis'], 2),
                'current_price': m['current_price'],
                'market_value': round(mv, 2) if mv is not None else None,
                'realized_pl': round(m['realized_pl'], 2),
                'unrealized_pl': round(m['unrealized_pl'], 2) if m['unrealized_pl'] is not None else None,
            })
        holdings.sort(key=lambda x: (x['market_value'] or x['cost_basis'] or 0), reverse=True)

        # Summary from the merged (already per-account-correct) holdings.
        invested = sum(h['buy_cost'] for h in holdings)
        cost_basis = sum(h['cost_basis'] for h in holdings)
        current_value = sum(h['market_value'] if h['market_value'] is not None else h['cost_basis'] for h in holdings)
        realized = sum(h['realized_pl'] for h in holdings)
        unrealized = sum(h['unrealized_pl'] or 0 for h in holdings)
        priced = any(h['current_price'] is not None for h in holdings)
        held = [h for h in holdings if h['net_qty'] > 0]
        arb = sum(1 for t in txns if t.get('is_arbitrage'))
        return {
            'id': 'combined', 'name': 'All accounts', 'created_at': '', 'updated_at': '',
            'txn_count': len(txns),
            'holdings_count': len(held),
            'unpriced_count': sum(1 for h in held if h['current_price'] is None),
            'invested': round(invested, 2),
            'cost_basis': round(cost_basis, 2),
            'current_value': round(current_value, 2) if priced else None,
            'realized_pl': round(realized, 2),
            'unrealized_pl': round(unrealized, 2) if priced else None,
            'total_pl': round(realized + unrealized, 2) if priced else round(realized, 2),
            'priced': priced,
            'transactions': sorted(txns,
                                   key=lambda t: (t.get('date') or '', t.get('created_at') or ''),
                                   reverse=True),
            'holdings': holdings,
            'account_count': len(ps),
            'arbitrage_count': arb,
        }

    @staticmethod
    def _is_steam(platform):
        """True if a platform is Steam. Steam balance is locked wallet money, not
        withdrawable cash, so its arbitrage is tracked as a separate category rather
        than mixed into the real-cash total."""
        return 'steam' in (platform or '').strip().lower()

    def arbitrage_deals(self, prices=None):
        """Count and value your tagged arbitrage deals (transactions flagged
        is_arbitrage), pooled across ALL accounts — a play can source on one
        account/market and sell on another, so it's never scoped to one account.

        Realized profit uses the same avg-cost method as the rest of Draupnir,
        applied to the tagged subset: buys build the cost basis, sells realize the
        spread. Cross-account and cross-date pairs fall out naturally, so we don't
        try to match individual buy↔sell legs.

        Every SELL leg is split into a category by where it settled: `steam`
        (locked wallet money) vs `market` (real, withdrawable cash). Realized P/L is
        additive across sell legs, so each category's total is exact; the shared
        avg-cost basis is pooled across all tagged buys of an item. Steam profit is
        counted at face value but kept in its own bucket so it's never confused with
        real cash.

        `prices` is an optional {item_name: usd} map, used only to value tagged
        inventory that's still open (bought to flip, not yet sold)."""
        with self._lock:
            ps = json.loads(json.dumps(list(self._data['portfolios'].values())))
        txns = []
        accounts = set()
        open_buys = 0
        for p in ps:
            for t in p['transactions']:
                if not t.get('is_arbitrage'):
                    continue
                txns.append({**t, 'account': p['name']})
                accounts.add(p['name'])
                if t['type'] != 'sell':
                    open_buys += 1

        holdings = self._holdings(txns, prices)
        avg_cost = {h['item_name']: h['avg_cost'] for h in holdings}

        # Bucket each sell leg into steam vs market and tally per-item within each.
        cats = {'market': {'realized_pl': 0.0, 'closed_deals': 0, 'units_flipped': 0,
                           'cost_of_sold': 0.0, 'proceeds': 0.0, '_items': {}},
                'steam':  {'realized_pl': 0.0, 'closed_deals': 0, 'units_flipped': 0,
                           'cost_of_sold': 0.0, 'proceeds': 0.0, '_items': {}}}
        for t in txns:
            if t['type'] != 'sell':
                continue
            cat = cats['steam' if self._is_steam(t.get('platform')) else 'market']
            item, qty, price = t['item_name'], t['qty'], t['price']
            ac = avg_cost.get(item, 0.0)
            cost, proc = ac * qty, price * qty
            rp = proc - cost
            cat['realized_pl'] += rp
            cat['closed_deals'] += 1
            cat['units_flipped'] += qty
            cat['cost_of_sold'] += cost
            cat['proceeds'] += proc
            d = cat['_items'].setdefault(item, {'item_name': item, 'sell_qty': 0, 'avg_cost': ac,
                                                'cost_of_sold': 0.0, 'proceeds': 0.0, 'realized_pl': 0.0})
            d['sell_qty'] += qty
            d['cost_of_sold'] += cost
            d['proceeds'] += proc
            d['realized_pl'] += rp

        def _finish(cat):
            rows = []
            for d in cat['_items'].values():
                d['cost_of_sold'] = round(d['cost_of_sold'], 2)
                d['proceeds'] = round(d['proceeds'], 2)
                d['realized_pl'] = round(d['realized_pl'], 2)
                d['margin_pct'] = round(d['realized_pl'] / d['cost_of_sold'] * 100, 2) if d['cost_of_sold'] else None
                rows.append(d)
            rows.sort(key=lambda r: r['realized_pl'], reverse=True)
            cost = round(cat['cost_of_sold'], 2)
            realized = round(cat['realized_pl'], 2)
            return {
                'realized_pl': realized,
                'closed_deals': cat['closed_deals'],
                'units_flipped': cat['units_flipped'],
                'cost_of_sold': cost,
                'proceeds': round(cat['proceeds'], 2),
                'avg_margin_pct': round(realized / cost * 100, 2) if cost else None,
                'items': len(rows),
                'rows': rows,
            }
        market, steam = _finish(cats['market']), _finish(cats['steam'])

        # Open inventory (bought to flip, not yet sold) — category-agnostic.
        open_units = open_cost = open_value = open_priced_cost = 0.0
        for h in holdings:
            oq = max(h['net_qty'], 0)
            if oq <= 0:
                continue
            open_units += oq
            open_cost += h['cost_basis']
            if h['market_value'] is not None:
                open_value += h['market_value']
                open_priced_cost += h['cost_basis']
        priced = any(h['current_price'] is not None for h in holdings)
        open_unrealized = round(open_value - open_priced_cost, 2)

        # Per-leg ledger: every tagged transaction with account, date and category.
        legs = []
        for t in txns:
            price, qty = t['price'], t['qty']
            is_sell = t['type'] == 'sell'
            st = self._is_steam(t.get('platform'))
            legs.append({
                'id': t.get('id'),
                'item_name': t['item_name'],
                'account': t['account'],
                'date': t.get('date', ''),
                'type': t['type'],
                'qty': qty,
                'price': round(price, 4),
                'total': round(price * qty, 2),
                'platform': t.get('platform', ''),
                'steam': st,
                'category': ('steam' if st else 'market') if is_sell else None,
                'note': t.get('note', ''),
                'realized_pl': (round((price - avg_cost.get(t['item_name'], 0.0)) * qty, 2)
                                if is_sell else None),
            })
        legs.sort(key=lambda l: l['date'], reverse=True)

        return {
            'account_count': len(ps),
            'accounts_used': sorted(accounts),
            'items': len(holdings),
            'open_buys': open_buys,
            'closed_deals': market['closed_deals'] + steam['closed_deals'],
            'units_flipped': market['units_flipped'] + steam['units_flipped'],
            'realized_pl': round(market['realized_pl'] + steam['realized_pl'], 2),
            'market': market,
            'steam': steam,
            'open_units': int(open_units),
            'open_cost': round(open_cost, 2),
            'open_value': round(open_value, 2) if priced else None,
            'open_unrealized': open_unrealized if priced else None,
            'priced': priced,
            'legs': legs,
        }
