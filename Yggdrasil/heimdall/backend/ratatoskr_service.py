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

    def disconnect(self, steam_id):
        """End the Ratatoskr GC session for a Steam account."""
        try:
            response = requests.post(f"{self.base_url}/disconnect/{steam_id}", timeout=10)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 404:
                return {"success": True, "message": "Already disconnected"}
            return {"error": response.json().get("error", "Disconnect failed")}
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Disconnect Error: {e}")
            return {"error": "Failed to connect to Ratatoskr"}

    def move_item(self, steam_id, item_id, source, target, casket_id=None):
        """
        Moves an item between inventory and storage unit (queued).
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

    def move_batch(self, steam_id, item_ids, source, target, casket_id=None):
        """Queue a batch of item moves with server-side throttling."""
        payload = {
            "steamID": steam_id,
            "itemIDs": item_ids,
            "source": source,
            "target": target,
            "casketID": casket_id,
        }
        try:
            response = requests.post(f"{self.base_url}/move/batch", json=payload, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Batch Move Error: {e}")
            if e.response:
                return {"error": e.response.json().get('error', 'Batch move failed')}
            return {"error": "Failed to connect to Ratatoskr"}

    def get_move_status(self, steam_id):
        """Poll move queue progress for an account."""
        try:
            response = requests.get(f"{self.base_url}/move/status/{steam_id}", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Move Status Error: {e}")
            return {"error": "Failed to fetch move status from Ratatoskr"}

    def get_move_delay(self):
        """Get delay between queued item moves (ms)."""
        try:
            response = requests.get(f"{self.base_url}/config/move-delay", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Move Delay GET Error: {e}")
            return {"error": "Failed to fetch move delay from Ratatoskr"}

    def set_move_delay(self, delay_ms):
        """Set delay between queued item moves (ms)."""
        try:
            response = requests.post(
                f"{self.base_url}/config/move-delay",
                json={"delayMs": delay_ms},
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Move Delay SET Error: {e}")
            if e.response:
                return {"error": e.response.json().get('error', 'Failed to set move delay')}
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

    def get_store(self, steam_id):
        """Fetch in-game store catalog and wallet info."""
        try:
            response = requests.get(f"{self.base_url}/store/{steam_id}", timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Store GET Error: {e}")
            if e.response:
                body = e.response.json()
                return {"error": body.get("error", "Failed to load store")}
            return {"error": "Failed to connect to Ratatoskr"}

    def begin_store_purchase(self, steam_id, item_def_id, quantity=1):
        """GC StorePurchaseInit — returns Steam Authorize URL (SkinLedger step 1)."""
        payload = {
            "steamID": steam_id,
            "itemDefId": item_def_id,
            "quantity": quantity,
        }
        try:
            response = requests.post(
                f"{self.base_url}/store/purchase/begin",
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Store Begin Error: {e}")
            if e.response is not None:
                try:
                    return {"error": e.response.json().get("error", "Purchase init failed")}
                except ValueError:
                    pass
            return {"error": "Failed to connect to Ratatoskr"}

    def finish_store_purchase(self, steam_id, txn_id):
        """Complete purchase after user clicks Authorize (SkinLedger step 2)."""
        payload = {"steamID": steam_id, "txnId": txn_id}
        try:
            response = requests.post(
                f"{self.base_url}/store/purchase/finish",
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Store Finish Error: {e}")
            if e.response is not None:
                try:
                    return {"error": e.response.json().get("error", "Purchase finalize failed")}
                except ValueError:
                    pass
            return {"error": "Failed to connect to Ratatoskr"}

    def purchase_store_item(self, steam_id, item_def_id, quantity=1):
        """Purchase an item from the CS2 in-game store."""
        payload = {
            "steamID": steam_id,
            "itemDefId": item_def_id,
            "quantity": quantity,
        }
        try:
            response = requests.post(
                f"{self.base_url}/store/purchase",
                json=payload,
                timeout=180,
            )
            if response.status_code == 402:
                return response.json()
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            print("Ratatoskr Store Purchase Error: timed out after 180s")
            return {
                "error": "Purchase timed out. Reconnect to Ratatoskr and check if the item arrived.",
            }
        except requests.exceptions.ConnectionError as e:
            print(f"Ratatoskr Store Purchase Error (connection): {e}")
            return {"error": f"Ratatoskr is not reachable ({e}). Is the service running?"}
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Store Purchase Error: {e}")
            if e.response is not None:
                try:
                    body = e.response.json()
                    return {"error": body.get("error", "Purchase failed")}
                except ValueError:
                    return {"error": e.response.text or "Purchase failed"}
            return {"error": f"Ratatoskr request failed: {e}"}

    def rename_casket(self, steam_id, casket_id, name):
        """Rename a storage unit (free via GC, no name tag consumed)."""
        payload = {
            "steamID": steam_id,
            "casketID": casket_id,
            "name": name if name is not None else "",
        }
        try:
            response = requests.post(
                f"{self.base_url}/casket/rename",
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ratatoskr Casket Rename Error: {e}")
            if e.response:
                body = e.response.json()
                return {"error": body.get("error", "Rename failed")}
            return {"error": "Failed to connect to Ratatoskr"}
