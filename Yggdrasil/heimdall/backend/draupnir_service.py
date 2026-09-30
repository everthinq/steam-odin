"""Draupnir — portfolio tracker.

Portfolios hold buy/sell transactions for CS items. We track cost basis,
realized P/L (moving-average cost method, see :meth:`DraupnirService._replay_moving_average`)
and, using Huginn's live pulse prices, current market value and unrealized P/L.
State persists as a single JSON file, guarded by a lock.
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
from validation import normalize_datetime, validate_transaction

log = logging.getLogger(__name__)

PORTFOLIOS_FILE = os.path.join(os.path.dirname(__file__), 'portfolios.json')

# Spellings of one market typed into the platform field -> its one canonical,
# lower-case name. Keys are matched lower-cased with runs of whitespace
# collapsed. Genuinely different markets stay different: 'buff163_buy' (a buy
# order filled) is not 'buff163' (a listing bought), 'steam_buy' is not
# 'steam', 'dmarket_buy' is not 'dmarket', 'cs.money.market' is not
# 'cs.money.trade'. Anything not listed here — including free-text notes typed
# into the platform field, such as 'sent from hidey_spidey' — is kept exactly as
# typed. Bump PLATFORM_ALIASES_VERSION when this map changes so the stored
# transactions are migrated again on the next start.
PLATFORM_ALIASES = {
    # Seen in the stored transactions (2026-09-30).
    'buff': 'buff163',
    'avan.market': 'avanmarket',
    'halo': 'haloskins',
    'haloskins avg': 'haloskins',
    'tradeit.gg': 'tradeit',
    'sold to tradeit.gg': 'tradeit',
    'tradeon,market': 'tradeon.market',
    'bought at skin.land': 'skin.land',
    'sold to skin.land': 'skin.land',
    'csfloat after fees': 'csfloat',
    # Other spellings of the same markets, so new entries stay consistent.
    'buff.163': 'buff163',
    'buff 163': 'buff163',
    'avan market': 'avanmarket',
    'avan': 'avanmarket',
    'halo skins': 'haloskins',
    'haloskins.com': 'haloskins',
    'trade it': 'tradeit',
    'tradeon': 'tradeon.market',
    'tradeon market': 'tradeon.market',
    'skinland': 'skin.land',
    'skin land': 'skin.land',
    'cs float': 'csfloat',
    'csfloat.com': 'csfloat',
    'cs.money trade': 'cs.money.trade',
    'cs money trade': 'cs.money.trade',
    'csmoney trade': 'cs.money.trade',
    'csmoney.trade': 'cs.money.trade',
    'cs.money market': 'cs.money.market',
    'cs money market': 'cs.money.market',
    'csmoney market': 'cs.money.market',
    'csmoney.market': 'cs.money.market',
    'lis-skins': 'lisskins',
    'lis skins': 'lisskins',
    'lootfarm': 'loot.farm',
    'loot farm': 'loot.farm',
    'white.market': 'whitemarket',
    'white market': 'whitemarket',
    'swapgg': 'swap.gg',
    'aim market': 'aim.market',
}

# Canonical market names: typed in any letter case they are stored lower-case.
PLATFORM_CANONICAL_NAMES = frozenset(PLATFORM_ALIASES.values()) | {
    'buff163_buy', 'steam', 'steam_buy', 'dmarket', 'dmarket_buy', 'lisskins',
    'loot.farm', 'whitemarket', 'swap.gg', 'skinswap_cn', 'uuskins', 'skins',
    'aim.market',
}

# Stored in data['meta']['platform_aliases_version'] once the stored
# transactions have been normalized with this version of PLATFORM_ALIASES.
PLATFORM_ALIASES_VERSION = 1


def normalize_platform(raw):
    """The canonical name of a market typed into the platform field.

    A known alias ('buff', 'tradeit.gg', 'Halo') becomes its canonical
    lower-case name ('buff163', 'tradeit', 'haloskins'); a canonical name in
    any letter case becomes lower-case; anything else (a market we do not know,
    or a free-text note) is returned as typed, only trimmed."""
    text = (raw or '').strip()
    lookup = ' '.join(text.lower().split())
    if lookup in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[lookup]
    if lookup in PLATFORM_CANONICAL_NAMES:
        return lookup
    return text


def _chronological_key(transaction):
    """Sort key that replays transactions in the order they happened: by date,
    then by created_at for transactions entered on the same date. Python's sort
    is stable, so transactions with neither keep their list order."""
    return (transaction.get('date') or '', transaction.get('created_at') or '')


class PortfolioSaveError(RuntimeError):
    """portfolios.json could not be written. The change is NOT on disk; the
    in-memory store is rolled back to what the file holds whenever that file can
    be read, so a retry does not double-apply the change."""


class CsvImportError(ValueError):
    """A CSV import had no valid row, so nothing (not even an empty portfolio)
    was created. ``errors`` lists what was wrong, row by row."""

    def __init__(self, message, errors):
        super().__init__(message)
        self.errors = errors


def parse_store_bytes(data):
    """Parse and validate raw portfolio-store JSON (a backup being restored).

    Returns the parsed dict. Raises ValueError unless it is a JSON object whose
    ``portfolios`` is an object of portfolio objects, each with a
    ``transactions`` list — the shape every other method relies on."""
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode('utf-8')
        except UnicodeDecodeError as e:
            raise ValueError(f'backup is not UTF-8 text: {e}') from e
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f'backup is not valid JSON: {e}') from e
    if not isinstance(parsed, dict):
        raise ValueError('backup is not a JSON object')
    portfolios = parsed.get('portfolios')
    if not isinstance(portfolios, dict):
        raise ValueError("backup has no 'portfolios' object")
    for pid, portfolio in portfolios.items():
        if not isinstance(portfolio, dict) or not isinstance(portfolio.get('transactions'), list):
            raise ValueError(f'portfolio {pid!r} in the backup has no transaction list')
    return parsed


def _now():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _norm_date(raw):
    """Canonicalize an entered date/datetime; blank → today; keep an
    unparseable non-empty value as-is so hand-entered data is never dropped."""
    if not raw:
        return _today()
    return normalize_datetime(raw) or raw


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
    this keeps an exported CSV round-trippable through import. Real dollars keep
    4 decimals (the stored precision) so a sub-cent unit price survives."""
    v = (v or '').strip()
    if v in ('', 'N/A', 'n/a'):
        return None
    try:
        if '.' in v:
            return round(float(v), 4)
        return round(float(v) / 100.0, 2)
    except ValueError:
        return None


_TRUE_WORDS = ('1', 'true', 'yes', 'y')


def _strip_byte_order_mark(text):
    """Drop a leading UTF-8 byte order mark, whether it was decoded properly
    (U+FEFF) or as latin-1/cp1252 mojibake ('ï»¿'); otherwise the first header
    would start with an invisible U+FEFF and every row's name would look
    missing."""
    for mark in ('\ufeff', '\u00ef\u00bb\u00bf'):
        if text.startswith(mark):
            return text[len(mark):]
    return text


def _sniff_delimiter(text):
    """';' when the header line has more semicolons than commas (a CSV saved by
    a spreadsheet in a locale that uses the comma as the decimal separator),
    else ','."""
    header = text.split('\n', 1)[0]
    return ';' if header.count(';') > header.count(',') else ','


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
        # One-time normalization of the stored platform names. It runs here and
        # not in __init__ because it must take a backup snapshot first. It never
        # stops the app from starting.
        try:
            self.migrate_platform_aliases()
        except Exception as e:
            log.error('platform alias migration failed: %s', e)

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
        """Caller must hold self._lock. Raises :class:`PortfolioSaveError` when
        the file cannot be written, so the route answers 500 instead of reporting
        a change that exists only in memory."""
        self._store_gen += 1   # invalidate the memoized name set
        try:
            atomic_write_json(self.path, self._data, indent=2)
        except Exception as e:
            log.error('could not persist portfolios: %s', e)
            self._rollback_to_disk()
            raise PortfolioSaveError(f'could not save portfolios: {e}') from e
        # Snapshot the new state for point-in-time restore. Best-effort and
        # deduped by content hash — never lets a backup issue break the write.
        if self._backup is not None:
            self._backup.snapshot('change')

    def _rollback_to_disk(self):
        """After a failed write, put the in-memory store back to what the file
        holds, so memory never carries a change the caller was told failed.
        Only when the file exists and parses cleanly — otherwise memory is kept,
        because replacing it with an empty store would risk the next successful
        write wiping the book. Caller must hold self._lock."""
        try:
            with open(self.path) as f:
                on_disk = json.load(f)
        except (OSError, ValueError) as e:
            log.error('could not re-read portfolios after a failed save (%s); '
                      'keeping the in-memory store', e)
            return
        if isinstance(on_disk, dict) and isinstance(on_disk.get('portfolios'), dict):
            self._data = on_disk
            self._store_gen += 1

    def restore_bytes(self, data):
        """Replace the whole store with a backup's JSON, validated and persisted
        under the service lock.

        Doing it here (instead of overwriting the file and calling reload())
        means no concurrent write can slip in between and put the pre-restore
        state back. Raises ValueError for an invalid backup (store untouched) and
        PortfolioSaveError when the write fails (store left as it was)."""
        parsed = parse_store_bytes(data)
        with self._lock:
            previous = self._data
            self._data = parsed
            try:
                self._persist()
            except Exception:
                self._data = previous
                self._store_gen += 1
                raise
            self._last_load_corrupt = False

    # ---- platform alias migration ----------------------------------------

    def _backup_holds_current_file(self):
        """True when a backup snapshot holds exactly the bytes of the live file.
        Caller must hold self._lock."""
        try:
            with open(self.path, 'rb') as f:
                live = f.read()
        except OSError:
            return False
        for entry in self._backup.list_backups():   # newest first
            if self._backup.read_backup(entry['name']) == live:
                return True
        return False

    def migrate_platform_aliases(self):
        """Rewrite every stored transaction's platform through
        :func:`normalize_platform`, once per PLATFORM_ALIASES_VERSION.

        Safety, in order: it needs the backup service; it skips a store that was
        not read cleanly from the file (corrupt, recovered from a backup, or no
        file yet) so it can never write over data it did not load; it takes a
        'manual' backup snapshot and checks a snapshot holds the exact current
        file before changing anything; then it changes only the platform field
        and saves through :meth:`_persist` under the store lock. The version
        marker is stored in data['meta']['platform_aliases_version'].

        Returns ``{'old -> new': transactions changed}`` when it ran (empty when
        nothing needed changing), or None when it was not due or was skipped."""
        if self._backup is None:
            return None
        with self._lock:
            meta = self._data.get('meta')
            meta = meta if isinstance(meta, dict) else {}
            if meta.get('platform_aliases_version', 0) >= PLATFORM_ALIASES_VERSION:
                return None
            if getattr(self, '_last_load_corrupt', False):
                log.warning('platform alias migration skipped: the store did not load cleanly')
                return None
            try:
                with open(self.path) as f:
                    on_disk = json.load(f)
            except (OSError, ValueError):
                on_disk = None
            if on_disk != self._data:
                log.warning('platform alias migration skipped: memory does not match %s', self.path)
                return None
            self._backup.snapshot('manual')
            if not self._backup_holds_current_file():
                log.error('platform alias migration skipped: no verified backup snapshot')
                return None

            changed = {}
            for portfolio in self._data['portfolios'].values():
                for txn in portfolio['transactions']:
                    old = txn.get('platform') or ''
                    new = normalize_platform(old)
                    if new != old:
                        txn['platform'] = new
                        change = f'{old} -> {new}'
                        changed[change] = changed.get(change, 0) + 1
            self._data['meta'] = {**meta, 'platform_aliases_version': PLATFORM_ALIASES_VERSION}
            self._persist()   # on a failed write it rolls memory back and raises
            log.info('platform alias migration version %s: %s transactions changed %s',
                     PLATFORM_ALIASES_VERSION, sum(changed.values()), changed)
            return changed

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
            # One canonical name per market ('buff' -> 'buff163'); free text
            # and unknown markets are kept as typed. See normalize_platform.
            'platform': normalize_platform(raw.get('platform')),
            # Normalize whatever date/datetime shape was entered into the canonical
            # stored form (YYYY-MM-DD, or YYYY-MM-DDThh:mm:ss when a time is given).
            # An unparseable non-empty value is kept as-is rather than dropped;
            # a blank date (manual quick-add) defaults to today so the row sorts
            # to the top with the other recent entries instead of the bottom.
            'date': _norm_date((raw.get('date') or '').strip()),
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
    def _parse_csv_rows(text):
        """Parse CSV text into ``[(line_number, transaction_dict), ...]``.

        Handles the integer-cents 'Unit Price' quirk (no decimal point), mojibake
        item names, a UTF-8 byte order mark and ';' as the delimiter. Unknown
        columns are ignored. Reads our own export's 'Arbitrage' and 'Created At'
        columns back, so export -> import round-trips; older files without them
        import as before."""
        text = _strip_byte_order_mark(text or '')
        reader = csv.DictReader(io.StringIO(text), delimiter=_sniff_delimiter(text))
        # Our own export (it has the 'Created At' column) writes every field
        # where it belongs, so the price-tracker "market in Note" repair below
        # must not move a note into an empty platform.
        own_export = 'Created At' in (reader.fieldnames or [])
        rows = []
        for row in reader:
            name = _demojibake((row.get('Name') or '').strip())
            if not name:
                continue
            raw_unit_price = (row.get('Unit Price') or '').strip()
            price = _cents_to_usd(raw_unit_price)
            if price is None:
                # fall back to Total / Quantity if unit price is missing
                total = _cents_to_usd(row.get('Total Price'))
                try:
                    q = float(row.get('Quantity') or 1)
                except ValueError:
                    q = 1
                if total is not None and q:
                    price = round(total / q, 4)
                elif raw_unit_price not in ('', 'N/A', 'n/a'):
                    price = raw_unit_price   # unparseable: let validation report it
                else:
                    price = 0.0
            platform = (row.get('Marketplace') or '').strip()
            note = _demojibake((row.get('Note') or '').strip())
            if not own_export and platform in ('', 'N/A') and note:
                platform = note  # some rows put the real market in Note
            txn = {
                'item_name': name,
                'type': row.get('Type'),
                'qty': row.get('Quantity'),
                'price': price,
                'platform': platform if platform != 'N/A' else '',
                'date': (row.get('Date') or '').strip(),
                'note': note if note != 'N/A' else '',
                # The price tracker writes 'N/A' for no fee; treat it (and blank) as 0.
                'fee_percent': (0 if (row.get('Fee Percentage') or '').strip() in ('', 'N/A', 'n/a')
                                else row.get('Fee Percentage')),
                'is_arbitrage': (row.get('Arbitrage') or '').strip().lower() in _TRUE_WORDS,
            }
            created_at = (row.get('Created At') or '').strip()
            if created_at:
                txn['created_at'] = created_at
            rows.append((reader.line_num, txn))
        return rows

    @staticmethod
    def parse_csv(text):
        """Parse a price-tracker (or our own exported) CSV into a list of
        transaction dicts. See :meth:`_parse_csv_rows`."""
        return [txn for _, txn in DraupnirService._parse_csv_rows(text)]

    def import_csv(self, text, name=None, pid=None):
        """Import a CSV into a new portfolio (default) or append to `pid`.

        Every row goes through the same validation as a hand-entered
        transaction; invalid rows are skipped and reported. Returns
        ``(portfolio, imported_count, errors)`` — ``(None, 0, [])`` when `pid`
        does not exist. Raises :class:`CsvImportError` when no row is valid,
        before anything (not even a new portfolio) is created."""
        txns, errors = [], []
        for line_number, raw in self._parse_csv_rows(text):
            row_errors = validate_transaction(raw)
            if row_errors:
                errors.append(f"line {line_number} ({raw['item_name']}): "
                              + '; '.join(row_errors))
                continue
            txns.append(self._clean_txn(raw))
        with self._lock:
            if pid:
                p = self._get(pid)
                if not p:
                    return None, 0, []
            if not txns:
                raise CsvImportError('no valid rows to import', errors)
            if not pid:
                pid = _new_id()
                p = {
                    'id': pid, 'name': (name or 'Imported').strip() or 'Imported',
                    'created_at': _now(), 'updated_at': _now(), 'transactions': [],
                }
                self._data['portfolios'][pid] = p
            p['transactions'].extend(txns)
            p['updated_at'] = _now()
            self._persist()
            return p, len(txns), errors

    # ---- CSV export --------------------------------------------------------

    EXPORT_COLUMNS = ['Name', 'Type', 'Quantity', 'Unit Price', 'Total Price',
                      'Marketplace', 'Date', 'Note', 'Fee Percentage',
                      'Arbitrage', 'Created At']

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
                # 4 decimals = the stored precision (a sub-cent unit price of a
                # bulk case buy would otherwise lose money on re-import).
                'Unit Price': f'{price:.4f}',
                'Total Price': f'{qty * price:.2f}',
                'Marketplace': t.get('platform', ''),
                'Date': t.get('date', ''),
                'Note': t.get('note', ''),
                'Fee Percentage': t.get('fee_percent', 0) or 0,
                'Arbitrage': 'true' if t.get('is_arbitrage') else 'false',
                'Created At': t.get('created_at', ''),
            })
        return name, buf.getvalue()

    # ---- valuation / aggregation ------------------------------------------

    @staticmethod
    def _replay_moving_average(txns):
        """Replay transactions in date order (created_at breaks a tie) with a
        moving-average cost per item. No fee is applied.

        * A buy moves the average: (held cost + qty * price) / (held qty + qty).
          When nothing is held the average simply becomes the buy price.
        * A sell costs the current average per unit sold (realized profit =
          (price - average) * qty) and lowers the held quantity; the average
          itself does not move, so held cost stays average * held quantity.
        * A sell of more than is held (a missing buy, or selling the copy you
          have and buying it back later) is charged the current average for the
          units not held, provisionally. The next buys first cover those units
          and replace the provisional cost with what was really paid, so a
          sell-then-rebuy books the real spread. Units never covered keep the
          provisional cost (the old all-time method did the same).

        Returns ``(items, sell_costs)``: ``items`` is {item_name: {'held_qty',
        'avg_cost'}}, where ``avg_cost`` is the moving average of what is held —
        once everything is sold it stays at the last average, a sell never
        moves it; ``sell_costs`` is {index in txns: cost of that sell's units}."""
        items = {}
        sell_costs = {}
        order = sorted(range(len(txns)), key=lambda index: _chronological_key(txns[index]))
        for index in order:
            txn = txns[index]
            state = items.setdefault(txn['item_name'], {
                'held_qty': 0, 'avg_cost': 0.0,
                'uncovered': [],   # [[qty not yet covered, cost charged per unit, sell index], ...]
            })
            qty, price = txn['qty'], txn['price']
            if txn['type'] == 'sell':
                sell_costs[index] = state['avg_cost'] * qty
                not_held = qty - max(state['held_qty'], 0)
                if not_held > 0:
                    state['uncovered'].append([not_held, state['avg_cost'], index])
                state['held_qty'] -= qty
                continue
            remaining = qty
            while remaining > 0 and state['uncovered']:
                short = state['uncovered'][0]
                covered = min(remaining, short[0])
                sell_costs[short[2]] += (price - short[1]) * covered
                short[0] -= covered
                remaining -= covered
                if short[0] <= 0:
                    state['uncovered'].pop(0)
            if state['held_qty'] > 0:
                held_cost = state['avg_cost'] * state['held_qty']
                state['avg_cost'] = (held_cost + qty * price) / (state['held_qty'] + qty)
            else:
                state['avg_cost'] = price
            state['held_qty'] += qty
        return items, sell_costs

    @staticmethod
    def _holdings(txns, prices):
        """Aggregate transactions per item into holdings with cost basis, P/L
        and (if a price is known) current value. `prices` is {name: usd} or None.

        Cost basis and realized P/L come from the moving-average replay
        (:meth:`_replay_moving_average`); ``avg_cost`` is the moving average of
        the units held (the last average once the item is fully sold)."""
        prices = prices or {}
        replay, sell_costs = DraupnirService._replay_moving_average(txns)
        by_item = {}
        for index, t in enumerate(txns):
            h = by_item.setdefault(t['item_name'], {
                'item_name': t['item_name'], 'buy_qty': 0, 'buy_cost': 0.0,
                'sell_qty': 0, 'sell_proceeds': 0.0, '_realized': 0.0,
            })
            total = t['qty'] * t['price']
            if t['type'] == 'sell':
                h['sell_qty'] += t['qty']
                h['sell_proceeds'] += total
                h['_realized'] += total - sell_costs[index]
            else:
                h['buy_qty'] += t['qty']
                h['buy_cost'] += total

        holdings = []
        for h in by_item.values():
            realized = h.pop('_realized')
            avg_cost = replay[h['item_name']]['avg_cost']
            net_qty = h['buy_qty'] - h['sell_qty']
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
                # More sold than bought (a missing buy, or a typo). The P/L math
                # above is unchanged; this only flags it for the user to fix.
                'oversold': net_qty < 0,
                'oversold_qty': max(-net_qty, 0),
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

    def _collection_map(self):
        """Best-effort ``{item_name: collection}`` built from Huginn's cached
        inventory scan. That scan is the *only* place collections exist — a
        hand-entered Draupnir transaction carries just a name — so an item not
        currently sitting in any account's inventory (e.g. already sold) simply
        gets no collection. Returns ``{}`` when Huginn is unwired or no scan has
        been run yet, which leaves every item's collection blank and the UI's
        collection filter empty/disabled (same behaviour as Huginn Arbitrage)."""
        if not self.huginn:
            return {}
        try:
            cache = self.huginn.get_cache()
        except Exception:
            return {}
        by_hash = (cache or {}).get('by_hash') or {}
        cmap = {}
        for name, entry in by_hash.items():
            for inst in (entry.get('instances') or []):
                collection = (inst.get('collection') or '').strip()
                if collection:
                    cmap[name] = collection
                    break
        return cmap

    @staticmethod
    def _attach_collections(items, cmap):
        """Decorate each holding/transaction dict with a ``collection`` string
        (empty when unknown), looked up by ``item_name``. Purely additive — it
        never reads or changes cost basis, quantities or P/L."""
        for item in items:
            item['collection'] = cmap.get(item.get('item_name', ''), '')
        return items

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
        # Newest first, with created_at as a tiebreaker so a just-added
        # transaction always appears at the top of its date.
        txns = sorted(p['transactions'],
                      key=lambda t: (t.get('date') or '', t.get('created_at') or ''),
                      reverse=True)
        cmap = self._collection_map()
        self._attach_collections(holdings, cmap)
        self._attach_collections(txns, cmap)
        return {
            **self._summarize(p, holdings),
            'arbitrage_count': sum(1 for t in p['transactions'] if t.get('is_arbitrage')),
            'transactions': txns,
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
                        'oversold_qty': 0, 'sold_avg_cost_total': 0.0,
                        'cost_basis': 0.0, 'realized_pl': 0.0,
                        'current_price': None, 'market_value': None, 'unrealized_pl': None,
                    }
                m['buy_qty'] += h['buy_qty']
                m['buy_cost'] += h['buy_cost']
                m['sell_qty'] += h['sell_qty']
                m['sell_proceeds'] += h['sell_proceeds']
                m['sold_avg_cost_total'] += h['avg_cost'] * h['sell_qty']
                # Only what an account really holds adds to the combined
                # quantity: one account's oversold (negative) position must not
                # cancel units another account still has. The oversold amount is
                # carried separately as a flag; realized P/L still sums as-is.
                m['net_qty'] += max(h['net_qty'], 0)
                m['oversold_qty'] += max(-h['net_qty'], 0)
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
            # Held avg cost keeps avg_cost × net_qty == cost_basis in the table.
            # For a fully sold item: the accounts' last moving averages, weighted
            # by how many units each account sold (one account: its own).
            if net > 0:
                avg_cost = m['cost_basis'] / net
            elif m['sell_qty']:
                avg_cost = m['sold_avg_cost_total'] / m['sell_qty']
            else:
                avg_cost = 0.0
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
                'oversold': m['oversold_qty'] > 0,
                'oversold_qty': m['oversold_qty'],
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
        sorted_txns = sorted(txns,
                             key=lambda t: (t.get('date') or '', t.get('created_at') or ''),
                             reverse=True)
        cmap = self._collection_map()
        self._attach_collections(holdings, cmap)
        self._attach_collections(sorted_txns, cmap)
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
            'transactions': sorted_txns,
            'holdings': holdings,
            'account_count': len(ps),
            'arbitrage_count': arb,
        }

    def open_lots(self):
        """Every unit still held, as the purchases it came from — no averaging:
        [{portfolio_id, account, item_name, price, qty, platform, last_buy_date}].
        Per account (accounts are separate books), arbitrage legs left out. Sells
        use up the OLDEST buys first (first in, first out), since the ledger does
        not record which copy was sold. What is left is grouped by item, unit
        price and platform as entered, so two buys of 50 at $4.00 on buff163_buy
        show as one lot of 100; `last_buy_date` is the newest buy in the lot (a
        hint for Steam's 7-day trade protection). Read-only."""
        with self._lock:
            portfolios = json.loads(json.dumps(list(self._data['portfolios'].values())))
        lots_out = []
        for portfolio in portfolios:
            transactions = sorted(self._non_arb(portfolio['transactions']),
                                  key=lambda txn: (txn.get('date') or '', txn.get('created_at') or ''))
            queues = {}      # item -> [[qty_left, price, platform, date], ...] oldest first
            # Units sold before any recorded buy (the original buy was never entered):
            # the next buys cover them first, matching the moving-average replay, so
            # an item whose net quantity is zero never shows up as a held lot.
            uncovered = {}   # item -> units sold with no earlier buy
            for txn in transactions:
                item = txn['item_name']
                queue = queues.setdefault(item, [])
                if txn['type'] != 'sell':
                    qty = txn['qty']
                    covered = min(qty, uncovered.get(item, 0))
                    if covered:
                        uncovered[item] -= covered
                        qty -= covered
                    if qty > 0:
                        queue.append([qty, txn['price'], (txn.get('platform') or '').strip(), txn.get('date') or ''])
                    continue
                left = txn['qty']
                while left > 0 and queue:
                    used = min(left, queue[0][0])
                    queue[0][0] -= used
                    left -= used
                    if queue[0][0] <= 0:
                        queue.pop(0)
                if left > 0:
                    uncovered[item] = uncovered.get(item, 0) + left
            for item, queue in queues.items():
                grouped = {}
                for qty, price, platform, date in queue:
                    lot = grouped.setdefault((round(price, 4), platform), {
                        'portfolio_id': portfolio['id'], 'account': portfolio['name'], 'item_name': item,
                        'price': round(price, 4), 'qty': 0, 'platform': platform, 'last_buy_date': ''})
                    lot['qty'] += qty
                    lot['last_buy_date'] = max(lot['last_buy_date'], date)
                lots_out.extend(sorted(grouped.values(), key=lambda lot: lot['price']))
        return lots_out

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

        Realized profit uses the same moving-average method as the rest of
        Draupnir (:meth:`_replay_moving_average`), applied to the tagged subset
        pooled across accounts in date order: buys move the average, sells
        realize the spread against it, and a sell made before its rebuy is
        costed at what the rebuy paid. Cross-account and cross-date pairs fall
        out naturally, so we don't try to match individual buy↔sell legs.

        Every SELL leg is split into a category by where it settled: `steam`
        (locked wallet money) vs `market` (real, withdrawable cash). Realized P/L is
        additive across sell legs, so each category's total is exact; the shared
        moving-average basis is pooled across all tagged buys of an item. An
        item row's ``avg_cost`` is the average cost of the units it sold. Steam profit is
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
        _, sell_costs = self._replay_moving_average(txns)

        # Bucket each sell leg into steam vs market and tally per-item within each.
        cats = {'market': {'realized_pl': 0.0, 'closed_deals': 0, 'units_flipped': 0,
                           'cost_of_sold': 0.0, 'proceeds': 0.0, '_items': {}},
                'steam':  {'realized_pl': 0.0, 'closed_deals': 0, 'units_flipped': 0,
                           'cost_of_sold': 0.0, 'proceeds': 0.0, '_items': {}}}
        for index, t in enumerate(txns):
            if t['type'] != 'sell':
                continue
            cat = cats['steam' if self._is_steam(t.get('platform')) else 'market']
            item, qty, price = t['item_name'], t['qty'], t['price']
            cost, proc = sell_costs[index], price * qty
            rp = proc - cost
            cat['realized_pl'] += rp
            cat['closed_deals'] += 1
            cat['units_flipped'] += qty
            cat['cost_of_sold'] += cost
            cat['proceeds'] += proc
            d = cat['_items'].setdefault(item, {'item_name': item, 'sell_qty': 0, 'avg_cost': 0.0,
                                                'cost_of_sold': 0.0, 'proceeds': 0.0, 'realized_pl': 0.0})
            d['sell_qty'] += qty
            d['cost_of_sold'] += cost
            d['proceeds'] += proc
            d['realized_pl'] += rp

        def _finish(cat):
            rows = []
            for d in cat['_items'].values():
                d['avg_cost'] = round(d['cost_of_sold'] / d['sell_qty'], 4) if d['sell_qty'] else 0.0
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
        for index, t in enumerate(txns):
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
                'realized_pl': round(price * qty - sell_costs[index], 2) if is_sell else None,
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
