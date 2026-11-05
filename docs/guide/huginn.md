# Huginn — the price scout

Huginn compares prices across Counter-Strike 2 markets so you can see where to
buy cheap and where to sell well. **Nothing on these screens buys or sells** —
they only show prices and links to the markets.

Needs the Tradeon token (see [getting-started.md](getting-started.md#4-prices-huginn-and-draupnir)).
Open it from the Dashboard tile **Huginn**. The switch at the top picks a view.

## Get all items and the morning routine

**Get all items** logs every account into Ratatoskr and reads its inventory and
Storage Units (up to a minute or so). Many views use it for "My Inventory",
"Owned" and collection filters. It runs by itself every morning at **08:00**
(or the first minute the computer is awake after 08:00), followed by the CSFloat
buy-order check and the Store Catalogue refresh (the in-game store's prices, every
market, then the Telegram post of profitable store items; see
[CS2 Store Arbitrage](#cs2-store-arbitrage)); the header shows "daily HH:MM ✓" when
that went well.

An account Get all items cannot log into keeps its items from the last scan that
reached it, so the Accounts filter and "My Inventory" never shrink to the accounts
that answered. An amber note above the table then names those accounts and when
their items were read; Mímir shows each one's login error. Press Get all items again
once the problem is gone.

## Fees — set once, used everywhere

**Fees** (Arbitrage view) holds each market's sell fee. Every profit figure in
Huginn (the four built-in "Tradeon → …" profiles included, since 2026-10-15),
Harvest, Gjallarhorn and the CS2 Store Arbitrage uses these numbers. Your number always wins.
A market you never changed uses our confirmed fee, or else Tradeon pulse's own
fee for it, tagged **pulse**; **yours** marks a fee you changed. ⚡ marks a
market with buy orders (you can sell there instantly). After changing fees,
fetch the profile again.

**Checked markets.** Some markets' prices were checked against live data and the
markets' own sites (on 2026-10-19). The Fees list and the profile picker mark them;
hover the mark for why. It is a mark only: nothing is hidden or left out.

- Red triangle, **unreliable**: Skinport's lowest listings (pulse showed them 15-45%
  below Skinport's own prices) and WhiteMarket's buy orders (they jump around).
- Green shield, **believable**: buy orders on Buff163, Market.CSGO, DMarket and
  CSMoney (Market); listings and buy orders on Buff.market, CSFloat and Steam.
- No mark: not checked yet.

## Arbitrage

1. Pick a **profile** — a buy market → sell market pair (for example "Buff163 →
   CSFloat autobuy"). Each "Buy on …" section lists **⚡ Sell instantly · buy
   orders** first (autobuy: the market buys from you at once), then **List for
   sale · lowest price** (min: you list the item and wait for a buyer). **Instant
   sell only** hides the second kind. Every pulse market is there, 40 in all.
   Type a market's name in the picker's search and its "Buy on …" section comes
   first, above the pairs that only sell to it.
   An ⓘ marks GgSwap, GamerPay and SkinSwap (Trade): the pulse website does not
   show them and their prices can be old, so check the market before you trade.
   SkinSwap has two sides; see [SkinSwap](#skinswap-market-trade-and-one-balance) below.
2. Press **Fetch live data**. Or switch on **Active** next to the profile (a
   profile you trade on): its price tables are then downloaded in the background
   every 5 minutes, and the profile opens by itself in about a second, with prices
   at most that old. Active profiles have a pin and are listed first under
   **Active** in the picker. **Fetch live data** still downloads fresh prices.
   Next to the button, **prices downloaded N minutes ago** says how old the shown
   prices are (hover for each table); **Newer prices ready · show** appears when
   the background download has replaced them.

### Active profiles: the only prices Heimdall downloads

Only Huginn Arbitrage downloads prices from pulse: the **active profiles**'
tables (every 5 minutes), Buff163's lowest listing once a day (the off-Steam
reference), **Fetch live data**, and **Live** for one item. Every other page,
Case Arbitrage and its alerts, Harvest, Cross-Profile, Price dips, Store
Catalogue, CS2 Store Boosts, Gjallarhorn and Draupnir, reads those tables from
memory and never downloads; a market no active profile uses shows as **not
active** there (the other tabs say which markets are covered).

The **Active profiles** panel under the profile picker lists them with each
table's age, opens one on a click, makes one inactive (×), and sets:

- **Download every** 5 to 60 minutes.
- **Active hours** (off by default): downloads only between the two times
  (local; an end before the start runs over midnight). Outside them the pages
  keep the last prices and Case Arbitrage alerts wait; the Counter-Strike 2 news
  watcher still rings, so you can sleep.
- **Price history**: every downloaded table adds one row a day per item
  (average, lowest, highest, last), kept for good in `backend/cache/price_history`.
  Price dips, Gjallarhorn and CS2 Store Boosts take their 30-day median from it
  (from pulse's own 30-day prices, downloaded once, until an item has 7 days).

When the Mac sleeps nothing is downloaded; afterwards the keeper waits a moment
for the network and catches up (the panel says when it slept).
3. The table lists items with buy price, sell price, profit and profit %, how
   many you own and on which accounts, and links to each market.
4. **Live** (↻ on a row): checks that one item on both markets right now, in about
   a second, and puts the live prices and profit in the row (green icon; red when
   one market no longer has it). Use it before you buy. Not for LOOT.Farm as the
   sell market, CSFloat buy orders or estimated SkinSwap (Trade) prices.

A **greyed-out price** is one its market has not updated for a day or more (hover
it for how long): it is probably gone.

Filters: search (wear shorthand works: `fn`, `mw`, `ft`, `ww`, `bs`), **My
Inventory**, **Hide Unstable** (LOOT.Farm overstock), and the **Filters** button:

- **Price**: a range on the buy price or the sell price, typed or picked from
  Under $1, $1–$10, $10–$100, $100+. SkinSwap (Trade) is filtered in real dollars.
- **Accounts**: only items held on the ticked accounts (needs Get all items).
- **Collections**: only items from the ticked collections you hold. With accounts
  ticked too, the same copy must match both.

Each option shows how many deals of the profile it matches. Active filters show as
chips under the search bar (× removes one) and are remembered in this browser.
Results are kept until you reload the page.

### SkinSwap: Market, Trade and one balance

SkinSwap has a Market page (real dollars) and a Trade page (Trade dollars,
shown 1.4 times higher: its 40% bonus).

- **Money you deposit** is one balance: the Trade page shows it 1.4 times higher
  than the Market page.
- **Money from selling a skin on the Trade page stays on Trade.** Tested on
  2026-10-10: a sale showed up on the Trade page and not on the Market page.
  It is most likely held while Steam's 7-day trade protection runs; whether it
  reaches the Market page afterwards is not confirmed.
- **SkinSwap (Trade) prices are shown as on the Trade page**, with the
  real-dollar value (divided by 1.4) in grey under each: $35.35 with "$25.25
  real" under it. Profit and profit % always use the real-dollar value.
- **To get Trade balance back out**, buy on the Trade page and sell to a market
  that pays money. It pays off when what you get after fees is at least 0.714 of
  the Trade page price (1 divided by 1.4). Steam, LOOT.Farm, TradeIt (Trade) and
  CSMoney (Trade) pay in their own balance, not money. The SkinSwap card has a
  calculator for this.
- **SkinSwap (Trade) min is an estimate**, not real data (pulse does not have the
  Trade page's asking prices). It shows as "(min, estimated)" in amber, with ≈
  before each price: what Trade pays plus the markup measured on 11 skins
  (about 1.6 times under $1, 1.19 times from $5; the $1–$5 range is the least
  certain). Asks differ per copy, so the real price can be about 10% lower. Skins SkinSwap barely wants (Trade pays under 60% of its Market
  price, or under $0.10) get no estimate. Cross-Profile and Store Catalogue
  Arbitrage leave the estimate out. Check the Trade page before you buy.
- Picking any SkinSwap profile shows a card under the profile picker with these
  rules, a Trade → real dollars calculator, the "worth it" check and links.

**Sell and buy back: on hold.** The idea was to sell items you own on the Trade
page and buy them back cheaper on the Market (**SkinSwap (Market) → SkinSwap
(Trade) (autobuy)** with **My Inventory**). The live test showed the sale money
stays on the Trade page, so the buy-back cannot happen at once. Don't do it until
it is known whether that money reaches the Market page after the trade protection.
If it does, check before each swap:

- the Trade page's offer for the whole batch (SkinSwap can lower its price as
  its stock grows);
- the Market price for that many copies (Huginn shows only the cheapest);
- peer-to-peer (P2P) Market listings cost 1% more and take up to 12 hours; instant ones do not;
- the copies you get back may have other floats;
- items in a Storage Unit must be moved to the inventory first (Ratatoskr →
  Transfer).

**CSFloat buy orders** (CSFloat autobuy profiles): **Fetch buy orders** checks
the highest CSFloat buy order for every item you own (runs in the background,
needs Get all items first). **Test connection** checks CSFloat and the proxy.

## Harvest

"If I sold what I hold right now, at what I actually paid for it, what would I
make?" Every purchase you still hold (from Draupnir, oldest sold first) against
an autobuy market's instant offer.

- Pick the account (or all) and the market.
- A lot the market has no buy order for is left out and counted as "lots not
  bought there" (CS.MONEY Trade, for example, does not buy many capsules and
  cheap stickers). Pick more markets to see it priced elsewhere.
- **check** marks an offer that looks wrong (much above Buff163, or paid in
  site balance) — it is left out of the totals.
- **Profitable only** shows every lot that sells above what you paid. The
  count at the top counts only the ones in the totals; "+N not counted" are
  profitable lots whose offer needs checking or whose price is stale (not
  updated by its market for a day).
- "only N offers at this price" means you hold more copies than the market
  buys at that price: the rest may sell lower.
- The clock icon marks lots bought recently (the number of days is shown under
  the status bar): they may still be trade protected.
- The **Filters** button works as on Arbitrage, with **Price** on what you
  paid or what the market pays now, **Accounts** from Draupnir and
  **Collections** from Get all items.

## Case Arbitrage

Cases, capsules and souvenir packages across nine markets: the cheapest market
for each, the best flip after fees, liquidity and a 7-day trend. **Hot** marks
flips above your threshold. The page shows only the newest answer, so switching
categories quickly never brings back an older list.

**Alerts** sends a Telegram message (or a Discord/Slack webhook) when a
container is cheaper than CSFloat by your percentage; **Send test** checks the
setup. The alert is one "board" message that is updated as prices move.

The **Filters** button works as on Arbitrage: **Price** on the cheapest buy
price or the best flip's sell price, and **Accounts** / **Collections** for the
containers you hold (needs Get all items).

## Cross-Profile

For each item you hold (or every item), the best route from any buy market to
any autobuy market, across all accounts at once. **Markets & chains** lets you
choose the markets and build multi-step chains (for example LisSkins →
CS.MONEY trade → CSFloat).

The **Filters** button works as on Arbitrage and narrows the chain tables too:
**Price** on the buy or the sell price (a chain's first buy and last sell),
**Accounts** from Draupnir holders, **Collections** from Get all items.

## Price dips

One market's lowest listing against the price the item usually had over the last
30 days (pulse's 30-day median, or average), with how many sold last week.

- **Market**: Steam, Buff163, CSMoney, DMarket, Market.CSGO, Waxpeer and the
  others pulse keeps price history for. Steam's medians are the most reliable;
  Buff163's are noisy.
- **Under (dips)**: listed below its usual price, deepest first: candidates to
  buy. **Over (spikes)**: listed above it; with **My Inventory**, what you hold
  that sells above its usual price right now.
- **at least %**, **sold / week ≥** (on that market; Steam's count where pulse
  has none), **price ≥ $** and **Hide stale** (on by default) narrow the list.
- **Back to usual**: what you would make buying at the listing and selling at the
  usual price, after the market's fee from the Fees editor. **Sells now** is the
  market's highest buy order.

A median lags: an item that has been falling for a month looks like a dip. Check
the weekly sales and the price chart (Steam icon) before buying. Refresh gets
prices under a minute old.

## Pulse feed

The **Pulse feed** button (top right of Huginn, or /huginn/pulse) watches the
price feed; it changes nothing. It shows the active profiles, the keeper (running,
paused by the active hours, falling behind, when the Mac slept), the downloads
since start and how many reads were served from memory, every table with why it
is downloaded (an active profile, the Buff163 reference, the price history's
seed, or a **Fetch live data**), its age, downloads, reads and which pages read
it, and the background pages (slaves) with how many of their tables are in memory.

## CS2 Store Arbitrage

http://localhost:3000/huginn?view=store-arbitrage (the **CS2 Store Arbitrage** tab
on Huginn's page; the Store Catalogue's header links here). Which store items resell for more than the store
charges, laid out like Huginn's Arbitrage. A recommendation only: nothing is
bought or sold here, and **Buy** opens the item's guarded buy page.

- **Profile**: **In-game store → a market**, picked like Huginn's profiles.
  **autobuy** sells into the market's buy orders (money at once), **min** lists
  at its lowest listing (you wait for a buyer). Two **Best market** profiles take
  the best market on that side; **Cash only** leaves out markets that pay in
  their own balance. The default is **CSMoney (Trade) autobuy**. **Fees** is
  Huginn's Fees editor.
- **The CSMoney (Trade) column is the indicator**: what CSMoney (Trade) pays at
  once, as a share of the store price. At **100% or more** (set it in **BUY
  signal at … %**) the row shows **BUY**: it is worth buying in the store and
  selling where the profile's market pays most. CSMoney (Trade) itself pays in
  its trade balance, not money. **BUY signals** shows only those rows. An
  unconfirmed item never signals.
- Each row: picture, name (click to copy), the Steam icon (opens the Steam
  Community Market through pulse), Buff163 and CSFloat icons, how many you
  own (Huginn → Get all items), the store price, what the market pays after its
  fee (the price links to the market through pulse), profit, profit %, the
  indicator, the 30-day history and **Buy**. A blue line under the store price
  means one of your wallet currencies is cheaper (at today's exchange rate).
- **Profitable only**, a category, and **Filters** (price on the store price or
  the sell price, accounts and collections from Get all items, with chips) work
  as on Huginn's tabs.
- **Refresh** pulls every market on the board again now (a market pulled in the
  last minute is skipped); the status bar counts the market sides left and says
  how old the prices are. **Store prices … ago** reads the store's price list
  again (one account logs in to Ratatoskr for about 15 seconds; nothing is
  bought).
- **Every morning** after Get all items, the store's price list and every
  market are read again, and the profitable items are posted to the Huginn
  Telegram bot (see [Telegram](telegram.md)). **Telegram: daily post** switches
  that off; **Send now** posts today's list at once.
- **Steam pays into the Steam wallet**, which buys more store items, so a Steam
  profit is a wallet-to-wallet loop, not cash.
- A price far above Buff163's lowest listing is **crossed out**: it is one
  pattern's buy order, or one seller on a thin market asking what nobody pays.
- **unconfirmed**: markets ask at least 1.5 times the store price now, and did
  on most days tracked (or there is little history yet). These items are
  listed after all the others. If store copies could be resold, traders would
  have closed that gap, so they probably cannot be (today: the StatTrak Swap
  Tool). One purchase settles it. In the web inventory, "Tradable
  After …" means the normal hold and "Not Tradable" means never.
- **resold before**: you sold this item before (from Draupnir), so store copies
  can be resold.
- **30 days**: on how many tracked days the best price (crossed-out prices left
  out) beat the store price, with a small line of the best price per day
  (dashes: the store price). The history is read every hour, even while the
  page is closed, so a spike (like Flickshot in January) shows apart from a gap
  that lasts.
- Click a row to see every market and side (price, fee, what you get, profit,
  how many listings or buy orders, how it pays), your record (bought, sold,
  average sale after fees) and which accounts hold it.
- **N store items left out** lists what is not on the board and why:
  - case keys (keys bought from the store cannot be traded or sold since 2019);
  - the game license and the Armory Pass;
  - items that cannot be traded (Storage Unit, Charm Detachment Pack);
  - the Name Tag: Valve stopped Name Tags bought in the store from being traded
    (2026);
  - items no market prices.

> Store purchases cannot be traded for **7 days**, so a price has to hold for a
> week. The board recommends; it does not know the price next week.

## CS2 Store Boosts

http://localhost:3000/huginn?view=store-boosts (the **CS2 Store Boosts** tab on
Huginn's page; the Store Catalogue's header links here). It catches a **price boost** on the Steam Market on its first
day, for every store item and every sticker inside the store's capsules. An
alarm and a recommendation only: nothing is bought or sold here, and **Buy**
opens the item's guarded buy page.

A **price boost** is when someone suddenly buys an item on the Steam Community
Market in large portions and at higher and higher prices. They are "boosting"
the price so they can resell the same item on third-party marketplaces, which
follow Steam's price, at a profit.

For store items this opens a window. The store sells any number of copies at
one fixed price, so during a boost a store copy is worth far more than it
costs. Example: Sticker | Flickshot (in the store since 2015 at $0.99) was
boosted from 11 January 2026, and third-party markets paid around $7.55 a
week later. A few days after that it was back near the store price.

Store copies cannot be traded for **7 days**. The profit therefore depends on
buying on the first day of the boost, so the copies unlock while the price is
still high. Copies bought three days late unlocked after the peak.

A store **sticker capsule** can be boosted in two ways: directly, or through one
sticker inside it. In October 2026, Sticker | World Eaters 1 from the Warhammer
40,000 Traitor Astartes capsule ($0.99 in the store) went from about $0.20 to
over $20 on Steam. The capsule followed about a day later, up to about $3. So
CS2 Store Boosts watches the stickers inside each capsule too.

**How it reads.** Every hour it reads Steam's and Buff163's prices of every
watched item from pulse. Steam's own price history (hourly prices and sales of
the last 30 days) is read one item at a time, at least 30 seconds apart:
first the items pulse flags (Steam's buy orders 1.3 times their 30-day median,
or the lowest listing 1.5 times) or you ask for, then items in a boost every
hour, store items every 3 hours, capsule stickers every 12 hours. When Steam
says "too many requests", it waits 10 minutes, then longer.

**The levels.** "Usual" is the median day of the last 30 days, leaving out the
last 3. Sales alone never raise the alarm: the Traitor Astartes capsule had
about 30 sales spikes with no price move before its boost.

- **Watch**: price at least 1.2 times the usual and at least 3 times the usual
  sales. An early sign.
- **Boost**: price at least 2 times the usual with at least 3 times the usual
  sales, or still within 70% of a recent peak. Buy in the store now.
- **Second wave**: after a peak, the price climbed 1.3 times off its low again.
- **Fading**: under 70% of the peak, still above the usual price. Sell what you
  hold before third-party markets follow Steam down.

Tested on the real histories: Flickshot was flagged on 11 January (its first
day), World Eaters 1 on the evening of 3–4 October, its capsule on 6 October,
and none of them raised an alarm in the quiet weeks before.

**The page.**

- The four cards at the top: how many items are at each level (and capsules
  with a moving sticker, items flagged by pulse, confirmed boosts); the Steam
  reader (what it reads now, how much of the list was read recently, the
  seconds between reads, which you can change); the hourly pulse check (**Check
  now**); and the alerts.
- **Track** (the pin next to any item or capsule sticker) puts it in the
  **Tracked** panel on top, with its level, price and sales against the
  usual, Buff163's listing, a 7-day chart and when it is read next. It is
  read on the same schedule as every other item (a sticker in a boost every
  hour, store items every 3 hours, capsule stickers every 12 hours; **Read
  Steam now** reads it at once). Any Steam Market item
  can be tracked; one the store does not sell gets messages but never rings
  the phone. The crossed pin stops tracking.
- **Moving now** shows items at any level, capsules with a moving sticker and
  items pulse flagged; **Capsules** and **All** show more. Search finds capsule
  stickers too.
- Each row: the level (and the level inside a capsule), the store price,
  Steam's price of the last 6 hours and how many times its usual it is, the
  sales of the last 24 hours against the usual, a 7-day chart (price line,
  sales bars, the store price in amber dashes, the usual price dotted), and
  **Confirmed**: what a market pays at once in money after its fee (green at
  or above the store price), or for a capsule its opening value.
- Click a row: Steam's hourly chart of the last 14 days (hover for any hour),
  the usual price, peak, low since the peak, when the rise started, **when
  copies bought now become tradable**, Steam's lowest listing, buy orders and
  30-day median, Buff163's listing and **Read Steam now**.
- A capsule's row opens its stickers: rarity, the chance one capsule gives each
  (High Grade 80%, Remarkable 16%, Exotic 3.2%, Extraordinary 0.64%, shared
  inside each rarity), its level, Steam price and sales, Buff163's price, and
  the **opening value**: each sticker's chance times its price, on Buff163
  listings and on Steam buy orders, against the store price.

**Alerts.** A message to the Huginn Telegram bot for each new level of a boost
(watch, boost, second wave, fading), each once per boost; a boost ends after 3
calm days. A **boost** also **rings the phone**, like Gjallarhorn's news alert.
A sticker's message names its capsule and the capsule's opening value. An item
seen for the first time is only recorded, unless its rise began in the last 48
hours, so switching it on does not flood you with old boosts. **Test message**
and **Test ring** try both. The switches: **Watch for boosts** (everything),
**Telegram message**, **Ring the phone on a boost**.

> Unknown yet: whether stickers opened from a store capsule keep the 7-day
> hold. One capsule bought and opened settles it.
