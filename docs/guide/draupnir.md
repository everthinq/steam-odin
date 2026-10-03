# Draupnir — portfolio tracker

Draupnir records every skin you buy and sell, per portfolio (usually one per
account), and shows what your holdings are worth now and what you made.

Open it from the Dashboard tile **Draupnir** (also /draupnir). Live values need
the Tradeon token ([getting-started.md](getting-started.md#4-prices-huginn-and-draupnir));
without it, holdings are shown at cost.

## Portfolios page

- **New portfolio**, **Import CSV** (one file = one new portfolio; exports from
  common skin price trackers store prices in cents and are converted), and
  per portfolio **Export** (CSV), **Open**, **Delete** (asks first; deletes all
  its transactions).
- Views: **Per account** (cards; drag to reorder, pin), **Combined** (one
  ledger across all accounts), **Arbitrage** (the deals you tagged as
  arbitrage, split into real-cash Market arbitrage and Steam-wallet arbitrage).
- **Value on**: the market used for current value (Steam, CSFloat, Buff163, or
  the lowest of them). Prices fill in after a few seconds; pages never wait for
  them.

## One portfolio

Tiles: current value, cost basis, unrealized, realized and total profit/loss.

**Add a transaction:** item name (suggestions as you type; picking one fills
the current price), Buy or Sell, quantity, unit price in dollars, platform,
date, note, and the **Arbitrage** tag. An unknown item name asks before saving.
**Drip last** copies the newest transaction into the form; after adding,
platform and date are kept for the next one.

Holdings and transactions can be searched, filtered by collection and sorted;
every transaction can be edited or deleted.

**How profit is counted:** moving average — each sell is measured against the
average price of the units held at that moment. Fees are not deducted.

## Backups

`portfolios.json` cannot be rebuilt from anywhere else, so Draupnir keeps
snapshots by itself: after every change, at every start and once a day. Old
snapshots are thinned (all for 7 days, daily for 90 days, weekly for 2 years).

**Backups** (Portfolios page) lists them: **Snapshot now**, **download** any,
**Restore** one — the state before a restore is saved first, so a restore can
itself be undone.

These snapshots live on the same computer. For an off-machine copy, see
[backups-and-safety.md](backups-and-safety.md).
