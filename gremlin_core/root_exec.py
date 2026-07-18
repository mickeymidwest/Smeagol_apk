"""
Caches a sudo password locally so root-requiring commands can run
remotely (from the phone, or the desktop's own chat panel) without
ever needing a monitor plugged back in -- and, just as important,
without that password ever crossing the network. The phone only ever
sends "run this as root," authorized by the existing admin token; this
machine is the only place that holds the real password.

Stored in a plain, restricted-permission file (data/.sudo_credential,
mode 600) rather than an OS keyring -- the same "plain file, not a
full secrets vault" choice already made for server_token.txt/
admin_token.txt. A keyring typically needs an unlocked desktop session
to actually unlock, which would defeat the point on a headless machine
running unattended after a reboot.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

from .sandbox import SecureExecutionSandbox, SandboxResult

SUDO_CRED_FILENAME = ".sudo_credential"


def _cred_path(root: str) -> Path:
    return Path(root) / "data" / SUDO_CRED_FILENAME


def has_sudo_password(root: str) -> bool:
    return _cred_path(root).exists()


def clear_sudo_password(root: str) -> None:
    path = _cred_path(root)
    if path.exists():
        path.unlink()


async def set_sudo_password(root: str, password: str) -> tuple[bool, str]:
    """Verifies the password actually authenticates with sudo BEFORE
    caching it -- a typo shouldn't get silently saved as "the"
    password, only to make every real root command fail later with no
    obvious reason why."""
    sandbox = SecureExecutionSandbox(root, timeout_seconds=10)
    result = await sandbox.run_safe_command("sudo -S -p '' true", stdin_data=(password + "\n").encode())
    if not result.ok:
        return False, f"that password didn't work: {result.stderr or 'sudo rejected it'}"

    path = _cred_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(password)
    os.chmod(path, 0o600)
    return True, "Sudo password verified and cached locally -- never sent over the network."


async def run_as_root(command_str: str, root: str, timeout: int = 60) -> SandboxResult:
    """Runs command_str with `sudo -S -p ''`, feeding the cached
    password via stdin -- never as part of the command line, so it's
    never visible in a process listing and never ends up in
    mutation_log (which only ever records the command string, not
    stdin). Returns a normal SandboxResult (never raises) with a clear
    error if nothing is cached yet -- same "caller just checks .ok"
    contract the sandbox itself already follows.

    `root` doubles as both where the cached credential lives and the
    sandbox's own workspace_dir -- none of what this runs (snapper,
    systemctl, arbitrary admin commands) actually depends on cwd, so
    there's no reason to make callers thread through a second path."""
    path = _cred_path(root)
    if not path.exists():
        return SandboxResult(
            stdout="",
            stderr="no sudo password cached -- run `gremlin set-sudo-password` on the desktop first",
            exit_code=-1, timed_out=False,
        )
    password = path.read_text()

    sandbox = SecureExecutionSandbox(root, timeout_seconds=timeout)
    return await sandbox.run_safe_command(f"sudo -S -p '' {command_str}", stdin_data=(password + "\n").encode())
