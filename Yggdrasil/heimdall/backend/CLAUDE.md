# CLAUDE.md — Heimdall backend (Flask)

Agent notes for the Python backend. The root [CLAUDE.md](../../../CLAUDE.md) has
the fleet-wide rules; this file is the backend map and its specific traps.
Deep dives on single features live in [docs/internals/](../../../docs/internals/).

## Boot & wiring

`app.py` is the composition root. At import it:

1. calls `setup_logging()` **before** constructing anything (rotating
   `logs/heimdall.log`, 5 MB × 5, plus console);
2. installs `request_guard` (Host allow-list + cross-origin write refusal);
3. constructs each service **once**, in this order (constructor arguments shown):

| # | Service | Built from |
|---|---------|------------|
| 1 | `SettingsManager()` | `settings.json` (defaults in `settings.py`) |
| 2 | `SteamService()` | its own `SecureStorage` over `maFiles/` |
| 3 | `RatatoskrService()` | `RATATOSKR_URL` |
| 4 | `HuginnService(steam, ratatoskr)` | + `settings_provider` (all market fees) |
| 5 | `DraupnirService(huginn)` | `portfolios.json` |
| 6 | `BackupService(draupnir.path)` | + `draupnir.set_backup(...)` |
| 7 | `ConfirmationScheduler(settings, steam, ratatoskr)` | |
| 8 | `MimirService(steam.storage)` | shares the maFile key |
| 9 | `SteamMarketService(steam)` | Gjallarhorn liquidity |
| 10 | `GjallarhornService(draupnir, huginn, steam_market, ratatoskr, steam)` | read-only cockpit |
| 11 | `TelegramCaller()` | `telegram_caller.json` |
| 12 | `GjallarhornNewsService(settings, telegram_caller)` | |
| 13 | `CrossArbitrageService(huginn, draupnir)` | |
| 14 | `HarvestService(huginn, draupnir)` | |
| 15 | `CardDealsService(steam, settings)` | Andvari |
| 16 | `AsfService(steam, ratatoskr)` | + `ratatoskr.before_login = asf.pause_for_ratatoskr`, `asf.card_deals = card_deals` |
| 17 | `TeamFortressService(settings, steam, asf, telegram_caller)` | |
| 18 | `CardSellerService(settings, steam, asf)` | |
| 19 | `StorePurchaseService(steam, card_deals, asf, settings_provider)` | Andvari "Buy games" |
| 20 | `StorageShopService(steam, ratatoskr, card_deals)` | Ratatoskr Buy Storage Units ("Storage shop" in the code) |
| 21 | `StoreCatalogueService(ratatoskr, storage_shop)` | Store Catalogue; sets `storage_shop.on_price_sheet` |
| 22 | `StoreArbitrageService(huginn, store_catalogue, storage_shop, draupnir)` | Store Catalogue Arbitrage (the Arbitrage tab) |
| 23 | `MorningRoutine(...)` | wired to `routes.huginn` scan + CSFloat sweep |

4. hangs the singletons on `context.ctx` and registers the blueprints
   (`routes/__init__.py`: accounts, settings, draupnir, ratatoskr, huginn, mimir);
5. starts the background threads — **only in the reloader child**
   (`WERKZEUG_RUN_MAIN == 'true'`) when `FLASK_ENV=development`, so they never
   run twice — then `app.run(threaded=True)`.

> **`ctx` is populated only here.** In a bare `python -c` or `docker exec`
> shell, `ctx.steam_service` etc. are `None`. Construct services directly or
> mirror this wiring if you need to script against them.

> **A constructor that raises takes the whole backend down** (and the
> auto-reloader keeps restarting into the same crash). Compile before saving
> (`python -m py_compile file.py`) and mind the order of attributes inside
> `__init__` (load state before deriving from it).

## Background threads (started at boot, all daemon threads)

| Thread | Interval | Does | Switch |
|--------|----------|------|--------|
| `scheduler` | `check_interval` (default 300 s, floor 10) | Token keep-alive sweep every 4 h (renews accounts with < 8 h + 0–2 h jitter left); confirmation sweep (1 s between accounts); auto-store sweep | `auto_check_enabled`, `auto_confirm_market`, `auto_confirm_trades`, `auto_store_enabled` |
| Huginn container refresh | `case_poll_interval_sec` (600 s, floor 60) | Full container pull hourly, alert markets in between, then Case Arbitrage alerts | needs `tradeon_token`; alerts need `case_alerts_enabled` |
| Draupnir backups | hourly | `boot` snapshot, then a `daily` snapshot after UTC midnight, then prune | always |
| LOOT.Farm auction tracker | 900 s | Snapshot into the auction log | always |
| Gjallarhorn news | `gjallarhorn_news_poll_minutes` (10, floor 2) | Counter-Strike 2 news → limited case/collection → Telegram + phone ring | `gjallarhorn_news_armed` |
| Andvari card deals | 300 s tick | Starts a scan every `card_deals_scan_interval_hours` (12) | `card_deals_auto_scan_enabled` |
| ASF | 45 s tick (20 s first wait) | Provision/enable bots (max 20), resume after Ratatoskr, assist one login, license sweep, Team Fortress 2 mode | off until `ASF_IPC_PASSWORD` is set |
| `team-fortress-news` | `team_fortress_poll_minutes` (2) | Team Fortress 2 news → case added → play + ring | `team_fortress_watch_enabled` |
| `team-fortress-sell` | 15 s | Auto-stop after `team_fortress_auto_stop_hours`; sell step (inventory at most every 5 min) | `team_fortress_auto_sell_enabled` |
| `andvari-card-sell` | 20 s | Card auto-sell step (each inventory at most every 30 min) | `card_auto_sell_enabled` (off by default) |
| `store-arbitrage-watch` | 1 h (5 min first wait) | Warms the Store Catalogue Arbitrage default markets, which records the daily price history | needs `tradeon_token` |
| `morning-routine` | 60 s tick | 08:00 local: "Get all items" scan, then the CSFloat buy-order sweep (unless < 12 h old); catches up after the Mac sleeps | always |

On-demand threads: CSFloat sweep, trade-alert sender, market/cross-arbitrage/
Harvest warmers, card-deals scans, `andvari-{kind}` store-purchase jobs,
`storage-shop-{kind}` jobs (`plan`, `purchase`, `delivery`, `price sheet`), `store-arbitrage-warm`, `team-fortress-sell-now`.

**Every one of these restarts when any `.py` file is saved** (see below).

## File map

| File | Responsibility |
|------|----------------|
| `app.py` | Composition root, `ctx` wiring, background thread start, JSON error handlers, `GET /health` |
| `context.py` | The `ctx` singleton holder (empty until `app.py` fills it) |
| `request_guard.py` | CORS only for `localhost:3000` / `127.0.0.1:3000`; 403 for a foreign `Host` (DNS rebinding) or a cross-origin write; extra hosts via `HEIMDALL_ALLOWED_HOSTS` |
| `routes/` | Flask blueprints by domain: `accounts`, `settings`, `draupnir`, `ratatoskr`, `huginn`, `mimir`. Each reads `ctx` at request time. About 135 routes; `tests/test_routes_blueprints.py` counts them per blueprint |
| `steam_service.py` | Steam login, TOTP, sessions, **mobile confirmations**, web-token lifecycle, password lookup from Mímir, outbound proxy (`HTTP(S)_PROXY`, `SOCKS_PROXY`); debug log `logs/steam_debug.log` with secrets redacted |
| `scheduler.py` | Background auto-confirm loop **+ session keep-alive sweep** + auto-store sweep + auto-confirmed trade alerts |
| `storage.py` | maFile load/save, encryption (key from `HEIMDALL_SECRET_KEY` or `maFiles/.heimdall_key`), legacy migration, soft delete to `maFiles/.deleted/` |
| `huginn_service.py` | Tradeon pulse price feed, cross-market prices, market registry + fees, case arbitrage catalog + Telegram board, CSFloat buy-order sweep and key rotation, LOOT.Farm auctions (largest file) |
| `cross_arbitrage_service.py` | One board across all accounts: per held item the best buy-minimum market vs the best autobuy market, fee-netted, plus user-defined chains; cached 10 min, warmed in the background |
| `harvest_service.py` | Harvest (Huginn tab): each purchase lot still held (`DraupnirService.open_lots()`: real price paid, sells use up the oldest buys first) vs the chosen autobuy market's instant offer; per-market index cached 10 min and warmed one market at a time; marks site-balance payouts and cash offers above 1.3× Buff163's listing |
| `draupnir_service.py` | Portfolio store, moving-average profit/loss, canonical platform names, CSV import/export, combined ledger |
| `draupnir_backup_service.py` | Point-in-time snapshots of `portfolios.json`, gzip, content-addressed, GFS retention, safe restore |
| `mimir_service.py` | Encrypted credential vault (shares the maFile key), import parser, plaintext export, test login, rolling `.bak` copies |
| `gjallarhorn_service.py` | Event-rotation cockpit (read-only, never trades): rotation sell list scored on liquidity, market-hold whitelist, target basket, Storage Unit readiness |
| `gjallarhorn_news_service.py` | Counter-Strike 2 news watcher: detects a case/collection added or removed (rules in the docstring), baseline on first run, records before alerting so a reload cannot ring twice |
| `steam_market_service.py` | Steam Market liquidity (`priceoverview` public, `pricehistory` needs the web session), serial with 3 s + 2 s gaps, defers history while the page is in use, gzip cache |
| `telegram_caller.py`, `telegram_caller_login.py` | Rings Ivan's phone through a burner Telegram user account (Telethon; a bot cannot call). One-time setup: `docker exec -it steam-odin-heimdall-backend-1 python /app/telegram_caller_login.py` |
| `morning_routine.py` | Daily "Get all items" at 08:00 local (catches up the first minute the Mac is awake after it), then rebuilds the CSFloat item dictionary (`cache/csfloat_item_links.json`: held item → CSFloat listing) and starts the buy-order sweep unless prices are under 12 hours old; state in `cache/morning_routine.json` |
| `card_deals_service.py` | Andvari (Huginn → Card deals): games whose trading-card drops resell for more than the game costs, per account; background scan with its own Steam throttles, cache in `cache/card_deals.json.gz`; listing values are capped at each card's recent sale price from the authenticated price history (`_refresh_sales`, US dollar answers only, a day per card) and the all-accounts total counts only the accounts the market absorbs in a week (`accounts_market_absorbs`) — shortlists ignore the history (`_IGNORE_SALES`) so a ruled-out game is re-checked; a badges page not served to the account (expired session, `badges_page_owner`) is an error, never "0 drops"; `read_drops(steamid)` re-reads one account's badges for the ASF service. Telegram (deal alerts, card-sale summaries) only through Andvari's own bot (`card_deals_bot_token` + `card_deals_chat_id`, `notifications.own_bot_settings`), never the shared Huginn arbitrage bot; without one, Andvari is silent |
| `asf_service.py` | ArchiSteamFarm driver: hardened bot per account, switched on only while it has cards to farm (max 20, Ivan's choice; ASF's FAQ recommends 10; never off with drops left: a bot that showed cards stays on until its badges pages, read through `CardDealsService.read_drops`, confirm 0), password + Steam Guard code only when ASF asks (paced, three tries), pause while Ratatoskr plays, farming status; Team Fortress 2 mode (chosen bots play app 440 outside the 20-bot ceiling) and the license sweep (every account, new ones included, gets the free Team Fortress 2 license; stopped bots started one at a time for it); off until `ASF_IPC_PASSWORD` is set |
| `team_fortress_service.py` | Team Fortress 2 case drops: polls the app 440 news feed for "Added the … Case" → starts Team Fortress 2 mode, adds the case to the sell list, texts + rings; auto-sell reads each playing account's inventory (paced) and lists sell-list items one minor unit under the lowest Market listing in the wallet currency (fee rules from `g_rgWalletInfo`), confirming only those listings; state in `cache/team_fortress.json` |
| `store_purchase_service.py` | Andvari "Buy games" tab: plan (per account: store country, wallet currency + balance from `g_rgWalletInfo`, owned games from the store's `dynamicstore/userdata`, regional price of the cheapest default package from `appdetails`, US dollar conversion, maximum price, balance, best Andvari profit first) and guarded wallet purchases (empty cart only, cart and Steam's final price must equal the plan to the cent, checkout on `checkout.steampowered.com`, dry run cancels before paying, then ASF farm now); history + statistics in `cache/store_purchases.json` |
| `storage_shop_service.py` | Ratatoskr Buy Storage Units ("Storage shop" in the code) — Counter-Strike 2 Storage Units, or any other price sheet entry (`item`, default `casket`; per-item price cap list × 1.25; game license and Armory Pass never), through the game store over the Game Coordinator. Full protocol, guards and live findings: [docs/internals/storage-shop.md](../../../docs/internals/storage-shop.md). Plan (wallet, store country, price sheet entry "casket", web-inventory Storage Unit count), guarded purchase (Init with the game store's 0-based currency → Steam's `ClientMicroTxnAuthRequest` must be exactly this order → approval page `approvetxn/<transid>` with `approved=1` → Finalize as a Game Coordinator job, which is when the wallet is charged); dry run cancels; after approval failures read "MAY BE PAID" and "Deliver again" re-sends Finalize; every sheet read goes to `on_price_sheet`, and the `price sheet` job only reads it; state in `cache/storage_shop.json` |
| `store_catalogue_service.py` | Store Catalogue (read-only): every entry of the game store's price sheet with its US dollar price, category and store-front flag; English names from Ratatoskr's `POST /items/store-names` (the last names known are kept when Ratatoskr is down; two named by hand). Takes every sheet `StorageShopService` reads (`on_price_sheet`); "Read prices again" is that service's `price sheet` job, so it never overlaps a purchase; state in `cache/store_catalogue.json`. Each row has `cannot_trade` (the item's own "cannot trade" from Ratatoskr, or the known list for older names) |
| `store_arbitrage_service.py` | Store Catalogue Arbitrage (read-only, recommendations only): every tradable catalogue item (keys, passes and "cannot trade" items left out) against the chosen markets' buy orders and lowest listings after `market_fee`, the cheapest wallet currency among the accounts, your Draupnir record (`DraupnirService.item_trades`) and a 180-day daily history (`cache/store_arbitrage_history.json.gz`); cash prices above 1.3× Buff163's listing are suspicious, a wide gap that stays open is "unconfirmed". Deep dive §6b of [storage-shop.md](../../../docs/internals/storage-shop.md) |
| `card_seller_service.py`, `market_seller.py`, `community_pacer.py` | Andvari card auto-sell (off by default): new trading cards listed in the wallet currency at the highest price that still sells — not above the 90th percentile of the last 7 days of sales, at most 3 days of sales queued ahead, one cent under the next wall of listings (`market_seller.patient_target`; price history kept 6 hours) — never under one cent below the lowest listing nor under the highest buy order, only cards that arrived after it was switched on, only their confirmations accepted (exact item name, type 3 or market type 12), retried until confirmed; statistics per account and game in `cache/card_sales.json`. `community_pacer` keeps one 4 s steamcommunity.com gap shared by the card seller, the Team Fortress 2 seller and Buy Storage Units |
| `jsonio.py` | Crash-safe atomic JSON read/write (`.tmp-*.json` then rename) |
| `validation.py` | Request-body validation for writes |
| `settings.py` | `settings.json` load with safe defaults; a corrupt file is copied aside and writes are refused until it loads |
| `notifications.py` | Telegram / webhook sender; `own_bot_settings(settings, prefix)` for a feature's own bot (no fallback) |
| `logging_setup.py` | Rotating log + console; level from `HEIMDALL_LOG_LEVEL` |
| `system_ops.py` | `trigger_restart()` (exit, Docker restarts) — currently unused |
| `cases_containers.json` | Bundled container catalog (read-only) |

## Telegram routing (who sends where)

| Feature | Bot | Chat |
|---------|-----|------|
| Case Arbitrage board, auto-confirmed trade alerts | Shared "Huginn arbitrage" bot (`telegram_bot_token`), else `notify_webhook_url` | `telegram_chat_id` |
| Gjallarhorn news | Shared bot | `gjallarhorn_chat_id`, else `telegram_chat_id` |
| Team Fortress 2 releases and auto-stop | Shared bot | `team_fortress_chat_id`, else `telegram_chat_id` |
| **Andvari** deal alerts and card-sale summaries | **Only its own bot** (`card_deals_bot_token`) | `card_deals_chat_id` — silent without them |
| Phone rings (Gjallarhorn news, Team Fortress 2 release, manual ring) | `TelegramCaller` burner user account | `telegram_caller.json` target |

Buy Storage Units, the Store Catalogue (and its Arbitrage tab), Buy games, ASF and Harvest send nothing. **Never route Andvari
through the shared bot**, and never add a fallback to it (Ivan's rule).

## Data files (all gitignored except `portfolios.json`)

| Path | Holds | Secret? |
|------|-------|---------|
| `portfolios.json` | Draupnir holdings — **committed on purpose**, but never stage it unless Ivan asks | no |
| `backups/portfolios/*.json.gz` | Snapshot history (`portfolios__<time>__<reason>__<sha8>`; reasons change, daily, boot, manual, pre-restore) | no |
| `maFiles/*.maFile` | Steam Guard secrets, encrypted | **yes** |
| `maFiles/.heimdall_key` | maFile + vault key (unless `HEIMDALL_SECRET_KEY` is set) | **yes** |
| `maFiles/credentials.vault` (+ `.bak-*`, newest 10) | Mímir vault | **yes** |
| `settings.json` (+ `.corrupt-*`) | Settings incl. tokens and chat ids | **yes** |
| `csfloat_keys.json` | CSFloat API key pool (+ optional proxy) | **yes** |
| `telegram_caller.json` | Burner Telegram session | **yes** |
| `cache/` | Every service's state (below) | low |
| `logs/` | `heimdall.log`, `steam_debug.log` (redacted), portfolio backup logs | low, but may hold SteamIDs |

`cache/` files: `huginn_scan.json`, `huginn_csfloat_buyorders.json`,
`csfloat_item_links.json`, `csfloat_key_state.json`, `case_price_history.json`,
`case_alert_state.json`, `huginn_container_snapshots.json`,
`lootfarm_auction_log.json`, `gjallarhorn_liquidity.json.gz`,
`card_deals.json.gz`, `asf_state.json`, `team_fortress.json`,
`card_sales.json`, `store_purchases.json`, `storage_shop.json`, `store_catalogue.json`, `store_arbitrage_history.json.gz`,
`storage_shop_approval_page.html`, `morning_routine.json`. Cache files are not
watched: `docker restart steam-odin-heimdall-backend-1` after editing one by hand.

## Settings and environment

Every setting with its default is in `settings.py` (`DEFAULT_SETTINGS`); the
Settings route accepts known keys only and type-casts them. Notes:
`csfloat_api_key` is a dead setting (keys come from `csfloat_keys.json`).

Environment: `FLASK_ENV`, `PORT` (5000), `RATATOSKR_URL` (compose:
`http://ratatoskr:3000`; code fallback `:3030`), `ASF_URL`, `ASF_IPC_PASSWORD`
(ASF off when empty), `HEIMDALL_SECRET_KEY`, `HEIMDALL_ALLOWED_HOSTS`,
`HEIMDALL_LOG_LEVEL`, proxy variables.

## Running & testing

Backend **auto-reloads on every `.py` save** (`FLASK_ENV=development` in
`docker-compose.yml` enables werkzeug's watcher; see `Detected change … reloading`
in `logs/heimdall.log`), and every background loop restarts with it — so an edit
is live immediately, half-finished or not. Test long runs with outward effects
(Steam calls, Telegram alerts) in a sandbox script with its own cache file and a
stubbed notifier.

```bash
# tests inside the running container (pytest installed ephemerally)
docker exec steam-odin-heimdall-backend-1 sh -c \
  'pip install -q pytest 2>/dev/null; cd /app && python -m pytest -q'

# lint (matches CI)
docker exec steam-odin-heimdall-backend-1 sh -c 'cd /app && ruff check .'
```

About 775 tests in 36 files (`tests/`); `conftest.py` stubs CSFloat so no test
reaches the network. Every feature that spends money or sells has pure helpers
with tests — extend them, including with bytes captured live.
Dev dependencies: `requirements-dev.txt` (pytest, ruff, pip-audit). The image
runs Python 3.9; CI runs 3.11 — write code that works on both.

## Backend-specific traps

- **Money paths are guarded, never bypassed.** Buy games
  (`store_purchase_service.py`) and Buy Storage Units (`storage_shop_service.py`)
  re-check currency, price and balance to the minor unit, and support a dry
  run that cancels before paying. Test with dry run; spend only on Ivan's
  explicit word, through the guarded routes.
- **Sell fees live in ONE place.** The market registry in `huginn_service.py` holds
  the defaults (ours where confirmed, else pulse's own fee from
  `GET /api/commission-settings`), and the Fees editor on the Arbitrage page (settings
  `huginn_market_fees`) overrides them. Every profit calculation reads them through
  `HuginnService.market_fee(market_id)`. That covers the Arbitrage profiles, Case
  Arbitrage, Cross-Profile, Harvest, Store Catalogue Arbitrage, LOOT.Farm and the auctions. Never add a
  per-feature fee constant.
- **The pulse market registry (`_MARKET_REGISTRY` in `huginn_service.py`) has
  rules beyond id + fee.** Every generated pair, Cross-Profile, Harvest and Store
  Catalogue Arbitrage read markets through `_pull_market` / `market_buy_index` /
  `market_autobuy_index`, so a rule placed there applies everywhere:
  - `buy_type` None = sell-only (buy orders, no listings): refused as a buy source
    or min target before pulse is asked.
  - `_MARKETS_NOT_IN_PULSE_UI` (SkinSwapTrade, GgSwap, GamerPay): tables answer
    but the pulse website hides them; prices can be months old. UI shows an ⓘ.
  - `_MARKET_PRICE_SCALE`: prices in a bonus balance are turned into real dollars
    in `_pull_market`. SkinSwap keeps one balance, "Trade = Market × 1.4"; pulse's
    SkinSwapTrade `Buy` is what its Trade page pays, in Trade dollars, so it is
    divided by 1.4 (checked on the live Trade page 2026-10-10: Redline $35.35 →
    $25.25 ≈ Buff). SkinSwapMarket `Sell` is already real dollars.
  - `buy_type` `_ESTIMATED_SELL` (SkinSwapTrade only): pulse has no Trade-page
    asking prices, so `_estimated_listings` makes them = pay price ×
    `_SKINSWAP_TRADE_ASK_MARKUP` (measured on 11 skins; the $1–$5 band rests on one
    point), only where pay ≥ `_ESTIMATE_MIN_PAY` and ≥ 0.6 × SkinSwap (Market)'s
    listing. Rows carry `estimated: True`; the pseudo price type must never reach
    pulse (`fetch_generated_pair` and `fetch_generated_csfloat_autobuy` route it);
    `market_buy_index` returns `{}` for it so Cross-Profile and Store Catalogue
    Arbitrage stay real-data only. More Trade-page prices → update the markup points.
  - `market_registry()` exposes these to the UI as `hasListings`, `notInPulseUi`,
    `priceNote`, `listingsEstimated`, plus fee `feeSource` / `feeEdited`.
  - Pulse discovery: `GET /api/table/supported-features/counter-strike/market-info`
    (price types per market), `GET /api/commission-settings` (fees), per-item
    `POST /api/item/market-best-prices` (`{"marketHashName", "gameType": "CsGo"}`).
    Tests: `tests/test_huginn_markets.py`.
- **Automatic selling only touches items that arrive after it is switched on**,
  unless an explicit opt-in says otherwise (`card_auto_sell_include_held`).
- **Confirmations `a` param = SteamID64**, not the 32-bit account id (see
  `steam_service.py`). Wrong form returns a fake-looking rate-limit message.
- **Web token for confirmations expires ~24h.** Only a full login mints a fresh
  one; `GenerateAccessTokenForApp` returns empty. `scheduler.py` keep-alive
  renews proactively — do not remove it. Watch `[KEEPALIVE] … failed`.
- **Passwords come from the Mímir vault by login**, never from the maFile
  (maFiles have no password field).
- **Do not parallelise or speed up Steam calls** — 429 rate limits. The
  scheduler sleeps between accounts on purpose; steamcommunity.com calls share
  `community_pacer`.
- **Any new Ratatoskr login path goes through `RatatoskrService.login`**, so the
  `before_login` hook pauses ASF (one "playing" session per account).
- **Blueprints import services from `ctx` at request time**, so import order is
  not a concern, but a service being `None` means `app.py` did not wire it.
