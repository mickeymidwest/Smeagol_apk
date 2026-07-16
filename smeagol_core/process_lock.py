"""
Guards against two separate smeagol processes mutating the same git
repo at once -- e.g. the desktop GUI's "Auto-fix" button spawns a new
terminal running `smeagol auto-fix` in its own process, and nothing
stops you from also running `smeagol improve` or `smeagol edit`
yourself in a different terminal at the same time. An in-process
asyncio.Lock wouldn't help here at all: it only serializes concurrent
coroutines inside ONE process's event loop, and these are two
completely separate OS processes. This needs a real file lock.

Uses fcntl.flock (Linux/Manjaro-only, consistent with the rest of this
project's target platform) in non-blocking mode: a second process
trying to acquire an already-held lock fails immediately with a clear
message, rather than either hanging indefinitely or silently racing.
"""
from __future__ import annotations
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(Exception):
    pass


@contextmanager
def git_mutation_lock(root: str):
    """Raises AlreadyRunning immediately if another smeagol process
    already holds the lock for this project -- never blocks waiting."""
    lock_dir = Path(root) / "data"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".smeagol.lock"

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning(
                "another smeagol process is already changing files in this project "
                "(self-improve, edit, or auto-fix) -- wait for it to finish before starting another"
            )
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
