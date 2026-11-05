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
| 3 | `RatatoskrService()` | `RATATOSKR_URL`; every call sends `X-Ratatoskr-Secret` (`RATATOSKR_SECRET`); `moves_in_progress = deposit.busy`, so `items_moving(ratatoskr, steamid)` — what Huginn's scan and Buy Storage Units ask before logging an account out — also covers a move run between two batches |
| 3a | `JobRegistry()` | `jobs.py`: long work started by a command, followed at `GET /api/jobs/<id>` |
| 3b | `DepositService(ratatoskr, jobs)` | `storage_deposit.py`: every Storage Unit move (Transfer, Auto-Store, the command line) |
| 4 | `HuginnService(steam, ratatoskr)` | + `settings_provider` (all market fees) |
| 5 | `DraupnirService(huginn)` | `portfolios.json` |
| 6 | `BackupService(draupnir.path)` | + `draupnir.set_backup(...)` |
| 7 | `ConfirmationScheduler(settings, steam, ratatoskr, deposit)` | Auto-Store deposits through `DepositService` |
| 7a | `AutoStore(settings, steam, deposit, on_change=scheduler._sync_protected_accounts)` | `auto_store.py`: one watchlist per account + the master view; `migrate_shared_watchlist()` at boot copies the old shared list (`auto_store_items`) to each switched-on account once |
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
| 20 | `StorageShopService(steam, ratatoskr, card_deals, jobs=jobs, moves_in_progress=deposit.busy)` | Ratatoskr Buy Storage Units ("Storage shop" in the code); its jobs are in the shared `JobRegistry`; new units are not named while a move runs |
| 21 | `StoreCatalogueService(ratatoskr, storage_shop)` | Store Catalogue; sets `storage_shop.on_price_sheet` |
| 22 | `StoreArbitrageService(huginn, store_catalogue, storage_shop, draupnir)` | Huginn → CS2 Store Arbitrage (routes in `routes/huginn.py`, `/api/huginn/store-arbitrage…`) |
| 23 | `StoreBoostService(huginn, store_catalogue, ratatoskr, steam, store_arbitrage, telegram_caller)` | Huginn → CS2 Store Boosts (routes in `routes/huginn.py`, `/api/huginn/store-boosts…`); `set_settings_provider` at once |
| 24 | `MarketAccessService(steam, card_deals, jobs=jobs)` | Mímir's Market access button and Market unlocker (routes in `routes/mimir.py`; Andvari's exchange rates for the price guard; the unlocker's jobs in `JobRegistry`) |
| 25 | `MorningRoutine(...)` | wired to `routes.huginn` scan + CSFloat sweep |

4. hangs the singletons on `context.ctx` and registers the blueprints
   (`routes/__init__.py`: accounts, settings, draupnir, ratatoskr, huginn, mimir, jobs);
5. starts the background threads, once (one process, no auto-reloader), then
   `app.run(threaded=True, use_reloader=False)`.

> **`ctx` is populated only here.** In a bare `python -c` or `docker exec`
> shell, `ctx.steam_service` etc. are `None`. Construct services directly or
> mirror this wiring if you need to script against them.

> **A constructor that raises takes the whole backend down** (Docker restarts
> it into the same crash). Compile before `make reload`
> (`python -m py_compile file.py`) and mind the order of attributes inside
> `__init__` (load state before deriving from it).

## Background threads (started at boot, all daemon threads)

| Thread | Interval | Does | Switch |
|--------|----------|------|--------|
| `scheduler` | `check_interval` (default 300 s, floor 10) | Token keep-alive sweep every 4 h (renews accounts with < 8 h + 0–2 h jitter left); confirmation sweep (1 s between accounts); auto-store sweep | `auto_check_enabled`, `auto_confirm_market`, `auto_confirm_trades`, `auto_store_enabled` |
| Huginn container refresh | `case_poll_interval_sec` (600 s, floor `CASE_POLL_MINIMUM_SECONDS` = 60) | Full container pull hourly, alert markets in between, then Case Arbitrage alerts | needs `tradeon_token`; alerts need `case_alerts_enabled` |
| Draupnir backups | hourly | `boot` snapshot, then a `daily` snapshot after UTC midnight, then prune | always |
| LOOT.Farm auction tracker | 900 s | Snapshot into the auction log | always |
| Gjallarhorn news | `gjallarhorn_news_poll_minutes` (10, floor 2) | Counter-Strike 2 news → limited case/collection → Telegram + phone ring | `gjallarhorn_news_armed` |
| Andvari card deals | 300 s tick | Starts a scan every `card_deals_scan_interval_hours` (12) | `card_deals_auto_scan_enabled` |
| ASF | 45 s tick (20 s first wait) | Provision/enable bots (max 20), resume after Ratatoskr, assist one login, license sweep, Team Fortress 2 mode | off until `ASF_IPC_PASSWORD` is set |
| `team-fortress-news` | `team_fortress_poll_minutes` (2) | Team Fortress 2 news → case added → play + ring | `team_fortress_watch_enabled` |
| `team-fortress-sell` | 15 s | Auto-stop after `team_fortress_auto_stop_hours`; sell step (inventory at most every 5 min) | `team_fortress_auto_sell_enabled` |
| `andvari-card-sell` | 20 s | Card auto-sell step (each inventory at most every 30 min) | `card_auto_sell_enabled` (off by default) |
| `store-arbitrage-watch` | 1 h (5 min first wait) | Warms the CS2 Store Arbitrage default markets, which records the daily price history, then posts newly profitable store items to the shared bot | needs `tradeon_token`; posts need `store_arbitrage_alerts_enabled` |
| `store-boosts-watch` | 1 h (3 min first wait) | Pulse check of every watched store item and capsule sticker (Steam listing, buy orders, 30-day median; Buff163 listing); flagged items to the front of the Steam queue | `store_boost_enabled`, needs `tradeon_token` |
| `store-boosts-steam` | one read per `store_boost_steam_gap_seconds` (30, floor 10; 1 min first wait) | Steam `pricehistory` one item at a time (logged-in, `community_pacer`), 429 back-off 10 min doubling to 2 h; analyses each and alerts (shared bot, ring on a boost) | `store_boost_enabled`; alerts `store_boost_alerts_enabled`, ring `store_boost_ring_enabled` |
| `pulse-keeper` | 30 s tick | The only background downloader: the active Huginn profiles' tables once older than `huginn_active_minutes`, Buff163's lowest listing once a day (the reference), then one-time downloads (the price history's seed, asked again hourly through `want_once`: missing or a month old), one at a time, only inside the active hours; a failed table waits 5 minutes; after the Mac slept it forgets the failures (seed failures too) and waits a tick for the network | `huginn_active_profiles` (empty: only the reference), `huginn_active_minutes`, `huginn_active_hours` |
| `morning-routine` | 60 s tick | 08:00 local: "Get all items" scan, then the CSFloat buy-order sweep (unless < 12 h old), then `store-arbitrage-daily` (store price sheet, every market, Telegram post); catches up after the Mac sleeps | always |

On-demand threads: CSFloat sweep, trade-alert sender, market/cross-arbitrage/
Harvest warmers, card-deals scans, `andvari-{kind}` store-purchase jobs,
`storage-shop-{kind}` jobs (`plan`, `purchase`, `delivery`, `price sheet`), `store-arbitrage-warm`, `store-arbitrage-daily`, `store-boosts-check`, `store-boosts-ring`, `team-fortress-sell-now`, `market-access-check`, `market-access-unlock`.

They start once, in `start_background()`, only when the backend runs as `python app.py`
(importing `app` starts none). They all start again on `make reload` (see below).

## File map

| File | Responsibility |
|------|----------------|
| `app.py` | Composition root, `ctx` wiring, background thread start, JSON error handlers, `GET /health` |
| `commands.py`, `error_handlers.py` | `CommandError(code, message, status)`: the one shape of a refused command, raised by any service; `error_handlers.install(app)` answers it as `{error, code}` with its status for every route (no route catches it; a test app installs the handlers too), Flask's own request errors keep their status with a `code`, a crash answers 500 `unexpected_error` |
| `context.py` | The `ctx` singleton holder (empty until `app.py` fills it) |
| `request_guard.py` | CORS only for `localhost:3000` / `127.0.0.1:3000`; 403 for a foreign `Host` (DNS rebinding) or a cross-origin write; extra hosts via `HEIMDALL_ALLOWED_HOSTS` |
| `routes/` | Flask blueprints by domain: `accounts`, `settings`, `draupnir`, `ratatoskr`, `huginn`, `mimir`, `jobs` (`GET /api/jobs/<id>`). Each reads `ctx` at request time. About 140 routes; `tests/test_routes_blueprints.py` counts them per blueprint |
| `steam_service.py` | Steam login, TOTP on Steam's clock (`_query_time`: asked again every 5 minutes, when this clock moved backwards, and after a rejected code, which is tried once more), sessions, **mobile confirmations**, web-token lifecycle, password lookup from Mímir, outbound proxy (`HTTP(S)_PROXY`, `SOCKS_PROXY`); debug log `logs/steam_debug.log` with secrets redacted |
| `auto_store.py` | Auto-Store's settings, the only place that reads or changes them: one watchlist per account (`auto_store_watchlists`), accounts switched on one by one, the master switch, the master view, suggestions from the account's inventory (`storage_block`), `sweep_refusal`; every change is one item or one account under one lock |
| `scheduler.py` | Background auto-confirm loop **+ session keep-alive sweep** + auto-store sweep + auto-confirmed trade alerts. Auto-Store deposits through the same command as Transfer (`DepositService.plan_deposit(names=…, allow_overflow=True)` → `execute`): Transfer's rules by construction; the history records only moves the Game Coordinator confirmed (`_auto_store_finished`); an item with code `refused` is not offered again for 6 hours, one not sent or unconfirmed is offered at the next sweep; the inventory is asked again up to 3 times while it comes back empty (right after a connect). Tests: `tests/test_storage_deposit.py` |
| `confirmation_settings.py` | The Global Confirmations settings: interval limits (`CHECK_INTERVAL_MIN_SECONDS` 10 to 3600, step 5; the scheduler sleeps `loop_interval`), `clean` (refuses a bad value with a code, used by `/api/confirmations/settings` and `/api/settings`), `view` (values, limits, `polling_note`). Check All Now is `ConfirmationScheduler.start_check_all`, a job (kind `confirmation_check`). Tests: `tests/test_confirmation_settings.py` |
| `storage_deposit.py` | **The one copy of the Storage Unit rules** ([storage-units.md](../../../docs/internals/storage-units.md)): `storage_block(item)` (Storage Unit, `item_moveable: false`, trade protection — attribute 312, `trade_locked` while `trade_protected_until` is ahead; a Market purchase's plain hold is no block), `plan_deposit` (one storage, fullest unit with room first), `STORAGE_CAPACITY`; `DepositService`: `plan_deposit` / `plan_withdraw` → `execute(plan_id)` once (a second call returns the same job; plans expire after 10 minutes, executed ones are forgotten after an hour; one run per account; a plan made before the account's last run finished is `plan_stale`; refusals are `commands.CommandError` with a `code`), runs the batches one after another through Ratatoskr's `/move/batch`, counts only the ids Ratatoskr confirmed for that batch's `runId` (a dropped queue → `unconfirmed`, never moved; every failed item has a `code`: `refused`, `not_sent`, `busy`, `unconfirmed`), calls `on_finish` before the job reads as finished, remembers what it just deposited per unit for 2 minutes (the Game Coordinator's count lags), `storage(steamid)` for the free space. Routes in `routes/ratatoskr.py`: `/storage/<steamid>`, `/deposit/plan`, `/withdraw/plan`, `/plans/<id>/execute`; the inventory route adds `storage_block` to every item |
| `storage_rename.py` | Rename Storage Unit tab: `RenameService(ratatoskr, moves_in_progress)` — `units(steamid)` (name, items, free places, named or not; unnamed first), `rename(steamid, item_id, name)` (1 to 20 characters, no two units of one account with the same name, refused while a move runs because a renamed unit gets a new item id), `plan_name_unnamed` → `execute(plan_id)` once ("Storage N" from `next_storage_names`, one rename after another). Routes `GET /api/ratatoskr/storage-units/<steamid>`, `POST …/<steamid>/rename`, `POST …/<steamid>/name-unnamed/plan`, `POST …/name-plans/<plan_id>/execute`. Tests: `tests/test_storage_rename.py` |
| `jobs.py` | `JobRegistry`: one shape for long work started by a command — `{id, kind, running, done, failed, total, phase, error, result, started_at, finished_at}` — kept in memory (newest 200), read at `GET /api/jobs/<id>` |
| `storage.py` | maFile load/save, encryption (key from `HEIMDALL_SECRET_KEY` or `maFiles/.heimdall_key`), legacy migration, soft delete to `maFiles/.deleted/` |
| `huginn_service.py` | "Get all items" inventory scan (`scan`: an account it cannot log into keeps its items from the previous scan, `account_scans` says when each account was read and which were `carried_over`), Tradeon pulse price feed, cross-market prices, market registry + fees, case arbitrage catalog (`_CONTAINER_MARKET_DISPLAY` is the one market list: `container_markets()` answers each market's label, chip, link slug, `sellable` and `balance` in `/api/huginn/cases` → `markets`) + Telegram board (`case_alert_status` never answers the bot token or webhook, only whether each is set; `clean_case_alert_settings` checks the panel's save, route `cases/alerts/settings`), CSFloat buy-order sweep and key rotation, LOOT.Farm auctions (largest file) |
| `pulse_feed.py` | **The master price feed: the only code that asks Tradeon pulse for prices.** A table is (market id, price type), one normalised body (`BASE_BODY`: no filters, sold-out items shown); single flight per table, at most 2 downloads at once (each peaks at ~200 MB; in the app each runs in a short-lived process, `download_in_separate_processes`, and `give_memory_back` returns freed memory), freshness chosen by the caller (`FRESH` 10 min, `ON_DEMAND` 1 min), compact storage packed into typed arrays (`packed_rows.py`, ~7 MB a table instead of 155 MB) with each price's market update time (`updated` / `offersUpdateTime`), weekly sales per item and market (`weekly_sales`, from `itemPopularity`), single-item `lookup` (name filter, ~0.5 s), **the gate: only Huginn Arbitrage downloads** (the keeper and `with feed.reading(download=True)`, opened only for the profile routes; every other read gets memory or `NotActive`), active tables (`set_active`, the Buff163 reference, `download_once`, `set_active_hours`, `start_keeper` with the Mac-sleep pause; `MEGABYTES_PER_DOWNLOAD` and `MEGABYTES_PER_TABLE_IN_MEMORY`, the only traffic and memory sizes, answered by `status()` as `downloaded_megabytes` / `megabytes_in_memory`), per-request reads (which tables a request read and when they were downloaded, the `X-Pulse-Tables` header), recurring jobs, slaves that only read (`declare_job`, `with feed.job(id)`: who reads what), `is_stale` / `STALE_SECONDS`, bonus balances scaled once, listeners, `status()` (`GET /api/huginn/pulse`). Anything that keeps its own copy stamps it with the table's download time (`HuginnService.market_index_at`), never "now". API facts and ideas: [docs/internals/pulse-api.md](../../../docs/internals/pulse-api.md) |
| `packed_rows.py` | `PackedRows`: rows of numbers per item name in typed arrays (about 8 bytes a number instead of ~300 bytes per Python tuple); values that cannot be packed are kept as they are, so every row reads back exactly. `PackedEntries`: a price table's entries with the same tuple shape. Used by the pulse tables, the weekly sales (one per market) and today's price history rows |
| `pulse_views.py` | Read-only views on the feed: `price_dips` (Huginn → Price dips: listing vs 30-day median/average, weekly sales), `arbitrage_profiles` / `CURATED_PROFILES` (every Arbitrage profile, `GET /api/huginn/profiles`), `check_live` (one Arbitrage row by name lookups, profit with `market_fee`; `check_live_refusal` says why a pair cannot be checked, the route answers `code: not_checkable`), `steam_liquidity` (Gjallarhorn: Steam weekly sales, lowest listing, highest buy order, 30-day median), `profile_tables` / `parse_active_profiles` / `apply_active_settings` / `active_status` (answers `estimated_megabytes_per_day`, itself and per `preview`, counting only the active hours: `active_minutes_a_day`) (the active profiles, the only tables pulse is asked for: each profile's tables, proven against every profile route by `tests/test_active_profiles.py`). Price dips and the liquidity read memory and `price_history.py`, never download. `STATISTICS_MARKETS`, `STALE_SECONDS` (a day) |
| `price_history.py` | Heimdall's own price statistics, only from the tables Huginn Arbitrage downloads (a listener on the feed): one row a day per item and table (average, lowest, highest, last, samples), `cache/price_history/<market>/<price type>/<day>.json.gz`, kept for good; `statistic()` = 30-day median or average of the daily averages from 7 days on, pulse's Median30d/Average30d seed (refreshed monthly, ignored after 45 days) before; `trade_statistic` = middle of the listing's and buy orders' own statistics, never mixing the seed into one side; reads `PulseTable.quotes()`, saves outside the main lock |
| `cross_arbitrage_service.py` | One board across all accounts: per held item the best buy-minimum market vs the best autobuy market, fee-netted, plus user-defined chains (each chain row answers its own `buy_price` = first market, `sell_net` = last market, null when that end is not priced); cached 10 min, warmed in the background; the scan answers `poll_after_seconds` (only while a warm runs) and `status: failed` + `error` after a crashed warm, which is not retried for `_RETRY_AFTER_FAILURE`; a refused settings save is `{error, code: 'save_failed'}` |
| `harvest_service.py` | Harvest (Huginn tab): each purchase lot still held (`DraupnirService.open_lots()`: real price paid, sells use up the oldest buys first) vs the chosen autobuy market's instant offer; per-market index cached 10 min and warmed one market at a time; marks site-balance payouts and cash offers above 1.3× Buff163's listing; decides every mark the page shows (row `profitable`, `counted`, `short_of_offers`, `maybe_trade_held`, `best.stale`; board `trade_hold_days`, `stale_after_seconds`, `summary.profitable_not_counted`) |
| `draupnir_service.py` | Portfolio store, moving-average profit/loss, canonical platform names, CSV import/export, combined ledger, `pricing_status` (the live-price line and when the page asks again) |
| `draupnir_backup_service.py` | Point-in-time snapshots of `portfolios.json`, gzip, content-addressed, GFS retention, safe restore |
| `mimir_service.py` | Encrypted credential vault (shares the maFile key): login, password, email, comment, `billing_address` (several lines; never sent to Steam, not in the four-field text export); import parser, plaintext export, test login, rolling `.bak` copies; the export and the single-password route are POST (Origin-checked) and every reveal is logged without the password |
| `gjallarhorn_service.py` | Event-rotation cockpit (read-only, never trades): rotation sell list scored on liquidity (from pulse via `pulse_views.steam_liquidity` for every held item; `SteamMarketService` only for items pulse lacks), market-hold whitelist (`clean_holds`, the only cleaner: the page sends its rows as typed; the save answers `save_failed`), target basket, Storage Unit readiness (answers its `steamid` and `looseFree`) |
| `gjallarhorn_reminders.py` | Gjallarhorn resell reminders: an item, the time its trade lock ends (ISO with offset), a note; a 60 s loop (`gjallarhorn-reminders` thread) sends one Telegram text when it is due (missed ones after the Mac wakes), never rings, never trades; marked sent only when Telegram accepted it; `cache/gjallarhorn_reminders.json`; routes `GET/POST /api/huginn/gjallarhorn/reminders`, `DELETE …/<id>` |
| `gjallarhorn_news_service.py` | Counter-Strike 2 news watcher: detects a case/collection added or removed (rules in the docstring), baseline on first run, records before alerting so a reload cannot ring twice; `save_settings` (route `news/settings`) is the one checked writer of `gjallarhorn_news_armed` and `gjallarhorn_chat_id` |
| `steam_market_service.py` | Gjallarhorn's fallback Steam Market liquidity (items pulse does not know) (`priceoverview` public, `pricehistory` needs the web session), serial with 3 s + 2 s gaps, defers history while the page is in use, gzip cache |
| `telegram_caller.py`, `telegram_caller_login.py` | Rings Ivan's phone through a burner Telegram user account (Telethon; a bot cannot call). One ring at a time for every feature (a class-wide lock: two Telethon clients on one session can get it revoked); `ring(wait=False)` answers `{'busy': True}` at once instead of queuing (CS2 Store Boosts), `busy()`. One-time setup: `docker exec -it steam-odin-heimdall-backend-1 python /app/telegram_caller_login.py` |
| `morning_routine.py` | Daily "Get all items" at 08:00 local (catches up the first minute the Mac is awake after it), then rebuilds the CSFloat item dictionary (`cache/csfloat_item_links.json`: held item → CSFloat listing) and starts the buy-order sweep unless prices are under 12 hours old, then its `after_scan` step (the CS2 Store Arbitrage daily refresh); state in `cache/morning_routine.json` |
| `card_deals_service.py` | Andvari (Huginn → Card deals): games whose trading-card drops resell for more than the game costs, per account; background scan with its own Steam throttles, cache in `cache/card_deals.json.gz`; listing values are capped at each card's recent sale price from the authenticated price history (`_refresh_sales`, US dollar answers only, a day per card) and the all-accounts total counts only the accounts the market absorbs in a week (`accounts_market_absorbs`) — shortlists ignore the history (`_IGNORE_SALES`) so a ruled-out game is re-checked; a badges page not served to the account (expired session, `badges_page_owner`) is an error, never "0 drops"; `read_drops(steamid)` re-reads one account's badges for the ASF service. Telegram (deal alerts, card-sale summaries) only through Andvari's own bot (`card_deals_bot_token` + `card_deals_chat_id`, `notifications.own_bot_settings`), never the shared Huginn arbitrage bot; without one, Andvari is silent. `summary.scopes` answers `sale`, `full` and `all`; each row's `few_listings` marks a set under `FEW_LISTINGS`; a scan or buy-order check while one runs raises `JobRunning` (route: 409 `already_running`) |
| `asf_service.py` | ArchiSteamFarm driver: hardened bot per account, switched on only while it has cards to farm (max 20, Ivan's choice; ASF's FAQ recommends 10; never off with drops left: a bot that showed cards stays on until its badges pages, read through `CardDealsService.read_drops`, confirm 0), password + Steam Guard code only when ASF asks (paced, three tries), pause while Ratatoskr plays, farming status (each account's `actions` from `STATE_ACTIONS`, the buttons the page shows; action route errors carry a `code`); Team Fortress 2 mode (chosen bots play app 440 outside the 20-bot ceiling) and the license sweep (every account, new ones included, gets the free Team Fortress 2 license; stopped bots started one at a time for it); off until `ASF_IPC_PASSWORD` is set |
| `team_fortress_service.py` | Team Fortress 2 case drops: polls the app 440 news feed for "Added the … Case" → starts Team Fortress 2 mode, adds the case to the sell list, texts + rings; auto-sell reads each playing account's inventory (paced) and lists sell-list items one minor unit under the lowest Market listing in the wallet currency (fee rules from `g_rgWalletInfo`), confirming only those listings; state in `cache/team_fortress.json` |
| `store_purchase_service.py` | Andvari "Buy games" tab: plan (per account: store country, wallet currency + balance from `g_rgWalletInfo`, owned games from the store's `dynamicstore/userdata`, regional price of the cheapest default package from `appdetails`, US dollar conversion, maximum price, balance, best Andvari profit first) and guarded wallet purchases as a command: `create_purchase_plan(plan_created_at, selection, empty_cart)` (`POST /api/huginn/card-deals/buy/purchase-plan`) → `execute_purchase_plan(id, dry_run)` (`POST …/purchase-plans/<id>/execute`, `GET …/purchase-plans/<id>`): the dry run is repeatable and empties the cart when `empty_cart` is on (logged); a real purchase only after a dry run of that same purchase plan finished without an error, only the accounts that passed, once; codes `dry_run_required`, `already_bought`, `plan_unknown`, `busy` (empty cart only, cart and Steam's final price must equal the plan to the cent, checkout on `checkout.steampowered.com`, dry run cancels before paying, then ASF farm now; fails closed: only `dry_run: false` buys and the plan time the page showed, `plan_created_at`, is required); history + statistics in `cache/store_purchases.json` |
| `storage_shop_service.py` | Ratatoskr Buy Storage Units ("Storage shop" in the code) — Counter-Strike 2 Storage Units, or any other price sheet entry (`item`, default `casket`; per-item price cap list × 1.25; game license and Armory Pass never), through the game store over the Game Coordinator. Full protocol, guards and live findings: [docs/internals/storage-shop.md](../../../docs/internals/storage-shop.md). Commands like the moves: `start_plan(selection, item)` → `describe_plan` (lines from `plan_lines`, totals, `state` checking / ready / failed / replaced / expired, `expires_at`) → `execute_plan(plan_id, dry_run)`: a dry run first (repeatable; its outcome kept on the plan by `_record_dry_run`), a real purchase only after a dry run of the same plan passed and only for the lines that passed, once (a second call answers the same job; `CommandError` codes such as `dry_run_required`, `plan_expired`, `plan_replaced`, `busy`); purchase and delivery jobs carry their `orders`, `steps` and per-account outcomes (`purchase_outcome`, `can_deliver_again`); account rows say `selectable` / `max_quantity` / `can_buy_code`, `quote` totals the order panel without Steam. Plan (wallet, store country, price sheet entry "casket", web-inventory Storage Unit count), guarded purchase (Init with the game store's 0-based currency → Steam's `ClientMicroTxnAuthRequest` must be exactly this order → approval page `approvetxn/<transid>` with `approved=1` → Finalize as a Game Coordinator job, which is when the wallet is charged); dry run cancels (fails closed: only `dry_run: false` buys); after approval failures read "MAY BE PAID" and "Deliver again" re-sends Finalize; every sheet read goes to `on_price_sheet`, and the `price sheet` job only reads it; state in `cache/storage_shop.json` |
| `store_catalogue_service.py` | Store Catalogue (read-only): every entry of the game store's price sheet with its US dollar price, category, store-front flag and `buyable` (the Buy button: not in `NOT_FOR_SALE_ENTRIES` and named by Ratatoskr); English names from Ratatoskr's `POST /items/store-names` (the last names known are kept when Ratatoskr is down; two named by hand). Takes every sheet `StorageShopService` reads (`on_price_sheet`); "Read prices again" is that service's `price sheet` job, so it never overlaps a purchase; state in `cache/store_catalogue.json`. Each row has `cannot_trade` (the item's own "cannot trade" from Ratatoskr, or the known list for older names) |
| `store_arbitrage_service.py` | CS2 Store Arbitrage (read-only, recommendations only): every tradable catalogue item (keys, passes and "cannot trade" items left out) against the chosen markets' buy orders and lowest listings after `market_fee`, the cheapest wallet currency among the accounts, your Draupnir record (`DraupnirService.item_trades`) and a 180-day daily history (`cache/store_arbitrage_history.json.gz`); cash prices above 1.3× Buff163's listing are suspicious, so is any price its market has not updated for a day (`is_stale`: never best, signal or Telegram), a wide gap that stays open is "unconfirmed". Indicator: CSMoney (Trade)'s instant offer as a share of the store price (`signal` at `store_arbitrage_signal_pct`, `near` from `NEAR_SIGNAL_RATIO` of it); **one rule for what a row sells for**, `sell_offer` (board, `summary.profitable` and Telegram), under a profile from `profiles_of` (`?profile=…&cash_only=…`, rows carry `sell`, `sell_profit`, `sell_pct`, `owned`); settings only through `POST …/store-arbitrage/settings` (`settings_changes`, checked); `take_pull` (store items from every full table Huginn pulls, `HuginnService.add_pull_listener`, so the Case Arbitrage polls price them for free), `refresh()` (Refresh button), `start_daily_refresh` (morning routine `after_scan`: price sheet, every default market, then `send_alerts`) and the hourly newly-profitable post, through the shared Huginn bot. Deep dive §6b of [storage-shop.md](../../../docs/internals/storage-shop.md) |
| `store_boost_service.py` | CS2 Store Boosts (read-only, an alarm): catches a Steam price boost of a store item or of a sticker inside a store capsule (contents from Ratatoskr's `/items/capsule-contents`) on its first day. Hourly pulse check (`preflag`: Steam buy orders 1.3× or listing 1.5× the 30-day median), a Steam `pricehistory` reader one item at a time (queue, active items hourly, store items 3 h, contents 12 h; 429 back-off), the pure detector `analyze` (usual = median day of days 30 to 3 ago; `watch` / `boost` / `second_wave` / `fading`; sales alone never alarm), `opening_value` (rarity odds), `alert_decision` (each level once an episode, first sight only records unless the rise is under 48 h old); the board decides every flag the page shows (`active`, `moving`, `recommend_buy` from `BUY_LEVELS`, `contents.moving`, `opening_beats_store`, `tradable_if_bought_now`, `level_hints` written from the thresholds); confirmation by `StoreArbitrageService.instant_cash_offers`. Shared Huginn bot + `TelegramCaller` ring on a boost. Tuned on Flickshot, World Eaters 1 and its capsule (`tests/fixtures/store_boost_histories.json.gz`). Deep dive §6c of [storage-shop.md](../../../docs/internals/storage-shop.md) |
| `market_access_service.py` | Market access (Mímir's button, `GET /api/mimir/market-access`, `POST …/check`; the Dashboard shows the result; read-only): per account, one at a time, the first page of Purchase History (`parse_history`; `counted_purchases`: types "Purchase" and "In-Game Purchase", not refunded; Market transactions never count; free promotional licenses never appear) → `access_summary` (lapses 365 days after the newest counted purchase, `opens_on` for one under 7 days old), and Steam's verdict from `market/eligibilitycheck/` (no redirects; `webTradeEligibility` cookie, `parse_eligibility`); `access_state` ok / warn (30 days) / lapsed / unknown; a 429 stops the check; `cache/market_access.json`. **Market unlocker**, a command (plan → execute once → a job): `plan_unlock` (`POST /api/mimir/market-access/unlock/plan {steamids}`) runs the dry run as a job and answers a plan (per account passed, or failed and why; price; `max_usd`; expires 10 minutes after the dry run); `execute_plan` (`POST …/plans/<plan_id>/execute {dry_run: false}`, `GET …/plans/<plan_id>`) buys only on the accounts that passed, once (again: the same job); only `dry_run: false` buys, anything else is refused (`real_purchase_only`); codes `plan_unknown`, `plan_expired`, `plan_not_ready`, `plan_replaced` (a newer dry run covers one of its accounts), `plan_failed`, `nothing_passed`, `busy`; no route buys without a plan. Each account: claims Zombie Hunter (free package 617273, `freelicense/addfreelicense`) if needed, `buyitem/1723420/330/1` → approval page → `check_unlock_page` (account, game, Editor Pack, quantity 1, total ≤ `UNLOCK_MAX_USD` $0.05 converted, Authorize offered; a billing-address page is refused) → dry run cancels (`approved=0`), real approves + finalize + `unlock_purchase_listed` in Purchase History, else "MAY BE PAID"; history in the same cache file. `status()` also decides what the pages print: per account `headline`, `advice`, `purchase_would_help` + `reason`; `flagged`, `not_checked`, `last_unlocks`. Deep dive: [steam-market-access.md](../../../docs/internals/steam-market-access.md) |
| `card_seller_service.py`, `market_seller.py`, `community_pacer.py` | Andvari card auto-sell (off by default): new trading cards listed in the wallet currency at the highest price that still sells — not above the 90th percentile of the last 7 days of sales, at most 3 days of sales queued ahead, one cent under the next wall of listings (`market_seller.patient_target`; price history kept 6 hours) — never under one cent below the lowest listing nor under the highest buy order, only cards that arrived after it was switched on, only their confirmations accepted (exact item name, type 3 or market type 12), retried until confirmed; statistics per account and game in `cache/card_sales.json`. Settings only through `POST /api/huginn/card-deals/selling/config` (`save_config`: enabled, foil, apps parsed by `store_purchase_service.parse_app_ids`, `bad_apps`) and `…/selling/include-held` (`set_include_held`: switching on answers 409 `confirm_needed` until `confirmed`); the status sends amounts per ISO currency code, each sale's `result` (`listed`, `buy_order`, `refused`) and `returns` (`purchase_returns`: Buy games spending against cards listed, `net` only when one currency). `community_pacer` keeps one 4 s steamcommunity.com gap shared by the card seller, the Team Fortress 2 seller, Buy Storage Units and the Market access check |
| `secret_scan.py` | The pre-commit guard's scanner (`scripts/pre-commit-secret-guard.sh`): reads a staged diff on standard input, refuses (exit 1) any line holding a Mímir password, email or billing address line, or a maFile secret or session token; prints only the kind and line, never the value; run inside this container (it holds the key) |
| `jsonio.py` | Crash-safe atomic JSON read/write (`.tmp-*.json` then rename) |
| `validation.py` | Request-body validation for writes; `normalize_datetime` (the one reader of typed dates), `date_display` / `date_editable` (the forms Draupnir sends) |
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
| Gjallarhorn resell reminders (trade lock ended) | Shared bot | `gjallarhorn_chat_id`, else `telegram_chat_id` |
| Team Fortress 2 releases and auto-stop | Shared bot | `team_fortress_chat_id`, else `telegram_chat_id` |
| CS2 Store Arbitrage: profitable in-game store items (daily after the morning refresh, hourly for newly profitable ones) | Shared bot, else `notify_webhook_url` | `telegram_chat_id` (switch `store_arbitrage_alerts_enabled`) |
| CS2 Store Boosts: each new level of a price boost (watch, boost, second wave, fading) | Shared bot, else `notify_webhook_url` | `telegram_chat_id` (switch `store_boost_alerts_enabled`) |
| **Andvari** deal alerts and card-sale summaries | **Only its own bot** (`card_deals_bot_token`) | `card_deals_chat_id` — silent without them |
| Phone rings (Gjallarhorn news, Team Fortress 2 release, a CS2 Store Boosts boost, manual ring) | `TelegramCaller` burner user account | `telegram_caller.json` target |

Buy Storage Units, the Store Catalogue's own tab, Buy games, ASF and Harvest send nothing. **Never route Andvari
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
`card_sales.json`, `store_purchases.json`, `storage_shop.json`, `store_catalogue.json`, `store_arbitrage_history.json.gz`, `store_arbitrage_alerts.json`,
`store_boosts.json.gz`, `store_boost_alerts.json`, `market_access.json`,
`storage_shop_approval_page.html`, `morning_routine.json`. Cache files are not
watched: `docker restart steam-odin-heimdall-backend-1` after editing one by hand.

## Settings and environment

Every setting with its default is in `settings.py` (`DEFAULT_SETTINGS`); the
Settings route accepts known keys only and type-casts them. Notes:
`csfloat_api_key` is a dead setting (keys come from `csfloat_keys.json`).

Environment: `PORT` (5000), `RATATOSKR_URL` (compose:
`http://ratatoskr:3000`; code fallback `:3030`), `ASF_URL`, `ASF_IPC_PASSWORD`
(ASF off when empty), `HEIMDALL_SECRET_KEY`, `HEIMDALL_ALLOWED_HOSTS`,
`HEIMDALL_LOG_LEVEL`, proxy variables.

## Running & testing

The backend **does not reload by itself**: after editing a `.py` file run
`make reload` (restarts this container only; every background loop starts again
with it). Until then the old code runs. Test long runs with outward effects
(Steam calls, Telegram alerts) in a sandbox script with its own cache file and a
stubbed notifier. Never `make reload` while the test suite runs inside the
container: the restart kills it (exit code 137); run the suite again afterwards.

```bash
# tests inside the running container (pytest installed ephemerally)
docker exec steam-odin-heimdall-backend-1 sh -c \
  'pip install -q pytest 2>/dev/null; cd /app && python -m pytest -q'

# lint (matches CI)
docker exec steam-odin-heimdall-backend-1 sh -c 'cd /app && ruff check .'
```

About 1,090 tests in 49 files (`tests/`); `conftest.py` stubs CSFloat and refuses
every pulse request (fake `_post_tradeon` on the service), so no test reaches the
network. Every feature that spends money or sells has pure helpers
with tests — extend them, including with bytes captured live.
Dev dependencies: `requirements-dev.txt` (pytest, ruff, pip-audit). The image
runs Python 3.9; CI runs 3.11 — write code that works on both.

## Backend-specific traps

- **Money paths are guarded, never bypassed.** Buy games
  (`store_purchase_service.py`), Buy Storage Units (`storage_shop_service.py`) and the
  Market unlocker (`market_access_service.py`)
  re-check currency, price and balance to the minor unit, and support a dry
  run that cancels before paying. Test with dry run; spend only on Ivan's
  explicit word, through the guarded routes.
- **Sell fees live in ONE place.** The market registry in `huginn_service.py` holds
  the defaults (ours where confirmed, else pulse's own fee from
  `GET /api/commission-settings`), and the Fees editor on the Arbitrage page (settings
  `huginn_market_fees`) overrides them. Every profit calculation reads them through
  `HuginnService.market_fee(market_id)`. That covers the Arbitrage profiles, Case
  Arbitrage, Cross-Profile, Harvest, CS2 Store Arbitrage, LOOT.Farm and the auctions. Never add a
  per-feature fee constant.
- **Every pulse request goes through the master feed (`pulse_feed.py`).** Never call
  `_post_tradeon` or build a pulse body anywhere else: ask `HuginnService._table(token,
  market, price_type, max_age)` (or `_pull_market` for pulse-shaped rows). Read
  [docs/internals/pulse-api.md](../../../docs/internals/pulse-api.md) first.
- **The pulse market registry (`_MARKET_REGISTRY` in `huginn_service.py`) has
  rules beyond id + fee.** Every feature reads markets through the feed
  (`_table` / `_pull_market` / `market_buy_index` / `market_autobuy_index`), so a
  rule placed there applies everywhere:
  - `buy_type` None = sell-only (buy orders, no listings): refused as a buy source
    or min target before pulse is asked.
  - No pulse request has a price floor (`pulse_feed.BASE_BODY`, the one body; Ivan
    removed the old $0.13 floor 2026-10-12). Every table shows items Tradeon has sold
    out of (on 2026-10-15 that changed no table's item count), so there is one table
    per (market, price type).
  - `_MARKETS_NOT_IN_PULSE_UI` (SkinSwapTrade, GgSwap, GamerPay): tables answer
    but the pulse website hides them; prices can be months old. UI shows an ⓘ.
  - `_MARKET_PRICE_SCALE`: prices in a bonus balance are turned into real dollars
    once, when the feed stores the table. SkinSwap says "Trade = Market × 1.4"; pulse's
    SkinSwapTrade `Buy` is what its Trade page pays, in Trade dollars, so it is
    divided by 1.4 (checked on the live Trade page 2026-10-10: Redline $35.35 →
    $25.25 ≈ Buff). SkinSwapMarket `Sell` is already real dollars. The ×1.4 rule
    holds for deposits only: money from selling a skin on the Trade page stayed on
    Trade (live test 2026-10-10, probably escrow under Steam trade protection). So
    each scaled row keeps the page's own price in `pagePrice` (Trade dollars) and
    `_MARKET_PAGE_LABEL` names the page; the UI shows `pagePrice` first. Profit
    always uses `price` (real dollars). `_SKINSWAP_TRADE_BONUS` is the one 1.4: the
    registry answers it as `pagePriceBonus` (and `skinswapBonus` for both SkinSwap
    markets), each profile as `fromPageBonus` / `toPageBonus` / `skinswapBonus`.
  - `buy_type` `_ESTIMATED_SELL` (SkinSwapTrade only): pulse has no Trade-page
    asking prices, so `_estimated_listings` makes them = pay price ×
    `_SKINSWAP_TRADE_ASK_MARKUP` (measured on 11 skins; the $1–$5 band rests on one
    point), only where pay ≥ `_ESTIMATE_MIN_PAY` and ≥ 0.6 × SkinSwap (Market)'s
    listing. Rows carry `estimated: True` and `pagePrice` (the estimate in Trade
    dollars; asks differ per copy, a Redline asked 41.87 then 38.52); the pseudo price type must never reach
    pulse (`fetch_generated_pair` and `fetch_generated_csfloat_autobuy` route it);
    `market_buy_index` returns `{}` for it so Cross-Profile and Store Catalogue
    Arbitrage stay real-data only. More Trade-page prices → update the markup points.
  - `_MARKET_RELIABILITY`: our own check of a market's pulse prices per side (`listings`,
    `buyOrders`): `unreliable` or `believable` with the reason (2026-10-19: Skinport
    listings and WhiteMarket buy orders unreliable; Buff163, Market.CSGO, DMarket,
    CSMoney (Market) buy orders believable; Buff.market, CSFloat, Steam both sides
    believable on Ivan's word). A mark only: never filter on it. Shown as
    `reliability` (registry, Harvest options) and `ReliabilityMark.jsx` in the UI.
    Haloskins, ShadowPay, C5Game and Youpine have no buy orders in pulse: a pair asked
    for in autobuy mode against them is priced on their lowest listing.
  - `market_registry()` exposes these to the UI as `hasListings`, `notInPulseUi`,
    `priceNote`, `listingsEstimated`, `pagePriceLabel`, plus fee `feeSource` / `feeEdited`.
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
- **Steam `pricehistory`** answers in the session's wallet currency, needs a login, and knows
  only market names (some store items differ): read
  [steam-pricehistory.md](../../../docs/internals/steam-pricehistory.md) before adding a reader.
- **Do not parallelise or speed up Steam calls** — 429 rate limits. The
  scheduler sleeps between accounts on purpose; steamcommunity.com calls share
  `community_pacer`.
- **Any new Ratatoskr login path goes through `RatatoskrService.login`**, so the
  `before_login` hook pauses ASF (one "playing" session per account).
- **Blueprints import services from `ctx` at request time**, so import order is
  not a concern, but a service being `None` means `app.py` did not wire it.
