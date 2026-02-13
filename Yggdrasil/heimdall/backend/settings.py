import json
import os
import threading

SETTINGS_FILE = 'settings.json'

DEFAULT_SETTINGS = {
    "check_interval": 300,        # seconds
    "auto_check_enabled": False,
    "auto_confirm_market": False,
    "auto_confirm_trades": False
}

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
                    else:
                        self.settings[key] = new_settings[key]
            
            try:
                with open(SETTINGS_FILE, 'w') as f:
                    json.dump(self.settings, f, indent=4)
                return True
            except Exception as e:
                print(f"[SETTINGS] Failed to save settings: {e}")
                return False

    def get_settings(self):
        with self.lock:
            return self.settings.copy()
