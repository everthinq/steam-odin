"""Unit tests for CardDealsService (Andvari) — the trading-card deal finder.

Nothing touches the network: a fake fetch plays SteamCardExchange, the Steam
store, the Community Market and the per-account pages, and records every call so
the scan ORDER (sale scope before full price) and the request parameters can be
pinned. Money math is checked against Steam's fee definition exhaustively.
"""
import json
import urllib.error

import pytest

import card_deals_service as deals_module
from card_deals_service import (
    CardDealsService, RateLimited, capped_card_prices, card_drops, clean_config, fill_net_cents,
    parse_badges_page, parse_orderbook, parse_store_items,
    parse_appdetails_prices, parse_card_exchange_feed, parse_market_cards, parse_store_country,
    parse_store_search, seller_receives_cents, store_price_step, to_usd_cents, undercut_net_cents,
)


# ---- markup builders mirroring the real Steam HTML ---------------------------

def store_row(app_id, title, final_cents, discount=0, original_cents=None, before_appid='',
              app_ids=None):
    """One store search result row, shaped like the real markup (the package id,
    when present, sits BEFORE the app ids exactly as Steam renders it)."""
    discount_block = (
        f'<div class="discount_block search_discount_block{"" if discount else " no_discount"}" '
        f'data-price-final="{final_cents}" data-bundlediscount="0" data-discount="{discount}">'
        + (f'<div class="discount_pct">-{discount}%</div>' if discount else '')
        + '<div class="discount_prices">'
        + (f'<div class="discount_original_price">${original_cents / 100:.2f}</div>' if original_cents else '')
        + f'<div class="discount_final_price">${final_cents / 100:.2f}</div></div></div>')
    return (f'<a href="https://store.steampowered.com/app/{app_id}/x/?snr=1"\n\t\t\t {before_appid} '
            f'data-ds-appid="{app_ids or app_id}" data-ds-itemkey="App_{app_id}" data-ds-tagids="[1,2]" '
            f'onmouseover="GameHover( this, event, \'global_hover\', {{&quot;type&quot;:&quot;app&quot;,'
            f'&quot;id&quot;:{app_id}}} );" class="search_result_row ds_collapse_flag ">'
            f'<div class="search_capsule"><img src="x.jpg" ></div>'
            f'<div class="responsive_search_name_combined"><div class="search_name ellipsis">'
            f'<span class="title">{title}</span></div>'
            f'<div class="search_price_discount_combined responsive_secondrow" data-price-final="{final_cents}">'
            f'<div class="search_discount_and_price responsive_secondrow">{discount_block}</div></div></div>\n</a>')


def badge_row(app_id, remaining):
    drops = f'{remaining} card drops remaining' if remaining else 'No card drops remaining'
    if remaining == 1:
        drops = '1 card drop remaining'
    return (f'<div id="badge_gamebadge_{app_id}" role="button" class="badge_row is_link">'
            f'<a class="badge_row_overlay" href="https://steamcommunity.com/profiles/1/gamecards/{app_id}/"></a>'
            f'<div class="badge_title_stats_drops"><span class="progress_info_bold">{drops}</span>'
            f'<span class="how_to_get_card_drops_ctn">How do I earn card drops?</span></div></div>')


# ---- pure helpers ---------------------------------------------------------------

def _fee_total(receives):
    return receives + max(1, int(receives * 0.05)) + max(1, int(receives * 0.10))


def test_seller_receives_matches_steam_fee_definition_exhaustively():
    # The seller gets the LARGEST amount whose amount + both fees fits the price.
    for buyer_pays in range(3, 20001):
        receives = seller_receives_cents(buyer_pays)
        assert receives >= 1
        assert _fee_total(receives) <= buyer_pays
        assert _fee_total(receives + 1) > buyer_pays


@pytest.mark.parametrize('buyer_pays, receives', [
    (0, 0), (2, 0), (3, 1), (4, 2), (10, 8), (23, 20), (100, 88), (115, 100), (1150, 1000),
])
def test_seller_receives_known_values(buyer_pays, receives):
    assert seller_receives_cents(buyer_pays) == receives


def test_undercut_lists_one_cent_below_and_never_below_minimum():
    assert undercut_net_cents(10) == seller_receives_cents(9)
    assert undercut_net_cents(3) == 1          # can't list under 3 cents
    assert undercut_net_cents(1) == 1


@pytest.mark.parametrize('cards, drops', [(5, 3), (6, 3), (7, 4), (15, 8), (0, 0)])
def test_card_drops_is_half_rounded_up(cards, drops):
    assert card_drops(cards) == drops


@pytest.mark.parametrize('maximum, step', [
    (None, None), (0, None), (0.5, 5), (2.5, 5), (5, 5), (5.01, 10), (20, 20),
    (21, 25), (60, 60), (61, None), (500, None),
])
def test_store_price_step_uses_only_accepted_slider_values(maximum, step):
    assert store_price_step(maximum) == step


# ---- parsers ----------------------------------------------------------------------

def test_capped_card_prices_tames_lone_outlier_asks():
    # Real sets seen live: one card asked at $20.28 / $1,150 among 5-30c cards.
    assert capped_card_prices([7, 11, 12, 24, 115000]) == ([7, 11, 12, 24, 36], 1)
    assert capped_card_prices([5, 8, 9, 10, 15, 2028]) == ([5, 8, 9, 10, 15, 28.5], 1)
    assert capped_card_prices([60, 50, 50, 40, 55, 45]) == ([60, 50, 50, 40, 55, 45], 0)
    assert capped_card_prices([]) == ([], 0)


def test_parse_card_exchange_feed():
    payload = {'data': [
        [['1958600', 'Barro F22'], 5, '$0.49', 1790278877, 1],
        [['893850', 'Tom &amp; Jerry&#039;s'], 14, '$1,234.56', 1790272288, 0],
        [['1', 'no cards'], 0, '$0.10', 1, 1],          # zero cards: dropped
        [['2', 'bad price'], 5, 'n/a', 1, 1],           # unparseable price: dropped
        ['broken'],                                     # malformed row: dropped
    ]}
    games = parse_card_exchange_feed(payload)
    assert set(games) == {'1958600', '893850'}
    assert games['1958600'] == {'name': 'Barro F22', 'card_count': 5, 'set_cents': 49, 'updated_at': 1790278877}
    assert games['893850']['set_cents'] == 123456
    assert games['893850']['name'] == "Tom & Jerry's"


def test_parse_store_search_keeps_single_paid_games_only():
    html = ''.join([
        store_row('10', 'Discounted &amp; Co', 44, discount=90, original_cents=449),
        store_row('20', 'Full Price', 199),
        store_row('30', 'Free Game', 0),
        # Package: id before the app ids and one single app id — must still be skipped.
        store_row('40', 'Single-app Package', 99, before_appid='data-ds-packageid="96994"'),
        store_row('50', 'Bundle', 99, before_appid='data-ds-bundleid="123"'),
        store_row('60', 'Multi-app row', 99, app_ids='60,61,62'),
    ])
    games = parse_store_search(html)
    assert [g['app_id'] for g in games] == ['10', '20']
    assert games[0] == {'app_id': '10', 'name': 'Discounted & Co', 'price_cents': 44,
                        'original_cents': 449, 'discount_percent': 90}
    assert games[1]['discount_percent'] == 0 and games[1]['original_cents'] is None


def test_parse_market_cards():
    payload = {'total_count': 2, 'results': [
        {'name': 'Card A', 'hash_name': '10-Card A', 'sell_price': 8, 'sell_listings': 1972},
        {'name': 'Card B', 'hash_name': '10-Card B', 'sell_price': None, 'sell_listings': None},
    ]}
    assert parse_market_cards(payload) == [
        {'name': 'Card A', 'hash_name': '10-Card A', 'price_cents': 8, 'listings': 1972},
        {'name': 'Card B', 'hash_name': '10-Card B', 'price_cents': 0, 'listings': 0},
    ]


def test_parse_badges_page_drops_and_pagination():
    html = (badge_row('730', 0) + badge_row('10', 3) + badge_row('20', 1)
            + '<div class="pageLinks"><a href="https://steamcommunity.com/profiles/1/badges/?p=2">2</a>'
              '<a href="https://steamcommunity.com/profiles/1/badges/?p=3">3</a></div>')
    drops, last_page = parse_badges_page(html)
    assert drops == {'730': 0, '10': 3, '20': 1}
    assert last_page == 3
    assert parse_badges_page('<html>no badges</html>') == ({}, 1)


def test_parse_store_country_escaped_and_plain():
    assert parse_store_country('data-config="{&quot;COUNTRY&quot;:&quot;MD&quot;}"') == 'MD'
    assert parse_store_country('var c = {"COUNTRY":"TR"};') == 'TR'
    assert parse_store_country('nothing here') is None


def test_parse_appdetails_prices():
    payload = {
        '357070': {'success': True, 'data': {'price_overview': {
            'currency': 'HKD', 'initial': 3500, 'final': 350, 'discount_percent': 90}}},
        '220': {'success': True, 'data': {'price_overview': {
            'currency': 'USD', 'initial': 999, 'final': 999, 'discount_percent': 0}}},
        '570': {'success': True, 'data': []},          # free game: Steam sends an empty list
        '9': {'success': False},                        # not sold in that country
    }
    assert parse_appdetails_prices(payload) == {
        '357070': {'price_cents': 350, 'original_cents': 3500, 'discount_percent': 90, 'currency': 'HKD'},
        '220': {'price_cents': 999, 'original_cents': 999, 'discount_percent': 0, 'currency': 'USD'},
    }


def test_parse_store_items():
    payload = {'response': {'store_items': [
        {'appid': 10, 'success': 1, 'categories': {'feature_categoryids': [29, 2]},
         'best_purchase_option': {'final_price_in_cents': '197', 'original_price_in_cents': '329', 'discount_pct': 40,
                                  'active_discounts': [{'discount_end_date': 2000}, {'discount_end_date': 1500}]}},
        {'appid': 20, 'success': 1, 'categories': {'feature_categoryids': [2]},
         'best_purchase_option': {'final_price_in_cents': '629'}},
        {'appid': 30, 'success': 1, 'best_purchase_option': {'final_price_in_cents': '0'}},       # free
        {'appid': 40, 'success': 1, 'best_purchase_option': {'final_price_in_cents': '99',
                                                             'is_free_to_keep': True}},           # giveaway
        {'appid': 50, 'success': 1},                                                             # not for sale
        {'appid': 60, 'success': 2, 'best_purchase_option': {'final_price_in_cents': '99'}},       # unknown app
    ]}}
    assert parse_store_items(payload) == {
        '10': {'price_cents': 197, 'original_cents': 329, 'discount_percent': 40,
               'discount_ends_at': 1500, 'has_trading_cards': True},
        '20': {'price_cents': 629, 'original_cents': 629, 'discount_percent': 0,
               'discount_ends_at': None, 'has_trading_cards': False},
    }


def test_parse_orderbook_both_shapes():
    book = {'amtMaxBuyOrder': 11, 'amtMinSellOrder': 1000, 'eCurrency': 1, 'cBuyOrders': 189, 'cSellOrders': 8,
            'rgCompactBuyOrders': [11, 25, 10, 8, 7, 0, 3, 122], 'rgCompactSellOrders': [1000, 1, 1121, 7]}
    expected = {'bids': [(11, 25), (10, 8), (3, 122)], 'asks': [(1000, 1), (1121, 7)],
                'buy_orders': 189, 'sell_orders': 8, 'currency': 1}
    assert parse_orderbook({'data': {'success': True, 'data': book}}) == expected      # wrapped (real)
    assert parse_orderbook({'success': True, 'data': book}) == expected                # unwrapped
    assert parse_orderbook({'data': {'success': False}}) is None
    assert parse_orderbook({}) is None


def test_fill_net_cents_walks_the_buy_orders():
    # 40c nets 36c (36+1+3 = 40), 30c nets 27c (27+1+2 = 30).
    assert fill_net_cents([(40, 1), (30, 50)], 1) == 36
    assert fill_net_cents([(40, 1), (30, 50)], 2) == 31.5
    assert fill_net_cents([(40, 1)], 3) == 36            # beyond depth: the lowest listed bid
    assert fill_net_cents([], 5) == 0                    # nobody is buying
    assert fill_net_cents([(40, 1)], 0) == 0


def test_to_usd_cents():
    rates = {'HKD': 7.8, 'NOK': 10.5}
    assert to_usd_cents(45, 'USD', rates) == 45
    assert to_usd_cents(390, 'HKD', rates) == 50
    assert to_usd_cents(525, 'NOK', rates) == 50
    assert to_usd_cents(100, 'EUR', rates) is None       # no rate: can't price it
    assert to_usd_cents(None, 'USD', rates) is None
    assert to_usd_cents(100, None, rates) is None        # unknown currency is never assumed USD


# ---- config validation --------------------------------------------------------------

def test_clean_config_coerces_and_keeps_only_supplied_keys():
    clean = clean_config({'card_deals_max_price': '12.345', 'card_deals_include_full_price': 0,
                          'card_deals_scan_interval_hours': '6', 'card_deals_fallback_country': 'md',
                          'card_deals_chat_id': ' -1001234 ', 'card_deals_valuation': 'Listing',
                          'unrelated': 'ignored'})
    assert clean == {'card_deals_max_price': 12.35, 'card_deals_include_full_price': False,
                     'card_deals_scan_interval_hours': 6, 'card_deals_fallback_country': 'MD',
                     'card_deals_chat_id': '-1001234', 'card_deals_valuation': 'listing'}


@pytest.mark.parametrize('body', [
    {'card_deals_scan_interval_hours': 0},
    {'card_deals_scan_interval_hours': 'soon'},
    {'card_deals_max_price': -1},
    {'card_deals_min_discount': 101},
    {'card_deals_fallback_country': 'Turkey'},
    {'card_deals_chat_id': 'drop table'},
    {'card_deals_valuation': 'optimistic'},
])
def test_clean_config_rejects_bad_values(body):
    with pytest.raises(ValueError):
        clean_config(body)


# ---- the service, end to end against a fake Steam --------------------------------------

class FakeStorage:
    def __init__(self, accounts):
        self.accounts = accounts

    def list_accounts(self):
        return list(self.accounts)

    def load_account(self, steamid):
        return {'account_name': self.accounts[steamid]}


class FakeSteam:
    def __init__(self, accounts, sessions):
        self.storage = FakeStorage(accounts)
        self.sessions = sessions

    def web_session_cookie_for(self, steamid, min_ttl_seconds=300):
        return {'steamLoginSecure': f'{steamid}%7C%7Ctoken', 'sessionid': 's'} if steamid in self.sessions else None

    def web_session_cookie(self, min_ttl_seconds=300):
        for steamid in self.storage.list_accounts():
            cookies = self.web_session_cookie_for(steamid)
            if cookies:
                return steamid, cookies
        return None


class FakeSettings:
    def __init__(self, **overrides):
        self.settings = {
            'card_deals_auto_scan_enabled': True, 'card_deals_scan_interval_hours': 12,
            'card_deals_include_full_price': True, 'card_deals_max_price': 20.0,
            'card_deals_min_discount': 0, 'card_deals_alerts_enabled': True,
            'card_deals_alert_min_return_percent': 50, 'card_deals_alert_min_profit': 0.25,
            'card_deals_chat_id': '', 'card_deals_fallback_country': 'TR',
            # The older tests pin the listing-price model; buy-order ("instant")
            # valuation — the production default — has its own tests below.
            'card_deals_valuation': 'listing',
            'telegram_bot_token': 'bot', 'telegram_chat_id': '111',
        }
        self.settings.update(overrides)

    def get_settings(self):
        return dict(self.settings)


# Accounts: alpha in Turkey (USD), bravo + delta in Moldova (USD) — so Moldova is
# the discovery country — and charlie in Hong Kong (Hong Kong dollars).
ACCOUNTS = {'A': 'alpha', 'B': 'bravo', 'C': 'charlie', 'D': 'delta'}
COUNTRY = {'A': 'TR', 'B': 'MD', 'C': 'HK', 'D': 'MD'}
OWNED = {'A': [200, 730], 'B': [730], 'C': [], 'D': [730]}
DROPS = {'A': ('200', 2), 'B': ('730', 0), 'C': ('730', 0), 'D': ('730', 0)}
CURRENCY = {'md': 'USD', 'tr': 'USD', 'hk': 'HKD'}
HKD_PER_USD = 7.8

# Game catalogue.
#   100: on sale, 6 cards (3 drops) at ~50c each -> clearly profitable
#   200: on sale, owned by alpha (2 drops left there)
#   300: on sale but a loser (cheap cards) -> never priced in the other regions
#   400: full price, profitable
#   500: full price, above the $20 maximum (filtered exactly)
#   600: discounted, ALSO returned by the full-price search (must stay in sale scope)
#   700: full price and hopeless -> never priced in the other regions
#   800: on sale, a loss at the Moldova price but a deal in Turkey (5x cheaper there)
#   900: on sale, looks huge only because one card is asked at $20.28 (capped -> no deal)
FEED = {'data': [
    [['100', 'Sale Winner'], 6, '$3.00', 1, 1],
    [['200', 'Owned Sale'], 6, '$3.00', 1, 1],
    [['300', 'Sale Loser'], 6, '$0.30', 1, 1],
    [['400', 'Full Price Winner'], 10, '$10.00', 1, 1],
    [['500', 'Too Expensive'], 10, '$900.00', 1, 1],
    [['600', 'Sale Also In Full'], 6, '$3.00', 1, 1],
    [['700', 'Full Price Loser'], 6, '$0.30', 1, 1],
    [['800', 'Turkey Only Deal'], 6, '$3.00', 1, 1],
    [['900', 'Outlier Card Mirage'], 6, '$20.73', 1, 1],
]}
# Discovery (Moldova store search): (app, title, final cents, discount, original cents)
SALE_ROWS = [('100', 'Sale Winner', 45, 90, 459), ('200', 'Owned Sale', 51, 80, 255),
             ('300', 'Sale Loser', 99, 50, 199), ('600', 'Sale Also In Full', 60, 70, 199),
             ('800', 'Turkey Only Deal', 500, 50, 1000), ('900', 'Outlier Card Mirage', 99, 50, 199)]
FULL_ROWS = [('400', 'Full Price Winner', 209, 0, None), ('500', 'Too Expensive', 2999, 0, None),
             ('600', 'Sale Also In Full', 60, 70, 199), ('700', 'Full Price Loser', 999, 0, None)]
# Other regions (appdetails): app -> (final, initial, discount), in that store's currency.
REGIONAL = {
    'tr': {'100': (44, 449, 90), '200': (50, 250, 80), '300': (99, 199, 50),
           '400': (199, 199, 0), '600': (60, 199, 70), '700': (999, 999, 0), '800': (100, 1000, 90),
           '220': (999, 999, 0)},
    'hk': {'100': (390, 3900, 90), '200': (390, 1950, 80), '300': (780, 1560, 50),
           '400': (1560, 1560, 0), '600': (468, 1560, 70), '700': (7800, 7800, 0), '800': (3900, 7800, 50),
           '220': (7800, 7800, 0)},
    'md': {'220': (999, 999, 0)},
}
SALE_ENDS_AT = 1893456000          # 2030-01-01, always in the future for the tests
NO_LONGER_HAS_CARDS = set()        # apps the store backend reports without trading cards
# Buy orders per card, [(price, quantity), ...] best first. Default: a modest book
# a bit under the ask. App 100's are explicit so the instant math can be pinned.
BIDS = {
    '100-Card 0': [(40, 1), (30, 50)], '100-Card 1': [(35, 3), (20, 50)],
    '100-Card 2': [(30, 2), (25, 50)], '100-Card 3': [(25, 1), (10, 50)],
    '100-Card 4': [(45, 1), (40, 50)], '100-Card 5': [(20, 5), (15, 50)],
}
ORDERBOOK_CURRENCY = {}            # card -> eCurrency override (default USD = 1)


def orderbook_for(name):
    app_id, index = name.split('-Card ')
    ask = MARKET[app_id][int(index)]
    bids = BIDS.get(name, [(max(3, ask * 7 // 10), 5), (max(3, ask // 2), 100)])
    return {'amtMaxBuyOrder': bids[0][0], 'amtMinSellOrder': ask, 'eCurrency': ORDERBOOK_CURRENCY.get(name, 1),
            'cBuyOrders': sum(q for _, q in bids), 'cSellOrders': 500,
            'rgCompactBuyOrders': [v for level in bids for v in level], 'rgCompactSellOrders': [ask, 500]}


MARKET = {
    '100': [60, 50, 50, 40, 55, 45],     # cents, lowest listing per card
    '200': [60, 50, 50, 40, 55, 45],
    '400': [100] * 10,
    '600': [60, 50, 50, 40, 55, 45],
    '900': [5, 8, 9, 10, 15, 2028],
}


class FakeSteamWeb:
    """Plays every remote endpoint and records the calls in order."""
    def __init__(self, free_first_page=False, market_rate_limited=False, rates_down=False,
                 currency_down=False, orderbook_rate_limited=False):
        self.calls = []
        self.free_first_page = free_first_page
        self.market_rate_limited = market_rate_limited
        self.rates_down = rates_down
        self.currency_down = currency_down
        self.orderbook_rate_limited = orderbook_rate_limited

    def __call__(self, url, params, cookies, host):
        params = dict(params or {})
        self.calls.append((url, params, bool(cookies), host))
        if url == deals_module.CARD_EXCHANGE_FEED_URL:
            return json.dumps(FEED)
        if url == deals_module.EXCHANGE_RATES_URL:
            if self.rates_down:
                raise urllib.error.URLError('rates offline')
            return json.dumps({'result': 'success', 'rates': {'USD': 1, 'HKD': HKD_PER_USD}})
        steamid = cookies['steamLoginSecure'].split('%7C')[0] if cookies else None
        if url == deals_module.STORE_ACCOUNT_URL:
            return f'<div data-config="{{&quot;COUNTRY&quot;:&quot;{COUNTRY[steamid]}&quot;}}"></div>'
        if url == deals_module.STORE_USERDATA_URL:
            return json.dumps({'rgOwnedApps': OWNED[steamid]})
        if '/badges/' in url:
            app_id, drops = DROPS[url.split('/profiles/')[1].split('/')[0]]
            return badge_row(app_id, drops)
        if url == deals_module.STORE_SEARCH_URL:
            return self._store(params)
        if url == deals_module.STORE_APPDETAILS_URL:
            if self.currency_down and params['appids'] == '220':
                raise urllib.error.URLError('store offline')
            region, currency = REGIONAL[params['cc']], CURRENCY[params['cc']]
            return json.dumps({app: ({'success': True, 'data': {'price_overview': {
                'currency': currency, 'final': region[app][0], 'initial': region[app][1],
                'discount_percent': region[app][2]}}} if app in region else {'success': False})
                for app in params['appids'].split(',')})
        if url == deals_module.STORE_BROWSE_ITEMS_URL:
            request = json.loads(params['input_json'])
            country = request['context']['country_code'].lower()
            region = self._backend_region(country)
            items = []
            for wanted in request['ids']:
                app = str(wanted['appid'])
                if app not in region:
                    items.append({'appid': int(app), 'success': 1})       # not sold there
                    continue
                final, initial, discount = region[app]
                option = {'final_price_in_cents': str(final), 'original_price_in_cents': str(initial),
                          'discount_pct': discount}
                if discount:
                    option['active_discounts'] = [{'discount_end_date': SALE_ENDS_AT}]
                items.append({'appid': int(app), 'success': 1, 'best_purchase_option': option,
                              'categories': {'feature_categoryids': [2] if app in NO_LONGER_HAS_CARDS else [29, 2]}})
            return json.dumps({'response': {'store_items': items}})
        if url == deals_module.MARKET_ORDERBOOK_URL:
            if self.orderbook_rate_limited:
                raise RateLimited('Steam rate limit (429) on orderbook')
            appid, name = json.loads(params['qp'])
            assert appid == 753 and params['q'] == 'Load'
            return json.dumps({'data': {'success': True, 'data': orderbook_for(name)}})
        if url == deals_module.MARKET_SEARCH_URL:
            if self.market_rate_limited:
                raise RateLimited('Steam rate limit (429) on community')
            app_id = params['category_753_Game[]'].split('_')[-1]
            prices = MARKET.get(app_id, [])
            start = params['start']
            page = prices[start:start + params['count']]
            return json.dumps({'success': True, 'total_count': len(prices), 'results': [
                {'name': f'Card {i}', 'hash_name': f'{app_id}-Card {i}', 'sell_price': p, 'sell_listings': 500}
                for i, p in enumerate(page, start)]})
        raise AssertionError(f'unexpected url {url}')

    @staticmethod
    def _backend_region(country):
        """Store-backend prices for a country: the other regions from REGIONAL,
        the discovery country (Moldova) from its store search rows."""
        if country != 'md':
            return REGIONAL[country]
        return {a: (p, o or p, d) for a, _, p, d, o in FULL_ROWS + SALE_ROWS}

    def _store(self, params):
        assert params['cc'] == 'md', 'discovery must run in the discovery country only'
        rows = SALE_ROWS if params.get('specials') else FULL_ROWS
        pages = [[store_row(a, t, p, d, o) for a, t, p, d, o in rows]]
        if self.free_first_page:
            # Real listing: free games come first — a page with rows but no priced game.
            pages.insert(0, [store_row(f'9{i}', 'Free Game', 0) for i in range(3)])
        index = params['start'] // 100
        html = ''.join(pages[index]) if index < len(pages) else ''
        total = len(pages) * 100 if len(pages) > 1 else sum(len(p) for p in pages)
        return json.dumps({'success': 1, 'total_count': total, 'results_html': html})

    def calls_to(self, url):
        return [c for c in self.calls if c[0] == url]


def make_service(tmp_path, web=None, settings=None, sessions=('A', 'B', 'C', 'D'), notify=None):
    sent = []

    def fake_notify(settings, text, html=None):
        sent.append({'chat': settings.get('telegram_chat_id'), 'text': text, 'html': html})
        return {'ok': True}
    service = CardDealsService(
        FakeSteam(ACCOUNTS, set(sessions)), settings or FakeSettings(),
        fetch=web or FakeSteamWeb(), sleep=lambda seconds: None,
        notify=notify or fake_notify, cache_file=str(tmp_path / 'card_deals.json.gz'))
    return service, sent


def run_scan(service, force=True, include_full_price=True):
    service._scan_safely(force, include_full_price)
    return service.status()


def by_app(result):
    return {row['app_id']: row for row in result['deals']}


def expected_cents(app_id):
    nets = [undercut_net_cents(p) for p in MARKET[app_id]]
    return card_drops(len(MARKET[app_id])) * sum(nets) / len(nets)


def test_sale_scope_is_scanned_and_checked_before_full_price(tmp_path):
    web = FakeSteamWeb()
    service, _ = make_service(tmp_path, web)
    status = run_scan(service)
    assert status['error'] is None and not status['running']

    def kind(call):
        url, params = call[0], call[1]
        if url == deals_module.STORE_SEARCH_URL:
            return 'store-sale' if params.get('specials') else 'store-full'
        if url == deals_module.MARKET_SEARCH_URL:
            return 'market-' + params['category_753_Game[]'].split('_')[-1]
        if url == deals_module.STORE_BROWSE_ITEMS_URL:
            return 'regional'
        return None
    order = [k for k in (kind(c) for c in web.calls) if k]
    first_full = order.index('store-full')
    before = set(order[:first_full])
    assert before <= {'store-sale', 'regional', 'market-100', 'market-200', 'market-600', 'market-800',
                      'market-900'}
    assert {'store-sale', 'regional', 'market-100'} <= before
    assert order.index('market-400') > first_full


def test_full_price_scope_is_skipped_when_disabled(tmp_path):
    web = FakeSteamWeb()
    service, _ = make_service(tmp_path, web)
    run_scan(service, include_full_price=False)
    store_calls = web.calls_to(deals_module.STORE_SEARCH_URL)
    assert store_calls and all(c[1].get('specials') == 1 for c in store_calls)
    assert 'tag_app_400' not in str(web.calls_to(deals_module.MARKET_SEARCH_URL))
    assert service.deals(FakeSettings().get_settings(), 'full')['deals'] == []


def test_request_parameters(tmp_path):
    web = FakeSteamWeb()
    service, _ = make_service(tmp_path, web)
    run_scan(service)
    for _, params, with_cookies, host in web.calls_to(deals_module.STORE_SEARCH_URL):
        assert params['category1'] == 998 and params['category2'] == 29
        assert params['maxprice'] == 20          # $20 maximum -> the $20 slider step
        assert params['cc'] == 'md'              # discovery: the country most accounts are in
        assert not with_cookies and host == 'store'
    # Regional prices + sale end dates: Steam's store backend, every account country.
    backend = web.calls_to(deals_module.STORE_BROWSE_ITEMS_URL)
    requests = [json.loads(c[1]['input_json']) for c in backend]
    assert {r['context']['country_code'] for r in requests} == {'MD', 'TR', 'HK'}
    assert all(c[3] == 'api' and not c[2] for c in backend)
    priced = {str(i['appid']) for r in requests for i in r['ids']}
    assert '300' in priced                   # on-sale scope: every game priced everywhere
    assert '700' not in priced               # full price: hopeless games are not
    assert max(len(r['ids']) for r in requests) <= 250
    # appdetails is only the once-a-week currency probe now.
    assert all(c[1]['appids'] == '220' for c in web.calls_to(deals_module.STORE_APPDETAILS_URL))
    assert web.calls_to(deals_module.MARKET_ORDERBOOK_URL) == []    # listing valuation: no order books
    currency_probes = [c for c in web.calls_to(deals_module.STORE_APPDETAILS_URL) if c[1]['appids'] == '220']
    assert sorted(c[1]['cc'] for c in currency_probes) == ['hk', 'md', 'tr']
    for _, params, with_cookies, host in web.calls_to(deals_module.MARKET_SEARCH_URL):
        assert params['category_753_item_class[]'] == 'item_class_2'
        assert params['category_753_cardborder[]'] == 'tag_cardborder_0'
        assert params['count'] == 10 and with_cookies and host == 'community'


def test_sale_deal_math_regions_currency_ownership_and_verification(tmp_path):
    service, _ = make_service(tmp_path)
    run_scan(service)
    result = service.deals(FakeSettings().get_settings(), 'sale')
    rows = by_app(result)
    assert set(rows) == {'100', '200', '600', '800'}  # 300 is a loser: not listed as a deal

    winner = rows['100']
    expected = expected_cents('100')
    nets = [undercut_net_cents(p) for p in MARKET['100']]
    assert winner['verified'] is True
    assert winner['card_drops'] == 3
    assert winner['expected_net'] == round(expected / 100, 2)
    assert winner['worst_case_net'] == round(3 * min(nets) / 100, 2)
    # Headline price = the discovery country (Moldova, where most accounts are);
    # HK$3.90 at 7.8 per USD = $0.50 is the dearest region, Turkey the cheapest.
    assert winner['price'] == 0.45 and winner['price_low'] == 0.44 and winner['price_high'] == 0.50
    assert winner['discovery_country'] == 'MD'
    assert winner['regional_prices'] == {
        'MD': {'usd': 0.45, 'local': 0.45, 'currency': 'USD'},
        'TR': {'usd': 0.44, 'local': 0.44, 'currency': 'USD'},
        'HK': {'usd': 0.50, 'local': 3.90, 'currency': 'HKD'},
    }
    assert winner['profit'] == round((expected - 45) / 100, 2)
    assert winner['best_profit'] == round((expected - 44) / 100, 2)          # alpha, in Turkey
    assert winner['is_deal'] is True and winner['profitable_accounts'] == 4
    assert winner['original_price'] == 4.59           # the headline (Moldova) original price
    assert winner['scope'] == 'sale' and winner['discount_percent'] == 90
    buyers = {b['account_name']: b for b in winner['buyers']}
    assert {name: b['price'] for name, b in buyers.items()} == {
        'alpha': 0.44, 'bravo': 0.45, 'charlie': 0.50, 'delta': 0.45}
    assert buyers['alpha']['profit'] == round((expected - 44) / 100, 2)
    assert buyers['charlie']['profit'] == round((expected - 50) / 100, 2)
    assert winner['total_profit_all_accounts'] == round(sum(b['profit'] for b in buyers.values()), 2)

    owned = rows['200']
    assert owned['owners'] == [{'account_name': 'alpha', 'drops_remaining': 2}]
    assert sorted(b['account_name'] for b in owned['buyers']) == ['bravo', 'charlie', 'delta']

    accounts = {a['account_name']: a for a in result['accounts']}
    assert accounts['alpha']['country'] == 'TR' and accounts['charlie']['country'] == 'HK'
    assert accounts['alpha']['card_drops_remaining'] == 2
    assert result['summary']['countries'] == ['HK', 'MD', 'TR']
    assert result['summary']['currencies'] == {'MD': 'USD', 'TR': 'USD', 'HK': 'HKD'}
    assert result['summary']['scopes']['sale']['discovery_country'] == 'MD'


def test_full_scope_filters_price_exactly_and_leaves_discounts_to_sale(tmp_path):
    service, _ = make_service(tmp_path)
    run_scan(service)
    settings = FakeSettings().get_settings()
    full = by_app(service.deals(settings, 'full'))
    assert set(full) == {'400'}                        # 500 over $20; 600 is on sale
    assert full['400']['scope'] == 'full' and full['400']['discount_percent'] == 0
    assert full['400']['price'] == 2.09 and full['400']['price_low'] == 1.99
    assert full['400']['original_price'] is None
    everything = by_app(service.deals(settings, 'all'))
    assert set(everything) == {'100', '200', '600', '800', '400'}
    assert everything['600']['scope'] == 'sale'
    assert '500' not in by_app(service.deals(settings, 'full', include_unprofitable=True))
    counts = service.deals(settings, 'sale')['summary']['scopes']
    assert counts['sale']['profitable'] == 4 and counts['full']['profitable'] == 1


def test_outlier_card_ask_does_not_make_a_fake_deal(tmp_path):
    service, _ = make_service(tmp_path)
    run_scan(service)
    settings = FakeSettings().get_settings()
    assert '900' not in by_app(service.deals(settings, 'sale'))
    mirage = by_app(service.deals(settings, 'sale', include_unprofitable=True))['900']
    assert mirage['verified'] is True and mirage['capped_cards'] == 1
    capped = [5, 8, 9, 10, 15, 28.5]
    assert mirage['expected_net'] == round(3 * sum(undercut_net_cents(p) for p in capped) / 6 / 100, 2)
    outlier = next(card for card in mirage['cards'] if card['capped'])
    assert outlier['lowest_ask'] == 20.28 and outlier['valued_at'] == 0.28


def test_checked_deals_rank_above_estimates(tmp_path):
    web = FakeSteamWeb(market_rate_limited=True)
    service, _ = make_service(tmp_path, web)
    run_scan(service)                                   # nothing checked: all estimates
    service._state['verified']['600'] = {'fetched_at': deals_module.time.time(),
                                         'cards': [{'name': 'x', 'hash_name': 'x', 'price_cents': 50, 'listings': 9}] * 6}
    ranked = service.deals(FakeSettings().get_settings(), 'sale')['deals']
    assert ranked[0]['app_id'] == '600' and ranked[0]['verified'] is True
    assert all(not r['verified'] for r in ranked[1:])


def test_cheap_region_makes_a_deal_even_when_most_accounts_lose(tmp_path):
    service, _ = make_service(tmp_path)
    run_scan(service)
    deal = by_app(service.deals(FakeSettings().get_settings(), 'sale'))['800']
    net = deal['expected_net']
    assert deal['discount_percent'] == 50 and deal['best_discount_percent'] == 90
    assert deal['original_price'] == 10.00
    assert deal['price'] == 5.00 and deal['profit'] == round(net - 5.00, 2) < 0   # a loss in Moldova
    assert deal['is_deal'] is True and deal['profitable_accounts'] == 1
    assert deal['best_profit'] == round(net - 1.00, 2) > 0                      # alpha, Turkey $1.00
    profitable = [b['account_name'] for b in deal['buyers'] if b['profit'] and b['profit'] > 0]
    assert profitable == ['alpha']
    assert deal['total_profit_all_accounts'] == deal['best_profit']


def test_hopeless_game_is_listed_but_not_a_deal(tmp_path):
    service, _ = make_service(tmp_path)
    run_scan(service)
    everything = by_app(service.deals(FakeSettings().get_settings(), 'all', include_unprofitable=True))
    assert everything['300']['is_deal'] is False and everything['300']['profitable_accounts'] == 0
    assert set(everything['700']['regional_prices']) == {'MD'}     # full price: not priced abroad
    ranked = [r['is_deal'] for r in service.deals(FakeSettings().get_settings(), 'all', True)['deals']]
    assert ranked == sorted(ranked, reverse=True)                   # deals always rank first


def test_filters_apply_without_rescanning(tmp_path):
    service, _ = make_service(tmp_path)
    run_scan(service)
    strict = FakeSettings(card_deals_min_discount=85, card_deals_max_price=1.0).get_settings()
    # 800 qualifies through Turkey: 90% off and $1.00 there.
    assert set(by_app(service.deals(strict, 'sale'))) == {'100', '800'}
    assert set(by_app(service.deals(strict, 'full'))) == set()     # 400 costs $1.99+


def test_free_games_first_page_does_not_end_the_listing(tmp_path):
    # Regression: the real full listing starts with pages of free games that parse
    # to nothing; the scan must keep paging instead of stopping.
    web = FakeSteamWeb(free_first_page=True)
    service, _ = make_service(tmp_path, web)
    run_scan(service)
    assert '400' in by_app(service.deals(FakeSettings().get_settings(), 'full'))


def test_market_rate_limit_degrades_to_feed_estimate(tmp_path):
    web = FakeSteamWeb(market_rate_limited=True)
    service, _ = make_service(tmp_path, web)
    status = run_scan(service)
    assert status['error'] is None                     # rate limit degrades, never aborts
    assert 'Market check stopped early' in (status['message'] or '')
    winner = by_app(service.deals(FakeSettings().get_settings(), 'sale'))['100']
    assert winner['verified'] is False and winner['worst_case_net'] is None
    # feed: $3.00 / 6 cards = 50c average -> 3 drops * net(49c)
    assert winner['expected_net'] == round(3 * seller_receives_cents(49) / 100, 2)


def test_missing_exchange_rates_leave_foreign_accounts_unpriced(tmp_path):
    web = FakeSteamWeb(rates_down=True)
    service, _ = make_service(tmp_path, web)
    status = run_scan(service)
    assert status['error'] is None
    assert 'Exchange rates unavailable' in (status['message'] or '') or status['message'] is None
    winner = by_app(service.deals(FakeSettings().get_settings(), 'sale'))['100']
    assert set(winner['regional_prices']) == {'MD', 'TR'}          # HKD can't be converted
    charlie = next(b for b in winner['buyers'] if b['account_name'] == 'charlie')
    assert charlie['price'] is None and charlie['profit'] is None
    assert winner['price'] == 0.45 and winner['price_high'] == 0.45  # USD regions only


def test_unknown_discovery_currency_fails_loudly(tmp_path):
    web = FakeSteamWeb(currency_down=True)
    service, _ = make_service(tmp_path, web)
    status = run_scan(service)
    assert 'could not learn the store currency for MD' in status['error']
    assert web.calls_to(deals_module.STORE_SEARCH_URL) == []        # nothing priced blindly


def test_discovery_country_is_the_most_common_then_fallback(tmp_path):
    service, _ = make_service(tmp_path)
    service._state['accounts'] = {'1': {'country': 'TR'}, '2': {'country': 'MD'}}
    assert service._discovery_country({'card_deals_fallback_country': 'TR'}) == 'TR'   # tie -> fallback
    assert service._discovery_country({'card_deals_fallback_country': 'NO'}) == 'MD'   # tie -> alphabetical
    service._state['accounts']['3'] = {'country': 'MD'}
    assert service._discovery_country({'card_deals_fallback_country': 'TR'}) == 'MD'   # majority wins
    service._state['accounts'] = {}
    assert service._discovery_country({'card_deals_fallback_country': 'NO'}) == 'NO'


def test_account_without_session_is_ownership_unknown(tmp_path):
    service, _ = make_service(tmp_path, sessions=('A', 'B', 'D'))
    run_scan(service)
    winner = by_app(service.deals(FakeSettings().get_settings(), 'sale'))['100']
    charlie = next(b for b in winner['buyers'] if b['account_name'] == 'charlie')
    assert charlie['ownership_unknown'] is True
    assert charlie['price'] is None                    # its country is unknown
    accounts = {a['account_name']: a for a in service.deals(FakeSettings().get_settings())['accounts']}
    assert accounts['charlie']['error'] == 'no fresh web session'


def test_alerts_once_per_price_per_scope_with_chat_override(tmp_path):
    settings = FakeSettings(card_deals_chat_id='-100999')
    service, sent = make_service(tmp_path, settings=settings)
    run_scan(service)
    assert [m['chat'] for m in sent] == ['-100999', '-100999']
    assert 'on sale' in sent[0]['text'] and 'full price' in sent[1]['text']
    assert 'Sale Winner' in sent[0]['text'] and 'Full Price Winner' in sent[1]['text']
    assert '<a href="https://store.steampowered.com/app/100/">Sale Winner</a>' in sent[0]['html']
    # Same prices on the next scan: nothing new to say.
    run_scan(service)
    assert len(sent) == 2
    # A price drop in ONE region (Turkey) is a new deal.
    original = REGIONAL['tr']['100']
    REGIONAL['tr']['100'] = (30, 449, 93)
    try:
        run_scan(service)
    finally:
        REGIONAL['tr']['100'] = original
    assert len(sent) == 3 and 'Sale Winner' in sent[2]['text']


def test_alerts_respect_thresholds_and_switch(tmp_path):
    service, sent = make_service(tmp_path, settings=FakeSettings(card_deals_alert_min_return_percent=100000))
    run_scan(service)
    assert sent == []
    (tmp_path / 'b').mkdir()
    service, sent = make_service(tmp_path / 'b', settings=FakeSettings(card_deals_alerts_enabled=False))
    run_scan(service)
    assert sent == []


def test_failed_alert_is_retried_next_scan(tmp_path):
    attempts = []

    def failing(settings, text, html=None):
        attempts.append(text)
        return {'ok': False, 'error': 'telegram down'}
    service, _ = make_service(tmp_path, notify=failing)
    run_scan(service)
    run_scan(service)
    assert len(attempts) == 4                          # both scopes, both scans: nothing marked sent


def test_cache_round_trip_and_older_layout_reset(tmp_path):
    service, _ = make_service(tmp_path)
    run_scan(service)
    settings = FakeSettings().get_settings()
    before = service.deals(settings, 'all')['deals']
    reloaded, _ = make_service(tmp_path)
    assert reloaded.deals(settings, 'all')['deals'] == before

    import gzip
    with gzip.open(tmp_path / 'card_deals.json.gz', 'wt') as f:
        # An older layout: store keyed by country instead of by scope.
        json.dump({'store': {'sale': {'TR': {'games': {}}}}, 'feed': None, 'alerted': {'x': 1}}, f)
    reset, _ = make_service(tmp_path)
    assert reset._state['store'] == {'sale': None, 'full': None}
    assert reset._state['alerted'] == {'x': 1}                        # the rest is kept
    assert reset.deals(settings, 'all')['deals'] == []


def test_cached_data_is_reused_unless_forced(tmp_path):
    web = FakeSteamWeb()
    service, _ = make_service(tmp_path, web)
    run_scan(service, force=True)
    store_calls = len(web.calls_to(deals_module.STORE_SEARCH_URL))
    run_scan(service, force=False)
    assert len(web.calls_to(deals_module.STORE_SEARCH_URL)) == store_calls      # still fresh
    assert len(web.calls_to(deals_module.CARD_EXCHANGE_FEED_URL)) == 1
    assert len(web.calls_to(deals_module.EXCHANGE_RATES_URL)) == 1
    assert len(web.calls_to(deals_module.STORE_ACCOUNT_URL)) == 4               # accounts once
    # Changing the maximum price invalidates the store snapshot even when fresh.
    service.settings_manager.settings['card_deals_max_price'] = 5.0
    run_scan(service, force=False)
    assert len(web.calls_to(deals_module.STORE_SEARCH_URL)) > store_calls
    assert web.calls_to(deals_module.STORE_SEARCH_URL)[-1][1]['maxprice'] == 5


def test_second_scan_request_while_running_is_ignored(tmp_path):
    service, _ = make_service(tmp_path)
    service._job['running'] = True
    status = service.start_scan()
    assert status['running'] is True and status['phase'] is None       # untouched, no new thread


# ---- buy-order ("instant") valuation, sale end dates, lost trading cards --------------

def instant_settings(**overrides):
    return FakeSettings(card_deals_valuation='instant', **overrides)


def test_instant_valuation_uses_buy_orders_and_order_book_depth(tmp_path):
    service, _ = make_service(tmp_path, settings=instant_settings())
    run_scan(service)
    row = by_app(service.deals(instant_settings().get_settings(), 'sale', include_unprofitable=True))['100']
    assert row['value_source'] == 'buy_orders'
    top_nets = [seller_receives_cents(BIDS[f'100-Card {i}'][0][0]) for i in range(6)]
    instant = 3 * sum(top_nets) / 6
    assert row['expected_net'] == row['expected_net_instant'] == round(instant / 100, 2)
    assert row['expected_net_listing'] == round(expected_cents('100') / 100, 2)
    assert row['worst_case_net'] == round(3 * min(top_nets) / 100, 2)
    # 4 accounts can buy it, 3 drops each over a 6-card set -> 2 copies of every card sold together.
    assert row['copies_per_card_all_accounts'] == 2
    depth = 3 * sum(fill_net_cents(BIDS[f'100-Card {i}'], 2) for i in range(6)) / 6
    prices = {'alpha': 44, 'bravo': 45, 'charlie': 50, 'delta': 45}
    assert row['total_profit_all_accounts'] == round(sum(max(0, depth - p) for p in prices.values()) / 100, 2)
    assert depth < instant                        # dumping copies together costs money
    card = next(c for c in row['cards'] if c['name'] == 'Card 0')
    assert card['highest_bid'] == 0.40 and card['buy_orders'] == 51
    assert row['profit'] == round((instant - 45) / 100, 2)


def test_order_books_follow_the_market_check_and_cover_only_deals(tmp_path):
    web = FakeSteamWeb()
    service, _ = make_service(tmp_path, web, settings=instant_settings())
    run_scan(service, include_full_price=False)
    kinds = [('book' if c[0] == deals_module.MARKET_ORDERBOOK_URL else
              'market' if c[0] == deals_module.MARKET_SEARCH_URL else None) for c in web.calls]
    kinds = [k for k in kinds if k]
    assert kinds.index('book') > max(i for i, k in enumerate(kinds) if k == 'market')
    books = {json.loads(c[1]['qp'])[1].split('-')[0] for c in web.calls_to(deals_module.MARKET_ORDERBOOK_URL)}
    assert books <= {'100', '200', '600', '800'}  # listing-price deals only: not 300 (loser) or 900 (mirage)
    assert '100' in books
    for _, params, with_cookies, host in web.calls_to(deals_module.MARKET_ORDERBOOK_URL):
        assert host == 'orderbook' and not with_cookies       # anonymous, own throttle


def test_instant_falls_back_to_listings_until_every_book_is_known(tmp_path):
    ORDERBOOK_CURRENCY['100-Card 3'] = 5                       # not US dollars: unusable
    try:
        service, _ = make_service(tmp_path, settings=instant_settings())
        run_scan(service)
    finally:
        ORDERBOOK_CURRENCY.clear()
    row = by_app(service.deals(instant_settings().get_settings(), 'sale', include_unprofitable=True))['100']
    assert row['value_source'] == 'listings' and row['expected_net_instant'] is None
    assert row['expected_net'] == round(expected_cents('100') / 100, 2)
    assert '100-Card 3' not in service._state['orderbooks']


def test_without_buy_orders_nothing_is_a_confirmed_deal(tmp_path):
    web = FakeSteamWeb(orderbook_rate_limited=True)
    service, sent = make_service(tmp_path, web, settings=instant_settings())
    status = run_scan(service)
    assert status['error'] is None and 'Buy-order check stopped early' in (status['message'] or '')
    result = service.deals(instant_settings().get_settings(), 'sale')
    assert result['deals'] == []                           # listing prices can't confirm a deal
    assert result['summary']['awaiting_buy_orders'] >= 1
    waiting = by_app(service.deals(instant_settings().get_settings(), 'sale', include_unprofitable=True))['100']
    assert waiting['awaiting_buy_orders'] is True and waiting['is_deal'] is False
    assert waiting['value_source'] == 'listings'
    assert sent == []                                      # and nothing is alerted


def test_buy_order_rows_rank_above_listing_rows(tmp_path):
    service, _ = make_service(tmp_path, settings=instant_settings())
    run_scan(service)
    for name in [n for n in service._state['orderbooks'] if n.startswith('600-')]:
        del service._state['orderbooks'][name]                # 600 falls back to listings
    everything = service.deals(instant_settings().get_settings(), 'sale', include_unprofitable=True)['deals']
    stages = [0 if r['is_deal'] else 1 if r['awaiting_buy_orders'] else 2 for r in everything]
    assert stages == sorted(stages)                        # deals, then awaiting, then the rest
    assert all(r['value_source'] == 'buy_orders' for r in everything if r['is_deal'])
    assert by_app({'deals': everything})['600']['awaiting_buy_orders'] is True


def test_instant_alerts_only_confirmed_deals(tmp_path):
    service, sent = make_service(tmp_path, settings=instant_settings(card_deals_alert_min_return_percent=0,
                                                                     card_deals_alert_min_profit=0))
    run_scan(service)
    every_name = {row[1] for row in SALE_ROWS + FULL_ROWS}
    confirmed = {r['name'] for r in service.deals(instant_settings().get_settings(), 'all')['deals']}
    alerted = {name for m in sent for name in every_name if f'• {name}:' in m['text']}
    assert confirmed, 'the fixture must produce at least one buy-order-confirmed deal'
    assert alerted == confirmed               # every confirmed deal, and nothing else
    assert all('sells instantly to buy orders' in line for m in sent for line in m['text'].split('\n')[1:])


def test_never_checked_cards_get_their_buy_orders_first(tmp_path):
    web = FakeSteamWeb()
    service, _ = make_service(tmp_path, web, settings=instant_settings())
    run_scan(service, include_full_price=False)
    # Age every book except app 600's, then drop 600's: 600 is "never checked".
    for name, book in service._state['orderbooks'].items():
        book['fetched_at'] -= 24 * 3600
    for name in [n for n in service._state['orderbooks'] if n.startswith('600-')]:
        del service._state['orderbooks'][name]
    web.calls.clear()
    service._refresh_orderbooks('sale', instant_settings().get_settings())
    order = [json.loads(c[1]['qp'])[1] for c in web.calls_to(deals_module.MARKET_ORDERBOOK_URL)]
    first_stale = next(i for i, name in enumerate(order) if not name.startswith('600-'))
    assert order[:first_stale] and all(name.startswith('600-') for name in order[:first_stale])
    assert all(not name.startswith('600-') for name in order[first_stale:])


def test_an_empty_order_book_answer_is_retried_once(tmp_path):
    web = FakeSteamWeb()
    service, _ = make_service(tmp_path, web, settings=instant_settings())
    run_scan(service, include_full_price=False)
    flaky = {'100-Card 2': 0}
    original = web.__call__

    def answer(url, params, cookies, host):
        if url == deals_module.MARKET_ORDERBOOK_URL:
            name = json.loads(params['qp'])[1]
            if name in flaky and flaky[name] == 0:
                flaky[name] += 1
                web.calls.append((url, dict(params), False, host))
                return json.dumps({'data': {'success': False}})
        return original(url, params, cookies, host)
    service._fetch = answer
    service._state['orderbooks'].clear()
    service._refresh_orderbooks('sale', instant_settings().get_settings())
    asked = [json.loads(c[1]['qp'])[1] for c in web.calls if c[0] == deals_module.MARKET_ORDERBOOK_URL]
    assert asked.count('100-Card 2') >= 2 and asked[-1] == '100-Card 2'   # retried at the end
    assert '100-Card 2' in service._state['orderbooks']


def test_sale_end_date_reaches_the_row_and_the_alert(tmp_path):
    service, sent = make_service(tmp_path)
    run_scan(service)
    rows = by_app(service.deals(FakeSettings().get_settings(), 'all'))
    assert rows['100']['discount_ends_at'] == SALE_ENDS_AT
    assert rows['400']['discount_ends_at'] is None           # full price: no sale to end
    assert 'sale ends in' in sent[0]['text']


def test_game_that_lost_its_trading_cards_is_excluded(tmp_path):
    NO_LONGER_HAS_CARDS.add('100')
    try:
        service, _ = make_service(tmp_path)
        run_scan(service)
    finally:
        NO_LONGER_HAS_CARDS.clear()
    assert '100' not in by_app(service.deals(FakeSettings().get_settings(), 'sale', include_unprofitable=True))


# ---- "both" valuation (the default): buy orders AND sell price side by side -------------

def both_settings(**overrides):
    return FakeSettings(card_deals_valuation='both', **overrides)


def with_worthless_bids(app_id):
    """Give every card of `app_id` a 3-cent top bid (a list-only deal)."""
    for index in range(len(MARKET[app_id])):
        BIDS[f'{app_id}-Card {index}'] = [(3, 5)]


def test_default_valuation_is_both():
    from settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS['card_deals_valuation'] == 'both'
    assert clean_config({'card_deals_valuation': 'Both'}) == {'card_deals_valuation': 'both'}


def test_both_carries_sell_now_and_list_metrics(tmp_path):
    service, _ = make_service(tmp_path, settings=both_settings())
    run_scan(service)
    row = by_app(service.deals(both_settings().get_settings(), 'sale', include_unprofitable=True))['100']
    top_nets = [seller_receives_cents(BIDS[f'100-Card {i}'][0][0]) for i in range(6)]
    assert row['sell_now']['expected_net'] == round(3 * sum(top_nets) / 6 / 100, 2)
    assert row['list']['expected_net'] == round(expected_cents('100') / 100, 2)
    # Sell price leads the row; buy orders ride along.
    assert row['expected_net'] == row['list']['expected_net'] and row['value_source'] == 'listings'
    alpha = next(b for b in row['buyers'] if b['account_name'] == 'alpha')
    assert alpha['profit_list'] == round((expected_cents('100') - 44) / 100, 2)
    assert alpha['profit_sell_now'] == round((3 * sum(top_nets) / 6 - 44) / 100, 2)
    assert row['deal_kind'] == ('sell_now' if row['sell_now']['is_profitable'] else 'list')


def test_both_counts_a_deal_either_way_and_ranks_sell_now_first(tmp_path):
    saved = dict(BIDS)
    with_worthless_bids('600')                           # 600: profitable only when listed
    try:
        service, sent = make_service(tmp_path, settings=both_settings(card_deals_alert_min_return_percent=0,
                                                                      card_deals_alert_min_profit=0))
        run_scan(service)
    finally:
        BIDS.clear()
        BIDS.update(saved)
    rows = service.deals(both_settings().get_settings(), 'sale')['deals']
    by_id = by_app({'deals': rows})
    assert by_id['600']['is_deal'] and by_id['600']['deal_kind'] == 'list'
    assert by_id['600']['sell_now']['is_profitable'] is False
    assert by_id['100']['deal_kind'] == 'sell_now'
    kinds = [r['deal_kind'] for r in rows]
    assert kinds == sorted(kinds, key=lambda kind: kind != 'sell_now')    # sell-now deals first
    assert not any(r['awaiting_buy_orders'] for r in rows)
    sale_alert = sent[0]['text']
    assert '• Sale Also In Full:' in sale_alert and 'list only' in sale_alert
    assert '• Sale Winner:' in sale_alert and 'sell now' in sale_alert


def test_both_fetches_order_books(tmp_path):
    web = FakeSteamWeb()
    service, _ = make_service(tmp_path, web, settings=both_settings())
    run_scan(service, include_full_price=False)
    assert web.calls_to(deals_module.MARKET_ORDERBOOK_URL)


# ---- HTTP: rate-limit ladder, circuit breaker, transient retries -----------------------

def _service_with_real_http(tmp_path):
    service, _ = make_service(tmp_path)
    service._fetch = service._http_get
    sleeps = []
    service._sleep = sleeps.append
    return service, sleeps


def test_http_get_backs_off_then_cools_down(tmp_path, monkeypatch):
    service, sleeps = _service_with_real_http(tmp_path)
    attempts = []

    def always_429(request, timeout):
        attempts.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 429, 'Too Many Requests', {}, None)
    monkeypatch.setattr(deals_module.urllib.request, 'urlopen', always_429)
    with pytest.raises(RateLimited):
        service._http_get(deals_module.MARKET_SEARCH_URL, {'a': 1}, None, 'community')
    assert len(attempts) == 3                          # first try + two backoffs
    assert [s for s in sleeps if s >= 60] == [60, 180]
    # Circuit breaker: the host is now skipped without touching the network.
    with pytest.raises(RateLimited):
        service._http_get(deals_module.MARKET_SEARCH_URL, {'a': 1}, None, 'community')
    assert len(attempts) == 3
    # ...but other hosts are unaffected.
    monkeypatch.setattr(deals_module.urllib.request, 'urlopen', lambda r, timeout: _Response('ok'))
    assert service._http_get(deals_module.STORE_SEARCH_URL, None, None, 'store') == 'ok'


@pytest.mark.parametrize('failure', [
    lambda url: urllib.error.URLError('timed out'),
    lambda url: TimeoutError('read timed out'),
    lambda url: ConnectionResetError('reset by peer'),
    lambda url: urllib.error.HTTPError(url, 502, 'Bad Gateway', {}, None),
])
def test_http_get_retries_transient_failures(tmp_path, monkeypatch, failure):
    service, sleeps = _service_with_real_http(tmp_path)
    attempts = []

    def flaky(request, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise failure(request.full_url)
        return _Response('recovered')
    monkeypatch.setattr(deals_module.urllib.request, 'urlopen', flaky)
    assert service._http_get(deals_module.STORE_SEARCH_URL, None, None, 'store') == 'recovered'
    assert len(attempts) == 3 and [s for s in sleeps if s >= 5] == [5, 20]


def test_http_get_gives_up_after_transient_retries_and_never_retries_404(tmp_path, monkeypatch):
    service, _ = _service_with_real_http(tmp_path)
    attempts = []

    def down(request, timeout):
        attempts.append(1)
        raise urllib.error.URLError('timed out')
    monkeypatch.setattr(deals_module.urllib.request, 'urlopen', down)
    with pytest.raises(urllib.error.URLError):
        service._http_get(deals_module.STORE_SEARCH_URL, None, None, 'store')
    assert len(attempts) == 3

    attempts.clear()

    def missing(request, timeout):
        attempts.append(1)
        raise urllib.error.HTTPError(request.full_url, 404, 'Not Found', {}, None)
    monkeypatch.setattr(deals_module.urllib.request, 'urlopen', missing)
    with pytest.raises(urllib.error.HTTPError):
        service._http_get(deals_module.STORE_SEARCH_URL, None, None, 'store')
    assert len(attempts) == 1


def test_http_get_sends_only_session_cookies(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path)
    seen = {}

    def capture(request, timeout):
        seen['cookie'] = request.get_header('Cookie')
        return _Response('ok')
    monkeypatch.setattr(deals_module.urllib.request, 'urlopen', capture)
    service._http_get('https://steamcommunity.com/x', None,
                      {'steamLoginSecure': 'S', 'sessionid': 'I', 'mobileClient': 'android'}, 'community')
    assert seen['cookie'] == 'steamLoginSecure=S; sessionid=I'


def test_http_get_sends_orderbook_headers(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path)
    seen = {}

    def capture(request, timeout):
        seen.update({k.lower(): v for k, v in request.header_items()})
        return _Response('ok')
    monkeypatch.setattr(deals_module.urllib.request, 'urlopen', capture)
    service._http_get(deals_module.MARKET_ORDERBOOK_URL, {'q': 'Load', 'qp': '[753, "x"]'}, None, 'orderbook')
    assert seen['x-valve-request-type'] == 'queryAction' and seen['x-requested-with'] == 'XMLHttpRequest'
    assert 'cookie' not in seen


class _Response:
    def __init__(self, body):
        self.body = body.encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
