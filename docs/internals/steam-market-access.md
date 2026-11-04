# Steam Community Market access (the one-year purchase rule)

Steam lets an account use the Community Market only while the account has a recent real
purchase. Without one the Market closes: no selling, no buying, and every Heimdall feature that
lists on the Market (card auto-sell, the Team Fortress 2 seller, Storage Unit resale) stops
working on that account. The **Market access** button in Mímir checks every account; the Dashboard
shows the result on each account card.
This page is what we know from live checks (2026-10-29), so the next reader does not rediscover it.

## The rule

- The account needs a **valid purchase that is between 7 days and one year old**, with no
  chargeback or payment dispute since (Steam's support text, quoted on the Steam forums; the
  support page itself only renders in a browser). The 7 days were 30 days in older versions.
- **Counts:** wallet funds (a top-up by card, PayPal or a wallet code), games, DLC, software,
  and in-game purchases (Storage Units, capsules bought in the game store; Ivan's word).
- **Does not count:** Community Market transactions, buying or selling (forum posts quoting
  Steam: the rule lists only the store and wallet funds), gifts, retail game key activations,
  free licenses (below), refunded purchases.
- A chargeback resets the clock: only purchases after the most recent chargeback count.

## Free promotional licenses do not count

Checked 2026-10-29 with Hexologic (a 100%-off "Free to Keep" promotion, package
"Hexologic Limited Free Promotional Package - Oct 2026", sub 1901258) added to every account:

- The **Licenses** page (`store.steampowered.com/account/licenses/`) shows it with the
  acquisition method **Complimentary**; paid games show **Steam Store**.
- It does **not** appear in **Purchase History** at all. Since the Market rule is about
  purchases, claiming a free game never extends Market access.
- It also drops **no trading cards**: Steamworks says Free to Keep recipients are not eligible,
  and ASF found nothing to farm on any account.

To keep the Market open, buy something real (a cheap game, a wallet top-up or an in-game item
such as a Storage Unit) at least once a year. Andvari's "Buy games" and Buy Storage Units count.

## Reading it: two requests per account

The service is `backend/market_access_service.py`, started by `POST
/api/mimir/market-access/check` (in `routes/mimir.py`), results at `GET /api/mimir/market-access` and in
`cache/market_access.json`. One account at a time, about 6 seconds each: a 2 second gap before
each store call, and the shared `community_pacer` (4 seconds) before each steamcommunity.com
call. An HTTP 429 stops the whole check. An account without a fresh web session gets one
`ensure_fresh_session` (the same renewal Andvari's Buy games uses), else it is shown as not checked.

### 1. Purchase History, first page only

```
GET https://store.steampowered.com/account/history/?l=english
Cookie: steamLoginSecure=…   (the account's web session; it works on the store domain too)
```

- The newest purchase is always on the first page, so the check never loads more. (Older rows
  come from `POST /account/AjaxLoadMoreHistory/` with the `g_historyCursor` the page sets, but
  none of our accounts had more than one page in October 2026.)
- Rows are `<tr class="wallet_table_row …">` in `table.wallet_history_table`, with cells
  `wht_date` ("2 Oct, 2026"), `wht_items` (one `<div>` per item), `wht_type` (the type, then a
  `<div class="wth_payment">` with the payment method), `wht_total`.
- Types seen: `Purchase` (payment `Wallet`, `Retail` for a wallet code, `Visa **12`, `PayPal`,
  `MasterCard **34`), `In-Game Purchase`, `Market Transaction` / `7 Market Transactions`,
  `Refund` (items named "<game> Refund"), `Conversion` (wallet currency change). `Gift Purchase`
  also exists.
- `counted_purchases` keeps `Purchase` and `In-Game Purchase` rows unless every item has a refund
  dated on or after the purchase. Access lapses **365 days after the newest counted purchase**; a first purchase under
  7 days old opens the Market on its eighth day (`opens_on`).
- A page without `wallet_history_table` (a login page) is an error, never "no purchases".

### 2. Steam's own verdict

```
GET https://steamcommunity.com/market/eligibilitycheck/?goto=%2Fmarket%2F   (no redirect following)
```

- An account that may use the Market gets a 302 to `/market/` and the cookie
  `webTradeEligibility`, URL-encoded JSON:
  `{"allowed":1,"allowed_at_time":0,"steamguard_required_days":15,"new_device_cooldown_days":0,"time_checked":…}`.
- A restricted account is redirected to the help page instead and gets `allowed: 0` (with a
  `reason` number and an `allowed_at_time` when Steam knows the date). We have not seen a
  restricted account yet, so the reason numbers are shown as Steam sends them, not translated.
- The Market page itself has no "allowed" flag in its HTML; do not scrape it for this.

## The Market unlocker (one-cent in-game purchase)

Since in-game purchases count, the cheapest way to keep the Market open is a one-cent item.
The old trick, a $0.01 in-game item of a game the account does not own ("!AnyWay! AerlyTight",
in our Purchase Histories from early 2026), stopped working: Steam no longer sells items of a game
the account does not own. The new one (shared by a trading course, 2026-10-29): claim the free
game **Zombie Hunter** (app 1723420, free package 617273) and buy its **"Editor Pack"** (item 330,
$0.01 on the item store; HK$ 0.08 on a Hong Kong dollar wallet). Mímir's **Market unlocker**
button does it, as two backend commands (plan → execute once → a job, like Transfer's moves; see
"The commands" below). Per account, one at a time:

1. Owned games from `store.steampowered.com/dynamicstore/userdata/`. Without Zombie Hunter:
   `POST store.steampowered.com/freelicense/addfreelicense/617273` (`ajax=true`, `sessionid`),
   the same call ASF makes, then read the owned games again. No ASF bot has to be online.
2. Wallet currency from the Market page's `g_rgWalletInfo`.
3. `GET store.steampowered.com/buyitem/1723420/330/1` (no redirect following): Steam opens a
   transaction and answers 302 to `checkout.steampowered.com/checkout/approvetxn/<transid>/?returnurl=…/buyitem/1723420/finalize/<orderid>…`.
   **Opening this link already creates a pending transaction**; it is never charged unless
   approved, but cancel it (step 5). For a game the account does not own, the same link shows
   "Oops, sorry! An error was encountered" with no message.
4. The approval page must show "Steam account: <login>", Zombie Hunter, `QTY PRICE NAME 1 …
   Editor Pack`, a total (the last amount before "Total:") of at most `UNLOCK_MAX_USD` ($0.05)
   after Andvari's exchange rates, and an `AuthorizeTransaction( true )` button. Its form is the
   same as Buy Storage Units' (`form_authtxn` → `checkout/approvetxnsubmit` with
   `transaction_id`, `returnurl`, `sessionid`, `approved`), read by `storage_shop_service.approval_form`.
5. Dry run, or any check failing: post the form with `approved=0` (what the page's Cancel,
   `AuthorizeTransaction( false )`, does). Steam answers 200; the approval page then reads "An
   unexpected error occurred while authorizing your transaction" (checked live).
6. Real run: post it with `approved=1`, open the finalize link (Steam's 302, or the form's
   `returnurl`), then the purchase is "bought" only once Purchase History lists the Zombie Hunter
   in-game purchase dated today (or yesterday: Steam's dates are not in our time zone). Any
   failure after approving reads "MAY BE PAID"; so does a backend restart in the middle. Then
   the account's Market access is checked again.

**Live dry run on all 21 accounts (2026-10-29):** 4 ready (the page offered Authorize at $0.01);
15 got "You must enter a billing address to complete your purchase" with an "Update Billing
Information" button instead of Authorize (the page's `form_updateaddress` posts to
`checkout/?purchasetype=updatebillinginfo…`; Heimdall never fills an address in: enter it once
in a browser; the address for each account is kept in its Mímir row); 2 Hong Kong dollar accounts showed HK$ 0.08 but no Authorize (an empty wallet).
Buy Storage Units' approval page (an in-game purchase started by the game) never asked for a
billing address. Every account now owns Zombie Hunter.

### The commands

There is no route that buys without a plan.

- `POST /api/mimir/market-access/unlock/plan {steamids: [...]}` → 202 and the plan at once
  (`MarketAccessService.plan_unlock`): `{plan_id, state: "dry run", accounts, passed, max_usd,
  error, created_at, expires_at, expired, executable: false, dry_run_job_id, job_id}`. The dry
  run (steps 1 to 5, always cancelled) runs as the job `dry_run_job_id` (kind
  `market-access-unlock`, `GET /api/jobs/<id>`; `done` = accounts that passed, `failed` = the
  others, `phase` = the account being tried). When it ends the job's `result` is the finished
  plan: `state: "ready"`, per account `{steamid, account_name, passed, reason, usd, total,
  currency, claimed_game, max_usd}` (`reason` says why it failed; accounts the dry run never
  reached, after an HTTP 429, read "not tried: the dry run stopped (…)"), and `expires_at` =
  10 minutes (`PLAN_LIFETIME_SECONDS`) after the dry run ended. `GET
  /api/mimir/market-access/plans/<plan_id>` reads it again.
- `POST /api/mimir/market-access/plans/<plan_id>/execute {dry_run: false}` → 202 and the job
  (`execute_plan`): buys only on the accounts that passed that plan's dry run, with every guard
  of steps 3 to 6 checked again. A second call answers the same job (a double click never buys
  twice). Its `result` per account: `{steamid, account_name, paid, approved, error, usd, kind,
  text}`, kind `bought`, `failed` or `may_be_paid`.
- Refusals are `{error, code}`: `bad_request` (no accounts list), `no_accounts` (none known),
  `busy` 409 (a check or an unlocker run is going), `plan_unknown` 404 (also after a backend
  restart: plans are kept in memory), `plan_not_ready` 409 (its dry run still runs),
  `plan_expired`, `plan_replaced` 409 (a newer dry run covers one of its accounts, so
  two plans can never buy on one account twice), `plan_failed` 409 (the dry run could
  not be finished), `nothing_passed`, `job_gone` 410, `real_purchase_only`. A plan
  reads `state` "expired" once it can no longer be bought, and the page reads the
  plan again every 30 seconds and at its expiry, so Buy for real turns off by itself.
- **Fails closed:** execute buys only on `dry_run` exactly `false`. Anything else (missing,
  `true`, `"false"`, `0`, `null`, a body that is not JSON) is refused with `real_purchase_only`
  without touching Steam or the plan. It is not turned into a dry run: a dry run claims free
  licenses and opens transactions on Steam, which nobody asked for there; a new dry run is a new
  plan.
- Plans are forgotten an hour after they were made (an unexecuted one once it expired).

## What the pages show

- **Mímir** (`pages/mimir/Vault.jsx`): the **Market access** button in the header starts the
  check and shows `done/total` while it runs (also while the unlocker runs); the **Market
  unlocker** button opens `components/MarketUnlocker.jsx` (choose accounts; the ones the backend
  marks `purchase_would_help` start ticked, also when the result arrives after the dialog opened;
  last run per account from `last_unlocks`: ready, bought, or why not; **Dry run** makes a plan;
  **Buy for real** executes it, enabled only while the plan is executable, after typing
  `BUY <accounts that passed>`); a **Market access** column on every row with a
  maFile; a banner with every amber or red account, soonest first, and accounts that could not
  be checked.
- **Dashboard** (`pages/Dashboard.jsx`): the same result, read-only: a line on each account card
  and the same banner. It never starts the check.
- Colours: green; amber within 30 days (`WARN_DAYS`) or when Steam allows the Market but no
  counted purchase is on the first page; red when lapsed or when Steam's check says no. The
  tooltip names the last counted purchase, Steam's verdict and when it was checked.
- The backend decides what the pages print: `GET /api/mimir/market-access` adds per account
  `headline` (the one line: which fact wins), `advice` (amber: buy something to keep it open),
  `purchase_would_help` and `reason` (amber or red with nothing keeping the Market open for more
  than 30 days; a red account with a later purchase is blocked by Steam for another reason, and
  a purchase under 7 days old will open it), and at the top `flagged` (amber and red steamids,
  no date first, then soonest), `not_checked`, and `last_unlocks` (per steamid the newest
  unlocker run: `{kind, text, at, dry_run, plan_id}`, kind `running`, `bought`, `passed`,
  `failed` or `may_be_paid`). `job.job_id` names the running job.
- Shared pieces: `components/MarketAccess.jsx` (`MarketAccessButton`, `MarketAccessBanner`,
  `MarketAccessLine`) and `utils/useMarketAccess.js` (loads the result, polls every 3 seconds
  while a job runs, starts the check, `planUnlock` and `executePlan` with `followJob` from
  `utils/moveCommands.js`).
- Days left are recomputed from the saved date every time a page asks, so old results still
  count down; press the button again after a purchase.
