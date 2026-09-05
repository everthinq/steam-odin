# CLAUDE.md — Ratatoskr (Node courier)

Agent notes for the Node service that moves items between Storage Units
("caskets") and inventory. The root [CLAUDE.md](../../CLAUDE.md) has the
fleet-wide rules; this file is Ratatoskr's map and its rate-limit contract.

## What it is

A small Express service that holds live Steam sessions and drives the
`steam-user` + `globaloffensive` (Game Coordinator) libraries to move items.
The Heimdall **backend** calls it over HTTP (`RATATOSKR_URL` /
`HEIMDALL_API_URL`); the **frontend never calls it directly** — it goes through
Heimdall's Ratatoskr routes and pages.

- `server.js` — Express app, session store (`sessions[steamID]`), move loop,
  idle auto-disconnect.
- `items.js` — item processing, rarity/wear tables, translations.
- `fetch_items.js` — helper for pulling item catalog data.

Scripts: `npm start` (= `node server.js`), `npm run dev` (nodemon).
Runs on container port 3000, published to host **3001**.

## Rate limits are the whole game — do not undo these

Steam and the Game Coordinator throttle aggressively (HTTP 429 / silent drops).
The current tuning exists because faster settings got accounts rate-limited:

- **Moves are serial with a delay** (`MOVE_DELAY_MS`, default 400 ms, clamped
  100–5000). Do not batch or parallelise moves.
- **Game Coordinator bursts were serialised** with a ~350 ms delay between
  calls; a double-fetch ref guard prevents overlapping fetches.
- **Idle auto-disconnect** frees sessions after `SESSION_IDLE_TIMEOUT_MS`
  (default 1 hour, 0 = never).
- On the Heimdall side, the scheduler sleeps ~2 s between accounts and the
  frontend poll interval was widened to reduce pressure.

If you are tempted to "speed things up", assume the delay is load-bearing and
confirm with Ivan before shortening any of it.

## Traps

- **No hot reload.** After editing any `.js`, `docker restart steam-odin-ratatoskr-1`.
  (`npm run dev`/nodemon only reloads if you run it that way, not in the
  compose service, which uses `npm start`.)
- **Keep per-item work out of hot loops.** Rarity/wear lookup tables are hoisted
  to module scope on purpose; do not move them back inside functions, and do not
  add per-item `console.log`/`console.warn` in the move or fetch paths.
- Sessions are **in-memory only** — a restart drops every live login.
