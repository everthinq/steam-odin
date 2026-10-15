"""Views on the master pulse feed (pulse_views.py): Price dips, "check live" on one Arbitrage row,
Gjallarhorn's liquidity from pulse, and the Pulse feed page's keep-fresh choice. A fake pulse
answers by (market, price type); nothing reaches the network."""
import time

import pytest

import pulse_feed
import pulse_views
from gjallarhorn_service import GjallarhornService
from huginn_service import HuginnService


def _row(name, price, count=5, updated=1700000000, first=None, sales=None):
    return {'itemName': {'marketHashName': name},
            'firstMarket': {'price': first} if first is not None else {},
            'secondMarket': {'price': price, 'totalOffersCount': count, 'offersUpdateTime': updated},
            'itemPopularity': [{'market': market, 'salesCount': sold} for market, sold in (sales or {}).items()]}


def _service(monkeypatch, tables, fees=None):
    """A HuginnService whose pulse answers from *tables*: {(market, price type): [rows]}; a name
    filter answers the rows whose name contains it. Records every request as (market, type, name)."""
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    service.settings_provider = lambda: {'huginn_market_fees': fees or {}}
    asked = []

    def post(url, token, body):
        market = url.rsplit('/', 2)[1]
        price_type = body['secondMarketOptions']['secondMarketPriceType']
        name = (body.get('marketHashNameFilter') or {}).get('value')
        asked.append((market, price_type, name))
        rows = tables.get((market, price_type), [])
        return [row for row in rows if name is None or name in row['itemName']['marketHashName']]
    monkeypatch.setattr(service, '_post_tradeon', post)
    return service, asked


# ---- Price dips ---------------------------------------------------------------------------------

def test_price_dips_compares_each_listing_with_its_30_day_median(monkeypatch):
    service, _ = _service(monkeypatch, {
        ('Steam', 'Sell'): [_row('Dip', 6.0, sales={'Steam': 120, 'Tm': 4}), _row('Spike', 13.0), _row('No median', 1.0)],
        ('Steam', 'Median30d'): [_row('Dip', 10.0), _row('Spike', 10.0)],
        ('Steam', 'Buy'): [_row('Dip', 5.5)],
    })
    answer = pulse_views.price_dips(service, 'token', 'Steam')
    assert [row['name'] for row in answer['rows']] == ['Dip', 'Spike']      # deepest dip first
    dip = answer['rows'][0]
    assert dip['vs_basis_percent'] == -40.0 and dip['basis'] == 10.0 and dip['instant'] == 5.5
    assert (dip['sold_week'], dip['steam_sold_week'], dip['tm_sold_week']) == (120, 120, 4)
    assert dip['updated'] == 1700000000 and 'image' not in dip
    assert answer['rows'][1]['vs_basis_percent'] == 30.0
    assert answer['fee'] == pytest.approx(0.13) and answer['has_instant']


def test_price_dips_refuses_markets_without_30_day_prices_and_unknown_bases(monkeypatch):
    service, asked = _service(monkeypatch, {})
    with pytest.raises(ValueError):
        pulse_views.price_dips(service, 'token', 'CsFloat')
    with pytest.raises(ValueError):
        pulse_views.price_dips(service, 'token', 'Steam', basis='Median90d')
    assert asked == []


def test_every_statistics_market_is_a_registry_market():
    assert [m['id'] for m in pulse_views.price_dips_markets(HuginnService(None, None))] == list(pulse_views.STATISTICS_MARKETS)


# ---- check live ---------------------------------------------------------------------------------

NAME = 'AK-47 | Redline (Field-Tested)'


def test_check_live_reads_both_sides_by_name_and_nets_the_sell_fee(monkeypatch):
    service, asked = _service(monkeypatch, {
        ('LisSkins', 'SellWithoutHold'): [_row(NAME, 26.0), _row('StatTrak™ ' + NAME, 80.0)],
        ('Steam', 'Buy'): [_row(NAME, 32.0)],
    }, fees={'Steam': 0.13})
    answer = pulse_views.check_live(service, 'token', NAME, 'LisSkins', 'Steam', 'autobuy')
    assert answer['found'] and answer['buy']['price'] == 26.0 and answer['sell']['price'] == 32.0
    assert answer['profit'] == pytest.approx(32.0 * 0.87 - 26.0, abs=0.001)
    assert answer['buy']['offersUpdateTime'] == 1700000000
    assert sorted(asked) == [('LisSkins', 'SellWithoutHold', NAME), ('Steam', 'Buy', NAME)]
    assert service.pulse.status()['downloads'] == 0                          # no full table


def test_check_live_with_tradeon_as_buyer_is_one_lookup(monkeypatch):
    service, asked = _service(monkeypatch, {('Buff', 'Buy'): [_row(NAME, 24.5, first=26.4)]})
    answer = pulse_views.check_live(service, 'token', NAME, 'TradeOnMarket', 'Buff', 'autobuy')
    assert asked == [('Buff', 'Buy', NAME)]
    assert answer['buy']['price'] == 26.4 and answer['sell']['price'] == 24.5 and answer['profit'] < 0


def test_check_live_says_when_a_side_no_longer_has_the_item(monkeypatch):
    service, _ = _service(monkeypatch, {('Steam', 'Buy'): [_row(NAME, 32.0)]})
    answer = pulse_views.check_live(service, 'token', NAME, 'LisSkins', 'Steam', 'autobuy')
    assert answer['found'] is False and 'profit' not in answer


@pytest.mark.parametrize('buy, sell, mode', [('LisSkins', 'CsFloat', 'autobuy'),       # the CSFloat sweep
                                            ('SkinSwapTrade', 'Steam', 'autobuy'),   # an estimate
                                            ('LisSkins', 'SkinSwapTrade', 'min'),
                                            ('Nowhere', 'Steam', 'autobuy')])
def test_check_live_refuses_pairs_that_are_not_two_pulse_prices(monkeypatch, buy, sell, mode):
    service, asked = _service(monkeypatch, {})
    with pytest.raises(ValueError):
        pulse_views.check_live(service, 'token', NAME, buy, sell, mode)
    assert asked == []


# ---- Gjallarhorn's liquidity --------------------------------------------------------------------

def test_steam_liquidity_from_pulse(monkeypatch):
    service, _ = _service(monkeypatch, {
        ('Steam', 'Sell'): [_row('Case', 1.00, sales={'Steam': 5000, 'Tm': 300})],
        ('Steam', 'Buy'): [_row('Case', 0.95)],
        ('Steam', 'Median30d'): [_row('Case', 1.25)],
    })
    view = pulse_views.steam_liquidity(service, 'token', ['Case', 'Unknown'])
    assert list(view) == ['Case']
    assert view['Case'] == {'volume7d': 5000, 'tm_volume7d': 300, 'lowest': 1.0, 'highest_buy': 0.95,
                            'median30d': 1.25, 'spreadPct': 5.0, 'vsMedianPct': -20.0}


class _SteamMarketThatMustNotBeAsked:
    def __init__(self):
        self.asked = []

    def liquidity(self, names):
        self.asked.append(sorted(names))
        return {name: {'volume7d': 7, 'trend7dPct': -3.0} for name in names}, 'fresh'


def test_gjallarhorn_asks_steam_only_for_items_pulse_does_not_know(monkeypatch):
    service, _ = _service(monkeypatch, {
        ('Steam', 'Sell'): [_row('Case', 1.00, sales={'Steam': 5000})],
        ('Steam', 'Buy'): [_row('Case', 0.95)],
        ('Steam', 'Median30d'): [_row('Case', 1.25)],
    })
    steam = _SteamMarketThatMustNotBeAsked()
    gjallarhorn = GjallarhornService(None, service, steam, None, None)
    pulse_views.steam_liquidity(service, 'token', ['Case'])                  # the tables are in memory
    holdings = [{'item_name': 'Case', 'current_price': 1.0, 'net_qty': 3},
                {'item_name': 'Rare sticker', 'current_price': 50.0, 'net_qty': 1}]
    views, status = gjallarhorn._liquidity('token', holdings, ['Case', 'Rare sticker'])
    assert steam.asked == [['Rare sticker']]
    assert views['Case']['source'] == 'pulse' and views['Case']['volume7d'] == 5000
    assert views['Case']['vsMedianPct'] == -20.0 and views['Rare sticker']['source'] == 'steam'
    assert status == 'pulse'
    row = gjallarhorn._row({**holdings[0], 'avg_cost': 2.0}, views['Case'], None)
    assert (row['volume7d'], row['vs_median_pct'], row['liquidity_source']) == (5000, -20.0, 'pulse')


def test_gjallarhorn_falls_back_to_steam_when_pulse_fails(monkeypatch):
    service, _ = _service(monkeypatch, {})

    def pulse_down(*args, **kwargs):
        raise OSError('pulse down')
    monkeypatch.setattr(service, '_table', pulse_down)
    steam = _SteamMarketThatMustNotBeAsked()
    gjallarhorn = GjallarhornService(None, service, steam, None, None)
    holdings = [{'item_name': 'Case', 'current_price': 1.0}]
    assert gjallarhorn._liquidity('token', holdings, ['Case']) == ({}, 'warming')   # starts the warm
    for _ in range(100):
        if pulse_views._liquidity_failed['at'] and not pulse_views._liquidity_warming.locked():
            break
        time.sleep(0.05)
    views, _ = gjallarhorn._liquidity('token', holdings, ['Case'])
    assert steam.asked == [['Case']] and views['Case']['source'] == 'steam'
    pulse_views._liquidity_failed['at'] = None


def test_gjallarhorn_never_waits_on_a_download_and_does_not_ask_steam_meanwhile(monkeypatch):
    service, asked = _service(monkeypatch, {
        ('Steam', 'Sell'): [_row('Case', 1.00, sales={'Steam': 5000})],
        ('Steam', 'Buy'): [_row('Case', 0.95)],
        ('Steam', 'Median30d'): [_row('Case', 1.25)],
    })
    steam = _SteamMarketThatMustNotBeAsked()
    gjallarhorn = GjallarhornService(None, service, steam, None, None)
    views, status = gjallarhorn._liquidity('token', [{'item_name': 'Case', 'current_price': 1.0}], ['Case'])
    assert (views, status) == ({}, 'warming') and steam.asked == []
    for _ in range(100):                                                     # the background warm
        if all(service.pulse.peek(*key) for key in pulse_views.STEAM_LIQUIDITY_TABLES):
            break
        time.sleep(0.05)
    views, status = gjallarhorn._liquidity('token', [{'item_name': 'Case', 'current_price': 1.0}], ['Case'])
    assert status == 'pulse' and views['Case']['volume7d'] == 5000


def test_listed_on_steam_without_a_weekly_count_means_none_sold(monkeypatch):
    service, _ = _service(monkeypatch, {('Steam', 'Sell'): [_row('Quiet sticker', 3.0)]})
    assert pulse_views.steam_liquidity(service, 'token', ['Quiet sticker'])['Quiet sticker']['volume7d'] == 0


def test_check_live_lists_on_a_market_the_way_the_row_was_priced(monkeypatch):
    service, asked = _service(monkeypatch, {('Buff', 'Sell'): [_row(NAME, 25.0)], ('LisSkins', 'Sell'): [_row(NAME, 27.0)]})
    answer = pulse_views.check_live(service, 'token', NAME, 'Buff', 'LisSkins', 'min')
    assert ('LisSkins', 'Sell', NAME) in asked and answer['sell']['price'] == 27.0


# ---- Pulse feed page ----------------------------------------------------------------------------

def test_the_page_offers_every_market_table_but_never_estimates_or_the_csfloat_sweep():
    choices = {c['market']: [o['price_type'] for o in c['price_types']] for c in pulse_views.pulse_choices()}
    assert 'TradeOnMarket' not in choices
    assert choices['Steam'] == ['Sell', 'Buy', 'Median30d', 'Average30d']
    assert choices['LisSkins'] == ['SellWithoutHold']
    assert choices['CsFloat'] == ['Sell']
    assert choices['SkinSwapTrade'] == ['Buy']


def test_parse_kept_accepts_offered_tables_only():
    assert pulse_views.parse_kept(['Steam:Sell', ['Buff', 'Buy'], 'Steam:Sell']) == [('Steam', 'Sell'), ('Buff', 'Buy')]
    with pytest.raises(ValueError):
        pulse_views.parse_kept(['SkinSwapTrade:EstimatedSell'])
    with pytest.raises(ValueError):
        pulse_views.parse_kept(['Steam'])


def test_at_most_twelve_tables_can_be_kept_fresh():
    choices = [f"{c['market']}:{o['price_type']}" for c in pulse_views.pulse_choices() for o in c['price_types']]
    assert len(pulse_views.parse_kept(choices[:12])) == 12
    with pytest.raises(ValueError):
        pulse_views.parse_kept(choices[:13])
    with pytest.raises(ValueError):
        pulse_views.parse_kept([{'market': 'Steam'}])


def test_the_page_downloads_offered_tables_and_tables_already_used_only():
    service = HuginnService(None, None)
    assert pulse_views.downloadable('Steam', 'Sell', service) == ('Steam', 'Sell')
    with pytest.raises(ValueError):
        pulse_views.downloadable('LisSkins', 'Sell', service)
    service.pulse._counts[('LisSkins', 'Sell')] = {**pulse_feed._new_counts(), 'downloads': 1, 'reads': 1}
    assert pulse_views.downloadable('LisSkins', 'Sell', service) == ('LisSkins', 'Sell')
    with pytest.raises(ValueError):
        pulse_views.downloadable(['Steam'], 'Sell', service)


def test_saved_settings_reach_the_feed_and_bad_entries_are_skipped():
    service = HuginnService(None, None)
    keys, minutes = pulse_views.apply_kept_settings(service, {
        'pulse_kept_tables': ['Steam:Sell', 'Gone:Sell', 'Dmarket:Buy'], 'pulse_keep_minutes': 7})
    assert keys == [('Steam', 'Sell'), ('Dmarket', 'Buy')] and minutes == 10      # 7 is not offered
    assert service.pulse.kept() == ([('Steam', 'Sell'), ('Dmarket', 'Buy')], 10)


def test_the_case_arbitrage_loop_skips_a_switched_off_table_in_its_parallel_threads(monkeypatch):
    service, asked = _service(monkeypatch, {('CsFloat', 'Sell'): [_row('Fracture Case', 0.5)]})
    service.pulse.set_switched_off([('CsFloat', 'Sell')])
    with service.pulse.job('case-arbitrage-alerts'):
        service._refresh_markets('token', ('csfloat', 'steam'), parallel=True)
    assert ('CsFloat', 'Sell', None) not in asked and ('Steam', 'Sell', None) in asked
    assert service._container_state.get('csfloat') != 'error'


def test_switched_off_settings_accept_job_tables_only():
    service = HuginnService(None, None)
    service.pulse.declare_job('store-arbitrage', 'Store', 'every hour', lambda: [('ItTrade', 'Buy')])
    assert pulse_views.parse_switched_off(['ItTrade:Buy', 'Steam:Sell'], service) == [('ItTrade', 'Buy'), ('Steam', 'Sell')]
    with pytest.raises(ValueError):
        pulse_views.parse_switched_off(['Nowhere:Sell'], service)
    pulse_views.apply_kept_settings(service, {'pulse_switched_off_tables': ['ItTrade:Buy', 7]})
    assert service.pulse.switched_off() == [('ItTrade', 'Buy')]


def test_a_case_snapshot_refreshed_in_the_background_keeps_the_callers_job(monkeypatch):
    service, asked = _service(monkeypatch, {('CsFloat', 'Sell'): [_row('Fracture Case', 0.5)]})
    service.pulse.set_switched_off([('CsFloat', 'Sell')])
    service._refresh_container_bg('token', 'csfloat', job='case-arbitrage-alerts')
    assert asked == [] and service._container_state.get('csfloat') != 'error'
    service._refresh_container_bg('token', 'csfloat')                         # a page: downloads
    assert asked == [('CsFloat', 'Sell', None)]
