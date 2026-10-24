"""Store Boosts: the detector on three real boosts, the alerts, capsule values and the reader.

The fixture holds Steam's own price history (public market prices, read 2026-10-24) of the two
boosts Ivan sent: Sticker | Flickshot (January 2026, daily points by then) and Sticker | World
Eaters 1 with the Warhammer 40,000 Traitor Astartes Sticker Capsule (October 2026, hourly).
"""
import contextlib
import datetime
import gzip
import json
import os

import pytest

from store_boost_service import (DAY, HOUR, RateLimited, SessionRejected, StoreBoostService, alert_decision,
                                 analyze, item_chances, market_name_candidates, opening_value, parse_history,
                                 preflag, render_alert, resolve_market_names, rise_started_at, spark)

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'store_boost_histories.json.gz')
WORLD_EATERS = 'Sticker | World Eaters 1'
CAPSULE = 'Warhammer 40,000 Traitor Astartes Sticker Capsule'
FLICKSHOT = 'Sticker | Flickshot'


def _utc(*parts):
    return int(datetime.datetime(*parts, tzinfo=datetime.timezone.utc).timestamp())


@pytest.fixture(scope='module')
def histories():
    with gzip.open(FIXTURE, 'rt', encoding='utf-8') as handle:
        return {name: parse_history(_payload(rows)) for name, rows in json.load(handle).items()}


def _payload(rows, prefix='$'):
    """A pricehistory answer around *rows*, as Steam sends it."""
    return {'success': True, 'price_prefix': prefix, 'price_suffix': '', 'prices': rows}


def _replay(points, start, stop, step=HOUR):
    """{level: first time seen} and the list of (time, level) changes, replaying hour by hour."""
    first, changes, previous = {}, [], None
    for now in range(start, stop + 1, step):
        level = analyze(points, now)['level']
        first.setdefault(level, now)
        if level != previous:
            changes.append((now, level))
            previous = level
    return first, changes


def test_steam_history_is_parsed_in_utc_and_junk_is_skipped():
    points = parse_history(_payload([['Oct 05 2026 16: +0', 11.0, '1,042'], ['junk'], ['Oct 05 2026 15: +0', 0, '3'],
                                     ['Oct 04 2026 23: +0', 1.48, '9']]))
    assert points == [(_utc(2026, 10, 4, 23), 1.48, 9), (_utc(2026, 10, 5, 16), 11.0, 1042)]


def test_a_history_in_another_currency_is_never_read_as_dollars():
    assert parse_history(_payload([['Oct 05 2026 16: +0', 450.0, '3']], prefix='')) == []
    assert parse_history({'success': False}) == [] and parse_history([]) == []


def test_world_eaters_is_flagged_on_its_first_evening_and_its_second_wave_is_seen(histories):
    first, _ = _replay(histories[WORLD_EATERS], _utc(2026, 8, 25), _utc(2026, 10, 24, 12))
    # Six quiet weeks without a single alarm, then sales and price rose together on 10-03 / 10-04.
    assert min(t for level, t in first.items() if level not in ('calm', 'unknown')) >= _utc(2026, 10, 3, 12)
    assert first['watch'] <= _utc(2026, 10, 4, 21)
    assert first['boost'] <= _utc(2026, 10, 5, 0)      # $1.48 at 23:00 UTC, before the $11 and $23 days
    assert first['fading'] >= _utc(2026, 10, 6)
    assert _utc(2026, 10, 21) <= first['second_wave'] <= _utc(2026, 10, 24)


def test_the_capsule_follows_and_its_sales_spikes_alone_never_alarm(histories):
    points = histories[CAPSULE]
    first, changes = _replay(points, _utc(2026, 8, 25), _utc(2026, 10, 20))
    # Before the boost: sales several times the usual on some days, never with the price.
    spikes = sum(1 for now in range(_utc(2026, 8, 25), _utc(2026, 10, 4), HOUR)
                 if (analyze(points, now).get('sales_ratio') or 0) >= 3)
    assert spikes > 0
    assert min(t for level, t in first.items() if level not in ('calm', 'unknown')) >= _utc(2026, 10, 5)
    assert first['watch'] <= _utc(2026, 10, 6, 6) and first['boost'] <= _utc(2026, 10, 6, 6)
    assert changes[-1][1] == 'calm' and changes[-1][0] <= _utc(2026, 10, 16)


def test_flickshot_is_flagged_on_its_first_day_and_turns_a_week_later(histories):
    first, changes = _replay(histories[FLICKSHOT], _utc(2025, 12, 20), _utc(2026, 2, 10), step=DAY)
    assert first['watch'] == _utc(2026, 1, 12)           # the daily point of 01-11 (161 sales at $1.97)
    assert first['boost'] == _utc(2026, 1, 13)
    assert _utc(2026, 1, 17) <= first['fading'] <= _utc(2026, 1, 21)
    assert changes[-1][1] == 'calm'
    assert 'watch' not in {level for t, level in changes if t < _utc(2026, 1, 11)}


def _flat(now, price=0.24, sales=6, days=30):
    """A quiet item: one point a day at *price*."""
    return [(now - day * DAY, price, sales) for day in range(days, 0, -1)]


def test_one_thin_day_at_a_high_price_is_not_a_peak():
    now = _utc(2026, 10, 24, 12)
    points = _flat(now) + [(now - 5 * DAY + HOUR, 0.6, 3)]            # 3 sales at 2.5 times usual
    points += [(now - 2 * HOUR, 0.35, 2)]                              # 1.44 times usual, 2 sales
    points.sort()
    assert analyze(points, now)['level'] == 'calm'


def test_no_second_wave_or_fading_below_the_usual_price():
    now = _utc(2026, 10, 24, 12)
    points = _flat(now)
    points += [(now - 9 * DAY + HOUR, 0.9, 40), (now - 6 * DAY + HOUR, 0.14, 20),   # a peak, then a low under usual
               (now - 30 * HOUR, 0.2, 20), (now - 3 * HOUR, 0.26, 12)]           # back to about usual
    points.sort()
    found = analyze(points, now)
    assert found['peak'] > 0.48 and found['low_after_peak'] < 0.24 and found['level'] == 'calm'


def test_the_rise_start_is_found_just_after_midnight():
    now = _utc(2026, 10, 24, 0, 20)                     # no point yet today
    points = _flat(now, price=1.0) + [(now - 5 * HOUR, 2.5, 40), (now - 26 * HOUR, 2.0, 30)]
    points.sort()
    assert rise_started_at(points, now, 1.0) == now - 26 * HOUR


def test_too_little_history_is_unknown():
    now = _utc(2026, 10, 10)
    assert analyze([(now - 3600, 1.0, 2)], now)['level'] == 'unknown'


def test_the_rise_start_is_reported(histories):
    found = analyze(histories[WORLD_EATERS], _utc(2026, 10, 5, 2))
    assert found['level'] == 'boost'
    assert _utc(2026, 10, 3) <= found['started_at'] <= _utc(2026, 10, 5)


def test_spark_buckets_six_hours():
    now = _utc(2026, 10, 10, 11)
    points = [(now - HOUR, 2.0, 1), (now, 4.0, 3), (now - 3 * DAY, 1.0, 5)]
    buckets = spark(points, now, days=7)
    assert len(buckets) == 29
    assert buckets[-1] == {'time': now // (6 * HOUR) * 6 * HOUR, 'price': 3.5, 'sales': 4}
    assert sum(b['sales'] for b in buckets) == 9


def test_pulse_preflag_needs_both_a_ratio_and_a_real_gain():
    assert preflag({'listing': 0.31, 'buy': 0.2, 'median30d': 0.2})
    assert preflag({'listing': None, 'buy': 1.5, 'median30d': 1.0})
    assert not preflag({'listing': 0.04, 'buy': None, 'median30d': 0.02})      # +2 cents
    assert not preflag({'listing': 1.1, 'buy': 1.0, 'median30d': 1.0})
    assert not preflag({'listing': 1.4, 'buy': 1.2, 'median30d': 1.0})       # Steam's normal spread
    assert not preflag({'listing': 9, 'median30d': None})


def test_capsule_odds_follow_rarity_tiers():
    traitor = [{'name': f'Sticker | {i}', 'tier': 'rare'} for i in range(18)]
    assert all(chance == pytest.approx(1 / 18) for chance in item_chances(traitor).values())
    enfu = ([{'name': f'paper {i}', 'tier': 'rare'} for i in range(12)]
            + [{'name': 'holo', 'tier': 'mythical'}]
            + [{'name': f'foil {i}', 'tier': 'legendary'} for i in range(2)])
    chances = item_chances(enfu)
    assert sum(chances.values()) == pytest.approx(1)
    assert chances['holo'] == pytest.approx(0.16 / 0.992)
    assert chances['foil 0'] == pytest.approx(0.016 / 0.992)


def test_opening_value_at_the_peak_beats_the_store_price():
    traitor = [{'name': WORLD_EATERS, 'tier': 'rare'}] + [{'name': f'other {i}', 'tier': 'rare'} for i in range(17)]
    peak = opening_value(traitor, {WORLD_EATERS: 20.0, **{f'other {i}': 0.3 for i in range(17)}})
    assert peak['value'] == pytest.approx((20.0 + 17 * 0.3) / 18, abs=0.001) and peak['value'] > 0.99
    assert peak['priced_share'] == pytest.approx(1)
    assert peak['top'][0]['name'] == WORLD_EATERS
    today = opening_value(traitor, {WORLD_EATERS: 2.85})
    assert today['value'] == pytest.approx(2.85 / 18, abs=0.001) and today['priced_share'] == pytest.approx(1 / 18, abs=0.001)
    assert opening_value(traitor, {}) is None


def test_each_level_is_sent_once_an_episode():
    now = _utc(2026, 10, 4, 21)
    started = {'started_at': now - 2 * HOUR}
    state, send = alert_decision({'level': 'calm', 'sent': {}}, 'watch', started, now)
    assert send == 'watch'
    state, send = alert_decision(state, 'watch', started, now + HOUR)
    assert send is None
    state, send = alert_decision(state, 'boost', started, now + 2 * HOUR)
    assert send == 'boost'
    state, send = alert_decision(state, 'fading', started, now + DAY)
    assert send == 'fading'
    state, send = alert_decision(state, 'boost', started, now + DAY + HOUR)
    assert send is None                                    # the same episode's boost was sent
    state, send = alert_decision(state, 'watch', started, now + DAY + 2 * HOUR)
    assert send is None                                    # never a watch after a boost
    state, send = alert_decision(state, 'second_wave', started, now + 2 * DAY)
    assert send == 'second_wave'
    state, send = alert_decision(state, 'fading', started, now + 2 * DAY + HOUR)
    state, send = alert_decision(state, 'second_wave', started, now + 2 * DAY + 2 * HOUR)
    assert send is None                                    # again within two days
    state, send = alert_decision(state, 'fading', started, now + 4 * DAY + 3 * HOUR)
    state, send = alert_decision(state, 'second_wave', started, now + 4 * DAY + 4 * HOUR)
    assert send == 'second_wave'


def test_three_calm_days_end_an_episode():
    now = _utc(2026, 10, 1)
    state, _ = alert_decision({'level': 'calm', 'sent': {}}, 'boost', {}, now)
    state, _ = alert_decision(state, 'calm', {}, now + DAY)
    state, send = alert_decision(state, 'boost', {}, now + 2 * DAY)
    assert send is None and state['episode'] == now        # back within 3 days: the same episode
    state, _ = alert_decision(state, 'calm', {}, now + 3 * DAY)
    state, _ = alert_decision(state, 'calm', {}, now + 6 * DAY + 1)
    assert state['episode'] is None
    state, send = alert_decision(state, 'boost', {}, now + 7 * DAY)
    assert send == 'boost'


def test_the_first_sight_records_unless_the_rise_is_fresh():
    now = _utc(2026, 10, 22)
    state, send = alert_decision(None, 'second_wave', {'started_at': now - 18 * DAY}, now)
    assert send is None
    state, send = alert_decision(state, 'second_wave', {'started_at': now - 18 * DAY}, now + HOUR)
    assert send is None                                    # recorded as known, not sent on the next read
    _, send = alert_decision(None, 'boost', {'started_at': now - 20 * HOUR}, now)
    assert send == 'boost'
    _, send = alert_decision(None, 'boost', {'started_at': now - 5 * DAY}, now)
    assert send is None
    state, send = alert_decision(None, 'calm', {}, now)
    assert send is None and state['level'] == 'calm'


def test_the_alert_names_the_store_price_the_capsule_and_the_unlock_date():
    analysis = {'price_6h': 1.48, 'usual_price': 0.2, 'price_ratio': 7.4, 'sales_24h': 105, 'sales_ratio': 21.0,
                'usual_sales': 5.0}
    plain, body = render_alert('boost', WORLD_EATERS, analysis, None,
                               [{'name': CAPSULE, 'cost': 0.99, 'opening': {'value': 1.12}}], None,
                               now=_utc(2026, 10, 4, 23))
    assert plain.startswith('🚀 Price boost: Sticker | World Eaters 1')
    assert '7.4× its usual $0.20' in plain and '105 sold in 24 h' in plain
    assert f'Inside {CAPSULE} (in-game store $0.99): opening worth about $1.12 on Buff163' in plain
    unlock = datetime.datetime.fromtimestamp(_utc(2026, 10, 11, 23)).astimezone().strftime('%d %b %H:%M %Z')
    assert f'tradable from {unlock}' in plain                 # local time, 7 days later
    assert 'short-pulse.tradeon.space/short-link/CsGo/Steam/' in body and '<b>' in body
    plain, _ = render_alert('fading', FLICKSHOT, {**analysis, 'peak': 14.23}, {'cost': 0.99},
                            confirm={'display': 'Market.CSGO', 'net': 1.42})
    assert 'In-game store: $0.99' in plain and '✅ Market.CSGO pays $1.42' in plain and 'Sell what you hold' in plain


# ---- the service, with fakes ---------------------------------------------------------------

STORE = [{'entry': 'coupon - warhammer_traitor_sticker_capsule', 'name': CAPSULE, 'category': 'Sticker capsules',
          'usd': 0.99, 'definition_index': 7012, 'cannot_trade': False},
         {'entry': 'coupon - flickshot', 'name': FLICKSHOT, 'category': 'Stickers', 'usd': 0.99,
          'definition_index': 20056, 'cannot_trade': False},
         {'entry': 'community_35_key', 'name': 'Fever Case Key', 'category': 'Case keys', 'usd': 2.49}]
CONTENTS = {'coupon - warhammer_traitor_sticker_capsule': {
    'capsule': CAPSULE, 'items': [{'name': WORLD_EATERS, 'tier': 'rare'}, {'name': 'Sticker | World Eaters 2', 'tier': 'rare'}]}}


class FakeCatalogue:
    def status(self):
        return {'items': STORE, 'read_at': 1.0}


class FakeRatatoskr:
    def __init__(self):
        self.asked = []

    def capsule_contents(self, names):
        self.asked.append(list(names))
        return {'success': True, 'contents': CONTENTS}


class FakeTable:
    def __init__(self, prices):
        self.prices = prices

    def index(self, names=None):
        return {n: {'price': p} for n, p in self.prices.items() if names is None or n in names}


class FakeFeed:
    def __init__(self):
        self.jobs = []

    def job(self, job_id):
        self.jobs.append(job_id)
        return contextlib.nullcontext()


class FakeHuginn:
    def __init__(self):
        self.pulse = FakeFeed()

    def market_index_at(self, token, market_id, side, max_age=None, names=None):
        prices = {('Steam', 'listing'): {WORLD_EATERS: 0.31, FLICKSHOT: 1.05, CAPSULE: 0.95},
                  ('Steam', 'instant'): {WORLD_EATERS: 0.22, FLICKSHOT: 0.98},
                  ('Buff', 'listing'): {WORLD_EATERS: 0.25, 'Sticker | World Eaters 2': 0.1}}[(market_id, side)]
        return 0, {n: {'price': p, 'count': 3, 'updated': None} for n, p in prices.items() if n in names}

    def _table(self, token, market_id, price_type, max_age=None):
        return FakeTable({WORLD_EATERS: 0.2, FLICKSHOT: 1.04, CAPSULE: 0.97})

    def pulse_table_of(self, market_id, side):
        return (market_id, {'listing': 'Sell', 'instant': 'Buy'}[side])


class FakeStorage:
    def list_accounts(self):
        return ['765', '766']


class FakeSteam:
    def __init__(self, session=True):
        self.session = session
        self.storage = FakeStorage()

    def web_session_cookie_for(self, steamid):
        return {'steamLoginSecure': steamid} if self.session else None


class FakeArbitrage:
    def instant_cash_offers(self, names, settings=None):
        return {FLICKSHOT: {'market': 'Tm', 'display': 'Market.CSGO', 'gross': 1.5, 'net': 1.42}}


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _service(tmp_path, histories, fetch=None, session=True, settings=None):
    sent = []
    clock = Clock(_utc(2026, 10, 4, 23, 30))
    rows = {name: [[datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime('%b %d %Y %H: +0'), p, str(v)]
                   for t, p, v in points] for name, points in histories.items()}

    def default_fetch(name, cookies):
        return _payload([row for row in rows.get(name, [])
                         if parse_history(_payload([row]))[0][0] <= clock.now])
    service = StoreBoostService(FakeHuginn(), FakeCatalogue(), FakeRatatoskr(), FakeSteam(session), FakeArbitrage(),
                                state_path=str(tmp_path / 'boosts.json.gz'),
                                alert_state_path=str(tmp_path / 'alerts.json'),
                                notify=lambda s, text, html=None: sent.append(text) or {'ok': True},
                                fetch=fetch or default_fetch, clock=clock, pace=lambda: None)
    base = {'tradeon_token': 't', 'telegram_bot_token': 'b', 'telegram_chat_id': '1',
            'store_boost_steam_gap_seconds': 10}
    service.set_settings_provider(lambda: {**base, **(settings or {})})
    return service, sent, clock


def test_the_pulse_check_reads_capsule_contents_and_flags_a_rising_price(tmp_path, histories):
    service, _, _ = _service(tmp_path, histories)
    service._pulse_check('t')
    assert set(service.huginn.pulse.jobs) == {'store-boosts'}         # every read as the Pulse feed job
    assert service._pulse[WORLD_EATERS]['preflag'] and not service._pulse[FLICKSHOT]['preflag']
    assert service._priority == [WORLD_EATERS]
    assert service.job_tables() == [('Steam', 'Sell'), ('Steam', 'Buy'), ('Buff', 'Sell'), ('Steam', 'Median30d')]


def test_the_reader_reads_the_queue_first_alerts_on_a_boost_and_queues_the_capsule(tmp_path, histories):
    service, sent, clock = _service(tmp_path, histories)
    # Seen once, calm, a day before (an item seen for the first time only records).
    clock.now = _utc(2026, 10, 2, 12)
    service.read_now(WORLD_EATERS)
    assert service._read_step() == 1
    assert service._items[WORLD_EATERS]['analysis']['level'] == 'calm' and sent == []
    clock.now = _utc(2026, 10, 4, 23, 30)
    service._contents = CONTENTS
    service.read_now(WORLD_EATERS)
    service._read_step()
    assert service._items[WORLD_EATERS]['analysis']['level'] == 'boost'
    assert len(sent) == 1 and sent[0].startswith('🚀 Price boost: Sticker | World Eaters 1')
    assert f'Inside {CAPSULE}' in sent[0]
    assert service._priority == [CAPSULE]
    # The same level again sends nothing.
    clock.now += 60
    service._priority = []
    service.read_now(WORLD_EATERS)
    service._read_step()
    assert len(sent) == 1


def test_the_gap_between_steam_reads_is_kept(tmp_path, histories):
    service, _, clock = _service(tmp_path, histories)
    service.read_now(FLICKSHOT)
    service.read_now(CAPSULE)
    assert service._read_step() == 1
    wait = service._read_step()
    assert wait == pytest.approx(10) and service._priority == [CAPSULE]
    clock.now += 10
    service._read_step()
    assert service._priority == []


def test_a_429_backs_off_and_keeps_the_item_first(tmp_path, histories):
    def limited(name, cookies):
        raise RateLimited('Steam answered 429')
    service, _, clock = _service(tmp_path, histories, fetch=limited)
    service.read_now(FLICKSHOT)
    service._read_step()
    assert service._reader['status'] == 'backing_off'
    assert service._reader['blocked_until'] == clock.now + 600
    assert service._priority == [FLICKSHOT]
    clock.now += 60
    assert service._read_step() == 60 and service._reader['status'] == 'backing_off'
    clock.now += 600
    service._read_step()
    assert service._reader['backoff'] == 1200                # a second 429 doubles the wait


def test_without_a_web_session_nothing_is_read(tmp_path, histories):
    service, _, _ = _service(tmp_path, histories, session=False)
    service.read_now(FLICKSHOT)
    assert service._read_step() == 300
    assert service._reader['status'] == 'no_session' and service._priority == [FLICKSHOT]


def test_switched_off_reads_nothing(tmp_path, histories):
    service, _, _ = _service(tmp_path, histories, settings={'store_boost_enabled': False})
    service.read_now(FLICKSHOT)
    assert service._read_step() == 60 and service._reader['status'] == 'off'


def test_the_board_ranks_boosts_first_and_values_the_capsule(tmp_path, histories):
    service, _, clock = _service(tmp_path, histories)
    service._pulse_check('t')
    clock.now = _utc(2026, 10, 6, 12)
    for name in (WORLD_EATERS, CAPSULE, FLICKSHOT):
        service.read_now(name)
        service._read_step()
        clock.now += 11
    board = service.board()
    assert [row['name'] for row in board['rows']] == [CAPSULE, FLICKSHOT]       # keys are left out
    capsule = board['rows'][0]
    assert capsule['level'] == 'boost' and capsule['contents']['level'] == 'boost'
    assert capsule['contents']['items'][0]['name'] == WORLD_EATERS
    assert capsule['contents']['items'][0]['chance'] == 0.5
    assert capsule['contents']['opening']['buff']['value'] == pytest.approx(0.175)
    assert capsule['spark'] and board['summary']['boost'] == 1
    flickshot = board['rows'][1]
    assert flickshot['confirm']['display'] == 'Market.CSGO' and flickshot['confirmed']
    assert board['reader']['store'] == {'total': 2, 'fresh': 2}
    assert board['alerts']['enabled'] and board['alerts']['channel'] == 'telegram'
    detail = service.item(WORLD_EATERS)
    assert detail['points'] and detail['analysis']['level'] == 'boost'
    assert service.item('nothing') is None


def test_the_state_survives_a_restart(tmp_path, histories):
    service, _, _ = _service(tmp_path, histories)
    service.read_now(FLICKSHOT)
    service._read_step()
    service._save(force=True)
    again, _, _ = _service(tmp_path, histories)
    assert again._items[FLICKSHOT]['read_at'] == service._items[FLICKSHOT]['read_at']


def test_a_test_alert_says_test(tmp_path, histories):
    service, sent, _ = _service(tmp_path, histories)
    result = service.send_test()
    assert result['sent'] and sent[0].startswith('🧪 Test — 🚀 Price boost: Sticker | Flickshot')
    assert service.board()['alerts']['log'][0]['test']


def test_a_session_in_another_currency_hands_over_to_the_next_account(tmp_path, histories):
    used = []

    def fetch(name, cookies):
        used.append(cookies['steamLoginSecure'])
        return _payload([], prefix='') if cookies['steamLoginSecure'] == '765' else _payload([])
    service, _, clock = _service(tmp_path, histories, fetch=fetch)
    service.read_now(FLICKSHOT)
    service._read_step()
    assert service._priority == [FLICKSHOT] and FLICKSHOT not in service._items
    clock.now += 10
    service._read_step()
    assert used == ['765', '766'] and service._items[FLICKSHOT]['error'] is None


def test_a_rejected_session_moves_to_the_next_account_and_keeps_the_item_first(tmp_path, histories):
    used = []

    def fetch(name, cookies):
        used.append((name, cookies['steamLoginSecure']))
        if cookies['steamLoginSecure'] == '765':
            raise SessionRejected('Steam refused the web session (HTTP 403)')
        return _payload([])
    service, _, clock = _service(tmp_path, histories, fetch=fetch)
    service.read_now(FLICKSHOT)
    service.read_now(CAPSULE)
    assert service._read_step() == 1
    assert service._priority == [FLICKSHOT, CAPSULE] and FLICKSHOT not in service._items   # the item's place kept
    clock.now += 10
    service._read_step()
    assert used == [(FLICKSHOT, '765'), (FLICKSHOT, '766')] and service._items[FLICKSHOT]['error'] is None
    clock.now += 31 * 60
    assert service._session()[0] == '765'                                         # tried again after 30 minutes


def test_a_name_steam_does_not_know_is_the_item_s_error_not_the_session_s(tmp_path, histories):
    import io
    import urllib.error

    def unknown(name, cookies):
        raise urllib.error.HTTPError('url', 500, 'Internal Server Error', {}, io.BytesIO())
    service, _, clock = _service(tmp_path, histories, fetch=unknown)
    for number in range(7):
        service.read_now(f'Sticker | unknown {number}')
    waits = []
    for _ in range(7):
        waits.append(service._read_step())
        clock.now += 10
    assert waits == [1] * 7 and service._reader['status'] == 'waiting' and not service._rejected


def test_an_account_in_another_currency_is_tried_again_after_six_hours(tmp_path, histories):
    service, _, clock = _service(tmp_path, histories)
    service._not_dollars = {'765': clock.now, '766': clock.now}
    assert service._session() is None
    service.read_now(FLICKSHOT)
    assert service._read_step() == 300 and 'US dollars' in service._reader['error']
    clock.now += 6 * HOUR + 1
    assert service._session()[0] == '765'


def test_five_network_failures_in_a_row_pause_the_reader(tmp_path, histories):
    import urllib.error

    def offline(name, cookies):
        raise urllib.error.URLError('connection refused')
    service, _, clock = _service(tmp_path, histories, fetch=offline)
    for name in ('a', 'b', 'c', 'd', 'e'):
        service.read_now(f'Sticker | {name}')
    waits = []
    for _ in range(5):
        waits.append(service._read_step())
        clock.now += 10
    assert waits == [1, 1, 1, 1, 300] and service._reader['status'] == 'session_failing'


def test_a_tracked_item_is_read_like_any_other_and_shown_on_top(tmp_path, histories):
    service, _, clock = _service(tmp_path, histories, settings={'store_boost_tracked': [WORLD_EATERS, 7, WORLD_EATERS]})
    service._contents = CONTENTS
    assert service.tracked() == [WORLD_EATERS]
    service._pulse = {CAPSULE: {'listing': 0.95}, FLICKSHOT: {'listing': 1.05}}
    # Tracked or not, a capsule sticker is read on the capsule-contents schedule: store items first.
    reads = []
    for _ in range(3):
        service._read_step()
        reads.append(service._reader['last_name'])
        clock.now += 10
    assert set(reads[:2]) == {CAPSULE, FLICKSHOT} and reads[2] == WORLD_EATERS
    assert service._items[WORLD_EATERS]['analysis']['level'] == 'boost'      # 2026-10-04 23:30, the boost's first evening
    service._priority = []                       # (its boost queued the capsule; not what this test is about)
    clock.now += 15 * 60
    service._items['Sticker | World Eaters 2'] = {'read_at': clock.now}        # the other sticker: just read
    assert service._next(clock.now) == (None, None)      # in a boost: every hour, never every 15 minutes
    clock.now += 46 * 60
    assert service._next(clock.now)[0] == WORLD_EATERS
    tracked = service.board()['tracked']
    assert [entry['name'] for entry in tracked] == [WORLD_EATERS]
    assert tracked[0]['capsules'] == [{'name': CAPSULE, 'entry': 'coupon - warhammer_traitor_sticker_capsule',
                                       'cost': 0.99}]
    assert tracked[0]['spark']


def test_a_tracked_item_outside_the_store_is_read_like_a_store_item(tmp_path, histories):
    service, _, clock = _service(tmp_path, histories, settings={'store_boost_tracked': ['AK-47 | Redline (Field-Tested)']})
    service._pulse = {CAPSULE: {'listing': 0.95}, FLICKSHOT: {'listing': 1.05},
                      'AK-47 | Redline (Field-Tested)': {'listing': 31.0}}
    service._items = {CAPSULE: {'read_at': clock.now}, FLICKSHOT: {'read_at': clock.now}}
    assert service._next(clock.now) == ('AK-47 | Redline (Field-Tested)', 'store')
    service._items['AK-47 | Redline (Field-Tested)'] = {'read_at': clock.now}
    assert service._next(clock.now + 3600) == (None, None)            # every 3 hours, like a store item


def test_a_tracked_item_the_store_does_not_sell_never_rings(tmp_path, histories):
    class Caller:
        rang = []

        def status(self):
            return {'configured': True}

        def ring(self, message=None):
            self.rang.append(message)
    service, sent, _ = _service(tmp_path, histories)
    service.telegram_caller = Caller()
    analysis = {'price_6h': 9.0, 'usual_price': 3.0, 'price_ratio': 3.0, 'sales_24h': 40, 'sales_ratio': 5.0,
                'usual_sales': 8}
    result = service._send('boost', 'AK-47 | Redline (Field-Tested)', analysis, [])
    assert result['sent'] and not result['rang']
    assert 'Not sold in the in-game store' in sent[0] and 'tradable from' not in sent[0]
    result = service._send('boost', FLICKSHOT, analysis, [])
    assert result['rang']


def test_the_alert_state_is_kept_in_memory_and_saved(tmp_path, histories):
    service, _, clock = _service(tmp_path, histories)
    service.read_now(FLICKSHOT)
    service._read_step()
    service._save(force=True)
    with open(tmp_path / 'alerts.json') as handle:
        assert FLICKSHOT in json.load(handle)['items']
    again, _, _ = _service(tmp_path, histories)
    assert FLICKSHOT in again._alerts['items']


def test_the_track_route_pins_unpins_and_refuses_a_text_flag(monkeypatch):
    from flask import Flask

    from context import ctx
    from routes import ratatoskr as ratatoskr_routes

    class Settings:
        saved = {'store_boost_tracked': []}

        def save_settings(self, changes):
            self.saved.update(changes)
            return True

    settings = Settings()

    class Boosts:
        queued = []

        def tracked(self):
            return list(settings.saved['store_boost_tracked'])

        def read_now(self, name, reason=None):
            self.queued.append(name)
    monkeypatch.setattr(ctx, 'settings_manager', settings, raising=False)
    monkeypatch.setattr(ctx, 'store_boost_service', Boosts(), raising=False)
    app = Flask(__name__)
    app.register_blueprint(ratatoskr_routes.bp)
    client = app.test_client()
    url = '/api/ratatoskr/store-boosts/track'
    assert client.post(url, json={'name': FLICKSHOT, 'tracked': 'false'}).status_code == 400
    assert client.post(url, json={'name': FLICKSHOT}).get_json() == {'tracked': [FLICKSHOT]}
    assert client.post(url, json={'name': WORLD_EATERS, 'tracked': True}).get_json() == {'tracked': [WORLD_EATERS, FLICKSHOT]}
    assert client.post(url, json={'name': FLICKSHOT, 'tracked': False}).get_json() == {'tracked': [WORLD_EATERS]}
    assert Boosts.queued == [FLICKSHOT, WORLD_EATERS]


def test_store_items_are_read_under_their_steam_market_name():
    bestiary = {'entry': 'coupon - bestiary_sticker_capsule', 'name': 'Bestiary Sticker Capsule'}
    darude = {'entry': 'coupon - darude_01', 'name': 'Music Kit | Darude, Moments CS:GO'}
    slab = {'entry': 'sticker_slab', 'name': 'Sticker Slab'}
    contents = {bestiary['entry']: {'capsule': 'Bestiary Capsule', 'items': []}}
    assert market_name_candidates(bestiary, contents) == ['Bestiary Capsule', 'Bestiary Sticker Capsule']
    assert 'StatTrak™ Radicals Box' in market_name_candidates({'entry': 'y', 'name': 'StatTrak™ Radicals Music Kit Box'})
    known = {'Bestiary Capsule', 'Music Kit | Darude, Moments CSGO', FLICKSHOT}
    assert resolve_market_names([bestiary, darude, slab, {'entry': 'x', 'name': FLICKSHOT}], contents, known) == {
        'Bestiary Sticker Capsule': 'Bestiary Capsule',
        'Music Kit | Darude, Moments CS:GO': 'Music Kit | Darude, Moments CSGO'}


def test_the_board_shows_the_market_name_and_confirms_on_the_store_name(tmp_path, histories):
    service, _, _ = _service(tmp_path, histories)
    service._market_names = {FLICKSHOT: 'Sticker | Flickshot (market)'}
    rows = {row['store_name']: row for row in service.board()['rows']}
    assert rows[FLICKSHOT]['name'] == 'Sticker | Flickshot (market)'
    assert rows[FLICKSHOT]['confirm']['display'] == 'Market.CSGO'      # the Arbitrage tab prices the store name


def test_a_store_item_pulse_never_priced_is_not_read(tmp_path, histories):
    service, _, _ = _service(tmp_path, histories)
    service._pulse = {CAPSULE: {'listing': 0.95}, FLICKSHOT: {'listing': None, 'buy': None, 'median30d': None}}
    assert service._next(service._clock()) == (CAPSULE, 'store')
    service._items[CAPSULE] = {'read_at': service._clock()}
    assert service._next(service._clock()) == (None, None)


def test_an_item_first_seen_as_unknown_still_counts_as_a_first_sight():
    now = _utc(2026, 10, 22)
    state, send = alert_decision(None, 'unknown', {}, now)
    _, send = alert_decision(state, 'boost', {'started_at': now - 5 * DAY}, now + DAY)
    assert send is None                                    # an old boost seen late never rings


def test_the_gap_between_reads_has_a_ceiling():
    from store_boost_service import steam_gap
    assert steam_gap({'store_boost_steam_gap_seconds': 99999999999}) == 3600
    assert steam_gap({'store_boost_steam_gap_seconds': 1}) == 10


def test_the_pulse_check_forgets_items_no_longer_watched(tmp_path, histories):
    service, _, _ = _service(tmp_path, histories)
    service._items['Sticker | gone'] = {'read_at': 1, 'error': 'x'}
    service._pulse['Sticker | gone'] = {'listing': 1}
    service._pulse_check('t')
    assert 'Sticker | gone' not in service._items and 'Sticker | gone' not in service._pulse
    assert FLICKSHOT in service._pulse


def test_the_queue_has_a_ceiling(tmp_path, histories):
    service, _, _ = _service(tmp_path, histories)
    for number in range(100):
        service.read_now(f'Sticker | {number}')
    assert service.read_now('Sticker | one too many')['queued'] is False


def test_the_ring_follows_its_own_switch_and_an_undelivered_alert_is_tried_again(tmp_path, histories):
    class Caller:
        rang = []

        def status(self):
            return {'configured': True}

        def ring(self, message=None):
            self.rang.append(message)
    service, sent, clock = _service(tmp_path, histories, settings={'store_boost_alerts_enabled': False})
    service.telegram_caller = Caller()
    analysis = {'price_6h': 5.0, 'usual_price': 1.0, 'price_ratio': 5.0, 'sales_24h': 90, 'sales_ratio': 9.0,
                'usual_sales': 10, 'level': 'boost', 'started_at': clock.now - HOUR}
    result = service._send('boost', FLICKSHOT, analysis, [])
    assert result['rang'] and not result['sent'] and sent == []           # messages off, the ring still rings
    quiet, quiet_sent, _ = _service(tmp_path / 'quiet', histories,
                                    settings={'store_boost_alerts_enabled': False, 'store_boost_ring_enabled': False})
    quiet._alerts = {'items': {FLICKSHOT: {'level': 'calm', 'sent': {}}}}
    quiet._after_analysis(FLICKSHOT, analysis, clock.now)
    assert 'boost' in quiet._alerts['items'][FLICKSHOT]['sent']           # switched off on purpose: handled
    assert not quiet._alerts.get('log')                                    # and not logged
    down, _, _ = _service(tmp_path / 'down', histories, settings={'store_boost_ring_enabled': False})
    down._notify = lambda settings, text, html=None: {'ok': False, 'error': 'Telegram is down'}
    down._alerts = {'items': {FLICKSHOT: {'level': 'calm', 'sent': {}}}}
    down._after_analysis(FLICKSHOT, analysis, clock.now)
    assert 'boost' not in down._alerts['items'][FLICKSHOT]['sent']        # not delivered: tried on the next read


def test_http_400_asks_a_second_account_to_tell_a_bad_session_from_a_bad_name(tmp_path, histories):
    from store_boost_service import BadRequest
    used = []

    def answer(name, cookies):
        used.append((name, cookies['steamLoginSecure']))
        if name == 'Sticker | unknown' or cookies['steamLoginSecure'] == '765':
            raise BadRequest('Steam answered HTTP 400')
        return _payload([])
    service, _, clock = _service(tmp_path, histories, fetch=answer)
    service.read_now(FLICKSHOT)
    service._read_step()
    assert used == [(FLICKSHOT, '765'), (FLICKSHOT, '766')]                 # one step, two accounts
    assert service._items[FLICKSHOT]['error'] is None and '765' in service._rejected
    service._rejected = {}
    used.clear()
    service.read_now('Sticker | unknown')
    clock.now += 10
    service._read_step()
    assert used == [('Sticker | unknown', '765'), ('Sticker | unknown', '766')]
    assert service._items['Sticker | unknown']['error'] and not service._rejected       # the name, no account blamed
    assert service._priority == []


def test_http_400_with_a_single_account_blames_the_name_and_moves_on(tmp_path, histories):
    from store_boost_service import BadRequest

    def answer(name, cookies):
        raise BadRequest('Steam answered HTTP 400')
    service, _, _ = _service(tmp_path, histories, fetch=answer)
    service._not_dollars = {'766': service._clock()}                       # only 765 is usable
    service.read_now('Sticker | unknown')
    assert service._read_step() == 1
    assert service._items['Sticker | unknown']['error'] and service._priority == [] and not service._rejected


def test_only_the_strongest_pulse_flags_are_queued(tmp_path, histories):
    from store_boost_service import preflag_strength
    assert preflag_strength({'listing': 3.0, 'buy': 1.5, 'median30d': 1.0}) == 3.0
    assert preflag_strength({'listing': 3.0}) == 0.0


def test_a_market_name_briefly_missing_from_pulse_is_kept(tmp_path, histories):
    service, _, _ = _service(tmp_path, histories)
    service._market_names = {FLICKSHOT: 'Sticker | Flickshot (market)'}
    service.huginn.market_index_at = lambda *arguments, **keywords: (0, {})   # pulse lists neither name
    service._resolve_market_names('t')
    assert service.market_names() == {FLICKSHOT: 'Sticker | Flickshot (market)'}


def test_a_dropped_second_wave_is_sent_on_the_next_read():
    now = _utc(2026, 10, 22)
    state = {'level': 'second_wave', 'episode': now - 10 * DAY, 'sent': {'boost': now - 10 * DAY}}
    _, send = alert_decision(state, 'second_wave', {}, now)
    assert send == 'second_wave'


def test_a_tracked_item_pulse_has_no_price_for_is_still_read(tmp_path, histories):
    service, _, clock = _service(tmp_path, histories, settings={'store_boost_tracked': ['Sticker | rare thing']})
    service._pulse = {CAPSULE: {'listing': 0.95}, FLICKSHOT: {'listing': 1.05}}
    service._items = {CAPSULE: {'read_at': clock.now}, FLICKSHOT: {'read_at': clock.now}}
    assert service._next(clock.now) == ('Sticker | rare thing', 'store')


def test_a_market_name_of_an_item_that_left_the_store_is_dropped(tmp_path, histories):
    service, _, _ = _service(tmp_path, histories)
    service._market_names = {'Gone Sticker Capsule': 'Gone Capsule'}
    service.huginn.market_index_at = lambda *arguments, **keywords: (0, {})
    service._resolve_market_names('t')
    assert service.market_names() == {}
