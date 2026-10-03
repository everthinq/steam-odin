# Architecture — steam-odin

How the pieces fit together, for a human reading the code for the first time.

- **Just want to run it?** → [README.md](README.md)
- **You are an AI coding agent?** → [CLAUDE.md](CLAUDE.md)
- **This document** explains the *mental model*: topology, how a request flows,
  the Steam session/token lifecycle, the data model, and how it is operated.

This is a personal, open-source Steam-trading toolset. Everything is named after
Norse figures, but the naming is decoration — the code hierarchy is the truth.

---

## 1. The realms (topology)

Four long-running containers, orchestrated by `docker-compose.yml`. The
browser talks only to the frontend (which proxies `/api` to the backend); the
backend is the only thing that talks to Steam, Ratatoskr, ASF and the outside
services. Every port is bound to 127.0.0.1.

```mermaid
flowchart TB
    subgraph Browser
        UI["Heimdall frontend<br/>(React + Vite)<br/>localhost:3000"]
    end

    subgraph Docker
        BE["Heimdall backend<br/>(Flask, threaded)<br/>localhost:5001 → :5000"]
        RAT["Ratatoskr<br/>(Node + steam-user + GC)<br/>localhost:3001 → :3000"]
        ASF["ArchiSteamFarm<br/>(pinned image)<br/>localhost:1242"]
    end

    subgraph External
        STEAM["Steam: login, mobile confirmations,<br/>Market, store + checkout"]
        GC["Counter-Strike 2<br/>Game Coordinator"]
        PRICES["Price sources: Tradeon pulse,<br/>CSFloat, LOOT.Farm, Steam Market"]
        TG["Telegram<br/>(bots + phone ring)"]
    end

    UI -->|REST /api/... via Vite proxy| BE
    BE -->|sessions, moves, store| RAT
    BE -->|bots, commands, login help| ASF
    BE -->|login, TOTP, confirmations,<br/>wallet, Market listings, checkout| STEAM
    BE -->|prices| PRICES
    BE -->|alerts, rings| TG
    RAT -->|inventory, Storage Units,<br/>in-game store| GC
    ASF -->|plays games for card drops| STEAM
```

Key point: **the frontend never calls Ratatoskr or Steam directly.** All of it
is proxied through the Flask backend, which owns credentials, tokens, and rate
limiting.

## 2. Inside Heimdall — the tools

Heimdall started as a Steam authenticator and grew a set of tools. Each tool is
a backend service + a set of frontend pages; none is a separate deployable.

| Tool | Norse role | What it does | Backend | Frontend |
|------|-----------|--------------|---------|----------|
| Authenticator | Heimdall, the Watchman | TOTP codes, login sessions, mobile confirmations | `steam_service.py`, `scheduler.py` | `Confirmations.jsx`, `AddAccount.jsx` |
| **Draupnir** | The Hoard | Portfolio tracker: buy/sell, moving-average profit/loss, live valuation, point-in-time backups | `draupnir_service.py`, `draupnir_backup_service.py` | `pages/draupnir/` |
| **Huginn** | The Scout | Cross-market price scouting + case arbitrage, off the Tradeon pulse feed | `huginn_service.py` | `pages/huginn/` |
| **Mímir** | The Well of Wisdom | Encrypted credential vault (login / password / email) | `mimir_service.py` | `pages/mimir/` |
| **Ratatoskr** | The Courier | Moves items between Storage Units and inventory, auto-store; **Storage shop** buys Storage Units on many accounts | `ratatoskr_service.py` → Node, `storage_shop_service.py` | `pages/ratatoskr/` |
| **Gjallarhorn** (in Huginn) | The Horn | Event rotation when Valve limits a case; news watcher that texts and rings | `gjallarhorn_service.py`, `gjallarhorn_news_service.py`, `steam_market_service.py`, `telegram_caller.py` | `pages/huginn/Gjallarhorn.jsx` |
| **Cross-Profile, Harvest** (in Huginn) | — | Best routes across accounts; holdings at purchase price vs autobuy offers | `cross_arbitrage_service.py`, `harvest_service.py` | Arbitrage page views |
| **Andvari** (in Huginn) | The Pike | Card-drop deals, "Buy games" on many accounts, ASF farming, card auto-sell | `card_deals_service.py`, `store_purchase_service.py`, `asf_service.py`, `card_seller_service.py` | `pages/huginn/CardDeals.jsx` |
| **Team Fortress 2** (in Huginn) | — | New case drops: play on release through ASF, sell drops | `team_fortress_service.py` | `pages/huginn/TeamFortress.jsx` |

How the tools feed each other:

- **Draupnir values holdings with Huginn's prices** (same `tradeon_token`);
  Harvest, Cross-Profile and Gjallarhorn read Draupnir's holdings.
- **Mímir supplies passwords** for every automatic login — the authenticator's
  keep-alive, Ratatoskr, ASF, purchases (maFiles do not carry the password).
- **Ratatoskr and ASF share accounts**: Steam allows one game session per
  account, so Heimdall pauses an account's ASF bot before every Ratatoskr login
  and resumes it afterwards.
- **Andvari drives ASF**: bots are switched on only while Andvari's scan shows
  cards left to drop.

## 3. Backend shape

The Flask backend is a small composition-root pattern.

```mermaid
flowchart LR
    APP["app.py<br/>(composition root)"] -->|constructs once| SVCS["about 20 services<br/>SteamService, HuginnService,<br/>DraupnirService, MimirService,<br/>AsfService, StorageShopService, ..."]
    APP -->|hangs singletons on| CTX["context.ctx"]
    APP -->|registers| BP["routes/ blueprints<br/>accounts · settings · draupnir<br/>huginn · ratatoskr · mimir"]
    BP -->|read services at request time| CTX
    APP -->|starts| SCHED["background scheduler thread"]
```

- `app.py` builds every service **once** at boot, stores them on `context.ctx`,
  registers the route blueprints, then starts the scheduler and serves with
  `threaded=True`.
- Blueprints in `routes/` are thin: they validate input and call into a service
  read from `ctx`. Business logic lives in the `*_service.py` modules.
- Because `ctx` is filled only by `app.py`, it is empty in any standalone
  interpreter — see [CLAUDE.md](CLAUDE.md) for why that matters when scripting.

### Example request flow — "show me a portfolio, priced"

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant R as routes/draupnir.py
    participant D as DraupnirService
    participant H as HuginnService
    participant T as Tradeon pulse

    UI->>R: GET /api/draupnir/portfolios/:id?market=steam
    R->>H: prices_for_valuation(token, market)
    Note over H: serves cached prices instantly,<br/>warms fresh prices in background
    H-->>R: {item: usd}, status
    R->>D: get_portfolio(id, prices)
    D-->>R: holdings + moving-average P/L
    R-->>UI: JSON (priced view, never blocks on pulse)
```

### Background work

About ten daemon threads start with the app: the confirmation scheduler (with
the token keep-alive and auto-store), Huginn's container price refresh and
case alerts, Draupnir's daily backup, the LOOT.Farm auction tracker, the
Gjallarhorn and Team Fortress 2 news watchers, Andvari's scans, the ASF driver,
the card and Team Fortress 2 sellers, and the 08:00 morning routine. The full
table with intervals and switches is in
[backend/CLAUDE.md](Yggdrasil/heimdall/backend/CLAUDE.md).

### Money flows (guarded)

Two features spend Steam wallet money; both follow the same pattern — **plan**
(read wallets, prices, countries), **review** in the UI, **dry run** that goes
through every check and cancels before paying, then **pay** with every price
re-checked to the minor unit:

- **Andvari "Buy games"** — the Steam web cart and checkout.
- **Storage shop** — the Counter-Strike 2 in-game store: a Game Coordinator
  transaction through Ratatoskr, approved on Steam's checkout page with the
  account's web session, delivered by the Game Coordinator. See
  [docs/internals/storage-shop.md](docs/internals/storage-shop.md).

Two features sell automatically (card auto-sell, Team Fortress 2 auto-sell):
they list on the Steam Market just under the lowest listing and confirm only
their own listings, and by default touch only items that arrive after they are
switched on.

### Non-blocking valuation

The valuation path is deliberately **non-blocking**: pages render immediately on
cached prices (or cost basis) and prices fill in on a later poll, so a slow or
rate-limited price feed never freezes the UI.

## 4. The Steam session & token lifecycle

This is the subtle part of the whole system, and the source of past outages.

- A Steam **mobile confirmation** request needs an authenticated web session
  cookie, which needs a valid **web access token**.
- That web token **expires roughly every 24 hours**.
- The only reliable way to mint a fresh one is a **full login**
  (`begin_auth_session`) — which needs the account password, which comes from
  the **Mímir vault**. (`GenerateAccessTokenForApp` returns empty even for fresh
  refresh tokens, so it cannot be used to refresh the web token.)

To keep this from lapsing, `scheduler.py` runs a proactive **keep-alive sweep**:

```mermaid
flowchart TB
    LOOP["scheduler loop"] -->|every few hours| SWEEP["keep-alive sweep<br/>over all accounts"]
    SWEEP --> CHECK{"web token<br/>TTL below floor?"}
    CHECK -->|yes| RENEW["full login → mint fresh web token<br/>(password from Mímir)"]
    CHECK -->|no| SKIP["leave it"]
    RENEW --> LOG["log [KEEPALIVE] N checked, R renewed, F failed"]
    SKIP --> LOG
    LOG -->|on failure| WARN["loud warning in logs"]
```

Operational tell: watch the logs for `[KEEPALIVE] … failed`. If confirmations
break fleet-wide, the web token has lapsed and the renewal path (usually the
password lookup) is the first suspect.

> Also note: the confirmation endpoint's `a` parameter must be the **SteamID64**,
> not the 32-bit account id. The wrong form returns a message that *looks* like a
> rate limit but is not.

## 5. Data model

State is plain files on disk (no database yet), which keeps the whole thing
portable and easy to back up.

| File | Holds | In git? |
|------|-------|---------|
| `backend/portfolios.json` | All Draupnir portfolios + transactions | **Yes**, on purpose (no secrets) |
| `backend/backups/portfolios/*.json.gz` | Snapshot history of the above | No (gitignored) |
| `backend/maFiles/*.maFile` | Steam Guard secrets (TOTP + identity secrets), encrypted | No — **secret** |
| `backend/maFiles/.heimdall_key` | Encryption key for maFiles + vault (unless `HEIMDALL_SECRET_KEY` is set in the root `.env`) | No — **secret** |
| `backend/maFiles/credentials.vault` | Mímir: encrypted login/password/email | No — **secret** |
| `backend/settings.json` | App settings incl. tokens and Telegram chat ids | No — **secret** |
| `backend/csfloat_keys.json` | CSFloat API key rotation pool | No — **secret** |
| `backend/telegram_caller.json` | Telegram session of the account that rings the phone | No — **secret** |
| `backend/cache/*` | Each service's state (scans, plans, purchase histories, ASF state) | No |
| `Yggdrasil/asf/config/` | ASF configuration and per-account login tokens | No — **secret** |

Portfolio shape:

```
portfolios.json
└── portfolios: { <id>: {
        id, name, created_at, updated_at,
        transactions: [ {
            id, item_name, type (buy|sell), qty, price,
            platform, date, note, fee_percent, created_at
        } ]
    } }
```

Valuation is computed on read (moving-average cost basis vs. live price); it is never
stored, so prices never go stale in the file.

### Backups (Draupnir point-in-time restore)

`portfolios.json` is hand-entered and cannot be regenerated, so every write and
once-a-day the backup service snapshots it:

- **Content-addressed**: filename carries a sha1 of the uncompressed content, so
  identical states dedupe for free.
- **Compressed**: snapshots are gzip (`*.json.gz`, roughly 10× smaller); legacy
  plain `.json` snapshots are still read.
- **GFS retention**: keep everything for 7 days, then thin to daily up to 90
  days, then weekly up to 2 years.
- **Safe restore**: restoring first snapshots the current state (as
  `pre-restore`) so the restore is itself reversible.

## 6. Frontend shape

React 19 + Vite + react-router-dom 7, single-page app.

- The **Dashboard** loads eagerly; every tool page is **code-split** with
  `React.lazy` + `Suspense`, so the initial bundle stays small and each tool
  loads on navigation.
- `pages/` holds route-level screens; `components/` holds the reusable pieces
  (market links, arbitrage panels, transfer queue, backups panel, and so on);
  `utils/` holds per-market helpers (Steam / Buff / CSFloat / LisSkins price
  links, Tradeon short links, transfer view math).
- The frontend only ever calls the backend REST API; it has no direct knowledge
  of Steam or Ratatoskr.

## 7. Operational model

- **Everything is Docker.** `make odin` builds and starts the fleet; see
  [README.md](README.md) for the full command list and ports.
- **Rate limiting is a first-class concern.** Steam throttles hard (HTTP 429),
  so item moves are serial with a delay, the scheduler sleeps between accounts,
  and the price feed is cached and warmed in the background. Do not remove these
  guards to "go faster".
- **Continuous integration** (`.github/workflows/ci.yml`) runs the backend
  pytest suite + ruff + `pip-audit`, and the frontend lint + build, on every
  push and pull request. The backend test suite is the real safety net; the
  frontend relies on lint + build.
- **Secrets never enter git or leave the machine.** They are all gitignored and
  excluded from the Docker build context. See [SECURITY.md](SECURITY.md).

---

*See also: [README.md](README.md) (commands), [the user guide](docs/guide/README.md)
(every screen), [CLAUDE.md](CLAUDE.md) (agent guide + gotchas),
[docs/internals/](docs/internals/) (feature deep dives).*
