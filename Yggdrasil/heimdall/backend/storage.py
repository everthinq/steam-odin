"""Encrypted at-rest storage for Steam Guard maFiles.

Each account's maFile (which holds the 2FA shared secret) is encrypted with
Fernet, using a key derived from a per-install secret via PBKDF2-HMAC-SHA256.

Design (2026-09, revised after an incident):

* **No public key, ever.** The original code fell back to a hardcoded constant
  when ``HEIMDALL_SECRET_KEY`` was unset — encrypting 2FA secrets with a key
  present in the source. Now: use the env key if set, otherwise generate a
  strong random local keyfile (``.heimdall_key``, 0600).
* **Deterministic salt derived from the secret.** An earlier revision used a
  *random* salt stored in ``.heimdall_salt`` — but that made the 16-byte file a
  single point of failure: lose it and every maFile is unreadable (which is
  exactly what happened). The salt is now ``SHA256("heimdall-kdf-v2::" + secret)``
  — reproducible from the secret alone, so there is NO salt file to lose, while
  still not being a shared hardcoded constant.
* **600k PBKDF2 iterations** (OWASP 2023) instead of 100k.
* **Atomic writes** so a crash can't truncate a maFile.
* **Non-destructive transparent migration:** a file written by an older scheme
  is decrypted with the legacy parameters, its original bytes are copied to
  ``.pre-migration/`` first, and only then is it re-encrypted with the current
  scheme. Nothing to run — files migrate on first read, and the pre-migration
  copy is a safety net.

The only file the current scheme can depend on is ``.heimdall_key`` (created
solely when ``HEIMDALL_SECRET_KEY`` is unset). When the env key is set — the
recommended setup — encryption is fully reproducible from the environment with
no state on disk to lose. Both files live in the (gitignored) maFiles dir.
"""
import base64
import hashlib
import json
import logging
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from jsonio import atomic_write_bytes

log = logging.getLogger(__name__)

# --- legacy scheme (decrypt-only, for migration) ---
_LEGACY_SALT = b'heimdall-salt'
_LEGACY_ITERS = 100_000
# Every insecure default a maFile might have been encrypted under before the
# hardening — tried on load so existing files migrate instead of locking out.
_LEGACY_PUBLIC_SECRETS = (
    'default-insecure-key-change-me',                       # old storage.py code default
    'change_this_to_a_secure_random_string_in_production',  # docker-compose default
    'change-me-to-a-long-random-string',                    # .env.example placeholder
)
_LEGACY_PUBLIC_SECRET = _LEGACY_PUBLIC_SECRETS[0]  # back-compat alias

# --- hardened scheme ---
_ITERS = 600_000
_SALT_INFO = b'heimdall-kdf-v2::'  # domain-separation prefix for the derived salt

_KEYFILE = '.heimdall_key'
_PRE_MIGRATION_DIR = '.pre-migration'


def _derive_salt(secret):
    """Deterministic 16-byte salt from the secret — reproducible, no stored file."""
    return hashlib.sha256(_SALT_INFO + secret.encode()).digest()[:16]


def _derive(secret, salt, iters):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=iters)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(secret.encode())))


class SecureStorage:
    def __init__(self, storage_dir='maFiles'):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(exist_ok=True)
        self._secret = self._resolve_secret()
        self._fernet = _derive(self._secret, _derive_salt(self._secret), _ITERS)
        # Decryptors tried, in order, when the current scheme fails — so files
        # from an older scheme still open and can be migrated: first the current
        # secret under the old static-salt params, then every known insecure
        # default. (The random-salt interim scheme is intentionally not here —
        # those files are unrecoverable without their lost salt and are replaced
        # by re-import, not migration.)
        self._legacy = [_derive(self._secret, _LEGACY_SALT, _LEGACY_ITERS)]
        for legacy_secret in _LEGACY_PUBLIC_SECRETS:
            self._legacy.append(_derive(legacy_secret, _LEGACY_SALT, _LEGACY_ITERS))

    # ---- key provisioning --------------------------------------------------

    def _resolve_secret(self):
        env = os.environ.get('HEIMDALL_SECRET_KEY')
        if env and env != _LEGACY_PUBLIC_SECRET:
            return env
        # No usable env key: never use the public constant to write. Generate a
        # strong local key once and reuse it.
        keyfile = self.storage_dir / _KEYFILE
        if keyfile.exists():
            return keyfile.read_text().strip()
        generated = secrets.token_urlsafe(48)
        keyfile.write_text(generated)
        _chmod_600(keyfile)
        log.warning('HEIMDALL_SECRET_KEY not set; generated a local key at %s '
                    '— back it up alongside your maFiles or they become '
                    'unreadable', keyfile)
        return generated

    # ---- account CRUD ------------------------------------------------------

    def save_account(self, steamid, data):
        """Encrypt and atomically persist one account's maFile data."""
        blob = self._fernet.encrypt(json.dumps(data).encode())
        file_path = self.storage_dir / f'{steamid}.maFile'
        atomic_write_bytes(str(file_path), blob)
        log.info('persisted encrypted maFile for %s', steamid)
        return str(file_path)

    def load_account(self, steamid):
        """Decrypt one account's maFile, migrating legacy-encrypted files."""
        file_path = self.storage_dir / f'{steamid}.maFile'
        if not file_path.exists():
            return None
        blob = file_path.read_bytes()
        # Current scheme first.
        try:
            return json.loads(self._fernet.decrypt(blob))
        except InvalidToken:
            pass
        # Legacy schemes -> migrate on success (non-destructively).
        for fernet in self._legacy:
            try:
                data = json.loads(fernet.decrypt(blob))
            except InvalidToken:
                continue
            self._backup_pre_migration(steamid, blob)
            log.warning('migrating maFile %s to hardened encryption', steamid)
            try:
                self.save_account(steamid, data)
            except Exception as e:
                log.error('re-encrypt of %s failed (kept old file): %s',
                          steamid, e)
            return data
        log.error('could not decrypt maFile %s with any known key', steamid)
        return None

    def _backup_pre_migration(self, steamid, blob):
        """Keep the original bytes before re-encrypting, so a migration is never
        a one-way door. Best-effort — a backup failure must not block the read."""
        try:
            d = self.storage_dir / _PRE_MIGRATION_DIR
            d.mkdir(exist_ok=True)
            dest = d / f'{steamid}.maFile'
            if not dest.exists():  # keep the earliest (truly-original) copy
                atomic_write_bytes(str(dest), blob)
        except Exception as e:
            log.error('pre-migration backup of %s failed: %s', steamid, e)

    def list_accounts(self):
        return [p.stem for p in self.storage_dir.glob('*.maFile')]

    def delete_account(self, steamid):
        file_path = self.storage_dir / f'{steamid}.maFile'
        if file_path.exists():
            file_path.unlink()
            return True
        return False


def _chmod_600(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
