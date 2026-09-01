"""Flask route blueprints, split out of the former monolithic app.py by domain.

Each module defines a ``bp`` Blueprint whose handlers read the app's services
from :mod:`context`. ``app.py`` imports and registers them via
:func:`register_blueprints`.
"""


def register_blueprints(app):
    """Register every route blueprint on *app*. Import inside the function so a
    blueprint import error can't break module import at collection time."""
    from routes.accounts import bp as accounts_bp
    from routes.settings import bp as settings_bp
    from routes.portfolios import bp as portfolios_bp
    from routes.ratatoskr import bp as ratatoskr_bp
    from routes.huginn import bp as huginn_bp
    from routes.mimir import bp as mimir_bp

    app.register_blueprint(accounts_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(portfolios_bp)
    app.register_blueprint(ratatoskr_bp)
    app.register_blueprint(huginn_bp)
    app.register_blueprint(mimir_bp)
