"""Store Catalogue Arbitrage: store items against what markets pay, net of fees."""
import datetime

from store_arbitrage_service import (SIDE_INSTANT, SIDE_LISTING, StoreArbitrageService, best_offer,
                                     cheapest_wallet, clean_history, gap_days, history_statistics,
                                     is_unconfirmed, item_row, make_offer, prune_history, record_prices,
                                     split_items, usable_prices, verdict_of)

TODAY = datetime.date(2026, 10, 4)

# Catalogue rows as the Store Catalogue keeps them (prices read live on 2026-10-03).
FLICKSHOT = {'entry': 'coupon - flickshot', 'name': 'Sticker | Flickshot', 'category': 'Stickers',
             'usd': 0.99, 'definition_index': 20056, 'cannot_trade': False}
NAME_TAG = {'entry': 'Name Tag', 'name': 'Name Tag', 'category': 'Tools', 'usd': 1.99,
            'definition_index': 1200, 'cannot_trade': False}
STORAGE_UNIT = {'entry': 'casket', 'name': 'Storage Unit', 'category': 'Tools', 'usd': 1.99, 'cannot_trade': True}
KEY = {'entry': 'community_35_key', 'name': 'Fever Case Key', 'category': 'Case keys', 'usd': 2.49}
PASS = {'entry': 'XpShopTicket1', 'name': 'Armory Pass', 'category': 'Passes and licenses', 'usd': 15.99}
CATALOGUE = [FLICKSHOT, NAME_TAG, STORAGE_UNIT, KEY, PASS]

MARKETS = [{'id': 'Buff', 'display': 'Buff163', 'balance': None},
           {'id': 'Steam', 'display': 'Steam', 'balance': 'Steam wallet'}]
FEES = {'Buff': 0.015, 'Steam': 0.13}


def test_keys_passes_and_untradable_items_are_left_out_with_a_reason():
    candidates, excluded = split_items(CATALOGUE)
    assert [item['name'] for item in candidates] == ['Sticker | Flickshot', 'Name Tag']
    assert {row['name']: row['reason'] for row in excluded} == {
        'Storage Unit': 'cannot_trade', 'Fever Case Key': 'keys', 'Armory Pass': 'passes'}


def test_an_offer_is_net_of_the_market_fee():
    offer = make_offer('Steam', 'Steam', SIDE_INSTANT, 1.27, 0.13, 0.99)
    assert offer['net'] == 1.105 and offer['profit'] == 0.115 and offer['profit_pct'] == 11.6


def test_a_normal_offer_beats_a_higher_suspicious_one():
    normal = make_offer('Buff', 'Buff163', SIDE_INSTANT, 1.0, 0, 0.99)
    odd = make_offer('Dmarket', 'DMarket', SIDE_INSTANT, 5.0, 0, 0.99, suspicious=True)
    assert best_offer([odd, normal]) is normal
    assert best_offer([]) is None


def test_the_cheapest_wallet_is_found_among_store_currencies():
    prices = {'USD': 99, 'TRY': 2999, 'KZT': 41000, 'XYZ': 1}
    wallets = [{'account_name': 'b', 'currency': 'TRY'}, {'account_name': 'a', 'currency': 'TRY'},
               {'account_name': 'c', 'currency': 'USD'}, {'account_name': 'd', 'currency': 'XYZ'},
               {'account_name': 'e', 'currency': 'KZT'}]
    best = cheapest_wallet(prices, wallets, {'TRY': 40.0, 'KZT': 500.0})
    assert best == {'usd': 0.75, 'currency': 'TRY', 'minor_units': 2999, 'accounts': ['a', 'b']}
    assert cheapest_wallet(prices, [], {}) is None


def test_verdicts():
    instant = make_offer('Buff', 'Buff163', SIDE_INSTANT, 1.2, 0, 0.99)
    listing = make_offer('Buff', 'Buff163', SIDE_LISTING, 1.5, 0, 0.99)
    losing = make_offer('Buff', 'Buff163', SIDE_INSTANT, 0.6, 0, 0.99)
    assert verdict_of(instant, listing, 0.99) == 'instant'
    assert verdict_of(losing, listing, 0.99) == 'listing'
    assert verdict_of(losing, None, 0.99) == 'loss'
    assert verdict_of(None, None, 0.99) == 'no_price'
    suspicious = make_offer('Dmarket', 'DMarket', SIDE_INSTANT, 3.0, 0, 0.99, suspicious=True)
    assert verdict_of(suspicious, None, 0.99) == 'loss'


def test_a_wide_gap_without_history_is_unconfirmed_unless_you_resold_it():
    assert is_unconfirmed([3.47, 5.13], 1.99, 0, 0, resold_before=False)
    assert not is_unconfirmed([3.47], 1.99, 0, 0, resold_before=True)
    assert not is_unconfirmed([1.27], 0.99, 0, 0, resold_before=False)       # a normal gap


def test_a_spike_is_not_unconfirmed_once_history_shows_it_was_closed():
    # Wide today, but on 3 of 10 tracked days only: a spike, worth acting on.
    assert not is_unconfirmed([1.6], 0.99, 10, 3, resold_before=False)
    assert is_unconfirmed([3.47], 1.99, 10, 9, resold_before=False)


def _history():
    return {
        '2026-09-01': {'Sticker | Flickshot': {'Buff:listing': 2.0}},                 # too old for 30 days
        '2026-10-02': {'Sticker | Flickshot': {'Buff:listing': 1.6, 'Steam:instant': 1.0, 'store': 0.99}},
        '2026-10-03': {'Sticker | Flickshot': {'Buff:listing': 0.8, 'Steam:instant': 0.9, 'store': 0.99}},
    }


def test_history_statistics_count_profitable_days_with_each_market_fee():
    keys = {'Buff:listing': 'Buff', 'Steam:instant': 'Steam'}
    stats = history_statistics(_history(), 'Sticker | Flickshot', keys, FEES, 0.99, today=TODAY)
    # 2 October: Buff 1.6 × 0.985 = 1.576 > 0.99; 3 October: Steam 0.9 × 0.87 = 0.783, Buff 0.788: below
    assert stats == {'days': 2, 'profitable_days': 1, 'best_net': 1.576, 'best_date': '2026-10-02',
                     'series': [1.576, 0.788]}
    assert gap_days(_history(), 'Sticker | Flickshot', ['Buff:listing'], 0.99, today=TODAY) == (2, 1)


def test_recording_keeps_the_best_price_of_the_day_and_the_store_price():
    history = {}
    indexes = {('Buff', SIDE_LISTING): {'Sticker | Flickshot': {'price': 0.76}, 'Other': {'price': 9}}}
    assert record_prices(history, '2026-10-04', ['Sticker | Flickshot'], indexes, {'Sticker | Flickshot': 0.99})
    assert not record_prices(history, '2026-10-04', ['Sticker | Flickshot'],
                             {('Buff', SIDE_LISTING): {'Sticker | Flickshot': {'price': 0.70}}},
                             {'Sticker | Flickshot': 0.99})                   # lower: nothing changes
    assert history == {'2026-10-04': {'Sticker | Flickshot': {'Buff:listing': 0.76, 'store': 0.99}}}
    history['2026-01-01'] = {}
    prune_history(history, '2026-10-04', days=180)
    assert list(history) == ['2026-10-04']


def _indexes():
    return {
        ('Buff', SIDE_INSTANT): {'Sticker | Flickshot': {'price': 0.612, 'count': 40}, 'Name Tag': {'price': 3.192}},
        ('Buff', SIDE_LISTING): {'Sticker | Flickshot': {'price': 0.761, 'count': 44}, 'Name Tag': {'price': 3.475}},
        ('Steam', SIDE_INSTANT): {'Sticker | Flickshot': {'price': 0.94}, 'Name Tag': {'price': 4.26}},
        ('Steam', SIDE_LISTING): {'Sticker | Flickshot': {'price': 1.27, 'rising': True}, 'Name Tag': {'price': 5.13}},
    }


def test_a_row_prices_every_market_and_side():
    ledger = {'Sticker | Flickshot': {'bought': 100, 'sold': 98}}
    row = item_row(FLICKSHOT, MARKETS, _indexes(), FEES, ledger, {}, [], {}, {}, today=TODAY)
    assert row['instant']['market'] == 'Steam' and row['instant']['net'] == 0.818       # 0.94 × 0.87
    assert row['listing']['market'] == 'Steam' and row['listing']['net'] == 1.105       # 1.27 × 0.87
    assert row['listing']['rising']
    assert row['verdict'] == 'listing' and row['profit_pct'] == 11.6
    assert [o['net'] for o in row['offers']] == sorted((o['net'] for o in row['offers']), reverse=True)
    assert row['resold_before'] and not row['unconfirmed']
    assert row['reference_listing'] == 0.761
    # Paid out as money: Buff163's listing 0.761 × 0.985, a loss against $0.99.
    assert row['cash']['market'] == 'Buff' and row['cash']['side'] == SIDE_LISTING
    assert row['cash_profit_pct'] == -24.3                                           # 0.7496 − 0.99


def test_a_thin_market_listing_far_above_buff_is_not_a_sell_price():
    indexes = _indexes()
    markets = MARKETS + [{'id': 'LisSkins', 'display': 'LisSkins', 'balance': None}]
    indexes[('LisSkins', SIDE_LISTING)] = {'Sticker | Flickshot': {'price': 8.37, 'count': 1}}
    indexes[('Steam', SIDE_LISTING)] = {'Sticker | Flickshot': {'price': 1.0}}
    row = item_row(FLICKSHOT, markets, indexes, {**FEES, 'LisSkins': 0}, {}, {}, [], {}, {}, today=TODAY)
    lisskins = next(o for o in row['offers'] if o['market'] == 'LisSkins')
    assert lisskins['suspicious'] and row['listing']['market'] == 'Steam' and row['verdict'] == 'loss'   # 0.87 < 0.99


def test_without_buff_the_cheapest_cash_listing_is_the_reference():
    markets = [{'id': 'LisSkins', 'display': 'LisSkins', 'balance': None},
               {'id': 'AvanMarket', 'display': 'AvanMarket', 'balance': None}]
    indexes = {('LisSkins', SIDE_LISTING): {'Sticker | Flickshot': {'price': 8.37}},
               ('AvanMarket', SIDE_LISTING): {'Sticker | Flickshot': {'price': 1.2}}}
    row = item_row(FLICKSHOT, markets, indexes, {}, {}, {}, [], {}, {}, today=TODAY)
    assert row['reference_listing'] == 1.2 and row['listing']['market'] == 'AvanMarket'
    assert row['verdict'] == 'listing'


def test_old_catalogue_rows_without_the_flag_still_leave_untradable_items_out():
    old_storage_unit = {key: value for key, value in STORAGE_UNIT.items() if key != 'cannot_trade'}
    _, excluded = split_items([old_storage_unit])
    assert excluded[0]['reason'] == 'cannot_trade'


def test_the_name_tag_looks_unsellable_from_the_store():
    row = item_row(NAME_TAG, MARKETS, _indexes(), FEES, {}, {}, [], {}, {}, today=TODAY)
    assert row['verdict'] == 'instant' and row['unconfirmed']


def test_a_cash_buy_order_far_above_the_buff_listing_is_suspicious():
    indexes = _indexes()
    markets = MARKETS + [{'id': 'Dmarket', 'display': 'DMarket', 'balance': None}]
    indexes[('Dmarket', SIDE_INSTANT)] = {'Sticker | Flickshot': {'price': 3.0}}
    row = item_row(FLICKSHOT, markets, indexes, {**FEES, 'Dmarket': 0}, {}, {}, [], {}, {}, today=TODAY)
    dmarket = next(o for o in row['offers'] if o['market'] == 'Dmarket')
    assert dmarket['suspicious'] and row['instant']['market'] == 'Steam'


# ---- the service --------------------------------------------------------------------------

class FakeHuginn:
    def __init__(self):
        self.pulled = []

    def market_registry(self, settings=None):
        return [{'id': 'Buff', 'display': 'Buff163', 'hasAutobuy': True, 'fee': 0.015, 'feeKnown': True},
                {'id': 'Steam', 'display': 'Steam', 'hasAutobuy': True, 'fee': 0.13, 'feeKnown': True},
                {'id': 'LisSkins', 'display': 'LisSkins', 'hasAutobuy': False, 'fee': 0.0, 'feeKnown': False}]

    def market_fee(self, market_id, settings=None):
        return {'Buff': 0.015, 'Steam': 0.13}.get(market_id, 0.0)

    def market_buy_index(self, token, market_id):
        self.pulled.append((market_id, SIDE_LISTING))
        return _indexes().get((market_id, SIDE_LISTING), {'Sticker | Flickshot': {'price': 0.9}})

    def market_autobuy_index(self, token, market_id):
        self.pulled.append((market_id, SIDE_INSTANT))
        return _indexes().get((market_id, SIDE_INSTANT), {})


class FakeCatalogue:
    def status(self):
        return {'items': CATALOGUE, 'read_at': 1.0}


class FakeShop:
    def wallets(self):
        return [{'steamid': '1', 'account_name': 'a', 'currency': 'USD'}]

    def exchange_rates(self):
        return {}

    def sheet_entries(self):
        return {'coupon - flickshot': {'USD': 99}}


class FakeDraupnir:
    def item_trades(self, names):
        return {}


def _service(tmp_path):
    return StoreArbitrageService(FakeHuginn(), FakeCatalogue(), FakeShop(), FakeDraupnir(),
                                 history_path=str(tmp_path / 'history.json.gz'), today=lambda: TODAY)


def test_the_board_warms_in_the_background_then_serves_rows(tmp_path):
    service = _service(tmp_path)
    first = service.board('token', ['Buff', 'Steam', 'LisSkins', 'Unknown'])
    assert first['status'] == 'warming' and first['rows'] == []
    assert [m['id'] for m in first['markets']] == ['Buff', 'Steam', 'LisSkins']
    for _ in range(200):
        if not service._warming:
            break
        import time
        time.sleep(0.01)
    board = service.board('token', ['Buff', 'Steam', 'LisSkins'])
    assert board['status'] == 'fresh'
    # LisSkins has no instant side; Buff163 is priced once per side.
    assert sorted(service.huginn.pulled) == sorted([('Buff', 'instant'), ('Buff', 'listing'), ('Steam', 'instant'),
                                                    ('Steam', 'listing'), ('LisSkins', 'listing')])
    names = [row['name'] for row in board['rows']]
    assert names == ['Sticker | Flickshot', 'Name Tag']                       # unconfirmed last
    assert board['summary']['unconfirmed'] == 1 and board['summary']['excluded'] == 3
    flickshot = next(row for row in board['rows'] if row['name'] == 'Sticker | Flickshot')
    assert flickshot['cheapest_wallet']['accounts'] == ['a']
    assert flickshot['listing']['market'] == 'Steam'
    # The pulls went into today's history, saved to disk.
    reloaded = _service(tmp_path)
    assert reloaded._history['2026-10-04']['Sticker | Flickshot']['Buff:listing'] == 0.761


def test_no_token_serves_nothing_and_says_so(tmp_path):
    board = _service(tmp_path).board('', None)
    assert board['status'] == 'no_token' and [m['id'] for m in board['markets']] == ['Buff', 'Steam', 'LisSkins']


def test_draupnir_item_trades_sum_your_own_record(tmp_path):
    from draupnir_service import DraupnirService
    draupnir = DraupnirService(path=str(tmp_path / 'portfolios.json'))
    first = draupnir.create_portfolio('one')['id']
    second = draupnir.create_portfolio('two')['id']
    for pid, raw in [
        (first, {'item_name': 'Sticker | Flickshot', 'type': 'buy', 'qty': 40, 'price': 1.0, 'date': '2026-01-14'}),
        (first, {'item_name': 'Sticker | Flickshot', 'type': 'sell', 'qty': 20, 'price': 1.34, 'date': '2026-01-22',
                 'platform': 'lisskins'}),
        (first, {'item_name': 'Sticker | Flickshot', 'type': 'sell', 'qty': 6, 'price': 1.0, 'date': '2026-03-07',
                 'platform': 'buff163', 'fee_percent': 2.5}),
        (second, {'item_name': 'Sticker | Flickshot', 'type': 'buy', 'qty': 60, 'price': 1.0, 'date': '2026-01-14'}),
        (second, {'item_name': 'Sticker | Flickshot', 'type': 'buy', 'qty': 5, 'price': 9, 'is_arbitrage': True}),
        (second, {'item_name': 'Other', 'type': 'buy', 'qty': 1, 'price': 5}),
    ]:
        draupnir.add_transaction(pid, raw)
    trades = draupnir.item_trades(['Sticker | Flickshot', 'Never traded'])
    assert list(trades) == ['Sticker | Flickshot']
    row = trades['Sticker | Flickshot']
    assert (row['bought'], row['spent'], row['sold']) == (100, 100.0, 26)
    assert row['received'] == round(26.8 + 6 * 0.975, 2)                   # after Buff163's 2.5 %
    assert sum(row['sold_on'].values()) == 26 and len(row['sold_on']) == 2
    assert (row['last_buy'], row['last_sell']) == ('2026-01-14', '2026-03-07')


def test_history_leaves_out_the_prices_the_board_crosses_out():
    # Live 2026-10-04: Ninja Defuse listed at $6.84 on LisSkins while Buff163 listed it at $1.00.
    day = {'Buff:listing': 0.999, 'LisSkins:listing': 6.84, 'Steam:listing': 9.0, 'store': 0.99}
    assert usable_prices(day, {'Steam'}) == {'Buff:listing': 0.999, 'Steam:listing': 9.0}
    history = {'2026-10-04': {'Sticker | Ninja Defuse': day}}
    keys = {'Buff:listing': 'Buff', 'LisSkins:listing': 'LisSkins'}
    stats = history_statistics(history, 'Sticker | Ninja Defuse', keys, FEES, 0.99, today=TODAY)
    assert stats['profitable_days'] == 0 and stats['best_net'] == round(0.999 * 0.985, 3)
    # Without Buff163 the cheapest cash listing is the reference.
    assert usable_prices({'LisSkins:listing': 6.84, 'AvanMarket:listing': 1.1}) == {'AvanMarket:listing': 1.1}


def test_a_malformed_history_keeps_only_its_well_formed_part():
    data = {'2026-10-03': 'broken', '2026-10-04': {'A': {'Buff:listing': 1.0, 'odd': 'x'}, 'B': 5}}
    assert clean_history(data) == {'2026-10-04': {'A': {'Buff:listing': 1.0}}}
    assert clean_history(['not', 'a', 'dict']) == {}


def test_an_unreadable_history_file_is_kept_aside(tmp_path):
    path = tmp_path / 'history.json.gz'
    path.write_bytes(b'not gzip')
    service = StoreArbitrageService(FakeHuginn(), FakeCatalogue(), FakeShop(), FakeDraupnir(),
                                    history_path=str(path), today=lambda: TODAY)
    assert service._history == {} and not path.exists()
    assert len(list(tmp_path.glob('history.json.gz.unreadable-*'))) == 1


def test_one_worker_thread_pulls_everything_in_turn(tmp_path, monkeypatch):
    import threading
    started = []
    real_thread = threading.Thread

    def counting_thread(*args, **kwargs):
        started.append(kwargs.get('name'))
        return real_thread(*args, **kwargs)

    service = _service(tmp_path)
    gate = threading.Event()
    real_fetch = service._fetch_index

    def slow_fetch(token, market_id, side):
        gate.wait(2)
        return real_fetch(token, market_id, side)

    monkeypatch.setattr(service, '_fetch_index', slow_fetch)
    monkeypatch.setattr('store_arbitrage_service.threading.Thread', counting_thread)
    service._queue_warm('token', [('Buff', SIDE_LISTING)])
    service._queue_warm('token', [('Steam', SIDE_LISTING), ('Buff', SIDE_LISTING)])   # Buff163 already queued
    gate.set()
    import time
    for _ in range(300):
        if not service._warming and not service._worker_running:
            break
        time.sleep(0.01)
    assert started == ['store-arbitrage-warm']
    assert sorted(service.huginn.pulled) == [('Buff', SIDE_LISTING), ('Steam', SIDE_LISTING)]


def test_unconfirmed_rows_rank_below_confirmed_ones(tmp_path):
    service = _service(tmp_path)
    service._indexes = {pair: (__import__('time').time(), index) for pair, index in _indexes().items()}
    rows = service.board('token', ['Buff', 'Steam'])['rows']
    assert [row['name'] for row in rows] == ['Sticker | Flickshot', 'Name Tag']
