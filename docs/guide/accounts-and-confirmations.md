# Accounts and confirmations

## The Dashboard (http://localhost:3000)

- **Tool tiles** at the top open each tool.
- **Account cards** — one per imported account:
  - the **Steam Guard code**, hidden until you press the eye (it hides again
    when the code changes); copy works while it is shown; the bar shows how long
    the code stays valid (yellow under 10 s, red under 5 s);
  - **Ratatoskr** — move items in and out of Storage Units for this account;
  - **Confirmations** — this account's pending trade and Market confirmations;
  - **pin** to keep a card first, **drag** the grip to reorder (saved in this
    browser), **trash** to remove the account (you must type its name).
- **Search** filters the cards by account name.
- **Import** adds accounts (see [getting-started.md](getting-started.md)).
- **Remove All** removes every account; you must type `DELETE <number>`.
  Removed maFiles are moved to `maFiles/.deleted/`, not destroyed — but without
  a backup, treat removal as final.
- The **eye button** at the top right hides the whole interface (only the
  background stays) — handy when sharing your screen.

## Confirmations

Steam asks you to confirm every trade and every Market listing on your phone.
Heimdall does the same with the account's maFile.

**One account:** card → **Confirmations**. Each pending item shows its type
(Trade, Market Listing, …) and summary. **Approve** and **Deny** act
immediately — there is no second question, so read the card first. Press
**Refresh** to reload; the page does not refresh by itself.

**All accounts, automatically:** Dashboard → **Confirms** (gear):

| Setting | Effect |
|---------|--------|
| Automatic Checking | Check every account on a timer |
| Check interval | Seconds between checks (default 300) |
| Auto-confirm Market Listings | Approve every Market listing confirmation by itself |
| Auto-confirm Trades | Approve every trade confirmation by itself |
| Check All Now | Run one check over every account right now |

Press **Save Settings**. Account recovery, phone-number changes and other
sensitive confirmation types are never approved automatically.

> **Careful with Auto-confirm Trades.** With it on, any trade offer an account
> sends is approved without you looking. Only switch it on if nothing else
> (no other program, no browser session you do not control) can create trades
> on these accounts. Each auto-confirmed trade can send you a Telegram message
> (see [telegram.md](telegram.md)).

## Staying logged in

Confirmations need a web session that Steam expires about once a day. The app
renews it by itself every few hours with a full login (password from Mímir,
code from the maFile). If confirmations stop working for every account at
once, see [troubleshooting.md](troubleshooting.md#confirmations-stopped-working).
