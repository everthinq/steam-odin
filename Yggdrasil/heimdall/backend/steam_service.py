import time
import hmac
import struct
import base64
import requests
import json
import secrets
import os
import re
import shutil
import logging
import threading
from logging.handlers import TimedRotatingFileHandler
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from datetime import datetime, timedelta
from hashlib import sha1
from storage import SecureStorage

logger = logging.getLogger(__name__)  # general diagnostics; steam_debug logger is separate

_STEAMID64_BASE = 76561197960265728

_LOG_RETENTION_DAYS = 2
_LOG_TRIM_MIN_SIZE = 5 * 1024 * 1024  # only trim a pre-existing file if it's over 5 MB
_steam_debug_logger = None

# Values never written to any log: tokens and login-session ids in Steam JSON
# bodies, and in URLs the confirmation signature (k), device id (p) and the
# confirmation id/nonce pair (cid/ck) that together authorise an accept.
_REDACTED = '<redacted>'
_SENSITIVE_BODY_KEYS = ('access_token', 'refresh_token', 'request_id', 'client_id', 'weak_token')
_SENSITIVE_BODY_PATTERN = re.compile(
    r'("(?:' + '|'.join(_SENSITIVE_BODY_KEYS) + r')"\s*:\s*)("(?:[^"\\]|\\.)*"|[^,}\s]+)'
)
_SENSITIVE_QUERY_KEYS = {
    'k', 'p', 'cid', 'ck', 'cid[]', 'ck[]', 'access_token', 'refresh_token',
    'request_id', 'client_id', 'steamLoginSecure',
}


def _redact_body(text):
    """Replace the values of token/session-id keys in a JSON-ish body."""
    return _SENSITIVE_BODY_PATTERN.sub(lambda m: f'{m.group(1)}"{_REDACTED}"', text)


def _redact_url(url):
    """Replace sensitive query-string values in a URL (keeps the parameter names)."""
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        query = [
            (key, _REDACTED if key in _SENSITIVE_QUERY_KEYS else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit(parts._replace(query=urlencode(query, safe='<>')))
    except Exception:
        return '<unparseable url>'


def _read_last_log_timestamp(path):
    """Return the newest header timestamp in the log by scanning only its tail."""
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read = min(size, 65536)
            f.seek(size - read)
            tail = f.read()
    except OSError:
        return None

    last = None
    for raw_line in tail.split(b'\n'):
        if raw_line.startswith(b'[') and b'[STEAM DEBUG]' in raw_line:
            ts_end = raw_line.find(b']')
            if ts_end > 1:
                ts_str = raw_line[1:ts_end].decode('ascii', 'ignore').rstrip('Z')
                try:
                    last = datetime.fromisoformat(ts_str)
                except ValueError:
                    pass
    return last


def _trim_steam_log(path, max_age_days=_LOG_RETENTION_DAYS):
    """
    One-time trim of a pre-existing oversized debug log: keep only entries from the
    last `max_age_days`. The rotating handler keeps things bounded afterwards; this
    just deals with a file that grew huge before rotation existed.

    The cutoff is anchored to the newest entry *in the file* (not the wall clock) so
    a container/host clock skew can't wrongly wipe otherwise-recent logs.
    """
    try:
        if not os.path.exists(path) or os.path.getsize(path) < _LOG_TRIM_MIN_SIZE:
            return
    except OSError:
        return

    anchor = _read_last_log_timestamp(path) or datetime.utcnow()
    cutoff = anchor - timedelta(days=max_age_days)
    keep_offset = 0
    found = False
    try:
        with open(path, 'rb') as f:
            offset = 0
            for raw_line in f:
                if raw_line.startswith(b'[') and b'[STEAM DEBUG]' in raw_line:
                    ts_end = raw_line.find(b']')
                    if ts_end > 1:
                        ts_str = raw_line[1:ts_end].decode('ascii', 'ignore').rstrip('Z')
                        try:
                            if datetime.fromisoformat(ts_str) >= cutoff:
                                keep_offset = offset
                                found = True
                                break
                        except ValueError:
                            pass
                offset += len(raw_line)
    except OSError:
        return

    try:
        if not found:
            # Every entry is older than the cutoff — start clean.
            open(path, 'w').close()
            return
        if keep_offset == 0:
            return  # everything already within the window
        tmp = path + '.trim'
        with open(path, 'rb') as src, open(tmp, 'wb') as dst:
            src.seek(keep_offset)
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        os.replace(tmp, path)
    except OSError as e:
        logger.error(f"[STEAM DEBUG] Log trim failed: {e}")


def _get_steam_debug_logger():
    """Lazily build a daily-rotating logger that retains `_LOG_RETENTION_DAYS` days."""
    global _steam_debug_logger
    if _steam_debug_logger is not None:
        return _steam_debug_logger

    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'steam_debug.log')

    from logging_setup import is_reloader_parent
    if is_reloader_parent():
        # The werkzeug watcher process must not open (and rotate) the same file
        # as the serving child; it gets a handler-less, non-propagating logger.
        quiet = logging.getLogger('steam_debug')
        quiet.propagate = False
        _steam_debug_logger = quiet
        return quiet

    # Shrink any huge pre-rotation file before attaching the handler.
    _trim_steam_log(log_path)

    logger = logging.getLogger('steam_debug')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = TimedRotatingFileHandler(
            log_path, when='midnight', backupCount=_LOG_RETENTION_DAYS, encoding='utf-8', utc=True
        )
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)

    _steam_debug_logger = logger
    return logger


class SteamService:
    # Exponential backoff for mobileconf.
    #
    # When Steam answers a confirmation request with "Oh nooooooes! ... try your
    # request again later" we stop hitting the endpoint for a while instead of
    # retrying in a tight loop. Each consecutive transient error doubles the wait
    # (up to the cap); the first successful fetch resets it back to the base.
    #
    # NOTE: that "try again later" message is a *generic* transient signal, not a
    # confirmed IP ban. The one time it appeared en masse it was actually caused by
    # sending the deprecated 32-bit account id in the `a` param (see
    # _confirmation_param_variants). The backoff is kept as a general safety net so
    # any future failure loop can't turn into a request storm.
    _MOBILECONF_COOLDOWN_BASE = 180
    _MOBILECONF_COOLDOWN_MAX = 3600

    # Network failures that mean "Steam is unavailable / throttling us", treated
    # like the "Oh nooooooes" message: trip the backoff instead of retrying.
    _TRANSIENT_EXCEPTIONS = (requests.ConnectionError, requests.Timeout)

    def __init__(self):
        self.storage = SecureStorage()
        self.time_offset = None
        self.last_time_sync = 0
        # Pause all mobileconf traffic until this timestamp after a transient error.
        self._mobileconf_cooldown_until = 0
        self._mobileconf_backoff_sec = self._MOBILECONF_COOLDOWN_BASE
        # One lock per SteamID64 around every read-modify-write of a maFile, so the
        # scheduler, keep-alive, request threads and Ratatoskr callbacks never
        # overwrite each other's Session changes. Held only for the disk work,
        # never across a Steam call.
        self._account_locks = {}
        self._account_locks_guard = threading.Lock()
        # GenerateAccessTokenForApp answers with an empty response in practice
        # (see CLAUDE.md gotcha 6). After the first empty answer in this process
        # renewals go straight to a full login instead of spending a Steam call.
        self._generate_access_token_for_app_disabled = False

    def _account_lock(self, steamid):
        """The re-entrant lock guarding one account's maFile read-modify-write."""
        key = str(steamid)
        with self._account_locks_guard:
            lock = self._account_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._account_locks[key] = lock
            return lock

    def _merge_save_session(self, steamid, updates, drop_if_unchanged=None):
        """Re-load the maFile, merge only these Session keys, and save it.

        ``updates`` is {key: value} written into Session. ``drop_if_unchanged`` is
        {key: value}: each key is removed only if the file still holds exactly that
        value — so a token another thread stored meanwhile is never thrown away.
        Everything else in the file (whatever other threads wrote) is kept.
        """
        with self._account_lock(steamid):
            fresh = self.storage.load_account(steamid)
            if fresh is None:
                raise RuntimeError(f'maFile for {steamid} vanished before the session could be saved')
            session_data = fresh.get('Session') or {}
            for key, value in (drop_if_unchanged or {}).items():
                if value is not None and session_data.get(key) == value:
                    session_data.pop(key, None)
            session_data.update(updates)
            fresh['Session'] = session_data
            self.storage.save_account(steamid, fresh)
            return fresh

    def _get_proxies(self):
        """Get proxy configuration from environment variables."""
        proxies = {}
        
        http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
        https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
        socks_proxy = os.environ.get('SOCKS_PROXY') or os.environ.get('socks_proxy')
        
        if http_proxy:
            proxies['http'] = http_proxy
        if https_proxy:
            proxies['https'] = https_proxy
        elif http_proxy:
            proxies['https'] = http_proxy
        
        if socks_proxy:
            try:
                import socks  # noqa: F401
                proxies['http'] = socks_proxy
                proxies['https'] = socks_proxy
            except ImportError:
                logger.warning("Warning: SOCKS_PROXY set but 'requests[socks]' not installed.")
        
        return proxies if proxies else None

    def _log_steam_response(self, label, resp):
        """Debug helper: log Steam HTTP responses (secrets redacted).

        The body only ever goes to the separate steam_debug log, never to the
        main heimdall log. Mobile-confirmation entries are written at DEBUG, so
        with the default INFO level they are not kept at all.
        """
        try:
            body = resp.text
        except Exception:
            body = '<no text>'
        snippet = _redact_body(body[:1000])
        try:
            method = getattr(resp.request, 'method', 'UNKNOWN')
            url = _redact_url(getattr(resp.request, 'url', 'UNKNOWN'))
        except Exception:
            method = 'UNKNOWN'
            url = 'UNKNOWN'

        timestamp = datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        log_header = (
            f"[{timestamp}] [STEAM DEBUG] {label} "
            f"{method} {url} status={resp.status_code} len={len(body)}"
        )
        log_body = f"[STEAM DEBUG] {label} body:\n{snippet}\n--- END {label} ---\n"
        level = logging.DEBUG if label.startswith('MobileConf') else logging.INFO

        logger.debug(f"[STEAM DEBUG] {label} {method} status={resp.status_code} len={len(body)}")

        try:
            _get_steam_debug_logger().log(level, log_header + "\n" + log_body)
        except Exception as e:
            logger.error(f"[STEAM DEBUG] Failed to write log file: {e}")

    # Keys the Add Account page sends alongside the maFile JSON that must never be
    # written into the maFile (the password belongs in the Mimir vault only).
    _IMPORT_TRANSPORT_KEYS = ('fileName', 'account_password', 'steamid_from_text')

    @staticmethod
    def _is_steamid64(value):
        """A SteamID64 as text: exactly 17 decimal digits."""
        return isinstance(value, str) and len(value) == 17 and value.isdigit()

    def import_account(self, mafile_data, filename=None):
        """Import a maFile, taking the SteamID64 from its file name.

        The SteamID64 comes from the file name (``<steamid>.maFile``), or else from
        ``steamid_from_text`` (read by the browser from the raw file text): the
        ``Session.SteamID`` inside the JSON was parsed by the browser as a number,
        and 17-digit ids lose precision there, so it is never trusted.
        """
        if isinstance(mafile_data, str):
            mafile_data = json.loads(mafile_data)
        if not isinstance(mafile_data, dict):
            return {'error': 'maFile content must be a JSON object'}

        steamid = str(filename).split('.')[0] if filename else ''
        if not self._is_steamid64(steamid):
            # A renamed file ("<login>.maFile", "x (1).maFile"): use the SteamID64 the
            # browser read from the raw file text as a string, never the parsed number.
            steamid = str(mafile_data.get('steamid_from_text') or '')
        if not self._is_steamid64(steamid):
            return {'error': 'Invalid SteamID. The file must be named <SteamID64>.maFile '
                             '(17 digits, e.g. 76561198000000000.maFile)'}
        logger.info(f"[IMPORT] Using SteamID from filename: {steamid}")

        mafile_data = {
            key: value for key, value in mafile_data.items()
            if key not in self._IMPORT_TRANSPORT_KEYS
        }
        # Synchronize internal session data with the chosen string ID
        session_data = dict(mafile_data.get('Session') or {})
        session_data['SteamID'] = steamid
        mafile_data['Session'] = session_data

        try:
            # Save as <steamid>.maFile
            with self._account_lock(steamid):
                self.storage.save_account(steamid, mafile_data)
            return {'status': 'success', 'steamid': steamid}
        except Exception as e:
            return {'error': f'Failed to save: {str(e)}'}

    def remove_account(self, steamid):
        """Soft-delete one account (its maFile is archived, never unlinked)."""
        with self._account_lock(steamid):
            return self.storage.delete_account(steamid)

    def remove_all_accounts(self):
        accounts = self.storage.list_accounts()
        count = 0
        for steamid in accounts:
            if self.remove_account(steamid):
                count += 1
        return count

    def _query_time(self):
        current_time = time.time()
        if self.time_offset is None or (current_time - self.last_time_sync) > 300:
            try:
                resp = requests.post(
                    'https://api.steampowered.com/ITwoFactorService/QueryTime/v0001',
                    timeout=10,
                    proxies=self._get_proxies()
                )
                self._log_steam_response('QueryTime', resp)
                server_time = int(resp.json()['response']['server_time'])
                self.time_offset = server_time - current_time
                self.last_time_sync = current_time
            except:
                if self.time_offset is None:
                    self.time_offset = 0
        return self.time_offset

    def _get_steam_time(self):
        return int(time.time() + self._query_time())

    def get_account(self, steamid):
        """Retrieve account data by SteamID."""
        return self.storage.load_account(steamid)

    def get_password(self, steamid):
        """Retrieve an account's password from the Mimir credential vault.

        SDA maFiles never carry a password (verified across the whole fleet), so
        we don't read them for one — the vault, keyed by the account's Steam
        login (``account_name``), is the single source of truth.
        """
        data = self.storage.load_account(steamid)
        if not data:
            return None
        login = data.get('account_name')
        if not login:
            return None
        from context import ctx  # lazy import avoids a startup import cycle
        if ctx.mimir_service:
            cred = ctx.mimir_service.get_by_login(login)
            if cred and cred.get('password'):
                return cred['password']
        return None

    def generate_code(self, shared_secret):
        if not shared_secret:
            return "N/A"
        
        timestamp = self._get_steam_time()
        time_slice = timestamp // 30
        time_bytes = struct.pack('>Q', time_slice)
        
        try:
            secret_bytes = base64.b64decode(shared_secret)
        except:
            return "ERR"
            
        hmac_obj = hmac.new(secret_bytes, time_bytes, sha1)
        digest = hmac_obj.digest()
        
        offset = digest[19] & 0xf
        code_int = struct.unpack('>I', digest[offset:offset+4])[0] & 0x7fffffff
        
        chars = '23456789BCDFGHJKMNPQRTVWXY'
        code = ''
        for _ in range(5):
            code += chars[code_int % len(chars)]
            code_int //= len(chars)
            
        return code

    def get_all_accounts_data(self):
        accounts = []
        ids = self.storage.list_accounts()
        for steamid in ids:
            data = self.storage.load_account(steamid)
            if data:
                code = self.generate_code(data.get('shared_secret'))
                accounts.append({
                    'steamid': steamid,
                    'account_name': data.get('account_name', 'Unknown'),
                    'code': code,
                    'time_remaining': 30 - (self._get_steam_time() % 30)
                })
        return accounts

    def _generate_session_id(self):
        return secrets.token_hex(16)

    def _get_cookies(self, steamid, access_token, session_id=None):
        if session_id is None:
            session_id = self._generate_session_id()
        steam_login_secure = f"{steamid}%7C%7C{access_token}"
        return {
            'steamLoginSecure': steam_login_secure,
            'sessionid': session_id,
            'mobileClient': 'android',
            'mobileClientVersion': '777777 3.6.1',
        }

    def web_session_cookie(self, min_ttl_seconds=300):
        """(steamid, cookies) for the first account with a still-fresh web session,
        for authenticated steamcommunity.com reads (e.g. Market pricehistory).

        Returns None when no account currently holds a usable web token — callers
        must degrade gracefully (public Market endpoints still work without it).
        The token is minted by a live Ratatoskr session and kept fresh by the
        scheduler keep-alive; it expires ~24h after the last login."""
        for steamid in self.storage.list_accounts():
            cookies = self.web_session_cookie_for(steamid, min_ttl_seconds)
            if cookies:
                return steamid, cookies
        return None

    def web_session_cookie_for(self, steamid, min_ttl_seconds=300):
        """Web cookies for ONE account's still-fresh session, or None.

        Same token rules as web_session_cookie, but for a specific account — used
        for per-account reads (owned games, badge card drops, store country)."""
        data = self.storage.load_account(steamid)
        if not data:
            return None
        session_data = data.get('Session') or {}
        token = self._pick_session_token(session_data)
        if token and self._token_ttl_seconds(token) >= min_ttl_seconds:
            return self._get_cookies(steamid, token, session_data.get('WebSessionId'))
        return None

    def _to_account_id(self, steamid):
        """Steam mobile confirmations expect account id, not SteamID64."""
        return str(int(steamid) - _STEAMID64_BASE)

    @staticmethod
    def _steam_json_success(value):
        return value is True or value == 1 or str(value).lower() in ('true', '1')

    @staticmethod
    def _normalize_confirmation_list(conf):
        if conf is None:
            return []
        if isinstance(conf, list):
            return conf
        if isinstance(conf, dict):
            return list(conf.values())
        return []

    def _mobileconf_error_message(self, payload, http_status):
        if not isinstance(payload, dict):
            return f'Steam returned HTTP {http_status} (non-JSON)'
        detail = payload.get('message') or payload.get('detail') or payload.get('error')
        if detail:
            return f'Steam failed confirmation fetch: {detail}'
        return 'Steam failed confirmation fetch'

    @staticmethod
    def _is_transient_steam_error(payload):
        """True for Steam's "Oh nooooooes! ... try your request again later" response.

        This is a generic "come back later" signal, not a stale-token error — a
        refresh/re-login won't clear it, so callers back off (see the backoff docs on
        the class) rather than retry immediately.
        """
        if not isinstance(payload, dict):
            return False
        blob = f"{payload.get('message', '')} {payload.get('detail', '')}".lower()
        return (
            'try your request again later' in blob
            or 'oh nooo' in blob
            or 'problem loading the confirmations' in blob
        )

    @staticmethod
    def _is_transient_status(status_code):
        """HTTP 429 (rate limited) and any 5xx mean "back off", not "log in again"."""
        return status_code == 429 or (isinstance(status_code, int) and 500 <= status_code < 600)

    @staticmethod
    def _is_needauth(payload):
        """Steam's "your web token is not accepted" answer from mobileconf."""
        return isinstance(payload, dict) and bool(payload.get('needauth'))

    def _mobileconf_cooldown_remaining(self):
        """Seconds left on the mobileconf backoff (0 if clear)."""
        remaining = self._mobileconf_cooldown_until - time.time()
        return remaining if remaining > 0 else 0

    def _cooldown_response(self):
        """Return a ready-made 'backing off' result if a cooldown is active, else None.

        Entry points call this first to avoid touching Steam while backing off.
        """
        remaining = self._mobileconf_cooldown_remaining()
        if not remaining:
            return None
        return {
            'success': False,
            'rate_limited': True,
            'message': f'Steam mobileconf backoff active — retrying in {int(remaining)}s',
        }

    def _note_transient(self, result):
        """If `result` is a transient Steam error, trip the backoff and flag it.

        Transient = the "Oh nooooooes" message, HTTP 429 / 5xx, or a connection
        error / timeout (flagged ``transient`` by the request helpers), from a
        confirmation call, a token refresh or a full login.
        Returns True when the backoff was tripped so callers can stop retrying.
        """
        if not isinstance(result, dict):
            return False
        if (
            result.get('transient')
            or self._is_transient_status(result.get('status_code'))
            or self._is_transient_steam_error(result.get('raw'))
        ):
            self._trip_mobileconf_cooldown()
            result['rate_limited'] = True
            return True
        return False

    def _trip_mobileconf_cooldown(self):
        """Start/extend the backoff window with exponential growth (capped)."""
        wait = self._mobileconf_backoff_sec
        self._mobileconf_cooldown_until = time.time() + wait
        self._mobileconf_backoff_sec = min(wait * 2, self._MOBILECONF_COOLDOWN_MAX)
        logger.error(
            f"[AUTH] mobileconf transient error — backing off {int(wait)}s "
            f"(next {int(self._mobileconf_backoff_sec)}s)"
        )

    def _reset_mobileconf_cooldown(self):
        """Clear the backoff after a successful request."""
        if self._mobileconf_backoff_sec != self._MOBILECONF_COOLDOWN_BASE:
            logger.info("[AUTH] mobileconf recovered — backoff reset")
        self._mobileconf_cooldown_until = 0
        self._mobileconf_backoff_sec = self._MOBILECONF_COOLDOWN_BASE

    def _pick_session_token(self, session_data):
        """The stored token with the most time left: WebAccessToken or AccessToken.

        WebAccessToken (from a Ratatoskr web session) used to win whenever it was
        present, even long after it expired. Now an expired WebAccessToken is
        dropped from ``session_data`` (callers persist it on their next save) and
        whichever token lives longer is returned. When neither is valid the
        mobile AccessToken (or whatever is left) is returned; callers check its
        time-to-live before using it.
        """
        web_token = session_data.get('WebAccessToken')
        mobile_token = session_data.get('AccessToken')
        web_ttl = self._token_ttl_seconds(web_token)
        if web_token and web_ttl == 0:
            session_data.pop('WebAccessToken', None)
            web_token = None
        if web_token and web_ttl >= self._token_ttl_seconds(mobile_token):
            return web_token
        return mobile_token or web_token

    @staticmethod
    def _token_ttl_seconds(token):
        """Seconds until a Steam access-token JWT expires.

        Returns 0 for a missing, expired, or unparseable token — callers treat
        that as "needs refresh". This is what keeps a long-dead cached token
        (the 190-day-stale AccessToken that caused the fleet-wide `needauth`)
        from being handed out before we try to renew it.
        """
        if not token:
            return 0
        try:
            payload = token.split('.')[1]
            payload += '=' * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            exp = claims.get('exp')
            if not exp:
                return 0
            return max(0, int(exp - time.time()))
        except Exception:
            return 0

    def _refresh_access_token(self, steamid, data):
        """Exchange RefreshToken for a new AccessToken (independent of Ratatoskr).

        Kept for documentation and in case Steam fixes it: in practice this
        endpoint answers an empty response (CLAUDE.md gotcha 6), so after the
        first empty answer in this process it is skipped and renewal goes
        straight to a full login.
        """
        if self._generate_access_token_for_app_disabled:
            return {'success': False, 'skipped': True,
                    'message': 'GenerateAccessTokenForApp skipped (returned no token earlier)'}
        session_data = data.get('Session') or {}
        refresh_token = session_data.get('RefreshToken')
        if not refresh_token:
            return {'success': False, 'message': 'No refresh token stored for this account'}

        stored_steamid = str(session_data.get('SteamID') or steamid)
        try:
            resp = requests.post(
                'https://api.steampowered.com/IAuthenticationService/GenerateAccessTokenForApp/v1/',
                data={
                    'refresh_token': refresh_token,
                    'steamid': stored_steamid,
                    # renewal_type 0 (None) is the variant that has worked at all.
                    # renewal_type 1 (Allow) intermittently returns an empty
                    # {"response":{}} — which forced a heavyweight full re-login.
                    'renewal_type': 0,
                },
                timeout=30,
                proxies=self._get_proxies(),
            )
            self._log_steam_response('GenerateAccessTokenForApp', resp)
            if resp.status_code != 200:
                return {
                    'success': False,
                    'message': f'Token refresh failed (HTTP {resp.status_code})',
                    'status_code': resp.status_code,
                    'transient': self._is_transient_status(resp.status_code),
                }

            body = resp.json().get('response', {})
            new_access = body.get('access_token')
            if not new_access:
                self._generate_access_token_for_app_disabled = True
                logger.warning("[AUTH] GenerateAccessTokenForApp returned no access_token — "
                               "skipping it for the rest of this process (full login instead)")
                return {'success': False, 'message': 'Token refresh returned no access_token'}

            session_data['AccessToken'] = new_access
            updates = {'AccessToken': new_access}
            if body.get('refresh_token'):
                session_data['RefreshToken'] = body['refresh_token']
                updates['RefreshToken'] = body['refresh_token']
            data['Session'] = session_data

            logger.info(f"[AUTH] AccessToken REFRESHED for {steamid}")
            self._merge_save_session(steamid, updates)
            logger.info(f"[STORAGE] Persisted refreshed session to {steamid}.maFile")

            return {'success': True, 'access_token': new_access, 'steamid': stored_steamid}
        except self._TRANSIENT_EXCEPTIONS as e:
            return {'success': False, 'message': f'Token refresh network error: {e}', 'transient': True}
        except Exception as e:
            return {'success': False, 'message': f'Token refresh error: {e}'}

    def _ensure_access_token(self, steamid, data, username, password, expected_steamid=None, force_refresh=False):
        """Ensure we have an access token and log persistence events."""
        session_data = data.get('Session') or {}
        refresh_token = session_data.get('RefreshToken')
        stored_steamid = session_data.get('SteamID') or steamid
        # Tokens this call throws away; removed from the maFile on save only if
        # no other thread replaced them in the meantime.
        discarded = {}

        if force_refresh:
            discarded['WebAccessToken'] = session_data.pop('WebAccessToken', None)
            discarded['AccessToken'] = session_data.pop('AccessToken', None)
            data['Session'] = session_data

        if not force_refresh:
            cached = self._pick_session_token(session_data)
            # Only reuse a cached token that is still valid — an expired one just
            # earns a `needauth` from Steam, so fall through to refresh/re-login.
            if cached and self._token_ttl_seconds(cached) > 120:
                return {'success': True, 'access_token': cached, 'steamid': stored_steamid}

        # Refresh token (survives Ratatoskr logOff)
        if refresh_token:
            refreshed = self._refresh_access_token(steamid, data)
            if refreshed.get('success'):
                return refreshed
            if refreshed.get('transient'):
                # Steam is throttling / unreachable — a full login now would only
                # add traffic; let the caller back off.
                return refreshed
            if not refreshed.get('skipped'):
                logger.error(f"[AUTH] Refresh failed for {steamid}: {refreshed.get('message')}")

        # Fallback: full login
        auth = self.begin_auth_session(username, password)
        if not auth.get('success'):
            failure = {'success': False, 'message': auth.get('message', 'Auth failed'), 'details': auth.get('details')}
            if auth.get('transient'):
                failure['transient'] = True
            return failure

        # Update local session data with results from full login
        access_token = auth['access_token']
        auth_steamid = auth.get('steamid')
        final_steamid = auth_steamid or steamid

        updates = {'AccessToken': access_token, 'SteamID': final_steamid}
        if auth.get('refresh_token'):
            updates['RefreshToken'] = auth['refresh_token']
        session_data.update(updates)
        data['Session'] = session_data

        # LOG FULL LOGIN PERSISTENCE
        logger.info(f"[AUTH] New AccessToken obtained via FULL LOGIN for {final_steamid}")
        self._merge_save_session(steamid, updates, drop_if_unchanged={'WebAccessToken': discarded.get('WebAccessToken')})
        logger.info(f"[STORAGE] Persisted new login session to {steamid}.maFile")

        result = {'success': True, 'access_token': access_token, 'steamid': final_steamid}
        if '_session' in auth:
            result['_session'] = auth['_session']
        return result

    def ensure_fresh_session(self, steamid, min_ttl_seconds=6 * 3600):
        """Proactively guarantee a usable web AccessToken, refreshing only if needed.

        Returns a small status dict for health reporting. It refreshes (or, if the
        stored RefreshToken is dead, fully re-logs-in) *only* when the stored token
        is missing or within ``min_ttl_seconds`` of expiry, so a periodic keep-alive
        sweep costs almost nothing while everything is healthy. This is the proactive
        counterpart to the on-demand renewal inside :meth:`get_confirmations`.
        """
        data = self.storage.load_account(steamid)
        if not data:
            return {'steamid': steamid, 'ok': False, 'state': 'no-account'}

        session_data = data.get('Session') or {}
        ttl = self._token_ttl_seconds(self._pick_session_token(session_data))
        if ttl >= min_ttl_seconds:
            return {'steamid': steamid, 'ok': True, 'state': 'fresh', 'ttl': ttl}

        username = data.get('account_name') or session_data.get('AccountName')
        password = self.get_password(steamid)
        result = self._ensure_access_token(
            steamid, data, username, password, expected_steamid=steamid, force_refresh=True
        )
        if result.get('success'):
            new_ttl = self._token_ttl_seconds(result.get('access_token'))
            return {'steamid': steamid, 'ok': True, 'state': 'renewed', 'ttl': new_ttl}

        # Couldn't renew — usually a missing vault password or bad credentials.
        # Surface it loudly so a broken account is noticed before the UI needs it.
        # A 429 / 5xx / network error trips the shared backoff and is flagged
        # `rate_limited` so the keep-alive sweep stops.
        rate_limited = self._note_transient(result)
        return {'steamid': steamid, 'ok': False, 'state': 'failed',
                'message': result.get('message'),
                'no_password': password is None,
                'rate_limited': rate_limited}

    def update_session_cookies(self, steamid, access_token, steam_login_secure, session_id):
        """
        Updates the session data with new cookies/tokens provided by an external service (Ratatoskr).
        """
        # Ratatoskr (steam-user) 'webSession' event gives sessionID and cookies.
        # steam-user v4+ uses the new token system, so steamLoginSecure carries the
        # access token; _get_cookies rebuilds steamLoginSecure from it on demand.
        # Ratatoskr web session — do not overwrite mobile AccessToken (invalidated on logOff)
        with self._account_lock(steamid):
            data = self.storage.load_account(steamid)
            if not data:
                return {'success': False, 'message': 'Account not found'}

            session_data = data.get('Session') or {}
            if access_token:
                session_data['WebAccessToken'] = access_token
            if session_id:
                session_data['WebSessionId'] = session_id
            data['Session'] = session_data

            try:
                self.storage.save_account(steamid, data)
                logger.info(f"[AUTH] Updated session cookies for {steamid} from external source")
                return {'success': True}
            except Exception as e:
                logger.error(f"[AUTH] Failed to save updated session for {steamid}: {e}")
                return {'success': False, 'message': str(e)}

    def clear_web_session(self, steamid):
        """Drop Ratatoskr web tokens so confirmations use mobile AccessToken/refresh."""
        with self._account_lock(steamid):
            data = self.storage.load_account(steamid)
            if not data:
                return {'success': False, 'message': 'Account not found'}

            session_data = data.get('Session') or {}
            if 'WebAccessToken' in session_data or 'WebSessionId' in session_data:
                session_data.pop('WebAccessToken', None)
                session_data.pop('WebSessionId', None)
                data['Session'] = session_data
                self.storage.save_account(steamid, data)
                logger.info(f"[AUTH] Cleared web session tokens for {steamid}")

        return {'success': True}

    def store_login_tokens(self, username, auth):
        """Save a successful ``begin_auth_session`` result into the matching maFile.

        Only when a maFile exists for the SteamID64 Steam returned AND its
        account_name is the login that was used — otherwise nothing is written.
        Returns True when the tokens were stored.
        """
        steamid = str(auth.get('steamid') or '')
        if not self._is_steamid64(steamid) or not auth.get('access_token'):
            return False
        with self._account_lock(steamid):
            data = self.storage.load_account(steamid)
            if not data or (data.get('account_name') or '').lower() != (username or '').lower():
                return False
            updates = {'AccessToken': auth['access_token'], 'SteamID': steamid}
            if auth.get('refresh_token'):
                updates['RefreshToken'] = auth['refresh_token']
            self._merge_save_session(steamid, updates)
        logger.info(f"[AUTH] Stored login session for {steamid} from Add Account")
        return True

    def begin_auth_session(self, username, password):
        """Full credential login (the only reliable way to mint a web token).

        Failures caused by Steam throttling or being unreachable (HTTP 429 / 5xx,
        connection errors, timeouts) are flagged ``transient`` so callers back off.
        """
        if not username:
            return {'success': False, 'message': 'No account name for this account'}
        if not password:
            return {'success': False, 'message': 'No password for this account — add it to the Mimir vault'}

        try:
            import rsa
        except Exception as e:
            return {'success': False, 'message': f'RSA error: {e}'}

        session = requests.Session()
        proxies = self._get_proxies()
        if proxies: session.proxies.update(proxies)

        def transient_failure(what, status_code=None, error=None):
            detail = f'HTTP {status_code}' if status_code is not None else str(error)
            return {'success': False, 'transient': True,
                    'message': f'{what} failed: {detail} (Steam throttling or unreachable)',
                    'details': {'status_code': status_code} if status_code is not None else None}

        try:
            rsa_resp = session.get(
                'https://api.steampowered.com/IAuthenticationService/GetPasswordRSAPublicKey/v1/',
                params={'account_name': username}, timeout=30
            )
            self._log_steam_response('GetPasswordRSAPublicKey', rsa_resp)
            if self._is_transient_status(rsa_resp.status_code):
                return transient_failure('RSA fetch', status_code=rsa_resp.status_code)
            rsa_data = rsa_resp.json()['response']
            public_key = rsa.PublicKey(int(rsa_data['publickey_mod'], 16), int(rsa_data['publickey_exp'], 16))
            encrypted_password = base64.b64encode(rsa.encrypt(password.encode('utf-8'), public_key)).decode('utf-8')
        except self._TRANSIENT_EXCEPTIONS as e:
            return transient_failure('RSA fetch', error=e)
        except Exception as e:
            return {'success': False, 'message': f'RSA fetch failed: {e}'}

        begin_auth_data = {
            'account_name': username,
            'encrypted_password': encrypted_password,
            'encryption_timestamp': rsa_data['timestamp'],
            'remember_login': 'false', 'platform_type': '2', 'persistence': '1', 'website_id': 'Mobile',
        }

        try:
            resp = session.post(
                'https://api.steampowered.com/IAuthenticationService/BeginAuthSessionViaCredentials/v1/',
                data=begin_auth_data, timeout=30
            )
            self._log_steam_response('BeginAuthSessionViaCredentials', resp)
            if resp.status_code == 429:
                return {'success': False, 'transient': True, 'message': 'Rate limited (429). Wait.', 'details': {'status_code': 429}}
            if self._is_transient_status(resp.status_code):
                return transient_failure('Auth start', status_code=resp.status_code)

            res_data = resp.json()['response']
            client_id, request_id = res_data.get('client_id'), res_data.get('request_id')
            steamid = res_data.get('steamid')
        except self._TRANSIENT_EXCEPTIONS as e:
            return transient_failure('Auth start', error=e)
        except Exception as e:
            return {'success': False, 'message': f'Auth start failed: {e}'}

        if not client_id or not request_id:
            # Wrong password / unknown account: Steam answers an empty response.
            return {'success': False, 'message': 'Steam rejected the login (check the account name and password)',
                    'details': {'eresult': resp.headers.get('x-eresult')}}

        try:
            if any((c or {}).get('confirmation_type') == 3 for c in (res_data.get('allowed_confirmations', []))):
                shared_secret = self._find_shared_secret(username, steamid)
                if not shared_secret:
                    return {'success': False, 'message': 'Guard required but no secret found.'}

                update_data = {'client_id': client_id, 'steamid': steamid, 'code': self.generate_code(shared_secret), 'code_type': '3'}
                guard_resp = session.post('https://api.steampowered.com/IAuthenticationService/UpdateAuthSessionWithSteamGuardCode/v1/', data=update_data, timeout=30)
                self._log_steam_response('UpdateAuthSessionWithSteamGuardCode', guard_resp)
                if self._is_transient_status(guard_resp.status_code):
                    return transient_failure('Steam Guard code', status_code=guard_resp.status_code)
                eresult = guard_resp.headers.get('x-eresult')
                if guard_resp.status_code != 200 or (eresult is not None and str(eresult) != '1'):
                    # Rejected code (wrong shared_secret or clock) — polling would
                    # only wait out 30 s for nothing.
                    return {'success': False,
                            'message': f'Steam rejected the Steam Guard code (HTTP {guard_resp.status_code}, eresult {eresult})',
                            'details': {'status_code': guard_resp.status_code, 'eresult': eresult}}

            for _ in range(30):
                time.sleep(1)
                poll_resp = session.post('https://api.steampowered.com/IAuthenticationService/PollAuthSessionStatus/v1/', data={'client_id': client_id, 'request_id': request_id}, timeout=30)
                if poll_resp.status_code == 200:
                    tokens = poll_resp.json().get('response', {})
                    if tokens.get('access_token'):
                        return {'success': True, 'access_token': tokens['access_token'], 'refresh_token': tokens.get('refresh_token'), 'steamid': steamid, '_session': session}
                elif self._is_transient_status(poll_resp.status_code):
                    return transient_failure('Login polling', status_code=poll_resp.status_code)
        except self._TRANSIENT_EXCEPTIONS as e:
            return transient_failure('Login', error=e)

        return {'success': False, 'message': 'Polling timed out.'}

    def _find_shared_secret(self, username, steamid=None):
        if steamid:
            data = self.storage.load_account(str(steamid))
            if data and data.get('shared_secret'): return data['shared_secret']
        for sid in self.storage.list_accounts():
            data = self.storage.load_account(sid)
            if data and (data.get('account_name') or '').lower() == username.lower():
                return data.get('shared_secret')
        return None

    def _confirmation_param_variants(self, steamid, data, identity_secret, tag):
        """
        mobileconf's `a` parameter must be the full SteamID64. Steam deprecated the
        32-bit account id — sending it now returns "Oh nooooooes! ... try again later"
        for every request. Verified head-to-head: a=SteamID64 loads confirmations,
        a=account_id fails, regardless of tag. Try m=react first, m=android as fallback.
        """
        timestamp = self._get_steam_time()
        device_id = data.get('device_id') or self._generate_device_id(steamid)
        conf_key = self._generate_confirmation_key(identity_secret, tag, timestamp)
        base = {'p': device_id, 'k': conf_key, 't': timestamp, 'tag': tag, 'a': str(steamid)}
        return [
            {**base, 'm': 'react'},
            {**base, 'm': 'android'},
        ]

    def _fetch_confirmations_once(self, steamid, data, access_token):
        identity_secret = data.get('identity_secret')
        if not identity_secret:
            return {'success': False, 'message': 'identity_secret missing from maFile'}

        session_data = data.get('Session') or {}
        session_id = session_data.get('WebSessionId')
        cookies = self._get_cookies(steamid, access_token, session_id=session_id)
        last_payload = None
        last_status = None

        for params in self._confirmation_param_variants(steamid, data, identity_secret, 'conf'):
            try:
                resp = requests.get(
                    'https://steamcommunity.com/mobileconf/getlist',
                    params=params,
                    headers={'User-Agent': 'okhttp/3.12.12'},
                    cookies=cookies,
                    timeout=30,
                    proxies=self._get_proxies(),
                )
            except self._TRANSIENT_EXCEPTIONS as e:
                return {'success': False, 'message': f'Steam unreachable: {e}', 'transient': True}
            self._log_steam_response('MobileConfGetList', resp)
            last_status = resp.status_code
            if self._is_transient_status(resp.status_code):
                # Throttled / Steam down — the other variant would fail the same way.
                break

            text = (resp.text or '').strip()
            if not text.startswith('{'):
                continue

            try:
                payload = resp.json()
            except ValueError:
                continue

            last_payload = payload
            if self._steam_json_success(payload.get('success')):
                return {
                    'success': True,
                    'confirmations': self._normalize_confirmation_list(payload.get('conf')),
                }
            if self._is_needauth(payload):
                # The token was refused; the m= variant does not change that.
                break

        return {
            'success': False,
            'message': self._mobileconf_error_message(last_payload, last_status),
            'raw': last_payload,
            'status_code': last_status,
        }

    def _parse_ajaxop_response(self, result):
        if isinstance(result, dict):
            if result.get('success'):
                return {'success': True}
            return {
                'success': False,
                'message': result.get('message', 'Authentication failed'),
                'raw': result,
            }
        if isinstance(result, Exception):
            return {'success': False, 'message': str(result)}

        if not hasattr(result, 'json'):
            return {'success': False, 'message': 'Invalid confirmation response'}

        if result.status_code != 200:
            return {'success': False, 'message': f'Confirmation action failed (HTTP {result.status_code})'}

        text = (result.text or '').strip()
        if not text.startswith('{'):
            return {'success': False, 'message': 'Steam returned non-JSON for confirmation action'}

        try:
            payload = result.json()
        except ValueError as e:
            return {'success': False, 'message': f'Invalid JSON from Steam: {e}'}

        if self._steam_json_success(payload.get('success')):
            return {'success': True}
        return {
            'success': False,
            'message': self._mobileconf_error_message(payload, result.status_code),
            'raw': payload,
        }

    def get_confirmations(self, steamid):
        data = self.storage.load_account(steamid)
        if not data:
            return {'success': False, 'message': 'Account not found'}

        identity_secret = data.get('identity_secret')
        if not identity_secret:
            return {'success': False, 'message': 'identity_secret missing from maFile'}

        username = data.get('account_name') or data.get('Session', {}).get('AccountName')
        password = self.get_password(steamid)  # maFile field first, then Mimir vault

        backoff = self._cooldown_response()
        if backoff:
            return backoff

        token_result = self._ensure_access_token(steamid, data, username, password, expected_steamid=steamid)
        if not token_result.get('success'):
            self._note_transient(token_result)
            return token_result

        try:
            result = self._fetch_confirmations_once(steamid, data, token_result['access_token'])
            if result.get('success'):
                self._reset_mobileconf_cooldown()
                return result
            # Throttled / unreachable: a session refresh would only add traffic.
            if self._note_transient(result):
                return result

            # First fetch failed. Try exactly one session refresh + retry to cover a
            # genuinely stale token. Only one refresh happens (the cooldown gates any
            # further attempts), so this can't spiral into a login storm.
            logger.error(f"[AUTH] Confirmation fetch failed for {steamid}, refreshing session once...")
            data = self.storage.load_account(steamid) or data
            refresh_result = self._ensure_access_token(
                steamid, data, username, password, expected_steamid=steamid, force_refresh=True
            )

            if refresh_result.get('success'):
                data = self.storage.load_account(steamid) or data
                retry = self._fetch_confirmations_once(steamid, data, refresh_result['access_token'])
                if retry.get('success'):
                    self._reset_mobileconf_cooldown()
                    return retry
                result = retry  # classify the post-refresh failure below
            else:
                # Couldn't get a fresh token — session expired beyond refresh
                # (re-import / re-authenticate the account).
                result['refresh'] = refresh_result
                if self._note_transient(refresh_result):
                    result['rate_limited'] = True
                    return result

            # Still failing after a fresh session → transient "try again later" → back off.
            self._note_transient(result)
            return result
        except Exception as e:
            return {'success': False, 'message': f'Fetch error: {e}'}

    def _generate_device_id(self, steamid):
        hexed = sha1(str(steamid).encode('ascii')).hexdigest()
        return f"android:{hexed[:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:32]}"

    def _generate_confirmation_key(self, identity_secret, tag, timestamp):
        buffer = struct.pack('>Q', timestamp) + tag.encode('ascii')
        key = hmac.new(base64.b64decode(identity_secret), buffer, sha1).digest()
        return base64.b64encode(key).decode('ascii')

    def act_on_confirmation(self, steamid, cid, ck, operation='allow'):
        """Approve or deny a specific confirmation with a single retry logic."""
        data = self.storage.load_account(steamid)
        if not data:
            return {'success': False, 'message': 'Account not found'}

        identity_secret = data.get('identity_secret')
        session_data = data.get('Session', {})
        username = data.get('account_name') or session_data.get('AccountName')
        password = self.get_password(steamid)  # maFile field first, then Mimir vault

        backoff = self._cooldown_response()
        if backoff:
            return backoff

        def attempt_action(force_refresh=False):
            token_result = self._ensure_access_token(
                steamid, data, username, password, expected_steamid=steamid, force_refresh=force_refresh
            )
            if not token_result.get('success'):
                return token_result

            tag = 'accept' if operation == 'allow' else 'reject'
            session_id = session_data.get('WebSessionId')
            cookies = self._get_cookies(steamid, token_result['access_token'], session_id=session_id)
            last_payload = None
            last_status = None

            for base_params in self._confirmation_param_variants(steamid, data, identity_secret, tag):
                params = {
                    **base_params,
                    'op': operation,
                    'cid': cid,
                    'ck': ck,
                }
                try:
                    resp = requests.get(
                        'https://steamcommunity.com/mobileconf/ajaxop',
                        params=params,
                        headers={'User-Agent': 'okhttp/3.12.12'},
                        cookies=cookies,
                        timeout=30,
                        proxies=self._get_proxies(),
                    )
                    self._log_steam_response('MobileConfAjaxOp', resp)
                    last_status = resp.status_code
                    parsed = self._parse_ajaxop_response(resp)
                    if parsed.get('success'):
                        return parsed
                    last_payload = parsed.get('raw')
                except self._TRANSIENT_EXCEPTIONS as e:
                    return {'success': False, 'message': f'Steam unreachable: {e}', 'transient': True}
                except Exception as e:
                    last_payload = {'error': str(e)}
                if self._is_transient_status(last_status) or self._is_needauth(last_payload):
                    break  # the other m= variant cannot fix a throttle or a refused token

            return {
                'success': False,
                'message': self._mobileconf_error_message(last_payload, last_status),
                'raw': last_payload,
                'status_code': last_status,
            }

        parsed = attempt_action()
        if parsed.get('success'):
            self._reset_mobileconf_cooldown()
            return parsed

        # Transient "try again later" — back off; a re-login won't clear it.
        if self._note_transient(parsed):
            return parsed

        logger.error(f"First attempt failed for confirmation {cid}. Refreshing session and retrying...")
        retry = attempt_action(force_refresh=True)
        if retry.get('success'):
            self._reset_mobileconf_cooldown()
            return retry
        self._note_transient(retry)
        return retry

    def act_on_confirmations_batch(self, steamid, items, operation='allow'):
        """Approve/deny many confirmations in ONE mobileconf/multiajaxop call.

        `items` is a list of (cid, ck) pairs. Steam accepts repeated cid[]/ck[]
        form fields and acts on all of them at once — far faster than issuing a
        request per confirmation. Mirrors act_on_confirmation's token/backoff/
        retry handling.
        """
        items = [(str(c), str(k)) for c, k in items if c and k]
        if not items:
            return {'success': True, 'accepted': 0}

        data = self.storage.load_account(steamid)
        if not data:
            return {'success': False, 'message': 'Account not found'}

        identity_secret = data.get('identity_secret')
        session_data = data.get('Session', {})
        username = data.get('account_name') or session_data.get('AccountName')
        password = self.get_password(steamid)  # maFile field first, then Mimir vault

        backoff = self._cooldown_response()
        if backoff:
            return backoff

        cids = [c for c, _ in items]
        cks = [k for _, k in items]

        def attempt_action(force_refresh=False):
            token_result = self._ensure_access_token(
                steamid, data, username, password, expected_steamid=steamid, force_refresh=force_refresh
            )
            if not token_result.get('success'):
                return token_result

            tag = 'accept' if operation == 'allow' else 'reject'
            session_id = session_data.get('WebSessionId')
            cookies = self._get_cookies(steamid, token_result['access_token'], session_id=session_id)
            last_payload = None
            last_status = None

            for base_params in self._confirmation_param_variants(steamid, data, identity_secret, tag):
                form = {**base_params, 'op': operation, 'cid[]': cids, 'ck[]': cks}
                try:
                    resp = requests.post(
                        'https://steamcommunity.com/mobileconf/multiajaxop',
                        data=form,
                        headers={'User-Agent': 'okhttp/3.12.12'},
                        cookies=cookies,
                        timeout=30,
                        proxies=self._get_proxies(),
                    )
                    self._log_steam_response('MobileConfMultiAjaxOp', resp)
                    last_status = resp.status_code
                    parsed = self._parse_ajaxop_response(resp)
                    if parsed.get('success'):
                        parsed['accepted'] = len(items)
                        return parsed
                    last_payload = parsed.get('raw')
                except self._TRANSIENT_EXCEPTIONS as e:
                    return {'success': False, 'message': f'Steam unreachable: {e}', 'transient': True}
                except Exception as e:
                    last_payload = {'error': str(e)}
                if self._is_transient_status(last_status) or self._is_needauth(last_payload):
                    break  # the other m= variant cannot fix a throttle or a refused token

            return {
                'success': False,
                'message': self._mobileconf_error_message(last_payload, last_status),
                'raw': last_payload,
                'status_code': last_status,
            }

        parsed = attempt_action()
        if parsed.get('success'):
            self._reset_mobileconf_cooldown()
            return parsed

        if self._note_transient(parsed):
            return parsed

        logger.error(f"Batch confirm failed for {steamid} ({len(items)} items). Refreshing session and retrying...")
        retry = attempt_action(force_refresh=True)
        if retry.get('success'):
            self._reset_mobileconf_cooldown()
            return retry
        self._note_transient(retry)
        return retry