from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import time
import threading
from steam_service import SteamService
from settings import SettingsManager
from scheduler import ConfirmationScheduler
from ratatoskr_service import RatatoskrService
from huginn_service import HuginnService, load_csfloat_keys, load_csfloat_proxy

app = Flask(__name__)
CORS(app)

settings_manager = SettingsManager()
steam_service = SteamService()
ratatoskr_service = RatatoskrService()
huginn_service = HuginnService(steam_service, ratatoskr_service)
scheduler = ConfirmationScheduler(settings_manager, steam_service, ratatoskr_service)

# Background CSFloat buy-order sweep state (one at a time). Progress is polled by the UI.
_csfloat_job_lock = threading.Lock()
_csfloat_job = {
    'running': False, 'done': 0, 'total': 0, 'found': 0,
    'current': None, 'started_at': None, 'finished_at': None, 'error': None,
    'waiting_until': None,   # epoch secs the sweep auto-resumes at (all keys cooling)
}

def trigger_restart():
    """Triggers a backend restart by exiting the process after a short delay."""
    def _restart():
        time.sleep(3)
        print("[SYSTEM] Restarting backend via os._exit(1)...")
        os._exit(1)
    
    threading.Thread(target=_restart).start()

def _should_start_background_scheduler():
    """
    Start the scheduler in the process that actually serves requests.
    With FLASK debug + reloader, only the child has WERKZEUG_RUN_MAIN=true;
    the old guard skipped the child, so auto-confirm never ran in Docker dev.
    """
    if os.environ.get('FLASK_ENV') != 'development':
        return True
    return os.environ.get('WERKZEUG_RUN_MAIN') == 'true'


if _should_start_background_scheduler():
    scheduler.start()

# Ensure all errors return JSON, not HTML
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    """List all accounts with current Steam Guard codes"""
    accounts = steam_service.get_all_accounts_data()
    return jsonify({
        "status": "success",
        "accounts": accounts
    })

@app.route('/api/accounts/import', methods=['POST'])
def import_account():
    if not request.json:
         return jsonify({"error": "Missing JSON body"}), 400
         
    # Capture 'fileName' which is already sent by your AddAccount.jsx
    filename = request.json.get('fileName')
    result = steam_service.import_account(request.json, filename=filename)
    
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/accounts/authenticate', methods=['POST'])
def authenticate_account():
    """Begin a Steam authentication session for an account."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    data = request.json
    username = data.get('account_name') or data.get('username')
    password = data.get('password')

    if not username:
        return jsonify({"error": "Missing account_name"}), 400
    if not password:
        return jsonify({"error": "Missing password"}), 400

    result = steam_service.begin_auth_session(username, password)

    if not result:
        return jsonify({"error": "Authentication failed"}), 400

    # Normalize common failure shapes from Steam API into a clear error
    if isinstance(result, dict):
        if result.get('error'):
            return jsonify(result), 400

        # Many Steam auth endpoints return a 'success' flag and message
        if result.get('success') is False:
            message = result.get('message') or 'Authentication failed'
            return jsonify({"error": message, "details": result}), 400

    # Remove internal-only fields before returning (session objects are not JSON serializable)
    auth_response = {k: v for k, v in result.items() if not k.startswith('_')}
    return jsonify({"status": "success", "auth": auth_response})

@app.route('/api/accounts/<steamid>/confirmations', methods=['GET'])
def list_confirmations(steamid):
    """List pending trade/market confirmations for an account."""
    try:
        result = steam_service.get_confirmations(steamid)
        if not result.get('success'):
            return jsonify({"error": result.get('message', 'Failed to load confirmations'), "details": result.get('details'), "raw": result.get('raw')}), 400
        return jsonify({"status": "success", "confirmations": result.get('confirmations', [])})
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@app.route('/api/accounts/<steamid>/confirmations/<cid>', methods=['POST'])
def act_on_confirmation(steamid, cid):
    """Approve or deny a specific confirmation."""
    try:
        if not request.json:
            return jsonify({"error": "Missing JSON body"}), 400

        body = request.json
        ck = body.get('ck')
        operation = body.get('op', 'allow')

        if not ck:
            return jsonify({"error": "Missing confirmation key (ck)"}), 400

        if operation not in ('allow', 'cancel'):
            return jsonify({"error": "Invalid operation; must be 'allow' or 'cancel'"}), 400

        result = steam_service.act_on_confirmation(steamid, cid, ck, operation=operation)

        if not result.get('success'):
            return jsonify({"error": result.get('message', 'Failed to act on confirmation'), "details": result.get('details'), "raw": result.get('raw')}), 400

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@app.route('/api/accounts', methods=['DELETE'])
def remove_all_accounts():
    """Remove all accounts"""
    count = steam_service.remove_all_accounts()
    trigger_restart()
    return jsonify({"status": "success", "message": f"Removed {count} accounts. Restarting backend...", "count": count}), 200

@app.route('/api/accounts/<steamid>', methods=['DELETE'])
def remove_account(steamid):
    """Remove an account"""
    success = steam_service.remove_account(steamid)
    if success:
        trigger_restart()
        return jsonify({"status": "success", "message": "Account removed. Restarting backend..."}), 200
    return jsonify({"error": "Account not found"}), 404

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get global application settings."""
    return jsonify(settings_manager.get_settings())

@app.route('/api/settings', methods=['POST'])
def update_settings():
    """Update global application settings."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400
    
    success = settings_manager.save_settings(request.json)
    if success:
        return jsonify({"status": "success", "settings": settings_manager.get_settings()})
    return jsonify({"error": "Failed to save settings"}), 500

@app.route('/api/confirmations/check-all', methods=['POST'])
def check_all_confirmations():
    """Trigger an immediate check for confirmations on all accounts."""
    # Run in a separate thread to not block the request
    # or just run synchronously if it's fast enough. 
    # For better UX, let's run it in the background thread logic if possible, 
    # but since scheduler loop sleeps, we might want to force a check.
    # The scheduler logic implementation is simple loop. 
    # Let's just call the check method directly here for immediate feedback, 
    # but beware of concurrency if the scheduler is also running.
    # The scheduler isn't locking the steam_service, so it should be fine.
    
    try:
        settings = settings_manager.get_settings()
        scheduler._check_all_accounts(settings)
        return jsonify({
            "status": "success",
            "message": "Check completed",
            "settings": {
                "auto_check_enabled": settings.get("auto_check_enabled"),
                "auto_confirm_market": settings.get("auto_confirm_market"),
                "auto_confirm_trades": settings.get("auto_confirm_trades"),
                "check_interval": settings.get("check_interval"),
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ratatoskr/login', methods=['POST'])
def ratatoskr_login():
    """Start a Ratatoskr session for a specific account."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400
    
    data = request.json
    steam_id = data.get('steam_id')
    
    if not steam_id:
        return jsonify({"error": "Missing steam_id"}), 400

    # Fetch credentials from SteamService (which reads .maFiles)
    account_data = steam_service.get_account(steam_id)
    if not account_data:
        return jsonify({"error": "Account not found"}), 404

    # We need password, which isn't in account_data usually (it's in secrets if saved, or passed in)
    # Ratatoskr needs password. 
    # Option A: User provides password every time.
    # Option B: We store password (insecure efficiently unless encrypted).
    # Option C: We rely on the fact that existing auth might have cached it or we ask user.
    
    # Correction: The current `authenticate_account` endpoint requires password from user. 
    # We should probably ask for password here too, OR reuse the session if `steam-user` supports it (it does with refreshToken).
    # Casemove uses RefreshToken/LoginKey. 
    # For this implementation, let's ask for password in the request to be safe and simple.
    
    password = data.get('password')
    if not password:
        # Try to get credentials from storage
        password = steam_service.get_password(steam_id)
    
    if not password:
        return jsonify({"error": "Password required (not found in storage)"}), 400

    result = ratatoskr_service.login(
        account_name=account_data.get('account_name'),
        password=password,
        shared_secret=account_data.get('shared_secret')
    )
    
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/accounts/update-session', methods=['POST'])
def update_account_session():
    """Update session cookies from external service (Ratatoskr)."""
    if not request.json:
        print("[DEBUG] Update session failed: Missing JSON body")
        return jsonify({"error": "Missing JSON body"}), 400

    data = request.json
    print(f"[DEBUG] Received session update payload: {data}")
    
    steamid = data.get('steamID')
    cookies = data.get('cookies') # Expected to be array of strings or dict

    if not steamid or not cookies:
        print("[DEBUG] Update session failed: Missing steamID or cookies")
        return jsonify({"error": "Missing steamID or cookies"}), 400

    # Extract AccessToken from steamLoginSecure cookie if possible
    access_token = None
    steam_login_secure = None
    session_id = None

    # Handle cookies as list of strings ["name=val", ...] or dict
    if isinstance(cookies, list):
        for cookie in cookies:
            if 'steamLoginSecure=' in cookie:
                steam_login_secure = cookie.split('steamLoginSecure=')[1].split(';')[0]
            if 'sessionid=' in cookie:
                session_id = cookie.split('sessionid=')[1].split(';')[0]
    elif isinstance(cookies, dict):
        steam_login_secure = cookies.get('steamLoginSecure')
        session_id = cookies.get('sessionid')

    if steam_login_secure and '%7C%7C' in steam_login_secure:
        # steamLoginSecure format: <steamid>||<token>
        parts = steam_login_secure.split('%7C%7C')
        if len(parts) >= 2:
            access_token = parts[1]

    if access_token:
        result = steam_service.update_session_cookies(steamid, access_token, steam_login_secure, session_id)
        if result.get('success'):
            return jsonify({"status": "success", "message": "Session updated"})
        return jsonify({"error": result.get('message')}), 500
    
    return jsonify({"error": "Could not extract access token from cookies"}), 400

@app.route('/api/accounts/clear-web-session', methods=['POST'])
def clear_web_session():
    """Remove Ratatoskr web session tokens (called when Ratatoskr disconnects)."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    steamid = request.json.get('steamID')
    if not steamid:
        return jsonify({"error": "Missing steamID"}), 400

    result = steam_service.clear_web_session(steamid)
    if not result.get('success'):
        return jsonify({"error": result.get('message', 'Failed')}), 400
    return jsonify({"status": "success"})

@app.route('/api/ratatoskr/status/<steamid>', methods=['GET'])
def ratatoskr_status(steamid):
    """Check Ratatoskr session status."""
    return jsonify(ratatoskr_service.get_status(steamid))

@app.route('/api/ratatoskr/disconnect', methods=['POST'])
def ratatoskr_disconnect():
    """End a Ratatoskr GC session."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    steam_id = request.json.get('steam_id')
    if not steam_id:
        return jsonify({"error": "Missing steam_id"}), 400

    result = ratatoskr_service.disconnect(steam_id)
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/ratatoskr/move', methods=['POST'])
def ratatoskr_move():
    """Move items via Ratatoskr."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400
        
    result = ratatoskr_service.move_item(
        steam_id=request.json.get('steamID'),
        item_id=request.json.get('itemID'),
        source=request.json.get('source'),
        target=request.json.get('target'),
        casket_id=request.json.get('casketID')
    )
    
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/ratatoskr/move/batch', methods=['POST'])
def ratatoskr_move_batch():
    """Queue a batch of item moves via Ratatoskr."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    result = ratatoskr_service.move_batch(
        steam_id=request.json.get('steamID'),
        item_ids=request.json.get('itemIDs'),
        source=request.json.get('source'),
        target=request.json.get('target'),
        casket_id=request.json.get('casketID'),
    )

    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/ratatoskr/move/status/<steamid>', methods=['GET'])
def ratatoskr_move_status(steamid):
    """Get move queue progress for an account."""
    return jsonify(ratatoskr_service.get_move_status(steamid))

@app.route('/api/ratatoskr/config/move-delay', methods=['GET'])
def ratatoskr_get_move_delay():
    """Get delay between queued item moves (ms)."""
    result = ratatoskr_service.get_move_delay()
    if 'error' in result:
        return jsonify(result), 502
    return jsonify(result)

@app.route('/api/ratatoskr/config/move-delay', methods=['POST'])
def ratatoskr_set_move_delay():
    """Set delay between queued item moves (ms)."""
    if not request.json or request.json.get('delayMs') is None:
        return jsonify({"error": "Missing delayMs"}), 400
    result = ratatoskr_service.set_move_delay(request.json.get('delayMs'))
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/ratatoskr/config/session-idle', methods=['GET'])
def ratatoskr_get_session_idle():
    """Get Ratatoskr auto-disconnect idle timeout (ms); 0 = never."""
    result = ratatoskr_service.get_session_idle_timeout()
    if 'error' in result:
        return jsonify(result), 502
    return jsonify(result)

@app.route('/api/ratatoskr/config/session-idle', methods=['POST'])
def ratatoskr_set_session_idle():
    """Set Ratatoskr auto-disconnect idle timeout (ms); 0 = never."""
    if not request.json or request.json.get('idleTimeoutMs') is None:
        return jsonify({"error": "Missing idleTimeoutMs"}), 400
    result = ratatoskr_service.set_session_idle_timeout(request.json.get('idleTimeoutMs'))
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/ratatoskr/inventory/<steamid>', methods=['GET'])
def ratatoskr_inventory(steamid):
    """Get inventory for account."""
    return jsonify(ratatoskr_service.get_inventory(steamid))

@app.route('/api/ratatoskr/caskets/<steamid>', methods=['GET'])
def ratatoskr_caskets(steamid):
    """Get storage units for account."""
    return jsonify(ratatoskr_service.get_caskets(steamid))

@app.route('/api/ratatoskr/casket/<steamid>/<casketid>', methods=['GET'])
def ratatoskr_casket_contents(steamid, casketid):
    """Get contents of a specific storage unit."""
    return jsonify(ratatoskr_service.get_casket_contents(steamid, casketid))

@app.route('/api/ratatoskr/casket/rename', methods=['POST'])
def ratatoskr_casket_rename():
    """Rename a storage unit via Ratatoskr."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    result = ratatoskr_service.rename_casket(
        steam_id=request.json.get('steamID'),
        casket_id=request.json.get('casketID'),
        name=request.json.get('name', ''),
    )

    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/ratatoskr/auto-store', methods=['GET'])
def get_auto_store():
    """Return auto-store watcher config + recent move history (newest first)."""
    s = settings_manager.get_settings()
    history = list(s.get("auto_store_history") or [])
    history.reverse()
    return jsonify({
        "enabled": bool(s.get("auto_store_enabled")),
        "items": s.get("auto_store_items") or [],
        "accounts": s.get("auto_store_accounts") or [],
        "history": history,
        "moved_total": sum(int(r.get("count", 0)) for r in history),
    })

@app.route('/api/ratatoskr/auto-store', methods=['POST'])
def update_auto_store():
    """Update the auto-store toggle, watched item names, and enabled accounts."""
    data = request.json or {}
    patch = {}
    if "enabled" in data:
        patch["auto_store_enabled"] = bool(data["enabled"])
    if "items" in data and isinstance(data["items"], list):
        # De-dup while preserving order, drop blanks.
        seen, items = set(), []
        for name in data["items"]:
            name = str(name).strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                items.append(name)
        patch["auto_store_items"] = items
    if "accounts" in data and isinstance(data["accounts"], list):
        patch["auto_store_accounts"] = [str(a) for a in data["accounts"]]

    if not patch:
        return jsonify({"error": "Nothing to update"}), 400
    if not settings_manager.save_settings(patch):
        return jsonify({"error": "Failed to save settings"}), 500

    # Keep the idle-disconnect exemption in sync immediately (also re-asserted by the scheduler).
    s = settings_manager.get_settings()
    protected = (s.get("auto_store_accounts") or []) if s.get("auto_store_enabled") else []
    ratatoskr_service.set_protected_accounts(protected)

    return get_auto_store()

@app.route('/api/ratatoskr/auto-store/sweep', methods=['POST'])
def trigger_auto_store_sweep():
    """Run an auto-store sweep immediately (background) for instant feedback."""
    settings = settings_manager.get_settings()
    if not scheduler._auto_store_on(settings):
        return jsonify({"error": "Auto-store is off or has no items/accounts configured"}), 400
    threading.Thread(
        target=lambda: scheduler._auto_store_sweep(settings), daemon=True
    ).start()
    return jsonify({"status": "sweeping"})

@app.route('/api/huginn/scan', methods=['POST'])
def huginn_scan():
    """Scan all accounts and cache inventory grouped by market hash name."""
    try:
        result = huginn_service.scan()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/scan/cache', methods=['GET'])
def huginn_scan_cache():
    """Return cached inventory scan result."""
    cache = huginn_service.get_cache()
    if not cache:
        return jsonify({'error': 'No scan data yet'}), 404
    return jsonify(cache)

@app.route('/api/huginn/csfloat/buy-orders', methods=['GET'])
def huginn_csfloat_buy_orders_status():
    """Report the CSFloat buy-order sweep progress + what's currently cached."""
    with _csfloat_job_lock:
        job = dict(_csfloat_job)
    cache = huginn_service.get_csfloat_buy_orders_cache()
    key_pairs = load_csfloat_keys()
    return jsonify({
        'job': job,
        'keys': huginn_service.csfloat_keys.status(key_pairs),
        'proxy_enabled': bool(load_csfloat_proxy()),
        'cache': None if not cache else {
            'fetched_at': cache.get('fetched_at'),
            'updated_at': cache.get('updated_at'),
            'count': cache.get('count'),
            'candidates': cache.get('candidates'),
            'done': len(cache.get('processed') or []),
            'complete': cache.get('complete', True),
            'interrupted': cache.get('interrupted', False),
            'reason': cache.get('reason'),
        },
    })

@app.route('/api/huginn/csfloat/buy-orders', methods=['POST'])
def huginn_csfloat_buy_orders_fetch():
    """Kick off a background CSFloat buy-order sweep over owned items."""
    settings = settings_manager.get_settings()
    token = settings.get('tradeon_token', '')
    if not load_csfloat_keys():
        return jsonify({'error': 'No CSFloat API keys configured — add them to csfloat_keys.json'}), 400

    scan = huginn_service.get_cache()
    if not scan or not scan.get('by_hash'):
        return jsonify({'error': 'No inventory scan yet — run "Get all items" first'}), 409

    with _csfloat_job_lock:
        if _csfloat_job['running']:
            return jsonify({'error': 'A CSFloat buy-order sweep is already running'}), 409
        _csfloat_job.update({
            'running': True, 'done': 0, 'total': 0, 'found': 0,
            'current': None, 'started_at': time.time(), 'finished_at': None, 'error': None,
            'waiting_until': None,
        })

    def _progress(done, total, current, found):
        with _csfloat_job_lock:
            _csfloat_job.update({'done': done, 'total': total, 'current': current, 'found': found})

    def _on_wait(resume_at):
        with _csfloat_job_lock:
            _csfloat_job['waiting_until'] = resume_at

    def _run():
        try:
            huginn_service.fetch_csfloat_buy_orders(token=token, progress=_progress, wait_cb=_on_wait)
        except Exception as e:
            with _csfloat_job_lock:
                _csfloat_job['error'] = str(e)
        finally:
            with _csfloat_job_lock:
                _csfloat_job['running'] = False
                _csfloat_job['finished_at'] = time.time()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'status': 'started'}), 202

@app.route('/api/huginn/tradeon/steam', methods=['GET'])
def huginn_tradeon_steam():
    """Proxy Tradeon → Steam arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_tradeon_steam(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/buff', methods=['GET'])
def huginn_tradeon_buff():
    """Proxy Tradeon → Buff163 arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_tradeon_buff(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/csfloat', methods=['GET'])
def huginn_tradeon_csfloat():
    """Proxy Tradeon → CSFloat arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_tradeon_csfloat(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/dmarket', methods=['GET'])
def huginn_tradeon_dmarket():
    """Proxy Tradeon → DMarket (autobuy) arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_tradeon_dmarket(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/csfloat-autobuy', methods=['GET'])
def huginn_tradeon_csfloat_autobuy():
    """Tradeon (min) buy + CSFloat buy-order (autobuy) sell, for owned items."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_tradeon_csfloat_autobuy(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/lisskins-steam', methods=['GET'])
def huginn_tradeon_lisskins_steam():
    """Fetch LisSkins buy + Steam sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_lisskins_steam(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/lisskins-buff', methods=['GET'])
def huginn_tradeon_lisskins_buff():
    """Fetch LisSkins buy + Buff163 sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_lisskins_buff(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/lisskins-csfloat', methods=['GET'])
def huginn_tradeon_lisskins_csfloat():
    """Fetch LisSkins buy + CSFloat sell (min listing) prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_lisskins_csfloat(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/lisskins-csfloat-autobuy', methods=['GET'])
def huginn_tradeon_lisskins_csfloat_autobuy():
    """LisSkins (min) buy + CSFloat buy-order (autobuy) sell, for owned items."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_lisskins_csfloat_autobuy(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/buff-steam', methods=['GET'])
def huginn_tradeon_buff_steam():
    """Fetch Buff163 buy + Steam sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_buff_steam(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/buff-csfloat', methods=['GET'])
def huginn_tradeon_buff_csfloat():
    """Fetch Buff163 buy + CSFloat sell (min listing) prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_buff_csfloat(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/buff-csfloat-autobuy', methods=['GET'])
def huginn_tradeon_buff_csfloat_autobuy():
    """Buff163 (min) buy + CSFloat buy-order (autobuy) sell, for owned items."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_buff_csfloat_autobuy(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/csfloat-steam', methods=['GET'])
def huginn_tradeon_csfloat_steam():
    """Fetch CSFloat min buy + Steam autobuy sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_csfloat_steam(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/csfloat-buff', methods=['GET'])
def huginn_tradeon_csfloat_buff():
    """Fetch CSFloat min buy + Buff163 autobuy sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_csfloat_buff(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/lisskins-dmarket', methods=['GET'])
def huginn_tradeon_lisskins_dmarket():
    """Fetch LisSkins min buy + DMarket autobuy sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_lisskins_dmarket(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/buff-dmarket', methods=['GET'])
def huginn_tradeon_buff_dmarket():
    """Fetch Buff163 min buy + DMarket autobuy sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_buff_dmarket(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/csfloat-dmarket', methods=['GET'])
def huginn_tradeon_csfloat_dmarket():
    """Fetch CSFloat min buy + DMarket autobuy sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_csfloat_dmarket(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/dmarket-steam', methods=['GET'])
def huginn_tradeon_dmarket_steam():
    """Fetch DMarket min buy + Steam autobuy sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_dmarket_steam(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/dmarket-buff', methods=['GET'])
def huginn_tradeon_dmarket_buff():
    """Fetch DMarket min buy + Buff163 autobuy sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_dmarket_buff(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/dmarket-csfloat', methods=['GET'])
def huginn_tradeon_dmarket_csfloat():
    """Fetch DMarket min buy + CSFloat min sell prices and combine into arbitrage data."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_dmarket_csfloat(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/huginn/tradeon/dmarket-csfloat-autobuy', methods=['GET'])
def huginn_tradeon_dmarket_csfloat_autobuy():
    """Fetch DMarket min buy + CSFloat autobuy (buy-order) sell prices and combine."""
    token = settings_manager.get_settings().get('tradeon_token', '')
    if not token:
        return jsonify({'error': 'tradeon_token not set in settings'}), 400
    try:
        data = huginn_service.fetch_dmarket_csfloat_autobuy(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    settings = settings_manager.get_settings()
    return jsonify({
        "status": "healthy",
        "scheduler": {
            "running": bool(scheduler.thread and scheduler.thread.is_alive()),
            "polling": ConfirmationScheduler._should_poll(settings),
            "interval_sec": settings.get("check_interval"),
        },
    }), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Disable debug in production!
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
