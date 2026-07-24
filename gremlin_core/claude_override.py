"""
"Claude override" -- a break-glass remote-debugging command triggered
from the phone app (see server.py's /admin/claude-override), gated the
same way /root already is (admin token) plus a typed "confirm" step
(same two-step pattern /rollback and /edit already use). Shells out to
the `claude` CLI already installed and authenticated on this desktop
under the user's own Claude subscription -- not a separate API key, see
config/models.yaml's now-disabled `claude` model entry for why that
distinction matters here.

Runs non-interactively with --dangerously-skip-permissions so it can
actually read/write files and run commands to fix things, not just
describe them -- deliberately NOT routed through self_improve.py's
two-reviewer gate. That gate exists because Gremlin's own automated
self-improvement loop has no human reviewing the goal before a patch
gets proposed. This is the opposite case: a human already typed the
problem and already confirmed running it, so the review step already
happened, just by a person instead of a second model.
"""
from __future__ import annotations
import json
import subprocess

DEFAULT_TIMEOUT = 600  # a real Claude Code session doing actual work can run a while


def run_override(project_root: str, prompt: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    if not prompt.strip():
        return {"ok": False, "error": "empty prompt"}

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--dangerously-skip-permissions", "--output-format", "json"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "`claude` CLI isn't installed/on PATH on this desktop"}
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"timed out after {timeout}s -- claude may still be running in the background",
        }

    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or f"exit code {result.returncode}"}

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": True, "result": result.stdout}

    # A session can exit 0 and still report failure in the payload itself
    # (e.g. it hit its own error partway through) -- returncode alone
    # isn't the whole picture, verified against a real invocation before
    # relying on this field.
    if payload.get("is_error"):
        return {"ok": False, "error": payload.get("result", "claude reported an error")}
    return {"ok": True, "result": payload.get("result", result.stdout)}
