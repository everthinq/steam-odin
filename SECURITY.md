# Security

## Sensitive data

**Never commit:**

| Path / pattern | Why |
|----------------|-----|
| `Yggdrasil/heimdall/backend/maFiles/*.maFile` | Steam Guard secrets and session tokens |
| `Yggdrasil/heimdall/backend/maFiles/.heimdall_key` | The key that decrypts maFiles and the vault |
| `Yggdrasil/heimdall/backend/maFiles/credentials.vault*` | Mímir: logins, passwords, emails, billing addresses |
| `Yggdrasil/heimdall/backend/settings.json` | Tradeon token, Telegram bot tokens and chat ids |
| `Yggdrasil/heimdall/backend/csfloat_keys.json` | CSFloat API keys |
| `Yggdrasil/heimdall/backend/telegram_caller.json` | Telegram user session that rings the phone |
| `.env`, `.env.*` | API keys, `HEIMDALL_SECRET_KEY`, `ASF_IPC_PASSWORD`, proxy credentials |
| `Yggdrasil/asf/config/` | ArchiSteamFarm API password and per-account login tokens (`*.db`) |
| `Yggdrasil/heimdall/backend/logs/` | May contain Steam auth responses, SteamIDs, IPs |

**Never copy a value out of Mímir** (password, email, billing address) or a maFile into any
tracked file, test, document or commit message: use obviously made-up values in tests.

### The pre-commit guard

`scripts/pre-commit-secret-guard.sh`, installed once per clone:

```bash
ln -sf ../../scripts/pre-commit-secret-guard.sh .git/hooks/pre-commit
```

Before every commit it refuses staged secret files (the table above), then pipes the added
lines of the staged changes through `backend/secret_scan.py` inside the backend container,
which decrypts Mímir and the maFiles and refuses the commit if any stored password, email,
billing address line (the name and street lines), maFile secret or session token appears. It
prints only the kind of value and the line, never the value. With the container stopped the
commit is refused too. To audit everything ever committed:

```bash
git log --all -p --no-color -U0 | grep '^+' | grep -v '^+++' \
  | docker exec -i steam-odin-heimdall-backend-1 python /app/secret_scan.py
```

On 2026-10-29 that audit found only the three passwords already known to be exposed in early
commits (removed from the files in 87eee59, still in the history): the fix is to change them.

## Before open-sourcing or sharing the repo

1. Confirm no maFiles are tracked: `git ls-files '*.maFile'`
2. Confirm git history is clean: `git log --all -- '**/maFiles/**' '*.maFile'`
3. Rotate `HEIMDALL_SECRET_KEY` if it was ever committed.
4. If maFiles were ever pushed, treat accounts as compromised: revoke sessions, re-link authenticator, rotate secrets.

## Running locally

- Docker Compose reads the **root** `.env`. Either set a strong random
  `HEIMDALL_SECRET_KEY` there before importing any account, or let the backend
  generate `maFiles/.heimdall_key` — and back that key up separately from the maFiles.
- Import maFiles only on your machine; store backups outside the repo
  ([backups and safety](docs/guide/backups-and-safety.md)).
- Keep every port on 127.0.0.1: the app has no login.

## Reporting issues

Do not open public issues with maFiles, passwords, or session dumps. Describe impact without pasting secrets.
