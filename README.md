# ⚡ STEAM-ODIN

**Steam-Odin** is a comprehensive suite of tools for the Steam ecosystem, forged in the fires of Asgard.

## 🗺️ The Realms (Project Structure)

This monorepo contains multiple applications, each named after a figure from Norse mythology:

- **`Yggdrasil/heimdall`** (Authenticator): The Watchman.
    - *Role:* Syncs with Steam servers, generates TOTP codes, and manages session tokens.
    - *Tech:* Flask + React.
- **`Yggdrasil/ratatoskr`** (Casemove): The Courier.
    - *Role:* Moves items between Storage Units and Inventory.
    - *Tech:* Steam-user & Global Offensive libraries.
- **`Yggdrasil/huginn`** (Skinsprice): The Scout.
    - *Role:* Scours marketplaces to find the best deals.

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

## 🔒 Security (open source)

This project uses **local-only** Steam Guard `maFile` storage. Those files are **not** in git.

1. Copy `Yggdrasil/heimdall/backend/.env.example` → `.env` and set `HEIMDALL_SECRET_KEY`.
2. Import your `.maFile` via the UI (saved under `Yggdrasil/heimdall/backend/maFiles/`).
3. Read [SECURITY.md](SECURITY.md) before publishing or forking.

**Do not commit** `*.maFile`, `.env`, or `backend/logs/`.
