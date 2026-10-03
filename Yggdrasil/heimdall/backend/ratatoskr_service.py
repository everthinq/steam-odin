import logging
import requests
import os

logger = logging.getLogger(__name__)

RATATOSKR_URL = os.environ.get('RATATOSKR_URL', 'http://localhost:3030')

def _error_body(error, fallback):
    """The error Ratatoskr sent back ({"error": ...} with any 4xx/5xx), or None when
    it never answered. A requests.Response is falsy for 4xx/5xx, so this must test
    `is not None`, not truthiness; a non-JSON body falls back to `fallback`."""
    response = getattr(error, 'response', None)
    if response is None:
        return None
    try:
        body = response.json()
    except ValueError:
        body = {}
    message = body.get('error') if isinstance(body, dict) else None
    return {"error": message or fallback, "status_code": response.status_code}


class RatatoskrService:
    def __init__(self):
        self.base_url = RATATOSKR_URL
        # Called with the Steam login before every Ratatoskr login. app.py points it
        # at AsfService.pause_for_ratatoskr: Steam allows one "playing" session per
        # account, and ASF card farming would block Ratatoskr's Counter-Strike 2 session.
        self.before_login = None

    def login(self, account_name, password, shared_secret=None, two_factor_code=None):
        """
        Initiates a Steam session via Ratatoskr.
        """
        if self.before_login:
            try:
                self.before_login(account_name)
            except Exception as e:
                logger.warning(f"Ratatoskr before-login hook failed: {e}")
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
            logger.error(f"Ratatoskr Login Error: {e}")
            return _error_body(e, 'Login failed') or {"error": "Failed to connect to Ratatoskr"}

    def get_status(self, steam_id):
        """
        Checks if a session is active for the given SteamID.
        """
        try:
            response = requests.get(f"{self.base_url}/status/{steam_id}", timeout=5)
            if response.status_code == 200:
                data = response.json()
                data.setdefault("status", "connected")
                return data
            if response.status_code == 503:
                data = response.json()
                data.setdefault("status", "gc_lost")
                return data
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
            logger.error(f"Ratatoskr Disconnect Error: {e}")
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
            logger.error(f"Ratatoskr Move Error: {e}")
            return _error_body(e, 'Move failed') or {"error": "Failed to connect to Ratatoskr"}

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
            logger.error(f"Ratatoskr Batch Move Error: {e}")
            return _error_body(e, 'Batch move failed') or {"error": "Failed to connect to Ratatoskr"}

    def get_move_status(self, steam_id):
        """Poll move queue progress for an account."""
        try:
            response = requests.get(f"{self.base_url}/move/status/{steam_id}", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr Move Status Error: {e}")
            return {"error": "Failed to fetch move status from Ratatoskr"}

    def get_move_delay(self):
        """Get delay between queued item moves (ms)."""
        try:
            response = requests.get(f"{self.base_url}/config/move-delay", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr Move Delay GET Error: {e}")
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
            logger.error(f"Ratatoskr Move Delay SET Error: {e}")
            return _error_body(e, 'Failed to set move delay') or {"error": "Failed to connect to Ratatoskr"}

    def set_protected_accounts(self, steam_ids):
        """Tell Ratatoskr which accounts must never be idle-disconnected."""
        try:
            response = requests.post(
                f"{self.base_url}/config/protected-accounts",
                json={"steamIds": [str(s) for s in (steam_ids or [])]},
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr Protected Accounts SET Error: {e}")
            return {"error": "Failed to set protected accounts"}

    def get_session_idle_timeout(self):
        """Get auto-disconnect idle timeout (ms); 0 = never."""
        try:
            response = requests.get(f"{self.base_url}/config/session-idle", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr Session Idle GET Error: {e}")
            return {"error": "Failed to fetch session idle timeout from Ratatoskr"}

    def set_session_idle_timeout(self, idle_timeout_ms):
        """Set auto-disconnect idle timeout (ms); 0 = never."""
        try:
            response = requests.post(
                f"{self.base_url}/config/session-idle",
                json={"idleTimeoutMs": idle_timeout_ms},
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr Session Idle SET Error: {e}")
            return _error_body(e, 'Failed to set session idle timeout') or {"error": "Failed to connect to Ratatoskr"}

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
            response = requests.get(f"{self.base_url}/casket/{steam_id}/{casket_id}",
                                    timeout=35)   # the Game Coordinator itself waits up to 30 s
            return response.json()
        except requests.exceptions.RequestException:
            return {"error": "Failed to fetch casket contents from Ratatoskr"}

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
            logger.error(f"Ratatoskr Casket Rename Error: {e}")
            return _error_body(e, "Rename failed") or {"error": "Failed to connect to Ratatoskr"}

    # ---- Counter-Strike 2 in-game store (Storage Units) — see Ratatoskr's store.js ----

    def _store_call(self, method, path, payload=None, timeout=35):
        """A store call; the Game Coordinator itself waits up to 20 s for its answer."""
        try:
            response = requests.request(method, f"{self.base_url}{path}", json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr store call {path} failed: {e}")
            return _error_body(e, 'Store call failed') or {"error": "Failed to connect to Ratatoskr"}

    def store_user_data(self, steam_id):
        """The store's price sheet (base64, LZMA-compressed binary KeyValues) and the
        Storage Unit count of the logged-in account."""
        return self._store_call('GET', f"/store/user-data/{steam_id}")

    def store_purchase_init(self, steam_id, country, currency, quantity, unit_price):
        """Open a wallet transaction for *quantity* Storage Units (nothing paid yet)."""
        return self._store_call('POST', '/store/purchase/init', {
            "steamID": steam_id, "country": country, "currency": currency,
            "quantity": quantity, "unitPrice": unit_price})

    def store_purchase_finalize(self, steam_id, transaction_id):
        """Deliver an approved transaction's Storage Units."""
        return self._store_call('POST', '/store/purchase/finalize',
                                {"steamID": steam_id, "transactionId": str(transaction_id)},
                                timeout=75)   # Ratatoskr waits up to 60 s for the delivery

    def store_purchase_cancel(self, steam_id, transaction_id):
        """Drop a transaction that was never approved."""
        return self._store_call('POST', '/store/purchase/cancel',
                                {"steamID": steam_id, "transactionId": str(transaction_id)})
