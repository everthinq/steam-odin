"""Andvari card auto-sell: only new marketable trading cards, one cent under the
lowest listing in the wallet currency (or higher, patiently, when the price
history shows buyers pay more), never undercutting our own listing, and the
statistics per account and game."""
import json
import time

import pytest

import card_seller_service
from card_seller_service import CardSellerService, inventory_cards, sales_statistics, MAX_SELL_TRIES


def card(assetid, name, app='745740', marketable=1, foil=False, item_class='item_class_2', index=None):
    return assetid, {'market_hash_name': f'{app}-{name}', 'name': name, 'marketable': marketable,
                     'market_fee_app': app,
                     'tags': [{'category': 'item_class', 'internal_name': item_class},
                              {'category': 'cardborder', 'internal_name': 'cardborder_1' if foil else 'cardborder_0'},
                              {'category': 'Game', 'internal_name': f'app_{app}'}]}


def page(*cards, more=False, last=None):
    answer = {'assets': [], 'descriptions': []}
    for index, (assetid, description) in enumerate(cards):
        answer['assets'].append({'assetid': assetid, 'classid': f'{assetid}c', 'instanceid': '0'})
        answer['descriptions'].append({'classid': f'{assetid}c', 'instanceid': '0', **description})
    if more:
        answer.update(more_items=1, last_assetid=last)
    return answer


def test_inventory_cards_filters_class_marketable_foil_and_apps():
    pages = [page(card('1', 'A'), card('2', 'B', marketable=0), card('3', 'C', foil=True),
                  card('4', 'Gems', item_class='item_class_7'), card('5', 'D', app='400740'))]
    assert [c['assetid'] for c in inventory_cards(pages)] == ['1', '3', '5']
    assert [c['assetid'] for c in inventory_cards(pages, foil=False)] == ['1', '5']
    assert [c['assetid'] for c in inventory_cards(pages, apps=[400740])] == ['5']
    assert inventory_cards(pages)[1]['foil'] is True


def test_sales_statistics_per_account_and_game():
    sales = [{'ok': True, 'account_name': 'alpha', 'game': 'app_1', 'currency': 1, 'receives': 10},
             {'ok': True, 'account_name': 'alpha', 'game': 'app_2', 'currency': 1, 'receives': 5},
             {'ok': True, 'account_name': 'bravo', 'game': 'app_1', 'currency': 9, 'receives': 40},
             {'ok': False, 'account_name': 'bravo', 'game': 'app_1', 'currency': 9, 'receives': 40}]
    stats = sales_statistics(sales)
    assert stats['accounts']['alpha'] == {'listed': 2, 'receives': {'1': 15}}
    assert stats['games']['app_1'] == {'listed': 2, 'receives': {'1': 10, '9': 40}}
    assert stats['listed'] == 3 and stats['failed'] == 1


class FakeSettings:
    def __init__(self, **values):
        self.values = {'card_auto_sell_enabled': True, **values}

    def get_settings(self):
        return dict(self.values)


class FakeResponse:
    def __init__(self, payload=None, status=200, text=''):
        self.payload, self.status_code, self.text, self.ok = payload, status, text, status < 400

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self):
        self.pages = {}
        # {card: (lowest listing, highest buy order)} in US cents; the order book answers in USD
        self.books = {'745740-A': (20, 5), '745740-B': (30, 10), '745740-C': (100, 50), '400740-D': (10, 2)}
        self.book_currency = 1
        self.calls = []
        self.sell_answer = {'success': True}
        self.sell_orders = {}           # {card: [(price, listings)]}; empty: only the lowest listing
        self.histories = {}             # {card: pricehistory answer}; missing: unreadable

    def get(self, url, params=None, cookies=None, timeout=None, headers=None):
        self.calls.append(('GET', url, dict(params or {})))
        if '/inventory/' in url:
            pages = self.pages.get(url.split('/')[4], [page()])
            start = (params or {}).get('start_assetid')
            return FakeResponse(pages[1] if start and len(pages) > 1 else pages[0])
        if 'orderbook' in url:
            appid, name = json.loads(params['qp'])
            book = self.books.get(name)
            if not book:
                return FakeResponse({'data': {'success': False}})
            flat = [number for level in self.sell_orders.get(name, []) for number in level]
            return FakeResponse({'data': {'success': True, 'data': {
                'eCurrency': self.book_currency, 'amtMinSellOrder': book[0], 'amtMaxBuyOrder': book[1],
                'rgCompactSellOrders': flat}}})
        if 'pricehistory' in url:
            return FakeResponse(self.histories.get(params['market_hash_name'], {'success': False}))
        if url.endswith('/market/'):
            return FakeResponse(text='g_rgWalletInfo = {"wallet_currency":1,"wallet_fee_minimum":"1"};')
        raise AssertionError(url)

    def post(self, url, data=None, cookies=None, timeout=None, headers=None):
        self.calls.append(('POST', url, data))
        return FakeResponse(dict(self.sell_answer))

    def sells(self):
        return [(data['assetid'], data['price'], data['appid'], data['contextid'])
                for method, url, data in self.calls if method == 'POST']


class FakeStorage:
    def list_accounts(self):
        return ['1', '2']

    def load_account(self, steamid):
        return {'account_name': {'1': 'alpha', '2': 'bravo'}[steamid]}


class FakeSteam:
    def __init__(self):
        self.storage = FakeStorage()
        self.confirmations = []
        self.accepted = []

    def web_session_cookie_for(self, steamid):
        return {'sessionid': 's' + steamid, 'steamLoginSecure': 'x'}

    def ensure_fresh_session(self, steamid):
        return {}

    def get_confirmations(self, steamid):
        return {'success': True, 'confirmations': list(self.confirmations)}

    def act_on_confirmations_batch(self, steamid, items, operation):
        self.accepted.append((steamid, list(items)))
        return {'success': True}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(card_seller_service, 'send_notification', lambda settings, text: None)
    http, steam, settings = FakeHttp(), FakeSteam(), FakeSettings()
    service = CardSellerService(settings, steam, None, state_path=str(tmp_path / 'sales.json'), http=http,
                                sleep=lambda seconds: None)
    return service, http, steam, settings


def test_off_by_default_reads_nothing(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_enabled'] = False
    assert service.sell_account('1', 'alpha')['error'] == 'card auto-sell is off'
    assert service.step() is None and http.calls == []


def test_cards_held_when_switched_on_are_left_alone_new_ones_sold(world):
    service, http, steam, _ = world
    http.pages['1'] = [page(card('100', 'A'))]
    assert service.sell_account('1', 'alpha')['listed'] == 0          # held: the starting point
    http.pages['1'] = [page(card('100', 'A'), card('101', 'B'), card('102', 'C', foil=True))]
    steam.confirmations = [{'id': 'x', 'nonce': 'n', 'type': 3, 'headline': 'Selling for $0.29 USD', 'summary': ['B']},
                           {'id': 'y', 'nonce': 'm', 'type': 3, 'headline': 'Selling for $0.99 USD', 'summary': ['C']},
                           {'id': 'z', 'nonce': 'o', 'type': 2, 'headline': 'Trade', 'summary': ['B']}]
    summary = service.sell_account('1', 'alpha')
    assert summary['listed'] == 2 and summary['confirmed'] == 2
    assert http.sells() == [('101', 26, 753, 6), ('102', 87, 753, 6)]     # $0.29 and $0.99 for the buyer
    assert steam.accepted == [('1', [('x', 'n'), ('y', 'm')])]
    assert service.sell_account('1', 'alpha')['listed'] == 0          # never listed twice


def test_include_held_sells_everything(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.pages['1'] = [page(card('100', 'A'))]
    assert service.sell_account('1', 'alpha')['listed'] == 1


def test_inventory_pages_are_followed(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.pages['1'] = [page(card('100', 'A'), more=True, last='100'), page(card('101', 'B'))]
    assert service.sell_account('1', 'alpha')['listed'] == 2


def test_our_own_lowest_listing_is_matched_not_undercut(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.pages['1'] = [page(card('100', 'A'))]
    service.sell_account('1', 'alpha')                                 # lowest $0.20 -> listed at $0.19
    http.books['745740-A'] = (19, 5)                                   # now our own listing is the lowest
    service.seller._prices.clear()
    http.pages['2'] = [page(card('200', 'A'))]
    service.sell_account('2', 'bravo')
    assert [price for _, price, _, _ in http.sells()] == [17, 17]     # both at $0.19 for the buyer


def test_no_price_is_not_a_try_and_refusals_stop_after_three(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.pages['1'] = [page(card('100', 'A'))]
    del http.books['745740-A']
    for _ in range(5):
        service.sell_account('1', 'alpha')
    assert http.sells() == []
    http.books['745740-A'] = (20, 5)
    http.sell_answer = {'success': False, 'message': 'no'}
    service.seller._prices.clear()
    for _ in range(MAX_SELL_TRIES + 2):
        service.sell_account('1', 'alpha')
    assert len(http.sells()) == MAX_SELL_TRIES


def test_rate_limit_pauses_the_loop(world):
    service, http, *_ = world
    real_get = http.get
    http.get = lambda url, params=None, cookies=None, timeout=None, headers=None: (
        FakeResponse({}, status=429) if '/inventory/' in url else real_get(url, params, cookies, timeout, headers))
    assert '429' in service.sell_account('1', 'alpha')['error']
    service._state['inventories'] = {}
    assert service.step() is None and service.status()['rate_limited_until']


def test_switching_off_and_on_starts_a_new_starting_point(world):
    service, http, _, settings = world
    http.pages['1'] = [page(card('100', 'A'))]
    service.sell_account('1', 'alpha')
    settings.values['card_auto_sell_enabled'] = False
    service.sell_account('1', 'alpha')
    settings.values['card_auto_sell_enabled'] = True
    http.pages['1'] = [page(card('100', 'A'), card('101', 'B'))]
    assert service.sell_account('1', 'alpha')['listed'] == 0          # 101 was held when switched on again


def test_statistics_in_status(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.pages['1'] = [page(card('100', 'A'), card('101', 'D', app='400740'))]
    service.sell_account('1', 'alpha')
    stats = service.status()['statistics']
    assert stats['listed'] == 2 and stats['accounts']['alpha']['listed'] == 2
    assert set(stats['games']) == {'app_745740', 'app_400740'}
    assert stats['apps']['745740'] == {'listed': 1, 'receives': {'1': 17}}


def test_cards_listed_before_a_stop_are_confirmed_on_a_later_pass(world):
    service, http, steam, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.pages['1'] = [page(card('100', 'A'), card('101', 'B'))]
    real_post = http.post
    calls = []

    def post(url, data=None, cookies=None, timeout=None, headers=None):
        calls.append(data['assetid'])
        if len(calls) == 2:
            return FakeResponse({}, status=429)              # the second listing is rate-limited
        return real_post(url, data, cookies, timeout, headers)
    http.post = post
    steam.get_confirmations = lambda steamid: {'success': False, 'message': 'token expired'}
    summary = service.sell_account('1', 'alpha')
    assert summary['listed'] == 1 and summary['confirmed'] == 0
    assert service.status()['unconfirmed'] == {'1': 1}
    steam.get_confirmations = lambda steamid: {'success': True, 'confirmations': [
        {'id': 'x', 'nonce': 'n', 'type': 3, 'headline': 'Selling for $0.19 USD', 'summary': ['A']}]}
    service._state['cooldown_until'] = 0
    assert service.sell_account('1', 'alpha')['confirmed'] == 1     # retried on the next pass
    assert service.status()['unconfirmed'] == {}


def test_rate_limit_pause_survives_a_reload(world):
    service, http, steam, settings = world
    http.get = lambda url, params=None, cookies=None, timeout=None, headers=None: FakeResponse({}, status=429)
    service.sell_account('1', 'alpha')
    reloaded = CardSellerService(settings, steam, None, state_path=service.state_path, http=http,
                                 sleep=lambda seconds: None)
    assert reloaded.step() is None and reloaded.status()['rate_limited_until']


def test_switching_on_reads_every_account_again_at_once(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_enabled'] = False
    service._state['inventories'] = {'1': {'at': 9e12}, '2': {'at': 9e12}}   # read "just now" earlier
    service._enabled_at(settings.get_settings())
    settings.values['card_auto_sell_enabled'] = True
    service._enabled_at(settings.get_settings())
    assert service._state['inventories'] == {}
    assert service.step()['steamid'] in ('1', '2')


def test_lowest_listing_equal_to_the_buy_order_sells_at_the_buy_order(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.books['745740-A'] = (20, 20)                                  # sell price now == buy order
    http.pages['1'] = [page(card('100', 'A'))]
    service.sell_account('1', 'alpha')
    sale = service.status()['sales'][0]
    assert sale['buyer_pays'] == 20 and 'buy order' in sale['how']   # not $0.19


def test_one_cent_under_would_cross_the_buy_order_so_the_buy_order_wins(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.books['745740-A'] = (21, 20)                                  # $0.20 == the buy order
    http.pages['1'] = [page(card('100', 'A'))]
    service.sell_account('1', 'alpha')
    assert service.status()['sales'][0]['buyer_pays'] == 20


def test_normal_spread_lists_one_cent_under_the_lowest_listing(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.pages['1'] = [page(card('100', 'A'))]                         # $0.20 listing, $0.05 buy order
    service.sell_account('1', 'alpha')
    sale = service.status()['sales'][0]
    assert sale['buyer_pays'] == 19 and sale['how'] == 'one under the lowest listing'


def test_no_buy_orders_still_lists_one_cent_under(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.books['745740-A'] = (20, 0)
    http.pages['1'] = [page(card('100', 'A'))]
    service.sell_account('1', 'alpha')
    assert service.status()['sales'][0]['buyer_pays'] == 19


def test_an_order_book_in_another_currency_is_not_used(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.book_currency = 9                                             # kroner, but the wallet is in dollars
    http.pages['1'] = [page(card('100', 'A'))]
    summary = service.sell_account('1', 'alpha')
    assert summary['listed'] == 0 and 'order book' in summary['error'] and http.sells() == []


def test_three_cent_cards_are_listed_at_the_minimum():
    from market_seller import target_price
    from team_fortress_service import US_DOLLAR_WALLET
    assert target_price(2, US_DOLLAR_WALLET) == (3, 1)                 # $0.03 lowest - 1 -> the $0.03 minimum


def test_the_buy_order_price_is_never_rounded_under_the_buy_order():
    from market_seller import target_price
    from team_fortress_service import US_DOLLAR_WALLET, buyer_pays_for
    kroner = {'fee_minimum': 10, 'fee_base': 0, 'fee_percent': 0.05, 'publisher_percent': 0.10}
    assert target_price(22, US_DOLLAR_WALLET, floor=22) == (23, 20)    # 22 cannot be made: 23, never 21
    for wallet in (US_DOLLAR_WALLET, kroner):
        smallest = buyer_pays_for(1, wallet)                           # $0.03, or 0.21 kr (10 øre fees)
        for bid in range(3, 400):
            price = target_price(bid, wallet, floor=bid)
            if bid < smallest:
                assert price is None                                    # cannot be listed at all
                continue
            buyer, receives = price
            assert buyer >= bid and buyer_pays_for(receives, wallet) == buyer
            assert target_price(bid, wallet)[0] <= bid                  # no buy order to respect: never above


def test_a_three_cent_card_from_the_service_is_listed(world):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.books['745740-A'] = (3, 0)
    http.pages['1'] = [page(card('100', 'A'))]
    assert service.sell_account('1', 'alpha')['listed'] == 1
    assert service.status()['sales'][0]['buyer_pays'] == 3


def test_card_sale_messages_go_only_to_andvaris_own_bot(world, monkeypatch):
    service, _, _, settings = world
    sent = []
    monkeypatch.setattr(card_seller_service, 'send_notification', lambda chosen, text: sent.append(chosen))
    settings.values.update(telegram_bot_token='arbitrage-bot', telegram_chat_id='111', notify_webhook_url='https://hook',
                           card_deals_bot_token='', card_deals_chat_id='')
    service._notify('listed 3 cards')
    assert sent == []                                   # no bot of its own: silent, never the arbitrage bot
    settings.values.update(card_deals_bot_token='andvari-bot', card_deals_chat_id='222')
    service._notify('listed 3 cards')
    assert [(s['telegram_bot_token'], s['telegram_chat_id'], s['notify_webhook_url']) for s in sent] == [
        ('andvari-bot', '222', '')]


# ---- patient pricing: the price history decides how high a card can go and still sell ----

def history(*rows):
    """A pricehistory answer from (hours ago, buyer price in dollars, units sold) rows."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    return {'success': True, 'price_prefix': '$', 'prices': [
        [(now - timedelta(hours=hours)).strftime('%b %d %Y %H: +0'), price, str(units)] for hours, price, units in rows]}


def patient_world(world, sell_orders, *rows):
    service, http, _, settings = world
    settings.values['card_auto_sell_include_held'] = True
    http.pages['1'] = [page(card('100', 'A'))]                         # $0.20 lowest listing, $0.05 buy order
    http.sell_orders['745740-A'] = sell_orders
    http.histories['745740-A'] = history(*rows)
    return service, http


def history_calls(http):
    return [params for method, url, params in http.calls if 'pricehistory' in url]


def test_price_history_rows_become_minor_units_and_bad_rows_are_skipped():
    from market_seller import parse_price_history
    points = parse_price_history({'success': True, 'prices': [
        ['Oct 08 2026 14: +0', 0.223, '3'], ['garbage', 1, '1'], ['Oct 08 2026 15: +0', 1.5, '1,204']]})
    assert [(price, units) for _, price, units in points] == [(22, 3), (150, 1204)]
    assert parse_price_history({'success': False}) == [] and parse_price_history(None) == []


def test_the_ceiling_is_the_price_ninety_percent_of_units_sold_at_or_under():
    from market_seller import history_summary, parse_price_history, MINIMUM_HISTORY_SALES
    points = parse_price_history(history((1, 0.20, 100), (2, 0.24, 50), (24 * 30, 0.90, 1000)))
    assert history_summary(points, time.time()) == {'ceiling': 24, 'per_day': 21.43, 'sold': 150}  # month-old sale ignored
    thin = parse_price_history(history((1, 0.20, MINIMUM_HISTORY_SALES - 1)))
    assert history_summary(thin, time.time()) is None


def test_patient_target_goes_one_under_a_wall_within_the_queue_and_under_the_ceiling():
    from market_seller import patient_target
    ladder = [(20, 3), (21, 4), (23, 6), (25, 10)]
    assert patient_target(ladder, 24, budget=64) == (24, 13)            # the ceiling itself: 13 listings ahead
    assert patient_target(ladder, 30, budget=64) == (30, 23)
    assert patient_target(ladder, 30, budget=10) == (22, 7)             # one under the $0.23 wall, 7 ahead
    assert patient_target(ladder, 30, budget=5) == (20, 3)              # one under the $0.21 wall, 3 ahead
    assert patient_target([(3, 100)], 10, budget=5) is None             # every price has too many ahead
    assert patient_target(ladder, 24, budget=0, own=19) == (19, 0)


def test_patient_price_is_used_when_buyers_paid_more(world):
    from market_seller import target_price
    from team_fortress_service import US_DOLLAR_WALLET
    service, http = patient_world(world, [(20, 3), (21, 4), (23, 6), (25, 10)], (1, 0.20, 100), (2, 0.24, 50))
    assert service.sell_account('1', 'alpha')['listed'] == 1
    sale = service.status()['sales'][0]
    assert sale['buyer_pays'] == target_price(24, US_DOLLAR_WALLET)[0] and sale['buyer_pays'] > 19
    assert sale['how'].startswith('patient: 13 listings ahead') and 'buy order' not in sale['how']


def test_patient_price_never_waits_behind_more_than_the_queue_allows(world):
    service, http = patient_world(world, [(20, 3), (21, 200), (25, 5)], (1, 0.20, 100), (2, 0.24, 50))
    service.sell_account('1', 'alpha')
    assert service.status()['sales'][0]['buyer_pays'] == 20           # under the 200-listing wall at $0.21


def test_patient_price_never_goes_under_the_quick_price(world):
    service, http = patient_world(world, [(20, 3)], (1, 0.10, 500))     # history says $0.10, lowest is $0.20
    service.sell_account('1', 'alpha')
    sale = service.status()['sales'][0]
    assert sale['buyer_pays'] == 19 and sale['how'] == 'one under the lowest listing'


def test_thin_or_unreadable_history_falls_back_to_the_quick_price(world):
    service, http = patient_world(world, [(20, 3), (30, 1)], (1, 0.29, 5))
    service.sell_account('1', 'alpha')
    assert service.status()['sales'][0]['buyer_pays'] == 19
    del http.histories['745740-A']
    service.seller.histories.clear()
    http.pages['2'] = [page(card('200', 'A'))]
    service.sell_account('2', 'bravo')
    assert service.status()['sales'][0]['buyer_pays'] == 19


def test_price_history_is_read_once_per_card_and_a_failed_read_retried_later(world):
    from market_seller import HISTORY_RETRY_SECONDS
    service, http = patient_world(world, [(20, 3)], (1, 0.24, 100))
    http.pages['2'] = [page(card('200', 'A'))]
    service.sell_account('1', 'alpha')
    service.seller._prices.clear()
    service.sell_account('2', 'bravo')
    assert len(history_calls(http)) == 1                              # kept for the second account
    key = '753|745740-A|1'
    service.seller.histories[key] = {'at': time.time() - HISTORY_RETRY_SECONDS + 60, 'summary': None}  # a failed read
    assert service.seller.sale_history(753, '745740-A', 1, {}) is None and len(history_calls(http)) == 1
    service.seller.histories[key]['at'] -= 120
    assert service.seller.sale_history(753, '745740-A', 1, {})['ceiling'] == 24
    assert len(history_calls(http)) == 2


def test_price_histories_survive_a_reload_and_old_ones_are_dropped(world):
    from market_seller import HISTORY_TIME_TO_LIVE_SECONDS
    service, http = patient_world(world, [(20, 3)], (1, 0.24, 100))
    service.sell_account('1', 'alpha')
    service.seller.histories['753|old|1'] = {'at': 1, 'summary': None}
    service._save()
    reloaded = CardSellerService(world[3], world[2], None, state_path=service.state_path, http=http,
                                 sleep=lambda seconds: None)
    assert set(reloaded.seller.histories) == {'753|745740-A|1'}
    assert reloaded.seller.histories['753|745740-A|1']['summary']['ceiling'] == 24
    assert HISTORY_TIME_TO_LIVE_SECONDS >= 3600


def test_a_ceiling_far_above_the_lowest_listing_is_not_trusted(world):
    service, http = patient_world(world, [(20, 3)], (1, 0.41, 100))    # over twice the $0.20 lowest listing
    service.sell_account('1', 'alpha')
    assert service.status()['sales'][0]['buyer_pays'] == 19
