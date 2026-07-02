# 🛡️ HEIMDALL (Authenticator)

**Heimdall** is the Watchman of the **steam-odin** ecosystem. It handles Steam login sessions, 2FA code generation, and manages the user's local inventory cache.

## Tech Stack
- **Frontend**: React (Vite)
- **Backend**: Python (Flask)
- **Database**: *(Coming Soon)*

## Development Setup

The easiest way to run this service is via the root `docker-compose.yml`, but you can run it standalone for deeper debugging.

### Prerequisites
- Python 3.9+
- Node.js 22+

### Running Backend (Flask)
```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export FLASK_APP=app.py
export FLASK_ENV=development
flask run
```
*Runs on [http://localhost:5000](http://localhost:5000)*

### Running Frontend (React)
```bash
cd frontend
npm install
npm run dev
```
*Runs on [http://localhost:5173](http://localhost:5173)*

## Environment Variables
Create a `.env` file in `Yggdrasil/heimdall/backend/.env`:

| Variable | Description | Default |
|----------|-------------|---------|
| `FLASK_ENV` | App environment | `production` |
| `SECRET_KEY` | Flask session secret | *Random* |
| `STEAM_API_KEY` | Your Steam Web API Key | *Required* |

## Configuration (`settings.json`)

App settings live in `backend/settings.json`. **This file is gitignored** because
it holds the Tradeon token — copy the template to create your own:

```bash
cp backend/settings.example.json backend/settings.json
```

| Key | Type | Description |
|-----|------|-------------|
| `check_interval` | int (seconds) | How often the auto-confirm scheduler polls. |
| `auto_check_enabled` | bool | Master switch for the background confirmation checker. |
| `auto_confirm_market` | bool | Auto-accept Steam **market** confirmations (types 3 & 12). |
| `auto_confirm_trades` | bool | Auto-accept Steam **trade** confirmations (type 2). |
| `tradeon_token` | string | **Required for Huginn.** Bearer token for `pulse.tradeon.space`. Without it, price fetching is disabled. |

If `settings.json` is missing, the backend boots with safe defaults (all
auto-confirm off, no Tradeon token).

### Getting the `tradeon_token`

Huginn (the arbitrage tool) proxies price data from `pulse.tradeon.space`, which
requires your account's bearer token:

1. Log into <https://pulse.tradeon.space> in your browser.
2. Open **DevTools → Network**, trigger any price table load.
3. Find a request to `api-pulse.tradeon.space` → **Headers** → copy the value of
   `authorization: Bearer <token>` (just the `<token>` part).
4. Paste it into `settings.json` as `tradeon_token`.

The token is a JWT and expires eventually; when Huginn starts returning auth
errors, repeat these steps to refresh it.

## API Reference

### Health Check
`GET /health`
```json
{ "status": "healthy" }
```
