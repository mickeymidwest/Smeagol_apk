"""
A structured record of every successful code change Gremlin has made
to itself (self_improve.py) or to a script it was asked to fix
(script_edit.py) -- separate from git history and backup files, this
is meant to be easy to scan or query later: what changed, why, and
when, in one place.
"""
from __future__ import annotations
import json
import os
import time

LOG_FILENAME = "mutation_log.jsonl"


def _log_path(root: str) -> str:
    path = os.path.join(root, "data", LOG_FILENAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def append_mutation(root: str, entry: dict) -> None:
    entry = {"timestamp": time.time(), **entry}
    with open(_log_path(root), "a") as f:
        f.write(json.dumps(entry) + "\n")
