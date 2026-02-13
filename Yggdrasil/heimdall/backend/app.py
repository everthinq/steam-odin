from flask import Flask, jsonify, request
from flask_cors import CORS
import os
from steam_service import SteamService
from settings import SettingsManager
from scheduler import ConfirmationScheduler

app = Flask(__name__)
CORS(app)

steam_service = SteamService()
settings_manager = SettingsManager()
scheduler = ConfirmationScheduler(settings_manager, steam_service)

# Start background task
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
    # Avoid double-starting in Flask debug reloader
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
    return jsonify({"status": "success", "message": f"Removed {count} accounts", "count": count}), 200

@app.route('/api/accounts/<steamid>', methods=['DELETE'])
def remove_account(steamid):
    """Remove an account"""
    success = steam_service.remove_account(steamid)
    if success:
        return jsonify({"status": "success", "message": "Account removed"}), 200
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
        scheduler._check_all_accounts(settings_manager.get_settings())
        return jsonify({"status": "success", "message": "Check initiated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Disable debug in production!
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
