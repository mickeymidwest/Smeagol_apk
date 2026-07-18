"""
BTRFS snapshot listing + rollback, via `snapper` -- the real, remote-
capable equivalent of picking an entry from the GRUB menu. GRUB itself
runs before Linux (and thus before any of this software) even starts,
so nothing running on the OS can display or drive it remotely without
special hardware (IPMI/KVM-over-IP) most desktops don't have.
`snapper rollback` sidesteps that entirely: it swaps which subvolume
boots by default on the NEXT boot, no GRUB interaction needed at all.

Requires the machine's setup.sh snapshot section (snapper + grub-btrfs
+ snap-pac) to have already been run, and root_exec's cached sudo
password to actually execute either command.
"""
from __future__ import annotations
import re

from . import root_exec

_ROW_PATTERN = re.compile(r"^\s*(\d+)\s*\|\s*([^|]*?)\s*\|\s*(.*?)\s*$")


def _parse_snapper_list(output: str) -> list[dict]:
    parsed = []
    for line in output.splitlines():
        match = _ROW_PATTERN.match(line)
        if not match:
            continue
        number, date, description = match.groups()
        parsed.append({
            "number": number,
            "date": date or "(no date)",
            "description": description or "(no description)",
        })
    return parsed


async def list_snapshots(root: str):
    """Returns (True, [{"number", "date", "description"}, ...]) or
    (False, error_message)."""
    result = await root_exec.run_as_root("snapper -c root list --columns number,date,description", root)
    if not result.ok:
        return False, result.stderr or result.stdout or "snapper list failed"
    return True, _parse_snapper_list(result.stdout)


async def rollback_to(number: str, root: str) -> tuple[bool, str]:
    """Stages the rollback, then reboots -- staging without following
    through would leave the system in a state where the rollback
    silently hasn't taken effect yet, easy to forget about and
    confusing to debug later."""
    if not str(number).strip().isdigit():
        return False, f"'{number}' isn't a valid snapshot number"

    rollback_result = await root_exec.run_as_root(f"snapper rollback {number}", root)
    if not rollback_result.ok:
        return False, f"rollback failed, NOT rebooting: {rollback_result.stderr or rollback_result.stdout}"

    reboot_result = await root_exec.run_as_root("systemctl reboot", root)
    if not reboot_result.ok:
        return False, (
            f"rollback staged successfully, but reboot failed to trigger: "
            f"{reboot_result.stderr or reboot_result.stdout}. Reboot manually to apply it."
        )
    return True, f"Rolled back to snapshot {number} -- rebooting now."
