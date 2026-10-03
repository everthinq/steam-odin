"""Tiny outbound notification helper — Telegram first, Discord/Slack webhook fallback.

No third-party deps: plain urllib POSTs. Used by the Case Arbitrage price alerts.
Configure via settings.json (gitignored):
  telegram_bot_token + telegram_chat_id   -> Telegram (preferred)
  notify_webhook_url                       -> Discord or Slack incoming webhook (fallback)

Telegram supports a live "board" pattern: send once (get message_id), then edit it in
place on later polls (silent, no push), and delete/repost when a genuinely new deal
should notify. edit/delete are Telegram-only; webhooks just send.
"""
import html as html_entities
import json
import re
import urllib.request
import urllib.error

# Hard message-length ceilings: Telegram rejects text over 4096 characters (counted
# after HTML parsing: tags and link addresses do not count, only what you see) and a
# Discord webhook rejects content over 2000 (Slack accepts more; 2000 is safe for
# both), so an over-long board would silently never arrive. Messages are trimmed at
# a line break and marked, so the HTML stays balanced (each line closes its tags).
TELEGRAM_MESSAGE_LIMIT = 4096
WEBHOOK_MESSAGE_LIMIT = 2000
_TRIMMED_MARKER = '\n… (trimmed)'


def trim_message(text, limit):
    """*text* cut to at most *limit* characters, at the last line break that fits,
    with a trailing marker. Unchanged when it already fits."""
    if text is None or len(text) <= limit:
        return text
    room = limit - len(_TRIMMED_MARKER)
    cut = text.rfind('\n', 0, room + 1)
    if cut <= 0:
        cut = room
    return text[:cut] + _TRIMMED_MARKER


def telegram_visible_length(html):
    """Characters Telegram counts for an HTML message: the text left after its tags
    (and the link addresses inside them) are parsed away."""
    return len(html_entities.unescape(re.sub(r'<[^>]*>', '', html)))


def trim_telegram_html(html, limit=TELEGRAM_MESSAGE_LIMIT):
    """*html* cut at a line break so its VISIBLE text fits *limit*, with a trailing
    marker. Every line closes its own tags, so cutting between lines stays valid."""
    if html is None or telegram_visible_length(html) <= limit:
        return html
    lines = html.split('\n')
    while len(lines) > 1 and telegram_visible_length('\n'.join(lines) + _TRIMMED_MARKER) > limit:
        lines.pop()
    return '\n'.join(lines) + _TRIMMED_MARKER


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


def own_bot_settings(settings, prefix):
    """*settings* pointed at a tool's own Telegram bot ({prefix}_bot_token and
    {prefix}_chat_id), or None when the tool has no bot of its own. No fallback to the
    shared bot, chat or webhook: a tool without its own bot stays silent."""
    token = str(settings.get(f'{prefix}_bot_token') or '').strip()
    chat = str(settings.get(f'{prefix}_chat_id') or '').strip()
    if not token or not chat:
        return None
    return {**settings, 'telegram_bot_token': token, 'telegram_chat_id': chat, 'notify_webhook_url': ''}


NO_OWN_BOT = {'ok': False, 'channel': None, 'message_id': None,
              'error': 'no Telegram bot of its own (bot token and chat id in its settings): messages are off'}


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
        p['text'] = trim_telegram_html(html)
        p['parse_mode'] = 'HTML'
    else:
        p['text'] = trim_message(text, TELEGRAM_MESSAGE_LIMIT)
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
        text = trim_message(text, WEBHOOK_MESSAGE_LIMIT)
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
