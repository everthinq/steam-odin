import logging
import random
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Storage units hold up to 1000 items (mirrors STORAGE_CAPACITY in the frontend).
STORAGE_CAPACITY = 1000
# Drop an in-flight move from the guard set after this long so a stuck/failed
# move gets retried on a later sweep instead of being skipped forever.
_INFLIGHT_TTL_SEC = 15 * 60

# Proactive session keep-alive. Steam web access tokens live ~24h; we sweep every
# few hours and renew any account with less than the min-TTL left, so confirmations
# never fall back to a long-dead token (the fleet-wide `needauth` outage). This runs
# regardless of the auto-confirm settings — token freshness is a separate concern.
_KEEPALIVE_INTERVAL_SEC = 4 * 3600   # how often to run the freshness sweep
_KEEPALIVE_MIN_TTL_SEC = 8 * 3600    # renew any account with less than this left


class ConfirmationScheduler:
    def __init__(self, settings_manager, steam_service, ratatoskr_service=None):
        self.settings_manager = settings_manager
        self.steam_service = steam_service
        self.ratatoskr_service = ratatoskr_service
        self.stop_event = threading.Event()
        self.thread = None
        # steamid -> {item_id: queued_at_epoch} for moves already handed to Ratatoskr
        # but not yet reflected in inventory, so we don't double-queue them.
        self._auto_store_inflight = {}
        # Last time the proactive token keep-alive sweep ran (0 = run on first loop).
        self._last_keepalive = 0.0

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("[SCHEDULER] Started background confirmation checker.")

    def stop(self):
        if self.thread:
            self.stop_event.set()
            self.thread.join(timeout=2)
            logger.info("[SCHEDULER] Stopped background confirmation checker.")

    @staticmethod
    def _confirm_polling_on(settings):
        return bool(
            settings.get("auto_check_enabled")
            or settings.get("auto_confirm_market")
            or settings.get("auto_confirm_trades")
        )

    @staticmethod
    def _auto_store_on(settings):
        return bool(
            settings.get("auto_store_enabled")
            and settings.get("auto_store_items")
            and settings.get("auto_store_accounts")
        )

    @classmethod
    def _should_poll(cls, settings):
        """Poll when confirmations need a watcher, or the auto-store watcher is armed."""
        return cls._confirm_polling_on(settings) or cls._auto_store_on(settings)

    def _sync_protected_accounts(self, settings):
        """Keep Ratatoskr's idle-disconnect exemption in sync with the auto-store
        accounts, so the watcher's account is never idled out. Re-asserted every
        loop so it self-heals after a Ratatoskr restart."""
        if not self.ratatoskr_service:
            return
        protected = (settings.get("auto_store_accounts") or []) if settings.get("auto_store_enabled") else []
        try:
            self.ratatoskr_service.set_protected_accounts(protected)
        except Exception as e:
            logger.error(f"[AUTO-STORE] Failed to sync protected accounts: {e}")

    def _run_loop(self):
        while not self.stop_event.is_set():
            settings = self.settings_manager.get_settings()

            self._sync_protected_accounts(settings)

            # Keep every account's web token fresh on a slow cadence, independent
            # of whether auto-confirm is on, so a stale token never silently breaks
            # confirmations again.
            if time.time() - self._last_keepalive >= _KEEPALIVE_INTERVAL_SEC:
                self._last_keepalive = time.time()
                try:
                    self._keepalive_sessions()
                except Exception as e:
                    logger.error(f"[KEEPALIVE] sweep error: {e}")

            if self._confirm_polling_on(settings):
                try:
                    self._check_all_accounts(settings)
                except Exception as e:
                    logger.error(f"[SCHEDULER] Error in check loop: {e}")

            if self._auto_store_on(settings):
                try:
                    self._auto_store_sweep(settings)
                except Exception as e:
                    logger.error(f"[SCHEDULER] Error in auto-store sweep: {e}")

            # Sleep for the configured interval, checking stop_event frequently
            interval = max(10, settings.get("check_interval", 300))
            # Sleep in 1s chunks to respond to stop_event quickly
            for _ in range(interval):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

    def _keepalive_sessions(self):
        """Proactively refresh each account's web token before it expires.

        Only accounts within ``_KEEPALIVE_MIN_TTL_SEC`` of expiry are touched, and
        a healthy account's refresh is a single cheap token exchange (no full login).
        Any account that can't be kept logged in is logged loudly with the likely
        cause, so a broken account (missing vault password, bad credentials) is
        caught here rather than when the confirmations UI needs it.
        """
        accounts = self.steam_service.get_all_accounts_data()
        renewed = 0
        failures = []
        for i, account in enumerate(accounts):
            steamid = account['steamid']
            try:
                status = self.steam_service.ensure_fresh_session(
                    steamid, min_ttl_seconds=_KEEPALIVE_MIN_TTL_SEC
                )
            except Exception as e:
                failures.append((steamid, str(e)))
                continue

            if status.get('state') == 'renewed':
                renewed += 1
            elif not status.get('ok'):
                failures.append((steamid, status.get('message') or status.get('state')))
                # Steam rate-limited us — stop hammering and finish next sweep.
                if '429' in str(status.get('message') or ''):
                    logger.warning("[KEEPALIVE] hit Steam rate limit — aborting rest of sweep")
                    break

            if i < len(accounts) - 1:
                time.sleep(1)

        # Always log a one-line heartbeat so "still healthy" is visible in the log.
        logger.info(f"[KEEPALIVE] session sweep: {len(accounts)} checked, "
                    f"{renewed} renewed, {len(failures)} failed")
        for steamid, why in failures:
            name = self._account_name(steamid)
            logger.error(f"[KEEPALIVE] Could not keep {steamid} ({name}) logged in: {why} "
                         f"— add its password to the Mimir vault or re-import the maFile")

    def _check_all_accounts(self, settings):
        # If mobileconf is in backoff, skip the whole sweep so we don't keep hitting it.
        cooldown = self.steam_service._mobileconf_cooldown_remaining()
        if cooldown:
            logger.warning(f"[SCHEDULER] Skipping sweep — mobileconf backoff active ({int(cooldown)}s left)")
            return

        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [SCHEDULER] Checking confirmations for all accounts...")
        accounts = self.steam_service.get_all_accounts_data()

        for i, account in enumerate(accounts):
            steamid = account['steamid']
            try:
                self._process_account(steamid, settings)
            except Exception as e:
                logger.error(f"[SCHEDULER] Failed to process {steamid}: {e}")
            # Stop the sweep immediately if the backoff was tripped mid-run.
            if self.steam_service._mobileconf_cooldown_remaining():
                logger.info("[SCHEDULER] mobileconf backoff tripped mid-sweep — aborting remaining accounts")
                break
            if i < len(accounts) - 1:
                time.sleep(1)

    def _process_account(self, steamid, settings):
        # 1. Fetch confirmations
        result = self.steam_service.get_confirmations(steamid)
        if not result.get('success'):
            logger.error(f"[SCHEDULER] Failed to fetch for {steamid}: {result.get('message')}")
            return

        confirmations = result.get('confirmations', [])
        if not confirmations:
            return

        logger.info(f"[SCHEDULER] Found {len(confirmations)} confirmations for {steamid}")

        # 2. Collect everything to accept, then approve it all in ONE batch call.
        auto_market = settings.get("auto_confirm_market")
        auto_trades = settings.get("auto_confirm_trades")

        to_accept = []
        skipped = 0
        for conf in confirmations:
            # Steam: type 2 = Trade, 3 = Market listing, 12 = Market purchase (may arrive as str)
            try:
                ctype = int(conf.get('type', 0) or 0)
            except (TypeError, ValueError):
                ctype = 0

            cid = conf.get('id')
            ck = conf.get('nonce') or conf.get('key')
            if cid is None or not ck:
                logger.warning(
                    f"[SCHEDULER] Skipping malformed confirmation for {steamid}: "
                    f"id={cid!r} nonce/key={ck!r} keys={list(conf.keys())}"
                )
                continue

            if (ctype in (3, 12) and auto_market) or (ctype == 2 and auto_trades):
                to_accept.append((str(cid), str(ck)))
            else:
                skipped += 1

        if skipped:
            logger.info(f"[SCHEDULER] {steamid}: {skipped} confirmation(s) not eligible "
                  f"(auto_market={auto_market}, auto_trades={auto_trades})")

        if not to_accept:
            return

        logger.info(f"[SCHEDULER] Auto-accepting {len(to_accept)} confirmation(s) for {steamid} in one batch")
        res = self.steam_service.act_on_confirmations_batch(steamid, to_accept, 'allow')
        if res.get('success'):
            logger.info(f"[SCHEDULER] Accepted {res.get('accepted', len(to_accept))} for {steamid}")
        else:
            logger.error(f"[SCHEDULER] Batch accept failed for {steamid}: {res.get('message')}")

    # ------------------------------------------------------------------
    # Auto-store watcher: sweep watched items from inventory into storage.
    # ------------------------------------------------------------------
    def _auto_store_sweep(self, settings):
        if not self.ratatoskr_service:
            return

        watch = {
            str(name).strip().lower()
            for name in (settings.get("auto_store_items") or [])
            if str(name).strip()
        }
        accounts = [str(s) for s in (settings.get("auto_store_accounts") or [])]
        if not watch or not accounts:
            return

        for steamid in accounts:
            try:
                self._auto_store_account(steamid, watch)
            except Exception as e:
                logger.error(f"[AUTO-STORE] Failed for {steamid}: {e}")

    def _auto_store_account(self, steamid, watch):
        if not self._ensure_connected(steamid):
            logger.error(f"[AUTO-STORE] {steamid} not connected and could not auto-connect — skipping")
            return

        items = self._fetch_inventory_items(steamid)
        if items is None:
            return

        present_ids = {str(it.get("item_id")) for it in items}
        now = time.time()

        status = self.ratatoskr_service.get_move_status(steamid) or {}
        queue_busy = bool(status.get("running") or (status.get("pending") or 0) > 0)

        # 1) Reconcile earlier moves against reality — logs only genuine deposits.
        self._reconcile_inflight(steamid, present_ids, now, queue_busy)

        # Don't pile more work onto a queue that's still draining.
        if queue_busy:
            return

        inflight = self._auto_store_inflight.setdefault(steamid, {})

        # 2) Storable, watched items not already in flight.
        id_to_name = {str(it.get("item_id")): it.get("item_name") for it in items}
        loose_ids = [
            str(it.get("item_id"))
            for it in items
            if self._is_storable(it)
            and str(it.get("item_name", "")).strip().lower() in watch
            and str(it.get("item_id")) not in inflight
        ]
        if not loose_ids:
            return

        caskets = (self.ratatoskr_service.get_caskets(steamid) or {}).get("caskets") or []
        plan, unplaced = self._plan_casket_moves(loose_ids, caskets)
        if not plan:
            logger.info(f"[AUTO-STORE] {steamid}: no storage room for {len(loose_ids)} item(s)")
            return

        for casket_id, batch_ids in plan:
            res = self.ratatoskr_service.move_batch(
                steamid, batch_ids, "inventory", "casket", casket_id
            )
            if res.get("error"):
                logger.error(f"[AUTO-STORE] {steamid}: move to {casket_id} failed: {res.get('error')}")
                continue

            casket_name = self._casket_label(caskets, casket_id)
            for iid in batch_ids:
                inflight[iid] = {
                    "casket_id": str(casket_id),
                    "casket_name": casket_name,
                    "item_name": id_to_name.get(iid),
                    "issued_at": now,
                }
            logger.info(
                f"[AUTO-STORE] {steamid}: issued {len(batch_ids)} move(s) → "
                f"'{casket_name}' ({casket_id}); confirming once they leave inventory"
            )

        if unplaced:
            logger.info(f"[AUTO-STORE] {steamid}: {len(unplaced)} item(s) had no room left")

    def _reconcile_inflight(self, steamid, present_ids, now, queue_busy):
        """Confirm previously-issued moves against the live inventory.

        A move is only real once the item has left the loose inventory (it's now
        inside a storage unit) — that's when we log it. Fire-and-forget GC adds
        give no acknowledgement, so this is the only trustworthy signal. Items
        that linger after the queue has drained were refused by the GC (almost
        always a trade hold) and are dropped so they aren't counted or stuck.
        """
        inflight = self._auto_store_inflight.setdefault(steamid, {})
        if not inflight:
            return

        confirmed = {}  # casket_id -> {"name": str, "count": int, "items": {mhn: qty}}
        for iid, meta in list(inflight.items()):
            if iid not in present_ids:
                cid = meta.get("casket_id")
                slot = confirmed.setdefault(
                    cid, {"name": meta.get("casket_name"), "count": 0, "items": {}}
                )
                slot["count"] += 1
                name = meta.get("item_name") or "Unknown item"
                slot["items"][name] = slot["items"].get(name, 0) + 1
                inflight.pop(iid, None)
            elif not queue_busy and (now - meta.get("issued_at", now)) > _INFLIGHT_TTL_SEC:
                logger.info(f"[AUTO-STORE] {steamid}: item {iid} never moved (likely trade-held) — releasing guard")
                inflight.pop(iid, None)

        if not confirmed:
            return

        account_name = self._account_name(steamid)
        for cid, slot in confirmed.items():
            breakdown = ", ".join(f"{qty}× {nm}" for nm, qty in slot["items"].items())
            logger.info(f"[AUTO-STORE] {steamid}: confirmed {slot['count']} stored in '{slot['name']}' ({cid}): {breakdown}")
            self.settings_manager.append_auto_store_history({
                "ts": datetime.now(timezone.utc).isoformat(),
                "steamid": steamid,
                "account_name": account_name,
                "count": slot["count"],
                "casket_id": str(cid),
                "casket_name": slot["name"],
                "items": slot["items"],
            })

    @staticmethod
    def _is_storable(it):
        """Whether a loose inventory item can actually be deposited into storage now.

        Skips storage units themselves, items the GC marks unmovable (medals,
        base weapons, untradable collectibles), and — the key distinction —
        items received via a trade. A traded item carries econ attribute 312
        (surfaced as `trade_locked`) and the GC refuses to store it. A plain
        market-purchase cooldown (`trade_unlock`, attribute 75) does NOT block
        storage, so those items are moved normally even mid-cooldown.
        """
        if it.get("def_index") == 1201:
            return False
        if not it.get("item_moveable", True):
            return False
        if it.get("trade_locked"):
            return False
        return True

    @staticmethod
    def _plan_casket_moves(item_ids, caskets):
        """Pick a random storage unit with room for all items; split across units if none fits.

        Returns (plan, unplaced) where plan is a list of (casket_id, [item_ids]).
        """
        def free(c):
            return max(0, STORAGE_CAPACITY - int(c.get("item_storage_total") or 0))

        n = len(item_ids)
        fits = [c for c in caskets if free(c) >= n]
        if fits:
            target = random.choice(fits)
            return [(target.get("item_id"), list(item_ids))], []

        # No single unit fits — spread greedily across random units with any room.
        units = [c for c in caskets if free(c) > 0]
        random.shuffle(units)
        plan = []
        remaining = list(item_ids)
        for c in units:
            if not remaining:
                break
            take = min(free(c), len(remaining))
            plan.append((c.get("item_id"), remaining[:take]))
            remaining = remaining[take:]
        return plan, remaining

    @staticmethod
    def _casket_label(caskets, casket_id):
        for c in caskets:
            if str(c.get("item_id")) == str(casket_id):
                return c.get("item_customname") or c.get("item_name") or "Storage Unit"
        return "Storage Unit"

    def _account_name(self, steamid):
        data = self.steam_service.get_account(steamid)
        return (data or {}).get("account_name", "Unknown")

    def _fetch_inventory_items(self, steamid, retries=3):
        """Fetch inventory, retrying briefly since it can lag a fresh GC connect."""
        for attempt in range(retries):
            resp = self.ratatoskr_service.get_inventory(steamid) or {}
            if resp.get("error"):
                logger.error(f"[AUTO-STORE] {steamid}: inventory fetch error: {resp.get('error')}")
                return None
            items = resp.get("items")
            if items:
                return items
            if attempt < retries - 1:
                time.sleep(1)
        return []

    def _ensure_connected(self, steamid):
        """Return True if the account has a live Ratatoskr GC session, auto-connecting if not.

        Auto-connect uses the stored password + shared secret, the same path the
        manual Connect button uses. (The stored RefreshToken is a web/mobile
        token, not a Steam-client token, so it can't be used for a GC login.)
        """
        status = self.ratatoskr_service.get_status(steamid) or {}
        if status.get("status") == "connected":
            return True

        data = self.steam_service.get_account(steamid)
        if not data:
            return False

        account_name = data.get("account_name")
        password = self.steam_service.get_password(steamid)
        if not password:
            logger.info(f"[AUTO-STORE] {steamid}: no stored password — connect it once manually to enable auto-store")
            return False

        logger.info(f"[AUTO-STORE] Auto-connecting {steamid} ({account_name})…")
        result = self.ratatoskr_service.login(
            account_name, password=password, shared_secret=data.get("shared_secret")
        )
        if result.get("error"):
            logger.error(f"[AUTO-STORE] {steamid}: auto-connect failed: {result.get('error')}")
            return False
        return True
