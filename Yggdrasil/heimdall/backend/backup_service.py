"""Draupnir — point-in-time backups for the portfolio store.

`portfolios.json` holds hand-entered holdings that can't be regenerated, so we
keep a full history of it and can restore any past state.

Design
------
* **Content-addressed snapshots**: every backup is a full copy of the JSON named
  ``portfolios__<UTC-timestamp>__<reason>__<sha1-8>.json`` in a gitignored
  ``backups/portfolios/`` dir. The hash in the name lets us dedupe identical
  states cheaply (no reading of file bodies to list history).
* **Two triggers, one guarantee**:
    - *change* — `PortfolioService._persist()` calls `snapshot('change')` after
      every write, so every distinct state the data ever passed through is
      preserved → true "restore to any point" granularity.
    - *daily*  — a daemon loop takes a `daily` snapshot (and prunes) once a day
      even if nothing changed, plus a `boot` snapshot on startup. This is the
      recurring daily backup.
  Identical content is deduped, so idle days and rapid successive writes don't
  pile up junk.
* **GFS retention** (`_prune`): keep everything < 7 days, newest-per-day up to
  90 days, newest-per-week up to 2 years, then drop. Deep history without
  unbounded growth. A tiny personal JSON — even years of history is a few MB.
* **Safe restore**: restoring first snapshots the *current* state as
  ``pre-restore`` so an unwanted restore is itself reversible, then overwrites
  the source file. The caller reloads the in-memory store afterwards.

Every operation is best-effort and self-contained: a backup failure logs and
returns, it never propagates into (and breaks) a portfolio write.
"""
import hashlib
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

BACKUPS_DIR = os.path.join(os.path.dirname(__file__), 'backups', 'portfolios')

# Retention tiers (see module docstring).
_KEEP_ALL_DAYS = 7
_DAILY_UNTIL_DAYS = 90
_WEEKLY_UNTIL_DAYS = 730

_VALID_REASONS = ('change', 'daily', 'boot', 'manual', 'pre-restore')
# portfolios__20260823T224700Z__change__1a2b3c4d.json
_NAME_RE = re.compile(
    r'^portfolios__(\d{8}T\d{6}Z)__([a-z-]+)__([0-9a-f]{8})\.json$')
_TS_FMT = '%Y%m%dT%H%M%SZ'


def _utcnow():
    return datetime.now(timezone.utc)


def _sha1_8(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:8]


class BackupService:
    def __init__(self, source_path, backup_dir=BACKUPS_DIR):
        self.source_path = source_path
        self.backup_dir = backup_dir
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        os.makedirs(self.backup_dir, exist_ok=True)

    # ---- snapshot ----------------------------------------------------------

    def snapshot(self, reason='change'):
        """Copy the source file into the backup dir, deduped by content hash.

        Returns the backup filename, or None if there was nothing new to save
        (identical to the latest snapshot, or the source is missing). Never
        raises — a failure is logged and swallowed so writes/loops stay safe."""
        if reason not in _VALID_REASONS:
            reason = 'manual'
        try:
            with self._lock:
                if not os.path.exists(self.source_path):
                    return None
                with open(self.source_path, 'rb') as f:
                    data = f.read()
                digest = _sha1_8(data)

                # Dedupe: skip if the most recent snapshot has the same content.
                latest = self._entries()
                if latest and latest[-1]['hash'] == digest:
                    return None

                ts = _utcnow().strftime(_TS_FMT)
                name = f'portfolios__{ts}__{reason}__{digest}.json'
                tmp = os.path.join(self.backup_dir, f'.{name}.tmp')
                dest = os.path.join(self.backup_dir, name)
                with open(tmp, 'wb') as f:
                    f.write(data)
                os.replace(tmp, dest)  # atomic
                return name
        except Exception as e:
            logger.error(f'[DRAUPNIR-BACKUP] snapshot failed: {e}')
            return None

    # ---- listing -----------------------------------------------------------

    def _entries(self):
        """Parsed backup files, oldest first. Cheap — reads only filenames."""
        out = []
        try:
            names = os.listdir(self.backup_dir)
        except FileNotFoundError:
            return out
        for name in names:
            m = _NAME_RE.match(name)
            if not m:
                continue
            ts_str, reason, digest = m.groups()
            try:
                ts = datetime.strptime(ts_str, _TS_FMT).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            out.append({'name': name, 'ts': ts, 'reason': reason, 'hash': digest})
        out.sort(key=lambda e: e['ts'])
        return out

    def list_backups(self):
        """History newest-first, with size + ISO timestamp, for the API/UI."""
        with self._lock:
            entries = self._entries()
        out = []
        for e in reversed(entries):
            try:
                size = os.path.getsize(os.path.join(self.backup_dir, e['name']))
            except OSError:
                size = None
            out.append({
                'name': e['name'],
                'timestamp': e['ts'].isoformat(),
                'reason': e['reason'],
                'hash': e['hash'],
                'size': size,
            })
        return out

    # ---- restore -----------------------------------------------------------

    def restore(self, name):
        """Overwrite the source file with a chosen snapshot.

        Snapshots the current state as ``pre-restore`` first, so restoring is
        itself reversible. Returns {ok, error}. The caller must reload the
        in-memory store afterwards."""
        if not _NAME_RE.match(name or ''):
            return {'ok': False, 'error': 'invalid backup name'}
        src = os.path.join(self.backup_dir, name)
        if not os.path.exists(src):
            return {'ok': False, 'error': 'backup not found'}
        # Safety snapshot of the live state before we clobber it.
        self.snapshot('pre-restore')
        try:
            with self._lock:
                with open(src, 'rb') as f:
                    data = f.read()
                tmp = f'{self.source_path}.restore.tmp'
                with open(tmp, 'wb') as f:
                    f.write(data)
                os.replace(tmp, self.source_path)  # atomic
            return {'ok': True, 'error': None}
        except Exception as e:
            logger.error(f'[DRAUPNIR-BACKUP] restore failed: {e}')
            return {'ok': False, 'error': str(e)}

    def read_backup(self, name):
        """Raw bytes of one snapshot (for download), or None."""
        if not _NAME_RE.match(name or ''):
            return None
        path = os.path.join(self.backup_dir, name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'rb') as f:
                return f.read()
        except OSError:
            return None

    # ---- retention ---------------------------------------------------------

    def _prune(self):
        """GFS thinning: keep all recent, then thin to daily, then weekly."""
        try:
            with self._lock:
                entries = self._entries()
                if not entries:
                    return 0
                now = _utcnow()
                keep = set()
                daily_seen = {}   # 'YYYY-MM-DD' -> newest entry name in that day
                weekly_seen = {}  # (iso-year, iso-week) -> newest entry name
                # newest-first so the first seen per bucket is the one we keep
                for e in reversed(entries):
                    age = now - e['ts']
                    if age <= timedelta(days=_KEEP_ALL_DAYS):
                        keep.add(e['name'])
                    elif age <= timedelta(days=_DAILY_UNTIL_DAYS):
                        day = e['ts'].strftime('%Y-%m-%d')
                        if day not in daily_seen:
                            daily_seen[day] = e['name']
                            keep.add(e['name'])
                    elif age <= timedelta(days=_WEEKLY_UNTIL_DAYS):
                        y, w, _ = e['ts'].isocalendar()
                        if (y, w) not in weekly_seen:
                            weekly_seen[(y, w)] = e['name']
                            keep.add(e['name'])
                    # older than the weekly window -> dropped
                removed = 0
                for e in entries:
                    if e['name'] not in keep:
                        try:
                            os.remove(os.path.join(self.backup_dir, e['name']))
                            removed += 1
                        except OSError:
                            pass
                return removed
        except Exception as e:
            logger.error(f'[DRAUPNIR-BACKUP] prune failed: {e}')
            return 0

    def stats(self):
        with self._lock:
            entries = self._entries()
        total = 0
        for e in entries:
            try:
                total += os.path.getsize(os.path.join(self.backup_dir, e['name']))
            except OSError:
                pass
        return {
            'count': len(entries),
            'total_size': total,
            'oldest': entries[0]['ts'].isoformat() if entries else None,
            'newest': entries[-1]['ts'].isoformat() if entries else None,
        }

    # ---- daily loop --------------------------------------------------------

    def start_daily_loop(self):
        """Daemon: boot snapshot now, then a daily snapshot + prune each day."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info('[DRAUPNIR-BACKUP] Started daily portfolio backup loop.')

    def stop(self):
        self._stop.set()

    def _loop(self):
        # Capture the state at boot, then thin old history once.
        self.snapshot('boot')
        self._prune()
        while not self._stop.is_set():
            # Wake roughly hourly; take the day's snapshot when the UTC date rolls
            # over. Cheap, resilient to restarts, and no clock math to get wrong.
            last_day = _utcnow().strftime('%Y-%m-%d')
            for _ in range(3600):
                if self._stop.is_set():
                    return
                time.sleep(1)
            today = _utcnow().strftime('%Y-%m-%d')
            if today != last_day:
                self.snapshot('daily')
                self._prune()
