"""
Lets Smeagol improve its own codebase using whichever models are
registered (local and/or API, one or several at once).

Flow:
  1. gather_source()   -- read Smeagol's own .py files
  2. propose_patch()   -- one or more models see the source + a goal,
                          each proposes a unified diff; if more than one
                          model is given, a synthesizer model merges the
                          proposals into a single final diff
  3. apply_patch()     -- git-checked apply: validated with `git apply
                          --check` first, then applied, then every
                          changed file is py_compile'd. If anything
                          fails, the change is reverted automatically.
                          If it passes, it's committed so it's always
                          one `git revert` away from undone.

This is intentionally conservative: no change lands without passing a
compile check, and every landed change is a discrete, revertible git
commit. Nothing here lets a model touch anything outside this repo.
"""
from __future__ import annotations
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .router import Router
from .sandbox import SecureExecutionSandbox
from . import mutation_log

SELF_IMPROVE_SYSTEM_PROMPT = (
    "You are improving the source code of an AI orchestrator called Smeagol. "
    "You will be shown its current source files and a goal. Respond with "
    "ONLY a valid unified diff (git-style, with ---/+++ headers and @@ hunks) "
    "that achieves the goal. No explanation, no markdown fences, no commentary "
    "-- just the raw diff. Keep changes minimal and focused on the stated goal. "
    "Never remove the sandboxing, safety checks, or error handling that already "
    "exist in the code."
)

MERGE_SYSTEM_PROMPT = (
    "You are merging several proposed unified diffs (from different AI models) "
    "that all attempt the same goal against the same source. Pick the best "
    "single approach, or combine the strongest parts, and output ONE final "
    "unified diff that applies cleanly. Output ONLY the diff, nothing else."
)


def gather_source(root: str, package: str = "smeagol_core") -> dict[str, str]:
    """Read every .py file in the given package, keyed by relative path."""
    base = Path(root) / package
    out = {}
    for path in base.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(root))
        out[rel] = path.read_text()
    return out


def _format_source_dump(source: dict[str, str]) -> str:
    chunks = []
    for path, content in source.items():
        chunks.append(f"--- FILE: {path} ---\n{content}")
    return "\n\n".join(chunks)


async def propose_patch(
    router: Router,
    model_names: list[str],
    goal: str,
    root: str,
    synthesizer: Optional[str] = None,
) -> str:
    """Ask one or more models to propose a diff; merge if more than one."""
    source_dump = _format_source_dump(gather_source(root))
    prompt = f"Goal: {goal}\n\nCurrent source:\n\n{source_dump}"

    if len(model_names) == 1:
        result = await router.route(model_names[0], prompt, system=SELF_IMPROVE_SYSTEM_PROMPT)
        return result.text

    results = await router.broadcast(model_names, prompt, system=SELF_IMPROVE_SYSTEM_PROMPT)
    proposals_text = "\n\n".join(
        f"=== Proposal from {name} ===\n{r.text if r.ok else f'[failed: {r.error}]'}"
        for name, r in results.items()
    )
    merge_prompt = f"Goal: {goal}\n\n{proposals_text}\n\nMerge into one final diff."
    synth = synthesizer or model_names[0]
    merged = await router.route(synth, merge_prompt, system=MERGE_SYSTEM_PROMPT)
    return merged.text


def _run(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


async def apply_patch(
    patch_text: str,
    root: str,
    goal: str,
    applied_by: str,
    run_tests: bool = False,
    test_timeout: int = 300,
) -> dict:
    """
    Validates and applies a unified diff against `root`, using git for
    safety. Returns a dict describing what happened -- never raises for
    an expected failure (bad patch, compile error, failing tests); those
    come back as {"applied": False, "reason": ...}.

    If `run_tests` is True, pytest runs (via SecureExecutionSandbox --
    confined cwd and a minimal PATH, not just a raw subprocess call)
    after the compile check and before the commit. Any test failure
    triggers the same rollback used for a compile failure. Off by
    default since a full suite can be slow or may not exist yet for
    every project state.
    """
    root = str(Path(root).resolve())

    if not (Path(root) / ".git").exists():
        _run(["git", "init"], root)
        _run(["git", "config", "user.email", "smeagol@localhost"], root)
        _run(["git", "config", "user.name", "Smeagol"], root)
        _run(["git", "add", "-A"], root)
        _run(["git", "commit", "-m", "baseline before self-improvement"], root)
    else:
        # repo-local identity fallback in case global git config isn't set
        _run(["git", "config", "user.email", "smeagol@localhost"], root)
        _run(["git", "config", "user.name", "Smeagol"], root)

    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
        f.write(patch_text)
        patch_path = f.name

    try:
        check = _run(["git", "apply", "--check", patch_path], root)
        if check.returncode != 0:
            return {"applied": False, "reason": f"patch does not apply cleanly:\n{check.stderr}"}

        apply_result = _run(["git", "apply", patch_path], root)
        if apply_result.returncode != 0:
            return {"applied": False, "reason": f"git apply failed:\n{apply_result.stderr}"}

        # Stage everything immediately, including newly-added files.
        # This matters for two reasons: (1) `git diff --name-only`
        # (unstaged) never lists untracked files, so a patch that adds a
        # new file would otherwise have that file silently skip the
        # compile-check below entirely -- a real gap, confirmed by
        # testing a patch that added a broken new .py file and watching
        # it sail through uncompiled. (2) reverting via `git checkout --
        # .` only restores tracked files; it never removes a newly
        # created untracked file, leaving it behind after a "reverted"
        # failure. Staging first, then using `git reset --hard HEAD` +
        # `git clean -fd` to revert, fixes both at once.
        _run(["git", "add", "-A"], root)
        changed = _run(["git", "diff", "--cached", "--name-only", "HEAD"], root).stdout.splitlines()

        def _revert():
            _run(["git", "reset", "--hard", "HEAD"], root)
            _run(["git", "clean", "-fd"], root)

        for rel_path in changed:
            if rel_path.endswith(".py"):
                compile_check = subprocess.run(
                    ["python3", "-m", "py_compile", rel_path], cwd=root,
                    capture_output=True, text=True,
                )
                if compile_check.returncode != 0:
                    _revert()
                    return {
                        "applied": False,
                        "reason": f"reverted -- {rel_path} failed to compile:\n{compile_check.stderr}",
                    }

        # Optional pytest gate: revert everything on any test failure,
        # same as a compile failure. Runs via SecureExecutionSandbox
        # rather than a raw subprocess call -- confined cwd, minimal PATH.
        if run_tests:
            sandbox = SecureExecutionSandbox(root, timeout_seconds=test_timeout)
            pytest_cmd = f"{shlex.quote(sys.executable)} -m pytest"
            test_run = await sandbox.run_safe_command(pytest_cmd)

            if test_run.timed_out:
                _revert()
                return {
                    "applied": False,
                    "reason": f"reverted -- test suite did not finish within {test_timeout}s",
                }

            # pytest exits 5 when it collects zero tests -- that's an
            # empty/missing suite, not a regression, so don't block on it.
            if test_run.exit_code not in (0, 5):
                _revert()
                if "No module named pytest" in test_run.stderr:
                    return {
                        "applied": False,
                        "reason": "reverted -- pytest is not installed (pip install pytest) "
                                  "but run_tests=True was requested",
                    }
                return {
                    "applied": False,
                    "reason": f"reverted -- test suite failed:\n{test_run.stdout}\n{test_run.stderr}",
                }

        commit_msg = f"self-improve ({applied_by}): {goal}"
        _run(["git", "add", "-A"], root)
        commit = _run(["git", "commit", "-m", commit_msg], root)
        if commit.returncode != 0:
            return {
                "applied": True,
                "committed": False,
                "commit_message": commit_msg,
                "files_changed": changed,
                "warning": f"patch applied but commit failed (files are on disk, uncommitted): {commit.stderr}",
            }
        mutation_log.append_mutation(root, {
            "kind": "self_improve",
            "goal": goal,
            "applied_by": applied_by,
            "files_changed": changed,
            "commit_message": commit_msg,
        })
        return {"applied": True, "committed": True, "commit_message": commit_msg, "files_changed": changed}
    finally:
        os.unlink(patch_path)
