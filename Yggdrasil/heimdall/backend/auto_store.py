"""Auto-Store: each account has its own watchlist; the master view combines them.

The one place that reads and changes Auto-Store's settings. Every change is one command for one
item or one account (never a whole list sent by the page), under one lock, so two tabs or a quick
second click can never drop an item. The scheduler's sweep and the routes read the watchlists here.

Settings:
- auto_store_enabled: the master switch (pauses Auto-Store on every account at once);
- auto_store_accounts: the accounts switched on, one by one (Ivan: never all at once);
- auto_store_watchlists: {steamid: [item names]}, one watchlist per account.
"""
import threading

from commands import CommandError
from storage_deposit import storage_block

# Items a Storage Unit can never hold; trade protection ends, so those items are still suggested.
_NEVER_STORABLE = {'storage_unit', 'unstorable'}

_lock = threading.Lock()


def _clean_name(name):
    return str(name or '').strip()


def watchlists(settings):
    """{steamid: [names]} as saved; a missing or broken value reads as no watchlists."""
    saved = settings.get('auto_store_watchlists')
    if not isinstance(saved, dict):
        return {}
    return {str(steamid): [n for n in (names or []) if _clean_name(n)]
            for steamid, names in saved.items() if isinstance(names, list)}


def switched_on(settings):
    return [str(s) for s in (settings.get('auto_store_accounts') or [])]


def watch_for(settings, steamid):
    """The lower-case names Auto-Store stores on this account."""
    return {_clean_name(n).lower() for n in watchlists(settings).get(str(steamid), [])}


def sweep_accounts(settings):
    """The accounts a sweep acts on: master switch on, the account switched on, a watchlist."""
    if not settings.get('auto_store_enabled'):
        return []
    lists = watchlists(settings)
    return [s for s in switched_on(settings) if lists.get(s)]


def sweep_refusal(settings, steamid):
    """Why "Sweep now" cannot run on this account ({code, reason}), or None when it can."""
    steamid = str(steamid or '')
    if not settings.get('auto_store_enabled'):
        return {'code': 'auto_store_off', 'reason': 'Auto-Store is switched off (master switch)'}
    if steamid not in switched_on(settings):
        return {'code': 'account_off', 'reason': 'Auto-Store is switched off for this account'}
    if not watchlists(settings).get(steamid):
        return {'code': 'no_items', 'reason': "This account's watchlist is empty"}
    return None


def protected_accounts(settings):
    """Accounts Ratatoskr keeps online (no idle disconnect) while Auto-Store watches them."""
    return switched_on(settings) if settings.get('auto_store_enabled') else []


class AutoStore:
    def __init__(self, settings_manager, steam_service, deposit_service, on_change=None):
        self.settings_manager = settings_manager
        self.steam_service = steam_service
        self.deposit_service = deposit_service
        # Called with the new settings after every change (keeps Ratatoskr's idle exemption in sync).
        self.on_change = on_change

    # ---- one-time move from the shared watchlist ---------------------------

    def migrate_shared_watchlist(self):
        """The old shared list (auto_store_items) becomes a copy on each switched-on account.

        Ivan, 2026-11-04: copy it so nothing stops working, then remove per account what it
        does not need. Runs at boot; does nothing once the shared list is empty."""
        with _lock:
            settings = self.settings_manager.get_settings()
            shared = [n for n in (settings.get('auto_store_items') or []) if _clean_name(n)]
            accounts = switched_on(settings)
            # No account to copy to: keep the shared list (emptying it now would lose it).
            if not shared or not accounts:
                return False
            lists = watchlists(settings)
            for steamid in accounts:
                names = lists.setdefault(steamid, [])
                for name in shared:
                    if name.lower() not in {n.lower() for n in names}:
                        names.append(name)
            return self._save({'auto_store_watchlists': lists, 'auto_store_items': []})

    # ---- reading ------------------------------------------------------------

    def _account_names(self):
        names = {}
        for steamid in self.steam_service.storage.list_accounts():
            data = self.steam_service.get_account(steamid) or {}
            names[str(steamid)] = data.get('account_name') or str(steamid)
        return names

    def status(self):
        """Everything the page shows: the switches, every account's watchlist, the master view."""
        settings = self.settings_manager.get_settings()
        history = list(settings.get('auto_store_history') or [])
        history.reverse()
        moved, moved_by_account = {}, {}
        for record in history:
            steamid = str(record.get('steamid'))
            moved_by_account[steamid] = moved_by_account.get(steamid, 0) + int(record.get('count', 0))
            for name, quantity in (record.get('items') or {}).items():
                key = (steamid, name.lower())
                moved[key] = moved.get(key, 0) + int(quantity or 0)

        names = self._account_names()
        lists = watchlists(settings)
        on = set(switched_on(settings))
        accounts = []
        for steamid in sorted(set(names) | set(lists) | on, key=lambda s: names.get(s, s).lower()):
            items = lists.get(steamid, [])
            accounts.append({
                'steamid': steamid,
                'account_name': names.get(steamid, steamid),
                'on': steamid in on,
                'moved': moved_by_account.get(steamid, 0),
                'sweep_refused': sweep_refusal(settings, steamid),
                'items': [{'name': n, 'moved': moved.get((steamid, n.lower()), 0)} for n in items],
            })

        master = {}
        for account in accounts:
            for item in account['items']:
                entry = master.setdefault(item['name'].lower(), {'name': item['name'], 'moved': 0, 'accounts': []})
                entry['moved'] += item['moved']
                entry['accounts'].append({'steamid': account['steamid'], 'account_name': account['account_name'],
                                          'on': account['on'], 'moved': item['moved']})
        return {
            'enabled': bool(settings.get('auto_store_enabled')),
            'accounts': accounts,
            'master': sorted(master.values(), key=lambda e: e['name'].lower()),
            'history': history,
            'moved_total': sum(int(r.get('count', 0)) for r in history),
        }

    def suggestions(self, steamid):
        """Names in this account's inventory that a Storage Unit can hold and it does not watch yet."""
        watched = watch_for(self.settings_manager.get_settings(), steamid)
        found = {}
        for item in self.deposit_service.inventory(steamid):
            name = _clean_name(item.get('item_name'))
            block = storage_block(item)
            if not name or name.lower() in watched or (block and block['code'] in _NEVER_STORABLE):
                continue
            found.setdefault(name.lower(), name)
        return {'steamid': str(steamid), 'suggestions': sorted(found.values(), key=str.lower)}

    # ---- commands -------------------------------------------------------------

    def _known(self, steamid, has_watchlist_ok=False):
        steamid = str(steamid or '').strip()
        if not steamid:
            raise CommandError('steamid_required', 'Which account? (steamid)')
        if has_watchlist_ok and steamid in watchlists(self.settings_manager.get_settings()):
            return steamid  # an account removed from Heimdall: its leftover watchlist can still be emptied
        if not self.steam_service.get_account(steamid):
            raise CommandError('account_unknown', f'No account {steamid}', 404)
        return steamid

    def _save(self, patch):
        if not self.settings_manager.save_settings(patch):
            raise CommandError('save_failed', 'Could not save the settings', 500)
        if self.on_change:
            self.on_change(self.settings_manager.get_settings())
        return True

    def set_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise CommandError('enabled_required', 'enabled must be true or false')
        with _lock:
            self._save({'auto_store_enabled': enabled})
        return self.status()

    def set_account(self, steamid, on):
        """Switch Auto-Store on or off for one account."""
        steamid = self._known(steamid)
        if not isinstance(on, bool):
            raise CommandError('on_required', 'on must be true or false')
        with _lock:
            accounts = [s for s in switched_on(self.settings_manager.get_settings()) if s != steamid]
            if on:
                accounts.append(steamid)
            self._save({'auto_store_accounts': accounts})
        return self.status()

    def add_item(self, steamid, name):
        """Add one item to one account's watchlist; a name already there (any letter case) is kept."""
        steamid = self._known(steamid)
        name = _clean_name(name)
        if not name:
            raise CommandError('name_required', 'Which item? (name)')
        with _lock:
            lists = watchlists(self.settings_manager.get_settings())
            names = lists.setdefault(steamid, [])
            if name.lower() not in {n.lower() for n in names}:
                names.append(name)
                self._save({'auto_store_watchlists': lists})
        return self.status()

    def remove_item(self, steamid, name):
        """Remove one item from one account's watchlist (any letter case)."""
        steamid = self._known(steamid, has_watchlist_ok=True)
        name = _clean_name(name)
        with _lock:
            lists = watchlists(self.settings_manager.get_settings())
            names = lists.get(steamid, [])
            kept = [n for n in names if n.lower() != name.lower()]
            if len(kept) == len(names):
                raise CommandError('not_found', f'{name or "That item"} is not on this account\'s watchlist', 404)
            lists[steamid] = kept
            self._save({'auto_store_watchlists': lists})
        return self.status()
