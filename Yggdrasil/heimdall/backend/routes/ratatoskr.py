"""Ratatoskr (Storage Unit / inventory courier) routes.

Extracted verbatim from the former monolithic app.py; behavior is unchanged.
Services are read from :data:`context.ctx` at request time.
"""
import threading

from flask import Blueprint, jsonify, request

from context import ctx

bp = Blueprint('ratatoskr', __name__)


@bp.route('/api/ratatoskr/login', methods=['POST'])
def ratatoskr_login():
    """Start a Ratatoskr session for a specific account."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    data = request.json
    steam_id = data.get('steam_id')

    if not steam_id:
        return jsonify({"error": "Missing steam_id"}), 400

    # Fetch credentials from SteamService (which reads .maFiles)
    account_data = ctx.steam_service.get_account(steam_id)
    if not account_data:
        return jsonify({"error": "Account not found"}), 404

    password = data.get('password')
    if not password:
        # Try to get credentials from storage
        password = ctx.steam_service.get_password(steam_id)

    if not password:
        return jsonify({"error": "Password required (not found in storage)"}), 400

    result = ctx.ratatoskr_service.login(
        account_name=account_data.get('account_name'),
        password=password,
        shared_secret=account_data.get('shared_secret')
    )

    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/ratatoskr/status/<steamid>', methods=['GET'])
def ratatoskr_status(steamid):
    """Check Ratatoskr session status."""
    return jsonify(ctx.ratatoskr_service.get_status(steamid))


@bp.route('/api/ratatoskr/disconnect', methods=['POST'])
def ratatoskr_disconnect():
    """End a Ratatoskr GC session."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    steam_id = request.json.get('steam_id')
    if not steam_id:
        return jsonify({"error": "Missing steam_id"}), 400

    result = ctx.ratatoskr_service.disconnect(steam_id)
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/ratatoskr/move', methods=['POST'])
def ratatoskr_move():
    """Move items via Ratatoskr."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    result = ctx.ratatoskr_service.move_item(
        steam_id=request.json.get('steamID'),
        item_id=request.json.get('itemID'),
        source=request.json.get('source'),
        target=request.json.get('target'),
        casket_id=request.json.get('casketID')
    )

    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/ratatoskr/move/batch', methods=['POST'])
def ratatoskr_move_batch():
    """Queue a batch of item moves via Ratatoskr."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    result = ctx.ratatoskr_service.move_batch(
        steam_id=request.json.get('steamID'),
        item_ids=request.json.get('itemIDs'),
        source=request.json.get('source'),
        target=request.json.get('target'),
        casket_id=request.json.get('casketID'),
    )

    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/ratatoskr/move/status/<steamid>', methods=['GET'])
def ratatoskr_move_status(steamid):
    """Get move queue progress for an account."""
    return jsonify(ctx.ratatoskr_service.get_move_status(steamid))


@bp.route('/api/ratatoskr/config/move-delay', methods=['GET'])
def ratatoskr_get_move_delay():
    """Get delay between queued item moves (ms)."""
    result = ctx.ratatoskr_service.get_move_delay()
    if 'error' in result:
        return jsonify(result), 502
    return jsonify(result)


@bp.route('/api/ratatoskr/config/move-delay', methods=['POST'])
def ratatoskr_set_move_delay():
    """Set delay between queued item moves (ms)."""
    if not request.json or request.json.get('delayMs') is None:
        return jsonify({"error": "Missing delayMs"}), 400
    result = ctx.ratatoskr_service.set_move_delay(request.json.get('delayMs'))
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/ratatoskr/config/session-idle', methods=['GET'])
def ratatoskr_get_session_idle():
    """Get Ratatoskr auto-disconnect idle timeout (ms); 0 = never."""
    result = ctx.ratatoskr_service.get_session_idle_timeout()
    if 'error' in result:
        return jsonify(result), 502
    return jsonify(result)


@bp.route('/api/ratatoskr/config/session-idle', methods=['POST'])
def ratatoskr_set_session_idle():
    """Set Ratatoskr auto-disconnect idle timeout (ms); 0 = never."""
    if not request.json or request.json.get('idleTimeoutMs') is None:
        return jsonify({"error": "Missing idleTimeoutMs"}), 400
    result = ctx.ratatoskr_service.set_session_idle_timeout(request.json.get('idleTimeoutMs'))
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/ratatoskr/inventory/<steamid>', methods=['GET'])
def ratatoskr_inventory(steamid):
    """Get inventory for account."""
    return jsonify(ctx.ratatoskr_service.get_inventory(steamid))


@bp.route('/api/ratatoskr/caskets/<steamid>', methods=['GET'])
def ratatoskr_caskets(steamid):
    """Get storage units for account."""
    return jsonify(ctx.ratatoskr_service.get_caskets(steamid))


@bp.route('/api/ratatoskr/casket/<steamid>/<casketid>', methods=['GET'])
def ratatoskr_casket_contents(steamid, casketid):
    """Get contents of a specific storage unit."""
    return jsonify(ctx.ratatoskr_service.get_casket_contents(steamid, casketid))


@bp.route('/api/ratatoskr/casket/rename', methods=['POST'])
def ratatoskr_casket_rename():
    """Rename a storage unit via Ratatoskr."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    result = ctx.ratatoskr_service.rename_casket(
        steam_id=request.json.get('steamID'),
        casket_id=request.json.get('casketID'),
        name=request.json.get('name', ''),
    )

    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/ratatoskr/auto-store', methods=['GET'])
def get_auto_store():
    """Return auto-store watcher config + recent move history (newest first)."""
    s = ctx.settings_manager.get_settings()
    history = list(s.get("auto_store_history") or [])
    history.reverse()
    return jsonify({
        "enabled": bool(s.get("auto_store_enabled")),
        "items": s.get("auto_store_items") or [],
        "accounts": s.get("auto_store_accounts") or [],
        "history": history,
        "moved_total": sum(int(r.get("count", 0)) for r in history),
    })


@bp.route('/api/ratatoskr/auto-store', methods=['POST'])
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
    if not ctx.settings_manager.save_settings(patch):
        return jsonify({"error": "Failed to save settings"}), 500

    # Keep the idle-disconnect exemption in sync immediately (also re-asserted by the scheduler).
    s = ctx.settings_manager.get_settings()
    protected = (s.get("auto_store_accounts") or []) if s.get("auto_store_enabled") else []
    ctx.ratatoskr_service.set_protected_accounts(protected)

    return get_auto_store()


@bp.route('/api/ratatoskr/auto-store/sweep', methods=['POST'])
def trigger_auto_store_sweep():
    """Run an auto-store sweep immediately (background) for instant feedback."""
    settings = ctx.settings_manager.get_settings()
    if not ctx.scheduler._auto_store_on(settings):
        return jsonify({"error": "Auto-store is off or has no items/accounts configured"}), 400
    threading.Thread(
        target=lambda: ctx.scheduler._auto_store_sweep(settings), daemon=True
    ).start()
    return jsonify({"status": "sweeping"})
