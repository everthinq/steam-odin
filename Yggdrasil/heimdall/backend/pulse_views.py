"""Read-only views built on the master pulse feed (pulse_feed.py), for Huginn's pages.

- ``price_dips``: a market's lowest listing against its own 30-day median (or average), with each
  item's weekly sales and how fresh the price is (Huginn → Price dips tab).
- ``check_live``: one Arbitrage row checked again on pulse right now (two ~0.5 s name lookups
  instead of two 59 MB tables), with the profit recomputed with the Fees editor's fee.
- ``steam_liquidity``: Gjallarhorn's liquidity (Steam sales in the last week, lowest listing,
  highest buy order, 30-day median) from pulse instead of Steam's rate-limited endpoints.

Every price comes through ``HuginnService._table`` / ``HuginnService.pulse``; nothing here builds a
pulse request.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from huginn_service import _AUTOBUY_VIA_CSFLOAT_SWEEP, _ESTIMATED_SELL, _MARKET_BY_ID
from pulse_feed import FRESH, KEEP_FRESH_MINUTES, MAX_KEPT_TABLES, STALE_SECONDS

# Markets with price history on pulse (market-info isSupportHistory, 2026-10-15): they have the
# statistics price types (Median30d, Average30d). Buff163 and WhiteMarket need pulse's Table
# subscription. CSFloat answers a Median30d table but pulse does not list it, so it is left out.
STATISTICS_MARKETS = ('Steam', 'Buff', 'CsMoneyTrade', 'CsMoneyMarket', 'Dmarket', 'Tm', 'Waxpeer',
                      'Skinport', 'WhiteMarket', 'CsDeals', 'ShadowPay', 'CsTradeTrade', 'CsTradeMarket')
STATISTICS_BASES = ('Median30d', 'Average30d')


def _parallel(calls):
    """Run a few feed reads at once (the feed allows four downloads at a time)."""
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(calls)))) as pool:
        futures = [pool.submit(call) for call in calls]
        return [future.result() for future in futures]


def price_dips(huginn, token, market_id, basis='Median30d', max_age=FRESH):
    """Every item of *market_id* with its lowest listing and its 30-day *basis* price.

    Rows (no image: the page builds it from the name, keeping the answer small): name, price
    (lowest listing), count, updated (when the market last refreshed the
    listing), basis (the 30-day median or average), vs_basis_percent ((price / basis - 1) × 100:
    negative = listed under its usual price), instant (the market's highest buy order, when it has
    buy orders), and weekly sales on this market, Steam and Market.CSGO."""
    market = _MARKET_BY_ID.get(market_id)
    if market_id not in STATISTICS_MARKETS or not market:
        raise ValueError(f'{market_id}: pulse has no 30-day prices for it')
    if basis not in STATISTICS_BASES:
        raise ValueError(f'{basis}: use one of {", ".join(STATISTICS_BASES)}')
    listing_type = market['buy_type'] if market['buy_type'] != _ESTIMATED_SELL else None
    if not listing_type:
        raise ValueError(f'{market_id} has no listings on pulse')
    reads = [lambda: huginn._table(token, market_id, listing_type, max_age),
             lambda: huginn._table(token, market_id, basis, max_age)]
    instant_type = market['autobuy'] if market_id not in _AUTOBUY_VIA_CSFLOAT_SWEEP else None
    if instant_type:
        reads.append(lambda: huginn._table(token, market_id, instant_type, max_age))
    tables = _parallel(reads)
    listing, statistic = tables[0].index(), tables[1].index()
    instant = tables[2].prices() if instant_type else {}
    sales = huginn.pulse.weekly_sales(listing)
    rows = []
    for name, quote in listing.items():
        typical = (statistic.get(name) or {}).get('price')
        if not typical:
            continue
        weekly = sales.get(name) or {}
        rows.append({
            'name': name, 'price': quote['price'], 'count': quote['count'],
            'updated': quote['updated'], 'basis': typical,
            'vs_basis_percent': round((quote['price'] / typical - 1) * 100, 1),
            'instant': instant.get(name),
            'sold_week': weekly.get(market_id), 'steam_sold_week': weekly.get('Steam'),
            'tm_sold_week': weekly.get('Tm'),
        })
    rows.sort(key=lambda row: row['vs_basis_percent'])
    return {'market': market_id, 'display': market['display'], 'basis': basis,
            'listing_type': listing_type, 'has_instant': bool(instant_type),
            'fee': huginn.market_fee(market_id),
            'downloaded_at': min(table.started_at for table in tables),
            'stale_seconds': STALE_SECONDS, 'rows': rows}


def price_dips_markets(huginn):
    """The markets the Price dips tab offers, in registry order."""
    return [{'id': market_id, 'display': huginn.market_display(market_id),
             'premium': _MARKET_BY_ID[market_id]['premium']}
            for market_id in STATISTICS_MARKETS if market_id in _MARKET_BY_ID]


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


STEAM_LIQUIDITY_TABLES = (('Steam', 'Sell'), ('Steam', 'Buy'), ('Steam', 'Median30d'))
_liquidity_warming = threading.Lock()
_liquidity_failed = {'at': None, 'error': None}   # the last background warm that failed
LIQUIDITY_RETRY_AFTER = 5 * 60


def _warm_steam_liquidity(huginn, token, max_age):
    """Download the missing Steam tables in the background (one warm at a time)."""
    if not _liquidity_warming.acquire(blocking=False):
        return

    def run():
        try:
            _parallel([lambda key=key: huginn._table(token, *key, max_age) for key in STEAM_LIQUIDITY_TABLES])
            _liquidity_failed['at'] = None
        except Exception as error:        # the next page loads use Steam until the retry
            _liquidity_failed['at'], _liquidity_failed['error'] = time.time(), str(error)[:200]
        finally:
            _liquidity_warming.release()
    threading.Thread(target=run, name='gjallarhorn-pulse-warm', daemon=True).start()


def steam_liquidity(huginn, token, names, max_age=FRESH, wait=True):
    """{name: {volume7d, lowest, highest_buy, median30d, spreadPct, vsMedianPct, tm_volume7d}} for
    the *names* pulse knows on Steam. spreadPct = (lowest listing − highest buy order) / lowest
    listing × 100 (tight = liquid); vsMedianPct = lowest listing vs its 30-day median. With *wait*
    False (a page request) it never downloads: when a table is not in memory it starts the download
    in the background and returns None ("warming")."""
    if not names:
        return {}
    if wait:
        listing, orders, median = _parallel([lambda key=key: huginn._table(token, *key, max_age)
                                             for key in STEAM_LIQUIDITY_TABLES])
    else:
        tables = [huginn.pulse.peek(*key, max_age) for key in STEAM_LIQUIDITY_TABLES]
        if not all(tables):
            failed_at = _liquidity_failed['at']
            if failed_at and time.time() - failed_at < LIQUIDITY_RETRY_AFTER:
                raise RuntimeError(f"pulse Steam tables unavailable: {_liquidity_failed['error']}")
            _warm_steam_liquidity(huginn, token, max_age)
            return None
        listing, orders, median = tables
    lowest, highest, typical = listing.prices(), orders.prices(), median.prices()
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



# ---- the Pulse feed page: which tables exist, which are kept fresh ------------------------------

PRICE_TYPE_LABELS = {'Sell': 'Lowest listing', 'SellWithoutHold': 'Lowest listing (no trade hold)',
                     'Buy': 'Buy orders', 'Median30d': '30-day median', 'Average30d': '30-day average'}


def pulse_choices():
    """Every table the Pulse feed page offers to keep fresh: per market (registry order) its
    lowest-listing table, its buy-orders table and, where pulse has them, the 30-day median and
    average. Tradeon is no table of its own (it is every table's first market); SkinSwap (Trade)'s
    estimated listings and CSFloat's swept buy orders are not pulse tables."""
    out = []
    for market_id, market in _MARKET_BY_ID.items():
        if market_id == 'TradeOnMarket':
            continue
        types = []
        if market['buy_type'] and market['buy_type'] != _ESTIMATED_SELL:
            types.append(market['buy_type'])
        if market['autobuy'] and market_id not in _AUTOBUY_VIA_CSFLOAT_SWEEP:
            types.append(market['autobuy'])
        if market_id in STATISTICS_MARKETS:
            types += list(STATISTICS_BASES)
        out.append({'market': market_id, 'display': market['display'], 'premium': market['premium'],
                    'price_types': [{'price_type': price_type, 'label': PRICE_TYPE_LABELS.get(price_type, price_type)}
                                    for price_type in types]})
    return out


def _offered():
    return {(choice['market'], option['price_type']) for choice in pulse_choices() for option in choice['price_types']}


def _parse_keys(values, allowed, what):
    """["Steam:Sell", ["Buff", "Buy"], ...] → [(market id, price type)] in *allowed*, without repeats;
    ValueError naming the first value that is not one."""
    out = []
    for value in values or []:
        if isinstance(value, str):
            key = tuple(value.split(':', 1))
        elif isinstance(value, (list, tuple)):
            key = tuple(value)
        else:
            raise ValueError(f'not a pulse table: {value!r}')
        if len(key) != 2 or not all(isinstance(part, str) for part in key) or key not in allowed:
            raise ValueError(f'not a pulse table {what}: {value}')
        if key not in out:
            out.append(key)
    return out


def downloadable(market_id, price_type, huginn):
    """A table the page may download now: one it offers, or one a page or job has used since start."""
    key = (market_id, price_type)
    if not all(isinstance(part, str) and part for part in key):
        raise ValueError('market and price_type are required')
    if key not in _offered() and key not in huginn.pulse.seen_tables():
        raise ValueError(f'not a pulse table Heimdall uses: {market_id} {price_type}')
    return key


def parse_kept(values):
    """["Steam:Sell", ...] or [["Steam", "Sell"], ...] → [(market id, price type)], only the tables
    pulse_choices() offers, at most MAX_KEPT_TABLES; ValueError otherwise."""
    out = _parse_keys(values, _offered(), 'Heimdall offers')
    if len(out) > MAX_KEPT_TABLES:
        raise ValueError(f'at most {MAX_KEPT_TABLES} tables can be kept fresh ({len(out)} chosen)')
    return out


def parse_switched_off(values, huginn):
    """["Steam:Sell", ...] → [(market id, price type)]: only tables a background job fetches or the
    page offers (switching off anything else would mean nothing)."""
    return _parse_keys(values, huginn.pulse.declared_tables() | _offered(), 'Heimdall fetches')


def apply_kept_settings(huginn, settings):
    """Hand the saved choice (settings pulse_kept_tables, pulse_keep_minutes,
    pulse_switched_off_tables) to the master feed; anything no longer offered is skipped."""
    switched_off = []
    for value in settings.get('pulse_switched_off_tables') or []:
        if isinstance(value, str) and ':' in value:
            switched_off.append(tuple(value.split(':', 1)))
    huginn.pulse.set_switched_off(switched_off)
    minutes = settings.get('pulse_keep_minutes') or 10
    if minutes not in KEEP_FRESH_MINUTES:
        minutes = 10
    offered = _offered()
    keys = []
    for value in settings.get('pulse_kept_tables') or []:
        try:
            keys += [key for key in _parse_keys([value], offered, 'Heimdall offers') if key not in keys]
        except ValueError:
            continue
    keys = keys[:MAX_KEPT_TABLES]
    huginn.pulse.set_kept(keys, minutes)
    return keys, minutes
