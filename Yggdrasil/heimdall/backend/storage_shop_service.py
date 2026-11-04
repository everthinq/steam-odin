"""Ratatoskr "Buy Storage Units" (the "storage shop" in the code): buy any Counter-Strike 2
in-game store item on many accounts — Storage Units by default, any other entry of the price
sheet from the Store Catalogue (its Buy button). Every guard below holds for every item; the
item is fixed by the plan (entry name, definition index, price per currency).

A Storage Unit holds 1,000 items; Gjallarhorn's rotation needs empty space on every
account the moment Valve limits a case. Storage Units are sold only in the game's
own store, through the Game Coordinator — so the purchase runs on Ratatoskr's
Counter-Strike 2 session, the same path the game takes:

1. **Plan** (``start_plan``: the item and how many each account wants) — per account:
   the Steam wallet currency and balance (``g_rgWalletInfo``, as Andvari's "Buy games"),
   the store country (Andvari's account refresh) and the Storage Unit price in that
   currency, read from the game store's own price sheet (``StoreGetUserData``:
   LZMA-compressed binary KeyValues; the entry is called "casket"). How many the
   balance covers. The plan answers its lines (``plan_lines``: what each account
   really buys, its total and why an account is skipped), the totals and its state
   (checking, ready, failed, replaced, expired); the page only shows them.
2. **Buy** (``execute_plan``) — first a dry run of the plan (it may be repeated), and a
   real purchase only after a dry run of that same plan passed (Ivan's rule, 2026-11-01):
   it buys only the lines whose dry run passed, once (a second call answers the same job).
   Per account, one at a time: a Ratatoskr session
   (logged in for the purchase and logged out after it, unless one was open; the
   login pauses the account's ASF farming), a fresh price sheet whose price must
   equal the plan, the balance re-checked, then

   a. ``StorePurchaseInit`` — the Game Coordinator opens a wallet transaction
      (only at the price sheet's exact price; nothing is paid yet);
   b. approval — the transaction's page on checkout.steampowered.com
      (``/checkout/approvetxn/<id>/``, what the game's Steam overlay shows) is read
      with the account's web session; it must name this transaction and show the
      planned total, and its approval form is sent;
   c. ``StorePurchaseFinalize`` — the Game Coordinator delivers the Storage Units
      and answers with their item ids; the account's Storage Unit count must grow.

   ``dry_run`` stops after reading the approval page and cancels the transaction
   (``StorePurchaseCancel``): nothing is paid. Before approving, any failure
   cancels; from the approval on nothing is cancelled and a failure is reported as
   "MAY BE PAID". The last approval page read is kept in
   ``cache/storage_shop_approval_page.html`` (local only) to check its layout.

Every purchase is kept in the history; ``status()`` sums what was bought per account.
Steam calls are serial and spaced. Plans, purchases and deliveries are jobs of the shared
``JobRegistry`` (``GET /api/jobs/<id>``): a purchase job carries its own orders and, as
each account finishes, that account's outcome, so any page can follow it by its id.
"""
import base64
import html as html_module
import json
import logging
import lzma
import re
import struct
import threading
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser

import requests

import community_pacer
from jobs import JobRegistry
from jsonio import atomic_write_json, read_json
from ratatoskr_service import items_moving
from storage_deposit import CommandError
from store_purchase_service import CURRENCY_CODES, to_usd

log = logging.getLogger(__name__)

STATE_PATH = 'cache/storage_shop.json'
APPROVAL_PAGE_PATH = 'cache/storage_shop_approval_page.html'
USER_AGENT = 'Mozilla/5.0 (Heimdall Ratatoskr storage shop)'
MARKET_URL = 'https://steamcommunity.com/market/'
# The Counter-Strike 2 inventory lists every Storage Unit (empty ones too) without a login.
INVENTORY_URL = 'https://steamcommunity.com/inventory/{steamid}/730/2'
INVENTORY_PAGE = 2000
INVENTORY_PAGES_MAX = 10
APPROVAL_URL = 'https://checkout.steampowered.com/checkout/approvetxn/{transaction_id}/'
HTTP_TIMEOUT_SECONDS = 30

STORAGE_UNIT_DEFINITION_INDEX = 1201
STORAGE_UNIT_ENTRY = 'casket'          # the price sheet's name for the Storage Unit
STORAGE_UNIT_CAPACITY = 1000
MAX_QUANTITY_PER_ACCOUNT = 20          # per purchase; the Game Coordinator caps one line too
MAX_USD_PER_UNIT = 2.50                # $1.99 list price; anything above is refused
# Any other item: refused above its US dollar list price times this (regional prices sit near it).
PRICE_TOLERANCE = 1.25
# Never sold here: the game license and the Armory Pass unlock rather than arrive as an item
# (and Ivan does not buy them).
NOT_FOR_SALE_ENTRIES = {'Game License', 'XpShopTicket1'}
PLAN_TIME_TO_LIVE_SECONDS = 30 * 60
PRICE_SHEET_TIME_TO_LIVE_SECONDS = 6 * 60 * 60
HISTORY_CAP = 500

STEAM_GAP_SECONDS = 2.0                # between web calls
COMMUNITY_GAP_SECONDS = 4.0            # between steamcommunity.com calls
ACCOUNT_GAP_SECONDS = 5.0              # between two accounts' purchases
FINALIZE_TRIES = 5
FINALIZE_GAP_SECONDS = 3.0
COUNT_TRIES = 5
COUNT_GAP_SECONDS = 2.0
GAME_COORDINATOR_OK = 1

# The game store's own 0-based currency enum (Valve's econ_store.h ECurrency), which
# StorePurchaseInit expects; Steam's wallet currency code (USD 1) is answered with
# result 8, "invalid parameter" (seen live).
GAME_STORE_CURRENCIES = {
    'USD': 0, 'GBP': 1, 'EUR': 2, 'RUB': 3, 'BRL': 4, 'JPY': 8, 'NOK': 9, 'IDR': 10, 'MYR': 11, 'PHP': 12,
    'SGD': 13, 'THB': 14, 'VND': 15, 'KRW': 16, 'TRY': 17, 'UAH': 18, 'MXN': 19, 'CAD': 20, 'AUD': 21,
    'NZD': 22, 'PLN': 23, 'CHF': 24, 'CNY': 25, 'TWD': 26, 'HKD': 27, 'INR': 28, 'AED': 29, 'SAR': 30,
    'ZAR': 31, 'COP': 32, 'PEN': 33, 'CLP': 34, 'ARS': 35, 'CRC': 36, 'ILS': 37, 'KWD': 38, 'QAR': 39,
    'UYU': 40, 'KZT': 41, 'BYN': 42,
}
GAME_COORDINATOR_INVALID_PARAMETER = 8
# Ratatoskr's errors after a store request timed out (that session's store is then closed).
STORE_CLOSED_ERRORS = ('did not answer in time', 'log in again')
# Currencies Steam prints without decimals ("₩ 2,670", "¥ 310", "Rp 34 299").
WHOLE_UNIT_CURRENCIES = {'JPY', 'KRW', 'IDR', 'VND', 'CLP', 'COP', 'KZT', 'CRC', 'UYU', 'TWD', 'UAH', 'INR'}

STATUS_BUY, STATUS_BALANCE, STATUS_CURRENCY = 'buy', 'balance', 'currency'
STATUS_COUNTRY, STATUS_PRICE, STATUS_WALLET = 'country', 'price', 'wallet'
STATUS_BOUGHT, STATUS_CHECK = 'bought', 'check'     # set on a plan's account by its purchase
# Why a plan line buys nothing (or less than asked), by its problem code.
PLAN_PROBLEMS = {
    STATUS_BALANCE: 'Not enough in the wallet',
    STATUS_CURRENCY: 'Wallet currency not sold in the game store',
    STATUS_COUNTRY: 'Store country unknown (refresh the Andvari accounts)',
    STATUS_PRICE: 'Price unknown or unusual',
    STATUS_WALLET: 'Wallet could not be read',
    STATUS_BOUGHT: 'Already bought in this check',
    STATUS_CHECK: 'A purchase is unclear: check the account first',
    'unknown_account': 'Not an account in the dashboard',
    'check_failed': 'The wallets could not be checked',
}
# A purchase's steps per account, as the job reports them (`step` is the current one).
PURCHASE_STEPS = [
    {'key': 'wallet', 'label': 'Checking the wallet'},
    {'key': 'login', 'label': 'Logging in'},
    {'key': 'opening', 'label': 'Opening the purchase'},
    {'key': 'approving', 'label': 'Approving'},
    {'key': 'delivering', 'label': 'Delivering'},
]
# A dry run stops at the approval page: it never approves or delivers.
DRY_RUN_STEPS = PURCHASE_STEPS[:3] + [{'key': 'approving', 'label': 'Checking the approval page'}]
DELIVERY_STEPS = [{'key': 'delivering', 'label': 'Delivering'}]
# Plans replaced by a newer one are remembered (with the job they ran, if any) this many.
OLD_PLANS_KEPT = 50


class ShopError(RuntimeError):
    pass


# ---- pure helpers (unit-tested) ------------------------------------------------------------

STORAGE_NAME_PATTERN = re.compile(r'^storage\s*(\d+)$', re.IGNORECASE)


def next_storage_names(existing_names, count):
    """`count` names "Storage N" for new Storage Units, N going on from the highest number already
    used ("Storage3" and "Storage 3" both count; none used = start at 1). The name only
    activates the unit: the Game Coordinator refuses items into a unit that has none (Ivan,
    2026-11-03: any name will do)."""
    numbers = [int(match.group(1)) for match in
               (STORAGE_NAME_PATTERN.match((name or '').strip()) for name in existing_names) if match]
    start = max(numbers) + 1 if numbers else 1
    return [f'Storage {number}' for number in range(start, start + count)]


def decode_price_sheet(raw):
    """The store's price sheet as a dict. *raw* is Valve's LZMA container ("LZMA",
    uncompressed size, compressed size, 5 property bytes, data) holding binary
    KeyValues; uncompressed binary KeyValues are read as they are."""
    if raw[:4] == b'LZMA':
        size, compressed_size = struct.unpack('<II', raw[4:12])
        properties = raw[12:17]
        raw = lzma.decompress(properties + struct.pack('<Q', size) + raw[17:17 + compressed_size],
                              format=lzma.FORMAT_ALONE)
    value, _ = _read_key_values(raw, 0)
    return value


def _read_string(data, index):
    end = data.index(b'\x00', index)
    return data[index:end].decode('utf-8', 'replace'), end + 1


def _read_key_values(data, index):
    """Binary KeyValues: type byte, key, value; 8 or 11 closes a section."""
    section = {}
    while index < len(data):
        kind = data[index]
        index += 1
        if kind in (8, 11):
            return section, index
        key, index = _read_string(data, index)
        if kind == 0:
            value, index = _read_key_values(data, index)
        elif kind == 1:
            value, index = _read_string(data, index)
        elif kind == 2:
            value, index = struct.unpack('<i', data[index:index + 4])[0], index + 4
        elif kind == 3:
            value, index = struct.unpack('<f', data[index:index + 4])[0], index + 4
        elif kind in (4, 6):
            value, index = struct.unpack('<I', data[index:index + 4])[0], index + 4
        elif kind in (7, 10):
            value, index = struct.unpack('<Q', data[index:index + 8])[0], index + 8
        else:
            raise ValueError(f'unknown KeyValues type {kind} at byte {index - 1}')
        section[key] = value
    return section, index


def sheet_prices(sheet):
    """{entry: {ISO currency: price in minor units}} for every entry of a decoded price sheet."""
    entries = ((sheet or {}).get('store') or {}).get('entries') or {}
    return {name: {currency: int(price) for currency, price in (entry.get('prices') or {}).items()
                   if isinstance(price, int) and price > 0}
            for name, entry in entries.items() if isinstance(entry, dict)}


def storage_unit_prices(sheet):
    """{ISO currency: Storage Unit price in minor units} from a decoded price sheet."""
    return sheet_prices(sheet).get(STORAGE_UNIT_ENTRY) or {}


def max_usd_per_unit(entry, usd_list_price):
    """The highest US dollar price a unit of *entry* may cost in any currency, or None."""
    if entry == STORAGE_UNIT_ENTRY:
        return MAX_USD_PER_UNIT
    return round(usd_list_price * PRICE_TOLERANCE, 2) if usd_list_price else None


def plan_account(account, prices, rates, max_usd=MAX_USD_PER_UNIT):
    """One account's plan row. *account*: {steamid, account_name, country, currency_id,
    balance}; *prices*: {ISO: minor units}; *max_usd*: the item's price cap."""
    currency = CURRENCY_CODES.get(account.get('currency_id'))
    price = prices.get(currency) if currency else None
    usd = to_usd(price, currency, rates) if price else None
    balance = account.get('balance')
    row = {**account, 'currency': currency, 'unit_price': price, 'usd_per_unit': usd,
           'affordable': (balance // price) if price and balance is not None else 0}
    if balance is None or not account.get('currency_id'):
        row['status'] = STATUS_WALLET
    elif not account.get('country'):
        row['status'] = STATUS_COUNTRY
    elif not prices:
        row['status'] = STATUS_PRICE
    elif not price or currency not in GAME_STORE_CURRENCIES:
        row['status'] = STATUS_CURRENCY
    elif usd is None or max_usd is None or usd > max_usd + 1e-9:
        row['status'] = STATUS_PRICE
    elif row['affordable'] < 1:
        row['status'] = STATUS_BALANCE
    else:
        row['status'] = STATUS_BUY
    return row


class _FormReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'form':
            self.forms.append({'action': attributes.get('action') or '', 'method': (attributes.get('method') or 'get').lower(),
                               'id': attributes.get('id') or '', 'fields': {}})
        elif tag == 'input' and self.forms and attributes.get('name'):
            kind = (attributes.get('type') or 'text').lower()
            if kind not in ('submit', 'button', 'image', 'reset') and (kind not in ('checkbox', 'radio')
                                                                       or 'checked' in attributes):
                self.forms[-1]['fields'][attributes['name']] = attributes.get('value') or ''


def approval_form(page, transaction_id):
    """(absolute action URL, fields to send) of the approval form for *transaction_id*,
    or None. Steam's page (seen live 2026-10-03): <form id="form_authtxn" action=
    ".../checkout/approvetxnsubmit" method="POST"> with transaction_id, returnurl,
    sessionid and approved=0; its Authorize button runs AuthorizeTransaction(true),
    which sets approved to 1 and submits. So the form must post, carry this
    transaction's id and have the "approved" field, which is sent as 1."""
    reader = _FormReader()
    reader.feed(page or '')
    wanted = str(transaction_id)
    for form in reader.forms:
        fields = form['fields']
        carries = any(str(value) == wanted for key, value in fields.items() if 'trans' in key.lower())
        if form['method'] == 'post' and carries and 'approve' in (form['action'] + form['id']).lower():
            action = html_module.unescape(form['action'])
            if action.startswith('/'):
                action = 'https://checkout.steampowered.com' + action
            if not action.startswith('https://checkout.steampowered.com/') and \
                    not action.startswith('https://store.steampowered.com/'):
                return None
            if 'approved' not in fields:
                return None
            return action, {**fields, 'approved': '1'}
    return None


def decode_auth_request(auth_request):
    """Steam's approval request (Ratatoskr's capture of ClientMicroTxnAuthRequest, hex
    of binary KeyValues) as its "MessageObject" dict, or None."""
    try:
        raw = bytes.fromhex((auth_request or {}).get('hex') or '')
        start = raw.find(b'\x00MessageObject\x00')
        if start < 0:
            return None
        value, _ = _read_key_values(raw, start)
        return value.get('MessageObject')
    except (ValueError, IndexError, struct.error):
        return None


def _unsigned(value):
    """Binary KeyValues int32 fields are signed; ids above 2**31 come back negative."""
    return value + 2 ** 32 if isinstance(value, int) and value < 0 else value


def check_auth_request(message, transaction_id, quantity, expected, wallet_currency_id,
                       definition_index=STORAGE_UNIT_DEFINITION_INDEX):
    """Steam's transaction id, once Steam's approval request is exactly this purchase:
    the Game Coordinator's transaction (orderid), Counter-Strike 2, only the item ordered
    (*definition_index*) in the quantity ordered, the planned total in the wallet's
    currency. Otherwise ShopError (nothing is approved)."""
    if not message:
        raise ShopError('Steam sent no approval request for the transaction: not bought')
    problems = []
    message = _case_insensitive(message, problems)
    items = [_case_insensitive(item, problems) for item in (message.get('lineitems') or {}).values()
             if isinstance(item, dict)]
    if str(_unsigned(message.get('orderid'))) != str(transaction_id):
        problems.append(f'order {_unsigned(message.get("orderid"))} is not transaction {transaction_id}')
    if message.get('appid') != 730:
        problems.append(f'app {message.get("appid")}')
    if len(items) != 1 or items[0].get('gameitemid') != definition_index \
            or items[0].get('quantity') != quantity:
        problems.append(f'items {items}')
    if message.get('total') != expected or message.get('billingtotal', expected) != expected:
        problems.append(f'total {message.get("total")} / billing {message.get("billingtotal")}, planned {expected}')
    if message.get('currency') != wallet_currency_id:
        problems.append(f'currency {message.get("currency")}, wallet {wallet_currency_id}')
    if not message.get('transid'):
        problems.append('no Steam transaction id')
    if problems:
        raise ShopError('Steam\'s approval request does not match the order (' + '; '.join(problems) + '): not bought')
    return str(message['transid'])


def _case_insensitive(fields, problems):
    """*fields* with lower-case keys: Steam spells them either way ("orderid" for a Storage
    Unit, "OrderID" for a sticker capsule, seen live 2026-10-03). The same name twice with
    different values is a problem, never a pick."""
    lowered = {}
    for key, value in fields.items():
        name = str(key).lower()
        if name in lowered and lowered[name] != value:
            problems.append(f'"{name}" appears twice with different values')
        lowered[name] = value
    return lowered


def steam_error(page):
    """Steam's own error text from an error page ("Oops, sorry! …"), or None."""
    text = re.sub(r'<(script|style)\b.*?</\1>', ' ', page or '', flags=re.S | re.I)
    text = html_module.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)))
    match = re.search(r'An error was encountered while processing your request:\s*(.{1,300}?(?:try again\.|$))', text)
    return match.group(1).strip() if match else None


def amount_shown(page, minor_units, currency=None):
    """Whether *page* shows *minor_units* as money ("$3.98", "37,00 kr", "₩ 5,340",
    "HK$ 31.20"): compared digit by digit, so separators do not matter. Scripts and
    styles are ignored, a number never spans a line break, and the whole-unit form
    ("₩ 5,340" for 534000) counts only for currencies Steam shows without decimals."""
    text = re.sub(r'<(script|style)\b.*?</\1>', ' ', page or '', flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', '\n', text)
    digits_wanted = {str(minor_units)}
    if currency in WHOLE_UNIT_CURRENCIES and minor_units % 100 == 0:
        digits_wanted.add(str(minor_units // 100))
    for match in re.finditer(r'\d(?:[\d.,]|[ \u00a0\u202f](?=\d))*\d|\d', text):
        # Leading zeros do not count: "$0.99" is 99 (seen live on a sticker capsule's page).
        if (re.sub(r'\D', '', match.group(0)).lstrip('0') or '0') in digits_wanted:
            return True
    return False


def shop_statistics(history, item=STORAGE_UNIT_ENTRY):
    """Units of *item* bought (paid) per account, and spent per currency. Purchases from
    before other items were sold carry no "entry": they are Storage Units."""
    accounts, spent, units = {}, {}, 0
    for entry in history:
        if not entry.get('paid') or entry.get('dry_run') or (entry.get('entry') or STORAGE_UNIT_ENTRY) != item:
            continue
        row = accounts.setdefault(entry['account_name'], {'units': 0, 'spent': {}})
        row['units'] += entry['quantity']
        row['spent'][entry['currency']] = row['spent'].get(entry['currency'], 0) + entry['expected']
        spent[entry['currency']] = spent.get(entry['currency'], 0) + entry['expected']
        units += entry['quantity']
    return {'units': units, 'spent': spent, 'accounts': accounts}


def units_text(count, entry, name):
    """"3 Storage Units", or "3 × Name Tag" for any other item."""
    if (entry or STORAGE_UNIT_ENTRY) == STORAGE_UNIT_ENTRY:
        return f'{count} Storage Unit{"" if count == 1 else "s"}'
    return f'{count} × {name}'


def can_deliver_again(entry):
    """Whether "Deliver again" is offered for a purchase: approved (it MAY BE PAID), not
    delivered, finished, with the game store's transaction to deliver. The one rule for
    the purchase log, the banner and ``start_deliver_again``."""
    return bool(entry.get('payment_attempted') and not entry.get('paid') and entry.get('transaction_id')
                and entry.get('state') == 'done')


def purchase_outcome(entry):
    """What a page shows of one account's purchase (a history entry): the order, how it
    ended (`outcome`: bought, test_passed, in_progress or failed) and a one-line summary."""
    item = entry.get('entry') or STORAGE_UNIT_ENTRY
    counts = item == STORAGE_UNIT_ENTRY
    error = entry.get('error')
    if entry.get('paid'):
        outcome = 'bought'
        delivered = len(entry.get('item_ids') or [])
        before, after = entry.get('storage_units_before'), entry.get('storage_units_after')
        summary = 'Bought' + (f' · Storage Units {"?" if before is None else before} → {"?" if after is None else after}'
                              if counts else f' · {delivered} item{"" if delivered == 1 else "s"} delivered')
        if error:
            summary += f' · {re.sub(r"^paid; ", "", error)}'
        if entry.get('named'):
            summary += f' · named {", ".join(entry["named"])}'
        if entry.get('naming_error'):
            summary += f' · {entry["naming_error"]}'
    elif entry.get('dry_run') and entry.get('ok'):
        outcome, summary = 'test_passed', 'Every check passed; cancelled before approving (nothing paid)'
    elif entry.get('state') == 'in progress':
        outcome, summary = 'in_progress', 'In progress'
    else:
        outcome, summary = 'failed', error or 'Not bought'
    view = {key: entry.get(key) for key in (
        'at', 'steamid', 'account_name', 'quantity', 'unit_price', 'currency', 'expected', 'dry_run', 'state',
        'ok', 'paid', 'payment_attempted', 'error', 'item_ids', 'storage_units_before', 'storage_units_after',
        'named', 'naming_error')}
    view.update(entry=item, item_name=entry.get('item_name') or 'Storage Unit', counts_storage_units=counts,
                outcome=outcome, summary=summary, can_deliver_again=can_deliver_again(entry))
    return view


def parse_selection(selection):
    """{steamid: quantity} in the order given, from [{steamid, quantity}]; CommandError otherwise.
    Each quantity is 1 to MAX_QUANTITY_PER_ACCOUNT."""
    if not isinstance(selection, list) or not all(isinstance(line, dict) for line in selection):
        raise CommandError('bad_request', 'selection must be a list of {steamid, quantity}')
    wanted = {}
    for line in selection:
        steamid = str(line.get('steamid') or '')
        if not steamid:
            raise CommandError('bad_request', 'every line needs a steamid')
        if steamid in wanted:
            raise CommandError('bad_request', f'account {steamid} is selected twice')
        quantity = line.get('quantity')
        if isinstance(quantity, bool) or not isinstance(quantity, int) \
                or not 1 <= quantity <= MAX_QUANTITY_PER_ACCOUNT:
            raise CommandError('bad_quantity', f'quantity must be 1 to {MAX_QUANTITY_PER_ACCOUNT}')
        wanted[steamid] = quantity
    return wanted


def plan_lines(rows, wanted):
    """The order once the wallets were read: per wanted account what it really buys now
    (at most what the wallet covers and MAX_QUANTITY_PER_ACCOUNT), its total, the wallet
    after it and why it buys nothing or less (`problem_code`, `problem`)."""
    by_account = {row['steamid']: row for row in rows}
    lines = []
    for steamid, asked in (wanted or {}).items():
        row = by_account.get(steamid)
        if row is None:
            row = {'steamid': steamid, 'account_name': steamid, 'status': 'unknown_account'}
        status = row.get('status')
        limit = min(row.get('affordable') or 0, MAX_QUANTITY_PER_ACCOUNT) if status == STATUS_BUY else 0
        quantity = min(asked, limit)
        total = quantity * row['unit_price'] if quantity else None
        if status != STATUS_BUY:
            # The row's own reason (HTTP 429, no web session …) is kept after the general text.
            general, reason = PLAN_PROBLEMS.get(status), row.get('error')
            code, problem = status, (f'{general}: {reason}' if general and reason else general or reason or status)
        elif quantity < asked:
            code, problem = 'covers_less', f'The wallet covers {quantity}, not {asked}'
        else:
            code, problem = None, None
        balance = row.get('balance')
        lines.append({'steamid': steamid, 'account_name': row.get('account_name') or steamid, 'asked': asked,
                      'quantity': quantity, 'unit_price': row.get('unit_price'), 'currency': row.get('currency'),
                      'usd_per_unit': row.get('usd_per_unit'), 'balance': balance, 'total': total,
                      'balance_after': balance - total if total is not None and balance is not None else None,
                      'payable': bool(quantity) and total is not None, 'problem_code': code, 'problem': problem})
    return lines


def order_totals(lines):
    """What the payable lines cost: units, per currency (minor units) and about how much in US dollars."""
    payable = [line for line in lines if line['payable']]
    by_currency = {}
    for line in payable:
        by_currency[line['currency']] = by_currency.get(line['currency'], 0) + line['total']
    return {'units': sum(line['quantity'] for line in payable), 'accounts_payable': len(payable),
            'skipped': len(lines) - len(payable), 'totals_by_currency': by_currency,
            'usd_total': round(sum((line.get('usd_per_unit') or 0) * line['quantity'] for line in payable), 2)}


def can_buy(sold_in_currency, affordable):
    """An account row's "Can buy" (code, text), whether it can be ticked, and the most it can order.
    *affordable* is None while the wallet was never read (it is checked at the review)."""
    if not sold_in_currency:
        code, text = 'currency_not_sold', 'currency not sold'
    elif affordable is None:
        code, text = 'unknown', '?'
    elif affordable == 0:
        code, text = 'balance_too_low', 'balance too low'
    else:
        code, text = 'up_to', f'up to {affordable}'
    return {'can_buy_code': code, 'can_buy': text,
            'can_buy_count': affordable if sold_in_currency else None,
            'selectable': bool(sold_in_currency and affordable != 0),
            'max_quantity': MAX_QUANTITY_PER_ACCOUNT if affordable is None else min(affordable, MAX_QUANTITY_PER_ACCOUNT)}


def _iso(epoch_seconds):
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).isoformat() if epoch_seconds else None


# ---- the service ---------------------------------------------------------------------------

class StorageShopService:
    def __init__(self, steam_service, ratatoskr_service, card_deals_service=None,
                 state_path=STATE_PATH, approval_page_path=APPROVAL_PAGE_PATH, http=None, sleep=time.sleep,
                 jobs=None, moves_in_progress=lambda steamid: False):
        self.steam = steam_service
        # True while Transfer or Auto-Store moves items on the account: new units are not named then.
        self.moves_in_progress = moves_in_progress
        self.ratatoskr = ratatoskr_service
        self.card_deals = card_deals_service
        # Plans, purchases and deliveries are followed at GET /api/jobs/<id> (the shared registry).
        self.jobs = jobs or JobRegistry()
        self.state_path = state_path
        self.approval_page_path = approval_page_path
        self._http = http or requests.Session()
        self._sleep = sleep
        self._lock = threading.RLock()
        self._job_lock = threading.Lock()
        self._last_call = {'steam': 0.0, 'community': 0.0}
        # Called with (decoded sheet, version, read at) every time the price sheet is read.
        self.on_price_sheet = None
        saved = read_json(state_path, default={}) or {}
        # The current plan: {id, state (checking / ready / failed), entry, wanted {steamid: quantity}
        # or None, accounts (rows), created_at (when its wallets were read), check_job_id, job_id
        # (its purchase), error, …}. A plan saved before plans had an id is dropped.
        self._plan = saved.get('plan') if (saved.get('plan') or {}).get('id') else None
        if self._plan and self._plan.get('state') == 'checking':       # a restart stopped the check
            self._plan.update(state='failed', error='the check was interrupted by a backend restart')
        if self._plan and (self._plan.get('dry_run') or {}).get('state') == 'running':   # … or its dry run
            self._plan['dry_run'].update(state='failed', error='the dry run was interrupted by a backend restart')
        # replaced plan id -> the purchase job it ran (or None); kept so a replaced plan never reads as unknown
        self._old_plans = dict(saved.get('old_plans') or {})
        # {'prices': Storage Unit {ISO: minor}, 'entries': {entry: {ISO: minor}}, 'version', 'fetched_at'}
        self._prices = saved.get('prices') or {}
        self._item_names = {STORAGE_UNIT_ENTRY: (STORAGE_UNIT_DEFINITION_INDEX, 'Storage Unit')}
        # Last known per account (the page lists every account without reading Steam):
        # {steamid: {currency_id, balance, checked_at, storage_units, storage_units_at}}
        self._known = saved.get('known') or {}
        self._history = list(saved.get('history') or [])
        for entry in self._history:      # the purchase log knows some Storage Unit counts already
            count = entry.get('storage_units_after') if entry.get('paid') else entry.get('storage_units_before')
            known = self._known.setdefault(str(entry.get('steamid')), {})
            if count is not None and (entry.get('at') or 0) >= (known.get('storage_units_at') or 0):
                known.update(storage_units=count, storage_units_at=entry.get('at'))
        for entry in self._history:      # a reload killed the job mid-purchase
            if entry.get('state') == 'in progress':
                entry['state'] = 'done'
                entry['error'] = ('interrupted by a backend restart'
                                  + (' after the approval was sent — it MAY BE PAID: check the account'
                                     if entry.get('payment_attempted') else ' before approving'))
        self._job = {'id': None, 'running': False, 'kind': None, 'done': 0, 'total': 0, 'phase': None,
                     'error': None, 'started_at': None, 'finished_at': None, 'dry_run': False,
                     'account': None, 'step': None}

    # ---- plumbing ------------------------------------------------------------------------------

    def _save(self):
        with self._lock:
            snapshot = json.loads(json.dumps({'plan': self._plan, 'old_plans': self._old_plans,
                                              'prices': self._prices, 'known': self._known,
                                              'history': self._history[-HISTORY_CAP:]}))
        try:
            atomic_write_json(self.state_path, snapshot)
        except Exception as e:
            log.error('[STORAGE-SHOP] could not save %s: %s', self.state_path, e)

    def _pace(self, host):
        if host == 'community':        # one gap shared with the Market sellers (same address)
            community_pacer.pace(self._sleep, COMMUNITY_GAP_SECONDS)
            return
        gap = STEAM_GAP_SECONDS
        wait = gap - (time.time() - self._last_call[host])
        if wait > 0:
            self._sleep(wait)
        self._last_call[host] = time.time()

    def _get(self, url, host='steam', client=None, **kwargs):
        self._pace(host)
        response = (client or self._http).get(url, timeout=HTTP_TIMEOUT_SECONDS,
                                              headers={'User-Agent': USER_AGENT}, **kwargs)
        if response.status_code == 429:
            raise ShopError('Steam rate-limited the request (HTTP 429): try again later')
        return response

    def _client(self, cookies):
        if not isinstance(self._http, requests.Session):
            return self._http                # a test's fake http
        client = requests.Session()
        client.cookies.update(cookies)
        return client

    def _cookies(self, steamid):
        cookies = self.steam.web_session_cookie_for(steamid)
        if not cookies:
            self.steam.ensure_fresh_session(steamid)
            cookies = self.steam.web_session_cookie_for(steamid)
        if not cookies:
            raise ShopError('no Steam web session for this account')
        return cookies

    def _wallet(self, cookies):
        """(currency id, balance in minor units) from the Market page's g_rgWalletInfo."""
        response = self._get(MARKET_URL, host='community', cookies=cookies)
        match = re.search(r'g_rgWalletInfo\s*=\s*(\{.*?\});', response.text if response.ok else '')
        if not match:
            raise ShopError('could not read the wallet (is the web session valid?)')
        info = json.loads(match.group(1))
        return int(info.get('wallet_currency') or 0), int(info.get('wallet_balance') or 0)

    def _count_storage_units(self, steamid, cookies):
        """How many Storage Units the account holds, from its Counter-Strike 2 web inventory
        (a renamed one keeps the market name "Storage Unit"); None when it cannot be read."""
        count, start = 0, None
        for _ in range(INVENTORY_PAGES_MAX):
            params = {'l': 'english', 'count': INVENTORY_PAGE, **({'start_assetid': start} if start else {})}
            response = self._get(INVENTORY_URL.format(steamid=steamid), host='community', cookies=cookies, params=params)
            if not response.ok:
                return None
            data = response.json() or {}
            if not data.get('success', 1):
                return None
            names = {(d.get('classid'), d.get('instanceid')): d.get('market_hash_name')
                     for d in data.get('descriptions') or []}
            count += sum(1 for asset in data.get('assets') or []
                         if names.get((asset.get('classid'), asset.get('instanceid'))) == 'Storage Unit')
            start = data.get('last_assetid')
            if not data.get('more_items') or not start:
                return count
        return None          # more pages than read: no partial count

    def _accounts(self):
        countries = self.card_deals.account_countries() if self.card_deals else {}
        rows = []
        for steamid in self.steam.storage.list_accounts():
            data = self.steam.storage.load_account(steamid) or {}
            rows.append((str(steamid), data.get('account_name') or str(steamid), countries.get(str(steamid))))
        return sorted(rows, key=lambda row: row[1].lower())

    def _rates(self):
        try:
            return self.card_deals.exchange_rates() if self.card_deals else {}
        except Exception:
            return {}

    def _remember(self, steamid, **fields):
        with self._lock:
            self._known.setdefault(str(steamid), {}).update(fields)

    def _step(self, steamid, step):
        """The running purchase's step, for the page's progress view."""
        self._set_job(account=str(steamid), step=step)

    def _set_job(self, **fields):
        """The running job's progress: kept here (``status()``, the Store Catalogue) and in the
        shared job registry (``GET /api/jobs/<id>``)."""
        with self._lock:
            self._job.update(fields)
            job_id = self._job.get('id')
        if job_id:
            self.jobs.update(job_id, **fields)

    def _start(self, kind, target, args, dry_run=False, fields=None):
        """Run *target* as this service's one job (a plan, purchase, delivery or price sheet read;
        never two at once). *fields* are extra job fields (the orders, the steps …)."""
        if not self._job_lock.acquire(blocking=False):
            return {'started': False, 'code': 'busy', 'error': f'a {self._job.get("kind")} is already running'}
        extra = {'dry_run': dry_run, 'account': None, 'step': None, **(fields or {})}
        job = self.jobs.create(f'storage_shop_{kind.replace(" ", "_")}', {**extra, 'phase': 'starting'})
        with self._lock:
            self._job = {'id': job['id'], 'running': True, 'kind': kind, 'done': 0, 'total': 0, 'phase': 'starting',
                         'error': None, 'started_at': time.time(), 'finished_at': None, **extra}
            started_at = self._job['started_at']

        def run():
            try:
                target(*args)
            except Exception as e:
                log.exception('[STORAGE-SHOP] %s failed', kind)
                self._set_job(error=str(e))
            finally:
                with self._lock:
                    self._job.update(running=False, phase=None, finished_at=time.time(), account=None, step=None)
                    final = dict(self._job)
                self.jobs.finish(job['id'], result=final.get('result'), error=final.get('error'),
                                 phase=None, account=None, step=None)
                self._job_lock.release()
        threading.Thread(target=run, daemon=True, name=f'storage-shop-{kind}').start()
        return {'started': True, 'started_at': started_at, 'kind': kind, 'job_id': job['id']}

    # ---- Ratatoskr sessions ------------------------------------------------------------------

    def _session(self, steamid):
        """Make sure Ratatoskr has a Game Coordinator session; True when it was opened
        here (and is to be closed after the purchase)."""
        status = self.ratatoskr.get_status(steamid) or {}
        if status.get('status') == 'connected':
            return False
        # A broken session ("gc_lost") belongs to someone else (an auto-store account, a
        # page left open): it is repaired here but never logged out afterwards.
        ours = status.get('status') == 'disconnected'
        account = self.steam.get_account(steamid) or {}
        password = self.steam.get_password(steamid)
        if not account.get('account_name') or not password:
            raise ShopError('no password in the Mímir vault for this account (Ratatoskr cannot log in)')
        answer = self.ratatoskr.login(account['account_name'], password, shared_secret=account.get('shared_secret'))
        if not answer or answer.get('error') or not answer.get('success'):
            raise ShopError(f'Ratatoskr could not log in: {(answer or {}).get("error") or "no answer"}')
        return ours and not answer.get('reused')

    def _close(self, steamid):
        """Log out of Ratatoskr, unless a move started on the account meanwhile."""
        if items_moving(self.ratatoskr, steamid):
            log.info('[STORAGE-SHOP] %s is moving items: Ratatoskr session kept', steamid)
            return
        try:
            self.ratatoskr.disconnect(steamid)
        except Exception as e:
            log.info('[STORAGE-SHOP] could not log out of Ratatoskr for %s: %s', steamid, e)

    def _read_prices(self, steamid):
        """(prices {entry: {ISO: minor units}}, Ratatoskr's answer: storage_units,
        account_country). The whole sheet goes to ``on_price_sheet`` first (the Store Catalogue)."""
        answer = self.ratatoskr.store_user_data(steamid) or {}
        if answer.get('error') or answer.get('result') != GAME_COORDINATOR_OK:
            raise ShopError(f'the game store did not send its price sheet ({answer.get("error") or answer.get("result")})')
        sheet = decode_price_sheet(base64.b64decode(answer.get('price_sheet_base64') or ''))
        if self.on_price_sheet:
            try:
                self.on_price_sheet(sheet, answer.get('price_sheet_version'), time.time())
            except Exception:
                log.exception('[STORAGE-SHOP] the price sheet listener failed')
        entries = sheet_prices(sheet)
        if not any(entries.values()):
            raise ShopError('the game store price sheet has no prices')
        with self._lock:
            self._prices = {'prices': entries.get(STORAGE_UNIT_ENTRY) or {}, 'entries': entries,
                            'version': answer.get('price_sheet_version'), 'fetched_at': time.time()}
        self._save()
        return entries, answer

    def _cached_prices(self, item):
        """(the last read prices of *item* {ISO: minor}, read at)."""
        with self._lock:
            cached = dict(self._prices)
        entries = cached.get('entries') or {STORAGE_UNIT_ENTRY: cached.get('prices') or {}}
        return entries.get(item) or {}, cached.get('fetched_at')

    def _item(self, item):
        """(definition index, English name) of a price sheet entry, from Ratatoskr's item list."""
        if item not in self._item_names:
            answer = self.ratatoskr.store_item_names([item]) or {}
            if answer.get('error'):
                raise ShopError(f'Ratatoskr could not name the item ({answer["error"]})')
            known = (answer.get('definitions') or {}).get(item) or {}
            if not isinstance(known.get('defIndex'), int) or known['defIndex'] <= 0:
                raise ShopError(f'"{item}" is not in Ratatoskr\'s item list: refresh it before buying')
            self._item_names[item] = (known['defIndex'], known.get('name') or item)
        return self._item_names[item]

    def _fetch_price_sheet(self, accounts):
        """(prices {entry: {ISO: minor}} or None, error or None) through one account's login (the sheet
        is the same for everyone). The next account is tried only when a login fails, never
        because the sheet itself is unreadable."""
        error = 'no account could log in to Ratatoskr'
        for steamid, name, _ in accounts:
            try:
                opened = self._session(steamid)
            except Exception as e:
                log.info('[STORAGE-SHOP] price sheet: %s could not log in: %s', name, e)
                continue
            try:
                return self._read_prices(steamid)[0], None
            except Exception as e:
                log.warning('[STORAGE-SHOP] price sheet through %s failed: %s', name, e)
                return None, str(e)
            finally:
                if opened:
                    self._close(steamid)
        return None, error

    def start_price_sheet(self):
        """Read the game store's price sheet now (the Store Catalogue's "Read prices again").
        A job of this service, so it never runs beside a purchase."""
        return self._start('price sheet', self._refresh_price_sheet, ())

    def _refresh_price_sheet(self):
        self._set_job(total=1, phase='reading the game store price sheet')
        _, error = self._fetch_price_sheet(self._accounts())
        if error:
            raise ShopError(error)
        self._set_job(done=1)

    # ---- plan ----------------------------------------------------------------------------------

    def start_plan(self, selection=None, item=STORAGE_UNIT_ENTRY):
        """Plan buying *item* (a price sheet entry; default the Storage Unit): *selection* is
        [{steamid, quantity}] (how many each account wants), or None to read every account's
        wallet without an order ("Check wallets"). Answers the plan (``describe_plan``) in
        state "checking"; its ``check_job_id`` is followed at GET /api/jobs/<id>, then the
        plan is read again. It replaces the current plan. CommandError when refused."""
        if not isinstance(item, str) or not item:
            raise CommandError('bad_request', 'item must be a price sheet entry')
        if item in NOT_FOR_SALE_ENTRIES:
            raise CommandError('not_for_sale', 'the game license and the Armory Pass are not sold here')
        wanted = parse_selection(selection) if selection is not None else None
        if wanted is not None and not wanted:
            raise CommandError('nothing_chosen', 'choose the accounts to buy for')
        plan_id = uuid.uuid4().hex
        with self._lock:       # the plan is recorded before its check can write to it
            started = self._start('plan', self._build_plan, (wanted, item, plan_id),
                                  fields={'plan_id': plan_id, 'accounts_done': 0, 'accounts_total': 0})
            if not started['started']:
                raise CommandError('busy', started['error'], 409)
            self._replace_plan(self._plan_record(plan_id, item, wanted, started['job_id']))
            answer = self.describe_plan(plan_id)       # always "checking": the check waits for this lock
        self._save()
        return answer

    @staticmethod
    def _plan_record(plan_id, item, wanted, check_job_id):
        return {'id': plan_id, 'state': 'checking', 'error': None, 'entry': item, 'wanted': wanted,
                'accounts': [], 'created_at': None, 'check_job_id': check_job_id, 'job_id': None,
                # The plan's last dry run: {state: running / passed / failed, job_id, error,
                # passed: {steamid: quantity}, failed: {steamid: reason}}; None before the first one.
                'dry_run': None}

    def _replace_plan(self, plan):
        """Make *plan* the current one; the one it replaces is remembered with its purchase job."""
        with self._lock:
            if self._plan and self._plan.get('id'):
                self._old_plans[self._plan['id']] = self._plan.get('job_id')
                while len(self._old_plans) > OLD_PLANS_KEPT:
                    self._old_plans.pop(next(iter(self._old_plans)))
            self._plan = plan

    def _build_plan(self, wanted=None, item=STORAGE_UNIT_ENTRY, plan_id=None):
        """Read the wallets of the wanted accounts (every account when *wanted* is None) and
        fill the plan *plan_id* (a new current plan when None)."""
        if plan_id is None:
            plan_id = uuid.uuid4().hex
            self._replace_plan(self._plan_record(plan_id, item, wanted, None))
        try:
            self._fill_plan(plan_id, wanted, item)
        except Exception as e:
            with self._lock:
                if self._plan and self._plan['id'] == plan_id:
                    self._plan.update(state='failed', error=str(e))
            self._save()
            raise

    def _fill_plan(self, plan_id, wanted, item):
        accounts = [row for row in self._accounts() if wanted is None or row[0] in wanted]
        definition_index, item_name = self._item(item)
        self._set_job(total=len(accounts) + 1, accounts_total=len(accounts), accounts_done=0,
                      phase='reading the game store price sheet')
        prices, fetched_at = self._cached_prices(item)
        if not prices or time.time() - (fetched_at or 0) > PRICE_SHEET_TIME_TO_LIVE_SECONDS:
            fetched = self._fetch_price_sheet(accounts)[0]
            if fetched is not None:
                prices = fetched.get(item) or {}
        self._set_job(done=1)
        rates = self._rates()
        max_usd = max_usd_per_unit(item, (prices.get('USD') or 0) / 100)
        rows = []
        for index, (steamid, name, country) in enumerate(accounts):
            self._set_job(phase=f'reading {name}')
            account = {'steamid': steamid, 'account_name': name, 'country': country,
                       'currency_id': None, 'balance': None, 'error': None}
            try:
                cookies = self._cookies(steamid)
                account['currency_id'], account['balance'] = self._wallet(cookies)
                self._remember(steamid, currency_id=account['currency_id'], balance=account['balance'],
                               checked_at=time.time())
            except Exception as e:
                account['error'] = str(e)
            if not account['error'] and item == STORAGE_UNIT_ENTRY:
                try:        # a count that fails (HTTP 429 …) keeps the last one; the wallet row stays good
                    units = self._count_storage_units(steamid, cookies)
                    if units is not None:
                        self._remember(steamid, storage_units=units, storage_units_at=time.time())
                except Exception as e:
                    log.info('[STORAGE-SHOP] could not count the Storage Units of %s: %s', name, e)
            rows.append(plan_account(account, prices, rates, max_usd))
            self._set_job(done=index + 2, accounts_done=index + 1)
        with self._lock:
            if self._plan and self._plan['id'] == plan_id:
                self._plan.update({'state': 'ready', 'created_at': time.time(), 'accounts': rows,
                                   'price_sheet_fetched_at': (self._prices or {}).get('fetched_at'),
                                   'entry': item, 'item_name': item_name, 'definition_index': definition_index,
                                   'usd_list_price': (prices.get('USD') or 0) / 100 or None,
                                   'max_usd_per_unit': max_usd})
        self._save()

    @staticmethod
    def _plan_deadline(plan):
        """When the plan expires: PLAN_TIME_TO_LIVE_SECONDS after its wallets were read, or after
        its FIRST dry run passed (a long dry run never leaves Pay expired; a later dry run does not
        extend it, so the wallets and prices are never older than that — Ivan, 2026-11-04)."""
        # Also while a later dry run runs or after it failed: the plan never flips to expired early.
        start = plan.get('first_passed_at')
        return (start or plan['created_at']) + PLAN_TIME_TO_LIVE_SECONDS

    @classmethod
    def _plan_state(cls, plan, now=None):
        """checking, failed, expired (past ``_plan_deadline``) or ready."""
        if plan['state'] != 'ready':
            return plan['state']
        if (now or time.time()) > cls._plan_deadline(plan):
            return 'expired'
        return 'ready'

    @staticmethod
    def _dry_run_view(plan, lines):
        """The plan's dry run as a client sees it, and the lines a real purchase would buy: those
        whose dry run passed, still payable and in the quantity tested."""
        record = plan.get('dry_run') or {}
        passed, failed = record.get('passed') or {}, record.get('failed') or {}
        names = {line['steamid']: line['account_name'] for line in lines}
        to_buy = [line for line in lines if line['payable'] and passed.get(line['steamid']) == line['quantity']]
        view = {'state': record.get('state') or 'not_run', 'job_id': record.get('job_id'), 'error': record.get('error'),
                'passed_lines': [{'steamid': steamid, 'account_name': names.get(steamid, steamid), 'quantity': quantity}
                                 for steamid, quantity in passed.items()],
                'failed_lines': [{'steamid': steamid, 'account_name': names.get(steamid, steamid), 'reason': reason}
                                 for steamid, reason in failed.items()]}
        return view, to_buy

    def describe_plan(self, plan_id):
        """What a client sees of a plan: its state, the lines (what each account buys, or why
        not, and how its dry run went), the totals, the dry run and what a real purchase would
        buy (`to_pay`). `can_test`: a dry run may start; `executable`: a real purchase may
        (only after a passed dry run of this plan). A replaced plan answers only its state (and
        the job it ran, if any). CommandError plan_unknown otherwise."""
        with self._lock:
            plan = json.loads(json.dumps(self._plan)) if self._plan and self._plan['id'] == plan_id else None
            replaced = plan_id in self._old_plans
            old_job_id = self._old_plans.get(plan_id)
        if plan is None:
            if not replaced:
                raise CommandError('plan_unknown', 'unknown plan: check the accounts again', 404)
            empty = {'units': 0, 'accounts_payable': 0, 'skipped': 0, 'totals_by_currency': {}, 'usd_total': 0}
            return {'plan_id': plan_id, 'state': 'replaced', 'error': None, 'item': None, 'lines': [], **empty,
                    'units_text': None, 'created_at': None, 'expires_at': None, 'check_job_id': None,
                    'job_id': old_job_id, 'can_test': False, 'executable': False,
                    'dry_run': {'state': 'not_run', 'job_id': None, 'error': None, 'passed_lines': [], 'failed_lines': []},
                    'to_pay': {**empty, 'units_text': None}}
        state = self._plan_state(plan)
        if state == 'failed':       # the check itself failed: every wanted account says why
            names = {steamid: name for steamid, name, _ in self._accounts()}
            rows = [{'steamid': steamid, 'account_name': names.get(steamid, steamid), 'status': 'check_failed',
                     'error': plan.get('error')} for steamid in (plan['wanted'] or {})]
            lines = plan_lines(rows, plan['wanted'])
        else:
            lines = plan_lines(plan['accounts'], plan['wanted']) if state != 'checking' else []
        dry_run, to_buy = self._dry_run_view(plan, lines)
        record = plan.get('dry_run') or {}
        for line in lines:
            if line['steamid'] in (record.get('passed') or {}):
                line.update(test='passed', test_result='Every check passed; cancelled before approving (nothing paid)')
            elif line['steamid'] in (record.get('failed') or {}):
                line.update(test='failed', test_result=record['failed'][line['steamid']])
            else:
                line.update(test=None, test_result=None)
        totals, pay = order_totals(lines), order_totals(to_buy)
        entry = plan.get('entry') or STORAGE_UNIT_ENTRY
        name = plan.get('item_name') or ('Storage Unit' if entry == STORAGE_UNIT_ENTRY else entry)
        open_plan = state == 'ready' and not plan.get('job_id')
        return {'plan_id': plan_id, 'state': state, 'error': plan.get('error'),
                'item': {'entry': entry, 'name': name}, 'lines': lines, **totals,
                'units_text': units_text(totals['units'], entry, name),
                'created_at': plan.get('created_at'),
                'expires_at': _iso(self._plan_deadline(plan)) if plan.get('created_at') else None,
                'check_job_id': plan.get('check_job_id'), 'job_id': plan.get('job_id'),
                'dry_run': dry_run, 'to_pay': {**pay, 'units_text': units_text(pay['units'], entry, name)},
                'can_test': open_plan and totals['units'] > 0 and dry_run['state'] != 'running',
                'executable': open_plan and dry_run['state'] == 'passed' and pay['units'] > 0}

    # ---- purchase ------------------------------------------------------------------------------

    def execute_plan(self, plan_id, dry_run=True, item=None):
        """Run the plan: a dry run (repeatable; while one runs, the same job is answered), or the
        real purchase — only after a dry run of this same plan passed, only the lines that passed,
        once (a second call answers the same job, never a second purchase). Answers the job
        (GET /api/jobs/<id>). *item*, when given, is the entry the page shows (it must be the
        plan's). CommandError when refused."""
        with self._lock:
            plan = self._plan if self._plan and self._plan['id'] == plan_id else None
            if plan is None and plan_id in self._old_plans:
                if self._old_plans[plan_id] and not dry_run:
                    return self._same_job(self._old_plans[plan_id])
                raise CommandError('plan_replaced', 'the plan changed since this page loaded: look at it again', 409)
            if plan is None:
                raise CommandError('plan_unknown', 'unknown plan: check the accounts again', 404)
            if plan.get('job_id'):
                if dry_run:
                    raise CommandError('plan_executed', 'this plan was already bought: check the accounts again', 409)
                return self._same_job(plan['job_id'])
            record = plan.get('dry_run') or {}
            if dry_run and record.get('state') == 'running':
                running = self.jobs.get(record.get('job_id'))
                if running and running['running']:
                    return running
            state = self._plan_state(plan)
            if state == 'checking':
                raise CommandError('plan_checking', 'the wallets are still being read', 409)
            if state == 'failed':
                raise CommandError('plan_failed', f'the check failed: {plan.get("error")}')
            if state == 'expired':
                raise CommandError('plan_expired', 'the plan is older than 30 minutes: check the accounts again')
            planned_item = plan.get('entry') or STORAGE_UNIT_ENTRY
            if item is not None and item != planned_item:
                raise CommandError('item_mismatch', 'the plan is for another item: look at it again')
            if planned_item in NOT_FOR_SALE_ENTRIES:
                raise CommandError('not_for_sale', 'the game license and the Armory Pass are not sold here')
            lines = plan_lines(plan['accounts'], plan['wanted'])
            if not dry_run:
                if record.get('state') == 'running':
                    raise CommandError('dry_run_required', 'the test of this plan is still running: pay once it passed', 409)
                if record.get('state') != 'passed':
                    raise CommandError('dry_run_required', 'test this plan without paying first: '
                                                           'only a plan whose test passed can be paid', 409)
                lines = self._dry_run_view(plan, lines)[1]
            item_fields = {'plan_id': plan_id, 'entry': planned_item, 'item_name': plan.get('item_name') or 'Storage Unit',
                           'definition_index': plan.get('definition_index') or STORAGE_UNIT_DEFINITION_INDEX}
            rows = {row['steamid']: row for row in plan['accounts']}
            orders = []
            for line in lines:
                if not line['payable']:
                    continue
                orders.append({**rows[line['steamid']], **item_fields, 'quantity': line['quantity']})
            if not orders:
                raise CommandError('nothing_to_buy', 'nothing in this plan can be bought')
            started = self._start('purchase', self._buy_all, (orders, dry_run, plan_id), dry_run=dry_run, fields={
                'plan_id': plan_id, 'item': {'entry': planned_item, 'name': item_fields['item_name']},
                'orders': [{key: order[key] for key in ('steamid', 'account_name', 'quantity', 'unit_price', 'currency')}
                           for order in orders],
                'steps': DRY_RUN_STEPS if dry_run else PURCHASE_STEPS,
                'accounts_done': 0, 'accounts_total': len(orders), 'total': len(orders),
                'result': self._purchase_result([], planned_item, item_fields['item_name'])})
            if not started['started']:
                raise CommandError('busy', started['error'], 409)
            if dry_run:
                plan['dry_run'] = {'state': 'running', 'job_id': started['job_id'], 'error': None,
                                   'passed': {}, 'failed': {}}
            else:
                plan['job_id'] = started['job_id']
        self._save()
        return self.jobs.get(started['job_id'])

    def _same_job(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            raise CommandError('job_gone', 'this plan already ran; its result is no longer kept '
                                           '(look at the purchase log)', 410)
        return job

    @staticmethod
    def _purchase_result(outcomes, entry, name):
        """A purchase job's result: each finished account's outcome and what was paid for."""
        paid_units = sum(outcome['quantity'] for outcome in outcomes if outcome['paid'])
        return {'accounts': outcomes, 'paid_units': paid_units, 'paid_text': units_text(paid_units, entry, name)}

    def _buy_all(self, orders, dry_run, plan_id=None):
        self._set_job(total=len(orders), phase='dry run (nothing is paid)' if dry_run else 'buying')
        outcomes, error = [], None
        try:
            for index, order in enumerate(orders):
                if index:
                    self._sleep(ACCOUNT_GAP_SECONDS)
                self._set_job(phase=f'{"dry run" if dry_run else "buying"}: {order["account_name"]}')
                outcomes.append(purchase_outcome(self._buy_account(order, dry_run)))
                self._set_job(done=index + 1, accounts_done=index + 1,
                              result=self._purchase_result(outcomes, order.get('entry'), order.get('item_name')))
        except Exception as e:
            error = str(e)
            raise
        finally:
            if dry_run and plan_id:
                self._record_dry_run(plan_id, orders, outcomes, error)

    def _record_dry_run(self, plan_id, orders, outcomes, error):
        """Keep on the plan how its dry run went: which accounts passed (in which quantity) and
        why the others failed. Passed only when it finished without an error and one passed."""
        passed = {o['steamid']: o['quantity'] for o in outcomes if o['outcome'] == 'test_passed'}
        failed = {o['steamid']: o['summary'] for o in outcomes if o['outcome'] != 'test_passed'}
        for order in orders:
            if order['steamid'] not in passed and order['steamid'] not in failed:
                failed[order['steamid']] = 'not tested (the dry run stopped before this account)'
        with self._lock:
            plan = self._plan if self._plan and self._plan['id'] == plan_id else None
            if plan is not None:
                good = bool(passed) and not error
                if good and not plan.get('first_passed_at'):
                    plan['first_passed_at'] = time.time()
                plan['dry_run'] = {'state': 'passed' if good else 'failed',
                                   'job_id': (plan.get('dry_run') or {}).get('job_id'), 'error': error,
                                   'passed': passed, 'failed': failed, 'passed_at': time.time() if good else None}
        self._save()

    def _record(self, result):
        with self._lock:
            if not any(entry is result for entry in self._history):
                self._history.append(result)
                self._history = self._history[-HISTORY_CAP:]
        self._save()

    def _mark_plan(self, steamid, status, plan_id):
        """Mark the account on the plan *plan_id* (bought, or a purchase to check) — only when
        that plan is still the current one: an older plan's purchase never touches a newer plan."""
        with self._lock:
            if not plan_id or not self._plan or self._plan['id'] != plan_id:
                return
            for row in self._plan.get('accounts') or []:
                if row['steamid'] == steamid:
                    row['status'] = status
        self._save()

    def _buy_account(self, order, dry_run):
        steamid, quantity, unit_price = order['steamid'], order['quantity'], order['unit_price']
        item = order.get('entry') or STORAGE_UNIT_ENTRY
        definition_index = order.get('definition_index') or STORAGE_UNIT_DEFINITION_INDEX
        item_name = order.get('item_name') or 'Storage Unit'
        storage_units = item == STORAGE_UNIT_ENTRY      # only then the Storage Unit count is checked
        expected = unit_price * quantity
        result = {'at': time.time(), 'plan_id': order.get('plan_id'), 'steamid': steamid, 'account_name': order['account_name'],
                  'entry': item, 'item_name': item_name, 'definition_index': definition_index,
                  'quantity': quantity, 'unit_price': unit_price, 'currency': order['currency'],
                  'expected': expected, 'transaction_id': None, 'dry_run': dry_run, 'state': 'in progress',
                  'payment_attempted': False, 'paid': False, 'ok': False, 'item_ids': [],
                  'storage_units_before': None, 'storage_units_after': None, 'country': None,
                  'steam_transaction_id': None, 'approval_request': None,
                  'approval_answer': None, 'error': None}
        self._record(result)
        transaction_id, opened = None, False
        try:
            self._step(steamid, 'wallet')
            cookies = self._cookies(steamid)
            currency_id, balance = self._wallet(cookies)
            self._remember(steamid, currency_id=currency_id, balance=balance, checked_at=time.time())
            if currency_id != order['currency_id']:
                raise ShopError('the wallet currency changed since the plan: not bought')
            if balance < expected:
                raise ShopError(f'the wallet holds {balance}, {quantity} × {item_name} cost {expected}: not bought')
            self._step(steamid, 'login')
            opened = self._session(steamid)
            prices, store_answer = self._read_prices(steamid)
            prices = prices.get(item) or {}
            before = store_answer.get('storage_units') if storage_units else None
            result['storage_units_before'] = before
            if before is not None:
                self._remember(steamid, storage_units=before, storage_units_at=time.time())
            if prices.get(order['currency']) != unit_price:
                raise ShopError(f'the game store price is now {prices.get(order["currency"])}, planned {unit_price}: not bought')
            # The store country first: Steam's checkout authorizes the transaction in it (seen
            # live: opened with the login's country TR, a store-country MD account's approval
            # page answered "An unexpected error occurred while authorizing your transaction").
            # Then the country Steam reports for the login; a refused open opens nothing.
            countries = [c for c in dict.fromkeys([(order.get('country') or '').upper(),
                                                   (store_answer.get('account_country') or '').upper()]) if len(c) == 2]
            self._step(steamid, 'opening')
            answer = {}
            for country in countries:
                answer = self.ratatoskr.store_purchase_init(steamid, country, GAME_STORE_CURRENCIES[order['currency']],
                                                            quantity, unit_price,
                                                            item_definition_index=definition_index) or {}
                result['country'] = country
                if answer.get('result') != GAME_COORDINATOR_INVALID_PARAMETER:
                    break
            if str(answer.get('transactionId') or '0') != '0':
                transaction_id = str(answer['transactionId'])     # cancelled on any failure below
            if answer.get('error') or answer.get('result') != GAME_COORDINATOR_OK or not transaction_id:
                raise ShopError(f'the game store did not open the purchase (result {answer.get("error") or answer.get("result")})')
            result['transaction_id'] = transaction_id
            self._record(result)
            # Steam's own approval request names the purchase in full: it must be exactly
            # this order, and its transaction id is the one the approval page takes.
            message = decode_auth_request(answer.get('authRequest'))
            result['approval_request'] = message
            steam_transaction_id = check_auth_request(message, transaction_id, quantity, expected, currency_id,
                                                      definition_index)
            approval_url = APPROVAL_URL.format(transaction_id=steam_transaction_id)
            result['steam_transaction_id'] = steam_transaction_id
            self._step(steamid, 'approving')
            self._record(result)
            client = self._client(cookies)
            response = self._get(approval_url, client=client, cookies=cookies, params={'returnurl': 'steam'})
            page = response.text if response.ok else ''
            self._keep_page(page)
            if not response.ok:
                raise ShopError(f'the approval page answered HTTP {response.status_code}')
            if steam_error(page):
                raise ShopError(f'Steam\'s approval page answered: {steam_error(page)}')
            form = approval_form(page, steam_transaction_id)
            if not form:
                raise ShopError('the approval page has no approval form for this transaction '
                                '(its layout is kept in cache/storage_shop_approval_page.html): not bought')
            if f'Steam account: {order["account_name"]}' not in re.sub(r'<[^>]+>', ' ', page):
                raise ShopError(f'the approval page is not for the account {order["account_name"]}: not bought')
            if not amount_shown(page, expected, order['currency']):
                raise ShopError(f'the approval page does not show the planned total {expected}: not bought')
            if dry_run:
                result.update(ok=True, error='dry run: stopped before approving (cancelled)')
                return result
            # Approving starts: from here on nothing is cancelled, and any error means
            # "may be paid" — the plan stops offering this account either way.
            result['payment_attempted'] = True
            self._record(result)
            self._mark_plan(steamid, STATUS_CHECK, order.get('plan_id'))
            approved_id, transaction_id = transaction_id, None
            action, fields = form
            self._pace('steam')
            response = client.post(action, data=fields, cookies=cookies, timeout=HTTP_TIMEOUT_SECONDS,
                                   allow_redirects=False,
                                   headers={'User-Agent': USER_AGENT, 'Origin': 'https://checkout.steampowered.com',
                                            'Referer': approval_url + '?returnurl=steam'})
            # Whatever Steam answered is kept; whether it was approved is the game store's
            # word below (Finalize delivers only an approved transaction).
            result['approval_answer'] = {'status': response.status_code,
                                         'location': response.headers.get('Location') if response.headers else None,
                                         'body': (response.text or '')[:300]}
            self._record(result)
            self._step(steamid, 'delivering')
            finalized, opened = self._finalize(steamid, approved_id, opened)
            if finalized.get('result') != GAME_COORDINATOR_OK:
                raise ShopError(f'the game store did not deliver (result {finalized.get("error") or finalized.get("result")}; '
                                f'the approval answered HTTP {response.status_code})')
            result['paid'] = True
            result['item_ids'] = [str(item) for item in finalized.get('itemIds') or []]
            self._mark_plan(steamid, STATUS_BOUGHT, order.get('plan_id'))
            after = finalized.get('storageUnits') if storage_units else None
            for attempt in range(COUNT_TRIES):
                if before is None or (after is not None and after >= before + quantity):
                    break
                self._sleep(COUNT_GAP_SECONDS)
                after = (self.ratatoskr.store_user_data(steamid) or {}).get('storage_units')
            result['storage_units_after'] = after
            result['ok'] = True
            if storage_units:
                self._name_new_units(steamid, result)
            # Paid: the wallet is lower by the total (re-read on the next check).
            self._remember(steamid, balance=balance - expected, checked_at=time.time(),
                           **({'storage_units': after, 'storage_units_at': time.time()} if after is not None else {}))
            if len(result['item_ids']) != quantity:
                result['error'] = f'paid; the game store answered with {len(result["item_ids"])} item ids for {quantity}'
            elif before is not None and (after is None or after < before + quantity):
                result['error'] = 'paid; the inventory does not show every new Storage Unit yet'
            log.info('[STORAGE-SHOP] bought %s × %s on %s for %s %s', quantity, item_name,
                     order['account_name'], expected, order['currency'])
        except Exception as e:
            message = str(e)
            if result['payment_attempted'] and not result['paid']:
                message += ' — it MAY BE PAID: check the account (Steam → account → purchase history)'
            result['error'] = message
            log.warning('[STORAGE-SHOP] %s: %s', order['account_name'], message)
        finally:
            if transaction_id:
                cancelled = self.ratatoskr.store_purchase_cancel(steamid, transaction_id) or {}
                log.info('[STORAGE-SHOP] cancelled unapproved transaction for %s: %s', order['account_name'],
                         cancelled.get('result', cancelled.get('error')))
            if opened:
                self._close(steamid)
            result['state'] = 'done'
            self._record(result)
        return result

    def _finalize(self, steamid, transaction_id, opened):
        """(Finalize's answer, whether the session is ours) — tried FINALIZE_TRIES times;
        a lost Ratatoskr session (restart, idle logout) is logged in again first."""
        finalized = {}
        for attempt in range(FINALIZE_TRIES):
            if attempt:
                self._sleep(FINALIZE_GAP_SECONDS)
            if finalized.get('error'):
                # Ratatoskr closes a session's store after a timeout (a late answer could be
                # taken for another request): only a fresh login opens it again. Only a
                # session the shop opened itself is logged out for that, never during a move.
                if any(text in str(finalized['error']) for text in STORE_CLOSED_ERRORS):
                    if not opened:
                        raise ShopError('the delivery timed out on a Ratatoskr session the shop did not open: '
                                        'disconnect the account in Ratatoskr, then press Deliver again')
                    if items_moving(self.ratatoskr, steamid):
                        raise ShopError('the delivery timed out while items are moving on the account: '
                                        'press Deliver again when the move is done')
                    self.ratatoskr.disconnect(steamid)
                try:
                    opened = self._session(steamid) or opened
                except Exception as e:
                    finalized = {'error': str(e)}
                    continue
            finalized = self.ratatoskr.store_purchase_finalize(steamid, transaction_id) or {'error': 'no answer'}
            if finalized.get('result') == GAME_COORDINATOR_OK:
                break
        return finalized, opened

    def start_deliver_again(self, at):
        """For a purchase left "MAY BE PAID": ask the game store to deliver it again
        (Finalize only delivers an approved, unpaid-out transaction, so this cannot pay twice).
        Answers the delivery job, which carries that one order and its outcome."""
        with self._lock:
            entry = next((h for h in self._history if abs(float(h.get('at') or 0) - float(at)) < 1e-6), None)
        if not entry or not can_deliver_again(entry):
            raise CommandError('nothing_to_deliver', 'no unclear purchase with that time', 404)
        item = entry.get('entry') or STORAGE_UNIT_ENTRY
        name = entry.get('item_name') or 'Storage Unit'
        started = self._start('delivery', self._deliver_again, (entry,), fields={
            'item': {'entry': item, 'name': name},
            'orders': [{key: entry.get(key) for key in ('steamid', 'account_name', 'quantity', 'unit_price', 'currency')}],
            'steps': DELIVERY_STEPS, 'accounts_done': 0, 'accounts_total': 1, 'total': 1,
            'result': self._purchase_result([], item, name)})
        if not started['started']:
            raise CommandError('busy', started['error'], 409)
        return self.jobs.get(started['job_id'])

    def _name_new_units(self, steamid, result):
        """Give each Storage Unit this purchase delivered a "Storage N" name, so it takes items (a new
        unit refuses them until it is named). Older unnamed units are left alone, and nothing is
        named while items move on the account (naming changes a unit's id). Never fails the
        purchase: what happened is kept on the result (`named`, `naming_error`). One rename after
        another (rule 21)."""
        named, problems = [], []
        try:
            if self.moves_in_progress(steamid):
                raise RuntimeError('items are moving on this account, so nothing was named')
            delivered = {str(item_id) for item_id in result.get('item_ids') or []}
            for attempt in range(COUNT_TRIES):      # the new units can reach the list a moment late
                caskets = (self.ratatoskr.get_caskets(steamid) or {}).get('caskets') or []
                if delivered <= {str(unit.get('item_id')) for unit in caskets}:
                    break
                self._sleep(COUNT_GAP_SECONDS)
            missing = delivered - {str(unit.get('item_id')) for unit in caskets}
            if missing:
                problems.append(f'not in the Storage Unit list yet: {", ".join(sorted(missing))}')
            unnamed = [unit for unit in caskets if str(unit.get('item_id')) in delivered
                       and not (unit.get('item_customname') or '').strip()]
            names = next_storage_names([unit.get('item_customname') for unit in caskets], len(unnamed))
            for unit, name in zip(unnamed, names):
                answer = self.ratatoskr.rename_casket(steamid, unit.get('item_id'), name) or {}
                if answer.get('error'):
                    problems.append(f'{unit.get("item_id")}: {answer["error"]}')
                else:
                    named.append(name)
        except Exception as e:      # the purchase itself succeeded; naming is a follow-up
            problems.append(str(e))
        naming_error = ('could not name every new Storage Unit (name it in the Rename Storage Unit tab): '
                        + '; '.join(problems)) if problems else None
        with self._lock:        # the entry may already be in the purchase log, copied under this lock
            if named:
                result['named'] = named
            if naming_error:
                result['naming_error'] = naming_error
        if named:
            log.info('[STORAGE-SHOP] named new Storage Units on %s: %s', steamid, ', '.join(named))
        if naming_error:
            log.warning('[STORAGE-SHOP] %s: %s', steamid, naming_error)

    def _deliver_again(self, entry):
        self._set_job(total=1, phase=f'delivering again: {entry["account_name"]}')
        self._step(entry['steamid'], 'delivering')
        opened = False
        try:
            finalized, opened = self._finalize(entry['steamid'], entry['transaction_id'], False)
            if finalized.get('result') == GAME_COORDINATOR_OK:
                storage_units = (entry.get('entry') or STORAGE_UNIT_ENTRY) == STORAGE_UNIT_ENTRY
                with self._lock:        # the entry is in the purchase log, copied under this lock
                    entry.update(paid=True, ok=True, item_ids=[str(i) for i in finalized.get('itemIds') or []],
                                 storage_units_after=finalized.get('storageUnits') if storage_units else None,
                                 error='delivered on the second request')
                if storage_units and finalized.get('storageUnits') is not None:
                    self._remember(entry['steamid'], storage_units=finalized['storageUnits'], storage_units_at=time.time())
                self._mark_plan(entry['steamid'], STATUS_BOUGHT, entry.get('plan_id'))
                if storage_units:
                    self._name_new_units(entry['steamid'], entry)
            else:
                with self._lock:
                    entry['error'] = (f'delivering again failed (result {finalized.get("error") or finalized.get("result")})'
                                      ' — it MAY BE PAID: check the account (Steam → account → purchase history)')
        finally:
            if opened:
                self._close(entry['steamid'])
            self._record(entry)
            self._set_job(done=1, accounts_done=1,
                          result=self._purchase_result([purchase_outcome(entry)], entry.get('entry') or STORAGE_UNIT_ENTRY,
                                                       entry.get('item_name') or 'Storage Unit'))

    def _keep_page(self, page):
        try:
            with open(self.approval_page_path, 'w', encoding='utf-8') as handle:
                handle.write(page or '')
        except OSError as e:
            log.info('[STORAGE-SHOP] could not keep the approval page: %s', e)

    # ---- status --------------------------------------------------------------------------------

    def accounts_view(self, known, prices, rates):
        """Every account in the dashboard with what is last known about it (nothing is read
        from Steam here): wallet, Storage Units, price, how many the wallet covers, and what
        the page may do with the row (``can_buy``: selectable, max_quantity, can_buy_code)."""
        rows = []
        for steamid, name, country in self._accounts():
            entry = known.get(steamid) or {}
            currency = CURRENCY_CODES.get(entry.get('currency_id'))
            price = prices.get(currency) if currency else None
            balance = entry.get('balance')
            affordable = min(balance // price, MAX_QUANTITY_PER_ACCOUNT) if price and balance is not None else None
            # No price sheet read yet: unknown, not "not sold" (the check reads the sheet).
            sold = bool(currency is None or (currency in GAME_STORE_CURRENCIES and (not prices or price)))
            rows.append({'steamid': steamid, 'account_name': name, 'country': country, 'currency': currency,
                         'balance': balance, 'checked_at': entry.get('checked_at'),
                         # The wallet in US dollars, so wallets in different currencies sort together.
                         'balance_usd': to_usd(balance, currency, rates),
                         'storage_units': entry.get('storage_units'), 'storage_units_at': entry.get('storage_units_at'),
                         'unit_price': price, 'usd_per_unit': to_usd(price, currency, rates) if price else None,
                         'affordable': affordable, 'sold_in_currency': sold, **can_buy(sold, affordable)})
        return rows

    def quote(self, item, selection):
        """The order panel before the review, from what is last known (no Steam call): per
        account its total in the wallet currency, or about how much in US dollars while its
        wallet was never read; the totals (`complete` false: only the dollar estimate)."""
        wanted = parse_selection(selection)
        prices, _ = self._cached_prices(item)
        with self._lock:
            known = json.loads(json.dumps(self._known))
        rows = {row['steamid']: row for row in self.accounts_view(known, prices, self._rates())}
        list_price = (prices.get('USD') or 0) / 100
        lines = []
        for steamid, quantity in wanted.items():
            row = rows.get(steamid)
            if row is None:
                continue
            lines.append({'steamid': steamid, 'account_name': row['account_name'], 'quantity': quantity,
                          'currency': row['currency'],
                          'total': row['unit_price'] * quantity if row['unit_price'] else None,
                          'usd_estimate': round((row['usd_per_unit'] or list_price) * quantity, 2)})
        complete = all(line['total'] is not None for line in lines)
        by_currency = {}
        for line in lines if complete else []:
            by_currency[line['currency']] = by_currency.get(line['currency'], 0) + line['total']
        return {'lines': lines, 'units': sum(line['quantity'] for line in lines), 'complete': complete,
                'totals_by_currency': by_currency,
                'usd_estimate': round(sum(line['usd_estimate'] for line in lines), 2)}

    def job_status(self):
        with self._lock:
            return dict(self._job)

    def sheet_entries(self):
        """{entry: {ISO currency: minor units}} of the last price sheet read (a copy; no Steam call)."""
        with self._lock:
            return json.loads(json.dumps((self._prices or {}).get('entries') or {}))

    def exchange_rates(self):
        """{currency: units per US dollar} (Andvari's last fetch)."""
        return self._rates()

    def wallets(self):
        """[{steamid, account_name, currency}] for every account whose wallet currency is known
        from an earlier wallet check (no Steam call)."""
        with self._lock:
            known = json.loads(json.dumps(self._known))
        return [{'steamid': steamid, 'account_name': name,
                 'currency': CURRENCY_CODES.get((known.get(steamid) or {}).get('currency_id'))}
                for steamid, name, _ in self._accounts()
                if CURRENCY_CODES.get((known.get(steamid) or {}).get('currency_id'))]

    def item_view(self, item):
        """What the page shows about *item*: its name, list price and whether it can be bought."""
        prices, _ = self._cached_prices(item)
        view = {'entry': item, 'name': item, 'definition_index': None, 'usd_list_price': (prices.get('USD') or 0) / 100 or None,
                'max_usd_per_unit': max_usd_per_unit(item, (prices.get('USD') or 0) / 100),
                'for_sale': item not in NOT_FOR_SALE_ENTRIES, 'is_storage_unit': item == STORAGE_UNIT_ENTRY,
                'error': None}
        try:
            view['definition_index'], view['name'] = self._item(item)
        except Exception as e:
            view['error'] = str(e)
        return view

    def status(self, item=STORAGE_UNIT_ENTRY):
        """The page's state for buying *item* (a price sheet entry; default the Storage Unit)."""
        with self._lock:
            state = json.loads(json.dumps({'job': self._job, 'plan': self._plan, 'history': self._history,
                                           'known': self._known}))
        prices, fetched_at = self._cached_prices(item)
        accounts = self.accounts_view(state['known'], prices, self._rates())
        plan_id = (state['plan'] or {}).get('id')
        try:
            plan = self.describe_plan(plan_id) if plan_id else None
        except CommandError:        # replaced (and forgotten) since the copy above
            plan = None
        history = [{**entry, **purchase_outcome(entry)} for entry in state['history'][-100:][::-1]]
        return {'job': state['job'], 'plan': plan,
                'history': history,
                # Approved but not delivered: "Deliver again" is offered for each (can_deliver_again).
                'unclear_purchases': [entry for entry in history if entry['can_deliver_again']],
                'accounts': accounts, 'item': self.item_view(item),
                'statistics': shop_statistics(state['history'], item),
                'price_sheet': {'prices': prices, 'fetched_at': fetched_at},
                'plan_time_to_live_seconds': PLAN_TIME_TO_LIVE_SECONDS,
                'max_quantity_per_account': MAX_QUANTITY_PER_ACCOUNT,
                'storage_unit_capacity': STORAGE_UNIT_CAPACITY}
