"""
A restricted way to run a shell command: confined to a specific
workspace directory (via cwd), a minimal PATH (so it can't casually
reach for arbitrary system tools), and a hard timeout that kills the
process rather than letting it hang forever.

Worth being precise about what this does and doesn't do: it's a
process-level restriction (cwd + PATH + timeout), not a kernel-level
sandbox. A determined or malicious command can still read/write files
outside `workspace_dir` using absolute paths, since nothing here uses
Linux namespaces, seccomp, or a chroot to actually block that at the
OS level. For real filesystem-level confinement on Manjaro/Arch, wrap
the same command with bubblewrap, e.g.:

    bwrap --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 \\
          --bind {workspace_dir} {workspace_dir} --dev /dev --proc /proc \\
          --unshare-all --die-with-parent -- <command>

That's a deliberate choice to keep this dependency-free (bubblewrap
may not be installed everywhere) rather than silently claiming a
stronger guarantee than what's actually enforced without it.
"""
from __future__ import annotations
import asyncio
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0


class SecureExecutionSandbox:
    def __init__(self, workspace_dir: str, timeout_seconds: int = 30):
        self.workspace = os.path.abspath(workspace_dir)
        self.timeout = timeout_seconds

    async def run_safe_command(self, command_str: str, stdin_data: Optional[bytes] = None) -> SandboxResult:
        """Executes a command with its cwd confined to the workspace and
        a minimal PATH. Never raises -- any failure (bad command, missing
        binary, timeout) comes back as a SandboxResult with exit_code=-1
        rather than an exception, so a caller can always just check
        `.ok` instead of wrapping every call in try/except.

        `stdin_data`, when given, is piped to the subprocess -- e.g. a
        sudo password fed via `sudo -S`, so it never appears on the
        command line (and therefore never in a process listing or in
        mutation_log, which only ever records the command string)."""
        try:
            parsed_cmd = shlex.split(command_str)
        except ValueError as e:
            return SandboxResult(stdout="", stderr=f"couldn't parse command: {e}", exit_code=-1, timed_out=False)

        if not parsed_cmd:
            return SandboxResult(stdout="", stderr="empty command", exit_code=-1, timed_out=False)

        try:
            proc = await asyncio.create_subprocess_exec(
                *parsed_cmd,
                cwd=self.workspace,
                stdin=subprocess.PIPE if stdin_data is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "PATH": "/usr/bin:/bin"},
            )
        except Exception as e:
            # e.g. FileNotFoundError if the binary doesn't exist
            return SandboxResult(stdout="", stderr=str(e), exit_code=-1, timed_out=False)

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(input=stdin_data), timeout=self.timeout)
            return SandboxResult(
                stdout=stdout.decode(errors="replace").strip(),
                stderr=stderr.decode(errors="replace").strip(),
                exit_code=proc.returncode or 0,
                timed_out=False,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()  # reap the process, avoid a zombie
            except ProcessLookupError:
                pass
            return SandboxResult(
                stdout="", stderr=f"command timed out after {self.timeout}s",
                exit_code=-1, timed_out=True,
            )
