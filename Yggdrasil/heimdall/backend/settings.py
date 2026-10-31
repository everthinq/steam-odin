import json
import logging
import os
import shutil
import threading
from datetime import datetime, timezone

from jsonio import atomic_write_json

log = logging.getLogger(__name__)

SETTINGS_FILE = 'settings.json'

DEFAULT_SETTINGS = {
    "check_interval": 300,        # seconds
    "auto_check_enabled": False,
    "auto_confirm_market": False,
    "auto_confirm_trades": False,
    # Telegram/webhook alert for every auto-confirmed trade offer (scam watch).
    "alert_auto_confirmed_trades": True,
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
    # Ping a channel when a buy market (LisSkins, Buff, Tradeon, CS.MONEY Market,
    # CS.MONEY Trade, SkinSwap (Market)) is cheaper than CSFloat for a container by
    # at least case_alert_min_pct. Evaluated after each hourly pulse pull. Telegram is
    # preferred (bot token + chat id); if those are empty, notify_webhook_url is used
    # (a Discord or Slack incoming webhook). settings.json is gitignored — keep tokens there.
    "case_alerts_enabled": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "notify_webhook_url": "",           # Discord/Slack webhook (fallback if no Telegram)
    "case_alert_min_pct": 0.0,          # alert when cheaper-than-CSFloat by >= this % (0 = any amount, even $0.01)
    "case_alert_categories": ["case"],  # which container types to watch (default: cases)
    # How often (seconds) to poll the alert markets. pulse reprices
    # CSFloat ~1min and Steam ~5min, so hourly is too slow to catch cheap-case windows.
    # The full 6-market UI/history refresh still runs hourly regardless.
    "case_poll_interval_sec": 600,      # 10 minutes
    # --- Store Catalogue Arbitrage (Ratatoskr) ---
    # The buy signal: CSMoney (Trade)'s instant offer at least this % of the store price.
    "store_arbitrage_signal_pct": 100.0,
    # After the daily refresh (morning routine) post the in-game store items that sell for
    # more than the store price to the shared Huginn bot (telegram_bot_token + telegram_chat_id);
    # the hourly check posts items that became profitable since (each at most once a day).
    "store_arbitrage_alerts_enabled": True,
    "store_arbitrage_alert_min_pct": 0.0,   # only items whose profit is at least this % (0 = any profit)
    # --- Store Boosts (Ratatoskr, store_boost_service.py) ---
    # Watch every store item and the stickers inside store capsules for a Steam price boost:
    # pulse every hour, Steam's price history one item at a time (at least this many seconds
    # apart, never under 10). Alerts go to the shared Huginn bot; a boost also rings the phone.
    "store_boost_enabled": True,
    "store_boost_alerts_enabled": True,
    "store_boost_ring_enabled": True,
    "store_boost_steam_gap_seconds": 30.0,
    # Items you track on the Boosts tab (any Steam Market name): shown on top, read on the normal schedule.
    "store_boost_tracked": [],
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
    # --- Cross-Profile Arbitrage (bullet 4) ---
    # Which markets take part in the cross-profile board, as HuginnService registry
    # ids (e.g. "LisSkins", "CsFloat"). Fully editable from the UI. Buy markets are
    # priced by their MIN listing (what you'd pay); sell markets by their AUTOBUY /
    # instant-sell buy order (what you'd get). Only autobuy-capable markets are kept
    # as sell targets. Adding markets outside the default five adds fresh pulse pulls.
    "cross_arb_buy_markets": ["LisSkins", "Buff", "CsFloat", "Dmarket", "Steam"],
    "cross_arb_sell_markets": ["Buff", "CsMoneyTrade", "CsMoneyMarket", "CsFloat"],
    # User-defined multi-hop chains. Each chain is an ORDERED list of markets, and
    # every adjacent pair is one buy(min) -> autobuy(instant-sell) leg, shown together
    # with a chain total. E.g. LisSkins -> CSMoney -> CSFloat = two joined legs
    # (LisSkins->CSMoney, then CSMoney->CSFloat). Each entry: {id, name, markets:[...]}.
    "cross_arb_chains": [
        {"id": "lisskins-csmoney-csfloat",
         "name": "LisSkins → CSMoney → CSFloat",
         "markets": ["LisSkins", "CsMoneyTrade", "CsFloat"]},
    ],
    # --- Andvari card deals (Huginn -> Card deals) ---
    # Games whose Steam trading-card drops resell for more than the game costs.
    # The on-sale scope is always scanned first; the full-price scope (every other
    # game with cards, slower, refreshed less often) only when include_full_price.
    "card_deals_auto_scan_enabled": True,
    "card_deals_scan_interval_hours": 12,
    "card_deals_include_full_price": True,
    "card_deals_max_price": 20.0,          # dollars; games above this are not scanned
    "card_deals_min_discount": 0,          # percent; on-sale scope only (0 = any discount)
    # How dropped cards are valued: "both" (default) = every game shows the buy-order
    # value (sell now) AND the sell-price value (list and wait), and is a deal when
    # either is profitable; "instant" = buy orders only (walking the order book for
    # every account's copies); "listing" = one cent under the lowest ask only.
    "card_deals_valuation": "both",
    # Telegram alert after each scan for NEW deals (same game at the same price is
    # not re-sent for 14 days), and the card auto-sell summaries. Only through
    # Andvari's OWN bot (card_deals_bot_token + card_deals_chat_id): never the shared
    # Huginn arbitrage bot (Ivan's choice). Without its own bot, Andvari is silent.
    "card_deals_alerts_enabled": True,
    "card_deals_alert_min_return_percent": 50,
    "card_deals_alert_min_profit": 0.25,   # dollars per copy
    "card_deals_bot_token": "",
    "card_deals_chat_id": "",
    # Store country priced when no account's country is known yet.
    "card_deals_fallback_country": "TR",
    # Card auto-sell: list dropped trading cards at the highest price that still
    # sells (from the price history; at least one cent under the lowest Market
    # listing, never under the buy orders) on every account, confirmed automatically.
    # Cards an account already held when it was switched on are left alone unless
    # card_auto_sell_include_held. card_auto_sell_apps: app ids (empty = every game).
    "card_auto_sell_enabled": False,
    "card_auto_sell_include_held": False,
    "card_auto_sell_foil": True,
    "card_auto_sell_apps": [],
    # --- Team Fortress 2 case drops (Huginn -> Team Fortress 2) ---
    # Watch Team Fortress 2's news for "Added the <name> Case" and, the minute it
    # appears: start Team Fortress 2 mode in ASF (every account plays it for item
    # drops), ring + text (team_fortress_chat_id, falling back to telegram_chat_id),
    # and add the case to the sell list. Auto-sell lists every item on the sell list
    # one cent under the lowest Market listing as soon as it drops, and confirms it.
    "team_fortress_watch_enabled": True,
    "team_fortress_poll_minutes": 2,
    "team_fortress_play_on_release": True,
    "team_fortress_ring_on_release": True,
    "team_fortress_accounts": [],          # SteamID64s that play; empty = every account
    "team_fortress_auto_sell_enabled": True,
    "team_fortress_sell_items": [],        # Market names to sell on drop, e.g. ["Haunted Hoard Case"]
    "team_fortress_chat_id": "",
    # Team Fortress 2 mode stops by itself after this many hours (0 = never), so a
    # release at night does not keep card farming paused on every account for days.
    "team_fortress_auto_stop_hours": 24,
    # --- Active profiles (Huginn -> Arbitrage): the only tables pulse is asked for ---
    # huginn_active_profiles ("LisSkins:CsMoneyTrade:autobuy" = buy market, sell market, autobuy or
    # min): None until first saved, and meanwhile the older huginn_kept_profiles is read (likewise
    # huginn_active_minutes, None → huginn_profile_keep_minutes, else 5). Their tables are downloaded
    # every huginn_active_minutes (5, 10, 15, 30 or 60), only inside the active hours when those are
    # enabled (local time, "HH:MM"; end before start = over midnight).
    "huginn_active_profiles": None,
    "huginn_active_minutes": None,
    "huginn_active_hours": {"enabled": False, "start": "08:00", "end": "01:00"},
}

# How many auto-store move records to keep in the history log.
AUTO_STORE_HISTORY_CAP = 200
# How many Gjallarhorn news-event records to keep in the history log.
GJALLARHORN_NEWS_HISTORY_CAP = 50

class SettingsManager:
    def __init__(self):
        self.lock = threading.Lock()
        # True while settings.json exists but could not be parsed: the in-memory
        # settings are then only defaults, and writing them would overwrite the
        # real tokens (tradeon_token, Telegram, CSFloat) with empty strings.
        self.persist_blocked = False
        self.settings = self._load_settings()

    def _load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            self.persist_blocked = False
            return DEFAULT_SETTINGS.copy()
        try:
            with open(SETTINGS_FILE, 'r') as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError(f'expected a JSON object, got {type(loaded).__name__}')
        except Exception as e:
            self.persist_blocked = True
            corrupt_copy = self._keep_corrupt_copy()
            log.error('failed to load %s (%s); running on defaults and REFUSING to '
                      'write settings until the file loads again — fix it (a copy is at '
                      '%s) and restart the backend', SETTINGS_FILE, e, corrupt_copy)
            return DEFAULT_SETTINGS.copy()
        self.persist_blocked = False
        return {**DEFAULT_SETTINGS, **loaded}

    @staticmethod
    def _keep_corrupt_copy():
        """Copy the unreadable settings.json aside so it can be repaired by hand."""
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        destination = f'{SETTINGS_FILE}.corrupt-{timestamp}'
        try:
            shutil.copy2(SETTINGS_FILE, destination)
            os.chmod(destination, 0o600)  # it may hold tokens
            return destination
        except OSError as e:
            log.error('could not copy unreadable %s aside: %s', SETTINGS_FILE, e)
            return None

    def reload_settings(self):
        """Re-read settings.json; clears the write block once it parses again."""
        with self.lock:
            self.settings = self._load_settings()
            return not self.persist_blocked

    @staticmethod
    def _convert_updates(new_settings):
        """Validate and convert every known key BEFORE anything is applied.

        Returns the converted {key: value} updates; raises ValueError/TypeError
        on the first bad value so a failed save never half-applies.
        """
        updates = {}
        for key, default in DEFAULT_SETTINGS.items():
            if key not in new_settings:
                continue
            value = new_settings[key]
            # Type casting for safety
            if isinstance(default, bool):
                updates[key] = bool(value)
            elif isinstance(default, int):
                updates[key] = int(value)
            elif isinstance(default, list):
                if isinstance(value, list):
                    updates[key] = value
            else:
                updates[key] = value
        return updates

    def save_settings(self, new_settings):
        with self.lock:
            if self.persist_blocked:
                log.error('not saving settings: %s failed to load at startup '
                          '(fix it and restart the backend)', SETTINGS_FILE)
                return False
            try:
                updates = self._convert_updates(new_settings)
            except (TypeError, ValueError) as e:
                log.error('rejected settings update, nothing applied: %s', e)
                return False
            self.settings.update(updates)
            return self._persist()

    def _persist(self):
        """Write current settings to disk atomically. Caller must hold self.lock."""
        if self.persist_blocked:
            log.error('skipped writing %s: it failed to load, so writing now would '
                      'replace the real settings with defaults', SETTINGS_FILE)
            return False
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
