from datetime import datetime, timezone

import pytest

from gjallarhorn_reminders import CommandError, ResellReminderService, message_for


class FakeSettings:
    def get_settings(self):
        return {'telegram_chat_id': 'shared', 'gjallarhorn_chat_id': 'gjallarhorn'}


def make(tmp_path, results):
    sent = []

    def sender(settings, text):
        sent.append((settings['telegram_chat_id'], text))
        return results.pop(0) if results else {'ok': True}
    service = ResellReminderService(FakeSettings(), path=str(tmp_path / 'r.json'), sender=sender)
    return service, sent


def test_sent_once_when_due_to_gjallarhorn_chat(tmp_path):
    service, sent = make(tmp_path, [])
    service.add('Test Knife | Example', '2026-11-10T12:00:00+02:00', 'resell')
    assert service.check_once(datetime(2026, 11, 10, 9, 59, tzinfo=timezone.utc)) == []
    assert len(service.check_once(datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc))) == 1
    assert service.check_once(datetime(2026, 11, 10, 11, 0, tzinfo=timezone.utc)) == []
    assert sent[0][0] == 'gjallarhorn' and 'Test Knife | Example' in sent[0][1]
    # kept across a restart, marked sent
    again = ResellReminderService(FakeSettings(), path=str(tmp_path / 'r.json'))
    assert again.list()[0]['sent_at']


def test_failed_send_is_tried_again(tmp_path):
    service, sent = make(tmp_path, [{'ok': False, 'error': 'down'}])
    service.add('Test Knife', '2026-11-10T12:00:00+02:00')
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    assert service.check_once(now) == []
    assert len(service.check_once(now)) == 1


def test_time_without_offset_or_name_refused(tmp_path):
    service, _ = make(tmp_path, [])
    with pytest.raises(CommandError):
        service.add('Test Knife', '2026-11-10T12:00:00')
    with pytest.raises(CommandError):
        service.add('', '2026-11-10T12:00:00+02:00')


def test_remove_and_message(tmp_path):
    service, _ = make(tmp_path, [])
    reminder = service.add('Test Knife', '2026-11-10T12:00:00+02:00')
    assert 'UTC+0200' in message_for(reminder)
    service.remove(reminder['id'])
    assert service.list() == []
    with pytest.raises(CommandError):
        service.remove(reminder['id'])
