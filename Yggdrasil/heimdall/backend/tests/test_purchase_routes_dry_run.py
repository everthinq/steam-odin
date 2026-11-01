"""The two wallet purchase routes fail closed: only an explicit ``"dry_run": false`` buys."""
import pytest
from flask import Flask

from context import ctx


class RecordingPurchases:
    def __init__(self):
        self.calls = []

    def start_purchase(self, selection, dry_run, plan_created_at, option):
        self.calls.append({'dry_run': dry_run, 'plan_created_at': plan_created_at})
        return {'started': True}


@pytest.fixture
def client(monkeypatch):
    from routes.huginn import bp as huginn_bp
    from routes.ratatoskr import bp as ratatoskr_bp
    purchases = RecordingPurchases()
    monkeypatch.setattr(ctx, 'store_purchase_service', purchases)
    monkeypatch.setattr(ctx, 'storage_shop_service', purchases)
    app = Flask(__name__)
    app.register_blueprint(huginn_bp)
    app.register_blueprint(ratatoskr_bp)
    return app.test_client(), purchases


@pytest.mark.parametrize('route', ['/api/huginn/card-deals/buy/run', '/api/ratatoskr/storage-shop/run'])
@pytest.mark.parametrize('body, dry_run', [({}, True), ({'dry_run': None}, True), ({'dry_run': 'false'}, True),
                                           ({'dry_run': 0}, True), ({'dry_run': True}, True),
                                           ({'dry_run': False}, False)])
def test_only_an_explicit_false_buys(client, route, body, dry_run):
    test_client, purchases = client
    test_client.post(route, json={'selection': [], 'plan_created_at': 1.5, **body})
    assert purchases.calls == [{'dry_run': dry_run, 'plan_created_at': 1.5}]
