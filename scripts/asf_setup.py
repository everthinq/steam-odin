#!/usr/bin/env python3
"""One-time setup for the ArchiSteamFarm (ASF) container — run `make asf-setup`.

What it writes (all gitignored, owner-only permissions):

- `.env` (repository root): `ASF_IPC_PASSWORD=<random>`. docker compose reads
  it and hands the same password to both ASF and the Heimdall backend.
- `Yggdrasil/asf/config/ASF.json`: ASF's global config, hardened for safety
  (no public listing / Steam group, no self-update, no bad-bot list download,
  slow login and web pacing, headless).
- `Yggdrasil/asf/config/IPC.config`: the ASF UI/API listens on port 1242 (the
  compose file publishes it on 127.0.0.1 only; the API password guards it).

Bot configs (one per Steam account) are not written here: Heimdall creates them
through the ASF API, with no password inside — Heimdall types the password and
the Steam Guard code in only when ASF asks for them, and ASF keeps neither on disk.

Safe to re-run: an existing password is kept; ASF.json and IPC.config are
rewritten to the hardened values.
"""
import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / '.env'
ASF_DIR = ROOT / 'Yggdrasil' / 'asf'
CONFIG_DIR = ASF_DIR / 'config'
LOGS_DIR = ASF_DIR / 'logs'
PASSWORD_KEY = 'ASF_IPC_PASSWORD'

# Counter-Strike 2 has no trading cards; listing it keeps ASF from ever
# "playing" it, so it can never collide with a Ratatoskr Game Coordinator session.
COUNTER_STRIKE_2 = 730


def global_config(ipc_password):
    return {
        'Headless': True,                 # no console in Docker; Heimdall supplies input over the API
        'IPC': True,
        'IPCPassword': ipc_password,
        'IPCPasswordFormat': 0,           # plain: the file is owner-only and never leaves this machine
        'AutoRestart': False,             # Docker's restart policy handles restarts
        'UpdateChannel': 0,               # no self-update: the image is pinned by digest, updates are deliberate
        'UpdatePeriod': 0,
        'FilterBadBots': False,           # skip downloading ASF's bad-bot list; trading is off anyway
        'LoginLimiterDelay': 30,          # seconds between Steam logins (default 10) — gentle on 21 accounts
        'WebLimiterDelay': 1000,          # milliseconds between community requests (default 300);
                                          # ASF shares this IP with Andvari and Gjallarhorn
        'Blacklist': [COUNTER_STRIKE_2],
    }


# Listen on every container interface; Docker publishes the port to this Mac only.
# (ASF's web server ignores ASP.NET's AllowedHosts filter — tested 2026-09-25 — so
# the random API password is what stops a DNS-rebinding website; ASF also blocks
# a source after five wrong passwords.)
IPC_CONFIG = {'Kestrel': {'Endpoints': {'HTTP': {'Url': 'http://*:1242'}}}}


def read_env():
    if not ENV_FILE.exists():
        return {}
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def write_private(path, text):
    """Write a file readable by the owner only (created 0600 before any content lands)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def main():
    password = read_env().get(PASSWORD_KEY)
    if not password:
        password = secrets.token_urlsafe(32)
        existing = ENV_FILE.read_text() if ENV_FILE.exists() else ''
        if existing and not existing.endswith('\n'):
            existing += '\n'
        write_private(ENV_FILE, f'{existing}{PASSWORD_KEY}={password}\n')
        print(f'created {PASSWORD_KEY} in {ENV_FILE.relative_to(ROOT)}')
    else:
        os.chmod(ENV_FILE, 0o600)
        print(f'kept the existing {PASSWORD_KEY} in {ENV_FILE.relative_to(ROOT)}')

    for directory in (ASF_DIR, CONFIG_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    write_private(CONFIG_DIR / 'ASF.json', json.dumps(global_config(password), indent=2) + '\n')
    write_private(CONFIG_DIR / 'IPC.config', json.dumps(IPC_CONFIG, indent=2) + '\n')
    print(f'wrote {(CONFIG_DIR / "ASF.json").relative_to(ROOT)} and IPC.config')
    print('next: docker compose up -d asf && docker compose up -d heimdall-backend')


if __name__ == '__main__':
    main()
