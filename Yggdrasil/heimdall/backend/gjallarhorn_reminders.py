"""Gjallarhorn resell reminders: "tell me when this item can be sold again".

An item bought on a trade-protected or trade-held account cannot be resold
until a date Steam shows. Each reminder holds the item, that date and a note;
a loop checks once a minute and sends ONE Telegram text when the date has
passed (Gjallarhorn's chat on the shared bot, like the news watcher). It never
rings the phone and never trades. A reminder missed while the Mac slept is sent
the first minute the backend is awake again.

State: cache/gjallarhorn_reminders.json.
"""
import logging
import secrets
import threading
import time
from datetime import datetime, timezone

from commands import CommandError
from jsonio import atomic_write_json, read_json
from notifications import send_notification

log = logging.getLogger(__name__)

REMINDERS_FILE = 'cache/gjallarhorn_reminders.json'
CHECK_SECONDS = 60
MAX_NAME = 200
MAX_NOTE = 500


def parse_when(value):
    """An ISO date and time WITH its offset (for example 2026-11-10T12:00:00+02:00)
    → an aware datetime. A time without an offset is refused: explicit is better."""
    try:
        when = datetime.fromisoformat(str(value).strip())
    except ValueError:
        raise CommandError('bad_time', 'unlock time must be an ISO date and time with its offset')
    if when.tzinfo is None:
        raise CommandError('bad_time', 'unlock time needs its offset (for example +02:00)')
    return when


def due(reminders, now):
    """The reminders whose time has come and that were never sent."""
    return [r for r in reminders if not r.get('sent_at') and parse_when(r['unlock_at']) <= now]


def message_for(reminder):
    when = parse_when(reminder['unlock_at']).strftime('%d %B %Y %H:%M (UTC%z)')
    text = f"🔓 Gjallarhorn: {reminder['item_name']} can be resold now (trade lock ended {when})."
    if reminder.get('note'):
        text += f"\n{reminder['note']}"
    return text


class ResellReminderService:
    def __init__(self, settings_manager, path=REMINDERS_FILE, sender=send_notification):
        self.settings_manager = settings_manager
        self.path = path
        self.sender = sender
        self._lock = threading.Lock()
        self._reminders = read_json(path, default=[]) or []
        self._thread = None

    def list(self):
        with self._lock:
            return sorted((dict(r) for r in self._reminders), key=lambda r: r['unlock_at'])

    def add(self, item_name, unlock_at, note=''):
        name = str(item_name or '').strip()[:MAX_NAME]
        if not name:
            raise CommandError('name_required', 'item name required')
        when = parse_when(unlock_at)
        reminder = {
            'id': secrets.token_hex(6),
            'item_name': name,
            'unlock_at': when.isoformat(),
            'note': str(note or '').strip()[:MAX_NOTE],
            'created_at': datetime.now(timezone.utc).isoformat(),
            'sent_at': None,
        }
        with self._lock:
            self._reminders.append(reminder)
            self._save()
        return dict(reminder)

    def remove(self, reminder_id):
        with self._lock:
            before = len(self._reminders)
            self._reminders = [r for r in self._reminders if r['id'] != reminder_id]
            if len(self._reminders) == before:
                raise CommandError('unknown_reminder', 'no such reminder', 404)
            self._save()

    def check_once(self, now=None):
        """Send every due reminder once. Marked sent only when Telegram accepted it."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            pending = due(self._reminders, now)
        sent = []
        for reminder in pending:
            result = self.sender(self._settings(), message_for(reminder))
            if not result.get('ok'):
                log.warning('[GJALLARHORN-REMINDER] not sent (%s), trying again next minute',
                            result.get('error'))
                continue
            with self._lock:
                for stored in self._reminders:
                    if stored['id'] == reminder['id']:
                        stored['sent_at'] = now.isoformat()
                self._save()
            sent.append(reminder['id'])
        return sent

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name='gjallarhorn-reminders', daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            try:
                self.check_once()
            except Exception as e:
                log.warning('[GJALLARHORN-REMINDER] check failed: %s', e)
            time.sleep(CHECK_SECONDS)

    def _settings(self):
        # Gjallarhorn's own chat on the shared bot, like the news watcher.
        settings = dict(self.settings_manager.get_settings())
        chat = str(settings.get('gjallarhorn_chat_id') or '').strip()
        if chat:
            settings['telegram_chat_id'] = chat
        return settings

    def _save(self):
        atomic_write_json(self.path, self._reminders)
