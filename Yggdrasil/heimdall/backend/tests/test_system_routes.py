"""The endpoint list comes from Flask's route table (routes/system.py)."""
from flask import Flask

import error_handlers
from routes import register_blueprints
from routes.system import endpoint_list


def _app():
    app = Flask(__name__)
    error_handlers.install(app)
    register_blueprints(app)
    return app


def test_list_names_every_route_with_methods_group_and_summary():
    app = _app()
    answer = app.test_client().get('/api/system/routes').get_json()
    by_key = {(e['path'], tuple(e['methods'])): e for e in answer['endpoints']}
    assert answer['count'] == len(answer['endpoints'])
    job = by_key[('/api/jobs/<job_id>', ('GET',))]
    assert job['group'] == 'jobs' and job['name'] == 'jobs.job_status'
    assert job['summary'].startswith('{id, kind')
    assert ('/api/system/routes', ('GET',)) in by_key
    # HEAD and OPTIONS are added by Flask to every route; the static route is not an endpoint.
    assert all('HEAD' not in e['methods'] and 'OPTIONS' not in e['methods'] for e in answer['endpoints'])
    assert all(e['name'] != 'static' for e in answer['endpoints'])


def test_new_route_appears_by_itself_and_missing_docstring_is_empty():
    app = _app()

    @app.route('/api/example', methods=['POST'])
    def example():
        return ''

    entry = next(e for e in endpoint_list(app) if e['path'] == '/api/example')
    assert entry == {'path': '/api/example', 'methods': ['POST'], 'group': 'app',
                     'name': 'example', 'summary': '', 'description': '', 'commands': {'POST': 'example'},
                     'arguments': [], 'spends_money': False, 'shows_secrets': False}


def test_every_blueprint_route_has_a_summary():
    """Each endpoint is described by its docstring, so the list (and odin routes) is complete.
    (Importing app.py would build every service against the real data; its one route, /health,
    has a docstring too.)"""
    missing = [e['name'] for e in endpoint_list(_app()) if not e['summary']]
    assert missing == []


def _commands():
    return {(entry['commands'][method], len(entry['arguments'])): (method, entry)
            for entry in endpoint_list(_app()) for method in entry['methods']}


def test_odin_command_names_come_from_the_path_and_method():
    commands = _commands()
    method, entry = commands[('ratatoskr plans execute', 1)]
    assert (method, entry['path'], entry['arguments']) == ('POST', '/api/ratatoskr/plans/<plan_id>/execute', ['plan_id'])
    assert commands[('settings', 0)][0] == 'GET'
    assert commands[('settings set', 0)][0] == 'POST'          # a POST next to a GET
    assert commands[('draupnir portfolios', 0)][0] == 'GET'    # the list ...
    assert commands[('draupnir portfolios', 1)][0] == 'GET'    # ... and one portfolio
    assert commands[('draupnir portfolios add', 0)][0] == 'POST'  # @command_verb
    assert commands[('draupnir portfolios change', 1)][0] == 'PATCH'
    assert commands[('draupnir portfolios delete', 1)][0] == 'DELETE'
    assert commands[('accounts confirmations answer', 2)][0] == 'POST'


def test_every_odin_command_is_unique():
    """Two routes with the same command words and argument count would be one command."""
    pairs = [(entry['commands'][method], len(entry['arguments']))
             for entry in endpoint_list(_app()) for method in entry['methods']]
    assert len(pairs) == len(set(pairs))


def test_money_and_secret_routes_are_marked():
    marked = {(entry['path'], method): (entry['spends_money'], entry['shows_secrets'])
              for entry in endpoint_list(_app()) for method in entry['methods']}
    spends = {key for key, (money, _) in marked.items() if money}
    secret = {key for key, (_, shows) in marked.items() if shows}
    assert spends == {('/api/ratatoskr/storage-shop/plans/<plan_id>/execute', 'POST'),
                      ('/api/huginn/card-deals/buy/purchase-plans/<purchase_plan_id>/execute', 'POST'),
                      ('/api/mimir/market-access/plans/<plan_id>/execute', 'POST')}
    assert {('/api/settings', 'GET'), ('/api/mimir/credentials/<rec_id>/password', 'POST'),
            ('/api/mimir/export', 'POST')} <= secret
