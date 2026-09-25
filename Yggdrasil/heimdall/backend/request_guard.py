"""Keep websites out of the Heimdall API.

The API has no login (it trusts whoever can reach it) and serves Steam Guard
codes, the Mímir password export and trade confirmations. Two browser attacks
could otherwise reach it from any page open on this Mac:

* **Cross-origin reads** — with CORS open to every origin, any website could
  ``fetch('http://localhost:5001/api/mimir/export')`` and read the answer.
  The frontend calls the API through Vite's same-origin proxy and needs no
  CORS at all, so only the Heimdall frontend's own origins are allowed.
* **DNS rebinding** — a website points its own domain at 127.0.0.1, making the
  request "same-origin" for the browser. Its ``Host`` header still names that
  domain, so requests are refused unless ``Host`` is one Heimdall itself uses:
  localhost, the Docker service name (Vite proxy, Ratatoskr), or extra names
  from ``HEIMDALL_ALLOWED_HOSTS`` (comma-separated).
* **Blind cross-site writes** — CORS stops a website *reading* answers, but the
  browser still *sends* a plain cross-site POST (no preflight). Browsers always
  add an ``Origin`` header to those, so a write whose ``Origin`` is not the
  Heimdall frontend is refused. Server-side callers (Vite proxy, Ratatoskr,
  curl) send the frontend origin or none at all.
"""
import os

from flask import jsonify, request
from flask_cors import CORS

FRONTEND_ORIGINS = ['http://localhost:3000', 'http://127.0.0.1:3000']
ALLOWED_HOSTS = {'localhost', '127.0.0.1', '::1', 'heimdall-backend'}
READ_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def host_name(host_header):
    """'localhost:5001' -> 'localhost', '[::1]:5001' -> '::1'."""
    host = (host_header or '').strip().lower()
    if host.startswith('['):
        return host[1:host.find(']')] if ']' in host else host
    return host.rsplit(':', 1)[0] if host.count(':') == 1 else host


def allowed_hosts():
    extra = os.environ.get('HEIMDALL_ALLOWED_HOSTS', '')
    return ALLOWED_HOSTS | {name.strip().lower() for name in extra.split(',') if name.strip()}


def install(app):
    CORS(app, origins=FRONTEND_ORIGINS)
    hosts = allowed_hosts()

    @app.before_request
    def refuse_foreign_hosts():
        if host_name(request.host) not in hosts:
            return jsonify({'error': 'host not allowed'}), 403
        origin = request.headers.get('Origin')
        if request.method not in READ_METHODS and origin and origin not in FRONTEND_ORIGINS:
            return jsonify({'error': 'cross-site request refused'}), 403
        return None
