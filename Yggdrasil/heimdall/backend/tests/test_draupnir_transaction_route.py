"""Adding or editing a Draupnir transaction: the backend checks the required
fields and the item name, and answers {error, code} when it refuses."""
from types import SimpleNamespace

import pytest
from flask import Flask

import error_handlers  # noqa: E402

from context import ctx
from draupnir_service import DraupnirService
from validation import validate_new_transaction

KNOWN = {'Fracture Case', 'Recoil Case', 'Revolution Case'}


def _full(**changes):
    body = {'item_name': 'Fracture Case', 'type': 'buy', 'qty': '2', 'price': '0.41',
            'platform': 'steam', 'date': '2026-11-04'}
    body.update(changes)
    return body


@pytest.fixture
def client(tmp_path, monkeypatch):
    from routes.draupnir import bp
    svc = DraupnirService(huginn_service=None, path=str(tmp_path / 'portfolios.json'))
    svc.set_backup(None)
    names = {'pulse': set(KNOWN)}
    monkeypatch.setattr(ctx, 'draupnir_service', svc)
    monkeypatch.setattr(ctx, 'settings_manager', SimpleNamespace(get_settings=lambda: {}))
    monkeypatch.setattr(ctx, 'huginn_service',
                        SimpleNamespace(known_item_names=lambda token, block=True: names['pulse']))
    app = Flask(__name__)
    error_handlers.install(app)
    app.register_blueprint(bp)
    portfolio = svc.create_portfolio('acct')
    return app.test_client(), svc, portfolio['id'], names


def test_every_required_field_is_named():
    errors = validate_new_transaction({'item_name': 'Fracture Case', 'qty': '', 'price': '  '})
    assert errors == ['qty is required', 'price is required', 'platform is required', 'date is required']
    assert validate_new_transaction(_full()) == []
    assert validate_new_transaction({**_full(qty=None), 'quantity': 3}) == []


def test_missing_field_is_refused_with_a_code(client):
    http, svc, pid, _ = client
    response = http.post(f'/api/draupnir/portfolios/{pid}/transactions', json=_full(platform=' '))
    assert response.status_code == 400
    assert response.get_json() == {'error': 'platform is required', 'code': 'invalid_transaction'}
    assert svc._get(pid)['transactions'] == []


def test_known_item_is_added(client):
    http, svc, pid, _ = client
    response = http.post(f'/api/draupnir/portfolios/{pid}/transactions', json=_full())
    assert response.status_code == 201
    assert response.get_json()['qty'] == 2 and 'confirm_unknown_item' not in response.get_json()


def test_unknown_item_needs_confirmation(client):
    http, svc, pid, _ = client
    url = f'/api/draupnir/portfolios/{pid}/transactions'
    refused = http.post(url, json=_full(item_name='Fracture Cas'))
    assert refused.status_code == 409
    body = refused.get_json()
    assert body['code'] == 'unknown_item' and 'Fracture Case' in body['suggestions']
    assert svc._get(pid)['transactions'] == []
    accepted = http.post(url, json=_full(item_name='Fracture Cas', confirm_unknown_item=True))
    assert accepted.status_code == 201


def test_name_is_accepted_when_the_item_list_cannot_be_read(client):
    http, _, pid, names = client
    names['pulse'] = set()
    response = http.post(f'/api/draupnir/portfolios/{pid}/transactions', json=_full(item_name='Brand New Item'))
    assert response.status_code == 201


def test_edit_checks_the_new_name_and_blank_platform(client):
    http, svc, pid, _ = client
    txn = svc.add_transaction(pid, _full())
    url = f"/api/draupnir/portfolios/{pid}/transactions/{txn['id']}"
    assert http.patch(url, json={'platform': ''}).get_json()['code'] == 'invalid_transaction'
    assert http.patch(url, json={'item_name': 'Nope Case'}).get_json()['code'] == 'unknown_item'
    assert http.patch(url, json={'price': '0.5'}).status_code == 200
    missing = http.patch(f'/api/draupnir/portfolios/{pid}/transactions/none', json={'price': '1'})
    assert missing.status_code == 404 and missing.get_json()['code'] == 'transaction_not_found'
