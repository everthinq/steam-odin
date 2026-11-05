# CLAUDE.md — Heimdall frontend (React + Vite)

Agent notes for the single-page app. The root [CLAUDE.md](../../../CLAUDE.md)
has the fleet-wide rules; this file is the frontend map, its conventions and
its traps. What each screen does for the user is in
[docs/guide/](../../../docs/guide/).

## Stack

- React 19, react-router-dom 7, Vite 7, Tailwind CSS 4, `lucide-react` icons.
  Nothing else at runtime: no state library, no data-fetching library, no chart
  library, no TypeScript.
- The container runs the **Vite dev server** (`npm run dev -- --host`, hot
  reload, `StrictMode` → effects run twice in development) on port 5173,
  published at **http://localhost:3000** (127.0.0.1 only).
- Every API call is a **relative `/api/...` path**; `vite.config.js` proxies
  `/api` to `http://heimdall-backend:5000` (the Docker service name — `npm run
  dev` on the host needs the target changed). No environment variables, no base
  URL constant.
- The frontend never calls Ratatoskr, Steam or ASF itself. The one exception is
  Andvari's "ASF UI" link, which opens `:1242` in a new tab.

## Gates (there are no frontend tests)

```bash
cd Yggdrasil/heimdall/frontend
npm run lint    # must stay at 0 errors (exhaustive-deps warnings exist)
npm run build
```

To check that a change renders, open http://localhost:3000 or drive headless
Chrome: `--dump-dom --virtual-time-budget=…` for the page text, or
`node scripts/ui-screenshot.mjs <url> <out.png> ["<JavaScript>"] [height]` (repository root;
no dependencies, puppeteer-core is not installed) for a screenshot after running JavaScript in
the page, such as clicking a row open. Never click a real Pay / Buy / Sell / Approve button
while testing; use the dry-run paths.

## Routes (`src/App.jsx`)

| Path | Page | Notes |
|------|------|-------|
| `/` | `pages/Dashboard.jsx` | The only eager page; account cards + tool tiles; each card shows its Market access line and a banner lists accounts that need a purchase (the result of Mímir's check, read-only: `utils/useMarketAccess.js`) |
| `/add-account` | `pages/AddAccount.jsx` | maFile import |
| `/accounts/:steamid/confirmations` | `pages/Confirmations.jsx` | |
| `/huginn` | `pages/huginn/Arbitrage.jsx` | Views: Arbitrage (rows of at most `PROFILES_KEPT_IN_BROWSER` (2) profiles stay in the browser, the shown one and the one before; the profiles come from `GET /api/huginn/profiles`, ready/warming from `/api/huginn/active`; per-row "check live" ↻ where the profile's `checkLive` says so; "Active" per profile, `components/arbitrage/ActiveSwitch.jsx`, and the Active profiles panel, `ActiveProfilesPanel.jsx`: interval, active hours, the Buff163 reference, the price history; an active profile opens by itself from memory, `?kept=1`; "prices downloaded N minutes ago" from the `X-Pulse-Tables` header, `PricesAge.jsx`), Harvest (`components/Harvest.jsx`: "Profitable only", the stale and "only N offers" marks and the trade-hold days are the backend's row flags and numbers), Case Arbitrage (`components/CaseArbitrage.jsx`: its markets, labels and sellable flags come from `/api/huginn/cases` → `markets`; only the newest request changes the page), Cross-Profile, Price dips (`components/PriceDips.jsx`) (the other tabs say which markets the active profiles cover; the LF Auctions and LF Arbitrage tabs were removed on 2026-11-04, `components/AuctionBoard.jsx` and `components/LootfarmArbitrage.jsx` and their backend stay unused for now), **CS2 Store Arbitrage** (`components/StoreArbitrage.jsx`: laid out like Huginn's Arbitrage: profile In-game store → market (`ProfilePicker`, default CSMoney (Trade) autobuy, plus "Best market"), CSMoney (Trade) indicator column with BUY signal, Refresh, filters, Telegram switch / Send now; Buy links to the guarded buy page (read-only); the backend judges every row under the chosen profile, so the page never picks an offer or a profit) and **CS2 Store Boosts** (`components/StoreBoosts.jsx`: cards (alarm counts, Steam reader with its read spacing, hourly pulse check + Check now, alert switches + Test message / Test ring), a Tracked panel (pinned items on top, `TrackButton`, read on the normal schedule), views Moving now / Capsules / All, rows with level, Steam now vs usual, sales vs usual, a 7-day SVG chart and Confirmed; a row opens the 14-day hourly chart (`BoostChart`, hover) and a capsule's stickers with odds and opening value (read-only; Buy links to the guarded buy page); level hints, colours, "moving" and "recommend buy" are the backend's flags, `BoardContext` hands them to the table); `?view=<tab>` opens a tab when the page opens; header link to Pulse feed |
| `/huginn/pulse` | `pages/huginn/PulseFeed.jsx` | Read-only monitor of the master pulse feed: active profiles, the keeper's state (paused, behind, the Mac slept), downloads and reads since start, every table with why it is downloaded (active profile, reference, seed, Fetch live data), its age and who reads it, and the slaves with their tables in memory (`GET /api/huginn/pulse`) |
| `/huginn/gjallarhorn` | `pages/huginn/Gjallarhorn.jsx` | |
| `/huginn/card-deals` | `pages/huginn/CardDeals.jsx` | Andvari; `#buy` opens Buy games (`components/carddeals/BuyPanel.jsx`: Dry run makes a purchase plan and runs its dry run, Buy executes that plan; the backend decides what is buyable) |
| `/huginn/team-fortress` | `pages/huginn/TeamFortress.jsx` | |
| `/draupnir`, `/draupnir/:portfolioId` | `pages/draupnir/` | |
| `/mimir` | `pages/mimir/Vault.jsx` | Credential vault; **Market unlocker** dialog (`components/MarketUnlocker.jsx`: accounts the backend marks `purchase_would_help` start ticked; Dry run = `POST …/unlock/plan`, a plan followed as a job; Buy for real executes that plan, only the accounts that passed, after typing `BUY <n passed>`); **Market access** button, banner and column (`components/MarketAccess.jsx`: `MarketAccessButton`, `MarketAccessBanner`, `MarketAccessLine`; `utils/useMarketAccess.js`: `GET /api/mimir/market-access`, polled every 3 s while the check runs, `POST …/check`, `planUnlock` / `executePlan` with `followJob`; the page prints the backend's `headline`, `advice`, `flagged` order and `last_unlocks`) |
| `/buy-storage-units` | `pages/ratatoskr/StorageShop.jsx` | Buy Storage Units, every account; outside the Ratatoskr layout; `/storage-shop` redirects here; displays the backend's plan and purchase job (see "Buy Storage Units is a backend command") |
| `/store-catalogue` | `pages/ratatoskr/StoreCatalogue.jsx` | Every in-game store item with its US dollar price; a Buy button per item; header links to Huginn's CS2 Store Arbitrage and CS2 Store Boosts |
| `/store-catalogue/arbitrage`, `/store-catalogue/boosts` | redirect | To `/huginn?view=store-arbitrage` and `/huginn?view=store-boosts` (the tabs moved to Huginn on 2026-11-04) |
| `/store-catalogue/buy/:item` | `pages/ratatoskr/StorageShop.jsx` | The Buy Storage Units page for any price sheet entry (keyed by it) |
| `/ratatoskr/:steamid/rename-storage-units` | `pages/ratatoskr/RenameStorageUnits.jsx` | Rename Storage Unit tab: the backend's unit list, a Rename field per unit, "Name them Storage N" (plan → Execute plan); every rule is in `backend/storage_rename.py` |
| `/ratatoskr/:steamid/{transfer,auto-store}` | `pages/RatatoskrLayout.jsx` + `pages/ratatoskr/` | Layout passes `{steamid, account}` via `<Outlet context>`; `/ratatoskr/:steamid` and the old `…/inventory` (page removed) redirect to `transfer` |

Every page except the Dashboard is `React.lazy`; all routes sit in a
`RouteErrorBoundary` keyed by path (a stale tab after a rebuild gets a
chunk-load error with a Reload button). `TitleManager` sets the tab title by
path prefix — add your page there. No catch-all route, no Settings page.

## Conventions

- **Fetching:** plain `fetch('/api/…')` in each component. Backend errors come
  as `{error: "…"}`; background jobs answer `{started, started_at}` and report
  `{job: {running, done, total, phase, error}}`.
  Read answers with `readJson(response)` (`utils/readJson.js`), never a bare
  `response.json()`: an empty or non-JSON body (the backend reloaded mid-request)
  then becomes an error naming the request and status instead of the browser's
  "Unexpected end of JSON input". The dev proxy (`vite.config.js`) also answers a
  lost backend with a JSON 502 saying why.
  An action (delete, rename, create) reads its answer with `readJsonOrThrow`,
  shows `failureMessage(error)` and reloads only on success. A status poll that
  should stay quiet through a backend restart checks `isTransientFailure(error)`,
  never the message text.
- **Stale answers:** a numbered-request ref
  (`const n = ++ref.current; … if (n !== ref.current) return`) so only the
  newest answer is applied. Use it for anything polled or re-requested.
- **Polling:** `setInterval`/`setTimeout` inside `useEffect` with cleanup;
  faster only while a job runs or a cache is "warming", with a cap. Follow a
  job by its server `started_at`, never by comparing browser time with server
  time.
- **Settings:** `POST /api/settings` with **only the keys your screen owns** —
  posting the whole object overwrites other screens' settings.
- **Dangerous actions** get a real confirmation: `TypedConfirmDialog` (type a
  phrase), `ConfirmDialog` / `PromptDialog` in `components/DraupnirDialog.jsx`,
  or a review step with a dry run (Buy Storage Units, Buy games). Guard double
  clicks on anything that spends money with a ref, not only state.
- **localStorage** is for per-browser conveniences only (layout, filters),
  every access wrapped in `try`/`catch`. Keys in use: `heimdall-dashboard-layout`,
  `draupnir-portfolio-layout`, `huginn.harvest`, `andvari.valuation`,
  `andvari.buy.apps`, `huginn.arbitrageFilters`, `huginn.harvestFilters`, `huginn.caseArbitrageFilters`, `huginn.crossProfileFilters`, `ratatoskr.storeArbitrage`, `ratatoskr.storeArbitrageFilters`, `ratatoskr.storeBoosts` (CS2 Store Arbitrage and CS2 Store Boosts, Huginn tabs now; the keys stayed so saved choices survive the move), `ratatoskr.showUnstorableItems`, `huginn.priceDips`, `huginn.skinswapNoteOpen`, `lf_balance_pct`, `lf_unlocked`.
- **Shared pieces:** `utils/useValuationMarkets.js` (the backend's valuation market list for Draupnir and Gjallarhorn: `markets`, `chosen(market)`, `label(market)`; never write a market list in a page); `components/gjallarhorn/InfoTip.jsx` (prop `tip`) for
  tooltips; `components/draupnir/columnSort.js` (`useColumnSort`, `sortRows`) with the header `components/ratatoskr/SortHeader.jsx`;
  `components/arbitrage/ArbitrageFilters.jsx` (price / account / collection filters + chips on Arbitrage, Harvest, Case Arbitrage, Cross-Profile and CS2 Store Arbitrage; each tab names its price sides via `priceSides`; helpers, `useStoredFilters` and `buildOptions` in `utils/arbitrageFilters.js`; the page passes its inventory scan down as `byHash`, the only source of collections);
  `utils/transferItems.js` (`matchesSearchQuery`, wear shorthand fn/mw/ft/ww/bs);
  per-market link builders in `utils/*Market.js` + `*MarketLink.jsx` (`SteamMarketLink viaPulse` opens the Steam Market through pulse's short link);
  `utils/tradeonShortLink.js`; `utils/priceAge.js` (`useNowSeconds` — one clock per page, passed down —, `isStale`, `staleTip`: a price whose market has not updated it for a day; `PriceCell` greys it with `updated` + `now`). Item images: `api.steamapis.com/image/item/730/<name>`.
- **Huginn Arbitrage profiles** are generated in `pages/huginn/Arbitrage.jsx`
  from `/api/huginn/markets` (every buy × sell pair, modes autobuy/min) and
  carry the registry flags: `fromNotInPulseUi`/`toNotInPulseUi` + `*PriceNote`
  (ⓘ via `components/arbitrage/NotInPulseNote.jsx`, prop `extra`),
  `fromEstimated`/`toEstimated` with sub "min, estimated" (amber in
  `MarketBadge`). Rows whose price side has `estimated: true` render "≈" via
  `PriceCell`'s `estimated` prop. Profiles also carry `fromPageLabel`/`toPageLabel`
  (registry `pagePriceLabel`); with a row's `pagePrice`, `PriceCell` shows the price
  as the Trade page does and "$x real" in grey under it (profit stays real dollars). `ProfilePicker.jsx` splits each group into
  "⚡ Sell instantly · buy orders" (toSub `autobuy`) and "List for sale"; while searching, groups whose buy market (Harvest: account) matches come first (`rankGroups`). Any
  SkinSwap profile shows `SkinSwapBalanceNote.jsx` (deposits: Trade = Market × 1.4;
  sale money stays on Trade; Trade → real dollars calculator; "worth it" check:
  money out ÷ Trade page price ≥ 1/1.4; estimate chip). Keep anything estimated visibly marked.
- **Styling:** Tailwind utility classes inline, dark slate palette
  (`bg-slate-950/85`, `border-white/10`, `text-slate-400`), `tabular-nums` for
  numbers, section labels `text-[10px] font-bold tracking-widest uppercase`.
  Accents: amber (Huginn, Ratatoskr), yellow (Draupnir), cyan (Mímir), emerald
  (profit/OK), red (loss/danger), sky (info). Fonts Inter + Cormorant Garamond
  (headings). Custom classes in `src/index.css` (`.glass-panel`, `.glass-card`).
- **Names:** spell things out (Ivan's rule) — "Storage Units", "Game
  Coordinator", not abbreviations, in code and in UI copy.

## Lint rules that bite (react-hooks v7, React Compiler rules as errors)

- **No `setState` directly in an effect body or during render.** Derive the
  value (`useMemo`, or compute during render), set state in event handlers or
  in async callbacks, or remount with a `key`.
- **No impure calls during render** (`Date.now()`, `Math.random()`): read the
  time in an effect/handler or from server data.
- **No reading `ref.current` during render.**
- `react/jsx-no-undef` is an error: a missing icon import blanks the whole page.
- `no-unused-vars` ignores capitalised names, so unused icon imports are not
  flagged — remove them yourself.

## Known gaps (do not copy these patterns)

- `tailwind.config.js` custom colours (`bg-odin-blue`, `text-asgard-gold`,
  `bg-bifrost-cyan`, `text-frost-white`, `bg-odin-dark`) are **not generated**:
  Tailwind 4 ignores a JS config without `@config`. Those classes render
  unstyled. Use standard palette classes.
- `custom-scrollbar` and `animate-in fade-in slide-in-*` do nothing (no plugin).
- UI text "Set tradeon_token in Settings" points nowhere: the token lives in
  `backend/settings.json`.
- Gjallarhorn's help says "Confirmations → Connect"; Connect is in the
  Ratatoskr sidebar. `NewsWatcher.jsx` passes `text=` to `InfoTip`, which reads
  `tip`, so those hints are empty.
- No confirmation before: Confirmations Approve/Deny, Transfer Move, Team
  Fortress 2 Play / Sell now, card auto-sell checkboxes (they save at once).

## Ratatoskr Transfer: tradable now vs trade hold

`groupItemsByName` (`utils/transferItems.js`) splits every name + wear line by trade status: the
copies tradable now, on a plain trade hold (`trade_unlock` in the future) and under trade
protection (`trade_protected_until` in the future, see below) are separate lines, sorted in that
order; `holdSplit` marks an item that has both (the tradable line gets the
"Tradable now" badge). A held line's `floatVariants` are its floats (skins) or, for float-less
items, one variant per unlock time (`variantKind: 'unlock'`, `trade_unlock`), sorted soonest
first. `formatTradeHoldLabel` gives the time left ("14h", "6d", range "1h–6d"),
`formatTradeUnlock` the unlock moment in the browser's time zone; a held line whose copies all
unlock at one moment carries `trade_unlock` ("Unlocks …" under the name). The queue entries carry
`tradeHold` (shown in `TransferQueueModal`) and the CSV export a range.

- **Two holds, two rules** (game data verified live 2026-10-31, Ivan's test items). A plain trade
  hold (`trade_unlock`, attribute 75 — e.g. a Steam Community Market purchase) does NOT block a
  Storage Unit. **Trade protection** (attribute 312, an item received in a trade; Ratatoskr's
  `trade_locked` + `trade_protected_until`, only while ahead) DOES. Lines split three ways
  (`tradable` / `hold` / `protected`, `isItemTradeProtected`); protected lines get a red "Trade
  protected" badge. `isItemTradeProtected` only reads the backend's `storage_block` (code
  `trade_protected`, `until`; the page clock drops a block that ended while the page is open). In To
  mode `isHeldForDeposit` (= `tradeProtected`) makes the line's and its variants' selectable quantity
  0 — select button and `GroupQtyInput` locked (`totalQty` still shows the count), Select all skips
  them, `selectTitle` says how long is left — and the backend's plan refuses them again anyway. Plain-hold lines stay selectable. Never key
  storability on `trade_unlock` alone. From mode is unaffected.

- **The page clock:** holds are judged at the `now` option of `groupItemsByName` (milliseconds);
  Transfer passes `useNowSeconds()` (`utils/priceAge.js`, ticks every minute) and lists it in the
  `useMemo` dependencies, so a hold that ends while the page is open moves its copies to the
  tradable line without a reload. Keep that dependency.
- **Trap:** `tradeHoldMs(item, now)` and `isItemOnTradeHold(item, now)` take an optional second
  argument. Never hand them straight to `map` / `filter` / `some` (`items.map(tradeHoldMs)`): the
  array index becomes `now` and every hold reads tens of thousands of days. Wrap them:
  `items.map((item) => tradeHoldMs(item))`.

## Ratatoskr Transfer: To mode is one bulk storage

**The idea (Ivan's, keep it):** in To mode the user never thinks about single 1,000-item Storage
Units — they are one "limitless" storage whose size is the sum of their free places. Do not bring
back a unit picker, per-unit choices or an exclusion list for To mode; units are picked and
renamed only in From mode. Underneath, the Game Coordinator still moves an item into one specific
unit (`casketID`), so the backend plans the split:

To mode has no Storage Unit picker: `components/transfer/StorageSpacePanel.jsx` shows the free
places across every unit from the backend (`GET /api/ratatoskr/storage/<steamid>`: `free`, `units`,
`units_with_room`; it also counts what a move just deposited while the Game Coordinator's own counts
lag), the selection and what is left (Move is disabled while the selection is bigger).

**Moves are backend commands** (`backend/storage_deposit.py`, [storage-units.md](../../../docs/internals/storage-units.md));
the page decides nothing about where an item goes or whether a unit takes it. `handleMove` posts
`/api/ratatoskr/deposit/plan` (To) or `/api/ratatoskr/withdraw/plan` (From, `{item_id, casket_id}`
from `groupItemsByCasket`), refuses a plan that is not `executable` (overflow, or every item
refused — the message names the reason), then `/api/ratatoskr/plans/<id>/execute` and follows the
job (`utils/moveCommands.js`: `postCommand`, `followJob`, `jobProgress`, `watchJobUntilDone`). The
result message names the units ("Stored 60 items: Temp0 · 6, …") and, when something did not move,
the refused items. Ratatoskr confirms every move, so `done` is what really moved. Closing the tab
never stops a move (the job runs in the backend); a second Move on the same account while one runs
gets "Items are already moving". After a backend restart the job is unknown (404 `job_unknown`):
`followJob` stops at once, the page says the result is unknown and reloads the lists. After a deposit only the inventory and the unit counts are fetched
again, never every unit's contents. Never bring back a planner, an "expected counts" ref or a
storability rule in the page: read `storage_block` and the plan. `STORAGE_CAPACITY` in
`utils/transferView.js` is for display only (unit lists).
Auto-Store (backend `scheduler.py`) uses the same deposit command — change them together.

**Buy Storage Units is a backend command too** (`backend/storage_shop_service.py`,
[storage-shop.md](../../../docs/internals/storage-shop.md)); `StorageShop.jsx` only displays. Each
account row carries `selectable`, `max_quantity`, `can_buy_code` + `can_buy`; the order panel's totals
come from `POST …/storage-shop/quote`. **Review & buy** posts `…/storage-shop/plan {item, selection}`,
follows its `check_job_id`, then reads `GET …/storage-shop/plans/<id>`: lines (quantity, total, wallet
after, `problem`), totals and `state` (checking / ready / failed / replaced / expired; expired offers
Review again). "Test without paying" is `POST …/plans/<id>/execute {dry_run: true}` (repeatable); when it
ends the dialog goes back to the same review, whose lines now say how each test went, and Pay
(`{dry_run: false}`, refused with `dry_run_required` before a passed test; buys only the lines that passed,
once — again: the same job) is enabled by the plan's `executable` and labelled with `to_pay`. The dialog
follows each job by id (`followJob` with purchase wording) — its `orders`, `steps`, `account`/`step` and
`result.accounts` outcomes — and stays until closed. A purchase running elsewhere (another tab, a
reload) is opened from the status's `job.id`. Only a 4xx answer with a code (`refusedBeforeStart`) means
nothing started; a 5xx or no answer may come after a purchase started, so the dialog closes, says "it may
be buying" and the next status read opens the running purchase. The full status is asked every 1.5 s only
while a shop job runs with no dialog open; an open dialog follows its job and plan by id (status every
30 s and at each new stage). **Deliver again** comes from `can_deliver_again` /
`unclear_purchases` and answers a delivery job the dialog follows. Never bring back quantity limits,
line totals, problem texts or a "casket" branch in the page.

**Where Transfer's pieces live** (the page itself is layout and wiring): `utils/moveCommands.js`
(commands and jobs), `utils/useTransferSelection.js` (which lines can be picked, quantities, the
select buttons' states and tooltips, Select all), `utils/useMoveDelay.js` (the pause between moves),
`components/transfer/RenameCasketDialog.jsx` (renaming a unit; mounted with `key={casket.item_id}`),
plus the older `components/transfer/*` pieces.

## Ratatoskr: items a Storage Unit cannot hold

The backend marks every inventory item with `storage_block` (`storage_deposit.py`): code
`unstorable` (Ratatoskr's `item_moveable: false` — untradable collectibles, the default music kit,
default weapons), `storage_unit`, or `trade_protected` (with `until`). Transfer's To mode hides the
first two unless the switch `components/transfer/UnstorableSwitch.jsx` is on (`utils/showUnstorable.js`: `useShowUnstorable`, localStorage `ratatoskr.showUnstorableItems`).
`canGoIntoStorageUnit` (`utils/transferItems.js`) only reads that code; `groupItemsByName(…, {markUnstorable})`
(To mode only — anything already in a Storage Unit can always come out) puts such items on lines of
their own (`unstorable`), whose selectable quantity is 0; the backend's plan refuses them anyway.
