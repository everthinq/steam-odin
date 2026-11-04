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
| 5 | One shape for plans, jobs and errors (`{error, code}` everywhere, one shared plan registry, one `CommandError` handler) | Not started |
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
- Other bugs: no success checks (delete transaction, rename, delete portfolio navigates away on failure, create/delete portfolio); "Drip last" drops is_arbitrage; old search answers win; Dashboard transient error filter misses readJson/Safari messages (use failureMessage).

### Huginn and Andvari
- 1 MONEY Andvari Buy games: freshness by browser clock (BuyPanel 59-60, hardcoded 30 min), default selection, totals summed in page, single /buy/run with selection (no plan id), only window.confirm, no ref double-click guard -> purchase-plan + plans/<id>/execute job. OPEN QUESTION to Ivan: require a passed dry run of the same plan before a real buy?
- 2 Auto-sell settings (SellingPanel 38-83, TeamFortress 93-116) via raw /api/settings, app ids parsed in page, "also cards held before" saves on tick w/o confirm (rule 16) -> selling/config + team-fortress/config with clean_config; include-held as separate confirmed command.
- 3 card selling return computed in page from two endpoints, own currency map, "buy order" text search -> return_on_purchases + listed code.
- 4 Arbitrage fixed+generated profiles in page; canCheckLive is the only gate (backend check_live accepts ineligible pairs) -> GET /api/huginn/profiles; check-live refuses with code.
- 5 active-profile ready/warming/newer computed in page -> /active returns them.
- 6 FIXED 2026-11-04: Gjallarhorn Target basket sends one `targets/add` or `targets/remove` per item (the whole-list POST is gone); GET always answers the names, with `pricing_error` when prices fail; only the newest answer is applied.
- 7 CaseArbitrage hardcoded MARKETS -> markets list from /api/huginn/cases.
- 8 Harvest: profitable-only differs from backend count; stale via frontend constant; hardcoded 8 days; offers count -> counted, short_of_offers, best.stale, stale_after_seconds.
- 9 CrossProfile chain price from first/last priced legs (wrong) -> backend buy_price, sell_net.
- 10 CardDeals All counts, scan_running inferred.
- 11 FarmingPanel actions per state -> actions list.
- 12 traffic estimates ignore active hours -> estimated_megabytes_per_day.
- 13 SkinSwap 1.4 ratio copy.
- 14 MarketHoldEditor re-clean; Gjallarhorn _PRICE_MARKETS copy; NewsWatcher raw settings + ignored failures; DealsTable 20 listings; CaseAlertsPanel reads whole settings incl. Telegram token.
- Team Fortress 2: left alone as a whole feature (Ivan, 2026-11-04: not used for now); do not work on it.
- BUGS: CaseArbitrage + CardDeals no stale-answer guard; startScan unchecked; app-id regex grabs every 2-9 digit run (page + backend parse_app_ids, Buy games uses it); CrossProfile save fails silently, warm polling uncapped; ReadinessPanel stale numbers.
