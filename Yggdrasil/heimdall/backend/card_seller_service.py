"""Andvari card auto-sell: list dropped trading cards one cent under the lowest
Market listing (not the buy orders), on every account, and keep the statistics.

Off until switched on (settings ``card_auto_sell_enabled``). In turn, paced,
each account's Steam inventory (app 753, context 6) is read at most every
``INVENTORY_INTERVAL_SECONDS``; every marketable trading card (foil ones too,
unless ``card_auto_sell_foil`` is off), of any game or only the games on
``card_auto_sell_apps``, is listed one minor unit under the lowest listing in
the account's wallet currency — at that price when the lowest listing is our
own last one, so our accounts never undercut each other — and only those
listings' confirmations are accepted.

Cards the account already held when auto-sell was switched on are left alone
(each account's first read records where its inventory stood), unless
``card_auto_sell_include_held`` is on. Every listing is logged with the account,
game, card and price; ``status()`` sums them per account and per game.
"""
import json
import logging
import threading
import time

import requests

from jsonio import atomic_write_json, read_json
from market_seller import MarketSeller, RateLimited
from notifications import send_notification

log = logging.getLogger(__name__)

STATE_PATH = 'cache/card_sales.json'
INVENTORY_URL = 'https://steamcommunity.com/inventory/{steamid}/753/6'
STEAM_COMMUNITY_APP, CARD_CONTEXT = 753, 6
INVENTORY_INTERVAL_SECONDS = 30 * 60
LOOP_SECONDS = 20
RATE_LIMIT_COOLDOWN_SECONDS = 15 * 60
MAX_SELL_TRIES = 3
MAX_INVENTORY_PAGES = 5
SALES_CAP = 2000
ATTEMPT_MEMORY_SECONDS = 14 * 86400
TRADING_CARD_CLASS = 'item_class_2'
FOIL_BORDER = 'cardborder_1'


def inventory_cards(pages, apps=(), foil=True):
    """[{assetid, market_hash_name, name, app_id, foil}] of the marketable trading
    cards in Steam inventory answers (one per page), filtered to *apps* (empty =
    every game) and without foil cards unless *foil*."""
    wanted_apps = {str(app) for app in apps or []}
    cards = []
    for page in pages:
        descriptions = {(item.get('classid'), item.get('instanceid')): item
                        for item in (page or {}).get('descriptions') or []}
        for asset in (page or {}).get('assets') or []:
            item = descriptions.get((asset.get('classid'), asset.get('instanceid'))) or {}
            tags = {tag.get('category'): tag.get('internal_name') for tag in item.get('tags') or []}
            if tags.get('item_class') != TRADING_CARD_CLASS or int(item.get('marketable') or 0) != 1:
                continue
            is_foil = tags.get('cardborder') == FOIL_BORDER
            app_id = str(item.get('market_fee_app') or '')
            if (is_foil and not foil) or (wanted_apps and app_id not in wanted_apps):
                continue
            cards.append({'assetid': str(asset.get('assetid')), 'market_hash_name': item.get('market_hash_name'),
                          'name': item.get('name') or item.get('market_hash_name'), 'app_id': app_id,
                          'game': (tags.get('Game') or ''), 'foil': is_foil})
    return cards


def sales_statistics(sales):
    """Listings summed per account and per game (per wallet currency)."""
    accounts, games = {}, {}
    for sale in sales:
        if not sale.get('ok'):
            continue
        for table, key in ((accounts, sale.get('account_name') or sale.get('steamid')),
                           (games, sale.get('game') or sale.get('app_id'))):
            entry = table.setdefault(key, {'listed': 0, 'receives': {}})
            entry['listed'] += 1
            currency = str(sale.get('currency'))
            entry['receives'][currency] = entry['receives'].get(currency, 0) + int(sale.get('receives') or 0)
    return {'accounts': accounts, 'games': games,
            'listed': sum(1 for sale in sales if sale.get('ok')),
            'failed': sum(1 for sale in sales if not sale.get('ok'))}


class CardSellerService:
    def __init__(self, settings_manager, steam_service, asf_service=None, state_path=STATE_PATH,
                 http=None, sleep=time.sleep):
        self.settings_manager = settings_manager
        self.steam = steam_service
        self.asf = asf_service
        self.state_path = state_path
        self._http = http or requests.Session()
        self._sleep = sleep
        self._lock = threading.RLock()
        self._step_lock = threading.Lock()
        saved = read_json(state_path, default={}) or {}
        self.seller = MarketSeller(self._http, steam_service, sleep, saved.get('seller'))
        self._state = {
            'enabled_at': saved.get('enabled_at'),                # when auto-sell was last switched on
            'floors': dict(saved.get('floors') or {}),           # {steamid: {enabled_at, max_assetid}}
            'inventories': dict(saved.get('inventories') or {}),  # {steamid: {at, cards, listed, error}}
            'attempted': {key: value for key, value in (saved.get('attempted') or {}).items()
                          if isinstance(value, dict)},           # {assetid: {at, tries, listed}}
            'sales': list(saved.get('sales') or []),
            # {steamid: {names: [...], since}}: listed but not confirmed yet (retried every pass)
            'unconfirmed': dict(saved.get('unconfirmed') or {}),
            'cooldown_until': float(saved.get('cooldown_until') or 0),   # survives a reload
        }
        self._stop = threading.Event()

    # ---- state -------------------------------------------------------------------------

    def _save(self):
        with self._lock:
            snapshot = json.loads(json.dumps({**self._state, 'seller': self.seller.state()}))
        try:
            atomic_write_json(self.state_path, snapshot)
        except Exception as e:
            log.error('[ANDVARI-SELL] could not save %s: %s', self.state_path, e)

    def _settings(self):
        return self.settings_manager.get_settings()

    def _enabled_at(self, settings):
        """The moment auto-sell was switched on (recorded the first time it is seen
        on; cleared when it is off, so switching it on again starts afresh)."""
        with self._lock:
            if not settings.get('card_auto_sell_enabled'):
                if self._state['enabled_at'] is not None:
                    self._state['enabled_at'] = None
                    self._save()
                return None
            if self._state['enabled_at'] is None:
                self._state['enabled_at'] = time.time()
                # Read every account soon: a card that drops before an account's
                # first read would count as held.
                self._state['inventories'] = {}
                self._save()
            return self._state['enabled_at']

    def _wallet_currency(self, steamid, cookies):
        """From ASF while the bot is logged in, otherwise the account's Market page (once)."""
        if self.asf is not None and getattr(self.asf, 'enabled', False):
            currency = self.asf.wallet_currency(steamid)
            if currency:
                return int(currency)
        return self.seller.account_currency(steamid, cookies)

    def _accounts(self):
        rows = []
        for steamid in self.steam.storage.list_accounts():
            data = self.steam.storage.load_account(steamid) or {}
            rows.append((str(steamid), data.get('account_name') or str(steamid)))
        return rows

    # ---- one account ---------------------------------------------------------------------

    def _read_inventory(self, steamid, cookies):
        pages, start = [], None
        for _ in range(MAX_INVENTORY_PAGES):
            params = {'l': 'english', 'count': 2000}
            if start:
                params['start_assetid'] = start
            response = self.seller.get(INVENTORY_URL.format(steamid=steamid), params, cookies)
            if not response.ok:
                raise RuntimeError(f'inventory answered HTTP {response.status_code}')
            page = response.json() or {}
            pages.append(page)
            if not page.get('more_items') or not page.get('last_assetid'):
                break
            start = page['last_assetid']
        return pages

    def sell_account(self, steamid, account_name=None):
        settings = self._settings()
        enabled_at = self._enabled_at(settings)
        now = time.time()
        summary = {'steamid': steamid, 'at': now, 'cards': 0, 'listed': 0, 'confirmed': 0, 'error': None}
        if enabled_at is None:
            summary['error'] = 'card auto-sell is off'
            return summary
        try:
            cookies = self.seller.cookies(steamid)
            if not cookies:
                raise RuntimeError('no Steam web session for this account')
            pages = self._read_inventory(steamid, cookies)
            newest = max((int(asset.get('assetid') or 0) for page in pages for asset in page.get('assets') or []),
                         default=0)
            with self._lock:
                floor_entry = self._state['floors'].get(steamid) or {}
                if floor_entry.get('enabled_at') != enabled_at:
                    # First read since auto-sell was switched on: what is here was held before.
                    floor_entry = {'enabled_at': enabled_at, 'max_assetid': newest}
                    self._state['floors'][steamid] = floor_entry
                attempted = dict(self._state['attempted'])
            floor = -1 if settings.get('card_auto_sell_include_held') else int(floor_entry['max_assetid'])
            cards = [card for card in inventory_cards(pages, settings.get('card_auto_sell_apps') or [],
                                                      settings.get('card_auto_sell_foil', True))
                     if int(card['assetid']) > floor
                     and not (attempted.get(card['assetid']) or {}).get('listed')
                     and (attempted.get(card['assetid']) or {}).get('tries', 0) < MAX_SELL_TRIES]
            summary['cards'] = len(cards)
            currency = self._wallet_currency(steamid, cookies) if cards else None
            with self._lock:
                pending = dict(self._state['unconfirmed'].get(steamid) or {})
            listed_names = list(pending.get('names') or [])
            for card in cards:
                price = self.seller.price_for(STEAM_COMMUNITY_APP, card['market_hash_name'], currency, cookies)
                if price is None:
                    summary['error'] = f'no usable Market price for {card["market_hash_name"]} yet'
                    continue             # not a try: asked again next time
                buyer_pays, receives = price
                ok, error = self.seller.sell(steamid, cookies, STEAM_COMMUNITY_APP, CARD_CONTEXT, card['assetid'],
                                             card['market_hash_name'], currency, receives, buyer_pays)
                with self._lock:
                    tries = (self._state['attempted'].get(card['assetid']) or {}).get('tries', 0) + 1
                    self._state['attempted'][card['assetid']] = {'at': now, 'tries': tries, 'listed': ok}
                    self._state['sales'].append({
                        'at': time.time(), 'steamid': steamid, 'account_name': account_name,
                        'assetid': card['assetid'], 'app_id': card['app_id'], 'game': card['game'],
                        'card': card['market_hash_name'], 'foil': card['foil'], 'currency': currency,
                        'buyer_pays': buyer_pays, 'receives': receives, 'ok': ok, 'error': error})
                    self._state['sales'] = self._state['sales'][-SALES_CAP:]
                if ok:
                    summary['listed'] += 1
                    listed_names.append(card['name'])
                    with self._lock:           # remembered at once: a stop mid-run still confirms later
                        entry = self._state['unconfirmed'].setdefault(steamid, {'names': [], 'since': now})
                        entry['names'] = sorted(set(entry['names']) | {card['name']})
        except RateLimited as e:
            summary['error'] = str(e)
            with self._lock:
                self._state['cooldown_until'] = time.time() + RATE_LIMIT_COOLDOWN_SECONDS
            log.warning('[ANDVARI-SELL] %s: pausing for %d minutes', e, RATE_LIMIT_COOLDOWN_SECONDS // 60)
        except Exception as e:
            summary['error'] = str(e)
            log.warning('[ANDVARI-SELL] %s: %s', account_name or steamid, e)
        finally:
            self._confirm_pending(steamid, summary)
        with self._lock:
            self._state['inventories'][steamid] = {'at': now, 'cards': summary['cards'], 'listed': summary['listed'],
                                                   'confirmed': summary['confirmed'], 'error': summary['error']}
            cutoff = now - ATTEMPT_MEMORY_SECONDS
            self._state['attempted'] = {key: value for key, value in self._state['attempted'].items()
                                        if value.get('at', 0) >= cutoff}
        self._save()
        if summary['listed']:
            log.info('[ANDVARI-SELL] listed %d cards on %s (%d confirmed)', summary['listed'],
                     account_name or steamid, summary['confirmed'])
            self._notify(f"🃏 Andvari: listed {summary['listed']} cards on {account_name or steamid} "
                         f"({summary['confirmed']} confirmed)")
        return summary

    def _confirm_pending(self, steamid, summary):
        """Confirm the account's listed-but-unconfirmed cards (also those an earlier
        pass listed before it stopped). Kept for a retry until confirmed or a day old."""
        with self._lock:
            pending = dict(self._state['unconfirmed'].get(steamid) or {})
        if not pending.get('names'):
            return
        try:
            summary['confirmed'] += self.seller.confirm(steamid, pending['names'])
            done = True
        except Exception as e:
            summary['error'] = summary['error'] or f'confirming: {e}'
            done = time.time() - pending.get('since', 0) > 86400
        if done:
            # Confirmed, or confirmations no longer pending (Steam's own auto-confirm,
            # or by hand): nothing to keep. A day-old failure is given up.
            with self._lock:
                self._state['unconfirmed'].pop(steamid, None)

    def _notify(self, text):
        settings = self._settings()
        chat = str(settings.get('card_deals_chat_id') or '').strip()
        try:
            send_notification({**settings, 'telegram_chat_id': chat} if chat else settings, text)
        except Exception as e:
            log.info('[ANDVARI-SELL] Telegram message failed: %s', e)

    # ---- loop --------------------------------------------------------------------------------

    def step(self):
        """Read the most overdue account inventory (one per call)."""
        if not self._settings().get('card_auto_sell_enabled') or time.time() < self._state['cooldown_until']:
            return None
        if not self._step_lock.acquire(blocking=False):
            return None
        try:
            now = time.time()
            with self._lock:
                seen = {steamid: (entry or {}).get('at', 0) for steamid, entry in self._state['inventories'].items()}
            due = [(seen.get(steamid, 0), steamid, name) for steamid, name in self._accounts()
                   if now - seen.get(steamid, 0) >= INVENTORY_INTERVAL_SECONDS]
            if not due:
                return None
            _, steamid, name = min(due)
            return self.sell_account(steamid, name)
        finally:
            self._step_lock.release()

    def start(self):
        def loop():
            self._stop.wait(60)
            while not self._stop.is_set():
                try:
                    self._enabled_at(self._settings())
                    self.step()
                except Exception as e:
                    log.warning('[ANDVARI-SELL] loop error: %s', e)
                self._stop.wait(LOOP_SECONDS)
        threading.Thread(target=loop, daemon=True, name='andvari-card-sell').start()

    def stop(self):
        self._stop.set()

    def status(self):
        settings = self._settings()
        with self._lock:
            state = json.loads(json.dumps(self._state))
        return {
            'enabled': bool(settings.get('card_auto_sell_enabled')),
            'enabled_at': state['enabled_at'],
            'include_held': bool(settings.get('card_auto_sell_include_held')),
            'foil': bool(settings.get('card_auto_sell_foil', True)),
            'apps': list(settings.get('card_auto_sell_apps') or []),
            'rate_limited_until': state['cooldown_until'] if state['cooldown_until'] > time.time() else None,
            'unconfirmed': {steamid: len(entry.get('names') or []) for steamid, entry in state['unconfirmed'].items()},
            'inventories': state['inventories'],
            'statistics': sales_statistics(state['sales']),
            'sales': state['sales'][-100:][::-1],
        }
