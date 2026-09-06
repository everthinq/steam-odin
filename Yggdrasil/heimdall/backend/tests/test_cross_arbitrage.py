"""Unit tests for CrossArbitrageService — the cross-profile arbitrage engine.

These use fakes for Huginn (pricing) and Draupnir (holdings) so nothing touches
pulse or the network: they pin the best-route math, fee netting, per-account
holders (arbitrage legs excluded), the potential-profit-across-owned-units, and
the request-time min-profit filter on the cached scan.
"""
import time

from cross_arbitrage_service import CrossArbitrageService


class FakeHuginn:
    """Minimal stand-in exposing exactly what CrossArbitrageService calls."""
    def __init__(self):
        # buy = min listing per price-map slug. M4 is priced on lisskins + buff so a
        # LisSkins->Buff->CSFloat chain has BOTH legs priced (leg 2 buys on Buff).
        self._buy = {
            'lisskins': {'AK': 10.0, 'AWP': 100.0, 'M4': 20.0},
            'buff': {'AK': 9.5, 'AWP': 105.0, 'M4': 22.0},
            'csfloat': {'AK': 9.8},
            'dmarket': {},
            'steam': {'AWP': 98.0},
        }
        # autobuy (buy-order) per market id
        self._auto = {
            'Buff': {'AK': {'price': 11.0, 'count': 5}, 'M4': {'price': 25.0, 'count': 4}},
            'CsMoneyTrade': {'AK': {'price': 12.0, 'count': 3}, 'AWP': {'price': 120.0, 'count': 1}},
            'CsMoneyMarket': {},
            'CsFloat': {'AWP': {'price': 130.0, 'count': 2}},   # (unused: CSFloat autobuy comes from the sweep)
        }
        self._fees = {'Buff': 0.015, 'CsMoneyTrade': 0.0, 'CsMoneyMarket': 0.0, 'CsFloat': 0.02}
        self._disp = {'Buff': 'Buff163', 'CsMoneyTrade': 'CSMoney (Trade)',
                      'CsMoneyMarket': 'CSMoney (Market)', 'CsFloat': 'CSFloat',
                      'LisSkins': 'LisSkins', 'Dmarket': 'DMarket', 'Steam': 'Steam'}
        # Which market ids exist + which of them can be sold into (autobuy). Drives
        # config validation exactly like HuginnService's registry does in production.
        self._autobuy = {'Buff', 'CsMoneyTrade', 'CsMoneyMarket', 'CsFloat', 'Dmarket', 'Steam'}
        self._ids = {'LisSkins', 'Buff', 'CsFloat', 'Dmarket', 'Steam',
                     'CsMoneyTrade', 'CsMoneyMarket'}

    def price_map(self, token, market, force=False):
        return dict(self._buy.get(market, {}))

    def market_buy_index(self, token, market_id):
        # Only the five price-map markets are priced in this fake; anything else is
        # a non-cached market and returns nothing (a real pulse pull in production).
        return {}

    def market_autobuy_index(self, token, market_id):
        if market_id in self._autobuy_via_sweep():
            cache = self.get_csfloat_buy_orders_cache()
            return {n: {'price': o['price'], 'count': o.get('qty')}
                    for n, o in cache['by_name'].items()}
        return dict(self._auto.get(market_id, {}))

    def _autobuy_via_sweep(self):
        return {'CsFloat'}

    def market_fee(self, market_id, settings=None):
        return self._fees.get(market_id, 0.0)

    def market_display(self, market_id):
        return self._disp.get(market_id, market_id)

    def market_ids(self):
        return set(self._ids)

    def market_registry(self, settings=None):
        return [{'id': mid, 'display': self._disp.get(mid, mid), 'hasAutobuy': mid in self._autobuy}
                for mid in self._ids]

    def get_csfloat_buy_orders_cache(self):
        return {'by_name': {'AWP': {'price': 130.0}, 'M4': {'price': 30.0}},
                'count': 2, 'complete': True, 'updated_at': 'now'}


class FakeDraupnir:
    def combined_ledger(self, prices=None):
        return {
            'holdings': [
                {'item_name': 'AK', 'net_qty': 5},
                {'item_name': 'AWP', 'net_qty': 2},
                {'item_name': 'GLOVES', 'net_qty': 1},   # held but no autobuy -> excluded from rows
            ],
            'transactions': [
                {'item_name': 'AK', 'type': 'buy', 'qty': 5, 'account': 'main'},
                {'item_name': 'AWP', 'type': 'buy', 'qty': 3, 'account': 'alt'},
                {'item_name': 'AWP', 'type': 'sell', 'qty': 1, 'account': 'alt'},
                # an arbitrage leg must NOT count toward held quantity / holders
                {'item_name': 'AWP', 'type': 'buy', 'qty': 9, 'account': 'ghost', 'is_arbitrage': True},
            ],
        }


def _svc():
    return CrossArbitrageService(FakeHuginn(), FakeDraupnir())


def _by_name(rows):
    return {r['item_name']: r for r in rows}


def test_best_route_and_fees():
    base = _svc()._compute('tok', owned_only=True, settings={})
    rows = _by_name(base['rows'])
    # AK: cheapest buy = Buff163 9.5; best sell = CSMoney(Trade) 12.0 (fee 0) net 12.0
    ak = rows['AK']
    assert ak['buy_market_display'] == 'Buff163' and ak['buy_price'] == 9.5
    assert ak['sell_market_display'] == 'CSMoney (Trade)' and ak['sell_net'] == 12.0
    assert ak['profit'] == 2.5 and ak['profit_pct'] == 26.32
    # AWP: cheapest buy = Steam 98.0; CSFloat autobuy 130 net 130*0.98=127.4 beats CSMoney 120
    awp = rows['AWP']
    assert awp['buy_market_display'] == 'Steam' and awp['buy_price'] == 98.0
    assert awp['sell_market_display'] == 'CSFloat' and awp['sell_net'] == 127.4
    assert awp['profit'] == 29.4 and awp['profit_pct'] == 30.0


def test_item_without_autobuy_is_excluded():
    rows = _by_name(_svc()._compute('tok', owned_only=True, settings={})['rows'])
    assert 'GLOVES' not in rows   # held, but no market has an autobuy for it


def test_holders_exclude_arbitrage_and_sum_net():
    rows = _by_name(_svc()._compute('tok', owned_only=True, settings={})['rows'])
    assert rows['AK']['holders'] == [{'account': 'main', 'qty': 5}]
    # AWP: 3 bought - 1 sold on 'alt' = 2; the is_arbitrage 'ghost' buy is ignored
    assert rows['AWP']['holders'] == [{'account': 'alt', 'qty': 2}]


def test_potential_profit_scales_with_owned_units():
    rows = _by_name(_svc()._compute('tok', owned_only=True, settings={})['rows'])
    assert rows['AK']['owned_qty'] == 5 and rows['AK']['potential_profit'] == 12.5   # 2.5 * 5
    assert rows['AWP']['owned_qty'] == 2 and rows['AWP']['potential_profit'] == 58.8  # 29.4 * 2


def test_rows_sorted_by_profit_pct_desc():
    rows = _svc()._compute('tok', owned_only=True, settings={})['rows']
    pcts = [r['profit_pct'] for r in rows]
    assert pcts == sorted(pcts, reverse=True)
    assert rows[0]['item_name'] == 'AWP'   # 30% beats AK's 26.32%


def test_summary_counts_and_potential():
    base = _svc()._compute('tok', owned_only=True, settings={})
    svc = _svc()
    summary = svc._summary(base['rows'], base['owned'])
    assert summary['rows'] == 2 and summary['profitable'] == 2
    assert summary['owned_items_total'] == 3          # AK, AWP, GLOVES all held
    assert summary['best_profit_pct'] == 30.0
    assert summary['total_potential_profit'] == 71.3  # 12.5 + 58.8


def test_scan_applies_min_profit_filter_on_cache():
    svc = _svc()
    buy, sell, chains = svc._config_from_settings(None)   # default markets, no chains
    base = svc._compute('tok', owned_only=True, config=(buy, sell, chains))
    sig = (True, tuple(buy), tuple(sell), tuple((c['id'], tuple(c['markets'])) for c in chains))
    svc._cache[sig] = (time.time(), base)             # seed a fresh cache (no warm)
    out = svc.scan('tok', owned_only=True, min_profit_pct=28.0)
    assert out['status'] == 'fresh'
    names = {r['item_name'] for r in out['rows']}
    assert names == {'AWP'}                              # AK (26.32%) filtered out


def test_scan_without_token_reports_no_token():
    out = _svc().scan('', owned_only=True)
    assert out['status'] == 'no_token' and out['rows'] == []


# --- configurable markets + chains ------------------------------------------

class FakeSettingsManager:
    """Captures what save_config persists, like SettingsManager.save_settings."""
    def __init__(self):
        self.saved = {}

    def save_settings(self, new_settings):
        self.saved.update(new_settings)
        return True


def test_config_defaults_when_settings_empty():
    buy, sell, chains = _svc()._config_from_settings({})
    assert buy == ['LisSkins', 'Buff', 'CsFloat', 'Dmarket', 'Steam']
    assert sell == ['Buff', 'CsMoneyTrade', 'CsMoneyMarket', 'CsFloat']
    assert chains == []


def test_save_config_validates_and_persists():
    svc = _svc()
    manager = FakeSettingsManager()
    out = svc.save_config({
        'buy_markets': ['LisSkins', 'Buff', 'NopeMarket', 'LisSkins'],   # unknown + dup dropped
        'sell_markets': ['CsFloat', 'LisSkins'],                          # LisSkins has no autobuy -> dropped
        'chains': [
            {'markets': ['LisSkins', 'CsFloat']},                        # ok (id + name auto-filled)
            {'markets': ['LisSkins']},                                   # < 2 markets -> dropped
            {'markets': ['Nope', 'AlsoNope']},                           # all invalid -> dropped
        ],
    }, manager)
    assert out['buy_markets'] == ['LisSkins', 'Buff']
    assert out['sell_markets'] == ['CsFloat']
    assert len(out['chains']) == 1
    chain = out['chains'][0]
    assert chain['markets'] == ['LisSkins', 'CsFloat']
    assert chain['id'] and chain['name'] == 'LisSkins → CSFloat'
    # persisted through the settings manager, under the settings keys
    assert manager.saved['cross_arb_buy_markets'] == ['LisSkins', 'Buff']
    assert manager.saved['cross_arb_sell_markets'] == ['CsFloat']
    assert len(manager.saved['cross_arb_chains']) == 1


def test_save_config_busts_warm_cache():
    svc = _svc()
    svc._cache['x'] = (time.time(), {'rows': []})   # a stale warmed result
    svc.save_config({'buy_markets': ['LisSkins'], 'sell_markets': ['CsFloat'], 'chains': []},
                    FakeSettingsManager())
    assert svc._cache == {}                          # cleared so the next scan recomputes


def _chain_config():
    return (['LisSkins', 'Buff', 'CsFloat'], ['Buff', 'CsFloat'],
            [{'id': 'c1', 'name': 'LisSkins → Buff → CSFloat',
              'markets': ['LisSkins', 'Buff', 'CsFloat']}])


def test_chain_concatenates_two_legs():
    base = _svc()._compute('tok', owned_only=False, config=_chain_config())
    assert len(base['chains']) == 1
    chain = base['chains'][0]
    assert chain['name'] == 'LisSkins → Buff → CSFloat'
    assert [m['display'] for m in chain['markets']] == ['LisSkins', 'Buff163', 'CSFloat']
    rows = {r['item_name']: r for r in chain['rows']}
    # M4 is priced on every hop, so BOTH legs resolve and the totals concatenate.
    m4 = rows['M4']
    assert len(m4['legs']) == 2
    leg1, leg2 = m4['legs']
    assert leg1['buy_market_display'] == 'LisSkins' and leg1['buy_price'] == 20.0
    assert leg1['sell_market_display'] == 'Buff163' and leg1['sell_gross'] == 25.0 and leg1['profit'] == 4.625
    assert leg2['buy_market_display'] == 'Buff163' and leg2['buy_price'] == 22.0
    assert leg2['sell_market_display'] == 'CSFloat' and leg2['sell_gross'] == 30.0 and leg2['profit'] == 7.4
    assert m4['total_profit'] == 12.025 and m4['total_profit_pct'] == 28.63


def test_chain_leaves_unpriced_leg_as_none():
    chain = _svc()._compute('tok', owned_only=False, config=_chain_config())['chains'][0]
    rows = {r['item_name']: r for r in chain['rows']}
    # AWP has no Buff buy order (leg 1 unpriced) but sells into CSFloat (leg 2 priced),
    # so the row still shows with the first leg blanked out.
    awp = rows['AWP']
    assert awp['legs'][0] is None
    assert awp['legs'][1] is not None and awp['legs'][1]['sell_market_display'] == 'CSFloat'


def test_scan_filters_chain_rows_by_min_pct():
    svc = _svc()
    settings = {
        'cross_arb_buy_markets': ['LisSkins', 'Buff', 'CsFloat'],
        'cross_arb_sell_markets': ['Buff', 'CsFloat'],
        'cross_arb_chains': [{'id': 'c1', 'name': 'LisSkins → Buff → CSFloat',
                              'markets': ['LisSkins', 'Buff', 'CsFloat']}],
    }
    buy, sell, chains = svc._config_from_settings(settings)
    base = svc._compute('tok', owned_only=False, config=(buy, sell, chains))
    sig = (False, tuple(buy), tuple(sell), tuple((c['id'], tuple(c['markets'])) for c in chains))
    svc._cache[sig] = (time.time(), base)            # seed a fresh cache (no warm)
    out = svc.scan('tok', owned_only=False, min_profit_pct=25.0, settings=settings)
    assert out['status'] == 'fresh'
    chain = out['chains'][0]
    assert all(r['total_profit_pct'] >= 25.0 for r in chain['rows'])
    names = {r['item_name'] for r in chain['rows']}
    assert 'M4' in names and 'AK' not in names       # M4 28.63% stays; AK ~8.35% filtered
