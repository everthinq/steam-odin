"""Views on the master pulse feed (pulse_views.py): Price dips, "check live" on one Arbitrage row and
Gjallarhorn's liquidity, all from memory and Heimdall's own price history. A fake pulse answers by
(market, price type); nothing reaches the network."""
import time

import pytest

import price_history
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


def _in_memory(service, *keys):
    """Download *keys* the way only Huginn Arbitrage may (the keeper or Fetch live data), then let
    the listeners (the price history's seed) see them."""
    with service.pulse.reading(download=True):
        for key in keys:
            service._table('token', *key, pulse_feed.ON_DEMAND)
    service.pulse.flush_listeners()


def _gated(service):
    service.pulse.slaves_may_download = False
    return service


# ---- Price dips (memory + the price history) ------------------------------------------------------

def test_price_dips_compares_each_listing_with_its_30_day_median(monkeypatch):
    service, _ = _service(monkeypatch, {
        ('Steam', 'Sell'): [_row('Dip', 6.0, sales={'Steam': 120, 'Tm': 4}), _row('Spike', 13.0), _row('No median', 1.0)],
        ('Steam', 'Median30d'): [_row('Dip', 10.0), _row('Spike', 10.0)],
        ('Steam', 'Buy'): [_row('Dip', 5.5)],
    })
    _gated(service)
    service.pulse.set_active([('Steam', 'Sell'), ('Steam', 'Buy')], 5)
    _in_memory(service, ('Steam', 'Median30d'), ('Steam', 'Sell'), ('Steam', 'Buy'))    # the seed, then the tables
    answer = pulse_views.price_dips(service, 'token', 'Steam')
    assert [row['name'] for row in answer['rows']] == ['Dip', 'Spike']      # deepest dip first
    dip = answer['rows'][0]
    assert dip['vs_basis_percent'] == -40.0 and dip['basis'] == 10.0 and dip['instant'] == 5.5
    assert (dip['sold_week'], dip['steam_sold_week'], dip['tm_sold_week']) == (120, 120, 4)
    assert dip['updated'] == 1700000000 and 'image' not in dip
    assert answer['rows'][1]['vs_basis_percent'] == 30.0
    assert answer['fee'] == pytest.approx(0.13) and answer['has_instant']
    assert answer['history'] == {'days': 0, 'seeded': {'Median30d': True, 'Average30d': False}}
    assert [market['id'] for market in pulse_views.price_dips_markets(service)] == ['Buff', 'Steam']   # + the reference


def test_price_dips_never_downloads_and_says_when_a_market_is_not_active(monkeypatch):
    service, asked = _service(monkeypatch, {})
    _gated(service)
    with pytest.raises(pulse_feed.NotActive):
        pulse_views.price_dips(service, 'token', 'Steam')
    with pytest.raises(ValueError):
        pulse_views.price_dips(service, 'token', 'Steam', basis='Median90d')
    assert asked == []


def test_the_history_replaces_the_seed_after_enough_days(tmp_path):
    clock = [time.time()]                    # the seed's age is its file's date
    history = price_history.PriceHistory(str(tmp_path), clock=lambda: clock[0])

    class Table:
        def __init__(self, price_type, prices, started_at):
            self.market_id, self.price_type, self._prices, self.started_at = 'Steam', price_type, prices, started_at

        def prices(self):
            return dict(self._prices)

        def quotes(self):
            return {name: (price, 3, None) for name, price in self._prices.items()}
    history.record(Table('Median30d', {'Case': 9.0, 'Sticker': 2.0}, clock[0]))
    for day in range(price_history.MIN_DAYS):
        for sample in (1.0, 3.0):                                            # the day's average: 2.0
            history.record(Table('Sell', {'Case': sample + day}, clock[0]))
        history.flush()
        clock[0] += 86400
    medians = history.statistic('Steam', 'Sell', 'Median30d')
    assert medians['Case'] == 5.0 and medians['Sticker'] == 2.0              # own history; seed for the rest
    assert history.statistic('Steam', 'Sell', 'Average30d', ['Case']) == {'Case': 5.0}
    assert history.coverage('Steam', 'Sell')['days'] == price_history.MIN_DAYS
    saved = sorted(path.name for path in (tmp_path / 'Steam' / 'Sell').iterdir())
    assert len(saved) == price_history.MIN_DAYS                               # one file a day, kept


def test_a_restart_keeps_the_days_samples(tmp_path):
    clock = [1_800_000_000.0]

    class Table:
        market_id, price_type = 'Steam', 'Buy'

        def __init__(self, price):
            self.started_at, self.price = clock[0], price

        def prices(self):
            return {'Case': self.price}

        def quotes(self):
            return {'Case': (self.price, 3, None)}
    first = price_history.PriceHistory(str(tmp_path), clock=lambda: clock[0])
    first.record(Table(1.0))
    first.flush()
    second = price_history.PriceHistory(str(tmp_path), clock=lambda: clock[0])   # after a restart
    second.record(Table(3.0))
    second.flush()
    day = price_history._day(clock[0])
    import gzip
    import json
    with gzip.open(tmp_path / 'Steam' / 'Buy' / f'{day}.json.gz', 'rt') as handle:
        assert json.load(handle)['Case'] == [2.0, 1.0, 3.0, 3.0, 2]


def test_seeds_are_wanted_once_for_markets_with_30_day_prices(tmp_path):
    history = price_history.PriceHistory(str(tmp_path))
    assert history.seeds_wanted(['Steam', 'CsFloat', 'Steam']) == [('Steam', 'Median30d'), ('Steam', 'Average30d')]


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

def test_steam_liquidity_from_memory_and_the_history(monkeypatch):
    service, asked = _service(monkeypatch, {
        ('Steam', 'Sell'): [_row('Case', 1.00, sales={'Steam': 5000, 'Tm': 300})],
        ('Steam', 'Buy'): [_row('Case', 0.95)],
        ('Steam', 'Median30d'): [_row('Case', 1.25)],
    })
    _gated(service)
    _in_memory(service, ('Steam', 'Median30d'), ('Steam', 'Sell'), ('Steam', 'Buy'))
    view = pulse_views.steam_liquidity(service, ['Case', 'Unknown'])
    assert list(view) == ['Case']
    assert view['Case'] == {'volume7d': 5000, 'tm_volume7d': 300, 'lowest': 1.0, 'highest_buy': 0.95,
                            'median30d': 1.25, 'spreadPct': 5.0, 'vsMedianPct': -20.0}
    assert len(asked) == 3                                                    # the downloads above, no more


class _SteamMarketThatMustNotBeAsked:
    def __init__(self):
        self.asked = []

    def liquidity(self, names):
        self.asked.append(sorted(names))
        return {name: {'volume7d': 7, 'trend7dPct': -3.0} for name in names}, 'fresh'


def _steam_in_memory(monkeypatch):
    service, _ = _service(monkeypatch, {
        ('Steam', 'Sell'): [_row('Case', 1.00, sales={'Steam': 5000})],
        ('Steam', 'Buy'): [_row('Case', 0.95)],
        ('Steam', 'Median30d'): [_row('Case', 1.25)],
    })
    _gated(service)
    service.pulse.set_active([('Steam', 'Sell'), ('Steam', 'Buy')], 5)
    _in_memory(service, ('Steam', 'Median30d'), ('Steam', 'Sell'), ('Steam', 'Buy'))
    return service


def test_gjallarhorn_asks_steam_only_for_items_pulse_does_not_know(monkeypatch):
    service = _steam_in_memory(monkeypatch)
    steam = _SteamMarketThatMustNotBeAsked()
    gjallarhorn = GjallarhornService(None, service, steam, None, None)
    holdings = [{'item_name': 'Case', 'current_price': 1.0, 'net_qty': 3},
                {'item_name': 'Rare sticker', 'current_price': 50.0, 'net_qty': 1}]
    views, status = gjallarhorn._liquidity('token', holdings, ['Case', 'Rare sticker'])
    assert steam.asked == [['Rare sticker']]
    assert views['Case']['source'] == 'pulse' and views['Case']['volume7d'] == 5000
    assert views['Case']['vsMedianPct'] == -20.0 and views['Rare sticker']['source'] == 'steam'
    assert status == 'pulse'
    row = gjallarhorn._row({**holdings[0], 'avg_cost': 2.0}, views['Case'], None)
    assert (row['volume7d'], row['vs_median_pct'], row['liquidity_source']) == (5000, -20.0, 'pulse')


def test_gjallarhorn_never_asks_steam_when_no_active_profile_keeps_steam(monkeypatch):
    service, asked = _service(monkeypatch, {})
    _gated(service)
    steam = _SteamMarketThatMustNotBeAsked()
    gjallarhorn = GjallarhornService(None, service, steam, None, None)
    views, status = gjallarhorn._liquidity('token', [{'item_name': 'Case', 'current_price': 1.0}], ['Case'])
    assert (views, status) == ({}, 'inactive') and steam.asked == [] and asked == []


def test_the_trade_price_is_the_middle_of_listing_and_buy_orders(tmp_path):
    clock = [time.time()]                    # the seed's age is its file's date
    history = price_history.PriceHistory(str(tmp_path), clock=lambda: clock[0])

    class Table:
        market_id = 'Steam'

        def __init__(self, price_type, prices):
            self.price_type, self._prices, self.started_at = price_type, prices, clock[0]

        def prices(self):
            return dict(self._prices)

        def quotes(self):
            return {name: (price, 3, None) for name, price in self._prices.items()}
    history.record(Table('Median30d', {'Seeded': 5.0}))
    assert history.trade_statistic('Steam', 'Sell', 'Buy', names=['Seeded']) == {'Seeded': 5.0}   # the seed alone
    for _ in range(price_history.MIN_DAYS):
        history.record(Table('Sell', {'Case': 1.2}))
        history.record(Table('Buy', {'Case': 1.0}))
        history.flush()
        clock[0] += 86400
    assert history.trade_statistic('Steam', 'Sell', 'Buy', names=['Case']) == {'Case': 1.1}


def test_a_failing_one_time_download_is_given_up(monkeypatch):
    service, asked = _service(monkeypatch, {})

    def refused(url, token, body):
        raise OSError('needs the Table subscription')
    monkeypatch.setattr(service, '_post_tradeon', refused)
    service.pulse.download_once([('Buff', 'Median30d')])
    for _ in range(pulse_feed.ONCE_TRIES):
        service.pulse._keeper_failed.clear()
        service.pulse.keep_fresh_once('token')
    assert service.pulse.status()['keeper']['once_pending'] == []


def test_the_active_settings_are_really_saved():
    """The real settings manager keeps only keys with a default: every active-profile key has one."""
    from settings import SettingsManager
    saved = SettingsManager._convert_updates({'huginn_active_profiles': ['LisSkins:Steam:autobuy'],
                                              'huginn_active_minutes': 10,
                                              'huginn_active_hours': {'enabled': True, 'start': '08:00', 'end': '01:00'}})
    assert set(saved) == {'huginn_active_profiles', 'huginn_active_minutes', 'huginn_active_hours'}
    assert pulse_views.saved_profiles({'huginn_active_profiles': None, 'huginn_kept_profiles': ['A:B:min']}) == ['A:B:min']
    assert pulse_views.saved_profiles({'huginn_active_profiles': [], 'huginn_kept_profiles': ['A:B:min']}) == []
    assert pulse_views.saved_minutes({'huginn_active_minutes': None, 'huginn_profile_keep_minutes': 30}) == 30


def test_gjallarhorn_waits_for_the_keeper_after_a_restart_and_shows_the_last_figures(monkeypatch):
    service = _steam_in_memory(monkeypatch)
    steam = _SteamMarketThatMustNotBeAsked()
    gjallarhorn = GjallarhornService(None, service, steam, None, None)
    holdings = [{'item_name': 'Case', 'current_price': 1.0}]
    gjallarhorn._liquidity('token', holdings, ['Case'])                       # remembered
    service.pulse._tables.clear()                                            # restarted: active, not in memory
    views, status = gjallarhorn._liquidity('token', holdings, ['Case'])
    assert status == 'warming' and views['Case']['volume7d'] == 5000 and steam.asked == []


def test_listed_on_steam_without_a_weekly_count_means_none_sold(monkeypatch):
    service, _ = _service(monkeypatch, {('Steam', 'Sell'): [_row('Quiet sticker', 3.0)], ('Steam', 'Buy'): []})
    _in_memory(service, ('Steam', 'Sell'), ('Steam', 'Buy'))
    assert pulse_views.steam_liquidity(service, ['Quiet sticker'])['Quiet sticker']['volume7d'] == 0


def test_check_live_lists_on_a_market_the_way_the_row_was_priced(monkeypatch):
    service, asked = _service(monkeypatch, {('Buff', 'Sell'): [_row(NAME, 25.0)], ('LisSkins', 'Sell'): [_row(NAME, 27.0)]})
    answer = pulse_views.check_live(service, 'token', NAME, 'Buff', 'LisSkins', 'min')
    assert ('LisSkins', 'Sell', NAME) in asked and answer['sell']['price'] == 27.0


# ---- Case Arbitrage reads memory only --------------------------------------------------------------

def test_the_case_arbitrage_loop_reads_memory_and_never_downloads(monkeypatch):
    service, asked = _service(monkeypatch, {('CsFloat', 'Sell'): [_row('Fracture Case', 0.5)]})
    _gated(service)
    service._refresh_markets('token', ('csfloat', 'steam'), parallel=True)
    assert asked == [] and service._container_state.get('csfloat') == 'inactive'
    _in_memory(service, ('CsFloat', 'Sell'))
    service._refresh_container_bg('token', 'csfloat', job='case-arbitrage-alerts')
    assert len(asked) == 1 and service._container_state.get('csfloat') == 'ok'


def test_a_new_alert_market_table_wakes_the_case_loop(monkeypatch):
    service, _ = _service(monkeypatch, {('CsFloat', 'Sell'): [_row('Fracture Case', 0.5)], ('Dmarket', 'Sell'): []})
    _in_memory(service, ('Dmarket', 'Sell'))
    assert not service._case_wake.is_set()                                    # not an alert market
    _in_memory(service, ('CsFloat', 'Sell'))
    assert service._case_wake.is_set()


def test_a_case_snapshot_of_the_daily_reference_is_fresh_after_a_memory_read(monkeypatch):
    service, asked = _service(monkeypatch, {('Buff', 'Sell'): [_row('Fracture Case', 0.5)]})
    _gated(service)
    service.pulse.set_active([], 5)                                          # Buff163: the daily reference
    _in_memory(service, ('Buff', 'Sell'))
    for table in service.pulse._tables.values():
        table.started_at -= 6 * 3600                                         # hours old
    service._refresh_one('token', 'buff')
    assert service._container_snapshot('token', 'buff')[1] == 'fresh'       # not "refreshing" forever


def test_sold_out_and_stale_prices_are_never_recorded(tmp_path):
    history = price_history.PriceHistory(str(tmp_path), clock=lambda: 1_800_000_000.0)

    class Table:
        market_id, price_type, started_at = 'Steam', 'Sell', 1_800_000_000.0

        def quotes(self):
            return {'Live': (2.0, 4, self.started_at - 60),
                    'Sold out': (9.0, 0, self.started_at - 60),
                    'Frozen': (7.0, 2, self.started_at - 3 * 86400)}
    history.record(Table())
    assert list(history._today[('Steam', 'Sell')]['items']) == ['Live']


def test_steam_liquidity_needs_only_the_listing(monkeypatch):
    service, _ = _service(monkeypatch, {('Steam', 'Sell'): [_row('Case', 1.0, sales={'Steam': 50})]})
    _gated(service)
    service.pulse.set_active([('Steam', 'Sell')], 5)                       # a "min" profile: no buy orders
    _in_memory(service, ('Steam', 'Sell'))
    view = pulse_views.steam_liquidity(service, ['Case'])['Case']
    assert view['lowest'] == 1.0 and view['highest_buy'] is None and view['volume7d'] == 50


def test_an_old_snapshot_of_a_market_no_longer_active_goes(monkeypatch):
    service, _ = _service(monkeypatch, {})
    _gated(service)
    service.pulse.set_active([('Steam', 'Sell')], 5)
    service._container_cache['dmarket'] = (1.0, {'Fracture Case': {'price': 0.4}})     # restored from disk
    service._container_cache['steam'] = (1.0, {'Fracture Case': {'price': 0.5}})
    service._refresh_markets('token', ('dmarket', 'steam'))
    assert 'dmarket' not in service._container_cache and service._container_state['dmarket'] == 'inactive'
    assert service._container_cache['steam'][1] and service._container_state['steam'] == 'ok'   # active: kept meanwhile
