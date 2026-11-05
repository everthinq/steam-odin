import logging
import requests
import os
import time

logger = logging.getLogger(__name__)

RATATOSKR_URL = os.environ.get('RATATOSKR_URL', 'http://localhost:3030')
# Shared with Ratatoskr (both read RATATOSKR_SECRET from the root .env): Ratatoskr
# refuses any request without it, so only this backend can move items or open a
# store transaction even if another container on the Docker network is compromised.
_HEADERS = {'X-Ratatoskr-Secret': os.environ.get('RATATOSKR_SECRET', '')}

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


def items_moving(ratatoskr, steam_id):
    """Whether items are moving on the account, so its session must not be logged out: Ratatoskr's
    queue is busy, or a backend move run (storage_deposit.py) sits between two of its batches —
    Ratatoskr's queue then looks idle for a moment."""
    moves = ratatoskr.get_move_status(steam_id) or {}
    if moves.get('running') or moves.get('pending'):
        return True
    in_progress = getattr(ratatoskr, 'moves_in_progress', None)
    return bool(in_progress and in_progress(steam_id))


class RatatoskrService:
    def __init__(self):
        self.base_url = RATATOSKR_URL
        # Called with the Steam login before every Ratatoskr login. app.py points it
        # at AsfService.pause_for_ratatoskr: Steam allows one "playing" session per
        # account, and ASF card farming would block Ratatoskr's Counter-Strike 2 session.
        self.before_login = None
        # app.py points it at DepositService.busy: see items_moving.
        self.moves_in_progress = None

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
            response = requests.post(f"{self.base_url}/login", json=payload, headers=_HEADERS, timeout=45)
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
            response = requests.get(f"{self.base_url}/status/{steam_id}", headers=_HEADERS, timeout=5)
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

    def health(self):
        """Can the backend use Ratatoskr? Read only (``GET /sessions`` never counts as activity):
        {reachable, secret_accepted, answer_ms, error, idle_timeout_ms, sessions: [{steam_id,
        account_name, game_coordinator, idle_seconds}]}."""
        answer = {'reachable': False, 'secret_accepted': None, 'answer_ms': None, 'error': None,
                  'idle_timeout_ms': None, 'sessions': []}
        started = time.monotonic()
        try:
            response = requests.get(f"{self.base_url}/sessions", headers=_HEADERS, timeout=5)
        except requests.exceptions.RequestException as error:
            answer['error'] = f'Ratatoskr does not answer: {type(error).__name__}'
            return answer
        answer.update(reachable=True, answer_ms=round((time.monotonic() - started) * 1000))
        if response.status_code == 401:
            answer.update(secret_accepted=False,
                          error='Ratatoskr refuses the shared secret (RATATOSKR_SECRET in the root .env)')
            return answer
        answer['secret_accepted'] = True
        if response.status_code == 404:
            answer['error'] = 'Ratatoskr runs older code: restart it (docker restart steam-odin-ratatoskr-1)'
            return answer
        if not response.ok:
            answer['error'] = f'Ratatoskr answered HTTP {response.status_code}'
            return answer
        data = response.json()
        answer['idle_timeout_ms'] = data.get('idleTimeoutMs')
        answer['sessions'] = [{
            'steam_id': session.get('steamID'),
            'account_name': session.get('accountName'),
            'game_coordinator': bool(session.get('gameCoordinator')),
            'idle_seconds': session.get('idleSeconds'),
        } for session in data.get('sessions') or []]
        return answer

    def disconnect(self, steam_id):
        """End the Ratatoskr GC session for a Steam account."""
        try:
            response = requests.post(f"{self.base_url}/disconnect/{steam_id}", headers=_HEADERS, timeout=10)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 404:
                return {"success": True, "message": "Already disconnected"}
            return {"error": response.json().get("error", "Disconnect failed")}
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr Disconnect Error: {e}")
            return {"error": "Failed to connect to Ratatoskr"}

    def move_batch(self, steam_id, item_ids, source, target, casket_id=None):
        """Queue a batch of item moves into or out of one Storage Unit (server-side throttling,
        each move confirmed by the Game Coordinator). Only storage_deposit.DepositService calls it."""
        payload = {
            "steamID": steam_id,
            "itemIDs": item_ids,
            "source": source,
            "target": target,
            "casketID": casket_id,
        }
        try:
            response = requests.post(f"{self.base_url}/move/batch", json=payload, headers=_HEADERS, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr Batch Move Error: {e}")
            return _error_body(e, 'Batch move failed') or {"error": "Failed to connect to Ratatoskr"}

    def get_move_status(self, steam_id):
        """Poll move queue progress for an account."""
        try:
            response = requests.get(f"{self.base_url}/move/status/{steam_id}", headers=_HEADERS, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr Move Status Error: {e}")
            return {"error": "Failed to fetch move status from Ratatoskr"}

    def get_move_delay(self):
        """Get delay between queued item moves (ms)."""
        try:
            response = requests.get(f"{self.base_url}/config/move-delay", headers=_HEADERS, timeout=10)
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
                headers=_HEADERS, timeout=10,
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
                headers=_HEADERS, timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr Protected Accounts SET Error: {e}")
            return {"error": "Failed to set protected accounts"}

    def get_session_idle_timeout(self):
        """Get auto-disconnect idle timeout (ms); 0 = never."""
        try:
            response = requests.get(f"{self.base_url}/config/session-idle", headers=_HEADERS, timeout=10)
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
                headers=_HEADERS, timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr Session Idle SET Error: {e}")
            return _error_body(e, 'Failed to set session idle timeout') or {"error": "Failed to connect to Ratatoskr"}

    def get_inventory(self, steam_id):
        """Fetch inventory."""
        try:
            response = requests.get(f"{self.base_url}/inventory/{steam_id}", headers=_HEADERS, timeout=10)
            return response.json()
        except requests.exceptions.RequestException:
            return {"error": "Failed to fetch inventory from Ratatoskr"}

    def get_caskets(self, steam_id):
        """Fetch storage units."""
        try:
            response = requests.get(f"{self.base_url}/caskets/{steam_id}", headers=_HEADERS, timeout=10)
            return response.json()
        except requests.exceptions.RequestException:
            return {"error": "Failed to fetch caskets from Ratatoskr"}

    def get_casket_contents(self, steam_id, casket_id):
        """Fetch contents of a specific storage unit."""
        try:
            response = requests.get(f"{self.base_url}/casket/{steam_id}/{casket_id}",
                                    headers=_HEADERS, timeout=35)   # the Game Coordinator itself waits up to 30 s
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
                headers=_HEADERS, timeout=30,
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
            response = requests.request(method, f"{self.base_url}{path}", json=payload, headers=_HEADERS, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ratatoskr store call {path} failed: {e}")
            return _error_body(e, 'Store call failed') or {"error": "Failed to connect to Ratatoskr"}

    def store_user_data(self, steam_id):
        """The store's price sheet (base64, LZMA-compressed binary KeyValues) and the
        Storage Unit count of the logged-in account."""
        return self._store_call('GET', f"/store/user-data/{steam_id}")

    def store_item_names(self, names):
        """{internal name: {defIndex, name, prefab}} for the price sheet's entries, from
        Ratatoskr's item definitions (local, no Steam call)."""
        return self._store_call('POST', '/items/store-names', {"names": list(names)})

    def capsule_contents(self, names):
        """{price sheet entry: {capsule, items: [{name, tier}]}} for the store's sticker capsules and
        patch packs, from Ratatoskr's item definitions (local, no Steam call)."""
        return self._store_call('POST', '/items/capsule-contents', {"names": list(names)})

    def store_purchase_init(self, steam_id, country, currency, quantity, unit_price, item_definition_index=1201):
        """Open a wallet transaction for *quantity* of an item (Storage Unit 1201 by default;
        nothing paid yet)."""
        return self._store_call('POST', '/store/purchase/init', {
            "steamID": steam_id, "country": country, "currency": currency,
            "quantity": quantity, "unitPrice": unit_price, "itemDefinitionIndex": item_definition_index})

    def store_purchase_finalize(self, steam_id, transaction_id):
        """Deliver an approved transaction's items."""
        return self._store_call('POST', '/store/purchase/finalize',
                                {"steamID": steam_id, "transactionId": str(transaction_id)},
                                timeout=75)   # Ratatoskr waits up to 60 s for the delivery

    def store_purchase_cancel(self, steam_id, transaction_id):
        """Drop a transaction that was never approved."""
        return self._store_call('POST', '/store/purchase/cancel',
                                {"steamID": steam_id, "transactionId": str(transaction_id)})
