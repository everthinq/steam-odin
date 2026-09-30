"""Huginn route guards: one inventory scan at a time (409 while one runs), the
generated-pair fee range check, and the Gjallarhorn ring clamps. Services are
fakes placed on ctx for the duration of each test; nothing reaches the network.
"""
import threading

import pytest
from flask import Flask

from context import ctx
from routes import huginn as huginn_routes


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(huginn_routes.bp)
    return app.test_client()


@pytest.fixture
def fake_ctx(monkeypatch):
    def install(**services):
        for name, service in services.items():
            monkeypatch.setattr(ctx, name, service, raising=False)
    return install


class FakeSettingsManager:
    def __init__(self, settings=None):
        self.settings = settings or {'tradeon_token': 'token'}

    def get_settings(self):
        return dict(self.settings)


def test_second_scan_while_one_runs_gets_409(client, fake_ctx):
    started, release = threading.Event(), threading.Event()

    class SlowHuginn:
        def scan(self):
            started.set()
            release.wait(5)
            return {'total_items': 0, 'by_hash': {}}

    fake_ctx(huginn_service=SlowHuginn())
    responses = {}
    first = threading.Thread(target=lambda: responses.setdefault('first', client.post('/api/huginn/scan')))
    first.start()
    assert started.wait(5)
    second = client.post('/api/huginn/scan')
    assert second.status_code == 409
    release.set()
    first.join(5)
    assert responses['first'].status_code == 200
    # The guard is released afterwards (also after a failure).
    assert client.post('/api/huginn/scan').status_code == 200


def test_scan_guard_is_released_after_an_error(client, fake_ctx):
    class BrokenHuginn:
        def scan(self):
            raise RuntimeError('ratatoskr down')

    fake_ctx(huginn_service=BrokenHuginn())
    assert client.post('/api/huginn/scan').status_code == 500
    assert client.post('/api/huginn/scan').status_code == 500   # not 409


class PairHuginn:
    def __init__(self):
        self.fees = []

    def market_ids(self):
        return {'LisSkins', 'CsFloat'}

    def market_fee(self, market_id, settings=None):
        return 0.02

    def fetch_generated_pair(self, token, buy, sell, mode, fee):
        self.fees.append(fee)
        return []


@pytest.mark.parametrize('fee, expected_status', [
    ('-0.1', 400), ('1', 400), ('1.5', 400), ('nan', 400), ('0', 200), ('0.05', 200),
])
def test_pair_fee_must_be_a_fraction_below_one(client, fake_ctx, fee, expected_status):
    huginn = PairHuginn()
    fake_ctx(huginn_service=huginn, settings_manager=FakeSettingsManager())
    response = client.get(f'/api/huginn/tradeon/pair?buy=LisSkins&sell=CsFloat&fee={fee}')
    assert response.status_code == expected_status
    if expected_status == 400:
        assert huginn.fees == []


def test_pair_without_fee_uses_the_effective_market_fee(client, fake_ctx):
    huginn = PairHuginn()
    fake_ctx(huginn_service=huginn, settings_manager=FakeSettingsManager())
    assert client.get('/api/huginn/tradeon/pair?buy=LisSkins&sell=CsFloat').status_code == 200
    assert huginn.fees == [0.02]


def test_ring_parameters_are_clamped(client, fake_ctx):
    calls = []

    class FakeCaller:
        def ring(self, message=None, **kwargs):
            calls.append(kwargs)
            return {'ok': True}

    fake_ctx(telegram_caller=FakeCaller())
    client.post('/api/huginn/gjallarhorn/ring',
                json={'repeats': 500, 'ring_seconds': 3600, 'gap_seconds': 99999})
    client.post('/api/huginn/gjallarhorn/ring',
                json={'repeats': -3, 'ring_seconds': 0, 'gap_seconds': -5})
    client.post('/api/huginn/gjallarhorn/ring', json={'repeats': 'lots'})
    assert calls[0] == {'repeats': 5, 'ring_seconds': 45, 'gap_seconds': 60}
    assert calls[1] == {'repeats': 1, 'ring_seconds': 1, 'gap_seconds': 0}
    assert calls[2] == {}                           # unparseable → the caller's default
