# Telegram alerts and phone rings

The app can message you on Telegram and, for urgent news, **ring your phone**.
Bots are created with **@BotFather** in Telegram (`/newbot` gives you a token
like `123456789:AA…`). To find a chat id, message your bot once and open
`https://api.telegram.org/bot<token>/getUpdates` — the number after
`"chat":{"id":` is it.

## Which message goes where

| Messages | Bot | Where to set it |
|----------|-----|-----------------|
| Case Arbitrage alerts (one board message, updated as prices move) and a note for each **auto-confirmed trade** | The shared "Huginn" bot | Huginn → Case Arbitrage → **Alerts** (bot token + chat id, or a Discord/Slack webhook) |
| Gjallarhorn news (a case or collection limited) | The shared bot | Gjallarhorn → news watcher → **Alert chat id** (empty = the Huginn chat) |
| Team Fortress 2 new case, auto-stop | The shared bot | Team Fortress 2 → settings → **Telegram chat** (empty = the Huginn chat) |
| **Andvari** deal alerts and card-sale summaries | **Andvari's own bot only** | Andvari → **Settings** → Andvari bot token + chat id, then **Test alert** |

Andvari never uses the Huginn bot: without its own bot it stays silent. Give
it a separate bot so card news does not drown the arbitrage alerts.

The Storage shop, Buy games and card farming send no messages.

## Phone rings

A Telegram bot cannot make calls, so rings come from a **spare Telegram user
account** (a second number) that calls your main account: Gjallarhorn news,
a Team Fortress 2 case release, and the **Ring test** button. The call has no
sound content — it is just the ringing, after a message saying why.

Set it up once:

1. Get an `api_id` and `api_hash` for the spare account at https://my.telegram.org.
2. Run, and follow the questions (it sends a login code to the spare number):

   ```bash
   docker exec -it steam-odin-heimdall-backend-1 python /app/telegram_caller_login.py
   ```

3. Press **Ring test** on the Gjallarhorn page.

The login is saved in `backend/telegram_caller.json` — a secret; it is never
committed.
