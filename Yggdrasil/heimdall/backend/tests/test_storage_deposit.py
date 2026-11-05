"""Storage Units: the one copy of the storage rules, the planner and the move commands
(storage_deposit.py) that Transfer, Auto-Store and the command line share."""
import time
from datetime import datetime, timedelta, timezone

import pytest

import storage_deposit
from jobs import JobRegistry
from scheduler import ConfirmationScheduler
from commands import CommandError
from storage_deposit import (STORAGE_CAPACITY, DepositService, plan_deposit, 
                             storage_block)

STEAMID = '76561190000000001'


def _casket(casket_id, count, name=None):
    return {'item_id': casket_id, 'item_storage_total': count, 'item_customname': name or casket_id}


def _item(item_id, name='Kilowatt Case', **fields):
    return {'item_id': item_id, 'item_name': name, 'def_index': 4904, 'item_moveable': True,
            'trade_locked': False, **fields}


def _in_future(hours=24):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace('+00:00', 'Z')


# ---- what may go in -----------------------------------------------------------------------

def test_market_purchases_are_storable_but_traded_items_are_not():
    market_bought = _item('a', trade_unlock=_in_future())
    traded = _item('b', trade_unlock=_in_future(), trade_locked=True, trade_protected_until=_in_future())
    assert storage_block(market_bought) is None
    assert storage_block(traded)['code'] == 'trade_protected'
    assert storage_block(traded)['until'] == traded['trade_protected_until']


def test_trade_protection_that_has_ended_no_longer_blocks():
    ended = _item('a', trade_locked=True, trade_protected_until='2020-01-01T00:00:00.000Z')
    assert storage_block(ended) is None


def test_storage_units_and_unmovable_items_are_never_storable():
    assert storage_block({'def_index': 1201, 'item_moveable': False})['code'] == 'storage_unit'
    assert storage_block({'def_index': 874, 'item_moveable': False})['code'] == 'unstorable'


# ---- which unit -------------------------------------------------------------------------

def test_the_fullest_unit_with_room_fills_first_then_the_next():
    caskets = [_casket('empty', 0), _casket('nearly_full', 995), _casket('half', 500), _casket('full', STORAGE_CAPACITY)]
    batches, overflow = plan_deposit([f'i{n}' for n in range(10)], caskets)
    assert batches == [('nearly_full', ['i0', 'i1', 'i2', 'i3', 'i4']), ('half', ['i5', 'i6', 'i7', 'i8', 'i9'])]
    assert overflow == []


def test_a_batch_that_fits_the_fullest_unit_goes_there_alone():
    batches, overflow = plan_deposit(['a', 'b'], [_casket('roomy', 10), _casket('tight', 990)])
    assert batches == [('tight', ['a', 'b'])]
    assert overflow == []


def test_items_beyond_the_free_space_overflow():
    batches, overflow = plan_deposit(['a', 'b', 'c'], [_casket('one', 998), _casket('full', STORAGE_CAPACITY)])
    assert batches == [('one', ['a', 'b'])]
    assert overflow == ['c']


def test_no_storage_units_means_no_batches():
    assert plan_deposit(['a'], []) == ([], ['a'])


# ---- the commands ------------------------------------------------------------------------

class FakeRatatoskr:
    """Ratatoskr's move queue: every move is confirmed except the ids in `refuse` (no answer) and
    `unsent` (never sent); `drop_queue` answers a new, empty run (a disconnect or restart)."""

    def __init__(self, inventory, caskets, refuse=(), unsent=(), drop_queue=False):
        self.inventory = inventory
        self.caskets = caskets
        self.refuse = set(refuse)
        self.unsent = set(unsent)
        self.drop_queue = drop_queue
        self.batches = []
        self.run_id = 0
        self.status = {'running': False, 'runId': 0}

    def get_inventory(self, steamid):
        return {'items': [dict(i) for i in self.inventory]}

    def get_caskets(self, steamid):
        return {'caskets': [dict(c) for c in self.caskets]}

    def move_batch(self, steamid, item_ids, source, target, casket_id=None):
        self.run_id += 1
        self.batches.append((casket_id, list(item_ids), source, target))
        refused = [i for i in item_ids if i in self.refuse]
        unsent = [i for i in item_ids if i in self.unsent]
        done = [i for i in item_ids if i not in self.refuse and i not in self.unsent]
        if self.drop_queue:
            self.status = {'running': False, 'runId': 0, 'done': 0, 'failed': 0, 'processed': 0,
                           'doneItemIDs': [], 'failedItemIDs': [], 'refusedItemIDs': []}
        else:
            self.status = {'running': False, 'runId': self.run_id, 'done': len(done),
                           'failed': len(refused) + len(unsent), 'processed': len(item_ids),
                           'doneItemIDs': done, 'failedItemIDs': refused + unsent, 'refusedItemIDs': refused}
        return {'running': True, 'runId': self.run_id, 'done': 0, 'failed': 0, 'processed': 0}

    def get_move_status(self, steamid):
        return self.status


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(storage_deposit, '_STATUS_POLL_SECONDS', 0)


def _service(inventory, caskets, refuse=(), unsent=()):
    ratatoskr = FakeRatatoskr(inventory, caskets, refuse, unsent)
    return DepositService(ratatoskr, JobRegistry()), ratatoskr


def _wait(service, job_id):
    for _ in range(200):
        job = service.jobs.get(job_id)
        if not job['running']:
            return job
        time.sleep(0.01)
    raise AssertionError('job never finished')


def test_a_chosen_trade_protected_item_is_refused_and_never_sent():
    inventory = [_item('ok'), _item('traded', trade_locked=True, trade_protected_until=_in_future())]
    service, ratatoskr = _service(inventory, [_casket('unit', 10)])
    plan = service.plan_deposit(STEAMID, item_ids=['ok', 'traded'])
    assert plan['moves'] == 1
    assert [r['item_id'] for r in plan['refused']] == ['traded']
    job = _wait(service, service.execute(plan['plan_id'])['id'])
    assert ratatoskr.batches == [('unit', ['ok'], 'inventory', 'casket')]
    assert job['done'] == 1 and job['failed'] == 0


def test_all_storable_leaves_out_medals_quietly_and_lists_protected_items():
    inventory = [_item('case'), _item('medal', item_moveable=False),
                 _item('traded', trade_locked=True, trade_protected_until=_in_future())]
    service, _ = _service(inventory, [_casket('unit', 0)])
    plan = service.plan_deposit(STEAMID, all_storable=True)
    assert plan['moves'] == 1
    assert [r['item_id'] for r in plan['refused']] == ['traded']


def test_executing_a_plan_twice_returns_the_same_job_and_moves_once():
    service, ratatoskr = _service([_item('a')], [_casket('unit', 0)])
    plan = service.plan_deposit(STEAMID, item_ids=['a'])
    first = service.execute(plan['plan_id'])
    _wait(service, first['id'])
    second = service.execute(plan['plan_id'])
    assert second['id'] == first['id']
    assert len(ratatoskr.batches) == 1


def test_a_plan_bigger_than_the_free_space_cannot_be_executed():
    service, ratatoskr = _service([_item('a'), _item('b')], [_casket('unit', STORAGE_CAPACITY - 1)])
    plan = service.plan_deposit(STEAMID, item_ids=['a', 'b'])
    assert plan['overflow'] == 1 and not plan['executable']
    with pytest.raises(CommandError) as error:
        service.execute(plan['plan_id'])
    assert error.value.code == 'no_room'
    assert ratatoskr.batches == []


def test_auto_store_plans_may_leave_the_overflow_for_later():
    service, ratatoskr = _service([_item('a'), _item('b')], [_casket('unit', STORAGE_CAPACITY - 1)])
    plan = service.plan_deposit(STEAMID, names=['Kilowatt Case'], allow_overflow=True)
    assert plan['executable']
    _wait(service, service.execute(plan['plan_id'])['id'])
    assert ratatoskr.batches == [('unit', ['a'], 'inventory', 'casket')]


def test_an_expired_plan_is_refused(monkeypatch):
    service, _ = _service([_item('a')], [_casket('unit', 0)])
    plan = service.plan_deposit(STEAMID, item_ids=['a'])
    service._plans[plan['plan_id']]['expires_at'] = time.time() - 1
    with pytest.raises(CommandError) as error:
        service.execute(plan['plan_id'])
    assert error.value.code == 'plan_expired'


def test_one_move_run_per_account_at_a_time():
    service, _ = _service([_item('a'), _item('b')], [_casket('unit', 0)])
    first = service.plan_deposit(STEAMID, item_ids=['a'])
    second = service.plan_deposit(STEAMID, item_ids=['b'])
    job = service.jobs.create('deposit', {'steamid': STEAMID})
    service._running[STEAMID] = job['id']           # a run still going
    with pytest.raises(CommandError) as error:
        service.execute(first['plan_id'])
    assert error.value.code == 'busy'
    service.jobs.finish(job['id'])
    _wait(service, service.execute(second['plan_id'])['id'])


def test_refused_moves_are_reported_by_item():
    service, _ = _service([_item('a'), _item('b', name='Fracture Case')], [_casket('unit', 0)], refuse=['b'])
    plan = service.plan_deposit(STEAMID, item_ids=['a', 'b'])
    job = _wait(service, service.execute(plan['plan_id'])['id'])
    assert job['done'] == 1 and job['failed'] == 1
    assert job['result']['failed_items'] == [{'item_id': 'b', 'item_name': 'Fracture Case', 'code': 'refused'}]
    assert job['result']['batches'][0]['moved_ids'] == ['a']


def test_a_quick_second_plan_counts_what_was_just_deposited():
    """The Game Coordinator's unit count lags behind a move: the next plan must not over-fill."""
    service, ratatoskr = _service([_item('a'), _item('b')], [_casket('tight', STORAGE_CAPACITY - 1), _casket('roomy', 0)])
    _wait(service, service.execute(service.plan_deposit(STEAMID, item_ids=['a'])['plan_id'])['id'])
    # Ratatoskr still reports the old count for 'tight'
    second = service.plan_deposit(STEAMID, item_ids=['b'])
    assert [b['casket_id'] for b in second['batches']] == ['roomy']
    assert service.storage(STEAMID)['free'] == STORAGE_CAPACITY


def test_withdraw_takes_items_out_of_each_unit():
    service, ratatoskr = _service([], [_casket('u1', 5), _casket('u2', 7)])
    plan = service.plan_withdraw(STEAMID, [{'item_id': 'x', 'casket_id': 'u1'}, {'item_id': 'y', 'casket_id': 'u2'}])
    _wait(service, service.execute(plan['plan_id'])['id'])
    assert ratatoskr.batches == [('u1', ['x'], 'casket', 'inventory'), ('u2', ['y'], 'casket', 'inventory')]


# ---- Auto-Store uses the same command ---------------------------------------------------------

class FakeSettings:
    def __init__(self):
        self.history = []

    def append_auto_store_history(self, record):
        self.history.append(record)


class FakeSteam:
    def get_account(self, steamid):
        return {'account_name': 'tester'}


def test_auto_store_deposits_watched_items_and_records_what_was_confirmed():
    inventory = [_item('a'), _item('b'), _item('other', name='Fracture Case'),
                 _item('traded', trade_locked=True, trade_protected_until=_in_future())]
    service, ratatoskr = _service(inventory, [_casket('unit', 0, 'Cases')], refuse=['b'])
    settings = FakeSettings()
    scheduler = ConfirmationScheduler(settings, FakeSteam(), ratatoskr, service)
    scheduler._ensure_connected = lambda steamid: True

    scheduler._auto_store_account(STEAMID, {'kilowatt case'})
    for _ in range(200):
        if not service.busy(STEAMID):
            break
        time.sleep(0.01)
    time.sleep(0.05)   # on_finish runs right after the job finishes

    assert ratatoskr.batches == [('unit', ['a', 'b'], 'inventory', 'casket')]
    assert settings.history[0]['count'] == 1
    assert settings.history[0]['items'] == {'Kilowatt Case': 1}
    assert 'b' in scheduler._auto_store_refused[STEAMID]

    # the refused item is not offered again on the next sweep
    ratatoskr.inventory = [_item('b')]
    scheduler._auto_store_account(STEAMID, {'kilowatt case'})
    assert len(ratatoskr.batches) == 1


def test_old_plans_are_forgotten():
    service, _ = _service([_item('a')], [_casket('unit', 0)])
    old = service.plan_deposit(STEAMID, item_ids=['a'])
    service._plans[old['plan_id']]['created_at'] -= storage_deposit.PLAN_KEEP_SECONDS + 1
    service._plans[old['plan_id']]['job_id'] = 'finished-long-ago'
    service.plan_deposit(STEAMID, item_ids=['a'])
    assert old['plan_id'] not in service._plans


def test_a_plan_with_nothing_to_send_does_not_read_the_units():
    service, ratatoskr = _service([_item('medal', item_moveable=False)], [_casket('unit', 0)])
    calls = []
    ratatoskr.get_caskets = lambda steamid: calls.append(steamid) or {'caskets': []}
    plan = service.plan_deposit(STEAMID, names=['Kilowatt Case'], allow_overflow=True)
    assert plan['moves'] == 0 and calls == []


def test_a_dropped_move_queue_counts_nothing_as_moved():
    """A disconnect or a Ratatoskr restart drops the queue: its fresh, empty status must not read
    as "everything moved"."""
    service, _ = _service([_item('a'), _item('b')], [_casket('unit', 0)])
    service.ratatoskr.drop_queue = True
    job = _wait(service, service.execute(service.plan_deposit(STEAMID, item_ids=['a', 'b'])['plan_id'])['id'])
    assert job['done'] == 0 and job['failed'] == 2
    assert {f['code'] for f in job['result']['failed_items']} == {'unconfirmed'}
    assert service.storage(STEAMID)['free'] == STORAGE_CAPACITY   # no phantom deposit counted


def test_a_plan_made_before_another_run_finished_is_stale():
    service, _ = _service([_item('a'), _item('b')], [_casket('unit', 0)])
    early = service.plan_deposit(STEAMID, item_ids=['b'])
    _wait(service, service.execute(service.plan_deposit(STEAMID, item_ids=['a'])['plan_id'])['id'])
    with pytest.raises(CommandError) as error:
        service.execute(early['plan_id'])
    assert error.value.code == 'plan_stale'


def test_a_queue_that_never_goes_idle_sends_nothing(monkeypatch):
    service, ratatoskr = _service([_item('a')], [_casket('unit', 0)])
    ratatoskr.status = {'running': True, 'runId': 99}
    monkeypatch.setattr(service, '_wait_until_idle', lambda steamid: False)
    job = _wait(service, service.execute(service.plan_deposit(STEAMID, item_ids=['a'])['plan_id'])['id'])
    assert ratatoskr.batches == []
    assert job['result']['failed_items'][0]['code'] == 'busy'


def test_withdraw_sends_each_item_once():
    service, ratatoskr = _service([], [_casket('u1', 5)])
    plan = service.plan_withdraw(STEAMID, [{'item_id': 'x', 'casket_id': 'u1'}, {'item_id': 'x', 'casket_id': 'u1'}])
    assert plan['moves'] == 1


def test_auto_store_offers_items_that_were_never_sent_again():
    service, ratatoskr = _service([_item('a')], [_casket('unit', 0)], unsent=['a'])
    scheduler = ConfirmationScheduler(FakeSettings(), FakeSteam(), ratatoskr, service)
    scheduler._ensure_connected = lambda steamid: True
    scheduler._auto_store_account(STEAMID, {'kilowatt case'})
    for _ in range(200):
        if not service.busy(STEAMID):
            break
        time.sleep(0.01)
    assert 'a' not in scheduler._auto_store_refused.get(STEAMID, {})


def test_no_answer_after_a_lost_session_is_unconfirmed_and_offered_again():
    service, ratatoskr = _service([_item('a')], [_casket('unit', 0)])
    original = ratatoskr.move_batch

    def lose_session(*args, **kwargs):
        answer = original(*args, **kwargs)
        ratatoskr.status.update({'doneItemIDs': [], 'done': 0, 'failed': 1, 'failedItemIDs': ['a'],
                                 'unconfirmedItemIDs': ['a']})
        return answer
    ratatoskr.move_batch = lose_session
    job = _wait(service, service.execute(service.plan_deposit(STEAMID, item_ids=['a'])['plan_id'])['id'])
    assert job['result']['failed_items'][0]['code'] == 'unconfirmed'


def test_nothing_logs_an_account_out_between_two_batches_of_a_run():
    from ratatoskr_service import items_moving

    class IdleRatatoskr:
        def get_move_status(self, steamid):
            return {'running': False, 'pending': 0}
    idle = IdleRatatoskr()
    assert not items_moving(idle, STEAMID)
    idle.moves_in_progress = lambda steamid: True    # DepositService.busy: a run between batches
    assert items_moving(idle, STEAMID)
