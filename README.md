# ⚡ STEAM-ODIN

**steam-odin** is a self-hosted toolset for running many Steam accounts that
trade Counter-Strike 2 items: a Steam Guard authenticator with automatic
confirmations, a portfolio tracker, cross-market price scouts, a card-farming
deal finder, a Storage Unit mover and a Storage Unit shop — one web app on your
own computer, at http://localhost:3000.

Everything is named after Norse figures. The names are flavour; the folder
layout is the truth.

## 📚 Documentation

| You are… | Read |
|----------|------|
| Using the app | **[User guide](docs/guide/README.md)** — setup, every screen, Telegram, backups, troubleshooting |
| Reading the code | **[ARCHITECTURE.md](ARCHITECTURE.md)** — how the pieces fit together |
| An AI coding agent | **[CLAUDE.md](CLAUDE.md)** (also [AGENTS.md](AGENTS.md)) and the per-component `CLAUDE.md` files |
| Curious how a feature works inside | [docs/internals/](docs/internals/) |
| Publishing or forking | [SECURITY.md](SECURITY.md) |

## 🗺️ The realms

```
Yggdrasil/
├── heimdall/      The Watchman — the main app (Flask backend + React frontend)
├── ratatoskr/     The Courier — Node service holding Counter-Strike 2 sessions
└── asf/           ArchiSteamFarm (pinned upstream image) for card farming
```

Tools inside Heimdall:

| Tool | Does |
|------|------|
| **Authenticator** | Steam Guard codes, trade and Market confirmations, auto-confirm, session keep-alive |
| **Huginn** | Price scout: market-to-market arbitrage, Case Arbitrage, Cross-Profile routes, Harvest, LOOT.Farm |
| **Gjallarhorn** | When Valve limits a case: rotation cockpit + news alarm that rings your phone |
| **Andvari** | Games whose trading cards pay for them; buys them on many accounts; ASF farming; card auto-sell |
| **Team Fortress 2** | New case drops: play on release, sell the drops |
| **Draupnir** | Portfolio tracker with profit/loss and automatic backups |
| **Mímir** | Encrypted vault for logins, passwords and emails |
| **Ratatoskr** | Move items in and out of Storage Units; buy Storage Units on many accounts |

## ⚔️ Commands of Power

```bash
make help       # list every command
make odin       # build + start everything
make raid       # start (detached)
make bifrost    # start with attached logs
make saga       # follow the logs
make sleep      # stop
make ragnarok   # stop + remove orphans + prune images
make asf-setup  # once: ArchiSteamFarm password + hardened config
make asf        # start ArchiSteamFarm and reconnect Heimdall
```

| Address | Service |
|---------|---------|
| http://localhost:3000 | The app (frontend) |
| http://localhost:5001 | Backend API |
| http://localhost:3001 | Ratatoskr (backend use only) |
| http://localhost:1242 | ArchiSteamFarm UI |

All are bound to **127.0.0.1 only** — the app has no login; never expose it.

## 🛠️ Development

- **Backend tests** (the real safety net, about 590 tests):

  ```bash
  docker exec steam-odin-heimdall-backend-1 sh -c \
    'pip install -q pytest 2>/dev/null; cd /app && python -m pytest -q'
  ```

- **Frontend:** `npm run lint` and `npm run build` in `Yggdrasil/heimdall/frontend`.
- **CI** (`.github/workflows/ci.yml`) runs backend pytest + ruff + `pip-audit`
  and frontend lint + build on every push and pull request.
- The backend reloads itself on every `.py` save; Ratatoskr needs
  `docker restart steam-odin-ratatoskr-1` after a change.

## 🔒 Security

Steam Guard maFiles, the encryption key, the credential vault, settings with
tokens, and ASF's configuration live **only on your machine** and are
gitignored. Back them up off-machine yourself — see
[backups and safety](docs/guide/backups-and-safety.md) and [SECURITY.md](SECURITY.md).
