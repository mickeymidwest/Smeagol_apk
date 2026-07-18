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
import json
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
        self._model_settings_windows: dict[str, object] = {}

    def get_status(self) -> dict:
        return get_status_data(CONFIG_PATH)

    def get_model_status(self, name: str) -> dict:
        """One entry from get_status() -- what the per-model settings
        window (opened from a hologram head-slot) renders and edits."""
        for m in self.get_status().get("models", []):
            if m["name"] == name:
                return m
        return {"name": name, "error": "not found"}

    def edit_model(self, name: str, field: str, value: str) -> dict:
        """Shells out to `gremlin model-edit` (non-interactive by design,
        see main.py) rather than touching config/models.yaml directly
        here -- one code path for the field-editing logic and its
        validation/rollback, whether triggered from this desktop window
        or remotely from the Android app's own model-edit call."""
        result = subprocess.run(
            [sys.executable, "main.py", "model-edit", name, f"--field={field}", f"--value={value}"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        )
        message = (result.stdout or result.stderr or "").strip()
        ok = result.returncode == 0 and not message.startswith("NOT edited:") and "Usage:" not in message
        return {"ok": ok, "message": message}

    def open_settings(self):
        import webview  # imported lazily -- only needed once a display exists

        if self._settings_window is None or self._settings_window not in webview.windows:
            self._settings_window = webview.create_window(
                "Gremlin Settings",
                str(ASSETS_DIR / "settings.html"),
                width=420, height=480, resizable=True, js_api=self,
            )

    def open_model_settings(self, name: str):
        import webview  # imported lazily -- only needed once a display exists

        existing = self._model_settings_windows.get(name)
        if existing is not None and existing in webview.windows:
            return

        window = webview.create_window(
            f"Gremlin -- {name}",
            str(ASSETS_DIR / "model-settings.html"),
            width=360, height=360, resizable=True, js_api=self,
        )
        self._model_settings_windows[name] = window

        # The page has no way to know which model it's for on its own
        # (it's the same static file for every model) -- evaluate_js
        # after load hands it the one piece of state it needs.
        def _init():
            window.evaluate_js(f"initModel({json.dumps(name)})")

        window.events.loaded += _init

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
