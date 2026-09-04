"""The typeahead name-universe is memoized for speed (rebuilt only when its
source changes). These guard that the caches stay correct: identical result on
an unchanged source, and a rebuilt result the moment the source changes.
"""
import time

from draupnir_service import DraupnirService
from huginn_service import HuginnService


def _draupnir(tmp_path):
    return DraupnirService(huginn_service=None,
                           path=str(tmp_path / 'portfolios.json'))


def test_all_item_names_cached_until_store_changes(tmp_path):
    svc = _draupnir(tmp_path)
    p = svc.create_portfolio('acct1')
    svc.add_transaction(p['id'], {'item_name': 'Fracture Case',
                                  'type': 'buy', 'qty': 1, 'price': 0.4})
    first = svc.all_item_names()
    # Unchanged store → same cached object handed back (no rebuild).
    assert svc.all_item_names() is first
    assert 'Fracture Case' in first

    # A write must invalidate the cache and surface the new name.
    svc.add_transaction(p['id'], {'item_name': 'Recoil Case',
                                  'type': 'buy', 'qty': 1, 'price': 0.9})
    second = svc.all_item_names()
    assert second is not first
    assert {'Fracture Case', 'Recoil Case'} <= second


def test_all_item_names_invalidates_on_delete(tmp_path):
    svc = _draupnir(tmp_path)
    p = svc.create_portfolio('acct1')
    t = svc.add_transaction(p['id'], {'item_name': 'Dreams & Nightmares Case',
                                      'type': 'buy', 'qty': 1, 'price': 1.5})
    assert 'Dreams & Nightmares Case' in svc.all_item_names()
    svc.delete_transaction(p['id'], t['id'])
    assert 'Dreams & Nightmares Case' not in svc.all_item_names()


def test_known_item_names_cached_until_prices_change():
    hs = HuginnService(steam_service=None, ratatoskr_service=None)
    with hs._price_lock:
        hs._price_cache['steam'] = (time.time(), {'AK-47 | Redline': 1.0})
        hs._price_cache_gen += 1
    first = hs.known_item_names('', block=False)
    assert first == {'AK-47 | Redline'}
    # Unchanged price cache → same cached object.
    assert hs.known_item_names('', block=False) is first

    with hs._price_lock:
        hs._price_cache['buff'] = (time.time(), {'AWP | Asiimov': 2.0})
        hs._price_cache_gen += 1
    second = hs.known_item_names('', block=False)
    assert second is not first
    assert {'AK-47 | Redline', 'AWP | Asiimov'} == second
