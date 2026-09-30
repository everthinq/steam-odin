"""Draupnir CSV import/export: byte order mark, ';' delimiter, per-row
validation, no empty portfolios from a bad file, and a lossless export -> import
round-trip (Arbitrage + Created At columns, 4-decimal unit prices) that still
accepts the older files without those columns."""
import pytest

from draupnir_service import CsvImportError, DraupnirService

PRICE_TRACKER_HEADER = 'Name,Type,Quantity,Unit Price,Total Price,Marketplace,Date,Note,Fee Percentage'


def _make(tmp_path):
    return DraupnirService(huginn_service=None, path=str(tmp_path / 'portfolios.json'))


def _only_portfolio(svc):
    (p,) = svc._data['portfolios'].values()
    return p


def test_price_tracker_file_keeps_integer_cents_and_mojibake_repair(tmp_path):
    svc = _make(tmp_path)
    mojibake_name = 'StatTrak™ AK-47 | Redline'.encode('utf-8').decode('cp1252')
    text = (PRICE_TRACKER_HEADER + '\n'
            + mojibake_name + ',buy,2,158,316,N/A,2026-08-11,buff163,0\n'
            'Fracture Case,sell,10,41,410,Steam,2026-08-12,,0\n')
    p, count, errors = svc.import_csv(text, name='acct')
    assert (count, errors) == (2, [])
    first, second = p['transactions']
    assert first['item_name'] == 'StatTrak™ AK-47 | Redline'
    assert first['price'] == 1.58                    # integer cents -> dollars
    assert first['platform'] == 'buff163'            # market-in-Note repair kept
    assert second['type'] == 'sell' and second['price'] == 0.41


def test_byte_order_mark_is_stripped(tmp_path):
    svc = _make(tmp_path)
    text = '﻿' + PRICE_TRACKER_HEADER + '\nFracture Case,buy,1,41,41,Steam,2026-08-11,,0\n'
    _, count, _ = svc.import_csv(text)
    assert count == 1
    assert _only_portfolio(svc)['transactions'][0]['item_name'] == 'Fracture Case'


def test_semicolon_delimiter_is_detected(tmp_path):
    svc = _make(tmp_path)
    text = (PRICE_TRACKER_HEADER.replace(',', ';') + '\n'
            'Kilowatt Case;buy;3;25;75;Steam;2026-08-11;a, b;0\n')
    _, count, _ = svc.import_csv(text)
    assert count == 1
    txn = _only_portfolio(svc)['transactions'][0]
    assert (txn['item_name'], txn['qty'], txn['price'], txn['note']) == ('Kilowatt Case', 3, 0.25, 'a, b')


def test_invalid_rows_are_skipped_and_reported_by_line(tmp_path):
    svc = _make(tmp_path)
    text = (PRICE_TRACKER_HEADER + '\n'
            'Good Case,buy,1,41,41,Steam,2026-08-11,,0\n'
            'Bad Type,gift,1,41,41,Steam,2026-08-11,,0\n'
            'Bad Price,buy,1,abc,,Steam,2026-08-11,,0\n'
            'Bad Date,buy,1,41,41,Steam,last tuesday,,0\n'
            'Bad Fee,buy,1,41,41,Steam,2026-08-11,,250\n')
    p, count, errors = svc.import_csv(text)
    assert count == 1
    assert [t['item_name'] for t in p['transactions']] == ['Good Case']
    assert len(errors) == 4
    assert errors[0].startswith('line 3 (Bad Type)') and 'type' in errors[0]
    assert errors[1].startswith('line 4 (Bad Price)') and 'price' in errors[1]
    assert errors[2].startswith('line 5 (Bad Date)') and 'date' in errors[2]
    assert errors[3].startswith('line 6 (Bad Fee)') and 'fee_percent' in errors[3]


def test_no_valid_row_creates_no_portfolio(tmp_path):
    svc = _make(tmp_path)
    text = PRICE_TRACKER_HEADER + '\nBad Type,gift,1,41,41,Steam,2026-08-11,,0\n'
    with pytest.raises(CsvImportError) as raised:
        svc.import_csv(text, name='should not exist')
    assert raised.value.errors and 'type' in raised.value.errors[0]
    assert svc._data['portfolios'] == {}
    with pytest.raises(CsvImportError):
        svc.import_csv('not,a,known,header\n1,2,3,4\n')
    assert svc._data['portfolios'] == {}


def test_unknown_target_portfolio_is_reported_before_row_errors(tmp_path):
    svc = _make(tmp_path)
    assert svc.import_csv(PRICE_TRACKER_HEADER + '\n', pid='missing') == (None, 0, [])


def test_export_then_import_round_trips_every_field(tmp_path):
    svc = _make(tmp_path)
    p = svc.create_portfolio('acct')
    originals = [
        {'item_name': 'Revolution Case', 'type': 'buy', 'qty': 1000, 'price': 0.1834,
         'platform': 'buff163_buy', 'date': '2026-08-11T15:23:24', 'note': 'bulk, cheap',
         'fee_percent': 2.5},
        {'item_name': 'AK-47 | Redline (Field-Tested)', 'type': 'sell', 'qty': 1, 'price': 12.5,
         'platform': 'lootfarm', 'date': '2026-08-12', 'note': '', 'is_arbitrage': True},
        # No platform but a note: our own export must not move the note into platform.
        {'item_name': 'StatTrak™ Glock-18', 'type': 'buy', 'qty': 2, 'price': 3.0,
         'platform': '', 'date': '2026-08-13', 'note': 'gift from friend'},
    ]
    for raw in originals:
        svc.add_transaction(p['id'], raw)
    _, text = svc.export_csv(p['id'])
    assert 'Arbitrage' in text.splitlines()[0] and 'Created At' in text.splitlines()[0]
    assert '0.1834' in text                           # 4 decimals, no sub-cent loss

    imported, count, errors = svc.import_csv(text, name='copy')
    assert (count, errors) == (3, [])

    def comparable(txns):
        keep = ('item_name', 'type', 'qty', 'price', 'platform', 'date', 'note',
                'fee_percent', 'is_arbitrage', 'created_at')
        return sorted((tuple(t[k] for k in keep) for t in txns), key=repr)

    assert comparable(imported['transactions']) == comparable(svc._get(p['id'])['transactions'])


def test_older_export_without_new_columns_still_imports(tmp_path):
    svc = _make(tmp_path)
    old_export = (PRICE_TRACKER_HEADER + '\n'
                  'Fracture Case,buy,10,0.41,4.10,Steam,2026-08-11,,0\n')
    p, count, _ = svc.import_csv(old_export)
    assert count == 1
    txn = p['transactions'][0]
    assert txn['price'] == 0.41                      # decimal point -> real dollars
    assert txn['is_arbitrage'] is False


def test_fee_percentage_n_a_imports_as_zero(tmp_path):
    svc = _make(tmp_path)
    text = PRICE_TRACKER_HEADER + '\nFracture Case,buy,10,41,410,Steam,2026-08-12,,N/A\n'
    p, count, errors = svc.import_csv(text, name='acct')
    assert (count, errors) == (1, [])
    assert p['transactions'][0]['fee_percent'] == 0
