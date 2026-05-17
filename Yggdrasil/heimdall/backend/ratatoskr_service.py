import requests
import os

RATATOSKR_URL = os.environ.get('RATATOSKR_URL', 'http://localhost:3030')

class RatatoskrService:
    def __init__(self):
        self.base_url = RATATOSKR_URL

    def login(self, account_name, password, shared_secret=None, two_factor_code=None):
        """
        Initiates a Steam session via Ratatoskr.
        """
        payload = {
            "accountName": account_name,
            "password": password,
        }
        if two_factor_code:
            payload["twoFactorCode"] = two_factor_code
        elif shared_secret:
            payload["sharedSecret"] = shared_secret

        try:
            response = requests.post(f"{self.base_url}/login", json=payload, timeout=45)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Login Error: {e}")
            if e.response:
                return {"error": e.response.json().get('error', 'Login failed'), "details": e.response.text}
            return {"error": "Failed to connect to Ratatoskr"}

    def get_status(self, steam_id):
        """
        Checks if a session is active for the given SteamID.
        """
        try:
            response = requests.get(f"{self.base_url}/status/{steam_id}", timeout=5)
            if response.status_code == 200:
                return {"status": "connected"}
            return {"status": "disconnected"}
        except requests.exceptions.RequestException:
             return {"status": "disconnected", "error": "Ratatoskr unreachable"}

    def move_item(self, steam_id, item_id, source, target, casket_id=None):
        """
        Moves an item between inventory and storage unit.
        """
        payload = {
            "steamID": steam_id,
            "itemID": item_id,
            "source": source,
            "target": target,
            "casketID": casket_id
        }
        try:
            response = requests.post(f"{self.base_url}/move", json=payload, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Move Error: {e}")
            if e.response:
                 return {"error": e.response.json().get('error', 'Move failed')}
            return {"error": "Failed to connect to Ratatoskr"}

    def get_inventory(self, steam_id):
        """Fetch inventory."""
        try:
            response = requests.get(f"{self.base_url}/inventory/{steam_id}", timeout=10)
            return response.json()
        except requests.exceptions.RequestException:
            return {"error": "Failed to fetch inventory from Ratatoskr"}

    def get_caskets(self, steam_id):
        """Fetch storage units."""
        try:
            response = requests.get(f"{self.base_url}/caskets/{steam_id}", timeout=10)
            return response.json()
        except requests.exceptions.RequestException:
            return {"error": "Failed to fetch caskets from Ratatoskr"}

    def get_casket_contents(self, steam_id, casket_id):
        """Fetch contents of a specific storage unit."""
        try:
            response = requests.get(f"{self.base_url}/casket/{steam_id}/{casket_id}", timeout=10)
            return response.json()
        except requests.exceptions.RequestException:
            return {"error": "Failed to fetch casket contents from Ratatoskr"}
