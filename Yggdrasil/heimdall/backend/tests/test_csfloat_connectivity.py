"""CSFloat proxy-failure surfacing.

These guard the pieces that turn a broken CSFloat route (direct IP-blocked +
Bright Data proxy rejecting our IP) into an explicit, actionable signal instead
of a silent, misleading "no buy orders exist":

- classify_proxy_error() recognises Bright Data's ip_forbidden / 407 auth failures;
- check_csfloat_connectivity() reports which path works, with the hint;
- fetch_csfloat_buy_orders() aborts fast, raises loudly, and writes an incomplete
  cache carrying the explicit reason (never complete=True/count=0 on a total failure).
"""
import io

import huginn_service
from huginn_service import (
    HuginnService,
    classify_proxy_error,
    detect_public_ip,
    _looks_like_ip,
    _CSFloatUnavailable,
    _CSFloatRateLimited,
)


# --- classify_proxy_error --------------------------------------------------

def test_classify_ip_forbidden():
    info = classify_proxy_error('Tunnel connection failed: 407 Auth Failed (code: ip_forbidden)')
    assert info['code'] == 'ip_forbidden'
    assert 'Bright Data' in info['hint']
    assert 'allowlist' in info['hint'].lower()


def test_classify_generic_407_auth():
    info = classify_proxy_error('proxy tunnel failed: 407 Auth Failed')
    assert info['code'] == 'proxy_auth_failed'
    assert 'csfloat_keys.json' in info['hint']


def test_classify_unknown_returns_empty():
    assert classify_proxy_error('TLS/SSL connection has been closed (EOF)') == {}
    assert classify_proxy_error('') == {}
    assert classify_proxy_error(None) == {}


# --- public IP detection ---------------------------------------------------

def test_looks_like_ip():
    assert _looks_like_ip('195.155.171.46')
    assert _looks_like_ip('8.8.8.8')
    assert _looks_like_ip('2001:db8::1')
    assert not _looks_like_ip('999.1.1.1')      # octet out of range
    assert not _looks_like_ip('not-an-ip')
    assert not _looks_like_ip('1.2.3')          # too few octets
    assert not _looks_like_ip('')
    assert not _looks_like_ip(None)


def test_detect_public_ip_parses_and_caches(monkeypatch):
    huginn_service._PUBLIC_IP_CACHE.update({'ip': None, 'at': 0.0})
    calls = {'n': 0}

    def fake_urlopen(url, timeout=0):
        calls['n'] += 1
        return io.BytesIO(b'195.155.171.46\n')      # echo services return bare text

    monkeypatch.setattr(huginn_service.urllib.request, 'urlopen', fake_urlopen)
    assert detect_public_ip() == '195.155.171.46'
    # Cached: a second call does not hit the network again.
    assert detect_public_ip() == '195.155.171.46'
    assert calls['n'] == 1


def test_detect_public_ip_all_fail_returns_none(monkeypatch):
    huginn_service._PUBLIC_IP_CACHE.update({'ip': None, 'at': 0.0})

    def boom(url, timeout=0):
        raise OSError('no network')

    monkeypatch.setattr(huginn_service.urllib.request, 'urlopen', boom)
    assert detect_public_ip() is None


# --- check_csfloat_connectivity -------------------------------------------

def _svc():
    return HuginnService(steam_service=None, ratatoskr_service=None)


def test_connectivity_no_keys(monkeypatch):
    monkeypatch.setattr(huginn_service, 'load_csfloat_keys', lambda: [])
    monkeypatch.setattr(huginn_service, 'load_csfloat_proxy', lambda: '')
    res = _svc().check_csfloat_connectivity()
    assert res['direct']['ok'] is False
    assert res['proxy']['ok'] is False
    assert res['usable'] is False
    assert 'no CSFloat API keys' in res['direct']['detail']


def test_connectivity_direct_blocked_proxy_ip_forbidden(monkeypatch):
    monkeypatch.setattr(huginn_service, 'load_csfloat_keys', lambda: [{'label': 'k1', 'key': 'K'}])
    monkeypatch.setattr(huginn_service, 'load_csfloat_proxy', lambda: 'http://u:p@brd.superproxy.io:33335')

    def fake_fetch(self, url, api_key, proxy=None):
        if proxy:
            raise _CSFloatUnavailable('connection failed: Tunnel connection failed: 407 '
                                      'Auth Failed (code: ip_forbidden)')
        raise _CSFloatUnavailable('connection failed: TLS/SSL connection has been closed (EOF)')

    monkeypatch.setattr(HuginnService, '_csfloat_fetch_once', fake_fetch)
    monkeypatch.setattr(huginn_service, 'detect_public_ip', lambda: '195.155.171.46')
    res = _svc().check_csfloat_connectivity()
    assert res['direct']['ok'] is False
    assert res['proxy']['ok'] is False
    assert res['proxy']['code'] == 'ip_forbidden'
    assert 'allowlist' in res['proxy']['hint'].lower()
    assert res['usable'] is False
    # The exact IP to whitelist is surfaced for the UI.
    assert res['public_ip'] == '195.155.171.46'


def test_connectivity_proxy_reachable(monkeypatch):
    monkeypatch.setattr(huginn_service, 'load_csfloat_keys', lambda: [{'label': 'k1', 'key': 'K'}])
    monkeypatch.setattr(huginn_service, 'load_csfloat_proxy', lambda: 'http://u:p@host:1')

    def fake_fetch(self, url, api_key, proxy=None):
        if proxy:
            return {'data': [{'id': 'abc'}]}
        raise _CSFloatUnavailable('connection failed: EOF')

    monkeypatch.setattr(HuginnService, '_csfloat_fetch_once', fake_fetch)
    res = _svc().check_csfloat_connectivity()
    assert res['direct']['ok'] is False
    assert res['proxy']['ok'] is True
    assert res['usable'] is True


def test_connectivity_rate_limited_counts_as_reachable(monkeypatch):
    monkeypatch.setattr(huginn_service, 'load_csfloat_keys', lambda: [{'label': 'k1', 'key': 'K'}])
    monkeypatch.setattr(huginn_service, 'load_csfloat_proxy', lambda: '')

    def fake_fetch(self, url, api_key, proxy=None):
        raise _CSFloatRateLimited('CSFloat rate limit (HTTP 429)')

    monkeypatch.setattr(HuginnService, '_csfloat_fetch_once', fake_fetch)
    res = _svc().check_csfloat_connectivity()
    # Throttled means we reached CSFloat — the path itself works.
    assert res['direct']['ok'] is True
    assert res['direct']['rate_limited'] is True
    assert res['proxy']['ok'] is None            # no proxy configured
    assert res['usable'] is True


# --- fetch_csfloat_buy_orders: fail fast + loud ---------------------------

def test_sweep_aborts_loudly_when_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(huginn_service, 'load_csfloat_keys', lambda: [{'label': 'k1', 'key': 'K'}])
    monkeypatch.setattr(huginn_service, 'load_csfloat_proxy', lambda: 'http://u:p@brd:1')
    monkeypatch.setattr(huginn_service, 'CSFLOAT_BUYORDERS_CACHE', str(tmp_path / 'bo.json'))
    monkeypatch.setattr(huginn_service, '_CSFLOAT_REQUEST_DELAY', 0)      # keep the test fast

    def fake_find(self, api_key, name, proxy=None):
        raise _CSFloatUnavailable('connection failed: Tunnel connection failed: 407 '
                                  'Auth Failed (code: ip_forbidden)')

    monkeypatch.setattr(HuginnService, '_csfloat_find_listing_id', fake_find)

    svc = _svc()
    names = [f'Item {i}' for i in range(50)]
    raised = None
    try:
        svc.fetch_csfloat_buy_orders(token=None, names=names)
    except RuntimeError as e:
        raised = str(e)

    # Loud: it raised rather than returning a silent empty result.
    assert raised is not None
    assert 'ip_forbidden' in raised.lower() or 'bright data' in raised.lower()

    # Cache reflects the real failure state, not "complete, 0 found".
    cache = svc.get_csfloat_buy_orders_cache()
    assert cache['complete'] is False
    assert cache['count'] == 0
    assert cache['reason'] and 'unreachable' in cache['reason'].lower()
    # It aborted early — it did NOT grind through all 50 items.
    assert len(cache['processed']) == 0


def test_sweep_does_not_mark_unreachable_items_processed(monkeypatch, tmp_path):
    """A transport failure must leave items un-processed so a later Resume retries
    them — unlike a genuine 'no listing', which is a real, permanent result."""
    monkeypatch.setattr(huginn_service, 'load_csfloat_keys', lambda: [{'label': 'k1', 'key': 'K'}])
    monkeypatch.setattr(huginn_service, 'load_csfloat_proxy', lambda: '')
    monkeypatch.setattr(huginn_service, 'CSFLOAT_BUYORDERS_CACHE', str(tmp_path / 'bo.json'))
    monkeypatch.setattr(huginn_service, '_CSFLOAT_REQUEST_DELAY', 0)

    calls = {'n': 0}

    # First 3 items price fine (so by_name is non-empty → no early abort), then the
    # route goes down: every later item is unreachable.
    def fake_find(self, api_key, name, proxy=None):
        calls['n'] += 1
        if calls['n'] <= 3:
            return f'listing-{name}'
        raise _CSFloatUnavailable('connection failed: EOF')

    def fake_top(self, api_key, listing_id, proxy=None):
        return {'price': 1.23, 'qty': 5}

    monkeypatch.setattr(HuginnService, '_csfloat_find_listing_id', fake_find)
    monkeypatch.setattr(HuginnService, '_csfloat_top_buy_order', fake_top)

    svc = _svc()
    names = [f'Item {i}' for i in range(10)]
    # by_name non-empty → the total-failure RuntimeError is not raised.
    res = svc.fetch_csfloat_buy_orders(token=None, names=names)

    assert res['count'] == 3                      # the 3 that priced
    assert res['complete'] is False               # unreachable ones still owed
    assert len(res['processed']) == 3             # unreachable items NOT marked done
