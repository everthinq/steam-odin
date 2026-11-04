"""Buy Storage Units routes: plan → execute once → a job, errors {error, code} (fake Steam, fake Ratatoskr)."""
import time

import pytest
from flask import Flask

import storage_shop_service as shop
from context import ctx
from jobs import JobRegistry
from test_storage_shop_service import STEAMID, FakeCardDeals, FakeHttp, FakeRatatoskr, FakeSteam


@pytest.fixture
def setup(monkeypatch, tmp_path):
    from routes.jobs import bp as jobs_bp
    from routes.ratatoskr import bp as ratatoskr_bp
    http = FakeHttp()
    service = shop.StorageShopService(FakeSteam(), FakeRatatoskr(), FakeCardDeals(),
                                      state_path=str(tmp_path / 'shop.json'),
                                      approval_page_path=str(tmp_path / 'page.html'),
                                      http=http, sleep=lambda seconds: None, jobs=JobRegistry())
    monkeypatch.setattr(ctx, 'storage_shop_service', service)
    monkeypatch.setattr(ctx, 'jobs', service.jobs)
    app = Flask(__name__)
    app.register_blueprint(ratatoskr_bp)
    app.register_blueprint(jobs_bp)
    return app.test_client(), service, http


def _finished(client, job_id):
    for _ in range(500):
        job = client.get(f'/api/jobs/{job_id}').get_json()
        if not job['running']:
            return job
        time.sleep(0.01)
    raise AssertionError('the job never finished')


def _ready_plan(client, quantity=2):
    answer = client.post('/api/ratatoskr/storage-shop/plan',
                         json={'item': 'casket', 'selection': [{'steamid': STEAMID, 'quantity': quantity}]})
    assert answer.status_code == 200
    plan = answer.get_json()
    assert plan['state'] == 'checking'
    _finished(client, plan['check_job_id'])
    return client.get(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}').get_json()


def test_plan_then_execute_twice_is_one_purchase(setup):
    client, _, http = setup
    plan = _ready_plan(client)
    assert plan['state'] == 'ready' and plan['can_test'] and plan['lines'][0]['quantity'] == 2
    refused = client.post(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}/execute', json={'dry_run': False})
    assert refused.status_code == 409 and refused.get_json()['code'] == 'dry_run_required'
    test = client.post(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}/execute', json={'dry_run': True})
    _finished(client, test.get_json()['id'])
    plan = client.get(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}').get_json()
    assert plan['dry_run']['state'] == 'passed' and plan['executable'] and http.posts == []
    first = client.post(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}/execute', json={'dry_run': False})
    second = client.post(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}/execute', json={'dry_run': False})
    assert first.status_code == second.status_code == 202
    assert first.get_json()['id'] == second.get_json()['id']
    job = _finished(client, first.get_json()['id'])
    assert job['result']['accounts'][0]['paid'] and len(http.posts) == 1
    again = client.post(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}/execute', json={'dry_run': False})
    assert again.get_json()['id'] == job['id'] and len(http.posts) == 1


def test_anything_but_false_is_a_dry_run_and_never_buys(setup):
    client, _, http = setup
    plan = _ready_plan(client)
    for dry_run in (None, 'false', 0):
        job = client.post(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}/execute', json={'dry_run': dry_run}).get_json()
        assert _finished(client, job['id'])['dry_run']
    assert http.posts == []


def test_a_failed_dry_run_is_refused_over_the_route(setup):
    client, _, http = setup
    http.page = http.page.replace('$3.98', '$9.99')
    plan = _ready_plan(client)
    test = client.post(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}/execute', json={'dry_run': True})
    _finished(client, test.get_json()['id'])
    answer = client.post(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}/execute', json={'dry_run': False})
    assert answer.status_code == 409 and answer.get_json()['code'] == 'dry_run_required' and http.posts == []


def test_a_missing_dry_run_tests_without_paying(setup):
    client, _, http = setup
    plan = _ready_plan(client)       # the approval page fixture shows 2 × $1.99
    job = client.post(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}/execute', json={}).get_json()
    finished = _finished(client, job['id'])
    assert finished['dry_run'] and finished['result']['accounts'][0]['outcome'] == 'test_passed'
    assert http.posts == []


def test_an_expired_plan_is_refused_and_says_so(setup):
    client, service, _ = setup
    plan = _ready_plan(client)
    service._plan['created_at'] = time.time() - shop.PLAN_TIME_TO_LIVE_SECONDS - 1
    assert client.get(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}').get_json()['state'] == 'expired'
    answer = client.post(f'/api/ratatoskr/storage-shop/plans/{plan["plan_id"]}/execute', json={'dry_run': True})
    assert answer.status_code == 400 and answer.get_json()['code'] == 'plan_expired'


def test_an_unknown_plan(setup):
    client, _, _ = setup
    answer = client.post('/api/ratatoskr/storage-shop/plans/nope/execute', json={'dry_run': True})
    assert answer.status_code == 404 and answer.get_json()['code'] == 'plan_unknown'
    assert client.get('/api/ratatoskr/storage-shop/plans/nope').get_json()['code'] == 'plan_unknown'


def test_a_bad_selection_is_refused_with_a_code(setup):
    client, _, _ = setup
    answer = client.post('/api/ratatoskr/storage-shop/plan', json={'selection': [{'steamid': STEAMID, 'quantity': 99}]})
    assert answer.status_code == 400 and answer.get_json()['code'] == 'bad_quantity'
    answer = client.post('/api/ratatoskr/storage-shop/deliver-again', json={'at': 'never'})
    assert answer.status_code == 400 and answer.get_json()['code'] == 'bad_request'
    answer = client.post('/api/ratatoskr/storage-shop/deliver-again', json={'at': 1.0})
    assert answer.status_code == 404 and answer.get_json()['code'] == 'nothing_to_deliver'


def test_status_and_quote(setup):
    client, _, _ = setup
    status = client.get('/api/ratatoskr/storage-shop').get_json()
    row = status['accounts'][0]
    assert row['selectable'] and row['can_buy_code'] == 'unknown' and status['unclear_purchases'] == []
    quote = client.post('/api/ratatoskr/storage-shop/quote',
                        json={'item': 'casket', 'selection': [{'steamid': STEAMID, 'quantity': 1}]}).get_json()
    assert quote['units'] == 1 and not quote['complete']        # no price read yet
