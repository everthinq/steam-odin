"""Andvari "Buy games": the plan (owned, currency, maximum price, balance, best
profit first) and the guarded purchase (empty cart only, exact cart and final
price, cancel + empty the cart on any mismatch, dry run pays nothing)."""
import json

import pytest

import store_purchase_service
from store_purchase_service import (StorePurchaseService, STATUS_BALANCE, STATUS_BUY, STATUS_CURRENCY,
                                    STATUS_OWNED, STATUS_TOO_EXPENSIVE, choose_package, parse_app_ids,
                                    plan_account, purchase_statistics, to_usd)

REFLEX, VERGE = 745740, 400740
RATES = {'HKD': 7.8, 'NOK': 10.2}


# ---- pure helpers ---------------------------------------------------------------------------

def test_parse_app_ids_from_links_and_numbers():
    text = ('https://store.steampowered.com/app/745740/Reflex/ '
            'https://store.steampowered.com/app/400740/VERGELost_chapter/, 745740')
    assert parse_app_ids(text) == [REFLEX, VERGE]
    assert parse_app_ids('nothing here') == []


def test_choose_package_takes_the_cheapest_default_package_not_the_commercial_license():
    details = {'name': 'Reflex', 'price_overview': {'currency': 'USD', 'final': 45},
               'package_groups': [{'name': 'default', 'subs': [
                   {'packageid': 218803, 'price_in_cents_with_discount': 45},
                   {'packageid': 272736, 'price_in_cents_with_discount': 90}]}]}
    assert choose_package(details) == (218803, 45, 'USD')
    assert choose_package({'is_free': True}) is None
    assert choose_package(None) is None


def test_to_usd():
    assert to_usd(45, 'USD', RATES) == 0.45
    assert to_usd(350, 'HKD', RATES) == pytest.approx(0.4487, abs=1e-4)
    assert to_usd(550, 'NOK', {}) is None


def game(app_id, price, currency='USD', packageid=None):
    return {'app_id': app_id, 'name': str(app_id), 'packageid': packageid or app_id + 1, 'price': price,
            'currency': currency}


def test_plan_both_games_when_the_balance_covers_them():
    rows, total = plan_account({'balance': 567, 'currency_code': 'USD', 'owned': set()},
                               [game(REFLEX, 45), game(VERGE, 45)], 0.45, RATES)
    assert [row['status'] for row in rows] == [STATUS_BUY, STATUS_BUY] and total == 90


def test_plan_small_balance_buys_the_more_profitable_game():
    rows, total = plan_account({'balance': 79, 'currency_code': 'USD', 'owned': set(),
                                'profits': {REFLEX: 0.09, VERGE: 0.06}},
                               [game(VERGE, 45), game(REFLEX, 45)], 0.45, RATES)
    assert [(row['app_id'], row['status']) for row in rows] == [(REFLEX, STATUS_BUY), (VERGE, STATUS_BALANCE)]
    assert total == 45


def test_plan_owned_currency_and_price_rules():
    rows, _ = plan_account({'balance': 99999, 'currency_code': 'NOK', 'owned': {VERGE}},
                           [game(REFLEX, 550, 'NOK'), game(VERGE, 600, 'NOK'), game(1, 45, 'USD')], 0.45, RATES)
    status = {row['app_id']: row['status'] for row in rows}
    assert status == {REFLEX: STATUS_TOO_EXPENSIVE, VERGE: STATUS_OWNED, 1: STATUS_CURRENCY}
    rows, _ = plan_account({'balance': 1000, 'currency_code': 'HKD', 'owned': set()},
                           [game(REFLEX, 350, 'HKD'), game(VERGE, 300, 'HKD')], 0.45, RATES)
    assert [row['status'] for row in rows] == [STATUS_BUY, STATUS_BUY]          # under $0.45 converted
    rows, _ = plan_account({'balance': 0, 'currency_code': 'HKD', 'owned': set()},
                           [game(REFLEX, 350, 'HKD')], 0.45, RATES)
    assert rows[0]['status'] == STATUS_BALANCE


def test_purchase_statistics_count_only_real_purchases():
    history = [
        {'ok': True, 'dry_run': False, 'account_name': 'alpha', 'currency': 'USD', 'charged': 90,
         'games': [{'name': 'Reflex', 'price': 45}, {'name': 'VERGE', 'price': 45}]},
        {'ok': True, 'dry_run': True, 'account_name': 'bravo', 'currency': 'USD', 'charged': 45,
         'games': [{'name': 'Reflex', 'price': 45}]},
        {'ok': False, 'dry_run': False, 'account_name': 'bravo', 'currency': 'USD', 'charged': None,
         'games': [{'name': 'Reflex', 'price': 45}]},
    ]
    stats = purchase_statistics(history)
    assert stats['purchases'] == 1 and stats['copies'] == 2
    assert stats['accounts'] == {'alpha': {'games': ['Reflex', 'VERGE'], 'spent': {'USD': 90}}}
    assert stats['games']['Reflex'] == {'copies': 1, 'accounts': ['alpha'], 'spent': {'USD': 45}}


# ---- the service with a fake Steam ---------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload=None, status=200, text=''):
        self.payload, self.status_code, self.text, self.ok = payload, status, text, status < 400

    def json(self):
        return self.payload


class FakeSteamWeb:
    """Store, cart and checkout as one stateful fake."""

    def __init__(self):
        self.balance = {'1': 567, '2': 79}
        self.currency = {'1': 1, '2': 1}
        self.owned = {'1': {10}, '2': {10}}
        self.cart = {'1': [], '2': []}
        self.prices = {REFLEX: (218803, 45), VERGE: (77778, 45)}
        self.final_total_bonus = 0           # Steam charging more than the cart says
        self.finalize_answer = {'success': 22}
        self.status_answers = [22, 1]
        self.transactions = {}
        self.calls = []

    def account(self, params, cookies):
        if params and 'access_token' in params:
            return params['access_token'].replace('token', '')
        return cookies['sessionid'].replace('session', '')

    def get(self, url, params=None, cookies=None, timeout=None, headers=None):
        self.calls.append(('GET', url.split('.com')[-1], dict(params or {})))
        if 'appdetails' in url:
            app = int(params['appids'])
            packageid, price = self.prices[app]
            return FakeResponse({str(app): {'success': True, 'data': {
                'name': {REFLEX: 'Reflex', VERGE: 'VERGE:Lost chapter'}[app],
                'price_overview': {'currency': 'USD', 'final': price, 'discount_percent': 50},
                'package_groups': [{'name': 'default', 'subs': [
                    {'packageid': packageid, 'price_in_cents_with_discount': price}]}]}}})
        if url.endswith('/market/'):
            who = self.account(None, cookies)
            return FakeResponse(text=f'g_rgWalletInfo = {{"wallet_currency":{self.currency[who]},'
                                     f'"wallet_balance":"{self.balance[who]}"}};')
        if 'userdata' in url:
            return FakeResponse({'rgOwnedApps': sorted(self.owned[self.account(None, cookies)]),
                                 'rgOwnedPackages': [1]})
        if 'GetCart' in url:
            who = self.account(params, cookies)
            items = self.cart[who]
            price_of = {package: price for package, price in self.prices.values()}
            return FakeResponse({'response': {'cart': {
                'subtotal': {'amount_in_cents': str(sum(price_of.get(p, 999) for p in items))},
                'line_items': [{'packageid': p} for p in items]}}})
        if url.endswith('/checkout/'):
            return FakeResponse(text='<html>checkout</html>')
        if 'getfinalprice' in url:
            transaction = self.transactions[params['transid']]
            return FakeResponse({'success': 1, 'total': transaction['total'] + self.final_total_bonus})
        if 'transactionstatus' in url:
            return FakeResponse({'success': self.status_answers.pop(0) if self.status_answers else 1})
        raise AssertionError(url)

    def post(self, url, params=None, data=None, cookies=None, timeout=None, headers=None):
        self.calls.append(('POST', url.split('.com')[-1], dict(data or {})))
        if 'AddItemsToCart' in url:
            who = self.account(params, cookies)
            self.cart[who] += [item['packageid'] for item in json.loads(data['input_json'])['items']]
            return FakeResponse({'response': {}})
        if 'DeleteCart' in url:
            self.cart[self.account(params, cookies)] = []
            return FakeResponse({'response': {}})
        if 'inittransaction' in url:
            who = self.account(None, cookies)
            price_of = {package: price for package, price in self.prices.values()}
            transid = f'tx{len(self.transactions) + 1}'
            self.transactions[transid] = {'who': who, 'items': list(self.cart[who]),
                                          'total': sum(price_of[p] for p in self.cart[who]), 'state': 'open'}
            return FakeResponse({'success': 1, 'transid': transid})
        if 'canceltransaction' in url:
            self.transactions[data['transid']]['state'] = 'cancelled'
            return FakeResponse({'success': 1})
        if 'finalizetransaction' in url:
            transaction = self.transactions[data['transid']]
            transaction['state'] = 'paid'
            who = transaction['who']
            self.balance[who] -= transaction['total']
            app_of = {package: app for app, (package, _) in self.prices.items()}
            self.owned[who] |= {app_of[p] for p in transaction['items']}
            return FakeResponse(dict(self.finalize_answer))
        raise AssertionError(url)

    def posts(self, fragment):
        return [data for method, url, data in self.calls if method == 'POST' and fragment in url]


class FakeStorage:
    def list_accounts(self):
        return ['1', '2']

    def load_account(self, steamid):
        return {'account_name': {'1': 'alpha', '2': 'bravo'}[steamid]}


class FakeSteam:
    storage = FakeStorage()

    def web_session_cookie_for(self, steamid):
        return {'sessionid': f'session{steamid}', 'steamLoginSecure': f'{steamid}%7C%7Ctoken{steamid}'}

    def ensure_fresh_session(self, steamid):
        return {}


class FakeCardDeals:
    def account_countries(self):
        return {'1': 'MD', '2': 'MD'}

    def exchange_rates(self):
        return dict(RATES)

    def deals(self, settings, scope='all', include_unprofitable=False):
        return {'deals': [{'app_id': str(REFLEX), 'buyers': [{'account_name': 'bravo', 'profit': 0.09}]},
                          {'app_id': str(VERGE), 'buyers': [{'account_name': 'bravo', 'profit': 0.06}]}]}


class FakeAsf:
    enabled = True

    def __init__(self):
        self.farm_now_calls = []

    def farm_now(self, steamid):
        self.farm_now_calls.append(steamid)


@pytest.fixture
def world(tmp_path, monkeypatch):
    web, asf = FakeSteamWeb(), FakeAsf()
    service = StorePurchaseService(FakeSteam(), FakeCardDeals(), asf, settings_provider=lambda: {},
                                   state_path=str(tmp_path / 'purchases.json'), http=web, sleep=lambda seconds: None)
    # Jobs run in the calling thread here.
    monkeypatch.setattr(store_purchase_service.threading, 'Thread',
                        lambda target, daemon=None, name=None: type('T', (), {'start': lambda self: target()})())
    return service, web, asf


def plan(service):
    assert service.start_plan(f'https://store.steampowered.com/app/{REFLEX}/ {VERGE}')['started']
    return {row['account_name']: row for row in service.status()['plan']['accounts']}


def test_plan_reads_wallets_ownership_and_prices(world):
    service, web, _ = world
    rows = plan(service)
    assert [g['status'] for g in rows['alpha']['games']] == [STATUS_BUY, STATUS_BUY]
    assert rows['alpha']['planned_total'] == 90 and rows['alpha']['balance'] == 567
    assert [(g['app_id'], g['status']) for g in rows['bravo']['games']] == [(REFLEX, STATUS_BUY),
                                                                            (VERGE, STATUS_BALANCE)]
    totals = service.status()['plan']['totals']
    assert totals == {'copies': 3, 'accounts': 2, 'usd': 1.35, 'profit': 0.09}


def test_buy_pays_exactly_the_plan_and_farms_now(world):
    service, web, asf = world
    plan(service)
    assert service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX, VERGE]}])['started']
    entry = service.status()['history'][0]
    assert entry['ok'] and entry['charged'] == 90
    assert sorted(entry['owned_after']) == sorted([REFLEX, VERGE])
    assert web.balance['1'] == 477 and web.cart['1'] == []
    assert asf.farm_now_calls == ['1']
    assert service.status()['statistics']['copies'] == 2


def test_dry_run_pays_nothing_and_cleans_up(world):
    service, web, asf = world
    plan(service)
    service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}], dry_run=True)
    entry = service.status()['history'][0]
    assert entry['ok'] and entry['dry_run'] and entry['charged'] == 45
    assert web.balance['1'] == 567 and web.cart['1'] == [] and asf.farm_now_calls == []
    assert [t['state'] for t in web.transactions.values()] == ['cancelled']
    assert service.status()['statistics']['purchases'] == 0


def test_a_different_final_price_cancels(world):
    service, web, _ = world
    plan(service)
    web.final_total_bonus = 5
    service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])
    entry = service.status()['history'][0]
    assert not entry['ok'] and 'would charge 50, planned 45' in entry['error']
    assert web.balance['1'] == 567 and web.cart['1'] == []
    assert [t['state'] for t in web.transactions.values()] == ['cancelled']


def test_a_cart_with_your_own_items_is_never_touched(world):
    service, web, _ = world
    plan(service)
    web.cart['1'] = [555]
    service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])
    entry = service.status()['history'][0]
    assert not entry['ok'] and 'cart is not empty' in entry['error']
    assert web.cart['1'] == [555] and web.posts('AddItemsToCart') == [] and web.posts('DeleteCart') == []


def test_only_planned_games_can_be_bought_and_the_plan_expires(world, monkeypatch):
    service, web, _ = world
    assert service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])['error'] == 'check the accounts first'
    plan(service)
    assert 'only games planned' in service.start_purchase([{'steamid': '2', 'app_ids': [VERGE]}])['error']
    assert 'selected twice' in service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]},
                                                       {'steamid': '1', 'app_ids': [VERGE]}])['error']
    real_time = store_purchase_service.time.time
    monkeypatch.setattr(store_purchase_service.time, 'time', lambda: real_time() + 31 * 60)
    assert 'older than 30 minutes' in service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])['error']
    assert web.posts('inittransaction') == []


def test_a_failed_payment_is_not_cancelled_and_says_check_the_account(world):
    service, web, _ = world
    plan(service)
    web.finalize_answer = {'success': 2, 'purchaseresultdetail': 53}
    service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])
    entry = service.status()['history'][0]
    assert not entry['ok'] and 'check the account' in entry['error']
    assert web.posts('canceltransaction') == []          # paying started: never cancelled from here


def test_history_survives_a_reload(world):
    service, web, asf = world
    plan(service)
    service.start_purchase([{'steamid': '2', 'app_ids': [REFLEX]}])
    reloaded = StorePurchaseService(FakeSteam(), FakeCardDeals(), asf, state_path=service.state_path, http=web,
                                    sleep=lambda seconds: None)
    assert reloaded.status()['statistics']['accounts']['bravo']['games'] == ['Reflex']


# ---- review fixes ---------------------------------------------------------------------------------

def test_bought_games_cannot_be_bought_again_from_the_same_plan(world):
    service, web, _ = world
    plan(service)
    service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])
    row = {r['account_name']: r for r in service.status()['plan']['accounts']}['alpha']
    assert {g['app_id']: g['status'] for g in row['games']}[REFLEX] == 'bought'
    assert 'only games planned' in service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])['error']
    assert len(web.posts('finalizetransaction')) == 1


def test_owned_meanwhile_or_balance_gone_stops_before_the_cart(world):
    service, web, _ = world
    plan(service)
    web.owned['1'].add(REFLEX)                                # bought by hand since the plan
    service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])
    assert 'owns one of these games now' in service.status()['history'][0]['error']
    plan(service)
    web.balance['2'] = 10
    service.start_purchase([{'steamid': '2', 'app_ids': [REFLEX]}])
    assert 'the wallet holds 10' in service.status()['history'][0]['error']
    assert web.posts('AddItemsToCart') == []


def test_an_unclear_payment_is_never_cancelled_and_says_may_be_paid(world):
    service, web, _ = world
    plan(service)

    def broken_finalize(url, params=None, data=None, cookies=None, timeout=None, headers=None):
        if 'finalizetransaction' in url:
            raise OSError('read timed out')
        return FakeSteamWeb.post(web, url, params, data, cookies, timeout, headers)
    web.post = broken_finalize
    service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])
    entry = service.status()['history'][0]
    assert entry['payment_attempted'] and not entry['paid'] and 'MAY BE PAID' in entry['error']
    assert web.posts('canceltransaction') == []
    row = {r['account_name']: r for r in service.status()['plan']['accounts']}['alpha']
    assert {g['app_id']: g['status'] for g in row['games']}[REFLEX] == 'check'
    assert 'only games planned' in service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])['error']


def test_paid_with_lagging_ownership_counts_as_bought(world):
    service, web, _ = world
    plan(service)
    real_get = web.get
    calls = {'userdata': 0}

    def lagging(url, params=None, cookies=None, timeout=None, headers=None):
        if 'userdata' in url:
            calls['userdata'] += 1
            if calls['userdata'] > 1:                         # the read after paying: not listed yet
                return FakeResponse({'rgOwnedApps': [10], 'rgOwnedPackages': [1]})
        return real_get(url, params, cookies, timeout, headers)
    web.get = lagging
    service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])
    entry = service.status()['history'][0]
    assert entry['paid'] and entry['ok'] and 'does not list every game yet' in entry['error']
    assert service.status()['statistics']['copies'] == 1


def test_an_interrupted_purchase_is_flagged_after_a_reload(world):
    service, web, asf = world
    plan(service)
    with open(service.state_path) as handle:
        state = json.load(handle)
    state['history'].append({'at': 1, 'steamid': '1', 'account_name': 'alpha', 'games': [], 'state': 'in progress',
                             'payment_attempted': True, 'paid': False, 'dry_run': False})
    with open(service.state_path, 'w') as handle:
        json.dump(state, handle)
    reloaded = StorePurchaseService(FakeSteam(), FakeCardDeals(), asf, state_path=service.state_path, http=web,
                                    sleep=lambda seconds: None)
    assert 'MAY BE PAID' in reloaded.status()['history'][0]['error']


def test_older_saved_purchases_are_recognised_as_paid(world, tmp_path):
    service, web, asf = world
    plan(service)
    created = service.status()['plan']['created_at']
    with open(service.state_path) as handle:
        state = json.load(handle)
    state['history'] = [
        {'at': created + 1, 'steamid': '1', 'account_name': 'alpha', 'games': [{'app_id': REFLEX, 'name': 'Reflex', 'price': 45}],
         'currency': 'USD', 'expected': 45, 'charged': 45, 'dry_run': False, 'ok': False,
         'error': 'paid, but the store does not list every game yet'},
        {'at': created + 2, 'steamid': '2', 'account_name': 'bravo', 'games': [], 'charged': None, 'dry_run': False,
         'ok': False, 'error': 'the cart is not empty'}]
    with open(service.state_path, 'w') as handle:
        json.dump(state, handle)
    reloaded = StorePurchaseService(FakeSteam(), FakeCardDeals(), asf, state_path=service.state_path, http=web,
                                    sleep=lambda seconds: None)
    status = reloaded.status()
    assert status['statistics']['accounts'] == {'alpha': {'games': ['Reflex'], 'spent': {'USD': 45}}}
    row = {r['account_name']: r for r in status['plan']['accounts']}['alpha']
    assert {g['app_id']: g['status'] for g in row['games']}[REFLEX] == 'bought'
    assert 'only games planned' in reloaded.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}])['error']


def test_the_selection_must_match_the_plan_on_screen_and_be_well_formed(world):
    service, *_ = world
    plan(service)
    created = service.status()['plan']['created_at']
    assert 'plan changed' in service.start_purchase([{'steamid': '1', 'app_ids': [REFLEX]}],
                                                    plan_created_at=created - 5)['error']
    assert 'must be a list' in service.start_purchase({'steamid': '1'})['error']
    assert 'numbers' in service.start_purchase([{'steamid': '1', 'app_ids': ['x']}])['error']
