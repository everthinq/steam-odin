# CLAUDE.md — steam-odin

Orientation for AI coding agents. Read this first; it is the map, the fleet
commands, and the list of things that will bite you if you do not know them.
Human-facing setup lives in [README.md](README.md) and the per-component
READMEs — this file does not repeat them, it points at them and adds the parts
that are not obvious from the code.

> This is Ivan's **personal** repository (a Steam-trading toolset). It is **not**
> the Revuze data platform. Ignore anything that talks about Databricks, Jira
> RD-tickets, OpenSearch, Knowledge Gate, Pipeline Resolver, or the `data-agents`
> plugin — that machinery belongs to a different repo and does not apply here.

---

## What this is

A monorepo of Steam-ecosystem trading tools, named after Norse figures. The
myth is only flavour — **the code hierarchy always wins over naming**; never move
or rename folders to make the mythology more accurate.

```
Yggdrasil/                      The World Tree — holds the deployable realms
├── heimdall/                   The Watchman — the main suite
│   ├── backend/                Flask API (Python). See backend/CLAUDE.md.
│   └── frontend/               React + Vite single-page app
└── ratatoskr/                  The Courier — Node service. See ratatoskr/CLAUDE.md.
docs/steam-trading/             Trading knowledge base + glossary + baselines
scripts/                        Host-side helpers (portfolio backup launchd job)
```

**Tools that live *inside* Heimdall** (each is frontend pages + backend service,
not a separate deployable):

| Tool | Role | Backend service | Frontend pages |
|------|------|-----------------|----------------|
| (core) | Steam authenticator: TOTP codes, sessions, mobile confirmations | `steam_service.py`, `scheduler.py` | `Confirmations.jsx`, `AddAccount.jsx` |
| **Draupnir** | Portfolio tracker (buy/sell, average-cost profit/loss, live valuation, point-in-time backups) | `draupnir_service.py`, `draupnir_backup_service.py` | `pages/draupnir/` |
| **Huginn** | Cross-market price scout / arbitrage (Tradeon pulse feed, case arbitrage) | `huginn_service.py` | `pages/huginn/` |
| **Mímir** | Encrypted credential vault (login / password / email), shares the maFile key | `mimir_service.py` | `pages/mimir/` |
| **Ratatoskr** | Moves items between Storage Units and inventory | `ratatoskr_service.py` → Node service | `pages/ratatoskr/` |

## Architecture in one breath

- **Backend** is a Flask app. `app.py` constructs every service **once** at boot
  and hangs the singletons off `context.ctx`; route blueprints in `routes/`
  read them from `ctx` at request time. Runs `threaded=True`.
- **Frontend** is React 19 + Vite + react-router-dom 7. Tool pages are
  code-split with `React.lazy`/`Suspense`; the Dashboard is eager.
- **Ratatoskr** is a separate Node service the backend calls over HTTP; the
  frontend never talks to it directly.
- **Everything runs in Docker** via the root `docker-compose.yml`.

---

## Fleet commands (`make`)

`make help` lists them. Real name (legacy alias):

| Command | Does |
|---------|------|
| `make odin` (`all`) | Build + start detached (`forge` then `raid`) |
| `make forge` (`build`) | `docker compose build` |
| `make raid` (`up`) | `docker compose up -d` |
| `make bifrost` (`dev`) | `docker compose up` (attached logs) |
| `make saga` (`logs`) | `docker compose logs -f` |
| `make sleep` (`down`) | `docker compose down` |
| `make ragnarok` (`clean`) | down + remove orphans + prune images |

Ports: **frontend** http://localhost:3000, **backend** http://localhost:5001
(container 5000). Ratatoskr http://localhost:3001.

Container names (compose project = directory name):
- `steam-odin-heimdall-backend-1`
- `steam-odin-heimdall-frontend-1`
- `steam-odin-ratatoskr-1`

---

## Gotchas that will bite you (read before editing)

1. **The backend does not auto-reload.** After editing any backend `.py`, you
   must `docker restart steam-odin-heimdall-backend-1` for the change to take
   effect. The frontend *does* hot-reload (Vite); Ratatoskr does **not**
   (`docker restart steam-odin-ratatoskr-1` after editing its `.js`).

2. **`context.ctx` is empty outside the running app.** The singletons are wired
   only by `app.py` at boot. A bare `python -c` / `docker exec … python` shell
   has `ctx.steam_service is None`. To poke a service ad hoc, construct it
   directly, or replicate the `app.py` wiring — do not assume `ctx` is populated.

3. **Some Bash subshells have a broken PATH** (`curl`, `python3`, `wc`, `tr`
   come back "command not found"). When that happens, use the host `python3`
   explicitly, or `docker exec` into a container, or write the script to a file
   and run it — do not fight inline shell escaping.

4. **Inline `python -c` and f-strings with quotes/backslashes** cause
   `SyntaxError` from shell escaping. Write the script to a file (the scratchpad
   directory) and run it instead.

5. **Steam mobile confirmations need the SteamID64**, not the 32-bit account id,
   in the `a` parameter. The deprecated form returns
   "Oh nooooooes! / try again later" — which *looks* like a rate limit but is
   not. See `docs`-adjacent notes and `steam_service.py`.

6. **The web access token that confirmations need expires about every 24 hours.**
   The only reliable way to mint a fresh one is a full login
   (`begin_auth_session`); `GenerateAccessTokenForApp` returns empty even for
   fresh refresh tokens — do not chase that endpoint. `scheduler.py` runs a
   keep-alive sweep to renew tokens proactively; watch the logs for
   `[KEEPALIVE] … failed`.

7. **maFiles do not contain the account password.** Read passwords from the
   Mímir vault (`get_password` → vault by login), never by probing the maFile.

8. **Steam rate limits (HTTP 429) are the enemy.** Ratatoskr moves items
   serially with a delay; the scheduler sleeps between accounts. Do not
   parallelise Steam calls or shorten these delays without a very good reason —
   see `ratatoskr/CLAUDE.md` and the notes in `scheduler.py`.

---

## Testing

**Backend** has a real pytest suite (`backend/tests/`). It is the gate. Run it
inside the container (pytest is installed ephemerally there):

```bash
docker exec steam-odin-heimdall-backend-1 sh -c \
  'pip install -q pytest 2>/dev/null; cd /app && python -m pytest -q'
```

Or on the host per the [README](README.md) (`pip install -r requirements-dev.txt && pytest`).

**Frontend** has **no test suite** — the gates are `npm run lint` and
`npm run build`. To verify a UI change actually renders, drive it in headless
Chrome (`--dump-dom --virtual-time-budget`) or open http://localhost:3000.

**Continuous integration** (`.github/workflows/ci.yml`) runs backend pytest +
ruff + `pip-audit`, and frontend lint + build, on every push and pull request.

---

## Conventions & house rules (non-negotiable)

- **Never commit or push without an explicit instruction.** "Put it on git" is
  not "commit and push now" — wait for the actual word. Commit messages are
  KISS + explanatory and end with a `Co-Authored-By:` trailer.
- **Do not abbreviate.** Spell things out in names, comments, and prose —
  explicit beats implicit. (Ivan's standing rule.)
- **Secrets stay untracked.** `*.maFile`, `.heimdall_key`, `.heimdall_salt`,
  `credentials.vault`, `settings.json`, `csfloat_keys.json` are gitignored and
  must never be committed or sent to any external service. See
  [SECURITY.md](SECURITY.md).
- **`portfolios.json` is committed on purpose** (personal holdings, no secrets)
  as a lightweight backup; the on-disk snapshot history under `backups/` is
  gitignored. Match this pattern — do not add secrets to committed files.
- **Behaviour-preserving changes** unless asked otherwise; this is production
  data for a real trader. When touching backups or portfolios, verify data is
  preserved before deleting anything.

## Where to read more

- Setup, environment variables, `tradeon_token`: [Yggdrasil/heimdall/README.md](Yggdrasil/heimdall/README.md)
- Backend internals, service wiring, per-file map: [Yggdrasil/heimdall/backend/CLAUDE.md](Yggdrasil/heimdall/backend/CLAUDE.md)
- Ratatoskr rate-limit rules: [Yggdrasil/ratatoskr/CLAUDE.md](Yggdrasil/ratatoskr/CLAUDE.md)
- Trading domain knowledge: [docs/steam-trading/](docs/steam-trading/)
