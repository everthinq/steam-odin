"""CSFloat buy-order sweep pacing: one request per item while its remembered
listing is still up, CSFloat's own retry time (or one hour) after a rate limit,
and the most valuable items first with few-cent items refreshed only weekly.

Everything is faked: no CSFloat, pulse or file outside tmp_path is touched (the
key-cooldown state lives in tmp_path too, so the real keys are never benched)."""
import email.utils
import io
import json
import time
import urllib.error
from datetime import datetime, timedelta, timezone

import huginn_service
from huginn_service import CSFloatKeyManager, HuginnService, _CSFloatRateLimited, _retry_after_seconds


def _service(monkeypatch, tmp_path, scan=None, prices=None):
    monkeypatch.setattr(huginn_service, 'load_csfloat_keys', lambda: [{'label': 'k1', 'key': 'K'}])
    monkeypatch.setattr(huginn_service, 'load_csfloat_proxy', lambda: '')
    monkeypatch.setattr(huginn_service, 'CSFLOAT_BUYORDERS_CACHE', str(tmp_path / 'buy_orders.json'))
    monkeypatch.setattr(huginn_service, 'CSFLOAT_ITEM_LINKS_FILE', str(tmp_path / 'item_links.json'))
    monkeypatch.setattr(huginn_service, '_CSFLOAT_REQUEST_DELAY', 0)
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    service.csfloat_keys = CSFloatKeyManager(state_path=str(tmp_path / 'key_state.json'))
    monkeypatch.setattr(service, 'get_cache', lambda: {'by_hash': scan or {}})
    monkeypatch.setattr(service, 'price_map', lambda token, market: prices or {})
    return service


def _write_previous(tmp_path, by_name, listing_ids=None, swept_at=None):
    (tmp_path / 'buy_orders.json').write_text(json.dumps({
        'by_name': by_name, 'processed': sorted(by_name), 'complete': True,
        'updated_at': '2020-01-01T00:00:00+00:00', 'started_at': '2020-01-01T00:00:00+00:00',
    }))
    names = set(listing_ids or {}) | set(swept_at or {})
    (tmp_path / 'item_links.json').write_text(json.dumps({
        name: {'listing_id': (listing_ids or {}).get(name), 'link': None,
               'checked_at': (swept_at or {}).get(name)} for name in names}))


def _links(tmp_path):
    return json.loads((tmp_path / 'item_links.json').read_text())


class Calls:
    """Fake CSFloat: listings per item, buy orders per listing, and a request log."""

    def __init__(self, listings, orders, gone=()):
        self.listings, self.orders, self.gone = listings, orders, set(gone)
        self.log = []

    def find(self, service, key, name, proxy=None):
        self.log.append(('find', name))
        return self.listings.get(name)

    def top(self, service, key, listing_id, proxy=None):
        self.log.append(('orders', listing_id))
        if listing_id in self.gone:
            raise urllib.error.HTTPError('u', 404, 'not found', {}, io.BytesIO(b''))
        return self.orders.get(listing_id)


def _patch_calls(monkeypatch, calls):
    monkeypatch.setattr(HuginnService, '_csfloat_find_listing_id', lambda self, *a, **k: calls.find(self, *a, **k))
    monkeypatch.setattr(HuginnService, '_csfloat_top_buy_order', lambda self, *a, **k: calls.top(self, *a, **k))


# ---- 1. one request per item while the remembered listing is up --------------

def test_remembered_listing_costs_one_request(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    _write_previous(tmp_path, {'A': {'price': 1.0, 'qty': 1}}, listing_ids={'A': 'L-A'})
    calls = Calls(listings={'A': 'L-A'}, orders={'L-A': {'price': 1.5, 'qty': 3}})
    _patch_calls(monkeypatch, calls)
    result = service.fetch_csfloat_buy_orders(token=None, names=['A'])
    assert calls.log == [('orders', 'L-A')]
    assert result['by_name']['A'] == {'price': 1.5, 'qty': 3}


def test_sold_listing_is_looked_up_again(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    _write_previous(tmp_path, {'A': {'price': 1.0, 'qty': 1}}, listing_ids={'A': 'L-old'})
    calls = Calls(listings={'A': 'L-new'}, orders={'L-new': {'price': 2.0, 'qty': 1}}, gone={'L-old'})
    _patch_calls(monkeypatch, calls)
    result = service.fetch_csfloat_buy_orders(token=None, names=['A'])
    assert calls.log == [('orders', 'L-old'), ('find', 'A'), ('orders', 'L-new')]
    assert result['by_name']['A']['price'] == 2.0
    assert _links(tmp_path)['A']['listing_id'] == 'L-new'
    assert _links(tmp_path)['A']['link'] == 'https://csfloat.com/item/L-new'


def test_same_listing_with_no_orders_is_not_read_twice(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    _write_previous(tmp_path, {'A': {'price': 1.0, 'qty': 1}}, listing_ids={'A': 'L-A'})
    calls = Calls(listings={'A': 'L-A'}, orders={})
    _patch_calls(monkeypatch, calls)
    result = service.fetch_csfloat_buy_orders(token=None, names=['A'])
    assert calls.log == [('orders', 'L-A'), ('find', 'A')]
    assert 'A' not in result['by_name']                   # no buy order any more


def test_listing_ids_and_sweep_times_are_saved(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, scan={'A': {'count': 1}, 'B': {'count': 1}})
    service.sync_csfloat_item_links()                # as the sweep route does first
    calls = Calls(listings={'A': 'L-A', 'B': None}, orders={'L-A': {'price': 1.0, 'qty': 1}})
    _patch_calls(monkeypatch, calls)
    service.fetch_csfloat_buy_orders(token=None, names=['A', 'B'])
    links = _links(tmp_path)
    assert links['A']['listing_id'] == 'L-A' and links['B']['listing_id'] is None
    assert links['A']['checked_at'] and links['B']['checked_at']


# ---- 2. wait CSFloat's own retry time, else one hour ------------------------

def test_retry_after_parsing():
    assert _retry_after_seconds({'Retry-After': '120'}) == 120
    later = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(minutes=10), usegmt=True)
    assert 590 <= _retry_after_seconds({'Retry-After': later}) <= 600
    assert 1790 <= _retry_after_seconds({'X-RateLimit-Reset': str(time.time() + 1800)}) <= 1800
    assert _retry_after_seconds({'X-RateLimit-Reset': '45'}) == 45
    assert _retry_after_seconds({}) is None and _retry_after_seconds(None) is None
    assert _retry_after_seconds({'Retry-After': 'soon'}) is None


def test_bench_uses_the_retry_time_or_one_hour(tmp_path):
    keys = CSFloatKeyManager(state_path=str(tmp_path / 'key_state.json'))
    keys.mark_limited('A')                              # no hint: one hour
    keys.mark_limited('B', retry_after=300)             # CSFloat said 5 minutes
    keys.mark_limited('C', retry_after=1)               # too short: at least a minute
    keys.mark_limited('D', retry_after=99999)           # too long: at most three hours
    remaining = {key: keys._remaining(key) for key in 'ABCD'}
    assert 3590 <= remaining['A'] <= 3600
    assert 290 <= remaining['B'] <= 300
    assert 50 <= remaining['C'] <= 60
    assert 3 * 3600 - 10 <= remaining['D'] <= 3 * 3600
    keys.mark_limited('A')                              # a repeat strike does not grow the wait
    assert 3590 <= keys._remaining('A') <= 3600


def test_http_429_carries_the_retry_time(monkeypatch):
    service = HuginnService(steam_service=None, ratatoskr_service=None)

    def refuse(request, timeout=30):
        raise urllib.error.HTTPError('u', 429, 'too many', {'Retry-After': '900'}, io.BytesIO(b''))

    monkeypatch.setattr(huginn_service.urllib.request, 'urlopen', refuse)
    try:
        service._csfloat_fetch_once('https://csfloat.com/api/v1/listings', 'K')
    except _CSFloatRateLimited as error:
        assert error.retry_after == 900
    else:
        raise AssertionError('expected a rate limit')


# ---- 3. most valuable first, few-cent items weekly -------------------------

def test_every_item_is_swept_most_valuable_first(monkeypatch, tmp_path):
    fresh = datetime.now(timezone.utc).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    scan = {'Knife': {'count': 1}, 'Case': {'count': 300}, 'Sticker': {'count': 2},
            'Cheap fresh': {'count': 50}, 'Cheap stale': {'count': 50}}
    prices = {'Knife': 120.0, 'Case': 1.0, 'Sticker': 5.0, 'Cheap fresh': 0.05, 'Cheap stale': 0.05}
    service = _service(monkeypatch, tmp_path, scan=scan, prices=prices)
    _write_previous(tmp_path, {'Cheap fresh': {'price': 0.04, 'qty': 9}},
                    swept_at={'Cheap fresh': fresh, 'Cheap stale': stale})
    calls = Calls(listings={name: 'L-' + name for name in scan},
                  orders={'L-' + name: {'price': 1.0, 'qty': 1} for name in scan})
    _patch_calls(monkeypatch, calls)
    result = service.fetch_csfloat_buy_orders(token='token', names=sorted(scan))
    swept = [name for kind, name in calls.log if kind == 'find']
    # value = price × units held: Case 300, Knife 120, Sticker 10, then the two 2.5s
    # (oldest swept first): cheap items are swept too, every time
    assert swept == ['Case', 'Knife', 'Sticker', 'Cheap stale', 'Cheap fresh']
    assert result['by_name']['Cheap fresh'] == {'price': 1.0, 'qty': 1}      # re-read
    assert result['complete'] is True


def test_items_missing_from_the_tradeon_feed_are_still_swept(monkeypatch, tmp_path):
    # Tradeon's CSFloat feed only has items Tradeon itself lists; Kilowatt Case was
    # missing from it while CSFloat had 200 wanted at $0.11 (2026-10-01).
    scan = {'Kilowatt Case': {'count': 5}, 'Listed on Tradeon': {'count': 1}}
    service = _service(monkeypatch, tmp_path, scan=scan)
    monkeypatch.setattr(service, '_post_tradeon',
                        lambda url, token, body=None: [{'itemName': {'marketHashName': 'Listed on Tradeon'}}])
    service.sync_csfloat_item_links()
    monkeypatch.setattr(HuginnService, '_csfloat_name_orders',
                        lambda self, key, name, **options: {'price': 0.11, 'qty': 200, 'depth': [[0.11, 200]]})
    result = service.fetch_csfloat_buy_orders(token='token', names=sorted(scan))
    assert set(result['by_name']) == set(scan)
    assert result['candidates'] == 2


# ---- the item dictionary: rebuilt from what you hold ------------------------

def test_dictionary_keeps_held_items_adds_new_and_drops_sold(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, scan={'Kept': {'count': 1}, 'New': {'count': 2}})
    (tmp_path / 'item_links.json').write_text(json.dumps({
        'Kept': {'listing_id': 'L-1', 'link': 'https://csfloat.com/item/L-1', 'checked_at': 'x'},
        'Sold': {'listing_id': 'L-2', 'link': 'https://csfloat.com/item/L-2', 'checked_at': 'y'}}))
    names, added, removed = service.sync_csfloat_item_links(['Only in Draupnir'])
    assert names == ['Kept', 'New', 'Only in Draupnir'] and (added, removed) == (2, 1)
    links = _links(tmp_path)
    assert links['Kept']['listing_id'] == 'L-1'                 # kept as it was
    assert links['New'] == {'listing_id': None, 'link': None, 'checked_at': None}
    assert 'Sold' not in links


def test_no_usable_scan_keeps_every_entry(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, scan={})
    (tmp_path / 'item_links.json').write_text(json.dumps({
        'Kept': {'listing_id': 'L-1', 'link': 'https://csfloat.com/item/L-1', 'checked_at': 'x'}}))
    names, added, removed = service.sync_csfloat_item_links(['Draupnir item'])
    assert names == ['Draupnir item', 'Kept'] and removed == 0
    assert _links(tmp_path)['Kept']['listing_id'] == 'L-1'


def test_a_rebuild_during_a_sweep_is_not_undone(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, scan={'A': {'count': 1}, 'Sold': {'count': 1}})
    service.sync_csfloat_item_links()
    # the sweep read the dictionary; meanwhile a scan finds 'Sold' gone and 'New' added
    (tmp_path / 'item_links.json').write_text(json.dumps({
        'A': {'listing_id': None, 'link': None, 'checked_at': None},
        'New': {'listing_id': None, 'link': None, 'checked_at': None}}))
    service._update_csfloat_item_links({
        'A': service._csfloat_link_entry('L-A', 't'), 'Sold': service._csfloat_link_entry('L-S', 't')})
    links = _links(tmp_path)
    assert set(links) == {'A', 'New'} and links['A']['listing_id'] == 'L-A'


# ---- the scan logs out only the sessions it opened --------------------------

def test_scan_logs_out_sessions_it_opened(monkeypatch, tmp_path):
    monkeypatch.setattr(huginn_service, 'CACHE_PATH', str(tmp_path / 'scan.json'))

    class Steam:
        def get_all_accounts_data(self):
            return [{'steamid': '1', 'account_name': 'already'}, {'steamid': '2', 'account_name': 'opened'}]

        def get_account(self, steam_id):
            return {'account_name': {'1': 'already', '2': 'opened'}[steam_id]}

        def get_password(self, steam_id):
            return 'password'

    class Ratatoskr:
        disconnected = []

        def get_status(self, steam_id):
            return {'status': 'connected' if steam_id == '1' else 'disconnected'}

        def login(self, **kwargs):
            return {'success': True}

        def get_inventory(self, steam_id):
            return {'items': []}

        def get_caskets(self, steam_id):
            return {'caskets': []}

        def disconnect(self, steam_id):
            self.disconnected.append(steam_id)
            return {'success': True}

        def get_move_status(self, steam_id):
            return {'running': False, 'pending': 0}

    service = HuginnService(steam_service=Steam(), ratatoskr_service=Ratatoskr())
    monkeypatch.setattr(HuginnService, '_record_login', staticmethod(lambda *args, **kwargs: None))
    service.scan()
    assert Ratatoskr.disconnected == ['2']



def test_scan_that_reached_no_account_keeps_the_previous_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(huginn_service, 'CACHE_PATH', str(tmp_path / 'scan.json'))
    (tmp_path / 'scan.json').write_text(json.dumps({'by_hash': {'Kept': {'count': 1}}}))

    class Steam:
        def get_all_accounts_data(self):
            return [{'steamid': '1', 'account_name': 'a'}]

        def get_account(self, steam_id):
            return {'account_name': 'a'}

        def get_password(self, steam_id):
            return None                                  # login impossible

    class Ratatoskr:
        def get_status(self, steam_id):
            return {'status': 'disconnected'}

    service = HuginnService(steam_service=Steam(), ratatoskr_service=Ratatoskr())
    monkeypatch.setattr(HuginnService, '_record_login', staticmethod(lambda *args, **kwargs: None))
    try:
        service.scan()
    except RuntimeError as error:
        assert 'previous scan is kept' in str(error)
    else:
        raise AssertionError('expected the scan to fail')
    assert json.loads((tmp_path / 'scan.json').read_text()) == {'by_hash': {'Kept': {'count': 1}}}


def test_scan_leaves_a_session_that_is_moving_items(monkeypatch, tmp_path):
    monkeypatch.setattr(huginn_service, 'CACHE_PATH', str(tmp_path / 'scan.json'))

    class Steam:
        def get_all_accounts_data(self):
            return [{'steamid': '2', 'account_name': 'opened'}]

        def get_account(self, steam_id):
            return {'account_name': 'opened'}

        def get_password(self, steam_id):
            return 'password'

    class Ratatoskr:
        disconnected = []

        def get_status(self, steam_id):
            return {'status': 'disconnected'}

        def login(self, **kwargs):
            return {'success': True}

        def get_inventory(self, steam_id):
            return {'items': []}

        def get_caskets(self, steam_id):
            return {'caskets': []}

        def get_move_status(self, steam_id):
            return {'running': True, 'pending': 40}      # a transfer started meanwhile

        def disconnect(self, steam_id):
            self.disconnected.append(steam_id)

    service = HuginnService(steam_service=Steam(), ratatoskr_service=Ratatoskr())
    monkeypatch.setattr(HuginnService, '_record_login', staticmethod(lambda *args, **kwargs: None))
    service.scan()
    assert Ratatoskr.disconnected == []


# ---- buy orders by name: one request per item -------------------------------

def _real_name_orders():
    """The real method, not the conftest guard (tests below fake the HTTP layer)."""
    from conftest import REAL_CSFLOAT_NAME_ORDERS
    return REAL_CSFLOAT_NAME_ORDERS


def test_name_lookup_is_one_request_and_keeps_the_listing(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, scan={'A': {'count': 1}})
    service.sync_csfloat_item_links()
    calls = Calls(listings={'A': 'L-A'}, orders={'L-A': {'price': 9.0, 'qty': 1}})
    _patch_calls(monkeypatch, calls)
    monkeypatch.setattr(HuginnService, '_csfloat_name_orders',
                        lambda self, key, name, **options: {'price': 4.71, 'qty': 10, 'depth': [[4.71, 10], [4.1, 10]]})
    result = service.fetch_csfloat_buy_orders(token=None, names=['A'])
    assert calls.log == []                                  # no listing requests at all
    assert result['by_name']['A'] == {'price': 4.71, 'qty': 10, 'depth': [[4.71, 10], [4.1, 10]]}


def test_unsupported_name_lookup_switches_the_sweep_to_listings(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, scan={'A': {'count': 1}, 'B': {'count': 1}})
    service.sync_csfloat_item_links()
    calls = Calls(listings={'A': 'L-A', 'B': 'L-B'},
                  orders={'L-A': {'price': 1.0, 'qty': 1}, 'L-B': {'price': 2.0, 'qty': 1}})
    _patch_calls(monkeypatch, calls)
    tries = []

    def unsupported(self, key, name, **options):
        tries.append(name)
        raise huginn_service._CSFloatNameLookupUnsupported('HTTP 405')

    monkeypatch.setattr(HuginnService, '_csfloat_name_orders', unsupported)
    result = service.fetch_csfloat_buy_orders(token=None, names=['A', 'B'])
    assert len(tries) == 1                                  # tried once, then listings only
    assert result['by_name']['A']['price'] == 1.0 and result['by_name']['B']['price'] == 2.0


def test_a_blocked_name_lookup_falls_back_for_that_item_only(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, scan={'A': {'count': 1}, 'B': {'count': 1}})
    service.sync_csfloat_item_links()
    calls = Calls(listings={'A': 'L-A'}, orders={'L-A': {'price': 1.0, 'qty': 1}})
    _patch_calls(monkeypatch, calls)

    def by_name(self, key, name, **options):
        if name == 'A':
            raise huginn_service._CSFloatUnavailable('bot challenge')
        return {'price': 3.0, 'qty': 2, 'depth': [[3.0, 2]]}

    monkeypatch.setattr(HuginnService, '_csfloat_name_orders', by_name)
    result = service.fetch_csfloat_buy_orders(token=None, names=['A', 'B'])
    assert [kind for kind, _ in calls.log] == ['find', 'orders']   # only A used a listing
    assert result['by_name']['A']['price'] == 1.0 and result['by_name']['B']['price'] == 3.0


def test_a_rate_limit_on_the_name_lookup_benches_the_key(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, scan={'A': {'count': 1}})
    service.sync_csfloat_item_links()
    monkeypatch.setattr(huginn_service, '_CSFLOAT_MAX_AUTO_WAITS', 0)   # pause instead of waiting an hour

    def limited(self, key, name, **options):
        raise _CSFloatRateLimited('HTTP 429')

    monkeypatch.setattr(HuginnService, '_csfloat_name_orders', limited)
    try:
        service.fetch_csfloat_buy_orders(token=None, names=['A'])
    except RuntimeError as error:                       # nothing priced: the sweep says so loudly
        assert 'cooling' in str(error)
    else:
        raise AssertionError('expected the paused sweep to report it')
    assert service.csfloat_keys._remaining('K') > 3000   # benched for the hour
    saved = json.loads((tmp_path / 'buy_orders.json').read_text())
    assert saved['complete'] is False                  # resumable


def test_name_answer_is_parsed_highest_first_without_conditional_orders(monkeypatch):
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    answer = {'data': [
        {'market_hash_name': 'Sticker | Gold Web (Foil)', 'price': 402, 'qty': 113, 'hybrid_properties': {}},
        {'market_hash_name': 'Sticker | Gold Web (Foil)', 'price': 900, 'qty': 1,
         'hybrid_properties': {'float': {'max': 0.01}}},                   # a float-specific order
        {'market_hash_name': 'Sticker | Gold Web (Foil)', 'price': 471, 'qty': 10, 'hybrid_properties': {}},
        {'market_hash_name': 'Sticker | Gold Web (Foil)', 'price': 0, 'qty': 5, 'hybrid_properties': {}},
        {'price': 90000, 'qty': 1, 'expression': 'float < 0.01'},             # advanced order, no name
        {'market_hash_name': 'Sticker | Gold Web (Foil) (other)', 'price': 800, 'qty': 1},
    ]}
    sent = {}

    def fetch(url, api_key, proxy=None, request_body=None):
        sent.update(url=url, proxy=proxy, body=request_body)
        return answer

    monkeypatch.setattr(service, '_csfloat_fetch_once', fetch)
    real = _real_name_orders()
    result = real(service, 'K', 'Sticker | Gold Web (Foil)')
    assert result == {'price': 4.71, 'qty': 10, 'depth': [[4.71, 10], [4.02, 113]]}
    assert sent['url'].endswith('/buy-orders/similar-orders') and sent['proxy'] is None
    assert sent['body'] == {'market_hash_name': 'Sticker | Gold Web (Foil)'}


def test_name_answer_that_is_not_a_list_is_unsupported(monkeypatch):
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    monkeypatch.setattr(service, '_csfloat_fetch_once', lambda url, api_key, proxy=None, request_body=None: {'error': 'x'})
    real = _real_name_orders()
    try:
        real(service, 'K', 'A')
    except huginn_service._CSFloatNameLookupUnsupported:
        pass
    else:
        raise AssertionError('expected unsupported')


def test_post_request_carries_a_json_body(monkeypatch):
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"data": []}'

    def fake_open(request, timeout=30):
        seen.update(method=request.get_method(), data=request.data,
                    content_type=request.get_header('Content-type'))
        return Response()

    monkeypatch.setattr(huginn_service.urllib.request, 'urlopen', fake_open)
    service._csfloat_fetch_once('https://csfloat.com/api/v1/buy-orders/similar-orders', 'K',
                                request_body={'market_hash_name': 'A'})
    assert seen == {'method': 'POST', 'data': b'{"market_hash_name": "A"}', 'content_type': 'application/json'}



def test_name_lookup_retries_a_brief_rate_limit(monkeypatch):
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    monkeypatch.setattr(huginn_service.time, 'sleep', lambda seconds: None)
    answers = [_CSFloatRateLimited('HTTP 429'), {'data': [{'market_hash_name': 'A', 'price': 150, 'qty': 2, 'hybrid_properties': {}}]}]

    def fetch(url, api_key, proxy=None, request_body=None):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(service, '_csfloat_fetch_once', fetch)
    assert _real_name_orders()(service, 'K', 'A')['price'] == 1.5


def test_name_lookup_errors_only_switch_off_for_a_missing_endpoint(monkeypatch):
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    for code, expected in [(405, huginn_service._CSFloatNameLookupUnsupported),
                           (410, huginn_service._CSFloatNameLookupUnsupported),
                           (404, huginn_service._CSFloatUnavailable),          # one unknown name only
                           (502, huginn_service._CSFloatUnavailable),
                           (400, huginn_service._CSFloatUnavailable)]:
        def fetch(url, api_key, proxy=None, request_body=None, code=code):
            raise urllib.error.HTTPError(url, code, 'x', {}, io.BytesIO(b''))

        monkeypatch.setattr(service, '_csfloat_fetch_once', fetch)
        try:
            _real_name_orders()(service, 'K', 'A')
        except expected:
            pass
        else:
            raise AssertionError(f'HTTP {code} should raise {expected.__name__}')


def test_direct_blocked_several_times_stops_the_name_lookup(monkeypatch, tmp_path):
    names = [f'Item {index}' for index in range(6)]
    service = _service(monkeypatch, tmp_path, scan={name: {'count': 1} for name in names})
    service.sync_csfloat_item_links()
    calls = Calls(listings={name: 'L-' + name for name in names},
                  orders={'L-' + name: {'price': 1.0, 'qty': 1} for name in names})
    _patch_calls(monkeypatch, calls)
    tries = []

    def blocked(self, key, name, **options):
        tries.append(name)
        raise huginn_service._CSFloatUnavailable('HTTP 403')

    monkeypatch.setattr(HuginnService, '_csfloat_name_orders', blocked)
    result = service.fetch_csfloat_buy_orders(token=None, names=names)
    assert len(tries) == huginn_service._CSFLOAT_NAME_LOOKUP_BLOCKED_LIMIT
    assert len(result['by_name']) == 6                     # all priced through listings



def test_with_a_proxy_a_direct_rate_limit_uses_the_listing_instead_of_benching(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, scan={'A': {'count': 1}})
    monkeypatch.setattr(huginn_service, 'load_csfloat_proxy', lambda: 'http://user:pass@proxy:1')
    service.sync_csfloat_item_links()
    calls = Calls(listings={'A': 'L-A'}, orders={'L-A': {'price': 1.0, 'qty': 1}})
    _patch_calls(monkeypatch, calls)
    options_seen = []

    def limited(self, key, name, **options):
        options_seen.append(options)
        raise _CSFloatRateLimited('HTTP 429')

    monkeypatch.setattr(HuginnService, '_csfloat_name_orders', limited)
    result = service.fetch_csfloat_buy_orders(token=None, names=['A'])
    assert options_seen == [{'retry_rate_limit': False}]            # no 15-second back-off either
    assert result['by_name']['A']['price'] == 1.0
    assert service.csfloat_keys._remaining('K') == 0                # key not benched


def test_blocked_with_no_way_around_still_stops_the_name_lookup(monkeypatch, tmp_path):
    names = [f'Item {index}' for index in range(6)]
    service = _service(monkeypatch, tmp_path, scan={name: {'count': 1} for name in names})
    service.sync_csfloat_item_links()
    tries = []

    def blocked_name(self, key, name, **options):
        tries.append(name)
        raise huginn_service._CSFloatUnavailable('HTTP 403')

    def blocked_listing(self, key, name, proxy=None):
        raise huginn_service._CSFloatUnavailable('HTTP 403')

    monkeypatch.setattr(HuginnService, '_csfloat_name_orders', blocked_name)
    monkeypatch.setattr(HuginnService, '_csfloat_find_listing_id', blocked_listing)
    try:
        service.fetch_csfloat_buy_orders(token=None, names=names)
    except RuntimeError:
        pass                                               # nothing priced: aborts loudly
    assert len(tries) == huginn_service._CSFLOAT_NAME_LOOKUP_BLOCKED_LIMIT
