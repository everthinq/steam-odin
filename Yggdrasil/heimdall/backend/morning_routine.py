"""Morning routine: once a day, "Get all items", then refresh CSFloat prices.

Every minute it checks whether today's run is due: at or after the configured
local time (08:00 by default) and no inventory scan yet since that time today.
A scan you ran by hand after 08:00 counts. Docker is frozen while the Mac
sleeps, so a missed morning (lid closed) runs within a minute of waking.

A run:
  1. "Get all items" (the same guarded scan as the button; it logs out the
     Ratatoskr sessions it opened, so ASF farming resumes straight away),
     and rebuilds the CSFloat item dictionary from what you now hold,
  2. starts the CSFloat buy-order sweep, unless one is running or a complete
     sweep finished within the last SWEEP_FRESH_SECONDS.
A scan that could not reach a single account counts as failed (the previous
scan and dictionary are kept).
A failed scan is retried after RETRY_SECONDS, at most MAX_ATTEMPTS a day.
State lives in cache/morning_routine.json so reloads never repeat a run.
"""

import datetime
import logging
import threading
import time

from jsonio import atomic_write_json, read_json

logger = logging.getLogger(__name__)


class MorningRoutine:
    TICK_SECONDS = 60
    RETRY_SECONDS = 30 * 60
    MAX_ATTEMPTS = 3
    SWEEP_FRESH_SECONDS = 12 * 60 * 60

    def __init__(self, run_scan, start_sweep, last_scan_at, sweep_state,
                 time_of_day='08:00', state_path='cache/morning_routine.json',
                 now=None, sleep=time.sleep):
        """run_scan() -> (ok, message) runs the guarded inventory scan (which also
        rebuilds the CSFloat item dictionary); start_sweep() -> (ok, message);
        last_scan_at() -> aware datetime of the latest scan or None;
        sweep_state() -> {'running', 'complete', 'finished_at' (aware datetime or None)}."""
        self.run_scan = run_scan
        self.start_sweep = start_sweep
        self.last_scan_at = last_scan_at
        self.sweep_state = sweep_state
        self.time_of_day = time_of_day
        self.state_path = state_path
        self._now = now or (lambda: datetime.datetime.now().astimezone())
        self._sleep = sleep
        self._lock = threading.Lock()
        self._state = read_json(state_path, default={}) or {}

    # ---- when ------------------------------------------------------------------

    def _today_start(self, now):
        hour, minute = (int(part) for part in self.time_of_day.split(':'))
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def due(self):
        now = self._now()
        start = self._today_start(now)
        if now < start:
            return False
        today = now.date().isoformat()
        if self._state.get('done_date') == today:
            return False
        last = self.last_scan_at()
        if last is not None and last >= start:
            # A scan already ran this morning (the button, even after failed automatic
            # attempts, or a run before a reload): only the sweep is left to do.
            self._finish(today, 'scan already done today', scanned=False)
            return False
        if self._state.get('attempt_date') == today:
            if self._attempts_left(today) <= 0:
                return False
            last_try = self._state.get('last_attempt_ts') or 0
            if time.time() - last_try < self.RETRY_SECONDS:
                return False
        return True

    def _attempts_left(self, today):
        if self._state.get('attempt_date') != today:
            return self.MAX_ATTEMPTS
        return self.MAX_ATTEMPTS - self._state.get('attempts', 0)

    # ---- run -------------------------------------------------------------------

    def run_once(self):
        """One morning run (scan, links, sweep). Returns a short result string."""
        now = self._now()
        today = now.date().isoformat()
        with self._lock:
            attempts = self._state.get('attempts', 0) if self._state.get('attempt_date') == today else 0
            self._state.update({'attempt_date': today, 'attempts': attempts + 1,
                                'last_attempt_ts': time.time(), 'last_attempt_at': now.isoformat()})
            self._persist()
        logger.info('[MORNING] Daily "Get all items" starting')
        ok, message = self.run_scan()
        if not ok:
            logger.warning(f'[MORNING] Scan did not run: {message}')
            with self._lock:
                self._state['last_error'] = message
                self._persist()
            return f'scan failed: {message}'
        self._finish(today, 'scan done', scanned=True)
        return self._state.get('last_result', 'scan done')

    def _finish(self, today, result, scanned):
        sweep = self._maybe_start_sweep()
        with self._lock:
            self._state.update({'done_date': today, 'last_error': None,
                                'last_result': f'{result}; {sweep}'})
            if scanned:
                self._state['last_scan_at'] = self._now().isoformat()
            self._persist()
        logger.info(f'[MORNING] {result}; {sweep}')

    def _maybe_start_sweep(self):
        state = self.sweep_state() or {}
        if state.get('running'):
            return 'CSFloat sweep already running'
        finished = state.get('finished_at')
        if state.get('complete') and finished is not None and \
                (self._now() - finished).total_seconds() < self.SWEEP_FRESH_SECONDS:
            return f'CSFloat prices are fresh (swept within {self.SWEEP_FRESH_SECONDS // 3600} hours)'
        ok, message = self.start_sweep()
        return 'CSFloat sweep started' if ok else f'CSFloat sweep not started: {message}'

    # ---- loop + status -----------------------------------------------------------

    def _persist(self):
        try:
            atomic_write_json(self.state_path, self._state, indent=None)
        except Exception as e:
            logger.warning(f'[MORNING] Could not save state: {e}')

    def tick(self):
        if self.due():
            self.run_once()

    def start_background(self):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:
                    logger.error(f'[MORNING] tick failed: {e}')
                self._sleep(self.TICK_SECONDS)
        threading.Thread(target=loop, name='morning-routine', daemon=True).start()

    def status(self):
        now = self._now()
        start = self._today_start(now)
        next_run = start if now < start else start + datetime.timedelta(days=1)
        with self._lock:
            state = dict(self._state)
        today = now.date().isoformat()
        if state.get('done_date') != today and now >= start and self._attempts_left(today) > 0:
            next_run = now      # due (or retrying) today; after the last failed try: tomorrow
        return {
            'time_of_day': self.time_of_day,
            'done_today': state.get('done_date') == now.date().isoformat(),
            'next_run': next_run.isoformat(),
            'last_attempt_at': state.get('last_attempt_at'),
            'last_result': state.get('last_result'),
            'last_error': state.get('last_error'),
        }
