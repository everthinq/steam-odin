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

## API Reference

### Health Check
`GET /health`
```json
{ "status": "healthy" }
```
