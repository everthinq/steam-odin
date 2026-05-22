# CS2 in-game store purchase (WIP)

**Status:** Experimental — **not production-ready**.  
**Scope:** Buy **Storage Unit** (`item_def_id` **1201**) with **Steam Wallet** via Game Coordinator + web checkout.

This branch captures working pieces and known blockers so the work can be resumed without re-discovering Steam’s behavior.

> **Open-source safety:** Do not commit real SteamID64 values, session cookies, `steamLoginSecure` tokens, wallet transaction IDs, or price-sheet dumps from live accounts. Use env vars and local-only paths under `/tmp/`.

---

## Goal

End-to-end headless purchase from Heimdall / Ratatoskr:

1. Load GC store catalog + wallet price for the account (e.g. Norway → NOK **1850** for Storage Unit).
2. `StorePurchaseInit` (GC msg **2510**) → Steam opens a **wallet microtransaction**.
3. Web **`approvetxn`** on `checkout.steampowered.com` (authenticated web session).
4. `ClientMicroTxnAuthorize` (steam-user) after web approval.
5. `StorePurchaseFinalize` (GC msg **2504**) → item delivered.

Typical reference clients load:

`GET https://checkout.steampowered.com/checkout/approvetxn/{wallet_txn_id}/?returnurl=steam`

and refresh `dynamicstore/userdata` on checkout and store hosts.

---

## Architecture

| Layer | Path | Role |
|--------|------|------|
| Ratatoskr | `store.js` | GC store, price sheet, purchase, checkout |
| Ratatoskr | `storeBinaryKv.js` | Parse LZMA + VBKV price sheet (`entries.casket.prices.{ISO}`) |
| Ratatoskr | `server.js` | HTTP: `/store/:steamid`, `/store/purchase/begin`, `/finish`, `/diag/run` |
| Heimdall API | `ratatoskr_service.py`, `app.py` | Proxy to Ratatoskr |
| Heimdall UI | `frontend/.../Store.jsx` | Store page, begin/finish buttons |

### Purchase flow (intended)

```
Connect Ratatoskr (GC + webSession cookies)
    → sync price sheet (StoreGetUserData 2500)
    → POST /store/purchase/begin
        → prepareSteamStoreSession (dynamicstore, GetStorePreferences)
        → syncGcPriceSheetForPurchase
        → StorePurchaseInit @ wallet price from sheet
        → wait wallet microtxn (15+ digit id)
    → POST /store/purchase/finish
        → GET/POST approvetxn (web-first; defer ClientMicroTxnAuthorize)
        → ClientMicroTxnAuthorize
        → StorePurchaseFinalize(gc_txn_id)
```

**Important:** GC `txn_id` (short numeric GC id) and wallet `approvetxn` id (long numeric id) are **different**. Finalize uses **GC** txn; checkout uses **wallet** txn.

---

## What works (when GC + web session are connected)

- **GC session** + **web session** (`steamLoginSecure`, `sessionid`) after Heimdall Connect.
- **Price sheet parse** from `StoreGetUserData` (result=1 Denied but sheet present): LZMA → VBKV → e.g. `entries.casket.prices.NOK` = **1850**.
- **Catalog API** can return Storage Unit at **NOK 18.50**, `price_source: gc_sheet_explicit`.
- **`StorePurchaseInit`** often returns **result=1 (Denied)** but with valid **GC `txn_id`** — treated as partial success.
- **`[STORE-DIAG]`** structured logging (`STORE_DIAG=1` default).
- **Diag endpoint** `POST /store/diag/run` and script `scripts/test-store-purchase.js`.

---

## What does not work yet (blockers)

### 1. Stuck wallet microtxn (primary blocker)

During testing, Steam sometimes **reuses the same wallet transaction id** across new `StorePurchaseInit` calls. The `approvetxn` page then returns HTTP 200 with a body error (“unexpected error while authorizing your transaction”). **Checkout never completes** → finalize times out or fails.

**Mitigations in code (partial):**

- `failedCheckoutMicroTxnIds` — remember failed approvetxn ids (in-memory per Ratatoskr session).
- `denyStaleMicroTxn` — `ClientMicroTxnAuthorize(deny)` before new init.
- `clearAbandonedStorePurchases` — cancel GC txn + deny stale wallet ids.
- Web-first checkout: defer auto-approve until after `approvetxn` POST.

**Operator steps:**

- Cancel any **pending wallet authorization** in the Steam client, **or**
- Disconnect Ratatoskr, wait ~30s, reconnect, retry until a **new** 19-digit microtxn appears in logs.

### 2. `StorePurchaseFinalize` timeout (2505)

When checkout fails, finalize may still be attempted → **GC message 2505 timed out** (90s). Usually a follow-on from (1).

### 3. Intermittent GC store unavailable (result=200)

If the price sheet is not loaded on GC before init, `PurchaseInit` returns **200**. Mitigated by `syncGcPriceSheetForPurchase` + single explicit price init. Wait ~15s after Connect on cold start.

---

## Configuration

| Env | Default | Meaning |
|-----|---------|---------|
| `STORE_DIAG` | `1` (on) | JSON `[STORE-DIAG]` logs (avoid sharing logs publicly) |
| `STORE_GC_SESSION_MODE` | `headful` (not `headless`) | ClientHello + socache for store |
| `STORE_COUNTRY_CODE` | derived from wallet currency | GC + checkout country |
| `STORE_DUMP_PRICESHEET` | `0` | Write `/tmp/pricesheet-{accountId}*.bin` (local only) |

---

## API (Ratatoskr)

| Method | Path | Body |
|--------|------|------|
| GET | `/store/:steamid` | Catalog + wallet |
| POST | `/store/purchase/begin` | `{ steamID, itemDefId: 1201, quantity }` |
| POST | `/store/purchase/finish` | `{ steamID, txnId }` (GC txn from begin) |
| POST | `/store/purchase` | begin + finish (one shot) |
| POST | `/store/diag/run` | `{ steamID, executePurchase?: false }` |

Heimdall proxies under `/api/ratatoskr/store/...`.

---

## Testing

```bash
# Set your SteamID64 locally — never commit it
export STEAM_ID="<your_steamid64>"

# Diagnostics only (no wallet charge)
curl -s -X POST http://localhost:3001/store/diag/run \
  -H 'Content-Type: application/json' \
  -d "{\"steamID\":\"$STEAM_ID\"}" | python3 -m json.tool

# Full attempt (charges wallet if checkout succeeds)
node Yggdrasil/ratatoskr/scripts/test-store-purchase.js --execute

# Logs (scrub before sharing)
docker logs steam-odin-ratatoskr-1 2>&1 | grep STORE-DIAG | tail -30
```

**Success signals:**

- `purchase_init_accepted` with a **new** wallet `microtxn` id (not a previously failed one).
- `approvetxn_post` with `success_body: true`.
- `purchase_finish_ok` with `item_ids`.

**Probe price sheet dump (local container only):**

```bash
docker exec steam-odin-ratatoskr-1 node /app/scripts/probe-pricesheet.js \
  /tmp/pricesheet-<account_id>-decoded.bin
```

(Set `STORE_DUMP_PRICESHEET=1` and open Store once to create dumps — files stay in `/tmp`, not in git.)

---

## Key files

| File | Purpose |
|------|---------|
| `store.js` | Main store + purchase logic |
| `storeBinaryKv.js` | Binary KV / VBKV price sheet parser |
| `server.js` | Routes + webSession stash |
| `scripts/test-store-purchase.js` | E2E test via HTTP |
| `scripts/probe-pricesheet.js` | Offline sheet debug |
| `heimdall/.../Store.jsx` | UI |

---

## Resume checklist

1. Clear Steam pending wallet charge for the test account (or use a clean account).
2. Connect Ratatoskr; confirm `GET /store/:steamid` shows explicit sheet price for Storage Unit.
3. Run `test-store-purchase.js` **without** `--execute`; confirm diag is green.
4. Run with `--execute`; confirm **new** wallet txn in `[STORE-DIAG]`.
5. If approvetxn still `hard_error`: capture checkout HTML locally, compare with a known-good HAR, try `StorePurchaseQueryTxn` (2508).
6. Keep web-first microtxn authorization (current default).

---

## Merge criteria

Do **not** merge to `master` until:

- [ ] New wallet microtxn on each purchase attempt.
- [ ] `approvetxn` completes without `hard_error`.
- [ ] `StorePurchaseFinalize` returns result=0 and item appears in inventory.

---

## Branch strategy

Park on **`wip/cs2-store-purchase`**; fix other bugs on **`master`**. This commit is WIP documentation + experimental code only.
