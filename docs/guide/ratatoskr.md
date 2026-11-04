# Ratatoskr — Storage Units

Ratatoskr logs an account into Counter-Strike 2's Game Coordinator (as if the
game were running) so items can be moved in and out of **Storage Units**, so
Storage Units can be bought from the game's own store, and so the store's price
list can be read.

## One account: transfer, auto-store, rename

Dashboard → account card → **Ratatoskr** (opens Transfer).

**Connect** in the left sidebar logs the account in (password from Mímir, code
from the maFile; the account's ASF card farming pauses meanwhile). "Online •
Stable" means ready. **Auto-disconnect when idle** frees the session after
15 minutes … never. **Disconnect** ends it.

### Transfer

1. Choose **To** (inventory → a Storage Unit) or **From** (Storage Units →
   inventory).
2. **To:** there is nothing to pick — your Storage Units count as one
   storage. The **Storage space** panel shows how many places are free across
   them, how many of your units have room, how many items you selected and how
   many places are left after the move (red, and Move disabled, when the
   selection does not fit; **Buy Storage Units** is next to it). The move fills
   the fullest unit with room first, then the next, so it touches as few units
   as it can and the emptier ones stay free. After the move the message says
   where the items went ("Stored 60 items: Temp0 · 6, Random investments1 · 42 …").
   **From:** pick one, several or **All storage units**. The pencil renames a
   Storage Unit (free, up to 20 characters; in From mode).
3. Select items: by group with a quantity, by float, select all; filter by
   collection or search. "N LEFT" shows the free places left after the move.
4. Open the queue (**N ITEMS**), set the delay between items if needed (200 ms by default), press
   **Move**. Moves start immediately — no second question — and run one at a
   time with a pause between them; one progress bar follows the whole move,
   even when it spans several Storage Units. The move runs in Heimdall's
   backend: closing or reloading the page does not stop it.
5. Each item counts as moved only when the game confirms it. If the game
   refuses an item (it stays where it was), the message names it. If the
   connection to the game drops during the move, the items without an answer
   are "not confirmed": refresh the page to see where they are.

**Tradable now and trade hold** are separate rows: when you hold copies of an
item that can be traded now and copies still on trade hold (a fresh purchase,
a trade), the item gets two rows next to each other — "Tradable now" (green)
and "Trade hold · 6–7d" (orange, the time left, in hours under a day).
Two kinds of hold look alike on Steam but differ for Storage Units:

- **Trade hold** (orange) — e.g. an item **bought on the Steam Community
  Market**: it cannot be traded or sold yet, but it **can go into a Storage
  Unit**. Selectable as usual.
- **Trade protected** (red) — an item **received in a trade with another
  player**: it **cannot go into a Storage Unit** until the protection ends (a
  game rule). In To mode its row shows how many you have but cannot be
  selected, **Select all** skips it, and Move would leave it out anyway. Hover
  the select button to see how long is left.

Each kind gets its own row, under the "Tradable now" row of the same item. A held case or sticker opens
(the arrow) into one line per unlock time ("Unlocks Thu 6 Nov, 08:00"), so you
see when each batch frees up; a held skin opens into its floats, each with its
hold. A held
row whose copies all unlock at once says when under its name. The queue and
the CSV export repeat the hold. The page checks the holds every minute: copies
whose hold ends while the page is open move to the "Tradable now" row by
themselves.

**Show items that can't be stored (N)** (To mode):
untradable items — service medals, veteran and operation coins, Loyalty Badge,
Global Offensive Badge, the default music kit (Music Kit | Valve, CS:GO) — can
never go into a Storage Unit, so they are hidden by default. N is how many there
are. Switch it on to see them, greyed and marked "Can't be stored"; they can
never be selected or moved. The choice is remembered in this browser. Which
items count comes from the game's own item data (Ratatoskr's `item_moveable`),
not from a list of names.

**One limitless storage (To mode).** You never deal with single Storage Units
when putting items away: all of them together act as one storage, as big as
their free places combined. Pick items and counts, press Move.

How it works underneath — worth knowing, nothing to do:

- Every Storage Unit still holds at most **1,000 items**; the game only ever
  moves an item into one specific unit. The "free" number is the sum of the
  free places of every unit (1,000 minus what each holds).
- At Move, Heimdall splits your selection over the units: the **fullest unit
  with room first**, then the next fullest, until every item has a place. So a
  move touches as few units as possible and your emptier units stay free for
  later. Full units are skipped.
- Each unit's share is sent as its own batch, one after another, with the same
  pause between items as always (no extra speed, no extra risk of Steam
  limits); the progress bar adds them up into one.
- The backend makes this plan, not the page — the same plan Auto-Store uses —
  and runs it once, even if Move is pressed twice.
- Only what a Storage Unit takes is ever sent: never items it can never hold
  (medals, coins, badges, the default music kit) and never trade-protected
  copies (received in a trade). Market purchases on a trade hold are sent.
- If the selection is bigger than the free space, nothing is sent: the panel
  turns red, says how many items are too many, and Move stays disabled until
  you remove some or buy more Storage Units.
- The unit counts are read again after every move, so the next plan uses the
  real numbers. The game's own count lags a few seconds behind a move; Heimdall
  counts what it just stored meanwhile, so a quick second move never over-fills
  a unit.

**Move one of each to inventory** (From mode) takes one copy of every
different skin out of storage — useful so price websites that read your
inventory can see what you hold — and reports what moved.

**Export** downloads the current list as CSV.

### Auto-Store

Moves the items you watch (exact names, any letter case, for example a case you
buy on the Steam Market) from the inventory into your Storage Units, every
confirmation-check cycle.

- **Each account has its own watchlist.** Items you watch on one account are
  never stored on another. Switch Auto-Store on for each account you buy on, one
  by one; the **master switch** pauses or resumes every account at once.
- **Suggestions** are the small "+ name" buttons under the Add box: the items in
  this account's inventory that a Storage Unit can hold and that you do not watch
  yet. One click adds that item to the watchlist; typing filters them.
- **Sweep now** runs it once for this account only, and shows the move until it
  ends.
- **All accounts** (the second tab) is the master watchlist: every account's
  watchlist combined, each item with the accounts that watch it and how many were
  moved. Add an item for a chosen account there, or remove one account from an
  item; switch each account on or off in its Accounts list. Greyed accounts are
  switched off.
- The history shows every confirmed move (this account's on its tab, all on the
  master tab).

It uses the very same move as Transfer's To mode, so the rules are identical:
the Storage Units are one storage, the **fullest unit with room fills first**;
items **bought on the Steam Market** are stored even during their trade hold;
items **received in a trade** (trade protected) and items a Storage Unit can
never hold are skipped. Items that do not fit anywhere wait for the next sweep.
The history lists only moves the game confirmed; an item the game refused is
not tried again for 6 hours (one that simply was not sent, for example while
Ratatoskr restarted, is tried again at the next sweep).

### Rename Storage Unit

The third tab lists every Storage Unit of the account: its name, how many items
it holds and its free places. Units **without a name** come first, marked "no
name": the game refuses items into them until they have one.

- **Rename** opens a field on the row; Enter (or ✓) saves, Escape cancels.
  Renaming is free in the game.
- A name is 1 to 20 characters, and two units of one account never share a
  name (upper and lower case count as the same).
- **Name them "Storage N"** (shown only when some units have no name) first
  lists the names it would give, numbered on from your highest "Storage N";
  **Execute plan** names them one after another.
- Nothing is renamed while items are moving on that account (the game gives a
  renamed unit a new id, which would break the move): wait for the move to end.

## Buy Storage Units

http://localhost:3000/buy-storage-units (Dashboard tile **Buy Storage Units**,
or the Ratatoskr sidebar; the old address `/storage-shop` still leads here). Buys Counter-Strike 2 Storage Units from the Steam wallet,
on as many accounts as you like, **without connecting anything first**.

> **This spends real wallet money.** A Storage Unit costs $1.99 (or the local
> price). The shop refuses anything above $2.50 a unit.

1. **Check wallets & Storage Units** reads every account's wallet, country and
   how many Storage Units it already has (about 8 seconds per account; nothing
   is bought). The table shows each with its age; **0** means none. Click a
   column header (Account, Wallet, Storage Units, Can buy) to sort by it —
   again to reverse, a third time for the dashboard order. Wallets in different
   currencies are compared in US dollars; unknown values sort last.
2. Tick accounts (row click toggles; **Can buy** filter shows only those whose
   wallet covers at least one) and set the quantity (1–20 each).
3. **Review & buy** re-reads the chosen wallets and shows the order: per account
   quantity, price, total, wallet now → after, and any problem. A review is
   valid for 30 minutes, counted from the first passed test; testing again
   does not extend it (it says until when); after that it offers **Review
   again** instead of Pay.
4. **Test without paying** runs every step, opens the purchase and cancels it
   before approving — nothing is paid. It is required: then the review comes
   back and says, per account, whether its test passed.
5. **Pay** (offered on the same review once the test passed) buys — only on
   the accounts whose test passed; the others are skipped. Each account goes through wallet → login → opening →
   approving → delivering; you can close the page, the purchase continues on
   the server. Opening the page again (or another tab) shows the running
   purchase with every account, and the result stays until you close it.
   Pressing Pay twice never buys twice.

**If a line says "MAY BE PAID"** the approval went through but delivery was
not confirmed. Check the account's purchase history on Steam, and press
**Deliver again** — it can only deliver a purchase already approved, so it can
never charge twice.

Everything the shop checks before paying: the plan (or its passed test) is
under 30 minutes old;
the wallet currency and balance are unchanged; the game store's price equals
the plan; Steam's own approval request is exactly this order (only Storage
Units, this quantity, this total); the approval page names this account and
shows this total. If any check fails, nothing is paid and the opened purchase
is cancelled.

How it works inside: [docs/internals/storage-shop.md](../internals/storage-shop.md).

## Store Catalogue

http://localhost:3000/store-catalogue (Dashboard tile **Store Catalogue**, or the
Ratatoskr sidebar). Every item the Counter-Strike 2 in-game store sells — about
250: tools such as the Storage Unit and Name Tag, case keys, sticker capsules,
stickers, music kits and music kit boxes, graffiti boxes, pins, patch packs, the
game license and the Armory Pass — with the game store's price in US dollars.
Nothing is bought on the list itself.

- Search by name, pick a category, or tick **Store front only** to see just
  what the in-game store front shows (case keys, the game license and the
  Armory Pass are sold but not shown there; cases shown there only link to the
  Steam Market).
- Click a column header (Item, Category, Store front, Price) to sort; again to
  reverse, a third time for the category order.
- The Steam icon opens the item's Steam Market page, to compare with the
  store price.
- The prices are refreshed every time **Buy Storage Units** reads the store.
  **Read prices again** reads them now: one account logs in to Ratatoskr for
  about 15 seconds and out again. It waits while Buy Storage Units is checking
  wallets or buying.
- An item marked **new** is missing from Ratatoskr's item list, so its name is
  made from the store's internal name. Refreshing the item list fixes it (see
  [Ratatoskr's agent guide](../../Yggdrasil/ratatoskr/CLAUDE.md)). It cannot be
  bought until then.

### Buying any store item

Every item except the game license and the Armory Pass has a **Buy** button.
It opens that item's own buy page (`/store-catalogue/buy/<item>`), which works
exactly like [Buy Storage Units](#buy-storage-units): tick accounts, set the
quantity (1–20 each), **Review & buy**, **Test without paying** first, then
**Pay**. The same checks apply, with the item's own price limit: a unit is
refused above its US dollar price × 1.25 in any currency (the header shows the
limit), and Steam's approval request must name exactly this item. There is no
Storage Unit column; a finished purchase reports how many items the store
delivered. The purchase log on every buy page lists every item bought.

> **This spends real wallet money**, like Buy Storage Units. A purchase from the
> game store is final.

### CS2 Store Arbitrage and CS2 Store Boosts

They moved to Huginn (2026-11-04): they only read prices and never buy. See
[CS2 Store Arbitrage](huginn.md#cs2-store-arbitrage) and
[CS2 Store Boosts](huginn.md#cs2-store-boosts). The Store Catalogue's header links to
both, and the old addresses open them.
