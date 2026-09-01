"""Central logging configuration for the Heimdall backend.

Call :func:`setup_logging` once at process start (in ``app.py`` and in any
stand-alone entrypoint such as the scheduler). It installs a rotating file
handler under ``logs/`` plus a console handler, so the scattered ``print()``
diagnostics become level-filterable, timestamped, and persisted across restarts
— which matters for the recurring Steam/Huginn rate-limit debugging.

Level comes from ``HEIMDALL_LOG_LEVEL`` (default ``INFO``). Idempotent: calling
it more than once is a no-op, so imports and re-imports won't duplicate handlers.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

_CONFIGURED = False
_FORMAT = '%(asctime)s %(levelname)-7s %(name)s: %(message)s'


def setup_logging(level=None, log_dir=None):
    """Configure the root logger. Safe to call repeatedly."""
    global _CONFIGURED
    if _CONFIGURED:
        return logging.getLogger()

    level = (level or os.environ.get('HEIMDALL_LOG_LEVEL', 'INFO')).upper()
    log_dir = log_dir or os.path.join(os.path.dirname(__file__), 'logs')
    os.makedirs(log_dir, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(_FORMAT)

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'heimdall.log'),
        maxBytes=5_000_000, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(fmt)

    console = logging.StreamHandler()
    console.setFormatter(fmt)

    root.addHandler(file_handler)
    root.addHandler(console)

    _CONFIGURED = True
    return root
