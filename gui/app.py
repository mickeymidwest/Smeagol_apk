"""
`python gui/app.py` -- the main desktop window: `main.html` (hologram
up top, a live Claude-Code-style conversation panel below it), plus a
settings panel (opened from below the hologram) and a per-model
settings panel (opened from a hologram head-slot), the latter two with
buttons that launch existing CLI commands in a terminal rather than
reimplementing their interactive flows.

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
import time
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gremlin_core import consult, model_scan  # noqa: E402
from gremlin_core import server as server_mod  # noqa: E402
from gremlin_core.status import get_status_data  # noqa: E402

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
CONFIG_PATH = PROJECT_ROOT / "config" / "models.yaml"
DATA_DIR = PROJECT_ROOT / "data"

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

    def is_talking(self) -> bool:
        """Polled by hologram.html (~every 400ms) to animate the mouth
        while Gremlin is actually generating an answer -- see
        gremlin_core.consult.is_talking for what sets/clears this. Works
        whether the answer came from this window's own "Chat with
        Gremlin" terminal or from `gremlin serve` handling a request
        from the phone, since both funnel through the same
        consult_and_learn call."""
        return consult.is_talking(str(PROJECT_ROOT))

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

    def _ensure_server(self) -> tuple[str, str]:
        """Returns (base_url, token) for a `gremlin serve` instance on
        this machine, starting one in the background if nothing is
        already answering on it. This is what lets main.html's chat
        panel work without the user having to run `gremlin serve`
        themselves first -- the same server the Android app already
        talks to over LAN, just addressed over localhost here instead."""
        token = server_mod.get_or_create_token(DATA_DIR)
        base_url = f"http://127.0.0.1:{server_mod.DEFAULT_PORT}"
        try:
            r = requests.get(f"{base_url}/status", headers={"Authorization": f"Bearer {token}"}, timeout=1.5)
            if r.status_code == 200:
                return base_url, token
        except requests.RequestException:
            pass

        subprocess.Popen(
            [sys.executable, "main.py", "serve"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return base_url, token

    def _wait_for_server(self, base_url: str, token: str, timeout_s: float = 15.0) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                r = requests.get(f"{base_url}/status", headers={"Authorization": f"Bearer {token}"}, timeout=2)
                if r.status_code == 200:
                    return True
            except requests.RequestException:
                pass
            time.sleep(0.5)
        return False

    def chat(self, message: str) -> dict:
        """Backs main.html's conversation panel -- a thin HTTP client to
        /chat on a local `gremlin serve` (auto-started via
        _ensure_server if not already running), not a reimplementation
        of the answer-generation logic. Same JSON shape consult_and_learn
        returns (answer/consulted/from_memory/contributors/...), plus
        "error": True on anything that isn't a clean 200 -- main.html
        renders that distinctly rather than treating it as a real answer."""
        base_url, token = self._ensure_server()
        if not self._wait_for_server(base_url, token):
            return {
                "answer": "Couldn't start gremlin serve -- check the terminal gui/app.py "
                          "was launched from for errors.",
                "error": True,
            }

        try:
            # First message after a cold start may include loading the
            # primary model -- generous timeout, this is a real
            # generation call, not a status check.
            r = requests.post(
                f"{base_url}/chat",
                json={"message": message, "token": token},
                headers={"Authorization": f"Bearer {token}"},
                timeout=180,
            )
        except requests.RequestException as e:
            return {"answer": f"Couldn't reach gremlin serve: {e}", "error": True}

        if r.status_code != 200:
            detail = (r.json().get("error") if r.headers.get("content-type", "").startswith("application/json") else None) or f"HTTP {r.status_code}"
            return {"answer": f"[error: {detail}]", "error": True}

        return r.json()

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

    # Not transparent, unlike the old hologram-only window -- the chat
    # panel below the hologram needs a real, readable background now.
    # The hologram itself (in its iframe) still renders its own dark/
    # scanline look, so nothing changes visually up there.
    webview.create_window(
        "Gremlin",
        str(ASSETS_DIR / "main.html"),
        width=width, height=height,
        frameless=True, easy_drag=True, on_top=True,
        resizable=True,
        js_api=api,
    )
    webview.start()


if __name__ == "__main__":
    main()
