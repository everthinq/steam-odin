"""One-time setup: log the caller Telegram account in and save its session.

Run this INTERACTIVELY once (Telegram texts a login code to the caller number):

    docker exec -it steam-odin-heimdall-backend-1 python /app/telegram_caller_login.py

You will be asked for:
  * api_id + api_hash   — create an app at https://my.telegram.org (API development tools)
  * the caller phone    — your SECOND (burner) number, in +country format
  * the login code       — Telegram sends it to that number
  * (2FA password        — only if the caller account has one)
  * the target           — your MAIN Telegram @username or numeric user id (who gets rung)

It writes telegram_caller.json (gitignored). NEVER commit that file — it holds the
api_hash and a full login session for the caller account.
"""
import json
import os

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'telegram_caller.json')


def main():
    api_id = int(input('api_id: ').strip())
    api_hash = input('api_hash: ').strip()
    target = input('target — your MAIN @username or numeric id (who gets rung): ').strip()

    print('\nConnecting… Telegram will text a login code to the caller number.')
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        me = client.get_me()
        session = client.session.save()
        caller = me.username or me.phone or str(me.id)

    data = {
        'api_id': api_id,
        'api_hash': api_hash,
        'session': session,
        'target': int(target) if target.lstrip('-').isdigit() else target,
    }
    with open(OUT_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    os.chmod(OUT_FILE, 0o600)
    print(f'\nSaved {OUT_FILE}')
    print(f'Caller account: {caller}  →  will ring: {data["target"]}')
    print('Do NOT commit telegram_caller.json. Test it with: POST /api/huginn/gjallarhorn/ring')


if __name__ == '__main__':
    main()
