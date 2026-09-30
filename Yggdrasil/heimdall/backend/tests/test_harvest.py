"""Unit tests for HarvestService — holdings at purchase price vs autobuy offers.

Fakes stand in for Huginn (autobuy prices, fees, registry) and Draupnir
(positions), so nothing touches pulse. Pinned: best offer across markets, fee
netting, per-account cost basis, zero-cost positions, the recent-buy hint, the
account and min-profit filters, the summary, and the non-blocking warm.
"""
import datetime
import time

from draupnir_service import DraupnirService
from harvest_service import HarvestService


class FakeHuginn:
    def __init__(self):
        self.autobuy = {
            'CsMoneyTrade': {'AK': {'price': 12.0, 'count': 3}, 'AWP': {'price': 90.0, 'count': 1},
                             'Case': {'price': 1.0, 'count': 700}},
            'Buff': {'AK': {'price': 13.0, 'count': 5}},
        }
        self.fees = {'CsMoneyTrade': 0.0, 'Buff': 0.1}
        self.listings = {}   # Buff163 min listing (the sanity reference)
        self.pulls = []

    def price_map(self, token, market):
        self.pulls.append('price_map:' + market)
        return self.listings

    def market_registry(self, settings=None):
        return [
            {'id': 'CsMoneyTrade', 'display': 'CSMoney (Trade)', 'hasAutobuy': True, 'fee': 0.0, 'feeKnown': False},
            {'id': 'Buff', 'display': 'Buff163', 'hasAutobuy': True, 'fee': 0.1, 'feeKnown': True},
            {'id': 'LisSkins', 'display': 'LisSkins', 'hasAutobuy': False, 'fee': 0.0, 'feeKnown': False},
        ]

    def market_fee(self, market_id, settings=None):
        return self.fees[market_id]

    def market_display(self, market_id):
        return {'CsMoneyTrade': 'CSMoney (Trade)', 'Buff': 'Buff163'}.get(market_id, market_id)

    def market_autobuy_index(self, token, market_id):
        self.pulls.append(market_id)
        return self.autobuy.get(market_id, {})


class FakeDraupnir:
    def open_lots(self):
        return [
            {'portfolio_id': 'p1', 'account': 'everthinklol', 'item_name': 'AK', 'qty': 2,
             'price': 10.0, 'platform': 'lisskins', 'last_buy_date': '2026-09-01'},
            {'portfolio_id': 'p2', 'account': 'hidey_spidey', 'item_name': 'AK', 'qty': 1,
             'price': 12.5, 'platform': 'buff163', 'last_buy_date': '2026-09-27T10:00:00'},
            {'portfolio_id': 'p1', 'account': 'everthinklol', 'item_name': 'AWP', 'qty': 1,
             'price': 100.0, 'platform': '', 'last_buy_date': ''},
            {'portfolio_id': 'p1', 'account': 'everthinklol', 'item_name': 'Case', 'qty': 5,
             'price': 0.0, 'platform': 'drop', 'last_buy_date': '2026-01-01'},
            {'portfolio_id': 'p1', 'account': 'everthinklol', 'item_name': 'Unpriced', 'qty': 1,
             'price': 3.0, 'platform': '', 'last_buy_date': ''},
        ]


def warmed(markets=('CsMoneyTrade', 'Buff')):
    huginn = FakeHuginn()
    service = HarvestService(huginn, FakeDraupnir(), today=lambda: datetime.date(2026, 9, 29))
    service._warm('token', [*markets, HarvestService.REFERENCE])
    return service, huginn


def rows_by_key(board):
    return {(r['account'], r['item_name']): r for r in board['rows']}


def test_best_offer_is_the_highest_net_after_fees():
    service, _ = warmed()
    board = service.board('token', market_ids=['CsMoneyTrade', 'Buff'])
    ak = rows_by_key(board)[('everthinklol', 'AK')]
    # Buff pays 13.00 gross but 11.70 after its 10% fee; CSMoney pays 12.00 flat.
    assert ak['best']['market'] == 'CsMoneyTrade'
    assert [o['net'] for o in ak['offers']] == [12.0, 11.7]
    assert ak['profit'] == 2.0 and ak['profit_pct'] == 20.0 and ak['total_profit'] == 4.0
    assert ak['proceeds'] == 24.0
    assert ak['paid'] == 10.0 and ak['platform'] == 'lisskins'


def test_each_lot_keeps_its_own_price():
    service, _ = warmed()
    rows = rows_by_key(service.board('token', market_ids=['CsMoneyTrade']))
    assert rows[('hidey_spidey', 'AK')]['profit'] == -0.5
    assert rows[('everthinklol', 'AK')]['profit'] == 2.0


def test_zero_cost_lot_has_no_percentage_but_counts_as_profit():
    service, _ = warmed()
    case = rows_by_key(service.board('token', market_ids=['CsMoneyTrade']))[('everthinklol', 'Case')]
    assert case['profit_pct'] is None and case['total_profit'] == 5.0


def test_recent_buy_is_flagged_as_maybe_trade_held():
    service, _ = warmed()
    rows = rows_by_key(service.board('token', market_ids=['CsMoneyTrade']))
    assert rows[('hidey_spidey', 'AK')]['days_since_buy'] == 2
    assert rows[('hidey_spidey', 'AK')]['maybe_trade_held'] is True
    assert rows[('everthinklol', 'AK')]['maybe_trade_held'] is False
    assert rows[('everthinklol', 'AWP')]['days_since_buy'] is None


def test_account_filter_sort_and_summary():
    service, _ = warmed()
    board = service.board('token', account='p1', market_ids=['CsMoneyTrade'])
    assert {r['account'] for r in board['rows']} == {'everthinklol'}
    assert [r['item_name'] for r in board['rows']] == ['Case', 'AK', 'AWP']   # total profit, high to low
    summary = board['summary']
    assert summary['lots'] == 4 and summary['priced'] == 3 and summary['unpriced'] == 1
    assert summary['profitable'] == 2 and summary['total_profit'] == 9.0
    assert summary['proceeds'] == 29.0 and summary['cost'] == 20.0
    assert summary['cash_profit'] == 0.0   # every best offer is CSMoney Trade balance


def test_balance_markets_are_marked_and_kept_out_of_cash_profit():
    service, huginn = warmed()
    huginn.autobuy['Buff']['AK'] = {'price': 14.0, 'count': 5}   # 12.60 after fee beats CSMoney
    board = service.board('token', account='p1', market_ids=['CsMoneyTrade', 'Buff'])
    ak = rows_by_key(board)[('everthinklol', 'AK')]
    assert ak['best']['market'] == 'Buff' and ak['best']['balance'] is None
    assert ak['offers'][1]['balance'] == 'cs.money trade balance'
    assert board['summary']['cash_profit'] == 5.2 and board['summary']['total_profit'] == 10.2


def test_min_profit_filter_keeps_zero_cost_rows():
    service, _ = warmed()
    board = service.board('token', account='p1', market_ids=['CsMoneyTrade'], min_profit_pct=10)
    assert [r['item_name'] for r in board['rows']] == ['Case', 'AK']


def test_unknown_or_non_autobuy_markets_fall_back_to_the_default():
    service, _ = warmed()
    assert service.clean_markets(['LisSkins', 'Nope']) == ['CsMoneyTrade']
    assert service.clean_markets(['Buff', 'Buff', 'CsMoneyTrade']) == ['Buff', 'CsMoneyTrade']


def test_cold_board_warms_in_the_background_without_blocking():
    huginn = FakeHuginn()
    service = HarvestService(huginn, FakeDraupnir())
    first = service.board('token', market_ids=['CsMoneyTrade'])
    assert first['status'] == 'warming' and first['rows'] == []
    deadline = time.time() + 2
    while service._warming and time.time() < deadline:
        time.sleep(0.01)
    second = service.board('token', market_ids=['CsMoneyTrade'])
    assert second['status'] == 'fresh' and second['rows']
    assert huginn.pulls == ['CsMoneyTrade', 'price_map:buff']


def test_no_token_never_pulls():
    huginn = FakeHuginn()
    service = HarvestService(huginn, FakeDraupnir())
    assert service.board('', market_ids=['CsMoneyTrade'])['status'] == 'no_token'
    assert huginn.pulls == []


def test_draupnir_open_lots_are_real_prices_with_oldest_sold_first(tmp_path):
    draupnir = DraupnirService(path=str(tmp_path / 'portfolios.json'))
    pid = draupnir.create_portfolio('everthinklol')['id']
    for raw in [
        {'item_name': 'AK', 'type': 'buy', 'qty': 2, 'price': 10, 'date': '2026-09-01', 'platform': 'buff163'},
        {'item_name': 'AK', 'type': 'buy', 'qty': 50, 'price': 4, 'date': '2026-09-03', 'platform': 'buff163_buy'},
        {'item_name': 'AK', 'type': 'buy', 'qty': 50, 'price': 4, 'date': '2026-09-14', 'platform': 'buff163_buy'},
        {'item_name': 'AK', 'type': 'buy', 'qty': 36, 'price': 3.84, 'date': '2026-09-21', 'platform': 'cs.money.market'},
        {'item_name': 'AK', 'type': 'sell', 'qty': 3, 'price': 20, 'date': '2026-09-25'},   # the $10 pair + one $4
        {'item_name': 'Flip', 'type': 'buy', 'qty': 1, 'price': 5, 'is_arbitrage': True},
        {'item_name': 'Gone', 'type': 'buy', 'qty': 1, 'price': 5},
        {'item_name': 'Gone', 'type': 'sell', 'qty': 1, 'price': 6},
    ]:
        draupnir.add_transaction(pid, raw)
    lots = [{k: lot[k] for k in ('item_name', 'price', 'qty', 'platform', 'last_buy_date')}
            for lot in draupnir.open_lots()]
    assert lots == [
        {'item_name': 'AK', 'price': 3.84, 'qty': 36, 'platform': 'cs.money.market', 'last_buy_date': '2026-09-21'},
        {'item_name': 'AK', 'price': 4.0, 'qty': 99, 'platform': 'buff163_buy', 'last_buy_date': '2026-09-14'},
    ]

def test_cash_offer_far_above_the_buff_listing_is_flagged_and_left_out():
    huginn = FakeHuginn()
    huginn.autobuy['Buff']['AK'] = {'price': 30.0, 'count': 1}    # a float-specific buy order
    huginn.listings = {'AK': 11.0}
    service = HarvestService(huginn, FakeDraupnir(), today=lambda: datetime.date(2026, 9, 29))
    service._warm('token', ['CsMoneyTrade', 'Buff', HarvestService.REFERENCE])
    board = service.board('token', account='p1', market_ids=['CsMoneyTrade', 'Buff'])
    ak = rows_by_key(board)[('everthinklol', 'AK')]
    # CSMoney's normal 12.00 wins over Buff's suspicious 27.00 net; Buff stays listed, flagged.
    assert ak['best']['market'] == 'CsMoneyTrade' and ak['buff_listing'] == 11.0
    assert ak['offers'][1]['market'] == 'Buff' and ak['offers'][1]['suspicious'] is True

    only_buff = service.board('token', account='p1', market_ids=['Buff'])
    ak = rows_by_key(only_buff)[('everthinklol', 'AK')]
    assert ak['best']['suspicious'] is True
    assert only_buff['summary']['to_check'] == 1 and only_buff['summary']['total_profit'] == 0


def test_csfloat_autobuy_is_read_live_so_a_finished_sweep_shows_at_once():
    huginn = FakeHuginn()
    huginn.autobuy['CsFloat'] = {'AK': {'price': 11.0, 'count': 4}}
    huginn.fees['CsFloat'] = 0.02
    service = HarvestService(huginn, FakeDraupnir(), today=lambda: datetime.date(2026, 9, 29))
    service._warm('token', [HarvestService.REFERENCE])
    assert service._index_state('token', ['CsFloat'])[1] == 'fresh'
    huginn.autobuy['CsFloat']['AWP'] = {'price': 120.0, 'count': 1}    # the sweep found more
    found, _ = service._index_state('token', ['CsFloat'])
    assert set(found['CsFloat']) == {'AK', 'AWP'}
    assert 'CsFloat' not in service._indexes


class FailingHuginn(FakeHuginn):
    """CSMoney (Trade)'s autobuy pull raises, like a pulse outage."""
    def market_autobuy_index(self, token, market_id):
        self.pulls.append(market_id)
        if market_id == 'CsMoneyTrade':
            raise RuntimeError('pulse 502')
        return self.autobuy.get(market_id, {})


def test_failed_warm_reports_error_naming_the_market_and_backs_off():
    huginn = FailingHuginn()
    service = HarvestService(huginn, FakeDraupnir())
    assert service.board('token', market_ids=['CsMoneyTrade'])['status'] == 'warming'
    deadline = time.time() + 2
    while service._warming and time.time() < deadline:
        time.sleep(0.01)
    board = service.board('token', market_ids=['CsMoneyTrade'])
    assert board['status'] == 'error'
    assert board['failed_markets'] == ['CsMoneyTrade']
    assert 'CSMoney (Trade)' in board['error']
    assert [m['failed'] for m in board['markets']] == [True]
    # Inside the back-off window nothing is re-pulled, however often the page asks.
    pulls_before = list(huginn.pulls)
    for _ in range(3):
        assert service.board('token', market_ids=['CsMoneyTrade'])['status'] == 'error'
    assert huginn.pulls == pulls_before
    # After the window the market is queued again.
    failed_at, message = service._failures['CsMoneyTrade']
    service._failures['CsMoneyTrade'] = (failed_at - HarvestService._RETRY_AFTER_FAILURE - 1, message)
    assert service.board('token', market_ids=['CsMoneyTrade'])['status'] == 'warming'


def test_a_successful_warm_clears_the_failure():
    huginn = FailingHuginn()
    service = HarvestService(huginn, FakeDraupnir())
    service._warm('token', ['CsMoneyTrade'])
    assert 'CsMoneyTrade' in service._failures
    huginn.market_autobuy_index = FakeHuginn.market_autobuy_index.__get__(huginn)
    service._warm('token', ['CsMoneyTrade', HarvestService.REFERENCE])
    assert service._failures == {}
    assert service.board('token', market_ids=['CsMoneyTrade'])['status'] == 'fresh'


def test_open_lots_a_sell_with_no_earlier_buy_is_covered_by_the_next_buy(tmp_path):
    draupnir = DraupnirService(path=str(tmp_path / 'portfolios.json'))
    pid = draupnir.create_portfolio('everthinklol')['id']
    for raw in [
        {'item_name': 'X', 'type': 'sell', 'qty': 1, 'price': 5, 'date': '2026-09-01'},   # buy never entered
        {'item_name': 'X', 'type': 'buy', 'qty': 1, 'price': 4, 'date': '2026-09-05'},
        {'item_name': 'Y', 'type': 'sell', 'qty': 1, 'price': 5, 'date': '2026-09-01'},
        {'item_name': 'Y', 'type': 'buy', 'qty': 3, 'price': 2, 'date': '2026-09-05'},
    ]:
        draupnir.add_transaction(pid, raw)
    lots = {lot['item_name']: lot['qty'] for lot in draupnir.open_lots()}
    assert lots == {'Y': 2}   # X nets to zero, Y keeps the two uncovered units
