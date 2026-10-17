"""An inventory scan that cannot log into some accounts keeps their items from the
previous scan (2026-10-16: one morning's scan reached 1 of 21 accounts and the Arbitrage
filters showed a single account)."""

import json

import huginn_service
from huginn_service import HuginnService


class _Steam:
    def __init__(self, accounts):
        self.accounts = accounts

    def get_all_accounts_data(self):
        return [{'steamid': steam_id, 'account_name': name} for steam_id, name in self.accounts]

    def get_account(self, steam_id):
        return {'account_name': dict(self.accounts)[steam_id]}

    def get_password(self, steam_id):
        return 'password'


class _Ratatoskr:
    def __init__(self, reachable, inventories):
        self.reachable = reachable
        self.inventories = inventories

    def get_status(self, steam_id):
        return {'status': 'connected' if steam_id in self.reachable else 'disconnected'}

    def login(self, **kwargs):
        return {'error': 'Steam Guard code rejected'}

    def get_inventory(self, steam_id):
        return {'items': [{'item_name': name, 'item_id': f'{steam_id}-{i}'}
                          for i, name in enumerate(self.inventories.get(steam_id, []))]}

    def get_caskets(self, steam_id):
        return {'caskets': []}

    def disconnect(self, steam_id):
        return {'success': True}

    def get_move_status(self, steam_id):
        return {'running': False, 'pending': 0}


def _service(monkeypatch, tmp_path, reachable, inventories):
    monkeypatch.setattr(huginn_service, 'CACHE_PATH', str(tmp_path / 'scan.json'))
    monkeypatch.setattr(HuginnService, '_record_login', staticmethod(lambda *args, **kwargs: None))
    steam = _Steam([('1', 'alpha'), ('2', 'bravo'), ('3', 'charlie')])
    return HuginnService(steam_service=steam, ratatoskr_service=_Ratatoskr(reachable, inventories))


def test_unreachable_accounts_keep_their_items_from_the_previous_scan(monkeypatch, tmp_path):
    inventories = {'1': ['Case A'], '2': ['Case B', 'Case A'], '3': ['Case C']}
    first = _service(monkeypatch, tmp_path, {'1', '2', '3'}, inventories).scan()
    assert first['accounts_skipped'] == 0
    assert set(first['account_scans']) == {'alpha', 'bravo', 'charlie'}

    inventories['1'] = ['Case D']                      # alpha changed since
    second = _service(monkeypatch, tmp_path, {'1'}, inventories).scan()
    by_hash = second['by_hash']
    assert second['accounts_scanned'] == 1 and second['accounts_skipped'] == 2
    assert {i['account_name'] for e in by_hash.values() for i in e['instances']} == {'alpha', 'bravo', 'charlie'}
    assert by_hash['Case A']['count'] == 1            # bravo's copy; alpha's is gone
    assert by_hash['Case D']['count'] == 1
    assert second['total_items'] == 4
    assert second['account_scans']['bravo'] == {'scanned_at': first['scan_timestamp'], 'carried_over': True}
    assert second['account_scans']['alpha']['carried_over'] is False

    # A third scan that again misses bravo keeps bravo's original scan time.
    third = _service(monkeypatch, tmp_path, {'1', '3'}, inventories).scan()
    assert third['account_scans']['bravo']['scanned_at'] == first['scan_timestamp']
    assert json.loads((tmp_path / 'scan.json').read_text())['by_hash']['Case B']['count'] == 1


def test_an_account_no_scan_ever_reached_is_listed_without_a_time(monkeypatch, tmp_path):
    result = _service(monkeypatch, tmp_path, {'1'}, {'1': ['Case A']}).scan()
    assert result['account_scans']['bravo'] == {'scanned_at': None, 'carried_over': True}
    assert set(result['by_hash']) == {'Case A'}
