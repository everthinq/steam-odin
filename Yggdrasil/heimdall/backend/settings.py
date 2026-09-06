import json
import logging
import os
import threading

from jsonio import atomic_write_json

log = logging.getLogger(__name__)

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
    # Per-market sell-side fees for Huginn's generated arbitrage pairs, as
    # {marketId: fraction} (e.g. {"Steam": 0.13}). Overrides the built-in defaults;
    # markets without a confirmed fee default to 0 until set here. Editable from the
    # Huginn arbitrage UI. settings.json is gitignored.
    "huginn_market_fees": {},
    # --- Ratatoskr auto-store watcher ---
    # When enabled, the scheduler watches the connected (or auto-connected)
    # accounts in `auto_store_accounts` and moves any loose inventory item whose
    # name is in `auto_store_items` into a randomly-picked storage unit with room.
    "auto_store_enabled": False,
    "auto_store_items": [],       # list of item names, e.g. ["Fracture Case"]
    "auto_store_accounts": [],    # list of SteamID64 the watcher acts on
    "auto_store_history": [],     # append-only move log (capped), written by the sweep
    # --- Case Arbitrage price alerts ---
    # Ping a channel when LisSkins or Buff is cheaper than CSFloat for a container by
    # at least case_alert_min_pct. Evaluated after each hourly pulse pull. Telegram is
    # preferred (bot token + chat id); if those are empty, notify_webhook_url is used
    # (a Discord or Slack incoming webhook). settings.json is gitignored — keep tokens there.
    "case_alerts_enabled": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "notify_webhook_url": "",           # Discord/Slack webhook (fallback if no Telegram)
    "case_alert_min_pct": 0.0,          # alert when cheaper-than-CSFloat by >= this % (0 = any amount, even $0.01)
    "case_alert_categories": ["case"],  # which container types to watch (default: cases)
    # How often (seconds) to poll CSFloat/LisSkins/Buff for alerts. pulse reprices
    # CSFloat ~1min and Steam ~5min, so hourly is too slow to catch cheap-case windows.
    # The full 6-market UI/history refresh still runs hourly regardless.
    "case_poll_interval_sec": 600,      # 10 minutes
    # --- Gjallarhorn (event-rotation cockpit) ---
    # Instant-redeploy market whitelist: which markets do NOT lock your balance for
    # days after a sale, so the proceeds can be re-spent on the freshly-limited case
    # right away. Filled in gradually from the UI. Each entry:
    #   {id, display, holdDays (0 = usable immediately), instantRedeploy, notes}
    "gjallarhorn_market_holds": [],
    # Target basket: the freshly-limited case(s)/item(s) to rotate INTO. Each entry
    # {name}; the page prices them and shows how many your capital buys.
    "gjallarhorn_targets": [],
    # --- Gjallarhorn news watcher (bullet 4) ---
    # Polls the official CS2 update feed (Steam news for appid 730) and RINGS +
    # texts when Valve ADDS or REMOVES a case / collection / capsule / souvenir
    # (a supply-shock "limiting" event). Map-pool changes are ignored on purpose.
    "gjallarhorn_news_armed": True,        # False = watch but never ring/alert
    "gjallarhorn_news_poll_minutes": 10,   # how often to poll the feed (floor 2 min)
    # Dedicated Telegram chat for Gjallarhorn alerts, so they land in their OWN
    # conversation instead of mixing with the Case Arbitrage board. Uses the same
    # telegram_bot_token; if empty, falls back to the shared telegram_chat_id.
    "gjallarhorn_chat_id": "",
    "gjallarhorn_news_last_seen_date": 0,  # unix date of newest processed post
    "gjallarhorn_news_last_gid": "",       # id of the newest post seen (cheap "unchanged?" check)
    "gjallarhorn_news_history": [],        # append-only log of detected events (capped)
}

# How many auto-store move records to keep in the history log.
AUTO_STORE_HISTORY_CAP = 200
# How many Gjallarhorn news-event records to keep in the history log.
GJALLARHORN_NEWS_HISTORY_CAP = 50

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
            log.error('failed to load settings: %s', e)
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
        """Write current settings to disk atomically. Caller must hold self.lock."""
        try:
            atomic_write_json(SETTINGS_FILE, self.settings, indent=4)
            return True
        except Exception as e:
            log.error('failed to save settings: %s', e)
            return False

    def append_auto_store_history(self, record):
        """Append one auto-store move record (keeps newest AUTO_STORE_HISTORY_CAP)."""
        with self.lock:
            history = list(self.settings.get("auto_store_history") or [])
            history.append(record)
            self.settings["auto_store_history"] = history[-AUTO_STORE_HISTORY_CAP:]
            self._persist()

    def record_gjallarhorn_news(self, last_seen_date, event_record=None, last_gid=None):
        """Advance the news watcher's high-water mark (date + newest post id) and
        optionally log one detected limiting event (keeps newest
        GJALLARHORN_NEWS_HISTORY_CAP)."""
        with self.lock:
            self.settings["gjallarhorn_news_last_seen_date"] = int(last_seen_date)
            if last_gid is not None:
                self.settings["gjallarhorn_news_last_gid"] = str(last_gid)
            if event_record is not None:
                history = list(self.settings.get("gjallarhorn_news_history") or [])
                history.append(event_record)
                self.settings["gjallarhorn_news_history"] = history[-GJALLARHORN_NEWS_HISTORY_CAP:]
            self._persist()

    def get_settings(self):
        with self.lock:
            return self.settings.copy()
