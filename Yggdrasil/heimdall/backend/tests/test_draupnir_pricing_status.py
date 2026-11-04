"""Draupnir's live-price status: the backend decides the note and when to ask again."""
import threading
import time

from draupnir_service import (
    PRICE_POLL_SECONDS, PRICE_SLOW_AFTER_SECONDS, PRICE_SLOW_POLL_SECONDS, pricing_status,
)
from huginn_service import HuginnService


def test_refreshing_asks_again_soon_then_slower():
    fast = pricing_status('refreshing', False, refreshing_seconds=1)
    assert fast['poll_after_seconds'] == PRICE_POLL_SECONDS
    assert fast['pricing_note'] == 'Fetching live prices…' and not fast['pricing_warning']
    slow = pricing_status('refreshing', True, refreshing_seconds=PRICE_SLOW_AFTER_SECONDS)
    assert slow['poll_after_seconds'] == PRICE_SLOW_POLL_SECONDS
    assert 'longer than usual' in slow['pricing_note']


def test_final_states_stop_asking_and_warn():
    for status in ('no_token', 'inactive', 'error'):
        answer = pricing_status(status, False, retry_seconds=120)
        assert answer['poll_after_seconds'] is None
        assert answer['pricing_warning'] and answer['pricing_note']
        assert answer['pricing'] == status and answer['priced'] is False
    assert 'tries again in 2 minutes' in pricing_status('error', False, retry_seconds=120)['pricing_note']
    assert 'last prices' in pricing_status('error', True)['pricing_note']


def test_fresh_says_nothing():
    answer = pricing_status('fresh', True)
    assert answer['pricing_note'] is None and answer['poll_after_seconds'] is None


def test_refresh_seconds_only_while_refreshing():
    service = HuginnService.__new__(HuginnService)
    service._price_lock = threading.Lock()
    service._price_state = {'steam': 'refreshing'}
    service._price_started_at = {'steam': time.time() - 30}
    service._normalize_price_market = lambda market: market
    assert 29 <= service.valuation_refresh_seconds('steam') < 40
    service._price_state['steam'] = 'ok'
    assert service.valuation_refresh_seconds('steam') is None
