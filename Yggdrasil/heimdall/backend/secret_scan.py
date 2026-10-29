"""Secret scan: refuse text that contains a stored secret (the pre-commit guard).

Reads text on standard input (a staged diff) and looks for every value Heimdall
keeps secret: Mímir's passwords, emails and billing address lines, and each
maFile's shared secret, identity secret, revocation code and session tokens.
Prints only what kind of value matched and on which input line, never the value,
and exits 1 when anything matched (0 when clean, 2 when the secrets cannot be read).

Run inside the backend container, where the maFiles and their key live:
    git diff --cached | docker exec -i steam-odin-heimdall-backend-1 python /app/secret_scan.py
``scripts/pre-commit-secret-guard.sh`` does exactly that before every commit.
"""
import re
import sys

MINIMUM_SECRET_LENGTH = 6     # shorter passwords would match ordinary words
MINIMUM_ADDRESS_LENGTH = 8    # address lines shorter than this ("MD") are too common
MAFILE_SECRET_KEYS = ('shared_secret', 'identity_secret', 'revocation_code', 'uri')
SESSION_SECRET_KEYS = ('AccessToken', 'RefreshToken', 'SteamLoginSecure', 'SessionID', 'WebSessionId')


def secret_values(credentials, mafiles):
    """{value: kind} for every secret worth looking for. *credentials*: Mímir records;
    *mafiles*: decrypted maFile dicts."""
    values = {}
    for record in credentials:
        password = record.get('password') or ''
        if len(password) >= MINIMUM_SECRET_LENGTH:
            values[password] = 'a Mímir password'
        email = (record.get('email') or '').strip()
        if '@' in email:
            values[email.lower()] = 'a Mímir email'
        lines = [line.strip() for line in (record.get('billing_address') or '').splitlines() if line.strip()]
        for position, line in enumerate(lines):
            # The name (first line) and street lines (with a number) identify a person;
            # letters-only lines such as "Moldova, Republic of" are too common to refuse.
            if len(line) >= MINIMUM_ADDRESS_LENGTH and (position == 0 or re.search(r'\d', line)):
                values[line.lower()] = 'a Mímir billing address'
    for data in mafiles:
        for key in MAFILE_SECRET_KEYS:
            value = str(data.get(key) or '')
            if len(value) >= MINIMUM_SECRET_LENGTH:
                values[value] = f'a maFile {key}'
        session = data.get('Session') or {}
        for key in SESSION_SECRET_KEYS:
            value = str(session.get(key) or '')
            if len(value) >= 16:
                values[value] = f'a maFile session {key}'
    return values


def find_secrets(text, values):
    """[(input line number, kind)] for each line of *text* that holds a secret.
    Emails and addresses match case-insensitively; passwords and keys exactly."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        for value, kind in values.items():
            exact = kind.startswith('a maFile') or kind == 'a Mímir password'
            if (value in line) if exact else (value in lowered):
                found.append((number, kind))
                break
    return found


def _load_secrets():
    from mimir_service import MimirService
    from storage import SecureStorage

    storage = SecureStorage()
    vault = MimirService(storage)
    if vault.load_error:
        raise RuntimeError(f'the Mímir vault could not be read ({vault.load_error})')
    mafiles = [storage.load_account(steamid) or {} for steamid in storage.list_accounts()]
    return secret_values(vault.list(), mafiles)


def main():
    try:
        values = _load_secrets()
    except Exception as e:
        print(f'secret scan: could not read the secrets ({type(e).__name__}): refusing', file=sys.stderr)
        return 2
    found = find_secrets(sys.stdin.read(), values)
    for number, kind in found:
        print(f'secret scan: line {number} of the staged changes holds {kind}', file=sys.stderr)
    if found:
        print(f'secret scan: {len(found)} line(s) hold a stored secret: commit refused', file=sys.stderr)
        return 1
    print(f'secret scan: clean ({len(values)} stored secrets checked)', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
