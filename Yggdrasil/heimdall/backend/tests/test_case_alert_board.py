"""Case Arbitrage Telegram board: every buy market cheaper than CSFloat is listed,
and the board always fits one Telegram message."""
import huginn_service
from huginn_service import HuginnService
from notifications import TELEGRAM_MESSAGE_LIMIT, telegram_visible_length, trim_telegram_html


def _service(monkeypatch, tmp_path, containers, held=None):
    monkeypatch.setattr(huginn_service, 'CASE_ALERT_STATE_FILE', str(tmp_path / 'alert_state.json'))
    service = HuginnService(None, None)
    monkeypatch.setattr(service, 'cases_prices', lambda token, categories: {'containers': containers})
    monkeypatch.setattr(service, 'get_cache', lambda: {'by_hash': held or {}})
    return service


def _run(monkeypatch, service):
    sent = []
    monkeypatch.setattr(huginn_service, 'send_notification',
                        lambda settings, text, html=None: (sent.append((text, html)), {'ok': True, 'message_id': 1})[1])
    settings = {'case_alerts_enabled': True, 'telegram_bot_token': 'x', 'telegram_chat_id': '1',
                'tradeon_token': 'token', 'case_alert_min_pct': 0}
    service.run_case_alerts(settings)
    return sent


def test_every_buy_market_cheaper_than_csfloat_is_listed(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, [
        {'name': 'Recoil Case', 'prices': {'csfloat': 0.25, 'lisskins': 0.24, 'buff': 0.26, 'tradeon': 0.22,
                                           'csmoney_market': 0.23, 'skinswap': 0.20, 'csmoney_trade': 0.19}},
    ], held={'Recoil Case': {'count': 8100}})
    sent = _run(monkeypatch, service)
    assert service._load_alert_state()['active'] == [
        'Recoil Case|csmoney_market', 'Recoil Case|csmoney_trade', 'Recoil Case|lisskins',
        'Recoil Case|skinswap', 'Recoil Case|tradeon']
    plain = sent[0][0]
    assert '• Recoil Case ×8100' in plain
    assert ('CS.MONEY Trade $0.19 · SkinSwap $0.20 · Tradeon $0.22 · CS.MONEY Market $0.23 · LisSkins $0.24  '
            'vs CSFloat $0.25  (-$0.06, -24.0%)') in plain


def test_a_crowded_board_still_fits_one_telegram_message(monkeypatch, tmp_path):
    containers = [{'name': f'Operation Breakout Weapon Case {i}',
                   'prices': {'csfloat': 1.20, 'lisskins': 1.00, 'buff': 1.01, 'tradeon': 1.02,
                              'csmoney_market': 1.03, 'skinswap': 0.90}}
                  for i in range(200)]
    service = _service(monkeypatch, tmp_path, containers,
                       held={f'Operation Breakout Weapon Case {i}': {'count': 5} for i in range(100)})
    plain, html = _run(monkeypatch, service)[0]
    assert telegram_visible_length(html) <= TELEGRAM_MESSAGE_LIMIT
    assert 'more in Huginn → Case Arbitrage' in plain
    assert plain.rstrip().splitlines()[-1].startswith('⏱ Updated')          # the footer survives


def test_every_market_is_a_link(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path, [
        {'name': 'Recoil Case', 'prices': {'csfloat': 0.25, 'lisskins': 0.23, 'tradeon': 0.23, 'skinswap': 0.23}},
    ])
    html = _run(monkeypatch, service)[0][1]
    for market in ('LisSkins', 'Tradeon', 'SkinSwap', 'CSFloat'):
        assert f'>{market}</a>' in html


def test_link_addresses_do_not_count_toward_the_telegram_limit():
    line = '<a href="https://short-pulse.tradeon.space/short-link/CsGo/Buff/Recoil%20Case">Buff</a> $0.23'
    assert telegram_visible_length(line) == len('Buff $0.23')
    board = '\n'.join([line] * 300)                      # ~3,300 visible, ~28,000 raw
    assert trim_telegram_html(board) == board
    long_board = '\n'.join([line] * 500)
    trimmed = trim_telegram_html(long_board)
    assert trimmed.endswith('(trimmed)') and telegram_visible_length(trimmed) <= TELEGRAM_MESSAGE_LIMIT
