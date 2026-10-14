"""Store Catalogue Arbitrage: which in-game store items resell for more than the store charges.

Read-only, a recommendation board: nothing here buys or sells. Each row is one item the
Counter-Strike 2 in-game store sells (the Store Catalogue's price sheet), priced against
what the chosen markets pay for it, net of each market's sell fee (``HuginnService.market_fee``,
the one Fees editor):

- **instant**: the market's buy orders / autobuy, what you get the moment you sell;
- **listing**: the market's lowest listing, what you get by listing at that price and waiting.

The store price is the US dollar one; the row also names the cheapest wallet currency among
your accounts (converted with Andvari's exchange rates). Store purchases cannot be traded for
7 days, so a profit has to survive a week: the daily history (``cache/store_arbitrage_history.json.gz``,
the best price seen per market each day, kept 180 days) shows whether a gap is a spike or
stays open, and your own Draupnir record shows what you sold the item for before.

Left out on purpose: case keys (keys bought from the store cannot be traded or sold, Valve's
2019 rule), passes and licenses, and every item whose own definition says "cannot trade"
(Storage Unit, Charm Detachment Pack). A gap that is wide (a market listing at 1.5 times the
store price or more) and stays open is marked **unconfirmed**: if store copies could be resold,
traders would have closed it, so it usually means they cannot (the Name Tag and the StatTrak
Swap Tool look like this). One purchase settles it: the web inventory shows "Tradable After"
for a 7-day hold, "Not Tradable" for good.

Prices come from Huginn's pulse pulls (one market and side at a time, in ONE worker thread
fed by a queue, cached 10 minutes; CSFloat's buy orders from its swept cache). An hourly watcher
warms the default markets so the history keeps growing while the page is closed.

**The indicator**: CSMoney (Trade)'s instant offer (its buy orders), as a share of the store price.
When it reaches ``store_arbitrage_signal_pct`` (100 by default) the row carries a buy signal:
CSMoney (Trade) pays at least the store price at once, so the item is worth buying in the store
and selling on the market that pays most (CSMoney (Trade) itself pays in its trade balance).

**Daily refresh** (``start_daily_refresh``, run by the morning routine after "Get all items"):
read the game store's price sheet again (one Ratatoskr login, skipped when read in the last 6
hours), pull every default market again, then post the profitable items to the shared Huginn
Telegram bot (``send_alerts``). The hourly watcher posts only items that became profitable since
(each item at most once a day). Switch: ``store_arbitrage_alerts_enabled``; floor:
``store_arbitrage_alert_min_pct``.
"""
import datetime
import gzip
import html
import json
import logging
import os
import threading
import time

from harvest_service import HarvestService
from huginn_service import pulse_short_link
from jsonio import atomic_write_json, read_json
from notifications import notification_channel, send_notification, telegram_visible_length, TELEGRAM_MESSAGE_LIMIT
from storage_shop_service import GAME_STORE_CURRENCIES
from store_catalogue_service import CANNOT_TRADE, CATEGORY_KEYS, CATEGORY_PASSES
from store_purchase_service import to_usd

log = logging.getLogger(__name__)

HISTORY_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'store_arbitrage_history.json.gz')
HISTORY_DAYS = 180
HISTORY_STATISTICS_DAYS = 30
HISTORY_SAVE_MIN_INTERVAL = 60      # seconds between history writes
ALERT_STATE_PATH = os.path.join(os.path.dirname(__file__), 'cache', 'store_arbitrage_alerts.json')

SIDE_INSTANT, SIDE_LISTING = 'instant', 'listing'
# The markets you sold store items on before, plus the two references (Buff163, Steam). The
# board's default set adds the indicator and every market with buy orders (default_market_ids).
DEFAULT_MARKETS = ['Buff', 'Steam', 'CsFloat', 'LisSkins', 'AvanMarket', 'Tm']
# Its instant offer, as a share of the store price, is the buy signal (Ivan's indicator).
INDICATOR_MARKET = 'CsMoneyTrade'
DEFAULT_SIGNAL_PCT = 100.0
REFERENCE_MARKET = 'Buff'           # its lowest listing is the sanity reference for every cash price
# Store prices move slowly; the Refresh button pulls on demand. 30 minutes keeps the ~35 sides
# of the default set from being pulled again every 10 minutes while the page is open.
INDEX_TTL = 30 * 60
RETRY_AFTER_FAILURE = 120
WATCH_INTERVAL = 60 * 60
WATCH_FIRST_WAIT = 5 * 60
REFRESH_MIN_AGE = 60                # the Refresh button re-pulls a market side at most once a minute
SHEET_FRESH_SECONDS = 6 * 60 * 60   # the daily refresh reads the price sheet unless read this recently
SHEET_WAIT_SECONDS = 5 * 60
WARM_WAIT_SECONDS = 20 * 60
ALERT_REPEAT_SECONDS = 24 * 60 * 60  # the hourly check posts an item again only after a day
ALERT_ITEMS_MAX = 25
STEAM_MARKET_SLUG = 'Steam'

TRADE_HOLD_DAYS = 7
# A cash price far above Buff163's lowest listing is not a price you can sell at: a buy order
# for one pattern or float, not for any copy (Harvest's rule), or a listing on a thin market
# where one seller asks what nobody pays (live 2026-10-04: a $0.99 sticker listed at $8.37 on
# LisSkins while it sold for under $1 everywhere else). Balance markets (Steam) price in
# their own scale and are not checked.
SUSPICIOUS_RATIO = 1.3
# A market listing this many times the store price, for most of the days tracked, means the
# store's copies probably cannot be resold (see the module docstring).
UNCONFIRMED_RATIO = 1.5
UNCONFIRMED_SHARE_OF_DAYS = 0.8
UNCONFIRMED_MIN_DAYS = 3

# Markets that pay a sale into their own balance, not money: Harvest's list, plus SkinSwap
# (Trade), whose sale money stayed on the Trade page in the live test of 2026-10-10.
BALANCE_PAYOUT = {**HarvestService.BALANCE_PAYOUT, 'SkinSwapTrade': 'SkinSwap Trade balance'}

EXCLUDED_REASONS = {
    'keys': 'case keys bought from the store cannot be traded or sold (since 2019)',
    'passes': 'a pass or license, not an item you can trade',
    'cannot_trade': 'the item itself cannot be traded',
    'no_price': 'none of the chosen markets has a price for it',
}
EXCLUDED_CATEGORIES = {CATEGORY_KEYS: 'keys', CATEGORY_PASSES: 'passes'}


# ---- pure helpers (unit-tested) ------------------------------------------------------------

def split_items(items):
    """(candidates, excluded) from the catalogue's rows: candidates can be bought and resold."""
    candidates, excluded = [], []
    for item in items or []:
        reason = EXCLUDED_CATEGORIES.get(item.get('category'))
        # Rows saved before the catalogue knew the flag: the known untradable entries.
        if not reason and item.get('cannot_trade', item.get('entry') in CANNOT_TRADE):
            reason = 'cannot_trade'
        if not reason and not item.get('usd'):
            continue                     # no US dollar price: nothing to compare
        if reason:
            excluded.append({'entry': item['entry'], 'name': item['name'], 'category': item.get('category'),
                             'reason': reason, 'reason_text': EXCLUDED_REASONS[reason]})
        else:
            candidates.append(item)
    return candidates, excluded


def history_key(market_id, side):
    return f'{market_id}:{side}'


def make_offer(market_id, display, side, gross, fee, cost, count=None, balance=None, suspicious=False,
               rising=False, falling=False):
    net = gross * (1 - fee)
    profit = net - cost
    return {'market': market_id, 'display': display, 'side': side, 'gross': round(gross, 3), 'fee': fee,
            'net': round(net, 3), 'profit': round(profit, 3),
            'profit_pct': round(profit / cost * 100, 1) if cost > 0 else None,
            'count': count, 'balance': balance, 'suspicious': bool(suspicious),
            'rising': bool(rising), 'falling': bool(falling)}


def best_offer(offers):
    """The highest net offer; a normal offer always beats a suspicious one."""
    return max(offers, key=lambda o: (not o['suspicious'], o['net']), default=None)


def cheapest_wallet(prices, wallets, rates):
    """The cheapest way your accounts can pay: {usd, currency, minor_units, accounts} for the wallet
    currency (one the game store sells in) with the lowest US dollar price, or None.
    *prices*: {ISO: minor units}; *wallets*: [{account_name, currency}]."""
    by_currency = {}
    for wallet in wallets or []:
        currency = wallet.get('currency')
        if currency and currency in GAME_STORE_CURRENCIES and prices.get(currency):
            by_currency.setdefault(currency, []).append(wallet.get('account_name'))
    best = None
    for currency, accounts in by_currency.items():
        usd = to_usd(prices[currency], currency, rates)
        if usd is not None and (best is None or usd < best['usd']):
            best = {'usd': round(usd, 3), 'currency': currency, 'minor_units': prices[currency],
                    'accounts': sorted(accounts, key=str.lower)}
    return best


def usable_prices(prices, balance_markets=()):
    """A day's {history key: price} without the cash prices the board would cross out: above
    SUSPICIOUS_RATIO × that day's Buff163 listing (else its cheapest cash listing)."""
    def market(key):
        return key.split(':', 1)[0]
    priced = {key: price for key, price in (prices or {}).items()
              if key != 'store' and isinstance(price, (int, float)) and price > 0}
    cash_listings = [price for key, price in priced.items()
                     if key.endswith(':' + SIDE_LISTING) and market(key) not in balance_markets]
    reference = priced.get(history_key(REFERENCE_MARKET, SIDE_LISTING)) or min(cash_listings, default=None)
    return {key: price for key, price in priced.items()
            if market(key) in balance_markets or not reference or price <= reference * SUSPICIOUS_RATIO}


def history_statistics(history, name, keys, fees, cost, balance_markets=(), days=HISTORY_STATISTICS_DAYS,
                       today=None):
    """What the last *days* days of history say about *name*: the best net price each day over
    *keys* ({history key: market id}), how many days it beat *cost*, and the best day. Prices
    the board would cross out are left out (``usable_prices``)."""
    today = today or datetime.date.today()
    first = (today - datetime.timedelta(days=days - 1)).isoformat()
    series = []
    for date in sorted(d for d in (history or {}) if d >= first):
        raw = ((history[date] or {}).get(name)) or {}
        prices = usable_prices(raw, balance_markets)
        nets = [prices[key] * (1 - fees.get(market_id, 0)) for key, market_id in keys.items() if prices.get(key)]
        if nets:
            series.append({'date': date, 'net': round(max(nets), 3),
                           'cost': raw.get('store') or cost})
    profitable = [point for point in series if point['net'] > point['cost']]
    best = max(series, key=lambda point: point['net'], default=None)
    return {'days': len(series), 'profitable_days': len(profitable),
            'best_net': best['net'] if best else None, 'best_date': best['date'] if best else None,
            'series': [point['net'] for point in series]}


def gap_days(history, name, listing_keys, cost, balance_markets=(), days=HISTORY_STATISTICS_DAYS, today=None):
    """(days tracked, days the cheapest usable market listing was at least UNCONFIRMED_RATIO ×
    *cost*) for *name*."""
    today = today or datetime.date.today()
    first = (today - datetime.timedelta(days=days - 1)).isoformat()
    tracked = wide = 0
    for date in (d for d in (history or {}) if d >= first):
        prices = usable_prices(((history[date] or {}).get(name)) or {}, balance_markets)
        listings = [prices[key] for key in listing_keys if prices.get(key)]
        if listings:
            tracked += 1
            wide += min(listings) >= cost * UNCONFIRMED_RATIO
    return tracked, wide


def is_unconfirmed(listing_prices, cost, tracked_days, wide_days, resold_before):
    """True when the item looks unsellable from the store: the cheapest market listing is far
    above the store price now and (when there is history) on most days tracked, and you never
    resold it yourself."""
    if resold_before or not listing_prices or not cost:
        return False
    if min(listing_prices) < cost * UNCONFIRMED_RATIO:
        return False
    if tracked_days < UNCONFIRMED_MIN_DAYS:
        return True
    return wide_days >= tracked_days * UNCONFIRMED_SHARE_OF_DAYS


def verdict_of(instant, listing, cost):
    if not instant and not listing:
        return 'no_price'
    if instant and not instant['suspicious'] and instant['net'] > cost:
        return 'instant'
    if listing and not listing['suspicious'] and listing['net'] > cost:
        return 'listing'
    return 'loss'


def reference_price(indexes, markets, name):
    """Buff163's lowest listing of *name*, else the lowest listing on the chosen cash markets."""
    reference = ((indexes.get((REFERENCE_MARKET, SIDE_LISTING)) or {}).get(name) or {}).get('price')
    if reference:
        return reference
    listings = [((indexes.get((m['id'], SIDE_LISTING)) or {}).get(name) or {}).get('price')
                for m in markets if not m.get('balance')]
    return min((price for price in listings if price), default=None)


def indicator_of(offers, cost, signal_pct=DEFAULT_SIGNAL_PCT):
    """The indicator for a row: CSMoney (Trade)'s instant offer as a share of the store price,
    and whether it reaches *signal_pct* (a suspicious offer never signals). None when that
    market has no buy order for the item."""
    offer = next((o for o in offers if o['market'] == INDICATOR_MARKET and o['side'] == SIDE_INSTANT), None)
    if not offer or not cost:
        return None
    pct = round(offer['net'] / cost * 100, 1)
    return {'net': offer['net'], 'gross': offer['gross'], 'count': offer['count'], 'display': offer['display'],
            'balance': offer['balance'], 'pct_of_store': pct,
            'signal': bool(not offer['suspicious'] and pct >= signal_pct)}


def item_row(item, markets, indexes, fees, ledger, history, wallets, rates, sheet_prices, today=None,
             signal_pct=DEFAULT_SIGNAL_PCT):
    """One board row for a catalogue item. *markets*: [{id, display, balance}]; *indexes*:
    {(market id, side): {name: {price, count, rising, falling}}}."""
    name, cost = item['name'], item['usd']
    reference = reference_price(indexes, markets, name)
    offers = []
    for market in markets:
        for side in (SIDE_INSTANT, SIDE_LISTING):
            quote = (indexes.get((market['id'], side)) or {}).get(name)
            if not quote or not quote.get('price'):
                continue
            suspicious = bool(not market.get('balance') and reference
                              and quote['price'] > reference * SUSPICIOUS_RATIO)
            offers.append(make_offer(market['id'], market['display'], side, quote['price'], fees.get(market['id'], 0.0), cost,
                                     quote.get('count'), market.get('balance'), suspicious,
                                     quote.get('rising'), quote.get('falling')))
    instant = best_offer([o for o in offers if o['side'] == SIDE_INSTANT])
    listing = best_offer([o for o in offers if o['side'] == SIDE_LISTING])
    record = (ledger or {}).get(name)
    resold_before = bool(record and record.get('sold'))
    listing_keys = [history_key(m['id'], SIDE_LISTING) for m in markets]
    balance_markets = {m['id'] for m in markets if m.get('balance')}
    tracked, wide = gap_days(history, name, listing_keys, cost, balance_markets, today=today)
    keys = {history_key(m['id'], side): m['id'] for m in markets for side in (SIDE_INSTANT, SIDE_LISTING)}
    best = best_offer([o for o in (instant, listing) if o])
    # The best price paid out as money (not Steam wallet or a site balance), instant or listing.
    cash = best_offer([o for o in offers if not o['balance'] and not o['suspicious']])
    offers.sort(key=lambda o: o['net'], reverse=True)
    unconfirmed = is_unconfirmed([o['gross'] for o in offers if o['side'] == SIDE_LISTING],
                                 cost, tracked, wide, resold_before)
    indicator = indicator_of(offers, cost, signal_pct)
    if indicator and unconfirmed:
        indicator['signal'] = False      # probably not resellable from the store
    return {
        'entry': item['entry'], 'name': name, 'category': item.get('category'),
        'definition_index': item.get('definition_index'), 'market_url': item.get('market_url'),
        'on_store_front': item.get('on_store_front'),
        'cost': cost,
        'cheapest_wallet': cheapest_wallet(sheet_prices.get(item['entry']) or {}, wallets, rates),
        'instant': instant, 'listing': listing, 'best': best, 'cash': cash, 'offers': offers,
        # Only a price you can sell at ranks the row (both sides suspicious: no percentage).
        'profit_pct': best['profit_pct'] if best and not best['suspicious'] else None,
        'cash_profit_pct': cash['profit_pct'] if cash else None,
        'reference_listing': round(reference, 3) if reference else None,
        'verdict': verdict_of(instant, listing, cost),
        'unconfirmed': unconfirmed,
        'indicator': indicator,
        'resold_before': resold_before,
        'ledger': record,
        'history': history_statistics(history, name, keys, fees, cost, balance_markets, today=today),
    }


def board_summary(rows, excluded):
    sellable = [r for r in rows if not r['unconfirmed']]
    return {
        'signals': sum(1 for r in sellable if r.get('indicator') and r['indicator']['signal']),
        'items': len(rows),
        'instant_profit': sum(1 for r in sellable if r['verdict'] == 'instant'),
        'listing_profit': sum(1 for r in sellable if r['verdict'] == 'listing'),
        'cash_profit': sum(1 for r in sellable if r['cash'] and r['cash']['net'] > r['cost']),
        'unconfirmed': sum(1 for r in rows if r['unconfirmed']),
        'excluded': len(excluded),
        'best_profit_pct': max((r['profit_pct'] for r in sellable
                                if r['verdict'] in ('instant', 'listing') and r['profit_pct'] is not None), default=None),
    }


# ---- Telegram alerts (pure) ------------------------------------------------------------------

def pulse_link(market_id, name):
    """pulse's short link that redirects to *name* on that market (Steam: the Community Market).
    Market ids are pulse's own slugs."""
    return pulse_short_link(market_id, name)


# The one balance a store profit counts in: the store is paid from the Steam wallet. Site trade
# balances (TradeIt, CSMoney, LOOT.Farm, SkinSwap Trade) price items in their own, inflated
# scale (live 2026-10-15: TradeIt (Trade) "paid" 1.5–1.8 times Buff163's listing on 53 store
# items), so they never make an item worth a message.
WALLET_MARKETS = {'Steam'}


def alert_candidates(rows, min_pct=0.0):
    """The rows worth a message: resellable (not unconfirmed) items whose best real offer, paid
    as money or into the Steam wallet, pays more than the store price by at least *min_pct*
    percent, best first. Each comes back with 'sell' (that offer) and 'cash' (the best offer
    paid as money, when 'sell' pays the Steam wallet)."""
    found = []
    for row in rows:
        if row['unconfirmed'] or not row['cost']:
            continue
        real = [o for o in row['offers'] if not o['suspicious']
                and (not o['balance'] or o['market'] in WALLET_MARKETS)]
        sell = max(real, key=lambda o: o['net'], default=None)
        if not sell or sell['net'] <= row['cost'] or (sell['profit_pct'] or 0) < min_pct:
            continue
        cash = None
        if sell['balance']:
            cash = max((o for o in real if not o['balance']), key=lambda o: o['net'], default=None)
            if cash and cash['net'] <= row['cost']:
                cash = None
        found.append({'row': row, 'sell': sell, 'cash': cash})
    found.sort(key=lambda c: c['sell']['profit_pct'] or 0, reverse=True)
    return found


def _escape(text):
    return html.escape(str(text), quote=False)


def _dollars(value):
    return f'${value:.2f}'


def _offer_line(offer, name, cost):
    """(plain, html) of "List on X at $a → $b after its fee: +$c (+d%)" (or "Sell instantly on X for")."""
    how = 'Sell instantly on {} for' if offer['side'] == SIDE_INSTANT else 'List on {} at'
    fee = f" after its {offer['fee'] * 100:g}% fee" if offer['fee'] else ' (no fee)'
    paid = f" · paid in {offer['balance']}" if offer['balance'] else ''
    tail = (f" {_dollars(offer['gross'])} → {_dollars(offer['net'])}{fee}: "
            f"+{_dollars(offer['net'] - cost)} (+{offer['profit_pct']:.1f}%){paid}")
    link = pulse_link(offer['market'], name)
    return (how.format(offer['display']) + tail,
            how.format(f"<a href=\"{link}\">{_escape(offer['display'])}</a>") + _escape(tail))


def render_alert(candidates, title, trade_hold_days=TRADE_HOLD_DAYS, limit=ALERT_ITEMS_MAX):
    """(plain, html) of one message: each item with its store price, where it sells and for how
    much, what the indicator market pays, and the Steam Community Market link through pulse."""
    plain, html = [title], [f'<b>{_escape(title)}</b>']
    for candidate in candidates[:limit]:
        row, sell, cash = candidate['row'], candidate['sell'], candidate['cash']
        name, cost = row['name'], row['cost']
        head = f"🛒 {name}: in-game store {_dollars(cost)}"
        wallet = row.get('cheapest_wallet')
        if wallet and wallet['usd'] < cost - 0.005:
            head += f" (about {_dollars(wallet['usd'])} in {wallet['currency']})"
        lines = [_offer_line(sell, name, cost)]
        if cash:
            line = _offer_line(cash, name, cost)
            lines.append(('Best for money: ' + line[0], 'Best for money: ' + line[1]))
        indicator = row.get('indicator')
        if indicator and sell['market'] != INDICATOR_MARKET:
            text = (f"{indicator['display']} pays {_dollars(indicator['net'])} instantly "
                    f"({indicator['pct_of_store']:.0f}% of the store price)"
                    + (': BUY signal' if indicator['signal'] else ''))
            lines.append(('⚡ ' + text, '⚡ ' + _escape(text)))
        steam = pulse_link(STEAM_MARKET_SLUG, name)
        plain.extend(['', head] + [f'   {p}' for p, _ in lines] + [f'   Steam Community Market: {steam}'])
        html.extend(['', f'<b>{_escape(head)}</b>'] + [f'   {h}' for _, h in lines]
                    + [f'   <a href="{steam}">Steam Community Market</a>'])
    if len(candidates) > limit:
        more = f'+{len(candidates) - limit} more in Store Catalogue → Arbitrage'
        plain.extend(['', more])
        html.extend(['', f'<i>{_escape(more)}</i>'])
    foot = (f'Store purchases cannot be traded for {trade_hold_days} days: check the price still holds '
            'before you buy (Store Catalogue → Arbitrage → Buy).')
    plain.extend(['', foot])
    html.extend(['', f'<i>{_escape(foot)}</i>'])
    return '\n'.join(plain), '\n'.join(html)


def fit_alert(candidates, title, trade_hold_days=TRADE_HOLD_DAYS):
    """render_alert with fewer items until it fits one Telegram message."""
    limit = ALERT_ITEMS_MAX
    while True:
        plain, html = render_alert(candidates, title, trade_hold_days, limit)
        if telegram_visible_length(html) <= TELEGRAM_MESSAGE_LIMIT or limit <= 1:
            return plain, html
        limit -= 1


def record_prices(history, today, names, indexes, store_costs):
    """Fold fresh indexes into *history* ({date: {name: {key: best price that day}}}); the store
    price of the day goes under 'store'. Returns True when anything changed."""
    day = history.setdefault(today, {})
    changed = False
    for (market_id, side), index in indexes.items():
        key = history_key(market_id, side)
        for name in names:
            price = ((index or {}).get(name) or {}).get('price')
            if not price:
                continue
            prices = day.setdefault(name, {})
            if price > prices.get(key, 0):
                prices[key] = round(price, 3)
                changed = True
    for name in names:
        if name in day and store_costs.get(name) and day[name].get('store') != store_costs[name]:
            day[name]['store'] = store_costs[name]
            changed = True
    return changed


def clean_history(data):
    """Only the well-formed part of a history read from disk: {date: {name: {key: number}}}."""
    if not isinstance(data, dict):
        return {}
    clean = {}
    for date, names in data.items():
        if not isinstance(date, str) or not isinstance(names, dict):
            continue
        day = {name: {key: value for key, value in prices.items() if isinstance(value, (int, float))}
               for name, prices in names.items() if isinstance(name, str) and isinstance(prices, dict)}
        if day:
            clean[date] = day
    return clean


def prune_history(history, today, days=HISTORY_DAYS):
    first = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days - 1)).isoformat()
    for date in [d for d in history if d < first]:
        del history[date]


def signal_percent(settings):
    """The indicator's threshold, percent of the store price (store_arbitrage_signal_pct)."""
    try:
        value = float((settings or {}).get('store_arbitrage_signal_pct') or DEFAULT_SIGNAL_PCT)
    except (TypeError, ValueError):
        return DEFAULT_SIGNAL_PCT
    return value if value > 0 else DEFAULT_SIGNAL_PCT


# ---- the service ---------------------------------------------------------------------------

class StoreArbitrageService:
    def __init__(self, huginn_service, catalogue_service, storage_shop_service, draupnir_service,
                 history_path=HISTORY_PATH, today=None, alert_state_path=ALERT_STATE_PATH, notify=send_notification):
        self.huginn = huginn_service
        self.catalogue = catalogue_service
        self.storage_shop = storage_shop_service
        self.draupnir = draupnir_service
        self.history_path = history_path
        self._today = today or datetime.date.today
        self._lock = threading.Lock()
        self._indexes = {}       # (market id, side) -> (fetched at, {name: quote}) for catalogue names only
        self._warming = set()   # pairs queued or being pulled
        self._queue = []        # pairs waiting for the one worker thread
        self._worker_running = False
        self._save_lock = threading.Lock()   # one history write at a time (temporary file + rename)
        self._failures = {}      # (market id, side) -> (failed at, error)
        self._history = self._load_history()
        self._history_saved_at = 0.0
        self._history_dirty = False
        self._watch_started = False
        self._daily_running = False
        self.alert_state_path = alert_state_path
        self._notify = notify
        self._alert_lock = threading.Lock()
        self._board_markets_cache = None
        self._save_timer_pending = False
        # Every full pulse table Huginn pulls (the Case Arbitrage refresh and alert polls, the
        # portfolio valuation, other tabs) also prices the store items it holds: no second download.
        huginn_service.add_pull_listener(self.take_pull)

    # ---- markets -------------------------------------------------------------------------------

    def markets(self, settings=None):
        """Every market you can sell on: {id, display, fee, fee_known, instant, balance}."""
        return [{'id': m['id'], 'display': m['display'], 'fee': m['fee'], 'fee_known': m['feeKnown'],
                 'instant': m['hasAutobuy'], 'balance': BALANCE_PAYOUT.get(m['id']),
                 # Real listings to sell into (SkinSwap (Trade)'s are estimated, never priced here).
                 'listings': bool(m.get('hasListings', True)) and not m.get('listingsEstimated')}
                for m in self.huginn.market_registry(settings)]

    def default_market_ids(self, settings=None):
        """Your usual markets, the indicator and every market with buy orders (instant sell)."""
        listed = self.markets(settings)
        known = {m['id'] for m in listed}
        wanted = [*DEFAULT_MARKETS, INDICATOR_MARKET, *(m['id'] for m in listed if m['instant'])]
        return [m for m in dict.fromkeys(wanted) if m in known]

    def clean_markets(self, ids, settings=None):
        known = [m['id'] for m in self.markets(settings)]
        chosen = [m for m in dict.fromkeys(ids or []) if m in known]
        return chosen or self.default_market_ids(settings)

    def _sides(self, market_ids, settings=None):
        """The (market id, side) pairs to price: every listing, the instant side where the market
        has one, and Buff163's listing as the reference."""
        listed = self.markets(settings)
        instant = {m['id'] for m in listed if m['instant']}
        no_listings = {m['id'] for m in listed if not m['listings']}
        pairs = [(m, side) for m in market_ids for side in (SIDE_INSTANT, SIDE_LISTING)
                 if (side == SIDE_LISTING and m not in no_listings) or (side == SIDE_INSTANT and m in instant)]
        if (REFERENCE_MARKET, SIDE_LISTING) not in pairs:
            pairs.append((REFERENCE_MARKET, SIDE_LISTING))
        return pairs

    # ---- price indexes (non-blocking, one background thread) -----------------------------------

    def _names(self):
        candidates, _ = split_items(self.catalogue.status().get('items'))
        return {item['name'] for item in candidates}

    def _fetch_index(self, token, market_id, side):
        if side == SIDE_INSTANT:
            index = self.huginn.market_autobuy_index(token, market_id) or {}
        else:
            index = self.huginn.market_buy_index(token, market_id) or {}
        names = self._names()
        return {name: quote for name, quote in index.items() if name in names}

    def _index_state(self, token, pairs):
        """({pair: index}, status, [failed pairs]). Serves what is cached and queues a warm for
        anything missing or stale; status: no_token | warming | refreshing | fresh | error."""
        now = time.time()
        found, missing, stale, failed = {}, [], [], []
        with self._lock:
            for pair in pairs:
                if pair == ('CsFloat', SIDE_INSTANT):
                    continue                 # read live below (the swept cache on disk)
                hit = self._indexes.get(pair)
                if hit:
                    found[pair] = hit[1]
                    if now - hit[0] < INDEX_TTL:
                        continue
                failure = self._failures.get(pair)
                if failure and now - failure[0] < RETRY_AFTER_FAILURE:
                    failed.append(pair)
                elif hit:
                    stale.append(pair)
                else:
                    missing.append(pair)
        if ('CsFloat', SIDE_INSTANT) in pairs:
            try:
                found[('CsFloat', SIDE_INSTANT)] = self._fetch_index(token, 'CsFloat', SIDE_INSTANT)
            except Exception as e:
                log.info('[STORE-ARBITRAGE] CSFloat buy orders unavailable: %s', e)
        if not token:
            return found, 'no_token', failed
        if missing or stale:
            self._queue_warm(token, missing + stale)
        if missing:                      # still loading others: keep polling fast
            return found, 'warming', failed
        if failed:
            return found, 'error', failed
        with self._lock:                 # the Refresh button re-pulls sides that were still fresh
            busy = any(pair in self._warming for pair in pairs)
        return found, ('refreshing' if stale or busy else 'fresh'), failed

    def _queue_warm(self, token, pairs):
        """Queue pairs for the ONE worker thread (started when none runs): pulse throttles
        parallel pulls, and each pull is tens of megabytes."""
        with self._lock:
            new = [pair for pair in pairs if pair not in self._warming]
            self._warming.update(new)
            self._queue.extend((token, pair) for pair in new)
            if not new or self._worker_running:
                return
            self._worker_running = True
        threading.Thread(target=self._work, daemon=True, name='store-arbitrage-warm').start()

    def _work(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._worker_running = False
                    break
                token, pair = self._queue.pop(0)
            self._warm(token, [pair])
        self._save_history(force=True)

    def _warm(self, token, pairs):
        for pair in pairs:
            started = time.time()
            try:
                index = self._fetch_index(token, *pair)
                with self._lock:
                    self._indexes[pair] = (time.time(), index)
                    self._failures.pop(pair, None)
                self._record({pair: index})
                log.info('[STORE-ARBITRAGE] %s %s: %d store items priced in %.0fs',
                         pair[0], pair[1], len(index), time.time() - started)
            except Exception as e:
                log.warning('[STORE-ARBITRAGE] %s %s prices failed: %s', pair[0], pair[1], e)
                with self._lock:
                    self._failures[pair] = (time.time(), str(e)[:200] or type(e).__name__)
            finally:
                with self._lock:
                    self._warming.discard(pair)

    def take_pull(self, market_id, side, items, started_at):
        """A full table Huginn pulled for another feature (Huginn's listener thread): keep the
        store items' prices as that side's index and in the history, for the board's own
        markets only, unless this side already holds a download that started later."""
        pair = (market_id, side)
        with self._lock:
            hit = self._indexes.get(pair)
        if hit and hit[0] >= started_at:
            return                       # the board's own pull (or a later one) is newer
        if market_id not in self._board_market_ids():
            return
        candidates, _ = split_items(self.catalogue.status().get('items'))
        names = {item['name'] for item in candidates}
        if not names:
            return
        mine = self.huginn._index_from_pull(
            [row for row in items if ((row or {}).get('itemName') or {}).get('marketHashName') in names])
        with self._lock:
            hit = self._indexes.get(pair)
            if hit and hit[0] >= started_at:
                return
            self._indexes[pair] = (started_at, mine)
            self._failures.pop(pair, None)
        self._record({pair: mine}, candidates)
        self._schedule_history_save()
        log.info('[STORE-ARBITRAGE] %s %s: %d store items priced from Huginn\'s pull', market_id, side, len(mine))

    def _board_market_ids(self):
        """The default set (cached a minute: the registry rarely changes)."""
        now = time.time()
        cached = self._board_markets_cache
        if cached and now - cached[0] < 60:
            return cached[1]
        ids = set(self.default_market_ids())
        self._board_markets_cache = (now, ids)
        return ids

    def _schedule_history_save(self):
        """Write the history shortly after a throttled save skipped it (one pending timer)."""
        with self._lock:
            if not self._history_dirty or self._save_timer_pending:
                return
            self._save_timer_pending = True

        def save():
            with self._lock:
                self._save_timer_pending = False
            self._save_history(force=True)
        timer = threading.Timer(HISTORY_SAVE_MIN_INTERVAL + 1, save)
        timer.daemon = True
        timer.start()

    def refresh(self, token, market_ids=None, settings=None):
        """Pull the board's markets again now, not waiting for the 10-minute cache (the Refresh
        button, the daily refresh). A side pulled under REFRESH_MIN_AGE seconds ago is left as it is,
        so pressing it again and again never floods pulse. Returns {queued, markets}."""
        if not token:
            return {'queued': 0, 'error': 'no Tradeon token (tradeon_token in backend/settings.json)'}
        market_ids = self.clean_markets(market_ids, settings)
        sides = self._sides(market_ids, settings)       # the registry is built outside the lock
        now = time.time()
        with self._lock:
            pairs = [pair for pair in sides
                     if pair != ('CsFloat', SIDE_INSTANT)          # read live from the swept cache
                     and pair not in self._warming
                     and now - (self._indexes.get(pair) or (0, None))[0] >= REFRESH_MIN_AGE]
            for pair in pairs:
                self._failures.pop(pair, None)
        self._queue_warm(token, pairs)
        return {'queued': len(pairs), 'markets': market_ids, 'pairs': pairs}

    def wait_idle(self, pairs=None, timeout=WARM_WAIT_SECONDS, step=2.0):
        """Block until *pairs* are pulled (None: everything queued), or *timeout*; True when done.
        Waiting for given pairs only keeps an open page, which queues stale sides again, from
        holding the hourly and daily posts back."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if pairs is not None and not any(pair in self._warming for pair in pairs):
                    return True
                if pairs is None and not self._queue and not self._warming and not self._worker_running:
                    return True
            time.sleep(step)
        return False

    # ---- history -------------------------------------------------------------------------------

    def _load_history(self):
        try:
            with gzip.open(self.history_path, 'rt', encoding='utf-8') as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except Exception as e:
            # Kept aside, not overwritten by the next save.
            aside = f'{self.history_path}.unreadable-{int(time.time())}'
            log.warning('[STORE-ARBITRAGE] history unreadable (%s): moved to %s, starting a new one', e, aside)
            try:
                os.replace(self.history_path, aside)
            except OSError:
                pass
            return {}
        return clean_history(data)

    def _record(self, indexes, candidates=None):
        if candidates is None:
            candidates, _ = split_items(self.catalogue.status().get('items'))
        costs = {item['name']: item['usd'] for item in candidates}
        today = self._today().isoformat()
        with self._lock:
            if record_prices(self._history, today, list(costs), indexes, costs):
                self._history_dirty = True
            prune_history(self._history, today)
        self._save_history()

    def _save_history(self, force=False):
        with self._save_lock:            # the snapshot and its rename stay in order
            self._write_history(force)

    def _write_history(self, force):
        with self._lock:
            if not self._history_dirty or (not force and time.time() - self._history_saved_at < HISTORY_SAVE_MIN_INTERVAL):
                return
            snapshot = json.dumps(self._history, separators=(',', ':'))
            self._history_dirty = False
            self._history_saved_at = time.time()
        temporary = f'{self.history_path}.{os.getpid()}.{threading.get_ident()}.tmp'
        try:
            os.makedirs(os.path.dirname(self.history_path) or '.', exist_ok=True)
            with gzip.open(temporary, 'wt', encoding='utf-8') as handle:
                handle.write(snapshot)
            os.replace(temporary, self.history_path)
        except OSError as e:
            log.error('[STORE-ARBITRAGE] could not save %s: %s', self.history_path, e)
            with self._lock:
                self._history_dirty = True

    # ---- the hourly watcher ----------------------------------------------------------------------

    def start_background(self, settings_provider):
        """Warm the default markets every hour, so the history grows while the page is closed."""
        if self._watch_started:
            return
        self._watch_started = True

        def loop():
            time.sleep(WATCH_FIRST_WAIT)
            while True:
                try:
                    settings = settings_provider() or {}
                    token = settings.get('tradeon_token', '')
                    if token and self.catalogue.status().get('items'):
                        pairs = self._sides(self.clean_markets(None, settings), settings)
                        self._queue_warm(token, pairs)
                        if self.wait_idle(pairs):
                            self.send_alerts(settings_provider() or {}, daily=False)
                        else:
                            log.warning('[STORE-ARBITRAGE] hourly check: prices still loading after %s s, no post this hour',
                                        WARM_WAIT_SECONDS)
                except Exception:
                    log.exception('[STORE-ARBITRAGE] watcher tick failed')
                time.sleep(WATCH_INTERVAL)
        threading.Thread(target=loop, daemon=True, name='store-arbitrage-watch').start()

    # ---- the daily refresh (morning routine) -------------------------------------------------

    def start_daily_refresh(self, settings_provider):
        """After the morning "Get all items": read the store price sheet (one Ratatoskr login),
        pull every default market again, then post the profitable items. Runs in its own
        thread; returns a short line for the morning routine's result."""
        with self._lock:
            if self._daily_running:
                return 'store prices: a refresh is already running'
            self._daily_running = True

        def run():
            try:
                self._daily_refresh(settings_provider)
            except Exception:
                log.exception('[STORE-ARBITRAGE] daily refresh failed')
            finally:
                with self._lock:
                    self._daily_running = False
        threading.Thread(target=run, daemon=True, name='store-arbitrage-daily').start()
        return 'store prices refresh started'

    def _read_price_sheet(self):
        """Read the store price sheet unless it was read in the last SHEET_FRESH_SECONDS. One
        login through Buy Storage Units' job, so it never runs beside a purchase."""
        read_at = self.catalogue.status().get('read_at') or 0
        if time.time() - read_at < SHEET_FRESH_SECONDS:
            return 'store price sheet is fresh'
        started = self.catalogue.start_refresh() or {}
        if not started.get('started'):
            return f"store price sheet not read: {started.get('error') or 'busy'}"
        deadline = time.time() + SHEET_WAIT_SECONDS
        while time.time() < deadline:
            job = self.catalogue.status().get('job') or {}
            if job.get('started_at') == started.get('started_at') and not job.get('running'):
                return f"store price sheet not read: {job['error']}" if job.get('error') else 'store price sheet read'
            time.sleep(2)
        return 'store price sheet still reading'

    def _daily_refresh(self, settings_provider):
        sheet = self._read_price_sheet()
        log.info('[STORE-ARBITRAGE] daily refresh: %s', sheet)
        settings = settings_provider() or {}
        token = settings.get('tradeon_token', '')
        if not token or not self.catalogue.status().get('items'):
            log.info('[STORE-ARBITRAGE] daily refresh: no Tradeon token or no store prices, markets not pulled')
            return
        queued = self.refresh(token, None, settings)
        log.info('[STORE-ARBITRAGE] daily refresh: %s market sides queued', queued.get('queued'))
        if not self.wait_idle(queued.get('pairs') or []):
            log.warning('[STORE-ARBITRAGE] daily refresh: markets still loading after %s s, posting what has loaded',
                        WARM_WAIT_SECONDS)
        result = self.send_alerts(settings_provider() or {}, daily=True, partial=True)
        log.info('[STORE-ARBITRAGE] daily refresh done: %s', result)

    # ---- Telegram alerts ---------------------------------------------------------------------

    def _load_alert_state(self):
        state = read_json(self.alert_state_path, default={}) or {}
        return state if isinstance(state, dict) else {}

    def _save_alert_state(self, state):
        try:
            atomic_write_json(self.alert_state_path, state, indent=None)
        except Exception as e:
            log.warning('[STORE-ARBITRAGE] could not save the alert state: %s', e)

    def send_alerts(self, settings, daily=True, force=False, partial=False):
        """Post the profitable store items to the shared Huginn bot (or the webhook).
        *daily*: every profitable item (or a line saying there is none). Otherwise (the hourly
        check) only items that were not profitable at the previous check and were not posted in
        the last ALERT_REPEAT_SECONDS; the very first check only notes what is profitable. *force*
        (the page's "Send now") ignores the on/off switch. *partial* (the daily refresh after its
        wait) posts from the markets that have loaded even while others are still loading.
        Returns {sent, items, error}."""
        if not force and not settings.get('store_arbitrage_alerts_enabled', True):
            # Forget the hourly snapshot: switched on again, the first check only records.
            with self._alert_lock:
                state = self._load_alert_state()
                if state.pop('profitable', None) is not None:
                    self._save_alert_state(state)
            return {'sent': False, 'items': 0, 'error': 'alerts are off'}
        if notification_channel(settings) is None:
            return {'sent': False, 'items': 0, 'error': 'no Telegram bot (telegram_bot_token + telegram_chat_id)'}
        board = self.board(settings.get('tradeon_token', ''), None, settings)
        if board['status'] == 'no_token' or (board['status'] == 'warming' and not (partial and board['rows'])):
            return {'sent': False, 'items': 0, 'error': f"prices not ready ({board['status']})"}
        try:
            min_pct = float(settings.get('store_arbitrage_alert_min_pct') or 0)
        except (TypeError, ValueError):
            min_pct = 0.0
        candidates = alert_candidates(board['rows'], min_pct)
        profitable_now = sorted(c['row']['name'] for c in candidates)
        with self._alert_lock:
            state = self._load_alert_state()
            posted = state.get('posted') or {}
            now = time.time()
            if not daily:
                before = state.get('profitable')
                candidates = [c for c in candidates if before is not None and c['row']['name'] not in before
                              and now - posted.get(c['row']['name'], 0) >= ALERT_REPEAT_SECONDS]
                if not candidates:
                    state['profitable'] = profitable_now
                    self._save_alert_state(state)
                    return {'sent': False, 'items': 0, 'error': None}
            count = len(candidates)
            if daily and not count:
                title = '🛒 In-game store: nothing sells above the store price today'
            elif daily:
                title = (f"🛒 In-game store: {count} item{'s sell' if count != 1 else ' sells'} "
                         'for more than the store price')
            else:
                title = f"🛒 In-game store: {count} item{'s' if count != 1 else ''} just became profitable"
            plain, html = fit_alert(candidates, title, board['trade_hold_days'])
            result = self._notify(settings, plain, html=html) or {}
            if result.get('ok'):
                for candidate in candidates:
                    posted[candidate['row']['name']] = now
                state.update({'posted': {k: v for k, v in posted.items() if now - v < 7 * ALERT_REPEAT_SECONDS},
                              'last_sent_at': now, 'last_items': count, 'profitable': profitable_now})
                self._save_alert_state(state)
            log.info('[STORE-ARBITRAGE] alert (%s): %d items, %s', 'daily' if daily else 'new', count,
                     'sent' if result.get('ok') else result.get('error'))
            return {'sent': bool(result.get('ok')), 'items': count, 'error': result.get('error')}

    def alert_status(self, settings):
        state = self._load_alert_state()
        return {'enabled': bool(settings.get('store_arbitrage_alerts_enabled', True)),
                'channel': notification_channel(settings),
                'min_pct': settings.get('store_arbitrage_alert_min_pct', 0.0),
                'last_sent_at': state.get('last_sent_at'), 'last_items': state.get('last_items')}

    # ---- the board -------------------------------------------------------------------------------

    def _wallets(self):
        try:
            return self.storage_shop.wallets(), self.storage_shop.exchange_rates(), self.storage_shop.sheet_entries()
        except Exception as e:
            log.info('[STORE-ARBITRAGE] wallets unavailable: %s', e)
            return [], {}, {}

    def board(self, token, market_ids=None, settings=None):
        catalogue = self.catalogue.status()
        candidates, excluded = split_items(catalogue.get('items'))
        market_ids = self.clean_markets(market_ids, settings)
        listed = {m['id']: m for m in self.markets(settings)}
        markets = [listed[m] for m in market_ids]
        pairs = self._sides(market_ids, settings)
        indexes, status, failed = self._index_state(token, pairs)
        fees = {m: self.huginn.market_fee(m, settings) for m in {*market_ids, REFERENCE_MARKET}}
        ledger = self.draupnir.item_trades([item['name'] for item in candidates]) if self.draupnir else {}
        wallets, rates, sheet = self._wallets()
        with self._lock:
            history = json.loads(json.dumps(self._history))
        today = self._today()
        signal_pct = signal_percent(settings)
        rows, unpriced = [], []
        for item in candidates:
            row = item_row(item, markets, indexes, fees, ledger, history, wallets, rates, sheet, today=today,
                           signal_pct=signal_pct)
            if row['verdict'] == 'no_price' and status in ('fresh', 'refreshing'):
                unpriced.append({'entry': item['entry'], 'name': item['name'], 'category': item.get('category'),
                                 'reason': 'no_price', 'reason_text': EXCLUDED_REASONS['no_price']})
            elif row['verdict'] != 'no_price':
                rows.append(row)
        # Unconfirmed rows (probably not resellable) go below every confirmed one.
        rows.sort(key=lambda r: (not r['unconfirmed'], r['profit_pct'] is not None, r['profit_pct'] or 0), reverse=True)
        excluded = excluded + unpriced
        loaded = {pair for pair in indexes}
        with self._lock:
            fetched = [self._indexes[pair][0] for pair in pairs
                       if pair in self._indexes and pair != ('CsFloat', SIDE_INSTANT)]
        return {
            'status': status,
            'error': ('Price pull failed for ' + ', '.join(f'{listed.get(m, {}).get("display", m)} ({side})'
                                                          for m, side in failed)
                      + ' — retrying in about two minutes') if failed else None,
            'markets': [{**m, 'fee': fees[m['id']],
                         'loaded': {side: (m['id'], side) in loaded for side in (SIDE_INSTANT, SIDE_LISTING)}}
                        for m in markets],
            'rows': rows,
            'excluded': excluded,
            'summary': board_summary(rows, excluded),
            'catalogue_read_at': catalogue.get('read_at'),
            'history_days': len(history),
            'trade_hold_days': TRADE_HOLD_DAYS,
            'unconfirmed_ratio': UNCONFIRMED_RATIO,
            # The oldest and newest pull among the sides shown (epoch seconds).
            'prices_at': min(fetched) if fetched else None,
            'prices_newest_at': max(fetched) if fetched else None,
            'indicator_market': INDICATOR_MARKET,
            'signal_pct': signal_pct,
            'pulling': self.pulling(pairs),
        }

    def pulling(self, pairs):
        """How many of *pairs* are queued or being pulled now."""
        with self._lock:
            return sum(1 for pair in pairs if pair in self._warming)

    def options(self, settings=None):
        settings = settings or {}
        return {'markets': self.markets(settings), 'default_markets': self.default_market_ids(settings),
                'indicator_market': INDICATOR_MARKET, 'signal_pct': signal_percent(settings),
                'alerts': self.alert_status(settings)}
