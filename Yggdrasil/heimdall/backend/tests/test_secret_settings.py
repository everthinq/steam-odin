"""No answer carries a secret setting; a save keeps, replaces or removes it (settings.py)."""
from settings import SECRET_SETTINGS, secret_update, without_secrets

FAKE_TOKEN = '12345:fake-token-for-tests-only-aaaaaaaa'


def test_without_secrets_says_only_whether_each_is_set():
    settings = {'tradeon_token': FAKE_TOKEN, 'telegram_bot_token': '', 'check_interval': 30}
    public = without_secrets(settings)
    assert public == {'check_interval': 30, 'tradeon_token_set': True, 'telegram_bot_token_set': False}
    assert FAKE_TOKEN not in str(public)
    assert settings['tradeon_token'] == FAKE_TOKEN  # the stored settings are untouched


def test_secret_update_replaces_removes_or_keeps():
    assert secret_update({'tradeon_token': f' {FAKE_TOKEN} '}, 'tradeon_token') == (True, FAKE_TOKEN)
    assert secret_update({'tradeon_token': None}, 'tradeon_token') == (True, '')
    assert secret_update({'tradeon_token': '  '}, 'tradeon_token') == (False, None)
    assert secret_update({}, 'tradeon_token') == (False, None)


def test_settings_routes_never_answer_a_secret(monkeypatch):
    from flask import Flask

    import error_handlers
    from context import ctx
    from routes import register_blueprints

    stored = {key: FAKE_TOKEN for key in SECRET_SETTINGS}
    stored['check_interval'] = 30

    class Manager:
        def get_settings(self):
            return dict(stored)

        def save_settings(self, body):
            stored.update(body)
            return True

    monkeypatch.setattr(ctx, 'settings_manager', Manager())
    app = Flask(__name__)
    error_handlers.install(app)
    register_blueprints(app)
    client = app.test_client()

    answer = client.get('/api/settings').get_json()
    assert FAKE_TOKEN not in str(answer) and answer['tradeon_token_set'] is True
    assert FAKE_TOKEN not in str(client.get('/api/huginn/card-deals/config').get_json())

    # blank keeps, null removes, text replaces; the answer still carries no secret
    answer = client.post('/api/settings', json={'tradeon_token': '', 'csfloat_api_key': None,
                                                'telegram_bot_token': 'new-fake-token'}).get_json()
    assert stored['tradeon_token'] == FAKE_TOKEN
    assert stored['csfloat_api_key'] == ''
    assert stored['telegram_bot_token'] == 'new-fake-token'
    assert 'new-fake-token' not in str(answer) and answer['settings']['csfloat_api_key_set'] is False
