# Security

## Sensitive data

**Never commit:**

| Path / pattern | Why |
|----------------|-----|
| `Yggdrasil/heimdall/backend/maFiles/*.maFile` | Steam Guard secrets and session tokens |
| `.env`, `.env.*` | API keys, `HEIMDALL_SECRET_KEY`, proxy credentials |
| `Yggdrasil/heimdall/backend/logs/` | May contain Steam auth responses, SteamIDs, IPs |

## Before open-sourcing or sharing the repo

1. Confirm no maFiles are tracked: `git ls-files '*.maFile'`
2. Confirm git history is clean: `git log --all -- '**/maFiles/**' '*.maFile'`
3. Rotate `HEIMDALL_SECRET_KEY` if it was ever committed.
4. If maFiles were ever pushed, treat accounts as compromised: revoke sessions, re-link authenticator, rotate secrets.

## Running locally

- Copy `Yggdrasil/heimdall/backend/.env.example` to `.env` and set a strong random `HEIMDALL_SECRET_KEY`.
- Import maFiles only on your machine; store backups outside the repo.

## Reporting issues

Do not open public issues with maFiles, passwords, or session dumps. Describe impact without pasting secrets.
