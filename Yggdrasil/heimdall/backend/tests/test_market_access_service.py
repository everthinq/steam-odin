"""Market access: Purchase History parsing, which rows count, the lapse date and
Steam's eligibility verdict. The page below copies the structure of Steam's real
Purchase History table (2026-10-29); every amount and game is made up."""
import error_handlers  # noqa: E402
import datetime
import time

from market_access_service import (STATE_LAPSED, STATE_OK, STATE_UNKNOWN, STATE_WARN, MarketAccessService,
                                   access_state, access_summary, counted_purchases, parse_date,
                                   parse_eligibility, parse_history)


def _row(date, items_html, kind, payment, total):
    return f'''
    <tr data-panel="{{&quot;focusable&quot;:true}}" role="button" class="wallet_table_row wallet_table_row_amt_change" onclick="x">
        <td class="wht_date">{date}</td>
        <td data-tooltip-text="Click" class="wht_items "">{items_html}</td>
        <td class="wht_type ">
            <div>{kind}</div>
            <div class="wth_payment">
                {payment}            </div>
        </td>
        <td class="wht_base_price"></td>
        <td class="wht_tax"></td>
        <td class="wht_shipping"></td>
        <td class="wht_total ">
            <div>{total}</div>
            <div class="wth_payment">Credit</div>
        </td>
    </tr>'''


def _items(*names):
    return ''.join(f'<div style="clear: both">{name}</div>' for name in names)


PAGE = '<table class="wallet_history_table"><tbody>' + ''.join([
    _row('2 Oct, 2026', 'Steam Community Market', '7 Market Transactions', 'Wallet', '$1.00 USD'),
    _row('2 Oct, 2026', _items('Game One', 'Game Two'), 'Purchase', 'Wallet', '$0.90 USD'),
    _row('12 Sep, 2026', _items('Counter-Strike 2 Storage Unit'), 'In-Game Purchase', 'Wallet', '$1.99 USD'),
    _row('23 Feb, 2026', _items('Refunded Game Refund'), 'Refund', 'Wallet Credit', '$0.45 USD'),
    _row('20 Feb, 2026', _items('Refunded Game'), 'Purchase', 'Wallet', '$0.45 USD'),
    _row('15 Feb, 2026', _items('Currency Conversion to USD'), 'Conversion', 'Wallet', ''),
    _row('15 Feb, 2026', _items('Purchased $5.00 USD Wallet Credit'), 'Purchase', 'Retail', '$5.00 USD'),
]) + '</tbody></table>'


def test_parse_date_both_orders():
    assert parse_date('2 Oct, 2026') == datetime.date(2026, 10, 2)
    assert parse_date('Oct 2, 2026') == datetime.date(2026, 10, 2)
    assert parse_date('yesterday') is None


def test_parse_history_reads_every_row():
    rows = parse_history(PAGE)
    assert [row['type'] for row in rows] == ['7 Market Transactions', 'Purchase', 'In-Game Purchase', 'Refund',
                                            'Purchase', 'Conversion', 'Purchase']
    assert rows[1] == {'date': '2026-10-02', 'type': 'Purchase', 'payment': 'Wallet',
                       'items': ['Game One', 'Game Two'], 'total': '$0.90 USD'}
    assert rows[0]['items'] == ['Steam Community Market']
    assert rows[6]['payment'] == 'Retail'


def test_parse_history_refuses_a_page_that_is_not_the_history():
    assert parse_history('<html>Sign in</html>') is None
    assert parse_history('<table class="wallet_history_table"></table>') == []


def test_store_and_in_game_purchases_count_market_transactions_and_refunds_do_not():
    counted = counted_purchases(parse_history(PAGE))
    assert [row['items'] for row in counted] == [['Game One', 'Game Two'], ['Counter-Strike 2 Storage Unit'],
                                                ['Purchased $5.00 USD Wallet Credit']]


def test_a_purchase_refunded_before_it_was_bought_again_still_counts():
    rows = parse_history('<table class="wallet_history_table">' + ''.join([
        _row('1 Mar, 2026', _items('Game'), 'Purchase', 'Wallet', '$1.00 USD'),
        _row('1 Feb, 2026', _items('Game Refund'), 'Refund', 'Wallet Credit', '$1.00 USD'),
    ]) + '</table>')
    assert len(counted_purchases(rows)) == 1


def test_access_lapses_a_year_after_the_newest_counted_purchase():
    summary = access_summary(parse_history(PAGE), datetime.date(2026, 10, 29))
    assert summary['last_purchase']['date'] == '2026-10-02'
    assert summary['access_until'] == '2027-10-02'
    assert summary['days_left'] == 338
    assert summary['opens_on'] is None


def test_an_in_game_purchase_keeps_the_market_open_a_market_transaction_does_not():
    rows = parse_history('<table class="wallet_history_table">'
                         + _row('20 Oct, 2026', 'Steam Community Market', '3 Market Transactions', 'Wallet', '$2.00 USD')
                         + _row('11 Mar, 2026', _items('Sticker Capsule'), 'In-Game Purchase', 'Wallet', '$5.00 USD')
                         + '</table>')
    summary = access_summary(rows, datetime.date(2026, 10, 29))
    assert summary['last_purchase']['items'] == ['Sticker Capsule'] and summary['access_until'] == '2027-03-11'


def test_market_transactions_alone_keep_nothing_open():
    rows = parse_history('<table class="wallet_history_table">'
                         + _row('20 Oct, 2026', 'Steam Community Market', 'Market Transaction', 'Wallet', '$2.00 USD')
                         + '</table>')
    assert access_summary(rows, datetime.date(2026, 10, 29))['access_until'] is None


def test_a_first_purchase_opens_the_market_after_seven_days():
    rows = parse_history('<table class="wallet_history_table">'
                         + _row('27 Oct, 2026', _items('Game'), 'Purchase', 'Wallet', '$1.00 USD') + '</table>')
    summary = access_summary(rows, datetime.date(2026, 10, 29))
    assert summary['opens_on'] == '2026-11-03'


def test_opens_on_ignores_a_purchase_older_than_a_year():
    rows = parse_history('<table class="wallet_history_table">'
                         + _row('27 Oct, 2026', _items('Game'), 'Purchase', 'Wallet', '$1.00 USD')
                         + _row('1 Sep, 2025', _items('Old game'), 'Purchase', 'Wallet', '$1.00 USD') + '</table>')
    assert access_summary(rows, datetime.date(2026, 10, 29))['opens_on'] == '2026-11-03'


def test_parse_eligibility_reads_the_cookie():
    cookie = ('%7B%22allowed%22%3A1%2C%22allowed_at_time%22%3A0%2C%22steamguard_required_days%22%3A15%2C'
              '%22new_device_cooldown_days%22%3A0%2C%22time_checked%22%3A1793298596%7D')
    verdict = parse_eligibility(cookie, 'https://steamcommunity.com/market/')
    assert verdict['allowed'] is True and verdict['allowed_at'] is None
    assert verdict['raw']['steamguard_required_days'] == 15


def test_parse_eligibility_falls_back_on_the_redirect():
    assert parse_eligibility(None, 'https://steamcommunity.com/market/')['allowed'] is True
    assert parse_eligibility(None, 'https://help.steampowered.com/en/faqs/view/451E-96B3-D194-50FC')['allowed'] is False
    assert parse_eligibility('%7B%22allowed%22%3A0%2C%22reason%22%3A2%7D', None)['reason'] == 2
    assert parse_eligibility(None, None)['allowed'] is None


def test_access_state():
    allowed, refused = {'allowed': True}, {'allowed': False}
    assert access_state({'days_left': 200}, allowed) == STATE_OK
    assert access_state({'days_left': 30}, allowed) == STATE_WARN
    assert access_state({'days_left': -1}, None) == STATE_LAPSED
    assert access_state({'days_left': 200}, refused) == STATE_LAPSED
    assert access_state({'days_left': None}, allowed) == STATE_WARN
    assert access_state(None, None) == STATE_UNKNOWN


class _Response:
    def __init__(self, text='', status=200, cookies=None, location=None):
        self.text, self.status_code, self.ok = text, status, status < 400
        self.cookies = cookies or {}
        self.headers = {'Location': location} if location else {}


class _Http:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class _Storage:
    def list_accounts(self):
        return ['1', '2']

    def load_account(self, steamid):
        return {'account_name': {'1': 'alpha', '2': 'beta'}[steamid]}


class _Steam:
    storage = _Storage()

    def web_session_cookie_for(self, steamid):
        return {'steamLoginSecure': 'x'} if steamid == '1' else None

    def ensure_fresh_session(self, steamid):
        return {'ok': False}


def _wait(service):
    for _ in range(200):
        if not service.status()['job']['running']:
            return
        time.sleep(0.01)


def test_check_all_accounts(tmp_path):
    http = _Http([_Response(PAGE), _Response(cookies={'webTradeEligibility': '%7B%22allowed%22%3A1%7D'},
                                             location='https://steamcommunity.com/market/')])
    service = MarketAccessService(_Steam(), state_path=str(tmp_path / 'market_access.json'), http=http,
                                  sleep=lambda seconds: None, today=lambda: datetime.date(2026, 10, 29))
    assert service.start_check()['started'] is True
    _wait(service)
    status = service.status()
    assert status['job']['done'] == 2 and status['job']['error'] is None
    alpha, beta = status['accounts']['1'], status['accounts']['2']
    assert alpha['state'] == STATE_OK and alpha['summary']['access_until'] == '2027-10-02'
    assert alpha['steam']['allowed'] is True
    assert http.calls[1][1]['allow_redirects'] is False
    assert beta['error'] == 'no Steam web session for this account' and beta['state'] == STATE_UNKNOWN
    reloaded = MarketAccessService(_Steam(), state_path=str(tmp_path / 'market_access.json'),
                                   today=lambda: datetime.date(2027, 9, 20))
    assert reloaded.status()['accounts']['1']['state'] == STATE_WARN   # days left move on


def test_a_rate_limit_stops_the_check(tmp_path):
    http = _Http([_Response(status=429)])
    service = MarketAccessService(_Steam(), state_path=str(tmp_path / 'market_access.json'), http=http,
                                  sleep=lambda seconds: None)
    service.start_check()
    _wait(service)
    status = service.status()
    assert 'HTTP 429' in status['job']['error'] and status['job']['done'] == 0
    assert http.calls and len(http.calls) == 1


# ---- Market unlocker ------------------------------------------------------------------------

from market_access_service import (UNLOCK_MAX_USD, approval_total, approval_transaction,  # noqa: E402
                                   check_unlock_page, unlock_purchase_listed)

TRANSACTION = '82900000000000001'
LOCATION = ('https://checkout.steampowered.com/checkout/approvetxn/' + TRANSACTION + '/?returnurl='
            'https%3A%2F%2Fstore.steampowered.com%2Fbuyitem%2F1723420%2Ffinalize%2F5140000000000001')
FINALIZE = 'https://store.steampowered.com/buyitem/1723420/finalize/5140000000000001?returnhost=store.steampowered.com'


def _approval_page(account='alpha', total='$0.01 USD', item='Editor Pack', quantity='1', billing=False,
                   authorize=True):
    """The structure of Steam's approval page for the Editor Pack (seen live 2026-10-29)."""
    return f'''<html><body>
    <form id="form_authtxn" action="https://checkout.steampowered.com/checkout/approvetxnsubmit" method="POST">
      <input type="hidden" name="transaction_id" value="{TRANSACTION}" />
      <input type="hidden" name="returnurl" value="{FINALIZE.replace('&', '&amp;')}" />
      <input type="hidden" name="sessionid" value="session" />
      <input type="hidden" name="approved" id="input_approved" value="0" />
    </form>
    <div class="review_steam_account_name">Steam account: {account}</div>
    <div>Payment method:</div><div>My Steam Wallet</div><div>($1.38 USD)</div>
    {'<div>You must enter a billing address to complete your purchase.</div>' if billing else ''}
    <div>Zombie Hunter</div><div>This game has requested authorization for the following transaction:</div>
    <div>QTY</div><div>PRICE</div><div>NAME</div>
    <div>{quantity}</div><div>{total}</div><div>{item}</div>
    <div>{total}</div><div>Total:</div>
    {'<a href="javascript:AuthorizeTransaction( true );">Authorize</a>' if authorize else ''}
    <a href="javascript:AuthorizeTransaction( false );">Cancel</a>
    </body></html>'''


def test_approval_transaction_from_the_redirect():
    assert approval_transaction(LOCATION) == TRANSACTION
    assert approval_transaction('https://store.steampowered.com/') is None
    assert approval_transaction(None) is None


def test_approval_total_reads_the_amount_before_total():
    assert approval_total(_approval_page(), 'USD') == 1
    assert approval_total(_approval_page(total='HK$ 0.08'), 'HKD') == 8
    assert approval_total(_approval_page(total='250₫'), 'VND') == 25000


def test_check_unlock_page_accepts_the_one_cent_editor_pack():
    form, total, usd, problem = check_unlock_page(_approval_page(), 'alpha', TRANSACTION, 'USD', {})
    assert problem is None and total == 1 and usd == 0.01
    action, fields = form
    assert action.endswith('/checkout/approvetxnsubmit') and fields['approved'] == '1'
    assert fields['returnurl'] == FINALIZE


def test_check_unlock_page_refuses():
    def problem(**kwargs):
        account = kwargs.pop('account_name', 'alpha')
        currency = kwargs.pop('currency', 'USD')
        rates = kwargs.pop('rates', {})
        return check_unlock_page(_approval_page(**kwargs), account, TRANSACTION, currency, rates)[3]
    assert 'billing address' in problem(billing=True)
    assert 'name this account' in problem(account_name='beta')
    assert 'Editor Pack' in problem(item='Gold Pack')
    assert 'exactly one item' in problem(quantity='2')
    assert 'above the' in problem(total='$0.99 USD')
    assert 'could not read the total' in problem(total='HK$ 0.08', currency='HKD', rates={})
    assert problem(total='HK$ 0.08', currency='HKD', rates={'HKD': 7.8}) is None
    assert 'Authorize' in problem(authorize=False)
    assert UNLOCK_MAX_USD == 0.05


def test_unlock_purchase_listed():
    rows = parse_history('<table class="wallet_history_table">'
                         + _row('29 Oct, 2026', _items('Zombie Hunter', 'Editor Pack'), 'In-Game Purchase', 'Wallet',
                                '$0.01 USD') + '</table>')
    assert unlock_purchase_listed(rows, datetime.date(2026, 10, 29))
    assert unlock_purchase_listed(rows, datetime.date(2026, 10, 30))
    assert not unlock_purchase_listed(rows, datetime.date(2026, 11, 2))
    assert not unlock_purchase_listed(parse_history(PAGE), datetime.date(2026, 10, 29))


class _Rates:
    def exchange_rates(self):
        return {}


class _UnlockHttp:
    """A fake Steam for one account (alpha, steamid 1): records every call."""

    def __init__(self, owned=False, page=None, listed=True):
        self.owned, self.page, self.listed, self.calls = owned, page or _approval_page(), listed, []

    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs.get('data')))
        if 'dynamicstore/userdata' in url:
            return _Json({'rgOwnedApps': [440] + ([1723420] if self.owned else [])})
        if url.startswith('https://steamcommunity.com/market/eligibilitycheck'):
            return _Response(cookies={'webTradeEligibility': '%7B%22allowed%22%3A1%7D'},
                             location='https://steamcommunity.com/market/')
        if url.startswith('https://steamcommunity.com/market/'):
            return _Response('<script>var g_rgWalletInfo = {"wallet_currency":1,"wallet_balance":"138"};</script>')
        if '/buyitem/1723420/330/1' in url:
            return _Response('', 302, location=LOCATION)
        if url.startswith('https://checkout.steampowered.com/checkout/approvetxn/'):
            return _Response(self.page)
        if url.startswith('https://store.steampowered.com/buyitem/1723420/finalize/'):
            return _Response('<html>Thank you</html>')
        if 'account/history' in url:
            row = (_row('29 Oct, 2026', _items('Zombie Hunter', 'Editor Pack'), 'In-Game Purchase', 'Wallet',
                        '$0.01 USD') if self.listed else '')
            return _Response('<table class="wallet_history_table">' + row + '</table>')
        raise AssertionError(url)

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs.get('data')))
        if 'addfreelicense' in url:
            self.owned = True
            return _Response('{}')
        if url.endswith('/checkout/approvetxnsubmit'):
            return _Response('', 200)
        raise AssertionError(url)

    def posts(self, fragment):
        return [data for method, url, data in self.calls if method == 'POST' and fragment in url]


class _Json(_Response):
    def __init__(self, data):
        super().__init__('')
        self._data = data

    def json(self):
        return self._data


def _unlocker(tmp_path, http):
    return MarketAccessService(_Steam(), card_deals_service=_Rates(), state_path=str(tmp_path / 'market_access.json'),
                               http=http, sleep=lambda seconds: None, today=lambda: datetime.date(2026, 10, 29))


import threading  # noqa: E402

from market_access_service import (MarketAccessError, advice, describe_unlock, headline,  # noqa: E402
                                   purchase_help)
from commands import CommandError  # noqa: E402


def _finish(service, job_id):
    """Wait until a job of the service has finished; answers it."""
    for _ in range(500):
        job = service.jobs.get(job_id)
        if job and not job['running']:
            return job
        time.sleep(0.01)
    raise AssertionError('the job did not finish')


def _dry_run(service, steamids=('1',)):
    """A plan: its dry run, finished."""
    plan = service.plan_unlock(list(steamids))
    assert plan['state'] == 'dry run' and plan['executable'] is False
    job = _finish(service, plan['dry_run_job_id'])
    assert job['result']['plan_id'] == plan['plan_id']
    return service.get_plan(plan['plan_id'])


def _refusal(call):
    try:
        call()
    except CommandError as error:
        return error.code
    raise AssertionError('not refused')


def test_unlock_dry_run_claims_the_game_and_cancels(tmp_path):
    http = _UnlockHttp()
    service = _unlocker(tmp_path, http)
    plan = _dry_run(service)
    entry = service.status()['unlocks'][0]
    assert entry['ok'] and entry['dry_run'] and not entry['approved'] and not entry['paid'], entry
    assert entry['claimed_game'] and entry['usd'] == 0.01
    assert http.posts('addfreelicense/617273')
    assert [data['approved'] for data in http.posts('approvetxnsubmit')] == ['0']
    assert not [call for call in http.calls if call[1].startswith('https://store.steampowered.com/buyitem/1723420/finalize/')]
    assert plan['state'] == 'ready' and plan['executable'] and plan['passed'] == 1 and plan['expires_at']
    assert plan['real_purchase'] == {'lines': [{'account_name': 'alpha', 'buys': 'Zombie Hunter: Editor Pack',
                                                'usd': 0.01}], 'usd_total': 0.01}
    assert plan['accounts'] == [{'steamid': '1', 'account_name': 'alpha', 'passed': True, 'reason': None,
                                 'usd': 0.01, 'total': 1, 'currency': 'USD', 'claimed_game': True,
                                 'max_usd': UNLOCK_MAX_USD}]


def test_executing_a_plan_buys_only_what_passed_and_checks_the_history(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    plan = _dry_run(service, ['1', '2'])          # beta has no web session: it fails the dry run
    assert [account['passed'] for account in plan['accounts']] == [True, False]
    assert plan['accounts'][1]['reason'] == 'no Steam web session for this account'
    job = service.execute_plan(plan['plan_id'], dry_run=False)
    assert job['kind'] == 'market-access-unlock' and job['total'] == 1 and job['plan_id'] == plan['plan_id']
    job = _finish(service, job['id'])
    assert job['done'] == 1 and job['failed'] == 0
    assert [(account['account_name'], account['kind'], account['paid']) for account in job['result']['accounts']] \
        == [('alpha', 'bought', True)]
    status = service.status()
    entry = status['unlocks'][0]
    assert entry['ok'] and entry['paid'] and entry['step'] == 'bought' and entry['plan_id'] == plan['plan_id'], entry
    assert not http.posts('addfreelicense')
    assert [data['approved'] for data in http.posts('approvetxnsubmit')] == ['0', '1']
    assert [call for call in http.calls if call[1] == FINALIZE]
    assert status['accounts']['1']['summary']['last_purchase']['items'] == ['Zombie Hunter', 'Editor Pack']
    assert status['last_unlocks']['1']['kind'] == 'bought'
    assert service.get_plan(plan['plan_id'])['executable'] is False


def test_executing_twice_answers_the_same_job_and_buys_once(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    plan = _dry_run(service)
    first = service.execute_plan(plan['plan_id'], dry_run=False)
    second = service.execute_plan(plan['plan_id'], dry_run=False)
    assert first['id'] == second['id']
    _finish(service, first['id'])
    assert service.execute_plan(plan['plan_id'], dry_run=False)['id'] == first['id']
    assert [data['approved'] for data in http.posts('approvetxnsubmit')] == ['0', '1']


def test_execute_fails_closed_unless_dry_run_is_exactly_false(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    plan = _dry_run(service)
    calls = len(http.calls)
    for flag in (None, True, 'false', 0, '', [], {}):
        assert _refusal(lambda: service.execute_plan(plan['plan_id'], dry_run=flag)) == 'real_purchase_only'
    assert len(http.calls) == calls                    # nothing was sent to Steam
    assert service.get_plan(plan['plan_id'])['executable'] is True   # and the plan is still there


def test_an_expired_or_unknown_plan_is_refused(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    plan = _dry_run(service)
    service._plans[plan['plan_id']]['expires_at'] = time.time() - 1
    assert service.get_plan(plan['plan_id'])['executable'] is False
    assert _refusal(lambda: service.execute_plan(plan['plan_id'], dry_run=False)) == 'plan_expired'
    assert _refusal(lambda: service.execute_plan('no-such-plan', dry_run=False)) == 'plan_unknown'
    assert _refusal(lambda: service.get_plan('no-such-plan')) == 'plan_unknown'
    assert [data['approved'] for data in http.posts('approvetxnsubmit')] == ['0']


def test_a_billing_address_page_fails_the_dry_run_and_nothing_can_be_bought(tmp_path):
    http = _UnlockHttp(owned=True, page=_approval_page(billing=True))
    service = _unlocker(tmp_path, http)
    plan = _dry_run(service)
    entry = service.status()['unlocks'][0]
    assert not entry['ok'] and 'billing address' in entry['error'] and not entry['approved']
    assert plan['accounts'][0]['passed'] is False and 'billing address' in plan['accounts'][0]['reason']
    assert plan['executable'] is False
    assert _refusal(lambda: service.execute_plan(plan['plan_id'], dry_run=False)) == 'nothing_passed'
    assert [data['approved'] for data in http.posts('approvetxnsubmit')] == ['0']


def test_the_real_run_keeps_every_guard(tmp_path):
    """The approval page changed between the dry run and the real run: refused and cancelled."""
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    plan = _dry_run(service)
    http.page = _approval_page(total='$0.99 USD')
    _finish(service, service.execute_plan(plan['plan_id'], dry_run=False)['id'])
    entry = service.status()['unlocks'][0]
    assert not entry['ok'] and 'above the $0.05 limit' in entry['error'] and not entry['approved']
    assert [data['approved'] for data in http.posts('approvetxnsubmit')] == ['0', '0']
    assert service.status()['last_unlocks']['1']['kind'] == 'failed'


def test_unlock_not_in_the_history_reads_may_be_paid(tmp_path):
    http = _UnlockHttp(owned=True, listed=False)
    service = _unlocker(tmp_path, http)
    plan = _dry_run(service)
    job = _finish(service, service.execute_plan(plan['plan_id'], dry_run=False)['id'])
    entry = service.status()['unlocks'][0]
    assert entry['approved'] and not entry['paid'] and 'MAY BE PAID' in entry['error']
    assert job['failed'] == 1 and job['result']['accounts'][0]['kind'] == 'may_be_paid'
    assert service.status()['last_unlocks']['1']['kind'] == 'may_be_paid'


def test_a_plan_is_not_executable_while_its_dry_run_runs(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    gate = threading.Event()
    original = service._wallet_currency

    def slow(cookies):
        gate.wait(5)
        return original(cookies)
    service._wallet_currency = slow
    plan = service.plan_unlock(['1'])
    assert _refusal(lambda: service.execute_plan(plan['plan_id'], dry_run=False)) == 'plan_not_ready'
    assert _refusal(lambda: service.plan_unlock(['1'])) == 'busy'
    gate.set()
    _finish(service, plan['dry_run_job_id'])
    assert service.get_plan(plan['plan_id'])['executable'] is True


def test_a_newer_dry_run_replaces_an_older_plan_for_the_same_account(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    first = _dry_run(service, ['1'])
    second = _dry_run(service, ['1'])
    assert service.get_plan(first['plan_id'])['state'] == 'replaced'
    assert service.get_plan(first['plan_id'])['executable'] is False
    assert _refusal(lambda: service.execute_plan(first['plan_id'], dry_run=False)) == 'plan_replaced'
    assert second['executable'] is True


def test_an_expired_plan_reads_expired_and_its_dry_run_is_no_longer_ready(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    plan = _dry_run(service)
    assert service.status()['last_unlocks']['1']['text'].startswith('ready: dry run passed')
    service._plans[plan['plan_id']]['expires_at'] = time.time() - 1
    described = service.get_plan(plan['plan_id'])
    assert described['state'] == 'expired' and described['executable'] is False
    assert _refusal(lambda: service.execute_plan(plan['plan_id'], dry_run=False)) == 'plan_expired'
    assert 'its plan can no longer be bought' in service.status()['last_unlocks']['1']['text']


def test_a_failure_while_finishing_the_dry_run_marks_the_plan_failed(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)

    def broken(plan, error):
        raise ValueError('boom')
    service._finish_dry_run = broken
    plan = service.plan_unlock(['1'])
    _finish(service, plan['dry_run_job_id'])
    described = service.get_plan(plan['plan_id'])
    assert described['state'] == 'failed' and described['executable'] is False
    assert _refusal(lambda: service.execute_plan(plan['plan_id'], dry_run=False)) == 'plan_failed'


def test_cleanup_keeps_a_plan_whose_job_still_runs(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    plan = _dry_run(service, ['1'])
    running = service.jobs.create('market-access-unlock', {'total': 1})   # a purchase still running
    stored = service._plans[plan['plan_id']]
    stored.update(job_id=running['id'], created_at=stored['created_at'] - 2 * 60 * 60)
    other = _dry_run(service, ['2'])
    assert plan['plan_id'] in service._plans
    service.jobs.finish(running['id'])
    _dry_run(service, ['2'])
    assert plan['plan_id'] not in service._plans and other['plan_id'] in service._plans


def test_a_job_that_cannot_start_leaves_the_service_unlocked(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    original = service.jobs.create

    def broken(*arguments, **keywords):
        raise RuntimeError('registry broken')
    service.jobs.create = broken
    try:
        service.plan_unlock(['1'])
    except RuntimeError:
        pass
    service.jobs.create = original
    assert len(service._plans) == 0
    assert service._job._busy.acquire(blocking=False)
    service._job._busy.release()


def test_a_malformed_saved_allowed_at_does_not_break_the_headline():
    result = {'summary': None, 'steam': {'allowed': False, 'allowed_at': 'not a number'}}
    assert headline(result) == 'No Market access (Steam says so)'


def test_an_unexpected_error_is_recorded_and_the_next_account_still_runs(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    calls = []

    def broken(*arguments):
        calls.append(1)
        raise ValueError('bad wallet JSON')
    service._wallet_currency = broken
    plan = _dry_run(service, ['1', '2'])
    by_account = {entry['steamid']: entry for entry in service.status()['unlocks']}
    assert calls == [1]
    assert by_account['1']['error'] == 'unexpected error (ValueError)' and by_account['1']['state'] == 'done'
    assert by_account['2']['state'] == 'done'           # the job went on to the next account
    assert plan['passed'] == 0


def test_a_rate_limit_stops_the_dry_run_and_the_rest_reads_not_tried(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)

    def limited(*arguments):
        raise MarketAccessError('Steam rate-limited the request (HTTP 429): try again later')
    service._wallet_currency = limited
    plan = _dry_run(service, ['1', '2'])
    assert 'HTTP 429' in plan['error']
    assert 'HTTP 429' in plan['accounts'][0]['reason']
    assert plan['accounts'][1]['reason'].startswith('not tried: the dry run stopped')


def test_unlock_needs_a_known_account(tmp_path):
    assert _refusal(lambda: _unlocker(tmp_path, _UnlockHttp()).plan_unlock(['999'])) == 'no_accounts'


def test_an_interrupted_unlock_after_approving_reads_may_be_paid(tmp_path):
    import json as json_module
    path = tmp_path / 'market_access.json'
    path.write_text(json_module.dumps({'unlocks': [{'state': 'in progress', 'approved': True}]}))
    service = MarketAccessService(_Steam(), state_path=str(path))
    assert 'MAY BE PAID' in service.status()['unlocks'][0]['error']


# ---- what the pages print -------------------------------------------------------------------

def test_headline_advice_and_whether_a_purchase_would_help():
    def result(state, days_left=None, until=None, steam=None, opens_on=None, error=None, summary=True):
        return {'state': state, 'error': error, 'steam': steam,
                'summary': {'days_left': days_left, 'access_until': until, 'opens_on': opens_on} if summary else None}

    ok = result(STATE_OK, 200, '2027-05-01', {'allowed': True})
    assert headline(ok) == 'Market until 1 May 2027 · 200 days' and advice(ok) is None
    assert purchase_help(ok) == (False, 'the Market stays open 200 more days')

    warn = result(STATE_WARN, 12, '2026-11-13', {'allowed': True})
    assert headline(warn) == 'Market until 13 Nov 2026 · 12 days'
    assert advice(warn).startswith('buy something')
    assert purchase_help(warn) == (True, 'Market access lapses in 12 days')

    lapsed = result(STATE_LAPSED, -3, '2026-10-29')
    assert headline(lapsed) == 'Market access lapsed 29 Oct 2026'
    assert purchase_help(lapsed) == (True, 'Market access lapsed 29 Oct 2026')

    nothing = result(STATE_WARN, steam={'allowed': True})
    assert headline(nothing) == 'No counted purchase on the first page of Purchase History'
    assert purchase_help(nothing) == (True, 'no counted purchase keeps the Market open')

    blocked = result(STATE_LAPSED, 200, '2027-05-01', {'allowed': False, 'allowed_at': 1798761600})
    assert headline(blocked) == 'No Market access (Steam) until 1 Jan 2027'
    assert purchase_help(blocked)[0] is False and 'another reason' in purchase_help(blocked)[1]
    assert headline(result(STATE_LAPSED, steam={'allowed': False})) == 'No Market access (Steam says so)'

    opening = result(STATE_LAPSED, 362, '2027-10-27', {'allowed': False}, opens_on='2026-11-04')
    assert purchase_help(opening) == (False, 'a purchase under 7 days old opens the Market on 4 Nov 2026')
    assert headline(result(STATE_OK, 362, '2027-10-27', opens_on='2026-11-04')) == 'Market opens 4 Nov 2026'

    broken = result(STATE_UNKNOWN, error='no Steam web session for this account', summary=False)
    assert headline(broken) == 'Not checked: no Steam web session for this account'
    assert purchase_help(broken) == (False, 'not checked yet')


def test_describe_unlock():
    assert describe_unlock({'state': 'in progress', 'step': 'wallet'})['text'] == 'wallet…'
    assert describe_unlock({'state': 'done', 'ok': True, 'paid': True})['kind'] == 'bought'
    assert describe_unlock({'state': 'done', 'ok': True, 'usd': 0.01})['text'] == 'ready: dry run passed ($0.0100)'
    assert describe_unlock({'state': 'done', 'ok': False, 'error': 'no wallet'}) \
        == {'kind': 'failed', 'text': 'no wallet', 'at': None, 'dry_run': None, 'plan_id': None}
    assert describe_unlock({'state': 'done', 'approved': True, 'error': 'x MAY BE PAID'})['kind'] == 'may_be_paid'


def test_status_orders_the_banner_and_says_what_to_tick(tmp_path):
    import json as json_module
    path = tmp_path / 'market_access.json'

    def account(name, access_until, allowed=True, error=None, state='unknown'):
        summary = {'access_until': access_until, 'days_left': None, 'opens_on': None,
                   'last_purchase': None} if access_until else None
        return {'account_name': name, 'summary': summary, 'steam': {'allowed': allowed} if allowed is not None else None,
                'error': error, 'state': state}
    path.write_text(json_module.dumps({
        'accounts': {'1': account('alpha', '2026-11-20'), '2': account('beta', '2027-06-01'),
                     '3': account('gamma', None, state='warn'), '4': account('delta', '2026-11-05'),
                     '5': account('eve', None, allowed=None, error='no Steam web session')},
        'unlocks': [{'steamid': '1', 'state': 'done', 'ok': True, 'usd': 0.01, 'at': 1},
                    {'steamid': '1', 'state': 'done', 'ok': False, 'error': 'newer', 'at': 2}]}))
    service = MarketAccessService(_Steam(), state_path=str(path), today=lambda: datetime.date(2026, 11, 1))
    status = service.status()
    assert status['flagged'] == ['3', '4', '1']           # nothing keeps it open, then soonest first
    assert status['not_checked'] == ['5']
    assert {steamid: result['purchase_would_help'] for steamid, result in status['accounts'].items()} \
        == {'1': True, '2': False, '3': True, '4': True, '5': False}
    assert status['accounts']['4']['headline'] == 'Market until 5 Nov 2026 · 4 days'
    assert status['accounts']['4']['advice'].startswith('buy something')
    assert status['last_unlocks'] == {'1': {'kind': 'failed', 'text': 'newer', 'at': 2, 'dry_run': None,
                                            'plan_id': None}}


# ---- routes ---------------------------------------------------------------------------------

def _client(service):
    from flask import Flask

    from routes.mimir import bp
    app = Flask(__name__)
    error_handlers.install(app)
    app.register_blueprint(bp)
    return app.test_client()


def test_the_unlocker_routes_are_plan_then_execute(tmp_path):
    from context import ctx
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    previous, ctx.market_access_service = ctx.market_access_service, service
    try:
        client = _client(service)
        assert client.post('/api/mimir/market-access/unlock', json={'steamids': ['1'], 'dry_run': False}).status_code \
            in (404, 405)                                  # no bare purchase without a plan any more
        assert client.post('/api/mimir/market-access/unlock/plan', json={}).get_json()['code'] == 'bad_request'
        answer = client.post('/api/mimir/market-access/unlock/plan', json={'steamids': ['1']})
        assert answer.status_code == 202
        plan = answer.get_json()
        _finish(service, plan['dry_run_job_id'])
        assert client.get(f'/api/mimir/market-access/plans/{plan["plan_id"]}').get_json()['executable'] is True
        execute = f'/api/mimir/market-access/plans/{plan["plan_id"]}/execute'
        for body in ({}, {'dry_run': True}, {'dry_run': 'false'}, {'dry_run': None}):
            refused = client.post(execute, json=body)
            assert refused.status_code == 400 and refused.get_json()['code'] == 'real_purchase_only'
        refused = client.post(execute, data='not json', content_type='text/plain')
        assert refused.get_json()['code'] == 'real_purchase_only'
        first = client.post(execute, json={'dry_run': False})
        second = client.post(execute, json={'dry_run': False})
        assert first.status_code == 202 and first.get_json()['id'] == second.get_json()['id']
        _finish(service, first.get_json()['id'])
        unknown = client.post('/api/mimir/market-access/plans/nope/execute', json={'dry_run': False})
        assert unknown.status_code == 404 and unknown.get_json() == {'error': 'Unknown plan (make a new dry run)',
                                                                     'code': 'plan_unknown'}
        assert client.get('/api/mimir/market-access/plans/nope').status_code == 404
    finally:
        ctx.market_access_service = previous
    assert [data['approved'] for data in http.posts('approvetxnsubmit')] == ['0', '1']
