"""Telegram alert for every auto-confirmed trade offer (scheduler._process_account).

send_notification is always stubbed here: no test may reach Telegram or a webhook.
"""
import pytest

import notifications
import scheduler as scheduler_module
from scheduler import ConfirmationScheduler

STEAMID = '76561198000000001'
CONFIGURED = {'auto_confirm_trades': True, 'auto_confirm_market': True,
              'telegram_bot_token': 'test-token', 'telegram_chat_id': '1'}


class _SynchronousThread:
    """Stands in for threading.Thread so the alert is sent before the test asserts."""

    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class _Steam:
    def __init__(self, confirmations, batch_result=None):
        self.confirmations = confirmations
        self.batch_result = batch_result or {'success': True}
        self.batches = []

    def get_confirmations(self, steamid):
        return {'success': True, 'confirmations': self.confirmations}

    def act_on_confirmations_batch(self, steamid, items, operation):
        self.batches.append((items, operation))
        return self.batch_result

    def get_account(self, steamid):
        return {'account_name': 'traderalpha'}


@pytest.fixture
def sent(monkeypatch):
    messages = []

    def fake_send(settings, text, html=None):
        messages.append(text)
        return {'ok': True, 'channel': 'telegram', 'error': None, 'message_id': 1}

    monkeypatch.setattr(notifications, 'send_notification', fake_send)
    monkeypatch.setattr(scheduler_module.threading, 'Thread', _SynchronousThread)
    return messages


def _trade(cid, headline, summary, creator_id):
    return {'id': cid, 'nonce': f'n{cid}', 'type': 2, 'type_name': 'Trade Offer',
            'headline': headline, 'summary': summary, 'creator_id': creator_id}


def test_one_message_per_account_lists_every_trade(sent):
    steam = _Steam([
        _trade('1', 'lootfarm bot', ['You will give 1 item', 'AK-47 | Redline (Field-Tested)'], '6001'),
        _trade('2', 'someone', ['You will receive 2 items'], '6002'),
        {'id': '3', 'nonce': 'n3', 'type': 3, 'type_name': 'Market Listing',
         'headline': 'Sell AWP', 'summary': ['AWP | Asiimov']},
    ])
    ConfirmationScheduler(settings_manager=None, steam_service=steam)._process_account(STEAMID, CONFIGURED)
    assert steam.batches == [([('1', 'n1'), ('2', 'n2'), ('3', 'n3')], 'allow')]
    assert len(sent) == 1
    message = sent[0]
    assert 'traderalpha' in message and STEAMID in message and '2 trade offers' in message
    assert 'lootfarm bot (trade offer 6001)' in message and 'someone (trade offer 6002)' in message
    assert 'You will give 1 item' in message and 'AK-47 | Redline (Field-Tested)' in message
    assert 'You will receive 2 items' in message
    assert 'AWP' not in message


def test_market_confirmations_do_not_alert(sent):
    steam = _Steam([
        {'id': '3', 'nonce': 'n3', 'type': 3, 'type_name': 'Market Listing', 'headline': 'Sell'},
        {'id': '4', 'nonce': 'n4', 'type': 12, 'type_name': 'Market Purchase', 'headline': 'Buy'},
    ])
    ConfirmationScheduler(settings_manager=None, steam_service=steam)._process_account(STEAMID, CONFIGURED)
    assert steam.batches == [([('3', 'n3'), ('4', 'n4')], 'allow')]
    assert sent == []


def test_failed_batch_does_not_alert(sent):
    steam = _Steam([_trade('1', 'bot', ['x'], '6001')], batch_result={'success': False, 'message': 'no'})
    ConfirmationScheduler(settings_manager=None, steam_service=steam)._process_account(STEAMID, CONFIGURED)
    assert sent == []


def test_toggle_off_or_no_channel_does_not_alert(sent):
    for settings in ({**CONFIGURED, 'alert_auto_confirmed_trades': False},
                     {'auto_confirm_trades': True}):
        steam = _Steam([_trade('1', 'bot', ['x'], '6001')])
        ConfirmationScheduler(settings_manager=None, steam_service=steam)._process_account(STEAMID, settings)
        assert steam.batches == [([('1', 'n1')], 'allow')]
    assert sent == []


def test_send_failure_never_stops_acceptance(monkeypatch, caplog):
    def exploding_send(settings, text, html=None):
        raise RuntimeError('telegram down')

    monkeypatch.setattr(notifications, 'send_notification', exploding_send)
    monkeypatch.setattr(scheduler_module.threading, 'Thread', _SynchronousThread)
    steam = _Steam([_trade('1', 'bot', ['x'], '6001')])
    scheduler = ConfirmationScheduler(settings_manager=None, steam_service=steam)
    caplog.set_level('INFO')
    scheduler._process_account(STEAMID, CONFIGURED)
    assert steam.batches == [([('1', 'n1')], 'allow')]
    assert 'Accepted' in caplog.text and 'trade alert failed: telegram down' in caplog.text


def test_broken_account_lookup_still_alerts(sent):
    class SteamWithoutAccounts(_Steam):
        def get_account(self, steamid):
            raise KeyError(steamid)

    steam = SteamWithoutAccounts([_trade('1', 'bot', [], None)])
    ConfirmationScheduler(settings_manager=None, steam_service=steam)._process_account(STEAMID, CONFIGURED)
    assert len(sent) == 1
    assert 'Unknown' in sent[0] and '1 trade offer on' in sent[0] and '(no item summary from Steam)' in sent[0]
