# Phase 2 — the backend decides, the page displays

Status page for the Phase 2 architecture work (started 2026-11-01). Save point
before it: the local tag `checkpoint/before-phase-2` (never pushed).

## The seven bullets

| # | Bullet | State |
|---|--------|-------|
| 1 | Security fixes: dry run fails closed, Andvari plan time required, Mímir passwords only over POST; errors keep their real status | **Done**, pushed dc8f926 |
| 2 | No auto-reloader: one process, `make reload`, loops start once from `python app.py`, clean stop on SIGTERM | **Done**, pushed 76ee0eb |
| 3 | Health checks for every container | **Done**, pushed 31dc36e |
| 4 | Move the remaining page logic to the backend, tool by tool | **In progress** — see below |
| 5 | One shape for plans, jobs and errors (`{error, code}` everywhere, one shared plan registry, one `CommandError` handler) | **Done**, last push 16483d5 — see the end |
| 6 | Endpoint list + System health page | Not started |
| 7 | `odin` command-line tool | Not started — decided: Python with only built-in libraries, talks to localhost:5001, installed on PATH; dry runs only for purchases; first version has four groups: Accounts (code, confirmations, accounts), Ratatoskr (storage, store, autostore), Prices and portfolio (price, arbitrage, harvest, portfolio), System (status, logs, job, routes) |

Working agreement for each bullet: build, review (rule 25), double-check live,
then commit + push with a plain message.

## Decisions (Ivan, 2026-11-01)

- A missing `dry_run` means a dry run; only an explicit `false` buys.
- **A real purchase only after a dry run of the same plan passed**, and it buys
  only what passed (rule 15). Applies to Buy Storage Units, Andvari Buy games
  and the Mímir Market unlocker.
- Bullet 4 goes tool by tool; plain sorting and view filters stay in the page.

## Bullet 4 — the three money flows (on master since 2026-11-04)

Three money flows were rebuilt as plan → dry run → execute once → a job. All
backend tests passed with fakes (no purchase and no Steam call was made), lint
and build pass. None of it has been tried in the real app yet.

1. **Mímir Market unlocker** (on master since 2026-11-04) — `POST /api/mimir/market-access/unlock/plan`
   (the plan is the dry run), `GET …/plans/<id>`, `POST …/plans/<id>/execute
   {dry_run: false}`; the old `/unlock` route is gone. **Review findings
   fixed 2026-11-04:** an older plan for the same account becomes `replaced`;
   a bad saved `allowed_at` no longer breaks the status; a failed finish marks
   the plan `failed`; cleanup keeps plans whose job still runs; the page reads
   the plan again every 30 seconds and at expiry; a job that cannot start
   unlocks the service; "ready" only while the plan can be bought;
   `PLAN_LIFETIME_SECONDS` (renamed in all three files). Left for bullet 5:
   the `_command` helper copied in three route files, `self._job` mirroring
   the registry job.
2. **Buy Storage Units / Store Catalogue Buy** — `POST
   /api/ratatoskr/storage-shop/plan {item, selection}`, `GET …/plans/<id>`,
   `POST …/plans/<id>/execute {dry_run}` (dry run repeatable, real only after
   it passed, once), `POST …/quote`; `/run` removed; catalogue items carry
   `buyable`. **The 8 earlier findings are fixed; reviewed again 2026-11-04**,
   and fixed: an unread price sheet no longer marks accounts "currency not
   sold"; purchase-log entries change under the lock; replaced plans survive a
   restart; the status never answers 500 for a plan replaced meanwhile.
   Decided (Ivan, 2026-11-04): the plan lives 30 minutes from its FIRST passed
   dry run (a later one does not extend it); after a purchase only the units it
   delivered are named, never while Transfer or Auto-Store moves items on the
   account. Left for bullet 5: `quote` repeats the totals of `order_totals`;
   the status sends whole purchase-log entries.
3. **Andvari Buy games** — `POST /api/huginn/card-deals/buy/purchase-plan`,
   `GET …/purchase-plans/<id>`, `POST …/purchase-plans/<id>/execute`;
   `/buy/run` removed; the app-id parser bug is fixed (a Steam link added extra
   wrong numbers such as 2077). **Reviewed and fixed 2026-11-04:** when
   "Empty the cart first" is ticked, the dry run empties the cart too and logs
   what it removed (Ivan's decision, rule 17); a dry run that stopped with an
   error is failed (nothing of it buyable); the page sends the purchase plan's
   own cart choice; one bad purchase plan never breaks the tab's status. Left
   for bullet 5: the page copies `parse_app_ids` (SellingPanel); a real buy
   refuses the whole purchase plan when one account was bought meanwhile (safe,
   by choice); the shop's automatic naming does not wait for a Rename Storage
   Unit run on the same account.

**Next:** `make reload`, then one real "Test without paying" per tool before any
real purchase (Ivan's hands-on test list is in the session reminders).

## Bullet 4 — the rest of the backlog (from three page audits)

Split into three groups (Ivan, 2026-11-04), done one by one: Ratatoskr, then
Accounts, Draupnir and Mímir, then Huginn and Andvari.

### Ratatoskr
- B1, B2, B3 **DONE 2026-11-04** (`auto_store.py`, rules 26 and 27): each
  account has its own watchlist (Ivan: the other accounts do not need to know
  what one account buys); the old shared list was copied to each switched-on
  account once; a master view ("All accounts") combines and edits every
  watchlist; every change is one command for one item or one account
  (`auto-store/enabled`, `/account`, `/items/add`, `/items/remove`), so nothing
  is lost; "Sweep now" sweeps one account (`sweep {steamid}`) and the page
  follows its job; suggestions come from `GET auto-store/suggestions/<steamid>`
  (the account's inventory, the backend's storage rules); the hard-coded case
  list is gone. Rule 20: Auto-Store only, Transfer already works on one account.
- C and D (CS2 Store Arbitrage, CS2 Store Boosts): moved to Huginn (Ivan, 2026-11-04) as two
  tabs of the Arbitrage page (`/huginn?view=store-arbitrage`, `?view=store-boosts`;
  the old Store Catalogue addresses redirect), routes `/api/huginn/store-arbitrage…`
  and `/api/huginn/store-boosts…`. Step 1, the move with no change in behaviour, is
  done; step 2 (C1 to C6, D1 to D5) **DONE 2026-11-04**:
- C1/C2: one rule, `sell_offer(row, market, side, payout)` in `store_arbitrage_service.py`, for the
  board, its count and Telegram (Telegram asks "Best market", either side, money or Steam wallet);
  the profiles come from the backend (`options.profiles`, `profiles_of`); the board takes
  `?profile=…&cash_only=…`, judges every row (`sell`, `sell_profit`, `sell_pct`) and counts
  `summary.profitable`; the page sends no market list any more.
- C3 `indicator.near` (`NEAR_SIGNAL_RATIO`), C4 `POST /api/huginn/store-arbitrage/settings`
  (`settings_changes`: signal threshold above 0 and at most `SIGNAL_PCT_MAX`, alert switch a
  boolean; `{error, code}`), C5 "Read store prices" follows its job by id (`/api/jobs/<id>`),
  C6 each row carries `owned` from the last "Get all items" (`inventory_scanned`).
- D1 `recommend_buy` (`BUY_LEVELS`), D2 `level_hints` written from the detector's thresholds and
  `thresholds.strong_ratio_factor` for the colours, D3 `active` / `moving` / `contents.moving` /
  `contents.level_active`, D4 the capsule colour reads `opening_beats_store`, D5
  `tradable_if_bought_now` on the board.

### Accounts, Draupnir and Mímir
- 3 Add Account **DONE 2026-11-04**: one command `POST /api/accounts/import {file_name, file_text, password}`
  (`steam_service.import_account`): reads the maFile (SteamID64 from the file name, else from the raw
  text), logs in to Steam with the file's own Steam Guard secret, checks Steam's SteamID is the file's,
  and only then writes; `/api/accounts/authenticate` and the page's rollback are gone; the password is
  never trimmed; refusals are `{error, code}` (`invalid_file`, `invalid_steamid`, `no_account_name`,
  `no_shared_secret`, `password_required`, `authentication_failed`, `steam_unreachable` 502,
  `steamid_mismatch`, `save_failed` 500). A maFile without an account name is now refused.
- 4 Draupnir transaction **DONE 2026-11-04**: `POST …/transactions` requires item name, quantity, price,
  platform and date (`REQUIRED_TRANSACTION_FIELDS`, `validate_new_transaction`; the CSV import keeps its
  defaults) and answers `invalid_transaction` (400); a name not in the item list answers `unknown_item`
  (409, with suggestions) unless the request says `confirm_unknown_item: true`; `PATCH` checks the same
  (a blank platform is refused too); one rule `_item_check` serves this and `validate-item`; the page
  has no checks of its own any more. A case-different name staying its own holding is NOT a bug (Ivan).
- 5 Draupnir CSV import **DONE 2026-11-04**: `POST /api/draupnir/portfolios/import/plan {csv, name | pid}` checks
  the file and writes nothing (rows to import, each skipped line and why; `csv_required`, `no_valid_rows`,
  `portfolio_not_found` 404); `POST …/import/plans/<id>/execute` writes it once (`already_executed` 409,
  `unknown_plan` 404, `IMPORT_PLAN_LIFETIME_SECONDS`); the old `/import` route is gone. The page shows every
  file's plan (skipped lines included) before Import and reports a file that failed.
- 6 Confirmations **DONE 2026-11-04**: every listed confirmation carries `key` and
  `type_name` (`describe_confirmation`: Steam's own `type_name`, else `CONFIRMATION_TYPE_NAMES`, else
  "Unknown type N"); `POST /api/accounts/<steamid>/confirmations/<cid> {op}` finds the key itself
  (`respond_to_confirmation`: the last list, else one fresh read; `confirmation_not_found` 404,
  `invalid_operation`, `confirmations_unavailable` 502, `steam_refused` 502); the scheduler reads `key`
  too; the page sends only the id and the operation.
- 7 Remove All accounts **DONE 2026-11-04**: `POST /api/accounts/remove-all/plan` names the accounts and the
  phrase (`DELETE N`) and writes nothing (`no_accounts`); `POST /api/accounts/remove-all/plans/<id>/execute {phrase}`
  soft-deletes only the plan's accounts, once (`phrase_mismatch`, `unknown_plan` 404, `already_executed` 409,
  `REMOVE_ALL_PLAN_LIFETIME_SECONDS`); an account added after the plan stays; `DELETE /api/accounts` is gone.
- 8 Draupnir numbers **DONE 2026-11-04**: one `_totals` serves every account, the list and All accounts, and
  adds `return_pct` (total P/L ÷ invested × 100, None when nothing was invested) and `oversold_count`; each
  holding carries `listed` (`_is_listed`: held, a realized profit or loss, or oversold; one rule for both
  views, an oversold row is never hidden); each transaction carries `total` (`_with_totals`); both tables
  show an "oversold N" mark (`components/draupnir/OversoldMark.jsx`); the pages compute none of these.
- 9 live price status **DONE 2026-11-05**: every Draupnir answer (list, one account, All accounts, Arbitrage)
  carries `pricing_status` (`draupnir_service.py`): `pricing`, `priced`, `pricing_note` (the words, None when
  fresh), `pricing_warning`, and `poll_after_seconds` (`PRICE_POLL_SECONDS`, then `PRICE_SLOW_POLL_SECONDS`
  once loading took `PRICE_SLOW_AFTER_SECONDS`; None when asking again changes nothing). Inactive, error
  (with or without the last prices, and when Heimdall tries again) and no token are all named. The pages
  poll while the backend says so (no 8-poll limit) and show one `components/draupnir/PricingNote.jsx`;
  CombinedLedger's own copy of the line is gone.
- 11 DONE Confirmation settings: `backend/confirmation_settings.py` holds the interval limits (10 to 3600, step 5),
  checks the four keys (out of range refused with `interval_out_of_range`, also on `POST /api/settings`) and writes the
  note on what the loop does; `GET/POST /api/confirmations/settings`. Check All Now is a job
  (`ConfirmationScheduler.start_check_all`, the lock taken before the thread; 409 `already_running`; result
  `Checked N of M accounts`). The window reads limits and note from the backend.
- 12 DONE copied date normaliser and valuation market list: `HuginnService.valuation_markets()` (labels in
  `_PRICE_MARKET_LABELS`, Lowest's members in `_LOWEST_OF`, `DEFAULT_PRICE_MARKET`) served at
  `GET /api/huginn/valuation-markets`; Draupnir and Gjallarhorn pickers read it (`utils/useValuationMarkets.js`; a page
  sends no market until one is picked, the routes fall back to the default). Dates: only `validation.normalize_datetime`
  reads typed dates; `POST /api/draupnir/dates/normalize` (code `bad_date`) rewrites the field on blur; each
  transaction carries `date_display` and `date_editable`; `DateField` reads only the year-first form.
- 13 DONE other bugs: delete transaction, rename, create and delete portfolio read the answer (`readJsonOrThrow`), show "Not deleted: …" and reload only on success; deleting a portfolio leaves the page only when it worked. "Drip last" keeps `is_arbitrage`. Item search shows only the newest answer (`suggestSeq`). Dashboard ignores only `isTransientFailure` (`utils/readJson.js`: the browser could not reach the backend, Safari's "Load failed" included, or the answer was empty or not JSON).

### Huginn and Andvari
- 1 MONEY Andvari Buy games: freshness by browser clock (BuyPanel 59-60, hardcoded 30 min), default selection, totals summed in page, single /buy/run with selection (no plan id), only window.confirm, no ref double-click guard -> purchase-plan + plans/<id>/execute job. OPEN QUESTION to Ivan: require a passed dry run of the same plan before a real buy?
- 2 Auto-sell settings (SellingPanel 38-83, TeamFortress 93-116) via raw /api/settings, app ids parsed in page, "also cards held before" saves on tick w/o confirm (rule 16) -> selling/config + team-fortress/config with clean_config; include-held as separate confirmed command.
- 3 card selling return computed in page from two endpoints, own currency map, "buy order" text search -> return_on_purchases + listed code.
- 2 + 3 (A1) **DONE 2026-11-05** (card selling only; Team Fortress 2 is parked): `POST /api/huginn/card-deals/selling/config`
  and `…/selling/include-held` (asks first, `confirm_needed`); the page no longer copies `parse_app_ids`,
  has no currency map (ISO codes from `CURRENCY_CODES`) and reads `returns` and each sale's `result`.
  Next: A2 = 1, 10, 11, DealsTable, CardDeals stale answers, startScan (the app-id bug was fixed in bullet 4).
Split into Huginn first (H1 to H6), then Andvari (Ivan, 2026-11-05). H1 = items 4 and 5, H2 = 7 and
the CaseArbitrage bug, H3 = 8, H4 = 9 and the CrossProfile bugs, H5 = 12 and 13, H6 = 14 (Gjallarhorn,
News watcher, Case alerts, Readiness); Andvari = 1, 2, 3, 10, 11, DealsTable and the app-id bug.
- 4 + 5 (H1) **DONE 2026-11-05**: `GET /api/huginn/profiles` (`pulse_views.arbitrage_profiles`: the
  curated `CURATED_PROFILES`, then every generated pair; each with `key`, `mode`, `fetchEndpoint`,
  `checkLive` and `checkLiveRefusal`); the page and `tests/test_active_profiles.py` read it (both
  copies gone). `check-live` refuses an uncheckable pair (LOOT.Farm's feed, CSFloat's swept buy
  orders, an estimate) with `{error, code: 'not_checkable'}`. `/api/huginn/active` answers each
  profile's `ready` and `warming` and each table's `downloaded_at`; `X-Pulse-Tables` adds each
  table's download time on the same clock, so "Newer prices ready" compares server times only.
- 6 FIXED 2026-11-04: Gjallarhorn Target basket sends one `targets/add` or `targets/remove` per item (the whole-list POST is gone); GET always answers the names, with `pricing_error` when prices fail; only the newest answer is applied.
- 7 + the CaseArbitrage stale-answer bug (H2) **DONE 2026-11-05**: `/api/huginn/cases` answers `markets` as
  objects (`container_markets()`: key, label, short, slug, sellable, balance) from the one table
  `_CONTAINER_MARKET_DISPLAY` (`_CONTAINER_MARKETS` is derived from it); the page's `MARKETS` copy is gone,
  and a request counter drops any answer that is not the newest.
- 8 Harvest (H3) **DONE 2026-11-05**: each `/api/huginn/harvest` row answers `profitable` (sells above cost,
  the "Profitable only" filter), `counted` (in the totals: profitable and the offer neither suspicious nor
  stale) and `short_of_offers` (more copies held than offers at the best price); the board answers
  `trade_hold_days` and `stale_after_seconds`, and `summary.profitable_not_counted` explains why "Profitable
  only" can show more lots than the count. The page uses `best.stale`, never its own clock rule.
- 9 + the CrossProfile bugs (H4) **DONE 2026-11-05**: each chain row answers `buy_price` (its first
  market) and `sell_net` (its last market), null when that end is not priced; the page's own leg
  picking is gone. The save answers `{error, code: 'save_failed'}` when settings are not written and
  the page shows it. The scan answers `poll_after_seconds` only while a warm runs; a crashed warm is
  `status: failed` with `error` and is not retried for `_RETRY_AFTER_FAILURE`, so polling stops.
- 10 CardDeals All counts, scan_running inferred.
- 11 FarmingPanel actions per state -> actions list.
- 1 + 10 + 11 + DealsTable + the CardDeals bugs (A2) **DONE 2026-11-05**: `summary.scopes.all` from the backend
  (the page's sum is gone); a scan or buy-order check while one runs answers 409 `already_running` with
  the running job's name and the page shows it (the guess from `job.kind` is gone); each farming account
  answers `actions` (`asf_service.STATE_ACTIONS`) and the page draws only those, `max_running` read, not
  20; each deal row answers `few_listings` (`FEW_LISTINGS`); Buy games reads the plan life from
  `plan_time_to_live_seconds`; a request counter drops old deals answers. Andvari done; next the
  memory work (reminder 11).
- Memory work, step 1 **DONE 2026-11-05**: `packed_rows.py` keeps the pulse tables' rows, the weekly
  sales (one `PackedRows` per market) and today's price history rows in typed arrays; reads give back
  the same tuples, so no reader changed. Sandbox with real data, 16 profiles, one round: 365 -> 242 MB;
  Python objects: tables 112 -> 56 MB, weekly sales 40 -> 29, today's history 57 -> 40 (the rest is
  the item names). `MEGABYTES_PER_TABLE_IN_MEMORY` 15 -> 7. Still open: the ~15 MB profile answers
  (built as one JSON per request), then Phase 2 bullets 5, 6, 7.
- Memory work **CLOSED 2026-11-05** (Ivan chose to stop here): a profile answer (Steam, 31,751 rows,
  22.9 MB of JSON) does not leak: the backend stays at about 320-330 MB after repeated answers (240
  idle). About 8 MB of each answer is picture addresses; dropping them belongs with the local item
  picture library.
- 12 + 13 (H5) **DONE 2026-11-05**: `/api/huginn/active` answers `estimated_megabytes_per_day`
  (and per `preview`), counting only the active hours (`active_minutes_a_day`) plus the daily
  reference; the feed's `status()` answers `downloaded_megabytes` and `megabytes_in_memory` from
  `MEGABYTES_PER_DOWNLOAD` / `MEGABYTES_PER_TABLE_IN_MEMORY` (`utils/pulseTraffic.js` and the page's
  15 MB are gone). The 1.4 lives only in `_SKINSWAP_TRADE_BONUS`: profiles answer `fromPageBonus`,
  `toPageBonus` and `skinswapBonus` (the page's SkinSwap market list is gone too).
- 14 (H6) **DONE 2026-11-05** (DealsTable moved to the Andvari group): MarketHoldEditor sends its rows
  as typed, `clean_holds` is the only cleaner, a failed save answers `save_failed`, and Save stays off
  when the saved list could not be read. The Gjallarhorn price market copy was already gone (item 12,
  `useValuationMarkets`). The news watcher saves through `POST /api/huginn/gjallarhorn/news/settings`
  (chat id checked: a number or a public @name) and shows every failure in red. Case Arbitrage alerts
  read `GET /api/huginn/cases/alerts` (secrets only as `telegram_bot_token_set` / `webhook_set`, plus
  `buy_markets`, `poll_interval_sec`, `poll_interval_minimum_sec`) and save through
  `POST /api/huginn/cases/alerts/settings`; the page never reads `/api/settings`. Readiness answers its
  `steamid` and `looseFree`; the page drops an answer for another account.
- Team Fortress 2: left alone as a whole feature (Ivan, 2026-11-04: not used for now); do not work on it.
- BUGS: CardDeals no stale-answer guard (CaseArbitrage's fixed in H2); startScan unchecked; app-id regex grabs every 2-9 digit run (page + backend parse_app_ids, Buy games uses it); ReadinessPanel stale numbers (FIXED in H6).

## Bullet 5 — one shape for plans, jobs and errors

- 5a **DONE 2026-11-05**: one `CommandError` in `commands.py` (was in `storage_deposit.py`, plus a second
  copy in `gjallarhorn_reminders.py`); `error_handlers.install` answers it as `{error, code}` with its
  status for every route, so the three `_command` helpers (Mímir, Draupnir, Ratatoskr) and Huginn's
  `_purchase_command` are gone: a route returns its answer only.
- 5b **DONE 2026-11-05**: the four other refusal classes with a code (`SettingsError`,
  `AlertSettingsError`, `NewsSettingsError`, `SellingConfigError`) are `CommandError` now, with the same
  codes and statuses (`confirm_needed` 409, `save_failed` 500, the rest 400); their routes no longer
  catch them (`_selling_command` is gone).
- 5c step 1 **DONE 2026-11-05** (Huginn routes): the 27 copies of the Tradeon token check are one
  `_tradeon_token(settings)` that refuses with code `tradeon_token_missing`; the 38 `except Exception`
  blocks answering `str(e)` with 500 are gone, so a crash reaches the app-wide handler (logged, code
  `unexpected_error`).
- 5c step 2 **DONE 2026-11-05**: every error answer in `routes/` carries a `code` (64 added in place,
  same text, status and extra keys such as `inactive`, `vault_unreadable`, `saved`, `details`):
  `missing_body`, `missing_field`, `bad_request`, `bad_value`, `bad_name`, `account_not_found`,
  `portfolio_not_found`, `credential_not_found`, `market_unreachable`, `save_failed` and others;
  `start_csfloat_sweep` returns a fourth value, the code; the confirmations list's own crash catch is gone.
- 5d step 1 **DONE 2026-11-05**: one shared plan store, `plans.py` (`PlanStore`: id, `created_at`,
  `expires_at`, cleanup by `forget`), used by the four plans that spend nothing: Transfer and Auto-Store
  moves (`storage_deposit.py`, cleanup keeps a plan that ran for an hour), Rename Storage Unit, Remove All
  accounts and the Draupnir CSV import. Same lifetimes, checks and codes; ids are now 32 hex characters
  everywhere.
- 5d step 2 **DONE 2026-11-05** (reviewed: money checks untouched): the Market unlocker and Buy games
  keep their plans in `PlanStore` too (lifetime None: the unlocker's plan expires when its dry run ends, a
  purchase plan with the plan it was made from; cleanup never drops a plan whose job runs). Buy Storage
  Units stays as it is: one current plan saved to disk with the ids of replaced ones, not a store of many.
- 5d step 3 **DONE 2026-11-05** (reviewed: the busy check before a purchase unchanged): `jobs.py`
  `JobSlot` replaces the `self._job` copy and lock that Buy Storage Units, Andvari Buy games and the Market
  unlocker each kept beside the registry job: one busy lock, one view for `status()` (same fields as
  before), the fields each copies to the registry. The next job may start as soon as the registry job
  reads finished.
- 5e **DONE 2026-11-05** (the leftovers named under bullet 4): Buy Storage Units' `quote` and
  `order_totals` add up the currencies with one helper (`totals_by_currency`); its status sends the
  page's view of each purchase (`purchase_outcome`), not the whole saved entry. The Andvari page's
  copy of `parse_app_ids` was already gone (A1, 2c69626: the backend reads the ids). Bullet 5 is
  done. Next: bullet 6.
