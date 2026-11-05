"""JobSlot: one job at a time for one service, its own view plus the registry job."""
import pytest

from jobs import JobRegistry, JobSlot

IDLE = {'running': False, 'kind': None, 'done': 0, 'total': 0, 'phase': None, 'error': None,
        'started_at': None, 'finished_at': None}


def test_a_second_start_while_one_runs_answers_none():
    slot = JobSlot(JobRegistry(), IDLE)
    job = slot.start('plan', 'service-plan')
    assert slot.start('purchase', 'service-purchase') is None
    view = slot.view()
    assert view['running'] and view['kind'] == 'plan' and view['job_id'] == job['id']
    slot.mark_finished()
    slot.release(result={'ok': True})
    assert slot.start('purchase', 'service-purchase') is not None


def test_set_copies_only_the_mirrored_fields_to_the_registry():
    jobs = JobRegistry()
    slot = JobSlot(jobs, IDLE, mirror=('done',))
    job = slot.start('plan', 'service-plan')
    slot.set(done=3, current='account one')
    assert slot.view()['current'] == 'account one'
    assert jobs.get(job['id'])['done'] == 3 and 'current' not in jobs.get(job['id'])


def test_release_finishes_the_registry_job_and_keeps_the_last_view():
    jobs = JobRegistry()
    slot = JobSlot(jobs, IDLE, id_key='id')
    job = slot.start('price sheet', 'shop-price-sheet', dry_run=True)
    slot.mark_finished(phase=None)
    slot.release(error='boom')
    assert jobs.get(job['id'])['running'] is False and jobs.get(job['id'])['error'] == 'boom'
    view = slot.view()
    assert view['running'] is False and view['id'] == job['id'] and view['dry_run'] is True


def test_a_job_that_cannot_be_made_leaves_the_slot_free():
    jobs = JobRegistry()
    slot = JobSlot(jobs, IDLE)

    def broken(*args, **kwargs):
        raise RuntimeError('no')
    jobs.create = broken
    with pytest.raises(RuntimeError):
        slot.start('plan', 'service-plan')
    del jobs.create
    assert slot.start('plan', 'service-plan') is not None
