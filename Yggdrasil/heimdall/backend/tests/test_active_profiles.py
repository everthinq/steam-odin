"""Active profiles, the master-slave rule (2026-10-31): only Huginn Arbitrage downloads from pulse.
profile_tables names exactly the tables each profile route reads (every curated route and every
generated pair), the profile routes download through the blueprint hook while every other reader
only reads memory, an active profile opening by itself (?kept=1) takes the keeper's copy up to its
interval, each change is merged on the server, and the saved choice reaches the feed. A fake pulse
answers every table; nothing reaches the network."""
import json

import pytest
from flask import Flask

import pulse_feed
import pulse_views
from context import ctx
from huginn_service import HuginnService
from routes import huginn as huginn_routes

ROW = {'itemName': {'marketHashName': 'Item A'}, 'firstMarket': {'price': 1.0},
       'secondMarket': {'price': 2.0, 'totalOffersCount': 3}}

# The curated profiles of the Arbitrage page (frontend pages/huginn/Arbitrage.jsx PROFILES):
# (route, buy market, sell market, mode). The page sends buy:sell:mode as the profile's key.
CURATED = [
    ('/api/huginn/tradeon/steam', 'TradeOnMarket', 'Steam', 'autobuy'),
    ('/api/huginn/tradeon/buff', 'TradeOnMarket', 'Buff', 'autobuy'),
    ('/api/huginn/tradeon/csfloat', 'TradeOnMarket', 'CsFloat', 'min'),
    ('/api/huginn/tradeon/csfloat-autobuy', 'TradeOnMarket', 'CsFloat', 'autobuy'),
    ('/api/huginn/tradeon/dmarket', 'TradeOnMarket', 'Dmarket', 'autobuy'),
    ('/api/huginn/tradeon/lootfarm', 'TradeOnMarket', 'LootFarm', 'autobuy'),
    ('/api/huginn/tradeon/lisskins-steam', 'LisSkins', 'Steam', 'autobuy'),
    ('/api/huginn/tradeon/lisskins-buff', 'LisSkins', 'Buff', 'autobuy'),
    ('/api/huginn/tradeon/lisskins-csfloat', 'LisSkins', 'CsFloat', 'min'),
    ('/api/huginn/tradeon/lisskins-csfloat-autobuy', 'LisSkins', 'CsFloat', 'autobuy'),
    ('/api/huginn/tradeon/lisskins-dmarket', 'LisSkins', 'Dmarket', 'autobuy'),
    ('/api/huginn/tradeon/lisskins-lootfarm', 'LisSkins', 'LootFarm', 'autobuy'),
    ('/api/huginn/tradeon/buff-steam', 'Buff', 'Steam', 'autobuy'),
    ('/api/huginn/tradeon/buff-csfloat', 'Buff', 'CsFloat', 'min'),
    ('/api/huginn/tradeon/buff-csfloat-autobuy', 'Buff', 'CsFloat', 'autobuy'),
    ('/api/huginn/tradeon/buff-dmarket', 'Buff', 'Dmarket', 'autobuy'),
    ('/api/huginn/tradeon/csfloat-steam', 'CsFloat', 'Steam', 'autobuy'),
    ('/api/huginn/tradeon/csfloat-buff', 'CsFloat', 'Buff', 'autobuy'),
    ('/api/huginn/tradeon/csfloat-dmarket', 'CsFloat', 'Dmarket', 'autobuy'),
    ('/api/huginn/tradeon/dmarket-steam', 'Dmarket', 'Steam', 'autobuy'),
    ('/api/huginn/tradeon/dmarket-buff', 'Dmarket', 'Buff', 'autobuy'),
    ('/api/huginn/tradeon/dmarket-csfloat', 'Dmarket', 'CsFloat', 'min'),
    ('/api/huginn/tradeon/dmarket-csfloat-autobuy', 'Dmarket', 'CsFloat', 'autobuy'),
]


def _generated(service):
    """The generated pairs, built the way the Arbitrage page builds them from /api/huginn/markets."""
    markets = service.market_registry({})
    for buy in markets:
        if buy['hasListings'] is False:
            continue
        for sell in markets:
            if sell['id'] in (buy['id'], 'TradeOnMarket', 'LootFarm'):
                continue
            modes = (['autobuy'] if sell['hasAutobuy'] else []) + (['min'] if sell['hasListings'] is not False else [])
            for mode in modes:
                yield f"/api/huginn/tradeon/pair?buy={buy['id']}&sell={sell['id']}&mode={mode}", buy['id'], sell['id'], mode


class Settings:
    def __init__(self, settings=None):
        self.settings = {'tradeon_token': 'token', **(settings or {})}

    def get_settings(self):
        return dict(self.settings)

    def save_settings(self, changes):
        self.settings.update(changes)
        return True


@pytest.fixture
def service(monkeypatch):
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    service.settings_provider = lambda: {}
    asked = service.asked = []

    def post(url, token, body):
        asked.append((url.rsplit('/', 2)[1], body['secondMarketOptions']['secondMarketPriceType']))
        return [dict(ROW)]
    monkeypatch.setattr(service, '_post_tradeon', post)
    service.pulse.slaves_may_download = False          # the app's gate: only Huginn Arbitrage downloads
    # CSFloat's swept buy orders and LOOT.Farm's feed hold the item, so those profiles read pulse.
    monkeypatch.setattr(service, 'get_csfloat_buy_orders_cache', lambda: {'by_name': {'Item A': {'price': 3.0, 'qty': 1}}})
    monkeypatch.setattr(service, '_fetch_lootfarm_feed', lambda game='cs': {'Item A': {'price': 300, 'have': 1, 'max': 5}})
    return service


@pytest.fixture
def client(monkeypatch, service):
    settings = Settings()
    monkeypatch.setattr(ctx, 'huginn_service', service, raising=False)
    monkeypatch.setattr(ctx, 'settings_manager', settings, raising=False)
    app = Flask(__name__)
    app.register_blueprint(huginn_routes.bp)
    client = app.test_client()
    client.settings = settings
    return client


def _tables_read(response):
    return {(market, price_type) for market, price_type, _ in json.loads(response.headers['X-Pulse-Tables'])}


def test_profile_tables_name_exactly_what_every_profile_route_reads(client, service):
    profiles = CURATED + list(_generated(service))
    assert len(profiles) > 300
    for route, buy, sell, mode in profiles:
        expected = set(pulse_views.profile_tables(buy, sell, mode))
        response = client.get(route)                  # downloads: profile routes are Huginn Arbitrage
        assert response.status_code == 200, (route, response.get_json())
        assert _tables_read(response) == expected, route


def test_active_tables_are_real_pulse_tables(service):
    for _, buy, sell, mode in CURATED + list(_generated(service)):
        assert all(price_type != 'EstimatedSell' for _, price_type in pulse_views.profile_tables(buy, sell, mode))


def test_only_the_profile_routes_download(client, service):
    with pytest.raises(pulse_feed.NotActive):
        service.market_autobuy_index('token', 'Steam')               # Harvest, Cross-Profile...: memory only
    client.get('/api/huginn/tradeon/lisskins-steam')                 # Fetch live data
    assert service.market_autobuy_index('token', 'Steam')            # now in memory for every slave
    assert service.asked == [('LisSkins', 'SellWithoutHold'), ('Steam', 'Buy')]


def test_an_active_profile_opens_from_memory_and_fetch_live_data_still_downloads(client, service):
    assert client.post('/api/huginn/active', json={'add': 'LisSkins:CsMoneyTrade:autobuy'}).status_code == 200
    route = '/api/huginn/tradeon/pair?buy=LisSkins&sell=CsMoneyTrade&mode=autobuy'
    client.get(route)
    downloads = len(service.asked)
    for table in service.pulse._tables.values():                     # the keeper's copies are 3 minutes old
        table.started_at -= 180
    opened = client.get(route + '&kept=1')
    assert opened.status_code == 200 and len(service.asked) == downloads
    assert sorted(age for _, _, age in json.loads(opened.headers['X-Pulse-Tables'])) == [pytest.approx(180, abs=5)] * 2
    fetched = client.get(route)                                       # Fetch live data: under a minute old
    assert len(service.asked) == downloads + 2
    assert all(age < 5 for _, _, age in json.loads(fetched.headers['X-Pulse-Tables']))


def test_active_tables_are_accepted_only_up_to_their_interval_and_two_ticks(client, service):
    client.post('/api/huginn/active', json={'add': 'LisSkins:CsMoneyTrade:autobuy'})
    client.post('/api/huginn/active', json={'minutes': 5})
    route = '/api/huginn/tradeon/pair?buy=LisSkins&sell=CsMoneyTrade&mode=autobuy&kept=1'
    client.get(route)
    for table in service.pulse._tables.values():
        table.started_at -= 5 * 60 + 30
    client.get(route)
    assert len(service.asked) == 2
    for table in service.pulse._tables.values():
        table.started_at -= 60
    client.get(route)
    assert len(service.asked) == 4


def test_changes_are_merged_on_the_server(client, service):
    client.post('/api/huginn/active', json={'add': 'LisSkins:Steam:autobuy'})
    client.post('/api/huginn/active', json={'add': 'LisSkins:CsMoneyTrade:autobuy'})     # another tab
    answer = client.post('/api/huginn/active', json={'minutes': 15}).get_json()
    assert client.settings.settings['huginn_active_profiles'] == ['LisSkins:Steam:autobuy', 'LisSkins:CsMoneyTrade:autobuy']
    assert answer['minutes'] == 15 and answer['tables_used'] == 3                       # LisSkins shared
    answer = client.post('/api/huginn/active', json={'remove': 'LisSkins:Steam:autobuy'}).get_json()
    assert list(answer['profiles']) == ['LisSkins:CsMoneyTrade:autobuy']
    assert client.settings.settings['huginn_active_profiles'] == ['LisSkins:CsMoneyTrade:autobuy']
    assert set(service.pulse.active()) == {('LisSkins', 'SellWithoutHold'), ('CsMoneyTrade', 'Buy'), ('Buff', 'Sell')}
    assert service.pulse.active()[('CsMoneyTrade', 'Buy')] == 15 * 60


@pytest.mark.parametrize('body', [['LisSkins:Steam:autobuy'], {'add': 'Nowhere:Steam:autobuy'}, {'minutes': 7},
                                  {'hours': {'enabled': True, 'start': '25:00', 'end': '01:00'}}, {}])
def test_bad_changes_are_refused(client, body):
    response = client.post('/api/huginn/active', json=body)
    assert response.status_code == 400 and response.get_json()['error']


def test_active_hours_are_saved_and_reach_the_keeper(client, service):
    answer = client.post('/api/huginn/active', json={'hours': {'enabled': True, 'start': '08:00', 'end': '01:00'}}).get_json()
    assert answer['hours'] == {'enabled': True, 'start': '08:00', 'end': '01:00'}
    assert answer['keeper']['active_hours'] == [8 * 60, 60]
    client.post('/api/huginn/active', json={'hours': {'enabled': False, 'start': '08:00', 'end': '01:00'}})
    assert service.pulse.status()['keeper']['active_hours'] is None


def test_the_older_kept_profiles_are_read_until_saved_again():
    service = HuginnService(None, None)
    tables = pulse_views.apply_active_settings(service, {'huginn_kept_profiles': ['LisSkins:Steam:autobuy', 'Gone:x:min', 7],
                                                         'huginn_profile_keep_minutes': 7})
    assert tables == [('LisSkins', 'SellWithoutHold'), ('Steam', 'Buy')]
    assert service.pulse.active()[('Steam', 'Buy')] == 5 * 60                # 7 is not a choice
    assert ('Steam', 'Median30d') in [tuple(key) for key in service.pulse.status()['keeper']['once_pending']]


def test_too_many_active_tables_are_refused():
    many = [f'Buff:{market}:min' for market in ('Steam', 'CsMoneyTrade', 'CsMoneyMarket', 'Tm', 'Waxpeer',
                                                 'Skinport', 'CsDeals', 'ShadowPay', 'Dmarket', 'TradeItStore',
                                                 'TradeItTrade', 'WhiteMarket')]
    with pytest.raises(ValueError, match='at most 12'):
        pulse_views.parse_active_profiles(many)
    profiles, tables = pulse_views.parse_active_profiles_leniently(many + ['Gone:Steam:min'])
    assert len(tables) <= pulse_feed.MAX_ACTIVE_TABLES and 'Gone:Steam:min' not in profiles


def test_a_preview_says_what_making_a_profile_active_adds(client, monkeypatch):
    client.post('/api/huginn/active', json={'add': 'LisSkins:Steam:autobuy'})
    preview = client.get('/api/huginn/active?preview=LisSkins:CsMoneyTrade:autobuy').get_json()['preview']
    assert preview['tables'] == [['LisSkins', 'SellWithoutHold'], ['CsMoneyTrade', 'Buy']] and preview['adds'] == 1
    assert 'error' in client.get('/api/huginn/active?preview=Nowhere').get_json()['preview']
    monkeypatch.setattr(pulse_views, 'MAX_ACTIVE_TABLES', 2)
    full = client.get('/api/huginn/active?preview=Buff:CsMoneyTrade:autobuy').get_json()['preview']
    assert 'Switch another profile off' in full['error']
    shared = client.get('/api/huginn/active?preview=LisSkins:Steam:autobuy').get_json()['preview']
    assert 'error' not in shared                                             # already active: always allowed


def test_the_status_shows_the_reference_and_the_statistics(client):
    client.post('/api/huginn/active', json={'add': 'LisSkins:Steam:autobuy'})
    status = client.get('/api/huginn/active').get_json()
    assert [entry['label'] for entry in status['reference']] == ['Buff163 · lowest listing']
    assert {entry['label'] for entry in status['statistics']} == {
        'Buff163 · lowest listing', 'LisSkins · lowest listing (no trade hold)', 'Steam · buy orders'}
    monitor = client.get('/api/huginn/pulse').get_json()
    assert monitor['active']['profiles'] and monitor['labels']
