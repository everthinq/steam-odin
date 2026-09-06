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


def test_draupnir_blueprint_registers_all_routes():
    from routes.draupnir import bp
    rules = _rules_for(bp)
    expected = {
        '/api/draupnir/portfolios',
        '/api/draupnir/portfolios/import',
        '/api/draupnir/portfolios/combined',
        '/api/draupnir/portfolios/arbitrage',
        '/api/draupnir/portfolios/<pid>/export',
        '/api/draupnir/portfolios/validate-item',
        '/api/draupnir/portfolios/item-search',
        '/api/draupnir/portfolios/<pid>',
        '/api/draupnir/portfolios/<pid>/transactions',
        '/api/draupnir/portfolios/<pid>/transactions/<tid>',
        '/api/draupnir/portfolios/backups',
        '/api/draupnir/portfolios/backups/snapshot',
        '/api/draupnir/portfolios/backups/<name>/download',
        '/api/draupnir/portfolios/backups/restore',
    }
    missing = expected - rules
    assert not missing, f'missing draupnir routes: {missing}'


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
                 '/api/huginn/lootfarm/arbitrage', '/api/huginn/markets',
                 '/api/huginn/markets/fees', '/api/huginn/tradeon/pair',
                 '/api/huginn/gjallarhorn/rotation', '/api/huginn/gjallarhorn/accounts',
                 '/api/huginn/gjallarhorn/readiness', '/api/huginn/gjallarhorn/holds',
                 '/api/huginn/gjallarhorn/targets', '/api/huginn/gjallarhorn/ring',
                 '/api/huginn/gjallarhorn/ring/status',
                 '/api/huginn/gjallarhorn/news/status',
                 '/api/huginn/gjallarhorn/news/check',
                 '/api/huginn/gjallarhorn/news/test']:
        assert want in rules, f'missing huginn route: {want}'
    # +9 gjallarhorn rules: rotation, accounts, readiness, holds (GET+POST),
    # targets (GET+POST), ring, ring/status; +3 news rules: news/status,
    # news/check, news/test.
    assert _rule_count(bp, '/api/huginn') == 50


def test_register_blueprints_wires_onto_app():
    from routes import register_blueprints
    app = Flask(__name__)
    register_blueprints(app)
    rules = {r.rule for r in app.url_map.iter_rules()}
    for want in ['/api/draupnir/portfolios', '/api/draupnir/portfolios/backups/restore',
                 '/api/ratatoskr/login', '/api/accounts', '/api/settings',
                 '/api/confirmations/check-all']:
        assert want in rules, f'missing after register_blueprints: {want}'
