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
Huginn, Harvest and Gjallarhorn uses these numbers. "assumed" marks a market
whose fee has not been confirmed — check it. After changing fees, fetch the
profile again.

## Arbitrage

1. Pick a **profile** — a buy market → sell market pair (for example "Buff163 →
   CSFloat autobuy").
2. Press **Fetch live data**.
3. The table lists items with buy price, sell price, profit and profit %, how
   many you own and on which accounts, and links to each market.

Filters: search (wear shorthand works: `fn`, `mw`, `ft`, `ww`, `bs`), **My
Inventory**, **Hide Unstable** (LOOT.Farm overstock), collection. Results are
kept until you reload the page.

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
