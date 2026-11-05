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
                     'name': 'example', 'summary': ''}


def test_every_blueprint_route_has_a_summary():
    """Each endpoint is described by its docstring, so the list (and odin routes) is complete.
    (Importing app.py would build every service against the real data; its one route, /health,
    has a docstring too.)"""
    missing = [e['name'] for e in endpoint_list(_app()) if not e['summary']]
    assert missing == []
