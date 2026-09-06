"""Write-endpoint validation (fix #5) — reject bad input instead of silently
coercing it into a wrong ledger."""
import pytest

from validation import (normalize_datetime, validate_portfolio_name,
                        validate_transaction)


def test_valid_transaction_passes():
    assert validate_transaction(
        {'item_name': 'Fracture Case', 'type': 'buy', 'qty': 10, 'price': 0.41}
    ) == []


def test_missing_item_name_rejected():
    errs = validate_transaction({'type': 'buy', 'qty': 1, 'price': 1.0})
    assert any('item_name' in e for e in errs)


def test_non_numeric_price_rejected():
    # The exact bug the normalizer used to hide (price -> 0.0 silently).
    errs = validate_transaction({'item_name': 'X', 'price': 'abc'})
    assert any('price' in e for e in errs)


def test_negative_price_rejected():
    errs = validate_transaction({'item_name': 'X', 'price': -5})
    assert any('price' in e for e in errs)


def test_zero_qty_rejected():
    errs = validate_transaction({'item_name': 'X', 'qty': 0})
    assert any('qty' in e for e in errs)


def test_bad_type_rejected():
    errs = validate_transaction({'item_name': 'X', 'type': 'gift'})
    assert any('type' in e for e in errs)


def test_bad_date_rejected():
    errs = validate_transaction({'item_name': 'X', 'date': 'last tuesday'})
    assert any('date' in e for e in errs)


def test_good_dates_accepted():
    for good in ('2026-09-01', '2026-09-01T12:30:00Z', '2026-09-01T12:30:00',
                 '2026-08-11 15:23:24', '08/31/2026', '08/31/2026, 12:12 AM'):
        assert validate_transaction({'item_name': 'X', 'date': good}) == [], good


@pytest.mark.parametrize('raw, expected', [
    # date-only shapes normalize to YYYY-MM-DD
    ('2026-08-11', '2026-08-11'),
    ('08/31/2026', '2026-08-31'),
    ('  2026-08-11  ', '2026-08-11'),          # surrounding whitespace tolerated
    # date-time shapes normalize to YYYY-MM-DDThh:mm:ss
    ('2026-08-11 15:23:24', '2026-08-11T15:23:24'),
    ('2026-08-11T15:23', '2026-08-11T15:23:00'),
    ('08/31/2026, 12:12 AM', '2026-08-31T00:12:00'),   # 12 AM → 00:xx
    ('08/31/2026, 12:12 PM', '2026-08-31T12:12:00'),   # 12 PM → 12:xx
    ('08/31/2026 1:05 PM', '2026-08-31T13:05:00'),     # 1 PM → 13:xx, no comma
    ('08/31/2026, 09:30:45 AM', '2026-08-31T09:30:45'),
    ('2026-09-01T12:30:00Z', '2026-09-01T12:30:00'),   # trailing Z dropped
])
def test_normalize_datetime_canonicalizes(raw, expected):
    assert normalize_datetime(raw) == expected


@pytest.mark.parametrize('bad', ['last tuesday', '', '   ', 'not a date',
                                 '2026-13-40', None, 12345])
def test_normalize_datetime_rejects_garbage(bad):
    assert normalize_datetime(bad) is None


def test_partial_update_allows_missing_item_name():
    # PATCH with only a price change must not demand item_name...
    assert validate_transaction({'price': 0.42}, partial=True) == []
    # ...but still rejects a bad value.
    assert validate_transaction({'price': 'nope'}, partial=True) != []


def test_portfolio_name_rules():
    assert validate_portfolio_name('acct1') == []
    assert validate_portfolio_name('', required=True) != []
    assert validate_portfolio_name(None, required=False) == []
    assert validate_portfolio_name('x' * 200, required=True) != []
