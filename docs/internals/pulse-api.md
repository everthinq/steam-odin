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
| `marketHashNameFilter: {"value": "…"}` in the body | substring search (`Flickshot` → sticker, slab, graffiti), one value per request, ~5 KB | a few items fresh, without the 59 MB table |
| `POST .../{first}/{second}/all/v2` with `viewTemplateId` | a **view template**: rows come back as blocks, one of which, `ExtraPrices`, is a list of `{market, priceType, price}` | **one request could carry every market's price** (section 4) |
| `GET /api/options/template/counter-strike/all` | saved view templates (Ivan's account: only "Default") | |
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
  10-minute alert polls), the portfolio valuation (Draupnir, Gjallarhorn) and Store Catalogue
  Arbitrage all read `table(token, market, price_type, max_age)`.
- **Single flight**: while a table downloads, every other caller of the same table waits for that
  download; at most 4 tables download at once (`MAX_PARALLEL_DOWNLOADS`: each download peaks at
  about 200 MB of memory; the Case Arbitrage alert poll's 7 markets arrive in two waves, about 3
  seconds apart). Parsing happens outside the
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
  quote `(price, count, rising, falling, overstock info, page price)`; names and images stored once
  for all tables. About 15 MB a table instead of 155 MB. `rows()` rebuilds fresh pulse-shaped
  dicts (only for `names` when given: pairs build rows for the items both sides have, LOOT.Farm
  boards for the items in its feed, CSFloat autobuy for swept items); `index()`, `prices()` and
  `entries` serve the boards without building rows. `EstimatedSell` (SkinSwap (Trade)'s estimated
  listings) is never a pulse table: `_table` refuses it, `_pull_market` builds it from real tables. Fields not
  kept: `realPrice` and currency, `bestOfferCount`, update times, hold info, `soldOutTime`,
  `itemPopularity`. Add a field to `_quote` if a feature needs it.
- **Bonus balances** become real dollars once, on arrival (`_MARKET_PRICE_SCALE`, SkinSwap (Trade)
  ÷ 1.4), the page price kept as `pagePrice`.
- **Slaves**: `add_listener(table)` after every download, on one background thread
  (`HuginnService.add_pull_listener` gives `(market, 'listing' | 'instant', table)`); Store
  Catalogue Arbitrage keeps the store items from every table this way.
- **Status**: `GET /api/huginn/pulse`: per table downloads, reads, age, failures, and the totals.
  Live on 2026-10-15, one pass over every pulse-backed page: 33 table reads, 12 downloads.
- **Equivalence**: on recorded tables, the old code (git `b3792d5`) and the feed gave the same
  output for all 59 feature outputs checked (curated and generated pairs, LOOT.Farm boards,
  valuation, Case Arbitrage, indexes, buy orders, auctions), apart from fields no page reads
  (`realPrice`) and a count of 0, which some old paths turned into "none".
- Tests: `tests/test_pulse_feed.py`; `tests/conftest.py` makes any un-faked pulse request fail.

## 4. Ideas not built yet

1. **One request for every market (view template v2).** A saved view template with an
   `ExtraPrices` block (market + price type pairs) returns those prices per item in one table.
   Today a full refresh downloads 13 to 30 tables (59 MB each); one template table could replace
   most of them. Needs: creating a template on Ivan's pulse account (a write to his account: ask
   first), checking which markets his subscription allows in ExtraPrices
   (`MarketSubscriptionRequired`), and the template request format (`/api/options/template/...`).
2. **Liquidity for free.** `itemPopularity` gives weekly sales counts on Buff, Steam, Tm and
   Tradeon in every row. Gjallarhorn reads Steam's `pricehistory` for liquidity and fights 429s;
   pulse already has a weekly count.
3. **Stale prices.** `offersUpdateTime` per market says how old a price is (GgSwap's are about a
   year old): pages could grey out prices older than a day instead of a fixed ⓘ per market.
4. **Spike check.** `Median30d` / `Average30d` as a price type: "lowest listing now vs its 30-day
   median" for Store Catalogue Arbitrage and Case Arbitrage, without our own history.
5. **Small lookups.** The name filter fetches one item in ~0.5 s and 5 KB, for single-item views
   (a Buy page, one held item) instead of a whole table.
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
