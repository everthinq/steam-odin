"""One store for the plans a command keeps between "plan" and "execute plan <id>".

A command that acts answers a plan first and runs it once later (the root CLAUDE.md,
"Architecture in one breath"). Every such plan is kept here: one id shape, one lifetime
stamp (`created_at`, `expires_at`), one cleanup. The checks that refuse a plan (unknown,
expired, already run) stay with each command, with that command's own codes.

Kept in memory only: a restart forgets every plan, so a page makes a new one.
"""
import secrets
import threading
import time


def expired(plan, now):
    """The default cleanup rule: a plan is forgotten once its lifetime is over."""
    return plan['expires_at'] < now


class PlanStore:
    def __init__(self, lifetime_seconds, forget=expired, clock=time.time):
        """`forget(plan, now)` says which stored plans the next `add` drops. With
        `lifetime_seconds` None the command sets `expires_at` itself (for example when
        its dry run ends); the store only gives the id and `created_at`."""
        self.lifetime_seconds = lifetime_seconds
        self.forget = forget
        self.clock = clock
        self._lock = threading.Lock()
        self._plans = {}

    def add(self, plan):
        """Give `plan` its id and lifetime, keep it, and drop the plans `forget` names."""
        now = self.clock()
        plan.update({'id': secrets.token_hex(16), 'created_at': now})
        if self.lifetime_seconds is not None:
            plan['expires_at'] = now + self.lifetime_seconds
        with self._lock:
            for plan_id in [key for key, old in self._plans.items() if self.forget(old, now)]:
                del self._plans[plan_id]
            self._plans[plan['id']] = plan
        return plan

    def get(self, plan_id):
        """The stored plan, expired or not (each command refuses an expired one itself), or None."""
        with self._lock:
            return self._plans.get(plan_id)

    def remove(self, plan_id):
        with self._lock:
            self._plans.pop(plan_id, None)

    def values(self):
        """Every stored plan, as a list (safe to change the store while going through it)."""
        with self._lock:
            return list(self._plans.values())

    def is_expired(self, plan):
        return plan['expires_at'] < self.clock()

    def __getitem__(self, plan_id):
        return self._plans[plan_id]

    def __setitem__(self, plan_id, plan):
        """Keep a plan under a chosen id (tests only; commands use `add`)."""
        with self._lock:
            self._plans[plan_id] = plan

    def __contains__(self, plan_id):
        return plan_id in self._plans

    def __len__(self):
        return len(self._plans)
