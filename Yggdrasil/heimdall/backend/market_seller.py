"""Listing items on the Steam Community Market for an account, in its wallet currency.

Shared by Andvari's card auto-sell (``card_seller_service.py``). One
``MarketSeller`` paces every steamcommunity.com call it makes, remembers each
wallet currency's fee rules (``g_rgWalletInfo``: the minimum fee is bigger than
one cent outside US dollars) and the last price it listed each item at (so our
own accounts never undercut each other down to the floor), lists one item, and
accepts only the Market-listing confirmations that name the items it listed.
"""
import json
import logging
import time

import community_pacer
from team_fortress_service import (US_DOLLAR_WALLET, listing_price, matching_listing_confirmations,
                                   parse_price_minor_units, parse_wallet_info)

log = logging.getLogger(__name__)

PRICE_URL = 'https://steamcommunity.com/market/priceoverview/'
SELL_URL = 'https://steamcommunity.com/market/sellitem/'
MARKET_URL = 'https://steamcommunity.com/market/'
USER_AGENT = 'Mozilla/5.0 (Heimdall Market seller)'
HTTP_TIMEOUT_SECONDS = 25
COMMUNITY_GAP_SECONDS = 4
PRICE_TIME_TO_LIVE_SECONDS = 120
WALLET_INFO_TIME_TO_LIVE_SECONDS = 30 * 86400
OWN_PRICE_MEMORY_SECONDS = 6 * 3600


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

    def state(self):
        return json.loads(json.dumps({'wallets': self.wallets, 'own_prices': self.own_prices,
                                      'account_currencies': self.account_currencies}))

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

    def lowest_price(self, appid, name, currency):
        """The lowest listing in the wallet currency (minor units), or None. A failed
        or empty answer is not cached, so it is asked again next time."""
        key = (int(appid), name, int(currency))
        cached = self._prices.get(key)
        if cached and time.time() - cached[1] < PRICE_TIME_TO_LIVE_SECONDS:
            return cached[0]
        response = self.get(PRICE_URL, {'appid': int(appid), 'market_hash_name': name, 'currency': int(currency)})
        payload = response.json() if response.ok else {}
        lowest = parse_price_minor_units(payload.get('lowest_price')) if payload.get('success') else None
        if lowest:
            self._prices[key] = (lowest, time.time())
        return lowest

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

    def price_for(self, appid, name, currency, cookies):
        """(buyer pays, seller receives) one minor unit under the lowest listing — or
        at it when that lowest listing is the one we made last — or None (no
        listing yet, or the fees would take the whole price)."""
        lowest = self.lowest_price(appid, name, currency)
        if not lowest:
            return None
        own = self.own_prices.get(f'{int(appid)}|{name}|{int(currency)}') or {}
        ours = own.get('buyer_pays') == lowest and time.time() - own.get('at', 0) < OWN_PRICE_MEMORY_SECONDS
        return listing_price(lowest, self.wallet(currency, cookies), undercut=not ours)

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

