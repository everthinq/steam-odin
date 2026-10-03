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
Chrome (`--dump-dom --virtual-time-budget=…`, or puppeteer-core). Never click a
real Pay / Buy / Sell / Approve button while testing; use the dry-run paths.

## Routes (`src/App.jsx`)

| Path | Page | Notes |
|------|------|-------|
| `/` | `pages/Dashboard.jsx` | The only eager page; account cards + tool tiles |
| `/add-account` | `pages/AddAccount.jsx` | maFile import |
| `/accounts/:steamid/confirmations` | `pages/Confirmations.jsx` | |
| `/huginn` | `pages/huginn/Arbitrage.jsx` | Views: Arbitrage, Case Arbitrage, Cross-Profile, Harvest, LF Auctions, LF Arbitrage |
| `/huginn/gjallarhorn` | `pages/huginn/Gjallarhorn.jsx` | |
| `/huginn/card-deals` | `pages/huginn/CardDeals.jsx` | Andvari; `#buy` opens Buy games |
| `/huginn/team-fortress` | `pages/huginn/TeamFortress.jsx` | |
| `/draupnir`, `/draupnir/:portfolioId` | `pages/draupnir/` | |
| `/mimir` | `pages/mimir/Vault.jsx` | |
| `/storage-shop` | `pages/ratatoskr/StorageShop.jsx` | Every account; outside the Ratatoskr layout |
| `/ratatoskr/:steamid/{inventory,transfer,auto-store}` | `pages/RatatoskrLayout.jsx` + `pages/ratatoskr/` | Layout passes `{steamid, account}` via `<Outlet context>` |

Every page except the Dashboard is `React.lazy`; all routes sit in a
`RouteErrorBoundary` keyed by path (a stale tab after a rebuild gets a
chunk-load error with a Reload button). `TitleManager` sets the tab title by
path prefix — add your page there. No catch-all route, no Settings page.

## Conventions

- **Fetching:** plain `fetch('/api/…')` in each component. Backend errors come
  as `{error: "…"}`; background jobs answer `{started, started_at}` and report
  `{job: {running, done, total, phase, error}}`.
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
  or a review step with a dry run (Storage shop, Buy games). Guard double
  clicks on anything that spends money with a ref, not only state.
- **localStorage** is for per-browser conveniences only (layout, filters),
  every access wrapped in `try`/`catch`. Keys in use: `heimdall-dashboard-layout`,
  `draupnir-portfolio-layout`, `huginn.harvest`, `andvari.valuation`,
  `andvari.buy.apps`, `lf_balance_pct`, `lf_unlocked`.
- **Shared pieces:** `components/gjallarhorn/InfoTip.jsx` (prop `tip`) for
  tooltips; `components/draupnir/columnSort.js` (`useColumnSort`, `sortRows`);
  `utils/transferItems.js` (`matchesSearchQuery`, wear shorthand fn/mw/ft/ww/bs);
  per-market link builders in `utils/*Market.js` + `*MarketLink.jsx`;
  `utils/tradeonShortLink.js`. Item images: `api.steamapis.com/image/item/730/<name>`.
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
- Ratatoskr Inventory lets you select items, but the selection has no actions.
