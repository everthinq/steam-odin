"""Collection enrichment — Draupnir has no collection field of its own, so
holdings and transactions are decorated with a `collection` looked up from
Huginn's cached inventory scan. These tests lock down that the decoration is
present, correct, best-effort, and — crucially — purely additive (it must never
disturb the cost-basis / P/L numbers Ivan trades on)."""
import json

from draupnir_service import DraupnirService


class _FakeHuginn:
    """Minimal stand-in for HuginnService — only get_cache() is used here."""
    def __init__(self, cache):
        self._cache = cache

    def get_cache(self):
        return self._cache


def _scan(by_hash):
    return {'scan_timestamp': 't', 'total_items': 0, 'by_hash': by_hash}


# ---- _collection_map: build {item_name: collection} from a scan ------------

def test_collection_map_picks_first_non_empty_collection():
    huginn = _FakeHuginn(_scan({
        'AK-47 | Redline (Field-Tested)': {
            'count': 2,
            'instances': [
                {'collection': ''},                       # blank ignored
                {'collection': 'The Huntsman Collection'},
            ],
        },
        'Fracture Case': {'count': 1, 'instances': [{'collection': ''}]},
    }))
    svc = DraupnirService(huginn_service=huginn, path='/nonexistent-does-not-load')
    cmap = svc._collection_map()
    assert cmap == {'AK-47 | Redline (Field-Tested)': 'The Huntsman Collection'}


def test_collection_map_empty_without_huginn():
    svc = DraupnirService(huginn_service=None, path='/nonexistent-does-not-load')
    assert svc._collection_map() == {}


def test_collection_map_empty_when_no_scan_yet():
    svc = DraupnirService(huginn_service=_FakeHuginn(None), path='/nonexistent')
    assert svc._collection_map() == {}


def test_collection_map_tolerates_broken_cache():
    class Boom:
        def get_cache(self):
            raise RuntimeError('cache read failed')
    svc = DraupnirService(huginn_service=Boom(), path='/nonexistent')
    assert svc._collection_map() == {}


# ---- _attach_collections: additive decoration -----------------------------

def test_attach_collections_sets_field_and_defaults_blank():
    items = [{'item_name': 'A'}, {'item_name': 'B'}]
    out = DraupnirService._attach_collections(items, {'A': 'Coll A'})
    assert out[0]['collection'] == 'Coll A'
    assert out[1]['collection'] == ''          # unknown -> blank, not missing


def test_attach_collections_does_not_touch_money_fields():
    holding = {'item_name': 'A', 'avg_cost': 3.0, 'net_qty': 5,
               'cost_basis': 15.0, 'realized_pl': 2.0}
    before = dict(holding)
    DraupnirService._attach_collections([holding], {'A': 'Coll A'})
    for k, v in before.items():
        assert holding[k] == v                 # every pre-existing number preserved
    assert holding['collection'] == 'Coll A'


# ---- end to end through get_portfolio / combined_ledger -------------------

def _service_with(tmp_path, portfolios, huginn):
    path = tmp_path / 'portfolios.json'
    path.write_text(json.dumps({'portfolios': portfolios}))
    return DraupnirService(huginn_service=huginn, path=str(path))


def _portfolio(pid, name, txns):
    return {'id': pid, 'name': name, 'created_at': '2026-01-01T00:00:00',
            'updated_at': '2026-01-01T00:00:00', 'transactions': txns}


def _txn(item, typ, qty, price, date='2026-01-01'):
    return {'id': f'{item}-{typ}-{date}', 'item_name': item, 'type': typ,
            'qty': qty, 'price': price, 'date': date}


def test_get_portfolio_enriches_holdings_and_transactions(tmp_path):
    huginn = _FakeHuginn(_scan({
        'Fracture Case': {'count': 1, 'instances': [{'collection': 'Cases'}]},
    }))
    svc = _service_with(tmp_path, {
        'p1': _portfolio('p1', 'Acct 1', [
            _txn('Fracture Case', 'buy', 10, 1.0),
            _txn('Unknown Item', 'buy', 5, 2.0),
        ]),
    }, huginn)

    detail = svc.get_portfolio('p1')
    holdings = {h['item_name']: h for h in detail['holdings']}
    assert holdings['Fracture Case']['collection'] == 'Cases'
    assert holdings['Unknown Item']['collection'] == ''      # not in scan
    # money math untouched by enrichment
    assert holdings['Fracture Case']['cost_basis'] == 10.0
    for t in detail['transactions']:
        assert 'collection' in t
    known = next(t for t in detail['transactions'] if t['item_name'] == 'Fracture Case')
    assert known['collection'] == 'Cases'


def test_combined_ledger_enriches_holdings_and_transactions(tmp_path):
    huginn = _FakeHuginn(_scan({
        'Kilowatt Case': {'count': 1, 'instances': [{'collection': 'Cases'}]},
    }))
    svc = _service_with(tmp_path, {
        'p1': _portfolio('p1', 'Acct 1', [_txn('Kilowatt Case', 'buy', 3, 0.5)]),
        'p2': _portfolio('p2', 'Acct 2', [_txn('Kilowatt Case', 'buy', 2, 0.7)]),
    }, huginn)

    combined = svc.combined_ledger()
    (holding,) = combined['holdings']
    assert holding['collection'] == 'Cases'
    assert all('collection' in t for t in combined['transactions'])


def test_enrichment_is_silent_when_no_scan(tmp_path):
    svc = _service_with(tmp_path, {
        'p1': _portfolio('p1', 'Acct 1', [_txn('Fracture Case', 'buy', 10, 1.0)]),
    }, _FakeHuginn(None))

    detail = svc.get_portfolio('p1')
    # Field is always present (blank), so the frontend can rely on it existing.
    assert all(h['collection'] == '' for h in detail['holdings'])
    assert all(t['collection'] == '' for t in detail['transactions'])
