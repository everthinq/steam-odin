"""The last errors the backend logged, and how much memory it uses (the System health page).

``recent_errors`` is a logging handler on the root logger (``logging_setup.py`` installs it):
it keeps the newest ``RECENT_ERROR_LIMIT`` records at level ERROR or above, from every loop,
route and service, in memory only (the full log stays in ``logs/heimdall.log``).

``memory_use()`` reads Linux's own numbers (the backend runs in a Linux container): the
process's resident memory and its peak from ``/proc/self/status``, and the container's total
and limit from its control group. A number that cannot be read is ``None``.
"""
import logging
import threading
from collections import deque

RECENT_ERROR_LIMIT = 30
PROCESS_STATUS_FILE = '/proc/self/status'
CONTAINER_MEMORY_FILES = {'container_bytes': '/sys/fs/cgroup/memory.current',
                          'container_limit_bytes': '/sys/fs/cgroup/memory.max'}


class RecentErrors(logging.Handler):
    def __init__(self, limit=RECENT_ERROR_LIMIT):
        super().__init__(level=logging.ERROR)
        self._records = deque(maxlen=limit)
        self._lock_records = threading.Lock()

    def emit(self, record):
        try:
            message = record.getMessage()
        except Exception:  # a bad format string must never break logging
            message = str(record.msg)
        if record.exc_info and record.exc_info[1] is not None:
            error = record.exc_info[1]
            message = f'{message} ({type(error).__name__}: {error})'
        with self._lock_records:
            self._records.append({'at': record.created, 'level': record.levelname,
                                  'source': record.name, 'thread': record.threadName,
                                  'message': message})

    def newest_first(self):
        with self._lock_records:
            return list(reversed(self._records))


recent_errors = RecentErrors()


def _kilobytes_field(text, field):
    """'VmRSS:     512616 kB' -> 524918784 (bytes)."""
    for line in text.splitlines():
        if line.startswith(f'{field}:'):
            return int(line.split()[1]) * 1024
    return None


def _read(path):
    try:
        with open(path, encoding='ascii') as handle:
            return handle.read().strip()
    except OSError:
        return None


def memory_use():
    """{process_bytes, process_peak_bytes, container_bytes, container_limit_bytes}; None when unknown.
    The container limit is None when Docker sets none (the file says 'max')."""
    status = _read(PROCESS_STATUS_FILE) or ''
    answer = {'process_bytes': _kilobytes_field(status, 'VmRSS'),
              'process_peak_bytes': _kilobytes_field(status, 'VmHWM')}
    for key, path in CONTAINER_MEMORY_FILES.items():
        value = _read(path)
        answer[key] = int(value) if value and value.isdigit() else None
    return answer
