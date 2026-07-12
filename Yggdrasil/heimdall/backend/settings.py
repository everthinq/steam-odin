import json
import os
import threading

SETTINGS_FILE = 'settings.json'

DEFAULT_SETTINGS = {
    "check_interval": 300,        # seconds
    "auto_check_enabled": False,
    "auto_confirm_market": False,
    "auto_confirm_trades": False,
    # Bearer token for pulse.tradeon.space, used by Huginn to fetch skin prices.
    # Grab it from the `authorization: Bearer <...>` header of any request the
    # pulse.tradeon.space site makes (DevTools → Network). Leave empty to disable
    # Huginn price fetching. settings.json is gitignored — keep the real token there.
    "tradeon_token": "",
    # CSFloat API key (csfloat.com → Profile → Developer → New Key). Used by Huginn
    # to fetch CSFloat buy-order (autobuy) prices for owned items. settings.json is
    # gitignored — keep the real key there.
    "csfloat_api_key": "",
    # --- Ratatoskr auto-store watcher ---
    # When enabled, the scheduler watches the connected (or auto-connected)
    # accounts in `auto_store_accounts` and moves any loose inventory item whose
    # name is in `auto_store_items` into a randomly-picked storage unit with room.
    "auto_store_enabled": False,
    "auto_store_items": [],       # list of item names, e.g. ["Fracture Case"]
    "auto_store_accounts": [],    # list of SteamID64 the watcher acts on
    "auto_store_history": [],     # append-only move log (capped), written by the sweep
}

# How many auto-store move records to keep in the history log.
AUTO_STORE_HISTORY_CAP = 200

class SettingsManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.settings = self._load_settings()

    def _load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            return DEFAULT_SETTINGS.copy()
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except Exception as e:
            print(f"[SETTINGS] Failed to load settings: {e}")
            return DEFAULT_SETTINGS.copy()

    def save_settings(self, new_settings):
        with self.lock:
            # Update only valid keys
            for key in DEFAULT_SETTINGS:
                if key in new_settings:
                    # Type casting for safety
                    if isinstance(DEFAULT_SETTINGS[key], bool):
                        self.settings[key] = bool(new_settings[key])
                    elif isinstance(DEFAULT_SETTINGS[key], int):
                        self.settings[key] = int(new_settings[key])
                    elif isinstance(DEFAULT_SETTINGS[key], list):
                        val = new_settings[key]
                        if isinstance(val, list):
                            self.settings[key] = val
                    else:
                        self.settings[key] = new_settings[key]

            return self._persist()

    def _persist(self):
        """Write current settings to disk. Caller must hold self.lock."""
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(self.settings, f, indent=4)
            return True
        except Exception as e:
            print(f"[SETTINGS] Failed to save settings: {e}")
            return False

    def append_auto_store_history(self, record):
        """Append one auto-store move record (keeps newest AUTO_STORE_HISTORY_CAP)."""
        with self.lock:
            history = list(self.settings.get("auto_store_history") or [])
            history.append(record)
            self.settings["auto_store_history"] = history[-AUTO_STORE_HISTORY_CAP:]
            self._persist()

    def get_settings(self):
        with self.lock:
            return self.settings.copy()
