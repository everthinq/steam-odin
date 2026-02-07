from flask import Flask, jsonify, request
from flask_cors import CORS
import os
from steam_service import SteamService

app = Flask(__name__)
CORS(app)

steam_service = SteamService()

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
    """Import .maFile (Plain JSON)"""
    if not request.json:
         return jsonify({"error": "Missing JSON body"}), 400
         
    result = steam_service.import_account(request.json)
    
    if 'error' in result:
        return jsonify(result), 400
        
    return jsonify(result)

@app.route('/api/accounts/<steamid>', methods=['DELETE'])
def remove_account(steamid):
    """Remove an account"""
    success = steam_service.remove_account(steamid)
    if success:
        return jsonify({"status": "success", "message": "Account removed"}), 200
    return jsonify({"error": "Account not found"}), 404

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Disable debug in production!
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
