"""Every fee comes from ONE place: the Fees editor (settings huginn_market_fees),
read through HuginnService.market_fee — Case Arbitrage, LOOT.Farm and the
auctions included. Also: Case Arbitrage's new buy-only price sources."""
from huginn_service import HuginnService


def _service(fees=None):
    service = HuginnService(steam_service=None, ratatoskr_service=None)
    service.settings_provider = lambda: {'huginn_market_fees': fees or {}}
    return service


def test_market_fee_reads_the_current_settings_when_none_are_passed():
    service = _service({'Steam': 0.15})
    assert service.market_fee('Steam') == 0.15
    assert service.market_fee('Buff') == 0.015                   # registry default
    assert service.market_fee('Steam', {'huginn_market_fees': {'Steam': 0.1}}) == 0.1   # explicit wins


def test_market_fee_survives_a_broken_settings_provider():
    service = HuginnService(steam_service=None, ratatoskr_service=None)

    def broken():
        raise OSError('settings unreadable')

    service.settings_provider = broken
    assert service.market_fee('Steam') == 0.13


def test_case_arbitrage_flips_use_the_edited_fees():
    service = _service({'Steam': 0.15, 'Buff': 0.02, 'CsFloat': 0.03})
    assert service._container_sell_fees() == {'steam': 0.15, 'buff': 0.02, 'csfloat': 0.03}


def test_case_arbitrage_flip_math_uses_them(monkeypatch, tmp_path):
    service = _service({'CsFloat': 0.10})
    monkeypatch.setattr(service, '_load_case_history', lambda: {})
    monkeypatch.setattr(service, '_save_case_history', lambda history: None)
    containers = [{'name': 'Case X'}]
    snaps = {market: {} for market in service._CONTAINER_MARKETS}
    snaps['csmoney_trade'] = {'Case X': {'price': 1.00, 'count': 50}}    # trade balance: the cheapest buy
    snaps['csmoney_market'] = {'Case X': {'price': 1.20, 'count': 30}}
    snaps['dmarket'] = {'Case X': {'price': 0.50, 'count': 5}}          # unfillable: shown only
    snaps['csfloat'] = {'Case X': {'price': 2.00, 'count': 9}}
    snaps['steam'] = {'Case X': {'price': 2.10, 'count': 900}}           # 2.10 × 0.87 = 1.827
    history = {}
    monkeypatch.setattr(service, '_load_case_history', lambda: history)
    row = service._price_rows_recording_history(containers, snaps, '2026-09-30')[0]
    assert row['prices']['dmarket'] == 0.5                               # still displayed
    assert row['cheapest_market'] == 'csmoney_trade' and row['cheapest'] == 1.0
    # the history keeps its original markets: CS.MONEY Trade / Market (new) are not its low
    assert history['Case X']['2026-09-30']['lo'] == 2.0
    # CSFloat 2.00 × (1 − 0.10) = 1.80 < Steam 1.827: Steam wins with the edited CSFloat fee
    assert row['flip']['sell_market'] == 'steam' and row['flip']['net_sell'] == 1.83


def test_new_sites_are_buy_only_price_sources(monkeypatch):
    service = _service()
    assert {'csmoney_market', 'csmoney_trade', 'skinswap'} <= set(service._CONTAINER_MARKETS)
    assert set(service._CONTAINER_SELL_MARKETS) == {'steam', 'buff', 'csfloat'}
    pulled = []

    def pull(token, market_id, price_type):
        pulled.append((market_id, price_type))
        return [{'itemName': {'marketHashName': 'Case X'}, 'secondMarket': {'price': 0.8, 'totalOffersCount': 7}},
                {'itemName': {'marketHashName': 'Some Skin'}, 'secondMarket': {'price': 5.0, 'totalOffersCount': 1}}]

    monkeypatch.setattr(service, '_pull_market', pull)
    monkeypatch.setattr(service, '_container_names', lambda: {'Case X'})
    assert service._single_container_map('token', 'skinswap') == {'Case X': {'price': 0.8, 'count': 7}}
    assert pulled == [('SkinSwapMarket', 'Sell')]


def test_lootfarm_profiles_default_to_the_fees_editor(monkeypatch):
    service = _service({'LootFarm': 0.03})
    monkeypatch.setattr(service, '_post_tradeon',
                        lambda url, token, body=None: [{'itemName': {'marketHashName': 'A'}, 'firstMarket': {'price': 1.0}}])
    monkeypatch.setattr(service, '_fetch_lootfarm_feed', lambda game='cs': {'A': {'price': 200, 'have': 0, 'max': 10}})
    rows = service.fetch_tradeon_lootfarm('token')
    assert rows and abs(rows[0]['secondMarket']['price'] - 1.94) < 0.001   # $2.00 × (1 − 3%)
    rows = service.fetch_tradeon_lootfarm('token', fee_pct=5)                # explicit still wins
    assert abs(rows[0]['secondMarket']['price'] - 1.90) < 0.001


def test_a_case_priced_only_by_a_new_source_records_no_history(monkeypatch):
    service = _service()
    history = {}
    monkeypatch.setattr(service, '_load_case_history', lambda: history)
    monkeypatch.setattr(service, '_save_case_history', lambda saved: None)
    snaps = {market: {} for market in service._CONTAINER_MARKETS}
    snaps['csmoney_market'] = {'Case Y': {'price': 0.50, 'count': 3}}
    row = service._price_rows_recording_history([{'name': 'Case Y'}], snaps, '2026-09-30')[0]
    assert row['cheapest_market'] == 'csmoney_market'      # the table still shows it
    assert 'Case Y' not in history and row['trend_pct'] is None
