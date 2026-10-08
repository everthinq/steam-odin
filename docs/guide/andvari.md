# Andvari — games whose trading cards pay for them

Many cheap Steam games drop trading cards when played. If an account's card
drops sell on the Steam Market for more than the game costs in that account's
region, buying the game is free money. Andvari finds those games for every
account, buys them from the Steam wallet if you say so, has ArchiSteamFarm play
them until the cards stop dropping, and can sell the cards.

Open it from the Dashboard tile **Andvari** (also /huginn/card-deals).

## Deals

- **Scan on sale** / **Scan sale + full price** looks up games and their card
  prices for every account (runs in the background; it also runs by itself
  every 12 hours). **Check buy orders** refreshes only the card prices.
- The table: game, regional price, how many cards drop, what the cards net
  after fees, the worst case, the profit, the best account, and how many
  accounts can buy it. Open a row to see who can buy it, who owns it, and each
  card's price.
- **Buy orders / Listings**: value cards at what buyers pay now (safe) or at
  the lowest listing (optimistic).
- Tiles: **Accounts** (country, games owned, drops left), **Card farming** and
  **Card selling** open their panels below.

## Buy games — spends wallet money

1. Enter games (store links or app ids) and the **maximum price per game** in
   dollars.
2. **Check accounts** builds a plan: every account × game, with its wallet,
   country and whether it can buy. Tick the cells you want.
3. **Dry run** goes through the whole checkout and cancels before paying.
4. **Buy N on M accounts** asks once more, then buys.

Protections: the plan expires after 30 minutes; every account's final checkout
price must equal the plan **to the cent** or nothing is bought; **Empty the
cart first** clears anything else from the Steam cart. After a purchase, ASF
starts farming the new game.

## Card farming (ArchiSteamFarm)

Needs ASF set up ([getting-started.md](getting-started.md#5-card-farming-optional-for-andvari)).
The panel shows each account's ASF state. An account's bot is switched on only
while it has cards left to drop (at most 20 at a time; the rest wait as
"Queued") and off again when it is done. "Done" is double-checked against the
account's badges page first, and a bot that loses its Steam connection mid-farm
stays on until it is back, so a network outage never leaves drops unfarmed.

| Button | Does |
|--------|------|
| ⚡ Farm now | Look for drops now (after buying a game) |
| ↺ Retry login | After "Needs you": try the login again |
| ⏸ Pause / ▶ Resume | Hold or release this account's bot |

ASF is paused automatically while Ratatoskr uses the account (Steam allows one
game session per account). **ASF UI** opens ASF's own screen; `make
asf-password` copies its password to the clipboard. Do not set a "master"
account or run `loot` there — see [Yggdrasil/asf/README.md](../../Yggdrasil/asf/README.md).

## Card selling — lists cards on the Market automatically

**Off by default.** When **Sell dropped cards automatically** is on, every
trading card that drops **after you switched it on** is listed on the Steam
Market at the **highest price that still sells**, and the listing is confirmed
automatically — only that listing, matched by the card's exact name.

The price is patient: it waits for buyers instead of racing to the bottom.
Andvari reads the card's last 7 days of sales and goes as high as the price 90%
of those sales fetched, as long as no more than 3 days' worth of sales are
listed cheaper ahead of it, placing it one cent under the next group of
listings. It is never lower than one cent under the lowest listing, nor under
the highest buy order. When a card has too few sales to judge, it is listed one
cent under the lowest listing. Hover over "Listed" in the sales table to see why
a price was chosen.

| Option | Effect |
|--------|--------|
| Also cards held before it was switched on | Sell older cards too (off by default) |
| Foil cards too | Include foil cards |
| Only these games | Limit to these app ids |

The checkboxes **save at once**. The panel shows sales per account and game,
and "bought with Buy games vs cards listed" so you can see whether a game paid
for itself.

## Andvari's Telegram bot

Deal alerts and card-sale summaries go **only to Andvari's own Telegram bot**
(Settings → Andvari bot token + chat id), never to the Huginn arbitrage bot.
Without its own bot, Andvari sends nothing. **Test alert** checks it. See
[telegram.md](telegram.md).

## Settings

Scan automatically and how often, include full-price games, maximum game
price, minimum discount, which card value the alerts use, minimum return % and
profit for an alert, and the fallback country for accounts whose country is
unknown.
