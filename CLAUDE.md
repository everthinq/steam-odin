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

> **Binding rules:** the numbered [Working rules](#working-rules-binding--read-every-session)
> near the end of this file. Unsure what Ivan means, or two rules clash? Ask him first.

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
scripts/                        Host-side helpers (ASF setup, portfolio backup launchd job, ui-screenshot.mjs)
```

**Tools that live *inside* Heimdall** (each is frontend pages + backend service,
not a separate deployable):

| Tool | Role | Backend service | Frontend pages |
|------|------|-----------------|----------------|
| (core) | Steam authenticator: TOTP codes, sessions, mobile confirmations; the Dashboard shows each account's Market access (result of Mímir's check) | `steam_service.py`, `scheduler.py` | `Confirmations.jsx`, `AddAccount.jsx`, `Dashboard.jsx` |
| **Draupnir** | Portfolio tracker (buy/sell, moving-average profit/loss, canonical platform names, live valuation, point-in-time backups) | `draupnir_service.py`, `draupnir_backup_service.py` | `pages/draupnir/` |
| **Huginn** | Cross-market price scout / arbitrage (Tradeon pulse feed, case arbitrage, Price dips vs the 30-day median, "check live" per row, stale prices greyed); **Active profiles**: only the profiles Ivan trades on are downloaded (every 5 minutes, active hours, Buff163 reference daily), every other page reads them from memory; Heimdall's own price history (`price_history.py`); **Pulse feed** page: read-only monitor | `huginn_service.py`, `pulse_feed.py`, `pulse_views.py` | `pages/huginn/` (`PulseFeed.jsx` at `/huginn/pulse`) |
| **Mímir** | Encrypted credential vault (login / password / email / comment / billing address), shares the maFile key; **Market access** button + column (when each account loses the Steam Community Market: newest store or in-game purchase + 1 year, and Steam's own eligibility check) and the **Market unlocker** (buys Zombie Hunter's one-cent Editor Pack, an in-game purchase that keeps the Market open a year; dry run cancels; price-guarded) | `mimir_service.py`, `market_access_service.py` | `pages/mimir/`, `components/MarketAccess.jsx`, `components/MarketUnlocker.jsx`, `utils/useMarketAccess.js` |
| **Ratatoskr** | Moves items between Storage Units and inventory (the **Transfer** page, where Ratatoskr opens: To mode treats every Storage Unit as one "limitless" storage — no unit to pick, a Storage space panel with the free places; underneath each unit still holds 1,000 and the deposit is split per unit, the fullest with room first; copies tradable now, copies on a plain trade hold (a Steam Market purchase — storable) and trade-protected copies (received in a trade — not storable, never selected) are separate rows, held cases and stickers open per unlock time; Transfer and Auto-Store move items only through the backend's commands (`storage_deposit.py`: plan → execute once → a job, the one copy of the storage rules; Ratatoskr confirms every move from the Game Coordinator's answer, a move with no answer is reported refused, or unconfirmed when the connection was lost first); items a Storage Unit cannot hold are hidden behind "Show items that can't be stored"); there is no Inventory page any more; the **Rename Storage Unit** tab lists every Storage Unit of the account and renames them (`storage_rename.py`: names of 1 to 20 characters, no duplicates, never during a move, "Name them Storage N" for unnamed units as plan → execute); **Buy Storage Units** ("Storage shop" in the code) buys Storage Units on many accounts through the game store (Game Coordinator transaction, approved with the account's web session, price-guarded, dry run cancels); **Store Catalogue** lists every in-game store item with its US dollar price from the same price sheet, and its Buy button buys any of them through the same guarded flow; its **Arbitrage** tab (Store Catalogue Arbitrage) recommends store items that resell above the store price, laid out like Huginn's Arbitrage (profile In-game store → market, autobuy or min), with CSMoney (Trade)'s instant offer as the BUY indicator, a daily price history, a Refresh button, and a daily refresh after the morning "Get all items" that posts profitable items to the Huginn Telegram bot (read-only, never buys); its **Boosts** tab (Store Boosts) catches a Steam price boost of a store item, or of a sticker inside a store capsule, on its first day: an hourly pulse check, Steam's price history one item at a time, levels watch / boost / second wave / fading, a Huginn Telegram message per new level and a phone ring on a boost (read-only, never buys) | `ratatoskr_service.py` → Node service (`moves.js`, `store.js`), `storage_deposit.py`, `jobs.py`, `storage_shop_service.py`, `store_catalogue_service.py`, `store_arbitrage_service.py`, `store_boost_service.py` | `pages/ratatoskr/` (`StorageShop.jsx` at `/buy-storage-units`, `StoreCatalogue.jsx` at `/store-catalogue`, `/store-catalogue/arbitrage` with `components/ratatoskr/StoreArbitrage.jsx` and `/store-catalogue/boosts` with `components/ratatoskr/StoreBoosts.jsx`) |
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
- **Ratatoskr** is a separate Node service the backend calls over HTTP (Docker
  network only, shared secret); the frontend never talks to it directly.
- **The backend is the product; the frontend is a screen on top of it**
  (Ivan's rule, 2026-10-31). Every decision — what is allowed, what to do, in
  what order — and every action with more than one step lives in the backend;
  the page only displays, sorts, filters and sends instructions. Each button is
  one backend command with the same name and parameters, so it could be typed
  in bash (a command-line tool over the same API is planned). Commands that act
  answer a plan first and act on "execute plan <id>" once; long work is a job
  (`GET /api/jobs/<id>`); errors are `{error, code}`. Ratatoskr's moves are the
  first feature built this way ([storage-units.md](docs/internals/storage-units.md)).
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
| `make reload` | restart the backend after editing its code (it never reloads by itself) |

Ports: **frontend** http://localhost:3000, **backend** http://localhost:5001
(container 5000). Ratatoskr is **not published**: only the backend reaches it,
over the Docker network, with the shared secret `RATATOSKR_SECRET` (root `.env`,
`make ratatoskr-secret` once; without it Ratatoskr refuses everything).
Both published ports are on **127.0.0.1 only**, and the backend refuses foreign
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

All four have a Docker health check (`docker ps` shows `healthy`) and restart after a
crash. ASF's own image check only sees that its process runs.

---

## Gotchas that will bite you (read before editing)

Numbered "Gotcha 1" to "Gotcha 9", so they are never confused with the
numbered [Working rules](#working-rules-binding--read-every-session) ("rule 9").

- Gotcha 1: **The backend does not reload by itself — `make reload` after editing
   a `.py` file.** It runs as one process with no auto-reloader (since Phase 2;
   before, every save restarted it and all background loops, half-finished
   edits included). Until you run `make reload` (restarts the backend container
   only) the old code keeps running, so a passing test does not mean the app
   runs it yet. A restart also starts every background loop (auto-confirm,
   Case Arbitrage refresh, Gjallarhorn news, Andvari card deals): when a loop has
   outward effects (Steam calls, Telegram alerts), test long runs in a separate
   sandbox script (own cache file, stubbed notifier) rather than in the app. The
   frontend still hot-reloads (Vite); Ratatoskr does not either
   (`docker restart steam-odin-ratatoskr-1` after editing its `.js`, which drops
   its Steam sessions).

- Gotcha 2: **`context.ctx` is empty outside the running app.** The singletons are wired
   only by `app.py` at boot. A bare `python -c` / `docker exec … python` shell
   has `ctx.steam_service is None`. To poke a service ad hoc, construct it
   directly, or replicate the `app.py` wiring — do not assume `ctx` is populated.

- Gotcha 3: **Some Bash subshells have a broken PATH** (`curl`, `python3`, `wc`, `tr`
   come back "command not found"). When that happens, use the host `python3`
   explicitly, or `docker exec` into a container, or write the script to a file
   and run it — do not fight inline shell escaping.

- Gotcha 4: **Inline `python -c` and f-strings with quotes/backslashes** cause
   `SyntaxError` from shell escaping. Write the script to a file (the scratchpad
   directory) and run it instead.

- Gotcha 5: **Steam mobile confirmations need the SteamID64**, not the 32-bit account id,
   in the `a` parameter. The deprecated form returns
   "Oh nooooooes! / try again later" — which *looks* like a rate limit but is
   not. See `docs`-adjacent notes and `steam_service.py`.

- Gotcha 6: **The web access token that confirmations need expires about every 24 hours.**
   The only reliable way to mint a fresh one is a full login
   (`begin_auth_session`); `GenerateAccessTokenForApp` returns empty even for
   fresh refresh tokens — do not chase that endpoint. `scheduler.py` runs a
   keep-alive sweep to renew tokens proactively; watch the logs for
   `[KEEPALIVE] … failed`.

- Gotcha 7: **maFiles do not contain the account password.** Read passwords from the
   Mímir vault (`get_password` → vault by login), never by probing the maFile.

- Gotcha 8: **One "playing" session per Steam account.** ASF farming and Ratatoskr's
   Counter-Strike 2 Game Coordinator session collide. `RatatoskrService.login`
   calls `before_login` (→ `AsfService.pause_for_ratatoskr`) and the ASF loop
   resumes the bot once Ratatoskr's session is gone. Keep that hook on any new
   Ratatoskr login path, and never hand ASF a 2FA seed or write a password into
   an ASF bot config.

- Gotcha 9: **Steam rate limits (HTTP 429) are the enemy.** Ratatoskr moves items
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

**Ratatoskr** has `npm test` (`Yggdrasil/ratatoskr/test/`, node's built-in
runner: move confirmations, item conversion).

**Continuous integration** (`.github/workflows/ci.yml`) runs backend pytest +
ruff + `pip-audit`, frontend lint + build, and Ratatoskr's tests, on every push
and pull request.

---

## Working rules (binding — read every session)

Every standing rule Ivan has given, numbered so a reply can cite one ("rule 4"; the
gotchas above are "Gotcha 1" to "Gotcha 9").
When a new rule is agreed, add it here in the same turn. Personal rules that
must not be public (account logins, holdings, the session reminder list) live
in the agent's private memory, not in this file.

### How we work together

1. **Explicit is better than implicit** (PEP 20, the Zen of Python). It is the
   reason behind most rules below. Never guess what Ivan means.
2. **When unsure, ask before acting.** If a request can be read two ways, or it
   is unclear how far to go ("fix it", "commit + push" — which files? the docs
   too?), ask one short, concrete question first. Asking is always welcome.
   Also ask when several tasks come without an order (which first?), and when
   a money action does not name the exact accounts and amounts.
3. **When two rules contradict each other, stop and tell Ivan**: name both
   rules and the case where they clash, propose a resolution, and wait. Never
   pick one silently.
4. **Do not abbreviate.** Write words out in full in prose, comments, docs,
   commit messages and new names ("for example", not "e.g."; "versus", not
   "vs"). Existing identifiers, field names and official names (CS2, Steam,
   API, USDT) stay exactly as they are. Use the official English name of a game
   feature, never a translation.
5. **Plain, short language.** KISS explanations: the essentials first, no
   jargon without a word on what it means.
6. **Behaviour-preserving changes** unless asked otherwise; this is
   production data for a real trader. When touching backups or portfolios,
   verify the data is preserved before deleting anything.
7. **Report honestly.** Failed tests, skipped steps and unverified claims are
   said plainly. A trade or flip is "safe" only after checking where the money
   lands and when it can be spent; otherwise say it is unverified.

### Git and the public repository

8. **Never commit or push without the explicit word** ("commit", "push",
   "commit + push"). "Put it on git" is not that word. Commit messages are
   KISS + explanatory and end with a `Co-Authored-By:` trailer. One exception:
   rule 25.
9. **The repository is public.** No account logins, balances, chat ids,
   holdings or trading results in tracked files, docs or commit messages —
   except `portfolios.json`, public by Ivan's choice (rule 11).
10. **Never push tags** (the local `checkpoint/*` tags stay local).
11. **Never stage `portfolios.json`** unless Ivan asks for it. It is committed
    on purpose as a lightweight backup (holdings, no secrets); the snapshot
    history under `backups/` is gitignored.
12. **Secrets stay untracked.** `*.maFile`, `.heimdall_key`, `.heimdall_salt`,
    `credentials.vault`, `settings.json`, `csfloat_keys.json`, `.env`,
    `Yggdrasil/asf/config/` are gitignored and never committed or sent to any
    external service. **Nothing stored in Mímir** (passwords, emails, billing
    addresses) is ever committed, written to a tracked file or shown outside
    the app. Test data uses obviously fake values. See [SECURITY.md](SECURITY.md).
13. **Never bypass the pre-commit guard** (no `--no-verify`).
    `scripts/pre-commit-secret-guard.sh` → `backend/secret_scan.py`; install with
    `ln -sf ../../scripts/pre-commit-secret-guard.sh .git/hooks/pre-commit`.
14. **Never edit permission settings** (`.claude/settings*.json`) on your own.

### Money (Ivan's real investments — be careful)

15. **Money is spent only through the guarded routes** (Andvari "Buy games",
    Ratatoskr "Buy Storage Units" and its per-item pages, Mímir's "Market
    unlocker"): dry run first, a real purchase only on Ivan's explicit word for
    the accounts and quantity he named. Never call Ratatoskr's raw
    `/store/purchase/*` endpoints or Steam checkout yourself.
16. **Never sell, list or move holdings on your own.** Automatic selling
    handles items that arrive after it is switched on; existing holdings only
    with an explicit opt-in. Show what would be affected before anything big.
17. **Small reversible housekeeping needs no permission** (for example emptying
    a Steam cart before a guarded purchase; log what was removed). Spending,
    selling and anything irreversible always does.
18. **Small safe gains count.** Never call a positive, low-risk flip "not worth
    it"; list the checks to make instead (rule 7).

### Tools and architecture

19. **The backend decides, the page displays** (see "Architecture in one
    breath"). New logic goes in the backend as a command: plan → execute once
    → a job, errors `{error, code}`.
20. **Ratatoskr: Transfer and Auto-Store share one set of storage rules.** On
    any Ratatoskr logic change, say so first and ask whether it applies to
    both. See [storage-units.md](docs/internals/storage-units.md).
21. **Steam rate limits:** never parallelise Steam or Game Coordinator calls.
    The move delay of 200 ms is proven by Ivan; do not warn about it, revisit
    only if refusals or HTTP 429 actually show up.
22. **Each tool that sends Telegram messages uses its own bot** when one is
    configured. Andvari never sends through the Huginn arbitrage bot — only its
    own (`card_deals_bot_token` + `card_deals_chat_id`), or nothing.
23. **Myth is flavour; the code hierarchy wins.** Never rename or move folders
    to make the Norse names more accurate.
24. **Ignore the Revuze `data-agents` plugin noise** (Knowledge Gate, RD
    tickets, plugin-update notices) in this repository.

### Reviews

25. **A review only when it is needed.** When Ivan asks for a pull request or
    code review and it is not necessary at the moment (for example a
    documentation-only change, or nothing touching money, security or moves),
    say so in one line with the reason, and do the commit + push instead: that
    request counts as the word for rule 8. When a review is needed, do it.

## Where to read more

- Setup, environment variables, `tradeon_token`: [Yggdrasil/heimdall/README.md](Yggdrasil/heimdall/README.md)
- Backend internals, service wiring, per-file map: [Yggdrasil/heimdall/backend/CLAUDE.md](Yggdrasil/heimdall/backend/CLAUDE.md)
- Frontend conventions, lint traps, known gaps: [Yggdrasil/heimdall/frontend/CLAUDE.md](Yggdrasil/heimdall/frontend/CLAUDE.md)
- Ratatoskr endpoints + rate-limit rules: [Yggdrasil/ratatoskr/CLAUDE.md](Yggdrasil/ratatoskr/CLAUDE.md)
- ASF safety model and operations: [Yggdrasil/asf/README.md](Yggdrasil/asf/README.md)
- What every screen does (human guide): [docs/guide/](docs/guide/README.md)
- Feature deep dives: [docs/internals/](docs/internals/) — start with
  [storage-shop.md](docs/internals/storage-shop.md) before touching Buy Storage Units or the Store Catalogue
- Storage Units — what can go in (trade hold vs trade protection, attribute 312) and which unit
  fills first; Transfer and Auto-Store share it: [docs/internals/storage-units.md](docs/internals/storage-units.md)
- Tradeon pulse (every price on every Huginn page): [docs/internals/pulse-api.md](docs/internals/pulse-api.md) —
  all requests go through the master feed `backend/pulse_feed.py`, never anywhere else
- Steam Market price history (`pricehistory`: login, wallet currency, errors, store vs market
  names, who reads it): [docs/internals/steam-pricehistory.md](docs/internals/steam-pricehistory.md)
- Steam Community Market access (the one-year purchase rule, what counts, free licenses do not,
  Purchase History and `eligibilitycheck`): [docs/internals/steam-market-access.md](docs/internals/steam-market-access.md)
- Known drift between code, config and docs: [docs/internals/known-issues.md](docs/internals/known-issues.md)
- Human architecture tour: [ARCHITECTURE.md](ARCHITECTURE.md)
- Trading domain knowledge: [docs/steam-trading/](docs/steam-trading/) — local only: the
  repository is public and it holds paid-course notes, picks and holdings
