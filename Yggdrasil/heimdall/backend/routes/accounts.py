"""Account management + Steam Guard confirmation + web-session routes.

Extracted verbatim from the former monolithic app.py; behavior is unchanged.
Services are read from :data:`context.ctx` at request time.
"""
import logging

from flask import Blueprint, jsonify, request

import confirmation_settings
from context import ctx

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


# HTTP status for each import refusal; anything else is a bad request.
_IMPORT_STATUS = {'steam_unreachable': 502, 'save_failed': 500}


@bp.route('/api/accounts/import', methods=['POST'])
def import_account():
    """Add Account: {file_name, file_text, password} -> logged in to Steam, then saved.

    Nothing is saved when the login fails (the tokens never reach the browser).
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Missing JSON body", "code": "invalid_request"}), 400
    result = ctx.steam_service.import_account(
        body.get('file_text'), body.get('file_name'), body.get('password'))
    if 'error' in result:
        return jsonify(result), _IMPORT_STATUS.get(result.get('code'), 400)
    return jsonify(result)


@bp.route('/api/accounts/<steamid>/confirmations', methods=['GET'])
def list_confirmations(steamid):
    """List pending trade/market confirmations for an account."""
    result = ctx.steam_service.get_confirmations(steamid)
    if not result.get('success'):
        return jsonify({"error": result.get('message', 'Failed to load confirmations'), "details": result.get('details'), "raw": result.get('raw'), "code": "confirmations_failed"}), 400
    return jsonify({"status": "success", "confirmations": result.get('confirmations', [])})


_RESPOND_STATUS = {'confirmation_not_found': 404, 'confirmations_unavailable': 502, 'steam_refused': 502}


@bp.route('/api/accounts/<steamid>/confirmations/<cid>', methods=['POST'])
def act_on_confirmation(steamid, cid):
    """Approve or deny one confirmation: body {op: 'allow' | 'cancel'}. The
    backend finds the confirmation's key itself."""
    body = request.get_json(silent=True) or {}
    result = ctx.steam_service.respond_to_confirmation(steamid, cid, body.get('op'))
    if 'error' in result:
        return jsonify(result), _RESPOND_STATUS.get(result.get('code'), 400)
    return jsonify(result)


_REMOVE_ALL_STATUS = {'unknown_plan': 404, 'already_executed': 409}


@bp.route('/api/accounts/remove-all/plan', methods=['POST'])
def plan_remove_all_accounts():
    """Remove All accounts, step 1: which accounts and the phrase to type. Writes nothing."""
    result = ctx.steam_service.plan_remove_all()
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/accounts/remove-all/plans/<plan_id>/execute', methods=['POST'])
def execute_remove_all_accounts(plan_id):
    """Remove All accounts, step 2: body {phrase}. Soft-deletes only the plan's
    accounts (maFiles move to maFiles/.deleted/), once."""
    body = request.get_json(silent=True) or {}
    result = ctx.steam_service.execute_remove_all(plan_id, body.get('phrase'))
    if 'error' in result:
        return jsonify(result), _REMOVE_ALL_STATUS.get(result.get('code'), 400)
    return jsonify(result)


@bp.route('/api/accounts/<steamid>', methods=['DELETE'])
def remove_account(steamid):
    """Remove an account"""
    success = ctx.steam_service.remove_account(steamid)
    if success:
        return jsonify({"status": "success", "message": "Account removed."}), 200
    return jsonify({"error": "Account not found", "code": "account_not_found"}), 404


@bp.route('/api/confirmations/settings', methods=['GET'])
def get_confirmation_settings():
    """The Global Confirmations window: values, the interval limits and a plain note."""
    return jsonify(confirmation_settings.view(ctx.settings_manager.get_settings()))


@bp.route('/api/confirmations/settings', methods=['POST'])
def save_confirmation_settings():
    """Save only the confirmation settings. A value outside the limits is refused
    ({error, code}), never changed silently."""
    changes = confirmation_settings.clean(request.get_json(silent=True))
    if not ctx.settings_manager.save_settings(changes):
        return jsonify({"error": "The settings could not be saved.", "code": "save_failed"}), 500
    return jsonify(confirmation_settings.view(ctx.settings_manager.get_settings()))


@bp.route('/api/confirmations/check-all', methods=['POST'])
def check_all_confirmations():
    """Check All Now: one confirmation sweep over every account, as a job
    (GET /api/jobs/<id>; its result says how many accounts were checked)."""
    job = ctx.scheduler.start_check_all(ctx.settings_manager.get_settings(), ctx.jobs)
    if job is None:
        return jsonify({"error": "A confirmation check is already running. Wait for it to finish.",
                        "code": "already_running"}), 409
    return jsonify({"job": job}), 202


@bp.route('/api/accounts/update-session', methods=['POST'])
def update_account_session():
    """Update session cookies from external service (Ratatoskr)."""
    if not request.json:
        logger.error("[DEBUG] Update session failed: Missing JSON body")
        return jsonify({"error": "Missing JSON body", "code": "missing_body"}), 400

    data = request.json
    # Never log the payload itself: the cookies carry a live Steam web token.
    logger.info("Session update from Ratatoskr for %s", data.get('steamID'))

    steamid = data.get('steamID')
    cookies = data.get('cookies')  # Expected to be array of strings or dict

    if not steamid or not cookies:
        logger.error("[DEBUG] Update session failed: Missing steamID or cookies")
        return jsonify({"error": "Missing steamID or cookies", "code": "missing_field"}), 400

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
        return jsonify({"error": result.get('message'), "code": "session_update_failed"}), 500

    return jsonify({"error": "Could not extract access token from cookies", "code": "no_access_token"}), 400


@bp.route('/api/accounts/clear-web-session', methods=['POST'])
def clear_web_session():
    """Remove Ratatoskr web session tokens (called when Ratatoskr disconnects)."""
    if not request.json:
        return jsonify({"error": "Missing JSON body", "code": "missing_body"}), 400

    steamid = request.json.get('steamID')
    if not steamid:
        return jsonify({"error": "Missing steamID", "code": "missing_field"}), 400

    result = ctx.steam_service.clear_web_session(steamid)
    if not result.get('success'):
        return jsonify({"error": result.get('message', 'Failed'), "code": "clear_session_failed"}), 400
    return jsonify({"status": "success"})
