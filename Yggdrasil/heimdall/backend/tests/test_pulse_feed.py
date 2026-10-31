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
        'Sticker | B': {'price': 3.0, 'count': 7, 'image': 'image of Sticker | B', 'rising': True, 'falling': False,
                        'updated': None}}
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


class _Gated:
    """A pulse that holds every download until its market is released, and records the order."""

    def __init__(self):
        self.started, self.gates, self.lock = [], {}, threading.Lock()

    def post(self, url, token, body):
        market = url.rsplit('/', 2)[1]
        with self.lock:
            self.started.append(market)
            gate = self.gates.setdefault(market, threading.Event())
        assert gate.wait(10)
        return []

    def release(self, market):
        with self.lock:
            self.gates.setdefault(market, threading.Event()).set()

    def wait_started(self, count):
        for _ in range(500):
            with self.lock:
                if len(self.started) >= count:
                    return list(self.started)
            threading.Event().wait(0.01)
        raise AssertionError(f'only {self.started} started')


def _in_job(feed, market, job='case-arbitrage-alerts'):
    def run():
        with feed.job(job):
            feed.table('token', market, 'Sell')
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _on_page(feed, market):
    thread = threading.Thread(target=feed.table, args=('token', market, 'Sell', ON_DEMAND), daemon=True)
    thread.start()
    return thread


def test_background_jobs_leave_a_slot_for_a_button():
    pulse = _Gated()
    feed = PulseFeed(post=pulse.post)
    jobs = [_in_job(feed, f'Job{i}') for i in range(6)]
    assert len(pulse.wait_started(pulse_feed.MAX_BACKGROUND_DOWNLOADS)) == 3
    threading.Event().wait(0.1)
    assert len(pulse.started) == 3                       # the fourth slot stays free
    button = _on_page(feed, 'Page')
    assert pulse.wait_started(4)[-1] == 'Page'           # the click starts at once
    pulse.release('Page')
    button.join(5)
    assert not button.is_alive()
    for i in range(6):
        pulse.release(f'Job{i}')
    for thread in jobs:
        thread.join(5)
    assert sorted(pulse.started) == ['Job0', 'Job1', 'Job2', 'Job3', 'Job4', 'Job5', 'Page']


def test_the_keeper_is_a_background_download():
    pulse = _Gated()
    feed = PulseFeed(post=pulse.post)
    jobs = [_in_job(feed, f'Job{i}') for i in range(3)]
    pulse.wait_started(3)
    keeper = threading.Thread(target=feed.table, args=('token', 'Kept', 'Sell'),
                              kwargs={'count_read': False, 'keeper': True}, daemon=True)
    keeper.start()
    threading.Event().wait(0.1)
    assert 'Kept' not in pulse.started                   # the fourth slot is not the keeper's
    pulse.release('Job0')
    assert pulse.wait_started(4)[-1] == 'Kept'
    for market in ['Kept', 'Job1', 'Job2']:
        pulse.release(market)
    for thread in jobs + [keeper]:
        thread.join(5)


def test_a_waiting_page_goes_before_the_queued_jobs():
    pulse = _Gated()
    feed = PulseFeed(post=pulse.post)
    first = [_on_page(feed, f'Page{i}') for i in range(4)]   # pages fill all four slots
    pulse.wait_started(4)
    jobs = [_in_job(feed, f'Job{i}') for i in range(3)]
    threading.Event().wait(0.1)
    late = _on_page(feed, 'Late')
    threading.Event().wait(0.1)
    pulse.release('Page0')                               # one slot frees: the page takes it
    assert pulse.wait_started(5)[4] == 'Late'
    for market in ['Page1', 'Page2', 'Page3', 'Late', 'Job0', 'Job1', 'Job2']:
        pulse.release(market)
    for thread in first + jobs + [late]:
        thread.join(5)


def test_a_page_joining_a_queued_job_download_moves_it_to_the_front():
    pulse = _Gated()
    feed = PulseFeed(post=pulse.post)
    running = [_in_job(feed, f'Job{i}') for i in range(3)]
    pulse.wait_started(3)
    queued = _in_job(feed, 'Steam')                      # a fourth job download waits for a slot
    threading.Event().wait(0.1)
    assert 'Steam' not in pulse.started
    page = _on_page(feed, 'Steam')                       # the button wants the same table
    assert pulse.wait_started(4)[-1] == 'Steam'          # one download, started now
    pulse.release('Steam')
    page.join(5)
    queued.join(5)
    assert pulse.started.count('Steam') == 1
    for i in range(3):
        pulse.release(f'Job{i}')
    for thread in running:
        thread.join(5)


def test_each_price_keeps_the_markets_own_update_time():
    feed, _ = _feed(rows=[_row('Sticker | A', offersUpdateTime=1700000000)])
    table = feed.table('token', 'Steam', 'Sell')
    assert table.index()['Sticker | A']['updated'] == 1700000000
    assert table.rows()[0]['secondMarket']['offersUpdateTime'] == 1700000000
    assert 'offersUpdateTime' not in table.rows()[0]['firstMarket']      # none given, none invented


def test_weekly_sales_are_kept_per_item_and_market_from_every_table():
    rows = {'Steam': [{**_row('Case'), 'itemPopularity': [{'market': 'Steam', 'salesCount': 900},
                                                          {'market': 'Tm', 'salesCount': 40}]}],
            'Buff': [{**_row('Case'), 'itemPopularity': [{'market': 'Buff', 'salesCount': 70},
                                                         {'market': 'Steam', 'salesCount': 910}]}]}
    feed = PulseFeed(post=lambda url, token, body: rows[url.rsplit('/', 2)[1]])
    feed.table('token', 'Steam', 'Sell')
    assert feed.weekly_sales() == {'Case': {'Steam': 900, 'Tm': 40}}
    feed.table('token', 'Buff', 'Sell')
    assert feed.weekly_sales(['Case', 'Unknown']) == {'Case': {'Steam': 910, 'Tm': 40, 'Buff': 70}}
    feed.weekly_sales()['Case']['Steam'] = 0                                  # a copy
    assert feed.weekly_sales()['Case']['Steam'] == 910
    assert feed.status()['weekly_sales_items'] == 1


def test_a_lookup_asks_for_one_name_and_returns_only_the_exact_item():
    seen = []

    def post(url, token, body):
        seen.append(body)
        return [_row('StatTrak™ AK-47 | Redline (Field-Tested)', second=90.0),
                _row('AK-47 | Redline (Field-Tested)', second=31.5, first=26.2)]
    feed = PulseFeed(post=post)
    answer = feed.lookup('token', 'Steam', 'Buy', 'AK-47 | Redline (Field-Tested)')
    assert answer['second']['price'] == 31.5 and answer['first']['price'] == 26.2
    assert seen[0]['marketHashNameFilter'] == {'value': 'AK-47 | Redline (Field-Tested)'}
    assert seen[0]['paginationRequest']['takeCount'] == pulse_feed.LOOKUP_ROWS
    assert seen[0]['secondMarketOptions']['secondMarketPriceType'] == 'Buy'
    assert feed.lookup('token', 'Steam', 'Buy', 'AK-47 | Redline') is None   # substring matches are not it
    assert feed.status()['lookups']['count'] == 2 and feed.status()['downloads'] == 0


def _gated(feed):
    """The app's gate: only the keeper and Huginn Arbitrage's requests download (conftest lets the
    older slave tests download; these tests switch that off)."""
    feed.slaves_may_download = False
    return feed


def test_a_slave_never_downloads_it_reads_memory_whatever_its_age():
    clock = Clock()
    feed, requests = _feed(clock=clock)
    _gated(feed)
    with pytest.raises(pulse_feed.NotActive, match='not kept by an active Huginn profile'):
        feed.table('token', 'Buff', 'Sell', job='case-arbitrage-hourly')
    assert requests == []
    with feed.reading(download=True):                                     # Huginn Arbitrage
        table = feed.table('token', 'Buff', 'Sell', max_age=ON_DEMAND)
    clock.now += 5 * 60
    assert feed.table('token', 'Buff', 'Sell', job='case-arbitrage-hourly') is table
    assert feed.table('token', 'Buff', 'Sell', max_age=ON_DEMAND) is table   # a slave's "button": memory
    assert requests == [('Buff', 'Sell')]
    status = {(t['market'], t['price_type']): t for t in feed.status()['tables']}
    assert 'case-arbitrage-hourly' in status[('Buff', 'Sell')]['jobs']


def test_an_active_table_not_downloaded_yet_says_so():
    feed, _ = _feed()
    _gated(feed)
    feed.set_active([('Steam', 'Buy')], 5)
    with pytest.raises(pulse_feed.NotActive, match='active but not downloaded yet'):
        feed.table('token', 'Steam', 'Buy')


def test_a_slave_waits_for_a_download_under_way():
    release, started = threading.Event(), threading.Event()

    def post(url, token, body):
        if '/Steam/' in url:                    # the reference (Buff163) goes by at once
            started.set()
            release.wait(5)
        return [_row('Sticker | A')]
    feed = _gated(PulseFeed(post=post, clock=Clock()))
    feed.set_active([('Steam', 'Buy')], 5)
    keeper = threading.Thread(target=feed.keep_fresh_once, args=('token',))
    keeper.start()
    assert started.wait(5)
    answers = []
    reader = threading.Thread(target=lambda: answers.append(feed.table('token', 'Steam', 'Buy')))
    reader.start()
    release.set()
    keeper.join(5)
    reader.join(5)
    assert len(answers) == 1 and len(answers[0]) == 1


def test_the_keeper_keeps_active_tables_and_the_daily_reference():
    clock = Clock()
    feed, requests = _feed(clock=clock)
    feed.set_active([('Steam', 'Sell'), ('Steam', 'Sell'), ('CsMoneyTrade', 'Buy')], minutes=5)
    assert feed.active() == {('Steam', 'Sell'): 300, ('CsMoneyTrade', 'Buy'): 300, **pulse_feed.REFERENCE_TABLES}
    assert feed.keep_fresh_once('token') == 3
    assert feed.keep_fresh_once('token') == 0                                 # all fresh: nothing
    clock.now += 5 * 60
    assert feed.keep_fresh_once('token') == 2                                 # the reference waits a day
    clock.now += 24 * 60 * 60
    assert feed.keep_fresh_once('token') == 3 and len(requests) == 8
    clock.now += KEEP_SECONDS + 60                                            # active: never dropped
    status = {(t['market'], t['price_type']): t for t in feed.status()['tables']}
    assert status[('Steam', 'Sell')]['items'] == 1 and status[('Steam', 'Sell')]['active']
    assert status[('Buff', 'Sell')]['reference']


def test_an_active_profile_table_shorter_than_the_reference_wins():
    feed, _ = _feed()
    feed.set_active([('Buff', 'Sell')], minutes=10)
    assert feed.active()[('Buff', 'Sell')] == 600


def test_one_time_downloads_happen_once():
    feed, requests = _feed()
    feed.set_active([], 5)
    feed.download_once([('Steam', 'Median30d'), ('Steam', 'Median30d')])
    feed.keep_fresh_once('token')
    feed.keep_fresh_once('token')
    assert requests.count(('Steam', 'Median30d')) == 1
    assert feed.status()['keeper']['once_pending'] == []


def test_a_failed_active_table_waits_before_the_keeper_tries_again():
    clock = Clock()
    calls = []

    def post(url, token, body):
        calls.append(url)
        raise OSError('pulse down')
    feed = PulseFeed(post=post, clock=clock)
    feed.set_active([('Steam', 'Sell')], 5)
    feed.keep_fresh_once('token')
    first = len(calls)                                                        # Steam and the reference
    clock.now += 60
    feed.keep_fresh_once('token')
    assert len(calls) == first                                                # still waiting
    feed.note_sleep(clock.now - 3600, clock.now)                              # the Mac slept: forgotten
    feed.keep_fresh_once('token')
    assert len(calls) == 2 * first
    assert feed.status()['keeper']['sleep'] == {'from': clock.now - 3600, 'to': clock.now}


def test_the_keeper_downloads_only_inside_the_active_hours(monkeypatch):
    clock = Clock()
    feed, requests = _feed(clock=clock)
    feed.set_active([('Steam', 'Sell')], 5)
    local = {'hour': 3, 'minute': 0}
    monkeypatch.setattr(pulse_feed.time, 'localtime',
                        lambda now=None: type('Local', (), {'tm_hour': local['hour'], 'tm_min': local['minute'], 'tm_sec': 0})())
    feed.set_active_hours((8 * 60, 1 * 60))                                   # 08:00 → 01:00 over midnight
    assert feed.keep_fresh_once('token') == 0 and requests == []
    keeper = feed.status()['keeper']
    assert keeper['paused'] and keeper['resumes_at'] == clock.now + 5 * 3600
    local['hour'] = 0                                                         # 00:00: inside
    assert feed.keep_fresh_once('token') == 2
    feed.set_active_hours(None)
    assert not feed.status()['keeper']['paused']


def test_a_profile_opening_by_itself_accepts_the_active_copy_up_to_its_interval():
    clock = Clock()
    feed, requests = _feed(clock=clock)
    feed.set_active([('Steam', 'Sell')], 5)
    feed.keep_fresh_once('token')
    clock.now += 5 * 60 + pulse_feed.KEEPER_TICK                              # the keeper may be on it
    with feed.reading(download=True, accept_kept=True) as reads:
        table = feed.table('token', 'Steam', 'Sell', max_age=ON_DEMAND)
    assert reads == {('Steam', 'Sell'): table.started_at} and requests.count(('Steam', 'Sell')) == 1
    with feed.reading(download=True):                                     # Fetch live data: fresh
        feed.table('token', 'Steam', 'Sell', max_age=ON_DEMAND)
    assert requests.count(('Steam', 'Sell')) == 2


def test_too_many_active_tables_are_refused():
    feed, _ = _feed()
    with pytest.raises(ValueError):
        feed.set_active([(f'Market{i}', 'Sell') for i in range(pulse_feed.MAX_ACTIVE_TABLES + 1)], 5)


def test_the_keepers_downloads_are_not_reads_and_never_served_from_memory():
    feed, _ = _feed()
    feed.set_active([('Steam', 'Sell')], 5)
    feed.keep_fresh_once('token')
    feed.table('token', 'Steam', 'Sell')
    status = feed.status()
    steam = next(t for t in status['tables'] if (t['market'], t['price_type']) == ('Steam', 'Sell'))
    assert (steam['downloads'], steam['reads']) == (1, 1) and status['served_from_memory'] == 1


def test_declared_jobs_appear_in_the_status_with_their_tables_and_period():
    feed, _ = _feed()
    feed.declare_job('case-arbitrage-alerts', 'Case Arbitrage alerts', lambda: 'after every keeper pass',
                     lambda: [('CsFloat', 'Sell'), ('CsFloat', 'Sell'), ('Steam', 'Buy')], active=lambda: True,
                     period=lambda: 600)
    status = feed.status()
    assert status['jobs'] == [{'id': 'case-arbitrage-alerts', 'label': 'Case Arbitrage alerts',
                               'schedule': 'after every keeper pass', 'period_seconds': 600, 'active': True,
                               'tables': [['CsFloat', 'Sell'], ['Steam', 'Buy']]}]
    declared = {(t['market'], t['price_type']): t['declared_jobs'] for t in status['tables']}
    assert declared[('Steam', 'Buy')] == ['case-arbitrage-alerts']


def test_an_older_download_finishing_late_never_replaces_newer_weekly_sales():
    feed, _ = _feed()
    with feed._lock:
        feed._merge_sales({'Case': {'Steam': 900}}, at=2000)
        feed._merge_sales({'Case': {'Steam': 100, 'Tm': 5}}, at=1000)
    assert feed.weekly_sales() == {'Case': {'Steam': 900, 'Tm': 5}}
    assert feed._sales_at == 2000


def test_a_refused_read_is_not_served_from_memory():
    feed, _ = _feed()
    _gated(feed)
    for _ in range(3):
        with pytest.raises(pulse_feed.NotActive):
            feed.table('token', 'Buff', 'Sell', job='case-arbitrage-alerts')
    assert feed.status()['served_from_memory'] == 0
