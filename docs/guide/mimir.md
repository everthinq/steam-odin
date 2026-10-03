# Mímir — credential vault

Mímir stores each account's **login, password, email and a comment**,
encrypted with the same key as the maFiles. The app reads passwords from here
whenever it has to log in by itself (renewing confirmations, Ratatoskr, ASF,
purchases). maFiles do not contain passwords, so **every account the app should
manage needs its password in Mímir**.

Open it from the Dashboard tile **Mímir** (also /mimir).

## Using it

- **Add credential** — one account by hand.
- **Import** — paste many lines of `login;password;email;comment`. Existing
  logins are updated, new ones added. Passwords containing `;` or `@` are
  handled.
- The list is split into **With maFile** (the app can log in) and **No maFile**.
- Per row: copy login, **reveal** the password (hides again after 30 seconds),
  copy password without showing it, copy email, **Test login** (logs in once and
  records the result as the coloured dot), edit, delete.
- **Hide all** hides every revealed password.

## Export — handle with care

**Export** downloads the whole vault as a **plain text file**, passwords
readable. Use it only to move to another vault, keep the file off cloud sync,
and delete it afterwards.

## Behind the scenes

Every change keeps a backup copy of the vault next to it (the newest ten).
The vault file is `maFiles/credentials.vault`; it is useless without the
encryption key — back up both (see [backups-and-safety.md](backups-and-safety.md)).
