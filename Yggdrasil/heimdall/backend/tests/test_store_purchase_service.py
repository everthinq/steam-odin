"""Andvari "Buy games": the plan (owned, currency, maximum price, balance, best
profit first) and the guarded purchase (empty cart only, exact cart and final
price, cancel + empty the cart on any mismatch, dry run pays nothing)."""
import error_handlers  # noqa: E402
import json

import pytest

import store_purchase_service
from commands import CommandError
from store_purchase_service import (StorePurchaseService, STATUS_BALANCE, STATUS_BUY, STATUS_CURRENCY,
                                    STATUS_OWNED, STATUS_TOO_EXPENSIVE, choose_package, default_selection,
                                    parse_app_ids, plan_account, purchase_statistics, to_usd)

REFLEX, VERGE = 745740, 400740
RATES = {'HKD': 7.8, 'NOK': 10.2}


# ---- pure helpers ---------------------------------------------------------------------------

def test_parse_app_ids_from_links_and_numbers():
    text = ('https://store.steampowered.com/app/745740/Reflex/ '
            'https://store.steampowered.com/app/400740/VERGELost_chapter/, 745740')
    assert parse_app_ids(text) == [REFLEX, VERGE]
    assert parse_app_ids('nothing here') == []


def test_parse_app_ids_takes_only_the_id_after_app_in_a_link():
    # The audit's example: "2077" in the name and "13" in ?snr= are not app ids.
    link = 'https://store.steampowered.com/app/1091500/Cyberpunk_2077/?snr=1_7_15__13'
    assert parse_app_ids(link) == [1091500]
    assert parse_app_ids(f'{link}\n745740, 400740;1091500') == [1091500, REFLEX, VERGE]
    assert parse_app_ids('(745740) "400740".') == [REFLEX, VERGE]
    assert parse_app_ids('Cyberpunk2077 abc123 7 0000000000') == []     # not a number token, too short, too long
    assert parse_app_ids('store.steampowered.com/app/12345678901/') == []
    assert parse_app_ids(None) == []


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
    assert stats['games']['Reflex'] == {'app_id': None, 'copies': 1, 'accounts': ['alpha'], 'spent': {'USD': 45}}


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


def make_purchase_plan(service, selection, empty_cart=True, plan_created_at=None):
    """What the page sends first: the plan on screen and the selection."""
    current = service.status()['plan']
    if plan_created_at is None and current:
        plan_created_at = current['created_at']
    return service.create_purchase_plan(plan_created_at, selection, empty_cart)


def dry_run(service, selection, **options):
    """A purchase plan and its dry run; answers the purchase plan afterwards."""
    made = make_purchase_plan(service, selection, **options)
    service.execute_purchase_plan(made['purchase_plan_id'], dry_run=True)
    return service.get_purchase_plan(made['purchase_plan_id'])


def buy(service, selection, **options):
    """The whole path: a purchase plan, its dry run, then the real purchase (the job)."""
    made = dry_run(service, selection, **options)
    return service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)


def refused(code, run, *args, **kwargs):
    with pytest.raises(CommandError) as caught:
        run(*args, **kwargs)
    assert caught.value.code == code, caught.value.message
    return caught.value.message


def test_plan_reads_wallets_ownership_and_prices(world):
    service, web, _ = world
    rows = plan(service)
    assert [g['status'] for g in rows['alpha']['games']] == [STATUS_BUY, STATUS_BUY]
    assert rows['alpha']['planned_total'] == 90 and rows['alpha']['balance'] == 567
    assert [(g['app_id'], g['status']) for g in rows['bravo']['games']] == [(REFLEX, STATUS_BUY),
                                                                            (VERGE, STATUS_BALANCE)]
    totals = service.status()['plan']['totals']
    assert totals == {'copies': 3, 'accounts': 2, 'usd': 1.35, 'profit': 0.09}


def test_the_plan_answers_freshness_and_the_default_selection(world, monkeypatch):
    service, *_ = world
    plan(service)
    current = service.status()['plan']
    assert current['fresh'] and current['expires_at'] == current['created_at'] + 30 * 60
    assert 29 * 60 < current['expires_in_seconds'] <= 30 * 60
    assert current['default_selection'] == [{'steamid': '1', 'app_ids': [REFLEX, VERGE]},
                                            {'steamid': '2', 'app_ids': [REFLEX]}]
    assert default_selection(None) == []
    real_time = store_purchase_service.time.time
    monkeypatch.setattr(store_purchase_service.time, 'time', lambda: real_time() + 31 * 60)
    old = service.status()['plan']
    assert not old['fresh'] and old['expires_in_seconds'] == 0


def test_the_purchase_plan_answers_lines_totals_and_refusals(world):
    service, *_ = world
    plan(service)
    made = make_purchase_plan(service, [{'steamid': '1', 'app_ids': [REFLEX, VERGE]},
                                        {'steamid': '2', 'app_ids': [VERGE]},      # balance too low
                                        {'steamid': '9', 'app_ids': [REFLEX]}])
    assert made['copies'] == 2 and made['accounts'] == 1 and made['total_usd'] == 0.9
    line = made['lines'][0]
    assert (line['account_name'], line['price'], line['currency'], line['wallet']) == ('alpha', 90, 'USD', 567)
    assert [game['app_id'] for game in line['games']] == [REFLEX, VERGE] and line['dry_run'] is None
    assert [(item['steamid'], item['reason']) for item in made['refused']] == [
        ('2', 'only games planned as "buy" can be bought'), ('9', 'account 9 is not in the plan')]
    assert made['dry_run'] == {'state': 'not run', 'job_id': None, 'passed': 0, 'failed': 0, 'error': None}
    assert not made['executable'] and made['fresh'] and made['buy'] == {'copies': 0, 'accounts': 0, 'total_usd': 0}
    assert service.status()['purchase_plan']['purchase_plan_id'] == made['purchase_plan_id']


def test_buy_pays_exactly_the_plan_and_farms_now(world):
    service, web, asf = world
    plan(service)
    job = buy(service, [{'steamid': '1', 'app_ids': [REFLEX, VERGE]}])
    assert job['kind'] == 'andvari-purchase' and not job['running'] and job['result']['paid'] == 1
    entry = service.status()['history'][0]
    assert entry['ok'] and entry['paid'] and not entry['dry_run'] and entry['charged'] == 90
    assert sorted(entry['owned_after']) == sorted([REFLEX, VERGE])
    assert web.balance['1'] == 477 and web.cart['1'] == []
    assert asf.farm_now_calls == ['1']
    assert service.status()['statistics']['copies'] == 2


def test_dry_run_pays_nothing_and_cleans_up(world):
    service, web, asf = world
    plan(service)
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    entry = service.status()['history'][0]
    assert entry['ok'] and entry['dry_run'] and entry['charged'] == 45
    assert entry['purchase_plan_id'] == made['purchase_plan_id']
    assert web.balance['1'] == 567 and web.cart['1'] == [] and asf.farm_now_calls == []
    assert [t['state'] for t in web.transactions.values()] == ['cancelled']
    assert service.status()['statistics']['purchases'] == 0
    assert made['dry_run']['state'] == 'done' and made['dry_run']['passed'] == 1 and made['executable']
    assert made['lines'][0]['dry_run'] == {'passed': True, 'paid': False, 'may_be_paid': False, 'charged': 45,
                                           'reason': None}
    assert made['buy'] == {'copies': 1, 'accounts': 1, 'total_usd': 0.45}
    job = service.jobs.get(made['dry_run']['job_id'])
    assert job['kind'] == 'andvari-dry run' and job['dry_run'] and job['result']['passed'] == 1


def test_a_real_purchase_needs_a_passed_dry_run_of_the_same_purchase_plan(world):
    service, web, _ = world
    plan(service)
    first = make_purchase_plan(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    refused('dry_run_required', service.execute_purchase_plan, first['purchase_plan_id'], dry_run=False)
    # A dry run of another purchase plan does not count for this one.
    dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    refused('dry_run_required', service.execute_purchase_plan, first['purchase_plan_id'], dry_run=False)
    refused('plan_unknown', service.execute_purchase_plan, 'nope', dry_run=False)
    assert web.posts('finalizetransaction') == []


def test_a_failed_dry_run_buys_nothing_and_only_passed_accounts_are_bought(world):
    service, web, _ = world
    plan(service)
    web.cart['2'] = [555]               # bravo's dry run fails (cart not empty, emptying switched off)
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}, {'steamid': '2', 'app_ids': [REFLEX]}],
                   empty_cart=False)
    assert made['dry_run']['passed'] == 1 and made['dry_run']['failed'] == 1
    assert made['buy'] == {'copies': 1, 'accounts': 1, 'total_usd': 0.45}
    bravo = made['lines'][1]['dry_run']
    assert not bravo['passed'] and 'cart is not empty' in bravo['reason']
    job = service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)
    assert [order for order in job['result']['accounts']] == ['1']
    assert web.balance == {'1': 522, '2': 79} and web.cart['2'] == [555]
    only_failed = dry_run(service, [{'steamid': '2', 'app_ids': [REFLEX]}], empty_cart=False)
    refused('nothing_passed', service.execute_purchase_plan, only_failed['purchase_plan_id'], dry_run=False)


def test_a_purchase_plan_is_bought_once_and_its_dry_run_can_repeat(world):
    service, web, _ = world
    plan(service)
    made = make_purchase_plan(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    service.execute_purchase_plan(made['purchase_plan_id'], dry_run=True)
    service.execute_purchase_plan(made['purchase_plan_id'], dry_run=True)        # again: allowed
    assert len([t for t in web.transactions.values() if t['state'] == 'cancelled']) == 2
    first = service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)
    second = service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)
    assert first['id'] == second['id'] and len(web.posts('finalizetransaction')) == 1
    refused('plan_executed', service.execute_purchase_plan, made['purchase_plan_id'], dry_run=True)
    assert not service.get_purchase_plan(made['purchase_plan_id'])['executable']


def test_a_dry_run_while_one_runs_answers_it_and_another_job_is_busy(world):
    service, web, _ = world
    plan(service)
    made = make_purchase_plan(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    service._job._busy.acquire()                              # an account check (or a purchase) is running
    try:
        refused('busy', service.execute_purchase_plan, made['purchase_plan_id'], dry_run=True)
        assert service.get_purchase_plan(made['purchase_plan_id'])['dry_run']['state'] == 'not run'
        running = service.jobs.create('andvari-dry run', {})
        service._purchase_plans[made['purchase_plan_id']].update(dry_run_state='running', dry_run_job_id=running['id'])
        assert service.execute_purchase_plan(made['purchase_plan_id'], dry_run=True)['id'] == running['id']
        refused('dry_run_required', service.execute_purchase_plan, made['purchase_plan_id'], dry_run=False)
    finally:
        service._job._busy.release()
    assert web.transactions == {}


def test_a_purchase_plan_expires_with_its_plan_and_a_new_check_replaces_it(world, monkeypatch):
    service, web, _ = world
    plan(service)
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    real_time = store_purchase_service.time.time
    monkeypatch.setattr(store_purchase_service.time, 'time', lambda: real_time() + 31 * 60)
    refused('plan_expired', service.execute_purchase_plan, made['purchase_plan_id'], dry_run=False)
    refused('plan_expired', service.execute_purchase_plan, made['purchase_plan_id'], dry_run=True)
    assert not service.get_purchase_plan(made['purchase_plan_id'])['fresh']
    monkeypatch.setattr(store_purchase_service.time, 'time', real_time)
    plan(service)                                             # the accounts checked again
    refused('plan_replaced', service.execute_purchase_plan, made['purchase_plan_id'], dry_run=False)
    assert service.get_purchase_plan(made['purchase_plan_id'])['replaced']
    assert web.posts('finalizetransaction') == []


def test_a_different_final_price_cancels(world):
    service, web, _ = world
    plan(service)
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    assert made['dry_run']['passed'] == 1
    web.final_total_bonus = 5                                 # the price changed after the dry run passed
    service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)
    entry = service.status()['history'][0]
    assert not entry['ok'] and 'would charge 50, planned 45' in entry['error']
    assert web.balance['1'] == 567 and web.cart['1'] == []
    assert [t['state'] for t in web.transactions.values()] == ['cancelled', 'cancelled']
    again = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    assert again['dry_run']['passed'] == 0 and 'would charge 50' in again['lines'][0]['dry_run']['reason']


def test_a_cart_with_other_items_is_emptied_first_and_logged(world):
    service, web, _ = world
    plan(service)
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    web.cart['1'] = [555]                                     # put in the cart after the dry run passed
    service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)
    entry = service.status()['history'][0]
    assert entry['paid'] and entry['removed_from_cart'] == ['package 555'] and entry['charged'] == 45
    assert web.balance['1'] == 522 and web.cart['1'] == []


def test_emptying_can_be_switched_off(world):
    service, web, _ = world
    plan(service)
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}], empty_cart=False)
    assert not made['empty_cart']
    web.cart['1'] = [555]
    service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)
    entry = service.status()['history'][0]
    assert not entry['ok'] and 'cart is not empty' in entry['error']
    assert web.cart['1'] == [555] and len(web.posts('AddItemsToCart')) == 1     # the dry run's only


def test_only_planned_games_can_be_bought_and_the_plan_expires(world, monkeypatch):
    service, web, _ = world
    assert refused('no_plan', make_purchase_plan, service, [{'steamid': '1', 'app_ids': [REFLEX]}],
                   plan_created_at=1.0) == 'check the accounts first'
    plan(service)
    assert 'only games planned' in refused('nothing_selected', make_purchase_plan, service,
                                           [{'steamid': '2', 'app_ids': [VERGE]}])
    assert 'selected twice' in refused('bad_selection', make_purchase_plan, service,
                                       [{'steamid': '1', 'app_ids': [REFLEX]}, {'steamid': '1', 'app_ids': [VERGE]}])
    refused('nothing_selected', make_purchase_plan, service, [])
    real_time = store_purchase_service.time.time
    monkeypatch.setattr(store_purchase_service.time, 'time', lambda: real_time() + 31 * 60)
    assert 'older than 30 minutes' in refused('plan_expired', make_purchase_plan, service,
                                              [{'steamid': '1', 'app_ids': [REFLEX]}])
    assert web.posts('inittransaction') == []


def test_a_failed_payment_is_not_cancelled_and_says_check_the_account(world):
    service, web, _ = world
    plan(service)
    web.finalize_answer = {'success': 2, 'purchaseresultdetail': 53}
    buy(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    entry = service.status()['history'][0]
    assert not entry['ok'] and 'check the account' in entry['error']
    assert len(web.posts('canceltransaction')) == 1           # the dry run's; paying started: never cancelled


def test_history_survives_a_reload(world):
    service, web, asf = world
    plan(service)
    buy(service, [{'steamid': '2', 'app_ids': [REFLEX]}])
    reloaded = StorePurchaseService(FakeSteam(), FakeCardDeals(), asf, state_path=service.state_path, http=web,
                                    sleep=lambda seconds: None)
    assert reloaded.status()['statistics']['accounts']['bravo']['games'] == ['Reflex']


# ---- review fixes ---------------------------------------------------------------------------------

def test_bought_games_cannot_be_bought_again_from_the_same_plan(world):
    service, web, _ = world
    plan(service)
    other = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    buy(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    row = {r['account_name']: r for r in service.status()['plan']['accounts']}['alpha']
    assert {g['app_id']: g['status'] for g in row['games']}[REFLEX] == 'bought'
    assert 'only games planned' in refused('nothing_selected', make_purchase_plan, service,
                                           [{'steamid': '1', 'app_ids': [REFLEX]}])
    # A second purchase plan whose dry run passed before the first one bought: refused too.
    refused('already_bought', service.execute_purchase_plan, other['purchase_plan_id'], dry_run=False)
    assert len(web.posts('finalizetransaction')) == 1


def test_owned_meanwhile_or_balance_gone_stops_before_the_cart(world):
    service, web, _ = world
    plan(service)
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    web.owned['1'].add(REFLEX)                                # bought by hand since the dry run
    service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)
    assert 'owns one of these games now' in service.status()['history'][0]['error']
    plan(service)
    made = dry_run(service, [{'steamid': '2', 'app_ids': [REFLEX]}])
    web.balance['2'] = 10
    adds = len(web.posts('AddItemsToCart'))
    service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)
    assert 'the wallet holds 10' in service.status()['history'][0]['error']
    assert len(web.posts('AddItemsToCart')) == adds


def test_an_unclear_payment_is_never_cancelled_and_says_may_be_paid(world):
    service, web, _ = world
    plan(service)
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])

    def broken_finalize(url, params=None, data=None, cookies=None, timeout=None, headers=None):
        if 'finalizetransaction' in url:
            raise OSError('read timed out')
        return FakeSteamWeb.post(web, url, params, data, cookies, timeout, headers)
    web.post = broken_finalize
    job = service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)
    assert job['result']['may_be_paid'] == 1 and job['result']['accounts']['1']['may_be_paid']
    entry = service.status()['history'][0]
    assert entry['payment_attempted'] and not entry['paid'] and 'MAY BE PAID' in entry['error']
    assert len(web.posts('canceltransaction')) == 1           # only the dry run's
    row = {r['account_name']: r for r in service.status()['plan']['accounts']}['alpha']
    assert {g['app_id']: g['status'] for g in row['games']}[REFLEX] == 'check'
    assert 'only games planned' in refused('nothing_selected', make_purchase_plan, service,
                                           [{'steamid': '1', 'app_ids': [REFLEX]}])


def test_paid_with_lagging_ownership_counts_as_bought(world):
    service, web, _ = world
    plan(service)
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    real_get = web.get
    calls = {'userdata': 0}

    def lagging(url, params=None, cookies=None, timeout=None, headers=None):
        if 'userdata' in url:
            calls['userdata'] += 1
            if calls['userdata'] > 1:                         # the read after paying: not listed yet
                return FakeResponse({'rgOwnedApps': [10], 'rgOwnedPackages': [1]})
        return real_get(url, params, cookies, timeout, headers)
    web.get = lagging
    service.execute_purchase_plan(made['purchase_plan_id'], dry_run=False)
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
    assert 'only games planned' in refused('nothing_selected', make_purchase_plan, reloaded,
                                           [{'steamid': '1', 'app_ids': [REFLEX]}])


def test_the_selection_must_match_the_plan_on_screen_and_be_well_formed(world):
    service, *_ = world
    plan(service)
    created = service.status()['plan']['created_at']
    assert 'plan changed' in refused('plan_replaced', make_purchase_plan, service,
                                     [{'steamid': '1', 'app_ids': [REFLEX]}], plan_created_at=created - 5)
    # The plan time is required (the review found it could be left out to skip this check).
    assert 'plan time (plan_created_at) is missing' in refused(
        'plan_time_missing', service.create_purchase_plan, None, [{'steamid': '1', 'app_ids': [REFLEX]}])
    refused('bad_request', service.create_purchase_plan, 'soon', [{'steamid': '1', 'app_ids': [REFLEX]}])
    assert 'must be a list' in refused('bad_selection', make_purchase_plan, service, {'steamid': '1'})
    assert 'numbers' in refused('bad_selection', make_purchase_plan, service, [{'steamid': '1', 'app_ids': ['x']}])


def test_a_dry_run_empties_the_cart_when_ticked(world):
    # Ivan, 2026-11-04: otherwise an account with something in its cart could never pass the dry run.
    service, web, _ = world
    plan(service)
    web.cart['1'] = [555]
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    assert made['dry_run']['passed'] == 1
    entry = service.status()['history'][0]
    assert entry['removed_from_cart'] == ['package 555'] and web.posts('DeleteCart')
    assert web.cart['1'] == [] and web.balance['1'] == 567       # nothing paid, the cart left empty


def test_a_dry_run_keeps_the_cart_when_emptying_is_off(world):
    service, web, _ = world
    plan(service)
    web.cart['1'] = [555]
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}], empty_cart=False)
    assert made['dry_run']['failed'] == 1
    assert web.cart['1'] == [555] and web.posts('DeleteCart') == []


@pytest.mark.parametrize('flag', [None, True, 'false', 0, 1, 'no'])
def test_anything_but_exactly_false_is_a_dry_run(world, flag):
    service, web, _ = world
    plan(service)
    made = make_purchase_plan(service, [{'steamid': '1', 'app_ids': [REFLEX]}])
    job = service.execute_purchase_plan(made['purchase_plan_id'], dry_run=flag)
    assert job['dry_run'] and service.status()['history'][0]['dry_run'] and web.balance['1'] == 567
    assert web.posts('finalizetransaction') == []
    assert service.execute_purchase_plan(made['purchase_plan_id'])['dry_run']     # missing = a dry run too


def test_the_routes_follow_the_commands(world, monkeypatch):
    """Through Flask: plan, dry run, buy; errors are {error, code}."""
    from flask import Flask

    from context import ctx
    from routes.huginn import bp
    service, web, _ = world
    monkeypatch.setattr(ctx, 'store_purchase_service', service)
    app = Flask(__name__)
    error_handlers.install(app)
    app.register_blueprint(bp)
    client = app.test_client()
    plan(service)
    status = client.get('/api/huginn/card-deals/buy').get_json()
    assert status['plan']['fresh'] and status['purchase_plan'] is None
    made = client.post('/api/huginn/card-deals/buy/purchase-plan', json={
        'plan_created_at': status['plan']['created_at'], 'selection': status['plan']['default_selection']}).get_json()
    base = f'/api/huginn/card-deals/buy/purchase-plans/{made["purchase_plan_id"]}'
    answer = client.post(f'{base}/execute', json={'dry_run': False})
    assert answer.status_code == 409 and answer.get_json()['code'] == 'dry_run_required'
    assert client.post(f'{base}/execute', json={}).status_code == 202
    assert client.get(base).get_json()['executable']
    bought = client.post(f'{base}/execute', json={'dry_run': False})
    assert bought.status_code == 202 and bought.get_json()['result']['paid'] == 2
    assert client.get('/api/huginn/card-deals/buy/purchase-plans/nope').get_json()['code'] == 'plan_unknown'
    assert client.post('/api/huginn/card-deals/buy/purchase-plan', json={'selection': []}).get_json()['code'] == \
        'plan_time_missing'
    assert web.balance == {'1': 477, '2': 34}


def test_cart_lines_name_bundles_and_other_lines():
    answer = {'response': {'cart': {'line_items': [{'packageid': 1}, {'bundleid': 2}, {'gift': True}]}}}
    assert store_purchase_service.cart_lines(answer) == ['package 1', 'bundle 2', '{"gift": true}']


def test_a_dry_run_that_stopped_with_an_error_buys_nothing(world):
    # The first account passed, then the job stopped: nothing of that dry run may be bought.
    service, web, _ = world
    plan(service)

    def stop(seconds):
        raise RuntimeError('stopped between accounts')
    service._sleep = stop
    made = dry_run(service, [{'steamid': '1', 'app_ids': [REFLEX]}, {'steamid': '2', 'app_ids': [REFLEX]}])
    assert made['dry_run']['state'] == 'failed' and not made['executable']
    refused('dry_run_required', service.execute_purchase_plan, made['purchase_plan_id'], dry_run=False)
    assert web.posts('finalizetransaction') == []
