"""request_guard: websites cannot read the API (CORS) or rebind DNS onto it (Host)."""
import pytest
from flask import Flask, jsonify

import request_guard


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('HEIMDALL_ALLOWED_HOSTS', 'heimdall.lan')
    app = Flask(__name__)
    request_guard.install(app)

    @app.route('/api/mimir/export')
    def export():
        return jsonify({'text': 'secret'})

    @app.route('/api/confirmations/check-all', methods=['POST'])
    def check_all():
        return jsonify({'status': 'success'})
    return app.test_client()


@pytest.mark.parametrize('host', ['localhost:5001', '127.0.0.1:5001', '[::1]:5001',
                                  'heimdall-backend:5000', 'localhost', 'heimdall.lan:5001'])
def test_own_hosts_pass(client, host):
    assert client.get('/api/mimir/export', headers={'Host': host}).status_code == 200


@pytest.mark.parametrize('host', ['evil.example', 'evil.example:5001', '192.168.1.5:5001',
                                  'localhost.evil.example', ''])
def test_foreign_hosts_refused(client, host):
    response = client.get('/api/mimir/export', headers={'Host': host})
    assert response.status_code == 403 and b'secret' not in response.data


def test_cors_only_for_the_frontend(client):
    foreign = client.get('/api/mimir/export', headers={'Host': 'localhost:5001', 'Origin': 'https://evil.example'})
    assert 'Access-Control-Allow-Origin' not in foreign.headers
    own = client.get('/api/mimir/export', headers={'Host': 'localhost:5001', 'Origin': 'http://localhost:3000'})
    assert own.headers.get('Access-Control-Allow-Origin') == 'http://localhost:3000'


@pytest.mark.parametrize('header, expected', [('localhost:5001', 'localhost'), ('[::1]:5001', '::1'),
                                               ('HEIMDALL-BACKEND:5000', 'heimdall-backend'), ('::1', '::1')])
def test_host_name(header, expected):
    assert request_guard.host_name(header) == expected


@pytest.mark.parametrize('origin, expected', [(None, 200), ('http://localhost:3000', 200),
                                              ('http://127.0.0.1:3000', 200), ('https://evil.example', 403),
                                              ('null', 403), ('http://localhost:3000.evil.example', 403)])
def test_writes_only_from_the_frontend_origin(client, origin, expected):
    headers = {'Host': 'localhost:5001', **({'Origin': origin} if origin else {})}
    assert client.post('/api/confirmations/check-all', headers=headers, data='x',
                       content_type='text/plain').status_code == expected


def test_reads_are_not_origin_checked(client):
    # A foreign origin may send a GET, but CORS keeps the answer from it.
    response = client.get('/api/mimir/export', headers={'Host': 'localhost:5001', 'Origin': 'https://evil.example'})
    assert response.status_code == 200 and 'Access-Control-Allow-Origin' not in response.headers
