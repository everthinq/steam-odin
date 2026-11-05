"""System: what the backend offers and how it is doing (the System health page reads it).

The endpoint list is read from Flask's own route table at request time, so it can
never drift from the code: a new route appears here by itself, with the first line
of its docstring as the summary.

It is also the `odin` command-line tool's list of commands (``command_name``): the words of
the path after ``/api`` without its ``<parameters>`` (those become the command's arguments, in
order), plus a last word for what the method does — ``delete`` (DELETE), ``change`` (PATCH,
PUT), ``set`` (a POST next to a GET with the same words and arguments) — unless the route's
``@command_verb`` says it (``command_marks.py``, which also marks ``spends_money`` and
``shows_secrets``).
"""
import inspect
import re
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


METHOD_VERBS = {'DELETE': 'delete', 'PATCH': 'change', 'PUT': 'change'}
PARAMETER = re.compile(r'^<(?:[^:>]+:)?([^>]+)>$')


def path_words(path):
    """'/api/ratatoskr/plans/<plan_id>/execute' -> (['ratatoskr', 'plans', 'execute'], ['plan_id'])."""
    words, arguments = [], []
    for segment in path.strip('/').split('/'):
        parameter = PARAMETER.match(segment)
        if parameter:
            arguments.append(parameter.group(1))
        elif segment and not (segment == 'api' and not words):
            words.append(segment)
    return words, arguments


def command_name(view, path, method, has_get_twin):
    """The odin command of one route and method (see the module docstring)."""
    words, _ = path_words(path)
    verb = getattr(view, 'command_verb', None) or METHOD_VERBS.get(method)
    if not verb and method == 'POST' and has_get_twin:
        verb = 'set'
    return ' '.join(words + ([verb] if verb else []))


def endpoint_list(app):
    """Every route of *app*: path, methods, blueprint (the tool), summary, odin command and marks,
    sorted by path."""
    rules = [rule for rule in app.url_map.iter_rules() if rule.endpoint != 'static']
    with_get = {(tuple(path_words(rule.rule)[0]), len(path_words(rule.rule)[1]))
                for rule in rules if 'GET' in rule.methods}
    endpoints = []
    for rule in rules:
        blueprint, _, _ = rule.endpoint.rpartition('.')
        view = app.view_functions.get(rule.endpoint)
        methods = sorted(set(rule.methods or ()) - AUTOMATIC_METHODS)
        words, arguments = path_words(rule.rule)
        twin = (tuple(words), len(arguments)) in with_get
        endpoints.append({
            'path': rule.rule,
            'methods': methods,
            'group': blueprint or 'app',
            'name': rule.endpoint,
            'summary': summary_of(view),
            'description': inspect.getdoc(view) or '',
            'commands': {method: command_name(view, rule.rule, method, twin) for method in methods},
            'arguments': arguments,
            'spends_money': bool(getattr(view, 'spends_money', False)),
            'shows_secrets': bool(getattr(view, 'shows_secrets', False)),
        })
    endpoints.sort(key=lambda endpoint: (endpoint['path'], endpoint['methods']))
    return endpoints


@bp.route('/api/system/routes', methods=['GET'])
def list_routes():
    """Every backend endpoint: path, methods, group, first docstring line and its odin command."""
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
