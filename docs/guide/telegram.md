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
| **In-game store items that sell above the store price**: once a day after the morning refresh, and when an item newly turns profitable (each item at most once a day) | The shared bot (the Huginn chat) | Store Catalogue → Arbitrage → **Telegram: daily post** (on by default) and **Send now** |
| **Price boosts** of store items and of stickers inside store capsules: each new level (watch, boost, second wave, fading), once per boost | The shared bot (the Huginn chat), and a **phone ring** on a boost | Store Catalogue → Boosts → **Alerts** (Telegram, ring, **Test message**, **Test ring**) |
| **Andvari** deal alerts and card-sale summaries | **Andvari's own bot only** | Andvari → **Settings** → Andvari bot token + chat id, then **Test alert** |

Andvari never uses the Huginn bot: without its own bot it stays silent. Give
it a separate bot so card news does not drown the arbitrage alerts.

A store-item message names each item, its store price, the market that pays most in money or Steam
wallet (with its price, fee and profit, linked through pulse), what CSMoney (Trade) pays (and "BUY
signal" when it reaches the threshold), and a link to the Steam Community Market. Site trade balances
(TradeIt, CSMoney, LOOT.Farm, SkinSwap Trade) never count: they price items in their own, inflated scale.
`store_arbitrage_alert_min_pct` in `backend/settings.json` sets a minimum profit (0 = any profit).

A price boost message names the item, Steam's price now against its usual, the sales of the last
24 hours against the usual, the store price (and what another market pays at once in money), the
capsules it is in with their opening value, the date copies bought now become tradable, and a link to
the Steam Community Market. See [ratatoskr.md](ratatoskr.md#boosts-tab).

Buy Storage Units, the Store Catalogue's own tab, Buy games and card farming send no messages.

## Phone rings

A Telegram bot cannot make calls, so rings come from a **spare Telegram user
account** (a second number) that calls your main account: Gjallarhorn news,
a Team Fortress 2 case release, a price boost of a store item (Store Catalogue →
Boosts), and the **Ring test** button. The call has no
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
