"""Rename Storage Unit: the backend commands behind Ratatoskr's "Rename Storage Unit" tab.

The page only shows what these return and sends instructions back:

- `units(steamid)`: every Storage Unit of the account, with its name, how many items
  it holds and whether it has a name at all (a unit without a name refuses items).
- `rename(steamid, item_id, name)`: one unit, one new name. Free in the game.
- `plan_name_unnamed(steamid)` → `execute(plan_id)`: give every unit without a name
  a "Storage N" name (the same numbering Buy Storage Units uses), one after another
  (rule 21). A plan runs once and expires after `PLAN_TTL_SECONDS`.

Rules (all checked here, never in the page):
- a name is 1 to 20 characters after trimming (the game's limit);
- two units of one account never share a name (ignoring upper and lower case);
- nothing is renamed while a move runs on that account: the Game Coordinator gives a
  renamed unit a new item id, which would break the move in the middle.

Errors are `CommandError` (`{error, code}`).
"""
import re
import secrets
import threading
import time

from storage_deposit import CommandError, free_places, unit_count
from storage_shop_service import next_storage_names

NAME_MAX_LENGTH = 20
PLAN_TTL_SECONDS = 10 * 60


def unit_name(unit):
    return (unit.get('item_customname') or '').strip()


def describe(unit):
    """One Storage Unit as the page shows it."""
    name = unit_name(unit)
    return {
        'item_id': str(unit.get('item_id')),
        'name': name,
        'named': bool(name),
        'items': unit_count(unit),
        'free_places': free_places(unit),
    }


def check_name(name, units, item_id):
    """The trimmed name, or CommandError when the game or our rules would refuse it."""
    name = str(name or '').strip()
    if not name:
        raise CommandError('name_required', 'a name is required: a Storage Unit without one refuses items')
    if len(name) > NAME_MAX_LENGTH:
        raise CommandError('name_too_long', f'a name is at most {NAME_MAX_LENGTH} characters')
    for unit in units:
        if str(unit.get('item_id')) != str(item_id) and unit_name(unit).lower() == name.lower():
            raise CommandError('name_taken', f'another Storage Unit is already named "{unit_name(unit)}"')
    return name


class RenameService:
    def __init__(self, ratatoskr, moves_in_progress=lambda steamid: False, clock=time.time):
        self.ratatoskr = ratatoskr
        self.moves_in_progress = moves_in_progress
        self.clock = clock
        self._lock = threading.Lock()
        self._plans = {}

    # ---- reading ----------------------------------------------------------

    def caskets(self, steamid):
        answer = self.ratatoskr.get_caskets(steamid) or {}
        if answer.get('error'):
            raise CommandError('not_connected', answer['error'], 409)
        return answer.get('caskets') or []

    def units(self, steamid):
        units = [describe(unit) for unit in self.caskets(steamid)]
        # Unnamed first (they need a name to take items), then by name, numbers in order.
        units.sort(key=lambda u: (u['named'], _natural(u['name'])))
        return {'units': units, 'unnamed': sum(1 for u in units if not u['named']),
                'name_max_length': NAME_MAX_LENGTH}

    # ---- acting -----------------------------------------------------------

    def rename(self, steamid, item_id, name):
        self._refuse_while_moving(steamid)
        caskets = self.caskets(steamid)
        if not any(str(unit.get('item_id')) == str(item_id) for unit in caskets):
            raise CommandError('unknown_unit', 'no such Storage Unit on this account', 404)
        name = check_name(name, caskets, item_id)
        answer = self.ratatoskr.rename_casket(steamid, item_id, name) or {}
        if answer.get('error'):
            raise CommandError('rename_failed', answer['error'], 502)
        return {'item_id': str(answer.get('item_id') or item_id),
                'previous_item_id': str(answer.get('previous_item_id') or item_id),
                'name': answer.get('custom_name') or name}

    def plan_name_unnamed(self, steamid):
        caskets = self.caskets(steamid)
        unnamed = [unit for unit in caskets if not unit_name(unit)]
        if not unnamed:
            raise CommandError('nothing_to_name', 'every Storage Unit already has a name')
        names = next_storage_names([unit_name(unit) for unit in caskets], len(unnamed))
        now = self.clock()
        plan = {'id': secrets.token_hex(8), 'steamid': str(steamid), 'created_at': now,
                'expires_at': now + PLAN_TTL_SECONDS, 'executed': False,
                'renames': [{'item_id': str(unit.get('item_id')), 'name': name}
                            for unit, name in zip(unnamed, names)]}
        with self._lock:
            for plan_id in [p for p, old in self._plans.items() if old['expires_at'] < now]:
                del self._plans[plan_id]
            self._plans[plan['id']] = plan
        return {key: plan[key] for key in ('id', 'steamid', 'renames', 'expires_at')}

    def execute(self, plan_id):
        with self._lock:
            plan = self._plans.get(plan_id)
            if not plan or plan['expires_at'] < self.clock():
                raise CommandError('unknown_plan', 'no such plan, or it expired: make a new one', 404)
            if plan['executed']:
                raise CommandError('already_executed', 'this plan already ran', 409)
            # Checked before the plan is used up: a refusal leaves it ready to run later.
            self._refuse_while_moving(plan['steamid'])
            plan['executed'] = True
        steamid = plan['steamid']
        results = []
        for step in plan['renames']:        # one after another (rule 21)
            try:
                done = self.rename(steamid, step['item_id'], step['name'])
                results.append({**step, 'ok': True, 'new_item_id': done['item_id']})
            except CommandError as error:
                results.append({**step, 'ok': False, 'error': error.message, 'code': error.code})
        return {'plan_id': plan_id, 'named': sum(1 for r in results if r['ok']),
                'failed': sum(1 for r in results if not r['ok']), 'results': results}

    def _refuse_while_moving(self, steamid):
        if self.moves_in_progress(steamid):
            raise CommandError('busy', 'items are moving on this account: rename when the move has finished', 409)


def _natural(name):
    """"Storage 2" before "Storage 10"."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', name)]
