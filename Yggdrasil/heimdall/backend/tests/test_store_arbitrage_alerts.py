"""Store Catalogue Arbitrage: the CSMoney (Trade) indicator, the Refresh button, the daily
refresh after "Get all items" and the Telegram post of profitable store items."""
import time

from notifications import telegram_visible_length
from store_arbitrage_service import (INDICATOR_MARKET, SIDE_INSTANT, SIDE_LISTING, StoreArbitrageService,
                                     alert_candidates, fit_alert, indicator_of, item_row, make_offer,
                                     pulse_link, render_alert, signal_percent)
from test_store_arbitrage_service import (FEES, FLICKSHOT, MARKETS, NAME_TAG, TODAY, FakeCatalogue, FakeDraupnir,
                                          FakeHuginn, FakeShop, _indexes)

CSMONEY = {'id': INDICATOR_MARKET, 'display': 'CSMoney (Trade)', 'balance': 'cs.money trade balance'}


def _row_with_csmoney(price):
    indexes = _indexes()
    indexes[(INDICATOR_MARKET, SIDE_INSTANT)] = {'Sticker | Flickshot': {'price': price, 'count': 22}}
    return item_row(FLICKSHOT, MARKETS + [CSMONEY], indexes, FEES, {}, {}, [], {}, {}, today=TODAY)


# ---- the indicator -----------------------------------------------------------------------------

def test_the_indicator_is_csmoney_trade_instant_offer_as_a_share_of_the_store_price():
    row = _row_with_csmoney(0.99)
    assert row['indicator']['pct_of_store'] == 100.0 and row['indicator']['signal']
    assert row['indicator']['count'] == 22 and row['indicator']['balance'] == 'cs.money trade balance'
    below = _row_with_csmoney(0.95)
    assert below['indicator']['pct_of_store'] == 96.0 and not below['indicator']['signal']


def test_the_signal_threshold_comes_from_the_settings():
    indexes = _indexes()
    indexes[(INDICATOR_MARKET, SIDE_INSTANT)] = {'Sticker | Flickshot': {'price': 0.95}}
    row = item_row(FLICKSHOT, MARKETS + [CSMONEY], indexes, FEES, {}, {}, [], {}, {}, today=TODAY, signal_pct=95)
    assert row['indicator']['signal']
    assert signal_percent({}) == 100.0 and signal_percent({'store_arbitrage_signal_pct': 90}) == 90.0
    assert signal_percent({'store_arbitrage_signal_pct': 'x'}) == 100.0
    assert signal_percent({'store_arbitrage_signal_pct': -5}) == 100.0


def test_no_csmoney_trade_offer_means_no_indicator():
    row = item_row(FLICKSHOT, MARKETS, _indexes(), FEES, {}, {}, [], {}, {}, today=TODAY)
    assert row['indicator'] is None
    assert indicator_of([], 0.99) is None
    # A suspicious CSMoney (Trade) offer never signals.
    offer = make_offer(INDICATOR_MARKET, 'CSMoney (Trade)', SIDE_INSTANT, 5.0, 0.0, 0.99, suspicious=True)
    assert indicator_of([offer], 0.99)['signal'] is False


def test_an_unconfirmed_item_never_signals():
    indexes = _indexes()
    indexes[(INDICATOR_MARKET, SIDE_INSTANT)] = {'Name Tag': {'price': 3.0}}
    row = item_row(NAME_TAG, MARKETS + [CSMONEY], indexes, FEES, {}, {}, [], {}, {}, today=TODAY)
    assert row['unconfirmed'] and row['indicator']['pct_of_store'] > 100 and not row['indicator']['signal']


# ---- the message --------------------------------------------------------------------------------

def test_alert_candidates_are_resellable_profitable_items_best_first():
    flickshot = _row_with_csmoney(0.99)                       # Steam listing 1.27 × 0.87 = 1.105 > 0.99
    name_tag = item_row(NAME_TAG, MARKETS, _indexes(), FEES, {}, {}, [], {}, {}, today=TODAY)   # unconfirmed
    found = alert_candidates([flickshot, name_tag])
    assert [c['row']['name'] for c in found] == ['Sticker | Flickshot']
    assert found[0]['sell']['market'] == 'Steam' and found[0]['sell']['side'] == SIDE_LISTING
    assert found[0]['cash'] is None            # Buff163 pays money but below the store price
    assert alert_candidates([flickshot], min_pct=20) == []   # 11.6 % < 20 %


def test_a_balance_payout_also_names_the_best_profitable_money_offer():
    indexes = _indexes()
    indexes[('Buff', SIDE_LISTING)] = {'Sticker | Flickshot': {'price': 1.05}}
    row = item_row(FLICKSHOT, MARKETS, indexes, FEES, {}, {}, [], {}, {}, today=TODAY)
    found = alert_candidates([row])
    assert found[0]['sell']['market'] == 'Steam' and found[0]['cash']['market'] == 'Buff'


def test_the_message_names_the_platform_the_price_and_links_the_steam_market_through_pulse():
    row = _row_with_csmoney(0.99)
    row['cheapest_wallet'] = {'usd': 0.968, 'currency': 'HKD', 'accounts': ['a']}
    plain, html = render_alert(alert_candidates([row]), '🛒 In-game store: 1 item sells for more than the store price')
    assert 'Sticker | Flickshot: in-game store $0.99 (about $0.97 in HKD)' in plain
    assert 'List on Steam at $1.27 → $1.10 after its 13% fee: +$0.11 (+11.6%) · paid in Steam wallet' in plain
    assert 'CSMoney (Trade) pays $0.99 instantly (100% of the store price): BUY signal' in plain
    steam = 'https://short-pulse.tradeon.space/short-link/CsGo/Steam/Sticker%20%7C%20Flickshot'
    assert pulse_link('Steam', 'Sticker | Flickshot') == steam
    assert f'<a href="{steam}">Steam Community Market</a>' in html
    assert f'List on <a href="{steam}">Steam</a> at' in html
    assert 'cannot be traded for 7 days' in plain
    assert 'hong' not in plain and "'a'" not in plain              # no account names in the message


def test_html_is_escaped_and_a_long_message_fits_one_telegram_message():
    row = _row_with_csmoney(0.99)
    row = {**row, 'name': 'Sticker | <Cat> & Dog'}
    _, html = render_alert(alert_candidates([row]), 'title')
    assert '&lt;Cat&gt; &amp; Dog' in html and '<Cat>' not in html
    many = alert_candidates([{**_row_with_csmoney(0.99), 'name': f'Sticker | Long name number {i:03d} ' + 'x' * 60}
                             for i in range(80)])
    plain, html = fit_alert(many, 'title')
    assert telegram_visible_length(html) <= 4096
    assert 'more in Store Catalogue → Arbitrage' in plain


# ---- the service: refresh, daily refresh, alerts -------------------------------------------------

class FakeCatalogueWithJob(FakeCatalogue):
    def __init__(self, read_at=1.0):
        self.read_at = read_at
        self.refreshes = 0
        self.job = {'running': False, 'started_at': None, 'error': None}

    def status(self):
        return {'items': super().status()['items'], 'read_at': self.read_at, 'job': dict(self.job)}

    def start_refresh(self):
        self.refreshes += 1
        self.read_at = time.time()
        self.job = {'running': False, 'started_at': 42.0, 'error': None}      # done at once
        return {'started': True, 'started_at': 42.0}


def _service(tmp_path, catalogue=None, sent=None):
    def notify(settings, plain, html=None):
        if sent is not None:
            sent.append((plain, html))
        return {'ok': True, 'channel': 'telegram'}
    return StoreArbitrageService(FakeHuginn(), catalogue or FakeCatalogue(), FakeShop(), FakeDraupnir(),
                                 history_path=str(tmp_path / 'history.json.gz'), today=lambda: TODAY,
                                 alert_state_path=str(tmp_path / 'alerts.json'), notify=notify)


SETTINGS = {'tradeon_token': 'token', 'telegram_bot_token': 'bot', 'telegram_chat_id': '1'}


def _warmed(service):
    service._indexes = {pair: (time.time(), index) for pair, index in _indexes().items()}
    for pair in service._sides(['Buff', 'Steam', 'LisSkins'], {}):
        service._indexes.setdefault(pair, (time.time(), {}))
    return service


def test_the_default_markets_add_the_indicator_and_every_market_with_buy_orders(tmp_path):
    service = _service(tmp_path)
    # FakeHuginn lists Buff, Steam (buy orders) and LisSkins; CSMoney (Trade) is not listed there.
    assert service.default_market_ids() == ['Buff', 'Steam', 'LisSkins']
    assert service.options()['default_markets'] == ['Buff', 'Steam', 'LisSkins']
    assert service.options()['indicator_market'] == INDICATOR_MARKET


def test_refresh_pulls_again_but_never_a_side_pulled_in_the_last_minute(tmp_path):
    service = _service(tmp_path)
    service._indexes = {('Buff', SIDE_LISTING): (time.time(), {}),           # just pulled: left alone
                        ('Steam', SIDE_LISTING): (time.time() - 120, {})}    # two minutes old: pulled again
    queued = []
    service._queue_warm = lambda token, pairs: queued.extend(pairs)
    result = service.refresh('token', ['Buff', 'Steam'])
    assert result['queued'] == 3
    assert sorted(queued) == [('Buff', SIDE_INSTANT), ('Steam', SIDE_INSTANT), ('Steam', SIDE_LISTING)]
    assert service.refresh('', ['Buff'])['error']


def test_the_board_carries_the_indicator_the_signal_threshold_and_the_price_age(tmp_path):
    service = _warmed(_service(tmp_path))
    board = service.board('token', ['Buff', 'Steam', 'LisSkins'], {'store_arbitrage_signal_pct': 90})
    assert board['indicator_market'] == INDICATOR_MARKET and board['signal_pct'] == 90
    assert board['prices_at'] and board['prices_at'] <= board['prices_newest_at'] <= time.time()
    assert 'signals' in board['summary']


def test_the_daily_post_lists_every_profitable_item_once_then_the_hourly_one_only_new_ones(tmp_path):
    sent = []
    service = _warmed(_service(tmp_path, sent=sent))
    first = service.send_alerts(SETTINGS, daily=True)
    assert first == {'sent': True, 'items': 1, 'error': None}
    assert 'Sticker | Flickshot' in sent[0][0] and '1 item sells' in sent[0][0]
    # The hourly check: still profitable, nothing new.
    assert service.send_alerts(SETTINGS, daily=False) == {'sent': False, 'items': 0, 'error': None}
    assert len(sent) == 1
    # It stops being profitable, a day passes, then it is profitable again: posted as new.
    state = service._load_alert_state()
    state['profitable'] = []
    state['posted']['Sticker | Flickshot'] -= 25 * 60 * 60
    import json
    (tmp_path / 'alerts.json').write_text(json.dumps(state))
    again = service.send_alerts(SETTINGS, daily=False)
    assert again['sent'] and 'just became profitable' in sent[1][0]


def test_the_first_hourly_check_only_notes_what_is_profitable(tmp_path):
    sent = []
    service = _warmed(_service(tmp_path, sent=sent))
    assert service.send_alerts(SETTINGS, daily=False) == {'sent': False, 'items': 0, 'error': None}
    assert sent == [] and service._load_alert_state()['profitable'] == ['Sticker | Flickshot']


def test_a_newly_profitable_item_is_not_posted_twice_in_a_day(tmp_path):
    sent = []
    service = _warmed(_service(tmp_path, sent=sent))
    import json
    (tmp_path / 'alerts.json').write_text(json.dumps({'profitable': []}))
    assert service.send_alerts(SETTINGS, daily=False)['sent']
    # It flickers out and back within the day: not posted again.
    (tmp_path / 'alerts.json').write_text(json.dumps({**service._load_alert_state(), 'profitable': []}))
    assert not service.send_alerts(SETTINGS, daily=False)['sent'] and len(sent) == 1


def test_alerts_respect_the_switch_the_floor_and_the_channel(tmp_path):
    sent = []
    service = _warmed(_service(tmp_path, sent=sent))
    assert service.send_alerts({**SETTINGS, 'store_arbitrage_alerts_enabled': False})['error'] == 'alerts are off'
    assert service.send_alerts({**SETTINGS, 'store_arbitrage_alerts_enabled': False}, force=True)['sent']
    assert service.send_alerts({'tradeon_token': 'token'})['error'].startswith('no Telegram bot')
    floor = service.send_alerts({**SETTINGS, 'store_arbitrage_alert_min_pct': 50})
    assert floor['sent'] and floor['items'] == 0 and 'nothing sells above the store price today' in sent[-1][0]


def test_a_failed_send_is_not_remembered(tmp_path):
    service = _warmed(_service(tmp_path))
    service._notify = lambda settings, plain, html=None: {'ok': False, 'error': 'Telegram 400'}
    assert service.send_alerts(SETTINGS) == {'sent': False, 'items': 1, 'error': 'Telegram 400'}
    assert service._load_alert_state() == {}


def test_prices_still_loading_send_nothing(tmp_path):
    sent = []
    service = _service(tmp_path, sent=sent)
    service._queue_warm = lambda token, pairs: None
    assert service.send_alerts(SETTINGS)['error'] == 'prices not ready (warming)' and sent == []


def test_the_daily_refresh_reads_the_price_sheet_pulls_every_market_then_posts(tmp_path):
    sent = []
    catalogue = FakeCatalogueWithJob(read_at=1.0)                         # read long ago
    service = _service(tmp_path, catalogue=catalogue, sent=sent)
    order = []
    real_refresh = service.refresh
    service.refresh = lambda token, ids=None, settings=None: order.append('markets') or real_refresh(token, ids, settings)
    service._daily_refresh(lambda: SETTINGS)
    assert catalogue.refreshes == 1 and order == ['markets']
    assert len(sent) == 1 and 'Sticker | Flickshot' in sent[0][0]
    # A sheet read in the last six hours is not read again.
    service._daily_refresh(lambda: SETTINGS)
    assert catalogue.refreshes == 1


def test_the_daily_refresh_reports_a_busy_price_sheet_and_still_prices_the_markets(tmp_path):
    catalogue = FakeCatalogueWithJob(read_at=1.0)
    catalogue.start_refresh = lambda: {'started': False, 'error': 'a purchase is already running'}
    service = _service(tmp_path, catalogue=catalogue)
    assert service._read_price_sheet() == 'store price sheet not read: a purchase is already running'
    queued = []
    service.refresh = lambda token, ids=None, settings=None: queued.append(token) or {'queued': 0}
    service.wait_idle = lambda timeout=0: True
    service._daily_refresh(lambda: SETTINGS)
    assert queued == ['token']


def test_only_one_daily_refresh_runs_at_a_time(tmp_path):
    service = _service(tmp_path)
    service._daily_running = True
    assert service.start_daily_refresh(lambda: SETTINGS) == 'store prices: a refresh is already running'


def test_wait_idle_returns_once_the_worker_is_done(tmp_path):
    service = _service(tmp_path)
    assert service.wait_idle(timeout=1, step=0.01)
    service._warming.add(('Buff', SIDE_LISTING))
    assert not service.wait_idle(timeout=0.05, step=0.01)


def test_listing_pairs_are_named_for_the_board(tmp_path):
    # The refresh route's market list is cleaned like the board's: unknown ids are dropped.
    service = _service(tmp_path)
    service._queue_warm = lambda token, pairs: None
    assert service.refresh('token', ['Unknown', 'LisSkins'])['markets'] == ['LisSkins']
    assert (('LisSkins', SIDE_LISTING) in service._sides(['LisSkins']))


def test_a_board_being_refreshed_says_so_and_counts_the_sides_left(tmp_path):
    service = _warmed(_service(tmp_path))
    service._warming.add(('Steam', SIDE_LISTING))
    board = service.board('token', ['Buff', 'Steam', 'LisSkins'])
    assert board['status'] == 'refreshing' and board['pulling'] == 1
    service._warming.clear()
    assert service.board('token', ['Buff', 'Steam', 'LisSkins'])['status'] == 'fresh'


def test_markets_say_whether_they_have_real_listings(tmp_path):
    service = _service(tmp_path)
    registry = service.huginn.market_registry()
    registry.append({'id': 'SkinSwapTrade', 'display': 'SkinSwap (Trade)', 'hasAutobuy': True, 'fee': 0,
                     'feeKnown': False, 'hasListings': True, 'listingsEstimated': True})
    registry.append({'id': 'SellOnly', 'display': 'Sell only', 'hasAutobuy': True, 'fee': 0,
                     'feeKnown': False, 'hasListings': False})
    service.huginn.market_registry = lambda settings=None: registry
    listings = {m['id']: m['listings'] for m in service.markets()}
    assert listings == {'Buff': True, 'Steam': True, 'LisSkins': True, 'SkinSwapTrade': False, 'SellOnly': False}


# ---- review fixes ----------------------------------------------------------------------------

def test_the_price_age_leaves_out_the_csfloat_buy_orders_refresh_never_pulls(tmp_path):
    service = _warmed(_service(tmp_path))
    service._indexes[('CsFloat', SIDE_INSTANT)] = (1.0, {})            # an old sweep entry
    pairs = service._sides(['Buff', 'Steam', 'LisSkins', 'CsFloat'])
    for pair in pairs:
        if pair != ('CsFloat', SIDE_INSTANT):
            service._indexes[pair] = (time.time(), service._indexes.get(pair, (0, {}))[1])
    board = service.board('token', ['Buff', 'Steam', 'LisSkins', 'CsFloat'])
    assert board['prices_at'] > time.time() - 60


def test_waiting_for_given_pairs_ignores_other_pulls(tmp_path):
    service = _service(tmp_path)
    service._warming.add(('Steam', SIDE_LISTING))                       # an open page's pull
    assert service.wait_idle([('Buff', SIDE_LISTING)], timeout=0.05, step=0.01)
    assert not service.wait_idle([('Steam', SIDE_LISTING)], timeout=0.05, step=0.01)


def test_switching_alerts_off_forgets_the_hourly_snapshot(tmp_path):
    sent = []
    service = _warmed(_service(tmp_path, sent=sent))
    service.send_alerts(SETTINGS, daily=False)                          # records the snapshot
    assert service._load_alert_state()['profitable'] == ['Sticker | Flickshot']
    service.send_alerts({**SETTINGS, 'store_arbitrage_alerts_enabled': False}, daily=False)
    assert 'profitable' not in service._load_alert_state()
    # Switched on again: the first check only records, nothing is posted as new.
    assert service.send_alerts(SETTINGS, daily=False)['sent'] is False and sent == []


def test_the_daily_post_goes_out_with_what_has_loaded(tmp_path):
    sent = []
    service = _warmed(_service(tmp_path, sent=sent))
    del service._indexes[('LisSkins', SIDE_LISTING)]                    # one side still missing
    service._queue_warm = lambda token, pairs: None
    assert service.board('token', None)['status'] == 'warming'
    assert service.send_alerts(SETTINGS, daily=True)['error'] == 'prices not ready (warming)'
    assert service.send_alerts(SETTINGS, daily=True, partial=True)['sent'] and len(sent) == 1


def test_skinswap_trade_pays_into_its_trade_balance(tmp_path):
    service = _service(tmp_path)
    registry = service.huginn.market_registry() + [
        {'id': 'SkinSwapTrade', 'display': 'SkinSwap (Trade)', 'hasAutobuy': True, 'fee': 0, 'feeKnown': False,
         'hasListings': True, 'listingsEstimated': True}]
    service.huginn.market_registry = lambda settings=None: registry
    skinswap = next(m for m in service.markets() if m['id'] == 'SkinSwapTrade')
    assert skinswap['balance'] == 'SkinSwap Trade balance'


def test_the_case_board_and_the_store_alerts_share_one_pulse_link():
    from huginn_service import HuginnService, pulse_short_link
    name = 'Sticker | Flickshot'
    assert pulse_link('Steam', name) == pulse_short_link('Steam', name)
    assert HuginnService._pulse_link(HuginnService.__new__(HuginnService), 'csfloat', name) == pulse_short_link('CsFloat', name)


def test_site_trade_balances_never_make_an_item_worth_a_message():
    # Live 2026-10-15: TradeIt (Trade) "paid" $1.84 in its trade balance for a $0.99 capsule that
    # Buff163 listed at $1.19.
    indexes = _indexes()
    tradeit = {'id': 'TradeItTrade', 'display': 'TradeIt (Trade)', 'balance': 'TradeIt trade balance'}
    indexes[('TradeItTrade', SIDE_INSTANT)] = {'Sticker | Flickshot': {'price': 1.84}}
    indexes[('Steam', SIDE_LISTING)] = {'Sticker | Flickshot': {'price': 1.0}}      # 0.87: a loss
    row = item_row(FLICKSHOT, MARKETS + [tradeit], indexes, {**FEES, 'TradeItTrade': 0.1}, {}, {}, [], {}, {},
                   today=TODAY)
    assert row['best']['market'] == 'TradeItTrade'          # the board still shows it, labelled
    assert alert_candidates([row]) == []


# ---- prices shared with Huginn's own pulls (the hourly Case Arbitrage refresh) ------------------

def _pulse_row(name, price, count=3):
    return {'itemName': {'marketHashName': name}, 'secondMarket': {'price': price, 'totalOffersCount': count}}


def _huginn(monkeypatch, rows):
    from huginn_service import HuginnService
    huginn = HuginnService(steam_service=None, ratatoskr_service=None)
    monkeypatch.setattr(huginn, '_post_tradeon', lambda url, token, body: rows)
    return huginn


def test_huginn_tells_listeners_which_side_a_pull_is(monkeypatch):
    huginn = _huginn(monkeypatch, [_pulse_row('Sticker | Flickshot', 0.8)])
    heard = []
    huginn.add_pull_listener(lambda market, side, items, started_at: heard.append((market, side, items, started_at)))
    before = time.time()
    huginn._pull_market('token', 'Buff', 'Sell')                         # lowest listing
    huginn._pull_market('token', 'Buff', 'Buy', unfiltered=True)         # buy orders
    huginn._pull_market('token', 'Buff', 'Buy')                          # a filtered buy-order pull: not ours
    huginn._pull_market('token', 'Buff', 'Sell')                         # cached: nothing pulled, nothing told
    huginn.flush_pull_listeners()
    assert [(m, s) for m, s, _, _ in heard] == [('Buff', 'listing'), ('Buff', 'instant')]
    assert heard[0][2][0]['secondMarket']['price'] == 0.8 and before <= heard[0][3] <= time.time()


def test_listeners_run_after_the_pull_is_cached_and_never_in_the_pulling_thread(monkeypatch):
    import threading
    huginn = _huginn(monkeypatch, [_pulse_row('Sticker | Flickshot', 0.8)])
    seen = []
    huginn.add_pull_listener(lambda market, side, items, started_at: seen.append(
        (threading.current_thread().name, ('Buff', 'Sell', False) in huginn._market_pull_cache)))
    huginn._pull_market('token', 'Buff', 'Sell')
    huginn.flush_pull_listeners()
    assert seen[0][0].startswith('huginn-pull-listener') and seen[0][1]


def test_the_case_arbitrage_and_valuation_pulls_go_through_one_pull_and_are_announced(monkeypatch):
    urls = []
    huginn = _huginn(monkeypatch, [])
    monkeypatch.setattr(huginn, '_post_tradeon', lambda url, token, body: urls.append(
        (url, body['secondMarketOptions']['secondMarketPriceType'])) or [_pulse_row('Sticker | Flickshot', 0.8)])
    heard = []
    huginn.add_pull_listener(lambda market, side, items, started_at: heard.append((market, side)))
    huginn._single_container_map('token', 'lisskins')
    huginn._single_container_map('token', 'tradeon')                      # TradeOnMarket's own side: not announced
    huginn._single_price_map('token', 'steam')
    huginn._single_price_map('token', 'lisskins')                         # cached from the Case Arbitrage pull
    huginn.flush_pull_listeners()
    assert heard == [('LisSkins', 'listing'), ('Steam', 'listing')]
    base = 'https://api-pulse.tradeon.space/api/table/counter-strike/TradeOnMarket/'
    # LisSkins pulled once for both features; the TradeOn side keeps its own (unchanged) request.
    assert urls == [(base + 'LisSkins/all', 'SellWithoutHold'), (base + 'Steam/all', 'Buy'), (base + 'Steam/all', 'Sell')]


def test_every_valuation_market_has_a_registry_id():
    from huginn_service import _MARKET_BY_ID, HuginnService
    assert set(HuginnService._PRICE_MARKETS) == set(HuginnService._PRICE_MARKET_IDS)
    for slug, market_id in HuginnService._PRICE_MARKET_IDS.items():
        assert _MARKET_BY_ID[market_id]['buy_type'] == HuginnService._PRICE_MARKETS[slug][1]
        assert HuginnService._PRICE_MARKETS[slug][0].endswith(f'/TradeOnMarket/{market_id}/all')


def test_a_failing_listener_or_an_odd_answer_never_breaks_the_pull(monkeypatch):
    huginn = _huginn(monkeypatch, [_pulse_row('Sticker | Flickshot', 0.8)])

    def broken(market, side, items, started_at):
        raise RuntimeError('boom')
    huginn.add_pull_listener(broken)
    assert huginn._single_price_map('token', 'buff') == {'Sticker | Flickshot': 0.8}
    monkeypatch.setattr(huginn, '_post_tradeon', lambda url, token, body: {'error': 'not a table'})
    huginn._announce_pull('Buff', 'Sell', False, {'error': 'not a table'}, time.time())   # ignored, no raise
    huginn.flush_pull_listeners()


def _store_on(huginn, tmp_path):
    return StoreArbitrageService(huginn, FakeCatalogue(), FakeShop(), FakeDraupnir(),
                                 history_path=str(tmp_path / 'history.json.gz'), today=lambda: TODAY,
                                 alert_state_path=str(tmp_path / 'alerts.json'))


def test_the_store_board_takes_store_items_from_huginns_pulls(tmp_path, monkeypatch):
    huginn = _huginn(monkeypatch, [_pulse_row('Sticker | Flickshot', 1.4), _pulse_row('AK-47 | Redline (Field-Tested)', 30)])
    service = _store_on(huginn, tmp_path)
    huginn._single_container_map('token', 'buff')                         # the Case Arbitrage refresh
    huginn.flush_pull_listeners()
    started_at, index = service._indexes[('Buff', SIDE_LISTING)]
    assert list(index) == ['Sticker | Flickshot'] and index['Sticker | Flickshot']['price'] == 1.4
    assert service._history['2026-10-04']['Sticker | Flickshot']['Buff:listing'] == 1.4


def test_an_older_download_never_replaces_newer_prices(tmp_path, monkeypatch):
    huginn = _huginn(monkeypatch, [_pulse_row('Sticker | Flickshot', 1.4)])
    service = _store_on(huginn, tmp_path)
    service._indexes[('Buff', SIDE_LISTING)] = (100.0, {'Sticker | Flickshot': {'price': 0.9}})
    service.take_pull('Buff', SIDE_LISTING, [_pulse_row('Sticker | Flickshot', 1.4)], started_at=50.0)
    assert service._indexes[('Buff', SIDE_LISTING)][0] == 100.0
    service.take_pull('Buff', SIDE_LISTING, [_pulse_row('Sticker | Flickshot', 1.4)], started_at=150.0)
    assert service._indexes[('Buff', SIDE_LISTING)] [1]['Sticker | Flickshot']['price'] == 1.4


def test_markets_the_board_does_not_show_are_not_kept(tmp_path, monkeypatch):
    huginn = _huginn(monkeypatch, [])
    service = _store_on(huginn, tmp_path)
    assert 'Skinport' not in service.default_market_ids()                 # no buy orders, not a usual market
    service.take_pull('Skinport', SIDE_LISTING, [_pulse_row('Sticker | Flickshot', 1.4)], started_at=time.time())
    assert ('Skinport', SIDE_LISTING) not in service._indexes and service._history == {}
