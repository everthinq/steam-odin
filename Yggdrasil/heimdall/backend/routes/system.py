"""System: what the backend offers and how it is doing (the System health page reads it).

The endpoint list is read from Flask's own route table at request time, so it can
never drift from the code: a new route appears here by itself, with the first line
of its docstring as the summary.
"""
import inspect
import time

from flask import Blueprint, current_app, jsonify

from context import ctx
from loop_status import loops
from system_status import memory_use, recent_errors

bp = Blueprint('system', __name__)

# Methods Flask adds to every route by itself; they say nothing about the route.
AUTOMATIC_METHODS = frozenset({'HEAD', 'OPTIONS'})


def summary_of(view):
    """First line of the view's docstring, or an empty string."""
    doc = inspect.getdoc(view) or ''
    return doc.splitlines()[0].strip() if doc else ''


def endpoint_list(app):
    """Every route of *app*: path, methods, blueprint (the tool) and summary, sorted by path."""
    endpoints = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        blueprint, _, _ = rule.endpoint.rpartition('.')
        endpoints.append({
            'path': rule.rule,
            'methods': sorted(set(rule.methods or ()) - AUTOMATIC_METHODS),
            'group': blueprint or 'app',
            'name': rule.endpoint,
            'summary': summary_of(app.view_functions.get(rule.endpoint)),
        })
    endpoints.sort(key=lambda endpoint: (endpoint['path'], endpoint['methods']))
    return endpoints


@bp.route('/api/system/routes', methods=['GET'])
def list_routes():
    """Every backend endpoint: path, methods, group and the first line of its docstring."""
    endpoints = endpoint_list(current_app)
    return jsonify({'count': len(endpoints), 'endpoints': endpoints})


@bp.route('/api/system/health', methods=['GET'])
def system_health():
    """Background loops, Ratatoskr and ASF, the last errors logged and the backend's memory use."""
    return jsonify({
        'checked_at': time.time(),
        'loops': loops.snapshot(),
        'ratatoskr': ctx.ratatoskr_service.health(),
        'asf': ctx.asf_service.health(),
        'errors': recent_errors.newest_first(),
        'memory': memory_use(),
    })
