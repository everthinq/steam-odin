# Known issues and drift

Things that are true in the code today but surprising, inconsistent, or
documented wrongly somewhere. Found during the October 2026 documentation pass.
None of them is urgent; each is listed so nobody trips over it. When you fix
one, delete its line.

## Configuration and build

| Issue | Where | Effect |
|-------|-------|--------|
| `backend/.env.example` suggests copying it to `backend/.env`, but nothing reads that file in Docker (no dotenv, no `env_file`); compose takes `HEIMDALL_SECRET_KEY` and `ASF_IPC_PASSWORD` from the **root** `.env` | `.env.example`, old README | Without the root variable the backend generates `maFiles/.heimdall_key` — which works, but must be backed up. Setting a key after accounts are stored makes them unreadable |
| Ratatoskr's code defaults to port 3030; Docker uses 3000 | `ratatoskr/server.js` `PORT`, backend `ratatoskr_service.py` `RATATOSKR_URL` fallback | Only matters when running outside Docker |
| CI runs Python 3.11 and Node 20; the images run Python 3.9 and Node 22 | `.github/workflows/ci.yml` vs the Dockerfiles | Code must work on both; a 3.10+ feature would pass CI and crash the container |
| CI lint and `ruff` are non-blocking (`|| true`) | `ci.yml` | Lint errors do not fail a push; keep them at zero by hand |
| The backend Dockerfile's `gunicorn` command is overridden by compose (`python app.py`) | `docker-compose.yml` | The development server with auto-reload is what actually runs |
| No container has a healthcheck; the frontend has no restart policy | `docker-compose.yml` | A crashed frontend stays down until `make raid` |
| A stale compose comment mentions `network_mode: host` | `docker-compose.yml` | None |
| `make ratatoskr` and `make huginn` only print a message | `Makefile` | Use `docker compose up -d ratatoskr` |
| `cors` and `dotenv` are declared but unused | `ratatoskr/package.json` | None |
| The setting `csfloat_api_key` is never read | `backend/settings.py` | CSFloat keys come from `csfloat_keys.json` |
| `system_ops.trigger_restart` is not wired to anything | `backend/system_ops.py` | None |

## Comments that disagree with the code

| Comment says | Code does |
|--------------|-----------|
| CSFloat key cooldown "10 min × strikes" (`huginn_service.py`, `CSFloatKeyManager`) | 60 minutes, or the 429 `Retry-After` |
| `market_seller.py` is shared | Only the card seller uses it; Team Fortress 2 has its own seller and shares only `community_pacer` |
| `.heimdall_salt` is a key file (older docs) | Mentioned only in a docstring about an older scheme; no code reads or writes it |

## Frontend

| Issue | Effect |
|-------|--------|
| `tailwind.config.js` custom colours are not generated (Tailwind 4 ignores a JS config without `@config`) | `bg-odin-blue`, `text-asgard-gold`, `bg-bifrost-cyan`, `text-frost-white`, `bg-odin-dark` render unstyled on the Dashboard, Add account, Confirmations |
| `custom-scrollbar`, `animate-in fade-in slide-in-*` classes are undefined | No effect |
| UI says "Set tradeon_token in Settings" | There is no Settings page; edit `backend/settings.json` |
| Gjallarhorn help says "Confirmations → Connect" | Connect is in the Ratatoskr sidebar |
| `NewsWatcher.jsx` passes `text=` to `InfoTip`, which reads `tip` | Three empty ⓘ hints |
| Ratatoskr Inventory selection has no actions | Selecting does nothing |
| No confirmation before Confirmations Approve/Deny, Transfer Move, Team Fortress 2 Play / Sell now, card auto-sell checkboxes | One click acts |
| Unused icon imports are not flagged (`varsIgnorePattern '^[A-Z_]'`) | Dead imports accumulate |
| 15 `react-hooks/exhaustive-deps` warnings | Lint is at 0 errors, not 0 warnings |
