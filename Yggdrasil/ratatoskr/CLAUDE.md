# CLAUDE.md — Ratatoskr (Node courier)

Agent notes for the Node service that holds live Counter-Strike 2 sessions:
it moves items between Storage Units ("caskets") and the inventory, renames
Storage Units, and talks to the game's own store (Buy Storage Units, Store Catalogue). The root
[CLAUDE.md](../../CLAUDE.md) has the fleet-wide rules; this file is
Ratatoskr's map and its rate-limit contract.

## What it is

A small Express service that drives the `steam-user` + `globaloffensive`
(Game Coordinator) libraries. The Heimdall **backend** calls it over HTTP
(`RATATOSKR_URL`); the **frontend never calls it directly** — it goes through
Heimdall's `/api/ratatoskr/*` routes. Any request carrying an `Origin` header
is refused with 403 (only server-to-server calls are accepted).

| File | Job |
|------|-----|
| `server.js` | Express app, session store, login, move queue, idle sweep, every endpoint |
| `store.js` | Counter-Strike 2 in-game store over the Game Coordinator: price sheet (`StoreGetUserData`), `StorePurchaseInit` / `Finalize` / `Cancel`, one request per session at a time, each sent as a Game Coordinator **job** (Finalize is answered only to a job); catches Steam's `ClientMicroTxnAuthRequest` (EMsg 5504, not handled by steam-user). Protocol and live findings: [docs/internals/storage-shop.md](../../docs/internals/storage-shop.md) |
| `items.js` | Converts Game Coordinator inventory into display items (names, images, stickers, wear, collections); logic derived from Casemove. Loads `csgo_english.json` and `items_game.json` once at start |
| `fetch_items.js` | Manual one-off: downloads fresh `items_game` / `csgo_english` from SteamDatabase's GameTracking-CS2 and writes the two JSON files (`node fetch_items.js`, on the Mac in this folder: the container's copy is read-only). Run it after a game update adds items (the Store Catalogue then marks fewer items "new"), then `docker restart steam-odin-ratatoskr-1`; last refreshed 2026-10-03 |

Scripts: `npm start` (= `node server.js`), `npm run dev` (nodemon). Image
`node:22-alpine`, runs as user `node`, all capabilities dropped, source mounted
read-only. Container port 3000, published on host **127.0.0.1:3001**.

## Environment

| Variable | Default | Notes |
|----------|---------|-------|
| `PORT` | 3030 in code | Compose sets 3000 |
| `MOVE_DELAY_MS` | 400 | Clamped 100–5000; changeable live via `/config/move-delay` |
| `SESSION_IDLE_TIMEOUT_MS` | 3600000 (1 h) | 0 = never; otherwise 5 min–24 h; changeable live |
| `HEIMDALL_API_URL` | `http://localhost:5000` | Compose: `http://heimdall-backend:5000` (web session hand-back) |

## Sessions

- `sessions[steamID64] = {user, csgo, accountName, lastActivity}` — **in memory
  only**; a restart drops every login.
- `POST /login` takes `{accountName, password, twoFactorCode | sharedSecret}`
  from Heimdall (Ratatoskr never stores credentials; Heimdall reads the
  password from Mímir). One login per account at a time (409); a healthy
  existing session is reused. Steam Guard prompts become 401 (never a console
  wait); 30 s without the Game Coordinator → 504.
- On login it plays app 730 and hands the web cookies back to Heimdall
  (`/api/accounts/update-session`); on disconnect it tells Heimdall to clear them.
- Idle sweep every 60 s: a session idle past the timeout is logged off unless
  it is protected (Heimdall syncs `protectedSteamIds` for auto-store accounts)
  or items are moving. Every session endpoint bumps `lastActivity`.
- `uncaughtException` exits the process; Docker restarts it.

## Endpoints

| Method + path | Purpose |
|---------------|---------|
| `POST /login` | Log in + connect to the Game Coordinator |
| `GET /status/:steamid` | 200 connected / 503 `gc_lost` / 404 disconnected (also keep-alive) |
| `POST /disconnect/:steamid` | Log off |
| `GET /inventory/:steamid` | Converted inventory |
| `GET /caskets/:steamid` | Storage Units (definition 1201) |
| `GET /casket/:steamid/:casketid` | One Storage Unit's contents |
| `POST /casket/rename` | Free rename, max 20 characters, 15 s timeout |
| `POST /move`, `POST /move/batch` | Queue moves (inventory ↔ casket) |
| `GET /move/status/:steamid` | Queue progress (last 20 errors) |
| `GET/POST /config/move-delay` | Delay between moves |
| `GET/POST /config/session-idle` | Idle timeout + presets |
| `GET/POST /config/protected-accounts` | Accounts exempt from the idle sweep |
| `GET /store/user-data/:steamid` | Price sheet (base64), wallet, Storage Unit count, countries |
| `POST /items/store-names` | `{names: [...]}` → definition index, English name, prefab and `cannotTrade` (the item's own "cannot trade" attribute, not its prefab's) per price sheet entry (local item files, no Steam call; unknown names left out) — the Store Catalogue's names |
| `POST /store/purchase/init` | Open a transaction (currency 0–63, two-letter country, quantity 1–50, `itemDefinitionIndex` default 1201 = Storage Unit); returns `transactionId` + Steam's raw approval request |
| `POST /store/purchase/finalize` | Deliver (and charge) an approved transaction |
| `POST /store/purchase/cancel` | Drop an unapproved transaction |

**The `/store/purchase/*` endpoints spend real money. Never call them
directly** — only Heimdall's guarded Buy Storage Units (and its per-item pages) does, after its checks.

## Rate limits are the whole game — do not undo these

Steam and the Game Coordinator throttle aggressively (HTTP 429 / silent drops).
The current tuning exists because faster settings got accounts rate-limited:

- **Moves are serial with a delay** (`MOVE_DELAY_MS`, default 400 ms). One
  runner per account; before each move it re-checks the session and Game
  Coordinator and fails the rest ("session lost") rather than sending blind.
  Do not batch or parallelise moves. A sent move counts as done (the Game
  Coordinator does not confirm).
- **Store requests are serial per session** (`serial` queue in `store.js`): two
  answers of the same type would mix.
- On the Heimdall side the scheduler sleeps between accounts; the Transfer page
  loads "All storage units" one unit at a time 350 ms apart and polls move
  status every 500 ms.
- **Idle auto-disconnect** frees sessions after the timeout.

If you are tempted to "speed things up", assume the delay is load-bearing and
confirm with Ivan before shortening any of it.

## Traps

- **No hot reload.** After editing any `.js`, `docker restart steam-odin-ratatoskr-1`
  (it drops every live session). `npm run dev` / nodemon only reloads when run
  that way, not in the compose service.
- **Game Coordinator callbacks can fire twice** (casket contents are tied to
  customization notifications). A second `res.json` throws
  `ERR_HTTP_HEADERS_SENT` and crashes the whole process — every Heimdall page
  then says "Failed to connect to Ratatoskr". Guard every response sent from a
  Game Coordinator callback with `res.headersSent` (login uses its own
  `isResponded` flag). When Ratatoskr "fails to connect", check
  `docker ps -a` first — it is usually a crashed container.
- **A store request that timed out closes that session's store** until a fresh
  login (`session.storeClosed`): a late answer could otherwise be taken for the
  next request's.
- **Keep per-item work out of hot loops.** Rarity/wear lookup tables are hoisted
  to module scope on purpose; do not move them back inside functions, and do not
  add per-item `console.log` in the move or fetch paths.
- **One "playing" session per Steam account.** Heimdall pauses the account's ASF
  bot before every Ratatoskr login (`RatatoskrService.before_login`); log in
  only through Heimdall.
- `cors` and `dotenv` are declared in `package.json` but unused.
