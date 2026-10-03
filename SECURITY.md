# Security

## Sensitive data

**Never commit:**

| Path / pattern | Why |
|----------------|-----|
| `Yggdrasil/heimdall/backend/maFiles/*.maFile` | Steam Guard secrets and session tokens |
| `Yggdrasil/heimdall/backend/maFiles/.heimdall_key` | The key that decrypts maFiles and the vault |
| `Yggdrasil/heimdall/backend/maFiles/credentials.vault*` | Mímir: logins, passwords, emails |
| `Yggdrasil/heimdall/backend/settings.json` | Tradeon token, Telegram bot tokens and chat ids |
| `Yggdrasil/heimdall/backend/csfloat_keys.json` | CSFloat API keys |
| `Yggdrasil/heimdall/backend/telegram_caller.json` | Telegram user session that rings the phone |
| `.env`, `.env.*` | API keys, `HEIMDALL_SECRET_KEY`, `ASF_IPC_PASSWORD`, proxy credentials |
| `Yggdrasil/asf/config/` | ArchiSteamFarm API password and per-account login tokens (`*.db`) |
| `Yggdrasil/heimdall/backend/logs/` | May contain Steam auth responses, SteamIDs, IPs |

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
