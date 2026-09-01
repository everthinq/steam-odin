"""Persistence + corruption self-healing through the real PortfolioService
(Critical fix #1 atomic writes, #7 recovery-on-corrupt)."""
import json

from portfolio_service import PortfolioService


def _make(tmp_path):
    return PortfolioService(huginn_service=None,
                            path=str(tmp_path / 'portfolios.json'))


def test_add_transaction_survives_reload(tmp_path):
    svc = _make(tmp_path)
    p = svc.create_portfolio('acct1')
    svc.add_transaction(p['id'], {'item_name': 'Fracture Case',
                                  'type': 'buy', 'qty': 10, 'price': 0.41})
    # Fresh instance reading the same file must see the write (atomic + durable).
    svc2 = _make(tmp_path)
    got = svc2.get_portfolio(p['id'])
    assert got is not None
    assert got['txn_count'] == 1


def test_write_is_atomic_no_temp_litter(tmp_path):
    svc = _make(tmp_path)
    p = svc.create_portfolio('acct1')
    svc.add_transaction(p['id'], {'item_name': 'Revolution Case',
                                  'type': 'buy', 'qty': 5, 'price': 0.18})
    leftovers = [f for f in tmp_path.iterdir() if f.name.startswith('.tmp-')]
    assert leftovers == []


class _StubBackup:
    """Minimal BackupService stand-in exposing the two methods _load() uses."""
    def __init__(self, snapshots):
        # snapshots: list of (name, dict) newest-first
        self._snaps = snapshots

    def list_backups(self):
        return [{'name': n} for n, _ in self._snaps]

    def read_backup(self, name):
        for n, data in self._snaps:
            if n == name:
                return json.dumps(data).encode()
        return None


def test_corrupt_file_recovers_from_backup(tmp_path):
    path = tmp_path / 'portfolios.json'
    # A good, known state lives in the (stubbed) backup.
    good = {'portfolios': {'p1': {'id': 'p1', 'name': 'saved',
                                  'transactions': [], 'created_at': 't',
                                  'updated_at': 't'}}}
    # The live file is truncated garbage.
    path.write_text('{ "portfolios": { corrupted ')

    svc = PortfolioService(huginn_service=None, path=str(path))
    # Boot load couldn't recover yet (no backup wired) -> flagged.
    assert getattr(svc, '_last_load_corrupt', False) is True
    # Wiring the backup triggers a recovery re-load.
    svc.set_backup(_StubBackup([('portfolios__x__change__deadbeef.json', good)]))
    assert 'p1' in svc._data['portfolios']
    assert svc._data['portfolios']['p1']['name'] == 'saved'


def test_missing_file_is_empty_not_corrupt(tmp_path):
    # A brand-new install (no file) is legitimately empty, never flagged corrupt.
    svc = PortfolioService(huginn_service=None,
                           path=str(tmp_path / 'does_not_exist.json'))
    assert svc._data == {'portfolios': {}}
    assert getattr(svc, '_last_load_corrupt', False) is False
