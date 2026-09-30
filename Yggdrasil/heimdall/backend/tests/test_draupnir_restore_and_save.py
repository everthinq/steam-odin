"""Draupnir store safety: a restore goes through the store lock (so it cannot be
silently undone), a failed write is reported instead of swallowed, oversold
positions are flagged without changing the profit/loss math, and backup
snapshots taken within one second still sort in order."""
import json
from datetime import datetime, timezone

import pytest
from flask import Flask

import draupnir_backup_service
import draupnir_service
from draupnir_backup_service import BackupService
from draupnir_service import DraupnirService, PortfolioSaveError


def _wired(tmp_path):
    svc = DraupnirService(huginn_service=None, path=str(tmp_path / 'portfolios.json'))
    backup = BackupService(svc.path, backup_dir=str(tmp_path / 'backups'))
    svc.set_backup(backup)
    return svc, backup


def _txn(item, typ, qty, price):
    return {'item_name': item, 'type': typ, 'qty': qty, 'price': price}


# ---- restore ---------------------------------------------------------------

def test_restore_bytes_replaces_memory_and_file(tmp_path):
    svc, _ = _wired(tmp_path)
    svc.create_portfolio('before')
    wanted = {'portfolios': {'p1': {'id': 'p1', 'name': 'restored', 'created_at': 't',
                                    'updated_at': 't', 'transactions': []}}}
    svc.restore_bytes(json.dumps(wanted).encode())
    assert svc._data == wanted
    with open(svc.path) as f:
        assert json.load(f) == wanted


@pytest.mark.parametrize('bad', [b'not json', b'[]', b'{}', b'{"portfolios": []}',
                                 b'{"portfolios": {"p1": {"name": "no transactions"}}}'])
def test_restore_bytes_rejects_invalid_backups_and_keeps_the_store(tmp_path, bad):
    svc, _ = _wired(tmp_path)
    p = svc.create_portfolio('keep me')
    with pytest.raises(ValueError):
        svc.restore_bytes(bad)
    assert p['id'] in svc._data['portfolios']


def test_restore_through_the_store_survives_a_later_write(tmp_path):
    """The old flow overwrote the file then reloaded; a write landing in
    between put the pre-restore state back. Now memory is swapped under the
    store lock, so the next write builds on the restored state."""
    svc, backup = _wired(tmp_path)
    old = svc.create_portfolio('old state')
    snapshot_name = backup.list_backups()[0]['name']      # holds only 'old state'
    newer = svc.create_portfolio('added after the snapshot')

    result = backup.restore(snapshot_name, store=svc)
    assert result == {'ok': True, 'error': None}
    assert set(svc._data['portfolios']) == {old['id']}
    svc.create_portfolio('written after the restore')     # must not resurrect `newer`
    with open(svc.path) as f:
        on_disk = json.load(f)['portfolios']
    assert newer['id'] not in on_disk and old['id'] in on_disk
    # The undo point (the state holding `newer`) is kept in the history; it is
    # the 'change' snapshot already, so the pre-restore one deduplicates onto it.
    assert any(newer['id'] in json.loads(backup.read_backup(b['name']))['portfolios']
               for b in backup.list_backups())


def test_restore_refuses_an_unreadable_snapshot_before_touching_anything(tmp_path):
    svc, backup = _wired(tmp_path)
    svc.create_portfolio('live')
    import gzip
    import os
    name = 'portfolios__20260101T000000Z__manual__deadbeef.json.gz'
    with gzip.open(os.path.join(backup.backup_dir, name), 'wb') as f:
        f.write(b'{"portfolios": "wrong"}')
    before = backup.list_backups()
    result = backup.restore(name, store=svc)
    assert result['ok'] is False and 'unreadable' in result['error']
    assert backup.list_backups() == before                 # not even a pre-restore snapshot
    assert len(svc._data['portfolios']) == 1


def test_restore_route_uses_the_store(tmp_path, monkeypatch):
    from context import ctx
    from routes.draupnir import bp
    svc, backup = _wired(tmp_path)
    kept = svc.create_portfolio('kept')
    name = backup.list_backups()[0]['name']
    svc.create_portfolio('dropped by the restore')
    monkeypatch.setattr(ctx, 'draupnir_service', svc)
    monkeypatch.setattr(ctx, 'draupnir_backup', backup)
    app = Flask(__name__)
    app.register_blueprint(bp)
    response = app.test_client().post('/api/draupnir/portfolios/backups/restore',
                                      json={'name': name})
    assert response.status_code == 200
    assert set(svc._data['portfolios']) == {kept['id']}


# ---- failed writes are reported ----------------------------------------------

def test_failed_write_raises_and_rolls_memory_back_to_disk(tmp_path, monkeypatch):
    svc, _ = _wired(tmp_path)
    p = svc.create_portfolio('acct')

    def refuse(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(draupnir_service, 'atomic_write_json', refuse)
    with pytest.raises(PortfolioSaveError):
        svc.add_transaction(p['id'], _txn('Fracture Case', 'buy', 1, 0.41))
    # Memory matches the file again, so a retry does not double-add.
    assert svc._get(p['id'])['transactions'] == []


def test_failed_write_answers_500_from_the_route(tmp_path, monkeypatch):
    from context import ctx
    from routes.draupnir import bp
    svc, _ = _wired(tmp_path)
    p = svc.create_portfolio('acct')
    monkeypatch.setattr(ctx, 'draupnir_service', svc)
    monkeypatch.setattr(draupnir_service, 'atomic_write_json',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('read-only')))
    app = Flask(__name__)
    app.register_blueprint(bp)
    response = app.test_client().post(f"/api/draupnir/portfolios/{p['id']}/transactions",
                                      json=_txn('Fracture Case', 'buy', 1, 0.41))
    assert response.status_code == 500
    body = response.get_json()
    assert body['saved'] is False and 'read-only' in body['error']


def test_failed_restore_write_keeps_the_previous_store(tmp_path, monkeypatch):
    svc, _ = _wired(tmp_path)
    p = svc.create_portfolio('acct')
    monkeypatch.setattr(draupnir_service, 'atomic_write_json',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('nope')))
    with pytest.raises(PortfolioSaveError):
        svc.restore_bytes(b'{"portfolios": {}}')
    assert p['id'] in svc._data['portfolios']


# ---- oversold flag -------------------------------------------------------------

def test_normal_holding_is_not_flagged_oversold():
    (h,) = DraupnirService._holdings([_txn('A', 'buy', 5, 2.0), _txn('A', 'sell', 2, 3.0)], None)
    assert h['oversold'] is False and h['oversold_qty'] == 0


def test_oversold_holding_is_flagged_and_math_is_unchanged():
    (h,) = DraupnirService._holdings([_txn('A', 'buy', 2, 2.0), _txn('A', 'sell', 5, 3.0)],
                                     {'A': 4.0})
    assert h['oversold'] is True and h['oversold_qty'] == 3
    assert h['net_qty'] == -3                          # per-account view unchanged
    assert h['realized_pl'] == 5.0                     # 15 - 2.0 * 5, as before
    assert h['market_value'] is None and h['cost_basis'] == 0.0


def test_combined_ledger_does_not_let_one_oversold_account_cancel_another(tmp_path):
    svc, _ = _wired(tmp_path)
    holder = svc.create_portfolio('holder')
    oversold = svc.create_portfolio('oversold')
    svc.add_transaction(holder['id'], _txn('A', 'buy', 10, 1.0))
    svc.add_transaction(oversold['id'], _txn('A', 'buy', 1, 1.0))
    svc.add_transaction(oversold['id'], _txn('A', 'sell', 4, 2.0))
    ledger = svc.combined_ledger({'A': 3.0})
    (h,) = ledger['holdings']
    assert h['net_qty'] == 10                          # was 10 + (-3) = 7
    assert h['market_value'] == 30.0
    assert h['oversold'] is True and h['oversold_qty'] == 3
    assert h['realized_pl'] == 4.0                     # 8 - 1.0 * 4, summed unchanged
    assert h['avg_cost'] == 1.0


def test_combined_ledger_unchanged_for_normal_data(tmp_path):
    svc, _ = _wired(tmp_path)
    a = svc.create_portfolio('a')
    b = svc.create_portfolio('b')
    svc.add_transaction(a['id'], _txn('A', 'buy', 10, 1.0))
    svc.add_transaction(a['id'], _txn('A', 'sell', 4, 2.0))
    svc.add_transaction(b['id'], _txn('A', 'buy', 5, 3.0))
    (h,) = svc.combined_ledger({'A': 2.5})['holdings']
    assert h['net_qty'] == 11
    assert h['cost_basis'] == 21.0                     # 6 * 1.0 + 5 * 3.0
    assert h['market_value'] == 27.5
    assert h['realized_pl'] == 4.0
    assert h['oversold'] is False


# ---- sub-second snapshot names -------------------------------------------------

def test_snapshots_within_one_second_sort_in_order(tmp_path, monkeypatch):
    source = tmp_path / 'portfolios.json'
    backup = BackupService(str(source), backup_dir=str(tmp_path / 'backups'))
    moments = iter([datetime(2026, 9, 30, 12, 0, 0, 900000, tzinfo=timezone.utc),
                    datetime(2026, 9, 30, 12, 0, 0, 100, tzinfo=timezone.utc)])
    # Deliberately give the SECOND snapshot a smaller microsecond than a
    # whole-second name would hide; the first one is taken later in the list.
    order = []
    for index, moment in enumerate(sorted(moments)):
        monkeypatch.setattr(draupnir_backup_service, '_utcnow', lambda m=moment: m)
        source.write_text(json.dumps({'portfolios': {}, 'n': index}))
        order.append(backup.snapshot('change'))
    assert [b['name'] for b in backup.list_backups()] == list(reversed(order))
    assert '20260930T120000000100Z' in order[0]


def test_old_whole_second_names_are_still_read(tmp_path):
    source = tmp_path / 'portfolios.json'
    source.write_text('{"portfolios": {}}')
    backup = BackupService(str(source), backup_dir=str(tmp_path / 'backups'))
    import hashlib
    import os
    data = b'{"portfolios": {}}'
    old = f'portfolios__20250101T000000Z__change__{hashlib.sha1(data).hexdigest()[:8]}.json'
    with open(os.path.join(backup.backup_dir, old), 'wb') as f:
        f.write(data)
    newer = backup.snapshot('manual')
    source.write_text('{"portfolios": {"x": {"transactions": []}}}')
    newest = backup.snapshot('manual')
    names = [b['name'] for b in backup.list_backups()]
    assert names == [newest, old] or names == [newest, newer, old]
    assert backup.list_backups()[-1]['timestamp'] == '2025-01-01T00:00:00+00:00'
