"""ASF card farming service: provisioning, login assist, Ratatoskr coordination,
and the safety rules (no secrets on disk or in the API, paced logins)."""
import json

import pytest

import asf_service
from asf_service import (AsfService, AsfError, HARDENED_BOT_CONFIG, INPUT_NONE, INPUT_PASSWORD,
                         INPUT_TWO_FACTOR, INPUT_STEAM_GUARD, MAX_LOGIN_ATTEMPTS, LOGIN_RETRY_SECONDS,
                         bot_view, farming_state, parse_time_span)

PASSWORD = 'hunter2-secret'
SHARED_SECRET = 'c2hhcmVkLXNlY3JldA=='
CODE = 'K7Q2M'


class FakeStorage:
    def __init__(self, accounts):
        self.accounts = accounts

    def list_accounts(self):
        return list(self.accounts)

    def load_account(self, steamid):
        return self.accounts.get(steamid)


class FakeSteam:
    def __init__(self, accounts, passwords, steam_time=1000):
        self.storage = FakeStorage(accounts)
        self.passwords = passwords
        self.steam_time = steam_time
        self.codes_generated = 0

    def get_password(self, steamid):
        return self.passwords.get(steamid)

    def _get_steam_time(self):
        return self.steam_time

    def generate_code(self, shared_secret):
        self.codes_generated += 1
        return CODE if shared_secret else 'N/A'


class FakeRatatoskr:
    def __init__(self):
        self.sessions = {}

    def get_status(self, steamid):
        return {'status': self.sessions.get(steamid, 'disconnected')}


def make_bot(connected=True, running=True, required=INPUT_NONE, farming=False, paused=False,
             to_farm=(), config=None):
    return {'IsConnectedAndLoggedOn': connected, 'KeepRunning': running, 'RequiredInput': required,
            'IsPlayingPossible': True,
            'BotConfig': config if config is not None else {**HARDENED_BOT_CONFIG},
            'CardsFarmer': {'NowFarming': farming, 'Paused': paused, 'TimeRemaining': '01:30:00',
                            'CurrentGamesFarming': [{'AppID': 10, 'GameName': 'Ten', 'CardsRemaining': 2,
                                                     'HoursPlayed': 1.25}] if farming else [],
                            'GamesToFarm': [{'AppID': app, 'GameName': f'G{app}', 'CardsRemaining': 3,
                                             'HoursPlayed': 0} for app in to_farm]}}


class FakeAsf:
    """Records every API call; answers from a mutable bots dict."""

    def __init__(self, bots=None):
        self.bots = bots or {}
        self.calls = []
        self.fail_on = None

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.fail_on and self.fail_on in path:
            raise AsfError('boom')
        if method == 'GET' and path == '/Api/Bot/ASF':
            return json.loads(json.dumps(self.bots))
        if method == 'POST' and path.startswith('/Api/Bot/') and path.count('/') == 3:
            name = path.rsplit('/', 1)[1]
            self.bots.setdefault(name, make_bot(connected=False, running=False, required=INPUT_PASSWORD))
            self.bots[name]['BotConfig'] = body['BotConfig']
        return None

    def posts(self, suffix=None):
        return [(path, body) for method, path, body in self.calls
                if method == 'POST' and (suffix is None or path.endswith(suffix))]


ACCOUNTS = {
    '76561198000000001': {'account_name': 'alpha', 'shared_secret': SHARED_SECRET},
    '76561198000000002': {'account_name': 'bravo', 'shared_secret': SHARED_SECRET},
}


@pytest.fixture
def world(tmp_path):
    steam = FakeSteam(ACCOUNTS, {'76561198000000001': PASSWORD, '76561198000000002': PASSWORD})
    ratatoskr = FakeRatatoskr()
    sleeps = []
    service = AsfService(steam, ratatoskr, base_url='http://asf:1242', ipc_password='ipc',
                         state_path=str(tmp_path / 'asf_state.json'), sleep=sleeps.append)
    fake = FakeAsf()
    service._call = fake
    return service, fake, steam, ratatoskr, sleeps


# ---- pure helpers ---------------------------------------------------------------

def test_parse_time_span():
    assert parse_time_span('01:30:00') == 5400
    assert parse_time_span('2.03:04:05.1234') == 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert parse_time_span(None) == 0
    assert parse_time_span('garbage') == 0


def test_bot_view_sums_cards_and_hides_config():
    view = bot_view(make_bot(farming=True, to_farm=(10, 20)))
    assert view['cards_remaining'] == 6 and view['games_to_farm'] == 2
    assert view['current_games'][0] == {'app_id': 10, 'name': 'Ten', 'cards_remaining': 2, 'hours_played': 1.2}
    assert view['time_remaining_seconds'] == 5400
    assert 'BotConfig' not in view and 'SteamLogin' not in json.dumps(view)


@pytest.mark.parametrize('bot, paused_for_ratatoskr, attempts, expected', [
    (None, False, None, 'not_added'),
    (make_bot(farming=True, to_farm=(1,)), False, None, 'farming'),
    (make_bot(farming=True, to_farm=(1,)), True, None, 'paused_for_ratatoskr'),
    (make_bot(paused=True, to_farm=(1,)), False, None, 'paused'),
    (make_bot(), False, None, 'done'),
    (make_bot(to_farm=(1,)), False, None, 'waiting'),
    (make_bot(connected=False, running=False, required=INPUT_PASSWORD), False, None, 'logging_in'),
    (make_bot(connected=False, running=False, required=INPUT_TWO_FACTOR), False,
     {'count': MAX_LOGIN_ATTEMPTS}, 'needs_attention'),
    (make_bot(connected=False, running=False, required=INPUT_STEAM_GUARD), False, None, 'needs_attention'),
    (make_bot(connected=False, running=True), False, None, 'connecting'),
    (make_bot(connected=False, running=False), False, None, 'stopped'),
])
def test_farming_state(bot, paused_for_ratatoskr, attempts, expected):
    view = bot_view(bot) if bot else None
    assert farming_state(view, paused_for_ratatoskr, attempts) == expected


# ---- provisioning -----------------------------------------------------------------

def test_provision_creates_hardened_bots_without_password(world):
    service, fake, *_ = world
    assert sorted(service.provision()) == ['alpha', 'bravo']
    for path, body in fake.posts():
        config = body['BotConfig']
        assert config['SteamLogin'] in ('alpha', 'bravo')
        assert 'SteamPassword' not in config and PASSWORD not in json.dumps(body)
        assert config['RemoteCommunication'] == 0 and config['OnlineStatus'] == 0
        assert config['TradingPreferences'] == 0 and config['GamesPlayedWhileIdle'] == []
        assert config['SteamUserPermissions'] == {} and config['AcceptGifts'] is False


def test_provision_skips_matching_and_rewrites_drifted(world):
    service, fake, *_ = world
    fake.bots = {'alpha': make_bot(), 'bravo': make_bot(config={**HARDENED_BOT_CONFIG, 'RemoteCommunication': 3})}
    assert service.provision() == ['bravo']


def test_accounts_skip_invalid_bot_names(tmp_path):
    steam = FakeSteam({'1': {'account_name': 'ASF'}, '2': {'account_name': '.hidden'},
                       '3': {'account_name': 'has space'}, '4': {'account_name': ''},
                       '5': {'account_name': 'fine_name'}}, {})
    service = AsfService(steam, FakeRatatoskr(), ipc_password='ipc', state_path=str(tmp_path / 's.json'))
    assert list(service._accounts()) == ['fine_name']


# ---- login assist -------------------------------------------------------------------

def test_assist_supplies_password_then_code_then_start(world):
    service, fake, steam, _, _ = world
    fake.bots = {'alpha': make_bot(connected=False, running=False, required=INPUT_PASSWORD),
                 'bravo': make_bot(connected=False, running=False, required=INPUT_PASSWORD)}
    service.tick()
    posts = fake.posts()
    assert posts == [('/Api/Bot/alpha/Input', {'Type': INPUT_PASSWORD, 'Value': PASSWORD}),
                     ('/Api/Bot/alpha/Input', {'Type': INPUT_TWO_FACTOR, 'Value': CODE}),
                     ('/Api/Bot/alpha/Start', {})]          # one bot per tick: bravo waits
    assert service._attempts['alpha']['count'] == 1


def test_assist_waits_while_asf_login_queue_is_busy(world):
    service, fake, *_ = world
    fake.bots = {'alpha': make_bot(connected=False, running=False, required=INPUT_PASSWORD),
                 'bravo': make_bot(connected=False, running=True)}      # still queued to connect
    service.tick()
    assert fake.posts() == []
    fake.bots['bravo'] = make_bot()
    service.tick()
    assert len(fake.posts('/Start')) == 1


def test_assist_code_only_when_token_asks_for_code(world):
    service, fake, *_ = world
    fake.bots = {'alpha': make_bot(connected=False, running=False, required=INPUT_TWO_FACTOR),
                 'bravo': make_bot()}
    service.tick()
    assert fake.posts() == [('/Api/Bot/alpha/Input', {'Type': INPUT_TWO_FACTOR, 'Value': CODE}),
                            ('/Api/Bot/alpha/Start', {})]


def test_assist_waits_for_a_fresh_code_window(world):
    service, fake, steam, _, sleeps = world
    steam.steam_time = 1000 * 30 + 25          # 5 seconds left in this window
    fake.bots = {'alpha': make_bot(connected=False, running=False, required=INPUT_TWO_FACTOR), 'bravo': make_bot()}
    service.tick()
    assert sleeps == [6]


def test_assist_ignores_running_bots_and_other_prompts(world):
    service, fake, *_ = world
    fake.bots = {'alpha': make_bot(connected=False, running=True, required=INPUT_PASSWORD),
                 'bravo': make_bot(connected=False, running=False, required=INPUT_STEAM_GUARD)}
    service.tick()
    assert fake.posts() == []


def test_assist_without_password_records_error_and_never_starts(world):
    service, fake, steam, _, _ = world
    steam.passwords.clear()
    fake.bots = {'alpha': make_bot(connected=False, running=False, required=INPUT_PASSWORD), 'bravo': make_bot()}
    service.tick()
    assert fake.posts() == []
    assert service._attempts['alpha']['last_error'] == 'no password in Mímir for this login'


def test_assist_backs_off_caps_and_retry_resets(world, monkeypatch):
    service, fake, *_ = world
    fake.bots = {'alpha': make_bot(connected=False, running=False, required=INPUT_TWO_FACTOR), 'bravo': make_bot()}
    clock = [10_000.0]
    monkeypatch.setattr(asf_service.time, 'time', lambda: clock[0])
    service.tick()
    service.tick()                                   # inside the back-off window: no second try
    assert len(fake.posts('/Start')) == 1
    for _ in range(MAX_LOGIN_ATTEMPTS + 2):
        clock[0] += LOGIN_RETRY_SECONDS + 1
        service.tick()
    assert len(fake.posts('/Start')) == MAX_LOGIN_ATTEMPTS
    assert service.status()['accounts'][0]['state'] == 'needs_attention'
    service.retry_login('76561198000000001')
    service.tick()
    assert len(fake.posts('/Start')) == MAX_LOGIN_ATTEMPTS + 1


def test_success_clears_attempts(world):
    service, fake, *_ = world
    service._attempts = {'alpha': {'count': 2, 'last_at': 1, 'last_error': None}}
    fake.bots = {'alpha': make_bot(), 'bravo': make_bot()}
    service.tick()
    assert 'alpha' not in service._attempts


# ---- Ratatoskr coordination -----------------------------------------------------------

def test_pause_for_ratatoskr_pauses_farming_bot_and_persists(world, tmp_path):
    service, fake, steam, ratatoskr, sleeps = world
    fake.bots = {'alpha': make_bot(farming=True, to_farm=(10,)), 'bravo': make_bot()}
    service.pause_for_ratatoskr('Alpha')             # logins match case-insensitively
    assert fake.posts('/Pause') == [('/Api/Bot/alpha/Pause', {'Permanent': True, 'ResumeInSeconds': 0})]
    assert sleeps == [asf_service.PAUSE_SETTLE_SECONDS]
    reloaded = AsfService(steam, ratatoskr, ipc_password='ipc', state_path=service.state_path)
    assert 'alpha' in reloaded._paused_for_ratatoskr   # survives a backend reload


def test_pause_for_ratatoskr_leaves_manual_pause_and_offline_bots(world):
    service, fake, *_ = world
    fake.bots = {'alpha': make_bot(paused=True), 'bravo': make_bot(connected=False)}
    service.pause_for_ratatoskr('alpha')
    service.pause_for_ratatoskr('bravo')
    service.pause_for_ratatoskr('unknown')
    assert fake.posts() == []


def test_pause_for_ratatoskr_never_raises(world):
    service, fake, *_ = world
    fake.fail_on = '/Api/Bot/ASF'
    service.pause_for_ratatoskr('alpha')             # ASF down: Ratatoskr login must go on


def test_resume_waits_for_ratatoskr_session_to_end(world):
    service, fake, steam, ratatoskr, _ = world
    fake.bots = {'alpha': make_bot(paused=True), 'bravo': make_bot()}
    service._paused_for_ratatoskr = {'alpha': {'steamid': '76561198000000001', 'since': 0}}
    ratatoskr.sessions['76561198000000001'] = 'connected'
    service.tick()
    assert fake.posts('/Resume') == []
    assert service.status()['accounts'][0]['state'] == 'paused_for_ratatoskr'
    ratatoskr.sessions.clear()
    service.tick()
    assert fake.posts('/Resume') == [('/Api/Bot/alpha/Resume', {})]
    assert service._paused_for_ratatoskr == {}


def test_manual_pause_is_never_auto_resumed(world):
    service, fake, *_ = world
    fake.bots = {'alpha': make_bot(farming=True, to_farm=(1,)), 'bravo': make_bot()}
    service.pause_for_ratatoskr('alpha')
    service.pause('76561198000000001')               # the user takes over the pause
    service.tick()
    assert fake.posts('/Resume') == []


def test_ratatoskr_login_calls_hook_and_survives_its_failure(monkeypatch):
    import ratatoskr_service
    service = ratatoskr_service.RatatoskrService()
    seen = []

    def hook(login):
        seen.append(login)
        raise RuntimeError('ASF down')
    service.before_login = hook

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'success': True}
    monkeypatch.setattr(ratatoskr_service.requests, 'post', lambda *a, **k: Response())
    assert service.login('alpha', 'pw') == {'success': True}
    assert seen == ['alpha']


# ---- status + safety -------------------------------------------------------------------

def test_status_never_contains_secrets(world):
    service, fake, *_ = world
    fake.bots = {'alpha': make_bot(connected=False, running=False, required=INPUT_PASSWORD),
                 'bravo': make_bot(farming=True, to_farm=(10,))}
    service.tick()
    text = json.dumps(service.status())
    for secret in (PASSWORD, CODE, SHARED_SECRET, 'ipc', 'BotConfig'):
        assert secret not in text
    totals = service.status()['totals']
    assert totals['farming'] == 1 and totals['cards_remaining'] == 3


def test_status_reports_unreachable_asf(world):
    service, fake, *_ = world
    fake.fail_on = '/Api/Bot/ASF'
    service.tick()
    status = service.status()
    assert status['reachable'] is False and status['error'] == 'boom'


def test_disabled_service_does_nothing(tmp_path):
    service = AsfService(FakeSteam(ACCOUNTS, {}), FakeRatatoskr(), ipc_password='',
                         state_path=str(tmp_path / 's.json'))
    service._call = FakeAsf()
    service.tick()
    service.pause_for_ratatoskr('alpha')
    assert service._call.calls == []
    assert service.status()['enabled'] is False
