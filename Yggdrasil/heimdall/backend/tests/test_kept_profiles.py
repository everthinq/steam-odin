"""Huginn Arbitrage profiles kept fresh: profile_tables names exactly the pulse tables each profile's
route reads (every curated route and every generated pair the page offers), a profile opening by
itself (?kept=1) takes its kept tables from memory up to their interval while Fetch live data still
downloads, every profile answer says how old its tables are, and the saved choice reaches the feed.
A fake pulse answers every table; nothing reaches the network."""
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
    checked = 0
    for route, buy, sell, mode in profiles:
        try:
            expected = set(pulse_views.profile_tables(buy, sell, mode))
        except ValueError:
            continue                        # a profile with no pulse table: nothing to keep
        response = client.get(route)
        assert response.status_code == 200, (route, response.get_json())
        assert _tables_read(response) == expected, route
        checked += 1
    assert checked == len(profiles)         # every profile the page offers can be kept fresh


def test_kept_tables_are_real_pulse_tables_shown_on_the_pulse_feed_page(service):
    # One exception: selling at LisSkins' lowest listing reads its plain 'Sell' table, while the
    # page's grid shows its listing without trade hold; the page lists it under Activity instead.
    offered = pulse_views._offered() | {('LisSkins', 'Sell')}
    for _, buy, sell, mode in CURATED + list(_generated(service)):
        tables = pulse_views.profile_tables(buy, sell, mode)
        assert set(tables) <= offered and all(price_type != 'EstimatedSell' for _, price_type in tables), (buy, sell, mode)


def test_a_kept_profile_opens_from_memory_and_fetch_live_data_still_downloads(client, service):
    assert client.post('/api/huginn/arbitrage/kept-profiles',
                       json={'profiles': ['LisSkins:CsMoneyTrade:autobuy']}).status_code == 200
    route = '/api/huginn/tradeon/pair?buy=LisSkins&sell=CsMoneyTrade&mode=autobuy'
    client.get(route)                                    # first read downloads both tables
    assert len(service.asked) == 2
    for table in service.pulse._tables.values():         # the keeper's copies are 3 minutes old
        table.started_at -= 180
    opened = client.get(route + '&kept=1')
    assert opened.status_code == 200 and len(service.asked) == 2          # from memory
    assert sorted(age for _, _, age in json.loads(opened.headers['X-Pulse-Tables'])) == [pytest.approx(180, abs=5)] * 2
    fetched = client.get(route)                           # Fetch live data: under a minute old
    assert len(service.asked) == 4
    assert all(age < 5 for _, _, age in json.loads(fetched.headers['X-Pulse-Tables']))


def test_kept_profile_tables_are_accepted_only_up_to_their_interval(client, service):
    client.post('/api/huginn/arbitrage/kept-profiles', json={'profiles': ['LisSkins:CsMoneyTrade:autobuy'], 'minutes': 5})
    route = '/api/huginn/tradeon/pair?buy=LisSkins&sell=CsMoneyTrade&mode=autobuy&kept=1'
    client.get(route)
    for table in service.pulse._tables.values():
        table.started_at -= 5 * 60 + 30                   # the keeper's next download may be under way
    client.get(route)
    assert len(service.asked) == 2
    for table in service.pulse._tables.values():
        table.started_at -= 60                            # past the interval and its two ticks
    client.get(route)
    assert len(service.asked) == 4


def test_the_interval_alone_keeps_the_saved_profiles(client, service):
    client.post('/api/huginn/arbitrage/kept-profiles', json={'profiles': ['LisSkins:Steam:autobuy']})
    answer = client.post('/api/huginn/arbitrage/kept-profiles', json={'minutes': 15}).get_json()
    assert list(answer['profiles']) == ['LisSkins:Steam:autobuy'] and answer['minutes'] == 15
    assert service.pulse.kept_for_profiles()[1] == 15


def test_kept_is_ignored_for_a_profile_not_kept(client, service):
    route = '/api/huginn/tradeon/lisskins-steam?kept=1'
    client.get(route)
    for table in service.pulse._tables.values():
        table.started_at -= 120
    client.get(route)
    assert len(service.asked) == 4                        # not kept: a minute at most, as before


def test_saving_kept_profiles_validates_and_reaches_the_feed(client, service):
    bad = client.post('/api/huginn/arbitrage/kept-profiles', json={'profiles': ['Nowhere:Steam:autobuy']})
    assert bad.status_code == 400 and 'not an Arbitrage profile' in bad.get_json()['error']
    assert client.post('/api/huginn/arbitrage/kept-profiles', json={'profiles': 'LisSkins'}).status_code == 400
    assert client.post('/api/huginn/arbitrage/kept-profiles',
                       json={'profiles': [], 'minutes': 7}).status_code == 400
    answer = client.post('/api/huginn/arbitrage/kept-profiles', json={
        'profiles': ['LisSkins:CsMoneyTrade:autobuy', 'LisSkins:Steam:autobuy'], 'minutes': 10}).get_json()
    assert answer['minutes'] == 10 and answer['tables_used'] == 3          # LisSkins' listing shared
    assert answer['profiles']['LisSkins:Steam:autobuy']['label'] == 'LisSkins → Steam (autobuy)'
    assert client.settings.settings['huginn_kept_profiles'] == ['LisSkins:CsMoneyTrade:autobuy', 'LisSkins:Steam:autobuy']
    kept, minutes = service.pulse.kept_for_profiles()
    assert set(kept) == {('LisSkins', 'SellWithoutHold'), ('CsMoneyTrade', 'Buy'), ('Steam', 'Buy')} and minutes == 10
    status = client.get('/api/huginn/pulse').get_json()
    steam_buy = next(table for table in status['tables'] if (table['market'], table['price_type']) == ('Steam', 'Buy'))
    assert steam_buy['kept'] and steam_buy['kept_for_profiles'] and not steam_buy['kept_by_you']
    assert set(status['kept_profiles']) == {'LisSkins:CsMoneyTrade:autobuy', 'LisSkins:Steam:autobuy'}


def test_too_many_profile_tables_are_refused():
    many = [f'Buff:{market}:min' for market in ('Steam', 'CsMoneyTrade', 'CsMoneyMarket', 'Tm', 'Waxpeer',
                                                 'Skinport', 'CsDeals', 'ShadowPay', 'Dmarket', 'TradeItStore',
                                                 'TradeItTrade', 'WhiteMarket')]
    with pytest.raises(ValueError, match='at most 12'):
        pulse_views.parse_kept_profiles(many)
    profiles, tables = pulse_views.parse_kept_profiles_leniently(many + ['Gone:Steam:min'])
    assert len(tables) <= pulse_feed.MAX_PROFILE_TABLES and 'Gone:Steam:min' not in profiles


def test_profile_and_manual_intervals_the_shorter_one_wins():
    now = [1000.0]
    feed = pulse_feed.PulseFeed(post=lambda url, token, body: [dict(ROW)], clock=lambda: now[0])
    feed.set_kept([('Steam', 'Buy')], minutes=30)
    feed.set_kept_for_profiles([('Steam', 'Buy'), ('Buff', 'Buy')], minutes=5)
    assert feed.status()['tables'][1]['keep_seconds'] == 300
    feed.table('token', 'Steam', 'Buy')
    now[0] += 4 * 60
    with feed.reading(accept_kept=True) as reads:
        table = feed.table('token', 'Steam', 'Buy', max_age=pulse_feed.ON_DEMAND)
    assert table.started_at == 1000.0 and reads == {('Steam', 'Buy'): 1000.0}
    now[0] += 2 * 60                                          # 6 minutes: past the profile's 5
    with feed.reading(accept_kept=True):
        assert feed.table('token', 'Steam', 'Buy', max_age=pulse_feed.ON_DEMAND).started_at == now[0]
    assert feed.ages([('Steam', 'Buy'), ('Buff', 'Buy')]) == {('Steam', 'Buy'): (0.0, False), ('Buff', 'Buy'): (None, False)}


def test_the_keeper_downloads_profile_tables_at_their_interval():
    now = [1000.0]
    asked = []
    feed = pulse_feed.PulseFeed(post=lambda url, token, body: asked.append(url) or [dict(ROW)], clock=lambda: now[0])
    feed.set_kept_for_profiles([('CsMoneyTrade', 'Buy')], minutes=5)
    assert feed.keep_fresh_once('token') == 1
    now[0] += 3 * 60
    assert feed.keep_fresh_once('token') == 0
    now[0] += 2 * 60
    assert feed.keep_fresh_once('token') == 1 and len(asked) == 2


def test_saved_profiles_are_applied_at_start_and_bad_ones_skipped():
    service = HuginnService(None, None)
    pulse_views.apply_kept_settings(service, {'huginn_kept_profiles': ['LisSkins:Steam:autobuy', 'Gone:x:min', 7],
                                              'huginn_profile_keep_minutes': 7})
    assert service.pulse.kept_for_profiles() == ([('LisSkins', 'SellWithoutHold'), ('Steam', 'Buy')], 5)


def test_a_preview_says_which_tables_keeping_a_profile_adds(client):
    client.post('/api/huginn/arbitrage/kept-profiles', json={'profiles': ['LisSkins:Steam:autobuy']})
    preview = client.get('/api/huginn/arbitrage/kept-profiles?preview=LisSkins:CsMoneyTrade:autobuy').get_json()['preview']
    assert preview['tables'] == [['LisSkins', 'SellWithoutHold'], ['CsMoneyTrade', 'Buy']] and preview['adds'] == 1
    assert 'error' in client.get('/api/huginn/arbitrage/kept-profiles?preview=Nowhere').get_json()['preview']


def test_a_preview_past_the_table_limit_says_so(client, monkeypatch):
    monkeypatch.setattr(pulse_views, 'MAX_PROFILE_TABLES', 2)
    monkeypatch.setattr(pulse_feed, 'MAX_PROFILE_TABLES', 2)
    client.post('/api/huginn/arbitrage/kept-profiles', json={'profiles': ['LisSkins:Steam:autobuy']})
    status = client.get('/api/huginn/arbitrage/kept-profiles?preview=Buff:CsMoneyTrade:autobuy').get_json()
    assert status['max_tables'] == 2 and 'Switch another profile off' in status['preview']['error']
    shared = client.get('/api/huginn/arbitrage/kept-profiles?preview=LisSkins:Steam:autobuy').get_json()
    assert 'error' not in shared['preview']               # already kept: switching it off is always allowed


def test_every_profile_route_records_its_tables_and_takes_kept():
    """A profile route without @pulse_profile would ignore ?kept=1 and show no price age."""
    app = Flask(__name__)
    app.register_blueprint(huginn_routes.bp)
    profile_routes = [rule for rule in app.url_map.iter_rules() if rule.rule.startswith('/api/huginn/tradeon/')]
    assert len(profile_routes) >= 24
    for rule in profile_routes:
        view = app.view_functions[rule.endpoint]
        assert getattr(view, '__wrapped__', None) is not None, rule.rule
        assert view.__code__ is huginn_routes.pulse_profile(lambda: None).__code__, rule.rule
