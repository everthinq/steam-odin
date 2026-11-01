"""Every error answers JSON, never Flask's HTML page.

A request error Flask itself raises (404 unknown route, 405 wrong method, 400 a
body that is not JSON, ...) keeps its own status and says what it is, as
``{error, code}``: ``{"error": "The method is not allowed ...", "code":
"method_not_allowed"}``. Only a real crash inside the code answers 500.
"""
import json
import logging

from flask import jsonify
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


def http_error_code(error):
    """'Method Not Allowed' -> 'method_not_allowed'."""
    return error.name.lower().replace(' ', '_').replace("'", '')


def install(app):
    @app.errorhandler(HTTPException)
    def request_error(error):
        # Werkzeug's own response keeps headers such as Allow (405) and Retry-After.
        response = error.get_response()
        response.data = json.dumps({'error': error.description, 'code': http_error_code(error)})
        response.content_type = 'application/json'
        return response

    @app.errorhandler(Exception)
    def unexpected_error(error):
        logger.exception('[ERROR] unexpected error')
        return jsonify({'error': f'Unexpected error: {error}', 'code': 'unexpected_error'}), 500
