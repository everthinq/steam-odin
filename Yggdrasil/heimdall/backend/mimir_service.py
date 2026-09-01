"""Mimir — the credential vault.

Named for the keeper of secret knowledge whose counsel Odin sought at the well
of wisdom. Mimir stores Steam account credentials (login, password, email and a
free-form comment) encrypted at rest with the SAME Fernet key that protects the
maFiles — so a password is never stored in weaker terms than the 2FA seed that
already lives beside it.

Storage: a single encrypted blob ``credentials.vault`` in the (gitignored)
maFiles directory. Records are keyed by Steam login name (``account_name`` in a
maFile), which lets :meth:`SteamService.get_password` resolve a password from a
steamid: steamid -> maFile.account_name -> vault.get_by_login.

The vault is a SUPERSET of the linked accounts: it may hold logins that have no
maFile yet (extra/parked accounts), and there may be maFiles with no vault entry
(e.g. the main accounts, whose passwords were never in the imported list).
"""
import logging
import re
import threading
import uuid
from datetime import datetime, timezone

from cryptography.fernet import InvalidToken

from jsonio import atomic_write_bytes

logger = logging.getLogger(__name__)

# A field is treated as the email column only if the WHOLE field is a valid
# address. This is what lets the parser survive passwords that contain '@'
# (e.g. ``7@1qvy-1D6|57_km``): those are not full emails, so they stay password.
_EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')

_VAULT_FILENAME = 'credentials.vault'


def _now():
    return datetime.now(timezone.utc).isoformat()


def parse_credentials_text(text):
    """Parse ``login;password;email;comment`` lines into records.

    Robust to the two gotchas in real exports:

    * **Passwords that contain ``;``** — the password is everything between the
      login and the email column, re-joined with ``;``.
    * **Passwords that contain ``@``** — the email is the first field that is a
      *complete* address (strict regex), not merely the first field with an '@'.

    Returns ``(rows, warnings)``. ``rows`` is a list of
    ``{login, password, email, comment}`` dicts; ``warnings`` is a list of
    human-readable strings for lines that parsed oddly (kept, not dropped).
    """
    rows, warnings = [], []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        fields = raw.split(';')
        login = fields[0].strip()
        if not login:
            warnings.append(f'line {lineno}: no login — skipped')
            continue
        email_idx = next(
            (i for i in range(1, len(fields)) if _EMAIL_RE.match(fields[i].strip())),
            None,
        )
        if email_idx is None:
            password = ';'.join(fields[1:]).strip()
            email, comment = '', ''
            warnings.append(f'line {lineno} ({login}): no email detected')
        else:
            password = ';'.join(fields[1:email_idx]).strip()
            email = fields[email_idx].strip()
            comment = ';'.join(fields[email_idx + 1:]).strip()
        if not password:
            warnings.append(f'line {lineno} ({login}): empty password')
        rows.append({'login': login, 'password': password,
                     'email': email, 'comment': comment})
    return rows, warnings


class MimirService:
    """Encrypted credential vault, backed by the shared :class:`SecureStorage`."""

    def __init__(self, storage):
        self.storage = storage
        self._path = storage.storage_dir / _VAULT_FILENAME
        self._lock = threading.RLock()
        self._records = self._load()

    # ---- persistence -------------------------------------------------------

    def _load(self):
        if not self._path.exists():
            return []
        try:
            blob = self._path.read_bytes()
            data = self.storage.decrypt_json(blob)
            recs = data.get('records', []) if isinstance(data, dict) else []
            return [self._normalize(r) for r in recs]
        except (InvalidToken, ValueError) as e:
            logger.error('Mimir vault unreadable (%s) — starting empty; the file '
                         'is left untouched at %s', e, self._path)
            return []

    def _save(self):
        blob = self.storage.encrypt_json({'version': 1, 'records': self._records})
        atomic_write_bytes(str(self._path), blob)

    @staticmethod
    def _normalize(r):
        return {
            'id': r.get('id') or uuid.uuid4().hex,
            'login': (r.get('login') or '').strip(),
            'password': r.get('password') or '',
            'email': (r.get('email') or '').strip(),
            'comment': (r.get('comment') or '').strip(),
            'created_at': r.get('created_at') or _now(),
            'updated_at': r.get('updated_at') or _now(),
            # Login-health, populated by record_login_result after a Ratatoskr
            # login attempt (scan or per-row test). status: 'ok'|'failed'|None.
            'last_login_status': r.get('last_login_status'),
            'last_login_at': r.get('last_login_at'),
            'last_login_error': r.get('last_login_error'),
        }

    # ---- lookups -----------------------------------------------------------

    def _find_by_login(self, login):
        low = (login or '').strip().lower()
        return next((r for r in self._records if r['login'].lower() == low), None)

    def get_by_login(self, login):
        """Return the record for a Steam login (case-insensitive), or None."""
        with self._lock:
            return self._find_by_login(login)

    def get(self, rec_id):
        """Return the record with this id, or None."""
        with self._lock:
            rec = next((r for r in self._records if r['id'] == rec_id), None)
            return dict(rec) if rec else None

    def list(self):
        with self._lock:
            return [dict(r) for r in self._records]

    def record_login_result(self, login, ok, error=None):
        """Stamp the outcome of a Ratatoskr login attempt onto the credential.

        No-op when the login has no stored credential (nothing to annotate).
        Called from the scan path and the per-row "Test login".
        """
        with self._lock:
            rec = self._find_by_login(login)
            if not rec:
                return
            rec['last_login_status'] = 'ok' if ok else 'failed'
            rec['last_login_at'] = _now()
            rec['last_login_error'] = None if ok else (error or 'login failed')
            self._save()

    def export_text(self):
        """Serialize the vault back to ``login;password;email;comment`` lines
        (sorted by login) — the exact format :meth:`import_text` accepts, for an
        off-machine backup. Trailing empty fields are trimmed for readability."""
        lines = []
        with self._lock:
            for r in sorted(self._records, key=lambda x: x['login'].lower()):
                fields = [r['login'], r['password'], r['email'], r['comment']]
                while len(fields) > 2 and not fields[-1]:
                    fields.pop()
                lines.append(';'.join(fields))
        return '\n'.join(lines) + ('\n' if lines else '')

    # ---- mutations ---------------------------------------------------------

    def add(self, login, password='', email='', comment=''):
        with self._lock:
            login = (login or '').strip()
            if not login:
                raise ValueError('login is required')
            if self._find_by_login(login):
                raise ValueError(f'a credential for "{login}" already exists')
            rec = self._normalize({'login': login, 'password': password,
                                   'email': email, 'comment': comment})
            self._records.append(rec)
            self._save()
            return dict(rec)

    def update(self, rec_id, fields):
        with self._lock:
            rec = next((r for r in self._records if r['id'] == rec_id), None)
            if not rec:
                raise KeyError(rec_id)
            for key in ('login', 'password', 'email', 'comment'):
                if key in fields and fields[key] is not None:
                    val = fields[key]
                    rec[key] = val.strip() if key != 'password' else val
            if 'login' in fields and not rec['login'].strip():
                raise ValueError('login cannot be empty')
            rec['updated_at'] = _now()
            self._save()
            return dict(rec)

    def delete(self, rec_id):
        with self._lock:
            before = len(self._records)
            self._records = [r for r in self._records if r['id'] != rec_id]
            if len(self._records) == before:
                return False
            self._save()
            return True

    def import_text(self, text):
        """Upsert credentials from pasted ``login;password;email;comment`` text.

        Existing logins are updated in place; a field is only overwritten when
        the imported value is non-empty, so a manually-added comment is never
        blanked by a bare import line. Returns a summary the UI can show.
        """
        rows, warnings = parse_credentials_text(text)
        added = updated = 0
        with self._lock:
            for row in rows:
                existing = self._find_by_login(row['login'])
                if existing:
                    for key in ('password', 'email', 'comment'):
                        if row[key]:
                            existing[key] = row[key]
                    existing['updated_at'] = _now()
                    updated += 1
                else:
                    self._records.append(self._normalize(row))
                    added += 1
            if rows:
                self._save()
        return {
            'parsed': len(rows),
            'added': added,
            'updated': updated,
            'warnings': warnings,
            'rows': rows,
        }
