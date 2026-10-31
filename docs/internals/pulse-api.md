# Tradeon pulse API and the master price feed

Everything Heimdall knows about the Tradeon pulse API (`api-pulse.tradeon.space`), and how it is
called: **only through `backend/pulse_feed.py`** (the master feed). Read this before adding any
price request. Investigated live on 2026-10-15 with the saved `tradeon_token`; market ids, price
types and fees per market are in the memory note `reference_pulse_markets_fees` (2026-10-10).

## 1. The one request: a price table

```
POST https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/{market}/all
```

- The **first market** is always Tradeon, priced as `Sell` (its lowest listing). The **second
  market** is `{market}`, priced as `secondMarketOptions.secondMarketPriceType`. Direct
  `{A}/{B}` tables are paywalled to items under about $2, so Heimdall never uses them: a pair
  A → B is two tables joined on `itemName.marketHashName`.
- So a table is fully named by **(market id, price type)**. Every feature asks the feed for
  tables by that key: `HuginnService._table(token, market, price_type, max_age)` (a
  `PulseTable`) or `_pull_market(...)` (pulse-shaped rows).
- Price types (`/api/price-type/status`, Ivan's account): `Sell` (lowest listing),
  `SellWithoutHold` / `SellWithHold`, `Buy` (highest buy order: what you get selling instantly),
  `Guaranteed`, and statistics: `Average30d`, `Median30d`, `Range50Percent7d`,
  `Range70Percent30d`, `SavePrice`, `TradePrice` enabled; `Auction`, the other averages, medians
  and ranges disabled on this account. A statistics type works as a normal second-market price
  (checked: Steam `Median30d`).
- Size and speed: one full table is about **32,000 rows, 59 MB of JSON, 3 seconds**; as Python
  objects about **155 MB**. That is why the feed keeps a compact form (section 3).
- On the wire it is much smaller: pulse gzips a table on request, **~4–5 MB** (`Accept-Encoding:
  gzip`, sent since 2026-10-30; it also offers Brotli, which Python cannot read without a library).
  On the slow connection of late October that took a table from ~100 s to 3–20 s.
- Headers that matter: `Authorization: Bearer <token>`, `Device-Id`, a browser `User-Agent`,
  `Origin`/`Referer` `https://pulse.tradeon.space`, `Usercurrency: USD`. Without `Device-Id` and
  `User-Agent` pulse answers 403 (`HuginnService._post_tradeon` sends them).

### A row

```
itemName      {marketHashName, isStatTrak, isSouvenir, itemTypeName, skinName}
imageUrl      Steam's image of the item
firstMarket / secondMarket
              {price, realPrice + realPriceCurrency (the market's own currency, e.g. CNY for Buff),
               totalOffersCount, bestOfferCount, offersUpdateTime / historyUpdateTime (epoch:
               how fresh the market's price is), overstockInfo (LOOT.Farm), holdInfoResponse,
               soldOutTime, isRaising / isFalling / isBoosting, overriddenPrice}
profit, profitPercent   pulse's own, with ITS commission settings (/api/commission-settings)
itemPopularity          [{market, salesCount (in salesCountPeriod, "Week"), marketItemUrl}]
                        for Buff, TradeOnMarket, Steam, Tm
```

### The normalised body (`pulse_feed.BASE_BODY`)

One body for every table. What it contains, so nothing is ambiguous:

| Part | Value | Why |
|------|-------|-----|
| Price filters (both markets) | none (`minValue`/`maxValue` null) | Ivan removed the $0.13 floor 2026-10-12: cheap items count |
| Count, update-time, profit, profit-percent, name, rarity, hold filters | none | the pages filter themselves |
| Item types / qualities / StatTrak / souvenir / sticker / graffiti | all | |
| `displaySoldOutItems` | **true** (both places) | shows items Tradeon has sold out of; on 2026-10-15 it changed no table's item count (Steam, Buff, CsMoneyTrade, Dmarket, SkinSwapTrade `Buy`, Steam `Sell`, LisSkins `SellWithoutHold`), so the old "filtered" and "unfiltered" variants were the same table downloaded twice |
| `salesCountPeriod` | `Week` | only affects `itemPopularity` |
| `countFilterMode` | `TotalOffersCount` | |
| Order, page | profit percent descending, `takeCount` 9,999,999 | every row in one page |

Before 2026-10-15 the code had five URL constants, six body variants and two sold-out variants;
they all reduced to (market, price type), with this body.

## 2. Other endpoints (from the pulse web app's code, `pulse.tradeon.space/app` → `/assets/chunks/*.js`)

| Endpoint | What it is | Use |
|----------|-----------|-----|
| `POST .../TradeOnMarket/{m}/count` | `{"totalTableItems": n}` for the same body, 0.5 s | cheap check without downloading the table |
| `marketHashNameFilter: {"value": "…"}` in the body | substring search (`AK-47 \| Redline (Field-Tested)` also returns the StatTrak and Souvenir ones), one value per request, ~0.5 s, ~5 KB | `PulseFeed.lookup` ("Check live" on an Arbitrage row) |
| `POST .../{first}/{second}/all/v2` with `viewTemplateId` | a **view template** table: rows come back as blocks (section 6); at most **300 rows per request** (`TakeCountLimitExceeded`) | tested, not used (section 6) |
| `GET/POST/DELETE /api/table-view/templates[/{id}]` (`?gameType=CsGo` on GET), `/presets`, `/palette`, `/formula-variables`, `/formula/generate` | view templates: create `{name, gameType, blocks}` → `{templateId}`; pulse's own presets ("Цены рынков" has an `extraPrices` block) | section 6 |
| `GET /api/options/template/counter-strike/all` | saved filter templates (Ivan's account: only "Default"); different from view templates | |
| `POST /api/item/market-best-prices` `{"marketHashName", "gameType": "CsGo"}` | one item on every market | |
| `/api/item/info`, `/api/item/info/history`, `/api/item/offers-history/history` | one item's details and price history | |
| `/api/live-feed/*` | pulse's own alert schemes (triggers, black lists, Telegram notification) | |
| `GET /api/table/supported-features/counter-strike/market-info` | markets, their price types, subscription gating | |
| `GET /api/commission-settings` | pulse's fee per market (editable per user on pulse) | registry defaults |
| `GET /api/price-type/status` | price types enabled on the account | |
| `GET /api/table/user/info` | account, balance, `pulsePublicApiResponse.tokenCount` (an official API exists; 0 tokens) | |

Errors are `{message, responseType}` with types such as `MarketSubscriptionRequired`,
`TakeCountLimitExceeded`, `WrongTemplate`, `WrongViewTemplate`.

## 3. The master feed (`backend/pulse_feed.py`)

- **The only code that builds a pulse request.** Huginn (curated profiles, generated pairs,
  LOOT.Farm boards, auctions), Harvest, Cross-Profile, Case Arbitrage (hourly refresh and the
  10-minute alert polls), the portfolio valuation (Draupnir, Gjallarhorn), Gjallarhorn's
  liquidity, Price dips, the kept-fresh keeper and Store Catalogue Arbitrage all read
  `table(token, market, price_type, max_age)`; "Check live" uses `lookup(...)`. The read-only views
  on top of it live in `backend/pulse_views.py`.
- **Single flight**: while a table downloads, every other caller of the same table waits for that
  download; at most 4 tables download at once (`MAX_PARALLEL_DOWNLOADS`: each download peaks at
  about 200 MB of memory; the Case Arbitrage alert poll's 7 markets arrive in two waves, about 3
  seconds apart). Background jobs (`job()`, the keeper) take at most 3 of the 4
  (`MAX_BACKGROUND_DOWNLOADS`) and a waiting page or button goes before them; a page that joins a
  job's queued download moves it to the front. So Fetch live data never queues behind the jobs:
  on 2026-10-29 the connection pulled ~0.8 MB/s and a table took 2–5 minutes (1–15 seconds
  before 27 October), and the clicks waited behind the jobs' queue. Since 2026-10-30 tables come gzipped
  (about ten times smaller), which brought a table back to seconds. Parsing happens outside the
  feed's lock, so readers of other tables never wait for it.
- **Stamp with the download time, never "now".** A feature that keeps its own copy (Store
  Catalogue Arbitrage indexes, Case Arbitrage snapshots, Harvest) stamps it with
  `table.started_at`: the feed may serve a table up to `max_age` old. Use
  `HuginnService.market_index_at(token, market, 'listing' | 'instant', max_age, names)`, which
  returns `(downloaded at, index)`.
- **Freshness**: `FRESH` (10 minutes) for boards and background work; `ON_DEMAND` (1 minute) for
  a button the user pressed (Fetch live data, curated and generated pairs, Store Catalogue
  Arbitrage Refresh and its daily refresh, Case Arbitrage "Check now") and the Case Arbitrage
  alert polls. Cross-Profile reads `FRESH` tables (its buy side already reuses the portfolio
  valuation's 1-hour map).
  The Case Arbitrage hourly refresh also asks for `ON_DEMAND` (its alert run compares the
  markets with each other), and the portfolio valuation (cached an hour) too. A table is dropped
  `KEEP_SECONDS` (10 minutes, = `FRESH`) after its download.
- **Compact**: per item `(first quote, second quote, pulse profit, pulse profit percent)` with a
  quote `(price, count, rising, falling, overstock info, page price, updated)` (`updated` = the
  market's `offersUpdateTime`, epoch seconds); names and images stored once
  for all tables. About 15 MB a table instead of 155 MB. `rows()` rebuilds fresh pulse-shaped
  dicts (only for `names` when given: pairs build rows for the items both sides have, LOOT.Farm
  boards for the items in its feed, CSFloat autobuy for swept items); `index()` (`{price, count,
  image, rising, falling, updated}`), `prices()` and `entries` serve the boards without building
  rows; `rows()` market dicts carry `offersUpdateTime` when pulse gave one. `EstimatedSell` (SkinSwap (Trade)'s estimated
  listings) is never a pulse table: `_table` refuses it, `_pull_market` builds it from real tables. Fields not
  kept: `realPrice` and currency, `bestOfferCount`, `historyUpdateTime`, hold info, `soldOutTime`.
  Add a field to `_quote` if a feature needs it.
- **Weekly sales** (`weekly_sales(names)`): every row's `itemPopularity` (sales in the last week on
  Steam, Market.CSGO `Tm`, Tradeon and the table's own market, e.g. Buff in a Buff table) is merged
  per item and market, newest wins; ~35,000 items after a few tables. Gjallarhorn's liquidity and
  Price dips read it.
- **Single items** (`lookup(token, market, price type, name)`): the name filter, exact name kept,
  not cached, shares the download limit; `status()['lookups']` counts them.
- **Only Huginn Arbitrage downloads** (2026-10-31, Ivan's master-slave rule). `_read` is the gate:
  a downloader is the keeper or a thread inside `feed.reading(download=True)`, which only the profile
  routes open (one `before_request` hook on `/api/huginn/tradeon/`, routes/huginn.py). Every other
  read is a slave: the copy in memory whatever its age, a download already under way, or `NotActive`
  ("not kept by an active Huginn profile", or "active but not downloaded yet" right after a
  restart). `PulseFeed.slaves_may_download` exists for the older slave tests only (conftest.py).
- **Active tables** (`set_active(keys, minutes)`, `keep_fresh_once`, `start_keeper`): the tables of
  the active profiles (settings `huginn_active_profiles` as `"BuyMarket:SellMarket:autobuy|min"`,
  read from the older `huginn_kept_profiles` until first saved; `huginn_active_minutes` 5/10/15/30/60,
  default 5; at most `MAX_ACTIVE_TABLES` 12) plus `REFERENCE_TABLES` (Buff163's lowest listing,
  once a day). `download_once` adds one-time downloads (the price history's seed). The keeper (30 s
  tick, one table at a time, a failed table waits 5 minutes) runs only inside `set_active_hours`
  (settings `huginn_active_hours` {enabled, start, end}, local time); a tick that took over
  `SLEEP_GAP` means the Mac slept: it forgets the failures, waits a tick for the network, then
  catches up (`note_sleep`, `status()['keeper']['sleep']`). An active table is never dropped from
  memory; others leave after `KEEP_SECONDS`. `profile_tables` mirrors each profile's fetch and
  `tests/test_active_profiles.py` calls every profile route to prove they match. `?kept=1` (an
  active profile opening by itself) accepts the keeper's copy up to its interval plus two ticks;
  the profile routes answer `X-Pulse-Tables` ([[market, price type, seconds since download]]).
- **Price history** (`price_history.py`, a listener): every listing or buy-orders table adds one
  sample per item to the day's row (average, lowest, highest, last, samples), one gzipped file a day
  per table in `cache/price_history/<market>/<price type>/`, kept for good. `statistic()` gives the
  30-day median or average of the daily averages from 7 days on, pulse's Median30d/Average30d seed
  (downloaded once for active markets pulse has them for, again monthly while active, ignored after
  45 days; a seed pulse refuses is marked `.unavailable` for a month) before. Sold-out items and
  prices their market has not refreshed for a day are not sampled; a table from before midnight that
  finishes late is added to its own day; today's rows are saved every minute (a docker restart kills
  the reloader's worker without atexit). `trade_statistic` (the middle of listing and buy orders)
  is what Store Boosts and Gjallarhorn compare with. Price dips, Gjallarhorn and Store
  Boosts read it. Measured before the switch (2026-10-31): 134 downloads an hour, about 17 GB a day;
  after: the active tables only (7 tables at 5 minutes, about 10 GB a day, plus Buff163 daily).
- "Check live" asks for 500 rows per name (a short name is contained in many) and prices the sell
  side exactly as `fetch_generated_pair` (`'Sell'` for a "min" target, LisSkins included).
- Routes: `GET/POST /api/huginn/active` (one change per request, merged on the server: `add`,
  `remove`, `minutes`, `hours`; `?preview=` says what making a profile active adds) and the
  read-only `GET /api/huginn/pulse` (the Pulse feed monitor).
- **Bonus balances** become real dollars once, on arrival (`_MARKET_PRICE_SCALE`, SkinSwap (Trade)
  ÷ 1.4), the page price kept as `pagePrice`.
- **Slaves**: `add_listener(table)` after every download, on one background thread
  (`HuginnService.add_pull_listener` gives `(market, 'listing' | 'instant', table)`); Store
  Catalogue Arbitrage keeps the store items from every table this way.
- **Jobs (slaves)**: the recurring jobs declare themselves (`declare_job(id, label, schedule,
  tables(), active(), period)` in `app.py`: `case-arbitrage-hourly`, `case-arbitrage-alerts`,
  `store-arbitrage`, `store-boosts`) and read inside `with feed.job(id):`, only so the monitor can
  say who reads what: they never download. The Case Arbitrage loop wakes when the keeper brings an
  alert-market table (`_wake_case_loop`, a listener) and runs its alerts after the pass settles,
  instead of polling every 10 minutes. Case Arbitrage alerts leave out any market whose snapshot is
  over 30 minutes old (`_ALERT_SNAPSHOT_MAX_AGE_SEC`), so a market not active never alerts on old prices.
- **Status**: `GET /api/huginn/pulse`: per table downloads, reads, last read, age, failures, kept,
  the totals, lookups, weekly-sales coverage, `jobs` (declared, with tables and period), per table
  `jobs` (which read it, when), `declared_jobs`, `switched_off`, plus `choices` (the page's grid)
  and `token_set`. The jobs' tables are cached `JOB_STATUS_SECONDS` (30); downloads and saves
  validate with `seen_tables()` / `declared_tables()`. `served_from_memory` counts only reads
  answered with a table already held (`from_memory`; refused or failed reads are not counted, the
  keeper's downloads are not reads). The keeper's own read is never stretched to the kept interval
  (`keeper=True`), so it refreshes on time at 15/30/60 minutes. A `SwitchedOff` hit by the store
  board's job is not recorded as a failure (the page's own request still downloads). Gjallarhorn
  serves the last pulse liquidity while the Steam tables download again (`_last_pulse_views`). "Check live" lookups have their own 2 slots (never behind full downloads). Weekly sales
  keep the newest count per item and market by download start. A stale price
  (`pulse_feed.is_stale`, `STALE_SECONDS` a day) is never Harvest's best offer nor the store board's
  reference, best, signal or Telegram post.
  The page is Huginn → **Pulse feed** (`/huginn/pulse`).
  Live on 2026-10-15, one pass over every pulse-backed page: 33 table reads, 12 downloads.
- **Equivalence**: on recorded tables, the old code (git `b3792d5`) and the feed gave the same
  output for all 59 feature outputs checked (curated and generated pairs, LOOT.Farm boards,
  valuation, Case Arbitrage, indexes, buy orders, auctions), apart from fields no page reads
  (`realPrice`) and a count of 0, which some old paths turned into "none".
- Tests: `tests/test_pulse_feed.py`; `tests/conftest.py` makes any un-faked pulse request fail.

## 4. Ideas: what became of them

1. **One request for every market (view template v2): tested 2026-10-15, not used.** See section 6:
   300 rows per request, so a full sweep is ~107 requests (~85 s, ~95 MB) carrying prices only
   (no counts, no update times, no LOOT.Farm overstock), against 13 full tables (~770 MB, ~40 s
   four at a time) with everything. Revisit if pulse's bandwidth or Ivan's ever matters more than
   counts, or for a price-only background sweep.
2. **Liquidity for free: done (2026-10-15).** `PulseFeed.weekly_sales`; Gjallarhorn's sell list
   takes Steam's weekly sales, lowest listing, highest buy order and 30-day median from pulse
   (`pulse_views.steam_liquidity`) for every held item (494 on 2026-10-15, 0.15 s, no Steam call);
   `SteamMarketService` only for items pulse lacks. The "7d trend" column became "vs 30-day
   median" (Steam's own 7-day trend only for those fallback items); spread is now lowest listing
   vs highest buy order.
3. **Stale prices: done (2026-10-15).** Measured: almost every price minutes old; some Steam
   listings 600+ days. A price whose market has not updated it for a day is greyed out on Arbitrage
   (`PriceCell` `updated`/`now`, `utils/priceAge.js`), flagged on Harvest, hidden by default on
   Price dips, and on Store Catalogue Arbitrage treated like a suspicious one (never best, BUY
   signal or Telegram post: `store_arbitrage_service.is_stale`).
4. **Spike check: done (2026-10-15)** as Huginn → **Price dips** (`pulse_views.price_dips`,
   `GET /api/huginn/price-dips?market=&basis=Median30d|Average30d`): a market's lowest listing vs
   its 30-day median or average, weekly sales, highest buy order, "back to usual" after the fee,
   My Inventory. Markets with statistics: `pulse_views.STATISTICS_MARKETS` (pulse market-info
   `isSupportHistory`; Buff163 and WhiteMarket need the Table subscription; CSFloat answers but is
   not listed, so left out). Buff163's medians are noisy (an AK-47 Redline (Field-Tested) showed a
   $43.5 median against a $25.3 listing); Steam's are sane (listing / median: middle 1.02).
5. **Small lookups: done (2026-10-15)** as "Check live" on each Arbitrage row
   (`pulse_views.check_live`, `GET /api/huginn/check-live?name=&buy=&sell=&mode=`): both sides by
   name, profit with the Fees editor's fee; not for LOOT.Farm as the target, CSFloat buy orders
   or estimates.
6. **Fees: done (2026-10-15).** The four curated Tradeon-first profiles (Tradeon → Steam,
   Buff163, CSFloat, DMarket) used to show pulse's own `profit`, computed with the fees saved on
   the pulse account; they now compute it with the Fees editor like every other profile
   (`_tradeon_first_rows` → `_join_direct` with `market_fee`). Measured that day: Steam 13.03% vs
   your 13% (+$0.003 per item), Buff163 and CSFloat equal, DMarket 5% vs your 0% (+$0.84 per item
   median). Pulse's `profit` / `profitPercent` fields are still stored but nothing displays them.

## 5. If pulse ever closes this access (a note, nothing built)

Every price in Heimdall comes through the access described in section 1: pulse's
`TradeOnMarket/{market}/all` tables with the second market readable. If pulse ever closes or
changes it (403/401 answers, `MarketSubscriptionRequired` on tables that work today, a new
request format), Ivan's plan (2026-10-15) is to build the same architecture himself: many
marketplaces, each scraped individually, combined behind one API, with one item name giving every
market's prices and price types (lowest listing, buy orders, statistics). The per-marketplace
scrapers are a separate project.

The master feed is already the seam for that: features ask `table(market, price type, max_age)`
and never see where the table came from, so another source can plug in behind the same interface
without touching the pages.

## 6. View templates, measured (2026-10-15)

Ivan allowed one test on his account; the template was created, used and deleted the same minute
(his account lists no view templates afterwards).

- **Create**: `POST /api/table-view/templates` `{name, gameType: "CsGo", blocks}` → `{templateId}`;
  blocks need `kind`, `category`, `leftBound`/`rightBound`/`width` on a 24-column row without
  overlap; `action` and `itemName` are required. `DELETE /api/table-view/templates/{id}`.
- **Extra prices per row**: an `extraPrices` block (at most 4 `{market, priceType}`) plus the
  `itemName` block's `marketPricesExtraInfo` (at most 9): 13 extra prices + Tradeon + the second
  market = 15 prices per item. Also available as blocks: `salesCount`, `salesGraphic` (price points,
  `medianPrice30d`, `volatility` for one market), `ordersCup` (one market's order book),
  `updateTime`, `formula` / `flag` (pulse computes your expression).
- **Request**: the normal body plus `viewTemplateId` to `.../TradeOnMarket/{market}/all/v2`;
  `takeCount` above 300 → 400 `TakeCountLimitExceeded "TakeCount is limited by 300 rows"`. Page
  with `skipCount`, ordered by `marketHashName` (stable; `profitPercent` order moves while you page;
  key `name` → HTTP 500). `/count` answered 31,833 rows for Steam.
- **Speed**: 300 rows ≈ 0.78 s and 0.9 MB; a whole Steam table ≈ 107 requests, ~85 s, ~95 MB.
- **Answer**: per row `marketHashName`, `imageUrl`, `itemName`, and `blocks`; extra prices are
  `{market, priceType, price}` only (no count, no update time); a missing price is `null` or
  `0.0` (Waxpeer), so treat 0 as none.
- **Accuracy**: ordered by name, 1,200 rows compared with the normal tables a minute apart: Steam
  Sell 1,194 identical (6 moved), Buff Sell 1,199, DMarket Buy 1,200, CSMoney (Trade) Buy 1,061 (the
  rest present on one side only, most as `0.0`).
