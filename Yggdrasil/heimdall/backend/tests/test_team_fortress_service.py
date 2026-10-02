"""Team Fortress 2 case drops: release detection, Market pricing in any wallet
currency, and the auto-sell flow (list, then confirm only those listings)."""
import json
import time

import pytest

import team_fortress_service
from team_fortress_service import (TeamFortressService, US_DOLLAR_WALLET, buyer_pays_for, detect_new_cases,
                                   listing_price, parse_price_minor_units, parse_wallet_info, sellable_items,
                                   seller_receives, MAX_SELL_TRIES, SELL_AFTER_STOP_SECONDS)

KRONER_WALLET = {'fee_minimum': 10, 'fee_base': 0, 'fee_percent': 0.05, 'publisher_percent': 0.10}

BLOG_POST = """An update to Team Fortress 2 has been released.<br/>
<b>Scream Fortress XVIII has arrived!</b>
<ul>
<li>Featuring 5 new community maps: HolyHell, Scarypass
<li>Added the Haunted Hoard Case<ul>
<li>Contains 23 new community-created cosmetic items that make up the Haunted Hoard Collection</ul>
<li>Added 5 new community-contributed taunts to the Mann Co. Store
<li>All cosmetic and taunt Cases will grant Halloween 2026 Unusual effects
</ul>
<b>General</b>
<ul>
<li>Fixed a case where the HUD was wrong
<li>Fixed Summer 2024 Cosmetic Key and Summer 2024 Cosmetic Key backpack images
</ul>"""

ANNOUNCEMENT_POST = """[p]The major changes include:[/p][list]
[*]Added the Summer 2027 Cosmetic Case and the Summer 2027 War Paint Case[/*]
[*]Introducing the Smissmas 2027 Crate[/*][/list]"""


# ---- pure helpers -------------------------------------------------------------------

def test_detects_the_case_in_a_blog_post_and_nothing_else():
    assert detect_new_cases(BLOG_POST) == ['Haunted Hoard Case']


def test_detects_several_cases_and_crates_in_bbcode():
    assert detect_new_cases(ANNOUNCEMENT_POST) == ['Summer 2027 Cosmetic Case', 'Summer 2027 War Paint Case',
                                                   'Smissmas 2027 Crate']


@pytest.mark.parametrize('text', ['Fixed a case where players crashed', 'Added a new map',
                                  'Added 5 new taunts to the Mann Co. Store', ''])
def test_no_release_without_a_named_case(text):
    assert detect_new_cases(text) == []


@pytest.mark.parametrize('text, expected', [
    ('$0.05', 5), ('$1,234.56', 123456), ('0,49 kr', 49), ('1.234,56€', 123456),
    ('₩ 1,000', 100000), ('Rp 1 000', 100000), ('¥ 7', 700), ('12,5 TL', 1250), (None, None), ('free', None),
])
def test_parse_price_minor_units(text, expected):
    assert parse_price_minor_units(text) == expected


def test_fees_match_steam_for_dollars_and_kroner():
    assert buyer_pays_for(2, US_DOLLAR_WALLET) == 4             # 2 + 1 + 1
    assert buyer_pays_for(42, KRONER_WALLET) == 62               # what Steam showed: 42 + 10 + 10
    assert seller_receives(48, KRONER_WALLET) == 28
    assert seller_receives(5000, US_DOLLAR_WALLET) == 4349           # 4349 + 217 + 434
    assert seller_receives(2, US_DOLLAR_WALLET) == 0


def test_listing_price_undercuts_by_one_and_never_lands_above_the_target():
    assert listing_price(5) == (4, 2)
    assert listing_price(49, KRONER_WALLET) == (48, 28)
    for lowest in range(3, 400):
        buyer_pays, receives = listing_price(lowest)
        assert buyer_pays <= max(3, lowest - 1) and buyer_pays_for(receives, US_DOLLAR_WALLET) == buyer_pays
    assert listing_price(None) is None
    assert listing_price(21, KRONER_WALLET) is None              # the fees would eat it all


def test_parse_wallet_info():
    page = ('<script>var g_rgWalletInfo = {"wallet_currency":9,"wallet_fee_minimum":"10","wallet_fee_base":"0",'
            '"wallet_fee_percent":"0.05","wallet_publisher_fee_percent_default":"0.10"};</script>')
    assert parse_wallet_info(page) == {'currency': 9, **KRONER_WALLET}
    assert parse_wallet_info('<html></html>') is None


def inventory(*items):
    """[(assetid, name, marketable)] -> a Steam inventory answer."""
    return {'assets': [{'assetid': assetid, 'classid': str(index), 'instanceid': '0'}
                       for index, (assetid, _, _) in enumerate(items)],
            'descriptions': [{'classid': str(index), 'instanceid': '0', 'market_hash_name': name,
                              'marketable': marketable} for index, (_, name, marketable) in enumerate(items)]}


def test_sellable_items_only_named_and_marketable():
    answer = inventory(('1', 'Haunted Hoard Case', 1), ('2', 'Haunted Hoard Case', 0), ('3', 'Mann Co. Cap', 1),
                       ('4', 'haunted hoard case', 1))
    assert sellable_items(answer, ['Haunted Hoard Case']) == [('1', 'Haunted Hoard Case'), ('4', 'haunted hoard case')]
    assert sellable_items(answer, []) == []


# ---- fakes ----------------------------------------------------------------------------

class FakeSettings:
    def __init__(self, **values):
        self.values = {'team_fortress_sell_items': ['Haunted Hoard Case'], 'team_fortress_accounts': [],
                       'team_fortress_auto_sell_enabled': True, 'team_fortress_play_on_release': True,
                       'team_fortress_ring_on_release': True, **values}

    def get_settings(self):
        return dict(self.values)

    def save_settings(self, updates):
        self.values.update(updates)
        return True


class FakeResponse:
    def __init__(self, payload=None, status=200, text=''):
        self.payload, self.status_code, self.text = payload, status, text
        self.ok = status < 400

    def raise_for_status(self):
        if not self.ok:
            raise OSError(f'HTTP {self.status_code}')

    def json(self):
        if self.payload is None:
            raise ValueError('no json')
        return self.payload


class FakeHttp:
    def __init__(self):
        self.news = []
        self.inventories = {}
        self.lowest = {1: '$0.05', 9: '0,49 kr'}
        self.sell_answer = {'success': True, 'requires_confirmation': 1}
        self.calls = []

    def get(self, url, params=None, cookies=None, timeout=None, headers=None):
        self.calls.append(('GET', url, params))
        if 'GetNewsForApp' in url:
            return FakeResponse({'appnews': {'newsitems': self.news}})
        if '/inventory/' in url:
            return FakeResponse(self.inventories.get(url.split('/')[4], inventory()))
        if 'priceoverview' in url:
            lowest = self.lowest[params['currency']]
            return FakeResponse({'success': True, 'lowest_price': lowest} if lowest else {'success': False})
        if url.endswith('/market/'):
            return FakeResponse(text='g_rgWalletInfo = {"wallet_currency":9,"wallet_fee_minimum":"10"};')
        raise AssertionError(url)

    def post(self, url, data=None, cookies=None, timeout=None, headers=None):
        self.calls.append(('POST', url, data))
        return FakeResponse(dict(self.sell_answer))

    def sells(self):
        return [data for method, url, data in self.calls if method == 'POST']


class FakeSteam:
    def __init__(self):
        self.confirmations = []
        self.accepted = []

    def web_session_cookie_for(self, steamid):
        return {'sessionid': 'session-' + steamid, 'steamLoginSecure': 'token'}

    def ensure_fresh_session(self, steamid):
        return {'ok': True}

    def get_confirmations(self, steamid):
        return {'success': True, 'confirmations': list(self.confirmations)}

    def act_on_confirmations_batch(self, steamid, items, operation):
        self.accepted.append((steamid, list(items), operation))
        return {'success': True}


class FakeAsf:
    enabled = True

    def __init__(self):
        self.started = []
        self.mode = {'active': True, 'stopped_at': None, 'accounts': [
            {'steamid': '1', 'account_name': 'alpha', 'in_mode': True, 'licensed': True},
            {'steamid': '2', 'account_name': 'bravo', 'in_mode': False, 'licensed': True}]}
        self.currencies = {'1': 1, '2': 9}

    def start_team_fortress(self, steamids=None, reason='manual'):
        self.started.append((steamids, reason))
        return {'success': True, 'accounts': 21}

    def team_fortress_status(self):
        return json.loads(json.dumps(self.mode))

    def wallet_currency(self, steamid):
        return self.currencies.get(steamid)


class FakeCaller:
    def __init__(self):
        self.rings = []

    def ring(self, message=None):
        self.rings.append(message)

    def status(self):
        return {'configured': True}


@pytest.fixture
def world(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(team_fortress_service, 'send_notification', lambda settings, text: sent.append(text))
    http, steam, asf, caller = FakeHttp(), FakeSteam(), FakeAsf(), FakeCaller()
    settings = FakeSettings()
    service = TeamFortressService(settings, steam, asf, caller, state_path=str(tmp_path / 'state.json'),
                                  http=http, sleep=lambda seconds: None)
    return service, http, steam, asf, caller, settings, sent


def post(gid, date, contents, title='Team Fortress 2 Update Released'):
    return {'gid': gid, 'date': date, 'contents': contents, 'title': title, 'url': f'https://example/{gid}'}


# ---- release watcher -------------------------------------------------------------------

def test_first_check_is_a_baseline_then_a_release_plays_rings_and_joins_the_sell_list(world):
    service, http, _, asf, caller, settings, sent = world
    settings.values['team_fortress_sell_items'] = []
    http.news = [post('1', 100, BLOG_POST)]
    assert service.check_news()['baseline'] is True
    assert asf.started == [] and sent == []
    http.news.append(post('2', 200, ANNOUNCEMENT_POST))
    result = service.check_news()
    assert result['released'] == ['Summer 2027 Cosmetic Case', 'Summer 2027 War Paint Case', 'Smissmas 2027 Crate']
    assert asf.started == [(None, 'release: Summer 2027 Cosmetic Case, Summer 2027 War Paint Case, '
                                  'Smissmas 2027 Crate')]
    assert settings.values['team_fortress_sell_items'] == result['released']
    assert len(sent) == 1 and 'Playing Team Fortress 2 on 21 accounts' in sent[0]
    assert caller.rings == sent
    assert service.check_news()['released'] == []            # same post: nothing again
    http.news.append(post('3', 300, ANNOUNCEMENT_POST))      # the same release posted on another feed
    assert service.check_news()['released'] == [] and len(asf.started) == 1


def test_release_uses_the_chosen_accounts_and_respects_the_switches(world):
    service, http, _, asf, caller, settings, sent = world
    settings.values.update(team_fortress_accounts=['7', '8'], team_fortress_ring_on_release=False)
    http.news = [post('1', 100, 'nothing')]
    service.check_news()
    http.news.append(post('2', 200, BLOG_POST))
    service.check_news()
    assert asf.started[0][0] == ['7', '8'] and caller.rings == [] and len(sent) == 1
    settings.values['team_fortress_play_on_release'] = False
    http.news.append(post('3', 300, ANNOUNCEMENT_POST))
    service.check_news()
    assert len(asf.started) == 1 and 'was not started' in sent[-1]


def test_news_fetch_error_is_reported(world):
    service, http, *_ = world
    http.get = lambda *args, **kwargs: (_ for _ in ()).throw(OSError('offline'))
    assert service.check_news() == {'ok': False, 'error': 'offline'}
    assert service.status()['watch']['error'] == 'offline'


# ---- auto-sell -------------------------------------------------------------------------------

def test_sells_each_case_one_cent_under_and_confirms_only_those_listings(world):
    service, http, steam, *_, sent = world
    http.inventories['1'] = inventory(('11', 'Haunted Hoard Case', 1), ('12', 'Haunted Hoard Case', 1),
                                      ('13', 'Mann Co. Cap', 1))
    steam.confirmations = [   # Steam's shape: the price is the headline, the item the summary
        {'id': 'a', 'nonce': 'n1', 'type': 3, 'headline': 'Selling for $0.04 USD', 'summary': ['Haunted Hoard Case']},
        {'id': 'b', 'nonce': 'n2', 'type': 3, 'headline': 'Selling for $9.00 USD', 'summary': ['AK-47 | Redline']},
        {'id': 'c', 'nonce': 'n3', 'type': 2, 'headline': 'Trade', 'summary': ['Haunted Hoard Case']},  # a trade
    ]
    summary = service.sell_account('1', 'alpha')
    assert summary['listed'] == 2 and summary['confirmed'] == 1 and summary['error'] is None
    assert [(data['assetid'], data['price'], data['appid'], data['contextid'], data['sessionid'])
            for data in http.sells()] == [('11', 2, 440, 2, 'session-1'), ('12', 2, 440, 2, 'session-1')]
    assert steam.accepted == [('1', [('a', 'n1')], 'allow')]
    assert 'listed 2 × Haunted Hoard Case on alpha' in sent[-1]
    assert service.sell_account('1', 'alpha')['listed'] == 0                # never listed twice


def test_sells_in_the_wallet_currency_with_its_fee_rules(world):
    service, http, *_ = world
    http.inventories['2'] = inventory(('21', 'Haunted Hoard Case', 1))
    service.sell_account('2', 'bravo')
    assert [data['price'] for data in http.sells()] == [28]                  # 0,48 kr for the buyer
    assert service.status()['sell']['sales'][0]['buyer_pays'] == 48
    prices = [params for method, url, params in http.calls if 'priceoverview' in url]
    assert prices[0]['currency'] == 9 and prices[0]['appid'] == 440


def test_unknown_wallet_currency_is_an_error_not_a_dollar_guess(world):
    service, http, _, asf, *_ = world
    asf.currencies = {}
    http.inventories['1'] = inventory(('11', 'Haunted Hoard Case', 1))
    summary = service.sell_account('1', 'alpha')
    assert http.sells() == [] and 'wallet currency unknown' in summary['error']


def test_a_refused_listing_is_retried_a_few_times_only(world):
    service, http, *_ = world
    http.sell_answer = {'success': False, 'message': 'There was a problem listing your item.'}
    http.inventories['1'] = inventory(('11', 'Haunted Hoard Case', 1))
    for _ in range(MAX_SELL_TRIES + 2):
        service.sell_account('1', 'alpha')
    assert len(http.sells()) == MAX_SELL_TRIES
    assert service.status()['sell']['sales'][0]['error'] == 'There was a problem listing your item.'


def test_empty_sell_list_reads_nothing(world):
    service, http, _, _, _, settings, _ = world
    settings.values['team_fortress_sell_items'] = []
    assert service.sell_account('1', 'alpha')['error'] == 'sell list is empty'
    assert http.calls == []


def test_accounts_watched_while_playing_and_for_a_while_after(world, monkeypatch):
    service, _, _, asf, *_ = world
    asf.mode['since'] = 500.0
    assert service._accounts_to_sell() == ([('1', 'alpha')], 500.0)
    asf.mode.update(active=False, stopped_at=time.time() - 60)
    asf.mode['accounts'][0]['chosen'] = True                  # was in the mode; bravo was not
    assert service._accounts_to_sell() == ([('1', 'alpha')], 500.0)
    asf.mode['stopped_at'] = time.time() - SELL_AFTER_STOP_SECONDS - 1
    assert service._accounts_to_sell() == ([], 500.0)


def test_sell_step_reads_one_overdue_inventory_at_a_time(world):
    service, http, *_ = world
    http.inventories['1'] = inventory(('11', 'Haunted Hoard Case', 1))
    assert service.sell_step()['steamid'] == '1'
    assert service.sell_step() is None                       # read just now: not due yet
    assert len(http.sells()) == 1


def test_state_survives_a_reload(world):
    service, http, steam, asf, caller, settings, _ = world
    http.inventories['1'] = inventory(('11', 'Haunted Hoard Case', 1))
    service.sell_account('1', 'alpha')
    reloaded = TeamFortressService(settings, steam, asf, caller, state_path=service.state_path,
                                   http=http, sleep=lambda seconds: None)
    assert reloaded.sell_account('1', 'alpha')['listed'] == 0
    assert reloaded.status()['sell']['sales'][0]['assetid'] == '11'


# ---- review fixes ----------------------------------------------------------------------

@pytest.mark.parametrize('text', [
    'Added Halloween 2026 Unusual effects to the Scream Fortress XVII War Paint Case',
    'Added the Mann Co. Supply Crate Key to the Mann Co. Store',
    'Fixed a crash. Added missing item to the Gargoyle Case',
    'Fixed the Haunted Hoard Case not being added',
])
def test_old_cases_mentioned_in_an_update_are_not_releases(text):
    assert team_fortress_service.detect_release(text) == ([], [])


@pytest.mark.parametrize('text, names', [
    ('We have added the Haunted Hoard Case', ['Haunted Hoard Case']),
    ("We've added the Haunted Hoard Case", ['Haunted Hoard Case']),
    ('Added The Haunted Hoard Case', ['Haunted Hoard Case']),
    ('added the Haunted Hoard Case', ['Haunted Hoard Case']),
])
def test_release_wording_variants(text, names):
    assert detect_new_cases(text) == names


def test_plural_release_plays_and_alerts_without_a_sell_list_name(world):
    service, http, _, asf, _, settings, sent = world
    settings.values['team_fortress_sell_items'] = []
    http.news = [post('1', 100, 'nothing')]
    service.check_news()
    http.news.append(post('2', 200, '<li>Added the Summer 2027 Cosmetic and War Paint Cases'))
    result = service.check_news()
    assert result['released'] == [] and result['unnamed'] == ['Added the Summer 2027 Cosmetic and War Paint Cases']
    assert len(asf.started) == 1 and settings.values['team_fortress_sell_items'] == []
    assert 'Add the exact case names' in sent[0]


def test_press_feeds_are_ignored(world):
    service, http, _, asf, *_ = world
    http.news = [post('1', 100, 'nothing')]
    service.check_news()
    http.news.append({**post('2', 200, BLOG_POST), 'feedname': 'PlayGround.ru'})
    assert service.check_news()['released'] == [] and asf.started == []


def test_only_drops_since_the_mode_started_are_sold(world):
    service, http, *_ = world
    http.inventories['1'] = inventory(('100', 'Haunted Hoard Case', 1))          # held before the mode
    service.sell_account('1', 'alpha', mode_since=400.0)       # an earlier mode read it ...
    assert len(http.sells()) == 1                              # ... and sold it (no earlier read: floor 0)
    http.calls.clear()
    service._state['attempted'].clear()
    # A new mode: the case already in alpha (id 100) is held; bravo's first read
    # already holds the drop it got at launch (id 105): sold, unlike a per-account baseline.
    http.inventories['2'] = inventory(('105', 'Haunted Hoard Case', 1))
    assert service.sell_account('1', 'alpha', mode_since=500.0)['listed'] == 0
    assert service.sell_account('2', 'bravo', mode_since=500.0)['listed'] == 1
    assert [data['assetid'] for data in http.sells()] == ['105']
    assert service.sell_account('1', 'alpha', mode_since=500.0, force=True)['listed'] == 1   # forced: all


def test_not_tradable_drops_are_counted_not_listed(world):
    service, http, *_ = world
    http.inventories['1'] = inventory(('11', 'Haunted Hoard Case', 0), ('12', 'Haunted Hoard Case', 0))
    summary = service.sell_account('1', 'alpha')
    assert summary['listed'] == 0 and summary['not_tradable'] == 2 and http.sells() == []
    assert service.status()['sell']['inventories']['1']['not_tradable'] == 2


def test_our_own_listing_is_not_undercut(world):
    service, http, *_ = world
    http.inventories['1'] = inventory(('11', 'Haunted Hoard Case', 1))
    service.sell_account('1', 'alpha')                       # $0.05 lowest -> listed at $0.04
    http.lowest[1] = '$0.04'                                 # the lowest is now our own listing
    service._prices.clear()
    http.inventories['1'] = inventory(('12', 'Haunted Hoard Case', 1))
    service.sell_account('1', 'alpha')
    assert [data['price'] for data in http.sells()] == [2, 2]                     # both at $0.04
    assert [sale['buyer_pays'] for sale in service.status()['sell']['sales']] == [4, 4]


def test_missing_price_is_not_a_try_and_not_cached(world):
    service, http, *_ = world
    http.inventories['1'] = inventory(('11', 'Haunted Hoard Case', 1))
    http.lowest[1] = None
    for _ in range(MAX_SELL_TRIES + 1):
        summary = service.sell_account('1', 'alpha')
    assert 'waiting for a Market price' in summary['error'] and http.sells() == []
    http.lowest[1] = '$0.90'                                 # the first listing appears
    assert service.sell_account('1', 'alpha')['listed'] == 1


def test_rate_limit_pauses_all_reads(world, monkeypatch):
    service, http, *_ = world
    real_get = http.get

    def limited(url, params=None, cookies=None, timeout=None, headers=None):
        if '/inventory/' in url:
            return FakeResponse({}, status=429)
        return real_get(url, params, cookies, timeout, headers)
    http.get = limited
    assert '429' in service.sell_account('1', 'alpha')['error']
    service._state['inventories'] = {}
    assert service.sell_step() is None                       # cooling down: nothing read
    assert service.start_sell_now()['started'] is False
    assert service.status()['sell']['rate_limited_until']


def test_sell_now_refused_while_a_sweep_runs(world):
    service, *_ = world
    with service._sell_lock:
        assert service.start_sell_now() == {'started': False, 'error': 'a sweep is already running'}


def test_mode_stops_by_itself_after_the_configured_hours(world):
    service, _, _, asf, _, settings, sent = world
    stopped = []
    asf.stop_team_fortress = lambda: stopped.append(True)
    settings.values['team_fortress_auto_stop_hours'] = 24
    asf.mode['since'] = time.time() - 23 * 3600
    assert service._auto_stop() is False
    asf.mode['since'] = time.time() - 25 * 3600
    assert service._auto_stop() is True and stopped and 'stopped playing after 24 hours' in sent[-1]
    settings.values['team_fortress_auto_stop_hours'] = 0
    assert service._auto_stop() is False


def test_confirmation_matching_is_exact_and_knows_type_twelve():
    confirmations = [
        {'id': '1', 'nonce': 'a', 'type': 3, 'headline': 'Selling for $0.10 USD', 'summary': ['Scout']},
        {'id': '2', 'nonce': 'b', 'type': 3, 'headline': 'Selling for $5.00 USD', 'summary': ['Scout Hat']},
        {'id': '3', 'nonce': 'c', 'type': 12, 'type_name': 'Market Listing', 'summary': [' scout ']},
        {'id': '4', 'nonce': 'd', 'type': 12, 'type_name': 'Something else', 'summary': ['Scout']},
        {'id': '5', 'nonce': 'e', 'type': 2, 'summary': ['Scout']},
    ]
    assert team_fortress_service.matching_listing_confirmations(confirmations, ['Scout']) == [('1', 'a'), ('3', 'c')]
