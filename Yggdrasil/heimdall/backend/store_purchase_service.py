"""Andvari "Buy games": buy a card-deal game on many accounts at once.

Two steps, both background jobs, one at a time:

1. **Plan** (``start_plan``) — for the chosen games (store links or app ids) and
   every account (or the chosen ones): the account's store country (Andvari's
   account refresh), its Steam wallet currency and balance (``g_rgWalletInfo``
   on the Community Market page), the games it already owns (the store's
   ``dynamicstore/userdata``, the authoritative list) and each game's price in
   that country (the public ``appdetails`` API: the cheapest package of the
   game's default purchase group, so never a "Commercial License" or a bundle),
   converted to US dollars with Andvari's exchange rates. A game is planned for
   an account only when it is not owned, it is priced in the wallet's currency,
   it costs at most the maximum US dollar price per game, and the balance still
   covers it — the game with the best Andvari profit for that account first, so
   a small balance goes to the better game.
2. **Buy** (``start_purchase``) — for accounts picked from the plan, one at a
   time: the account's cart must be empty (Heimdall never buys or removes what
   you put there yourself); the packages go into the cart and the cart must hold
   exactly them at the planned total; a wallet checkout starts and Steam's final
   price must equal the plan to the cent, otherwise the transaction is
   cancelled. After paying, ownership is re-read and the account's ASF bot is
   told to farm now. ``dry_run`` goes through everything but pays nothing (the
   transaction is cancelled), to test the path on a real account.

Every purchase is kept in the history with the account, games, prices and
outcome; ``status()`` sums what was bought per account and per game.

No card number or password is involved: the payment method is the Steam wallet
and the account's web session (kept fresh by the scheduler) authenticates.
Steam calls are serial and spaced.
"""
import json
import logging
import re
import threading
import time

import requests

from jsonio import atomic_write_json, read_json

log = logging.getLogger(__name__)

STATE_PATH = 'cache/store_purchases.json'
USER_AGENT = 'Mozilla/5.0 (Heimdall Andvari buyer)'
HTTP_TIMEOUT_SECONDS = 30
APPDETAILS_URL = 'https://store.steampowered.com/api/appdetails'
USERDATA_URL = 'https://store.steampowered.com/dynamicstore/userdata/'
MARKET_URL = 'https://steamcommunity.com/market/'
CART_API = 'https://api.steampowered.com/IAccountCartService/'
# Steam moved checkout to its own domain (the store's /checkout/ redirects there);
# its page sets the checkout cookies (beginCheckoutCart, browserid) a browser sends.
CHECKOUT_URL = 'https://checkout.steampowered.com/checkout/'

STORE_GAP_SECONDS = 2.0            # between store / Web API calls
COMMUNITY_GAP_SECONDS = 4.0        # between steamcommunity.com calls
ACCOUNT_GAP_SECONDS = 5.0          # between two accounts' purchases
PRICE_TIME_TO_LIVE_SECONDS = 30 * 60
PLAN_TIME_TO_LIVE_SECONDS = 30 * 60   # buying needs a plan at most this old
TRANSACTION_POLL_SECONDS = 2.0
TRANSACTION_POLL_TRIES = 20
TRANSACTION_PENDING = 22
HISTORY_CAP = 500
MAX_GAMES_PER_PLAN = 10
DEFAULT_MAX_USD_PER_GAME = 0.45

# Steam ECurrencyCode -> ISO code (the currencies the store prices in).
CURRENCY_CODES = {
    1: 'USD', 2: 'GBP', 3: 'EUR', 4: 'CHF', 5: 'RUB', 6: 'PLN', 7: 'BRL', 8: 'JPY', 9: 'NOK', 10: 'IDR',
    11: 'MYR', 12: 'PHP', 13: 'SGD', 14: 'THB', 15: 'VND', 16: 'KRW', 17: 'TRY', 18: 'UAH', 19: 'MXN',
    20: 'CAD', 21: 'AUD', 22: 'NZD', 23: 'CNY', 24: 'INR', 25: 'CLP', 26: 'PEN', 27: 'COP', 28: 'ZAR',
    29: 'HKD', 30: 'TWD', 31: 'SAR', 32: 'AED', 34: 'ARS', 35: 'ILS', 37: 'KZT', 38: 'KWD', 39: 'QAR',
    40: 'CRC', 41: 'UYU',
}

# Plan statuses (the UI explains each).
STATUS_BUY, STATUS_OWNED, STATUS_TOO_EXPENSIVE = 'buy', 'owned', 'too_expensive'
STATUS_BALANCE, STATUS_CURRENCY, STATUS_UNAVAILABLE = 'balance', 'currency', 'unavailable'


class PurchaseError(RuntimeError):
    pass


# ---- pure helpers (unit-tested) ------------------------------------------------------------

def parse_app_ids(text):
    """App ids from store links or bare numbers, in order, once each."""
    found = []
    for match in re.finditer(r'(?:/app/)?(\d{2,9})', str(text or '')):
        app_id = int(match.group(1))
        if app_id not in found:
            found.append(app_id)
    return found


def choose_package(details):
    """(packageid, price in minor units, currency code) for a game from its
    appdetails data: the cheapest package of the default purchase group (never a
    "Commercial License" priced above it, nor a bundle). None when it cannot be bought."""
    if not details or details.get('is_free'):
        return None
    groups = details.get('package_groups') or []
    group = next((g for g in groups if g.get('name') == 'default'), groups[0] if groups else None)
    subs = [sub for sub in (group or {}).get('subs') or []
            if not sub.get('is_free_license') and sub.get('packageid')
            and sub.get('price_in_cents_with_discount') is not None]
    currency = (details.get('price_overview') or {}).get('currency')
    if subs:
        sub = min(subs, key=lambda item: int(item['price_in_cents_with_discount']))
        price = int(sub['price_in_cents_with_discount'])
        return (int(sub['packageid']), price, currency) if price > 0 else None
    overview = details.get('price_overview')
    packages = details.get('packages') or []
    if overview and packages and int(overview.get('final') or 0) > 0:
        return int(packages[0]), int(overview['final']), overview.get('currency')
    return None


def to_usd(minor_units, currency, rates):
    """Minor units of *currency* in US dollars with *rates* (units per US dollar), or None."""
    if minor_units is None or not currency:
        return None
    if currency == 'USD':
        return round(minor_units / 100, 4)
    rate = (rates or {}).get(currency)
    return round(minor_units / 100 / rate, 4) if rate else None


def plan_account(account, games, max_usd, rates):
    """The plan for one account. *account*: {balance, currency_code, owned (set of
    app ids), profits {app_id: dollars}}; *games*: [{app_id, name, packageid, price,
    currency}] priced for the account's country. Returns (rows, planned total):
    best profit first, so a small balance goes to the better game."""
    balance = int(account.get('balance') or 0)
    profits = account.get('profits') or {}
    ordered = sorted(games, key=lambda game: (-profits.get(game['app_id'], -1e9), game.get('price') or 0))
    rows, total = [], 0
    for game in ordered:
        usd = to_usd(game.get('price'), game.get('currency'), rates)
        row = {**game, 'usd': usd, 'profit': profits.get(game['app_id'])}
        if game['app_id'] in (account.get('owned') or set()):
            row['status'] = STATUS_OWNED
        elif game.get('packageid') is None or game.get('price') is None:
            row['status'] = STATUS_UNAVAILABLE
        elif game.get('currency') != account.get('currency_code'):
            row['status'] = STATUS_CURRENCY
        elif usd is None or usd > max_usd + 1e-9:
            row['status'] = STATUS_TOO_EXPENSIVE
        elif total + game['price'] > balance:
            row['status'] = STATUS_BALANCE
        else:
            row['status'] = STATUS_BUY
            total += game['price']
        rows.append(row)
    return rows, total


def checkout_form(country, session_id):
    """The wallet-checkout form Steam's store sends (no card, no address)."""
    blank = ['CardNumber', 'CardExpirationYear', 'CardExpirationMonth', 'FirstName', 'LastName', 'Address',
             'AddressTwo', 'City', 'State', 'PostalCode', 'Phone', 'ShippingFirstName', 'ShippingLastName',
             'ShippingAddress', 'ShippingAddressTwo', 'ShippingCity', 'ShippingState', 'ShippingPostalCode',
             'ShippingPhone', 'GifteeEmail', 'GifteeName', 'GiftMessage', 'Sentiment', 'Signature',
             'BankAccount', 'BankCode', 'BankIBAN', 'BankBIC', 'TPBankID', 'BankAccountID', 'gidPaymentID']
    form = {key: '' for key in blank}
    form.update({'gidShoppingCart': -1, 'gidReplayOfTransID': -1, 'bUseAccountCart': 1,
                 'PaymentMethod': 'steamaccount', 'abortPendingTransactions': 0, 'bHasCardInfo': 0,
                 'Country': country, 'ShippingCountry': country, 'bIsGift': 0, 'GifteeAccountID': 0,
                 'ScheduledSendOnDate': 0, 'bSaveBillingAddress': 1, 'bUseRemainingSteamAccount': 0,
                 'bPreAuthOnly': 0, 'sessionid': session_id})
    return form


def cart_contents(cart_answer):
    """(subtotal in minor units, sorted [packageid, ...]) from IAccountCartService/GetCart."""
    cart = ((cart_answer or {}).get('response') or {}).get('cart') or {}
    subtotal = int(((cart.get('subtotal') or {}).get('amount_in_cents')) or 0)
    packages = sorted(int(item['packageid']) for item in cart.get('line_items') or [] if item.get('packageid'))
    return subtotal, packages


def purchase_statistics(history):
    """What was really bought (dry runs and failures left out), per account and per game."""
    accounts, games = {}, {}
    # Older entries (before "paid" existed) counted when ok.
    bought = [entry for entry in history if not entry.get('dry_run')
              and (entry.get('paid') or (entry.get('ok') and 'paid' not in entry))]
    for entry in bought:
        currency = str(entry.get('currency'))
        account = accounts.setdefault(entry.get('account_name') or entry.get('steamid'),
                                      {'games': [], 'spent': {}})
        account['spent'][currency] = account['spent'].get(currency, 0) + int(entry.get('charged') or 0)
        for game in entry.get('games') or []:
            account['games'].append(game['name'])
            row = games.setdefault(game['name'], {'copies': 0, 'accounts': [], 'spent': {}})
            row['copies'] += 1
            row['accounts'].append(entry.get('account_name'))
            row['spent'][currency] = row['spent'].get(currency, 0) + int(game.get('price') or 0)
    return {'accounts': accounts, 'games': games, 'purchases': len(bought),
            'copies': sum(len(entry.get('games') or []) for entry in bought)}


class StorePurchaseService:
    def __init__(self, steam_service, card_deals_service, asf_service=None, settings_provider=None,
                 state_path=STATE_PATH, http=None, sleep=time.sleep):
        self.steam = steam_service
        self.card_deals = card_deals_service
        self.asf = asf_service
        self.settings_provider = settings_provider
        self.state_path = state_path
        self._http = http or requests.Session()
        self._sleep = sleep
        self._lock = threading.RLock()
        self._job_lock = threading.Lock()
        self._last_call = {'store': 0.0, 'community': 0.0}
        self._prices = {}                # {(app_id, country): (game, fetched_at)}
        saved = read_json(state_path, default={}) or {}
        self._plan = saved.get('plan')
        self._history = list(saved.get('history') or [])
        for entry in self._history:
            # Saved before "paid" existed: a real purchase that reached Steam's final
            # price and was then reported finished (ok, or the "store does not list
            # every game yet" note written only after that) was paid.
            if 'paid' not in entry and not entry.get('dry_run') and entry.get('charged') is not None and (
                    entry.get('ok') or str(entry.get('error') or '').startswith('paid, but the store')):
                entry.update(paid=True, payment_attempted=True, ok=True,
                             error=None if entry.get('ok') else 'bought; the store did not list every game yet')
        for entry in self._history:
            if entry.get('paid') and self._plan and entry.get('at', 0) >= self._plan.get('created_at', 0):
                for row in self._plan.get('accounts') or []:
                    if row['steamid'] == entry['steamid']:
                        for game in row['games']:
                            if game['app_id'] in {g['app_id'] for g in entry.get('games') or []}:
                                game['status'] = 'bought'
        for entry in self._history:      # a reload killed the job mid-purchase
            if entry.get('state') == 'in progress':
                entry['state'] = 'done'
                entry['error'] = ('interrupted by a backend restart'
                                  + (' after paying started — it MAY BE PAID: check the account'
                                     if entry.get('payment_attempted') else ' before paying'))
        self._job = {'running': False, 'kind': None, 'done': 0, 'total': 0, 'phase': None,
                     'error': None, 'started_at': None, 'finished_at': None, 'dry_run': False}

    # ---- plumbing ------------------------------------------------------------------------------

    def _save(self):
        with self._lock:
            snapshot = json.loads(json.dumps({'plan': self._plan, 'history': self._history[-HISTORY_CAP:]}))
        try:
            atomic_write_json(self.state_path, snapshot)
        except Exception as e:
            log.error('[ANDVARI-BUY] could not save %s: %s', self.state_path, e)

    def _pace(self, host):
        gap = COMMUNITY_GAP_SECONDS if host == 'community' else STORE_GAP_SECONDS
        wait = gap - (time.time() - self._last_call[host])
        if wait > 0:
            self._sleep(wait)
        self._last_call[host] = time.time()

    def _get(self, url, host='store', client=None, **kwargs):
        self._pace(host)
        response = (client or self._http).get(url, timeout=HTTP_TIMEOUT_SECONDS,
                                              headers={'User-Agent': USER_AGENT}, **kwargs)
        if response.status_code == 429:
            raise PurchaseError('Steam rate-limited the request (HTTP 429): try again later')
        return response

    def _post(self, url, client=None, **kwargs):
        self._pace('store')
        headers = {'User-Agent': USER_AGENT, 'Origin': 'https://checkout.steampowered.com',
                   'Referer': CHECKOUT_URL + '?accountcart=1'}
        return (client or self._http).post(url, timeout=HTTP_TIMEOUT_SECONDS, headers=headers, **kwargs)

    def _client(self, cookies):
        """One connection session per account's checkout, so the cookies the checkout
        page sets travel with the next steps (a test's fake http is used as is)."""
        if not isinstance(self._http, requests.Session):
            return self._http
        client = requests.Session()
        client.cookies.update(cookies)
        return client

    def _cookies(self, steamid):
        cookies = self.steam.web_session_cookie_for(steamid)
        if not cookies:
            self.steam.ensure_fresh_session(steamid)
            cookies = self.steam.web_session_cookie_for(steamid)
        if not cookies:
            raise PurchaseError('no Steam web session for this account')
        return cookies

    @staticmethod
    def _token(cookies):
        return cookies['steamLoginSecure'].split('%7C%7C', 1)[1]

    def _accounts(self):
        """[(steamid, account name, store country)] — maFiles + Andvari's account refresh."""
        countries = self.card_deals.account_countries() if self.card_deals else {}
        rows = []
        for steamid in self.steam.storage.list_accounts():
            data = self.steam.storage.load_account(steamid) or {}
            rows.append((str(steamid), data.get('account_name') or str(steamid), countries.get(str(steamid))))
        return sorted(rows, key=lambda row: row[1].lower())

    def _set_job(self, **fields):
        with self._lock:
            self._job.update(fields)

    def _start(self, kind, target, args, dry_run=False):
        if not self._job_lock.acquire(blocking=False):
            return {'started': False, 'error': f'a {self._job.get("kind")} is already running'}
        self._set_job(running=True, kind=kind, done=0, total=0, phase='starting', error=None,
                      started_at=time.time(), finished_at=None, dry_run=dry_run)

        def run():
            try:
                target(*args)
            except Exception as e:
                log.exception('[ANDVARI-BUY] %s failed', kind)
                self._set_job(error=str(e))
            finally:
                self._set_job(running=False, phase=None, finished_at=time.time())
                self._job_lock.release()
        threading.Thread(target=run, daemon=True, name=f'andvari-{kind}').start()
        return {'started': True}

    # ---- reading Steam ---------------------------------------------------------------------

    def _game_price(self, app_id, country):
        key = (app_id, country)
        cached = self._prices.get(key)
        if cached and time.time() - cached[1] < PRICE_TIME_TO_LIVE_SECONDS:
            return cached[0]
        response = self._get(APPDETAILS_URL, params={'appids': app_id, 'cc': country.lower(), 'l': 'english'})
        entry = ((response.json() or {}).get(str(app_id)) or {}) if response.ok else {}
        details = entry.get('data') if entry.get('success') else None
        chosen = choose_package(details)
        game = {'app_id': app_id, 'name': (details or {}).get('name') or str(app_id),
                'packageid': chosen[0] if chosen else None, 'price': chosen[1] if chosen else None,
                'currency': chosen[2] if chosen else None,
                'discount_percent': ((details or {}).get('price_overview') or {}).get('discount_percent')}
        if details is not None:          # a failed answer is asked again next time
            self._prices[key] = (game, time.time())
        return game

    def _wallet(self, cookies):
        """(currency id, balance in minor units) from the Market page's g_rgWalletInfo."""
        response = self._get(MARKET_URL, host='community', cookies=cookies)
        match = re.search(r'g_rgWalletInfo\s*=\s*(\{.*?\});', response.text if response.ok else '')
        if not match:
            raise PurchaseError('could not read the wallet (is the web session valid?)')
        info = json.loads(match.group(1))
        return int(info.get('wallet_currency') or 0), int(info.get('wallet_balance') or 0)

    def _owned(self, cookies):
        response = self._get(USERDATA_URL, params={'t': int(time.time())}, cookies=cookies)
        data = response.json() if response.ok else {}
        if not data.get('rgOwnedApps') and not data.get('rgOwnedPackages'):
            raise PurchaseError('the store did not return the owned games (session not accepted?)')
        return {int(app) for app in data.get('rgOwnedApps') or []}

    def _profits(self, app_ids):
        """{account name: {app_id: dollars per copy}} from Andvari's deals (best effort)."""
        if not self.card_deals or not self.settings_provider:
            return {}
        try:
            deals = self.card_deals.deals(self.settings_provider(), scope='all', include_unprofitable=True)
        except Exception as e:
            log.info('[ANDVARI-BUY] no Andvari profits for the plan: %s', e)
            return {}
        wanted, profits = {str(app) for app in app_ids}, {}
        for row in deals.get('deals') or []:
            if str(row.get('app_id')) in wanted:
                for buyer in row.get('buyers') or []:
                    if buyer.get('profit') is not None:
                        profits.setdefault(buyer['account_name'], {})[int(row['app_id'])] = buyer['profit']
        return profits

    # ---- plan --------------------------------------------------------------------------------

    def start_plan(self, apps_text, max_usd_per_game=DEFAULT_MAX_USD_PER_GAME, steamids=None):
        app_ids = parse_app_ids(apps_text)
        if not app_ids:
            return {'started': False, 'error': 'no store link or app id found'}
        if len(app_ids) > MAX_GAMES_PER_PLAN:
            return {'started': False, 'error': f'at most {MAX_GAMES_PER_PLAN} games at once'}
        try:
            max_usd = float(max_usd_per_game)
        except (TypeError, ValueError):
            return {'started': False, 'error': 'the maximum price must be a number'}
        if max_usd <= 0:
            return {'started': False, 'error': 'the maximum price must be above zero'}
        wanted = {str(steamid) for steamid in steamids} if steamids else None
        return self._start('plan', self._build_plan, (app_ids, max_usd, wanted))

    def _build_plan(self, app_ids, max_usd, wanted):
        accounts = [row for row in self._accounts() if wanted is None or row[0] in wanted]
        rates = self.card_deals.exchange_rates() if self.card_deals else {}
        profits = self._profits(app_ids)
        self._set_job(total=len(accounts), phase='reading accounts and prices')
        rows = []
        for index, (steamid, name, country) in enumerate(accounts):
            row = {'steamid': steamid, 'account_name': name, 'country': country, 'games': [],
                   'balance': None, 'currency': None, 'planned_total': 0, 'error': None}
            try:
                if not country:
                    raise PurchaseError('store country unknown (run an Andvari scan first)')
                cookies = self._cookies(steamid)
                currency_id, balance = self._wallet(cookies)
                currency = CURRENCY_CODES.get(currency_id)
                row.update(balance=balance, currency=currency or f'currency {currency_id}')
                owned = self._owned(cookies)
                games = [self._game_price(app_id, country) for app_id in app_ids]
                row['games'], row['planned_total'] = plan_account(
                    {'balance': balance, 'currency_code': currency, 'owned': owned,
                     'profits': profits.get(name) or {}}, games, max_usd, rates)
            except Exception as e:
                row['error'] = str(e)
                log.warning('[ANDVARI-BUY] plan for %s: %s', name, e)
            rows.append(row)
            self._set_job(done=index + 1)
        buys = [(row, game) for row in rows for game in row['games'] if game['status'] == STATUS_BUY]
        plan = {'created_at': time.time(), 'app_ids': app_ids, 'max_usd_per_game': max_usd, 'accounts': rows,
                'totals': {'copies': len(buys), 'accounts': len({row['steamid'] for row, _ in buys}),
                           'usd': round(sum(game['usd'] or 0 for _, game in buys), 2),
                           'profit': round(sum(game.get('profit') or 0 for _, game in buys), 2)}}
        with self._lock:
            self._plan = plan
        self._save()

    # ---- buy ---------------------------------------------------------------------------------

    def start_purchase(self, selection, dry_run=False, plan_created_at=None):
        """*selection*: [{steamid, app_ids}]; each game must be planned as "buy" in the
        plan the screen showed (*plan_created_at*), younger than PLAN_TIME_TO_LIVE_SECONDS,
        and not bought (or paid for) since that plan was made."""
        if not isinstance(selection, list) or not all(isinstance(item, dict) for item in selection):
            return {'started': False, 'error': 'selection must be a list of {steamid, app_ids}'}
        with self._lock:
            plan = json.loads(json.dumps(self._plan)) if self._plan else None
            history = list(self._history)
        if not plan:
            return {'started': False, 'error': 'check the accounts first'}
        if plan_created_at is not None and abs(float(plan_created_at) - plan['created_at']) > 1e-6:
            return {'started': False, 'error': 'the plan changed since this page loaded: look at it again'}
        if time.time() - plan['created_at'] > PLAN_TIME_TO_LIVE_SECONDS:
            return {'started': False, 'error': 'the plan is older than 30 minutes: check the accounts again'}
        # Paid (or possibly paid) since the plan was made: never offered twice.
        already = {(entry['steamid'], game['app_id']) for entry in history
                   if entry.get('at', 0) >= plan['created_at'] and entry.get('payment_attempted')
                   for game in entry.get('games') or []}
        by_account = {row['steamid']: row for row in plan['accounts']}
        orders, seen = [], set()
        for item in selection:
            row = by_account.get(str(item.get('steamid')))
            if not row:
                return {'started': False, 'error': f'account {item.get("steamid")} is not in the plan'}
            if row['steamid'] in seen:
                return {'started': False, 'error': f'{row["account_name"]} is selected twice'}
            seen.add(row['steamid'])
            try:
                wanted = {int(app) for app in item.get('app_ids') or []}
            except (TypeError, ValueError):
                return {'started': False, 'error': 'app ids must be numbers'}
            games = [game for game in row['games'] if game['app_id'] in wanted]
            if not wanted or len(games) != len(wanted) or any(game['status'] != STATUS_BUY for game in games):
                return {'started': False, 'error': f'{row["account_name"]}: only games planned as "buy" can be bought'}
            if any((row['steamid'], game['app_id']) in already for game in games):
                return {'started': False, 'error': f'{row["account_name"]}: already bought (or paid for) since this plan'}
            orders.append({'steamid': row['steamid'], 'account_name': row['account_name'],
                           'country': row['country'], 'currency': row['currency'], 'games': games})
        if not orders:
            return {'started': False, 'error': 'nothing selected'}
        return self._start('purchase', self._buy_all, (orders, dry_run), dry_run=dry_run)

    def _buy_all(self, orders, dry_run):
        self._set_job(total=len(orders), phase='dry run (nothing is paid)' if dry_run else 'buying')
        for index, order in enumerate(orders):
            if index:
                self._sleep(ACCOUNT_GAP_SECONDS)
            self._set_job(phase=f'{"dry run" if dry_run else "buying"}: {order["account_name"]}')
            self._buy_account(order, dry_run)
            self._set_job(done=index + 1)

    def _record(self, result):
        """Save the purchase's history entry now (it is updated in place step by step,
        so a backend reload mid-purchase still leaves a trace)."""
        with self._lock:
            if not any(entry is result for entry in self._history):
                self._history.append(result)
                self._history = self._history[-HISTORY_CAP:]
        self._save()

    def _mark_plan(self, order, status):
        """After paying (or possibly paying), the plan stops offering those games."""
        with self._lock:
            for row in (self._plan or {}).get('accounts') or []:
                if row['steamid'] == order['steamid']:
                    for game in row['games']:
                        if game['app_id'] in {g['app_id'] for g in order['games']}:
                            game['status'] = status
        self._save()

    def _buy_account(self, order, dry_run):
        steamid, country = order['steamid'], order['country']
        expected = sum(game['price'] for game in order['games'])
        packages = sorted(game['packageid'] for game in order['games'])
        result = {'at': time.time(), 'steamid': steamid, 'account_name': order['account_name'],
                  'games': [{'app_id': g['app_id'], 'name': g['name'], 'price': g['price']} for g in order['games']],
                  'currency': order['currency'], 'expected': expected, 'charged': None, 'dry_run': dry_run,
                  'state': 'in progress', 'payment_attempted': False, 'paid': False,
                  'ok': False, 'owned_after': None, 'error': None}
        self._record(result)
        cookies, token, transid, added, client = None, None, None, False, None
        try:
            cookies = self._cookies(steamid)
            token = self._token(cookies)
            # Fresh look right before buying: owned meanwhile, or the money gone?
            owned = self._owned(cookies)
            if any(game['app_id'] in owned for game in order['games']):
                raise PurchaseError('the account owns one of these games now: not bought')
            _, balance = self._wallet(cookies)
            if balance < expected:
                raise PurchaseError(f'the wallet holds {balance}, the games cost {expected}: not bought')
            subtotal, in_cart = cart_contents(self._cart(token, country))
            if in_cart or subtotal:
                raise PurchaseError('the cart is not empty: empty it in the Steam store first '
                                    '(Heimdall never buys or removes what is already there)')
            added = True                 # from here on the cart is ours: emptied at the end
            response = self._post(CART_API + 'AddItemsToCart/v1/', params={'access_token': token}, data={
                'input_json': json.dumps({'user_country': country,
                                          'items': [{'packageid': package} for package in packages]})})
            if not response.ok:
                raise PurchaseError(f'adding to the cart answered HTTP {response.status_code}')
            subtotal, in_cart = cart_contents(self._cart(token, country))
            if in_cart != packages or subtotal != expected:
                raise PurchaseError(f'the cart holds {in_cart} for {subtotal}, planned {packages} for {expected}')
            client = self._client(cookies)
            self._get(CHECKOUT_URL, client=client, params={'accountcart': 1}, cookies=cookies)  # sets its cookies
            response = self._post(CHECKOUT_URL + 'inittransaction/', client=client, cookies=cookies,
                                  data=checkout_form(country, cookies['sessionid']))
            init = response.json() if response.ok else {}
            transid = init.get('transid')
            if not transid or str(init.get('success')) != '1':
                raise PurchaseError(f'checkout did not start (result {init.get("purchaseresultdetail", response.status_code)})')
            response = self._get(CHECKOUT_URL + 'getfinalprice/', client=client, cookies=cookies, params={
                'count': 1, 'transid': transid, 'purchasetype': 'self', 'microtxnid': -1, 'cart': -1,
                'gidReplayOfTransID': -1})
            final = response.json() if response.ok else {}
            try:
                charged = int(final.get('total'))
            except (TypeError, ValueError):
                charged = None
            result['charged'] = charged
            if str(final.get('success')) != '1' or charged != expected:
                raise PurchaseError(f'Steam would charge {charged}, planned {expected}: not bought')
            if dry_run:
                result.update(ok=True, error='dry run: stopped before paying')
                return result
            # Paying starts: from here on nothing is cancelled, and any error means
            # "may be paid" — the plan stops offering these games either way.
            paid_transid, transid = transid, None
            result['payment_attempted'] = True
            self._record(result)
            self._mark_plan(order, 'check')
            response = self._post(CHECKOUT_URL + 'finalizetransaction/', client=client, cookies=cookies, data={
                'transid': paid_transid, 'CardCVV2': '',
                'browserInfo': json.dumps({'language': 'en-US', 'javaEnabled': 'false', 'colorDepth': 24,
                                           'screenHeight': 1080, 'screenWidth': 1920})})
            finalized = response.json() if response.ok else {}
            if str(finalized.get('success')) not in ('1', str(TRANSACTION_PENDING)):
                raise PurchaseError(f'paying failed (result {finalized.get("purchaseresultdetail", response.status_code)})')
            status = self._wait_for_transaction(client, cookies, paid_transid)
            if status != 1:
                raise PurchaseError(f'Steam reports the purchase as not finished (status {status})')
            result['paid'] = True
            self._mark_plan(order, 'bought')
            # The store's owned list can lag the licence by minutes (seen live): the
            # purchase counts once Steam reports it finished; ownership is a note.
            owned = self._owned(cookies)
            result['owned_after'] = [game['app_id'] for game in order['games'] if game['app_id'] in owned]
            result['ok'] = True
            if len(result['owned_after']) != len(order['games']):
                result['error'] = 'bought; the store does not list every game yet (it can lag a few minutes)'
            if self.asf is not None and getattr(self.asf, 'enabled', False):
                try:
                    self.asf.farm_now(steamid)
                except Exception as e:
                    log.info('[ANDVARI-BUY] farm now for %s: %s', order['account_name'], e)
            log.info('[ANDVARI-BUY] bought %s on %s for %s %s', [g['name'] for g in order['games']],
                     order['account_name'], expected, order['currency'])
        except Exception as e:
            message = str(e)
            if result['payment_attempted'] and not result['paid']:
                message += ' — it MAY BE PAID: check the account (Steam store → account → purchase history)'
            result['error'] = message
            log.warning('[ANDVARI-BUY] %s: %s', order['account_name'], message)
        finally:
            if transid and cookies:
                self._cancel(client, cookies, transid)
            if added and token:
                self._empty_cart(token)
            result['state'] = 'done'
            self._record(result)
        return result

    def _cart(self, token, country):
        response = self._get(CART_API + 'GetCart/v1/', params={'access_token': token, 'user_country': country})
        if not response.ok:
            raise PurchaseError(f'reading the cart answered HTTP {response.status_code}')
        return response.json()

    def _cancel(self, client, cookies, transid):
        """Best effort: Steam answers an unpaid checkout's cancel with success 2, and
        such a checkout lapses by itself (a new one starts fine), so only log it."""
        try:
            response = self._post(CHECKOUT_URL + 'canceltransaction/', client=client, cookies=cookies,
                                  data={'transid': transid, 'sessionid': cookies['sessionid']})
            log.info('[ANDVARI-BUY] cancel of an unpaid checkout answered %s', response.text[:60])
        except Exception as e:
            log.warning('[ANDVARI-BUY] could not cancel a transaction: %s', e)

    def _empty_cart(self, token):
        """Empty the cart Heimdall filled (it was empty before, so nothing of yours is lost)."""
        try:
            self._post(CART_API + 'DeleteCart/v1/', params={'access_token': token}, data={'input_json': '{}'})
        except Exception as e:
            log.warning('[ANDVARI-BUY] could not empty the cart: %s', e)

    def _wait_for_transaction(self, client, cookies, transid):
        status = None
        for _ in range(TRANSACTION_POLL_TRIES):
            response = self._get(CHECKOUT_URL + 'transactionstatus/', client=client, cookies=cookies,
                                 params={'count': 1, 'transid': transid})
            try:
                status = int((response.json() if response.ok else {}).get('success') or 0)
            except (TypeError, ValueError):
                status = 0
            if status != TRANSACTION_PENDING:
                return status
            self._sleep(TRANSACTION_POLL_SECONDS)
        return status

    # ---- status --------------------------------------------------------------------------------

    def status(self):
        with self._lock:
            state = json.loads(json.dumps({'job': self._job, 'plan': self._plan, 'history': self._history}))
        return {'job': state['job'], 'plan': state['plan'], 'history': state['history'][-100:][::-1],
                'statistics': purchase_statistics(state['history']),
                'plan_time_to_live_seconds': PLAN_TIME_TO_LIVE_SECONDS,
                'default_max_usd_per_game': DEFAULT_MAX_USD_PER_GAME}
