"""Market registry: pulse's fees as defaults (your edited fees win), markets the
pulse website does not list, sell-only markets (buy orders, no listings), and
SkinSwap (Trade): prices in real dollars with the Trade page price kept beside
them, its min an estimate.

Nothing here touches pulse: every pull is replaced by a fake.
"""
import pytest

import huginn_service
from huginn_service import HuginnService


def _service():
    return HuginnService(steam_service=None, ratatoskr_service=None)


def _registry(settings=None):
    return {m['id']: m for m in _service().market_registry(settings)}


def test_unconfirmed_fees_default_to_pulse_and_confirmed_ones_stay_ours():
    registry = _registry()
    assert registry['Waxpeer']['fee'] == pytest.approx(0.06)
    assert registry['Waxpeer']['feeSource'] == 'pulse'
    assert registry['Skinport']['fee'] == pytest.approx(0.12)
    # Ours where confirmed, even when pulse says otherwise (Steam 13.03%, DMarket 5%).
    assert registry['Steam']['fee'] == pytest.approx(0.13)
    assert registry['Steam']['feeSource'] == 'confirmed'
    assert registry['Dmarket']['fee'] == 0.0


def test_your_edited_fee_wins_over_pulse():
    registry = _registry({'huginn_market_fees': {'Waxpeer': 0.04}})
    assert registry['Waxpeer']['fee'] == pytest.approx(0.04)
    assert registry['Waxpeer']['feeDefault'] == pytest.approx(0.06)
    assert _service().market_fee('Waxpeer', {'huginn_market_fees': {'Waxpeer': 0.04}}) == pytest.approx(0.04)


def test_markets_not_on_the_pulse_website_are_flagged():
    registry = _registry()
    assert {i for i, m in registry.items() if m['notInPulseUi']} == {'SkinSwapTrade', 'GgSwap', 'GamerPay'}
    assert 'SkinSwapChina' not in registry   # pulse answers nothing for it


# A made-up market with buy orders and no listings (none is registered today).
_SELL_ONLY = {'id': 'SellOnlyExample', 'display': 'Sell Only Example', 'buy_type': None,
              'autobuy': 'Buy', 'fee': 0.0, 'premium': False}


def test_sell_only_market_has_buy_orders_but_no_listings(monkeypatch):
    monkeypatch.setattr(huginn_service, '_MARKET_REGISTRY', huginn_service._MARKET_REGISTRY + [_SELL_ONLY])
    sell_only = _registry()['SellOnlyExample']
    assert sell_only['hasAutobuy'] is True
    assert sell_only['hasListings'] is False
    assert _registry()['Waxpeer']['hasListings'] is True


def test_sell_only_market_is_never_a_buy_source_or_a_min_target(monkeypatch):
    monkeypatch.setitem(huginn_service._MARKET_BY_ID, 'SellOnlyExample', _SELL_ONLY)
    service = _service()
    pulls = []
    monkeypatch.setattr(service, '_post_tradeon', lambda url, token, body: pulls.append(url) or [])
    with pytest.raises(ValueError):
        service.fetch_generated_pair('token', 'SellOnlyExample', 'Steam', 'autobuy')
    with pytest.raises(ValueError):
        service.fetch_generated_pair('token', 'Steam', 'SellOnlyExample', 'min')
    with pytest.raises(ValueError):
        service.fetch_generated_csfloat_autobuy('token', 'SellOnlyExample')
    assert service.market_buy_index('token', 'SellOnlyExample') == {}
    assert pulls == []   # refused before asking pulse


def test_selling_into_a_sell_only_markets_buy_orders_works(monkeypatch):
    service = _service()
    bodies = []

    def fake_pull(url, token, body):
        bodies.append((url, body['secondMarketOptions']['secondMarketPriceType']))
        return [{'itemName': {'marketHashName': 'AK'}, 'firstMarket': {'price': 1.0}, 'secondMarket': {'price': 2.0}}]

    monkeypatch.setattr(service, '_post_tradeon', fake_pull)
    rows = service.fetch_generated_pair('token', 'TradeOnMarket', 'SkinSwapTrade', 'autobuy')
    assert bodies == [('https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/SkinSwapTrade/all', 'Buy')]
    assert rows and rows[0]['secondMarket']['price'] == pytest.approx(1.43)   # 2.00 Trade dollars / 1.4


def test_skinswap_trade_prices_become_real_dollars(monkeypatch):
    # SkinSwap: Trade balance = Market balance x 1.4. Its Trade page paid $35.35
    # for an AK-47 Redline on 2026-10-10, which is $25.25 of real (Market) balance.
    service = _service()
    monkeypatch.setattr(service, '_post_tradeon', lambda url, token, body: [
        {'itemName': {'marketHashName': 'AK'}, 'firstMarket': {'price': 20.0},
         'secondMarket': {'price': 35.35, 'realPrice': 35.35}}])
    index = service.market_autobuy_index('token', 'SkinSwapTrade')
    assert index['AK']['price'] == pytest.approx(25.25)
    rows = service.fetch_generated_pair('token', 'TradeOnMarket', 'SkinSwapTrade', 'autobuy', fee=0.0)
    assert rows[0]['profit'] == pytest.approx(5.25)
    # The Trade page's own price stays on the row, for the UI to show first.
    assert rows[0]['secondMarket']['pagePrice'] == 35.35


def test_trade_page_price_is_labelled_only_for_skinswap_trade():
    registry = _registry()
    assert registry['SkinSwapTrade']['pagePriceLabel'] == 'Trade page'
    assert registry['SkinSwapMarket']['pagePriceLabel'] is None
    assert registry['Steam']['pagePriceLabel'] is None


def test_other_markets_keep_pulse_prices(monkeypatch):
    service = _service()
    monkeypatch.setattr(service, '_post_tradeon', lambda url, token, body: [
        {'itemName': {'marketHashName': 'AK'}, 'secondMarket': {'price': 25.02}}])
    assert service.market_buy_index('token', 'SkinSwapMarket')['AK']['price'] == 25.02
    assert 'pagePrice' not in service._pull_market('token', 'SkinSwapMarket', 'Sell')[0]['secondMarket']


def test_skinswap_market_and_trade_are_told_apart():
    registry = _registry()
    assert registry['SkinSwapMarket']['display'] == 'SkinSwap (Market)'
    assert registry['SkinSwapTrade']['display'] == 'SkinSwap (Trade)'
    assert '1.4' in registry['SkinSwapTrade']['priceNote']
    assert registry['SkinSwapMarket']['priceNote'] is None


def test_fee_edited_only_when_your_value_differs_from_the_default():
    registry = _registry({'huginn_market_fees': {'Waxpeer': 0.06, 'BuffMarket': 0.025}})
    assert registry['Waxpeer']['feeEdited'] is False      # same as pulse's fee
    assert registry['BuffMarket']['feeEdited'] is True    # yours (pulse says 0%)
    assert registry['Skinport']['feeEdited'] is False     # never edited


def _skinswap_trade_pull(trade_dollar_prices, market_prices=None):
    """A fake pulse: SkinSwap (Trade)'s buy prices in Trade dollars; SkinSwap
    (Market)'s listings (real dollars) default to the same price divided by 1.4."""
    def pull(url, token, body):
        if url.endswith('/SkinSwapMarket/all'):
            prices = market_prices or {n: p / 1.4 for n, p in trade_dollar_prices.items()}
        else:
            prices = trade_dollar_prices
        return [{'itemName': {'marketHashName': name}, 'firstMarket': {'price': 1.0},
                 'secondMarket': {'price': price, 'realPrice': price}}
                for name, price in prices.items()]
    return pull


@pytest.mark.parametrize('pays, asks', [
    (0.40, 0.64),     # Fracture Case on Ivan's Trade page, 2026-10-10
    (35.35, 41.87),   # AK-47 Redline (Field-Tested)
    (135.51, 160.42), # AWP Asiimov (Field-Tested)
])
def test_skinswap_trade_min_estimate_is_close_to_the_trade_page(monkeypatch, pays, asks):
    service = _service()
    monkeypatch.setattr(service, '_post_tradeon', _skinswap_trade_pull({'item': pays}))
    row = service._pull_market('token', 'SkinSwapTrade', 'EstimatedSell')[0]['secondMarket']
    assert row['estimated'] is True
    # In real dollars (Trade dollars / 1.4), within 2% of what the page asked.
    assert row['price'] == pytest.approx(asks / 1.4, rel=0.02)
    # As the Trade page shows it, in Trade dollars.
    assert row['pagePrice'] == pytest.approx(asks, rel=0.02)


def test_skinswap_trade_min_is_marked_estimated_and_kept_off_real_data_pages(monkeypatch):
    registry = _registry()
    assert registry['SkinSwapTrade']['listingsEstimated'] is True
    assert registry['SkinSwapTrade']['hasListings'] is True
    assert 'ESTIMATE' in registry['SkinSwapTrade']['priceNote']
    assert registry['Waxpeer']['listingsEstimated'] is False
    service = _service()
    pulls = []
    monkeypatch.setattr(service, '_post_tradeon', lambda url, token, body: pulls.append(url) or [])
    assert service.market_buy_index('token', 'SkinSwapTrade') == {}   # Cross-Profile, Store Catalogue
    assert pulls == []


def test_skinswap_trade_min_works_as_buy_source_and_min_target(monkeypatch):
    service = _service()
    bodies = []

    def fake_pull(url, token, body):
        price_type = body['secondMarketOptions']['secondMarketPriceType']
        bodies.append((url.rsplit('/', 2)[1], price_type))
        assert price_type != 'EstimatedSell'      # never sent to pulse
        price = 35.35 if 'SkinSwapTrade' in url else 40.0
        return [{'itemName': {'marketHashName': 'AK'}, 'firstMarket': {'price': 20.0},
                 'secondMarket': {'price': price, 'realPrice': price}}]

    monkeypatch.setattr(service, '_post_tradeon', fake_pull)
    rows = service.fetch_generated_pair('token', 'SkinSwapTrade', 'Steam', 'autobuy', fee=0.0)
    assert rows[0]['firstMarket']['estimated'] is True
    assert rows[0]['firstMarket']['price'] == pytest.approx(29.91, rel=0.01)   # 41.87 / 1.4, estimated
    service._market_pull_cache.clear()
    rows = service.fetch_generated_pair('token', 'Steam', 'SkinSwapTrade', 'min', fee=0.0)
    assert rows[0]['secondMarket']['estimated'] is True
    assert ('SkinSwapTrade', 'Buy') in bodies


def test_no_estimate_where_skinswap_does_not_want_the_item(monkeypatch):
    # 2026-10-10: Trade paid $0.01 for a souvenir SkinSwap (Market) lists at $2.88.
    service = _service()
    monkeypatch.setattr(service, '_post_tradeon', _skinswap_trade_pull(
        {'unwanted': 0.014, 'cheap': 0.10, 'normal': 35.35},
        market_prices={'unwanted': 2.88, 'cheap': 0.07, 'normal': 25.02}))
    names = [r['itemName']['marketHashName'] for r in service._pull_market('token', 'SkinSwapTrade', 'EstimatedSell')]
    assert names == ['normal']   # 'cheap' pays $0.07, under the $0.10 measured floor
