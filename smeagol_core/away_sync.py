"""
When the phone answers a message itself (away-mode, via direct Claude/
Gemini calls because the desktop wasn't reachable), the desktop has no
idea that exchange ever happened. This closes that gap: the phone
queues up away-mode exchanges locally, and the moment it successfully
reaches the desktop again, ships that queue along with its next
message -- no separate handshake needed, it rides along with the very
first successful reconnection.
"""
from __future__ import annotations
import json
import os
import time


def append_away_session(root: str, entries: list[dict]) -> int:
    """Appends each synced away-mode exchange to a durable log. Returns
    how many entries were actually written (skips anything malformed
    rather than failing the whole batch over one bad entry)."""
    path = os.path.join(root, "data", "away_session_log.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    written = 0
    with open(path, "a") as f:
        for entry in entries:
            if not isinstance(entry, dict) or "prompt" not in entry or "answer" not in entry:
                continue
            record = {
                "prompt": entry.get("prompt", ""),
                "answer": entry.get("answer", ""),
                "source": entry.get("source", "unknown"),
                "occurred_at": entry.get("timestamp"),
                "synced_at": time.time(),
            }
            f.write(json.dumps(record) + "\n")
            written += 1
    return written


def recent_entries(root: str, limit: int = 5) -> list[dict]:
    """The last `limit` synced away-session exchanges, oldest first --
    used to give Smeagol background on what was discussed while the
    user was away, without needing to re-read the whole log every time."""
    path = os.path.join(root, "data", "away_session_log.jsonl")
    if not os.path.exists(path):
        return []

    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip a corrupted line rather than fail the whole read
    return entries[-limit:]
