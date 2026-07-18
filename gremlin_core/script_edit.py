"""
`gremlin edit <path> "<problem description>"` -- fixes a problem in any
script on the machine Gremlin is installed on. This is deliberately
separate from self_improve.py and more cautious about it:

- self_improve.py only ever touches gremlin_core/*.py, has full git
  history, and requires two-model review before anything lands.
- This touches files anywhere you point it, which is a bigger blast
  radius, so instead it leans on: a plain-file backup made before
  anything is touched, a syntax/compile check for Python files with
  automatic revert on failure, a diff shown before writing anything,
  and a hard refusal on paths under system directories -- "a script on
  my computer" means your own stuff, not /etc or /usr.
"""
from __future__ import annotations
import difflib
import py_compile
import shutil
import time
from pathlib import Path
from typing import Optional

from .router import Router
from .sandbox import SecureExecutionSandbox
from . import mutation_log

EDIT_SYSTEM_PROMPT = (
    "You are fixing a problem in a script on the user's own computer. "
    "You'll be given the file's current content and a description of "
    "the problem. Respond with ONLY the complete corrected file content "
    "-- no explanation, no markdown fences, no commentary. Keep changes "
    "minimal and focused on the described problem; don't rewrite parts "
    "that aren't related to it."
)

MERGE_EDIT_SYSTEM_PROMPT = (
    "You are merging several proposed fixes for the same file and "
    "problem, each written by a different AI model. Pick the best "
    "single approach, or combine the strongest parts, and output ONLY "
    "the final complete corrected file content -- no explanation, no "
    "markdown fences, no commentary."
)

# Refusing to touch these outright: "a script on my computer" means your
# own stuff, not the operating system. Linux-focused since that's the
# target platform, but covers the obvious equivalents too.
SENSITIVE_PREFIXES = [
    "/etc", "/boot", "/bin", "/sbin", "/usr", "/lib", "/lib64",
    "/sys", "/proc", "/var",
    "/System", "/Windows", "/Program Files",
]


def check_path_safety(path: str) -> Optional[str]:
    """Returns a refusal reason if this path shouldn't be touched, or
    None if it's fine."""
    resolved = str(Path(path).expanduser().resolve())
    for prefix in SENSITIVE_PREFIXES:
        if resolved == prefix or resolved.startswith(prefix.rstrip("/") + "/"):
            return f"refusing to edit anything under {prefix} -- that's a system path, not a personal script"
    return None


def backup_path(path: Path) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.name}.gremlin-backup-{timestamp}")


def diff_preview(old_content: str, new_content: str, filename: str) -> str:
    diff = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"{filename} (original)",
        tofile=f"{filename} (proposed)",
    )
    return "".join(diff)


async def propose_fix(
    router: Router,
    model_names: list[str],
    file_path: str,
    problem: str,
    synthesizer: Optional[str] = None,
) -> str:
    original = Path(file_path).read_text()
    prompt = f"File: {file_path}\n\nProblem: {problem}\n\nCurrent content:\n{original}"

    if len(model_names) == 1:
        result = await router.route(model_names[0], prompt, system=EDIT_SYSTEM_PROMPT)
        return result.text

    results = await router.broadcast(model_names, prompt, system=EDIT_SYSTEM_PROMPT)
    proposals_text = "\n\n".join(
        f"=== Proposal from {name} ===\n{r.text if r.ok else f'[failed: {r.error}]'}"
        for name, r in results.items()
    )
    merge_prompt = f"Problem: {problem}\n\nOriginal content:\n{original}\n\n{proposals_text}\n\nMerge into one final corrected file."
    synth = synthesizer or model_names[0]
    merged = await router.route(synth, merge_prompt, system=MERGE_EDIT_SYSTEM_PROMPT)
    return merged.text


async def apply_fix(
    file_path: str,
    new_content: str,
    verify_command: Optional[str] = None,
    project_root: Optional[str] = None,
    problem: str = "",
) -> dict:
    """Backs up the original, writes the fix, and verifies it:
    - .py files always get a compile check (unchanged behavior).
    - If verify_command is given, it runs via SecureExecutionSandbox
      (confined to the file's own directory) regardless of file type --
      this is what lets a shell script or anything else get checked too,
      not just Python. Either check failing reverts to the original.
    If project_root is given, a successful fix is also recorded in
    data/mutation_log.jsonl there -- optional, since the target file can
    be anywhere on disk, not necessarily inside this project.
    Never raises for an expected failure -- comes back as
    {"applied": False, "reason": ...}."""
    path = Path(file_path)
    original_content = path.read_text()
    backup = backup_path(path)
    shutil.copy2(path, backup)

    path.write_text(new_content)

    if path.suffix == ".py":
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            path.write_text(original_content)  # revert
            backup.unlink(missing_ok=True)  # backup is redundant, original is back in place
            return {
                "applied": False,
                "reason": f"reverted -- fixed file failed to compile:\n{e}",
                "backup_path": None,
            }

    if verify_command:
        sandbox = SecureExecutionSandbox(str(path.parent), timeout_seconds=60)
        result = await sandbox.run_safe_command(verify_command)
        if not result.ok:
            path.write_text(original_content)  # revert
            backup.unlink(missing_ok=True)
            reason = "timed out" if result.timed_out else f"exit code {result.exit_code}"
            return {
                "applied": False,
                "reason": f"reverted -- verify command failed ({reason}):\n{result.stdout}\n{result.stderr}",
                "backup_path": None,
            }

    if project_root:
        mutation_log.append_mutation(project_root, {
            "kind": "script_edit",
            "target_file": str(path.resolve()),
            "problem": problem,
            "backup_path": str(backup),
        })

    return {"applied": True, "backup_path": str(backup)}
