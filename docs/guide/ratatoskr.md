# Ratatoskr — Storage Units

Ratatoskr logs an account into Counter-Strike 2's Game Coordinator (as if the
game were running) so items can be moved in and out of **Storage Units**, so
Storage Units can be bought from the game's own store, and so the store's price
list can be read.

## One account: inventory, transfer, auto-store

Dashboard → account card → **Ratatoskr**.

**Connect** in the left sidebar logs the account in (password from Mímir, code
from the maFile; the account's ASF card farming pauses meanwhile). "Online •
Stable" means ready. **Auto-disconnect when idle** frees the session after
15 minutes … never. **Disconnect** ends it.

### Inventory

Every Counter-Strike 2 item in the inventory, with search.

### Transfer

1. Choose **To** (inventory → a Storage Unit) or **From** (Storage Units →
   inventory).
2. Pick the Storage Unit (To: one with room; From: one, several or **All
   storage units**). The pencil renames a Storage Unit (free, up to 20
   characters).
3. Select items: by group with a quantity, by float, select all; filter by
   collection or search. "N LEFT" shows free space.
4. Open the queue (**N ITEMS**), set the delay between items if needed, press
   **Move**. Moves start immediately — no second question — and run one at a
   time with a pause between them; the progress bar follows.

**Move one of each to inventory** (From mode) takes one copy of every
different skin out of storage — useful so price websites that read your
inventory can see what you hold — and reports what moved.

**Export** downloads the current list as CSV.

### Auto-Store

Watches chosen accounts and moves listed items (exact names, for example a
case you collect) from the inventory into a Storage Unit with room, every
confirmation-check cycle. Switch the watcher on, switch "Act on this account"
on, add item names, **Sweep now** to run it once. The history shows every move.

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
   quantity, price, total, wallet now → after, and any problem.
4. **Test without paying** runs every step, opens the purchase and cancels it
   before approving — nothing is paid. Do this first if anything changed.
5. **Pay** buys. Each account goes through wallet → login → opening →
   approving → delivering; you can close the page, the purchase continues on
   the server.

**If a line says "MAY BE PAID"** the approval went through but delivery was
not confirmed. Check the account's purchase history on Steam, and press
**Deliver again** — it can only deliver a purchase already approved, so it can
never charge twice.

Everything the shop checks before paying: the plan is under 30 minutes old;
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

### Arbitrage tab

http://localhost:3000/store-catalogue/arbitrage (the **Arbitrage** tab on the
Store Catalogue page). Which store items resell for more than the store
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

### Boosts tab

http://localhost:3000/store-catalogue/boosts (the **Boosts** tab on the Store
Catalogue page). It catches a **price boost** on the Steam Market on its first
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
the tab watches the stickers inside each capsule too.

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
