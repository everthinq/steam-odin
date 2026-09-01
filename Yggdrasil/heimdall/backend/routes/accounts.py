"""Account management + Steam Guard confirmation + web-session routes.

Extracted verbatim from the former monolithic app.py; behavior is unchanged.
Services are read from :data:`context.ctx` at request time.
"""
import logging

from flask import Blueprint, jsonify, request

from context import ctx
from system_ops import trigger_restart

logger = logging.getLogger(__name__)

bp = Blueprint('accounts', __name__)


@bp.route('/api/accounts', methods=['GET'])
def get_accounts():
    """List all accounts with current Steam Guard codes"""
    accounts = ctx.steam_service.get_all_accounts_data()
    return jsonify({
        "status": "success",
        "accounts": accounts
    })


@bp.route('/api/accounts/import', methods=['POST'])
def import_account():
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    # Capture 'fileName' which is already sent by your AddAccount.jsx
    filename = request.json.get('fileName')
    result = ctx.steam_service.import_account(request.json, filename=filename)

    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/accounts/authenticate', methods=['POST'])
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

    result = ctx.steam_service.begin_auth_session(username, password)

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


@bp.route('/api/accounts/<steamid>/confirmations', methods=['GET'])
def list_confirmations(steamid):
    """List pending trade/market confirmations for an account."""
    try:
        result = ctx.steam_service.get_confirmations(steamid)
        if not result.get('success'):
            return jsonify({"error": result.get('message', 'Failed to load confirmations'), "details": result.get('details'), "raw": result.get('raw')}), 400
        return jsonify({"status": "success", "confirmations": result.get('confirmations', [])})
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


@bp.route('/api/accounts/<steamid>/confirmations/<cid>', methods=['POST'])
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

        result = ctx.steam_service.act_on_confirmation(steamid, cid, ck, operation=operation)

        if not result.get('success'):
            return jsonify({"error": result.get('message', 'Failed to act on confirmation'), "details": result.get('details'), "raw": result.get('raw')}), 400

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


@bp.route('/api/accounts', methods=['DELETE'])
def remove_all_accounts():
    """Remove all accounts"""
    count = ctx.steam_service.remove_all_accounts()
    trigger_restart()
    return jsonify({"status": "success", "message": f"Removed {count} accounts. Restarting backend...", "count": count}), 200


@bp.route('/api/accounts/<steamid>', methods=['DELETE'])
def remove_account(steamid):
    """Remove an account"""
    success = ctx.steam_service.remove_account(steamid)
    if success:
        trigger_restart()
        return jsonify({"status": "success", "message": "Account removed. Restarting backend..."}), 200
    return jsonify({"error": "Account not found"}), 404


@bp.route('/api/confirmations/check-all', methods=['POST'])
def check_all_confirmations():
    """Trigger an immediate check for confirmations on all accounts."""
    try:
        settings = ctx.settings_manager.get_settings()
        ctx.scheduler._check_all_accounts(settings)
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


@bp.route('/api/accounts/update-session', methods=['POST'])
def update_account_session():
    """Update session cookies from external service (Ratatoskr)."""
    if not request.json:
        logger.error("[DEBUG] Update session failed: Missing JSON body")
        return jsonify({"error": "Missing JSON body"}), 400

    data = request.json
    logger.info(f"[DEBUG] Received session update payload: {data}")

    steamid = data.get('steamID')
    cookies = data.get('cookies')  # Expected to be array of strings or dict

    if not steamid or not cookies:
        logger.error("[DEBUG] Update session failed: Missing steamID or cookies")
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
        result = ctx.steam_service.update_session_cookies(steamid, access_token, steam_login_secure, session_id)
        if result.get('success'):
            return jsonify({"status": "success", "message": "Session updated"})
        return jsonify({"error": result.get('message')}), 500

    return jsonify({"error": "Could not extract access token from cookies"}), 400


@bp.route('/api/accounts/clear-web-session', methods=['POST'])
def clear_web_session():
    """Remove Ratatoskr web session tokens (called when Ratatoskr disconnects)."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    steamid = request.json.get('steamID')
    if not steamid:
        return jsonify({"error": "Missing steamID"}), 400

    result = ctx.steam_service.clear_web_session(steamid)
    if not result.get('success'):
        return jsonify({"error": result.get('message', 'Failed')}), 400
    return jsonify({"status": "success"})
