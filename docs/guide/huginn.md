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
