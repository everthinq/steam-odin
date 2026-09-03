"""Avg-cost accounting — the money math the whole tool is trusted on (fix #4).

These lock down cost basis, realized P/L (avg-cost method), and unrealized P/L
so a refactor can't silently move the numbers Ivan trades on.
"""
from draupnir_service import DraupnirService, _demojibake


def _txn(item, typ, qty, price):
    return {'item_name': item, 'type': typ, 'qty': qty, 'price': price}


def test_avg_cost_and_realized_pl():
    # Buy 100@2 then 100@4 -> avg 3.00. Sell 50@10 -> realized = 500 - 3*50 = 350.
    txns = [
        _txn('Fracture Case', 'buy', 100, 2.0),
        _txn('Fracture Case', 'buy', 100, 4.0),
        _txn('Fracture Case', 'sell', 50, 10.0),
    ]
    (h,) = DraupnirService._holdings(txns, prices=None)
    assert h['avg_cost'] == 3.0
    assert h['net_qty'] == 150
    assert h['cost_basis'] == 450.0        # 3.00 * 150
    assert h['realized_pl'] == 350.0       # 500 - 3*50
    assert h['market_value'] is None       # no price supplied
    assert h['unrealized_pl'] is None


def test_unrealized_pl_with_price():
    txns = [
        _txn('Revolution Case', 'buy', 200, 0.18),
    ]
    (h,) = DraupnirService._holdings(txns, prices={'Revolution Case': 0.30})
    assert h['avg_cost'] == 0.18
    assert h['net_qty'] == 200
    assert h['cost_basis'] == 36.0                 # 0.18 * 200
    assert h['market_value'] == 60.0               # 0.30 * 200
    assert h['unrealized_pl'] == 24.0              # 60 - 36


def test_fully_sold_position_has_no_market_value():
    txns = [
        _txn('Kilowatt Case', 'buy', 10, 0.10),
        _txn('Kilowatt Case', 'sell', 10, 0.25),
    ]
    (h,) = DraupnirService._holdings(txns, prices={'Kilowatt Case': 0.20})
    assert h['net_qty'] == 0
    assert h['realized_pl'] == 1.5                  # 2.50 - 0.10*10
    assert h['market_value'] is None               # net_qty == 0 -> not valued
    assert h['cost_basis'] == 0.0


def test_summarize_falls_back_to_cost_basis_for_unpriced():
    # One priced holding, one unpriced -> current_value counts price + cost basis,
    # but unrealized counts only the priced one (no invented gains).
    txns = [
        _txn('Priced Case', 'buy', 100, 1.0),
        _txn('Unpriced Case', 'buy', 100, 2.0),
    ]
    holdings = DraupnirService._holdings(txns, prices={'Priced Case': 3.0})
    p = {'id': 'x', 'name': 'n', 'created_at': 't', 'updated_at': 't',
         'transactions': txns}
    s = DraupnirService._summarize(p, holdings)
    assert s['invested'] == 300.0            # 100*1 + 100*2
    assert s['cost_basis'] == 300.0
    assert s['current_value'] == 500.0       # priced 300 + unpriced cost-basis 200
    assert s['unrealized_pl'] == 200.0       # only the priced holding (300-100)
    assert s['unpriced_count'] == 1
    assert s['priced'] is True


def test_demojibake_repairs_latin1_utf8():
    # Build the exact mojibake: 'StatTrak™' UTF-8 bytes misdecoded as latin-1.
    clean = 'StatTrak™'                       # StatTrak™
    mojibake = clean.encode('utf-8').decode('latin-1')
    assert mojibake != clean                       # sanity: it really is broken
    assert _demojibake(mojibake) == clean
    # Clean strings pass through untouched.
    assert _demojibake('Fracture Case') == 'Fracture Case'
