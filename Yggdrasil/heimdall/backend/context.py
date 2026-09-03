"""Shared service holder for the route blueprints.

``app.py`` is the composition root: it constructs the singleton services once at
startup and assigns them onto :data:`ctx`. Blueprint route handlers read them
from here at REQUEST time (by which point they are set), so the blueprints never
import from ``app.py`` (no circular imports) and don't depend on import order.
"""


class _Context:
    """Populated by app.py at startup; attributes are the app's singletons."""
    settings_manager = None
    steam_service = None
    ratatoskr_service = None
    huginn_service = None
    draupnir_service = None
    draupnir_backup = None
    scheduler = None
    mimir_service = None


ctx = _Context()
