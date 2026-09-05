"""Telegram voice-call *ringer* for Gjallarhorn event alerts.

A Telegram BOT cannot place calls. This uses a dedicated second Telegram USER
account (the "caller") via Telethon's raw MTProto ``phone.requestCall``: firing
that request makes the target account's phone RING. We ring, hold briefly, then
hang up (``phone.discardCall``). No audio is carried (Telegram's client VoIP
media layer, libtgvoip, is deprecated and unavailable here), so it rings then
drops — which is exactly what we want for a "wake up, the event is now" alarm.

⚠️  Telegram may rate-limit or BAN an account that fires automated calls. Use a
disposable burner number for the caller, never your main. Credentials + login
session live in ``telegram_caller.json`` (gitignored) — never commit them.

Set up once with ``telegram_caller_login.py`` (interactive: it needs the code
Telegram texts to the caller number).
"""
import asyncio
import hashlib
import json
import logging
import os
import secrets

logger = logging.getLogger(__name__)

CALLER_FILE = 'telegram_caller.json'


def load_caller_config():
    """Read telegram_caller.json (api_id, api_hash, session, target) or None."""
    if not os.path.exists(CALLER_FILE):
        return None
    try:
        with open(CALLER_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error('[TELEGRAM CALLER] bad %s: %s', CALLER_FILE, e)
        return None


class TelegramCaller:
    """Rings a fixed target from the configured caller account. Best-effort."""

    def __init__(self):
        self._cfg = load_caller_config()

    def status(self):
        cfg = self._cfg or load_caller_config()
        return {
            'configured': self._is_ready(cfg),
            'target': (cfg or {}).get('target'),
        }

    @staticmethod
    def _is_ready(cfg):
        return bool(cfg and cfg.get('api_id') and cfg.get('api_hash')
                    and cfg.get('session') and cfg.get('target'))

    def ring(self, ring_seconds=8):
        """Ring the target once for ~ring_seconds, then hang up. Reloads config
        each call so a fresh login is picked up without a restart."""
        cfg = load_caller_config()
        self._cfg = cfg
        if not self._is_ready(cfg):
            return {'ok': False, 'error': 'telegram_caller.json not set up — run telegram_caller_login.py'}
        try:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(self._ring_async(cfg, max(1, int(ring_seconds))))
            finally:
                loop.close()
        except Exception as e:
            logger.error('[TELEGRAM CALLER] ring failed: %s', e)
            return {'ok': False, 'error': str(e)}

    async def _ring_async(self, cfg, ring_seconds):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.functions.messages import GetDhConfigRequest
        from telethon.tl.functions.phone import DiscardCallRequest, RequestCallRequest
        from telethon.tl.types import (
            InputPhoneCall, PhoneCallDiscardReasonHangup, PhoneCallProtocol,
        )

        client = TelegramClient(StringSession(cfg['session']), int(cfg['api_id']), cfg['api_hash'])
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return {'ok': False, 'error': 'caller session not authorized — re-run telegram_caller_login.py'}

            target = await client.get_input_entity(cfg['target'])

            # Diffie-Hellman params required by phone.requestCall.
            dh = await client(GetDhConfigRequest(version=0, random_length=256))
            p_int = int.from_bytes(dh.p, 'big')
            a = int.from_bytes(secrets.token_bytes(256), 'big') % (p_int - 2) + 1
            g_a = pow(dh.g, a, p_int)
            g_a_hash = hashlib.sha256(g_a.to_bytes(256, 'big')).digest()

            protocol = PhoneCallProtocol(
                min_layer=65, max_layer=92, udp_p2p=True, udp_reflector=True,
                library_versions=['4.0.0', '3.0.0', '2.7.7', '2.4.4'],
            )
            requested = await client(RequestCallRequest(
                user_id=target,
                random_id=int.from_bytes(secrets.token_bytes(4), 'big') & 0x7FFFFFFF,
                g_a_hash=g_a_hash,
                protocol=protocol,
            ))
            call = requested.phone_call  # PhoneCallWaiting (id + access_hash)

            # Let it ring, then hang up cleanly so it doesn't linger.
            await asyncio.sleep(ring_seconds)
            await client(DiscardCallRequest(
                peer=InputPhoneCall(id=call.id, access_hash=call.access_hash),
                duration=ring_seconds,
                connection_id=0,
                reason=PhoneCallDiscardReasonHangup(),
            ))
            logger.info('[TELEGRAM CALLER] rang target for %ss (call %s)', ring_seconds, call.id)
            return {'ok': True, 'call_id': call.id, 'rang_seconds': ring_seconds}
        finally:
            await client.disconnect()
