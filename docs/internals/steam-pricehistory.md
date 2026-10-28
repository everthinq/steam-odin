# Steam Market price history (`pricehistory`)

Steam's own record of every sale of an item: the hourly (recent) or daily median price and
the number sold. Four features read it. This page is what we know about the endpoint from live
use (October 2026), so the next reader does not rediscover it.

## The endpoint

```
GET https://steamcommunity.com/market/pricehistory/
    ?country=US&currency=1&appid=730&market_hash_name=<name>
Cookie: steamLoginSecure=…   (a logged-in web session)
```

- **It needs a logged-in session.** Without one it gives no history. The backend takes cookies from
  `SteamService.web_session_cookie()` / `web_session_cookie_for(steamid)`; they only check the
  token's time left, so a session Steam has revoked still looks usable.
- **The public listing page no longer helps.** Checked 2026-10-22: the Community Market listing
  page redirects and no longer embeds the chart (`line1`), so scraping it gives nothing.
- **It answers in the session's wallet currency**, whatever `currency=1` says. The answer names it
  only by symbol: `price_prefix` `"$"` and an empty `price_suffix` mean US dollars. Never mix
  answers from accounts with different wallets: `card_deals_service.parse_sale_history` returns
  nothing unless the answer is in dollars, and Store Boosts skips such an account for 6 hours.

## The answer

```json
{"success": true, "price_prefix": "$", "price_suffix": "",
 "prices": [["Oct 05 2026 16: +0", 11.0, "42"], ...]}
```

- Each point is `[time, median price, units sold]`. The time is a **UTC hour** (`"+0"`).
- **Hourly for about the last 30 days, daily before that**, back to the item's first sale.
- The price is what the **buyer paid**, Steam's fee included (about 13% above what a seller gets).
- Units sold are a string and can carry a thousands separator (`"1,234"`), so strip commas before
  converting. A capsule in a boost sells over 1,000 an hour.
- Hours with no sale are missing, not zero.

## Errors

| Answer | Meaning | What to do |
|--------|---------|------------|
| HTTP 429 | Too many requests (from this address) | Back off: Store Boosts waits 10 minutes, doubling to 2 hours |
| HTTP 500 (also 400) | A name Steam does not know (live 2026-10-25: `Bestiary Sticker Capsule`, whose market name is `Bestiary Capsule`) | The item's own error; check the market name |
| HTTP 401 / 403 | The session is refused | Use another account |
| No `success`, or an empty answer | No session, or no history | Treat as no data |
| Many items failing in a row | Usually expired sessions (the keep-alive failed) | Pause, say so (Store Boosts: 8 in a row) |

## Store items have market names of their own

The in-game store names some items differently from the Community Market, and `pricehistory`
only knows the market name (10 items on 2026-10-25): `… Sticker Capsule` is `… Capsule` for
eight old capsules, `Music Kit | Darude, Moments CS:GO` is `… CSGO`, `StatTrak™ Radicals Music Kit
Box` is `StatTrak™ Radicals Box`. A capsule's market name is its crate's own English name in the
game files (Ratatoskr's `/items/capsule-contents` returns it as `capsule`). See
`store_boost_service.resolve_market_names` and [known-issues.md](known-issues.md).

## Who reads it

| Feature | Code | Pacing |
|---------|------|--------|
| Gjallarhorn liquidity (items pulse lacks) | `steam_market_service.py` | 3 s + 2 s gaps, deferred while the page is in use, 24-hour cache |
| Andvari card sale prices | `card_deals_service.py` (`parse_sale_history`, app 753) | capped per scan, a day per card |
| Andvari card auto-sell | `market_seller.py` (`parse_price_history`) | history kept 6 hours, `community_pacer` |
| Store Boosts | `store_boost_service.py` | one item every 30 seconds (setting, 10 to 3600), `community_pacer`, 429 back-off |

They do not share one pacer or one back-off ([known-issues.md](known-issues.md)): a 429 seen by
one does not slow the others. Live, Store Boosts read 100–107 items an hour at its 30-second gap
and met two 429s on its first evening (it backed off 10, then 20 minutes, and carried on).

## Reading one by hand

For an analysis (the boost examples were read this way): copy a small script into the backend
container, take cookies from `SteamService().web_session_cookie()` (`context.ctx` is empty outside
the app, so build the service directly), read the names one at a time with several seconds
between them, write the answers to a file, copy it out, and delete both files from the container.
Never loop over many names: that is what a 429 looks like from Steam's side.
