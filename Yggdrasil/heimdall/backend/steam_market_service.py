"""Steam Community Market liquidity for Gjallarhorn.

Two Steam endpoints back the "can I actually sell this fast?" signal:

* ``priceoverview`` — PUBLIC. Gives the lowest listing, median, and 24h volume
  (units sold in the last day). No login needed, so it always works.
* ``pricehistory`` — AUTHENTICATED (needs a ``steamLoginSecure`` cookie). Gives
  the full daily [date, median, volume] series, from which we derive 7-day and
  30-day traded volume plus a short-horizon price trend. We reuse a live account
  web session (SteamService.web_session_cookie) when one is available and simply
  skip the 7-day enrichment when it is not.

Both endpoints throttle hard (Steam 429s on bursts), so every call is serial,
spaced by a minimum gap, and cached; fetching happens off the request path in a
single background warm thread. This mirrors HuginnService.prices_for_valuation's
non-blocking "serve cache now, warm in the background" pattern — the request
returns instantly with whatever is cached and a status of 'warming' | 'fresh'.
"""
import gzip
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_APPID = 730  # CS2 / CS:GO
_OVERVIEW_URL = 'https://steamcommunity.com/market/priceoverview/'
_HISTORY_URL = 'https://steamcommunity.com/market/pricehistory/'
_USER_AGENT = 'Mozilla/5.0 (Heimdall Gjallarhorn liquidity)'
# 7-day/30-day history barely moves and is the expensive authenticated call, so
# we persist it (gzip JSON) and reload it on boot — that is what stops the page
# re-fetching every item's history (the Steam 429 source) after a restart.
_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'cache', 'gjallarhorn_liquidity.json.gz')


def _money(value):
    """'$1,234.56' -> 1234.56 (USD; currency=1). None if unparseable."""
    if value is None:
        return None
    digits = ''.join(c for c in str(value) if c.isdigit() or c == '.')
    try:
        return round(float(digits), 4) if digits else None
    except ValueError:
        return None


def _int(value):
    """'1,234' -> 1234. None if unparseable."""
    if value is None:
        return None
    digits = ''.join(c for c in str(value) if c.isdigit())
    return int(digits) if digits else None


class SteamMarketService:
    # How long a cached datum is served before the warmer refreshes it. The 24h
    # overview is cheap and fresh-sensitive; the daily history barely moves, so it
    # is refreshed far less often (once a day) to stay well under Steam's rate limits.
    _OVERVIEW_TTL = 15 * 60          # 15 minutes
    _HISTORY_TTL = 24 * 60 * 60      # 24 hours (persisted to disk between runs)
    _THROTTLE_SEC = 3.0              # minimum gap between Steam HTTP calls (429 guard)
    _HISTORY_EXTRA_SLEEP = 2.0       # extra pause after each authenticated history call
    # While the user is actively on the Gjallarhorn page we only refresh the cheap
    # overview; the heavy authenticated history is deferred to idle so it never
    # competes with (and 429s) the live page. Activity is a short sliding window.
    _ACTIVE_WINDOW = 120             # seconds a page hit counts as "active"
    _IDLE_RECHECK_SEC = 20           # how often the warmer re-checks for idle to drain deferred history
    _WARM_MAX_LIFETIME = 30 * 60     # cap the warm thread so deferred work can't spin forever
    _SAVE_MIN_INTERVAL = 30          # don't rewrite the gzip cache more often than this

    def __init__(self, steam_service):
        self.steam = steam_service
        self._cache = {}             # name -> {'overview': (ts, data), 'history': (ts, data)}
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._warming = False
        self._queue = []             # names pending a warm fetch (FIFO)
        self._deferred = []          # names owed a history fetch, held until the page is idle
        self._active_until = 0.0     # wall-clock until which the page counts as active
        self._dirty = False          # cache changed since last disk save
        self._last_save = 0.0
        self._load_cache()

    # ---- public API --------------------------------------------------------

    def liquidity(self, names):
        """Return ({name: view_or_None}, status) for the requested names.

        Serves cached liquidity instantly and warms anything missing or stale in
        a single background thread. status is 'warming' when a refresh is in
        flight for this call's names, else 'fresh'. Never blocks on Steam."""
        names = [n for n in names if n]
        now = time.time()
        out = {}
        stale = []
        with self._lock:
            for name in names:
                entry = self._cache.get(name)
                out[name] = self._view(entry)
                if self._needs_refresh(entry, now):
                    stale.append(name)
            if stale:
                pending = set(self._queue)
                self._queue.extend(n for n in stale if n not in pending)
                if not self._warming:
                    self._warming = True
                    threading.Thread(target=self._warm, daemon=True).start()
        return out, ('warming' if stale else 'fresh')

    def note_activity(self):
        """Mark the Gjallarhorn page as actively in use right now. While active,
        the warmer refreshes only the cheap overview and defers the heavy
        authenticated history to idle, so it never 429s the live page."""
        with self._lock:
            self._active_until = time.time() + self._ACTIVE_WINDOW

    def _is_active(self):
        return time.time() < self._active_until

    # ---- warm loop ---------------------------------------------------------

    def _needs_refresh(self, entry, now):
        if not entry:
            return True
        overview = entry.get('overview')
        return not overview or (now - overview[0]) >= self._OVERVIEW_TTL

    def _history_stale(self, entry, now):
        history = (entry or {}).get('history')
        return not history or (now - history[0]) >= self._HISTORY_TTL

    def _warm(self):
        deadline = time.time() + self._WARM_MAX_LIFETIME
        try:
            while time.time() < deadline:
                with self._lock:
                    if not self._queue and not self._deferred:
                        self._warming = False
                        break
                    # Nothing queued but history is owed: wait for the page to go
                    # idle, then drain the deferred history fetches.
                    if not self._queue and self._deferred:
                        if self._is_active():
                            name = None
                        else:
                            self._queue = self._deferred
                            self._deferred = []
                            name = self._queue.pop(0)
                    else:
                        name = self._queue.pop(0)
                if name is None:
                    self._save_cache()          # persist what we have while we wait
                    time.sleep(self._IDLE_RECHECK_SEC)
                    continue
                try:
                    self._fetch_one(name)
                except Exception as e:
                    logger.warning('[GJALLARHORN] liquidity fetch failed for %s: %s', name, e)
            self._save_cache()
        except Exception as e:  # never leave _warming stuck on
            logger.error('[GJALLARHORN] liquidity warm loop crashed: %s', e)
        finally:
            with self._lock:
                self._warming = False

    def _fetch_one(self, name):
        now = time.time()
        entry = dict(self._cache.get(name) or {})
        # A logged-in cookie also lifts priceoverview's (harsh) anonymous rate
        # limit, so use it for both calls when a session is available.
        cookies = self._cookies()

        # 24h overview — lowest/median/24h volume. Cheap; always refreshed.
        try:
            data = self._http_get(_OVERVIEW_URL, {
                'appid': _APPID, 'currency': 1, 'market_hash_name': name}, cookies=cookies)
            entry['overview'] = (now, self._parse_overview(data))
        except Exception as e:
            logger.warning('[GJALLARHORN] priceoverview %s: %s', name, e)
            entry.setdefault('overview', (now, None))

        # 7-day history — authenticated and heavy. Only when stale and a session
        # exists; DEFERRED while the page is active so it warms during idle only.
        if cookies and self._history_stale(entry, now):
            if self._is_active():
                with self._lock:
                    self._cache[name] = entry
                    if name not in self._deferred:
                        self._deferred.append(name)
                self._dirty = True
                return
            try:
                data = self._http_get(_HISTORY_URL, {
                    'appid': _APPID, 'market_hash_name': name}, cookies=cookies)
                entry['history'] = (now, self._parse_history(data))
                # Extra breathing room after the harsh authenticated endpoint.
                time.sleep(self._HISTORY_EXTRA_SLEEP)
            except Exception as e:
                logger.warning('[GJALLARHORN] pricehistory %s: %s', name, e)

        with self._lock:
            self._cache[name] = entry
            self._dirty = True

    # ---- disk cache (gzip JSON) --------------------------------------------

    def _load_cache(self):
        try:
            if not os.path.exists(_CACHE_FILE):
                return
            with gzip.open(_CACHE_FILE, 'rt', encoding='utf-8') as f:
                raw = json.load(f)
            # JSON turns the (ts, data) tuples into [ts, data] lists — restore them.
            cache = {}
            for name, entry in (raw or {}).items():
                restored = {}
                for kind in ('overview', 'history'):
                    pair = entry.get(kind)
                    if isinstance(pair, list) and len(pair) == 2:
                        restored[kind] = (float(pair[0]), pair[1])
                if restored:
                    cache[name] = restored
            self._cache = cache
            logger.info('[GJALLARHORN] loaded %d cached liquidity entries', len(cache))
        except Exception as e:
            logger.warning('[GJALLARHORN] could not load liquidity cache: %s', e)

    def _save_cache(self):
        with self._lock:
            if not self._dirty:
                return
            if (time.time() - self._last_save) < self._SAVE_MIN_INTERVAL:
                return
            snapshot = {
                name: {kind: [pair[0], pair[1]] for kind, pair in entry.items()}
                for name, entry in self._cache.items()
            }
            self._last_save = time.time()
            self._dirty = False
        try:
            os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
            tmp = f'{_CACHE_FILE}.tmp'
            with gzip.open(tmp, 'wt', encoding='utf-8') as f:
                json.dump(snapshot, f)
            os.replace(tmp, _CACHE_FILE)
        except Exception as e:
            logger.warning('[GJALLARHORN] could not save liquidity cache: %s', e)
            with self._lock:
                self._dirty = True

    # ---- HTTP + auth -------------------------------------------------------

    def _throttle(self):
        with self._lock:
            wait = self._THROTTLE_SEC - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        with self._lock:
            self._last_call = time.time()

    def _http_get(self, url, params, cookies=None):
        self._throttle()
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f'{url}?{query}', headers={'User-Agent': _USER_AGENT})
        if cookies:
            request.add_header('Cookie', '; '.join(f'{k}={v}' for k, v in cookies.items()))
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))

    def _cookies(self):
        got = self.steam.web_session_cookie()
        return got[1] if got else None

    # ---- parsing -----------------------------------------------------------

    @staticmethod
    def _parse_overview(data):
        if not isinstance(data, dict) or not data.get('success'):
            return None
        return {
            'lowest': _money(data.get('lowest_price')),
            'median': _money(data.get('median_price')),
            'volume24h': _int(data.get('volume')),
        }

    @staticmethod
    def _parse_history(data):
        if not isinstance(data, dict) or not data.get('success'):
            return None
        now = datetime.now(timezone.utc)
        points = []
        for row in (data.get('prices') or []):
            try:
                # "Jul 18 2019 01: +0" -> drop the "+0" hour-offset tail, parse the rest.
                stamp = str(row[0]).rsplit(':', 1)[0].strip()
                when = datetime.strptime(stamp, '%b %d %Y %H').replace(tzinfo=timezone.utc)
                points.append((when, float(row[1]), int(float(row[2]))))
            except (ValueError, IndexError, TypeError):
                continue
        if not points:
            return None
        volume7d = sum(v for w, _, v in points if (now - w).days <= 7)
        volume30d = sum(v for w, _, v in points if (now - w).days <= 30)
        latest = points[-1][1]
        trend7d = None
        for when, median, _ in reversed(points):
            if (now - when).days >= 7 and median:
                trend7d = round((latest - median) / median * 100, 2)
                break
        return {
            'volume7d': volume7d,
            'volume30d': volume30d,
            'trend7dPct': trend7d,
            'latestMedian': latest,
        }

    @staticmethod
    def _view(entry):
        if not entry:
            return None
        overview = (entry.get('overview') or (0, None))[1] or {}
        history = (entry.get('history') or (0, None))[1] or {}
        lowest = overview.get('lowest')
        median = overview.get('median') or history.get('latestMedian')
        spread = None
        if lowest and median and lowest > 0:
            spread = round((median - lowest) / lowest * 100, 2)
        return {
            'volume24h': overview.get('volume24h'),
            'volume7d': history.get('volume7d'),
            'volume30d': history.get('volume30d'),
            'trend7dPct': history.get('trend7dPct'),
            'lowest': lowest,
            'median': median,
            'spreadPct': spread,
        }
