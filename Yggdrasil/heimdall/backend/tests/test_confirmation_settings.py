"""Global Confirmations settings: limits refused not changed, Check All Now as a job."""
from commands import CommandError  # noqa: E402
import threading

import pytest

import confirmation_settings as cs
from jobs import JobRegistry
from scheduler import ConfirmationScheduler


def test_interval_below_the_minimum_is_refused_not_raised():
    with pytest.raises(CommandError) as error:
        cs.clean({'check_interval': cs.CHECK_INTERVAL_MIN_SECONDS - 1})
    assert error.value.code == 'interval_out_of_range'


def test_interval_inside_the_limits_and_switches_are_kept():
    assert cs.clean({'check_interval': cs.CHECK_INTERVAL_MIN_SECONDS, 'auto_confirm_trades': True,
                     'tradeon_token': 'ignored'}) == {
        'check_interval': cs.CHECK_INTERVAL_MIN_SECONDS, 'auto_confirm_trades': True}


@pytest.mark.parametrize('body, code', [
    ({'check_interval': '30'}, 'bad_interval'),
    ({'check_interval': True}, 'bad_interval'),
    ({'auto_check_enabled': 'yes'}, 'bad_switch'),
    ({}, 'nothing_to_save'),
    (None, 'bad_body'),
])
def test_bad_values_are_refused(body, code):
    with pytest.raises(CommandError) as error:
        cs.clean(body)
    assert error.value.code == code


def test_loop_interval_keeps_an_old_value_inside_the_limits():
    assert cs.loop_interval({'check_interval': 1}) == cs.CHECK_INTERVAL_MIN_SECONDS
    assert cs.loop_interval({'check_interval': 99999}) == cs.CHECK_INTERVAL_MAX_SECONDS
    assert cs.loop_interval({'check_interval': 60}) == 60


def test_view_sends_limits_and_the_note():
    view = cs.view({'check_interval': 60, 'auto_confirm_market': True, 'tradeon_token': 'secret'})
    assert view['limits']['check_interval']['min'] == cs.CHECK_INTERVAL_MIN_SECONDS
    assert 'tradeon_token' not in view['settings']
    assert view['polling_on'] is True
    assert 'even with Automatic Checking off' in view['polling_note']
    assert 'does not check' in cs.view({'check_interval': 60})['polling_note']


class _Steam:
    def __init__(self, gate=None):
        self.gate = gate

    def _mobileconf_cooldown_remaining(self):
        return 0

    def get_all_accounts_data(self):
        return [{'steamid': '1'}, {'steamid': '2'}]

    def get_confirmations(self, steamid):
        if self.gate:
            self.gate.wait(5)
        return {'success': steamid == '1', 'confirmations': [], 'message': 'no session'}


def _wait(jobs, job_id):
    for _ in range(100):
        job = jobs.get(job_id)
        if not job['running']:
            return job
        threading.Event().wait(0.05)
    raise AssertionError('job did not finish')


def test_check_all_now_is_a_job_with_a_summary(monkeypatch):
    monkeypatch.setattr('scheduler.time.sleep', lambda seconds: None)
    jobs = JobRegistry()
    scheduler = ConfirmationScheduler(None, _Steam())
    job = scheduler.start_check_all({}, jobs)
    finished = _wait(jobs, job['id'])
    assert finished['result']['checked'] == 1
    assert finished['result']['failed'] == 1
    assert finished['result']['message'].startswith('Checked 1 of 2 accounts.')


def test_a_second_check_while_one_runs_gets_no_job(monkeypatch):
    monkeypatch.setattr('scheduler.time.sleep', lambda seconds: None)
    gate = threading.Event()
    jobs = JobRegistry()
    scheduler = ConfirmationScheduler(None, _Steam(gate))
    first = scheduler.start_check_all({}, jobs)
    assert scheduler.start_check_all({}, jobs) is None
    gate.set()
    _wait(jobs, first['id'])
    assert scheduler.start_check_all({}, jobs) is not None
