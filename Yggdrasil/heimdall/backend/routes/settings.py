"""Global application settings routes.

Extracted verbatim from the former monolithic app.py; behavior is unchanged.
Services are read from :data:`context.ctx` at request time.
"""
from flask import Blueprint, jsonify, request

import confirmation_settings
from context import ctx
from command_marks import shows_secrets

bp = Blueprint('settings', __name__)


@bp.route('/api/settings', methods=['GET'])
@shows_secrets
def get_settings():
    """Get global application settings."""
    return jsonify(ctx.settings_manager.get_settings())


@bp.route('/api/settings', methods=['POST'])
def update_settings():
    """Update global application settings."""
    if not request.json:
        return jsonify({"error": "Missing JSON body", "code": "missing_body"}), 400

    # The confirmation settings have one checker (confirmation_settings.clean), here too.
    confirmation_keys = {key: value for key, value in request.json.items()
                         if key in confirmation_settings.KEYS}
    if confirmation_keys:
        confirmation_settings.clean(confirmation_keys)

    success = ctx.settings_manager.save_settings(request.json)
    if success:
        return jsonify({"status": "success", "settings": ctx.settings_manager.get_settings()})
    return jsonify({"error": "Failed to save settings", "code": "save_failed"}), 500
