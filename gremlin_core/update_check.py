"""
Advisory-only check of pending pacman/Manjaro updates against Manjaro's
own "Stable Update" forum announcements. Manjaro devs curate a "Known
issues" section directly in the opening post of each stable-update
thread (forum.manjaro.org/c/announcements/stable-updates), and the
community maintains a "Known issues and solutions" wiki reply right
under it -- both get checked for mentions of whatever's actually
pending on this machine.

Deliberately never runs the update itself, and never modifies package
state: relies entirely on `checkupdates` (from pacman-contrib), which
syncs into its own isolated copy of the package database rather than
the real one, so calling this is always safe regardless of how often
it's used. See gremlin_core.sandbox / script_edit for the actual "run a
system command" machinery this project already has elsewhere -- this
module is intentionally narrower and doesn't touch it at all.
"""
from __future__ import annotations
import json
import subprocess
from html.parser import HTMLParser
from typing import Optional
from urllib.request import Request, urlopen

STABLE_UPDATES_CATEGORY_URL = "https://forum.manjaro.org/c/announcements/stable-updates/12.json"
FORUM_BASE = "https://forum.manjaro.org"
USER_AGENT = "gremlin-update-check/1.0"


class _HTMLTextExtractor(HTMLParser):
    """Strips tags from a Discourse post's `cooked` HTML down to plain
    text -- just enough to substring-search against, not meant to
    preserve structure or formatting."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)


def _strip_html(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.text()


def checkupdates_available() -> bool:
    return subprocess.run(["which", "checkupdates"], capture_output=True).returncode == 0


def get_pending_updates() -> list[str]:
    """Package names with an update pending, via `checkupdates` -- syncs
    into its own temp copy of the pacman database, never the real one.
    Returns [] both when nothing needs updating and when checkupdates
    isn't installed; see checkupdates_available() to tell those apart
    if that distinction matters to the caller."""
    if not checkupdates_available():
        return []
    result = subprocess.run(["checkupdates"], capture_output=True, text=True)
    # checkupdates exits 2 with empty stdout when nothing's pending --
    # that's a normal outcome, not an error.
    if result.returncode not in (0, 2):
        return []
    packages = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            packages.append(line.split()[0])
    return packages


def fetch_latest_stable_update_thread() -> Optional[dict]:
    """The most recent [Stable Update] announcement topic. Returns None
    on any network/parse failure -- this is advisory, so a forum outage
    should degrade to "couldn't check," never block or crash anything
    that depends on it."""
    try:
        req = Request(STABLE_UPDATES_CATEGORY_URL, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=10) as resp:
            category = json.loads(resp.read())
        topics = category.get("topic_list", {}).get("topics", [])
        if not topics:
            return None
        topic_id = topics[0]["id"]
        title = topics[0]["title"]

        req = Request(f"{FORUM_BASE}/t/{topic_id}.json", headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=10) as resp:
            topic = json.loads(resp.read())

        posts = topic.get("post_stream", {}).get("posts", [])
        # Post 1: Manjaro devs' own changelog + curated "Known issues".
        # Post 2 (when present): community "Known issues and solutions" wiki.
        combined_html = "\n".join(p.get("cooked", "") for p in posts[:2])
        return {
            "title": title,
            "url": f"{FORUM_BASE}/t/{topic_id}",
            "text": _strip_html(combined_html),
        }
    except Exception:
        return None


def find_mentioned_packages(pending: list[str], thread_text: str) -> list[str]:
    """Which pending packages actually show up in the thread's
    known-issues text -- a plain case-insensitive substring match, not
    an LLM judgment call. A false positive here just means a flagged
    package turns out to be an irrelevant mention; a false negative
    means missing a real warning -- substring matching is the safer
    failure mode to reason about than trusting an LLM summary as the
    only signal."""
    lowered = thread_text.lower()
    return [pkg for pkg in pending if pkg.lower() in lowered]


def run_check() -> dict:
    """The whole advisory flow in one call. Never runs the actual
    system update -- only reports what's pending and what's been
    flagged, so the human still decides."""
    if not checkupdates_available():
        return {
            "ok": False,
            "error": "checkupdates isn't installed -- run: sudo pacman -S pacman-contrib",
        }

    pending = get_pending_updates()
    if not pending:
        return {"ok": True, "pending": [], "flagged": [], "thread_url": None,
                 "summary": "No updates pending."}

    thread = fetch_latest_stable_update_thread()
    if thread is None:
        return {
            "ok": True,
            "pending": pending,
            "flagged": [],
            "thread_url": None,
            "summary": f"{len(pending)} update(s) pending. Couldn't reach the Manjaro forum to check for known issues.",
        }

    flagged = find_mentioned_packages(pending, thread["text"])
    if flagged:
        summary = (
            f"{len(pending)} update(s) pending. {len(flagged)} mentioned in the forum's latest "
            f"stable-update thread (\"{thread['title']}\"): {', '.join(flagged)}. "
            f"Worth reading before updating: {thread['url']}"
        )
    else:
        summary = (
            f"{len(pending)} update(s) pending. None mentioned in the known-issues section of "
            f"the forum's latest stable-update thread (\"{thread['title']}\") -- doesn't "
            f"guarantee a clean update, just nothing flagged there yet."
        )

    return {
        "ok": True,
        "pending": pending,
        "flagged": flagged,
        "thread_url": thread["url"],
        "summary": summary,
    }
