"""One shape for long work: a command answers with a job, `GET /api/jobs/<id>` follows it.

A job is {id, kind, running, done, failed, total, phase, error, result,
started_at, finished_at} plus the fields its command adds (steamid, plan_id).
The page polls it; the command line will too. Kept in memory: the newest
`_KEEP` jobs, so a finished job can still be read for a while.
"""
import threading
import time
import uuid
from datetime import datetime, timezone

_KEEP = 200


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class JobRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}

    def create(self, kind, fields=None):
        job = {
            'id': uuid.uuid4().hex, 'kind': kind, 'running': True,
            'done': 0, 'failed': 0, 'total': 0, 'phase': None,
            'error': None, 'result': None,
            'started_at': _now_iso(), 'finished_at': None, '_created': time.time(),
        }
        job.update(fields or {})
        with self._lock:
            self._jobs[job['id']] = job
            if len(self._jobs) > _KEEP:
                for old in sorted(self._jobs.values(), key=lambda j: j['_created'])[:len(self._jobs) - _KEEP]:
                    if not old['running']:
                        self._jobs.pop(old['id'], None)
        return self.get(job['id'])

    def update(self, job_id, **fields):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(fields)

    def finish(self, job_id, result=None, error=None, **fields):
        self.update(job_id, running=False, result=result, error=error,
                    finished_at=_now_iso(), **fields)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return {k: v for k, v in job.items() if not k.startswith('_')} if job else None

    def recent(self, kind=None, steamid=None, limit=20):
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j['_created'], reverse=True)
        picked = [j for j in jobs
                  if (kind is None or j['kind'] == kind) and (steamid is None or j.get('steamid') == str(steamid))]
        return [{k: v for k, v in j.items() if not k.startswith('_')} for j in picked[:limit]]
