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
| `cors` and `dotenv` are declared but unused | `ratatoskr/package.json` | None |
| The setting `csfloat_api_key` is never read | `backend/settings.py` | CSFloat keys come from `csfloat_keys.json` |
| `system_ops.trigger_restart` is not wired to anything | `backend/system_ops.py` | None |

## Store item names

| Issue | Where | Effect |
|-------|-------|--------|
| The game store names 10 items differently from the Steam Market ("Bestiary Sticker Capsule" is "Bestiary Capsule", "Music Kit \| Darude, Moments CS:GO" is "… CSGO") | `store_catalogue_service.py` names from the game's English strings | Store Boosts resolves them (`store_boost_service.resolve_market_names`) and the Arbitrage tab prices them under those names; the Catalogue's Steam links still open an empty listing |
| Several Steam price history readers, each with its own pacing and 429 back-off ([steam-pricehistory.md](steam-pricehistory.md)) | `steam_market_service.py` (Gjallarhorn), `card_deals_service.py` and `market_seller.py` (Andvari), `store_boost_service.py` (Store Boosts) | A 429 seen by one does not slow the others; one shared reader would be safer |

## Comments that disagree with the code

| Comment says | Code does |
|--------------|-----------|
| CSFloat key cooldown "10 min × strikes" (`huginn_service.py`, `CSFloatKeyManager`) | 60 minutes, or the 429 `Retry-After` |
| `market_seller.py` is shared | Only the card seller uses it; Team Fortress 2 has its own seller and shares only `community_pacer` |
| `.heimdall_salt` is a key file (older docs) | Mentioned only in a docstring about an older scheme; no code reads or writes it |

## Market access and Mímir

| Issue | Where | Effect |
|-------|-------|--------|
| The Market access check reads only the first page of Purchase History (a deliberate choice) | `market_access_service.py` | An account with many Market sales could push its last real purchase off page 1: it then shows amber with no date (never wrongly green); Steam's own verdict still shows |
| Billing addresses are not in Mímir's text export | `mimir_service.export_text` | Restoring the vault from the exported text loses them; the encrypted `.bak` copies keep them |
| The Market unlocker reads the approval total as the last amount before "Total:", the layout captured live in October 2026 | `approval_total` | If Steam reorders the page, the price guard could read another amount; it still refuses anything it cannot read or above $0.05 |
| A Market unlocker dry run claims the free Zombie Hunter license on an account that lacks it | `_unlock_steps` | Free and harmless, but not undone by the cancel; every account owns it since 2026-10-29 |

## Frontend

| Issue | Effect |
|-------|--------|
| `tailwind.config.js` custom colours are not generated (Tailwind 4 ignores a JS config without `@config`) | `bg-odin-blue`, `text-asgard-gold`, `bg-bifrost-cyan`, `text-frost-white`, `bg-odin-dark` render unstyled on the Dashboard, Add account, Confirmations |
| `custom-scrollbar`, `animate-in fade-in slide-in-*` classes are undefined | No effect |
| UI says "Set tradeon_token in Settings" | There is no Settings page; edit `backend/settings.json` |
| Gjallarhorn help says "Confirmations → Connect" | Connect is in the Ratatoskr sidebar |
| `NewsWatcher.jsx` passes `text=` to `InfoTip`, which reads `tip` | Three empty ⓘ hints |
| No confirmation before Confirmations Approve/Deny, Transfer Move, Team Fortress 2 Play / Sell now, card auto-sell checkboxes | One click acts |
| Unused icon imports are not flagged (`varsIgnorePattern '^[A-Z_]'`) | Dead imports accumulate |
| 13 `react-hooks/exhaustive-deps` warnings | Lint is at 0 errors, not 0 warnings |

## Ratatoskr: renaming Storage Units broke on 2026-10-08 (fixed 2026-11-03)

A freshly bought Storage Unit has no name, and the Game Coordinator refuses to put items into it until it is named. Renaming had stopped working for every unit: since the Counter-Strike 2 update of 2026-10-08 the Game Coordinator silently ignores the old binary NameItem message that `globaloffensive`'s `nameItem` sends (no answer at all, so "Rename timed out waiting for GC"). Our own changes were not the cause: the rename code was unchanged since May. Reported upstream in node-globaloffensive issue #122 (crafting broke the same day).

Fix (`Yggdrasil/ratatoskr/server.js` `renameCasket`): send the protobuf `CMsgModifyItemStringAttr {tool_item_id: 0, subject_item_id, attr_value}` under the same message id 1006, `attr_def` left unset (with 111 there is no answer). Verified live on 2026-11-03. The first naming of a new unit gave it a **new item id** (the old one is removed), so the rename answers `item_id` and `previous_item_id`, and the Transfer page swaps the unit and its selection. The Rename button is in From mode (a pencil per unit, and Rename above the list). Still open: whether the deposit plan should warn about or skip an unnamed unit (rule 20, Transfer and Auto-Store together).
