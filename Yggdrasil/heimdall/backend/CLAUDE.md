# CLAUDE.md — Heimdall backend (Flask)

Agent notes for the Python backend. The root [CLAUDE.md](../../../CLAUDE.md) has
the fleet-wide rules; this file is the backend map and its specific traps.

## Boot & wiring

`app.py` is the composition root. At import it:

1. calls `setup_logging()` **before** constructing anything (so every module
   logger goes through the rotating file + console handlers);
2. constructs each service **once** (`SteamService`, `SettingsManager`,
   `RatatoskrService`, `HuginnService`, `DraupnirService`, `BackupService`,
   `ConfirmationScheduler`, `MimirService`);
3. hangs the singletons on `context.ctx`;
4. registers route blueprints via `routes.register_blueprints(app)`;
5. starts the background scheduler in the request-serving process, then
   `app.run(threaded=True)`.

Dependency shape (who is passed what):
`HuginnService(steam_service, ratatoskr_service)` →
`DraupnirService(huginn_service)` → `BackupService(draupnir_service.path)` +
`draupnir_service.set_backup(...)`; `MimirService(steam_service.storage)`;
`ConfirmationScheduler(settings_manager, steam_service, ratatoskr_service)`.

> **`ctx` is populated only here.** In a bare `python -c` or `docker exec`
> shell, `ctx.steam_service` etc. are `None`. Construct services directly or
> mirror this wiring if you need to script against them.

## File map

| File | Responsibility |
|------|----------------|
| `app.py` | Composition root, `ctx` wiring, scheduler start |
| `context.py` | The `ctx` singleton holder (empty until `app.py` fills it) |
| `routes/` | Flask blueprints by domain: `accounts`, `settings`, `draupnir`, `ratatoskr`, `huginn`, `mimir`. Each reads `ctx` at request time. |
| `steam_service.py` | Steam login, TOTP, sessions, **mobile confirmations**, web-token lifecycle (largest hot file) |
| `scheduler.py` | Background auto-confirm loop **+ session keep-alive sweep** |
| `huginn_service.py` | Tradeon pulse price feed, cross-market prices, case arbitrage catalog (largest file) |
| `draupnir_service.py` | Portfolio store, average-cost profit/loss, CSV import |
| `draupnir_backup_service.py` | Point-in-time snapshots of `portfolios.json`, gzip-compressed, GFS retention |
| `mimir_service.py` | Encrypted credential vault (shares the maFile key) |
| `morning_routine.py` | Daily "Get all items" at 08:00 local (catches up the first minute the Mac is awake after it), then rebuilds the CSFloat item dictionary (`cache/csfloat_item_links.json`: held item → CSFloat listing) and starts the buy-order sweep unless prices are under 12 hours old; state in `cache/morning_routine.json` |
| `harvest_service.py` | Harvest (Huginn tab): each purchase lot still held (`DraupnirService.open_lots()`: real price paid, sells use up the oldest buys first) vs the chosen autobuy market's instant offer; per-market index cached 10 min and warmed one market at a time; marks site-balance payouts and cash offers above 1.3× Buff163's listing |
| `card_deals_service.py` | Andvari (Huginn → Card deals): games whose trading-card drops resell for more than the game costs, per account; background scan with its own Steam throttles, cache in `cache/card_deals.json.gz` |
| `asf_service.py` | ArchiSteamFarm driver: hardened bot per account, switched on only while it has cards to farm (max 20, Ivan's choice; ASF's FAQ recommends 10), password + Steam Guard code only when ASF asks (paced, three tries), pause while Ratatoskr plays, farming status; Team Fortress 2 mode (chosen bots play app 440 outside the 10-bot ceiling) and the license sweep (every account, new ones included, gets the free Team Fortress 2 license; stopped bots started one at a time for it); off until `ASF_IPC_PASSWORD` is set |
| `team_fortress_service.py` | Team Fortress 2 case drops: polls the app 440 news feed for "Added the … Case" → starts Team Fortress 2 mode, adds the case to the sell list, texts + rings; auto-sell reads each playing account's inventory (paced) and lists sell-list items one minor unit under the lowest Market listing in the wallet currency (fee rules from `g_rgWalletInfo`), confirming only those listings; state in `cache/team_fortress.json` |
| `store_purchase_service.py` | Andvari "Buy games" tab: plan (per account: store country, wallet currency + balance from `g_rgWalletInfo`, owned games from the store's `dynamicstore/userdata`, regional price of the cheapest default package from `appdetails`, US dollar conversion, maximum price, balance, best Andvari profit first) and guarded wallet purchases (empty cart only, cart and Steam's final price must equal the plan to the cent, checkout on `checkout.steampowered.com`, dry run cancels before paying, then ASF farm now); history + statistics in `cache/store_purchases.json` |
| `storage_shop_service.py` | Ratatoskr Storage shop: plan (wallet currency + balance, store country, the game store's Storage Unit price from its LZMA binary KeyValues price sheet, entry "casket") and guarded purchases through Ratatoskr (`StorePurchaseInit` with the game store's 0-based currency, USD 0 — Steam's wallet code gets result 8 — and Steam's account country, then the store country → approval form on `checkout.steampowered.com/checkout/approvetxn/<id>/`, which must name the transaction and show the planned total → `StorePurchaseFinalize`, count must grow); dry run cancels before approving; after approving nothing is cancelled and failures read "MAY BE PAID" ("Deliver again" re-sends Finalize, which only delivers an approved transaction); logs the account in and out of Ratatoskr (never a session it did not open, never during moves); history in `cache/storage_shop.json`, last approval page in `cache/storage_shop_approval_page.html` |
| `card_seller_service.py`, `market_seller.py`, `community_pacer.py` | Andvari card auto-sell (off by default): new trading cards listed one cent under the lowest Market listing in the wallet currency, only cards that arrived after it was switched on, only their confirmations accepted (exact item name, type 3 or market type 12), retried until confirmed; statistics per account and game in `cache/card_sales.json`. One steamcommunity.com gap shared with the Team Fortress 2 seller |
| `storage.py` | maFile load/save, encryption/migration |
| `jsonio.py` | Crash-safe atomic JSON read/write |
| `validation.py` | Request-body validation for writes |
| `settings.py` | `settings.json` load with safe defaults |
| `notifications.py`, `logging_setup.py`, `system_ops.py` | Supporting utilities |

## Data files (all gitignored except `portfolios.json`)

- `portfolios.json` — Draupnir holdings. **Committed on purpose** (no secrets).
- `backups/portfolios/` — gzip snapshot history (`*.json.gz`, content-addressed
  by sha1 of the *uncompressed* content; legacy plain `.json` still read).
- `maFiles/` — Steam Guard files (`*.maFile`). Secret. Never commit.
- `.heimdall_key` / `.heimdall_salt` — maFile encryption key/salt. Secret.
- `credentials.vault` — Mímir vault. Secret.
- `settings.json` — holds `tradeon_token`. Secret. Template: `settings.example.json`.
- `csfloat_keys.json` — CSFloat API key rotation pool. Secret.
- `logs/`, `cache/` — runtime, gitignored.

## Running & testing

Backend **auto-reloads on every `.py` save** (`FLASK_ENV=development` in
`docker-compose.yml` enables werkzeug's watcher; see `Detected change … reloading`
in `logs/heimdall.log`), and every background loop restarts with it — so an edit
is live immediately, half-finished or not. Test long runs with outward effects
(Steam calls, Telegram alerts) in a sandbox script with its own cache file and a
stubbed notifier. Use `docker restart steam-odin-heimdall-backend-1` to reload a
changed cache or data file (those are not watched).

```bash
# tests inside the running container (pytest installed ephemerally)
docker exec steam-odin-heimdall-backend-1 sh -c \
  'pip install -q pytest 2>/dev/null; cd /app && python -m pytest -q'

# lint (matches CI)
docker exec steam-odin-heimdall-backend-1 sh -c 'cd /app && ruff check .'
```

Dev dependencies: `requirements-dev.txt` (pytest, ruff, pip-audit).

## Backend-specific traps

- **Sell fees live in ONE place.** The market registry in `huginn_service.py` holds
  the defaults, and the Fees editor on the Arbitrage page (settings
  `huginn_market_fees`) overrides them. Every profit calculation reads them through
  `HuginnService.market_fee(market_id)`. That covers the Arbitrage profiles, Case
  Arbitrage, Cross-Profile, Harvest, LOOT.Farm and the auctions. Never add a
  per-feature fee constant.

- **Confirmations `a` param = SteamID64**, not the 32-bit account id (see
  `steam_service.py`). Wrong form returns a fake-looking rate-limit message.
- **Web token for confirmations expires ~24h.** Only a full login mints a fresh
  one; `GenerateAccessTokenForApp` returns empty. `scheduler.py` keep-alive
  renews proactively — do not remove it. Watch `[KEEPALIVE] … failed`.
- **Passwords come from the Mímir vault by login**, never from the maFile
  (maFiles have no password field).
- **Do not parallelise or speed up Steam calls** — 429 rate limits. The
  scheduler sleeps between accounts on purpose.
- **Blueprints import services from `ctx` at request time**, so import order is
  not a concern, but a service being `None` means `app.py` did not wire it.
