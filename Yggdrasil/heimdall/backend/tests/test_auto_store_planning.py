"""Auto-Store: which items it may deposit and how it splits them over the Storage Units."""
from scheduler import ConfirmationScheduler as Scheduler, STORAGE_CAPACITY


def _casket(casket_id, count):
    return {'item_id': casket_id, 'item_storage_total': count}


def test_the_fullest_unit_with_room_fills_first_then_the_next():
    caskets = [_casket('empty', 0), _casket('nearly_full', 995), _casket('half', 500), _casket('full', STORAGE_CAPACITY)]
    plan, unplaced = Scheduler._plan_casket_moves([f'i{n}' for n in range(10)], caskets)
    assert plan == [('nearly_full', ['i0', 'i1', 'i2', 'i3', 'i4']), ('half', ['i5', 'i6', 'i7', 'i8', 'i9'])]
    assert unplaced == []


def test_a_batch_that_fits_the_fullest_unit_goes_there_alone():
    plan, unplaced = Scheduler._plan_casket_moves(['a', 'b'], [_casket('roomy', 10), _casket('tight', 990)])
    assert plan == [('tight', ['a', 'b'])]
    assert unplaced == []


def test_items_beyond_the_free_space_are_left_unplaced():
    plan, unplaced = Scheduler._plan_casket_moves(['a', 'b', 'c'], [_casket('one', 998), _casket('full', STORAGE_CAPACITY)])
    assert plan == [('one', ['a', 'b'])]
    assert unplaced == ['c']


def test_no_storage_units_means_no_plan():
    assert Scheduler._plan_casket_moves(['a'], []) == ([], ['a'])


def test_market_purchases_are_storable_but_traded_items_are_not():
    market_bought = {'def_index': 4818, 'item_moveable': True, 'trade_unlock': '2026-11-01T00:00:00Z', 'trade_locked': False}
    traded = {'def_index': 4818, 'item_moveable': True, 'trade_unlock': '2026-11-06T00:00:00Z', 'trade_locked': True}
    assert Scheduler._is_storable(market_bought)
    assert not Scheduler._is_storable(traded)


def test_storage_units_and_unmovable_items_are_never_storable():
    assert not Scheduler._is_storable({'def_index': 1201})
    assert not Scheduler._is_storable({'def_index': 874, 'item_moveable': False})
