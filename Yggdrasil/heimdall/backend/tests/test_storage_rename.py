"""Rename Storage Unit commands (storage_rename.py)."""
import pytest

from commands import CommandError
from storage_rename import RenameService, check_name


class FakeRatatoskr:
    def __init__(self, caskets, fail=None):
        self.caskets = caskets
        self.fail = fail or set()
        self.renamed = []

    def get_caskets(self, steamid):
        return {'caskets': [dict(c) for c in self.caskets]}

    def rename_casket(self, steamid, item_id, name):
        if item_id in self.fail:
            return {'error': 'Game Coordinator did not answer'}
        self.renamed.append((item_id, name))
        for unit in self.caskets:
            if unit['item_id'] == item_id:
                unit['item_customname'] = name
                unit['item_id'] = item_id + 'x'      # the Game Coordinator gives a new id
        return {'item_id': item_id + 'x', 'previous_item_id': item_id, 'custom_name': name}


def units():
    return [{'item_id': '1', 'item_customname': 'Storage 10', 'item_storage_total': 5},
            {'item_id': '2', 'item_customname': 'Storage 2', 'item_storage_total': 1000},
            {'item_id': '3', 'item_storage_total': 0},
            {'item_id': '4', 'item_customname': '  ', 'item_storage_total': 0}]


def test_units_lists_unnamed_first_then_natural_order():
    answer = RenameService(FakeRatatoskr(units())).units('s')
    assert [u['item_id'] for u in answer['units']] == ['3', '4', '2', '1']
    assert answer['unnamed'] == 2
    assert answer['units'][2] == {'item_id': '2', 'name': 'Storage 2', 'named': True,
                                  'items': 1000, 'free_places': 0}


def test_check_name_rules():
    assert check_name('  Cases  ', units(), '3') == 'Cases'
    with pytest.raises(CommandError) as error:
        check_name('   ', units(), '3')
    assert error.value.code == 'name_required'
    with pytest.raises(CommandError) as error:
        check_name('x' * 21, units(), '3')
    assert error.value.code == 'name_too_long'
    with pytest.raises(CommandError) as error:
        check_name('storage 2', units(), '3')
    assert error.value.code == 'name_taken'
    assert check_name('Storage 2', units(), '2') == 'Storage 2'     # its own name again is fine


def test_rename_one_returns_new_id():
    fake = FakeRatatoskr(units())
    done = RenameService(fake).rename('s', '3', 'Knives')
    assert done == {'item_id': '3x', 'previous_item_id': '3', 'name': 'Knives'}


def test_rename_refused_while_moving_and_for_unknown_unit():
    fake = FakeRatatoskr(units())
    with pytest.raises(CommandError) as error:
        RenameService(fake, moves_in_progress=lambda s: True).rename('s', '3', 'Knives')
    assert error.value.code == 'busy'
    with pytest.raises(CommandError) as error:
        RenameService(fake).rename('s', '99', 'Knives')
    assert error.value.code == 'unknown_unit'
    assert fake.renamed == []


def test_rename_failure_is_a_command_error():
    with pytest.raises(CommandError) as error:
        RenameService(FakeRatatoskr(units(), fail={'3'})).rename('s', '3', 'Knives')
    assert error.value.code == 'rename_failed'


def test_name_unnamed_plan_then_execute_once():
    fake = FakeRatatoskr(units())
    service = RenameService(fake)
    plan = service.plan_name_unnamed('s')
    assert plan['renames'] == [{'item_id': '3', 'name': 'Storage 11'},
                               {'item_id': '4', 'name': 'Storage 12'}]
    assert fake.renamed == []                       # a plan changes nothing
    done = service.execute(plan['id'])
    assert done['named'] == 2 and done['failed'] == 0
    assert fake.renamed == [('3', 'Storage 11'), ('4', 'Storage 12')]
    with pytest.raises(CommandError) as error:
        service.execute(plan['id'])
    assert error.value.code == 'already_executed'


def test_execute_refused_while_moving_keeps_the_plan():
    moving = [True]
    fake = FakeRatatoskr(units())
    service = RenameService(fake, moves_in_progress=lambda s: moving[0])
    plan = service.plan_name_unnamed('s')
    with pytest.raises(CommandError) as error:
        service.execute(plan['id'])
    assert error.value.code == 'busy' and fake.renamed == []
    moving[0] = False
    assert service.execute(plan['id'])['named'] == 2


def test_name_unnamed_keeps_going_after_a_failure():
    service = RenameService(FakeRatatoskr(units(), fail={'3'}))
    done = service.execute(service.plan_name_unnamed('s')['id'])
    assert done['named'] == 1 and done['failed'] == 1
    assert done['results'][0]['code'] == 'rename_failed'


def test_nothing_to_name_and_expired_plan():
    named = [u for u in units() if u['item_id'] in ('1', '2')]
    with pytest.raises(CommandError) as error:
        RenameService(FakeRatatoskr(named)).plan_name_unnamed('s')
    assert error.value.code == 'nothing_to_name'
    now = [1000.0]
    service = RenameService(FakeRatatoskr(units()), clock=lambda: now[0])
    plan = service.plan_name_unnamed('s')
    now[0] += 11 * 60
    with pytest.raises(CommandError) as error:
        service.execute(plan['id'])
    assert error.value.code == 'unknown_plan'


def test_not_connected():
    class Down(FakeRatatoskr):
        def get_caskets(self, steamid):
            return {'error': 'No active GC session'}
    with pytest.raises(CommandError) as error:
        RenameService(Down([])).units('s')
    assert error.value.code == 'not_connected'
