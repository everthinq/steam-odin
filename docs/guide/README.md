# steam-odin user guide

steam-odin is a toolset for running many Steam accounts that trade
Counter-Strike 2 items: a Steam Guard authenticator, a portfolio tracker,
price scouts, a card-farming deal finder, a Storage Unit mover and shop — all
in one web app on your own computer, at **http://localhost:3000**.

This guide explains what every screen does and how to use it safely. It is
written for the person running the app, not for programmers (they start at
[ARCHITECTURE.md](../../ARCHITECTURE.md)).

## Start here

1. [Getting started](getting-started.md) — install, first run, import your accounts.
2. [Accounts and confirmations](accounts-and-confirmations.md) — the Dashboard,
   Steam Guard codes, trade and Market confirmations, auto-confirm.
3. [Backups and safety](backups-and-safety.md) — **read this before you rely on
   the app**: what must be backed up, and what is never shared.

## The tools

| Tool | What it is for | Guide |
|------|----------------|-------|
| **Huginn** | Price scout: arbitrage between markets, case prices, Harvest (what your holdings fetch now), LOOT.Farm | [huginn.md](huginn.md) |
| **Gjallarhorn** | When Valve limits a case: what to sell, what to buy, and a phone alarm on the news | [gjallarhorn.md](gjallarhorn.md) |
| **Andvari** | Games whose trading cards pay for them; buys them on many accounts; card farming and selling | [andvari.md](andvari.md) |
| **Team Fortress 2** | New Team Fortress 2 case drops: play, then sell the drops | [team-fortress-2.md](team-fortress-2.md) |
| **Draupnir** | Portfolio tracker: buys, sells, profit and loss, backups | [draupnir.md](draupnir.md) |
| **Mímir** | Encrypted vault for account logins, passwords and emails | [mimir.md](mimir.md) |
| **Ratatoskr** | Move items in and out of Storage Units; buy Storage Units on many accounts | [ratatoskr.md](ratatoskr.md) |

Plus: [Telegram alerts and phone rings](telegram.md) and
[Troubleshooting](troubleshooting.md).

## Words used in this guide

| Word | Meaning |
|------|---------|
| **maFile** | The Steam Guard file for one account (from Steam Desktop Authenticator or similar). It holds the secrets that make login codes and approve confirmations. Losing it can lock you out of the account. |
| **Confirmation** | Steam's "confirm on your phone" step for a trade or a Market listing. |
| **Storage Unit** | A Counter-Strike 2 item that holds up to 1,000 other items, so the inventory stays small. |
| **Game Coordinator** | Valve's server for Counter-Strike 2 itself (inventory, Storage Units, the in-game store), separate from the Steam website. |
| **Autobuy market** | A site that buys your item instantly at a fixed price (as opposed to listing it and waiting). |
| **Trade protection / trade hold** | The 7 days after a trade or purchase during which an item cannot be traded again. |
| **ASF** | ArchiSteamFarm, a well-known open-source program that "plays" games so their trading cards drop. |
| **Dry run** | A test purchase that goes through every check and stops before paying. |
