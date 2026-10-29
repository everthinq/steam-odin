# Mímir — credential vault

Mímir stores each account's **login, password, email, a comment and a billing address**,
encrypted with the same key as the maFiles. The app reads passwords from here
whenever it has to log in by itself (renewing confirmations, Ratatoskr, ASF,
purchases). maFiles do not contain passwords, so **every account the app should
manage needs its password in Mímir**.

Open it from the Dashboard tile **Mímir** (also /mimir).

## Using it

- **Add credential** — one account by hand.
- **Import** — paste many lines of `login;password;email;comment`. Existing
  logins are updated, new ones added. Passwords containing `;` or `@` are
  handled.
- The list is split into **With maFile** (the app can log in) and **No maFile**.
- Per row: copy login, **reveal** the password (hides again after 30 seconds),
  copy password without showing it, copy email, **Test login** (logs in once and
  records the result as the coloured dot), edit, delete.
- **Hide all** hides every revealed password.
- **Billing address** (in the edit form, several lines): the address Steam asks some accounts
  for before a web purchase. Shown under the comment, found by the search. Heimdall never sends
  it to Steam, and the text **Export** does not include it (the vault file and its backups do).

## Market access

The **Market access** button checks, one account at a time (about 90 seconds for 21), how long
each account can still use the Steam Community Market. Steam keeps the Market open only while
the account has a real purchase between 7 days and one year old: a game, DLC, a wallet top-up or
an in-game item such as a Storage Unit or a capsule. Community Market transactions (buying or
selling), free promotional games and refunded purchases do not count.

- The **Market access** column shows "Market until <date> · N days": green, amber within 30 days
  of the date, red once it lapsed or when Steam's own check says no. Hover for the last counted
  purchase, Steam's answer and when it was checked.
- A banner lists the amber and red accounts, soonest first, and any account that could not be
  checked (no web session, Steam unreachable).
- The result is kept, so the dates count down without checking again; press it again after
  buying something. The Dashboard shows the same result on each account card.

### Market unlocker

The cheapest purchase that keeps the Market open is a one-cent in-game item: the **Market
unlocker** button buys Zombie Hunter's "Editor Pack" ($0.01) on the accounts you tick (accounts
that lose the Market within 30 days are ticked for you). The free game is added first when the
account does not own it.

1. **Dry run** goes as far as Steam's approval page, checks the account, the item, one piece and
   a price of at most $0.05, and cancels. It does claim the free game first when the account
   lacks it (a free license, nothing paid). The last column then says "ready" or why not.
2. **Buy for real** asks you to type `BUY <number of accounts>`. It approves, then checks that
   Purchase History lists the purchase and the account's date moves a year on. If something fails
   after approving, the row says **MAY BE PAID**: look at the account's Purchase History.

Steam asks some accounts for a **billing address** before any web purchase: log in to that
account in a browser once, open any purchase and fill the address in (the one kept in the
account's Mímir row); Heimdall never does. An
account with an empty wallet cannot pay the cent either.

Details: [steam-market-access.md](../internals/steam-market-access.md).

## Export — handle with care

**Export** downloads the whole vault as a **plain text file**, passwords
readable. Use it only to move to another vault, keep the file off cloud sync,
and delete it afterwards.

## Behind the scenes

Every change keeps a backup copy of the vault next to it (the newest ten).
The vault file is `maFiles/credentials.vault`; it is useless without the
encryption key — back up both (see [backups-and-safety.md](backups-and-safety.md)).
