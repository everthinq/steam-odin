# 🛡️ HEIMDALL

**Heimdall** is the main app of steam-odin: a Flask backend (`backend/`) and a
React + Vite frontend (`frontend/`). It started as a Steam Guard authenticator
and now hosts every tool — Huginn, Gjallarhorn, Andvari, Team Fortress 2,
Draupnir, Mímir and the Ratatoskr pages.

- **Using it:** [the user guide](../../docs/guide/README.md).
- **Changing it:** [backend/CLAUDE.md](backend/CLAUDE.md) and
  [frontend/CLAUDE.md](frontend/CLAUDE.md) (written for AI agents, useful for
  anyone), and [ARCHITECTURE.md](../../ARCHITECTURE.md).

## Running

The normal way is Docker from the repository root (`make odin`): frontend on
http://localhost:3000, backend on http://localhost:5001, both bound to
127.0.0.1. In Docker the backend runs `python app.py` with
`FLASK_ENV=development`, so it **reloads on every `.py` save**.

Running outside Docker is possible but not the supported path: the frontend's
Vite proxy points at the Docker host name `heimdall-backend`, and the backend
expects Ratatoskr at `RATATOSKR_URL`.

## Environment variables

Docker Compose reads these from the **root** `.env` (next to
`docker-compose.yml`); `backend/.env.example` lists them for reference.

| Variable | Meaning | Default |
|----------|---------|---------|
| `HEIMDALL_SECRET_KEY` | Encrypts maFiles and the Mímir vault | Unset → a key is generated in `backend/maFiles/.heimdall_key` |
| `ASF_IPC_PASSWORD` | ArchiSteamFarm API password (`make asf-setup` writes it) | Empty → ASF integration off |
| `RATATOSKR_URL` | Ratatoskr address | `http://ratatoskr:3000` in compose |
| `ASF_URL` | ASF address | `http://asf:1242` |
| `HEIMDALL_ALLOWED_HOSTS` | Extra `Host` names the API accepts (comma-separated) | none |
| `HEIMDALL_LOG_LEVEL` | Log level | `INFO` |
| `HTTP_PROXY`, `HTTPS_PROXY`, `SOCKS_PROXY` | Outbound proxy for Steam calls | none |

## Configuration (`backend/settings.json`)

Gitignored (it holds tokens). Copy `backend/settings.example.json` to start.
Every key and its default is in `backend/settings.py`; most are changed from
the app's screens. The ones you set by hand:

| Key | Meaning |
|-----|---------|
| `tradeon_token` | Bearer token for pulse.tradeon.space — prices for Huginn and Draupnir ([how to get it](../../docs/guide/getting-started.md#4-prices-huginn-and-draupnir)) |

CSFloat API keys go in `backend/csfloat_keys.json`; the Telegram phone-ring
account in `backend/telegram_caller.json` (created by
`telegram_caller_login.py`).

## API

All routes are under `/api/` and grouped by blueprint in `backend/routes/`:

| Prefix | Blueprint | Covers |
|--------|-----------|--------|
| `/api/accounts`, `/api/confirmations` | `accounts.py` | Accounts, maFile import, codes, confirmations |
| `/api/settings` | `settings.py` | Settings (known keys only) |
| `/api/draupnir/portfolios` | `draupnir.py` | Portfolios, transactions, CSV, backups |
| `/api/huginn` | `huginn.py` | Prices, arbitrage, Case Arbitrage, Harvest, Gjallarhorn, Andvari, Team Fortress 2 |
| `/api/ratatoskr` | `ratatoskr.py` | Sessions, moves, Storage Units, auto-store, Buy Storage Units (`storage-shop`), Store Catalogue (`store-catalogue`) and its Arbitrage tab (`store-arbitrage`) |
| `/api/mimir` | `mimir.py` | Credential vault |

`GET /health` reports the backend and its scheduler.

## Tests

```bash
docker exec steam-odin-heimdall-backend-1 sh -c \
  'pip install -q pytest 2>/dev/null; cd /app && python -m pytest -q'
cd frontend && npm run lint && npm run build
```
