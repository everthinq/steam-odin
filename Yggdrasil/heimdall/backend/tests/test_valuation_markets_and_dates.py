"""One valuation market list for every picker, and the date forms the backend sends."""
import pytest
from flask import Flask

from huginn_service import DEFAULT_PRICE_MARKET, HuginnService
from validation import date_display, date_editable


def test_every_valuation_market_has_a_label_and_the_default_is_listed():
    view = HuginnService.valuation_markets(HuginnService.__new__(HuginnService))
    ids = [m['id'] for m in view['markets']]
    assert set(ids) == set(HuginnService._PRICE_MARKETS) | {'lowest'}
    assert view['default'] == DEFAULT_PRICE_MARKET in ids
    assert set(HuginnService._LOWEST_OF) <= set(HuginnService._PRICE_MARKETS)


def test_date_forms():
    assert date_editable('2026-08-11T15:23:24') == '2026-08-11 15:23:24'
    assert date_display('2026-08-11T15:23:24') == '2026-08-11 15:23'
    assert date_display('2026-08-11') == date_editable('2026-08-11') == '2026-08-11'
    assert date_display('sometime in May') == 'sometime in May'
    assert date_display(None) == ''


@pytest.fixture
def client():
    from routes.draupnir import bp
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


def test_normalize_route(client):
    good = client.post('/api/draupnir/dates/normalize', json={'text': '08/31/2026, 12:12 AM'})
    assert good.get_json() == {'date': '2026-08-31T00:12:00', 'editable': '2026-08-31 00:12:00'}
    bad = client.post('/api/draupnir/dates/normalize', json={'text': 'soon'})
    assert bad.status_code == 400 and bad.get_json()['code'] == 'bad_date'
