"""Gjallarhorn news watcher: the detector, and the rules that keep the phone
from ringing twice for one post (no network: the feed and the alerts are fakes)."""
import threading

import pytest

import gjallarhorn_news_service
from gjallarhorn_news_service import GjallarhornNewsService, detect, detect_title

KILOWATT = {'gid': '5001', 'title': 'Counter-Strike 2 Update', 'url': 'https://example.invalid/5001',
            'date': 2_000, 'contents': '[list][*]Introducing the Kilowatt Case, now available[/*][/list]'}
BUG_FIX = {'gid': '4001', 'title': 'Counter-Strike 2 Update', 'url': 'https://example.invalid/4001',
           'date': 1_000, 'contents': '[list][*]Fixed a case where the scoreboard flickered[/*][/list]'}


class FakeSettings:
    """The SettingsManager surface the watcher uses, in memory."""

    def __init__(self, **values):
        self.settings = {'gjallarhorn_news_armed': True, 'gjallarhorn_news_history': [], **values}

    def get_settings(self):
        return dict(self.settings)

    def record_gjallarhorn_news(self, last_seen_date, event_record=None, last_gid=None):
        self.settings['gjallarhorn_news_last_seen_date'] = int(last_seen_date)
        if last_gid is not None:
            self.settings['gjallarhorn_news_last_gid'] = str(last_gid)
        if event_record is not None:
            self.settings['gjallarhorn_news_history'] = self.settings['gjallarhorn_news_history'] + [event_record]


class FakeCaller:
    def __init__(self, on_ring=None):
        self.rings = []
        self.on_ring = on_ring

    def ring(self, message=None):
        self.rings.append(message)
        if self.on_ring:
            self.on_ring()

    def status(self):
        return {'configured': True}


@pytest.fixture
def texts(monkeypatch):
    sent = []
    monkeypatch.setattr(gjallarhorn_news_service, 'send_notification', lambda settings, text: sent.append(text))
    return sent


def make_service(settings, posts, caller=None):
    service = GjallarhornNewsService(settings, telegram_caller=caller or FakeCaller())
    service._fetch = lambda: [dict(post) for post in posts]
    return service


# ---- detector ----------------------------------------------------------------------

def test_detect_new_case_is_an_add():
    assert detect(KILOWATT['contents']) == [
        {'action': 'add', 'kind': 'case', 'text': 'Introducing the Kilowatt Case, now available'}]


def test_detect_removed_collection_is_a_remove():
    hits = detect('[list][*]Removed the Dead Hand Collection from the weekly drop list[/*][/list]')
    assert [(hit['action'], hit['kind']) for hit in hits] == [('remove', 'collection')]


@pytest.mark.parametrize('body', [
    BUG_FIX['contents'],
    '[list][*]Added Train to the Active Duty map pool[/*][/list]',
    '[list][*]Souvenir quality items can now be used in a trade-up[/*][/list]',
])
def test_detect_ignores_generic_words_and_map_pool(body):
    assert detect(body) == []


def test_detect_title_names_a_container_launch():
    assert detect_title('The Jackass Sticker Capsule')['kind'] == 'capsule'
    assert detect_title('Counter-Strike 2 Update') is None


# ---- alerting rules -------------------------------------------------------------------

def test_first_run_adopts_baseline_without_alerting(texts):
    settings = FakeSettings()
    caller = FakeCaller()
    result = make_service(settings, [KILOWATT, BUG_FIX], caller).check_once()
    assert result['baseline'] is True
    assert texts == [] and caller.rings == []
    assert settings.settings['gjallarhorn_news_last_gid'] == '5001'


def test_new_post_alerts_once(texts):
    settings = FakeSettings(gjallarhorn_news_last_seen_date=1_000, gjallarhorn_news_last_gid='4001')
    caller = FakeCaller()
    service = make_service(settings, [KILOWATT, BUG_FIX], caller)
    assert len(service.check_once()['events']) == 1
    assert len(texts) == 1 and len(caller.rings) == 1
    service.check_once()
    service.check_once(force=True)
    assert len(texts) == 1 and len(caller.rings) == 1


def test_check_now_reports_an_alerted_post_but_never_rings_again(texts):
    settings = FakeSettings(gjallarhorn_news_last_seen_date=2_000, gjallarhorn_news_last_gid='5001',
                            gjallarhorn_news_history=[{'gid': '5001', 'title': 'Counter-Strike 2 Update'}])
    caller = FakeCaller()
    result = make_service(settings, [KILOWATT], caller).check_once(force=True)
    assert [event['gid'] for event in result['events']] == ['5001']
    assert result['events'][0]['already_alerted'] is True
    assert texts == [] and caller.rings == []
    assert len(settings.settings['gjallarhorn_news_history']) == 1      # not logged twice


def test_post_is_recorded_before_the_phone_rings(texts):
    settings = FakeSettings(gjallarhorn_news_last_seen_date=1_000, gjallarhorn_news_last_gid='4001')
    seen_at_ring = []
    caller = FakeCaller(on_ring=lambda: seen_at_ring.append(
        ([entry['gid'] for entry in settings.settings['gjallarhorn_news_history']],
         settings.settings['gjallarhorn_news_last_seen_date'])))
    make_service(settings, [KILOWATT, BUG_FIX], caller).check_once()
    assert seen_at_ring == [(['5001'], 2_000)]


class Reloaded(BaseException):
    """Stands in for the backend reloading while the phone rings."""


def test_reload_mid_ring_never_rings_the_same_post_again(texts):
    settings = FakeSettings(gjallarhorn_news_last_seen_date=1_000, gjallarhorn_news_last_gid='4001')

    def reload_now():
        raise Reloaded()
    with pytest.raises(Reloaded):
        make_service(settings, [KILOWATT, BUG_FIX], FakeCaller(on_ring=reload_now)).check_once()
    caller = FakeCaller()
    make_service(settings, [KILOWATT, BUG_FIX], caller).check_once()    # the fresh process
    assert caller.rings == [] and len(texts) == 1


def test_overlapping_check_returns_already_running(texts):
    settings = FakeSettings(gjallarhorn_news_last_seen_date=1_000, gjallarhorn_news_last_gid='4001')
    entered, release = threading.Event(), threading.Event()
    caller = FakeCaller()
    service = make_service(settings, [KILOWATT, BUG_FIX], caller)

    def slow_fetch():
        entered.set()
        release.wait(5)
        return [dict(KILOWATT), dict(BUG_FIX)]
    service._fetch = slow_fetch
    first = threading.Thread(target=service.check_once)
    first.start()
    assert entered.wait(5)
    assert service.check_once(force=True) == {'ok': False, 'error': 'already running', 'running': True}
    release.set()
    first.join(5)
    assert len(caller.rings) == 1
    assert service.check_once(force=True)['ok'] is True               # the lock is released afterwards


def test_detect_splits_bullets_on_one_line():
    body = '[list][*]Removed the Dead Hand Collection from the drop list[/*][*]Introducing the Kilowatt Case[/*][/list]'
    assert [(hit['action'], hit['kind']) for hit in detect(body)] == [('remove', 'collection'), ('add', 'case')]
    assert detect('[p]The [price] of the Kilowatt Case is now available[/p]')[0]['text'] == \
        'The of the Kilowatt Case is now available'                 # "[price]" is not a paragraph tag
