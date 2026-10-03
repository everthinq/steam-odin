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
Wait for the next code and retry. If it keeps failing, check the computer's
clock is correct — codes depend on it.

## Prices do not load (Huginn, Draupnir)

The Tradeon token is missing or expired. Get a fresh one
([getting-started.md](getting-started.md#4-prices-huginn-and-draupnir)) and
restart the backend. Draupnir still shows holdings at cost meanwhile.

## HTTP 429 / "Too Many Requests"

Steam is rate-limiting this connection. The app already spaces its calls; wait
(often 15–60 minutes) instead of retrying. Do not run several heavy jobs
(Andvari scan, Get all items, a big transfer) at the same time.

## ASF shows "Needs you" or "ASF down"

- "Needs you": the login failed three times; check the password in Mímir and
  press ↺ **Retry login** in Andvari → Card farming.
- "ASF down": `make asf` starts it (after `make asf-setup` once).

## Storage shop: "MAY BE PAID"

See [ratatoskr.md](ratatoskr.md#storage-shop): check the account's purchase
history on Steam, then press **Deliver again** (it cannot charge twice).

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
