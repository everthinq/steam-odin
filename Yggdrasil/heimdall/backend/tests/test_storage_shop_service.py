"""Ratatoskr Storage shop: price sheet decoding, plan, approval page checks and the
guarded purchase (fake Steam, fake Ratatoskr, fake web)."""
import base64
import lzma
import re
import struct
import time

import pytest

import storage_shop_service as shop
from storage_shop_service import (ShopError, StorageShopService, amount_shown, approval_form,
                                  check_auth_request, decode_auth_request, decode_price_sheet, plan_account, shop_statistics, steam_error,
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
])
def test_amount_shown(text, minor_units, currency, shown):
    assert amount_shown(text, minor_units, currency) is shown


# ---- purchase (fakes) --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, text='', status_code=200, headers=None):
        self.text, self.status_code, self.headers = text, status_code, headers or {}

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return {}


class FakeHttp:
    def __init__(self, page=PAGE, wallet=(1, 759)):
        self.page, self.wallet = page, wallet
        self.gets, self.posts = [], []

    def get(self, url, **kwargs):
        self.gets.append(url)
        if 'steamcommunity.com/market' in url:
            return FakeResponse(f'var g_rgWalletInfo = {{"wallet_currency":{self.wallet[0]},'
                                f'"wallet_balance":"{self.wallet[1]}"}};')
        if 'approvetxn' in url:
            return FakeResponse(self.page)
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

    def store_purchase_init(self, steamid, country, currency, quantity, unit_price):
        self.calls.append(('init', country, currency, quantity, unit_price))
        if country in self.refused_countries:
            return {'success': False, 'result': 8, 'transactionId': '0'}
        if self.init_result != 1:
            return {'success': False, 'result': self.init_result, 'transactionId': '0', 'url': ''}
        message = {'lineitems': {'0': {'description': 'Storage Unit', 'gameitemid': 1201,
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


def _planned(service):
    service._build_plan(None)
    return service.status()['plan']


def _order(service, quantity=2):
    row = _planned(service)['accounts'][0]
    return {**row, 'quantity': quantity}


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
    assert service.status()['plan']['accounts'][0]['status'] == 'bought'


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
    assert service.status()['plan']['accounts'][0]['status'] == 'check'


def test_an_open_session_is_kept(tmp_path):
    ratatoskr = FakeRatatoskr()
    service = _service(tmp_path, ratatoskr=ratatoskr)
    order = _order(service)
    ratatoskr.calls.clear()
    ratatoskr.connected = True
    service._buy_account(order, dry_run=True)
    assert 'login' not in ratatoskr.calls and 'disconnect' not in ratatoskr.calls


def test_start_purchase_guards(tmp_path):
    service = _service(tmp_path)
    plan = _planned(service)
    created = plan['created_at']
    assert 'check the accounts first' in StorageShopService(
        FakeSteam(), FakeRatatoskr(), FakeCardDeals(), state_path=str(tmp_path / 'other.json'),
        http=FakeHttp()).start_purchase([{'steamid': STEAMID, 'quantity': 1}])['error']
    assert '1 to 3' in service.start_purchase([{'steamid': STEAMID, 'quantity': 4}], plan_created_at=created)['error']
    assert 'selected twice' in service.start_purchase(
        [{'steamid': STEAMID, 'quantity': 1}] * 2, plan_created_at=created)['error']
    assert 'plan changed' in service.start_purchase([{'steamid': STEAMID, 'quantity': 1}],
                                                    plan_created_at=created - 5)['error']
    assert 'plan changed' in service.start_purchase([{'steamid': STEAMID, 'quantity': 1}])['error']   # time required
    service._plan['created_at'] = created = time.time() - shop.PLAN_TIME_TO_LIVE_SECONDS - 1
    assert 'older than 30 minutes' in service.start_purchase([{'steamid': STEAMID, 'quantity': 1}],
                                                             plan_created_at=created)['error']


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
    assert service.status()['plan']['accounts'][0]['status'] == 'bought'
    assert service.start_deliver_again(entry['at'])['error'] == 'no unclear purchase with that time'


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
    """The page saved by the everthinklol dry run (2026-10-03), when present locally."""
    import os
    path = os.path.join(os.path.dirname(__file__), '..', 'cache', 'storage_shop_approval_page.html')
    if not os.path.exists(path) or 'form_authtxn' not in open(path, encoding='utf-8').read():
        pytest.skip('no saved approval page')
    page = open(path, encoding='utf-8').read()
    identifier = re.search(r'name="transaction_id" value="(\d+)"', page).group(1)
    action, fields = approval_form(page, identifier)
    assert action.endswith('/checkout/approvetxnsubmit') and fields['approved'] == '1'
    assert amount_shown(page, 199, 'USD')
