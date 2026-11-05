# Gjallarhorn — when Valve limits a case

When Valve removes a case or collection from the active drop pool, its price
usually jumps within hours. Gjallarhorn helps you act fast: it tells you the
moment the news is out (a Telegram message, plus a phone call when **Ring** is on), shows which of
your holdings to sell, and how much of the newly limited item your capital buys.

**Gjallarhorn never buys or sells.** It is a cockpit; you act on the markets.

Open it from the Dashboard tile **Gjallarhorn** (also /huginn/gjallarhorn).
**How to use** in the header walks through it step by step.

## The news watcher

Every 10 minutes it reads the official Counter-Strike 2 news and looks for an
update that adds or removes a case, capsule, collection or souvenir package.
When it finds one, it sends a Telegram message, and rings your phone only when **Ring** is on.

- **Armed / Disarmed** switches the alarm.
- **Ring on / Ring off (text only)** chooses whether an alarm also rings your phone. It is off
  by default: the alarm only texts. "Ring not set up" means the phone call is not configured.
- **Check now** reads the news immediately.
- **Test detection**: paste an update text to see what it would detect — no
  alarm is sent.
- **Alert chat id**: where the message goes (see [telegram.md](telegram.md)). It
  must be a number (a group's starts with a minus sign) or a public @name; anything
  else is refused in red. A failed save or a failed **Check now** is shown in red too.
- **Ring test** in the header makes one short test call.

The first check after installing only records what is already out; it never
alarms on old news, and a known update never alarms twice.

## The rotation table

Your holdings (one account or all combined), each with what you paid, what it
is worth now on the reference market, profit or loss if sold, how many sold on
the Steam Market in 7 days, the spread (lowest listing vs highest buy order) and
how far the price is from its 30-day median (all from Tradeon pulse, so Steam's
rate limits no longer slow this page; items pulse does not know still come from
Steam, with Steam's 7-day trend), whether it is tradable now,
and a **score**: high = sell first (deflated, liquid, tradable).

- **Deflated only** shows items worth less than they cost.
- The **tradable overlay** account (● = connected in Ratatoskr) shows which of
  its items can be traded right now.

## Redeploy markets and the target basket

- **Redeploy markets**: the markets where sale money is usable again at once
  (and how many days a hold lasts on each). Sell there when speed matters.
  Blank or repeated markets are dropped when you save. If the saved list cannot
  be read, **Save list** stays off, so a blank list never replaces it.
- **Target basket**: the items you want to rotate into, with your capital in
  dollars; it shows how many units each buys. Adding or removing an item never
  changes the others, and the list stays visible when prices cannot be read.

## Readiness

Free Storage Unit space and how full the loose inventory is for the chosen
account (needs it connected in Ratatoskr). When you switch accounts, a slow
answer for the previous account is ignored, never shown under the new name. Running out of space during a rush
is the classic mistake — buy Storage Units beforehand with
[Buy Storage Units](ratatoskr.md#buy-storage-units).
