"""Ratatoskr Storage shop: price sheet decoding, plan, approval page checks and the
guarded purchase (fake Steam, fake Ratatoskr, fake web)."""
import base64
import lzma
import re
import struct
import time

import pytest

import storage_shop_service as shop
from storage_deposit import CommandError
from storage_shop_service import (ShopError, StorageShopService, amount_shown, approval_form, can_buy, can_deliver_again,
                                  check_auth_request, decode_auth_request, decode_price_sheet, order_totals, parse_selection,
                                  plan_account, plan_lines, purchase_outcome, shop_statistics, steam_error,
                                  storage_unit_prices)

STEAMID = '76561198760595820'
TRANSACTION = '324965973906164367'               # Steam's transaction id (the approval page)
GAME_COORDINATOR_TRANSACTION = '2662912291'      # the Game Coordinator's (StorePurchaseInit)


# ---- price sheet -------------------------------------------------------------------------------

def _key_values(section):
    out = b''
    for key, value in section.items():
        name = key.encode() + b'\x00'
        if isinstance(value, dict):
            out += b'\x00' + name + _key_values(value)
        elif isinstance(value, int) and not -2 ** 31 <= value < 2 ** 31:
            out += b'\x07' + name + struct.pack('<Q', value)
        elif isinstance(value, int):
            out += b'\x02' + name + struct.pack('<i', value)
        else:
            out += b'\x01' + name + str(value).encode() + b'\x00'
    return out + b'\x0b'


def _valve_lzma(data):
    alone = lzma.compress(data, format=lzma.FORMAT_ALONE)
    properties, body = alone[:5], alone[13:]
    return b'LZMA' + struct.pack('<II', len(data), len(body)) + properties + body


def _approval_request(message):
    """Steam's ClientMicroTxnAuthRequest body as captured live: 0x01, then binary KeyValues."""
    return b'\x01' + _key_values({'MessageObject': message})[:-1]


SHEET = {'store': {'entries': {
    'Name Tag': {'item_link': 'Name Tag', 'prices': {'USD': 199}},
    'casket': {'item_link': 'casket', 'category_tags': 'Misc',
               'prices': {'USD': 199, 'EUR': 175, 'NOK': 1850, 'HKD': 1560}},
}, 'currencies': {'USD': 88888}}}


def test_price_sheet_decodes_and_gives_the_storage_unit_prices():
    raw = _valve_lzma(_key_values(SHEET))
    decoded = decode_price_sheet(raw)
    assert decoded['store']['entries']['casket']['category_tags'] == 'Misc'
    assert storage_unit_prices(decoded) == {'USD': 199, 'EUR': 175, 'NOK': 1850, 'HKD': 1560}


def test_price_sheet_without_a_storage_unit_gives_nothing():
    assert storage_unit_prices(decode_price_sheet(_key_values({'store': {'entries': {}}}))) == {}
    assert storage_unit_prices({}) == {}


# ---- plan --------------------------------------------------------------------------------------

PRICES = {'USD': 199, 'EUR': 175, 'NOK': 1850}
RATES = {'EUR': 0.9, 'NOK': 10.0, 'HKD': 7.8}


def _account(**changes):
    account = {'steamid': STEAMID, 'account_name': 'mer_tols', 'country': 'US', 'currency_id': 1,
               'balance': 759, 'error': None}
    account.update(changes)
    return account


def test_plan_counts_what_the_balance_covers():
    row = plan_account(_account(), PRICES, RATES)
    assert (row['status'], row['unit_price'], row['affordable'], row['currency']) == ('buy', 199, 3, 'USD')


@pytest.mark.parametrize('changes, status', [
    ({'balance': 150}, 'balance'),
    ({'currency_id': 17}, 'currency'),           # Turkish lira: not in the price sheet
    ({'country': None}, 'country'),
    ({'balance': None, 'currency_id': None}, 'wallet'),
])
def test_plan_statuses(changes, status):
    assert plan_account(_account(**changes), PRICES, RATES)['status'] == status


def test_plan_without_a_price_sheet_or_with_an_odd_price_is_not_buyable():
    assert plan_account(_account(), {}, RATES)['status'] == 'price'
    assert plan_account(_account(), {'USD': 999}, RATES)['status'] == 'price'       # above the cap
    assert plan_account(_account(currency_id=9, balance=5000), PRICES, {})['status'] == 'price'   # no rate


# ---- approval page -----------------------------------------------------------------------------

# The layout Steam served live (2026-10-03), trimmed.
PAGE = f"""<html><head><title>Purchase</title><script language="JavaScript">
function AuthorizeTransaction( approved ) {{ $('input_approved').value = approved ? 1 : 0; $('form_authtxn').submit(); }}
</script></head><body>
<form id="form_updateaddress" action="https://checkout.steampowered.com/checkout/?purchasetype=updatebillinginfo&microtxn={TRANSACTION}" method="POST"></form>
<form id="form_authtxn" action="https://checkout.steampowered.com/checkout/approvetxnsubmit" method="POST">
  <input type="hidden" name="transaction_id" value="{TRANSACTION}">
  <input type="hidden" name="returnurl" value="steam">
  <input type="hidden" name="sessionid" value="abc">
  <input type="hidden" id="input_approved" name="approved" value="0">
</form>
<div class="review_steam_account_name">Steam account: mer_tols</div>
<div>QTY PRICE NAME</div><div>2</div><div>$3.98 USD</div><div>Storage Unit</div>
<div class="total">Total: $3.98 USD</div>
<a id="purchase_button_bottom" href="javascript:AuthorizeTransaction( true );"><span>Authorize</span></a>
</body></html>"""


def test_approval_form_is_found_for_this_transaction():
    action, fields = approval_form(PAGE, TRANSACTION)
    assert action == 'https://checkout.steampowered.com/checkout/approvetxnsubmit'
    # "approved" is 0 in the page; Authorize sets it to 1
    assert fields == {'transaction_id': TRANSACTION, 'returnurl': 'steam', 'sessionid': 'abc', 'approved': '1'}
    assert approval_form(PAGE.replace('name="approved"', 'name="other"'), TRANSACTION) is None


def test_approval_form_refuses_another_transaction_or_a_foreign_action():
    assert approval_form(PAGE, '1') is None
    foreign = PAGE.replace('https://checkout.steampowered.com/checkout/approvetxnsubmit', 'https://evil.example/approve')
    assert approval_form(foreign, TRANSACTION) is None
    assert approval_form('<html>React shell</html>', TRANSACTION) is None


def test_approval_form_accepts_a_relative_action():
    relative = PAGE.replace('https://checkout.steampowered.com/checkout/approvetxnsubmit', '/checkout/approvetxnsubmit')
    assert approval_form(relative, TRANSACTION)[0] == 'https://checkout.steampowered.com/checkout/approvetxnsubmit'


@pytest.mark.parametrize('text, minor_units, currency, shown', [
    ('Total: $3.98 USD', 398, 'USD', True),
    ('Total: 37,00 kr', 3700, 'NOK', True),
    ('<b>₩ 5,340</b>', 534000, 'KRW', True),
    ('HK$ 31.20', 3120, 'HKD', True),
    ('Total: $1.99 USD', 398, 'USD', False),
    ('Order 398', 399, 'USD', False),
    ('Total: 37 kr', 3700, 'NOK', False),                    # whole units only where Steam prints them
    ('20 items in your cart', 2000, 'USD', False),
    ('$3.98\n1 item', 39800, 'USD', False),                  # no number across a line break
    ('<script>var total = 398;</script>Total: $9.99', 398, 'USD', False),
    ('$0.99 USD', 99, 'USD', True),                          # a sticker capsule (seen live)
    ('$0.99 USD', 990, 'USD', False),
    ('$0.09 USD', 99, 'USD', False),
])
def test_amount_shown(text, minor_units, currency, shown):
    assert amount_shown(text, minor_units, currency) is shown


# ---- purchase (fakes) --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, text='', status_code=200, headers=None, payload=None):
        self.text, self.status_code, self.headers, self.payload = text, status_code, headers or {}, payload

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self.payload if self.payload is not None else {}


class FakeHttp:
    def __init__(self, page=PAGE, wallet=(1, 759)):
        self.page, self.wallet = page, wallet
        self.inventory = None      # (first page, second page) of the Counter-Strike 2 inventory
        self.gets, self.posts = [], []

    def get(self, url, **kwargs):
        self.gets.append(url)
        if 'steamcommunity.com/market' in url:
            return FakeResponse(f'var g_rgWalletInfo = {{"wallet_currency":{self.wallet[0]},'
                                f'"wallet_balance":"{self.wallet[1]}"}};')
        if 'approvetxn' in url:
            return FakeResponse(self.page)
        if '/inventory/' in url and self.inventory is not None:
            page = self.inventory[1] if (kwargs.get('params') or {}).get('start_assetid') else self.inventory[0]
            return FakeResponse(payload=page)
        return FakeResponse('', 404)

    def post(self, url, **kwargs):
        assert kwargs.get('allow_redirects') is False      # the answer is recorded, not followed
        self.posts.append((url, kwargs.get('data')))
        return FakeResponse('ok')


class FakeStorage:
    def list_accounts(self):
        return [STEAMID]

    def load_account(self, steamid):
        return {'account_name': 'mer_tols'}


class FakeSteam:
    storage = FakeStorage()

    def web_session_cookie_for(self, steamid):
        return {'steamLoginSecure': f'{steamid}%7C%7Ctoken', 'sessionid': 'abc'}

    def ensure_fresh_session(self, steamid):
        pass

    def get_account(self, steamid):
        return {'account_name': 'mer_tols', 'shared_secret': 'secret'}

    def get_password(self, steamid):
        return 'password'


class FakeRatatoskr:
    def __init__(self, prices=None, finalize_result=1, init_result=1, account_country=None, refused_countries=(),
                 status='disconnected'):
        self.sheet = base64.b64encode(_valve_lzma(_key_values(
            {'store': {'entries': {'casket': {'prices': prices or {'USD': 199}}}}}))).decode()
        self.finalize_result, self.init_result = finalize_result, init_result
        self.units, self.calls = 2, []
        self.connected = False
        self.account_country, self.refused_countries = account_country, set(refused_countries)
        self.status, self.pending_moves, self.finalize_errors = status, 0, 0
        self.request_changes, self.no_request = {}, False
        self.item_definitions = []
        self.caskets = [{'item_id': '1', 'item_customname': 'Storage0'}, {'item_id': '2', 'item_customname': 'Storage 3'}]
        self.rename_error = None

    def get_caskets(self, steamid):
        return {'success': True, 'caskets': [dict(unit) for unit in self.caskets]}

    def rename_casket(self, steamid, casket_id, name):
        self.calls.append(('rename', casket_id, name))
        if self.rename_error:
            return {'error': self.rename_error}
        for unit in self.caskets:
            if unit['item_id'] == casket_id:
                unit['item_customname'] = name
        return {'success': True, 'item_id': casket_id, 'custom_name': name}

    def get_status(self, steamid):
        return {'status': 'connected' if self.connected else self.status}

    def get_move_status(self, steamid):
        return {'running': False, 'pending': self.pending_moves}

    def login(self, account_name, password, shared_secret=None):
        self.calls.append('login')
        self.connected = True
        return {'success': True, 'steamID': STEAMID}

    def disconnect(self, steamid):
        self.calls.append('disconnect')
        self.connected = False

    def store_user_data(self, steamid):
        return {'result': 1, 'price_sheet_base64': self.sheet, 'price_sheet_version': 7, 'storage_units': self.units,
                'account_country': self.account_country}

    def store_purchase_init(self, steamid, country, currency, quantity, unit_price, item_definition_index=1201):
        self.calls.append(('init', country, currency, quantity, unit_price))
        self.item_definitions.append(item_definition_index)
        if country in self.refused_countries:
            return {'success': False, 'result': 8, 'transactionId': '0'}
        if self.init_result != 1:
            return {'success': False, 'result': self.init_result, 'transactionId': '0', 'url': ''}
        message = {'lineitems': {'0': {'description': 'Storage Unit', 'gameitemid': item_definition_index,
                                       'amount': unit_price * quantity, 'quantity': quantity}},
                   'transid': int(TRANSACTION), 'orderid': int(GAME_COORDINATOR_TRANSACTION), 'appid': 730,
                   'currency': 1, 'total': unit_price * quantity, 'BillingTotal': unit_price * quantity}
        message.update(self.request_changes)
        return {'success': True, 'result': 1, 'transactionId': GAME_COORDINATOR_TRANSACTION, 'url': '',
                'authRequest': None if self.no_request else {'hex': _approval_request(message).hex()}}

    def store_purchase_finalize(self, steamid, transaction_id):
        self.calls.append(('finalize', transaction_id))
        if self.finalize_errors:
            self.finalize_errors -= 1
            self.connected = False
            return {'error': 'No active GC session', 'status_code': 401}
        if self.finalize_result == 1:
            self.units += 2
            self.caskets += [{'item_id': '11', 'item_customname': None}, {'item_id': '12', 'item_customname': None}]
            return {'result': 1, 'itemIds': ['11', '12'], 'storageUnits': self.units}
        return {'result': self.finalize_result, 'itemIds': []}

    def store_purchase_cancel(self, steamid, transaction_id):
        self.calls.append(('cancel', transaction_id))
        return {'result': 1}


class FakeCardDeals:
    def account_countries(self):
        return {STEAMID: 'US'}

    def exchange_rates(self):
        return RATES


def _service(tmp_path, http=None, ratatoskr=None):
    service = StorageShopService(FakeSteam(), ratatoskr or FakeRatatoskr(), FakeCardDeals(),
                                 state_path=str(tmp_path / 'shop.json'),
                                 approval_page_path=str(tmp_path / 'page.html'),
                                 http=http or FakeHttp(), sleep=lambda seconds: None)
    return service


def _planned(service, wanted=None):
    service._build_plan(wanted)
    return service._plan


def _order(service, quantity=2):
    plan = _planned(service)
    return {**plan['accounts'][0], 'quantity': quantity, 'plan_id': plan['id']}


def test_plan_reads_the_price_sheet_once_and_logs_out(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    row = _planned(service)['accounts'][0]
    assert (row['status'], row['unit_price'], row['affordable']) == ('buy', 199, 3)
    assert ratatoskr.calls == ['login', 'disconnect']


def test_dry_run_reads_the_approval_page_and_cancels(tmp_path):
    http, ratatoskr = FakeHttp(), FakeRatatoskr()
    service = _service(tmp_path, http, ratatoskr)
    result = service._buy_account(_order(service), dry_run=True)
    assert result['ok'] and not result['payment_attempted'] and not result['paid']
    assert ('init', 'US', 0, 2, 199) in ratatoskr.calls      # the game store's currency: USD = 0
    assert ('cancel', GAME_COORDINATOR_TRANSACTION) in ratatoskr.calls
    assert not any(isinstance(call, tuple) and call[0] == 'finalize' for call in ratatoskr.calls)
    assert http.posts == []
    assert (tmp_path / 'page.html').read_text() == PAGE
    assert ratatoskr.calls[-1] == 'disconnect'


def test_purchase_approves_finalizes_and_counts(tmp_path):
    http, ratatoskr = FakeHttp(), FakeRatatoskr()
    service = _service(tmp_path, http, ratatoskr)
    result = service._buy_account(_order(service), dry_run=False)
    assert result['ok'] and result['paid'] and result['error'] is None
    assert result['item_ids'] == ['11', '12']
    assert (result['storage_units_before'], result['storage_units_after']) == (2, 4)
    assert http.posts == [('https://checkout.steampowered.com/checkout/approvetxnsubmit',
                           {'transaction_id': TRANSACTION, 'returnurl': 'steam', 'sessionid': 'abc', 'approved': '1'})]
    assert not any(isinstance(call, tuple) and call[0] == 'cancel' for call in ratatoskr.calls)
    assert service.status()['statistics'] == {'units': 2, 'spent': {'USD': 398},
                                              'accounts': {'mer_tols': {'units': 2, 'spent': {'USD': 398}}}}
    assert service._plan['accounts'][0]['status'] == 'bought'


def test_a_changed_price_buys_nothing(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    order = _order(service)
    ratatoskr.sheet = FakeRatatoskr(prices={'USD': 249}).sheet
    result = service._buy_account(order, dry_run=False)
    assert not result['ok'] and 'price is now 249' in result['error']
    assert not any(isinstance(call, tuple) and call[0] == 'init' for call in ratatoskr.calls)


def test_a_page_without_the_total_or_the_form_cancels(tmp_path):
    for page in (PAGE.replace('$3.98', '$9.99'), '<html><script src="manifest.js"></script></html>'):
        http, ratatoskr = FakeHttp(page=page), FakeRatatoskr()
        service = _service(tmp_path, http, ratatoskr)
        result = service._buy_account(_order(service), dry_run=False)
        assert not result['ok'] and not result['payment_attempted']
        assert ('cancel', GAME_COORDINATOR_TRANSACTION) in ratatoskr.calls
        assert http.posts == []


def test_a_low_balance_buys_nothing(tmp_path):
    http, ratatoskr = FakeHttp(), FakeRatatoskr()
    service = _service(tmp_path, http, ratatoskr)
    order = _order(service, quantity=3)
    http.wallet = (1, 500)
    result = service._buy_account(order, dry_run=False)
    assert 'not bought' in result['error'] and 'login' not in ratatoskr.calls[2:]


def test_a_refused_init_cancels_nothing_and_pays_nothing(tmp_path):
    ratatoskr = FakeRatatoskr(init_result=2)
    service = _service(tmp_path, ratatoskr=ratatoskr)
    result = service._buy_account(_order(service), dry_run=False)
    assert not result['ok'] and 'did not open the purchase' in result['error']
    assert not any(isinstance(call, tuple) and call[0] == 'cancel' for call in ratatoskr.calls)


def test_no_delivery_after_approval_is_may_be_paid_and_never_cancelled(tmp_path):
    ratatoskr = FakeRatatoskr(finalize_result=2)
    service = _service(tmp_path, ratatoskr=ratatoskr)
    result = service._buy_account(_order(service), dry_run=False)
    assert result['payment_attempted'] and not result['paid']
    assert 'MAY BE PAID' in result['error']
    assert sum(1 for call in ratatoskr.calls if isinstance(call, tuple) and call[0] == 'finalize') == shop.FINALIZE_TRIES
    assert not any(isinstance(call, tuple) and call[0] == 'cancel' for call in ratatoskr.calls)
    assert service._plan['accounts'][0]['status'] == 'check'


def test_an_open_session_is_kept(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    order = _order(service)
    ratatoskr.calls.clear()
    ratatoskr.connected = True
    service._buy_account(order, dry_run=True)
    assert 'login' not in ratatoskr.calls and 'disconnect' not in ratatoskr.calls


def _code(call):
    with pytest.raises(CommandError) as raised:
        call()
    return raised.value.code


def _wait(service, job_id):
    for _ in range(500):
        job = service.jobs.get(job_id)
        if not job['running']:
            return job
        time.sleep(0.01)
    raise AssertionError('the job never finished')


def test_execute_guards(tmp_path):
    service = _service(tmp_path)
    assert _code(lambda: service.execute_plan('no such plan', dry_run=True)) == 'plan_unknown'
    first = _planned(service, {STEAMID: 1})['id']
    second = _planned(service, {STEAMID: 4})['id']          # a newer plan replaces the first
    assert _code(lambda: service.execute_plan(first, dry_run=True)) == 'plan_replaced'
    assert _code(lambda: service.execute_plan(first, dry_run=False)) == 'plan_replaced'
    assert service.describe_plan(first)['state'] == 'replaced'
    assert _code(lambda: service.execute_plan(second, dry_run=True, item='Name Tag')) == 'item_mismatch'
    service._plan['created_at'] = time.time() - shop.PLAN_TIME_TO_LIVE_SECONDS - 1
    assert service.describe_plan(second)['state'] == 'expired'
    assert _code(lambda: service.execute_plan(second, dry_run=True)) == 'plan_expired'
    service._plan['created_at'] = time.time()
    service._plan['accounts'][0]['status'] = 'balance'
    assert _code(lambda: service.execute_plan(second, dry_run=True)) == 'nothing_to_buy'
    service._plan['state'] = 'checking'
    assert _code(lambda: service.execute_plan(second, dry_run=True)) == 'plan_checking'


def test_selection_is_checked_before_anything_is_read(tmp_path):
    service = _service(tmp_path)
    assert _code(lambda: service.start_plan([{'steamid': STEAMID, 'quantity': 1}] * 2)) == 'bad_request'
    for quantity in (0, shop.MAX_QUANTITY_PER_ACCOUNT + 1, '2', True, None):
        assert _code(lambda: service.start_plan([{'steamid': STEAMID, 'quantity': quantity}])) == 'bad_quantity'
    assert _code(lambda: service.start_plan([])) == 'nothing_chosen'
    assert service._plan is None and service.job_status()['id'] is None


def test_the_plan_lines_buy_what_the_wallet_covers(tmp_path):
    service = _service(tmp_path)
    plan_id = _planned(service, {STEAMID: 5, 'gone': 1})['id']
    plan = service.describe_plan(plan_id)
    line, gone = plan['lines']
    assert (line['asked'], line['quantity'], line['total'], line['balance_after']) == (5, 3, 597, 162)
    assert line['payable'] and line['problem_code'] == 'covers_less' and line['problem'] == 'The wallet covers 3, not 5'
    assert not gone['payable'] and gone['problem_code'] == 'unknown_account'
    assert (plan['state'], plan['units'], plan['totals_by_currency'], plan['skipped']) == ('ready', 3, {'USD': 597}, 1)
    assert plan['units_text'] == '3 Storage Units' and plan['can_test'] and plan['expires_at']
    assert not plan['executable']                                   # not before a passed dry run


def _tested(service, plan_id):
    """Run the plan's dry run to its end (a real purchase needs one that passed)."""
    return _wait(service, service.execute_plan(plan_id, dry_run=True)['id'])


def test_executing_a_plan_twice_answers_the_same_job_and_buys_once(tmp_path):
    http, ratatoskr = FakeHttp(), FakeRatatoskr()
    service = _service(tmp_path, http, ratatoskr)
    plan_id = _planned(service, {STEAMID: 2})['id']
    _tested(service, plan_id)
    assert service.describe_plan(plan_id)['executable']
    job = service.execute_plan(plan_id, dry_run=False)
    assert service.execute_plan(plan_id, dry_run=False)['id'] == job['id']
    assert job['orders'] == [{'steamid': STEAMID, 'account_name': 'mer_tols', 'quantity': 2, 'unit_price': 199,
                              'currency': 'USD'}]
    assert [step['key'] for step in job['steps']] == ['wallet', 'login', 'opening', 'approving', 'delivering']
    finished = _wait(service, job['id'])
    assert service.execute_plan(plan_id, dry_run=False)['id'] == job['id']
    assert len(http.posts) == 1                                    # approved once
    outcome = finished['result']['accounts'][0]
    assert outcome['paid'] and outcome['outcome'] == 'bought' and outcome['summary'] == 'Bought · Storage Units 2 → 4 · named Storage 4, Storage 5'
    assert (finished['result']['paid_units'], finished['result']['paid_text']) == (2, '2 Storage Units')
    assert (finished['accounts_done'], finished['accounts_total']) == (1, 1)
    assert not service.describe_plan(plan_id)['executable']
    assert _code(lambda: service.execute_plan(plan_id, dry_run=True)) == 'plan_executed'
    service._plan['job_id'] = 'forgotten after a restart'
    assert _code(lambda: service.execute_plan(plan_id, dry_run=False)) == 'job_gone'


def test_a_real_purchase_needs_a_passed_dry_run_of_the_same_plan(tmp_path):
    http = FakeHttp()
    service = _service(tmp_path, http)
    plan_id = _planned(service, {STEAMID: 2})['id']
    plan = service.describe_plan(plan_id)
    assert plan['dry_run']['state'] == 'not_run' and plan['can_test'] and not plan['executable']
    assert _code(lambda: service.execute_plan(plan_id, dry_run=False)) == 'dry_run_required'
    _tested(service, _planned(service, {STEAMID: 2})['id'])           # a dry run of another plan
    assert _code(lambda: service.execute_plan(plan_id, dry_run=False)) == 'plan_replaced'
    assert http.posts == []


def test_a_failed_dry_run_refuses_the_real_purchase(tmp_path):
    http = FakeHttp(page=PAGE.replace('$3.98', '$9.99'))              # the page does not show the total
    service = _service(tmp_path, http)
    plan_id = _planned(service, {STEAMID: 2})['id']
    _tested(service, plan_id)
    plan = service.describe_plan(plan_id)
    assert plan['dry_run']['state'] == 'failed' and not plan['executable'] and plan['can_test']
    assert 'planned total' in plan['dry_run']['failed_lines'][0]['reason']
    assert plan['lines'][0]['test'] == 'failed'
    assert _code(lambda: service.execute_plan(plan_id, dry_run=False)) == 'dry_run_required'
    assert http.posts == []


def test_a_dry_run_may_be_repeated_and_answers_the_running_one(tmp_path):
    service = _service(tmp_path)
    plan_id = _planned(service, {STEAMID: 2})['id']
    first = _tested(service, plan_id)
    service._plan['dry_run']['state'] = 'running'                     # as while one runs
    service._plan['dry_run']['job_id'] = first['id']
    with service.jobs._lock:
        service.jobs._jobs[first['id']]['running'] = True
    assert service.execute_plan(plan_id, dry_run=True)['id'] == first['id']
    with service.jobs._lock:
        service.jobs._jobs[first['id']]['running'] = False
    assert _tested(service, plan_id)['id'] != first['id']             # finished: a new dry run
    assert service.describe_plan(plan_id)['dry_run']['state'] == 'passed'


def test_pay_is_allowed_for_30_minutes_after_the_dry_run_passed(tmp_path):
    service = _service(tmp_path)
    plan_id = _planned(service, {STEAMID: 2})['id']
    _tested(service, plan_id)
    # The wallets were read 40 minutes ago, the test passed 10 minutes ago: still payable.
    service._plan['created_at'] = time.time() - 40 * 60
    service._plan['first_passed_at'] = time.time() - 10 * 60
    plan = service.describe_plan(plan_id)
    assert plan['state'] == 'ready' and plan['executable']
    assert plan['expires_at'] == shop._iso(service._plan['first_passed_at'] + shop.PLAN_TIME_TO_LIVE_SECONDS)
    # 31 minutes after the pass: expired.
    service._plan['first_passed_at'] = time.time() - shop.PLAN_TIME_TO_LIVE_SECONDS - 60
    assert not service.describe_plan(plan_id)['executable']
    assert _code(lambda: service.execute_plan(plan_id, dry_run=False)) == 'plan_expired'


def test_a_later_dry_run_does_not_extend_the_plan(tmp_path):
    service = _service(tmp_path)
    plan_id = _planned(service, {STEAMID: 2})['id']
    _tested(service, plan_id)
    service._plan['first_passed_at'] -= 20 * 60      # the first pass was 20 minutes ago
    first = service._plan['first_passed_at']
    _tested(service, plan_id)                         # passes again: the deadline stays
    assert service._plan['dry_run']['state'] == 'passed' and service._plan['first_passed_at'] == first
    assert service.describe_plan(plan_id)['expires_at'] == shop._iso(first + shop.PLAN_TIME_TO_LIVE_SECONDS)
    service._plan['first_passed_at'] -= 11 * 60       # 31 minutes after the first pass
    assert _code(lambda: service.execute_plan(plan_id, dry_run=False)) == 'plan_expired'
    assert _code(lambda: service.execute_plan(plan_id, dry_run=True)) == 'plan_expired'


def test_without_a_passed_dry_run_the_lifetime_counts_from_the_wallet_read(tmp_path):
    service = _service(tmp_path)
    plan_id = _planned(service, {STEAMID: 2})['id']
    service._plan['created_at'] = time.time() - shop.PLAN_TIME_TO_LIVE_SECONDS - 1
    assert service.describe_plan(plan_id)['state'] == 'expired'
    assert _code(lambda: service.execute_plan(plan_id, dry_run=True)) == 'plan_expired'


OTHER = '76561198000000002'


class TwoAccountStorage:
    def list_accounts(self):
        return [STEAMID, OTHER]

    def load_account(self, steamid):
        return {'account_name': 'mer_tols' if steamid == STEAMID else 'other_account'}


class TwoAccountSteam(FakeSteam):
    storage = TwoAccountStorage()


class TwoAccountCardDeals(FakeCardDeals):
    def account_countries(self):
        return {STEAMID: 'US', OTHER: 'US'}


def test_only_the_lines_whose_dry_run_passed_are_bought(tmp_path):
    http, ratatoskr = FakeHttp(), FakeRatatoskr()
    service = StorageShopService(TwoAccountSteam(), ratatoskr, TwoAccountCardDeals(),
                                 state_path=str(tmp_path / 'shop.json'), approval_page_path=str(tmp_path / 'page.html'),
                                 http=http, sleep=lambda seconds: None)
    plan_id = _planned(service, {STEAMID: 2, OTHER: 2})['id']
    _tested(service, plan_id)          # the approval page names mer_tols: other_account fails its test
    plan = service.describe_plan(plan_id)
    assert plan['dry_run']['state'] == 'passed'
    assert [line['steamid'] for line in plan['dry_run']['passed_lines']] == [STEAMID]
    assert [line['steamid'] for line in plan['dry_run']['failed_lines']] == [OTHER]
    assert (plan['units'], plan['to_pay']['units'], plan['to_pay']['totals_by_currency']) == (4, 2, {'USD': 398})
    job = _wait(service, service.execute_plan(plan_id, dry_run=False)['id'])
    assert [order['steamid'] for order in job['orders']] == [STEAMID]
    assert job['result']['paid_units'] == 2 and len(http.posts) == 1


def test_a_dry_run_job_reports_the_shortened_steps(tmp_path):
    service = _service(tmp_path)
    plan_id = _planned(service, {STEAMID: 2})['id']         # the approval page fixture shows 2 × $1.99
    job = _wait(service, service.execute_plan(plan_id, dry_run=True)['id'])
    assert job['steps'][-1] == {'key': 'approving', 'label': 'Checking the approval page'} and len(job['steps']) == 4
    assert job['result']['accounts'][0]['outcome'] == 'test_passed' and job['result']['paid_units'] == 0


def test_an_older_plans_delivery_never_marks_the_current_plan(tmp_path):
    ratatoskr = FakeRatatoskr(finalize_result=2)
    service = _service(tmp_path, ratatoskr=ratatoskr)
    entry = service._buy_account(_order(service), dry_run=False)          # left MAY BE PAID on the first plan
    assert entry['plan_id'] and service._plan['accounts'][0]['status'] == 'check'
    _planned(service, {STEAMID: 2})                                        # a newer plan
    ratatoskr.finalize_result = 1
    service._deliver_again(entry)
    assert entry['paid'] and service._plan['accounts'][0]['status'] == 'buy'


def test_a_failed_check_shows_its_own_reason_on_every_line(tmp_path):
    service = _service(tmp_path)
    service._replace_plan(service._plan_record('abc', 'casket', {STEAMID: 1}, None))
    service._plan.update(state='failed', error='Ratatoskr could not name the item')
    line = service.describe_plan('abc')['lines'][0]
    assert line['account_name'] == 'mer_tols' and line['problem_code'] == 'check_failed'
    assert line['problem'] == 'The wallets could not be checked: Ratatoskr could not name the item'


def test_a_lines_problem_keeps_the_rows_real_reason():
    row = {'steamid': '1', 'account_name': 'a', 'status': 'wallet', 'error': 'Steam rate-limited the request (HTTP 429)'}
    assert plan_lines([row], {'1': 1})[0]['problem'] == 'Wallet could not be read: Steam rate-limited the request (HTTP 429)'
    assert plan_lines([{**row, 'error': None}], {'1': 1})[0]['problem'] == 'Wallet could not be read'


def test_a_check_interrupted_by_a_restart_is_a_failed_plan(tmp_path):
    service = _service(tmp_path)
    service._replace_plan(service._plan_record('abc', 'casket', {STEAMID: 1}, None))
    service._save()
    reloaded = _service(tmp_path)
    assert reloaded.describe_plan('abc')['state'] == 'failed'
    assert _code(lambda: reloaded.execute_plan('abc', dry_run=True)) == 'plan_failed'


def test_account_rows_say_what_the_page_may_do():
    assert can_buy(False, 3)['can_buy_code'] == 'currency_not_sold' and not can_buy(False, 3)['selectable']
    assert can_buy(True, None) == {'can_buy_code': 'unknown', 'can_buy': '?', 'can_buy_count': None,
                                   'selectable': True, 'max_quantity': shop.MAX_QUANTITY_PER_ACCOUNT}
    assert (can_buy(True, 0)['can_buy_code'], can_buy(True, 0)['selectable']) == ('balance_too_low', False)
    assert (can_buy(True, 3)['can_buy'], can_buy(True, 3)['max_quantity']) == ('up to 3', 3)


def test_pure_order_helpers():
    assert parse_selection([{'steamid': 1, 'quantity': 2}]) == {'1': 2}
    rows = [{'steamid': '1', 'account_name': 'a', 'status': 'buy', 'affordable': 30, 'unit_price': 100,
             'currency': 'EUR', 'usd_per_unit': 1.1, 'balance': 3000}]
    lines = plan_lines(rows, {'1': 25})
    assert lines[0]['quantity'] == shop.MAX_QUANTITY_PER_ACCOUNT          # never more than 20 a purchase
    assert order_totals(lines)['usd_total'] == 22.0


def test_the_quote_totals_the_order_panel_without_steam(tmp_path):
    http = FakeHttp()
    service = _service(tmp_path, http)
    quote = service.quote('casket', [{'steamid': STEAMID, 'quantity': 2}])
    assert (quote['complete'], quote['totals_by_currency'], quote['units']) == (False, {}, 2)   # never read
    _planned(service)
    gets = len(http.gets)
    quote = service.quote('casket', [{'steamid': STEAMID, 'quantity': 2}])
    assert (quote['complete'], quote['totals_by_currency'], quote['usd_estimate']) == (True, {'USD': 398}, 3.98)
    assert len(http.gets) == gets


def test_an_interrupted_purchase_is_flagged_on_load(tmp_path):
    service = _service(tmp_path)
    service._record({'at': 1, 'steamid': STEAMID, 'account_name': 'mer_tols', 'state': 'in progress',
                     'payment_attempted': True, 'paid': False})
    reloaded = _service(tmp_path)
    assert 'MAY BE PAID' in reloaded.status()['history'][0]['error']


def test_statistics_skip_dry_runs_and_unpaid():
    history = [{'account_name': 'a', 'paid': True, 'quantity': 2, 'currency': 'USD', 'expected': 398},
               {'account_name': 'a', 'paid': True, 'dry_run': True, 'quantity': 5, 'currency': 'USD', 'expected': 995},
               {'account_name': 'b', 'paid': False, 'quantity': 1, 'currency': 'EUR', 'expected': 175}]
    assert shop_statistics(history) == {'units': 2, 'spent': {'USD': 398},
                                        'accounts': {'a': {'units': 2, 'spent': {'USD': 398}}}}


def test_the_store_country_goes_first_and_the_login_country_follows_an_invalid_parameter(tmp_path):
    ratatoskr = FakeRatatoskr(account_country='tr', refused_countries={'US'})
    service = _service(tmp_path, ratatoskr=ratatoskr)
    result = service._buy_account(_order(service), dry_run=True)
    inits = [call for call in ratatoskr.calls if isinstance(call, tuple) and call[0] == 'init']
    assert inits == [('init', 'US', 0, 2, 199), ('init', 'TR', 0, 2, 199)]
    assert result['ok'] and result['country'] == 'TR'


def test_currencies_the_game_store_does_not_know_are_not_planned():
    assert shop.GAME_STORE_CURRENCIES['NOK'] == 9 and shop.GAME_STORE_CURRENCIES['HKD'] == 27
    assert plan_account(_account(currency_id=9, balance=5000), PRICES, RATES)['status'] == 'buy'


def test_a_lost_session_after_approval_is_logged_in_again_for_delivery(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    order = _order(service)
    ratatoskr.finalize_errors = 1          # Ratatoskr restarted between approval and delivery
    result = service._buy_account(order, dry_run=False)
    assert result['paid'] and result['ok']
    assert ratatoskr.calls.count('login') == 3        # plan, purchase, and again for the delivery
    assert result['approval_answer']['status'] == 200


def test_deliver_again_finishes_an_unclear_purchase(tmp_path):
    ratatoskr = FakeRatatoskr(finalize_result=2)
    service = _service(tmp_path, ratatoskr=ratatoskr)
    entry = service._buy_account(_order(service), dry_run=False)
    assert 'MAY BE PAID' in entry['error']
    ratatoskr.finalize_result = 1
    service._deliver_again(entry)
    assert entry['paid'] and entry['item_ids'] == ['11', '12']
    assert service._plan['accounts'][0]['status'] == 'bought'
    assert _code(lambda: service.start_deliver_again(entry['at'])) == 'nothing_to_deliver'


def test_deliver_again_is_a_job_with_its_order_and_outcome(tmp_path):
    ratatoskr = FakeRatatoskr(finalize_result=2)
    service = _service(tmp_path, ratatoskr=ratatoskr)
    entry = service._buy_account(_order(service), dry_run=False)
    status = service.status()
    assert status['history'][0]['can_deliver_again'] and len(status['unclear_purchases']) == 1
    ratatoskr.finalize_result = 1
    job = service.start_deliver_again(entry['at'])
    assert job['orders'][0]['quantity'] == 2 and job['steps'] == [{'key': 'delivering', 'label': 'Delivering'}]
    finished = _wait(service, job['id'])
    assert finished['result']['accounts'][0]['paid'] and finished['result']['paid_units'] == 2
    assert service.status()['unclear_purchases'] == []


def test_can_deliver_again_needs_an_approved_finished_undelivered_purchase():
    unclear = {'payment_attempted': True, 'paid': False, 'transaction_id': '1', 'state': 'done'}
    assert can_deliver_again(unclear) and purchase_outcome(unclear)['can_deliver_again']
    for change in ({'paid': True}, {'payment_attempted': False}, {'transaction_id': None}, {'state': 'in progress'}):
        assert not can_deliver_again({**unclear, **change})


def test_a_broken_session_of_someone_else_is_repaired_but_not_logged_out(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    order = _order(service)
    ratatoskr.calls.clear()
    ratatoskr.status = 'gc_lost'
    service._buy_account(order, dry_run=True)
    assert 'login' in ratatoskr.calls and 'disconnect' not in ratatoskr.calls


def test_no_logout_while_items_are_moving(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    order = _order(service)
    ratatoskr.calls.clear()
    ratatoskr.pending_moves = 3
    service._buy_account(order, dry_run=True)
    assert 'login' in ratatoskr.calls and 'disconnect' not in ratatoskr.calls


def test_an_unreadable_price_sheet_does_not_log_in_every_account(tmp_path):
    class TwoAccounts(FakeStorage):
        def list_accounts(self):
            return [STEAMID, '76561198000000001']
    ratatoskr = FakeRatatoskr()
    ratatoskr.sheet = base64.b64encode(_key_values({'store': {'entries': {}}})).decode()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    service.steam.storage = TwoAccounts()
    service._build_plan(None)
    assert ratatoskr.calls == ['login', 'disconnect']



def test_steams_error_page_is_reported_and_cancelled(tmp_path):
    page = ('<title>Site Error</title><h2>Oops, sorry!</h2><div>An error was encountered while processing your request:'
            '<br><br>An unexpected error occurred while authorizing your transaction. Please try again.</div>')
    assert steam_error(page).startswith('An unexpected error occurred while authorizing')
    http, ratatoskr = FakeHttp(page=page), FakeRatatoskr()
    service = _service(tmp_path, http, ratatoskr)
    result = service._buy_account(_order(service), dry_run=False)
    assert 'An unexpected error occurred' in result['error'] and not result['payment_attempted']
    assert ('cancel', GAME_COORDINATOR_TRANSACTION) in ratatoskr.calls




LIVE_REQUEST = ('01004d6573736167654f626a65637400006c696e656974656d7300003000016465736372697074696f6e0053746f'
                '7261676520556e6974000767616d656974656d696400b1040000000000000a616d6f756e7400c7000000000000000271'
                '75616e7469747900010000000808077472616e736964004f7f61e05d8ec602076f726465726964009bd6b89e00000000'
                '02617070696400da0200000263757272656e637900010000000276617400010000000a746f74616c00c7000000000000'
                '000a546178000000000000000000026c616e677561676500000000000273616e64626f7800000000000242696c6c696e'
                '6743757272656e637900010000000a42696c6c696e67546f74616c00c7000000000000000808')


def test_the_live_approval_request_decodes_and_matches_its_order():
    """Captured 2026-10-03 on everthinklol: one Storage Unit, Game Coordinator transaction 2662913691."""
    message = decode_auth_request({'hex': LIVE_REQUEST})
    assert message['lineitems']['0'] == {'description': 'Storage Unit', 'gameitemid': 1201, 'amount': 199, 'quantity': 1}
    assert check_auth_request(message, '2662913691', 1, 199, 1) == '200003767312154447'


@pytest.mark.parametrize('transaction, quantity, expected, currency', [
    ('2662913692', 1, 199, 1),     # another order
    ('2662913691', 2, 199, 1),     # another quantity
    ('2662913691', 1, 198, 1),     # another total
    ('2662913691', 1, 199, 3),     # another currency
])
def test_an_approval_request_for_anything_else_is_refused(transaction, quantity, expected, currency):
    with pytest.raises(ShopError):
        check_auth_request(decode_auth_request({'hex': LIVE_REQUEST}), transaction, quantity, expected, currency)
    with pytest.raises(ShopError):
        check_auth_request(None, transaction, quantity, expected, currency)


def test_the_approval_page_is_steams_transaction_and_delivery_the_game_coordinators(tmp_path):
    http, ratatoskr = FakeHttp(), FakeRatatoskr()
    service = _service(tmp_path, http, ratatoskr)
    result = service._buy_account(_order(service), dry_run=False)
    assert result['paid'] and result['steam_transaction_id'] == TRANSACTION
    assert any(f'approvetxn/{TRANSACTION}/' in url for url in http.gets)
    assert ('finalize', GAME_COORDINATOR_TRANSACTION) in ratatoskr.calls


@pytest.mark.parametrize('changes', [{'appid': 440}, {'total': 999}, {'orderid': 1}])
def test_a_mismatching_or_missing_approval_request_cancels(tmp_path, changes):
    http, ratatoskr = FakeHttp(), FakeRatatoskr()
    ratatoskr.request_changes = changes
    service = _service(tmp_path, http, ratatoskr)
    result = service._buy_account(_order(service), dry_run=False)
    assert 'does not match the order' in result['error'] and not result['payment_attempted']
    assert ('cancel', GAME_COORDINATOR_TRANSACTION) in ratatoskr.calls and http.posts == []
    ratatoskr = FakeRatatoskr()
    ratatoskr.no_request = True
    service = _service(tmp_path, http, ratatoskr)
    assert 'no approval request' in service._buy_account(_order(service), dry_run=False)['error']


def test_a_page_for_another_account_is_refused(tmp_path):
    http, ratatoskr = FakeHttp(page=PAGE.replace('Steam account: mer_tols', 'Steam account: someone_else')), FakeRatatoskr()
    service = _service(tmp_path, http, ratatoskr)
    result = service._buy_account(_order(service), dry_run=False)
    assert 'not for the account mer_tols' in result['error'] and http.posts == []
    assert ('cancel', GAME_COORDINATOR_TRANSACTION) in ratatoskr.calls


def test_the_live_approval_page_is_accepted():
    """The page saved by the last dry run, when present locally: a Storage Unit ($1.99,
    2026-10-03) or a 10 Year Birthday Sticker Capsule ($0.99, the same day)."""
    import os
    path = os.path.join(os.path.dirname(__file__), '..', 'cache', 'storage_shop_approval_page.html')
    if not os.path.exists(path) or 'form_authtxn' not in open(path, encoding='utf-8').read():
        pytest.skip('no saved approval page')
    page = open(path, encoding='utf-8').read()
    identifier = re.search(r'name="transaction_id" value="(\d+)"', page).group(1)
    action, fields = approval_form(page, identifier)
    assert action.endswith('/checkout/approvetxnsubmit') and fields['approved'] == '1'
    assert amount_shown(page, 199, 'USD') or amount_shown(page, 99, 'USD')


def test_a_closed_store_is_logged_in_afresh_for_delivery(tmp_path):
    """Live 2026-10-03: Finalize timed out, the store closed, and retries on the same
    session were refused until a fresh login."""
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    order = _order(service)
    original = ratatoskr.store_purchase_finalize
    answers = [{'error': 'The Game Coordinator did not answer in time'},
               {'error': 'An earlier store request timed out: log in again before buying'}]

    def finalize(steamid, transaction_id):
        if answers:
            ratatoskr.calls.append(('finalize', transaction_id))
            return answers.pop(0)
        return original(steamid, transaction_id)
    ratatoskr.store_purchase_finalize = finalize
    result = service._buy_account(order, dry_run=False)
    assert result['paid'] and result['item_ids'] == ['11', '12']
    finalize_index = max(i for i, call in enumerate(ratatoskr.calls) if call == ('finalize', GAME_COORDINATOR_TRANSACTION))
    assert 'disconnect' in ratatoskr.calls[:finalize_index] and ratatoskr.calls.count('login') >= 3


def test_every_account_is_listed_with_what_is_last_known(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    row = service.status()['accounts'][0]
    assert (row['account_name'], row['balance'], row['affordable']) == ('mer_tols', None, None)   # never read
    assert row['balance_usd'] is None
    service._build_plan(None)
    row = service.status()['accounts'][0]
    assert (row['balance'], row['currency'], row['unit_price'], row['affordable']) == (759, 'USD', 199, 3)
    assert row['balance_usd'] == 7.59                                  # what the Wallet column sorts on
    service._buy_account(_order(service, quantity=2), dry_run=False)
    row = service.status()['accounts'][0]
    assert (row['balance'], row['storage_units']) == (361, 4)        # paid 398; 2 + 2 delivered
    assert _service(tmp_path, ratatoskr=ratatoskr).status()['accounts'][0]['storage_units'] == 4   # kept on disk


def test_the_purchase_reports_its_steps(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    order = _order(service)
    steps = []
    original = service._step
    service._step = lambda steamid, step: (steps.append(step), original(steamid, step))
    service._buy_account(order, dry_run=False)
    assert steps == ['wallet', 'login', 'opening', 'approving', 'delivering']


def _inventory_page(units, others, more=False, last=None):
    descriptions = [{'classid': '1', 'instanceid': '0', 'market_hash_name': 'Storage Unit', 'name': 'Storage Unit'},
                    {'classid': '2', 'instanceid': '0', 'market_hash_name': 'Revolution Case'}]
    assets = [{'classid': '1', 'instanceid': '0'}] * units + [{'classid': '2', 'instanceid': '0'}] * others
    return {'success': 1, 'assets': assets, 'descriptions': descriptions,
            **({'more_items': 1, 'last_assetid': last} if more else {})}


def test_the_check_counts_storage_units_from_the_web_inventory(tmp_path):
    http = FakeHttp()
    http.inventory = (_inventory_page(2, 5, more=True, last='999'), _inventory_page(1, 3))
    service = _service(tmp_path, http=http)
    service._build_plan(None)
    assert service.status()['accounts'][0]['storage_units'] == 3       # 2 on the first page, 1 on the second
    http.inventory = (_inventory_page(0, 4), None)
    service._build_plan(None)
    assert service.status()['accounts'][0]['storage_units'] == 0       # 0 is shown as 0, not unknown


def test_an_unreadable_inventory_keeps_the_last_known_count(tmp_path):
    http = FakeHttp()
    http.inventory = (_inventory_page(4, 0), None)
    service = _service(tmp_path, http=http)
    service._build_plan(None)
    http.inventory = ({'success': 0}, None)
    service._build_plan(None)
    assert service.status()['accounts'][0]['storage_units'] == 4


def _timing_out_finalize(ratatoskr, answers):
    original = ratatoskr.store_purchase_finalize

    def finalize(steamid, transaction_id):
        if answers:
            ratatoskr.calls.append(('finalize', transaction_id))
            return answers.pop(0)
        return original(steamid, transaction_id)
    ratatoskr.store_purchase_finalize = finalize


def test_a_timed_out_session_the_shop_did_not_open_is_never_logged_out(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    order = _order(service)
    ratatoskr.calls.clear()
    ratatoskr.connected = True                       # the Ratatoskr page holds the session
    _timing_out_finalize(ratatoskr, [{'error': 'The Game Coordinator did not answer in time'}])
    result = service._buy_account(order, dry_run=False)
    assert 'disconnect' not in ratatoskr.calls and 'did not open' in result['error']
    assert 'MAY BE PAID' in result['error']


def test_a_timed_out_session_is_not_logged_out_during_a_move(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    order = _order(service)
    ratatoskr.calls.clear()
    _timing_out_finalize(ratatoskr, [{'error': 'The Game Coordinator did not answer in time'}])
    original_status = ratatoskr.get_move_status
    ratatoskr.get_move_status = lambda steamid: {'running': True, 'pending': 0}
    result = service._buy_account(order, dry_run=False)
    ratatoskr.get_move_status = original_status
    assert 'items are moving' in result['error'] and 'disconnect' not in ratatoskr.calls


def test_a_failed_count_keeps_the_wallet_row_buyable(tmp_path):
    http = FakeHttp()
    http.inventory = (_inventory_page(1, 0), None)
    service = _service(tmp_path, http=http)
    original = service._count_storage_units
    service._count_storage_units = lambda steamid, cookies: (_ for _ in ()).throw(shop.ShopError('HTTP 429'))
    service._build_plan(None)
    row = service._plan['accounts'][0]
    assert row['status'] == 'buy' and row['error'] is None
    service._count_storage_units = original


def test_a_plan_is_a_job_followed_by_its_id(tmp_path):
    service = _service(tmp_path)
    plan = service.start_plan([{'steamid': STEAMID, 'quantity': 1}])
    assert plan['state'] == 'checking' and plan['check_job_id']
    job = _wait(service, plan['check_job_id'])
    assert (job['plan_id'], job['accounts_done'], job['accounts_total']) == (plan['plan_id'], 1, 1)
    assert service.describe_plan(plan['plan_id'])['state'] == 'ready'
    assert service.status()['job']['id'] == plan['check_job_id']


def test_wallets_in_other_currencies_are_valued_in_dollars():
    service = StorageShopService.__new__(StorageShopService)
    service._accounts = lambda: [('1', 'euro_account', 'DE'), ('2', 'unknown_account', 'DE')]
    known = {'1': {'currency_id': 3, 'balance': 1000}}                 # 10.00 EUR
    rows = service.accounts_view(known, {'EUR': 175}, {'EUR': 0.8})
    assert rows[0]['balance_usd'] == 12.5
    assert rows[1]['balance_usd'] is None                              # sorts last, never as 0


def test_every_price_sheet_read_goes_to_the_listener(tmp_path):
    service = _service(tmp_path)
    seen = []
    service.on_price_sheet = lambda sheet, version, read_at: seen.append((sheet['store']['entries'], version))
    _planned(service)
    assert seen == [({'casket': {'prices': {'USD': 199}}}, 7)]


def test_a_failing_listener_does_not_stop_the_plan(tmp_path):
    service = _service(tmp_path)
    service.on_price_sheet = lambda *arguments: 1 / 0
    assert _planned(service)['accounts'][0]['status'] == 'buy'


def test_price_sheet_job_reads_once_and_logs_out(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    service._refresh_price_sheet()
    assert ratatoskr.calls == ['login', 'disconnect']
    assert service.status()['price_sheet']['prices'] == {'USD': 199}


def test_price_sheet_job_reports_an_unreadable_sheet(tmp_path):
    ratatoskr = FakeRatatoskr()
    ratatoskr.store_user_data = lambda steamid: {'error': 'No active GC session'}
    service = _service(tmp_path, ratatoskr=ratatoskr)
    with pytest.raises(ShopError, match='No active GC session'):
        service._refresh_price_sheet()
    assert ratatoskr.calls == ['login', 'disconnect']


# ---- any other store item (the Store Catalogue's Buy button) ----------------------------------

NAME_TAG = 'Name Tag'       # $1.99 like the Storage Unit, so the approval page fixture fits


class FakeStoreRatatoskr(FakeRatatoskr):
    """A price sheet with a Name Tag next to the Storage Unit, and Ratatoskr's item names."""

    def __init__(self, **changes):
        super().__init__(**changes)
        self.sheet = base64.b64encode(_valve_lzma(_key_values({'store': {'entries': {
            'casket': {'prices': {'USD': 199}}, NAME_TAG: {'prices': {'USD': 199}},
            'XpShopTicket1': {'prices': {'USD': 1599}}}}}))).decode()

    def store_item_names(self, names):
        known = {NAME_TAG: {'defIndex': 1200, 'name': 'Name Tag', 'prefab': 'valve csgo_tool'}}
        return {'success': True, 'definitions': {name: known[name] for name in names if name in known}}


def _name_tag_plan(service, wanted=None):
    service._build_plan(wanted, NAME_TAG)
    return service._plan


def test_a_plan_for_another_item_carries_it_and_counts_no_storage_units(tmp_path):
    http = FakeHttp()
    service = _service(tmp_path, http, FakeStoreRatatoskr())
    plan = _name_tag_plan(service)
    assert (plan['entry'], plan['item_name'], plan['definition_index']) == (NAME_TAG, 'Name Tag', 1200)
    assert plan['max_usd_per_unit'] == 2.49            # $1.99 × 1.25
    assert plan['accounts'][0]['status'] == 'buy'
    assert not any('/inventory/' in url for url in http.gets)       # no Storage Unit count for a Name Tag
    status = service.status(NAME_TAG)
    assert status['item']['name'] == 'Name Tag' and status['accounts'][0]['unit_price'] == 199


def test_buying_another_item_opens_it_and_checks_it_in_the_approval_request(tmp_path):
    http, ratatoskr = FakeHttp(), FakeStoreRatatoskr()
    service = _service(tmp_path, http, ratatoskr)
    plan_id = _name_tag_plan(service, {STEAMID: 2})['id']
    # the screen shows another item than the plan
    assert _code(lambda: service.execute_plan(plan_id, dry_run=False, item='casket')) == 'item_mismatch'
    assert service.describe_plan(plan_id)['units_text'] == '2 × Name Tag'
    row = service._plan['accounts'][0]
    result = service._buy_account({**row, 'quantity': 2, 'entry': NAME_TAG, 'item_name': 'Name Tag',
                                   'definition_index': 1200}, dry_run=False)
    assert result['ok'] and result['paid'] and result['item_ids'] == ['11', '12']
    assert ratatoskr.item_definitions == [1200]
    assert (result['entry'], result['storage_units_before'], result['storage_units_after']) == (NAME_TAG, None, None)
    assert service.status(NAME_TAG)['statistics']['units'] == 2
    assert service.status()['statistics']['units'] == 0          # Storage Units bought: none


def test_an_approval_request_for_another_item_is_refused(tmp_path):
    ratatoskr = FakeStoreRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    row = _name_tag_plan(service)['accounts'][0]
    ratatoskr.request_changes = {'lineitems': {'0': {'gameitemid': 1201, 'quantity': 1, 'amount': 199}}}
    result = service._buy_account({**row, 'quantity': 1, 'entry': NAME_TAG, 'item_name': 'Name Tag',
                                   'definition_index': 1200}, dry_run=False)
    assert not result['ok'] and not result['payment_attempted'] and 'does not match' in result['error']
    assert ('cancel', GAME_COORDINATOR_TRANSACTION) in ratatoskr.calls


def test_passes_and_unknown_items_are_not_sold(tmp_path):
    service = _service(tmp_path, ratatoskr=FakeStoreRatatoskr())
    assert _code(lambda: service.start_plan(None, 'XpShopTicket1')) == 'not_for_sale'
    assert _code(lambda: service.start_plan(None, 'Game License')) == 'not_for_sale'
    assert not service.status('XpShopTicket1')['item']['for_sale']
    with pytest.raises(ShopError, match='not in Ratatoskr'):
        service._build_plan(None, 'coupon - unknown')
    assert service.status('coupon - unknown')['item']['error']


def test_older_purchases_without_an_item_count_as_storage_units():
    history = [{'paid': True, 'account_name': 'a', 'quantity': 1, 'currency': 'USD', 'expected': 199},
               {'paid': True, 'account_name': 'a', 'quantity': 3, 'currency': 'USD', 'expected': 597, 'entry': NAME_TAG}]
    assert shop_statistics(history)['units'] == 1
    assert shop_statistics(history, NAME_TAG)['units'] == 3


# Steam's approval request for a sticker capsule, as seen live 2026-10-03 (dry run): the order
# id is spelled "OrderID", not "orderid" as for a Storage Unit.
CAPSULE_REQUEST = {'BillingCurrency': 1, 'BillingTotal': 99, 'OrderID': 2663192793, 'Refundable': 1, 'appid': 730,
                   'currency': 1, 'language': 0, 'total': 99, 'transid': 129072073187068990,
                   'lineitems': {'0': {'amount': 99, 'description': '10 Year Birthday Sticker Capsule',
                                       'gameitemid': 20188, 'quantity': 1}}}


def test_approval_request_names_are_read_in_any_case():
    assert check_auth_request(CAPSULE_REQUEST, '2663192793', 1, 99, 1, 20188) == '129072073187068990'
    with pytest.raises(ShopError, match='items'):
        check_auth_request(CAPSULE_REQUEST, '2663192793', 1, 99, 1)       # a Storage Unit was ordered
    with pytest.raises(ShopError, match='appears twice'):
        check_auth_request({**CAPSULE_REQUEST, 'orderid': 1}, '2663192793', 1, 99, 1, 20188)


def test_wallets_and_sheet_entries_need_no_steam_call(tmp_path):
    service = _service(tmp_path)
    assert service.wallets() == [] and service.sheet_entries() == {}
    _planned(service)
    wallets = service.wallets()
    assert len(wallets) == 1 and wallets[0]['currency'] == 'USD'
    assert service.sheet_entries()['casket']['USD'] == 199
    assert service.exchange_rates() == RATES


def test_next_storage_names_go_on_from_the_highest_number():
    assert shop.next_storage_names(['Storage0', 'Storage 3', 'Main', None, 'storage2'], 2) == ['Storage 4', 'Storage 5']
    assert shop.next_storage_names([None, 'Cases'], 1) == ['Storage 1']
    assert shop.next_storage_names(['Storage1'], 0) == []


def test_new_storage_units_are_named_after_delivery(tmp_path):
    service = _service(tmp_path)
    ratatoskr = service.ratatoskr
    result = {}
    service._name_new_units(STEAMID, result)
    assert result == {} and not [c for c in ratatoskr.calls if c[0] == 'rename']
    # '10' is an older unnamed unit: only the units this purchase delivered are named (Ivan, 2026-11-04).
    ratatoskr.caskets += [{'item_id': '10', 'item_customname': None}, {'item_id': '11', 'item_customname': None},
                          {'item_id': '12', 'item_customname': ' '}]
    result = {'item_ids': ['11', '12']}
    service._name_new_units(STEAMID, result)
    assert [c for c in ratatoskr.calls if c[0] == 'rename'] == [('rename', '11', 'Storage 4'), ('rename', '12', 'Storage 5')]
    assert result == {'item_ids': ['11', '12'], 'named': ['Storage 4', 'Storage 5']}


def test_new_storage_units_are_not_named_while_items_move(tmp_path):
    service = _service(tmp_path)
    service.moves_in_progress = lambda steamid: True
    service.ratatoskr.caskets.append({'item_id': '11', 'item_customname': None})
    result = {'item_ids': ['11']}
    service._name_new_units(STEAMID, result)
    assert not [c for c in service.ratatoskr.calls if c[0] == 'rename']
    assert 'items are moving' in result['naming_error'] and 'Rename Storage Unit tab' in result['naming_error']


def test_a_failed_naming_never_fails_the_purchase(tmp_path):
    service = _service(tmp_path)
    ratatoskr = service.ratatoskr
    ratatoskr.caskets.append({'item_id': '11', 'item_customname': None})
    ratatoskr.rename_error = 'Rename timed out waiting for GC'
    result = {'ok': True, 'item_ids': ['11']}
    service._name_new_units(STEAMID, result)
    assert result['ok'] is True and 'named' not in result
    assert 'Rename timed out' in result['naming_error']


def test_a_delivered_unit_not_listed_yet_is_reported(tmp_path):
    service = _service(tmp_path)
    ratatoskr = service.ratatoskr
    ratatoskr.caskets.append({'item_id': '11', 'item_customname': None})
    result = {'item_ids': ['11', '99']}
    service._name_new_units(STEAMID, result)
    assert result['named'] == ['Storage 4']
    assert 'not in the Storage Unit list yet: 99' in result['naming_error']
