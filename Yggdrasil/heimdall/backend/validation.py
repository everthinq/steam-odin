"""Request-body validation for the Draupnir write endpoints (fix #5).

The store is hand-entered, irreplaceable data, and the normalizer
(``DraupnirService._clean_txn``) *silently* coerces bad input — ``price="abc"``
becomes ``0.0``, a garbage ``qty`` becomes ``1`` — which corrupts P/L without
telling anyone. These validators reject clearly-invalid payloads at the API
boundary so the caller gets a 400 instead of a quietly-wrong ledger.

Hand-rolled (no schema dependency) to match the project's lean stack. Each
function returns a list of human-readable error strings; empty means valid.
"""
from datetime import datetime

MAX_NAME_LEN = 120
MAX_ITEM_LEN = 200
MAX_QTY = 10_000_000
MAX_PRICE = 1_000_000.0


def validate_portfolio_name(name, *, required=True):
    errors = []
    if name is None or (isinstance(name, str) and not name.strip()):
        if required:
            errors.append('name is required')
        return errors
    if not isinstance(name, str):
        errors.append('name must be a string')
    elif len(name) > MAX_NAME_LEN:
        errors.append(f'name must be at most {MAX_NAME_LEN} characters')
    return errors


def validate_transaction(body, *, partial=False):
    """Validate a transaction payload. ``partial=True`` (PATCH) only checks the
    fields that are present; a full add requires ``item_name``."""
    errors = []
    if not isinstance(body, dict):
        return ['request body must be a JSON object']

    # item_name — required on create, optional on partial update.
    name = body.get('item_name')
    if name is None:
        if not partial:
            errors.append('item_name is required')
    elif not isinstance(name, str) or not name.strip():
        errors.append('item_name must be a non-empty string')
    elif len(name) > MAX_ITEM_LEN:
        errors.append(f'item_name must be at most {MAX_ITEM_LEN} characters')

    # type — optional; must be buy/sell if present.
    if 'type' in body and body['type'] is not None:
        if str(body['type']).strip().lower() not in ('buy', 'sell'):
            errors.append("type must be 'buy' or 'sell'")

    # qty — optional; positive integer if present.
    qty = body.get('qty', body.get('quantity'))
    if qty is not None and qty != '':
        iv = _as_int(qty)
        if iv is None:
            errors.append('qty must be a whole number')
        elif iv < 1:
            errors.append('qty must be at least 1')
        elif iv > MAX_QTY:
            errors.append(f'qty must be at most {MAX_QTY}')

    # price — optional; non-negative number if present.
    price = body.get('price')
    if price is not None and price != '':
        fv = _as_float(price)
        if fv is None:
            errors.append('price must be a number')
        elif fv < 0:
            errors.append('price must not be negative')
        elif fv > MAX_PRICE:
            errors.append(f'price must be at most {MAX_PRICE}')

    # fee_percent — optional; 0..100 if present.
    fee = body.get('fee_percent')
    if fee is not None and fee != '':
        fv = _as_float(fee)
        if fv is None:
            errors.append('fee_percent must be a number')
        elif not (0 <= fv <= 100):
            errors.append('fee_percent must be between 0 and 100')

    # date — optional; must parse if present.
    date = body.get('date')
    if date not in (None, '') and normalize_datetime(date) is None:
        errors.append('date must be a date (YYYY-MM-DD or MM/DD/YYYY) '
                      'optionally with a time (e.g. "2026-08-11 15:23:24" '
                      'or "08/31/2026, 12:12 AM")')

    return errors


def _as_int(v):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _as_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# Accepted hand-entry date/datetime shapes. Date-only formats have no %H/%I and
# normalize to "YYYY-MM-DD"; the rest carry a time and normalize to
# "YYYY-MM-DDThh:mm:ss". Order matters only in that the first match wins.
_DATE_ONLY_FORMATS = ('%Y-%m-%d', '%m/%d/%Y')
_DATETIME_FORMATS = (
    '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M',
    '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
    '%m/%d/%Y, %I:%M:%S %p', '%m/%d/%Y, %I:%M %p',
    '%m/%d/%Y %I:%M:%S %p', '%m/%d/%Y %I:%M %p',
    '%m/%d/%Y, %H:%M:%S', '%m/%d/%Y, %H:%M',
    '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M',
)


def normalize_datetime(v):
    """Parse a flexible, hand-entered date/datetime string into a canonical
    stored form: ``YYYY-MM-DD`` when only a date is given, or
    ``YYYY-MM-DDThh:mm:ss`` when a time is present. Returns ``None`` if the value
    cannot be parsed.

    Accepts, among others: ``2026-08-11``, ``2026-08-11 15:23:24``,
    ``2026-08-11T15:23``, ``08/31/2026``, ``08/31/2026, 12:12 AM``,
    ``08/31/2026 12:12:00 PM`` — and ISO strings with a trailing ``Z``/offset.
    """
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    for fmt in _DATE_ONLY_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%dT%H:%M:%S')
        except ValueError:
            continue
    try:  # tolerate ISO strings with a trailing Z / timezone offset
        return datetime.fromisoformat(s.replace('Z', '+00:00')).strftime('%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return None
