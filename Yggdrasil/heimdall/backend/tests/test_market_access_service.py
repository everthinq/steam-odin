"""Market access: Purchase History parsing, which rows count, the lapse date and
Steam's eligibility verdict. The page below copies the structure of Steam's real
Purchase History table (2026-10-29); every amount and game is made up."""
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


def test_unlock_dry_run_claims_the_game_and_cancels(tmp_path):
    http = _UnlockHttp()
    service = _unlocker(tmp_path, http)
    assert service.start_unlock(['1'])['started']
    _wait(service)
    entry = service.status()['unlocks'][0]
    assert entry['ok'] and entry['dry_run'] and not entry['approved'] and not entry['paid'], entry
    assert entry['claimed_game'] and entry['usd'] == 0.01
    assert http.posts('addfreelicense/617273')
    assert [data['approved'] for data in http.posts('approvetxnsubmit')] == ['0']
    assert not [call for call in http.calls if call[1].startswith('https://store.steampowered.com/buyitem/1723420/finalize/')]


def test_unlock_for_real_approves_finalizes_and_checks_the_history(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    service.start_unlock(['1'], dry_run=False)
    _wait(service)
    status = service.status()
    entry = status['unlocks'][0]
    assert entry['ok'] and entry['paid'] and entry['step'] == 'bought', entry
    assert not http.posts('addfreelicense')
    assert [data['approved'] for data in http.posts('approvetxnsubmit')] == ['1']
    assert [call for call in http.calls if call[1] == FINALIZE]
    assert status['accounts']['1']['summary']['last_purchase']['items'] == ['Zombie Hunter', 'Editor Pack']


def test_unlock_refuses_a_billing_address_page_and_cancels(tmp_path):
    http = _UnlockHttp(owned=True, page=_approval_page(billing=True))
    service = _unlocker(tmp_path, http)
    service.start_unlock(['1'], dry_run=False)
    _wait(service)
    entry = service.status()['unlocks'][0]
    assert not entry['ok'] and 'billing address' in entry['error'] and not entry['approved']
    assert [data['approved'] for data in http.posts('approvetxnsubmit')] == ['0']


def test_unlock_not_in_the_history_reads_may_be_paid(tmp_path):
    http = _UnlockHttp(owned=True, listed=False)
    service = _unlocker(tmp_path, http)
    service.start_unlock(['1'], dry_run=False)
    _wait(service)
    entry = service.status()['unlocks'][0]
    assert entry['approved'] and not entry['paid'] and 'MAY BE PAID' in entry['error']


def test_an_unexpected_error_is_recorded_and_the_next_account_still_runs(tmp_path):
    http = _UnlockHttp(owned=True)
    service = _unlocker(tmp_path, http)
    calls = []

    def broken(*arguments):
        calls.append(1)
        raise ValueError('bad wallet JSON')
    service._wallet_currency = broken
    service.start_unlock(['1', '2'], dry_run=True)
    _wait(service)
    by_account = {entry['steamid']: entry for entry in service.status()['unlocks']}
    assert calls == [1]
    assert by_account['1']['error'] == 'unexpected error (ValueError)' and by_account['1']['state'] == 'done'
    assert by_account['2']['state'] == 'done'           # the job went on to the next account


def test_unlock_needs_a_known_account(tmp_path):
    assert _unlocker(tmp_path, _UnlockHttp()).start_unlock(['999']) == {'started': False,
                                                                        'error': 'no known account chosen'}


def test_an_interrupted_unlock_after_approving_reads_may_be_paid(tmp_path):
    import json as json_module
    path = tmp_path / 'market_access.json'
    path.write_text(json_module.dumps({'unlocks': [{'state': 'in progress', 'approved': True}]}))
    service = MarketAccessService(_Steam(), state_path=str(path))
    assert 'MAY BE PAID' in service.status()['unlocks'][0]['error']


def test_the_unlock_route_is_a_dry_run_unless_told_otherwise():
    from flask import Flask

    from context import ctx
    from routes.mimir import bp

    calls = []

    class _Service:
        def start_unlock(self, steamids, dry_run=True):
            calls.append((steamids, dry_run))
            return {'started': True}
    app = Flask(__name__)
    app.register_blueprint(bp)
    previous, ctx.market_access_service = ctx.market_access_service, _Service()
    try:
        client = app.test_client()
        assert client.post('/api/mimir/market-access/unlock', json={'steamids': ['1']}).status_code == 200
        client.post('/api/mimir/market-access/unlock', json={'steamids': ['1'], 'dry_run': 'false'})
        client.post('/api/mimir/market-access/unlock', json={'steamids': ['1'], 'dry_run': False})
        assert client.post('/api/mimir/market-access/unlock', json={}).status_code == 400
    finally:
        ctx.market_access_service = previous
    assert [dry_run for _, dry_run in calls] == [True, True, False]
