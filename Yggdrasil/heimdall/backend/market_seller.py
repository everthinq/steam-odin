"""Listing items on the Steam Community Market for an account, in its wallet currency.

Shared by Andvari's card auto-sell (``card_seller_service.py``). One
``MarketSeller`` paces every steamcommunity.com call it makes, reads each item's
order book in the account's wallet currency (lowest listing + highest buy order),
remembers each wallet currency's fee rules (``g_rgWalletInfo``: the minimum fee
is bigger than one cent outside US dollars) and the last price it listed each
item at (so our own accounts never undercut each other down to the floor), and
accepts only the Market-listing confirmations that name the items it listed.

Pricing is patient: we do not mind waiting for a sale, so an item is listed at
the highest price that still sells — not above what buyers actually paid lately
(``CEILING_PERCENTILE`` of the last ``HISTORY_WINDOW_DAYS`` of sales, from the
authenticated price history) and with no more cheaper listings ahead of it than
``QUEUE_DAYS`` of sales clear, one cent under the next wall of listings above.
It never goes under the quick price (one cent under the lowest listing, never
under the highest buy order), which is also what is used while an item's price
history is unreadable, too thin to trust, or its ceiling more than
``MAXIMUM_CEILING_OVER_LOWEST`` times the lowest listing.
"""
import bisect
import json
import logging
import time
from datetime import datetime, timezone

import community_pacer
from team_fortress_service import (US_DOLLAR_WALLET, MINIMUM_LISTING_MINOR_UNITS, buyer_pays_for,
                                   matching_listing_confirmations, parse_wallet_info, seller_receives)

log = logging.getLogger(__name__)

ORDERBOOK_URL = 'https://steamcommunity.com/market/orderbook'
SELL_URL = 'https://steamcommunity.com/market/sellitem/'
MARKET_URL = 'https://steamcommunity.com/market/'
PRICE_HISTORY_URL = 'https://steamcommunity.com/market/pricehistory/'
USER_AGENT = 'Mozilla/5.0 (Heimdall Market seller)'
HTTP_TIMEOUT_SECONDS = 25
COMMUNITY_GAP_SECONDS = 4
PRICE_TIME_TO_LIVE_SECONDS = 120
WALLET_INFO_TIME_TO_LIVE_SECONDS = 30 * 86400
OWN_PRICE_MEMORY_SECONDS = 6 * 3600
# Patient pricing. The price history is the heavy authenticated call, so each
# item's summary is kept (and saved) for hours; a failed read is retried sooner.
HISTORY_TIME_TO_LIVE_SECONDS = 6 * 3600
HISTORY_RETRY_SECONDS = 30 * 60
HISTORY_WINDOW_DAYS = 7
CEILING_PERCENTILE = 0.90       # of the units sold in the window, weighted by volume
QUEUE_DAYS = 3                  # cheaper listings ahead of ours: at most this many days of sales
MINIMUM_HISTORY_SALES = 20      # fewer units sold in the window: too thin, quick price only
# The price history does not name its currency (the order book does): a ceiling
# far above the lowest listing is a currency mix-up or a freak spike, not a price.
MAXIMUM_CEILING_OVER_LOWEST = 2


def parse_price_history(payload):
    """[(epoch seconds, price in minor units, units sold)] from a ``pricehistory``
    answer (hourly or daily medians in the session's wallet currency)."""
    if not isinstance(payload, dict) or not payload.get('success'):
        return []
    points = []
    for row in payload.get('prices') or []:
        try:
            # "Oct 08 2026 14: +0": drop the "+0" offset tail, the hour is UTC.
            stamp = str(row[0]).rsplit(':', 1)[0].strip()
            when = datetime.strptime(stamp, '%b %d %Y %H').replace(tzinfo=timezone.utc).timestamp()
            points.append((when, int(round(float(row[1]) * 100)), int(float(str(row[2]).replace(',', '')))))
        except (ValueError, IndexError, TypeError):
            continue
    return points


def history_summary(points, now):
    """{'ceiling', 'per_day', 'sold'} of the last ``HISTORY_WINDOW_DAYS``: the price
    ``CEILING_PERCENTILE`` of the units sold went at or under, and units sold per
    day. None when too few sold to trust."""
    start = now - HISTORY_WINDOW_DAYS * 86400
    recent = sorted((price, units) for when, price, units in points if when >= start and units > 0 and price > 0)
    sold = sum(units for _, units in recent)
    if sold < MINIMUM_HISTORY_SALES:
        return None
    wanted, counted, ceiling = CEILING_PERCENTILE * sold, 0, recent[-1][0]
    for price, units in recent:
        counted += units
        if counted >= wanted:
            ceiling = price
            break
    return {'ceiling': ceiling, 'per_day': round(sold / HISTORY_WINDOW_DAYS, 2), 'sold': sold}


def patient_target(sell_orders, ceiling, budget, own=None):
    """(price, listings ahead) — the highest buyer price at or under *ceiling* with at
    most *budget* listings at or under it (they sell first), placed one under a
    wall of listings, at the ceiling itself, or at our own last price. None when
    every such price has more listings ahead than *budget*."""
    levels = sorted((int(price), int(count)) for price, count in sell_orders)
    prices = [price for price, _ in levels]
    running, cumulative = 0, []
    for _, count in levels:
        running += count
        cumulative.append(running)
    candidates = {ceiling, *(price - 1 for price in prices)} | ({own} if own else set())
    best = None
    for price in candidates:
        if price > ceiling or price < MINIMUM_LISTING_MINOR_UNITS:
            continue
        index = bisect.bisect_right(prices, price)
        ahead = cumulative[index - 1] if index else 0
        if ahead <= budget and (best is None or price > best[0]):
            best = (price, ahead)
    return best


def target_price(target, wallet, floor=0):
    """(buyer pays, seller receives) for a listing a buyer pays at most *target* for
    (Steam shows the buyer price recomputed from what the seller receives), never
    under Steam's minimum listing. Not every buyer price can be made from the fee
    formula (in dollars, receiving 19 cents is 21 for the buyer and 20 is 23), so
    when rounding down lands under *floor* (the highest buy order), the cheapest
    price at or above the floor is used instead. None when the fees take it all."""
    target = max(MINIMUM_LISTING_MINOR_UNITS, int(target))
    receives = seller_receives(target, wallet)
    if receives <= 0:
        return None
    while floor and buyer_pays_for(receives, wallet) < floor:
        receives += 1
    return buyer_pays_for(receives, wallet), receives


class RateLimited(RuntimeError):
    """Steam answered HTTP 429."""


class MarketSeller:
    def __init__(self, http, steam_service, sleep=time.sleep, state=None):
        self.http = http
        self.steam = steam_service
        self._sleep = sleep
        self._prices = {}                    # {(appid, name, currency): (lowest, fetched_at)}
        state = state or {}
        self.wallets = dict(state.get('wallets') or {})        # {currency id: fee rules + fetched_at}
        self.own_prices = dict(state.get('own_prices') or {})  # {"appid|name|currency": {buyer_pays, at}}
        self.account_currencies = dict(state.get('account_currencies') or {})   # {steamid: currency id}
        self.histories = dict(state.get('histories') or {})    # {"appid|name|currency": {at, summary}}

    def state(self):
        cutoff = time.time() - 2 * HISTORY_TIME_TO_LIVE_SECONDS
        histories = {key: value for key, value in self.histories.items() if value.get('at', 0) >= cutoff}
        return json.loads(json.dumps({'wallets': self.wallets, 'own_prices': self.own_prices,
                                      'account_currencies': self.account_currencies, 'histories': histories}))

    def account_currency(self, steamid, cookies):
        """The account's wallet currency id, read once from its Market page (which
        also caches that currency's fee rules)."""
        known = self.account_currencies.get(str(steamid))
        if known:
            return int(known)
        response = self.get(MARKET_URL, None, cookies)
        info = parse_wallet_info(response.text) if response.ok else None
        if not info:
            raise RuntimeError('could not read the wallet currency (is the web session valid?)')
        info['fetched_at'] = time.time()
        self.wallets.setdefault(str(info['currency']), info)
        self.account_currencies[str(steamid)] = info['currency']
        return info['currency']

    def pace(self):
        community_pacer.pace(self._sleep, COMMUNITY_GAP_SECONDS)   # shared with the Team Fortress 2 seller

    def get(self, url, params=None, cookies=None):
        self.pace()
        response = self.http.get(url, params=params, cookies=cookies, timeout=HTTP_TIMEOUT_SECONDS,
                                 headers={'User-Agent': USER_AGENT})
        if response.status_code == 429:
            raise RateLimited(f'Steam rate-limited {url.split("steamcommunity.com")[-1]} (HTTP 429)')
        return response

    def cookies(self, steamid):
        cookies = self.steam.web_session_cookie_for(steamid)
        if cookies:
            return cookies
        self.steam.ensure_fresh_session(steamid)
        return self.steam.web_session_cookie_for(steamid)

    def order_spread(self, appid, name, currency, cookies):
        """(lowest listing, highest buy order, sell orders) in the wallet currency's
        minor units, read from the Market order book with the account's own session
        (it answers in the account's wallet currency; anonymously it is always US
        dollars). Sell orders are [(price, listings)], cheapest first. The highest
        buy order is 0 when nobody wants to buy. None when the book cannot be read or
        is in another currency — then nothing is listed (asked again next time)."""
        key = (int(appid), name, int(currency))
        cached = self._prices.get(key)
        if cached and time.time() - cached[1] < PRICE_TIME_TO_LIVE_SECONDS:
            return cached[0]
        response = self.get(ORDERBOOK_URL, {'q': 'Load', 'qp': json.dumps([int(appid), name])}, cookies)
        try:
            payload = response.json() if response.ok else {}
        except ValueError:
            payload = {}
        wrapper = payload.get('data') if isinstance(payload.get('data'), dict) and payload['data'].get('success') else payload
        book = wrapper.get('data') if isinstance(wrapper, dict) and wrapper.get('success') else None
        if not isinstance(book, dict) or int(book.get('eCurrency') or 0) != int(currency):
            return None
        lowest = int(book.get('amtMinSellOrder') or 0)
        highest_bid = int(book.get('amtMaxBuyOrder') or 0)
        if lowest <= 0:
            return None                  # nobody sells it: no price to undercut
        flat = book.get('rgCompactSellOrders') or []
        try:
            sell_orders = [(int(flat[index]), int(flat[index + 1])) for index in range(0, len(flat) - 1, 2)]
        except (TypeError, ValueError):
            sell_orders = []
        spread = (lowest, highest_bid, sell_orders)
        self._prices[key] = (spread, time.time())
        return spread

    def wallet(self, currency, cookies):
        if int(currency) == 1:
            return US_DOLLAR_WALLET
        known = self.wallets.get(str(int(currency)))
        if known and time.time() - known.get('fetched_at', 0) < WALLET_INFO_TIME_TO_LIVE_SECONDS:
            return known
        response = self.get(MARKET_URL, None, cookies)
        info = parse_wallet_info(response.text) if response.ok else None
        if not info or info['currency'] != int(currency):
            raise RuntimeError('could not read the Market fee rules for this wallet currency')
        info['fetched_at'] = time.time()
        self.wallets[str(info['currency'])] = info
        return info

    def sale_history(self, appid, name, currency, cookies):
        """The item's ``history_summary`` in the wallet currency (the price history
        answers in the session's wallet currency), kept for
        ``HISTORY_TIME_TO_LIVE_SECONDS``. None when unreadable or too thin."""
        key = f'{int(appid)}|{name}|{int(currency)}'
        known = self.histories.get(key)
        if known:
            keep = HISTORY_TIME_TO_LIVE_SECONDS if known.get('summary') else HISTORY_RETRY_SECONDS
            if time.time() - known.get('at', 0) < keep:
                return known.get('summary')
        response = self.get(PRICE_HISTORY_URL, {'appid': int(appid), 'market_hash_name': name}, cookies)
        try:
            payload = response.json() if response.ok else {}
        except ValueError:
            payload = {}
        summary = history_summary(parse_price_history(payload), time.time())
        self.histories[key] = {'at': time.time(), 'summary': summary}
        return summary

    def price_for(self, appid, name, currency, cookies):
        """(buyer pays, seller receives, how) for one item, or None.

        The quick price is one minor unit under the lowest listing — at it when that
        listing is the one we made last, so our accounts never undercut each other —
        but never under the highest buy order: when the two meet (or the cent would
        cross it) the item is listed at the buy order, which sells it at once.
        The patient price (see the module docstring) is used instead whenever it is
        higher. None when the order book cannot be read, or the fees would take it all."""
        spread = self.order_spread(appid, name, currency, cookies)
        if spread is None:
            return None
        lowest, highest_bid, sell_orders = spread
        own = self.own_prices.get(f'{int(appid)}|{name}|{int(currency)}') or {}
        recent_own = own.get('buyer_pays') if time.time() - own.get('at', 0) < OWN_PRICE_MEMORY_SECONDS else None
        ours = recent_own == lowest
        target = lowest if ours else lowest - 1
        how = 'matched our own listing' if ours else 'one under the lowest listing'
        if highest_bid and target <= highest_bid:
            target, how = highest_bid, 'at the highest buy order (sells at once)'
        summary = self.sale_history(appid, name, currency, cookies)
        if summary and summary['ceiling'] <= MAXIMUM_CEILING_OVER_LOWEST * lowest:
            patient = patient_target(sell_orders, summary['ceiling'], summary['per_day'] * QUEUE_DAYS, recent_own)
            if patient and patient[0] > target:
                target = patient[0]
                how = (f'patient: {patient[1]} listings ahead ({patient[1] / summary["per_day"]:.1f} days of sales), '
                       f'{round(CEILING_PERCENTILE * 100)}% of the last {HISTORY_WINDOW_DAYS} days sold at or under '
                       f'{summary["ceiling"] / 100:.2f}')
        price = target_price(target, self.wallet(currency, cookies), floor=highest_bid)
        return (*price, how) if price else None

    def sell(self, steamid, cookies, appid, contextid, assetid, name, currency, receives, buyer_pays):
        """List one item. Returns (ok, error)."""
        self.pace()
        response = self.http.post(SELL_URL, cookies=cookies, timeout=HTTP_TIMEOUT_SECONDS, data={
            'sessionid': cookies.get('sessionid'), 'appid': int(appid), 'contextid': int(contextid),
            'assetid': assetid, 'amount': 1, 'price': int(receives)},
            headers={'User-Agent': USER_AGENT, 'Origin': 'https://steamcommunity.com',
                     'Referer': f'https://steamcommunity.com/profiles/{steamid}/inventory/'})
        if response.status_code == 429:
            raise RateLimited('Steam rate-limited listing (HTTP 429)')
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.ok and payload.get('success'):
            self.own_prices[f'{int(appid)}|{name}|{int(currency)}'] = {'buyer_pays': buyer_pays, 'at': time.time()}
            return True, None
        return False, (payload.get('message') or f'HTTP {response.status_code}')

    def confirm(self, steamid, names):
        """Accept the account's pending Market-listing confirmations that name one of
        *names*. Returns how many were accepted."""
        result = self.steam.get_confirmations(steamid)
        if not result.get('success'):
            raise RuntimeError(result.get('message') or 'could not read confirmations')
        chosen = matching_listing_confirmations(result.get('confirmations'), names)
        if not chosen:
            return 0
        outcome = self.steam.act_on_confirmations_batch(steamid, chosen, 'allow')
        if not outcome.get('success'):
            raise RuntimeError(outcome.get('message') or 'confirming failed')
        return len(chosen)

