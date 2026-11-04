"""Auto-Store: one watchlist per account, one item per command, the master view, Sweep now for
one account. Fakes only; nothing reaches Steam or Ratatoskr."""
import pytest

import auto_store
from auto_store import AutoStore
from scheduler import ConfirmationScheduler
from storage_deposit import CommandError


class FakeSettingsManager:
    def __init__(self, settings):
        self.settings = settings
        self.saves = 0

    def get_settings(self):
        return dict(self.settings)

    def save_settings(self, patch):
        self.saves += 1
        self.settings.update(patch)
        return True


class FakeStorage:
    def list_accounts(self):
        return ['1', '2', '3']


class FakeSteam:
    storage = FakeStorage()
    names = {'1': 'alpha', '2': 'bravo', '3': 'charlie'}

    def get_account(self, steamid):
        name = self.names.get(str(steamid))
        return {'account_name': name} if name else None


class FakeDeposit:
    def __init__(self, items=()):
        self.items = list(items)

    def inventory(self, steamid):
        return self.items


def make(settings=None, items=()):
    manager = FakeSettingsManager(settings or {})
    return AutoStore(manager, FakeSteam(), FakeDeposit(items)), manager


def test_the_shared_watchlist_is_copied_to_each_switched_on_account_once():
    store, manager = make({'auto_store_items': ['Recoil Case', 'Clutch Case'],
                           'auto_store_accounts': ['1', '3'],
                           'auto_store_watchlists': {'3': ['clutch case', 'Fracture Case']}})
    assert store.migrate_shared_watchlist()
    assert manager.settings['auto_store_watchlists'] == {
        '1': ['Recoil Case', 'Clutch Case'],
        '3': ['clutch case', 'Fracture Case', 'Recoil Case'],
    }
    assert manager.settings['auto_store_items'] == []
    assert not store.migrate_shared_watchlist()


def test_adding_an_item_keeps_the_others_and_touches_only_that_account():
    store, manager = make({'auto_store_watchlists': {'1': ['Recoil Case'], '2': ['Clutch Case']}})
    store.add_item('1', ' Fracture Case ')
    store.add_item('1', 'recoil case')  # already there in another letter case
    assert manager.settings['auto_store_watchlists'] == {
        '1': ['Recoil Case', 'Fracture Case'], '2': ['Clutch Case']}


def test_removing_an_item_removes_only_that_one():
    store, manager = make({'auto_store_watchlists': {'1': ['Recoil Case', 'Clutch Case'], '2': ['Recoil Case']}})
    store.remove_item('1', 'RECOIL CASE')
    assert manager.settings['auto_store_watchlists'] == {'1': ['Clutch Case'], '2': ['Recoil Case']}
    with pytest.raises(CommandError) as missing:
        store.remove_item('1', 'Recoil Case')
    assert missing.value.code == 'not_found'


def test_commands_refuse_an_unknown_account_or_a_missing_name():
    store, _ = make()
    for call, code in [(lambda: store.add_item('9', 'Recoil Case'), 'account_unknown'),
                       (lambda: store.add_item('', 'Recoil Case'), 'steamid_required'),
                       (lambda: store.add_item('1', '  '), 'name_required'),
                       (lambda: store.set_account('1', 'yes'), 'on_required'),
                       (lambda: store.set_enabled(None), 'enabled_required')]:
        with pytest.raises(CommandError) as refused:
            call()
        assert refused.value.code == code


def test_switching_one_account_never_changes_the_others():
    changes = []
    store, manager = make({'auto_store_accounts': ['2']})
    store.on_change = changes.append
    store.set_account('1', True)
    store.set_account('1', True)
    store.set_account('2', False)
    assert manager.settings['auto_store_accounts'] == ['1']
    assert len(changes) == 3


def test_a_sweep_acts_only_on_switched_on_accounts_with_their_own_watchlist():
    settings = {'auto_store_enabled': True, 'auto_store_accounts': ['1', '2'],
                'auto_store_watchlists': {'1': ['Recoil Case'], '2': [], '3': ['Clutch Case']}}
    assert auto_store.sweep_accounts(settings) == ['1']
    assert auto_store.watch_for(settings, '1') == {'recoil case'}
    assert auto_store.watch_for(settings, '3') == {'clutch case'}
    assert auto_store.sweep_accounts({**settings, 'auto_store_enabled': False}) == []
    assert auto_store.protected_accounts(settings) == ['1', '2']


def test_status_shows_each_account_and_the_master_view_with_moved_counts():
    store, _ = make({
        'auto_store_enabled': True, 'auto_store_accounts': ['1'],
        'auto_store_watchlists': {'1': ['Recoil Case'], '2': ['recoil case', 'Clutch Case']},
        'auto_store_history': [{'steamid': '1', 'count': 3, 'items': {'Recoil Case': 3}},
                               {'steamid': '2', 'count': 1, 'items': {'Clutch Case': 1}}],
    })
    status = store.status()
    assert [a['account_name'] for a in status['accounts']] == ['alpha', 'bravo', 'charlie']
    assert status['accounts'][0]['on'] and not status['accounts'][1]['on']
    assert status['accounts'][0]['items'] == [{'name': 'Recoil Case', 'moved': 3}]
    assert status['accounts'][0]['moved'] == 3 and status['accounts'][0]['sweep_refused'] is None
    assert status['accounts'][1]['sweep_refused']['code'] == 'account_off'
    recoil = status['master'][1]
    assert recoil['name'] == 'Recoil Case' and recoil['moved'] == 3
    assert [a['account_name'] for a in recoil['accounts']] == ['alpha', 'bravo']
    assert status['moved_total'] == 4


def test_suggestions_are_this_accounts_storable_items_not_watched_yet():
    items = [
        {'item_name': 'Recoil Case'},                                   # watched already
        {'item_name': 'Clutch Case'}, {'item_name': 'clutch case'},     # one suggestion
        {'item_name': 'Storage Unit', 'def_index': 1201},               # never storable
        {'item_name': 'Graffiti', 'item_moveable': False},              # never storable
        {'item_name': 'Traded Case', 'trade_locked': True},             # storable once protection ends
    ]
    store, _ = make({'auto_store_watchlists': {'1': ['Recoil Case']}}, items)
    assert store.suggestions('1')['suggestions'] == ['Clutch Case', 'Traded Case']


class SweepScheduler(ConfirmationScheduler):
    def __init__(self, settings):
        super().__init__(FakeSettingsManager(settings), FakeSteam(), ratatoskr_service=object(),
                         deposit_service=object())
        self.swept = []

    def _auto_store_account(self, steamid, watch):
        self.swept.append((steamid, watch))
        return {'job': {'id': 'job-1'}}


@pytest.mark.parametrize('settings, code', [
    ({'auto_store_enabled': False, 'auto_store_accounts': ['1'], 'auto_store_watchlists': {'1': ['A']}},
     'auto_store_off'),
    ({'auto_store_enabled': True, 'auto_store_accounts': ['2'], 'auto_store_watchlists': {'1': ['A']}},
     'account_off'),
    ({'auto_store_enabled': True, 'auto_store_accounts': ['1'], 'auto_store_watchlists': {}}, 'no_items'),
])
def test_sweep_now_refuses_when_auto_store_is_not_on_for_that_account(settings, code):
    with pytest.raises(CommandError) as refused:
        SweepScheduler(settings).sweep_account('1')
    assert refused.value.code == code


def test_sweep_now_sweeps_only_the_named_account():
    scheduler = SweepScheduler({'auto_store_enabled': True, 'auto_store_accounts': ['1', '2'],
                                'auto_store_watchlists': {'1': ['Recoil Case'], '2': ['Clutch Case']}})
    assert scheduler.sweep_account('1') == {'job': {'id': 'job-1'}}
    assert scheduler.swept == [('1', {'recoil case'})]


def test_the_shared_watchlist_is_kept_when_no_account_is_switched_on():
    store, manager = make({'auto_store_items': ['Recoil Case'], 'auto_store_accounts': []})
    assert not store.migrate_shared_watchlist()
    assert manager.settings['auto_store_items'] == ['Recoil Case']
    assert 'auto_store_watchlists' not in manager.settings


def test_a_removed_accounts_leftover_watchlist_can_still_be_emptied():
    store, manager = make({'auto_store_watchlists': {'99': ['Recoil Case']}})
    store.remove_item('99', 'Recoil Case')
    assert manager.settings['auto_store_watchlists'] == {'99': []}
    with pytest.raises(CommandError) as refused:
        store.add_item('99', 'Clutch Case')
    assert refused.value.code == 'account_unknown'


def test_a_second_run_on_the_same_account_is_refused_while_one_works():
    import threading

    started, release = threading.Event(), threading.Event()

    class SlowScheduler(ConfirmationScheduler):
        def __init__(self):
            super().__init__(FakeSettingsManager({}), FakeSteam())

        def _auto_store_run(self, steamid, watch):
            started.set()
            release.wait(5)
            return {'job': None, 'nothing': 'nothing_to_store'}

    scheduler = SlowScheduler()
    first = threading.Thread(target=scheduler._auto_store_account, args=('1', {'a'}))
    first.start()
    started.wait(5)
    with pytest.raises(CommandError) as refused:
        scheduler._auto_store_account('1', {'a'})
    assert refused.value.code == 'busy'
    assert scheduler._auto_store_account('2', {'a'}) == {'job': None, 'nothing': 'nothing_to_store'}
    release.set()
    first.join(5)
