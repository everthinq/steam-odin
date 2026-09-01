"""Hardened maFile encryption + transparent legacy migration (Critical fix #2).

The critical guarantee: a maFile written by the OLD scheme (static salt, 100k
iters, public fallback key) must still decrypt AND get re-encrypted with the new
scheme — no lockout of the 21 existing accounts.
"""
import base64
import json

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import storage
from storage import SecureStorage


def _legacy_fernet(secret):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=storage._LEGACY_SALT, iterations=storage._LEGACY_ITERS)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(secret.encode())))


def test_save_load_roundtrip_new_scheme(tmp_path, monkeypatch):
    monkeypatch.setenv('HEIMDALL_SECRET_KEY', 'a-real-strong-key')
    st = SecureStorage(storage_dir=str(tmp_path))
    st.save_account('7656119', {'shared_secret': 'abc', 'account_name': 'x'})
    assert st.load_account('7656119') == {'shared_secret': 'abc',
                                          'account_name': 'x'}


def test_no_temp_litter_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv('HEIMDALL_SECRET_KEY', 'a-real-strong-key')
    st = SecureStorage(storage_dir=str(tmp_path))
    st.save_account('123', {'shared_secret': 's'})
    assert [f for f in tmp_path.iterdir() if f.name.startswith('.tmp-')] == []


def test_migrates_legacy_public_key_file(tmp_path, monkeypatch):
    # Simulate an OLD file encrypted with the public fallback key.
    blob = _legacy_fernet(storage._LEGACY_PUBLIC_SECRET).encrypt(
        json.dumps({'shared_secret': 'legacy-secret'}).encode())
    (tmp_path / '999.maFile').write_bytes(blob)

    # New install with a real env key. Load must still succeed (via legacy path).
    monkeypatch.setenv('HEIMDALL_SECRET_KEY', 'a-real-strong-key')
    st = SecureStorage(storage_dir=str(tmp_path))
    got = st.load_account('999')
    assert got == {'shared_secret': 'legacy-secret'}

    # And the file must now be re-encrypted under the NEW scheme: a fresh
    # instance decrypts it with the current fernet directly (no legacy needed).
    st2 = SecureStorage(storage_dir=str(tmp_path))
    raw = (tmp_path / '999.maFile').read_bytes()
    assert json.loads(st2._fernet.decrypt(raw)) == {'shared_secret': 'legacy-secret'}


def test_generates_local_key_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv('HEIMDALL_SECRET_KEY', raising=False)
    st = SecureStorage(storage_dir=str(tmp_path))
    # A strong local key was generated (never the public constant).
    assert (tmp_path / storage._KEYFILE).exists()
    assert st._secret != storage._LEGACY_PUBLIC_SECRET
    assert len(st._secret) >= 40
    # And it round-trips.
    st.save_account('1', {'shared_secret': 'z'})
    assert st.load_account('1') == {'shared_secret': 'z'}


def test_salt_is_deterministic_no_sidecar_file_needed(tmp_path, monkeypatch):
    """Incident regression: encryption must not depend on any deletable sidecar
    file. A fresh instance (after wiping every dotfile) must still decrypt."""
    monkeypatch.setenv('HEIMDALL_SECRET_KEY', 'a-real-strong-key')
    st = SecureStorage(storage_dir=str(tmp_path))
    st.save_account('42', {'shared_secret': 'seed', 'identity_secret': 'id'})

    # Nuke any sidecar dotfiles the dir might hold (salt/key). None should matter.
    for p in tmp_path.glob('.*'):
        if p.is_file():
            p.unlink()

    st2 = SecureStorage(storage_dir=str(tmp_path))
    assert st2.load_account('42') == {'shared_secret': 'seed', 'identity_secret': 'id'}


def test_migration_keeps_pre_migration_backup(tmp_path, monkeypatch):
    """A legacy-encrypted file must be copied to .pre-migration/ before it is
    re-encrypted, so migration is never a one-way door."""
    blob = _legacy_fernet(storage._LEGACY_PUBLIC_SECRET).encrypt(
        json.dumps({'shared_secret': 's'}).encode())
    (tmp_path / '7.maFile').write_bytes(blob)
    monkeypatch.setenv('HEIMDALL_SECRET_KEY', 'a-real-strong-key')
    st = SecureStorage(storage_dir=str(tmp_path))
    assert st.load_account('7') == {'shared_secret': 's'}
    backup = tmp_path / storage._PRE_MIGRATION_DIR / '7.maFile'
    assert backup.exists() and backup.read_bytes() == blob
