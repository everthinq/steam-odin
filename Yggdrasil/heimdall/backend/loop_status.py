"""One record of every background loop: is its thread alive, when did its last round end,
and what was its last error. The System health page reads it (``GET /api/system/health``).

A loop starts through ``loops.start(name, target)`` (a named daemon thread) and wraps each
round in ``with loops.round(name):``. The round records the error and raises it again, so
the loop's own ``except`` still logs it and keeps the thread alive.
"""
import threading
import time
from contextlib import contextmanager


class LoopStatus:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._loops = {}

    def _entry(self, name):
        return self._loops.setdefault(name, {
            'thread': None, 'part_of': None, 'started_at': None, 'rounds': 0,
            'last_round_at': None, 'last_error': None, 'last_error_at': None,
        })

    def start(self, name, target, *args):
        """Start *target(*args)* as a daemon thread called *name* and remember it."""
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        with self._lock:
            entry = self._entry(name)
            entry.update(thread=thread, started_at=self._clock())
        thread.start()
        return thread

    def watch(self, name, thread):
        """Remember a thread a service started itself (it keeps its own handle)."""
        with self._lock:
            self._entry(name).update(thread=thread, started_at=self._clock())

    @contextmanager
    def round(self, name, part_of=None):
        """One round of the loop *name*: its end time, and its error when it raised. *part_of*
        names the loop whose thread runs it (one thread doing several jobs, such as the scheduler)."""
        try:
            yield
        except Exception as error:
            self._finish(name, part_of, f'{type(error).__name__}: {error}')
            raise
        self._finish(name, part_of, None)

    def beat(self, name):
        """A round of *name* ended without an error (for a loop whose parts catch their own)."""
        self._finish(name, None, None)

    def _finish(self, name, part_of, error):
        now = self._clock()
        with self._lock:
            entry = self._entry(name)
            if part_of:
                entry['part_of'] = part_of
            entry['rounds'] += 1
            entry['last_round_at'] = now
            if error:
                entry.update(last_error=error, last_error_at=now)

    def snapshot(self):
        """Every loop, sorted by name: running, part_of, started_at, rounds, last_round_at,
        last_error, last_error_at. A part runs while the thread of the loop it is part of runs."""
        with self._lock:
            entries = {name: dict(entry) for name, entry in self._loops.items()}
        threads = {name: entry['thread'] for name, entry in entries.items()}
        rows = []
        for name in sorted(entries):
            entry = entries[name]
            thread = threads.get(entry['part_of']) if entry['part_of'] else entry['thread']
            entry.pop('thread')
            rows.append({'name': name, 'running': bool(thread and thread.is_alive()), **entry})
        return rows


loops = LoopStatus()
