import time
import hmac
import struct
import base64
import requests
import json
import rsa
from hashlib import sha1
from storage import SecureStorage

class SteamService:
    def __init__(self):
        self.storage = SecureStorage()
        self.time_offset = None
        self.last_time_sync = 0

    def import_account(self, mafile_data):
        """Imports a plain JSON maFile, encrypts it, and saves it."""
        if isinstance(mafile_data, str):
            try:
                mafile_data = json.loads(mafile_data)
            except json.JSONDecodeError:
                return {'error': 'Invalid JSON format'}
        
        # Validation
        required_fields = ['shared_secret', 'identity_secret', 'Session']
        for field in required_fields:
            if field not in mafile_data:
                return {'error': f'Missing required field: {field}'}
        
        if 'SteamID' not in mafile_data.get('Session', {}):
             return {'error': 'Missing SteamID in Session'}

        steamid = str(mafile_data['Session']['SteamID'])
        
        # Save (this automatically handles encryption via SecureStorage)
        self.storage.save_account(steamid, mafile_data)
        
        return {'status': 'success', 'steamid': steamid}

    def remove_account(self, steamid):
        return self.storage.delete_account(steamid)

    def _query_time(self):
        current_time = time.time()
        if self.time_offset is None or (current_time - self.last_time_sync) > 300:
            try:
                resp = requests.post('https://api.steampowered.com/ITwoFactorService/QueryTime/v0001', timeout=10)
                server_time = int(resp.json()['response']['server_time'])
                self.time_offset = server_time - current_time
                self.last_time_sync = current_time
            except:
                if self.time_offset is None:
                    self.time_offset = 0
        return self.time_offset

    def _get_steam_time(self):
        return int(time.time() + self._query_time())

    def generate_code(self, shared_secret):
        if not shared_secret:
            return "N/A"
        
        timestamp = self._get_steam_time()
        time_slice = timestamp // 30
        
        # Pack timestamp as 8-byte big-endian
        time_bytes = struct.pack('>Q', time_slice)
        
        # Decode secret
        try:
            secret_bytes = base64.b64decode(shared_secret)
        except:
            return "ERR"
            
        # HMAC-SHA1
        hmac_obj = hmac.new(secret_bytes, time_bytes, sha1)
        digest = hmac_obj.digest()
        
        # Dynamic truncation
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

    # --- Enrollment Logic (Simplified for API) ---
    
    def fetch_rsa_key(self, username):
        session = requests.Session()
        resp = session.get(
            'https://api.steampowered.com/IAuthenticationService/GetPasswordRSAPublicKey/v1/',
            params={'account_name': username}, timeout=10
        )
        if resp.status_code != 200: return None
        return resp.json()['response']

    def begin_auth_session(self, username, password):
        # 1. Get RSA
        rsa_data = self.fetch_rsa_key(username)
        if not rsa_data: return {'error': 'Failed to get RSA key'}
        
        # 2. Encrypt Password
        rsa_mod = int(rsa_data['publickey_mod'], 16)
        rsa_exp = int(rsa_data['publickey_exp'], 16)
        pub_key = rsa.PublicKey(rsa_mod, rsa_exp)
        encrypted_pw = base64.b64encode(rsa.encrypt(password.encode('utf-8'), pub_key)).decode('utf-8')
        
        # 3. Begin Session
        params = {
            'account_name': username,
            'encrypted_password': encrypted_pw,
            'encryption_timestamp': rsa_data['timestamp'],
            'remember_login': 'false',
            'platform_type': '2',
            'persistence': '1',
            'website_id': 'Mobile'
        }
        
        resp = requests.post(
            'https://api.steampowered.com/IAuthenticationService/BeginAuthSessionViaCredentials/v1/',
            data=params, timeout=10
        )
        
        if resp.status_code != 200: return {'error': 'Auth request failed'}
        
        result = resp.json().get('response', {})
        # Return necessary data to frontend to handle next steps (SMS or 2FA)
        return result

    # Note: Full enrollment flow requires state management (handling SMS).
    # For now, we'll focus on the data viewer aspect as a first step.
    
    # --- Confirmation Logic ---
    
    def _generate_device_id(self, steamid):
        hexed = sha1(str(steamid).encode('ascii')).hexdigest()
        return f"android:{hexed[:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:32]}"

    def _generate_confirmation_key(self, identity_secret, tag, timestamp):
        buffer = struct.pack('>Q', timestamp) + tag.encode('ascii')
        key = hmac.new(base64.b64decode(identity_secret), buffer, sha1).digest()
        return base64.b64encode(key).decode('ascii')

    def get_confirmations(self, steamid):
        data = self.storage.load_account(steamid)
        if not data: return []
        
        identity_secret = data.get('identity_secret')
        if not identity_secret: return []
        
        timestamp = int(time.time())
        device_id = data.get('device_id', self._generate_device_id(steamid))
        conf_key = self._generate_confirmation_key(identity_secret, 'conf', timestamp)
        
        params = {
            'p': device_id,
            'a': steamid,
            'k': conf_key,
            't': timestamp,
            'm': 'react',
            'tag': 'conf'
        }
        
        # Need cookies! (Login logic needed here to get session cookies if not present)
        # This is complex because we need fresh cookies. 
        # For this iteration, we'll stub this or need to implement full login refresh.
        # Returning empty for now to be safe until full login flow is ported.
        return []
