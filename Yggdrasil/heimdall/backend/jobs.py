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


class JobSlot:
    """One job at a time for one service (a check, a dry run, a purchase), kept in two shapes:

    - the shared registry job (`GET /api/jobs/<id>`), which every command answers;
    - the service's own view in its `status()`: the service's own `kind` ("plan", "unlock",
      "price sheet"), times in epoch seconds, and fields such as `account` and `step` that
      its page reads.

    `set` changes the view and copies to the registry the fields named in `mirror` (None:
    every field). A second `start` while a job runs answers None (the service is busy).
    """

    def __init__(self, jobs, idle, id_key='job_id', mirror=None):
        self.jobs = jobs
        self._idle = dict(idle, **{id_key: None})
        self._id_key = id_key
        self._mirror = mirror
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self._view = dict(self._idle)

    def start(self, kind, registry_kind, registry_fields=None, **view_fields):
        """Make the registry job and the fresh view; None when a job already runs."""
        if not self._busy.acquire(blocking=False):
            return None
        try:
            job = self.jobs.create(registry_kind, registry_fields)
        except BaseException:
            self._busy.release()     # never leave the service busy when the job could not start
            raise
        with self._lock:
            self._view = {**self._idle, 'running': True, 'kind': kind, 'phase': 'starting',
                          'started_at': time.time(), 'finished_at': None, self._id_key: job['id'],
                          **view_fields}
        return job

    def set(self, **fields):
        with self._lock:
            self._view.update(fields)
            job_id = self._view.get(self._id_key)
        mirrored = fields if self._mirror is None else {key: fields[key] for key in self._mirror if key in fields}
        if job_id and mirrored:
            self.jobs.update(job_id, **mirrored)

    def mark_finished(self, **view_fields):
        """The view reads as finished; the service may still save before `release`."""
        with self._lock:
            self._view.update(running=False, finished_at=time.time(), **view_fields)

    def release(self, result=None, error=None, **registry_fields):
        """Let the next job start, then finish the registry job (a page that sees it finished
        can start the next one at once)."""
        with self._lock:
            job_id = self._view.get(self._id_key)
        self._busy.release()
        if job_id:
            self.jobs.finish(job_id, result=result, error=error, **registry_fields)

    def view(self):
        with self._lock:
            return dict(self._view)

    @property
    def kind(self):
        with self._lock:
            return self._view.get('kind')
