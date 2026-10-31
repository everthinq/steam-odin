# Storage Units — what can go in, where it goes, and how a move is confirmed

How Heimdall decides **which items may be deposited** into Counter-Strike 2
Storage Units, **which unit each one goes into**, and **how it knows a move
really happened**. The rules live in one place, the backend's
`Yggdrasil/heimdall/backend/storage_deposit.py`; everything that moves items
goes through its commands:

- **Transfer** (the page, `frontend/src/pages/ratatoskr/Transfer.jsx`): To and
  From mode send commands and follow the job; the page decides nothing;
- **Auto-Store** (the background sweep, `backend/scheduler.py`): the same
  deposit command, on a timer;
- the **command line** (planned): the same endpoints.

Users: see [the user guide](../guide/ratatoskr.md#transfer). Agents: the rules
at the end of this page are binding.

## 1. The commands

Every move is **plan → execute once → a job**:

| Step | Endpoint | Answers |
|------|----------|---------|
| Plan a deposit | `POST /api/ratatoskr/deposit/plan` `{steamid, item_ids: [...]}` or `{steamid, all_storable: true}` | `{plan_id, moves, batches: [{casket_id, casket_name, count}], refused: [{item_id, item_name, code, reason, until?}], overflow, free, executable, expires_at}` |
| Plan a withdrawal | `POST /api/ratatoskr/withdraw/plan` `{steamid, items: [{item_id, casket_id}]}` | the same shape |
| Execute | `POST /api/ratatoskr/plans/<plan_id>/execute` | 202 + the job; **a second call returns the same job** (a double click or a retried request never moves twice) |
| Follow | `GET /api/jobs/<job_id>` | `{id, kind, running, done, failed, total, phase, error, result, started_at, finished_at}`; `result.batches` lists per unit the moved ids, `result.failed_items` every item that did not move with its `code`: `refused` (the Game Coordinator never answered), `not_sent`, `busy` (another run never finished, nothing sent) or `unconfirmed` (the run's queue was dropped — a disconnect or a restart — or stalled) |
| Free space | `GET /api/ratatoskr/storage/<steamid>` | `{free, units, units_with_room, stored, capacity}` (the Storage space panel) |

Errors are `{error, code}`: `no_room`, `plan_expired` (plans live 10 minutes),
`plan_unknown`, `busy` (one move run per account at a time), `plan_stale` (another
run finished on the account after the plan was made: its unit counts are out of
date), `job_gone` (the plan ran long ago), `nothing_to_move`,
`ratatoskr_unavailable`. Jobs live in the backend's memory: after a backend
restart `GET /api/jobs/<id>` answers 404 `job_unknown` and the page says the
result is unknown. `GET /api/ratatoskr/inventory/<steamid>` adds
`storage_block` to every item (`null`, or `{code, reason, until}`), so the page
shows the backend's decision instead of working it out.

The job runs in the backend: closing the page never stops it. Ratatoskr's raw
`/move/batch` is called only by `DepositService`; there is no other move path.

## 2. One limitless storage

The user never picks a Storage Unit when depositing. All units together are one
storage whose size is the sum of their free places. Underneath:

- A Storage Unit holds at most **1,000 items** (`STORAGE_CAPACITY` in
  `storage_deposit.py`; the frontend keeps a copy for display only).
- The Game Coordinator moves an item into **one specific unit** (`casketID`);
  Ratatoskr's `POST /move/batch` takes one unit per batch and knows nothing of
  the pooling or of the 1,000 limit.
- The deposit is split **fullest unit with room first**, then the next fullest;
  full units are skipped (`plan_deposit`). A move touches as few units as it can
  and emptier units stay free. Ivan chose this order (2026-10-31).
- One batch per unit, **one after another** at the normal move delay. Never in
  parallel (Steam rate limits).
- A Transfer plan bigger than the free space cannot be executed (`no_room`; the
  Storage space panel turns red, Move is disabled). Auto-Store plans with
  `allow_overflow`: what has no room waits for the next sweep.
- The Game Coordinator's unit count (`item_storage_total`) can lag a few seconds
  behind a move. `DepositService` remembers what a run just deposited per unit
  for two minutes and plans with the higher count, so a quick second deposit
  never over-fills a unit; a withdrawal forgets it.

## 3. What a Storage Unit refuses

`storage_block(item)` in `storage_deposit.py`:

| Item | Can go in? | How it is recognised | `code` |
|------|-----------|----------------------|--------|
| Tradable item | Yes | — | — |
| **Trade hold** — e.g. bought on the Steam Community Market | **Yes**, even during the hold | Attribute **75** (`tradable_after`) only → Ratatoskr `trade_unlock` | — |
| **Trade protected** — received in a trade with another player | **No**, until the protection ends | Attribute **312**, value = the end date (the same date as 75) → Ratatoskr `trade_locked` + `trade_protected_until`, set only while the date is ahead | `trade_protected` |
| Untradable collectibles (service medals, veteran and operation coins, Loyalty Badge, Global Offensive Badge), the default music kit, default weapons | **Never** | Ratatoskr `item_moveable: false` (`itemProcessorCanBeMoved` in `ratatoskr/items.js`) | `unstorable` |
| Storage Units | **Never** | definition index 1201 | `storage_unit` |

The important trap: **both holds set `trade_unlock`**, so a hold date alone
says nothing about Storage Units. Only attribute 312 blocks them.

A chosen item that is blocked comes back in the plan's `refused` with its
reason and is never sent. With `all_storable`, unstorable items are left out
quietly and trade-protected ones are listed.

### Evidence

- Auto-Store's first live runs (commit 88f3e44): traded cases had attributes
  [75, 312] and the Game Coordinator refused to store them; market-bought cases
  had [75] (or none) and stored fine mid-cooldown.
- 2026-10-31, Ivan's test: a sticker bought on the Steam Community Market went
  into a Storage Unit during its hold; cases received in trades did not. The
  raw Game Coordinator data showed attribute 312 on every traded item and not
  on the market-bought sticker; `origin` (how the item was created) and
  `flags` did not tell them apart.

## 4. How a move is confirmed

The Game Coordinator sends no reply to a move request. It does update the
inventory, and Ratatoskr (`ratatoskr/moves.js`) treats that update as the
answer. Observed live on 2026-10-31 with one case, in and out of a unit,
answers within milliseconds:

| Move | What the Game Coordinator reports | Counts as moved when |
|------|-----------------------------------|----------------------|
| Deposit, unit contents **not** opened in this session | `itemRemoved` (the item left the inventory) | `itemRemoved` for the item |
| Deposit, unit contents opened | `itemRemoved`, then `itemAcquired` with `casket_id` = the unit, then `itemChanged` (the unit) and notification 1013 | either of the two |
| Withdrawal | `itemAcquired` without `casket_id` (back in the inventory), `itemChanged` (the unit), notification 1014 | `itemAcquired` without a unit |

No answer within **15 seconds** means the Game Coordinator **refused** the
move: the item stays where it was. If the session or the Game Coordinator
connection was lost before the answer, the move is `unconfirmed` instead (it
may or may not have happened), never `refused`. Moves keep going out at the move delay and
are confirmed in the background, so a refusal costs that wait but slows nothing
else. Each run (a batch queued on an idle queue) gets a `runId`, returned by
`/move/batch`; `/move/status` reports it with `doneItemIDs` (confirmed),
`refusedItemIDs`, `unconfirmedItemIDs` and `failedItemIDs` (all three kinds of
failure, including never sent). The backend counts
**only the ids confirmed for its own run**: a disconnect or a restart drops the
queue, the status then shows another run, and whatever was not confirmed by
then counts as `unconfirmed` — never as moved.

Auto-Store records only confirmed moves in its history. An item the Game
Coordinator refused is not offered again for 6 hours; one that was not sent or
is unconfirmed is offered again at the next sweep.

## 5. Where the code lives

| Piece | File |
|-------|------|
| Item fields (`item_moveable`, `trade_unlock`, `trade_locked`, `trade_protected_until`) | `Yggdrasil/ratatoskr/items.js` (`inventoryConverter`); tests `ratatoskr/test/items.test.js` |
| Move queue + confirmations | `Yggdrasil/ratatoskr/moves.js`; tests `ratatoskr/test/moves.test.js` |
| Storage rules, planner, commands | `backend/storage_deposit.py` (`storage_block`, `plan_deposit`, `DepositService`); tests `backend/tests/test_storage_deposit.py` |
| Jobs | `backend/jobs.py`, `backend/routes/jobs.py` |
| Routes | `backend/routes/ratatoskr.py` (`/storage`, `/deposit/plan`, `/withdraw/plan`, `/plans/<id>/execute`) |
| Auto-Store | `backend/scheduler.py` (`_auto_store_account`, `_auto_store_finished`) |
| Page | `frontend/src/pages/ratatoskr/Transfer.jsx`, `utils/moveCommands.js` (commands and jobs), `utils/useTransferSelection.js` (what can be selected), `components/transfer/StorageSpacePanel.jsx`; `utils/transferItems.js` reads `storage_block` |

## 6. Reading the raw Game Coordinator data

Ratatoskr's `/inventory` returns converted items; the raw attributes live only
in the running process. Restarting Ratatoskr drops every session, so to look
without restarting (read-only, inside the container): send `SIGUSR1` to the
Node process (`kill -USR1 1`) to open its inspector on the container's
127.0.0.1:9229, connect with a small WebSocket client, find the
`globaloffensive` instances with `Runtime.queryObjects` on
`process.mainModule.require('globaloffensive').prototype`, and read
`inventory[].attribute` with `Runtime.callFunctionOn` (or attach listeners to
watch `itemRemoved` / `itemAcquired` during one move, as in §4). A restart
closes the inspector again. Never change anything this way.

## Rules for agents

1. **The backend decides, the page displays.** Storage rules, the planner and
   the batch run live only in `storage_deposit.py`. Never re-add a planner or a
   storability rule to the frontend; read `storage_block` and the plan instead.
2. Transfer and Auto-Store use the **same** command. When Ivan asks to change
   one, tell him to think about both.
3. Never decide storability from `trade_unlock` alone; trade protection
   (`trade_locked` / `trade_protected_until`, attribute 312) is what blocks.
4. Keep the order fullest-with-room first and keep the user out of choosing
   units in To mode; no unit picker, no exclusion list.
5. Filter before sending, and count a move only when Ratatoskr confirmed it.
6. Batches run one after another at the move delay, never in parallel.
