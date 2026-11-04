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
  done; step 2 fixes C1 to C6 and D1 to D5 below.
- C1/C2 CS2 Store Arbitrage: page picks sell offer and profit; board, count and Telegram use three different rules -> one backend function with profile + cash_only; summary.profitable.
- C3 near band, C4 settings unchecked -> store-arbitrage/settings route, C5 job follow, C6 owned.
- D1-D5 CS2 Store Boosts: recommend_buy flag, thresholds from data.thresholds (page copies disagree), moving flags, opening_beats_store, tradable_if_bought_now.

### Accounts, Draupnir and Mímir
- 3 Add Account: 4-step flow + rollback in page -> one import command (authenticate before writing). Bugs: re-import overwrites a working maFile before authentication; rollback DELETE unchecked; password trimmed.
- 4 Draupnir transaction: NOT a bug (Ivan, 2026-11-04): a case-different name staying its own holding is fine, he picks names from the hints and can price any item; never raise it again. Still open: required fields only in page; unknown_item confirm should be a code from POST /transactions.
- 5 Draupnir CSV import: answers ignored, 400 and skipped rows silently lost -> import plan/execute.
- 6 Confirmations: page picks nonce/key; type 12 unknown -> backend looks up the key, sends type_name.
- 7 Remove All accounts: phrase only in page, DELETE removes accounts added later -> plan/execute.
- 8 Draupnir return_pct, holdings visibility differs between views, oversold flag never shown, transaction total.
- 9 live price status: page gives up after 8 polls; inactive/error unhandled in Portfolios, CombinedLedger, ArbitrageDeals.
- 11 Confirmation settings: interval 1-9 silently raised to 10; check-all synchronous, 409 shown as HTTP 409; wording.
- 12 copied date normaliser and valuation market list.
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
