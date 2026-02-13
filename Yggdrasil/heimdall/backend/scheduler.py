import threading
import time
from datetime import datetime

class ConfirmationScheduler:
    def __init__(self, settings_manager, steam_service):
        self.settings_manager = settings_manager
        self.steam_service = steam_service
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        print("[SCHEDULER] Started background confirmation checker.")

    def stop(self):
        if self.thread:
            self.stop_event.set()
            self.thread.join(timeout=2)
            print("[SCHEDULER] Stopped background confirmation checker.")

    def _run_loop(self):
        while not self.stop_event.is_set():
            settings = self.settings_manager.get_settings()
            
            if settings.get("auto_check_enabled"):
                try:
                    self._check_all_accounts(settings)
                except Exception as e:
                    print(f"[SCHEDULER] Error in check loop: {e}")

            # Sleep for the configured interval, checking stop_event frequently
            interval = max(10, settings.get("check_interval", 300))
            # Sleep in 1s chunks to respond to stop_event quickly
            for _ in range(interval):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

    def _check_all_accounts(self, settings):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [SCHEDULER] Checking confirmations for all accounts...")
        accounts = self.steam_service.get_all_accounts_data()
        
        for account in accounts:
            steamid = account['steamid']
            try:
                self._process_account(steamid, settings)
            except Exception as e:
                print(f"[SCHEDULER] Failed to process {steamid}: {e}")

    def _process_account(self, steamid, settings):
        # 1. Fetch confirmations
        result = self.steam_service.get_confirmations(steamid)
        if not result.get('success'):
            print(f"[SCHEDULER] Failed to fetch for {steamid}: {result.get('message')}")
            return

        confirmations = result.get('confirmations', [])
        if not confirmations:
            return

        print(f"[SCHEDULER] Found {len(confirmations)} confirmations for {steamid}")

        # 2. filter and act
        auto_market = settings.get("auto_confirm_market")
        auto_trades = settings.get("auto_confirm_trades")

        for conf in confirmations:
            # conf.type: 2=Trade, 3=Market
            ctype = conf.get('type')
            cid = conf.get('id')
            ck = conf.get('nonce')
            
            should_accept = False
            if ctype == 3 and auto_market:
                should_accept = True
            elif ctype == 2 and auto_trades:
                should_accept = True
            
            if should_accept:
                print(f"[SCHEDULER] Auto-accepting {'Market' if ctype==3 else 'Trade'} confirmation {cid} for {steamid}")
                res = self.steam_service.act_on_confirmation(steamid, cid, ck, 'allow')
                if not res.get('success'):
                   print(f"[SCHEDULER] Failed to accept {cid}: {res.get('message')}")
