"""Store Catalogue: every item the Counter-Strike 2 in-game store sells, with its price.

Read-only: nothing here buys anything. The list comes from the game store's own price
sheet (``StoreGetUserData``), the same one Buy Storage Units reads for its Storage Unit
price. Every time that service reads the sheet it hands the whole sheet over
(``StorageShopService.on_price_sheet``), so the catalogue is refreshed for free; "Read
prices again" runs that service's ``price sheet`` job (one Ratatoskr login, never beside
a purchase).

The sheet names items by their internal name ("casket", "coupon - noisia_01");
Ratatoskr's item definitions turn those into English names (``store_item_names``).
A "coupon" is how the store sells something that becomes another item when bought: a
sticker, a music kit, a capsule. ``store_banner_layout`` lists what the store front
shows; keys are sold but not shown there (the game sells them when a case is opened).
"""
import json
import logging
import threading
import urllib.parse

from jsonio import atomic_write_json, read_json

log = logging.getLogger(__name__)

STATE_PATH = 'cache/store_catalogue.json'
MARKET_LISTING_URL = 'https://steamcommunity.com/market/listings/730/'
COUPON_PREFIX = 'coupon - '

# Names the item definitions get wrong or lack: the Sticker Slab's item name is the generic
# "Charm"; the first case key (definition 1203) has no item name at all.
NAME_OVERRIDES = {'sticker_display_case': 'Sticker Slab', 'Weapon Case Key': 'CS:GO Case Key'}
# Not on the Steam Community Market: no Market link.
NOT_ON_MARKET = {'casket', 'Game License', 'XpShopTicket1'}
# Entries whose own item definition says "cannot trade", for item names read before Ratatoskr
# reported that flag (``cannotTrade``); the game license is not an item at all.
CANNOT_TRADE = {'casket', 'Remove Keychain Tool Pack', 'XpShopTicket1', 'Game License'}

CATEGORY_TOOLS = 'Tools'
CATEGORY_KEYS = 'Case keys'
CATEGORY_STICKER_CAPSULES = 'Sticker capsules'
CATEGORY_STICKERS = 'Stickers'
CATEGORY_MUSIC_KIT_BOXES = 'Music kit boxes'
CATEGORY_MUSIC_KITS = 'Music kits'
CATEGORY_STATTRAK_MUSIC_KITS = 'StatTrak music kits'
CATEGORY_GRAFFITI = 'Graffiti boxes'
CATEGORY_PINS = 'Pin capsules'
CATEGORY_PATCHES = 'Patch packs'
CATEGORY_PASSES = 'Passes and licenses'
CATEGORY_OTHER = 'Other'
CATEGORY_ORDER = [CATEGORY_TOOLS, CATEGORY_KEYS, CATEGORY_STICKER_CAPSULES, CATEGORY_STICKERS,
                  CATEGORY_MUSIC_KIT_BOXES, CATEGORY_MUSIC_KITS, CATEGORY_STATTRAK_MUSIC_KITS,
                  CATEGORY_GRAFFITI, CATEGORY_PINS, CATEGORY_PATCHES, CATEGORY_PASSES, CATEGORY_OTHER]


# ---- pure helpers (unit-tested) ------------------------------------------------------------

def readable_name(entry):
    """A name for an entry the item definitions do not know (a newer item):
    "coupon - alrt_01_stattrak" → "Alrt 01 Stattrak"."""
    text = entry[len(COUPON_PREFIX):] if entry.startswith(COUPON_PREFIX) else entry
    return ' '.join(word.capitalize() for word in text.replace('_', ' ').split()) or entry


def category_of(entry, name, prefab):
    prefab, lowered = prefab or '', (name or '').lower()
    if entry in ('Game License', 'XpShopTicket1') or 'season_pass' in prefab:
        return CATEGORY_PASSES
    if 'weapon_case_key' in prefab or entry.lower().endswith((' key', '_key')):
        return CATEGORY_KEYS
    if 'csgo_tool' in prefab or entry == 'sticker_display_case':
        return CATEGORY_TOOLS
    if lowered.startswith('stattrak™ music kit |') or lowered.startswith('stattrak music kit |'):
        return CATEGORY_STATTRAK_MUSIC_KITS
    if lowered.startswith('music kit |'):
        return CATEGORY_MUSIC_KITS
    if lowered.startswith('sticker |'):
        return CATEGORY_STICKERS
    if 'music kit box' in lowered or 'musickit_capsule' in entry:
        return CATEGORY_MUSIC_KIT_BOXES
    if 'graffiti' in lowered or 'sprays' in entry:
        return CATEGORY_GRAFFITI
    if 'pins' in entry:
        return CATEGORY_PINS
    if 'patch' in entry:
        return CATEGORY_PATCHES
    if 'capsule' in lowered or 'sticker_capsule' in entry:
        return CATEGORY_STICKER_CAPSULES
    return CATEGORY_OTHER


def catalogue_items(sheet, definitions):
    """The catalogue's rows from a decoded price sheet. *definitions*: {internal name:
    {defIndex, name, prefab}} (entries it does not know get a readable name)."""
    store = (sheet or {}).get('store') or {}
    front = {str(key) for key in (store.get('store_banner_layout') or {})}
    rows = []
    for entry, data in (store.get('entries') or {}).items():
        if not isinstance(data, dict):
            continue
        prices = {currency: int(price) for currency, price in (data.get('prices') or {}).items()
                  if isinstance(price, int) and price > 0}
        known = (definitions or {}).get(entry) or {}
        name = NAME_OVERRIDES.get(entry) or known.get('name') or readable_name(entry)
        definition_index = known.get('defIndex')
        usd = prices.get('USD')
        cannot_trade = known.get('cannotTrade') if 'cannotTrade' in known else entry in CANNOT_TRADE
        rows.append({
            'entry': entry,
            'definition_index': definition_index,
            'name': name,
            'named': bool(known.get('name')) or entry in NAME_OVERRIDES,
            'category': category_of(entry, name, known.get('prefab')),
            'usd_cents': usd,
            'usd': round(usd / 100, 2) if usd else None,
            'currencies': len(prices),
            'on_store_front': definition_index is not None and str(definition_index) in front,
            # The item itself cannot be traded (its own "cannot trade"). Keys are not flagged: they
            # are tradable items, only the store's copies are not (since 2019), so the Arbitrage
            # tab leaves keys out by category.
            'cannot_trade': bool(cannot_trade or entry == 'Game License'),
            'market_url': (MARKET_LISTING_URL + urllib.parse.quote(name)
                           if entry not in NOT_ON_MARKET and (known.get('name') or entry in NAME_OVERRIDES)
                           else None),
        })
    return rows


def catalogue_summary(items):
    counts = {}
    for item in items:
        counts[item['category']] = counts.get(item['category'], 0) + 1
    return [{'category': category, 'count': counts[category]} for category in CATEGORY_ORDER if category in counts]


# ---- the service ---------------------------------------------------------------------------

class StoreCatalogueService:
    def __init__(self, ratatoskr_service, storage_shop_service, state_path=STATE_PATH):
        self.ratatoskr = ratatoskr_service
        self.storage_shop = storage_shop_service
        self.state_path = state_path
        self._lock = threading.Lock()
        self._state = read_json(state_path, default={}) or {}
        storage_shop_service.on_price_sheet = self.take_sheet

    def take_sheet(self, sheet, version, read_at):
        """Rebuild the catalogue from a freshly read price sheet."""
        entries = list((((sheet or {}).get('store') or {}).get('entries') or {}))
        answer = self.ratatoskr.store_item_names(entries) or {}
        definitions = answer.get('definitions') or {}
        if answer.get('error'):
            log.warning('[STORE-CATALOGUE] item names unavailable: %s', answer.get('error'))
            definitions = self._state.get('definitions') or {}      # the last names known
        items = catalogue_items(sheet, definitions)
        with self._lock:
            self._state = {'items': items, 'version': version, 'read_at': read_at, 'definitions': definitions}
            snapshot = json.loads(json.dumps(self._state))
        try:
            atomic_write_json(self.state_path, snapshot)
        except Exception as e:
            log.error('[STORE-CATALOGUE] could not save %s: %s', self.state_path, e)
        log.info('[STORE-CATALOGUE] %s items from price sheet version %s', len(items), version)

    def start_refresh(self):
        return self.storage_shop.start_price_sheet()

    def status(self):
        with self._lock:
            state = json.loads(json.dumps(self._state))
        job = self.storage_shop.job_status()
        items = state.get('items') or []
        return {'items': items, 'categories': catalogue_summary(items),
                'read_at': state.get('read_at'), 'version': state.get('version'),
                # The shared job: a price sheet read, or a Storage Unit check or purchase.
                'job': {key: job.get(key) for key in ('running', 'kind', 'phase', 'error', 'started_at', 'finished_at')}}
