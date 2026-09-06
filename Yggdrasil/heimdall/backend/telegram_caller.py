"""Telegram voice-call *ringer* for Gjallarhorn event alerts.

A Telegram BOT cannot place calls. This uses a dedicated second Telegram USER
account (the "caller") via Telethon's raw MTProto ``phone.requestCall``: firing
that request makes the target account's phone RING. Because a single unanswered
call only rings ~30s before Telegram marks it "missed", we place SEVERAL calls
back to back so it keeps ringing long enough to wake someone. No audio is carried
(Telegram's client VoIP media layer, libtgvoip, is deprecated and unavailable
here), so each call rings then drops — exactly what we want for a "wake up, the
event is now" alarm. The caller also sends a text message first (the WHY), so the
explanation sits in the same chat the call comes from.

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

    # Wake-up defaults: a single Telegram call only rings for ~30s before it
    # auto-ends as "missed", so to actually wake someone we place SEVERAL calls
    # back to back. Total on-phone time ≈ repeats * (ring_seconds + gap).
    _RING_SECONDS = 30
    _REPEATS = 5
    _GAP_SECONDS = 3

    def ring(self, message=None, ring_seconds=None, repeats=None, gap_seconds=None):
        """Wake the target: first send `message` (the WHY — so it sits in the same
        chat the call comes from), then ring repeatedly. Reloads config each call
        so a fresh login is picked up without a restart."""
        cfg = load_caller_config()
        self._cfg = cfg
        if not self._is_ready(cfg):
            return {'ok': False, 'error': 'telegram_caller.json not set up — run telegram_caller_login.py'}
        ring_seconds = self._RING_SECONDS if ring_seconds is None else max(1, int(ring_seconds))
        repeats = self._REPEATS if repeats is None else max(1, int(repeats))
        gap_seconds = self._GAP_SECONDS if gap_seconds is None else max(0, int(gap_seconds))
        try:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(
                    self._ring_async(cfg, message, ring_seconds, repeats, gap_seconds))
            finally:
                loop.close()
        except Exception as e:
            logger.error('[TELEGRAM CALLER] ring failed: %s', e)
            return {'ok': False, 'error': str(e)}

    async def _ring_async(self, cfg, message, ring_seconds, repeats, gap_seconds):
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

            # Send the explanation FIRST so it lands in the same conversation as
            # the incoming call — the ring itself can carry no text.
            message_sent = False
            if message:
                try:
                    await client.send_message(target, message)
                    message_sent = True
                except Exception as e:
                    logger.warning('[TELEGRAM CALLER] could not send reason message: %s', e)

            protocol = PhoneCallProtocol(
                min_layer=65, max_layer=92, udp_p2p=True, udp_reflector=True,
                library_versions=['4.0.0', '3.0.0', '2.7.7', '2.4.4'],
            )

            calls = 0
            rang_total = 0
            last_error = None
            for i in range(repeats):
                try:
                    # Fresh Diffie-Hellman g_a per call (required by phone.requestCall).
                    dh = await client(GetDhConfigRequest(version=0, random_length=256))
                    p_int = int.from_bytes(dh.p, 'big')
                    a = int.from_bytes(secrets.token_bytes(256), 'big') % (p_int - 2) + 1
                    g_a = pow(dh.g, a, p_int)
                    g_a_hash = hashlib.sha256(g_a.to_bytes(256, 'big')).digest()

                    requested = await client(RequestCallRequest(
                        user_id=target,
                        random_id=int.from_bytes(secrets.token_bytes(4), 'big') & 0x7FFFFFFF,
                        g_a_hash=g_a_hash,
                        protocol=protocol,
                    ))
                    call = requested.phone_call  # PhoneCallWaiting (id + access_hash)
                    await asyncio.sleep(ring_seconds)
                    # Hang up cleanly so the next call can ring afresh. The call may
                    # already have auto-ended (missed) — that discard error is fine.
                    try:
                        await client(DiscardCallRequest(
                            peer=InputPhoneCall(id=call.id, access_hash=call.access_hash),
                            duration=ring_seconds, connection_id=0,
                            reason=PhoneCallDiscardReasonHangup(),
                        ))
                    except Exception as e:
                        logger.debug('[TELEGRAM CALLER] discard (call likely already missed): %s', e)
                    calls += 1
                    rang_total += ring_seconds
                except Exception as e:
                    last_error = str(e)
                    logger.warning('[TELEGRAM CALLER] ring %d/%d failed: %s', i + 1, repeats, e)
                if i < repeats - 1:
                    await asyncio.sleep(gap_seconds)

            logger.info('[TELEGRAM CALLER] placed %d/%d calls (~%ss ringing), message_sent=%s',
                        calls, repeats, rang_total, message_sent)
            return {'ok': calls > 0, 'calls': calls, 'rang_seconds_total': rang_total,
                    'message_sent': message_sent, 'error': None if calls > 0 else last_error}
        finally:
            await client.disconnect()
