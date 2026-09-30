"""Offline tests for the Steam authenticator core: log redaction, soft-deleted
maFiles, settings corruption guard, token picking, import validation, login
guards, transient backoff, the confirmation sweep lock and auto-accept rules.

Nothing here touches the network: every Steam call is replaced by a fake, and
every file lives in a pytest temporary directory.
"""
import base64
import json
import logging
import os
import stat
import threading
import time

import pytest
import requests

import settings as settings_module
import steam_service as steam_service_module
from scheduler import ConfirmationScheduler
from settings import SettingsManager
from steam_service import SteamService, _redact_body, _redact_url

STEAMID = '76561198000000001'


def _fake_jwt(expires_in_seconds):
    def segment(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b'=').decode()
    return f"{segment({'typ': 'JWT'})}.{segment({'exp': int(time.time()) + expires_in_seconds})}.signature"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, url='https://example.test/'):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)
        self.headers = headers or {}
        self.request = type('Request', (), {'method': 'GET', 'url': url})()

    def json(self):
        return self._payload


@pytest.fixture
def service(tmp_path, monkeypatch):
    """A SteamService whose maFiles directory is a temporary folder."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HEIMDALL_SECRET_KEY', 'a-test-only-strong-key')
    # Keep test traffic out of the real logs/steam_debug.log.
    quiet = logging.getLogger('steam_debug_under_test')
    quiet.propagate = False
    if not quiet.handlers:
        quiet.addHandler(logging.NullHandler())
    monkeypatch.setattr(steam_service_module, '_steam_debug_logger', quiet)
    steam = SteamService()
    steam.time_offset = 0
    steam.last_time_sync = time.time()  # never query Steam for the time
    return steam


# ---- 1. log redaction -------------------------------------------------------

def test_redact_body_hides_tokens_and_session_ids():
    body = json.dumps({'response': {
        'access_token': 'eyJ.secret.token', 'refresh_token': 'eyJ.refresh',
        'client_id': '1234567890', 'request_id': 'cmVxdWVzdA==', 'steamid': STEAMID}})
    redacted = _redact_body(body)
    for secret in ('eyJ.secret.token', 'eyJ.refresh', '1234567890', 'cmVxdWVzdA=='):
        assert secret not in redacted
    assert STEAMID in redacted
    assert json.loads(redacted)['response']['access_token'] == '<redacted>'


def test_redact_url_hides_confirmation_signature_and_device_id():
    url = ('https://steamcommunity.com/mobileconf/getlist?p=android%3Adevice&k=SIGNATURE%3D'
           f'&t=1&tag=conf&a={STEAMID}&m=react')
    redacted = _redact_url(url)
    assert 'SIGNATURE' not in redacted and 'device' not in redacted
    assert f'a={STEAMID}' in redacted and 'm=react' in redacted


def test_steam_response_body_never_reaches_main_log(service, caplog):
    caplog.set_level('DEBUG')
    service._log_steam_response('GenerateAccessTokenForApp',
                                _FakeResponse(payload={'response': {'access_token': 'eyJ.leak'}}))
    assert 'eyJ.leak' not in caplog.text
    assert 'body' not in caplog.text


# ---- 2. soft delete ---------------------------------------------------------

def test_delete_moves_mafile_into_deleted_folder_and_never_unlinks(service, tmp_path):
    service.storage.save_account(STEAMID, {'shared_secret': 'seed', 'account_name': 'alpha'})
    assert service.remove_account(STEAMID) is True

    assert service.storage.list_accounts() == []
    deleted_dir = tmp_path / 'maFiles' / '.deleted'
    archived = list(deleted_dir.glob(f'{STEAMID}.*.maFile'))
    assert len(archived) == 1
    assert stat.S_IMODE(os.stat(deleted_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(archived[0]).st_mode) == 0o600
    # The archived copy is the untouched encrypted maFile (restorable by moving it back).
    assert json.loads(service.storage._fernet.decrypt(archived[0].read_bytes()))['shared_secret'] == 'seed'


def test_delete_missing_or_unsafe_name_returns_false(service):
    assert service.remove_account(STEAMID) is False
    assert service.storage.delete_account('../settings') is False
    assert service.storage.delete_account('.heimdall_key') is False


def test_remove_all_accounts_soft_deletes_each_and_listing_stays_empty(service):
    for index in range(3):
        service.storage.save_account(f'7656119800000000{index}', {'shared_secret': 's'})
    assert service.remove_all_accounts() == 3
    assert service.storage.list_accounts() == []
    assert service.get_all_accounts_data() == []


def test_delete_routes_do_not_restart_the_backend(service, monkeypatch):
    from flask import Flask
    import routes.accounts as accounts_routes
    import system_ops
    from context import ctx

    def forbidden_restart():
        raise AssertionError('a delete must not restart the backend')
    monkeypatch.setattr(system_ops, 'trigger_restart', forbidden_restart)
    monkeypatch.setattr(ctx, 'steam_service', service)
    service.storage.save_account(STEAMID, {'shared_secret': 's'})

    app = Flask(__name__)
    app.register_blueprint(accounts_routes.bp)
    client = app.test_client()
    response = client.delete(f'/api/accounts/{STEAMID}')
    assert response.status_code == 200 and response.get_json()['status'] == 'success'
    assert client.delete(f'/api/accounts/{STEAMID}').status_code == 404
    response = client.delete('/api/accounts')
    assert response.status_code == 200 and response.get_json()['count'] == 0


# ---- 3. settings corruption guard -------------------------------------------

def test_corrupt_settings_are_copied_aside_and_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original = '{"tradeon_token": "real-token", '  # truncated JSON
    (tmp_path / settings_module.SETTINGS_FILE).write_text(original)

    manager = SettingsManager()
    assert manager.persist_blocked is True
    assert manager.get_settings()['tradeon_token'] == ''
    copies = list(tmp_path.glob('settings.json.corrupt-*'))
    assert len(copies) == 1 and copies[0].read_text() == original

    assert manager.save_settings({'check_interval': 60}) is False
    manager.append_auto_store_history({'count': 1})
    manager.record_gjallarhorn_news(123)
    assert (tmp_path / settings_module.SETTINGS_FILE).read_text() == original

    # Once the file is repaired, a reload clears the block.
    (tmp_path / settings_module.SETTINGS_FILE).write_text('{"tradeon_token": "real-token"}')
    assert manager.reload_settings() is True
    assert manager.get_settings()['tradeon_token'] == 'real-token'
    assert manager.save_settings({'check_interval': 60}) is True


def test_non_object_settings_file_counts_as_corrupt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / settings_module.SETTINGS_FILE).write_text('[1, 2]')
    assert SettingsManager().persist_blocked is True


def test_save_settings_applies_nothing_when_one_value_is_invalid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = SettingsManager()
    assert manager.save_settings({'check_interval': 120}) is True
    before = manager.get_settings()

    assert manager.save_settings({'auto_confirm_market': True, 'check_interval': 'not-a-number'}) is False
    assert manager.get_settings() == before
    on_disk = json.loads((tmp_path / settings_module.SETTINGS_FILE).read_text())
    assert on_disk['check_interval'] == 120 and on_disk['auto_confirm_market'] is False


def test_missing_settings_file_is_not_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = SettingsManager()
    assert manager.persist_blocked is False
    assert manager.save_settings({'auto_check_enabled': True}) is True


# ---- 4. token picking -------------------------------------------------------

def test_pick_session_token_prefers_the_longer_lived_token(service):
    short_web, long_mobile = _fake_jwt(600), _fake_jwt(20 * 3600)
    session = {'WebAccessToken': short_web, 'AccessToken': long_mobile}
    assert service._pick_session_token(session) == long_mobile
    assert session['WebAccessToken'] == short_web  # still valid, kept

    session = {'WebAccessToken': _fake_jwt(20 * 3600), 'AccessToken': _fake_jwt(600)}
    assert service._pick_session_token(session) == session['WebAccessToken']


def test_pick_session_token_drops_an_expired_web_token(service):
    mobile = _fake_jwt(3600)
    session = {'WebAccessToken': _fake_jwt(-60), 'AccessToken': mobile}
    assert service._pick_session_token(session) == mobile
    assert 'WebAccessToken' not in session


def test_web_session_cookie_uses_valid_mobile_token_behind_expired_web_token(service):
    mobile = _fake_jwt(3600)
    service.storage.save_account(STEAMID, {'Session': {
        'WebAccessToken': _fake_jwt(-60), 'AccessToken': mobile, 'SteamID': STEAMID}})
    cookies = service.web_session_cookie_for(STEAMID)
    assert cookies is not None and cookies['steamLoginSecure'].endswith(mobile)


# ---- 5. per-account merge save ----------------------------------------------

def test_merge_save_keeps_keys_written_by_another_thread(service):
    service.storage.save_account(STEAMID, {'shared_secret': 's', 'Session': {'SteamID': STEAMID}})
    # Another thread (Ratatoskr callback) stores a web session meanwhile.
    service.update_session_cookies(STEAMID, 'web-token', None, 'web-session-id')
    service._merge_save_session(STEAMID, {'AccessToken': 'mobile-token'},
                                drop_if_unchanged={'WebAccessToken': 'some-older-token'})
    session = service.storage.load_account(STEAMID)['Session']
    assert session['AccessToken'] == 'mobile-token'
    assert session['WebAccessToken'] == 'web-token'
    assert session['WebSessionId'] == 'web-session-id'


def test_merge_save_drops_a_discarded_token_only_if_unchanged(service):
    service.storage.save_account(STEAMID, {'Session': {'WebAccessToken': 'stale'}})
    service._merge_save_session(STEAMID, {}, drop_if_unchanged={'WebAccessToken': 'stale'})
    assert 'WebAccessToken' not in service.storage.load_account(STEAMID)['Session']


# ---- 10. import validation --------------------------------------------------

def test_import_requires_a_17_digit_steamid_file_name(service):
    data = {'shared_secret': 's', 'Session': {'SteamID': 76561198000000001}}
    assert 'error' in service.import_account(data, filename=None)
    assert 'error' in service.import_account(data, filename='12345.maFile')
    assert 'error' in service.import_account(data, filename='alpha.maFile')
    assert 'error' in service.import_account(data, filename='7656119800000000X.maFile')
    assert service.storage.list_accounts() == []


def test_import_strips_transport_keys_and_uses_the_file_name_id(service):
    payload = {'shared_secret': 's', 'account_name': 'alpha',
               'Session': {'SteamID': 76561198000000000},  # precision already lost in JS
               'fileName': f'{STEAMID}.maFile', 'account_password': 'hunter2'}
    result = service.import_account(payload, filename=f'{STEAMID}.maFile')
    assert result == {'status': 'success', 'steamid': STEAMID}
    saved = service.storage.load_account(STEAMID)
    assert 'account_password' not in saved and 'fileName' not in saved
    assert saved['Session']['SteamID'] == STEAMID


# ---- 11 / 12. login guards --------------------------------------------------

def test_begin_auth_session_without_password_makes_no_network_call(service, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('no Steam call may happen without a password')
    monkeypatch.setattr(requests, 'Session', no_network)
    result = service.begin_auth_session('alpha', None)
    assert result['success'] is False and 'password' in result['message'].lower()


class _ScriptedSession:
    """A requests.Session stand-in answering each URL from a script."""
    def __init__(self, answers):
        self.answers = answers
        self.proxies = {}
        self.calls = []

    def _answer(self, url):
        self.calls.append(url)
        for fragment, response in self.answers.items():
            if fragment in url:
                return response
        raise AssertionError(f'unexpected call {url}')

    def get(self, url, **kwargs):
        return self._answer(url)

    def post(self, url, **kwargs):
        return self._answer(url)


def _rsa_answer():
    import rsa
    public_key, _ = rsa.newkeys(512)
    return _FakeResponse(payload={'response': {
        'publickey_mod': format(public_key.n, 'x'), 'publickey_exp': format(public_key.e, 'x'),
        'timestamp': '1'}})


def test_rejected_steam_guard_code_fails_fast_without_polling(service, monkeypatch):
    service.storage.save_account(STEAMID, {'shared_secret': base64.b64encode(b'x' * 20).decode(),
                                           'account_name': 'alpha'})
    session = _ScriptedSession({
        'GetPasswordRSAPublicKey': _rsa_answer(),
        'BeginAuthSessionViaCredentials': _FakeResponse(payload={'response': {
            'client_id': '1', 'request_id': 'cg==', 'steamid': STEAMID,
            'allowed_confirmations': [{'confirmation_type': 3}]}}),
        'UpdateAuthSessionWithSteamGuardCode': _FakeResponse(payload={'response': {}},
                                                             headers={'x-eresult': '88'}),
    })
    monkeypatch.setattr(requests, 'Session', lambda: session)
    monkeypatch.setattr(time, 'sleep', lambda seconds: pytest.fail('must not poll'))
    result = service.begin_auth_session('alpha', 'password')
    assert result['success'] is False and 'Steam Guard code' in result['message']
    assert not any('PollAuthSessionStatus' in url for url in session.calls)


def test_login_rate_limit_is_flagged_transient(service, monkeypatch):
    session = _ScriptedSession({'GetPasswordRSAPublicKey': _FakeResponse(status_code=429)})
    monkeypatch.setattr(requests, 'Session', lambda: session)
    result = service.begin_auth_session('alpha', 'password')
    assert result['success'] is False and result['transient'] is True


# ---- 7. transient backoff ---------------------------------------------------

def test_http_429_and_5xx_and_network_errors_trip_the_backoff(service):
    for result in ({'status_code': 429}, {'status_code': 503}, {'transient': True},
                   {'raw': {'message': 'Oh nooooooes! try your request again later'}}):
        service._reset_mobileconf_cooldown()
        assert service._note_transient(dict(result)) is True
        assert service._mobileconf_cooldown_remaining() > 0
    service._reset_mobileconf_cooldown()
    assert service._note_transient({'status_code': 200, 'raw': {'needauth': True}}) is False


def _account_with_valid_token(service):
    service.storage.save_account(STEAMID, {
        'identity_secret': base64.b64encode(b'i' * 20).decode(), 'account_name': 'alpha',
        'Session': {'AccessToken': _fake_jwt(3600), 'SteamID': STEAMID}})
    return service.storage.load_account(STEAMID)


def test_needauth_stops_the_parameter_variant_loop(service, monkeypatch):
    data = _account_with_valid_token(service)
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs['params']['m'])
        return _FakeResponse(payload={'success': False, 'needauth': True})
    monkeypatch.setattr(requests, 'get', fake_get)
    result = service._fetch_confirmations_once(STEAMID, data, data['Session']['AccessToken'])
    assert result['success'] is False and calls == ['react']


def test_confirmation_429_backs_off_without_refreshing(service, monkeypatch):
    _account_with_valid_token(service)
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(status_code=429)
    monkeypatch.setattr(requests, 'get', fake_get)
    monkeypatch.setattr(requests, 'post', lambda *a, **k: pytest.fail('no token refresh on a 429'))
    result = service.get_confirmations(STEAMID)
    assert result['success'] is False and result['rate_limited'] is True
    assert len(calls) == 1
    assert service._mobileconf_cooldown_remaining() > 0


def test_connection_error_on_confirmations_backs_off(service, monkeypatch):
    _account_with_valid_token(service)

    def unreachable(*args, **kwargs):
        raise requests.ConnectionError('down')
    monkeypatch.setattr(requests, 'get', unreachable)
    result = service.get_confirmations(STEAMID)
    assert result['rate_limited'] is True and service._mobileconf_cooldown_remaining() > 0


# ---- 8. GenerateAccessTokenForApp skipped after an empty answer --------------

def test_generate_access_token_for_app_is_skipped_after_empty_answer(service, monkeypatch):
    service.storage.save_account(STEAMID, {'Session': {'RefreshToken': 'r', 'SteamID': STEAMID}})
    data = service.storage.load_account(STEAMID)
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _FakeResponse(payload={'response': {}})
    monkeypatch.setattr(requests, 'post', fake_post)
    assert service._refresh_access_token(STEAMID, data)['success'] is False
    second = service._refresh_access_token(STEAMID, data)
    assert second['skipped'] is True and len(calls) == 1


# ---- 6. sweep lock ----------------------------------------------------------

class _FakeSteamForScheduler:
    def __init__(self):
        self.accepted = []
        self.release = threading.Event()
        self.entered = threading.Event()

    def _mobileconf_cooldown_remaining(self):
        return 0

    def get_all_accounts_data(self):
        return [{'steamid': STEAMID}]

    def get_confirmations(self, steamid):
        self.entered.set()
        self.release.wait(5)
        return {'success': True, 'confirmations': []}


def test_overlapping_confirmation_sweep_reports_already_running():
    steam = _FakeSteamForScheduler()
    scheduler = ConfirmationScheduler(settings_manager=None, steam_service=steam)
    results = []
    worker = threading.Thread(target=lambda: results.append(scheduler._check_all_accounts({})))
    worker.start()
    assert steam.entered.wait(5)
    assert scheduler._check_all_accounts({}) is False
    steam.release.set()
    worker.join(5)
    assert results == [True]


# ---- 14. auto-accept rules --------------------------------------------------

@pytest.mark.parametrize('conf, settings, expected', [
    ({'type': 2}, {'auto_confirm_trades': True}, True),
    ({'type': '2'}, {'auto_confirm_trades': True}, True),
    ({'type': 2}, {'auto_confirm_market': True}, False),
    ({'type': 3}, {'auto_confirm_market': True}, True),
    ({'type': 3}, {'auto_confirm_trades': True}, False),
    ({'type': 12, 'type_name': 'Market Purchase'}, {'auto_confirm_market': True}, True),
    ({'type': 12, 'type_name': 'Market Purchase'}, {'auto_confirm_trades': True}, False),
    ({'type': 12, 'type_name': 'Something Else'}, {'auto_confirm_market': True}, False),
    ({'type': 12}, {'auto_confirm_market': True, 'auto_confirm_trades': True}, False),
    ({'type': 9, 'type_name': 'API Key'}, {'auto_confirm_market': True, 'auto_confirm_trades': True}, False),
    ({'type': 6}, {'auto_confirm_market': True, 'auto_confirm_trades': True}, False),
    ({'type': 'junk'}, {'auto_confirm_market': True, 'auto_confirm_trades': True}, False),
])
def test_should_auto_accept(conf, settings, expected):
    assert ConfirmationScheduler._should_auto_accept(conf, settings) is expected


def test_process_account_logs_each_accepted_confirmation(caplog):
    class Steam:
        def get_confirmations(self, steamid):
            return {'success': True, 'confirmations': [
                {'id': '1', 'nonce': 'n1', 'type': 2, 'type_name': 'Trade Offer',
                 'headline': 'lootfarm bot', 'summary': ['You will give 1 item'], 'creator_id': '555'},
                {'id': '2', 'nonce': 'n2', 'type': 9, 'type_name': 'API Key', 'headline': 'key'},
            ]}

        def act_on_confirmations_batch(self, steamid, items, operation):
            self.items = items
            return {'success': True, 'accepted': len(items)}

    steam = Steam()
    scheduler = ConfirmationScheduler(settings_manager=None, steam_service=steam)
    caplog.set_level('INFO')
    scheduler._process_account(STEAMID, {'auto_confirm_trades': True, 'auto_confirm_market': True})
    assert steam.items == [('1', 'n1')]
    assert "headline='lootfarm bot'" in caplog.text and 'creator_id=555' in caplog.text
    assert 'You will give 1 item' in caplog.text


# ---- 13. logging in the reloader parent ---------------------------------------

def test_reloader_parent_detection(monkeypatch):
    import sys
    from logging_setup import is_reloader_parent
    monkeypatch.setattr(sys, 'argv', ['app.py'])
    monkeypatch.setenv('FLASK_ENV', 'development')
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    assert is_reloader_parent() is True
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')
    assert is_reloader_parent() is False
    monkeypatch.delenv('WERKZEUG_RUN_MAIN')
    monkeypatch.setenv('FLASK_ENV', 'production')
    assert is_reloader_parent() is False
