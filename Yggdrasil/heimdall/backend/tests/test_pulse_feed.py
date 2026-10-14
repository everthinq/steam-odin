"""The master pulse feed (pulse_feed.py): one request per (market, price type) table, shared by
every feature; a fake transport stands in for pulse."""
import threading

import pytest

import pulse_feed
from pulse_feed import FRESH, KEEP_SECONDS, ON_DEMAND, PulseFeed


def _row(name, second=1.0, first=0.9, **second_extra):
    return {'itemName': {'marketHashName': name, 'skinName': name}, 'imageUrl': f'image of {name}',
            'firstMarket': {'price': first, 'totalOffersCount': 4},
            'secondMarket': {'price': second, 'totalOffersCount': 7, 'isRaising': True, **second_extra},
            'profit': 0.1, 'profitPercent': 11.1, 'itemPopularity': [{'market': 'Buff', 'salesCount': 3}]}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _feed(rows=None, clock=None, scale=None):
    requests = []

    def post(url, token, body):
        requests.append((url.rsplit('/', 2)[1], body['secondMarketOptions']['secondMarketPriceType']))
        return [_row('Sticker | A')] if rows is None else rows
    return PulseFeed(post=post, clock=clock or Clock(), scale=scale), requests


def test_one_download_serves_every_reader_while_it_is_fresh():
    clock = Clock()
    feed, requests = _feed(clock=clock)
    first = feed.table('token', 'Buff', 'Sell')
    clock.now += FRESH - 1
    assert feed.table('token', 'Buff', 'Sell') is first
    assert requests == [('Buff', 'Sell')]
    clock.now += 2                                           # older than FRESH: downloaded again
    feed.table('token', 'Buff', 'Sell')
    assert requests == [('Buff', 'Sell')] * 2


def test_the_caller_chooses_how_fresh():
    clock = Clock()
    feed, requests = _feed(clock=clock)
    feed.table('token', 'Steam', 'Buy')
    clock.now += ON_DEMAND + 1
    feed.table('token', 'Steam', 'Buy')                      # a board: ten minutes is fine
    feed.table('token', 'Steam', 'Buy', max_age=ON_DEMAND)   # a button: not older than a minute
    assert len(requests) == 2


def test_each_price_type_is_its_own_table():
    feed, requests = _feed()
    feed.table('token', 'Buff', 'Sell')
    feed.table('token', 'Buff', 'Buy')
    feed.table('token', 'Steam', 'Sell')
    assert requests == [('Buff', 'Sell'), ('Buff', 'Buy'), ('Steam', 'Sell')]


def test_callers_asking_during_a_download_wait_for_it():
    gate = threading.Event()
    requests = []

    def slow_post(url, token, body):
        requests.append(url)
        gate.wait(5)
        return [_row('Sticker | A')]
    feed = PulseFeed(post=slow_post)
    results = []
    threads = [threading.Thread(target=lambda: results.append(feed.table('token', 'Buff', 'Sell'))) for _ in range(5)]
    for thread in threads:
        thread.start()
    gate.set()
    for thread in threads:
        thread.join(10)
    assert len(requests) == 1 and len(results) == 5 and all(table is results[0] for table in results)


def test_a_failed_download_reaches_every_waiter_and_the_next_call_tries_again():
    calls = []

    def post(url, token, body):
        calls.append(url)
        if len(calls) == 1:
            raise OSError('pulse down')
        return [_row('Sticker | A')]
    feed = PulseFeed(post=post)
    with pytest.raises(OSError):
        feed.table('token', 'Buff', 'Sell')
    assert len(feed.table('token', 'Buff', 'Sell')) == 1
    status = {(t['market'], t['price_type']): t for t in feed.status()['tables']}
    assert status[('Buff', 'Sell')]['failures'] == 1 and status[('Buff', 'Sell')]['last_error'] is None


def test_an_answer_that_is_not_a_table_is_an_error():
    feed, _ = _feed(rows={'message': 'TakeCountLimitExceeded'})
    with pytest.raises(ValueError):
        feed.table('token', 'Buff', 'Sell')


def test_rows_are_pulse_shaped_fresh_copies_with_only_the_fields_heimdall_reads():
    feed, _ = _feed(rows=[_row('Sticker | A', overstockInfo={'limit': 0})])
    table = feed.table('token', 'LootFarm', 'Buy')
    row = table.rows()[0]
    assert row == {'itemName': {'marketHashName': 'Sticker | A', 'skinName': 'Sticker | A'},
                   'imageUrl': 'image of Sticker | A',
                   'firstMarket': {'price': 0.9, 'totalOffersCount': 4, 'isRaising': False, 'isFalling': False},
                   'secondMarket': {'price': 1.0, 'totalOffersCount': 7, 'isRaising': True, 'isFalling': False,
                                    'overstockInfo': {'limit': 0}},
                   'profit': 0.1, 'profitPercent': 11.1}
    row['secondMarket']['price'] = 99
    row['itemName']['marketHashName'] = 'changed'
    assert table.rows()[0]['secondMarket']['price'] == 1.0
    assert table.rows()[0]['itemName']['marketHashName'] == 'Sticker | A'


def test_index_prices_and_names():
    feed, _ = _feed(rows=[_row('Sticker | A', second=2.0), _row('Sticker | B', second=3.0, first=None)])
    table = feed.table('token', 'Buff', 'Sell')
    assert table.prices() == {'Sticker | A': 2.0, 'Sticker | B': 3.0}
    assert table.prices('first') == {'Sticker | A': 0.9}
    assert table.index(['Sticker | B', 'Not there']) == {
        'Sticker | B': {'price': 3.0, 'count': 7, 'image': 'image of Sticker | B', 'rising': True, 'falling': False}}
    assert [row['itemName']['marketHashName'] for row in table.rows(['Sticker | B'])] == ['Sticker | B']


def test_bonus_balance_markets_become_real_dollars_once_with_the_page_price_kept():
    feed, _ = _feed(rows=[_row('AK', second=35.35)], scale={'SkinSwapTrade': 1 / 1.4})
    market = feed.table('token', 'SkinSwapTrade', 'Buy').rows()[0]['secondMarket']
    assert market['price'] == pytest.approx(25.25) and market['pagePrice'] == 35.35
    assert 'pagePrice' not in feed.table('token', 'SkinSwapMarket', 'Sell').rows()[0]['secondMarket']


def test_tables_nobody_can_want_any_more_are_dropped():
    clock = Clock()
    feed, _ = _feed(clock=clock)
    feed.table('token', 'Buff', 'Sell')
    clock.now += KEEP_SECONDS + 1
    feed.table('token', 'Steam', 'Sell')
    assert feed.status()['tables_in_memory'] == 1


def test_every_request_is_the_one_normalised_body():
    seen = []
    feed = PulseFeed(post=lambda url, token, body: seen.append((url, body)) or [])
    feed.table('token', 'LisSkins', 'SellWithoutHold')
    url, body = seen[0]
    assert url == 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/LisSkins/all'
    assert body['secondMarketOptions']['secondMarketPriceType'] == 'SellWithoutHold'
    assert body['firstMarketOptions']['firstMarketPriceType'] == 'Sell'
    for side in ('firstMarketOptions', 'secondMarketOptions'):
        prefix = side.replace('Options', '')
        assert body[side][f'{prefix}PriceFilter'] == {'minValue': None, 'maxValue': None}   # no price floor
        assert body[side][f'{prefix}CountFilter'] == {'minValue': None, 'maxValue': None}
    assert body['marketHashNameFilter'] is None and body['profitFilter'] is None
    assert body['counterStrikeItemTypeOptions']['displaySoldOutItems'] is True
    assert body['paginationRequest']['takeCount'] == 9999999
    assert pulse_feed.BASE_BODY['secondMarketOptions']['secondMarketPriceType'] == 'Buy'   # never changed in place


def test_listeners_hear_every_download_on_their_own_thread():
    feed, _ = _feed()
    heard = []
    feed.add_listener(lambda table: heard.append((table.market_id, table.price_type, threading.current_thread().name)))
    feed.table('token', 'Buff', 'Sell')
    feed.table('token', 'Buff', 'Sell')                      # from memory: no second announcement
    feed.flush_listeners()
    assert heard == [('Buff', 'Sell', heard[0][2])] and heard[0][2].startswith('pulse-feed-listener')


def test_status_counts_downloads_and_reads():
    feed, _ = _feed()
    for _ in range(4):
        feed.table('token', 'Buff', 'Sell')
    status = feed.status()
    assert (status['downloads'], status['reads'], status['served_from_memory']) == (1, 4, 3)
    table = status['tables'][0]
    assert (table['market'], table['price_type'], table['items'], table['downloading']) == ('Buff', 'Sell', 1, False)


def test_old_tables_are_dropped_on_reads_too():
    clock = Clock()
    feed, _ = _feed(clock=clock)
    feed.table('token', 'Buff', 'Sell')
    clock.now += KEEP_SECONDS + 1
    feed.status()
    feed.table('token', 'Buff', 'Sell', max_age=10 ** 9)     # a read, no download needed... but it is too old
    assert feed.status()['downloads'] == 2


def test_nested_overstock_info_is_a_copy_too():
    feed, _ = _feed(rows=[_row('Sticker | A', overstockInfo={'limit': 5})])
    table = feed.table('token', 'LootFarm', 'Buy')
    table.rows()[0]['secondMarket']['overstockInfo']['limit'] = 0
    assert table.rows()[0]['secondMarket']['overstockInfo'] == {'limit': 5}


def test_a_waiter_on_an_interrupted_download_gets_a_clear_error():
    feed, _ = _feed()
    flight = pulse_feed._Flight()
    flight.done.set()                                       # finished with neither a table nor an error
    feed._flights[('Buff', 'Sell')] = flight
    with pytest.raises(RuntimeError, match='interrupted'):
        feed.table('token', 'Buff', 'Sell')


def test_at_most_four_downloads_run_at_once():
    running, peak, lock = [0], [0], threading.Lock()
    gate = threading.Event()

    def post(url, token, body):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        gate.wait(0.2)
        with lock:
            running[0] -= 1
        return []
    feed = PulseFeed(post=post)
    threads = [threading.Thread(target=feed.table, args=('token', f'Market{i}', 'Sell')) for i in range(9)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert peak[0] == pulse_feed.MAX_PARALLEL_DOWNLOADS == 4
