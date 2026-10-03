"""Store Catalogue: every item of the game store's price sheet with its US dollar price."""
from store_catalogue_service import (CATEGORY_KEYS, CATEGORY_MUSIC_KIT_BOXES, CATEGORY_PASSES,
                                     CATEGORY_STATTRAK_MUSIC_KITS, CATEGORY_STICKER_CAPSULES,
                                     CATEGORY_STICKERS, CATEGORY_TOOLS, StoreCatalogueService,
                                     catalogue_items, catalogue_summary, category_of, readable_name)

# Shaped like the live sheet (2026-10-03): entries by internal name, prices in minor units,
# the store front by definition index (cases there link to the Market, they are not sold).
SHEET = {'store': {
    'store_banner_layout': {'1201': {'custom_format': 'single'}, '20188': {'custom_format': 'coupon'},
                            '7007': {'custom_format': 'double', 'market_link': 1}},
    'entries': {
        'casket': {'item_link': 'casket', 'prices': {'USD': 199, 'EUR': 175}},
        'community_35_key': {'item_link': 'community_35_key', 'prices': {'USD': 249}},
        'coupon - csgo10_sticker_capsule': {'prices': {'USD': 99}},
        'coupon - alrt_01_stattrak': {'prices': {'USD': 799}},
        'sticker_display_case': {'prices': {'USD': 49}},
        'XpShopTicket1': {'prices': {'USD': 1599}},
        'odd': 'not a section',
    },
}}
DEFINITIONS = {
    'casket': {'defIndex': 1201, 'name': 'Storage Unit', 'prefab': 'valve csgo_tool'},
    'community_35_key': {'defIndex': 7008, 'name': 'Fever Case Key', 'prefab': 'weapon_case_key'},
    'coupon - csgo10_sticker_capsule': {'defIndex': 20188, 'name': '10 Year Birthday Sticker Capsule',
                                        'prefab': 'coupon_csgo10_capsule_prefab'},
    'sticker_display_case': {'defIndex': 4000, 'name': 'Charm', 'prefab': 'valve csgo_tool'},
    'XpShopTicket1': {'defIndex': 1354, 'name': 'Armory Pass', 'prefab': 'valve season_pass'},
}


def _by_entry(items):
    return {item['entry']: item for item in items}


def test_items_carry_names_prices_and_the_store_front():
    items = _by_entry(catalogue_items(SHEET, DEFINITIONS))
    assert set(items) == {'casket', 'community_35_key', 'coupon - csgo10_sticker_capsule',
                          'coupon - alrt_01_stattrak', 'sticker_display_case', 'XpShopTicket1'}
    unit = items['casket']
    assert (unit['name'], unit['usd'], unit['usd_cents'], unit['currencies']) == ('Storage Unit', 1.99, 199, 2)
    assert unit['on_store_front'] and unit['market_url'] is None          # Storage Units are not on the Market
    key = items['community_35_key']
    assert key['category'] == CATEGORY_KEYS and not key['on_store_front']
    assert key['market_url'] == 'https://steamcommunity.com/market/listings/730/Fever%20Case%20Key'
    assert items['coupon - csgo10_sticker_capsule']['on_store_front']


def test_unknown_entries_get_a_readable_name_and_no_market_link():
    item = _by_entry(catalogue_items(SHEET, DEFINITIONS))['coupon - alrt_01_stattrak']
    assert (item['name'], item['named'], item['market_url']) == ('Alrt 01 Stattrak', False, None)
    assert readable_name('Name Tag') == 'Name Tag'


def test_the_first_case_key_is_named_by_hand():
    sheet = {'store': {'entries': {'Weapon Case Key': {'prices': {'USD': 249}}}}}
    item = catalogue_items(sheet, {'Weapon Case Key': {'defIndex': 1203, 'name': None, 'prefab': 'valve weapon_case_key'}})[0]
    assert (item['name'], item['category']) == ('CS:GO Case Key', CATEGORY_KEYS)
    assert item['market_url'].endswith('/CS%3AGO%20Case%20Key')


def test_the_sticker_slab_is_named_by_hand():
    item = _by_entry(catalogue_items(SHEET, DEFINITIONS))['sticker_display_case']
    assert (item['name'], item['category']) == ('Sticker Slab', CATEGORY_TOOLS)


def test_categories():
    assert category_of('casket', 'Storage Unit', 'valve csgo_tool') == CATEGORY_TOOLS
    assert category_of('Community Case Key 1', 'Winter Offensive Case Key', 'weapon_case_key') == CATEGORY_KEYS
    assert category_of('community_35_key', 'Community 35 Key', None) == CATEGORY_KEYS    # names unavailable
    assert category_of('coupon - x', 'Sticker | Bossy Burger', 'coupon_prefab') == CATEGORY_STICKERS
    assert category_of('coupon - x', 'StatTrak™ Music Kit | Noisia, Sharpened', 'coupon_prefab') \
        == CATEGORY_STATTRAK_MUSIC_KITS
    assert category_of('coupon - deluge_musickit_capsule', 'Deluge Music Kit Box', 'coupon_prefab') \
        == CATEGORY_MUSIC_KIT_BOXES
    assert category_of('coupon - halo_sticker_capsule', 'Halo Sticker Capsule', 'x') == CATEGORY_STICKER_CAPSULES
    assert category_of('Game License', 'Counter-Strike Game License', 'valve collectible_untradable_coin') \
        == CATEGORY_PASSES


def test_summary_counts_each_category_in_order():
    summary = catalogue_summary(catalogue_items(SHEET, DEFINITIONS))
    assert summary[0] == {'category': CATEGORY_TOOLS, 'count': 2}
    assert sum(row['count'] for row in summary) == 6


class FakeRatatoskr:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def store_item_names(self, names):
        self.asked.append(list(names))
        return self.answer


class FakeShop:
    on_price_sheet = None

    def job_status(self):
        return {'running': False, 'kind': 'price sheet', 'error': None, 'account': 'ignored'}

    def start_price_sheet(self):
        return {'started': True}


def test_the_service_takes_each_sheet_and_keeps_it(tmp_path):
    shop = FakeShop()
    ratatoskr = FakeRatatoskr({'success': True, 'definitions': DEFINITIONS})
    service = StoreCatalogueService(ratatoskr, shop, state_path=str(tmp_path / 'catalogue.json'))
    assert shop.on_price_sheet == service.take_sheet
    shop.on_price_sheet(SHEET, 12, 1000.0)
    status = service.status()
    assert (len(status['items']), status['version'], status['read_at']) == (6, 12, 1000.0)
    assert 'account' not in status['job']
    reloaded = StoreCatalogueService(ratatoskr, FakeShop(), state_path=str(tmp_path / 'catalogue.json'))
    assert len(reloaded.status()['items']) == 6


def test_without_ratatoskr_names_the_last_known_names_are_kept(tmp_path):
    shop = FakeShop()
    path = str(tmp_path / 'catalogue.json')
    StoreCatalogueService(FakeRatatoskr({'definitions': DEFINITIONS}), shop, state_path=path).take_sheet(SHEET, 1, 1.0)
    service = StoreCatalogueService(FakeRatatoskr({'error': 'Failed to connect to Ratatoskr'}), shop, state_path=path)
    service.take_sheet(SHEET, 2, 2.0)
    assert _by_entry(service.status()['items'])['casket']['name'] == 'Storage Unit'


def test_items_say_whether_they_cannot_be_traded():
    definitions = {**DEFINITIONS, 'casket': {**DEFINITIONS['casket'], 'cannotTrade': True},
                   'coupon - csgo10_sticker_capsule': {**DEFINITIONS['coupon - csgo10_sticker_capsule'],
                                                       'cannotTrade': False}}
    items = _by_entry(catalogue_items(SHEET, definitions))
    assert items['casket']['cannot_trade'] and not items['coupon - csgo10_sticker_capsule']['cannot_trade']
    # Names read before Ratatoskr sent the flag: the known untradable entries are still marked.
    old = _by_entry(catalogue_items(SHEET, DEFINITIONS))
    assert old['casket']['cannot_trade'] and old['XpShopTicket1']['cannot_trade']
    assert not old['community_35_key']['cannot_trade']        # keys: left out by category instead
