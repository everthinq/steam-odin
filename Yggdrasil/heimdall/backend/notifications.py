"""Tiny outbound notification helper — Telegram first, Discord/Slack webhook fallback.

No third-party deps: plain urllib POSTs. Used by the Case Arbitrage price alerts.
Configure via settings.json (gitignored):
  telegram_bot_token + telegram_chat_id   -> Telegram (preferred)
  notify_webhook_url                       -> Discord or Slack incoming webhook (fallback)

Telegram supports a live "board" pattern: send once (get message_id), then edit it in
place on later polls (silent, no push), and delete/repost when a genuinely new deal
should notify. edit/delete are Telegram-only; webhooks just send.
"""
import json
import urllib.request
import urllib.error


def _post_json(url, payload, timeout=10):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace') if hasattr(e, 'read') else ''
        return e.code, body


def notification_channel(settings):
    """Which channel is configured, or None. ('telegram' | 'webhook' | None)."""
    if (settings.get('telegram_bot_token') or '').strip() and str(settings.get('telegram_chat_id') or '').strip():
        return 'telegram'
    if (settings.get('notify_webhook_url') or '').strip():
        return 'webhook'
    return None


def _tg(settings, method, payload, timeout=10):
    tok = settings['telegram_bot_token'].strip()
    payload = {'chat_id': str(settings['telegram_chat_id']).strip(), **payload}
    status, body = _post_json(f'https://api.telegram.org/bot{tok}/{method}', payload, timeout)
    try:
        data = json.loads(body)
    except Exception:
        data = {}
    return status, data


def _tg_text_payload(text, html):
    p = {'disable_web_page_preview': True}
    if html:
        p['text'] = html
        p['parse_mode'] = 'HTML'
    else:
        p['text'] = text
    return p


def send_notification(settings, text, html=None):
    """Send via the configured channel. Returns {ok, channel, error, message_id}.

    `text` is plain (webhook + Telegram fallback); `html` renders links on Telegram."""
    channel = notification_channel(settings)
    if channel is None:
        return {'ok': False, 'channel': None, 'error': 'no notification channel configured', 'message_id': None}
    try:
        if channel == 'telegram':
            status, data = _tg(settings, 'sendMessage', _tg_text_payload(text, html))
            ok = bool(data.get('ok'))
            mid = (data.get('result') or {}).get('message_id')
            return {'ok': ok, 'channel': 'telegram', 'message_id': mid,
                    'error': None if ok else f"HTTP {status}: {data.get('description', '')}"}
        url = settings['notify_webhook_url'].strip()
        status, body = _post_json(url, {'content': text, 'text': text})
        ok = 200 <= status < 300
        return {'ok': ok, 'channel': 'webhook', 'message_id': None,
                'error': None if ok else f'HTTP {status}: {body[:200]}'}
    except Exception as e:
        return {'ok': False, 'channel': channel, 'error': str(e), 'message_id': None}


def edit_notification(settings, message_id, text, html=None):
    """Edit an existing Telegram message in place (silent — no push). Returns
    {ok, not_found, error}. 'message is not modified' counts as ok (no-op)."""
    if notification_channel(settings) != 'telegram' or not message_id:
        return {'ok': False, 'not_found': False, 'error': 'edit unsupported'}
    try:
        payload = {'message_id': message_id, **_tg_text_payload(text, html)}
        status, data = _tg(settings, 'editMessageText', payload)
        if data.get('ok'):
            return {'ok': True, 'not_found': False, 'error': None}
        desc = (data.get('description') or '').lower()
        if 'not modified' in desc:
            return {'ok': True, 'not_found': False, 'error': None}
        not_found = 'not found' in desc or 'message to edit' in desc or "message can't be edited" in desc
        return {'ok': False, 'not_found': not_found, 'error': f"HTTP {status}: {data.get('description', '')}"}
    except Exception as e:
        return {'ok': False, 'not_found': False, 'error': str(e)}


def delete_notification(settings, message_id):
    """Delete a Telegram message. Best-effort; returns {ok, error}."""
    if notification_channel(settings) != 'telegram' or not message_id:
        return {'ok': False, 'error': 'delete unsupported'}
    try:
        status, data = _tg(settings, 'deleteMessage', {'message_id': message_id})
        return {'ok': bool(data.get('ok')), 'error': None if data.get('ok') else data.get('description')}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
