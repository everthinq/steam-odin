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
buy-order check; the header shows "daily HH:MM ✓" when that went well.

## Fees — set once, used everywhere

**Fees** (Arbitrage view) holds each market's sell fee. Every profit figure in
Huginn, Harvest and Gjallarhorn uses these numbers. Your number always wins.
A market you never changed uses our confirmed fee, or else Tradeon pulse's own
fee for it, tagged **pulse**; **yours** marks a fee you changed. ⚡ marks a
market with buy orders (you can sell there instantly). After changing fees,
fetch the profile again.

## Arbitrage

1. Pick a **profile** — a buy market → sell market pair (for example "Buff163 →
   CSFloat autobuy"). Each "Buy on …" section lists **⚡ Sell instantly · buy
   orders** first (autobuy: the market buys from you at once), then **List for
   sale · lowest price** (min: you list the item and wait for a buyer). **Instant
   sell only** hides the second kind. Every pulse market is there, 40 in all.
   An ⓘ marks GgSwap, GamerPay and SkinSwap (Trade): the pulse website does not
   show them and their prices can be old, so check the market before you trade.
   SkinSwap has two sides; see [SkinSwap](#skinswap-market-trade-and-one-balance) below.
2. Press **Fetch live data**.
3. The table lists items with buy price, sell price, profit and profit %, how
   many you own and on which accounts, and links to each market.

Filters: search (wear shorthand works: `fn`, `mw`, `ft`, `ww`, `bs`), **My
Inventory**, **Hide Unstable** (LOOT.Farm overstock), collection. Results are
kept until you reload the page.

### SkinSwap: Market, Trade and one balance

SkinSwap keeps **one balance** and shows it two ways: the Trade page shows it
1.4 times higher than the Market page (its 40% bonus). There is no conversion
button: sell on the Trade page, open the Market page, and the money is already
there, divided by 1.4.

- **SkinSwap (Market)** is where you buy, in real dollars.
- **SkinSwap (Trade)** buys from you. Huginn divides its prices by 1.4, so they
  are real dollars too: $35.35 on the Trade page is $25.25 in Huginn.
- **SkinSwap (Trade) min is an estimate**, not real data (pulse does not have the
  Trade page's asking prices). It shows as "(min, estimated)" in amber, with ≈
  before each price: what Trade pays plus the markup measured on 11 skins
  (about 1.6 times under $1, 1.19 times from $5; the $1–$5 range is the least
  certain). Skins SkinSwap barely wants (Trade pays under 60% of its Market
  price, or under $0.10) get no estimate. Cross-Profile and Store Catalogue
  Arbitrage leave the estimate out. Check the Trade page before you buy.
- Picking any SkinSwap profile shows a card under the profile picker with these
  steps, a Trade → Market calculator and links to both pages.

**Sell and buy back (keep the item, pocket the gap).** Pick **Buy on SkinSwap
(Market) → SkinSwap (Trade) (autobuy)**, press Fetch live data and turn on **My
Inventory**: each row is an item you own that Trade pays more for than the
Market sells it. Sell your copies on the Trade page and buy the same number back
on the Market; you keep the items and the gap stays as SkinSwap balance. Before
each swap check:

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

## Case Arbitrage

Cases, capsules and souvenir packages across nine markets: the cheapest market
for each, the best flip after fees, liquidity and a 7-day trend. **Hot** marks
flips above your threshold.

**Alerts** sends a Telegram message (or a Discord/Slack webhook) when a
container is cheaper than CSFloat by your percentage; **Send test** checks the
setup. The alert is one "board" message that is updated as prices move.

## Cross-Profile

For each item you hold (or every item), the best route from any buy market to
any autobuy market, across all accounts at once. **Markets & chains** lets you
choose the markets and build multi-step chains (for example LisSkins →
CS.MONEY trade → CSFloat).

## Harvest

"If I sold what I hold right now, at what I actually paid for it, what would I
make?" Every purchase you still hold (from Draupnir, oldest sold first) against
an autobuy market's instant offer.

- Pick the account (or all) and the market.
- **check** marks an offer that looks wrong (much above Buff163, or paid in
  site balance) — it is left out of the totals.
- The clock icon marks lots bought less than 8 days ago (may still be trade
  protected).

## LF Auctions and LF Arbitrage

LOOT.Farm views: live auctions against Steam resale (**Snapshot** records the
current lots to learn how auctions clear), and buying LOOT.Farm balance cheap
to buy items there and resell elsewhere. "Balance @ +%" is the rate you pay for
balance; "Unlocked +3%" counts the surcharge for trade-unlocked items.
