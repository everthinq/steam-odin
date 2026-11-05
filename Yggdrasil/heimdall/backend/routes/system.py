"""System: what the backend offers and how it is doing.

The endpoint list is read from Flask's own route table at request time, so it can
never drift from the code: a new route appears here by itself, with the first line
of its docstring as the summary.
"""
import inspect

from flask import Blueprint, current_app, jsonify

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
