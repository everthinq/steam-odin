"""Global application settings routes.

Extracted verbatim from the former monolithic app.py; behavior is unchanged.
Services are read from :data:`context.ctx` at request time.
"""
from flask import Blueprint, jsonify, request

import confirmation_settings
from context import ctx
from settings import SECRET_SETTINGS, secret_update, without_secrets

bp = Blueprint('settings', __name__)


@bp.route('/api/settings', methods=['GET'])
def get_settings():
    """Global application settings; each secret (tokens, keys, webhook) only as `<name>_set`."""
    return jsonify(without_secrets(ctx.settings_manager.get_settings()))


@bp.route('/api/settings', methods=['POST'])
def update_settings():
    """Update global application settings. A secret: text replaces, null removes, blank keeps."""
    if not request.json:
        return jsonify({"error": "Missing JSON body", "code": "missing_body"}), 400
    body = {key: value for key, value in request.json.items() if key not in SECRET_SETTINGS}
    for key in SECRET_SETTINGS:
        changed, value = secret_update(request.json, key)
        if changed:
            body[key] = value

    # The confirmation settings have one checker (confirmation_settings.clean), here too.
    confirmation_keys = {key: value for key, value in body.items()
                         if key in confirmation_settings.KEYS}
    if confirmation_keys:
        confirmation_settings.clean(confirmation_keys)

    success = ctx.settings_manager.save_settings(body)
    if success:
        return jsonify({"status": "success", "settings": without_secrets(ctx.settings_manager.get_settings())})
    return jsonify({"error": "Failed to save settings", "code": "save_failed"}), 500
