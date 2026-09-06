"""Gjallarhorn news watcher (bullet 4).

Polls the official Counter-Strike 2 update feed and RINGS + texts the moment
Valve ADDS or REMOVES a case / collection / capsule / souvenir. Those are the
supply-shock "limiting" events Gjallarhorn is built to trade: a limited item
pumps, so the horn has to sound the same minute.

Source of truth
---------------
The counter-strike.net/news/updates page is JavaScript-rendered, so we read the
machine feed that backs it: the Steam news API for appid 730. Every official
patch is published there as a "Counter-Strike 2 Update" (older: "Release Notes
for ...") post whose body is BBCode with section headers like ``[ ARMORY ]`` and
bulleted items.

Detection philosophy — RECALL over precision
--------------------------------------------
Ivan's real examples proved the biggest plays are the ones Valve does NOT
announce as removals: introducing the Kilowatt Case silently limited Snakebite;
the Dead Hand Collection launch never said what rotated out. So we fire on BOTH
directions:

* ADD    — a new case/collection/capsule/souvenir (or a "Limited Edition Item")
           is introduced. A new container almost always means an old one just
           rotated out of the drop pool and got limited.
* REMOVE  — a case/collection/capsule/souvenir is explicitly pulled
           ("no longer available", "Removed ... from the ... drop list").

Map-pool changes are ignored entirely (Ivan's rule). A routine bug-fix patch
mentions none of the nouns, so it stays silent — the phone only rings on a real
container event.
"""

import datetime
import json
import logging
import re
import threading
import time
import urllib.request

from notifications import send_notification

log = logging.getLogger(__name__)

NEWS_URL = (
    "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
    "?appid=730&count=15&maxlength=0&format=json"
)
_HTTP_TIMEOUT = 20
_USER_AGENT = "Mozilla/5.0 (steam-odin Gjallarhorn news watcher)"

# Only the canonical patch-note posts carry the authoritative container changes;
# marketing recaps ("Season 5, Armory, and More") would double-alert the same
# launch under a different post id, so we skip them.
_UPDATE_TITLE_RE = re.compile(r"^(counter-strike 2 update|release notes)", re.IGNORECASE)

# The containers whose add/removal is a limiting event. Valve CAPITALIZES the
# type word when naming a real product ("Kilowatt Case", "Dead Hand Collection",
# "Ambush Sticker Capsule", "Cologne 2026 Souvenir Package") but lowercases the
# generic English word ("fixed a case where", "a collection of items", "souvenir
# quality items in a trade-up"). Matching the capitalized product form — plus the
# explicit "weapon/sticker collection" phrasing Valve uses for Armory rotations —
# is what separates a real limiting event from a coincidental word. Live-tested
# against the real feed: this drops the "Souvenir Charms" and "Souvenir quality
# / a collection of items" false positives while keeping every real rotation.
_CONTAINER_RES = [
    ("case", re.compile(r"\bCases?\b")),                          # "Kilowatt Case"
    ("capsule", re.compile(r"\bCapsules?\b")),                    # "Ambush Sticker Capsule"
    # A proper-noun collection: Title-Case name(s) followed by "Collection".
    ("collection", re.compile(r"\b(?:[A-Z][\w'&.-]*\s+){1,4}Collection\b")),  # "Dead Hand Collection"
    # Valve's Armory-rotation phrasing (lowercase but explicitly typed).
    ("collection", re.compile(r"\b(?:weapon|sticker)\s+collections?\b", re.IGNORECASE)),
    # A souvenir CONTAINER (package/case), not the "souvenir" quality tier or charms.
    ("souvenir", re.compile(r"\bSouvenir\s+(?:Package|Case)s?\b", re.IGNORECASE)),
]
# A separately-called-out limited item (e.g. "AK-47 | Aphrodite").
_LIMITED_ITEM_RE = re.compile(r"limited edition item", re.IGNORECASE)

# Phrases that mean "this just appeared" vs "this just went away".
_ADD_RE = re.compile(
    r"\b(introducing|now available|available for purchase|added|is now available"
    r"|is available)\b",
    re.IGNORECASE,
)
_REMOVE_RE = re.compile(
    r"\b(no longer available|removed|are no longer|is no longer)\b",
    re.IGNORECASE,
)

# Skip anything about the map pool — not a container event.
_MAP_POOL_RE = re.compile(r"map pool|active duty", re.IGNORECASE)


def _bbcode_to_lines(raw):
    """Turn a Steam BBCode post body into clean plain-text lines.

    Section headers arrive as escaped brackets (``\\[ ARMORY ]``) and items as
    ``[*]...[/*]`` inside ``[list]``; every other tag (``[p]``, ``[b]``, ``[url]``,
    ...) is dropped. List/paragraph boundaries become line breaks so each bullet
    is one line the detector can scan.
    """
    if not raw:
        return []
    text = raw.replace("\\[", "[").replace("\\]", "]")
    # Turn structural tags into line breaks before stripping the rest.
    text = re.sub(r"\[/?(?:p|list|\*|br|h\d|tr)\b[^\]]*\]", "\n", text, flags=re.IGNORECASE)
    # Drop every remaining BBCode tag ([b], [url=...], [img], [i], ...).
    text = re.sub(r"\[/?[a-z][^\]]*\]", "", text, flags=re.IGNORECASE)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def detect_title(title):
    """A standalone post titled after a container ("The Jackass Sticker Capsule",
    "Dreams & Nightmares Case") IS a launch announcement — treat the title as an
    ADD even though it carries no verb. Patch-note titles ("Counter-Strike 2
    Update") and recap/esports titles ("Season 5, Armory, and More", "IEM Cologne
    2026") name no container, so they return nothing and never double-ring."""
    title = (title or "").strip()
    for container_kind, rx in _CONTAINER_RES:
        if rx.search(title):
            return {"action": "add", "kind": container_kind, "text": title}
    return None


def detect(contents):
    """Return the container add/remove hits in one post body.

    Each hit: ``{action: 'add'|'remove', kind, text}``. ``kind`` is the matched
    noun (case/collection/capsule/souvenir) or 'limited item'. Map-pool lines are
    dropped. Removal wins over addition when a single line matches both (a
    "no longer available" line is the stronger, explicit signal).
    """
    hits = []
    for line in _bbcode_to_lines(contents):
        if _MAP_POOL_RE.search(line):
            continue
        kind = None
        for container_kind, rx in _CONTAINER_RES:
            if rx.search(line):
                kind = container_kind
                break
        is_limited_item = bool(_LIMITED_ITEM_RE.search(line))
        if kind is None and not is_limited_item:
            continue
        if kind is None:
            kind = "limited item"
        if _REMOVE_RE.search(line):
            hits.append({"action": "remove", "kind": kind, "text": line})
        elif _ADD_RE.search(line) or is_limited_item:
            hits.append({"action": "add", "kind": kind, "text": line})
    return hits


class GjallarhornNewsService:
    """Background poller: fetch the CS2 update feed, detect container events, ring."""

    def __init__(self, settings_manager, telegram_caller=None):
        self.settings_manager = settings_manager
        self.telegram_caller = telegram_caller
        self.thread = None
        self.stop_event = threading.Event()
        self._last_run = None      # unix ts of the last poll attempt
        self._last_error = None

    # -- feed --------------------------------------------------------------
    def _fetch(self):
        req = urllib.request.Request(NEWS_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.load(resp)
        # Newest first from Steam. We keep ALL posts and decide per-post how to
        # read them (patch-note body vs container-titled announcement) in _hits.
        return (data.get("appnews") or {}).get("newsitems") or []

    @staticmethod
    def _hits(post):
        """The container add/remove hits for one post. Canonical patch notes are
        body-scanned (Armory rotations, Weekly Care Package changes, new cases);
        a post whose TITLE names a container is a standalone launch → title ADD
        plus any body hits; everything else (recaps, esports) is ignored."""
        title = (post.get("title") or "").strip()
        if _UPDATE_TITLE_RE.match(title):
            return detect(post.get("contents") or "")
        title_hit = detect_title(title)
        if title_hit is None:
            return []
        # Merge title hit with body hits, dropping duplicates of the same line.
        hits = [title_hit]
        for h in detect(post.get("contents") or ""):
            if h["text"] != title_hit["text"]:
                hits.append(h)
        return hits

    # -- one poll ----------------------------------------------------------
    def check_once(self, force=False):
        """Poll once. Alert on new limiting posts. Returns a summary dict.

        `force` re-evaluates the newest post even if already seen (used by the
        "check now" button) but never re-alerts older backlog.
        """
        self._last_run = time.time()
        settings = self.settings_manager.get_settings()
        last_seen = int(settings.get("gjallarhorn_news_last_seen_date") or 0)
        last_gid = str(settings.get("gjallarhorn_news_last_gid") or "")
        armed = bool(settings.get("gjallarhorn_news_armed"))
        try:
            posts = self._fetch()
        except Exception as e:
            self._last_error = str(e)
            log.warning("[GJALLARHORN-NEWS] fetch failed: %s", e)
            return {"ok": False, "error": str(e)}
        self._last_error = None

        if not posts:
            return {"ok": True, "checked": 0, "events": []}

        newest = max(posts, key=lambda p: int(p.get("date") or 0))
        newest_date = int(newest.get("date") or 0)
        newest_gid = str(newest.get("gid") or "")

        # Cheap "is the latest the same as last time?" check: if the newest post
        # id is unchanged, nothing new has been published — skip the whole scan.
        if not force and newest_gid and newest_gid == last_gid:
            return {"ok": True, "checked": 0, "events": [], "unchanged": True}

        # First-ever run: adopt the current newest as the baseline and do NOT
        # alert the whole backlog. From now on only genuinely new posts fire.
        if last_seen == 0 and not force:
            self.settings_manager.record_gjallarhorn_news(newest_date, last_gid=newest_gid)
            return {"ok": True, "checked": len(posts), "events": [], "baseline": True}

        # Consider posts newer than the high-water mark, oldest first so history
        # reads chronologically. `force` also re-checks the single newest post.
        candidates = [p for p in posts if int(p.get("date") or 0) > last_seen]
        if force and not candidates and posts:
            candidates = [max(posts, key=lambda p: int(p.get("date") or 0))]
        candidates.sort(key=lambda p: int(p.get("date") or 0))

        fired = []
        for post in candidates:
            hits = self._hits(post)
            if not hits:
                continue
            record = {
                "gid": post.get("gid"),
                "title": post.get("title"),
                "url": post.get("url"),
                "date": int(post.get("date") or 0),
                "detected_at": int(time.time()),
                "hits": hits,
                "armed": armed,
            }
            if armed:
                self._alert(record)
            self.settings_manager.record_gjallarhorn_news(newest_date, record, last_gid=newest_gid)
            fired.append(record)

        # No hits anywhere, but advance the marker so we don't re-scan them.
        if not fired and candidates:
            self.settings_manager.record_gjallarhorn_news(newest_date, last_gid=newest_gid)
        elif not candidates:
            # Newest changed (e.g. a post edited/re-id'd) but nothing newer to scan;
            # still record the id so we don't re-scan next poll.
            self.settings_manager.record_gjallarhorn_news(last_seen, last_gid=newest_gid)

        return {"ok": True, "checked": len(candidates), "events": fired}

    # -- alerting ----------------------------------------------------------
    def _alert(self, record):
        text = self._format(record)
        settings = self.settings_manager.get_settings()
        # Send to Gjallarhorn's OWN chat so it never mixes with the Case Arbitrage
        # board; same bot token, just a different chat. Fall back to the shared
        # chat if a dedicated one isn't configured.
        alert_settings = settings
        gjallarhorn_chat = str(settings.get("gjallarhorn_chat_id") or "").strip()
        if gjallarhorn_chat:
            alert_settings = dict(settings)
            alert_settings["telegram_chat_id"] = gjallarhorn_chat
        try:
            send_notification(alert_settings, text)
        except Exception as e:
            log.warning("[GJALLARHORN-NEWS] text alert failed: %s", e)
        if self.telegram_caller is not None:
            try:
                # Pass the WHY as the message so it lands in the same chat as the
                # wake-up call; the caller sends it before it starts ringing.
                self.telegram_caller.ring(message=text)
            except Exception as e:
                log.warning("[GJALLARHORN-NEWS] ring failed: %s", e)

    @staticmethod
    def _format(record):
        adds = [h for h in record["hits"] if h["action"] == "add"]
        removes = [h for h in record["hits"] if h["action"] == "remove"]
        lines = []
        if record.get("date"):
            date_str = datetime.datetime.utcfromtimestamp(int(record["date"])).strftime("%d %b %Y")
            lines.append(f"📅 {date_str}")
        lines += ["🔔 Gjallarhorn — possible LIMITING event",
                  record.get("title") or "Counter-Strike 2 Update"]
        if record.get("url"):
            lines.append(record["url"])
        if adds:
            lines.append("")
            lines.append("ADDED (something likely just rotated out):")
            lines += [f"• [{h['kind']}] {h['text']}" for h in adds]
        if removes:
            lines.append("")
            lines.append("REMOVED / limited:")
            lines += [f"• [{h['kind']}] {h['text']}" for h in removes]
        return "\n".join(lines)

    # -- daemon ------------------------------------------------------------
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        log.info("[GJALLARHORN-NEWS] watcher started")

    def stop(self):
        self.stop_event.set()

    def _run_loop(self):
        while not self.stop_event.is_set():
            try:
                self.check_once()
            except Exception as e:  # never let the watcher thread die
                log.warning("[GJALLARHORN-NEWS] poll error: %s", e)
            settings = self.settings_manager.get_settings()
            # Floor at 2 min so you can poll aggressively on a limiting day without
            # a config typo turning into a hammering loop.
            minutes = max(2, int(settings.get("gjallarhorn_news_poll_minutes") or 10))
            for _ in range(minutes * 60):
                if self.stop_event.is_set():
                    return
                time.sleep(1)

    # -- status ------------------------------------------------------------
    def status(self):
        settings = self.settings_manager.get_settings()
        history = list(settings.get("gjallarhorn_news_history") or [])
        return {
            "armed": bool(settings.get("gjallarhorn_news_armed")),
            "poll_minutes": int(settings.get("gjallarhorn_news_poll_minutes") or 10),
            "chat_id": str(settings.get("gjallarhorn_chat_id") or ""),
            "shared_chat_id": str(settings.get("telegram_chat_id") or ""),
            "last_seen_date": int(settings.get("gjallarhorn_news_last_seen_date") or 0),
            "last_run": self._last_run,
            "last_error": self._last_error,
            "running": bool(self.thread and self.thread.is_alive()),
            "can_ring": bool(self.telegram_caller and self.telegram_caller.status().get("configured")),
            "recent": history[-10:][::-1],
        }
