"""Moving-average profit/loss and one canonical name per platform (2026-09-30).

Profit/loss replays each account's transactions in date order with a moving
average cost (no fees). Platform aliases typed into a transaction ('buff',
'tradeit.gg') are stored as one canonical lower-case name, both for new
transactions and, once, for the stored ones (after a verified backup)."""
import json

from draupnir_backup_service import BackupService
from draupnir_service import (
    PLATFORM_ALIASES_VERSION,
    DraupnirService,
    normalize_platform,
)


def _txn(item, typ, qty, price, date='', platform=''):
    return {'item_name': item, 'type': typ, 'qty': qty, 'price': price,
            'date': date, 'platform': platform}


def _only(txns, prices=None):
    (holding,) = DraupnirService._holdings(txns, prices)
    return holding


# ---- moving average --------------------------------------------------------------

def test_rebuy_after_a_full_sale_does_not_change_realized_profit():
    # Ivan's example: buy 10@1, sell 10@2 -> +10; then buy 10@3.
    txns = [_txn('A', 'buy', 10, 1.0, '2026-01-01'),
            _txn('A', 'sell', 10, 2.0, '2026-01-02')]
    assert _only(txns)['realized_pl'] == 10.0
    txns.append(_txn('A', 'buy', 10, 3.0, '2026-01-03'))
    holding = _only(txns)
    assert holding['realized_pl'] == 10.0      # all-time average (2.0) would say 0
    assert holding['cost_basis'] == 30.0
    assert holding['avg_cost'] == 3.0
    assert holding['net_qty'] == 10


def test_buy_moves_the_average_and_a_sell_does_not():
    txns = [_txn('A', 'buy', 10, 1.0, '2026-01-01'),
            _txn('A', 'sell', 5, 4.0, '2026-01-02'),    # realizes (4 - 1) * 5 = 15
            _txn('A', 'buy', 5, 3.0, '2026-01-03'),     # (5 * 1 + 5 * 3) / 10 = 2
            _txn('A', 'sell', 2, 5.0, '2026-01-04')]    # realizes (5 - 2) * 2 = 6
    holding = _only(txns)
    assert holding['avg_cost'] == 2.0
    assert holding['realized_pl'] == 21.0
    assert holding['net_qty'] == 8
    assert holding['cost_basis'] == 16.0


def test_transactions_are_replayed_in_date_order_not_list_order():
    txns = [_txn('A', 'buy', 10, 3.0, '2026-01-03'),
            _txn('A', 'sell', 10, 2.0, '2026-01-02'),
            _txn('A', 'buy', 10, 1.0, '2026-01-01')]
    holding = _only(txns)
    assert holding['realized_pl'] == 10.0
    assert holding['cost_basis'] == 30.0


def test_created_at_breaks_a_same_date_tie():
    buy = {**_txn('A', 'buy', 10, 1.0, '2026-01-01'), 'created_at': '2026-01-01T10:00:00'}
    sell = {**_txn('A', 'sell', 10, 2.0, '2026-01-01'), 'created_at': '2026-01-01T11:00:00'}
    rebuy = {**_txn('A', 'buy', 10, 3.0, '2026-01-01'), 'created_at': '2026-01-01T12:00:00'}
    holding = _only([rebuy, sell, buy])
    assert holding['realized_pl'] == 10.0
    assert holding['cost_basis'] == 30.0


def test_fully_sold_item_keeps_its_last_average():
    txns = [_txn('A', 'buy', 4, 1.0, '2026-01-01'),
            _txn('A', 'buy', 4, 2.0, '2026-01-02'),
            _txn('A', 'sell', 8, 3.0, '2026-01-03')]
    holding = _only(txns)
    assert holding['net_qty'] == 0
    assert holding['avg_cost'] == 1.5
    assert holding['cost_basis'] == 0.0
    assert holding['realized_pl'] == 12.0


def test_sell_before_rebuy_is_costed_at_the_rebuy_price():
    # Sell the copy you hold, buy it back cheaper later (trade-ban arbitrage).
    txns = [_txn('A', 'buy', 1, 10.0, '2026-01-01'),
            _txn('A', 'sell', 2, 12.0, '2026-01-02'),   # one unit is not held yet
            _txn('A', 'buy', 1, 9.0, '2026-01-03')]     # it covers that unit
    holding = _only(txns)
    assert holding['realized_pl'] == 5.0     # (12 - 10) + (12 - 9)
    assert holding['net_qty'] == 0
    assert holding['oversold'] is False


def test_rebuy_larger_than_the_uncovered_units_holds_the_rest_at_its_price():
    txns = [_txn('A', 'sell', 3, 5.0, '2026-01-01'),    # nothing held
            _txn('A', 'buy', 5, 2.0, '2026-01-02')]     # 3 cover the sale, 2 held
    holding = _only(txns)
    assert holding['realized_pl'] == 9.0     # (5 - 2) * 3
    assert holding['net_qty'] == 2
    assert holding['cost_basis'] == 4.0
    assert holding['avg_cost'] == 2.0


def test_oversold_with_no_later_buy_keeps_flag_and_charges_the_average():
    holding = _only([_txn('A', 'buy', 2, 2.0, '2026-01-01'),
                     _txn('A', 'sell', 5, 3.0, '2026-01-02')])
    assert holding['oversold'] is True and holding['oversold_qty'] == 3
    assert holding['realized_pl'] == 5.0     # 15 - 2.0 * 5
    assert holding['cost_basis'] == 0.0


def _wired(tmp_path):
    service = DraupnirService(huginn_service=None, path=str(tmp_path / 'portfolios.json'))
    backup = BackupService(service.path, backup_dir=str(tmp_path / 'backups'))
    service.set_backup(backup)
    return service, backup


def test_arbitrage_pools_accounts_in_date_order(tmp_path):
    service, _ = _wired(tmp_path)
    first = service.create_portfolio('first')
    second = service.create_portfolio('second')
    # Sell the unlocked copy on one account, rebuy a locked one on another.
    service.add_transaction(first['id'], {**_txn('A', 'buy', 1, 10.0, '2026-01-01', 'buff163'), 'is_arbitrage': True})
    service.add_transaction(first['id'], {**_txn('A', 'sell', 1, 14.0, '2026-01-10', 'csfloat'), 'is_arbitrage': True})
    service.add_transaction(second['id'], {**_txn('A', 'sell', 1, 15.0, '2026-01-05', 'steam'), 'is_arbitrage': True})
    service.add_transaction(second['id'], {**_txn('A', 'buy', 1, 11.0, '2026-01-06', 'buff163'), 'is_arbitrage': True})
    deals = service.arbitrage_deals()
    # Date order: buy 10, sell 15 (costs 10), buy 11, sell 14 (costs 11).
    assert deals['steam']['realized_pl'] == 5.0
    assert deals['market']['realized_pl'] == 3.0
    assert deals['realized_pl'] == 8.0
    assert deals['market']['rows'][0]['avg_cost'] == 11.0
    legs = {leg['date']: leg['realized_pl'] for leg in deals['legs']}
    assert legs == {'2026-01-01': None, '2026-01-05': 5.0, '2026-01-06': None, '2026-01-10': 3.0}


def test_arbitrage_sell_before_its_rebuy_books_the_real_spread(tmp_path):
    service, _ = _wired(tmp_path)
    account = service.create_portfolio('account')
    service.add_transaction(account['id'], {**_txn('A', 'sell', 2, 5.0, '2026-01-01', 'lisskins'), 'is_arbitrage': True})
    service.add_transaction(account['id'], {**_txn('A', 'buy', 2, 4.0, '2026-01-02', 'loot.farm'), 'is_arbitrage': True})
    deals = service.arbitrage_deals()
    assert deals['realized_pl'] == 2.0       # the all-time average also said 2.0
    assert deals['market']['cost_of_sold'] == 8.0


def test_combined_ledger_fully_sold_average_is_the_accounts_last_average(tmp_path):
    service, _ = _wired(tmp_path)
    account = service.create_portfolio('account')
    service.add_transaction(account['id'], _txn('A', 'buy', 2, 1.0, '2026-01-01'))
    service.add_transaction(account['id'], _txn('A', 'sell', 2, 2.0, '2026-01-02'))
    service.add_transaction(account['id'], _txn('A', 'buy', 2, 3.0, '2026-01-03'))
    service.add_transaction(account['id'], _txn('A', 'sell', 2, 4.0, '2026-01-04'))
    (holding,) = service.combined_ledger()['holdings']
    assert holding['net_qty'] == 0
    assert holding['avg_cost'] == 3.0
    assert holding['realized_pl'] == 4.0


# ---- platform names ----------------------------------------------------------------

def test_normalize_platform_maps_aliases_and_keeps_free_text():
    assert normalize_platform('buff') == 'buff163'
    assert normalize_platform('  Buff ') == 'buff163'
    assert normalize_platform('tradeit.gg') == 'tradeit'
    assert normalize_platform('sold to tradeit.gg') == 'tradeit'
    assert normalize_platform('halo') == 'haloskins'
    assert normalize_platform('haloskins   avg') == 'haloskins'
    assert normalize_platform('avan.market') == 'avanmarket'
    assert normalize_platform('tradeon,market') == 'tradeon.market'
    assert normalize_platform('bought at skin.land') == 'skin.land'
    assert normalize_platform('sold to skin.land') == 'skin.land'
    assert normalize_platform('csfloat after fees') == 'csfloat'
    assert normalize_platform('CSMoney Trade') == 'cs.money.trade'
    assert normalize_platform('Steam') == 'steam'
    # Distinct markets stay distinct.
    assert normalize_platform('buff163_buy') == 'buff163_buy'
    assert normalize_platform('steam_buy') == 'steam_buy'
    assert normalize_platform('dmarket_buy') == 'dmarket_buy'
    assert normalize_platform('cs.money.market') == 'cs.money.market'
    # Free text and unknown markets are kept as typed (only trimmed).
    assert normalize_platform('sent from hidey_spidey') == 'sent from hidey_spidey'
    assert normalize_platform(' Some New Market ') == 'Some New Market'
    assert normalize_platform(None) == ''


def test_new_and_edited_transactions_store_the_canonical_platform(tmp_path):
    service, _ = _wired(tmp_path)
    account = service.create_portfolio('account')
    txn = service.add_transaction(account['id'], _txn('A', 'buy', 1, 1.0, '2026-01-01', 'buff'))
    assert txn['platform'] == 'buff163'
    edited = service.update_transaction(account['id'], txn['id'], {'platform': 'tradeit.gg'})
    assert edited['platform'] == 'tradeit'


def _write_store(path, platforms, meta=None):
    transactions = [{'id': f'id{index}', 'item_name': 'A', 'type': 'buy', 'qty': index + 1,
                     'price': 1.5, 'platform': platform, 'date': '2026-01-01', 'note': '',
                     'fee_percent': 0.0, 'is_arbitrage': False, 'created_at': 'x'}
                    for index, platform in enumerate(platforms)]
    data = {'portfolios': {'p': {'id': 'p', 'name': 'p', 'created_at': 'x',
                                 'updated_at': 'x', 'transactions': transactions}}}
    if meta is not None:
        data['meta'] = meta
    path.write_text(json.dumps(data))
    return data


def test_migration_backs_up_first_then_normalizes_stored_platforms_once(tmp_path):
    path = tmp_path / 'portfolios.json'
    original = _write_store(path, ['buff', 'halo', 'buff163_buy', 'sent from hidey_spidey', 'buff'])
    service, backup = _wired(tmp_path)   # set_backup runs the migration

    platforms = [t['platform'] for t in service._data['portfolios']['p']['transactions']]
    assert platforms == ['buff163', 'haloskins', 'buff163_buy', 'sent from hidey_spidey', 'buff163']
    on_disk = json.loads(path.read_text())
    assert on_disk['meta']['platform_aliases_version'] == PLATFORM_ALIASES_VERSION
    # Only the platform changed.
    strip = [{k: v for k, v in t.items() if k != 'platform'}
             for t in original['portfolios']['p']['transactions']]
    assert [{k: v for k, v in t.items() if k != 'platform'}
            for t in on_disk['portfolios']['p']['transactions']] == strip
    # The untouched original is in a backup snapshot.
    snapshots = [json.loads(backup.read_backup(e['name'])) for e in backup.list_backups()]
    assert original in snapshots
    assert any(e['reason'] == 'manual' for e in backup.list_backups())

    # Already migrated: a second run does nothing.
    assert service.migrate_platform_aliases() is None


def test_migration_skips_a_store_already_on_this_version(tmp_path):
    path = tmp_path / 'portfolios.json'
    _write_store(path, ['buff'], meta={'platform_aliases_version': PLATFORM_ALIASES_VERSION})
    service, backup = _wired(tmp_path)
    assert service._data['portfolios']['p']['transactions'][0]['platform'] == 'buff'
    assert backup.list_backups() == []


def test_migration_does_not_run_without_a_verified_backup(tmp_path, monkeypatch):
    path = tmp_path / 'portfolios.json'
    _write_store(path, ['buff'])
    service = DraupnirService(huginn_service=None, path=str(path))
    backup = BackupService(service.path, backup_dir=str(tmp_path / 'backups'))
    monkeypatch.setattr(backup, 'snapshot', lambda reason='change': None)   # snapshot fails
    service.set_backup(backup)
    assert json.loads(path.read_text())['portfolios']['p']['transactions'][0]['platform'] == 'buff'
    assert 'meta' not in json.loads(path.read_text())


def test_migration_does_not_run_on_a_missing_file(tmp_path):
    service, backup = _wired(tmp_path)
    assert not (tmp_path / 'portfolios.json').exists()
    assert backup.list_backups() == []
    assert service.migrate_platform_aliases() is None
