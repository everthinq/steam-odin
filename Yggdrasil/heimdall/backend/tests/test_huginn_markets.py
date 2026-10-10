"""Market registry: pulse's fees as defaults (your edited fees win), markets the
pulse website does not list, and sell-only markets (buy orders, no listings).

Nothing here touches pulse: every pull is replaced by a fake.
"""
import pytest

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


def test_sell_only_market_has_buy_orders_but_no_listings():
    skinswap_trade = _registry()['SkinSwapTrade']
    assert skinswap_trade['hasAutobuy'] is True
    assert skinswap_trade['hasListings'] is False
    assert _registry()['Waxpeer']['hasListings'] is True


def test_sell_only_market_is_never_a_buy_source_or_a_min_target(monkeypatch):
    service = _service()
    pulls = []
    monkeypatch.setattr(service, '_post_tradeon', lambda url, token, body: pulls.append(url) or [])
    with pytest.raises(ValueError):
        service.fetch_generated_pair('token', 'SkinSwapTrade', 'Steam', 'autobuy')
    with pytest.raises(ValueError):
        service.fetch_generated_pair('token', 'Steam', 'SkinSwapTrade', 'min')
    with pytest.raises(ValueError):
        service.fetch_generated_csfloat_autobuy('token', 'SkinSwapTrade')
    assert service.market_buy_index('token', 'SkinSwapTrade') == {}
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
    assert rows and rows[0]['secondMarket']['price'] == 2.0


def test_fee_edited_only_when_your_value_differs_from_the_default():
    registry = _registry({'huginn_market_fees': {'Waxpeer': 0.06, 'BuffMarket': 0.025}})
    assert registry['Waxpeer']['feeEdited'] is False      # same as pulse's fee
    assert registry['BuffMarket']['feeEdited'] is True    # yours (pulse says 0%)
    assert registry['Skinport']['feeEdited'] is False     # never edited
