"""Portfolio snapshots are gzip-compressed on disk (~10x smaller) while every
snapshot stays independently, losslessly restorable. These guard that contract,
including backward-compatibility with any legacy plain-.json snapshot.
"""
import gzip
import json
import os

from draupnir_backup_service import BackupService


def _seed(tmp_path, payload):
    src = tmp_path / 'portfolios.json'
    src.write_text(json.dumps(payload))
    return BackupService(str(src), backup_dir=str(tmp_path / 'bk'))


def test_snapshot_writes_gzip_and_roundtrips(tmp_path):
    payload = {'portfolios': {'p1': {'transactions': [{'item_name': 'AK-47 | Redline'}]}}}
    bs = _seed(tmp_path, payload)

    name = bs.snapshot('manual')
    assert name.endswith('.json.gz')
    # On disk it's really gzip (magic bytes), and much smaller than the source.
    on_disk = os.path.join(bs.backup_dir, name)
    with open(on_disk, 'rb') as f:
        assert f.read(2) == b'\x1f\x8b'

    # read_backup hands back the decompressed JSON, byte-identical to the source.
    raw = bs.read_backup(name)
    assert json.loads(raw) == payload


def test_dedup_still_works_when_compressed(tmp_path):
    bs = _seed(tmp_path, {'portfolios': {}})
    assert bs.snapshot('manual') is not None
    # Identical content → no second snapshot.
    assert bs.snapshot('manual') is None


def test_restore_from_gzip(tmp_path):
    payload = {'portfolios': {'p1': {'transactions': [{'item_name': 'Fracture Case'}]}}}
    bs = _seed(tmp_path, payload)
    name = bs.snapshot('manual')

    # Clobber the live file, then restore the compressed snapshot over it.
    (tmp_path / 'portfolios.json').write_text('CORRUPTED')
    res = bs.restore(name)
    assert res['ok'] is True
    assert json.loads((tmp_path / 'portfolios.json').read_text()) == payload


def test_legacy_plain_json_snapshot_is_still_read(tmp_path):
    """A pre-migration plain .json snapshot must still list and restore."""
    payload = {'portfolios': {'p1': {'transactions': []}}}
    bs = _seed(tmp_path, payload)

    # Hand-craft a legacy snapshot the old code would have written.
    import hashlib
    data = json.dumps(payload).encode()
    digest = hashlib.sha1(data).hexdigest()[:8]
    legacy = f'portfolios__20250101T000000Z__change__{digest}.json'
    with open(os.path.join(bs.backup_dir, legacy), 'wb') as f:
        f.write(data)

    names = [b['name'] for b in bs.list_backups()]
    assert legacy in names
    assert json.loads(bs.read_backup(legacy)) == payload
