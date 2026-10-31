# Storage Units — what can go in, and where it goes

How Heimdall decides **which items may be deposited** into Counter-Strike 2
Storage Units and **which unit each one goes into**. Two features share these
rules and must change together:

- **Transfer, To mode**: the page, `Yggdrasil/heimdall/frontend/src/pages/ratatoskr/Transfer.jsx`;
- **Auto-Store**: the background sweep, `Yggdrasil/heimdall/backend/scheduler.py`.

Users: see [the user guide](../guide/ratatoskr.md#transfer). Agents: the rules
at the end of this page are binding.

## 1. One limitless storage

The user never picks a Storage Unit when depositing. All units together are one
storage whose size is the sum of their free places. Underneath:

- A Storage Unit holds at most **1,000 items** (`STORAGE_CAPACITY`, in
  `frontend/src/utils/transferView.js` and `backend/scheduler.py`).
- The Game Coordinator moves an item into **one specific unit**
  (`casketID`); Ratatoskr's `POST /move/batch` takes one unit per batch and
  knows nothing of the pooling or of the 1,000 limit.
- The deposit is split **fullest unit with room first**, then the next fullest;
  full units are skipped. A move touches as few units as it can and emptier
  units stay free. Ivan chose this order (2026-10-31). Frontend:
  `planBulkDeposit`; backend: `_plan_casket_moves`. Both are tested
  (`tests/test_auto_store_planning.py`; the frontend planner was tested
  against live unit counts).
- One batch per unit, **one after another** at the normal move delay. Never
  in parallel (Steam rate limits).
- If the selection is bigger than the free space, Transfer sends nothing (the
  Storage space panel turns red, Move is disabled); Auto-Store leaves the rest
  for the next sweep.
- The unit counts (`item_storage_total`) come from the Game Coordinator and
  can lag a few seconds behind a move. Transfer keeps what it just deposited
  (`expectedCasketCountsRef`) and plans with the higher count until the Game
  Coordinator catches up, so a quick second deposit never over-fills a unit.
  Auto-Store does not sweep while the move queue is still draining.

## 2. What a Storage Unit refuses

| Item | Can go in? | How it is recognised |
|------|-----------|----------------------|
| Tradable item | Yes | — |
| **Trade hold** — e.g. bought on the Steam Community Market | **Yes**, even during the hold | Attribute **75** (`tradable_after`) only → Ratatoskr `trade_unlock` |
| **Trade protected** — received in a trade with another player | **No**, until the protection ends | Attribute **312**, value = the end date (the same date as 75) → Ratatoskr `trade_locked` + `trade_protected_until`, set only while the date is ahead |
| Untradable collectibles (service medals, veteran and operation coins, Loyalty Badge, Global Offensive Badge), the default music kit, default weapons, Storage Units | **Never** | Ratatoskr `item_moveable: false` (`itemProcessorCanBeMoved` in `ratatoskr/items.js`) |

The important trap: **both holds set `trade_unlock`**, so a hold date alone
says nothing about Storage Units. Only attribute 312 blocks them.

### Evidence

- Auto-Store's first live runs (commit 88f3e44): traded cases had attributes
  [75, 312] and the Game Coordinator refused to store them; market-bought cases
  had [75] (or none) and stored fine mid-cooldown.
- 2026-10-31, Ivan's test: a sticker bought on the Steam Community Market went
  into a Storage Unit during its hold; cases received in trades did not. The
  raw Game Coordinator data showed attribute 312 on every traded item and not
  on the market-bought sticker; `origin` (how the item was created) and
  `flags` did not tell them apart.

### What happens to a refused item

The Game Coordinator gives no answer to a deposit. Ratatoskr counts a sent move
as **done** even when the Game Coordinator refuses it, so a refused item just
stays in the inventory, silently. That is why both features filter **before**
sending:

- Transfer: protected and unstorable rows cannot be selected in To mode, Select
  all skips them, and `handleMove` drops them again before planning.
- Auto-Store: `_is_storable` skips Storage Units, `item_moveable: false` and
  `trade_locked`; an item still in the inventory after the queue drained is
  released from its in-flight guard and logged as refused.

## 3. Where the code lives

| Piece | File |
|-------|------|
| Item fields (`item_moveable`, `trade_unlock`, `trade_locked`, `trade_protected_until`) | `Yggdrasil/ratatoskr/items.js` (`inventoryConverter`) |
| Storable test, rows split tradable / trade hold / trade protected | `frontend/src/utils/transferItems.js` (`canGoIntoStorageUnit`, `isItemTradeProtected`, `groupItemsByName`) |
| Planner, free space | `frontend/src/utils/transferView.js` (`planBulkDeposit`, `storageFreeSpace`) |
| Page: Storage space panel, locks, move | `frontend/src/pages/ratatoskr/Transfer.jsx`, `components/transfer/StorageSpacePanel.jsx` |
| Auto-Store | `backend/scheduler.py` (`_is_storable`, `_plan_casket_moves`, `_auto_store_account`) |

## 4. Reading the raw Game Coordinator data

Ratatoskr's `/inventory` returns converted items; the raw attributes live only
in the running process. Restarting Ratatoskr drops every session, so to look
without restarting (read-only, inside the container): send `SIGUSR1` to the
Node process (`kill -USR1 1`) to open its inspector on the container's
127.0.0.1:9229, connect with a small WebSocket client, find the
`globaloffensive` instances with `Runtime.queryObjects` on
`process.mainModule.require('globaloffensive').prototype`, and read
`inventory[].attribute` with `Runtime.callFunctionOn`. A restart closes the
inspector again. Never change anything this way.

## Rules for agents

1. Transfer's To mode and Auto-Store follow the **same** rules. When Ivan asks
   to change one, tell him to think about both, and change both unless he says
   otherwise.
2. Never decide storability from `trade_unlock` alone; trade protection
   (`trade_locked` / `trade_protected_until`, attribute 312) is what blocks.
3. Keep the order fullest-with-room first and keep the user out of choosing
   units in To mode; no unit picker, no exclusion list.
4. Filter before sending: a refused deposit fails silently.
5. Batches run one after another at the move delay, never in parallel.
