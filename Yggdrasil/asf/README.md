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

Heimdall then creates one bot per account. Nothing else to configure.

## Only accounts with cards to farm run

ASF's FAQ recommends at most **10 bots** ("based on internal Valve guidelines";
ASF logs a warning above that), and a logged-in bot with nothing to farm is risk
without reward. So Heimdall switches a bot **on** only while its account has
work — ASF is farming it, Andvari's badges scan shows drops left, or you pressed
**Farm now** — at most 10 at once (the rest show "Queued"), and switches it
**off** once ASF has been logged in for 5 minutes and found nothing. The login
token is kept, so switching back on needs no password.

**Bought a game?** Andvari → Card farming → press ⚡ (Farm now) on that account:
ASF logs in within a minute, finds the new drops and farms them. (Otherwise the
next Andvari scan, up to 12 hours later, notices the drops.)

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
| Port on 127.0.0.1 only | The ASF UI/API is reachable from this Mac only (plus `heimdall-backend` over Docker), and every call needs the 43-character API password; ASF blocks a source after 5 wrong passwords |
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

## ASF UI

Andvari's header has an **ASF UI** button (or open http://localhost:1242). The
first time, ASF-ui asks for the API password: run `make asf-password` in the
repository — it copies it to the clipboard without printing it — and paste it.
The browser remembers it for that page only.

The UI is powerful: it can edit bot configs and run commands. Heimdall re-applies
the safety settings (no trading, no chat commands, no public listing, offline,
no hour boosting) within 45 seconds if they are changed there; your other edits
(farming order, hours until drops, ...) are kept. Switching a bot on or off
is Heimdall's job (see above) — use Pause in Andvari to hold one. **Never set a "master"
account or Steam user permissions, and never run `loot`**: Heimdall
auto-confirms trades (`auto_confirm_trades`), so a trade ASF creates would go
through. `transfer` moves items between your own accounts (safe, but it links
them with trades); `redeem` of your own keys is safe (keep redeeming
forwarding/distributing off). ASF's own Steam Guard page shows nothing useful here —
by design ASF has no 2FA secrets.

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
