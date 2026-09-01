"""Crash-safe JSON persistence for Heimdall/Draupnir.

All hand-entered, un-regenerable state (``portfolios.json``, ``settings.json``)
and the regenerable caches are written through :func:`atomic_write_json` so that
a crash, a full disk, or a kill mid-write can never truncate the live file:

1. write a sibling temp file in the *same* directory,
2. ``flush`` + ``fsync`` it so the bytes are really on disk,
3. ``os.replace`` it over the target — an atomic swap on POSIX,
4. ``fsync`` the directory so the rename itself is durable.

Reads go through :func:`read_json`, which on a corrupt/half-written file logs and
falls back to a caller-supplied ``recover`` hook (e.g. the newest good backup)
rather than silently returning an empty store — the difference between "restore
from backup" and "lose the book".

The module holds no state; a per-path lock is the caller's responsibility (the
services already hold their own ``threading.Lock`` around read-modify-write).
"""
import json
import logging
import os
import tempfile

log = logging.getLogger(__name__)


def atomic_write_json(path, data, *, indent=2):
    """Durably write *data* as JSON to *path*, atomically.

    Never leaves a half-written target: on any failure the temp file is removed
    and the original *path* is left exactly as it was. Raises on failure so the
    caller can decide (the callers log-and-continue to stay write-safe)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.tmp-', suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX; overwrites in one step
        _fsync_dir(directory)
    except BaseException:
        # Clean up the temp file; the original target is untouched.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_bytes(path, blob):
    """Durably write raw *blob* bytes to *path*, atomically (see module docstring).

    Used for encrypted maFiles so a crash mid-write can't truncate an account's
    2FA secrets."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.tmp-', suffix='.bin')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(directory)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, default=None, *, recover=None):
    """Read JSON from *path*, tolerating a missing or corrupt file.

    * missing file      -> *default* (called if it is callable)
    * corrupt/truncated -> log, try *recover()*; if that yields None, *default*

    Use *recover* to point at the newest good backup so a corrupted live file is
    self-healing instead of fatal."""
    if not os.path.exists(path):
        return default() if callable(default) else default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        log.error('corrupt/unreadable JSON at %s: %s', path, e)
        if recover is not None:
            try:
                recovered = recover()
            except Exception as re:  # a broken recovery must not mask the read
                log.error('recovery hook failed for %s: %s', path, re)
                recovered = None
            if recovered is not None:
                log.warning('recovered %s from fallback', path)
                return recovered
        return default() if callable(default) else default


def _fsync_dir(directory):
    """Best-effort fsync of a directory so a rename is durable. No-op where the
    platform doesn't support opening a directory (e.g. Windows)."""
    try:
        dfd = os.open(directory, os.O_DIRECTORY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)
