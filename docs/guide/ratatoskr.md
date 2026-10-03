# Ratatoskr — Storage Units

Ratatoskr logs an account into Counter-Strike 2's Game Coordinator (as if the
game were running) so items can be moved in and out of **Storage Units**, and
so Storage Units can be bought from the game's own store.

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

## Storage shop

http://localhost:3000/storage-shop (Dashboard tile **Storage shop**, or the
Ratatoskr sidebar). Buys Counter-Strike 2 Storage Units from the Steam wallet,
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
