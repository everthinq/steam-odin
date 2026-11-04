import logging
import random
import threading
import time
from datetime import datetime, timezone

import auto_store
import notifications
from storage_deposit import CommandError

logger = logging.getLogger(__name__)

# An item the Game Coordinator refused is not offered again for this long (the trade
# protection rule already keeps traded items out; this covers anything unexpected).
_AUTO_STORE_REFUSED_RETRY_SEC = 6 * 3600

# Proactive session keep-alive. Steam web access tokens live ~24h; we sweep every
# few hours and renew any account with less than the min-TTL left, so confirmations
# never fall back to a long-dead token (the fleet-wide `needauth` outage). This runs
# regardless of the auto-confirm settings — token freshness is a separate concern.
_KEEPALIVE_INTERVAL_SEC = 4 * 3600   # how often to run the freshness sweep
_KEEPALIVE_MIN_TTL_SEC = 8 * 3600    # renew any account with less than this left
# Spread renewals out: accounts renewed in one sweep would otherwise all expire
# together and renew in one burst forever. Each account's threshold gets up to
# this much extra (random), so some renew a sweep early and the fleet drifts apart.
_KEEPALIVE_THRESHOLD_SPREAD_SEC = 2 * 3600
# Extra pause after an account actually hit Steam for a renewal (on top of the
# 1 s between accounts), plus up to this much random jitter.
_KEEPALIVE_RENEWAL_PAUSE_SEC = 5
_KEEPALIVE_RENEWAL_JITTER_SEC = 5

# Confirmation types (the `type` field of mobileconf/getlist).
_CONFIRMATION_TYPE_TRADE = 2
_CONFIRMATION_TYPE_MARKET_LISTING = 3
# 12 is not in the documented type list; it has been seen on market
# confirmations, so it is accepted under the market switch only when Steam's own
# `type_name` says it is a market confirmation.
_CONFIRMATION_TYPE_UNDOCUMENTED_MARKET = 12


class ConfirmationScheduler:
    def __init__(self, settings_manager, steam_service, ratatoskr_service=None, deposit_service=None):
        self.settings_manager = settings_manager
        self.steam_service = steam_service
        self.ratatoskr_service = ratatoskr_service
        # Auto-Store deposits through the same command as Transfer (storage_deposit.py).
        self.deposit_service = deposit_service
        self.stop_event = threading.Event()
        self.thread = None
        # steamid -> {item_id: refused_at_epoch}: items the Game Coordinator refused.
        self._auto_store_refused = {}
        # One Auto-Store run per account at a time: the loop and Sweep now never log in twice.
        self._auto_store_locks = {}
        self._auto_store_locks_guard = threading.Lock()
        # Last time the proactive token keep-alive sweep ran (0 = run on first loop).
        self._last_keepalive = 0.0
        # Held for the whole confirmation sweep so the "Check now" button and the
        # background loop can never run two sweeps (double Steam traffic) at once.
        self._sweep_lock = threading.Lock()

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
        return bool(auto_store.sweep_accounts(settings))

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
        protected = auto_store.protected_accounts(settings)
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
            # While Steam is backing us off, wait (the sweep is retried on the next
            # loop once the cooldown clears) instead of adding renewal traffic.
            if (time.time() - self._last_keepalive >= _KEEPALIVE_INTERVAL_SEC
                    and not self.steam_service._mobileconf_cooldown_remaining()):
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
            min_ttl = _KEEPALIVE_MIN_TTL_SEC + random.uniform(0, _KEEPALIVE_THRESHOLD_SPREAD_SEC)
            try:
                status = self.steam_service.ensure_fresh_session(
                    steamid, min_ttl_seconds=int(min_ttl)
                )
            except Exception as e:
                failures.append((steamid, str(e)))
                continue

            if status.get('state') == 'renewed':
                renewed += 1
            elif not status.get('ok'):
                failures.append((steamid, status.get('message') or status.get('state')))
                # Steam rate-limited us (HTTP 429 / 5xx / network error) — stop
                # hammering and finish next sweep.
                if status.get('rate_limited'):
                    logger.warning("[KEEPALIVE] hit Steam rate limit — aborting rest of sweep")
                    break

            if i < len(accounts) - 1:
                time.sleep(1)
                hit_steam = (status.get('state') == 'renewed'
                             or (status.get('state') == 'failed' and not status.get('no_password')))
                if hit_steam:
                    # This account hit Steam — pause longer (with jitter) before
                    # the next one so renewals never land in one burst.
                    time.sleep(_KEEPALIVE_RENEWAL_PAUSE_SEC
                               + random.uniform(0, _KEEPALIVE_RENEWAL_JITTER_SEC))

        # Always log a one-line heartbeat so "still healthy" is visible in the log.
        logger.info(f"[KEEPALIVE] session sweep: {len(accounts)} checked, "
                    f"{renewed} renewed, {len(failures)} failed")
        for steamid, why in failures:
            name = self._account_name(steamid)
            logger.error(f"[KEEPALIVE] Could not keep {steamid} ({name}) logged in: {why} "
                         f"— add its password to the Mimir vault or re-import the maFile")

    def _check_all_accounts(self, settings):
        """Run one confirmation sweep over every account.

        Returns False (and does nothing) when another sweep is already running —
        the background loop and the "Check now" button share this lock — else True.
        """
        if not self._sweep_lock.acquire(blocking=False):
            logger.info("[SCHEDULER] Confirmation sweep already running — not starting another")
            return False
        try:
            self._check_all_accounts_locked(settings)
        finally:
            self._sweep_lock.release()
        return True

    def _check_all_accounts_locked(self, settings):
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
        accepted_details = []
        skipped = 0
        for conf in confirmations:
            cid = conf.get('id')
            ck = conf.get('key')
            if cid is None or not ck:
                logger.warning(
                    f"[SCHEDULER] Skipping malformed confirmation for {steamid}: "
                    f"id={cid!r} key={ck!r} keys={list(conf.keys())}"
                )
                continue

            if self._should_auto_accept(conf, settings):
                to_accept.append((str(cid), str(ck)))
                accepted_details.append(conf)
            else:
                skipped += 1

        if skipped:
            logger.info(f"[SCHEDULER] {steamid}: {skipped} confirmation(s) not eligible "
                  f"(auto_market={auto_market}, auto_trades={auto_trades})")

        if not to_accept:
            return

        logger.info(f"[SCHEDULER] Auto-accepting {len(to_accept)} confirmation(s) for {steamid} in one batch")
        # One audit line per confirmation: what it was and who the partner is.
        for conf in accepted_details:
            logger.info(
                f"[SCHEDULER] {steamid}: auto-accepting id={conf.get('id')} "
                f"type={conf.get('type')} ({conf.get('type_name')!r}) "
                f"headline={conf.get('headline')!r} summary={conf.get('summary')!r} "
                f"creator_id={conf.get('creator_id')}"
            )
        res = self.steam_service.act_on_confirmations_batch(steamid, to_accept, 'allow')
        if res.get('success'):
            logger.info(f"[SCHEDULER] Accepted {res.get('accepted', len(to_accept))} for {steamid}")
            self._alert_auto_confirmed_trades(steamid, accepted_details, settings)
        else:
            logger.error(f"[SCHEDULER] Batch accept failed for {steamid}: {res.get('message')}")

    def _alert_auto_confirmed_trades(self, steamid, accepted_confirmations, settings):
        """Send ONE notification listing every trade offer just auto-confirmed.

        A trade auto-confirmed from a stolen API key is the classic scam, so each one
        is announced (market listings are not — too noisy). Runs on a background
        thread and swallows every error: the alert must never break or delay the
        confirmation sweep. Off when `alert_auto_confirmed_trades` is false.
        """
        try:
            if not settings.get("alert_auto_confirmed_trades", True):
                return
            trades = [conf for conf in accepted_confirmations
                      if self._confirmation_type(conf) == _CONFIRMATION_TYPE_TRADE]
            if not trades or notifications.notification_channel(settings) is None:
                return
            try:
                account_name = self._account_name(steamid)
            except Exception:
                account_name = "Unknown"
            text = self._auto_confirmed_trades_message(steamid, account_name, trades)
        except Exception as e:
            logger.warning(f"[SCHEDULER] {steamid}: could not build the auto-confirmed trade alert: {e}")
            return
        threading.Thread(target=self._send_trade_alert, args=(steamid, settings, text),
                         name="auto-confirmed-trade-alert", daemon=True).start()

    @staticmethod
    def _send_trade_alert(steamid, settings, text):
        try:
            result = notifications.send_notification(settings, text)
            if not result.get('ok'):
                logger.warning(f"[SCHEDULER] {steamid}: auto-confirmed trade alert not sent: {result.get('error')}")
        except Exception as e:
            logger.warning(f"[SCHEDULER] {steamid}: auto-confirmed trade alert failed: {e}")

    @staticmethod
    def _auto_confirmed_trades_message(steamid, account_name, trades):
        """Plain-text alert body: account, then per trade the partner and the summary lines."""
        count = len(trades)
        lines = [f"Auto-confirmed {count} trade offer{'s' if count != 1 else ''} on "
                 f"{account_name} ({steamid})",
                 "If you did not make this trade, revoke the Steam Web API key now."]
        for index, conf in enumerate(trades, start=1):
            partner = str(conf.get('headline') or 'unknown partner')
            creator_id = conf.get('creator_id')
            if creator_id:
                partner += f" (trade offer {creator_id})"
            lines.append("")
            lines.append(f"{index}. Partner: {partner}")
            summary = conf.get('summary') or []
            if isinstance(summary, str):
                summary = [summary]
            for summary_line in summary:
                lines.append(f"   {summary_line}")
            if not summary:
                lines.append("   (no item summary from Steam)")
        return "\n".join(lines)

    @staticmethod
    def _confirmation_type(conf):
        """The confirmation's numeric type (it may arrive as a string); 0 if unknown."""
        try:
            return int(conf.get('type', 0) or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _should_auto_accept(cls, conf, settings):
        """Whether the auto-confirm switches allow accepting this confirmation.

        Steam types: 2 = Trade (auto_confirm_trades), 3 = Market listing
        (auto_confirm_market). Type 12 is undocumented, so it is accepted under
        the market switch only when its `type_name` names a market confirmation;
        anything else (API key, account recovery, phone change, ...) never is.
        """
        ctype = cls._confirmation_type(conf)
        if ctype == _CONFIRMATION_TYPE_TRADE:
            return bool(settings.get("auto_confirm_trades"))
        if ctype == _CONFIRMATION_TYPE_MARKET_LISTING:
            return bool(settings.get("auto_confirm_market"))
        if ctype == _CONFIRMATION_TYPE_UNDOCUMENTED_MARKET:
            if not settings.get("auto_confirm_market"):
                return False
            type_name = str(conf.get('type_name') or '')
            if 'market' in type_name.lower():
                return True
            logger.warning(
                f"[SCHEDULER] Not auto-accepting type 12 confirmation id={conf.get('id')} "
                f"(type_name={type_name!r}, headline={conf.get('headline')!r}) — "
                f"not recognisable as a market confirmation"
            )
        return False

    # ------------------------------------------------------------------
    # Auto-store watcher: sweep watched items from inventory into storage.
    # ------------------------------------------------------------------
    def _auto_store_sweep(self, settings):
        """Each switched-on account stores the items of its own watchlist (auto_store.py)."""
        if not self.ratatoskr_service or not self.deposit_service:
            return
        for steamid in auto_store.sweep_accounts(settings):
            try:
                self._auto_store_account(steamid, auto_store.watch_for(settings, steamid))
            except CommandError as e:
                if e.code in ('busy', 'plan_stale'):
                    logger.info(f"[AUTO-STORE] {steamid}: not now ({e.message}); next sweep")
                else:
                    logger.error(f"[AUTO-STORE] {steamid}: {e.message}")
            except Exception as e:
                logger.error(f"[AUTO-STORE] Failed for {steamid}: {e}")

    def sweep_account(self, steamid):
        """"Sweep now" for one account: its own watchlist, only while Auto-Store is on for it.

        Answers {job} (follow it with GET /api/jobs/<id>) or {job: None, nothing: <code>}."""
        settings = self.settings_manager.get_settings()
        steamid = str(steamid or '')
        refusal = auto_store.sweep_refusal(settings, steamid)
        if refusal:
            raise CommandError(refusal['code'], refusal['reason'])
        watch = auto_store.watch_for(settings, steamid)
        if not self.ratatoskr_service or not self.deposit_service:
            raise CommandError('ratatoskr_unavailable', 'Ratatoskr is not available', 503)
        return self._auto_store_account(steamid, watch)

    def _auto_store_account(self, steamid, watch):
        """Deposit the watched items through the deposit command, Transfer's own rules:
        refused items (trade protected, unstorable) are never sent, the fullest unit with
        room fills first, what has no room waits for the next sweep.

        Answers {job} or {job: None, nothing: <code>}; raises CommandError when it cannot start."""
        with self._auto_store_locks_guard:
            lock = self._auto_store_locks.setdefault(steamid, threading.Lock())
        if not lock.acquire(blocking=False):
            raise CommandError('busy', 'Auto-Store is already working on this account', 409)
        try:
            return self._auto_store_run(steamid, watch)
        finally:
            lock.release()

    def _auto_store_run(self, steamid, watch):
        # A deposit still running (this sweep's or Transfer's) finishes first.
        if self.deposit_service.busy(steamid):
            raise CommandError('busy', 'Items are already moving on this account', 409)
        if not self._ensure_connected(steamid):
            raise CommandError('not_connected', 'Not connected, and the automatic connect failed', 502)
        status = self.ratatoskr_service.get_move_status(steamid) or {}
        if status.get("running"):
            raise CommandError('busy', 'Items are already moving on this account', 409)

        now = time.time()
        refused = self._auto_store_refused.setdefault(steamid, {})
        for item_id in [i for i, at in list(refused.items()) if now - at > _AUTO_STORE_REFUSED_RETRY_SEC]:
            refused.pop(item_id, None)

        # The inventory can come back empty right after a connect: asked again up to 3 times.
        plan = self.deposit_service.plan_deposit(
            steamid, names=watch, exclude=set(refused), allow_overflow=True, retry_empty=3)
        if not plan["moves"]:
            if plan["overflow"]:
                logger.info(f"[AUTO-STORE] {steamid}: no storage room for {plan['overflow']} item(s)")
                return {"job": None, "nothing": "no_room", "overflow": plan["overflow"]}
            return {"job": None, "nothing": "nothing_to_store"}
        job = self.deposit_service.execute(plan["plan_id"], on_finish=self._auto_store_finished)
        units = ", ".join(f"{b['count']} → '{b['casket_name']}'" for b in plan["batches"])
        logger.info(f"[AUTO-STORE] {steamid}: storing {plan['moves']} item(s): {units}")
        if plan["overflow"]:
            logger.info(f"[AUTO-STORE] {steamid}: {plan['overflow']} item(s) had no room left")
        return {"job": job, "overflow": plan["overflow"]}

    def _auto_store_finished(self, plan, job):
        """Record what the Game Coordinator confirmed; remember what it refused.

        Runs before the job reads as finished, so no sweep re-plans this account meanwhile. Only
        a real refusal (the Game Coordinator never answered) is remembered; an item that was never
        sent (Ratatoskr down, session lost) or is unconfirmed is offered again at the next sweep."""
        steamid = plan["steamid"]
        result = (job or {}).get("result") or {}
        if (job or {}).get("error"):
            logger.error(f"[AUTO-STORE] {steamid}: deposit failed: {job['error']}")
        account_name = self._account_name(steamid)
        for batch in result.get("batches") or []:
            if not batch.get("moved"):
                continue
            items = {}
            for item_id in batch.get("moved_ids") or []:
                name = plan["item_names"].get(item_id) or "Unknown item"
                items[name] = items.get(name, 0) + 1
            breakdown = ", ".join(f"{qty}× {name}" for name, qty in items.items())
            logger.info(f"[AUTO-STORE] {steamid}: confirmed {batch['moved']} stored in "
                        f"'{batch['casket_name']}' ({batch['casket_id']}): {breakdown}")
            self.settings_manager.append_auto_store_history({
                "ts": datetime.now(timezone.utc).isoformat(),
                "steamid": steamid,
                "account_name": account_name,
                "count": batch["moved"],
                "casket_id": str(batch["casket_id"]),
                "casket_name": batch["casket_name"],
                "items": items,
            })
        refused = self._auto_store_refused.setdefault(steamid, {})
        for item in result.get("failed_items") or []:
            name = item.get("item_name") or item["item_id"]
            if item.get("code") == "refused":
                refused[item["item_id"]] = time.time()
                logger.info(f"[AUTO-STORE] {steamid}: {name} was refused by the Game Coordinator — "
                            f"not offered again for 6 hours")
            else:
                logger.info(f"[AUTO-STORE] {steamid}: {name} did not move ({item.get('code')}) — "
                            f"offered again at the next sweep")

    def _account_name(self, steamid):
        data = self.steam_service.get_account(steamid)
        return (data or {}).get("account_name", "Unknown")

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
