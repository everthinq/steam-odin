"""Storage Units: what may go in, which unit it goes into, and the move commands.

The one copy of the storage rules (docs/internals/storage-units.md). Transfer's
To mode and Auto-Store both deposit through `DepositService`, so they cannot
drift apart; the page only displays what the backend decides.

* **What may go in** — `storage_block(item)`: a Storage Unit, an item the game
  cannot store (`item_moveable: false`: medals, coins, badges, the default music
  kit, default weapons) and an item under trade protection (attribute 312,
  received in a trade; Ratatoskr's `trade_locked` + `trade_protected_until`) are
  refused. A plain trade hold (a Steam Community Market purchase, `trade_unlock`
  only) is NOT a block.
* **Which unit** — `plan_deposit`: every unit together is one storage; the
  fullest unit with room fills first (Ivan's choice, 2026-10-31), full units are
  skipped, one batch per unit.
* **The commands** — `plan` → `execute` once → a job. Executing a plan a second
  time returns the same job, so a double click or a retried request never moves
  twice. Plans expire after `PLAN_LIFETIME_SECONDS`. One move run per account at a
  time; batches go one after another (Steam rate limits), never in parallel.
  Ratatoskr confirms every move from the Game Coordinator's inventory update
  (a deposit: the item left the inventory; a withdrawal: it came back) and
  lists the confirmed ids of each run; only those count as moved, so the result
  says exactly which items moved and which were refused, not sent or unconfirmed.
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from commands import CommandError

logger = logging.getLogger(__name__)

STORAGE_CAPACITY = 1000
STORAGE_UNIT_DEFINITION = 1201
PLAN_LIFETIME_SECONDS = 10 * 60
# An executed plan is kept this long (its execute keeps answering the same job), then forgotten.
PLAN_KEEP_SECONDS = 60 * 60
# The Game Coordinator's unit count lags a few seconds behind a move; what a run
# just deposited is remembered this long so a quick second plan never over-fills a unit.
EXPECTED_COUNT_SECONDS = 120
_STATUS_POLL_SECONDS = 0.5
# A batch whose Ratatoskr queue shows no progress for this long is given up.
_BATCH_STALL_SECONDS = 60


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def storage_block(item, now=None):
    """Why this inventory item cannot go into a Storage Unit, or None when it can.

    {'code', 'reason'} plus 'until' (ISO time) for trade protection.
    """
    if item.get('def_index') == STORAGE_UNIT_DEFINITION:
        return {'code': 'storage_unit', 'reason': 'A Storage Unit cannot go into another Storage Unit'}
    if item.get('item_moveable') is False:
        return {'code': 'unstorable', 'reason': 'A Storage Unit cannot hold this item'}
    if item.get('trade_locked'):
        until = item.get('trade_protected_until')
        until_time = _parse_time(until)
        now = now or datetime.now(timezone.utc)
        if until_time is None or until_time > now:
            return {'code': 'trade_protected', 'until': until,
                    'reason': 'Received in a trade: it can be stored once the trade protection ends'}
    return None


def is_storable(item, now=None):
    return storage_block(item, now) is None


def unit_count(casket):
    return int(casket.get('item_storage_total') or 0)


def free_places(casket):
    return max(0, STORAGE_CAPACITY - unit_count(casket))


def plan_deposit(item_ids, caskets):
    """Split the items over the Storage Units as if they were one storage.

    Returns (batches, overflow): batches is a list of (casket_id, [item_ids]),
    the fullest unit with room first; overflow are the items no unit has room for.
    """
    units = sorted((c for c in caskets if free_places(c) > 0), key=free_places)
    batches = []
    remaining = list(item_ids)
    for casket in units:
        if not remaining:
            break
        take = min(free_places(casket), len(remaining))
        batches.append((str(casket.get('item_id')), remaining[:take]))
        remaining = remaining[take:]
    return batches, remaining


def casket_label(caskets, casket_id):
    for casket in caskets:
        if str(casket.get('item_id')) == str(casket_id):
            return casket.get('item_customname') or casket.get('item_name') or 'Storage Unit'
    return 'Storage Unit'


class DepositService:
    """Deposit and withdraw commands over Ratatoskr, for Transfer, Auto-Store and the command line."""

    def __init__(self, ratatoskr_service, jobs):
        self.ratatoskr = ratatoskr_service
        self.jobs = jobs
        self._lock = threading.Lock()
        self._plans = {}          # plan id -> plan
        self._running = {}        # steamid -> job id of its move run
        self._expected = {}       # steamid -> {casket_id: (count, at)}
        self._last_finished = {}  # steamid -> when its last move run finished

    # ---- reading ----------------------------------------------------------

    def inventory(self, steamid):
        """The loose inventory (Auto-Store's suggestions read it)."""
        return self._inventory(steamid)

    def _inventory(self, steamid, retry_empty=0):
        """The loose inventory; asked again (1 s apart) while empty — it can lag a fresh connect."""
        for attempt in range(retry_empty + 1):
            answer = self.ratatoskr.get_inventory(steamid) or {}
            if answer.get('error'):
                raise CommandError('ratatoskr_unavailable', answer['error'], 502)
            items = answer.get('items') or []
            if items or attempt == retry_empty:
                return items
            time.sleep(1)
        return []

    def caskets(self, steamid):
        """The account's Storage Units, each count raised to what a run just deposited."""
        answer = self.ratatoskr.get_caskets(steamid) or {}
        if answer.get('error'):
            raise CommandError('ratatoskr_unavailable', answer['error'], 502)
        caskets = [dict(c) for c in (answer.get('caskets') or [])]
        now = time.time()
        with self._lock:
            expected = self._expected.get(str(steamid), {})
            for casket in caskets:
                known = expected.get(str(casket.get('item_id')))
                if not known:
                    continue
                count, at = known
                if now - at > EXPECTED_COUNT_SECONDS or unit_count(casket) >= count:
                    expected.pop(str(casket.get('item_id')), None)
                else:
                    casket['item_storage_total'] = count
        return caskets

    def storage(self, steamid):
        """Free places across every unit (the Storage space panel)."""
        caskets = self.caskets(steamid)
        return {
            'capacity': STORAGE_CAPACITY,
            'units': len(caskets),
            'units_with_room': sum(1 for c in caskets if free_places(c) > 0),
            'free': sum(free_places(c) for c in caskets),
            'stored': sum(unit_count(c) for c in caskets),
        }

    # ---- plans ------------------------------------------------------------

    def plan_deposit(self, steamid, item_ids=None, all_storable=False, names=None,
                     exclude=(), allow_overflow=False, retry_empty=0):
        """Plan a deposit of the given items, every storable item, or the items with these names.

        Refused items are listed with their reason and never sent. With
        allow_overflow (Auto-Store) the items no unit has room for are left out;
        otherwise a plan with overflow cannot be executed (Transfer sends nothing).
        """
        steamid = str(steamid)
        loose = {str(i.get('item_id')): i for i in self._inventory(steamid, retry_empty)}
        excluded = {str(i) for i in exclude}
        if item_ids is not None:
            wanted = [str(i) for i in item_ids]
        elif names is not None:
            watch = {str(n).strip().lower() for n in names if str(n).strip()}
            wanted = [i for i, it in loose.items() if str(it.get('item_name', '')).strip().lower() in watch]
        elif all_storable:
            wanted = list(loose)
        else:
            raise CommandError('nothing_chosen', 'Choose items, names or all storable items')

        now = datetime.now(timezone.utc)
        to_send, refused = [], []
        for item_id in dict.fromkeys(wanted):
            if item_id in excluded:
                continue
            item = loose.get(item_id)
            if item is None:
                refused.append({'item_id': item_id, 'code': 'not_in_inventory',
                                'reason': 'Not in the inventory (already stored or gone)'})
                continue
            block = storage_block(item, now)
            if block:
                if item_ids is not None or block['code'] == 'trade_protected':
                    refused.append({'item_id': item_id, 'item_name': item.get('item_name'), **block})
                continue
            to_send.append(item_id)

        # Nothing to send (an Auto-Store sweep with nothing new): no need to read the units.
        caskets = self.caskets(steamid) if to_send else []
        batches, overflow = plan_deposit(to_send, caskets)
        names_by_id = {i: loose[i].get('item_name') for i in to_send}
        plan = self._store_plan({
            'kind': 'deposit',
            'steamid': steamid,
            'source': 'inventory',
            'target': 'casket',
            'batches': [{'casket_id': cid, 'casket_name': casket_label(caskets, cid), 'item_ids': ids,
                         'count_before': next((unit_count(c) for c in caskets
                                               if str(c.get('item_id')) == cid), 0)}
                        for cid, ids in batches],
            'item_names': names_by_id,
            'refused': refused,
            'overflow': overflow,
            'allow_overflow': bool(allow_overflow),
            'free': sum(free_places(c) for c in caskets) if caskets else None,
        })
        return self.describe(plan)

    def plan_withdraw(self, steamid, items):
        """Plan taking items out of Storage Units: items is [{item_id, casket_id}]."""
        steamid = str(steamid)
        by_casket, seen = {}, set()
        for entry in items or []:
            item_id, casket_id = str(entry.get('item_id') or ''), str(entry.get('casket_id') or '')
            if not item_id or not casket_id:
                raise CommandError('bad_item', 'Every item needs item_id and casket_id')
            if item_id not in seen:
                seen.add(item_id)
                by_casket.setdefault(casket_id, []).append(item_id)
        if not by_casket:
            raise CommandError('nothing_chosen', 'Choose the items to take out')
        caskets = self.caskets(steamid)
        plan = self._store_plan({
            'kind': 'withdraw',
            'steamid': steamid,
            'source': 'casket',
            'target': 'inventory',
            'batches': [{'casket_id': cid, 'casket_name': casket_label(caskets, cid), 'item_ids': ids}
                        for cid, ids in by_casket.items()],
            'item_names': {},
            'refused': [],
            'overflow': [],
            'allow_overflow': False,
        })
        return self.describe(plan)

    def _store_plan(self, plan):
        now = time.time()
        plan.update({'id': uuid.uuid4().hex, 'created_at': now,
                     'expires_at': now + PLAN_LIFETIME_SECONDS, 'job_id': None})
        with self._lock:
            for plan_id in [p for p, old in self._plans.items()
                            if (old['expires_at'] < now and not old['job_id'])
                            or old['created_at'] < now - PLAN_KEEP_SECONDS]:
                del self._plans[plan_id]
            self._plans[plan['id']] = plan
        return plan

    @staticmethod
    def describe(plan):
        """What a client sees of a plan."""
        moves = sum(len(b['item_ids']) for b in plan['batches'])
        return {
            'plan_id': plan['id'],
            'kind': plan['kind'],
            'steamid': plan['steamid'],
            'moves': moves,
            'batches': [{'casket_id': b['casket_id'], 'casket_name': b['casket_name'],
                         'count': len(b['item_ids'])} for b in plan['batches']],
            'refused': plan['refused'],
            'overflow': len(plan['overflow']),
            'free': plan.get('free'),
            'executable': moves > 0 and (plan['allow_overflow'] or not plan['overflow']),
            'expires_at': datetime.fromtimestamp(plan['expires_at'], timezone.utc).isoformat(),
            'job_id': plan['job_id'],
        }

    # ---- execution --------------------------------------------------------

    def execute(self, plan_id, on_finish=None):
        """Run a plan once and return its job. A second call returns the same job."""
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise CommandError('plan_unknown', 'Unknown plan (make a new one)', 404)
            if plan['job_id']:
                job = self.jobs.get(plan['job_id'])
                if job is None:
                    raise CommandError('job_gone', 'This plan already ran; its result is no longer kept', 410)
                return job
            if plan['expires_at'] < time.time():
                raise CommandError('plan_expired', 'The plan expired: make a new one')
            if plan['overflow'] and not plan['allow_overflow']:
                raise CommandError('no_room', f"{len(plan['overflow'])} item(s) more than the Storage Units have room for")
            if not plan['batches']:
                raise CommandError('nothing_to_move', 'Nothing in this plan can be moved')
            running = self._running.get(plan['steamid'])
            if running and (self.jobs.get(running) or {}).get('running'):
                raise CommandError('busy', 'Items are already moving on this account', 409)
            # The unit counts it was made from are out of date once another run has moved items.
            if plan['created_at'] < self._last_finished.get(plan['steamid'], 0):
                raise CommandError('plan_stale', 'Items moved on this account since this plan was made: make a new one', 409)
            job = self.jobs.create(plan['kind'], {
                'steamid': plan['steamid'], 'plan_id': plan_id,
                'total': sum(len(b['item_ids']) for b in plan['batches']),
            })
            plan['job_id'] = job['id']
            self._running[plan['steamid']] = job['id']
        threading.Thread(target=self._run, args=(plan, job['id'], on_finish),
                         name=f"move-{plan['kind']}", daemon=True).start()
        return job

    def busy(self, steamid):
        job_id = self._running.get(str(steamid))
        return bool(job_id and (self.jobs.get(job_id) or {}).get('running'))

    def _run(self, plan, job_id, on_finish):
        steamid = plan['steamid']
        moved, failed, results, error = 0, 0, [], None
        try:
            for index, batch in enumerate(plan['batches']):
                self.jobs.update(job_id, phase=f"{batch['casket_name']} ({index + 1} of {len(plan['batches'])})")
                outcome = self._run_batch(plan, batch, job_id, moved, failed)
                moved += len(outcome['moved_ids'])
                failed += len(outcome['failed'])
                results.append({'casket_id': batch['casket_id'], 'casket_name': batch['casket_name'],
                                'moved': len(outcome['moved_ids']), 'moved_ids': outcome['moved_ids'],
                                'failed': outcome['failed']})
                if plan['kind'] == 'deposit' and outcome['moved_ids']:
                    with self._lock:
                        self._expected.setdefault(steamid, {})[batch['casket_id']] = (
                            batch.get('count_before', 0) + len(outcome['moved_ids']), time.time())
                elif plan['kind'] == 'withdraw':
                    with self._lock:
                        self._expected.pop(steamid, None)
        except Exception as exception:   # never leave a job "running" forever
            logger.exception(f"[MOVE] {steamid}: {plan['kind']} run failed")
            error = str(exception)
        result = {
            'moved': moved, 'failed': failed, 'batches': results,
            'refused': plan['refused'],
            'failed_items': [{'item_id': f['item_id'], 'item_name': plan['item_names'].get(f['item_id']),
                              'code': f['code']}
                             for r in results for f in r['failed']],
        }
        self.jobs.update(job_id, result=result, error=error, done=moved, failed=failed)
        # Before the job reads as finished: nothing (an Auto-Store sweep) acts on this account
        # until what the run did is recorded.
        if on_finish:
            try:
                on_finish(plan, self.jobs.get(job_id))
            except Exception:
                logger.exception('[MOVE] on_finish failed')
        with self._lock:
            self._last_finished[steamid] = time.time()
        self.jobs.finish(job_id, result=result, error=error, done=moved, failed=failed)

    def _run_batch(self, plan, batch, job_id, moved_before, failed_before):
        """Send one unit's batch and wait for Ratatoskr's answers.

        Returns {'moved_ids', 'failed': [{item_id, code}]}. Only ids Ratatoskr confirmed for this
        run count as moved; `code` is 'refused' (the Game Coordinator never answered), 'not_sent',
        'busy' (another run never finished, nothing sent) or 'unconfirmed' (the run's queue was
        dropped — a disconnect or a restart — or stalled before answering).
        """
        steamid = plan['steamid']
        ids = list(batch['item_ids'])

        def outcome(own_status, default_code):
            done = {str(i) for i in own_status.get('doneItemIDs') or []}
            refused = {str(i) for i in own_status.get('refusedItemIDs') or []}
            lost = {str(i) for i in own_status.get('unconfirmedItemIDs') or []}
            not_sent = {str(i) for i in own_status.get('failedItemIDs') or []} - refused - lost
            failed = []
            for item_id in ids:
                if item_id in done:
                    continue
                code = 'refused' if item_id in refused else 'not_sent' if item_id in not_sent else default_code
                failed.append({'item_id': item_id, 'code': code})
            return {'moved_ids': [i for i in ids if i in done], 'failed': failed}

        if not self._wait_until_idle(steamid):
            logger.error(f"[MOVE] {steamid}: another run never finished; batch into {batch['casket_id']} not sent")
            return outcome({}, 'busy')
        answer = self.ratatoskr.move_batch(steamid, ids, plan['source'], plan['target'], batch['casket_id'])
        if answer.get('error'):
            logger.error(f"[MOVE] {steamid}: batch into {batch['casket_id']} not sent: {answer['error']}")
            return outcome({}, 'not_sent')

        run_id = answer.get('runId')
        own = answer                     # the newest status of this run
        last_progress, last_seen = time.time(), -1
        while own.get('running'):
            time.sleep(_STATUS_POLL_SECONDS)
            status = self.ratatoskr.get_move_status(steamid) or {}
            if not status.get('error'):
                if status.get('runId') != run_id:
                    logger.error(f"[MOVE] {steamid}: the move queue was dropped (disconnect or restart); "
                                 f"what was not confirmed counts as not moved")
                    break
                own = status
                self.jobs.update(job_id, done=moved_before + int(own.get('done') or 0),
                                 failed=failed_before + int(own.get('failed') or 0))
            processed = int(own.get('processed') or 0)
            if processed != last_seen:
                last_seen, last_progress = processed, time.time()
            elif time.time() - last_progress > _BATCH_STALL_SECONDS:
                logger.error(f"[MOVE] {steamid}: Ratatoskr shows no progress for {_BATCH_STALL_SECONDS} s; giving up the batch")
                break
        return outcome(own, 'unconfirmed')

    def _wait_until_idle(self, steamid, limit=_BATCH_STALL_SECONDS):
        """Another client's moves on this account finish first (one Ratatoskr queue per account).
        False when the queue is still busy after `limit` seconds."""
        deadline = time.time() + limit
        while time.time() < deadline:
            status = self.ratatoskr.get_move_status(steamid) or {}
            if not status.get('running'):
                return True
            time.sleep(_STATUS_POLL_SECONDS)
        return False
