import time
import hmac
import struct
import base64
import requests
import json
import secrets
import os
from datetime import datetime
from hashlib import sha1
from storage import SecureStorage


class SteamService:
    def __init__(self):
        self.storage = SecureStorage()
        self.time_offset = None
        self.last_time_sync = 0

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
                print("Warning: SOCKS_PROXY set but 'requests[socks]' not installed.")
        
        return proxies if proxies else None

    def _log_steam_response(self, label, resp):
        """Debug helper: log Steam HTTP responses."""
        try:
            body = resp.text
        except Exception:
            body = '<no text>'
        snippet = body[:1000]
        try:
            method = getattr(resp.request, 'method', 'UNKNOWN')
            url = getattr(resp.request, 'url', 'UNKNOWN')
        except Exception:
            method = 'UNKNOWN'
            url = 'UNKNOWN'

        timestamp = datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        log_header = (
            f"[{timestamp}] [STEAM DEBUG] {label} "
            f"{method} {url} status={resp.status_code} len={len(body)}"
        )
        log_body = f"[STEAM DEBUG] {label} body:\n{snippet}\n--- END {label} ---\n"

        print(log_header)
        print(log_body)

        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            log_dir = os.path.join(base_dir, 'logs')
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, 'steam_debug.log')
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(log_header + "\n")
                f.write(log_body + "\n")
        except Exception as e:
            print(f"[STEAM DEBUG] Failed to write log file: {e}")

    def import_account(self, mafile_data, filename=None):
        """Fixed import to avoid JS rounding errors by using the filename."""
        if isinstance(mafile_data, str):
            mafile_data = json.loads(mafile_data)
        
        # 1. Source the SteamID from the filename (e.g., '76561198123456789.maFile')
        steamid = None
        if filename:
            # Splits by dot and take the first part: '76561198123456789'
            steamid = str(filename.split('.')[0]) 
            print(f"[IMPORT] Using SteamID from filename: {steamid}")

        # 2. Fallback to internal data ONLY as a string
        if not steamid or not steamid.isdigit():
            steamid = str(mafile_data.get('Session', {}).get('SteamID', ''))

        if not steamid or len(steamid) < 10:
            return {'error': 'Invalid SteamID. Please ensure the filename is your SteamID.maFile'}

        # 3. Synchronize internal session data with the chosen string ID
        if 'Session' not in mafile_data:
            mafile_data['Session'] = {}
        mafile_data['Session']['SteamID'] = steamid
        
        try:
            # Save as <steamid>.maFile
            self.storage.save_account(steamid, mafile_data)
            return {'status': 'success', 'steamid': steamid}
        except Exception as e:
            return {'error': f'Failed to save: {str(e)}'}

    def remove_account(self, steamid):
        return self.storage.delete_account(steamid)
    
    def remove_all_accounts(self):
        accounts = self.storage.list_accounts()
        count = 0
        for steamid in accounts:
            if self.storage.delete_account(steamid):
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
        """Retrieve the stored password for an account."""
        data = self.storage.load_account(steamid)
        # Note: In a real scenario, this should be encrypted. 
        # For this implementation, we assume it's stored in 'account_password' or similar field from import.
        # If it's encrypted in maFile (e.g. SDA), we might need to decrypt it if we knew the key.
        # However, the user said "decrypt it and use the password from there".
        # Standard maFiles from SDA usually don't store the password unless explicitly added or in a specific format.
        # But if the user says it's there, let's look for likely fields.
        if not data: return None
        return data.get('account_password') or data.get('password') or data.get('Session', {}).get('Password')

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

    def _ensure_access_token(self, steamid, data, username, password, expected_steamid=None):
        """Ensure we have an access token and log persistence events."""
        session_data = data.get('Session') or {}
        access_token = session_data.get('AccessToken')
        refresh_token = session_data.get('RefreshToken')
        stored_steamid = session_data.get('SteamID') or steamid

        # 1) Try existing token
        if access_token:
            return {'success': True, 'access_token': access_token, 'steamid': stored_steamid}

        # 2) Try refresh token
        if refresh_token:
            try:
                # ... (refresh request logic) ...
                if resp.status_code == 200:
                    new_access = resp.json().get('response', {}).get('access_token')
                    if new_access:
                        session_data['AccessToken'] = new_access
                        data['Session'] = session_data
                        
                        # LOG REFRESH PERSISTENCE
                        print(f"[AUTH] AccessToken REFRESHED for {steamid}")
                        self.storage.save_account(steamid, data)
                        print(f"[STORAGE] Persisted refreshed session to {steamid}.maFile")
                        
                        return {'success': True, 'access_token': new_access, 'steamid': stored_steamid}
            except Exception as e:
                print(f"[AUTH] Refresh failed for {steamid}: {e}")

        # 3) Fallback: full login
        auth = self.begin_auth_session(username, password)
        if not auth.get('success'):
            return {'success': False, 'message': auth.get('message', 'Auth failed'), 'details': auth.get('details')}

        # Update local session data with results from full login
        access_token = auth['access_token']
        auth_steamid = auth.get('steamid')
        final_steamid = auth_steamid or steamid
        
        session_data.update({'AccessToken': access_token, 'SteamID': final_steamid})
        if auth.get('refresh_token'):
            session_data['RefreshToken'] = auth['refresh_token']
        
        data['Session'] = session_data

        # LOG FULL LOGIN PERSISTENCE
        print(f"[AUTH] New AccessToken obtained via FULL LOGIN for {final_steamid}")
        self.storage.save_account(steamid, data)
        print(f"[STORAGE] Persisted new login session to {steamid}.maFile")

        result = {'success': True, 'access_token': access_token, 'steamid': final_steamid}
        if '_session' in auth:
            result['_session'] = auth['_session']
        return result

    def update_session_cookies(self, steamid, access_token, steam_login_secure, session_id):
        """
        Updates the session data with new cookies/tokens provided by an external service (Ratatoskr).
        """
        data = self.storage.load_account(steamid)
        if not data:
            return {'success': False, 'message': 'Account not found'}

        session_data = data.get('Session') or {}
        
        # Update fields
        # Note: 'steamLoginSecure' usually contains the access token if it's the new format, 
        # or we might receive the raw components.
        # Ratatoskr (steam-user) 'webSession' event gives sessionID and cookies.
        # Cookies are usually strings like 'steamLoginSecure=...'
        
        # We trust the caller to provide valid data
        if access_token:
            session_data['AccessToken'] = access_token
        
        # If we have a full steamLoginSecure cookie value (steamid%7C%7Ctoken), we can extract token if needed,
        # but for requests, we construct headers/cookies dynamically.
        # The key persistence is AccessToken for MobileAPI and steamLoginSecure for Community scraping.
        
        # However, steam_service._get_cookies constructs steamLoginSecure FROM AccessToken.
        # If the external service gives us a steamLoginSecure that ISN'T based on AccessToken 
        # (e.g. old session style, though unlikely for mobile), we might have a mismatch.
        # steam-user v4+ uses the new token system, so steamLoginSecure should contain the access token.
        
        data['Session'] = session_data
        
        try:
            self.storage.save_account(steamid, data)
            print(f"[AUTH] Updated session cookies for {steamid} from external source")
            return {'success': True}
        except Exception as e:
            print(f"[AUTH] Failed to save updated session for {steamid}: {e}")
            return {'success': False, 'message': str(e)}

    def begin_auth_session(self, username, password):
        try:
            import rsa
        except Exception as e:
            return {'success': False, 'message': f'RSA error: {e}'}

        session = requests.Session()
        proxies = self._get_proxies()
        if proxies: session.proxies.update(proxies)

        try:
            rsa_resp = session.get(
                'https://api.steampowered.com/IAuthenticationService/GetPasswordRSAPublicKey/v1/',
                params={'account_name': username}, timeout=30
            )
            self._log_steam_response('GetPasswordRSAPublicKey', rsa_resp)
            rsa_data = rsa_resp.json()['response']
            public_key = rsa.PublicKey(int(rsa_data['publickey_mod'], 16), int(rsa_data['publickey_exp'], 16))
            encrypted_password = base64.b64encode(rsa.encrypt(password.encode('utf-8'), public_key)).decode('utf-8')
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
                return {'success': False, 'message': 'Rate limited (429). Wait.', 'details': {'status_code': 429}}
            
            res_data = resp.json()['response']
            client_id, request_id = res_data.get('client_id'), res_data.get('request_id')
            steamid = res_data.get('steamid')
        except Exception as e:
            return {'success': False, 'message': f'Auth start failed: {e}'}

        if any((c or {}).get('confirmation_type') == 3 for c in (res_data.get('allowed_confirmations', []))):
            shared_secret = self._find_shared_secret(username, steamid)
            if not shared_secret:
                return {'success': False, 'message': 'Guard required but no secret found.'}

            update_data = {'client_id': client_id, 'steamid': steamid, 'code': self.generate_code(shared_secret), 'code_type': '3'}
            session.post('https://api.steampowered.com/IAuthenticationService/UpdateAuthSessionWithSteamGuardCode/v1/', data=update_data, timeout=30)

        for _ in range(30):
            time.sleep(1)
            poll_resp = session.post('https://api.steampowered.com/IAuthenticationService/PollAuthSessionStatus/v1/', data={'client_id': client_id, 'request_id': request_id}, timeout=30)
            if poll_resp.status_code == 200:
                tokens = poll_resp.json().get('response', {})
                if tokens.get('access_token'):
                    return {'success': True, 'access_token': tokens['access_token'], 'refresh_token': tokens.get('refresh_token'), 'steamid': steamid, '_session': session}
        
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

    def get_confirmations(self, steamid):
        data = self.storage.load_account(steamid)
        if not data: return {'success': False, 'message': 'Account not found'}
        
        identity_secret = data.get('identity_secret')
        username = data.get('account_name') or data.get('Session', {}).get('AccountName')
        password = data.get('account_password')

        token_result = self._ensure_access_token(steamid, data, username, password, expected_steamid=steamid)
        if not token_result.get('success'): return token_result

        timestamp = int(time.time())
        conf_key = self._generate_confirmation_key(identity_secret, 'conf', timestamp)
        params = {'p': data.get('device_id') or self._generate_device_id(steamid), 'a': steamid, 'k': conf_key, 't': timestamp, 'm': 'react', 'tag': 'conf'}

        try:
            resp = requests.get('https://steamcommunity.com/mobileconf/getlist', params=params, headers={'User-Agent': 'okhttp/3.12.12'}, cookies=self._get_cookies(steamid, token_result['access_token']), timeout=30, proxies=self._get_proxies())
            self._log_steam_response('MobileConfGetList', resp)
            payload = resp.json()
            if payload.get('success'): return {'success': True, 'confirmations': payload.get('conf', [])}
            return {'success': False, 'message': 'Steam failed confirmation fetch', 'raw': payload}
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
        password = data.get('account_password')

        def attempt_action():
            token_result = self._ensure_access_token(steamid, data, username, password, expected_steamid=steamid)
            if not token_result.get('success'):
                return token_result

            timestamp = int(time.time())
            tag = 'accept' if operation == 'allow' else 'reject'
            conf_key = self._generate_confirmation_key(identity_secret, tag, timestamp)
            
            params = {
                'op': operation,
                'p': data.get('device_id') or self._generate_device_id(steamid),
                'a': steamid,
                'k': conf_key,
                't': timestamp,
                'm': 'react',
                'tag': tag,
                'cid': cid,
                'ck': ck,
            }
            
            try:
                resp = requests.get(
                    'https://steamcommunity.com/mobileconf/ajaxop',
                    params=params,
                    headers={'User-Agent': 'okhttp/3.12.12'},
                    cookies=self._get_cookies(steamid, token_result['access_token']),
                    timeout=30,
                    proxies=self._get_proxies()
                )
                self._log_steam_response('MobileConfAjaxOp', resp)
                return resp
            except Exception as e:
                return e

        # First Attempt
        result = attempt_action()
        
        # Check for failure to trigger retry
        is_failed = (
            isinstance(result, Exception) or 
            (hasattr(result, 'status_code') and result.status_code != 200) or 
            (isinstance(result, dict) and not result.get('success')) or
            (hasattr(result, 'json') and not result.json().get('success'))
        )

        if is_failed:
            print(f"First attempt failed for confirmation {cid}. Retrying once...")
            result = attempt_action() # Final attempt
            
            # Final verification
            if isinstance(result, Exception) or (hasattr(result, 'status_code') and result.status_code != 200):
                return {'success': False, 'message': 'Action failed after single retry.'}
            
            final_payload = result.json() if hasattr(result, 'json') else result
            if not final_payload.get('success'):
                return {'success': False, 'message': 'Steam reported failure on retry.', 'raw': final_payload}

        return {'success': True}