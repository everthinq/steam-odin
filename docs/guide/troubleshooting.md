# Troubleshooting

First look: `make saga` (all logs), or the backend's own log
`Yggdrasil/heimdall/backend/logs/heimdall.log`. `docker ps -a` shows which
containers are running.

## A page says "Failed to connect to Ratatoskr"

Ratatoskr probably crashed or restarted. `docker ps -a` — if
`steam-odin-ratatoskr-1` is not "Up", run `docker compose up -d ratatoskr`.
A restart logs every account out of Ratatoskr; press **Connect** again.

## Confirmations stopped working

- **For every account at once:** the web sessions expired and were not
  renewed. Search the log for `[KEEPALIVE]` — "failed" lines name the cause,
  usually a missing or wrong password in Mímir. Fix it, then **Test login** in
  Mímir.
- **"Oh nooooooes" / "try again later"** looks like a rate limit but often is
  not — it is a request Steam rejected. Wait a few minutes; if it persists, the
  session is the problem (above).

## "Steam Guard code rejected"

The same 30-second code was used twice (for example two logins in one window).
Wait for the next code and retry. Codes are timed by Steam's own clock, and a
rejected code is tried once more after asking Steam for the time again, so a clock
that fell behind while the Mac slept no longer breaks logins. If it keeps failing,
the account's shared secret (maFile) is the likely problem.

## Prices do not load (Huginn, Draupnir)

The Tradeon token is missing or expired. Get a fresh one
([getting-started.md](getting-started.md#4-prices-huginn-and-draupnir)) and
restart the backend. Draupnir still shows holdings at cost meanwhile.

To see what the price feed is doing, open http://localhost:5001/api/huginn/pulse:
every price table with its age, downloads, reads and the last error. Every page
shares these tables, so one failing table explains every page that is missing
that market. Pages take tables up to 10 minutes old; buttons (Fetch live data,
Refresh) ask for one under a minute old.

## HTTP 429 / "Too Many Requests"

Steam is rate-limiting this connection. The app already spaces its calls; wait
(often 15–60 minutes) instead of retrying. Do not run several heavy jobs
(Andvari scan, Get all items, a big transfer) at the same time.

## ASF shows "Needs you" or "ASF down"

- "Needs you": the login failed three times; check the password in Mímir and
  press ↺ **Retry login** in Andvari → Card farming.
- "ASF down": `make asf` starts it (after `make asf-setup` once).

## Buy Storage Units: "MAY BE PAID"

See [ratatoskr.md](ratatoskr.md#buy-storage-units): check the account's purchase
history on Steam, then press **Deliver again** (it cannot charge twice).

## Store Boosts: "not read yet" or "No Steam web session"

- Steam's price history is read one item at a time, 30 seconds apart: the
  whole list (about 200 store items and 575 capsule stickers) takes hours the
  first time. **Read Steam now** on a row puts it first.
- "No Steam web session": no account holds a fresh web login. Connect any
  account (Ratatoskr) or wait for the keep-alive; reading resumes on its own.
- "Steam said slow down": Steam answered 429. It waits 10 minutes, then longer;
  raise the seconds between reads if it keeps happening.
- No alert for an item already boosting when you switched it on: an item seen
  for the first time is only recorded, unless its rise began in the last 48
  hours.

## Store Catalogue Arbitrage shows nothing

- "Reading market prices…": each market is read one after another (about 5
  seconds each); wait a minute. After a backend restart the prices are read
  again.
- "No store prices yet": press **Store prices never read** in the status bar (or
  **Read prices again** on the Catalogue tab). The morning routine reads them daily.
- No Telegram post in the morning: **Telegram: daily post** must be ticked and the
  Huginn bot set (`telegram_bot_token` + `telegram_chat_id`); **Send now** shows the
  reason when a post fails.
- "No market prices": the Tradeon token (`tradeon_token` in
  `backend/settings.json`) is missing or expired. Huginn's Arbitrage page needs
  it too.
- "Price pull failed for …": that market's feed failed; it is retried after
  about two minutes. The other markets still show.
- An empty table with "Nothing sells above the store price" is a real answer:
  tick **Show items that lose money** to see every item.

## The page is blank or says "Loading…" forever

Reload the page (an old tab after an update can miss new code). If it stays
blank, `docker compose logs heimdall-frontend` shows the error.

## Backend does not answer

`docker compose logs heimdall-backend` shows the error (often a Python
traceback after a code change). `docker restart steam-odin-heimdall-backend-1`
after it is fixed.

## Settings do not save

A broken `settings.json` is copied aside as `settings.json.corrupt-<time>`; the
app then runs on defaults and refuses to save until the file loads again. Fix
or restore the file and restart the backend.
