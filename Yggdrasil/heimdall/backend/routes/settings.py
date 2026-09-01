"""Global application settings routes.

Extracted verbatim from the former monolithic app.py; behavior is unchanged.
Services are read from :data:`context.ctx` at request time.
"""
from flask import Blueprint, jsonify, request

from context import ctx

bp = Blueprint('settings', __name__)


@bp.route('/api/settings', methods=['GET'])
def get_settings():
    """Get global application settings."""
    return jsonify(ctx.settings_manager.get_settings())


@bp.route('/api/settings', methods=['POST'])
def update_settings():
    """Update global application settings."""
    if not request.json:
        return jsonify({"error": "Missing JSON body"}), 400

    success = ctx.settings_manager.save_settings(request.json)
    if success:
        return jsonify({"status": "success", "settings": ctx.settings_manager.get_settings()})
    return jsonify({"error": "Failed to save settings"}), 500
