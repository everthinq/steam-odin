"""One shared gap between steamcommunity.com calls made by Heimdall's Market sellers
(Andvari card auto-sell and the Team Fortress 2 auto-sell run at the same time):
Steam rate-limits per address, so their gaps must add up, not overlap."""
import threading
import time

COMMUNITY_GAP_SECONDS = 4

_lock = threading.Lock()
_last_call = [0.0]


def pace(sleep=time.sleep, gap=COMMUNITY_GAP_SECONDS):
    """Wait until *gap* seconds passed since the last call any seller made."""
    with _lock:
        wait = gap - (time.time() - _last_call[0])
        if wait > 0:
            sleep(wait)
        _last_call[0] = time.time()
