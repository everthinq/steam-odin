# ⚡ STEAM-ODIN

**Steam-Odin** is a comprehensive suite of tools for the Steam ecosystem, forged in the fires of Asgard.

## 🗺️ The Realms (Project Structure)

This monorepo contains two deployable realms, plus several tools that live
inside Heimdall — each named after a figure from Norse mythology:

- **`Yggdrasil/heimdall`** — The Watchman. The main suite (Flask backend + React
  frontend). Started as the Steam authenticator (TOTP codes, session tokens,
  confirmations) and now hosts the tools below.
    - *Tech:* Flask + React/Vite.
- **`Yggdrasil/ratatoskr`** — The Courier. Node service that moves items between
  Storage Units and inventory via the Steam-user & Global Offensive libraries.
  Driven from Heimdall's Ratatoskr pages.

Tools **within Heimdall** (frontend pages under `heimdall/frontend/src/pages/`):

- **Draupnir** (Portfolio Tracker): The Hoard. Tracks buy/sell transactions per
  portfolio with avg-cost P/L and live valuation; point-in-time backups; CSV import.
- **Huginn** (Arbitrage / Skins Scout): The Scout. Cross-market price scouting &
  arbitrage profiles (Tradeon/LisSkins/Buff/CSFloat/DMarket) using live pulse
  prices; Case Arbitrage tracker and price alerts.

> [!NOTE]
> The `apps` directory has been renamed to **`Yggdrasil`** (The World Tree), which contains the individual realms (applications).

## ⚔️ Commands of Power

We use `make` to command the fleet. Speaking the old names (`build`, `up`) will still work, but the true commands are:

```bash
# Speak the wisdom (List all commands)
make help

# The All-Father commands everything (Build + Start)
make odin

# Forge the containers
make forge

# Launch the longships (Start background)
make raid

# Open the Bifrost (Start interactive/logs)
make bifrost

# Rest the warriors (Stop)
make sleep

# Destruction and Renewal (Clean up orphans)
make ragnarok
```

## 🛠️ Development

The `docker-compose.yml` orchestrates all services.
- **Heimdall Frontend**: http://localhost:3000
- **Heimdall Backend**: http://localhost:5001

### Tests

Backend tests (avg-cost math, crash-safe JSON I/O, maFile encryption/migration,
write validation) run with pytest:

```bash
cd Yggdrasil/heimdall/backend
pip install -r requirements-dev.txt
pytest
```

CI (`.github/workflows/ci.yml`) runs the backend tests + ruff + `pip-audit`, and
the frontend lint + build, on every push and pull request.

## 🔒 Security (open source)

This project uses **local-only** Steam Guard `maFile` storage. Those files are **not** in git.

1. Copy `Yggdrasil/heimdall/backend/.env.example` → `.env` and set `HEIMDALL_SECRET_KEY`.
2. Import your `.maFile` via the UI (saved under `Yggdrasil/heimdall/backend/maFiles/`).
3. Read [SECURITY.md](SECURITY.md) before publishing or forking.

**Do not commit** `*.maFile`, `.env`, or `backend/logs/`.
