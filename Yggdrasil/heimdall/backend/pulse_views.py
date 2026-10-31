"""Read-only views built on the master pulse feed (pulse_feed.py), for Huginn's pages, and the
active profiles: the one list that decides what pulse is asked for.

- ``price_dips``: a market's lowest listing against its own 30-day median (or average) from
  Heimdall's price history (price_history.py), with each item's weekly sales and how fresh the
  price is (Huginn → Price dips tab). Only markets an active profile keeps.
- ``check_live``: one Arbitrage row checked again on pulse right now (two ~0.5 s name lookups
  instead of two whole tables), with the profit recomputed with the Fees editor's fee.
- ``steam_liquidity``: Gjallarhorn's liquidity (Steam sales in the last week, lowest listing,
  highest buy order, 30-day median) from memory and the price history, never a request.
- **Active profiles** (``profile_tables``, ``parse_active_profiles``, ``apply_active_settings``,
  ``active_status``): the Arbitrage profiles Ivan trades on (settings huginn_active_profiles,
  every huginn_active_minutes, inside huginn_active_hours). Their tables, Buff163's daily
  reference and the statistics seed are the only tables the keeper downloads.

Every price comes through ``HuginnService._table`` / ``HuginnService.pulse``; nothing here builds a
pulse request.
"""
import time
from concurrent.futures import ThreadPoolExecutor

from huginn_service import _AUTOBUY_VIA_CSFLOAT_SWEEP, _ESTIMATED_SELL, _MARKET_BY_ID
from price_history import BASES as STATISTICS_BASES, STATISTICS_MARKETS  # noqa: F401  (re-exported)
from pulse_feed import ACTIVE_MINUTES, FRESH, MAX_ACTIVE_TABLES, REFERENCE_TABLES, STALE_SECONDS, NotActive


def _parallel(calls):
    """Run a few feed reads at once (lookups: the feed allows two at a time)."""
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(calls)))) as pool:
        futures = [pool.submit(call) for call in calls]
        return [future.result() for future in futures]


def price_dips(huginn, token, market_id, basis='Median30d'):
    """Every item of *market_id* with its lowest listing and its 30-day *basis* price.

    Rows (no image: the page builds it from the name, keeping the answer small): name, price
    (lowest listing), count, updated (when the market last refreshed the listing), basis (the
    30-day median or average from the price history), vs_basis_percent ((price / basis - 1) × 100:
    negative = listed under its usual price), instant (the market's highest buy order, when an
    active profile keeps them), and weekly sales on this market, Steam and Market.CSGO."""
    market = _MARKET_BY_ID.get(market_id)
    if not market:
        raise ValueError(f'{market_id}: unknown market')
    if basis not in STATISTICS_BASES:
        raise ValueError(f'{basis}: use one of {", ".join(STATISTICS_BASES)}')
    listing_type = market['buy_type'] if market['buy_type'] != _ESTIMATED_SELL else None
    if not listing_type:
        raise ValueError(f'{market_id} has no listings on pulse')
    # A slave read: memory only, whatever its age (FRESH matters only to a downloader).
    listing_table = huginn._table(token, market_id, listing_type, FRESH)     # NotActive: not kept
    listing = listing_table.index()
    instant_type = market['autobuy'] if market_id not in _AUTOBUY_VIA_CSFLOAT_SWEEP else None
    instant = {}
    if instant_type:
        try:
            instant = huginn._table(token, market_id, instant_type, FRESH).prices()
        except NotActive:
            instant_type = None
    typical = huginn.history.statistic(market_id, listing_type, basis, listing)
    sales = huginn.pulse.weekly_sales(listing)
    rows = []
    for name, quote in listing.items():
        usual = typical.get(name)
        if not usual:
            continue
        weekly = sales.get(name) or {}
        rows.append({
            'name': name, 'price': quote['price'], 'count': quote['count'],
            'updated': quote['updated'], 'basis': usual,
            'vs_basis_percent': round((quote['price'] / usual - 1) * 100, 1),
            'instant': instant.get(name),
            'sold_week': weekly.get(market_id), 'steam_sold_week': weekly.get('Steam'),
            'tm_sold_week': weekly.get('Tm'),
        })
    rows.sort(key=lambda row: row['vs_basis_percent'])
    return {'market': market_id, 'display': market['display'], 'basis': basis,
            'listing_type': listing_type, 'has_instant': bool(instant_type),
            'fee': huginn.market_fee(market_id), 'downloaded_at': listing_table.started_at,
            'history': huginn.history.coverage(market_id, listing_type),
            'stale_seconds': STALE_SECONDS, 'rows': rows}


def price_dips_markets(huginn):
    """The markets the Price dips tab offers: those whose lowest listing is kept (an active profile
    or the reference), in registry order."""
    active = huginn.pulse.active()
    return [{'id': market_id, 'display': market['display'], 'premium': market['premium']}
            for market_id, market in _MARKET_BY_ID.items()
            if market['buy_type'] and market['buy_type'] != _ESTIMATED_SELL and (market_id, market['buy_type']) in active]




def _side_types(buy_id, sell_id, mode):
    """(buy price type or None for Tradeon's own first market, sell price type) of an Arbitrage
    pair, the way HuginnService.fetch_generated_pair prices it; ValueError when the pair cannot be
    checked on pulse (CSFloat buy orders come from the sweep, estimates are not pulse data)."""
    buy, sell = _MARKET_BY_ID.get(buy_id), _MARKET_BY_ID.get(sell_id)
    if not buy or not sell:
        raise ValueError('unknown market')
    if sell_id in _AUTOBUY_VIA_CSFLOAT_SWEEP and mode == 'autobuy':
        raise ValueError("CSFloat's buy orders come from the CSFloat sweep, not pulse")
    # Exactly as fetch_generated_pair prices it: buy orders for autobuy, else 'Sell' (the lowest
    # listing; LisSkins as a target is its 'Sell' table, not 'SellWithoutHold').
    if mode == 'autobuy' and sell['autobuy']:
        sell_type = sell['autobuy']
    else:
        sell_type = _ESTIMATED_SELL if sell['buy_type'] == _ESTIMATED_SELL else 'Sell'
    if mode != 'autobuy' and not sell['buy_type']:
        raise ValueError(f'{sell_id} has no listings on pulse')
    if _ESTIMATED_SELL in (sell_type, buy['buy_type']) or not buy['buy_type']:
        raise ValueError('an estimated or missing price cannot be checked live')
    return (None if buy_id == 'TradeOnMarket' else buy['buy_type']), sell_type


def check_live(huginn, token, name, buy_id, sell_id, mode='autobuy'):
    """One Arbitrage row checked on pulse now: {buy, sell} pulse-shaped market dicts (with
    offersUpdateTime), profit and profit percent after the sell market's fee, checked_at; 'found'
    false when either side no longer has the item."""
    buy_type, sell_type = _side_types(buy_id, sell_id, mode)
    if buy_type is None:
        answer = huginn.pulse.lookup(token, sell_id, sell_type, name)
        buy_side, sell_side = ((answer or {}).get('first'), (answer or {}).get('second'))
        checked_at = (answer or {}).get('checked_at')
    else:
        buy_answer, sell_answer = _parallel([lambda: huginn.pulse.lookup(token, buy_id, buy_type, name),
                                             lambda: huginn.pulse.lookup(token, sell_id, sell_type, name)])
        buy_side, sell_side = (buy_answer or {}).get('second'), (sell_answer or {}).get('second')
        checked_at = min(answer['checked_at'] for answer in (buy_answer, sell_answer) if answer) \
            if (buy_answer or sell_answer) else None
    fee = huginn.market_fee(sell_id)
    buy_price = (buy_side or {}).get('price')
    sell_price = (sell_side or {}).get('price')
    out = {'name': name, 'buy': buy_side or None, 'sell': sell_side or None, 'fee': fee,
           'checked_at': checked_at, 'found': bool(buy_price and sell_price is not None)}
    if out['found']:
        profit = sell_price * (1 - fee) - buy_price
        out['profit'] = round(profit, 3)
        out['profitPercent'] = round(profit / buy_price * 100, 2)
    return out


STEAM_LIQUIDITY_TABLES = (('Steam', 'Sell'), ('Steam', 'Buy'))


def steam_liquidity(huginn, names):
    """{name: {volume7d, lowest, highest_buy, median30d, spreadPct, vsMedianPct, tm_volume7d}} for
    the *names* pulse knows on Steam, from memory (Steam's lowest listing and buy orders, kept by
    the active profiles) and the price history (the 30-day median); never a request. spreadPct =
    (lowest listing − highest buy order) / lowest listing × 100 (tight = liquid); vsMedianPct =
    lowest listing vs its 30-day median. The buy orders are optional (a profile may keep only the
    listing). None while an active Steam table is not in memory yet (after a restart: the keeper is
    downloading it); NotActive when no active profile keeps Steam's listing."""
    if not names:
        return {}
    listing, orders = (huginn.pulse.peek(*key) for key in STEAM_LIQUIDITY_TABLES)
    missing = [key for table, key in zip((listing, orders), STEAM_LIQUIDITY_TABLES) if table is None]
    if missing and all(huginn.pulse.will_download(key) for key in missing):
        return None                                        # the keeper is about to bring them (after a restart)
    if listing is None:
        raise NotActive('Steam Sell is not in memory: no active Huginn profile keeps it, it is outside '
                        'the active hours, or its download is failing')
    lowest, highest = listing.prices(), orders.prices() if orders is not None else {}
    typical = huginn.history.trade_statistic('Steam', 'Sell', 'Buy', 'Median30d', names)
    sales = huginn.pulse.weekly_sales(names)
    out = {}
    for name in names:
        weekly = sales.get(name) or {}
        if 'Steam' not in weekly and name not in lowest:
            continue
        low, high, middle = lowest.get(name), highest.get(name), typical.get(name)
        out[name] = {
            # Listed on Steam but no weekly count: nothing sold (Steam's own history said 0 too).
            'volume7d': weekly.get('Steam', 0), 'tm_volume7d': weekly.get('Tm'),
            'lowest': low, 'highest_buy': high, 'median30d': middle,
            'spreadPct': round((low - high) / low * 100, 2) if low and high else None,
            'vsMedianPct': round((low / middle - 1) * 100, 2) if low and middle else None,
        }
    return out


PRICE_TYPE_LABELS = {'Sell': 'Lowest listing', 'SellWithoutHold': 'Lowest listing (no trade hold)',
                     'Buy': 'Buy orders', 'Median30d': '30-day median', 'Average30d': '30-day average'}


def table_label(market_id, price_type):
    market = _MARKET_BY_ID.get(market_id)
    return f"{market['display'] if market else market_id} · {PRICE_TYPE_LABELS.get(price_type, price_type).lower()}"


# ---- Active profiles: the one list that decides what pulse is asked for ----------------------

ACTIVE_DEFAULT_MINUTES = 5
PROFILE_MODES = ('autobuy', 'min')


def _estimated_tables(market_id):
    """The real tables an estimated listing is built from (HuginnService._estimated_listings)."""
    return [('SkinSwapMarket', 'Sell'), (market_id, 'Buy')]


def profile_tables(buy_id, sell_id, mode):
    """[(market id, price type)] an Arbitrage profile (buy market, sell market, 'autobuy' or 'min')
    reads from pulse, exactly as its fetch does (fetch_generated_pair, the curated profiles, the
    LOOT.Farm feed profiles, CSFloat's swept buy orders); ValueError for a profile with none.
    tests/test_active_profiles.py calls every profile route to prove the two agree."""
    buy, sell = _MARKET_BY_ID.get(buy_id), _MARKET_BY_ID.get(sell_id)
    if not buy or not sell or buy_id == sell_id or mode not in PROFILE_MODES:
        raise ValueError(f'not an Arbitrage profile: {buy_id} → {sell_id} ({mode})')
    if not buy['buy_type'] or (mode != 'autobuy' and not sell['buy_type']):
        raise ValueError(f'{buy_id if not buy["buy_type"] else sell_id} has no listings on pulse')
    buy_tables = (_estimated_tables(buy_id) if buy['buy_type'] == _ESTIMATED_SELL
                  else [(buy_id, buy['buy_type'])])
    if sell_id == 'LootFarm':
        # LOOT.Farm's own feed is the sell side: only the buy side is pulse (Tradeon is read as
        # the first market of the Steam buy-orders table: fetch_tradeon_lootfarm).
        if buy_id not in ('TradeOnMarket', 'LisSkins'):
            raise ValueError('LOOT.Farm profiles buy on Tradeon or LisSkins')
        return [('Steam', 'Buy')] if buy_id == 'TradeOnMarket' else buy_tables
    if sell_id in _AUTOBUY_VIA_CSFLOAT_SWEEP and mode == 'autobuy':
        # CSFloat's buy orders come from the sweep: only the buy side is pulse.
        return [('CsFloat', 'Sell')] if buy_id == 'TradeOnMarket' else buy_tables
    if mode == 'autobuy' and sell['autobuy']:
        sell_tables = [(sell_id, sell['autobuy'])]
    elif sell['buy_type'] == _ESTIMATED_SELL:
        sell_tables = _estimated_tables(sell_id)
    else:
        sell_tables = [(sell_id, 'Sell')]
    if buy_id == 'TradeOnMarket':
        return sell_tables          # Tradeon is the sell table's own first market
    return list(dict.fromkeys(buy_tables + sell_tables))


def profile_label(key):
    buy_id, sell_id, mode = key.split(':')
    display = lambda market_id: _MARKET_BY_ID[market_id]['display']  # noqa: E731
    return f'{display(buy_id)} → {display(sell_id)} ({mode})'


def parse_active_profiles(values):
    """["LisSkins:CsMoneyTrade:autobuy", ...] → ({profile key: [tables]}, [every table, once]);
    ValueError naming the first value that is not a profile with pulse tables, or when the
    profiles need more than MAX_ACTIVE_TABLES tables."""
    profiles = {}
    for value in values or []:
        parts = value.split(':') if isinstance(value, str) else []
        if len(parts) != 3:
            raise ValueError(f'not an Arbitrage profile: {value!r}')
        profiles[value] = profile_tables(*parts)
    tables = list(dict.fromkeys(key for keys in profiles.values() for key in keys))
    if len(tables) > MAX_ACTIVE_TABLES:
        raise ValueError(f'these profiles need {len(tables)} pulse tables; at most {MAX_ACTIVE_TABLES} '
                         'can be active. Switch another profile off first.')
    return profiles, tables


def parse_active_profiles_leniently(values):
    """parse_active_profiles for saved settings: a profile no longer valid is skipped, and the
    profiles past MAX_ACTIVE_TABLES tables are left out."""
    profiles, tables = {}, []
    for value in values or []:
        try:
            keys = parse_active_profiles([value])[0][value]
        except (ValueError, KeyError):
            continue
        more = [key for key in keys if key not in tables]
        if len(tables) + len(more) > MAX_ACTIVE_TABLES:
            continue
        profiles[value], tables = keys, tables + more
    return profiles, tables


def saved_profiles(settings):
    """The saved active profiles (huginn_kept_profiles before 2026-10-31, still read once)."""
    if settings.get('huginn_active_profiles') is not None:
        return list(settings['huginn_active_profiles'])
    return list(settings.get('huginn_kept_profiles') or [])


def saved_minutes(settings):
    minutes = settings.get('huginn_active_minutes')
    if minutes is None:
        minutes = settings.get('huginn_profile_keep_minutes') or ACTIVE_DEFAULT_MINUTES
    return minutes if minutes in ACTIVE_MINUTES else ACTIVE_DEFAULT_MINUTES


def parse_clock(text):
    """'08:30' → minutes of the day; ValueError otherwise."""
    try:
        hours, minutes = (int(part) for part in str(text).split(':'))
    except (TypeError, ValueError):
        raise ValueError(f'not a time of day: {text!r} (use HH:MM)') from None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise ValueError(f'not a time of day: {text!r} (use HH:MM)')
    return hours * 60 + minutes


def saved_hours(settings):
    """{enabled, start, end} of the active hours (off, 08:00 to 01:00 until changed)."""
    hours = settings.get('huginn_active_hours')
    if not isinstance(hours, dict):
        hours = {}
    return {'enabled': bool(hours.get('enabled')), 'start': hours.get('start') or '08:00',
            'end': hours.get('end') or '01:00'}


def apply_active_settings(huginn, settings):
    """Hand the saved choice to the master feed: the active profiles' tables (plus the Buff163
    reference), their interval, the active hours, and the statistics seed (missing or a month old,
    asked again hourly by the keeper)."""
    _, tables = parse_active_profiles_leniently(saved_profiles(settings))
    huginn.pulse.set_active(tables, saved_minutes(settings))
    hours = saved_hours(settings)
    try:
        window = (parse_clock(hours['start']), parse_clock(hours['end'])) if hours['enabled'] else None
    except ValueError:
        window = None
    huginn.pulse.set_active_hours(window)
    pulse = huginn.pulse
    pulse.want_once(lambda: huginn.history.seeds_wanted(market_id for market_id, _ in pulse.active()),
                    give_up=huginn.history.seed_unavailable)
    return tables


def _table_view(key, ages):
    age, downloading = ages.get(key, (None, False))
    return {'market': key[0], 'price_type': key[1], 'label': table_label(*key),
            'age_seconds': age, 'downloading': downloading}


def active_status(huginn, settings, preview=None):
    """Everything the Active profiles panel shows: the profiles with their tables' ages, the
    reference, the statistics (days of history, seeds), the interval and hours, the keeper's state
    and, with *preview* (a profile key), what making it active would add."""
    profiles, tables = parse_active_profiles_leniently(saved_profiles(settings))
    keys = set(tables) | set(REFERENCE_TABLES)
    ages = huginn.pulse.ages(keys)
    out = {
        'minutes': saved_minutes(settings), 'choices': list(ACTIVE_MINUTES), 'max_tables': MAX_ACTIVE_TABLES,
        'tables_used': len(tables), 'hours': saved_hours(settings), 'keeper': huginn.pulse.keeper_status(),
        'profiles': {key: {'label': profile_label(key), 'tables': [_table_view(table, ages) for table in keys_of]}
                     for key, keys_of in profiles.items()},
        'reference': [{**_table_view(key, ages), 'every_seconds': every,
                       'note': 'off-Steam price reference, read once a day'}
                      for key, every in REFERENCE_TABLES.items()],
        'statistics': [{**_table_view(key, ages), **huginn.history.coverage(*key)}
                       for key in sorted(keys) if key[1] in ('Sell', 'SellWithoutHold', 'Buy')],
        'now': time.time(),
    }
    if preview:
        try:
            wanted = parse_active_profiles([preview])[1]
        except ValueError as error:
            out['preview'] = {'key': preview, 'error': str(error)}
        else:
            adds = sum(1 for key in wanted if key not in tables)
            out['preview'] = {'key': preview, 'tables': [list(key) for key in wanted], 'adds': adds}
            if preview not in profiles and len(tables) + adds > MAX_ACTIVE_TABLES:
                out['preview']['error'] = (f"it needs {adds} more pulse table{'s' if adds != 1 else ''} and "
                                           f'{len(tables)} of {MAX_ACTIVE_TABLES} are in use. Switch another profile off first.')
    return out
