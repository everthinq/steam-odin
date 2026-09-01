"""Smoke test for the blueprint split (#6): each extracted blueprint registers
its expected routes on a Flask app, without needing the real services.
"""
from flask import Flask


def _rules_for(bp):
    """Set of URL path strings (GET/POST on one path collapse to one entry)."""
    app = Flask(__name__)
    app.register_blueprint(bp)
    return {r.rule for r in app.url_map.iter_rules()}


def _rule_count(bp, prefix):
    """Count actual Rule objects (method variants counted separately)."""
    app = Flask(__name__)
    app.register_blueprint(bp)
    return len([r for r in app.url_map.iter_rules() if prefix in r.rule])


def test_portfolios_blueprint_registers_all_routes():
    from routes.portfolios import bp
    rules = _rules_for(bp)
    expected = {
        '/api/portfolios',
        '/api/portfolios/import',
        '/api/portfolios/combined',
        '/api/portfolios/arbitrage',
        '/api/portfolios/<pid>/export',
        '/api/portfolios/validate-item',
        '/api/portfolios/item-search',
        '/api/portfolios/<pid>',
        '/api/portfolios/<pid>/transactions',
        '/api/portfolios/<pid>/transactions/<tid>',
        '/api/portfolios/backups',
        '/api/portfolios/backups/snapshot',
        '/api/portfolios/backups/<name>/download',
        '/api/portfolios/backups/restore',
    }
    missing = expected - rules
    assert not missing, f'missing portfolio routes: {missing}'


def test_ratatoskr_blueprint_registers_all_routes():
    from routes.ratatoskr import bp
    rules = _rules_for(bp)
    for want in ['/api/ratatoskr/login', '/api/ratatoskr/status/<steamid>',
                 '/api/ratatoskr/move/batch', '/api/ratatoskr/auto-store',
                 '/api/ratatoskr/auto-store/sweep']:
        assert want in rules, f'missing ratatoskr route: {want}'
    assert _rule_count(bp, '/api/ratatoskr') == 17


def test_accounts_blueprint_registers_all_routes():
    from routes.accounts import bp
    rules = _rules_for(bp)
    for want in ['/api/accounts', '/api/accounts/import', '/api/accounts/authenticate',
                 '/api/accounts/<steamid>/confirmations',
                 '/api/accounts/<steamid>/confirmations/<cid>',
                 '/api/accounts/<steamid>', '/api/confirmations/check-all',
                 '/api/accounts/update-session', '/api/accounts/clear-web-session']:
        assert want in rules, f'missing account route: {want}'
    assert _rule_count(bp, '/api/accounts') == 9  # incl. GET+DELETE on /api/accounts


def test_settings_blueprint_registers_all_routes():
    from routes.settings import bp
    assert _rule_count(bp, '/api/settings') == 2  # GET + POST


def test_huginn_blueprint_registers_all_routes():
    from routes.huginn import bp
    rules = _rules_for(bp)
    for want in ['/api/huginn/scan', '/api/huginn/cases',
                 '/api/huginn/csfloat/buy-orders', '/api/huginn/tradeon/steam',
                 '/api/huginn/lootfarm/arbitrage']:
        assert want in rules, f'missing huginn route: {want}'
    assert _rule_count(bp, '/api/huginn') == 35


def test_register_blueprints_wires_onto_app():
    from routes import register_blueprints
    app = Flask(__name__)
    register_blueprints(app)
    rules = {r.rule for r in app.url_map.iter_rules()}
    for want in ['/api/portfolios', '/api/portfolios/backups/restore',
                 '/api/ratatoskr/login', '/api/accounts', '/api/settings',
                 '/api/confirmations/check-all']:
        assert want in rules, f'missing after register_blueprints: {want}'
