# ⚡ STEAM-ODIN

**steam-odin** is a self-hosted web app for running many Steam accounts that
trade Counter-Strike 2 items. It keeps every account's Steam Guard codes and
confirmations in one place, compares prices across 10+ marketplaces, tracks
your portfolio, farms trading cards and case drops, buys in-game items in bulk
with price guards, and alerts you by Telegram and phone call when something
important happens. It runs in Docker on your own computer at
http://localhost:3000.

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
| **Authenticator** | Steam Guard codes, trade and Market confirmations, auto-confirm, session keep-alive; the Dashboard shows each account's health and Market access |
| **Huginn** | Price scout across 10+ marketplaces: market-to-market arbitrage after fees, Case Arbitrage, LOOT.Farm, price dips against the 30-day median, a live re-check per item, and the Pulse feed page that controls automatic price downloads |
| **Cross-Profile** (in Huginn) | The best buy-here, sell-there route for every held item across all accounts |
| **Harvest** (in Huginn) | Every lot you still hold at the price you paid, against what instant-buy markets pay now |
| **Gjallarhorn** | When Valve limits a case: suggests selling deflated holdings and rotating into it; a news watcher texts and calls your phone |
| **Andvari** | Games whose trading cards pay for them; buys them on many accounts; ArchiSteamFarm farming; card auto-sell |
| **Team Fortress 2** | When a new case is added: every account starts playing, drops are listed on the Market |
| **Draupnir** | Portfolio tracker: buys, sells, moving-average profit/loss, live valuation, automatic backups |
| **Mímir** | Encrypted vault for logins, passwords, emails and billing addresses; Market access check (when each account loses the Steam Market) and the Market unlocker (a one-cent in-game purchase that keeps it open a year) |
| **Ratatoskr** | Moves items in and out of Storage Units; buys Storage Units and any in-game store item on many accounts (price-guarded, dry run first); Store Catalogue Arbitrage (store items that resell above store price); Store Boosts (catches a Steam price boost on its first day) |

## 🧰 Built with

| Part | Language and stack |
|------|--------------------|
| Heimdall backend | **Python** (about 60% of the code): Flask API, background schedulers, pytest |
| Heimdall frontend | **JavaScript**: React 19 + Vite |
| Ratatoskr | **JavaScript**: Node.js service for Counter-Strike 2's Game Coordinator |
| Card farming | ArchiSteamFarm (unmodified upstream image) |
| Running it | Docker Compose, Makefile, Shell scripts, GitHub Actions |

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

- **Backend tests** (the real safety net, about 1,050 tests):

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
