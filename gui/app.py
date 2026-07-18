"""
`python gui/app.py` -- a small always-on-top hologram window; click it
to open a settings panel showing registered models and Gremlin's
persona config, with buttons that launch existing CLI commands in a
terminal rather than reimplementing their interactive flows.

Requires pywebview (`pip install pywebview`) plus a native web
rendering backend -- on Manjaro, `sudo pacman -S webkit2gtk-4.1` for
the GTK backend, or PyQt5 + PyQtWebEngine for the Qt backend.

The Api class is deliberately split from window creation: get_status()
and _build_launch_command() are pure logic with no GUI dependency, so
they can be (and are) unit-tested without a display. Only main() itself
needs an actual windowing system to run.
"""
from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gremlin_core import model_scan  # noqa: E402
from gremlin_core.status import get_status_data  # noqa: E402

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
CONFIG_PATH = PROJECT_ROOT / "config" / "models.yaml"

TERMINAL_CANDIDATES = ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"]


def find_terminal() -> str | None:
    for term in TERMINAL_CANDIDATES:
        if shutil.which(term):
            return term
    return None


def build_launch_command(terminal: str, subcommand: str, project_root: Path = PROJECT_ROOT) -> list[str]:
    """Pure logic, no GUI/subprocess dependency -- returns the argv list
    that would be used to open a terminal running the given gremlin
    subcommand. Split out from launch() so the exact command
    construction can be tested without actually spawning a process."""
    inner = f'cd "{project_root}" && python3 main.py {subcommand}; exec bash'
    if terminal in ("gnome-terminal", "xfce4-terminal"):
        return [terminal, "--", "bash", "-c", inner]
    if terminal == "konsole":
        return [terminal, "-e", "bash", "-c", inner]
    # xterm, x-terminal-emulator, and other bare emulators
    return [terminal, "-e", f"bash -c '{inner}'"]


class Api:
    def __init__(self):
        self._settings_window = None

    def get_status(self) -> dict:
        return get_status_data(CONFIG_PATH)

    def open_settings(self):
        import webview  # imported lazily -- only needed once a display exists

        if self._settings_window is None or self._settings_window not in webview.windows:
            self._settings_window = webview.create_window(
                "Gremlin Settings",
                str(ASSETS_DIR / "settings.html"),
                width=420, height=480, resizable=True, js_api=self,
            )

    def launch(self, subcommand: str) -> dict:
        terminal = find_terminal()
        if terminal is None:
            return {"ok": False, "error": "no terminal emulator found on this system"}
        cmd = build_launch_command(terminal, subcommand)
        try:
            subprocess.Popen(cmd)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def quit(self):
        import webview
        for w in list(webview.windows):
            w.destroy()


def main():
    import webview

    api = Api()

    # webview.screens must be read before webview.start() -- primary
    # display is always the first element. Falls back to the old fixed
    # size if, for whatever reason, no screen info is available (e.g.
    # a headless/virtual display setup).
    try:
        primary = webview.screens[0]
        width, height = primary.width // 2, primary.height // 2
    except (IndexError, AttributeError):
        width, height = 200, 200

    webview.create_window(
        "Gremlin",
        str(ASSETS_DIR / "hologram.html"),
        width=width, height=height,
        frameless=True, easy_drag=True, on_top=True,
        transparent=True, resizable=True,
        js_api=api,
    )
    webview.start()


if __name__ == "__main__":
    main()
