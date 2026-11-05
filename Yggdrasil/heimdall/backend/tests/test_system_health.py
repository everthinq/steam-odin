"""System health: the loop registry (loop_status.py), Ratatoskr's and ASF's health, the route."""
import threading
from types import SimpleNamespace

import pytest
import requests
from flask import Flask

import error_handlers
import ratatoskr_service
from context import ctx
from loop_status import LoopStatus
from routes import register_blueprints


def test_rounds_record_time_count_and_last_error_and_raise_again():
    now = [100.0]
    status = LoopStatus(clock=lambda: now[0])
    release = threading.Event()
    status.start('Worker', release.wait)
    with status.round('Worker'):
        pass
    now[0] = 160.0
    with pytest.raises(ValueError):
        with status.round('Worker'):
            raise ValueError('market down')
    (row,) = status.snapshot()
    assert row == {'name': 'Worker', 'running': True, 'part_of': None, 'started_at': 100.0, 'rounds': 2,
                   'last_round_at': 160.0, 'last_error': 'ValueError: market down', 'last_error_at': 160.0}
    release.set()


def test_a_part_runs_while_its_parent_thread_runs():
    status = LoopStatus()
    release = threading.Event()
    parent = threading.Thread(target=release.wait, daemon=True)
    status.watch('Scheduler', parent)
    parent.start()
    with status.round('Keep-alive', part_of='Scheduler'):
        pass
    status.beat('Scheduler')
    rows = {row['name']: row for row in status.snapshot()}
    assert rows['Keep-alive']['running'] and rows['Keep-alive']['part_of'] == 'Scheduler'
    assert rows['Scheduler']['rounds'] == 1
    release.set()
    parent.join(timeout=2)
    assert not any(row['running'] for row in status.snapshot())


class _Response:
    def __init__(self, status_code, body=None):
        self.status_code, self.ok, self._body = status_code, status_code < 400, body or {}

    def json(self):
        return self._body


def _health(monkeypatch, outcome):
    def fake_get(url, headers, timeout):
        assert url.endswith('/sessions')
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(ratatoskr_service.requests, 'get', fake_get)
    return ratatoskr_service.RatatoskrService().health()


def test_ratatoskr_health_lists_sessions(monkeypatch):
    answer = _health(monkeypatch, _Response(200, {'idleTimeoutMs': 3600000, 'sessions': [
        {'steamID': '7656', 'accountName': 'fake_account', 'gameCoordinator': True, 'idleSeconds': 12}]}))
    assert answer['reachable'] and answer['secret_accepted'] and answer['error'] is None
    assert answer['sessions'] == [{'steam_id': '7656', 'account_name': 'fake_account',
                                   'game_coordinator': True, 'idle_seconds': 12}]


def test_ratatoskr_health_names_each_failure(monkeypatch):
    down = _health(monkeypatch, requests.exceptions.ConnectionError('refused'))
    assert not down['reachable'] and 'does not answer' in down['error']
    refused = _health(monkeypatch, _Response(401))
    assert refused['reachable'] and refused['secret_accepted'] is False and 'RATATOSKR_SECRET' in refused['error']
    old = _health(monkeypatch, _Response(404))
    assert old['secret_accepted'] and 'restart' in old['error']


def test_route_answers_loops_ratatoskr_and_asf(monkeypatch):
    monkeypatch.setattr(ctx, 'ratatoskr_service', SimpleNamespace(health=lambda: {'reachable': True}), raising=False)
    monkeypatch.setattr(ctx, 'asf_service', SimpleNamespace(health=lambda: {'reachable': False}), raising=False)
    app = Flask(__name__)
    error_handlers.install(app)
    register_blueprints(app)
    answer = app.test_client().get('/api/system/health').get_json()
    assert answer['ratatoskr'] == {'reachable': True} and answer['asf'] == {'reachable': False}
    assert isinstance(answer['loops'], list) and answer['checked_at'] > 0
    assert isinstance(answer['errors'], list) and 'process_bytes' in answer['memory']


def test_recent_errors_keep_the_newest_errors_with_their_exception():
    import logging

    from system_status import RecentErrors
    handler = RecentErrors(limit=2)
    log = logging.getLogger('test.recent_errors')
    log.addHandler(handler)
    try:
        log.warning('only a warning')
        log.error('first %s', 'failure')
        try:
            raise KeyError('price')
        except KeyError:
            log.exception('second failure')
        log.error('third failure')
    finally:
        log.removeHandler(handler)
    messages = [row['message'] for row in handler.newest_first()]
    assert messages == ['third failure', "second failure (KeyError: 'price')"]
    assert handler.newest_first()[0]['source'] == 'test.recent_errors'


def test_memory_use_reads_the_process_and_the_container(monkeypatch, tmp_path):
    import system_status
    status = tmp_path / 'status'
    status.write_text('Name:\tpython\nVmHWM:\t  600000 kB\nVmRSS:\t  500000 kB\n')
    current, limit = tmp_path / 'current', tmp_path / 'max'
    current.write_text('558993408\n')
    limit.write_text('max\n')
    monkeypatch.setattr(system_status, 'PROCESS_STATUS_FILE', str(status))
    monkeypatch.setattr(system_status, 'CONTAINER_MEMORY_FILES',
                        {'container_bytes': str(current), 'container_limit_bytes': str(limit)})
    assert system_status.memory_use() == {'process_bytes': 512000000, 'process_peak_bytes': 614400000,
                                          'container_bytes': 558993408, 'container_limit_bytes': None}
    monkeypatch.setattr(system_status, 'PROCESS_STATUS_FILE', str(tmp_path / 'missing'))
    assert system_status.memory_use()['process_bytes'] is None
