"""The shared plan store (`plans.py`): one id, one lifetime, one cleanup."""
from plans import PlanStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_add_gives_an_id_and_a_lifetime():
    clock = Clock()
    store = PlanStore(600, clock=clock)
    plan = store.add({'steamid': '1'})
    assert len(plan['id']) == 32 and plan['created_at'] == 1000.0 and plan['expires_at'] == 1600.0
    assert store.get(plan['id']) is plan and plan['id'] in store and not store.is_expired(plan)


def test_an_expired_plan_is_still_read_but_forgotten_at_the_next_add():
    clock = Clock()
    store = PlanStore(600, clock=clock)
    old = store.add({})
    clock.now += 601
    assert store.is_expired(old) and store.get(old['id']) is old
    store.add({})
    assert store.get(old['id']) is None and len(store) == 1


def test_forget_decides_the_cleanup():
    clock = Clock()
    store = PlanStore(600, forget=lambda plan, now: False, clock=clock)
    old = store.add({})
    clock.now += 10_000
    store.add({})
    assert old['id'] in store


def test_unknown_plan_is_none():
    assert PlanStore(600).get('nope') is None


def test_real_purchase_totals_the_lines_and_says_when_a_price_is_unknown():
    from plans import real_purchase
    lines = [{'account_name': 'alpha', 'buys': '1 Storage Unit', 'usd': 1.99},
             {'account_name': 'bravo', 'buys': '2 Storage Units', 'usd': 3.98}]
    assert real_purchase(lines) == {'lines': lines, 'usd_total': 5.97}
    assert real_purchase(lines + [{'account_name': 'charlie', 'buys': 'x', 'usd': None}])['usd_total'] is None
    assert real_purchase([]) == {'lines': [], 'usd_total': 0}
