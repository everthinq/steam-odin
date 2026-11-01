"""Request errors keep their own status; only a crash answers 500."""
import pytest
from flask import Flask, jsonify

import error_handlers


@pytest.fixture
def client():
    app = Flask(__name__)
    error_handlers.install(app)

    @app.route('/api/export', methods=['POST'])
    def export():
        return jsonify({'text': 'ok'})

    @app.route('/api/crash')
    def crash():
        raise RuntimeError('boom')
    return app.test_client()


@pytest.mark.parametrize('method, path, status, code', [
    ('get', '/api/export', 405, 'method_not_allowed'),
    ('get', '/api/nowhere', 404, 'not_found'),
    ('get', '/api/crash', 500, 'unexpected_error'),
])
def test_each_error_keeps_its_status_and_answers_json(client, method, path, status, code):
    response = getattr(client, method)(path)
    assert response.status_code == status and response.get_json()['code'] == code
    assert response.get_json()['error']


def test_a_wrong_method_names_the_right_one(client):
    assert 'POST' in client.get('/api/export').headers['Allow']


def test_a_crash_still_says_what_happened(client):
    assert client.get('/api/crash').get_json()['error'] == 'Unexpected error: boom'
