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
| `/huginn` | `pages/huginn/Arbitrage.jsx` | Views: Arbitrage (per-row "check live" ↻; "Active" per profile, `components/arbitrage/ActiveSwitch.jsx`, and the Active profiles panel, `ActiveProfilesPanel.jsx`: interval, active hours, the Buff163 reference, the price history; an active profile opens by itself from memory, `?kept=1`; "prices downloaded N minutes ago" from the `X-Pulse-Tables` header, `PricesAge.jsx`), Harvest, Case Arbitrage, Cross-Profile, Price dips (`components/PriceDips.jsx`), LF Auctions, LF Arbitrage (the other tabs say which markets the active profiles cover); header link to Pulse feed |
| `/huginn/pulse` | `pages/huginn/PulseFeed.jsx` | Read-only monitor of the master pulse feed: active profiles, the keeper's state (paused, behind, the Mac slept), downloads and reads since start, every table with why it is downloaded (active profile, reference, seed, Fetch live data), its age and who reads it, and the slaves with their tables in memory (`GET /api/huginn/pulse`) |
| `/huginn/gjallarhorn` | `pages/huginn/Gjallarhorn.jsx` | |
| `/huginn/card-deals` | `pages/huginn/CardDeals.jsx` | Andvari; `#buy` opens Buy games |
| `/huginn/team-fortress` | `pages/huginn/TeamFortress.jsx` | |
| `/draupnir`, `/draupnir/:portfolioId` | `pages/draupnir/` | |
| `/mimir` | `pages/mimir/Vault.jsx` | Credential vault; **Market unlocker** dialog (`components/MarketUnlocker.jsx`: tick accounts, Dry run, Buy for real after typing `BUY <n>`); **Market access** button, banner and column (`components/MarketAccess.jsx`: `MarketAccessButton`, `MarketAccessBanner`, `MarketAccessLine`; `utils/useMarketAccess.js`: `GET /api/mimir/market-access`, polled every 3 s while the check runs, `POST …/check`) |
| `/buy-storage-units` | `pages/ratatoskr/StorageShop.jsx` | Buy Storage Units, every account; outside the Ratatoskr layout; `/storage-shop` redirects here |
| `/store-catalogue` | `pages/ratatoskr/StoreCatalogue.jsx` | Every in-game store item with its US dollar price; a Buy button per item |
| `/store-catalogue/arbitrage` | `pages/ratatoskr/StoreCatalogue.jsx` (`tab="arbitrage"`) → `components/ratatoskr/StoreArbitrage.jsx` | Store Catalogue Arbitrage, laid out like Huginn's Arbitrage: profile In-game store → market (`ProfilePicker`, default CSMoney (Trade) autobuy, plus "Best market"), CSMoney (Trade) indicator column with BUY signal, Refresh, filters, Telegram switch / Send now; Buy links to the guarded buy page (read-only) |
| `/store-catalogue/boosts` | `pages/ratatoskr/StoreCatalogue.jsx` (`tab="boosts"`) → `components/ratatoskr/StoreBoosts.jsx` | Store Boosts: cards (alarm counts, Steam reader with its read spacing, hourly pulse check + Check now, alert switches + Test message / Test ring), a Tracked panel (pinned items on top, `TrackButton`, read on the normal schedule), views Moving now / Capsules / All, rows with level, Steam now vs usual, sales vs usual, a 7-day SVG chart and Confirmed; a row opens the 14-day hourly chart (`BoostChart`, hover) and a capsule's stickers with odds and opening value (read-only; Buy links to the guarded buy page) |
| `/store-catalogue/buy/:item` | `pages/ratatoskr/StorageShop.jsx` | The Buy Storage Units page for any price sheet entry (keyed by it) |
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
  `andvari.buy.apps`, `huginn.arbitrageFilters`, `huginn.harvestFilters`, `huginn.caseArbitrageFilters`, `huginn.crossProfileFilters`, `ratatoskr.storeArbitrage`, `ratatoskr.storeArbitrageFilters`, `ratatoskr.storeBoosts`, `ratatoskr.showUnstorableItems`, `huginn.priceDips`, `huginn.skinswapNoteOpen`, `lf_balance_pct`, `lf_unlocked`.
- **Shared pieces:** `components/gjallarhorn/InfoTip.jsx` (prop `tip`) for
  tooltips; `components/draupnir/columnSort.js` (`useColumnSort`, `sortRows`) with the header `components/ratatoskr/SortHeader.jsx`;
  `components/arbitrage/ArbitrageFilters.jsx` (price / account / collection filters + chips on Arbitrage, Harvest, Case Arbitrage, Cross-Profile and Store Catalogue Arbitrage; each tab names its price sides via `priceSides`; helpers, `useStoredFilters` and `buildOptions` in `utils/arbitrageFilters.js`; the page passes its inventory scan down as `byHash`, the only source of collections);
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
  protected" badge. In To mode `isHeldForDeposit` (= `tradeProtected`) makes the line's and its
  variants' selectable quantity 0 — select button and `GroupQtyInput` locked (`totalQty` still
  shows the count), Select all skips them, `selectTitle` says how long is left — and `handleMove`
  drops protected and unstorable ids before planning. Plain-hold lines stay selectable. Never key
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
unit (`casketID`), so the page plans the split itself:

To mode has no Storage Unit picker: `components/transfer/StorageSpacePanel.jsx` shows the free
places across every unit (`storageFreeSpace`, `utils/transferView.js`, with `STORAGE_CAPACITY`
1000), the selection and what is left; `planBulkDeposit(itemIds, caskets)` splits a move into one
batch per unit, the fullest with room first, and reports `overflow` (Move is disabled and
`handleMove` refuses while it is above 0). `runMoveBatches` queues the batches one after another
(From mode uses it too) and `showMoveProgress` adds the finished batches on, because Ratatoskr
counts each batch from zero — one progress bar for the whole move. After a deposit only the
inventory and the unit counts are fetched again, never every unit's contents.
The plan is made from the unit counts (`item_storage_total`) the page last loaded; the counts are
re-read after every move. The Game Coordinator's count can lag a few seconds behind a move, so
`expectedCasketCountsRef` keeps what the page just deposited per unit and `fetchCaskets` uses the
higher number until the Game Coordinator catches up (cleared by any withdrawal and on account
change) — a quick second deposit never over-fills a unit. Ratatoskr counts a sent move as done
even when the Game Coordinator refuses it, so an over-filled unit would fail silently: the items
would just stay in the inventory. Fill order is
fullest-with-room first (Ivan chose it: fewest units touched, emptier units stay free); the
batches run one after another at the normal move delay, never in parallel (rate limits).
Auto-Store (backend `scheduler.py`, `_plan_casket_moves` and `_is_storable`) follows the same two
rules — fullest first, trade-protected and unstorable items skipped; change them together.

## Ratatoskr: items a Storage Unit cannot hold

Ratatoskr's `item_moveable` (`items.js` `itemProcessorCanBeMoved`) is false for untradable
collectibles, the default music kit, Storage Units and default weapons. Transfer's To mode hides
those items unless the switch `components/transfer/UnstorableSwitch.jsx` is on (`utils/showUnstorable.js`: `useShowUnstorable`, localStorage `ratatoskr.showUnstorableItems`).
`canGoIntoStorageUnit` (`utils/transferItems.js`) is the one test; `groupItemsByName(…, {markUnstorable})`
(To mode only — anything already in a Storage Unit can always come out) puts such items on lines of
their own (`unstorable`), whose selectable quantity is 0, and `handleMove` drops
them before anything reaches the Game Coordinator.
