"""Regression tests for the Huginn review fixes: warm back-off after a failure,
the CSFloat sweep keeping the previous complete cache, the short-lived raw pull
cache, file-backed caches re-read only on change, case-alert retries after a
failed send, message trimming, CSFloat fee overrides, the direct-CSFloat
"unavailable" classification, unknown valuation markets, the container refresh
restoring its last full run, and the stale temporary-file sweep.

Nothing here touches pulse, CSFloat, Steam or Telegram: every outward call is
replaced by a fake.
"""
import json
import os
import threading
import time

import pytest

import huginn_service
import notifications
from huginn_service import HuginnService, _CSFloatUnavailable


def _service():
    return HuginnService(steam_service=None, ratatoskr_service=None)


def _wait_until(condition, timeout=2.0):
    deadline = time.time() + timeout
    while not condition() and time.time() < deadline:
        time.sleep(0.01)
    return condition()


# ---- 1. warms back off after a failure ------------------------------------

def test_valuation_warm_failure_backs_off_with_error_status(monkeypatch):
    service = _service()
    calls = []

    def failing(token, market):
        calls.append(market)
        raise RuntimeError('pulse down')

    monkeypatch.setattr(service, '_compute_price_map', failing)
    prices, status = service.prices_for_valuation('token', 'buff')
    assert (prices, status) == (None, 'refreshing')
    assert _wait_until(lambda: service._price_state.get('buff') == 'error')

    # Inside the back-off window: 'error', and no new background pull.
    for _ in range(5):
        assert service.prices_for_valuation('token', 'buff') == (None, 'error')
    assert calls == ['buff']

    # Once the window has passed, the next call retries.
    service._price_failed_at['buff'] -= huginn_service._WARM_RETRY_AFTER_FAILURE_SEC + 1
    assert service.prices_for_valuation('token', 'buff')[1] == 'refreshing'
    assert _wait_until(lambda: len(calls) == 2)


def test_valuation_back_off_still_serves_stale_prices(monkeypatch):
    service = _service()
    service._price_cache['steam'] = (time.time() - 10 * 3600, {'AK': 1.0})
    service._price_state['steam'] = 'error'
    service._price_failed_at['steam'] = time.time()
    monkeypatch.setattr(service, '_compute_price_map',
                        lambda token, market: pytest.fail('must not retry inside the window'))
    assert service.prices_for_valuation('token', 'steam') == ({'AK': 1.0}, 'error')


def test_container_snapshot_failure_backs_off(monkeypatch):
    service = _service()
    calls = []

    def failing(token, market):
        calls.append(market)
        raise RuntimeError('pulse down')

    monkeypatch.setattr(service, '_single_container_map', failing)
    assert service._container_snapshot('token', 'csfloat') == ({}, 'refreshing')
    assert _wait_until(lambda: service._container_state.get('csfloat') == 'error')
    for _ in range(3):
        assert service._container_snapshot('token', 'csfloat') == ({}, 'error')
    assert calls == ['csfloat']


# ---- 9. unknown valuation markets -------------------------------------------

def test_unknown_valuation_market_is_normalized_to_steam(monkeypatch):
    service = _service()
    monkeypatch.setattr(service, '_compute_price_map', lambda token, market: {'AK': 2.0})
    service.price_map('token', 'definitely-not-a-market')
    assert list(service._price_cache) == ['steam']
    service.prices_for_valuation('token', 'junk<script>')
    assert set(service._price_cache) == {'steam'}
    assert 'junk<script>' not in service._price_state


# ---- 2. CSFloat sweep keeps the previous complete cache ----------------------

def _patch_sweep(monkeypatch, tmp_path):
    monkeypatch.setattr(huginn_service, 'load_csfloat_keys', lambda: [{'label': 'k1', 'key': 'K'}])
    monkeypatch.setattr(huginn_service, 'load_csfloat_proxy', lambda: '')
    monkeypatch.setattr(huginn_service, 'CSFLOAT_BUYORDERS_CACHE', str(tmp_path / 'buy_orders.json'))
    monkeypatch.setattr(huginn_service, '_CSFLOAT_REQUEST_DELAY', 0)


def _write_previous(tmp_path, by_name, complete=True):
    (tmp_path / 'buy_orders.json').write_text(json.dumps({
        'by_name': by_name, 'processed': sorted(by_name), 'complete': complete,
        'updated_at': '2020-01-01T00:00:00+00:00', 'started_at': '2020-01-01T00:00:00+00:00',
    }))


def test_new_sweep_is_seeded_from_the_previous_complete_cache(monkeypatch, tmp_path):
    _patch_sweep(monkeypatch, tmp_path)
    _write_previous(tmp_path, {'A': {'price': 1.0, 'qty': 1}, 'B': {'price': 2.0, 'qty': 1},
                               'C': {'price': 3.0, 'qty': 1}, 'Gone': {'price': 9.0, 'qty': 1}})
    monkeypatch.setattr(huginn_service, '_CSFLOAT_CHECKPOINT_EVERY', 1)

    checkpoints = []
    original_write = HuginnService._write_buyorders_cache

    def recording_write(self, by_name, *args, **kwargs):
        checkpoints.append(dict(by_name))
        return original_write(self, by_name, *args, **kwargs)

    monkeypatch.setattr(HuginnService, '_write_buyorders_cache', recording_write)
    # A: new price. B: no listing any more. C: unexpected error (keeps its old price).
    monkeypatch.setattr(HuginnService, '_csfloat_find_listing_id',
                        lambda self, key, name, proxy=None: (_ for _ in ()).throw(ValueError('boom'))
                        if name == 'C' else ('listing-A' if name == 'A' else None))
    monkeypatch.setattr(HuginnService, '_csfloat_top_buy_order',
                        lambda self, key, listing_id, proxy=None: {'price': 5.0, 'qty': 2})

    service = _service()
    processed, seeded, started_at = service._resumable_state(['A', 'B', 'C'])
    assert processed == set() and started_at is None
    assert set(seeded) == {'A', 'B', 'C'}           # 'Gone' is no longer a candidate

    result = service.fetch_csfloat_buy_orders(token=None, names=['A', 'B', 'C'])
    # The first checkpoint still carries the previous prices of the not-yet-swept items.
    assert set(checkpoints[0]) == {'A', 'B', 'C'}
    assert result['complete'] is True
    assert result['by_name'] == {'A': {'price': 5.0, 'qty': 2}, 'C': {'price': 3.0, 'qty': 1}}


def test_seeded_prices_do_not_hide_a_total_failure(monkeypatch, tmp_path):
    _patch_sweep(monkeypatch, tmp_path)
    _write_previous(tmp_path, {f'Item {i}': {'price': 1.0, 'qty': 1} for i in range(20)})

    def unreachable(self, key, name, proxy=None):
        raise _CSFloatUnavailable('connection failed: EOF')

    monkeypatch.setattr(HuginnService, '_csfloat_find_listing_id', unreachable)
    service = _service()
    with pytest.raises(RuntimeError):
        service.fetch_csfloat_buy_orders(token=None, names=[f'Item {i}' for i in range(20)])
    cache = service.get_csfloat_buy_orders_cache()
    assert cache['complete'] is False
    assert len(cache['by_name']) == 20              # previous prices survive the failed run


def test_recent_unfinished_sweep_still_resumes(monkeypatch, tmp_path):
    _patch_sweep(monkeypatch, tmp_path)
    now = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
    (tmp_path / 'buy_orders.json').write_text(json.dumps({
        'by_name': {'A': {'price': 1.0}}, 'processed': ['A', 'B'], 'complete': False,
        'updated_at': now, 'started_at': now,
    }))
    processed, by_name, started_at = _service()._resumable_state(['A', 'B', 'C'])
    assert processed == {'A', 'B'} and by_name == {'A': {'price': 1.0}} and started_at == now


# ---- 8. direct CSFloat without a proxy -------------------------------------

def test_direct_unavailable_without_proxy_stays_unavailable(monkeypatch):
    service = _service()
    monkeypatch.setattr(huginn_service.time, 'sleep', lambda seconds: None)

    def blocked(url, api_key, proxy=None):
        raise _CSFloatUnavailable('CSFloat forbidden (HTTP 403)')

    monkeypatch.setattr(service, '_csfloat_fetch_once', blocked)
    with pytest.raises(_CSFloatUnavailable):
        service._csfloat_get('/listings', 'K', proxy=None)


def test_direct_rate_limit_without_proxy_is_still_rate_limited(monkeypatch):
    service = _service()
    monkeypatch.setattr(huginn_service.time, 'sleep', lambda seconds: None)

    def limited(url, api_key, proxy=None):
        raise huginn_service._CSFloatRateLimited('429')

    monkeypatch.setattr(service, '_csfloat_fetch_once', limited)
    with pytest.raises(huginn_service._CSFloatRateLimited):
        service._csfloat_get('/listings', 'K', proxy=None)


# ---- 3. raw pull cache evicts expired pulls ---------------------------------

def test_market_pull_cache_drops_expired_pulls_on_write(monkeypatch):
    service = _service()
    monkeypatch.setattr(service, '_post_tradeon', lambda url, token, body: [{'url': url}])
    service._pull_market('token', 'Buff', 'Buy')
    service._pull_market('token', 'Steam', 'Buy')
    assert len(service._market_pull_cache) == 2
    # Age both past the TTL; the next write keeps only the new pull.
    service._market_pull_cache = {k: (v[0] - huginn_service._MARKET_PULL_TTL - 1, v[1])
                                  for k, v in service._market_pull_cache.items()}
    service._pull_market('token', 'Dmarket', 'Buy')
    assert list(service._market_pull_cache) == [('Dmarket', 'Buy')]


# ---- 4. file caches re-read only on change + locks ----------------------------

def test_scan_cache_is_parsed_once_until_the_file_changes(monkeypatch, tmp_path):
    path = tmp_path / 'scan.json'
    path.write_text(json.dumps({'by_hash': {'AK': {'count': 1}}}))
    monkeypatch.setattr(huginn_service, 'CACHE_PATH', str(path))
    service = _service()
    first = service.get_cache()
    assert service.get_cache() is first
    path.write_text(json.dumps({'by_hash': {'AWP': {'count': 2}}, 'extra': True}))
    os.utime(path, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    assert set(service.get_cache()['by_hash']) == {'AWP'}


def test_scan_cache_missing_file_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(huginn_service, 'CACHE_PATH', str(tmp_path / 'absent.json'))
    assert _service().get_cache() is None


def test_case_history_is_kept_in_memory_across_saves(monkeypatch, tmp_path):
    path = tmp_path / 'history.json'
    path.write_text(json.dumps({'Case': {'2026-09-01': 1.0}}))
    monkeypatch.setattr(huginn_service, 'CASE_HISTORY_FILE', str(path))
    service = _service()
    history = service._load_case_history()
    assert service._load_case_history() is history
    history['Case']['2026-09-30'] = {'lo': 2.0, 'hi': 2.0}
    service._save_case_history(history)
    reloaded = service._load_case_history()
    assert reloaded['Case']['2026-09-30'] == {'lo': 2.0, 'hi': 2.0}
    assert json.loads(path.read_text())['Case']['2026-09-30'] == {'lo': 2.0, 'hi': 2.0}
    assert service._load_case_history() is reloaded   # no re-parse after our own save


def test_case_alert_runs_never_overlap(monkeypatch):
    service = _service()
    running, overlaps = [0], []

    def slow_run(settings, force=False, refresh=False):
        running[0] += 1
        if running[0] > 1:
            overlaps.append(True)
        time.sleep(0.05)
        running[0] -= 1
        return {'ran': True}

    monkeypatch.setattr(service, '_run_case_alerts_locked', slow_run)
    threads = [threading.Thread(target=service.run_case_alerts, args=({},)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlaps == []


# ---- 5. case alert send failures retry ---------------------------------------

def _alert_service(monkeypatch, tmp_path, sends):
    monkeypatch.setattr(huginn_service, 'CASE_ALERT_STATE_FILE', str(tmp_path / 'alert_state.json'))
    service = _service()
    monkeypatch.setattr(service, 'cases_prices', lambda token, categories: {'containers': [
        {'name': 'Revolution Case', 'prices': {'csfloat': 1.00, 'lisskins': 0.80}},
    ]})
    monkeypatch.setattr(service, 'get_cache', lambda: {})

    def fake_send(settings, text, html=None):
        return sends.pop(0)

    monkeypatch.setattr(huginn_service, 'send_notification', fake_send)
    return service


def test_failed_webhook_send_is_retried_next_poll(monkeypatch, tmp_path):
    sends = [{'ok': False, 'error': 'HTTP 500: down'}, {'ok': True, 'error': None}]
    service = _alert_service(monkeypatch, tmp_path, sends)
    settings = {'case_alerts_enabled': True, 'notify_webhook_url': 'https://example.invalid/hook',
                'tradeon_token': 'token'}
    first = service.run_case_alerts(settings)
    assert first['sent'] is False and first['send_error'] == 'HTTP 500: down'
    assert service._load_alert_state()['active'] == []      # not marked active → retried
    second = service.run_case_alerts(settings)
    assert second['new'] == 1 and second['sent'] is True
    assert service._load_alert_state()['active'] == ['Revolution Case|lisskins']


def test_failed_telegram_send_is_retried_next_poll(monkeypatch, tmp_path):
    sends = [{'ok': False, 'error': 'HTTP 400: too long'},
             {'ok': True, 'error': None, 'message_id': 7}]
    service = _alert_service(monkeypatch, tmp_path, sends)
    settings = {'case_alerts_enabled': True, 'telegram_bot_token': 'x', 'telegram_chat_id': '1',
                'tradeon_token': 'token'}
    assert service.run_case_alerts(settings)['sent'] is False
    assert service._load_alert_state()['active'] == []
    assert service.run_case_alerts(settings)['sent'] is True
    state = service._load_alert_state()
    assert state['active'] == ['Revolution Case|lisskins'] and state['board_message_id'] == 7


def test_trim_message_cuts_at_a_line_break_within_the_limit():
    text = '\n'.join(f'<b>line {i}</b>' for i in range(1000))
    trimmed = notifications.trim_message(text, notifications.TELEGRAM_MESSAGE_LIMIT)
    assert len(trimmed) <= notifications.TELEGRAM_MESSAGE_LIMIT
    assert trimmed.endswith('(trimmed)')
    body = trimmed.rsplit('\n', 1)[0]
    assert body.endswith('</b>')                  # no half line / half tag
    assert notifications.trim_message('short', 2000) == 'short'


def test_webhook_payload_is_trimmed_to_discord_limit(monkeypatch):
    posted = {}

    def fake_post(url, payload, timeout=10):
        posted.update(payload)
        return 204, ''

    monkeypatch.setattr(notifications, '_post_json', fake_post)
    result = notifications.send_notification({'notify_webhook_url': 'https://example.invalid'}, 'x\n' * 5000)
    assert result['ok'] is True
    assert len(posted['content']) <= notifications.WEBHOOK_MESSAGE_LIMIT


# ---- 7. CSFloat fee overrides ---------------------------------------------------

def test_csfloat_autobuy_uses_the_edited_fee(monkeypatch):
    service = _service()
    monkeypatch.setattr(service, 'get_csfloat_buy_orders_cache',
                        lambda: {'by_name': {'AK': {'price': 10.0, 'qty': 1}}})
    monkeypatch.setattr(service, '_post_tradeon', lambda url, token, body: [
        {'itemName': {'marketHashName': 'AK'}, 'secondMarket': {'price': 5.0}}])
    settings = {'huginn_market_fees': {'CsFloat': 0.1}}
    rows = service.fetch_lisskins_csfloat_autobuy('token', settings)
    assert rows[0]['profit'] == pytest.approx(10.0 * 0.9 - 5.0)
    # The generated pair passes its fee through the CSFloat-autobuy early return.
    rows = service.fetch_generated_pair('token', 'LisSkins', 'CsFloat', 'autobuy', fee=0.25)
    assert rows[0]['profit'] == pytest.approx(10.0 * 0.75 - 5.0)
    # No fee given → CSFloat's registry default (2%).
    rows = service.fetch_generated_pair('token', 'LisSkins', 'CsFloat', 'autobuy')
    assert rows[0]['profit'] == pytest.approx(10.0 * 0.98 - 5.0)


def test_lootfarm_arbitrage_uses_the_edited_csfloat_fee(monkeypatch):
    service = _service()
    monkeypatch.setattr(service, '_fetch_lootfarm_feed',
                        lambda game: {'AK': {'price': 100, 'have': 1}})
    monkeypatch.setattr(service, 'get_csfloat_buy_orders_cache',
                        lambda: {'by_name': {'AK': {'price': 10.0}}})
    board = service.lootfarm_arbitrage('', settings={'huginn_market_fees': {'CsFloat': 0.5}})
    assert board['rows'][0]['csfloat'] == pytest.approx(5.0)


# ---- 12. container refresh restores its last full run + temporary sweep ------

def test_container_snapshots_round_trip(monkeypatch, tmp_path):
    path = str(tmp_path / 'snapshots.json')
    service = _service()
    service._container_cache['csfloat'] = (time.time(), {'Revolution Case': {'price': 1.0, 'count': 5}})
    service._save_container_snapshots(12345.0, path=path)
    restored = _service()
    restored._load_container_snapshots(path=path)
    assert restored._container_last_full == 12345.0
    assert restored._container_cache['csfloat'][1] == {'Revolution Case': {'price': 1.0, 'count': 5}}
    assert restored._container_snapshot('token', 'csfloat')[1] == 'fresh'


def test_refresh_loop_skips_the_full_pull_after_a_recent_one(monkeypatch):
    service = _service()
    service._container_last_full = time.time()      # as restored from disk
    refreshed = []
    monkeypatch.setattr(service, '_refresh_markets',
                        lambda token, markets, parallel=False: refreshed.append(tuple(markets)))

    class StopLoop(Exception):
        pass

    def stop(seconds):
        raise StopLoop

    monkeypatch.setattr(huginn_service.time, 'sleep', stop)
    with pytest.raises(StopLoop):
        service._container_refresh_loop(lambda: {'tradeon_token': 'token'}, 600)
    assert refreshed == []                           # alerts off and a full run is recent


def test_stale_temporary_files_are_swept(tmp_path):
    old_temporary = tmp_path / '.tmp-abc123.json'
    new_temporary = tmp_path / '.tmp-def456.json'
    keep_other = tmp_path / 'case_price_history.json'
    keep_binary = tmp_path / '.tmp-xyz.bin'
    for path in (old_temporary, new_temporary, keep_other, keep_binary):
        path.write_text('{}')
    two_hours_ago = time.time() - 2 * 3600
    for path in (old_temporary, keep_other, keep_binary):
        os.utime(path, (two_hours_ago, two_hours_ago))
    removed = HuginnService._sweep_stale_temporary_files(directory=str(tmp_path))
    assert removed == 1
    assert not old_temporary.exists()
    assert new_temporary.exists() and keep_other.exists() and keep_binary.exists()
