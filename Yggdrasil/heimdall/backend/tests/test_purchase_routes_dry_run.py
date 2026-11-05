"""The two wallet purchase routes fail closed: only an explicit ``"dry_run": false`` buys (and the
services buy only after a passed dry run of the same plan: tests/test_store_purchase_service.py,
tests/test_storage_shop_service.py)."""
import pytest
from flask import Flask

import error_handlers  # noqa: E402

from context import ctx


class RecordingPurchases:
    def __init__(self):
        self.calls = []

    def execute_purchase_plan(self, purchase_plan_id, dry_run):                  # Andvari "Buy games"
        self.calls.append({'dry_run': dry_run})
        return {'id': 'job'}

    def execute_plan(self, plan_id, dry_run, item):                               # Buy Storage Units
        self.calls.append({'dry_run': dry_run})
        return {'id': 'job'}


@pytest.fixture
def client(monkeypatch):
    from routes.huginn import bp as huginn_bp
    from routes.ratatoskr import bp as ratatoskr_bp
    purchases = RecordingPurchases()
    monkeypatch.setattr(ctx, 'store_purchase_service', purchases)
    monkeypatch.setattr(ctx, 'storage_shop_service', purchases)
    app = Flask(__name__)
    error_handlers.install(app)
    app.register_blueprint(huginn_bp)
    app.register_blueprint(ratatoskr_bp)
    return app.test_client(), purchases


@pytest.mark.parametrize('route', ['/api/huginn/card-deals/buy/purchase-plans/abc/execute',
                                   '/api/ratatoskr/storage-shop/plans/abc/execute'])
@pytest.mark.parametrize('body, dry_run', [({}, True), ({'dry_run': None}, True), ({'dry_run': 'false'}, True),
                                           ({'dry_run': 0}, True), ({'dry_run': True}, True),
                                           ({'dry_run': False}, False)])
def test_only_an_explicit_false_buys(client, route, body, dry_run):
    test_client, purchases = client
    test_client.post(route, json={'selection': [], 'plan_created_at': 1.5, **body})
    assert purchases.calls == [{'dry_run': dry_run}]


class NoSteam:
    """Any Steam call fails the test: a refused purchase never reaches Steam."""

    def __getattr__(self, name):
        raise AssertionError(f'Steam was called ({name})')


@pytest.mark.parametrize('dry_run_state, results, code', [
    ('not run', None, 'dry_run_required'),
    ('running', None, 'dry_run_required'),
    ('done', {'1': {'passed': False, 'reason': 'cart is not empty'}}, 'nothing_passed'),
])
def test_buy_games_buys_only_after_a_passed_dry_run(tmp_path, monkeypatch, dry_run_state, results, code):
    import time

    from routes.huginn import bp as huginn_bp
    from store_purchase_service import StorePurchaseService
    service = StorePurchaseService(NoSteam(), None, state_path=str(tmp_path / 'purchases.json'), http=NoSteam())
    created = time.time()
    service._plan = {'created_at': created, 'accounts': [{'steamid': '1', 'account_name': 'alpha', 'games': []}]}
    service._purchase_plans['abc'] = {
        'id': 'abc', 'created_at': created, 'plan_created_at': created, 'expires_at': created + 600,
        'orders': [{'steamid': '1', 'account_name': 'alpha', 'country': 'US', 'currency': 'USD', 'balance': 100,
                    'games': []}],
        'refused': [], 'empty_cart': True, 'dry_run_state': dry_run_state, 'dry_run_job_id': None,
        'dry_run_results': results, 'dry_run_error': None, 'job_id': None, 'purchase_results': None}
    monkeypatch.setattr(ctx, 'store_purchase_service', service)
    app = Flask(__name__)
    error_handlers.install(app)
    app.register_blueprint(huginn_bp)
    answer = app.test_client().post('/api/huginn/card-deals/buy/purchase-plans/abc/execute', json={'dry_run': False})
    assert answer.status_code == 409 and answer.get_json()['code'] == code
    assert service._purchase_plans['abc']['job_id'] is None
