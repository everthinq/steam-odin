"""Small process-level operations shared between app.py and route blueprints.

Kept in its own module (imported by both) so the blueprints don't have to import
app.py — which would be a circular import.
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)


def trigger_restart():
    """Restart the backend by exiting the process after a short delay (the
    container's restart policy brings it back up)."""
    def _restart():
        time.sleep(3)
        logger.info("[SYSTEM] Restarting backend via os._exit(1)...")
        os._exit(1)

    threading.Thread(target=_restart).start()
