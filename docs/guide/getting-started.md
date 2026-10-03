# Getting started

## What you need

- A Mac or Linux computer with **Docker** (Docker Desktop on a Mac) and `make`.
- Your accounts' **maFiles** (Steam Guard files) and their **passwords**.
- Optional, per tool: a Tradeon pulse account (prices for Huginn and Draupnir),
  CSFloat API keys, a Telegram bot (alerts), a spare Telegram account (phone
  rings).

Everything runs on your own computer and is reachable **only from it**
(127.0.0.1). The app has no login of its own, so never expose its ports to
your network or the internet.

## 1. Start the app

```bash
git clone <this repository> steam-odin
cd steam-odin
make odin        # build and start everything
```

Your maFiles are stored encrypted. On the first start the backend creates a
strong random key for that in `Yggdrasil/heimdall/backend/maFiles/.heimdall_key`
— **back that file up** with the maFiles (see
[backups-and-safety.md](backups-and-safety.md)). If you prefer to keep the key
out of the data folder, set `HEIMDALL_SECRET_KEY=<long random string>` in the
**root** `.env` file (next to `docker-compose.yml`; Docker reads only that one)
*before* importing any account — changing the key later makes already stored
maFiles unreadable.

Open **http://localhost:3000**. `make help` lists every command; the main ones:

| Command | Does |
|---------|------|
| `make odin` | Build and start everything |
| `make raid` | Start (already built) |
| `make sleep` | Stop |
| `make saga` | Follow the logs |

| Address | What |
|---------|------|
| http://localhost:3000 | The app |
| http://localhost:5001 | The backend API (used by the app) |
| http://localhost:3001 | Ratatoskr (used by the backend only) |
| http://localhost:1242 | ArchiSteamFarm's own UI (after step 5) |

## 2. Import your accounts

Dashboard → **Import** → drop one or more `.maFile` files. The app asks for
each account's Steam password, logs in once to check it, and keeps the maFile
encrypted on disk. An account whose login fails is not kept; fix the password
and retry the failed ones.

The cards on the Dashboard now show each account's Steam Guard code (hidden
until you press the eye).

## 3. Put the passwords in Mímir

maFiles do not contain passwords, but several features need to log in again
by themselves (renewing confirmation sessions, Ratatoskr, ASF). Open
**Mímir** → **Import**, paste one line per account:

```
login;password;email;comment
```

then press **Test login** on a row to check it. See [mimir.md](mimir.md).

## 4. Prices (Huginn and Draupnir)

Huginn and Draupnir read prices from Tradeon's pulse service with your own
account's token:

1. Log into https://pulse.tradeon.space in your browser.
2. Open the developer tools → Network, load any price table, pick a request to
   `api-pulse.tradeon.space`, and copy the token after `authorization: Bearer`.
3. Put it in `Yggdrasil/heimdall/backend/settings.json` as `"tradeon_token"`
   (copy `settings.example.json` first if the file does not exist), then
   restart the backend: `docker restart steam-odin-heimdall-backend-1`.

The token expires eventually; when prices stop loading, repeat this.

CSFloat buy orders (optional) need CSFloat API keys in
`Yggdrasil/heimdall/backend/csfloat_keys.json`.

## 5. Card farming (optional, for Andvari)

```bash
make asf-setup   # once: creates a random password and a locked-down ASF configuration
make asf         # starts ArchiSteamFarm and connects the app to it
```

The app then creates one ASF bot per account by itself. ASF never receives
your Steam Guard secrets. Details: [Yggdrasil/asf/README.md](../../Yggdrasil/asf/README.md).

## 6. Telegram (optional)

Alerts go to Telegram bots you create with @BotFather. See [telegram.md](telegram.md).

## 7. Back up

Before you rely on the app, back up your maFiles and keys **off this
computer**. See [backups-and-safety.md](backups-and-safety.md).
