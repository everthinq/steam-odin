import os
import json
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pathlib import Path

# In a real app, this should be derived from a user password or a secure vault.
# For this migration, we'll use an environment variable or a generated key file.
SECRET_KEY = os.environ.get('HEIMDALL_SECRET_KEY', 'default-insecure-key-change-me')

class SecureStorage:
    def __init__(self, storage_dir='maFiles'):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(exist_ok=True)
        self.fernet = self._get_fernet_key()

    def _get_fernet_key(self):
        # Derive a 32-byte key from the secret
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'heimdall-salt', # In prod, salt should be random and stored
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(SECRET_KEY.encode()))
        return Fernet(key)

    def save_account(self, steamid, data):
        """Encrypts and saves account data with persistence logging."""
        json_data = json.dumps(data)
        encrypted_data = self.fernet.encrypt(json_data.encode())
        
        file_path = self.storage_dir / f"{steamid}.maFile"
        with open(file_path, 'wb') as f:
            f.write(encrypted_data)
        
        # ADD THIS LOG LINE
        print(f"[STORAGE] Successfully persisted encrypted data for SteamID: {steamid}")
        return str(file_path)

    def load_account(self, steamid):
        """Loads and decrypts account data."""
        file_path = self.storage_dir / f"{steamid}.maFile"
        if not file_path.exists():
            self.storage_dir.mkdir(exist_ok=True)
        
        with open(file_path, 'rb') as f:
            encrypted_data = f.read()
        
        try:
            decrypted_data = self.fernet.decrypt(encrypted_data)
            return json.loads(decrypted_data.decode())
        except Exception as e:
            print(f"Error decrypting {steamid}: {e}")
            return None

    def list_accounts(self):
        """Lists all encrypted accounts."""
        accounts = []
        for file_path in self.storage_dir.glob('*.maFile'):
            # steamid is the filename without extension
            accounts.append(file_path.stem)
        return accounts

    def delete_account(self, steamid):
        file_path = self.storage_dir / f"{steamid}.maFile"
        if file_path.exists():
            file_path.unlink()
            return True
        return False
