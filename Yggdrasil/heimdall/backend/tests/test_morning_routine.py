"""Morning routine: "Get all items" once a day at 08:00 local (catching up after
the Mac slept), then the CSFloat item dictionary and sweep. Fake clock, fake
scan/sweep; the state file lives in tmp_path."""
import datetime

from morning_routine import MorningRoutine

ZONE = datetime.timezone(datetime.timedelta(hours=3))


def at(day, hour, minute=0):
    return datetime.datetime(2026, 9, day, hour, minute, tzinfo=ZONE)


class World:
    def __init__(self, now, last_scan=None, sweep=None, scan_ok=True):
        self.now, self.last_scan, self.scan_ok = now, last_scan, scan_ok
        self.sweep = sweep or {'running': False, 'complete': False, 'finished_at': None}
        self.scans = self.sweeps = 0

    def run_scan(self):
        self.scans += 1
        if not self.scan_ok:
            return False, 'Ratatoskr unreachable'
        self.last_scan = self.now
        return True, 'ok'

    def start_sweep(self):
        self.sweeps += 1
        return True, 'started'

    def routine(self, tmp_path):
        return MorningRoutine(run_scan=self.run_scan, start_sweep=self.start_sweep,
                              last_scan_at=lambda: self.last_scan, sweep_state=lambda: self.sweep,
                              state_path=str(tmp_path / 'morning.json'), now=lambda: self.now)


def folder(tmp_path, name):
    (tmp_path / name).mkdir()
    return tmp_path / name


def test_not_before_eight(tmp_path):
    world = World(at(30, 7, 59), last_scan=at(29, 9))
    routine = world.routine(tmp_path)
    routine.tick()
    assert world.scans == 0


def test_runs_once_at_eight_then_sweeps(tmp_path):
    world = World(at(30, 8, 0), last_scan=at(29, 9))
    routine = world.routine(tmp_path)
    routine.tick()
    assert (world.scans, world.sweeps) == (1, 1)
    world.now = at(30, 9)
    routine.tick()
    assert world.scans == 1                         # once a day


def test_catches_up_after_the_mac_slept(tmp_path):
    world = World(at(30, 13, 5), last_scan=at(29, 18))   # lid closed all morning
    world.routine(tmp_path).tick()
    assert world.scans == 1


def test_a_scan_by_hand_this_morning_counts(tmp_path):
    world = World(at(30, 9, 30), last_scan=at(30, 8, 40))
    world.routine(tmp_path).tick()
    assert world.scans == 0 and world.sweeps == 1


def test_fresh_or_running_sweep_is_not_restarted(tmp_path):
    fresh = World(at(30, 8), last_scan=at(29, 9),
                  sweep={'running': False, 'complete': True, 'finished_at': at(30, 2)})
    fresh.routine(folder(tmp_path, 'a')).tick()
    assert fresh.sweeps == 0
    running = World(at(30, 8), last_scan=at(29, 9), sweep={'running': True})
    running.routine(folder(tmp_path, 'b')).tick()
    assert running.sweeps == 0
    stale = World(at(30, 8), last_scan=at(29, 9),
                  sweep={'running': False, 'complete': True, 'finished_at': at(29, 8)})
    stale.routine(folder(tmp_path, 'c')).tick()
    assert stale.sweeps == 1


def test_failed_scan_retries_later_at_most_three_times(tmp_path, monkeypatch):
    import morning_routine
    clock = [1_000_000.0]
    monkeypatch.setattr(morning_routine.time, 'time', lambda: clock[0])
    world = World(at(30, 8), last_scan=at(29, 9), scan_ok=False)
    routine = world.routine(tmp_path)
    routine.tick()
    routine.tick()                                   # too soon to retry
    assert world.scans == 1
    for _ in range(5):
        clock[0] += MorningRoutine.RETRY_SECONDS
        routine.tick()
    assert world.scans == MorningRoutine.MAX_ATTEMPTS and world.sweeps == 0
    assert routine.status()['last_error'] == 'Ratatoskr unreachable'


def test_state_survives_a_reload(tmp_path):
    world = World(at(30, 8), last_scan=at(29, 9))
    world.routine(tmp_path).tick()
    world.last_scan = at(29, 9)                      # even if the scan time were unknown
    again = world.routine(tmp_path)                  # a backend reload: new instance
    again.tick()
    assert world.scans == 1
    assert again.status()['done_today'] is True



def test_a_scan_by_hand_after_failed_attempts_still_counts(tmp_path, monkeypatch):
    import morning_routine
    clock = [1_000_000.0]
    monkeypatch.setattr(morning_routine.time, 'time', lambda: clock[0])
    world = World(at(30, 8), last_scan=at(29, 9), scan_ok=False)
    routine = world.routine(tmp_path)
    for _ in range(MorningRoutine.MAX_ATTEMPTS):
        routine.tick()
        clock[0] += MorningRoutine.RETRY_SECONDS
    assert world.scans == MorningRoutine.MAX_ATTEMPTS
    assert routine.status()['next_run'].startswith('2026-10-01T08:00')   # no more tries today
    world.last_scan = at(30, 11)                    # then you press "Get all items"
    routine.tick()
    assert world.sweeps == 1 and routine.status()['done_today'] is True
