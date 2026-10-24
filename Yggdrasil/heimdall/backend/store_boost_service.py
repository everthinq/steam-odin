"""Store Boosts: catch a price boost of an in-game store item on its first day.

A **price boost** (Ivan's definition): someone suddenly buys an item on the Steam Community
Market in large portions and at higher and higher prices, to resell the same item on
third-party markets, which follow Steam's price. The in-game store sells any number of copies
at one fixed price, so during a boost a store copy is worth far more than it costs. Store copies
cannot be traded for 7 days, so only a purchase on the first day unlocks while the price is high
(Flickshot, January 2026: three days late cost about 80% of the upside).

Read-only, a recommendation and an alarm: nothing here buys or sells.

**What is watched**: every tradable store item (the Store Catalogue Arbitrage's candidates) and
every sticker or patch inside the store's sticker capsules and patch packs (Ratatoskr's
``/items/capsule-contents``). A boosted sticker warned about its capsule about 20 hours early in
October 2026 (Sticker | World Eaters 1 → the Warhammer 40,000 Traitor Astartes capsule).

**Two sources**:

- **pulse, every hour** (job ``store-boosts`` on the Pulse feed page): Steam's lowest listing,
  highest buy order and 30-day median, and Buff163's lowest listing, for every watched item.
  A Steam price well above its 30-day median (``PREFLAG_RATIO``) puts the item at the front of
  the Steam queue. Buff163 and Steam buy orders also price each capsule's opening value.
- **Steam's own price history** (``pricehistory``, needs a logged-in web session, 429-prone):
  hourly median price and number of sales for the last 30 days. One item at a time, at least
  ``store_boost_steam_gap_seconds`` apart and through the shared steamcommunity.com pacer,
  backing off on 429. Order: items pulse flagged or you asked for, then store items not read
  for ``STORE_ITEM_EVERY`` seconds, then capsule contents not read for ``CONTENT_EVERY``.

**The detector** (``analyze``; tuned on Flickshot, World Eaters 1 and the Traitor Astartes
capsule, see tests/test_store_boost_service.py): the usual price is the median daily price of
days 30 to 3 ago, the usual sales the median sales a day. Sales alone never alarm (the capsule
had about 30 sales spikes of 5–76 times with no price move); the signal is sales AND price:

- ``watch``: price at least 1.2 times usual and sales at least 3 times usual (10+ sales in 24 hours);
- ``boost``: price at least 2 times usual and sales at least 3 times usual (20+ sales in 24 hours);
- after a peak of at least 2 times usual in the last 14 days: ``boost`` while the 24-hour price
  holds 70% of the peak, ``second_wave`` when it climbs 1.3 times off its low since the peak,
  ``fading`` (sell) when it fell under 70% of the peak but is still 1.2 times usual.

**Confirmed**: a store item whose best cash buy order (Store Catalogue Arbitrage's markets,
after fees) pays at least the store price; a capsule whose opening value (Buff163 listings of its
contents, weighted by rarity odds) beats its store price.

**Alerts** (``store_boost_alerts_enabled``): a message to the shared Huginn bot for each new
level of an episode (watch, boost, fading, second wave; an episode ends after 3 calm days), and a
phone ring (``store_boost_ring_enabled``, ``TelegramCaller``) when a store item or a capsule's
sticker reaches ``boost``. An item seen for the first time only records, unless its rise began in
the last 48 hours. State: ``cache/store_boosts.json.gz`` and ``cache/store_boost_alerts.json``.
"""
import datetime
import gzip
import html
import json
import logging
import os
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import community_pacer
from card_deals_service import RateLimited, parse_sale_history
from huginn_service import pulse_short_link
from jsonio import atomic_write_json, read_json
from notifications import notification_channel, send_notification
from pulse_feed import FRESH, ON_DEMAND, SwitchedOff, is_stale
from store_arbitrage_service import TRADE_HOLD_DAYS, split_items

log = logging.getLogger(__name__)

STATE_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'store_boosts.json.gz')
ALERT_STATE_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'store_boost_alerts.json')

BOOST_JOB = 'store-boosts'
HOUR, DAY = 3600, 86400

# ---- the detector's thresholds (see the module docstring) -----------------------------------
BASELINE_FROM_DAYS, BASELINE_TO_DAYS = 30, 3
BASELINE_MIN_DAYS = 5               # fewer priced days than this: not enough history to judge
WATCH_PRICE_RATIO, WATCH_SALES_RATIO, WATCH_MIN_SALES = 1.2, 3.0, 10
BOOST_PRICE_RATIO, BOOST_SALES_RATIO, BOOST_MIN_SALES = 2.0, 3.0, 20
EPISODE_DAYS = 14                   # a peak this recent still shapes the level
PEAK_MIN_DAY_SALES = 3              # a day with fewer sales cannot be the peak
HOLD_SHARE_OF_PEAK = 0.7
SECOND_WAVE_RISE = 1.3
FADING_FLOOR_RATIO = 1.2
# pulse's hourly pre-flag: Steam's highest buy order this many times its 30-day median (buyers
# paying up), or its lowest listing PREFLAG_LISTING_RATIO times (the cheap listings bought out;
# the lowest listing sits 1.3–1.4 times above the sale median on a normal day, live 2026-10-24),
# and at least PREFLAG_MIN_GAIN dollars above it (a $0.03 sticker moving one cent is not a boost).
PREFLAG_RATIO = 1.3
PREFLAG_LISTING_RATIO = 1.5
PREFLAG_MIN_GAIN = 0.03

LEVELS = ('unknown', 'calm', 'fading', 'watch', 'second_wave', 'boost')
SEVERITY = {level: index for index, level in enumerate(LEVELS)}
ACTIVE_LEVELS = ('watch', 'boost', 'fading', 'second_wave')

# Case odds by rarity tier (Valve's published odds: High Grade 80%, Remarkable 16%, Exotic 3.2%,
# Extraordinary 0.64%), shared equally inside a tier, rescaled to the tiers a capsule has.
TIER_ODDS = {'rare': 0.8, 'mythical': 0.16, 'legendary': 0.032, 'ancient': 0.0064}
TIER_LABEL = {'rare': 'High Grade', 'mythical': 'Remarkable', 'legendary': 'Exotic', 'ancient': 'Extraordinary'}
CAPSULE_CATEGORIES = ('Sticker capsules', 'Patch packs')

# ---- the Steam reader and the hourly pulse check -------------------------------------------
STEAM_HISTORY_URL = 'https://steamcommunity.com/market/pricehistory/'
DEFAULT_STEAM_GAP = 30.0            # seconds between two price history reads
MIN_STEAM_GAP = 10.0
MAX_STEAM_GAP = 3600.0              # an hour at most (a huge value would stop the reader thread)
STORE_ITEM_EVERY = 3 * HOUR         # a store item's history is read again after this
CONTENT_EVERY = 12 * HOUR           # a capsule sticker's history is read again after this
ACTIVE_EVERY = HOUR                 # an item in a boost episode is read again after this
TRACKED_EVERY = 15 * 60             # an item you track (store_boost_tracked) is read again after this
TRACKED_SHARE = 0.5                 # tracked items never take more than half of the reads: the rest still runs
NOT_DOLLARS_FORGET = 6 * HOUR       # an account seen answering in another currency is skipped this long
REJECTED_SESSION_WAIT = 30 * 60     # an account whose session Steam refused is skipped this long
ALERT_SAVE_MIN_INTERVAL = 30
TRACKED_MAX = 30
PRIORITY_MAX = 100                  # items waiting at the front of the Steam queue
FAILED_RETRY = 2 * HOUR             # an item whose read failed waits this long
FAILURES_IN_A_ROW = 5               # this many failed reads in a row: the session is suspect, pause
BACKOFF_FIRST, BACKOFF_MAX = 10 * 60, 2 * HOUR
NO_SESSION_WAIT = 5 * 60
POINTS_KEPT_DAYS = 14               # hourly points kept per item for the charts
WATCH_INTERVAL = HOUR
WATCH_FIRST_WAIT = 3 * 60
READER_FIRST_WAIT = 60              # the backend restarts on every save: never read at once
CONTENTS_EVERY = DAY
SAVE_MIN_INTERVAL = 60
FIRST_SIGHT_ALERT_HOURS = 48
EPISODE_ENDS_AFTER_CALM = 3 * DAY
SECOND_WAVE_REPEAT = 2 * DAY
STEAM_SLUG = 'Steam'


# ---- pure helpers (unit-tested) ------------------------------------------------------------

def parse_history(payload):
    """Steam's ``pricehistory`` answer → [(epoch seconds, US dollars, sales)], oldest first, through
    Andvari's parser (``card_deals_service.parse_sale_history``: UTC hours, "1,234" sales, and
    empty unless Steam answered in US dollars — it answers in the session's wallet currency)."""
    points = [(int(when), cents / 100, sales) for when, cents, sales in parse_sale_history(payload)
              if cents > 0 and sales >= 0]
    points.sort(key=lambda point: point[0])
    return points


def answers_in_dollars(payload):
    """True when a ``pricehistory`` answer is in US dollars (its price prefix "$", no suffix)."""
    return (isinstance(payload, dict) and payload.get('price_prefix') == '$'
            and not (payload.get('price_suffix') or '').strip())


def weighted_price(points):
    """The sales-weighted average price of *points*, or None when nothing sold."""
    sales = sum(point[2] for point in points)
    return sum(point[1] * point[2] for point in points) / sales if sales else None


def _days(points):
    """{UTC day number: [points]}."""
    out = {}
    for point in points:
        out.setdefault(point[0] // DAY, []).append(point)
    return out


def analyze(points, now):
    """What Steam's price history says about an item at *now* (see the module docstring).
    Returns {level, usual_price, usual_sales, price_6h, price_24h, sales_6h, sales_24h,
    price_ratio, sales_ratio, peak, peak_at, low_after_peak, started_at}."""
    now = int(now)
    points = [point for point in points if point[0] <= now]
    first_day = (now - BASELINE_FROM_DAYS * DAY) // DAY
    last_day = (now - BASELINE_TO_DAYS * DAY) // DAY
    by_day = _days(point for point in points if first_day <= point[0] // DAY < last_day)
    day_prices = [price for price in (weighted_price(day_points) for day_points in by_day.values()) if price]
    if len(day_prices) < BASELINE_MIN_DAYS:
        return {'level': 'unknown', 'reason': 'not enough sales in the last 30 days to know the usual price'}
    usual_price = statistics.median(day_prices)
    day_sales = [sum(point[2] for point in by_day.get(day, [])) for day in range(first_day, last_day)]
    usual_sales = max(statistics.median(day_sales), statistics.mean(day_sales) * 0.5, 1.0)

    last_6h = [point for point in points if point[0] > now - 6 * HOUR]
    last_24h = [point for point in points if point[0] > now - 24 * HOUR]
    sales_6h = sum(point[2] for point in last_6h)
    sales_24h = sum(point[2] for point in last_24h)
    price_24h = weighted_price(last_24h)
    price_6h = weighted_price(last_6h) or price_24h
    out = {'level': 'calm', 'usual_price': round(usual_price, 4), 'usual_sales': round(usual_sales, 1),
           'price_6h': _round(price_6h), 'price_24h': _round(price_24h), 'sales_6h': sales_6h, 'sales_24h': sales_24h,
           'price_ratio': None, 'sales_ratio': round(max(sales_24h / usual_sales, sales_6h / (usual_sales / 4)), 1),
           'peak': None, 'peak_at': None, 'low_after_peak': None, 'started_at': None}
    if not price_6h:
        return out
    price_ratio = price_6h / usual_price
    out['price_ratio'] = round(price_ratio, 2)

    # The last 14 days before today, by day (days with a few sales only). A peak is a day at
    # BOOST_PRICE_RATIO times the usual price that also sold at least a usual day's sales: one
    # or two sales of a thin sticker at a high price are not a boost.
    recent = _days(point for point in points if now - EPISODE_DAYS * DAY < point[0] <= now - 24 * HOUR)
    daily = sorted((day, weighted_price(day_points), sum(point[2] for point in day_points))
                   for day, day_points in recent.items()
                   if sum(point[2] for point in day_points) >= PEAK_MIN_DAY_SALES)
    peaks = [(day, price) for day, price, sales in daily
             if price >= BOOST_PRICE_RATIO * usual_price and sales >= usual_sales]
    current = price_24h or price_6h
    current_ratio = current / usual_price
    if peaks:
        peak_day, peak = max(peaks, key=lambda day_price: day_price[1])
        if current > peak:
            peak_day, peak = now // DAY, current
        after = [price for day, price, _ in daily if day > peak_day]
        low = min(after) if after else None
        previous_24h = weighted_price([point for point in points if now - 48 * HOUR < point[0] <= now - 24 * HOUR])
        if current >= HOLD_SHARE_OF_PEAK * peak and current_ratio >= BOOST_PRICE_RATIO:
            level = 'boost' if sales_24h >= WATCH_MIN_SALES else 'watch'
        elif (low and current >= SECOND_WAVE_RISE * low and current_ratio >= FADING_FLOOR_RATIO
              and previous_24h and current > previous_24h and sales_24h >= WATCH_MIN_SALES):
            level = 'second_wave'
        elif current_ratio >= FADING_FLOOR_RATIO:
            level = 'fading'
        else:
            level = 'calm'
        out.update(peak=round(peak, 4), peak_at=int(peak_day * DAY), low_after_peak=_round(low))
    elif (price_ratio >= BOOST_PRICE_RATIO and out['sales_ratio'] >= BOOST_SALES_RATIO
          and sales_24h >= BOOST_MIN_SALES):
        level = 'boost'
    elif (price_ratio >= WATCH_PRICE_RATIO and out['sales_ratio'] >= WATCH_SALES_RATIO
          and sales_24h >= WATCH_MIN_SALES):
        level = 'watch'
    else:
        level = 'calm'
    out['level'] = level
    if level != 'calm':
        out['started_at'] = rise_started_at(points, now, usual_price)
    return out


def _round(value, digits=4):
    return round(value, digits) if value else None


def rise_started_at(points, now, usual_price):
    """The start of the run of days (back from today, at most 14) whose price stayed at least
    WATCH_PRICE_RATIO times the usual price, or the first such hour today."""
    now = int(now)
    by_day = _days(point for point in points if now - EPISODE_DAYS * DAY < point[0] <= now)
    day = now // DAY if now // DAY in by_day else now // DAY - 1     # just after midnight: from yesterday
    started = None
    while day in by_day:
        price = weighted_price(by_day.get(day, []))
        if not price or price < WATCH_PRICE_RATIO * usual_price:
            break
        started = day
        day -= 1
    if started is None:
        return None
    first = [point[0] for point in by_day.get(started, []) if point[1] >= WATCH_PRICE_RATIO * usual_price]
    return min(first) if first else int(started * DAY)


def spark(points, now, days=7, bucket_hours=6):
    """[{time, price, sales}] in *bucket_hours* buckets over the last *days* days (price None when
    nothing sold): the row's small chart."""
    size = bucket_hours * HOUR
    start = (now - days * DAY) // size * size
    buckets = {}
    for point in points:
        if point[0] >= start:
            buckets.setdefault(point[0] // size * size, []).append(point)
    out = []
    for bucket in range(start, now + 1, size):
        chunk = buckets.get(bucket, [])
        price = weighted_price(chunk)
        out.append({'time': bucket, 'price': _round(price), 'sales': sum(point[2] for point in chunk)})
    return out


def preflag(quote):
    """True when pulse's Steam prices look like a boost starting: the highest buy order at least
    PREFLAG_RATIO times the 30-day median, or the lowest listing PREFLAG_LISTING_RATIO times, and
    PREFLAG_MIN_GAIN dollars above it."""
    median = (quote or {}).get('median30d')
    if not median:
        return False
    buy, listing = quote.get('buy') or 0, quote.get('listing') or 0
    return ((buy >= PREFLAG_RATIO * median and buy - median >= PREFLAG_MIN_GAIN)
            or (listing >= PREFLAG_LISTING_RATIO * median and listing - median >= PREFLAG_MIN_GAIN))


def item_chances(items):
    """{name: chance of getting it from one capsule} for [{name, tier}] (TIER_ODDS, rescaled to the
    tiers present, shared equally inside a tier)."""
    tiers = {}
    for item in items or []:
        tiers.setdefault(item.get('tier') or 'rare', []).append(item['name'])
    total = sum(TIER_ODDS.get(tier, TIER_ODDS['rare']) for tier in tiers)
    out = {}
    for tier, names in tiers.items():
        share = TIER_ODDS.get(tier, TIER_ODDS['rare']) / total / len(names)
        for name in names:
            out[name] = out.get(name, 0) + share
    return out


def opening_value(items, prices):
    """What one capsule is worth opened, priced by *prices* {name: price}: {value, priced_share,
    top: [{name, chance, price, part}]} or None when nothing is priced. The value counts the
    priced contents only; priced_share says how much of the odds that covers."""
    chances = item_chances(items)
    parts = [{'name': name, 'chance': chance, 'price': prices[name], 'part': chance * prices[name]}
             for name, chance in chances.items() if prices.get(name)]
    if not parts:
        return None
    parts.sort(key=lambda part: part['part'], reverse=True)
    return {'value': round(sum(part['part'] for part in parts), 3),
            'priced_share': round(sum(part['chance'] for part in parts), 3),
            'top': [{**part, 'chance': round(part['chance'], 4), 'part': round(part['part'], 3)} for part in parts[:3]]}


def market_name_candidates(item, contents=None):
    """The Steam Market names a store item may have, most likely first. The store names some items
    differently from the market (live 2026-10-25: "Bestiary Sticker Capsule" is "Bestiary Capsule" on
    the market, "Music Kit | Darude, Moments CS:GO" is "… CSGO", "StatTrak™ Radicals Music Kit Box"
    is "StatTrak™ Radicals Box"): the capsule's own name from the game files (Ratatoskr's capsule
    contents), then the store's name, without colons, and "… Box" for "… Music Kit Box"."""
    name = item['name']
    names = [((contents or {}).get(item['entry']) or {}).get('capsule'), name, name.replace(':', ''),
             name.replace(' Music Kit Box', ' Box')]
    return [name for name in dict.fromkeys(names) if name]


def resolve_market_names(items, contents, known):
    """{store name: market name} for the store items whose market name differs, picked as the first
    candidate *known* (the names pulse's Steam table holds). Unknown items keep the store's name."""
    out = {}
    for item in items:
        found = next((name for name in market_name_candidates(item, contents) if name in known), None)
        if found and found != item['name']:
            out[item['name']] = found
    return out


def alert_decision(state, level, analysis, now):
    """(new state, level to alert or None) for one item's new *level*. *state*: {level, episode,
    sent: {level: when}, calm_since, seen} or None for an item never seen. A level is sent once
    an episode (a second wave again after SECOND_WAVE_REPEAT), a watch never after a boost; the
    first sight only records unless the rise began in the last FIRST_SIGHT_ALERT_HOURS."""
    first_sight = not state or not state.get('level')     # never seen, or seen only as 'unknown'
    state = dict(state or {})
    sent = dict(state.get('sent') or {})
    send = None
    if level in ACTIVE_LEVELS:
        if not state.get('episode'):
            state['episode'] = now
            sent = {}
        state['calm_since'] = None
        if level == 'second_wave':
            due = state.get('level') != 'second_wave' and now - sent.get('second_wave', 0) >= SECOND_WAVE_REPEAT
        elif level == 'watch':
            due = 'watch' not in sent and 'boost' not in sent
        else:
            due = level not in sent
        if due and first_sight:
            started = (analysis or {}).get('started_at')
            due = level in ('watch', 'boost') and bool(started) and now - started <= FIRST_SIGHT_ALERT_HOURS * HOUR
        if due:
            send = level
        if due or first_sight:
            sent[level] = now           # the first sight records the level as known
    elif level == 'calm' and state.get('episode'):
        state['calm_since'] = state.get('calm_since') or now
        if now - state['calm_since'] >= EPISODE_ENDS_AFTER_CALM:
            state['episode'], sent, state['calm_since'] = None, {}, None
    if level != 'unknown':
        state['level'] = level
    state['sent'] = sent
    state['seen'] = state.get('seen') or now
    return state, send


def _escape(text):
    return html.escape(str(text), quote=False)


def _dollars(value):
    return f'${value:.2f}' if value is not None else '?'


ALERT_TITLES = {
    'watch': '👀 Early price boost sign',
    'boost': '🚀 Price boost',
    'second_wave': '🔁 Second wave of a price boost',
    'fading': '📉 Price boost turning: time to sell',
}


def render_alert(level, name, analysis, store=None, capsules=(), confirm=None, now=None):
    """(plain, html) of one alert. *store*: {cost} when *name* itself is sold in the store;
    *capsules*: [{name, cost, opening}] store capsules that hold it; *confirm*: the best cash buy
    order {display, net} for a store item."""
    now = now or time.time()
    title = f"{ALERT_TITLES.get(level, level)}: {name}"
    ratio = analysis.get('price_ratio')
    lines = [f"Steam {_dollars(analysis.get('price_6h'))} now, {ratio:g}× its usual {_dollars(analysis.get('usual_price'))}"
             if ratio else f"Steam {_dollars(analysis.get('price_6h'))} now"]
    lines.append(f"{analysis.get('sales_24h', 0)} sold in 24 hours ({analysis.get('sales_ratio', 0):g}× the usual "
                 f"{analysis.get('usual_sales', 0):g} a day)")
    if analysis.get('peak') and level in ('fading', 'second_wave'):
        lines.append(f"Peak {_dollars(analysis['peak'])}"
                     + (f", low since {_dollars(analysis['low_after_peak'])}" if analysis.get('low_after_peak') else ''))
    if store:
        lines.append(f"In-game store: {_dollars(store['cost'])}")
        if confirm:
            mark = '✅' if confirm['net'] >= store['cost'] else '⏳'
            lines.append(f"{mark} {confirm['display']} pays {_dollars(confirm['net'])} at once after its fee")
    for capsule in capsules:
        line = f"Inside {capsule['name']} (in-game store {_dollars(capsule['cost'])})"
        opening = capsule.get('opening')
        if opening:
            line += f": opening worth about {_dollars(opening['value'])} on Buff163"
        lines.append(line)
    unlock = datetime.datetime.fromtimestamp(now + TRADE_HOLD_DAYS * DAY).strftime('%d %b %H:%M')
    if not store and not capsules:
        foot = 'Not sold in the in-game store: tracked on Steam only. Store Catalogue → Boosts.'
    elif level in ('watch', 'boost', 'second_wave'):
        foot = (f"Bought in the store now, tradable from {unlock}. Store Catalogue → Boosts; buying stays "
                'on the guarded Buy page.')
    else:
        foot = 'Sell what you hold before third-party markets follow Steam down. Store Catalogue → Boosts.'
    steam = pulse_short_link(STEAM_SLUG, name)
    plain = '\n'.join([title, *lines, f'Steam Community Market: {steam}', foot])
    body = '\n'.join([f'<b>{_escape(title)}</b>', *(_escape(line) for line in lines),
                      f'<a href="{steam}">Steam Community Market</a>', f'<i>{_escape(foot)}</i>'])
    return plain, body


def steam_gap(settings):
    try:
        value = float((settings or {}).get('store_boost_steam_gap_seconds') or DEFAULT_STEAM_GAP)
    except (TypeError, ValueError):
        return DEFAULT_STEAM_GAP
    return min(MAX_STEAM_GAP, max(MIN_STEAM_GAP, value))


class SessionRejected(RuntimeError):
    """Steam refused the web session (HTTP 401 or 403)."""


def fetch_steam_history(name, cookies, timeout=20):
    """Steam's ``pricehistory`` answer for *name* (logged in), as JSON. Raises RateLimited
    (card_deals_service's) on 429 and SessionRejected on 401 or 403 (a name Steam does not know answers 400 or 500,
    which is that item's error)."""
    url = STEAM_HISTORY_URL + '?' + urllib.parse.urlencode(
        {'country': 'US', 'currency': 1, 'appid': 730, 'market_hash_name': name})
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Heimdall Store Boosts)',
        'Cookie': '; '.join(f'{key}={value}' for key, value in cookies.items())})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited('Steam answered 429 (too many requests)') from e
        if e.code in (401, 403):
            raise SessionRejected(f'Steam refused the web session (HTTP {e.code})') from e
        raise


# ---- the service ---------------------------------------------------------------------------

class StoreBoostService:
    def __init__(self, huginn_service, catalogue_service, ratatoskr_service, steam_service,
                 store_arbitrage_service, telegram_caller=None, state_path=STATE_PATH,
                 alert_state_path=ALERT_STATE_PATH, notify=send_notification, fetch=fetch_steam_history,
                 clock=time.time, pace=community_pacer.pace):
        self.huginn = huginn_service
        self.catalogue = catalogue_service
        self.ratatoskr = ratatoskr_service
        self.steam = steam_service
        self.arbitrage = store_arbitrage_service
        self.telegram_caller = telegram_caller
        self.state_path = state_path
        self.alert_state_path = alert_state_path
        self._notify = notify
        self._fetch = fetch
        self._clock = clock
        self._pace = pace
        self._lock = threading.Lock()
        self._alert_lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._settings_provider = lambda: {}
        self._started = False
        self._wake = threading.Event()
        self._priority = []          # names to read first, oldest request first
        self._dirty = False
        self._saved_at = 0.0
        self._reader = {'status': 'idle', 'reading': None, 'last_read_at': None, 'last_name': None,
                        'blocked_until': None, 'backoff': 0, 'error': None, 'reads': []}
        self._check = {'running': False, 'checked_at': None, 'error': None, 'started_at': None}
        self._not_dollars = {}       # steamid -> when its web session answered in another currency
        self._rejected = {}          # steamid -> when Steam refused its web session
        self._alerts_dirty = False
        self._alerts_saved_at = 0.0
        self._failures_in_row = 0
        self._store_cache = (0.0, [])
        state = self._load()
        self._items = state.get('items') or {}        # name -> {read_at, points, analysis, error}
        self._pulse = state.get('pulse') or {}        # name -> {listing, buy, median30d, buff, updated, preflag}
        self._contents = state.get('contents') or {}  # entry -> {capsule, items}
        self._contents_at = state.get('contents_at') or 0
        self._market_names = state.get('market_names') or {}   # store name -> Steam Market name, when they differ
        self._check['checked_at'] = state.get('checked_at')
        self._alerts = self._load_alert_state()        # {items: {name: episode state}, log: [...]}, kept in memory

    # ---- state on disk -------------------------------------------------------------------------

    def _load(self):
        try:
            with gzip.open(self.state_path, 'rt', encoding='utf-8') as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            aside = f'{self.state_path}.unreadable-{int(time.time())}'
            log.warning('[STORE-BOOSTS] state unreadable (%s): moved to %s', e, aside)
            try:
                os.replace(self.state_path, aside)
            except OSError:
                pass
            return {}

    def _save(self, force=False):
        self._save_alerts(force)
        with self._save_lock:
            with self._lock:
                if not self._dirty or (not force and self._clock() - self._saved_at < SAVE_MIN_INTERVAL):
                    return
                # Shallow copies under the lock (entries are replaced, never changed in place);
                # the slow dump runs outside it.
                state = {'items': dict(self._items), 'pulse': dict(self._pulse), 'contents': dict(self._contents),
                         'contents_at': self._contents_at, 'checked_at': self._check['checked_at'],
                         'market_names': dict(self._market_names)}
                self._dirty, self._saved_at = False, self._clock()
            snapshot = json.dumps(state, separators=(',', ':'))
            temporary = f'{self.state_path}.{os.getpid()}.{threading.get_ident()}.tmp'
            try:
                os.makedirs(os.path.dirname(self.state_path) or '.', exist_ok=True)
                with gzip.open(temporary, 'wt', encoding='utf-8') as handle:
                    handle.write(snapshot)
                os.replace(temporary, self.state_path)
            except OSError as e:
                log.error('[STORE-BOOSTS] could not save %s: %s', self.state_path, e)
                with self._lock:
                    self._dirty = True

    # ---- what is watched -------------------------------------------------------------------------

    def _store_items(self):
        """The tradable store items (``split_items``) under their Steam Market name ('name'; the
        store's own in 'store_name'), kept 15 seconds: the catalogue's status() copies the whole
        catalogue, and every reader step and board asks for it."""
        cached_at, items = self._store_cache
        if self._clock() - cached_at < 15 and items:
            return items
        raw, _ = split_items(self.catalogue.status().get('items'))
        with self._lock:
            names = dict(self._market_names)
        items = [{**item, 'name': names.get(item['name'], item['name']), 'store_name': item['name']} for item in raw]
        self._store_cache = (self._clock(), items)
        return items

    def _refresh_contents(self, store_items, force=False):
        """Ask Ratatoskr what each store capsule holds (once a day; the last answer is kept
        when Ratatoskr is down)."""
        entries = [item['entry'] for item in store_items if item.get('category') in CAPSULE_CATEGORIES]
        missing = [entry for entry in entries if entry not in self._contents]
        if not entries or (not force and not missing and self._clock() - self._contents_at < CONTENTS_EVERY):
            return
        answer = self.ratatoskr.capsule_contents(entries) or {}
        contents = answer.get('contents')
        if not isinstance(contents, dict):
            log.info('[STORE-BOOSTS] capsule contents unavailable: %s', answer.get('error'))
            return
        with self._lock:
            self._contents = {entry: value for entry, value in contents.items()
                              if isinstance(value, dict) and isinstance(value.get('items'), list)}
            self._contents_at = self._clock()
            self._dirty = True
        log.info('[STORE-BOOSTS] capsule contents: %d capsules, %d items', len(self._contents),
                 sum(len(value['items']) for value in self._contents.values()))

    def _watched(self, store_items=None):
        """(store item names, capsule content names, {content name: [capsule entries]})."""
        store_items = self._store_items() if store_items is None else store_items
        store_names = [item['name'] for item in store_items]
        entries = {item['entry'] for item in store_items}
        holders = {}
        with self._lock:
            contents = dict(self._contents)
        for entry, value in contents.items():
            if entry not in entries:
                continue
            for item in value['items']:
                holders.setdefault(item['name'], []).append(entry)
        store_set = set(store_names)
        content_names = [name for name in holders if name not in store_set]
        return store_names, content_names, holders

    # ---- the hourly pulse check ------------------------------------------------------------------

    def job_tables(self):
        """The pulse tables the hourly check reads (the Pulse feed page)."""
        out = []
        for market_id, side in (('Steam', 'listing'), ('Steam', 'instant'), ('Buff', 'listing')):
            key = self.huginn.pulse_table_of(market_id, side)
            if key:
                out.append(key)
        out.append(('Steam', 'Median30d'))
        return out

    def check_now(self, max_age=ON_DEMAND):
        """Run the pulse check in the background now (the Check now button)."""
        settings = self._settings_provider() or {}
        token = settings.get('tradeon_token', '')
        if not token:
            return {'started': False, 'error': 'no Tradeon token (tradeon_token in backend/settings.json)'}
        with self._lock:
            if self._check['running']:
                return {'started': False, 'error': 'a check is already running'}
            self._check.update(running=True, started_at=self._clock(), error=None)

        def run():
            try:
                self._pulse_check(token, max_age)
            finally:
                with self._lock:
                    self._check['running'] = False
        threading.Thread(target=run, daemon=True, name='store-boosts-check').start()
        return {'started': True}

    def market_names(self):
        """{store name: Steam Market name} for the store items named differently on the market."""
        with self._lock:
            return dict(self._market_names)

    def _resolve_market_names(self, token, max_age=FRESH):
        """Find each store item's Steam Market name among pulse's Steam listings (resolve_market_names)."""
        raw, _ = split_items(self.catalogue.status().get('items'))
        with self._lock:
            contents = dict(self._contents)
        candidates = {name for item in raw for name in market_name_candidates(item, contents)}
        with self.huginn.pulse.job(BOOST_JOB):
            _, known = self.huginn.market_index_at(token, 'Steam', 'listing', max_age, names=candidates)
        found = resolve_market_names(raw, contents, set(known))
        with self._lock:
            changed = found != self._market_names
            self._market_names = found
            self._dirty = self._dirty or changed
        if changed:
            self._store_cache = (0.0, [])
            log.info('[STORE-BOOSTS] %d store items have another name on the Steam Market', len(found))

    def _pulse_check(self, token, max_age=FRESH):
        """Read Steam's and Buff163's prices of every watched item from pulse, flag the items whose
        Steam price runs ahead of its 30-day median and queue them for a Steam read."""
        try:
            self._refresh_contents(self._store_items())
            self._resolve_market_names(token, max_age)
            store_items = self._store_items()
            store_names, content_names, _ = self._watched(store_items)
            names = set(store_names) | set(content_names) | set(self.tracked())
            if not names:
                return
            with self.huginn.pulse.job(BOOST_JOB):
                _, listing = self.huginn.market_index_at(token, 'Steam', 'listing', max_age, names=names)
                _, buy = self.huginn.market_index_at(token, 'Steam', 'instant', max_age, names=names)
                _, buff = self.huginn.market_index_at(token, 'Buff', 'listing', max_age, names=names)
                median = self.huginn._table(token, 'Steam', 'Median30d', max_age).index(names)
            now = self._clock()
            flagged = []
            with self._lock:
                for name in names:
                    quote = {'listing': (listing.get(name) or {}).get('price'),
                             'listing_count': (listing.get(name) or {}).get('count'),
                             'buy': (buy.get(name) or {}).get('price'),
                             'buy_count': (buy.get(name) or {}).get('count'),
                             'median30d': (median.get(name) or {}).get('price'),
                             'buff': None if is_stale((buff.get(name) or {}).get('updated'), now)
                             else (buff.get(name) or {}).get('price'),
                             'updated': (listing.get(name) or {}).get('updated')}
                    quote['preflag'] = preflag(quote)
                    # Flagged and not read in the last hour: to the front of the Steam queue.
                    if quote['preflag'] and now - ((self._items.get(name) or {}).get('read_at') or 0) >= ACTIVE_EVERY:
                        flagged.append(name)
                    self._pulse[name] = quote
                # Forget items no longer watched (left the store, untracked, a one-off read).
                keep = names | set(self._priority)
                for state in (self._items, self._pulse):
                    for name in [name for name in state if name not in keep]:
                        del state[name]
                self._check.update(checked_at=now, error=None)
                self._dirty = True
            for name in flagged:
                self.read_now(name, reason='pulse')
            log.info('[STORE-BOOSTS] pulse check: %d items, %d flagged for a Steam read', len(names), len(flagged))
        except SwitchedOff as e:
            with self._lock:
                self._check['error'] = f'switched off on the Pulse feed page: {e}'
        except Exception as e:
            log.warning('[STORE-BOOSTS] pulse check failed: %s', e)
            with self._lock:
                self._check['error'] = str(e)[:200] or type(e).__name__
        self._save(force=True)

    # ---- the Steam reader ------------------------------------------------------------------------

    def read_now(self, name, reason='asked'):
        """Put *name* at the front of the Steam queue."""
        if not name:
            return {'queued': False, 'error': 'no item name'}
        with self._lock:
            if name not in self._priority:
                if len(self._priority) >= PRIORITY_MAX:
                    return {'queued': False, 'error': f'the queue already holds {PRIORITY_MAX} items'}
                self._priority.append(name)
        self._wake.set()
        log.info('[STORE-BOOSTS] %s queued for a Steam read (%s)', name, reason)
        return {'queued': True, 'position': self._priority.index(name) + 1 if name in self._priority else 0}

    def tracked(self):
        """The items you track (settings store_boost_tracked): any Steam Market name, read every
        TRACKED_EVERY seconds and shown on top of the tab."""
        names = (self._settings_provider() or {}).get('store_boost_tracked') or []
        return [name for name in dict.fromkeys(names) if isinstance(name, str) and name][:TRACKED_MAX]

    def tracked_every(self, settings=None):
        """Seconds between two reads of a tracked item: TRACKED_EVERY, longer when many are tracked,
        so they never take more than TRACKED_SHARE of the reads."""
        settings = settings if settings is not None else (self._settings_provider() or {})
        return max(TRACKED_EVERY, len(self.tracked()) * steam_gap(settings) / TRACKED_SHARE)

    def _next(self, now):
        """The next item to read: the front of the queue, the items you track (every
        TRACKED_EVERY), items in a boost (ACTIVE_EVERY), then the store item read longest ago
        (older than STORE_ITEM_EVERY), then the capsule content read longest ago (CONTENT_EVERY)."""
        with self._lock:
            if self._priority:
                return self._priority.pop(0), 'priority'
            items = dict(self._items)
        store_names, content_names, _ = self._watched()

        def due(names, every):
            waiting = []
            for name in names:
                entry = items.get(name) or {}
                wait = FAILED_RETRY if entry.get('error') else every
                last = entry.get('read_at') or 0
                if now - last >= wait:
                    waiting.append((last, name))
            return min(waiting)[1] if waiting else None
        name = due(self.tracked(), self.tracked_every())
        if name:
            return name, 'tracked'
        active = [name for name in store_names + content_names
                  if ((items.get(name) or {}).get('analysis') or {}).get('level') in ACTIVE_LEVELS]
        name = due(active, ACTIVE_EVERY)
        if name:
            return name, 'active'
        # A store item pulse knows no Steam price for is not on the market (the Sticker Slab tool):
        # once a pulse check has run, it is not read (it would fail every two hours).
        with self._lock:
            pulse = dict(self._pulse)
        if pulse:
            store_names = [name for name in store_names
                           if any((pulse.get(name) or {}).get(key) for key in ('listing', 'buy', 'median30d'))]
        name = due(store_names, STORE_ITEM_EVERY)
        if name:
            return name, 'store'
        name = due(content_names, CONTENT_EVERY)
        return (name, 'contents') if name else (None, None)

    def queue_status(self):
        now = self._clock()
        store_names, content_names, _ = self._watched()
        with self._lock:
            items = dict(self._items)
            priority = list(self._priority)

        def fresh(names, every):
            return sum(1 for name in names if now - ((items.get(name) or {}).get('read_at') or 0) < every)
        return {'priority': priority,
                'store': {'total': len(store_names), 'fresh': fresh(store_names, STORE_ITEM_EVERY)},
                'contents': {'total': len(content_names), 'fresh': fresh(content_names, CONTENT_EVERY)}}

    def _session(self):
        """(steamid, cookies) of the first account with a fresh web session that is not known to
        answer in another currency than US dollars, or None."""
        storage = getattr(self.steam, 'storage', None) if self.steam else None
        now = self._clock()
        for steamid in (storage.list_accounts() if storage else []):
            if now - self._not_dollars.get(steamid, -NOT_DOLLARS_FORGET) < NOT_DOLLARS_FORGET:
                continue
            if now - self._rejected.get(steamid, -REJECTED_SESSION_WAIT) < REJECTED_SESSION_WAIT:
                continue
            cookies = self.steam.web_session_cookie_for(steamid)
            if cookies:
                return steamid, cookies
        return None

    def _no_session_reason(self):
        now = self._clock()
        if any(now - when < NOT_DOLLARS_FORGET for when in self._not_dollars.values()):
            return ('no account with a fresh Steam web session answers in US dollars (accounts with another '
                    'wallet currency are skipped for 6 hours)')
        if any(now - when < REJECTED_SESSION_WAIT for when in self._rejected.values()):
            return 'Steam refused the web sessions; tried again within 30 minutes (or log an account in)'
        return 'no account has a fresh Steam web session (log one in, or wait for the keep-alive)'

    def _read_loop(self):
        time.sleep(READER_FIRST_WAIT)
        while True:
            try:
                wait = self._read_step()
            except Exception:
                log.exception('[STORE-BOOSTS] reader step failed')
                wait = 60
            if wait:
                self._wake.wait(wait)
                self._wake.clear()

    def _set_reader(self, **values):
        with self._lock:
            self._reader.update(values)

    def _read_step(self):
        """Read one item if one is due; returns how long to wait before the next step."""
        settings = self._settings_provider() or {}
        if not settings.get('store_boost_enabled', True):
            self._set_reader(status='off', reading=None)
            return 60
        now = self._clock()
        blocked = self._reader.get('blocked_until')
        if blocked and now < blocked:
            if self._reader.get('status') != 'session_failing':
                self._set_reader(status='backing_off')
            return min(60, blocked - now)
        gap = steam_gap(settings)
        last = self._reader.get('last_read_at') or 0
        if now - last < gap:
            return gap - (now - last)
        name, why = self._next(now)
        if not name:
            self._set_reader(status='idle', reading=None)
            return 30
        # The session only once something is due (it decrypts maFiles).
        got = self._session()
        if not got:
            with self._lock:
                if why == 'priority' and name not in self._priority:
                    self._priority.insert(0, name)
            self._set_reader(status='no_session', reading=None, error=self._no_session_reason())
            return NO_SESSION_WAIT
        self._set_reader(status='reading', reading=name)
        self._pace()
        started = self._clock()
        try:
            payload = self._fetch(name, got[1])
            if isinstance(payload, dict) and payload.get('success') and not answers_in_dollars(payload):
                # Another wallet currency: never mix it with dollars; read with another account.
                with self._lock:
                    self._not_dollars[got[0]] = self._clock()
                    if name not in self._priority:
                        self._priority.insert(0, name)
                    self._reader.update(reading=None, last_read_at=self._clock())
                log.info('[STORE-BOOSTS] account %s answers in %s, not US dollars: using another',
                         got[0], payload.get('price_prefix') or payload.get('price_suffix'))
                return 1
            if not isinstance(payload, dict) or not payload.get('success'):
                raise ValueError('Steam has no price history for it')
        except RateLimited as e:
            with self._lock:
                backoff = min(BACKOFF_MAX, (self._reader.get('backoff') or BACKOFF_FIRST // 2) * 2)
                self._reader.update(status='backing_off', reading=None, backoff=backoff, error=str(e),
                                    blocked_until=self._clock() + backoff, last_read_at=self._clock())
                if why == 'priority' and name not in self._priority:
                    self._priority.insert(0, name)
            log.warning('[STORE-BOOSTS] Steam 429: waiting %d minutes', backoff // 60)
            return 60
        except SessionRejected as e:
            # The account's fault, not the item's: skip the account a while, read the item next
            # with another one (when none is left, the reader says "no session").
            with self._lock:
                self._rejected[got[0]] = self._clock()
                if name not in self._priority:
                    self._priority.insert(0, name)
                self._reader.update(status='waiting', reading=None, last_read_at=self._clock(),
                                    error=f'{got[0]}: {e}')
            log.warning('[STORE-BOOSTS] %s: %s, trying another account', got[0], e)
            return 1
        except Exception as e:
            with self._lock:
                entry = dict(self._items.get(name) or {})
                entry.update(read_at=self._clock(), error=str(e)[:200] or type(e).__name__)
                self._items[name] = entry
                # Only network failures (no answer at all) count towards a pause: an item Steam has
                # no history for (HTTP 400 or 500, an empty answer) is that item's problem, retried
                # after FAILED_RETRY, and never pauses the reader.
                network = isinstance(e, (urllib.error.URLError, OSError)) and not isinstance(e, urllib.error.HTTPError)
                self._failures_in_row = self._failures_in_row + 1 if network else 0
                suspect = self._failures_in_row >= FAILURES_IN_A_ROW
                if suspect:
                    self._failures_in_row = 0
                self._reader.update(status='session_failing' if suspect else 'waiting', reading=None,
                                    last_read_at=self._clock(), last_name=name, error=f'{name}: {str(e)[:200]}',
                                    blocked_until=self._clock() + NO_SESSION_WAIT if suspect else None)
                self._dirty = True
            self._save()
            return NO_SESSION_WAIT if suspect else 1
        self._take_history(name, payload, started)
        with self._lock:
            self._failures_in_row = 0
            reads = [read_at for read_at in self._reader['reads'] if self._clock() - read_at < HOUR] + [self._clock()]
            self._reader.update(status='waiting', reading=None, last_read_at=self._clock(), last_name=name,
                                backoff=0, blocked_until=None, error=None, reads=reads)
        self._save()
        return 1

    def _take_history(self, name, payload, now=None):
        """Store a fresh Steam history of *name*, analyse it and alert on a new level."""
        now = int(now or self._clock())
        points = parse_history(payload)
        analysis = analyze(points, now)
        kept = [list(point) for point in points if point[0] > now - POINTS_KEPT_DAYS * DAY]
        with self._lock:
            self._items[name] = {'read_at': now, 'points': kept, 'analysis': analysis, 'error': None}
            self._dirty = True
        self._after_analysis(name, analysis, now)

    # ---- alerts ----------------------------------------------------------------------------------

    def _load_alert_state(self):
        state = read_json(self.alert_state_path, default={}) or {}
        return state if isinstance(state, dict) else {}

    def _save_alerts(self, force=False):
        """Write the alert state (kept in memory) at most every ALERT_SAVE_MIN_INTERVAL seconds."""
        with self._alert_lock:
            if not self._alerts_dirty or (not force and self._clock() - self._alerts_saved_at < ALERT_SAVE_MIN_INTERVAL):
                return
            snapshot = json.loads(json.dumps(self._alerts))
            self._alerts_dirty, self._alerts_saved_at = False, self._clock()
        try:
            atomic_write_json(self.alert_state_path, snapshot, indent=None)
        except Exception as e:
            log.warning('[STORE-BOOSTS] could not save the alert state: %s', e)
            with self._alert_lock:
                self._alerts_dirty = True

    def _after_analysis(self, name, analysis, now):
        level = analysis.get('level') or 'unknown'
        with self._alert_lock:
            items = self._alerts.setdefault('items', {})
            items[name], send = alert_decision(items.get(name), level, analysis, now)
            self._alerts_dirty = True
        self._save_alerts(force=bool(send))
        _, _, holders = self._watched()
        if level in ('watch', 'boost', 'second_wave'):
            for entry in holders.get(name, []):
                capsule = self._capsule_name(entry)
                if capsule and capsule != name:
                    self.read_now(capsule, reason=f'{name} is {level}')
        if send:
            self._send(send, name, analysis, holders.get(name, []))

    def _capsule_name(self, entry):
        for item in self._store_items():
            if item['entry'] == entry:
                return item['name']
        return None

    def _send(self, level, name, analysis, holder_entries, test=False, ring=None):
        """Post one alert to the shared Huginn bot, and ring the phone for a boost (*ring*: None
        follows store_boost_ring_enabled)."""
        settings = self._settings_provider() or {}
        if not test and not settings.get('store_boost_alerts_enabled', True):
            return {'sent': False, 'error': 'alerts are off'}
        store_items = {item['name']: item for item in self._store_items()}
        by_entry = {item['entry']: item for item in store_items.values()}
        store = {'cost': store_items[name]['usd']} if name in store_items else None
        store_name = store_items[name]['store_name'] if store else None
        confirm = self._confirm_offers([store_name], settings).get(store_name) if store else None
        capsules = []
        for entry in holder_entries:
            item = by_entry.get(entry)
            if item:
                capsules.append({'name': item['name'], 'cost': item['usd'], 'opening': self._opening(entry)['buff']})
        plain, body = render_alert(level, name, analysis, store, capsules, confirm, self._clock())
        # Only something the store sells (itself, or inside a capsule) is worth waking up for.
        buyable = bool(store or capsules) or test
        if test:
            plain, body = '🧪 Test — ' + plain, '🧪 <b>Test</b> — ' + body
        result = {'ok': False, 'error': 'no Telegram bot (telegram_bot_token + telegram_chat_id)'}
        if notification_channel(settings):
            result = self._notify(settings, plain, html=body) or {}
        log.info('[STORE-BOOSTS] alert %s %s: %s', level, name, 'sent' if result.get('ok') else result.get('error'))
        rang = False
        if ring is None:
            ring = bool(settings.get('store_boost_ring_enabled', True))
        if level == 'boost' and ring and buyable and self.telegram_caller:
            if self.telegram_caller.status().get('configured'):
                threading.Thread(target=self.telegram_caller.ring, kwargs={'message': plain},
                                 daemon=True, name='store-boosts-ring').start()
                rang = True
        with self._alert_lock:
            log_entries = (self._alerts.get('log') or [])[-49:]
            log_entries.append({'at': self._clock(), 'level': level, 'name': name, 'sent': bool(result.get('ok')),
                                'rang': rang, 'test': test, 'error': None if result.get('ok') else result.get('error')})
            self._alerts['log'] = log_entries
            self._alerts_dirty = True
        self._save_alerts(force=True)
        return {'sent': bool(result.get('ok')), 'rang': rang, 'error': None if result.get('ok') else result.get('error')}

    def send_test(self, ring=False):
        """A test alert (Telegram, and a ring when *ring*), shaped like a real boost message."""
        sample = {'level': 'boost', 'usual_price': 1.05, 'usual_sales': 10, 'price_6h': 5.14, 'price_ratio': 4.9,
                  'sales_24h': 131, 'sales_ratio': 13.1}
        return self._send('boost', 'Sticker | Flickshot', sample, [], test=True, ring=bool(ring))

    # ---- the board -------------------------------------------------------------------------------

    def _confirm_offers(self, names, settings):
        try:
            return self.arbitrage.instant_cash_offers(names, settings) if self.arbitrage else {}
        except Exception as e:
            log.info('[STORE-BOOSTS] cash offers unavailable: %s', e)
            return {}

    def _opening(self, entry, snapshot=None):
        """A capsule's opening value on Buff163 listings and on Steam buy orders (*snapshot*: one
        _snapshot() shared by a whole board)."""
        snapshot = snapshot or self._snapshot()
        contents = (snapshot['contents'].get(entry) or {}).get('items') or []
        pulse = snapshot['pulse']
        buff = {item['name']: (pulse.get(item['name']) or {}).get('buff') for item in contents}
        steam = {item['name']: (pulse.get(item['name']) or {}).get('buy') for item in contents}
        return {'buff': opening_value(contents, buff), 'steam_buy': opening_value(contents, steam)}

    def _snapshot(self):
        """Shallow copies of the state, taken once under the lock (entries are replaced, never changed)."""
        with self._lock:
            return {'items': dict(self._items), 'pulse': dict(self._pulse), 'contents': dict(self._contents),
                    'priority': set(self._priority), 'reading': self._reader.get('reading')}

    def _summary_of(self, name, now, snapshot=None):
        snapshot = snapshot or self._snapshot()
        entry = snapshot['items'].get(name) or {}
        quote = snapshot['pulse'].get(name)
        queued = name in snapshot['priority']
        analysis = entry.get('analysis') or {}
        points = [tuple(point) for point in entry.get('points') or []]
        return {'name': name, 'level': analysis.get('level') or ('unknown' if entry else None),
                'analysis': analysis or None, 'read_at': entry.get('read_at'), 'error': entry.get('error'),
                'spark': spark(points, int(now)) if points else [], 'pulse': quote, 'queued': queued,
                'reading': snapshot['reading'] == name}

    def board(self):
        settings = self._settings_provider() or {}
        now = self._clock()
        store_items = self._store_items()
        _, _, holders = self._watched(store_items)
        confirm = self._confirm_offers([item['store_name'] for item in store_items], settings)
        snapshot = self._snapshot()
        contents = snapshot['contents']
        rows = []
        for item in store_items:
            row = self._summary_of(item['name'], now, snapshot)
            row.update(entry=item['entry'], store_name=item['store_name'], category=item.get('category'), cost=item['usd'],
                       definition_index=item.get('definition_index'), on_store_front=item.get('on_store_front'))
            offer = confirm.get(item['store_name'])
            row['confirm'] = offer
            row['confirmed'] = bool(offer and offer['net'] >= item['usd'])
            capsule = contents.get(item['entry'])
            if capsule:
                chances = item_chances(capsule['items'])
                inside = []
                for content in capsule['items']:
                    summary = self._summary_of(content['name'], now, snapshot)
                    summary.pop('spark', None)
                    summary.update(tier=content['tier'], tier_label=TIER_LABEL.get(content['tier'], content['tier']),
                                   chance=round(chances.get(content['name'], 0), 4))
                    inside.append(summary)
                inside.sort(key=lambda summary: (SEVERITY.get(summary['level'] or 'unknown', 0),
                                           (summary['analysis'] or {}).get('price_ratio') or 0), reverse=True)
                opening = self._opening(item['entry'], snapshot)
                row['contents'] = {'items': inside, 'opening': opening,
                                   'opening_beats_store': bool(opening['buff'] and opening['buff']['value'] > item['usd']),
                                   'level': max((summary['level'] or 'unknown' for summary in inside),
                                                key=lambda level: SEVERITY.get(level, 0), default='unknown')}
                row['confirmed'] = row['confirmed'] or row['contents']['opening_beats_store']
            rows.append(row)

        def rank(row):
            own = SEVERITY.get(row['level'] or 'unknown', 0)
            inside = SEVERITY.get((row.get('contents') or {}).get('level') or 'unknown', 0)
            preflagged = bool((row.get('pulse') or {}).get('preflag'))
            return (max(own, inside), own, preflagged, (row['analysis'] or {}).get('price_ratio') or 0)
        rows.sort(key=rank, reverse=True)
        summary = {level: 0 for level in ACTIVE_LEVELS}
        for row in rows:
            if row['level'] in summary:
                summary[row['level']] += 1
        summary['capsule_signals'] = sum(1 for row in rows if (row.get('contents') or {}).get('level') in ('watch', 'boost', 'second_wave'))
        summary['preflagged'] = sum(1 for row in rows if (row.get('pulse') or {}).get('preflag'))
        summary['confirmed'] = sum(1 for row in rows if row['confirmed'] and row['level'] in ('watch', 'boost', 'second_wave'))
        with self._alert_lock:
            alert_log = list(self._alerts.get('log') or [])
        with self._lock:
            reader = {key: value for key, value in self._reader.items() if key != 'reads'}
            reader['reads_last_hour'] = sum(1 for read_at in self._reader['reads'] if now - read_at < HOUR)
            check = dict(self._check)
        can_ring = bool(self.telegram_caller and self.telegram_caller.status().get('configured'))
        store_by_name = {item['name']: item for item in store_items}
        by_entry = {item['entry']: item for item in store_items}
        tracked = []
        for name in self.tracked():
            summary_of = self._summary_of(name, now, snapshot)
            summary_of['capsules'] = [{'name': by_entry[entry]['name'], 'entry': entry, 'cost': by_entry[entry]['usd']}
                                      for entry in holders.get(name, []) if entry in by_entry]
            store_item = store_by_name.get(name)
            summary_of['cost'] = store_item['usd'] if store_item else None
            summary_of['entry'] = store_item['entry'] if store_item else None
            summary_of['next_read_at'] = (summary_of['read_at'] or now) + self.tracked_every(settings)
            tracked.append(summary_of)
        return {
            'rows': rows, 'summary': summary, 'now': now, 'tracked': tracked,
            'tracked_every_minutes': round(self.tracked_every(settings) / 60),
            'reader': {**reader, 'gap_seconds': steam_gap(settings), **self.queue_status()},
            'check': {**check, 'next_at': (check['checked_at'] or now) + WATCH_INTERVAL},
            'contents_at': self._contents_at,
            'alerts': {'enabled': bool(settings.get('store_boost_alerts_enabled', True)),
                       'ring': bool(settings.get('store_boost_ring_enabled', True)),
                       'watching': bool(settings.get('store_boost_enabled', True)),
                       'channel': notification_channel(settings), 'can_ring': can_ring,
                       'log': list(reversed(alert_log[-15:]))},
            'thresholds': {'watch_price': WATCH_PRICE_RATIO, 'watch_sales': WATCH_SALES_RATIO,
                           'boost_price': BOOST_PRICE_RATIO, 'boost_sales': BOOST_SALES_RATIO,
                           'hold_share': HOLD_SHARE_OF_PEAK, 'second_wave_rise': SECOND_WAVE_RISE,
                           'preflag': PREFLAG_RATIO, 'preflag_listing': PREFLAG_LISTING_RATIO, 'store_item_every_hours': STORE_ITEM_EVERY // HOUR,
                           'active_every_hours': ACTIVE_EVERY // HOUR,
                           'content_every_hours': CONTENT_EVERY // HOUR},
            'trade_hold_days': TRADE_HOLD_DAYS,
            'has_token': bool(settings.get('tradeon_token')),
        }

    def item(self, name):
        """One item's chart: its kept hourly points, analysis and pulse prices."""
        with self._lock:
            entry = self._items.get(name)
            quote = self._pulse.get(name)
        if not entry and not quote:
            return None
        entry = entry or {}
        return {'name': name, 'points': entry.get('points') or [], 'analysis': entry.get('analysis'),
                'read_at': entry.get('read_at'), 'error': entry.get('error'), 'pulse': quote,
                'unlock_if_bought_now': self._clock() + TRADE_HOLD_DAYS * DAY}

    # ---- background ------------------------------------------------------------------------------

    def start_background(self, settings_provider):
        """The hourly pulse check and the Steam reader (two daemon threads)."""
        self._settings_provider = settings_provider
        if self._started:
            return
        self._started = True

        def watch():
            time.sleep(WATCH_FIRST_WAIT)
            while True:
                try:
                    settings = settings_provider() or {}
                    token = settings.get('tradeon_token', '')
                    if settings.get('store_boost_enabled', True) and token and self.catalogue.status().get('items'):
                        with self._lock:
                            busy = self._check['running']
                            if not busy:
                                self._check.update(running=True, started_at=self._clock(), error=None)
                        if not busy:
                            try:
                                self._pulse_check(token)
                            finally:
                                with self._lock:
                                    self._check['running'] = False
                except Exception:
                    log.exception('[STORE-BOOSTS] hourly check failed')
                time.sleep(WATCH_INTERVAL)
        threading.Thread(target=watch, daemon=True, name='store-boosts-watch').start()
        threading.Thread(target=self._read_loop, daemon=True, name='store-boosts-steam').start()

    def set_settings_provider(self, settings_provider):
        self._settings_provider = settings_provider
