# ASF — ArchiSteamFarm, Andvari's card farmer

[ArchiSteamFarm](https://github.com/JustArchiNET/ArchiSteamFarm) (Apache-2.0)
"plays" games over the Steam client protocol so their trading cards drop. It
runs here as an unmodified, pinned Docker image; Heimdall drives it over ASF's
API (`backend/asf_service.py`) and shows the result in Huginn → Andvari →
**Card farming**.

Loop: Andvari finds a game whose cards pay for it → you buy it (manual) → ASF
farms the drops → you sell the cards (manual for now).

## Setup (once)

```bash
make asf-setup     # random API password into .env + hardened config/ASF.json and IPC.config
make asf           # start ASF, then restart the backend so it picks the password up
```

Heimdall then creates one bot per account and logs them in one by one (about
45 seconds apart). Nothing else to configure.

## Safety model

**Your 2FA seeds never reach ASF.** Bot configs hold the Steam login only. When
ASF needs to log in it stops and says what it needs; Heimdall hands over the
password (from Mímir) and a 30-second Steam Guard code, and ASF keeps the
password **in memory only**. ASF then saves a login token so this repeats only
when the token expires. Without the shared/identity secret, ASF cannot confirm
a trade or a market listing — so a stolen ASF token cannot move items off an
account. (It can log in, see the inventory and spend wallet funds on the store:
treat `config/` like a password file.)

**Nothing phones home, nothing is exposed:**

| Setting | Why |
|---------|-----|
| No published port | Only `heimdall-backend` can reach the ASF API (Docker network), with a 43-character password |
| `RemoteCommunication: 0` per bot | No ASF Steam group, no public bot listing — nothing publicly links the 21 accounts |
| `FilterBadBots: false` | No download of ASF's bad-bot list (trading is off anyway) |
| `UpdateChannel: 0` | No self-update; the image is pinned by digest and updated on purpose |
| SteamDB token dumper | Off (ASF's default); no plugins folder is mounted, so no third-party plugins |
| Container | Non-root user, all Linux capabilities dropped, read-only filesystem, no privilege escalation |

**Valve ban risk kept at the floor:**

- Card idling is what ASF has done for 11 years with no known account
  restrictions; Valve's own VAC FAQ names "Steam idlers" as a known thing.
  It is still automation under the Steam Subscriber Agreement — at your own risk.
- ASF never runs a game's files, so VAC (which scans running game processes) is
  not involved. Counter-Strike 2 is blacklisted anyway.
- Off: hour boosting after farming (`GamesPlayedWhileIdle: []`), trade matching,
  gift accepting, sending items, chat commands (`SteamUserPermissions: {}`).
- `OnlineStatus: 0` (offline): cards still drop, and friends do not see 21
  accounts "in game" at once.
- Paced: logins 30 seconds apart (`LoginLimiterDelay`), community requests 1
  second apart (`WebLimiterDelay`, ASF shares this IP with Andvari/Gjallarhorn),
  Heimdall helps one login at a time, only when ASF's login queue is empty (so a
  code is never stale), and at most three tries per account before it waits for
  you (Andvari shows **Needs you** with a retry button).

**Ratatoskr:** Steam allows one "playing" session per account and Ratatoskr
plays Counter-Strike 2 to reach its Game Coordinator. Heimdall pauses the
account's bot before every Ratatoskr login and resumes it once Ratatoskr's
session is gone (checked every 45 seconds; survives backend reloads).

## What is stored where (all gitignored)

| Path | Content | Secret? |
|------|---------|---------|
| `/.env` | `ASF_IPC_PASSWORD` (0600) | yes |
| `config/ASF.json` | global config incl. the API password | yes |
| `config/<login>.json` | bot config: login + safety flags, **no password** | low |
| `config/<login>.db` | ASF login token for that account | **yes** — like a session |
| `logs/` | ASF logs (ASF masks credentials) | low |

`config/` and `logs/` are owner-only (0700). FileVault protects them at rest.
Do not copy `config/` into cloud backups; losing it only means the accounts log
in again once.

## Operations

- Status: Huginn → Andvari → **Card farming**, or `GET /api/huginn/card-deals/farming`.
- Logs: `docker compose logs -f asf`.
- Stop farming everywhere: `docker compose stop asf` (Heimdall shows "ASF down").
- Remove ASF completely: `docker compose rm -sf asf && rm -rf Yggdrasil/asf/config Yggdrasil/asf/logs`
  (tokens gone; the accounts are unaffected).
- Update ASF: pick a newer stable tag on Docker Hub, replace the tag **and digest**
  in `docker-compose.yml`, read the release notes, `make asf`.
