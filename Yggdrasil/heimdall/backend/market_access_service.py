"""Market access: when each account would lose the Steam Community Market.

Steam lets an account use the Market only while it has a valid purchase that is
between 7 days and one year old: wallet funds, games, DLC, software and in-game
purchases count; gifts, retail game keys, free licenses and Community Market
transactions do not. The "Market access" button in Mímir (routes in
``routes/mimir.py``) checks every account, one at a time, and the Dashboard shows
the result on each account card. Two reads per account:

1. **Purchase History** (``store.steampowered.com/account/history/``), first page
   only: the newest purchase is always on it. ``counted_purchases`` keeps the rows
   of type "Purchase" (games bought, wallet top-ups by card, PayPal or wallet
   code) and "In-Game Purchase" (Storage Units, capsules; Ivan's word) that were
   not refunded. Market transactions (buying or selling on the Community Market:
   that is not a store purchase), refunds, gift purchases and currency
   conversions never count. Access lapses one year after the newest counted
   purchase.
2. **Steam's own verdict**: ``steamcommunity.com/market/eligibilitycheck/``
   redirects to the Market when the account may use it (to the help page when
   not) and sets the ``webTradeEligibility`` cookie (``allowed``,
   ``allowed_at_time``, ``reason``, ...), kept as Steam sent it.

Free promotional packages ("Limited Free Promotional Package", acquisition method
Complimentary) never appear in Purchase History, so claiming one does not extend
access (checked live 2026-10-29 with Hexologic). Results are kept in
``cache/market_access.json``.

**Market unlocker** (``start_unlock``): the cheapest purchase that counts, an
in-game purchase of Zombie Hunter's "Editor Pack" (app 1723420, item 330, $0.01;
Steam no longer sells items of a game the account does not own, so the free game
is claimed first: package 617273 through the store website, as ASF does). Per
account, one at a time: owned games (``dynamicstore/userdata``) → claim the free
package if needed → wallet currency (Market page ``g_rgWalletInfo``) →
``buyitem/1723420/330/1`` redirects to Steam's approval page
``checkout/approvetxn/<transid>`` → the page must name this account, Zombie Hunter,
the Editor Pack, quantity 1 and a total of at most ``UNLOCK_MAX_USD`` after
conversion (Andvari's exchange rates), and must offer Authorize (some accounts are
asked for a billing address first: reported, never filled in) → a dry run (the
default) cancels (``approved=0``, what the page's Cancel does); a real run approves
(``approved=1``) and opens Steam's finalize link. From the approval on nothing is
cancelled and a failure reads "MAY BE PAID". Then that account's access is checked
again. A real purchase only on Ivan's word, for the accounts he names.

The unlocker is a command, like Transfer's moves: ``plan_unlock`` runs the dry run
as a job and makes a plan (per account: passed, or failed and why; the price; the
ceiling); ``execute_plan(plan_id, dry_run=False)`` buys only on the accounts that
passed that dry run, once (a second call answers the same job). A plan expires
``PLAN_LIFETIME_SECONDS`` after its dry run ends. There is no other way to buy.

``status()`` also decides what the pages print: per account ``headline``,
``advice``, ``purchase_would_help`` + ``reason`` (which accounts the unlocker
ticks), the order of the banner (``flagged``, ``not_checked``) and the last
unlocker run per account (``last_unlocks``).
"""
import datetime
import html
import json
import logging
import re
import threading
import time
import uuid
from urllib.parse import unquote

import requests

import community_pacer
from jobs import JobRegistry
from jsonio import atomic_write_json, read_json
from storage_deposit import CommandError
from storage_shop_service import WHOLE_UNIT_CURRENCIES, approval_form
from store_purchase_service import CURRENCY_CODES, to_usd

log = logging.getLogger(__name__)

STATE_PATH = 'cache/market_access.json'
HISTORY_URL = 'https://store.steampowered.com/account/history/?l=english'
ELIGIBILITY_URL = 'https://steamcommunity.com/market/eligibilitycheck/?goto=%2Fmarket%2F'
USER_AGENT = 'Mozilla/5.0 (Heimdall market access check)'
HTTP_TIMEOUT_SECONDS = 30
STORE_GAP_SECONDS = 2.0
ACCESS_DAYS = 365          # a purchase keeps the Market open this long
MINIMUM_AGE_DAYS = 7       # ... once it is this old
WARN_DAYS = 30             # Mímir and the Dashboard warn this long before access lapses

COUNTED_TYPES = ('Purchase', 'In-Game Purchase')
REFUND_TYPE = 'Refund'

STATE_OK, STATE_WARN, STATE_LAPSED, STATE_UNKNOWN = 'ok', 'warn', 'lapsed', 'unknown'

# Market unlocker: Zombie Hunter's one-cent "Editor Pack" (shared by a trading course, 2026-10-29).
UNLOCK_APP = 1723420
UNLOCK_PACKAGE = 617273          # the game's free package (ASF's addlicense answer)
UNLOCK_ITEM = 330
UNLOCK_GAME_NAME = 'Zombie Hunter'
UNLOCK_ITEM_NAME = 'Editor Pack'
UNLOCK_MAX_USD = 0.05            # refused above this, whatever the currency
USERDATA_URL = 'https://store.steampowered.com/dynamicstore/userdata/'
FREE_LICENSE_URL = 'https://store.steampowered.com/freelicense/addfreelicense/{package}'
BUY_ITEM_URL = 'https://store.steampowered.com/buyitem/{app}/{item}/1'
APPROVAL_PREFIX = 'https://checkout.steampowered.com/checkout/approvetxn/'
FINALIZE_PREFIX = 'https://store.steampowered.com/buyitem/{app}/finalize/'.format(app=UNLOCK_APP)
MARKET_URL = 'https://steamcommunity.com/market/'
UNLOCK_HISTORY_CAP = 300
PLAN_LIFETIME_SECONDS = 10 * 60       # a dry run's plan can be bought this long after the dry run ended
PLAN_KEEP_SECONDS = 60 * 60      # then it is forgotten (an executed one keeps answering its job until then)
BILLING_ADDRESS_TEXT = 'You must enter a billing address'


class MarketAccessError(RuntimeError):
    pass


# ---- pure helpers (unit-tested) ------------------------------------------------------------

def _text(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))).strip()


def _cell(row_html, css_class):
    match = re.search(r'<td[^>]*class="%s[^"]*"[^>]*>(.*?)</td>' % css_class, row_html, re.S)
    return match.group(1) if match else ''


def parse_date(text):
    """'2 Oct, 2026' (or 'Oct 2, 2026') -> date, or None."""
    for pattern in ('%d %b, %Y', '%b %d, %Y', '%d %B, %Y', '%B %d, %Y'):
        try:
            return datetime.datetime.strptime(text.strip(), pattern).date()
        except ValueError:
            continue
    return None


def parse_history(page_html):
    """Rows of the first Purchase History page: [{date, type, payment, items, total}],
    newest first. None when the page is not a Purchase History page (session not
    accepted, login page)."""
    if 'wallet_history_table' not in (page_html or ''):
        return None
    rows = []
    for row in re.findall(r'<tr[^>]*class="[^"]*wallet_table_row[^"]*"[^>]*>(.*?)</tr>', page_html, re.S):
        kind_cell = _cell(row, 'wht_type')
        payment = re.search(r'<div class="wth_payment">(.*?)</div>', kind_cell, re.S)
        items_cell = _cell(row, 'wht_items')
        items = [_text(item) for item in re.findall(r'<div[^>]*>(.*?)</div>', items_cell, re.S)]
        items = [item for item in items if item] or ([_text(items_cell)] if _text(items_cell) else [])
        date = parse_date(_text(_cell(row, 'wht_date')))
        rows.append({
            'date': date.isoformat() if date else None,
            'type': _text(re.sub(r'<div class="wth_payment">.*', '', kind_cell, flags=re.S)),
            'payment': _text(payment.group(1)) if payment else '',
            'items': items,
            'total': _text(re.sub(r'<div class="wth_payment">.*?</div>', '', _cell(row, 'wht_total'), flags=re.S)),
        })
    return rows


def counted_purchases(rows):
    """The rows Steam counts as a purchase for the Market, newest first: type
    "Purchase" or "In-Game Purchase", dated, and not refunded (every item has a
    refund on or after it)."""
    refunds = {}
    for row in rows:
        if row['type'] == REFUND_TYPE and row['date']:
            for item in row['items']:
                name = item[:-len(' Refund')] if item.endswith(' Refund') else item
                refunds.setdefault(name, []).append(row['date'])
    counted = []
    for row in rows:
        if row['type'] not in COUNTED_TYPES or not row['date']:
            continue
        refunded = row['items'] and all(any(day >= row['date'] for day in refunds.get(item, []))
                                        for item in row['items'])
        if not refunded:
            counted.append(row)
    return sorted(counted, key=lambda row: row['date'], reverse=True)


def access_summary(rows, today):
    """When the Market stays open by Purchase History: {last_purchase,
    access_until, opens_on, days_left}. *today* is a date."""
    counted = counted_purchases(rows)
    summary = {'last_purchase': counted[0] if counted else None, 'access_until': None,
               'opens_on': None, 'days_left': None}
    if not counted:
        return summary
    newest = datetime.date.fromisoformat(counted[0]['date'])
    until = newest + datetime.timedelta(days=ACCESS_DAYS)
    summary['access_until'] = until.isoformat()
    summary['days_left'] = (until - today).days
    old_enough = [row for row in counted
                  if MINIMUM_AGE_DAYS <= (today - datetime.date.fromisoformat(row['date'])).days <= ACCESS_DAYS]
    recent = [datetime.date.fromisoformat(row['date']) for row in counted
              if (today - datetime.date.fromisoformat(row['date'])).days < MINIMUM_AGE_DAYS]
    if not old_enough and recent:
        summary['opens_on'] = (min(recent) + datetime.timedelta(days=MINIMUM_AGE_DAYS)).isoformat()
    return summary


def parse_eligibility(cookie_value, location):
    """Steam's verdict from the eligibility check: {allowed, allowed_at, reason, raw}.
    ``allowed`` is None when Steam sent no cookie; the redirect decides then."""
    raw = None
    try:
        raw = json.loads(unquote(cookie_value)) if cookie_value else None
    except ValueError:
        raw = None
    allowed = None
    if isinstance(raw, dict) and 'allowed' in raw:
        allowed = bool(raw.get('allowed'))
    elif location:
        allowed = '/market' in location and 'help.steampowered.com' not in location
    raw = raw if isinstance(raw, dict) else {}
    return {'allowed': allowed, 'allowed_at': raw.get('allowed_at_time') or None,
            'reason': raw.get('reason'), 'raw': raw, 'redirect': location}


def approval_transaction(location):
    """Steam's transaction id from the buyitem redirect to the approval page, or None."""
    if not location or not location.startswith(APPROVAL_PREFIX):
        return None
    match = re.match(r'(\d+)/', location[len(APPROVAL_PREFIX):])
    return match.group(1) if match else None


def approval_total(page, currency):
    """The total the approval page asks for, in minor units (the last amount before
    "Total:"), or None."""
    text = re.sub(r'<(script|style)\b.*?</\1>', ' ', page or '', flags=re.S | re.I)
    text = html.unescape(re.sub(r'<[^>]+>', '\n', text))
    end = text.find('Total:')
    if end < 0:
        return None
    amounts = re.findall(r'\d(?:[\d.,]|[ \u00a0\u202f](?=\d))*\d|\d', text[max(0, end - 400):end])
    if not amounts:
        return None
    digits = int(re.sub(r'\D', '', amounts[-1]) or 0)
    return digits * 100 if currency in WHOLE_UNIT_CURRENCIES else digits


def check_unlock_page(page, account_name, transaction_id, currency, rates):
    """Everything the approval page must show before approving. Returns
    (form, total in minor units, US dollars, problem or None)."""
    text = _text(page)
    if BILLING_ADDRESS_TEXT in text:
        return None, None, None, 'Steam asks for a billing address on this account first (enter it once in a browser)'
    if f'Steam account: {account_name}' not in text:
        return None, None, None, 'the approval page does not name this account'
    if UNLOCK_GAME_NAME not in text or UNLOCK_ITEM_NAME not in text:
        return None, None, None, f'the approval page is not for {UNLOCK_GAME_NAME} / {UNLOCK_ITEM_NAME}'
    quantity = re.search(r'QTY\s+PRICE\s+NAME\s+(\d+)\s', text)
    if not quantity or quantity.group(1) != '1':
        return None, None, None, 'the approval page is not for exactly one item'
    total = approval_total(page, currency)
    usd = to_usd(total, currency, rates)
    if total is None or usd is None:
        return None, total, usd, f'could not read the total in {currency or "the wallet currency"}'
    if not 0 < usd <= UNLOCK_MAX_USD:
        return None, total, usd, f'the total is ${usd:.4f}, above the ${UNLOCK_MAX_USD:.2f} limit'
    if 'AuthorizeTransaction( true' not in page:
        return None, total, usd, 'Steam does not offer Authorize on this page (wallet balance?)'
    form = approval_form(page, transaction_id)
    if not form:
        return None, total, usd, 'the approval form for this transaction is missing'
    return form, total, usd, None


def unlock_purchase_listed(rows, today):
    """Whether Purchase History lists the Zombie Hunter in-game purchase, dated today
    or yesterday (Steam's dates are not in our time zone)."""
    earliest = (today - datetime.timedelta(days=1)).isoformat()
    return any(row['type'] == 'In-Game Purchase' and (row['date'] or '') >= earliest
               and any(UNLOCK_ITEM_NAME in item or UNLOCK_GAME_NAME in item for item in row['items'])
               for row in rows)


def access_state(summary, steam):
    """ok / warn / lapsed / unknown for Mímir and the Dashboard."""
    if steam and steam.get('allowed') is False:
        return STATE_LAPSED
    days_left = (summary or {}).get('days_left')
    if days_left is None:
        # No counted purchase on the first page, yet Steam allows it (a purchase we
        # do not recognise): nothing here keeps it open, so warn.
        return STATE_WARN if steam and steam.get('allowed') else STATE_UNKNOWN
    if days_left < 0:
        return STATE_LAPSED
    if days_left <= WARN_DAYS:
        return STATE_WARN
    return STATE_OK


def _day(iso):
    """'2026-10-02' -> '2 Oct 2026'."""
    day = datetime.date.fromisoformat(iso)
    return f'{day.day} {day.strftime("%b")} {day.year}'


def headline(result):
    """The one line Mímir and the Dashboard print for an account: which fact wins."""
    summary = result.get('summary') or {}
    steam = result.get('steam') or {}
    if result.get('error') and not result.get('summary'):
        return f'Not checked: {result["error"]}'
    if steam.get('allowed') is False:
        allowed_at = steam.get('allowed_at')
        try:
            when = (datetime.datetime.fromtimestamp(int(allowed_at), datetime.timezone.utc).date().isoformat()
                    if allowed_at else None)
        except (TypeError, ValueError, OverflowError, OSError):
            when = None                  # one bad saved value must not break the whole status
        if when:
            return f'No Market access (Steam) until {_day(when)}'
        return 'No Market access (Steam says so)'
    if summary.get('opens_on'):
        return f'Market opens {_day(summary["opens_on"])}'
    if not summary.get('access_until'):
        return 'No counted purchase on the first page of Purchase History'
    if summary.get('days_left') is not None and summary['days_left'] < 0:
        return f'Market access lapsed {_day(summary["access_until"])}'
    return f'Market until {_day(summary["access_until"])} · {summary.get("days_left")} days'


def advice(result):
    """What to do about it, or None."""
    if result.get('state') == STATE_WARN:
        return 'buy something (a game, a wallet top-up or an in-game item) to keep it open'
    return None


def purchase_help(result):
    """(would the Market unlocker's purchase help, why): amber or red with no counted
    purchase that keeps the Market open for more than ``WARN_DAYS`` (red with one:
    Steam blocks it for another reason, which a purchase does not fix)."""
    summary = result.get('summary') or {}
    days_left = summary.get('days_left')
    state = result.get('state')
    if state == STATE_UNKNOWN:
        return False, 'not checked yet'
    if state == STATE_OK:
        return False, f'the Market stays open {days_left} more days'
    if summary.get('opens_on') and days_left is not None and days_left > WARN_DAYS:
        return False, f'a purchase under {MINIMUM_AGE_DAYS} days old opens the Market on {_day(summary["opens_on"])}'
    if days_left is None:
        return True, 'no counted purchase keeps the Market open'
    if days_left < 0:
        return True, f'Market access lapsed {_day(summary["access_until"])}'
    if days_left <= WARN_DAYS:
        return True, f'Market access lapses in {days_left} days'
    return False, f'Steam blocks the Market for another reason (a purchase keeps it open until {_day(summary["access_until"])})'


def describe_unlock(entry, plan_open=True):
    """An unlocker run as the pages print it: {kind, text, at, ...}; kind is running,
    bought, passed (a dry run that would buy), failed or may_be_paid. *plan_open* is
    False when the dry run's plan can no longer be bought (expired, replaced, forgotten)."""
    if entry.get('state') == 'in progress':
        kind, text = 'running', f'{entry.get("step")}…'
    elif entry.get('ok') and entry.get('paid'):
        kind, text = 'bought', 'bought'
    elif entry.get('ok'):
        kind = 'passed'
        price = f' (${entry["usd"]:.4f})' if entry.get('usd') is not None else ''
        text = (f'ready: dry run passed{price}' if plan_open
                else f'dry run passed{price}; its plan can no longer be bought: run the dry run again')
    else:
        kind = 'may_be_paid' if entry.get('approved') else 'failed'
        text = entry.get('error') or 'failed'
    return {'kind': kind, 'text': text, 'at': entry.get('at'), 'dry_run': entry.get('dry_run'),
            'plan_id': entry.get('plan_id')}


def _days_left_or_first(result):
    """Banner order: soonest first; no date at all (nothing keeps it open) before everything."""
    days_left = (result.get('summary') or {}).get('days_left')
    return -9999 if days_left is None else days_left


# ---- the service -------------------------------------------------------------------------------

class MarketAccessService:
    def __init__(self, steam_service, card_deals_service=None, state_path=STATE_PATH, http=None,
                 sleep=time.sleep, today=datetime.date.today, jobs=None):
        self.steam = steam_service
        self.card_deals = card_deals_service
        self.jobs = jobs or JobRegistry()
        self._plans = {}                 # plan id -> Market unlocker plan (memory only, like Transfer's)
        self.state_path = state_path
        self._http = http or requests.Session()
        self._sleep = sleep
        self._today = today
        self._lock = threading.RLock()
        self._job_lock = threading.Lock()
        self._last_store_call = 0.0
        saved = read_json(state_path, default={}) or {}
        self._accounts = dict(saved.get('accounts') or {})
        self._unlocks = list(saved.get('unlocks') or [])
        for entry in self._unlocks:      # a reload killed the job mid-purchase
            if entry.get('state') == 'in progress':
                entry['state'] = 'done'
                entry['error'] = ('interrupted by a backend restart'
                                  + (' after approving — it MAY BE PAID: check the account'
                                     if entry.get('approved') else ' before approving'))
        self._job = {'running': False, 'kind': None, 'done': 0, 'total': 0, 'current': None, 'error': None,
                     'started_at': None, 'finished_at': saved.get('finished_at'), 'dry_run': False,
                     'job_id': None}

    def _save(self):
        with self._lock:
            snapshot = json.loads(json.dumps({'accounts': self._accounts, 'unlocks': self._unlocks[-UNLOCK_HISTORY_CAP:],
                                              'finished_at': self._job.get('finished_at')}))
        try:
            atomic_write_json(self.state_path, snapshot)
        except Exception as e:
            log.error('[MARKET-ACCESS] could not save %s: %s', self.state_path, e)

    def _set_job(self, **fields):
        with self._lock:
            self._job.update(fields)

    def status(self):
        """Every account's result plus what the pages print (``headline``, ``advice``,
        ``purchase_would_help`` + ``reason``), the banner's order (``flagged``: amber and
        red, soonest first; ``not_checked``), the last unlocker run per account
        (``last_unlocks``) and the running job."""
        with self._lock:
            state = json.loads(json.dumps({'job': self._job, 'accounts': self._accounts,
                                           'unlocks': self._unlocks[-50:][::-1]}))
            last_unlocks = {}
            for entry in reversed(self._unlocks):          # newest first
                if entry.get('steamid') in last_unlocks:
                    continue
                plan = self._plans.get(entry.get('plan_id'))
                # a dry run still running on other accounts is not a closed plan
                plan_open = (not entry.get('dry_run') or self._buyable(plan)
                             or bool(plan and plan['state'] == 'dry run'))
                last_unlocks[entry.get('steamid')] = describe_unlock(entry, plan_open)
        for result in state['accounts'].values():     # days left move on every day
            if result.get('summary') and result['summary'].get('access_until'):
                until = datetime.date.fromisoformat(result['summary']['access_until'])
                result['summary']['days_left'] = (until - self._today()).days
                result['state'] = access_state(result['summary'], result.get('steam'))
            result['headline'] = headline(result)
            result['advice'] = advice(result)
            result['purchase_would_help'], result['reason'] = purchase_help(result)
        flagged = sorted((steamid for steamid, result in state['accounts'].items()
                          if result['state'] in (STATE_WARN, STATE_LAPSED)),
                         key=lambda steamid: _days_left_or_first(state['accounts'][steamid]))
        not_checked = sorted((steamid for steamid, result in state['accounts'].items()
                              if result['state'] == STATE_UNKNOWN),
                             key=lambda steamid: (state['accounts'][steamid].get('account_name') or '').lower())
        return {**state, 'warn_days': WARN_DAYS, 'flagged': flagged, 'not_checked': not_checked,
                'last_unlocks': last_unlocks,
                'unlocker': {'game': UNLOCK_GAME_NAME, 'item': UNLOCK_ITEM_NAME, 'app': UNLOCK_APP,
                             'max_usd': UNLOCK_MAX_USD}}

    def _all_accounts(self):
        accounts = []
        for steamid in self.steam.storage.list_accounts():
            data = self.steam.storage.load_account(steamid) or {}
            accounts.append((str(steamid), data.get('account_name') or str(steamid)))
        return sorted(accounts, key=lambda account: account[1].lower())

    def _start(self, kind, accounts, step, dry_run=False, on_finish=None, job_fields=None):
        """One job at a time (a check or an unlock): *step(steamid, name)* per account (it
        answers False for an account that failed). Followed in ``status()['job']`` and at
        ``GET /api/jobs/<job_id>``; *on_finish(error)* gives the job's result and runs
        before the job reads as finished."""
        if not self._job_lock.acquire(blocking=False):
            return {'started': False, 'error': f'a market access {self._job.get("kind") or "job"} is already running'}
        started_at = time.time()
        try:
            job = self.jobs.create(f'market-access-{kind}', {'total': len(accounts), 'dry_run': dry_run,
                                                            **(job_fields or {})})
            self._set_job(running=True, kind=kind, done=0, total=len(accounts), current=None, error=None,
                          started_at=started_at, finished_at=None, dry_run=dry_run, job_id=job['id'])
        except BaseException:
            self._job_lock.release()     # never leave the service locked when the job could not start
            raise

        def run():
            error, result, passed, failed = None, None, 0, 0
            try:
                for steamid, name in accounts:
                    self._set_job(current=name)
                    self.jobs.update(job['id'], phase=name)
                    if step(steamid, name) is False:
                        failed += 1
                    else:
                        passed += 1
                    self._set_job(done=self._job['done'] + 1)
                    self.jobs.update(job['id'], done=passed, failed=failed)
            except Exception as e:
                log.exception('[MARKET-ACCESS] %s failed', kind)
                error = str(e)
                self._set_job(error=error)
            finally:
                # The plan is ready and the next job may start before this job reads as finished.
                if on_finish:
                    try:
                        result = on_finish(error)
                    except Exception:
                        log.exception('[MARKET-ACCESS] %s: could not finish the plan', kind)
                try:
                    self._set_job(running=False, current=None, finished_at=time.time())
                    self._save()
                finally:
                    self._job_lock.release()
                    self.jobs.finish(job['id'], result=result, error=error, phase=None)
        try:
            threading.Thread(target=run, daemon=True, name=f'market-access-{kind}').start()
        except BaseException as e:
            self._set_job(running=False, error=str(e), finished_at=time.time())
            self._job_lock.release()
            self.jobs.finish(job['id'], error=f'could not start ({type(e).__name__})', phase=None)
            raise
        return {'started': True, 'started_at': started_at, 'job_id': job['id']}

    def start_check(self):
        return self._start('check', self._all_accounts(), self._check_and_store)

    # ---- Market unlocker commands: plan (a dry run) -> execute once -> a job ---------------

    def plan_unlock(self, steamids):
        """Dry-run the Market unlocker on *steamids* as a job and make a plan of it.
        Answers the plan at once (``state`` "dry run", ``dry_run_job_id``); it is
        ready when that job finishes (``GET .../plans/<plan_id>`` or the job's result)."""
        wanted = {str(steamid) for steamid in steamids or []}
        accounts = [account for account in self._all_accounts() if account[0] in wanted]
        if not accounts:
            raise CommandError('no_accounts', 'Choose at least one known account')
        now = time.time()
        plan = {'id': uuid.uuid4().hex, 'created_at': now, 'accounts': accounts, 'state': 'dry run',
                'results': None, 'error': None, 'finished_at': None, 'expires_at': None,
                'dry_run_job_id': None, 'job_id': None}
        with self._lock:
            for plan_id in [key for key, old in self._plans.items()
                            if not self._job_running(old)
                            and ((old['expires_at'] and old['expires_at'] < now and not old['job_id'])
                                 or old['created_at'] < now - PLAN_KEEP_SECONDS)]:
                del self._plans[plan_id]
            self._plans[plan['id']] = plan
            try:
                started = self._start('unlock', accounts,
                                      lambda steamid, name: self._unlock(steamid, name, True, plan['id']),
                                      dry_run=True, on_finish=lambda error: self._finish_dry_run_safely(plan, error),
                                      job_fields={'plan_id': plan['id']})
            except BaseException:
                del self._plans[plan['id']]  # the dry run never started: no plan
                raise
            if not started['started']:
                del self._plans[plan['id']]
                raise CommandError('busy', started['error'], 409)
            plan['dry_run_job_id'] = started['job_id']
            # An older plan for any of these accounts can no longer be bought: two plans
            # covering one account would buy on it twice.
            mine = {steamid for steamid, _ in accounts}
            for old in self._plans.values():
                if (old is not plan and not old['job_id'] and old['state'] in ('dry run', 'ready')
                        and mine & {steamid for steamid, _ in old['accounts']}):
                    old['state'] = 'replaced'
            return self.describe_plan(plan)

    def _job_running(self, plan):
        """True while the plan's dry run or purchase job still runs."""
        for job_id in (plan.get('dry_run_job_id'), plan.get('job_id')):
            job = self.jobs.get(job_id) if job_id else None
            if job and job.get('running'):
                return True
        return False

    def _finish_dry_run_safely(self, plan, error):
        """``_finish_dry_run``, but a failure while finishing marks the plan failed
        instead of leaving it in "dry run" for ever."""
        try:
            return self._finish_dry_run(plan, error)
        except Exception as e:
            log.exception('[MARKET-ACCESS] plan %s: could not finish the dry run', plan['id'])
            with self._lock:
                plan.update(state='failed', error=f'could not finish the dry run ({type(e).__name__})',
                            finished_at=time.time())
            return self.describe_plan(plan)

    def _finish_dry_run(self, plan, error):
        """The dry run ended: each account passed (Steam's approval page was right and
        offered Authorize) or failed with the reason. The plan can be bought from now
        until ``expires_at``."""
        with self._lock:
            entries = {entry['steamid']: entry for entry in self._unlocks
                       if entry.get('plan_id') == plan['id'] and entry.get('dry_run')}
            results = []
            for steamid, name in plan['accounts']:
                entry = entries.get(steamid)
                if entry is None:
                    results.append({'steamid': steamid, 'account_name': name, 'passed': False,
                                    'reason': f'not tried: the dry run stopped ({error})' if error else 'not tried',
                                    'usd': None, 'total': None, 'currency': None, 'claimed_game': False,
                                    'max_usd': UNLOCK_MAX_USD})
                    continue
                passed = bool(entry['ok']) and not entry['approved']
                results.append({'steamid': steamid, 'account_name': name, 'passed': passed,
                                'reason': None if passed else (entry['error'] or 'failed'),
                                'usd': entry['usd'], 'total': entry['total'], 'currency': entry['currency'],
                                'claimed_game': entry['claimed_game'], 'max_usd': UNLOCK_MAX_USD})
            now = time.time()
            plan.update(results=results, state='ready', error=error, finished_at=now,
                        expires_at=now + PLAN_LIFETIME_SECONDS)
            return self.describe_plan(plan)

    @staticmethod
    def _iso(seconds):
        return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat() if seconds else None

    def describe_plan(self, plan):
        """What a client sees of a plan."""
        with self._lock:
            results = plan['results'] or [{'steamid': steamid, 'account_name': name, 'passed': None,
                                           'reason': None, 'usd': None, 'total': None, 'currency': None,
                                           'claimed_game': False, 'max_usd': UNLOCK_MAX_USD}
                                          for steamid, name in plan['accounts']]
            passed = [result for result in results if result['passed']]
            expired = bool(plan['expires_at'] and plan['expires_at'] < time.time())
            state = 'expired' if expired and plan['state'] == 'ready' and not plan['job_id'] else plan['state']
            return {
                'plan_id': plan['id'],
                'state': state,
                'accounts': [dict(result) for result in results],
                'passed': len(passed),
                'max_usd': UNLOCK_MAX_USD,
                'error': plan['error'],
                'created_at': self._iso(plan['created_at']),
                'expires_at': self._iso(plan['expires_at']),
                'expired': expired,
                'executable': self._buyable(plan),
                'dry_run_job_id': plan['dry_run_job_id'],
                'job_id': plan['job_id'],
            }

    @staticmethod
    def _buyable(plan):
        """True when *plan* can be bought now: its dry run is done, at least one account
        passed, it has not expired, was not replaced and was not executed."""
        return bool(plan and plan['state'] == 'ready' and not plan['job_id']
                    and plan['expires_at'] and plan['expires_at'] >= time.time()
                    and any(result['passed'] for result in plan['results'] or []))

    def get_plan(self, plan_id):
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise CommandError('plan_unknown', 'Unknown plan (make a new dry run)', 404)
            return self.describe_plan(plan)

    def execute_plan(self, plan_id, dry_run=None):
        """Buy for real on the accounts that passed this plan's dry run, once; a second
        call answers the same job. Fails closed: only ``dry_run`` exactly False buys,
        anything else is refused (``real_purchase_only``) without touching Steam or the
        plan, so a missing or misspelt flag can neither buy nor start an unasked dry run
        (which would claim free licenses and open transactions); a new dry run is
        ``plan_unlock``."""
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise CommandError('plan_unknown', 'Unknown plan (make a new dry run)', 404)
            if dry_run is not False:
                raise CommandError('real_purchase_only',
                                   'Executing a plan buys for real: send dry_run false (a new dry run is a new plan)')
            if plan['job_id']:
                job = self.jobs.get(plan['job_id'])
                if job is None:
                    raise CommandError('job_gone', 'This plan already ran; its result is no longer kept', 410)
                return job
            if plan['state'] == 'replaced':
                raise CommandError('plan_replaced', 'A newer dry run covers these accounts: buy that plan', 409)
            if plan['state'] == 'failed':
                raise CommandError('plan_failed', plan['error'] or 'The dry run of this plan failed: make a new one', 409)
            if plan['state'] != 'ready':
                raise CommandError('plan_not_ready', 'The dry run of this plan is still running', 409)
            if plan['expires_at'] < time.time():
                raise CommandError('plan_expired', 'The plan expired: make a new dry run')
            names = dict(plan['accounts'])
            accounts = [(result['steamid'], names[result['steamid']]) for result in plan['results'] if result['passed']]
            if not accounts:
                raise CommandError('nothing_passed', 'No account passed the dry run of this plan')
            started = self._start('unlock', accounts,
                                  lambda steamid, name: self._unlock(steamid, name, False, plan_id),
                                  dry_run=False, on_finish=lambda error: self._purchase_result(plan_id, error),
                                  job_fields={'plan_id': plan_id})
            if not started['started']:
                raise CommandError('busy', started['error'], 409)
            plan.update(job_id=started['job_id'], state='executed')
            log.warning('[MARKET-UNLOCK] plan %s: buying for real on %s', plan_id,
                        ', '.join(name for _, name in accounts))
            return self.jobs.get(started['job_id'])

    def _purchase_result(self, plan_id, error):
        """The real run's result per account: bought, failed (nothing paid) or MAY BE PAID."""
        with self._lock:
            entries = [entry for entry in self._unlocks if entry.get('plan_id') == plan_id and not entry.get('dry_run')]
            return {'plan_id': plan_id, 'error': error,
                    'accounts': [{'steamid': entry['steamid'], 'account_name': entry['account_name'],
                                  'paid': entry['paid'], 'approved': entry['approved'], 'error': entry['error'],
                                  'usd': entry['usd'], **{key: value for key, value in describe_unlock(entry).items()
                                                          if key in ('kind', 'text')}}
                                 for entry in entries]}

    def _check_and_store(self, steamid, name):
        result = {'account_name': name, 'checked_at': time.time(), 'error': None,
                  'summary': None, 'steam': None, 'state': STATE_UNKNOWN}
        try:
            cookies = self._cookies(steamid)
            rows = self._history(cookies)
            result['summary'] = access_summary(rows, self._today())
            result['steam'] = self._eligibility(cookies)
        except MarketAccessError as e:
            result['error'] = str(e)
            if 'HTTP 429' in str(e):
                raise                    # rate-limited: stop the whole check
        except requests.RequestException as e:
            result['error'] = f'Steam unreachable ({type(e).__name__})'
        finally:
            result['state'] = access_state(result['summary'], result['steam'])
            with self._lock:
                self._accounts[steamid] = result
            self._save()
        log.info('[MARKET-ACCESS] %s: %s (until %s, Steam allowed %s)%s', name, result['state'],
                 (result['summary'] or {}).get('access_until'), (result['steam'] or {}).get('allowed'),
                 f' error: {result["error"]}' if result['error'] else '')

    def _cookies(self, steamid):
        cookies = self.steam.web_session_cookie_for(steamid)
        if not cookies:
            self.steam.ensure_fresh_session(steamid)
            cookies = self.steam.web_session_cookie_for(steamid)
        if not cookies:
            raise MarketAccessError('no Steam web session for this account')
        return cookies

    def _pace_store(self):
        wait = STORE_GAP_SECONDS - (time.time() - self._last_store_call)
        if wait > 0:
            self._sleep(wait)
        self._last_store_call = time.time()

    def _store_get(self, url, cookies, **kwargs):
        self._pace_store()
        response = self._http.get(url, cookies=cookies, timeout=HTTP_TIMEOUT_SECONDS,
                                  headers={'User-Agent': USER_AGENT}, **kwargs)
        if response.status_code == 429:
            raise MarketAccessError('Steam rate-limited the request (HTTP 429): try again later')
        return response

    def _store_post(self, url, cookies, data, referer):
        self._pace_store()
        response = self._http.post(url, cookies=cookies, data=data, timeout=HTTP_TIMEOUT_SECONDS,
                                   allow_redirects=False,
                                   headers={'User-Agent': USER_AGENT, 'Referer': referer,
                                            'Origin': re.match(r'https://[^/]+', url).group(0)})
        if response.status_code == 429:
            raise MarketAccessError('Steam rate-limited the request (HTTP 429): try again later')
        return response

    # ---- Market unlocker -----------------------------------------------------------------

    def _owns_unlock_game(self, cookies):
        response = self._store_get(USERDATA_URL, cookies, params={'t': int(time.time())})
        data = response.json() if response.ok else {}
        if not data.get('rgOwnedApps') and not data.get('rgOwnedPackages'):
            raise MarketAccessError('the store did not return the owned games (session not accepted?)')
        return UNLOCK_APP in {int(app) for app in data.get('rgOwnedApps') or []}

    def _wallet_currency(self, cookies):
        community_pacer.pace(self._sleep)
        response = self._http.get(MARKET_URL, cookies=cookies, timeout=HTTP_TIMEOUT_SECONDS,
                                  headers={'User-Agent': USER_AGENT})
        match = re.search(r'g_rgWalletInfo\s*=\s*(\{.*?\});', response.text if response.ok else '')
        if not match:
            raise MarketAccessError('could not read the wallet (is the web session valid?)')
        return CURRENCY_CODES.get(int(json.loads(match.group(1)).get('wallet_currency') or 0))

    def _record_unlock(self, entry, **fields):
        with self._lock:
            entry.update(fields)
        self._save()

    def _unlock(self, steamid, name, dry_run, plan_id):
        """One account of a plan's dry run or real run; answers whether it went through."""
        entry = {'steamid': steamid, 'account_name': name, 'at': time.time(), 'dry_run': dry_run,
                 'state': 'in progress', 'step': 'starting', 'ok': False, 'error': None, 'approved': False,
                 'paid': False, 'total': None, 'currency': None, 'usd': None, 'transaction_id': None,
                 'claimed_game': False, 'plan_id': plan_id}
        with self._lock:
            self._unlocks.append(entry)
        try:
            self._unlock_steps(entry, steamid, name, dry_run)
        except Exception as e:           # any failure is recorded, and the next account still runs
            if isinstance(e, MarketAccessError):
                error = str(e)
            elif isinstance(e, requests.RequestException):
                error = f'Steam unreachable ({type(e).__name__})'
            else:
                log.exception('[MARKET-UNLOCK] %s: unexpected error', name)
                error = f'unexpected error ({type(e).__name__})'
            if entry['approved'] and not entry['paid']:
                error += ' — after approving: it MAY BE PAID, check the account'
            self._record_unlock(entry, error=error)
            if 'HTTP 429' in error:
                raise MarketAccessError(error)
        finally:
            self._record_unlock(entry, state='done')
            log.info('[MARKET-UNLOCK] %s%s: %s', name, ' (dry run)' if dry_run else '',
                     'ok' if entry['ok'] else entry['error'])
        if entry['approved']:
            self._check_and_store(steamid, name)      # the new purchase moves the date
        return bool(entry['ok'])

    def _unlock_steps(self, entry, steamid, name, dry_run):
        cookies = self._cookies(steamid)
        self._record_unlock(entry, step='owned games')
        if not self._owns_unlock_game(cookies):
            self._record_unlock(entry, step=f'claiming {UNLOCK_GAME_NAME} (free)')
            self._store_post(FREE_LICENSE_URL.format(package=UNLOCK_PACKAGE), cookies,
                             {'ajax': 'true', 'sessionid': cookies.get('sessionid', '')},
                             f'https://store.steampowered.com/app/{UNLOCK_APP}/')
            if not self._owns_unlock_game(cookies):
                raise MarketAccessError(f'could not claim {UNLOCK_GAME_NAME} (Steam did not add it)')
            self._record_unlock(entry, claimed_game=True)
        self._record_unlock(entry, step='wallet')
        currency = self._wallet_currency(cookies)
        rates = self.card_deals.exchange_rates() if self.card_deals else {}
        self._record_unlock(entry, step='opening the purchase', currency=currency)
        response = self._store_get(BUY_ITEM_URL.format(app=UNLOCK_APP, item=UNLOCK_ITEM), cookies,
                                   allow_redirects=False)
        transaction_id = approval_transaction(response.headers.get('Location'))
        if not transaction_id:
            raise MarketAccessError(f'Steam did not open a purchase (HTTP {response.status_code}; '
                                    f'does the account own {UNLOCK_GAME_NAME}?)')
        location = response.headers['Location']
        self._record_unlock(entry, step='approval page', transaction_id=transaction_id)
        page = self._store_get(location, cookies).text
        form, total, usd, problem = check_unlock_page(page, name, transaction_id, currency, rates)
        self._record_unlock(entry, total=total, usd=usd)
        if problem or dry_run:
            self._cancel(cookies, page, transaction_id, location)
            if problem:
                raise MarketAccessError(problem + ' (cancelled)')
            self._record_unlock(entry, ok=True, step='dry run: stopped before approving (cancelled)')
            return
        action, fields = form
        self._record_unlock(entry, step='approving', approved=True)
        answer = self._store_post(action, cookies, fields, location)
        finalize = answer.headers.get('Location') or fields.get('returnurl') or ''
        if not finalize.startswith(FINALIZE_PREFIX):
            raise MarketAccessError(f'Steam did not send the purchase to its finalize page (HTTP {answer.status_code})')
        self._record_unlock(entry, step='finalizing')
        done = self._store_get(finalize, cookies)
        if not done.ok:
            raise MarketAccessError(f'the finalize page answered HTTP {done.status_code}')
        self._record_unlock(entry, step='checking Purchase History')
        if not unlock_purchase_listed(self._history(cookies), self._today()):
            raise MarketAccessError('Purchase History does not list the purchase yet')
        self._record_unlock(entry, ok=True, paid=True, step='bought')

    def _cancel(self, cookies, page, transaction_id, location):
        """What the approval page's Cancel button does: the same form with approved=0."""
        form = approval_form(page, transaction_id)
        if not form:
            log.warning('[MARKET-UNLOCK] no approval form to cancel transaction %s', transaction_id)
            return
        action, fields = form
        try:
            self._store_post(action, cookies, {**fields, 'approved': '0'}, location)
        except (MarketAccessError, requests.RequestException) as e:
            log.warning('[MARKET-UNLOCK] could not cancel transaction %s: %s', transaction_id, e)

    def _history(self, cookies):
        response = self._store_get(HISTORY_URL, cookies)
        rows = parse_history(response.text if response.ok else '')
        if rows is None:
            raise MarketAccessError(f'Purchase History not served (HTTP {response.status_code}, '
                                    'session not accepted?)')
        return rows

    def _eligibility(self, cookies):
        community_pacer.pace(self._sleep)
        response = self._http.get(ELIGIBILITY_URL, cookies=cookies, timeout=HTTP_TIMEOUT_SECONDS,
                                  headers={'User-Agent': USER_AGENT}, allow_redirects=False)
        if response.status_code == 429:
            raise MarketAccessError('Steam rate-limited the request (HTTP 429): try again later')
        return parse_eligibility(response.cookies.get('webTradeEligibility'), response.headers.get('Location'))
