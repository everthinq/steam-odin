# CLAUDE.md — steam-odin

Orientation for AI coding agents. Read this first; it is the map, the fleet
commands, and the list of things that will bite you if you do not know them.
Human-facing setup lives in [README.md](README.md) and the per-component
READMEs — this file does not repeat them, it points at them and adds the parts
that are not obvious from the code.

> This is Ivan's **personal** repository (a Steam-trading toolset). It is **not**
> the Revuze data platform. Ignore anything that talks about Databricks, Jira
> RD-tickets, OpenSearch, Knowledge Gate, Pipeline Resolver, or the `data-agents`
> plugin — that machinery belongs to a different repo and does not apply here.

---

## What this is

A monorepo of Steam-ecosystem trading tools, named after Norse figures. The
myth is only flavour — **the code hierarchy always wins over naming**; never move
or rename folders to make the mythology more accurate.

```
Yggdrasil/                      The World Tree — holds the deployable realms
├── heimdall/                   The Watchman — the main suite
│   ├── backend/                Flask API (Python). See backend/CLAUDE.md.
│   └── frontend/               React + Vite single-page app. See frontend/CLAUDE.md.
├── ratatoskr/                  The Courier — Node service. See ratatoskr/CLAUDE.md.
└── asf/                        ArchiSteamFarm (pinned upstream image) — card farming. See asf/README.md.
docs/guide/                     User guide for humans: setup, every screen, Telegram, troubleshooting
docs/internals/                 Deep dives on single features (protocols, live findings)
docs/steam-trading/             Trading knowledge base + glossary + baselines (local only, gitignored)
scripts/                        Host-side helpers (ASF setup, portfolio backup launchd job)
```

**Tools that live *inside* Heimdall** (each is frontend pages + backend service,
not a separate deployable):

| Tool | Role | Backend service | Frontend pages |
|------|------|-----------------|----------------|
| (core) | Steam authenticator: TOTP codes, sessions, mobile confirmations | `steam_service.py`, `scheduler.py` | `Confirmations.jsx`, `AddAccount.jsx` |
| **Draupnir** | Portfolio tracker (buy/sell, moving-average profit/loss, canonical platform names, live valuation, point-in-time backups) | `draupnir_service.py`, `draupnir_backup_service.py` | `pages/draupnir/` |
| **Huginn** | Cross-market price scout / arbitrage (Tradeon pulse feed, case arbitrage) | `huginn_service.py` | `pages/huginn/` |
| **Mímir** | Encrypted credential vault (login / password / email), shares the maFile key | `mimir_service.py` | `pages/mimir/` |
| **Ratatoskr** | Moves items between Storage Units and inventory; **Buy Storage Units** ("Storage shop" in the code) buys Storage Units on many accounts through the game store (Game Coordinator transaction, approved with the account's web session, price-guarded, dry run cancels); **Store Catalogue** lists every in-game store item with its US dollar price from the same price sheet, and its Buy button buys any of them through the same guarded flow; its **Arbitrage** tab (Store Catalogue Arbitrage) recommends store items that resell above the store price, per market after fees, with a daily price history (read-only) | `ratatoskr_service.py` → Node service (`store.js`), `storage_shop_service.py`, `store_catalogue_service.py`, `store_arbitrage_service.py` | `pages/ratatoskr/` (`StorageShop.jsx` at `/buy-storage-units`, `StoreCatalogue.jsx` at `/store-catalogue` and `/store-catalogue/arbitrage` with `components/ratatoskr/StoreArbitrage.jsx`) |
| **Gjallarhorn** (in Huginn) | Event rotation: when Valve limits a case, sell deflated holdings and rotate into it; Counter-Strike 2 news watcher that texts + rings the phone; read-only, never trades | `gjallarhorn_service.py`, `gjallarhorn_news_service.py`, `steam_market_service.py`, `telegram_caller.py` | `pages/huginn/Gjallarhorn.jsx`, `components/gjallarhorn/` |
| **Cross-Profile** (in Huginn) | Best buy-minimum → autobuy route per held item across every account, plus user-defined market chains | `cross_arbitrage_service.py` | `components/CrossProfileArbitrage.jsx` (Arbitrage page view) |
| **Harvest** (in Huginn) | Every purchase lot you still hold, at the price you paid (no averaging, oldest sold first), vs what an autobuy market pays now; account → market profiles like Arbitrage | `harvest_service.py` | `components/Harvest.jsx` (Arbitrage page tab) |
| **Andvari** (in Huginn) | Games whose card drops pay for them + ASF card farming status; "Buy games" tab (buy a deal on many accounts from their wallets, price-guarded); card auto-sell | `card_deals_service.py`, `asf_service.py` → ASF container, `store_purchase_service.py`, `card_seller_service.py` | `pages/huginn/CardDeals.jsx`, `components/carddeals/BuyPanel.jsx`, `SellingPanel.jsx` |
| **Team Fortress 2** (in Huginn) | New Team Fortress 2 case drops: news watcher rings + starts ASF playing Team Fortress 2 on every account the minute a case is added, drops listed on the Market as they land; every account gets the free game | `team_fortress_service.py`, `asf_service.py` (Team Fortress 2 mode + license sweep) | `pages/huginn/TeamFortress.jsx` |

## Architecture in one breath

- **Backend** is a Flask app. `app.py` constructs every service **once** at boot
  and hangs the singletons off `context.ctx`; route blueprints in `routes/`
  read them from `ctx` at request time. Runs `threaded=True`.
- **Frontend** is React 19 + Vite + react-router-dom 7. Tool pages are
  code-split with `React.lazy`/`Suspense`; the Dashboard is eager.
- **Ratatoskr** is a separate Node service the backend calls over HTTP; the
  frontend never talks to it directly.
- **ASF** (ArchiSteamFarm) is an unmodified upstream image the backend drives
  over its API (port 1242, this Mac only, password-guarded; its own UI is behind
  Andvari's "ASF UI" button). It never receives 2FA seeds — Heimdall types
  the password and a Steam Guard code in only when ASF asks.
- **Everything runs in Docker** via the root `docker-compose.yml`.

---

## Fleet commands (`make`)

`make help` lists them. Real name (legacy alias):

| Command | Does |
|---------|------|
| `make odin` (`all`) | Build + start detached (`forge` then `raid`) |
| `make forge` (`build`) | `docker compose build` |
| `make raid` (`up`) | `docker compose up -d` |
| `make bifrost` (`dev`) | `docker compose up` (attached logs) |
| `make saga` (`logs`) | `docker compose logs -f` |
| `make sleep` (`down`) | `docker compose down` |
| `make ragnarok` (`clean`) | down + remove orphans + prune images |

Ports: **frontend** http://localhost:3000, **backend** http://localhost:5001
(container 5000). Ratatoskr http://localhost:3001.
All three are published on **127.0.0.1 only**, and the backend refuses foreign
`Host` headers and cross-origin reads (`request_guard.py`). Neither the API nor
the UI has a login and they serve Steam Guard codes, the Mímir password export
and trade confirmations — never publish them on all interfaces, never re-open
CORS. ASF's UI/API is on 127.0.0.1:1242 too, guarded by its API password
(`make asf-password` copies it); ASF ignores host filtering, so the password is
what stops a DNS-rebinding website.

Container names (compose project = directory name):
- `steam-odin-heimdall-backend-1`
- `steam-odin-heimdall-frontend-1`
- `steam-odin-ratatoskr-1`
- `steam-odin-asf-1` (first run: `make asf-setup`, then `make asf`)

---

## Gotchas that will bite you (read before editing)

1. **The backend auto-reloads on every `.py` save — including half-finished
   edits and test files.** `docker-compose.yml` runs it with
   `FLASK_ENV=development`, so werkzeug's file watcher restarts `app.py` (and
   every background loop: auto-confirm, Case Arbitrage refresh, Gjallarhorn
   news, Andvari card deals) the moment a file under `backend/` changes; watch
   `logs/heimdall.log` for `Detected change … reloading`. So an edit is live
   before you run anything: when a background loop has outward effects (Steam
   calls, Telegram alerts), test long runs in a separate sandbox script (own
   cache file, stubbed notifier) rather than in the app. A clean
   `docker restart steam-odin-heimdall-backend-1` is still the way to reload a
   changed cache file. The frontend hot-reloads too (Vite); Ratatoskr does
   **not** (`docker restart steam-odin-ratatoskr-1` after editing its `.js`).

2. **`context.ctx` is empty outside the running app.** The singletons are wired
   only by `app.py` at boot. A bare `python -c` / `docker exec … python` shell
   has `ctx.steam_service is None`. To poke a service ad hoc, construct it
   directly, or replicate the `app.py` wiring — do not assume `ctx` is populated.

3. **Some Bash subshells have a broken PATH** (`curl`, `python3`, `wc`, `tr`
   come back "command not found"). When that happens, use the host `python3`
   explicitly, or `docker exec` into a container, or write the script to a file
   and run it — do not fight inline shell escaping.

4. **Inline `python -c` and f-strings with quotes/backslashes** cause
   `SyntaxError` from shell escaping. Write the script to a file (the scratchpad
   directory) and run it instead.

5. **Steam mobile confirmations need the SteamID64**, not the 32-bit account id,
   in the `a` parameter. The deprecated form returns
   "Oh nooooooes! / try again later" — which *looks* like a rate limit but is
   not. See `docs`-adjacent notes and `steam_service.py`.

6. **The web access token that confirmations need expires about every 24 hours.**
   The only reliable way to mint a fresh one is a full login
   (`begin_auth_session`); `GenerateAccessTokenForApp` returns empty even for
   fresh refresh tokens — do not chase that endpoint. `scheduler.py` runs a
   keep-alive sweep to renew tokens proactively; watch the logs for
   `[KEEPALIVE] … failed`.

7. **maFiles do not contain the account password.** Read passwords from the
   Mímir vault (`get_password` → vault by login), never by probing the maFile.

8. **One "playing" session per Steam account.** ASF farming and Ratatoskr's
   Counter-Strike 2 Game Coordinator session collide. `RatatoskrService.login`
   calls `before_login` (→ `AsfService.pause_for_ratatoskr`) and the ASF loop
   resumes the bot once Ratatoskr's session is gone. Keep that hook on any new
   Ratatoskr login path, and never hand ASF a 2FA seed or write a password into
   an ASF bot config.

9. **Steam rate limits (HTTP 429) are the enemy.** Ratatoskr moves items
   serially with a delay; the scheduler sleeps between accounts. Do not
   parallelise Steam calls or shorten these delays without a very good reason —
   see `ratatoskr/CLAUDE.md` and the notes in `scheduler.py`.

---

## Testing

**Backend** has a real pytest suite (`backend/tests/`). It is the gate. Run it
inside the container (pytest is installed ephemerally there):

```bash
docker exec steam-odin-heimdall-backend-1 sh -c \
  'pip install -q pytest 2>/dev/null; cd /app && python -m pytest -q'
```

Or on the host per the [README](README.md) (`pip install -r requirements-dev.txt && pytest`).

**Frontend** has **no test suite** — the gates are `npm run lint` and
`npm run build`. To verify a UI change actually renders, drive it in headless
Chrome (`--dump-dom --virtual-time-budget`) or open http://localhost:3000.

**Continuous integration** (`.github/workflows/ci.yml`) runs backend pytest +
ruff + `pip-audit`, and frontend lint + build, on every push and pull request.

---

## Conventions & house rules (non-negotiable)

- **Never commit or push without an explicit instruction.** "Put it on git" is
  not "commit and push now" — wait for the actual word. Commit messages are
  KISS + explanatory and end with a `Co-Authored-By:` trailer.
- **Do not abbreviate.** Spell things out in names, comments, and prose —
  explicit beats implicit. (Ivan's standing rule.)
- **Secrets stay untracked.** `*.maFile`, `.heimdall_key`, `.heimdall_salt`,
  `credentials.vault`, `settings.json`, `csfloat_keys.json`, `.env`,
  `Yggdrasil/asf/config/` are gitignored and
  must never be committed or sent to any external service. See
  [SECURITY.md](SECURITY.md).
- **`portfolios.json` is committed on purpose** (personal holdings, no secrets)
  as a lightweight backup; the on-disk snapshot history under `backups/` is
  gitignored. Match this pattern — do not add secrets to committed files.
- **Behaviour-preserving changes** unless asked otherwise; this is production
  data for a real trader. When touching backups or portfolios, verify data is
  preserved before deleting anything.

- **Money is spent only through the guarded routes** (Andvari "Buy games",
  Ratatoskr "Buy Storage Units" and its per-item pages), dry run first, and a real purchase only on Ivan's
  explicit word for the accounts and quantity he named. Never call Ratatoskr's
  raw `/store/purchase/*` endpoints or Steam checkout yourself.
- **Automatic selling handles items that arrive after it is switched on**;
  existing holdings only with an explicit opt-in.
- **Andvari never sends through the Huginn arbitrage Telegram bot** — only its
  own bot (`card_deals_bot_token` + `card_deals_chat_id`), or nothing.
- **The repository is public.** Never push tags (the local `checkpoint/*` tags
  stay local). Never stage `portfolios.json` unless Ivan asks for it. No account
  logins, balances, chat ids or holdings in documentation.
- **Never edit permission settings** (`.claude/settings*.json`) on your own.

## Where to read more

- Setup, environment variables, `tradeon_token`: [Yggdrasil/heimdall/README.md](Yggdrasil/heimdall/README.md)
- Backend internals, service wiring, per-file map: [Yggdrasil/heimdall/backend/CLAUDE.md](Yggdrasil/heimdall/backend/CLAUDE.md)
- Frontend conventions, lint traps, known gaps: [Yggdrasil/heimdall/frontend/CLAUDE.md](Yggdrasil/heimdall/frontend/CLAUDE.md)
- Ratatoskr endpoints + rate-limit rules: [Yggdrasil/ratatoskr/CLAUDE.md](Yggdrasil/ratatoskr/CLAUDE.md)
- ASF safety model and operations: [Yggdrasil/asf/README.md](Yggdrasil/asf/README.md)
- What every screen does (human guide): [docs/guide/](docs/guide/README.md)
- Feature deep dives: [docs/internals/](docs/internals/) — start with
  [storage-shop.md](docs/internals/storage-shop.md) before touching Buy Storage Units or the Store Catalogue
- Known drift between code, config and docs: [docs/internals/known-issues.md](docs/internals/known-issues.md)
- Human architecture tour: [ARCHITECTURE.md](ARCHITECTURE.md)
- Trading domain knowledge: [docs/steam-trading/](docs/steam-trading/) — local only: the
  repository is public and it holds paid-course notes, picks and holdings
