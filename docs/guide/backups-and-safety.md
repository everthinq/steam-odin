# Backups and safety

## The files you cannot lose

| File (under `Yggdrasil/heimdall/backend/`) | Why it matters |
|--------------------------------------------|----------------|
| `maFiles/*.maFile` | Each account's Steam Guard secrets. **Without it (and without the revocation code) you can be locked out of the account.** |
| `maFiles/.heimdall_key` — or your `HEIMDALL_SECRET_KEY` | The key that decrypts the maFiles and the Mímir vault. Without it, the backups above are useless. |
| `maFiles/credentials.vault` | Mímir: logins, passwords, emails. |
| `portfolios.json` | Draupnir's history; cannot be rebuilt. |
| `settings.json`, `csfloat_keys.json`, `telegram_caller.json` | Tokens and settings; annoying to recreate. |

These exist **only on this computer** unless you copy them. A disk failure,
theft or a mistaken delete would lose them.

## How to back up

- **3-2-1:** three copies, on two kinds of storage, one away from this
  computer (an encrypted USB stick in a drawer, an encrypted cloud archive).
- **Keep the key apart from the files.** Store `.heimdall_key` (or the secret
  key) somewhere other than the maFile backup — a password manager is ideal —
  so one stolen copy is not enough.
- Encrypt the archive itself (for example an encrypted disk image or a
  password-protected archive with a strong password).
- Test a restore once: copy the backup to a fresh folder and check the
  Dashboard shows codes.
- Also keep each account's **revocation code** (the `R…` code from when Steam
  Guard was added) somewhere safe.

**Draupnir** keeps local snapshots by itself, and `scripts/backup_portfolios.sh`
can push `portfolios.json` hourly to a `portfolio-backup` branch of your git
remote (install instructions are in `scripts/com.steamodin.portfolio-backup.plist`).
That covers portfolios only — **never** put maFiles, keys or the vault in git.

## What is never shared

- No secret file above is ever committed (they are all in `.gitignore`) or
  sent anywhere by the app.
- ArchiSteamFarm never receives Steam Guard secrets: it gets a password and a
  30-second code only when it asks to log in, so it cannot confirm trades.
- The app, its API, Ratatoskr and ASF listen on **127.0.0.1 only** and refuse
  requests from other websites. Do not change the ports to listen on your
  network: the app has no login and shows codes, passwords and confirmations.
- Never paste cookies (`steamLoginSecure`), maFiles or tokens into chats,
  issues or websites — they give full access to the account.

## Things that act on their own

Know what is switched on: auto-confirm (Dashboard → Confirms), card auto-sell
(Andvari), Team Fortress 2 watcher and auto-sell, Andvari automatic scans,
Gjallarhorn alarm, auto-store, the morning routine. Anything that **sells** only
touches items that arrive after you switch it on, unless you tick the option
for older ones. Anything that **spends money** (Buy games, Storage shop) only
runs when you press its button, after a review.
