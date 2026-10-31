"""The slaves of the master pulse feed with the app's gate on (only Huginn Arbitrage downloads): each
one reads memory, says "not active" for a market no active profile keeps without an error or an
endless "refreshing", and takes the real prices once a profile for that market is made active.
Also Heimdall's price history across midnight and the age of pulse's seed. A fake pulse answers
every table; nothing reaches the network."""
import gzip
import json
import os
import time

import pytest

import price_history
import pulse_feed
import pulse_views
from harvest_service import HarvestService
from huginn_service import HuginnService
from store_arbitrage_service import SIDE_INSTANT, StoreArbitrageService

ROW = {'itemName': {'marketHashName': 'Item A'}, 'firstMarket': {'price': 1.0},
       'secondMarket': {'price': 2.0, 'totalOffersCount': 3}}


@pytest.fixture
def service(monkeypatch):
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    service.settings_provider = lambda: {}
    service.asked = []

    def post(url, token, body):
        service.asked.append((url.rsplit('/', 2)[1], body['secondMarketOptions']['secondMarketPriceType']))
        return [dict(ROW)]
    monkeypatch.setattr(service, '_post_tradeon', post)
    service.pulse.slaves_may_download = False                   # the app's gate
    return service


def _download(service, *keys):
    """What only Huginn Arbitrage (the keeper, Fetch live data) may do."""
    with service.pulse.reading(download=True):
        for key in keys:
            service._table('token', *key, pulse_feed.ON_DEMAND)
    service.pulse.flush_listeners()


# ---- Harvest ---------------------------------------------------------------------------------------

def test_harvest_says_nothing_is_wrong_for_a_market_not_active_and_picks_it_up_later(service):
    service.pulse.set_active([('Steam', 'Buy')], 5)
    _download(service, ('Steam', 'Buy'), ('Buff', 'Sell'))
    harvest = HarvestService(service, None)
    harvest._warm('token', ['CsMoneyTrade', 'Steam', harvest.REFERENCE])
    found, status = harvest._index_state('token', ['CsMoneyTrade', 'Steam'])
    assert status == 'fresh' and found['CsMoneyTrade'] == {} and found['Steam']
    harvest._indexes['CsMoneyTrade'] = (time.time() - 120, {})            # a minute later: a quiet look
    assert harvest._index_state('token', ['CsMoneyTrade', 'Steam'])[1] == 'fresh'
    service.pulse.set_active([('Steam', 'Buy'), ('CsMoneyTrade', 'Buy')], 5)
    _download(service, ('CsMoneyTrade', 'Buy'))
    harvest._warm('token', ['CsMoneyTrade'])
    assert harvest._index_state('token', ['CsMoneyTrade', 'Steam'])[0]['CsMoneyTrade']
    assert service.asked.count(('CsMoneyTrade', 'Buy')) == 1                # downloaded by Huginn Arbitrage only


# ---- Store Catalogue Arbitrage -----------------------------------------------------------------------

def test_store_catalogue_shows_a_market_not_active_as_empty_and_takes_it_once_active(service, tmp_path, monkeypatch):
    store = StoreArbitrageService(service, None, None, None, history_path=str(tmp_path / 'history.json.gz'))
    monkeypatch.setattr(store, '_names', lambda: {'Item A'})
    monkeypatch.setattr(store, '_aliases', lambda: {})
    pair = ('CsMoneyMarket', SIDE_INSTANT)
    store._warm('token', [pair])
    found, status, failed = store._index_state('token', [pair])
    assert (found[pair], status, failed) == ({}, 'fresh', [])
    store._indexes[pair] = (time.time() - 120, {})
    assert store._index_state('token', [pair])[1] == 'fresh'                # never "refreshing" forever
    service.pulse.set_active([('CsMoneyMarket', 'Buy')], 5)
    _download(service, ('CsMoneyMarket', 'Buy'))
    store._warm('token', [pair])
    assert store._index_state('token', [pair])[0][pair]                     # the real prices replace the marker


# ---- Case Arbitrage and Draupnir's valuation -------------------------------------------------------

def test_case_arbitrage_serves_its_last_prices_while_an_active_table_is_on_its_way(service):
    service.pulse.set_active([('Steam', 'Sell')], 5)
    service._container_cache['steam'] = (1.0, {'Fracture Case': {'price': 0.5}})   # restored from disk
    service._container_checked_at['steam'] = 0.0
    for _ in range(3):
        assert service._container_snapshot('token', 'steam')[1] == 'fresh'   # no "refreshing" spinner
    assert service.asked == []


def test_draupnir_says_when_its_valuation_market_is_not_active(service):
    service._refresh_prices_bg('token', 'lowest')
    assert service.prices_for_valuation('token', 'lowest')[1] == 'inactive'
    service._refresh_prices_bg('token', 'steam')
    assert service.prices_for_valuation('token', 'steam')[1] == 'inactive' and service.asked == []


def test_gjallarhorn_liquidity_outside_the_active_hours_is_not_warming_forever(service, monkeypatch):
    service.pulse.set_active([('Steam', 'Sell'), ('Steam', 'Buy')], 5)
    assert pulse_views.steam_liquidity(service, ['Item A']) is None          # the keeper will bring them
    monkeypatch.setattr(service.pulse, '_within_active_hours', lambda now: (False, now + 3600))
    with pytest.raises(pulse_feed.NotActive):
        pulse_views.steam_liquidity(service, ['Item A'])


# ---- the price history -------------------------------------------------------------------------------

class _Table:
    def __init__(self, market_id, price_type, prices, started_at):
        self.market_id, self.price_type, self._prices, self.started_at = market_id, price_type, prices, started_at

    def prices(self):
        return dict(self._prices)

    def index(self):
        return {name: {'price': price, 'count': 3, 'updated': None} for name, price in self._prices.items()}


def _saved(tmp_path, day):
    with gzip.open(tmp_path / 'Steam' / 'Sell' / f'{day}.json.gz', 'rt') as handle:
        return json.load(handle)


def test_a_table_from_before_midnight_finishing_late_is_added_to_its_own_day(tmp_path):
    midnight = time.mktime((2026, 11, 1, 0, 0, 0, 0, 0, -1))
    clock = [midnight - 600]
    history = price_history.PriceHistory(str(tmp_path), clock=lambda: clock[0])
    history.record(_Table('Steam', 'Sell', {'Case': 1.0}, midnight - 600))
    history.record(_Table('Steam', 'Sell', {'Case': 3.0}, midnight - 300))
    history.flush()
    clock[0] = midnight + 60
    history.record(_Table('Steam', 'Sell', {'Case': 5.0}, midnight + 1))      # the new day starts
    history.record(_Table('Steam', 'Sell', {'Case': 2.0}, midnight - 10))     # started before midnight, late
    history.flush()
    before, after = price_history._day(midnight - 600), price_history._day(midnight + 1)
    assert _saved(tmp_path, before)['Case'] == [2.0, 1.0, 3.0, 2.0, 3]          # three samples, none lost
    assert _saved(tmp_path, after)['Case'] == [5.0, 5.0, 5.0, 5.0, 1]


def test_the_seed_is_refreshed_monthly_and_an_old_one_is_no_longer_used(tmp_path):
    clock = [1_800_000_000.0]
    history = price_history.PriceHistory(str(tmp_path), clock=lambda: clock[0])
    history.record(_Table('Steam', 'Median30d', {'Case': 9.0}, clock[0]))
    os.utime(history._seed_path('Steam', 'Median30d'), (clock[0], clock[0]))
    assert history.statistic('Steam', 'Sell', names=['Case']) == {'Case': 9.0}
    assert ('Steam', 'Median30d') not in history.seeds_wanted(['Steam'])
    clock[0] += (price_history.SEED_MAX_AGE_DAYS + 1) * 86400
    assert ('Steam', 'Median30d') in history.seeds_wanted(['Steam'])             # asked again while active
    clock[0] += (price_history.SEED_TRUSTED_DAYS - price_history.SEED_MAX_AGE_DAYS) * 86400
    history._statistics.clear()
    assert history.statistic('Steam', 'Sell', names=['Case']) == {}             # too old: not used


def test_a_seed_pulse_refuses_is_not_asked_again_after_a_restart(tmp_path):
    history = price_history.PriceHistory(str(tmp_path))
    history.seed_unavailable(('Buff', 'Median30d'))
    again = price_history.PriceHistory(str(tmp_path))
    assert ('Buff', 'Median30d') not in again.seeds_wanted(['Buff'])
    assert ('Buff', 'Average30d') in again.seeds_wanted(['Buff'])
