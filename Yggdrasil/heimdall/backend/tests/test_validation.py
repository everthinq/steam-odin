"""Write-endpoint validation (fix #5) — reject bad input instead of silently
coercing it into a wrong ledger."""
from validation import validate_portfolio_name, validate_transaction


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
    assert validate_transaction({'item_name': 'X', 'date': '2026-09-01'}) == []
    assert validate_transaction(
        {'item_name': 'X', 'date': '2026-09-01T12:30:00Z'}) == []


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
