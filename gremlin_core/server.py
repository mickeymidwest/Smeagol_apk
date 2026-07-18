"""
`gremlin serve` -- runs an HTTP server so a phone app (or anything else
on the network) can talk to Gremlin.

Threading/asyncio note, because getting this wrong causes a real
deadlock: Gremlin's backends (LlamaCppBackend in particular) hold
asyncio.Lock instances created once at registry-build time and reused
across every request. Flask's threaded mode spawns a new OS thread per
request. Calling asyncio.run(...) fresh inside each request thread
would mean multiple threads each running their own independent event
loop while sharing the SAME lock object -- asyncio primitives are not
thread-safe, only coroutine-safe within a single loop, and this was
confirmed to deadlock in testing, not just theorized.

The fix: one persistent event loop, started once in a single dedicated
background thread, alive for the server's whole lifetime. Every Flask
request thread submits its coroutine to that one loop via
asyncio.run_coroutine_threadsafe(...) and blocks on the result -- the
actual coroutine execution (and all lock arbitration) always happens
serialized on that one loop, exactly how asyncio is meant to be used.
"""
from __future__ import annotations
import asyncio
import secrets
import socket
import subprocess
import threading
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request

from .registry import ModelRegistry
from .router import Router
from . import consult
from . import away_sync
from . import mutation_log
from .sandbox import SecureExecutionSandbox
from .status import get_status_data

TOKEN_PATH_NAME = "server_token.txt"
ADMIN_TOKEN_PATH_NAME = "admin_token.txt"
DEFAULT_PORT = 8765


def get_or_create_token(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    token_path = data_dir / TOKEN_PATH_NAME
    if token_path.exists():
        return token_path.read_text().strip()
    token = secrets.token_urlsafe(24)
    token_path.write_text(token)
    return token


def get_or_create_admin_token(data_dir: Path) -> str:
    """Deliberately separate from get_or_create_token(): the regular
    token gets embedded in a QR code and scanned by the phone -- fine
    for chat, but this second token gates system command execution and
    reboot, so it's never shown in the pairing flow at all. You copy it
    in manually, once, via `gremlin admin-token`."""
    data_dir.mkdir(parents=True, exist_ok=True)
    token_path = data_dir / ADMIN_TOKEN_PATH_NAME
    if token_path.exists():
        return token_path.read_text().strip()
    token = secrets.token_urlsafe(32)
    token_path.write_text(token)
    return token


def get_lan_ip() -> str:
    """Best-effort LAN IP for showing a pairing address. Uses the
    standard UDP-connect trick -- this doesn't actually send any
    packets or require real connectivity, it just asks the OS which
    local interface it would route through, which is enough to pick
    the right IP without needing an argument for it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def start_background_loop() -> asyncio.AbstractEventLoop:
    """The one persistent event loop -- see module docstring for why
    this exists instead of asyncio.run() per request."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True, name="gremlin-asyncio-loop")
    thread.start()
    return loop


def run_coro(loop: asyncio.AbstractEventLoop, coro, timeout: float = 120.0):
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def create_app(
    registry: ModelRegistry,
    router: Router,
    project_root: Path,
    loop: asyncio.AbstractEventLoop,
    token: str,
    admin_token: str,
) -> Flask:
    app = Flask(__name__)
    config_path = project_root / "config" / "models.yaml"

    def _check_auth() -> Optional[tuple]:
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not supplied:
            supplied = (request.get_json(silent=True) or {}).get("token", "")
        if not secrets.compare_digest(supplied, token):
            return jsonify({"error": "invalid or missing token"}), 401
        return None

    def _check_admin_auth() -> Optional[tuple]:
        """Separate from _check_auth() on purpose -- see get_or_create_admin_token."""
        supplied = request.headers.get("X-Admin-Token", "").strip()
        if not supplied:
            supplied = (request.get_json(silent=True) or {}).get("admin_token", "")
        if not secrets.compare_digest(supplied, admin_token):
            return jsonify({"error": "invalid or missing admin token"}), 401
        return None

    @app.route("/status", methods=["GET"])
    def status():
        auth_error = _check_auth()
        if auth_error:
            return auth_error
        data = get_status_data(config_path)
        # Live persona voice from the actual running registry, not just
        # the config file -- lets the phone cache the real system_prompt
        # for use when it can't reach this server at all.
        gremlin_backend = registry.get("gremlin")
        data["system_prompt"] = gremlin_backend.system_prompt
        return jsonify(data)

    @app.route("/chat", methods=["POST"])
    def chat():
        auth_error = _check_auth()
        if auth_error:
            return auth_error

        body = request.get_json(silent=True) or {}
        message = body.get("message", "").strip()
        if not message:
            return jsonify({"error": "empty message"}), 400

        # Away-mode exchanges the phone couldn't deliver until now --
        # rides along with the first successful reconnection rather than
        # needing a separate sync call.
        pending_sync = body.get("pending_sync")
        synced_count = 0
        if pending_sync:
            synced_count = away_sync.append_away_session(str(project_root), pending_sync)

        gremlin_backend = registry.get("gremlin")
        result = run_coro(
            loop,
            consult.consult_and_learn(
                router, "gremlin", gremlin_backend.consult_model_names, message, str(project_root),
                last_resort_model=gremlin_backend.last_resort_model_name,
            ),
        )
        result["synced_count"] = synced_count
        return jsonify(result)

    @app.route("/admin/execute", methods=["POST"])
    def admin_execute():
        auth_error = _check_admin_auth()
        if auth_error:
            return auth_error

        body = request.get_json(silent=True) or {}
        command = body.get("command", "").strip()
        if not command:
            return jsonify({"error": "empty command"}), 400
        workspace_dir = body.get("workspace_dir") or str(Path.home())
        timeout = min(int(body.get("timeout", 120)), 600)  # hard cap regardless of what's requested

        sandbox = SecureExecutionSandbox(workspace_dir, timeout_seconds=timeout)
        result = run_coro(loop, sandbox.run_safe_command(command), timeout=timeout + 10)

        mutation_log.append_mutation(str(project_root), {
            "kind": "admin_command",
            "command": command,
            "workspace_dir": workspace_dir,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        })

        return jsonify({
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "ok": result.ok,
        })

    @app.route("/admin/reboot", methods=["POST"])
    def admin_reboot():
        auth_error = _check_admin_auth()
        if auth_error:
            return auth_error

        mutation_log.append_mutation(str(project_root), {
            "kind": "admin_reboot_requested",
        })

        # Fixed command, not user-supplied -- no injection surface here,
        # unlike /admin/execute above. Requires passwordless sudo scoped
        # specifically to this command (see the README) -- this process
        # does not run as root itself.
        try:
            subprocess.Popen(["sudo", "systemctl", "reboot"])
        except Exception as e:
            return jsonify({"error": f"couldn't trigger reboot: {e}"}), 500

        return jsonify({"ok": True, "note": "reboot triggered, connection will drop shortly"})

    return app


def pairing_url(lan_ip: str, port: int, token: str) -> str:
    """What the phone app scans/parses to auto-configure itself --
    plain enough that Android's Uri parser handles it with no custom
    scheme needed."""
    return f"http://{lan_ip}:{port}/?token={token}"


def print_pairing_info(url: str):
    print(f"Pairing URL: {url}")
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make()
        qr.print_ascii(invert=True)
    except ImportError:
        print("(install `qrcode` for a scannable code here: pip install qrcode --break-system-packages)")


def serve(registry: ModelRegistry, router: Router, project_root: str, port: int = DEFAULT_PORT):
    root = Path(project_root).resolve()
    data_dir = root / "data"
    token = get_or_create_token(data_dir)
    admin_token = get_or_create_admin_token(data_dir)
    loop = start_background_loop()
    app = create_app(registry, router, root, loop, token, admin_token)

    lan_ip = get_lan_ip()
    url = pairing_url(lan_ip, port, token)
    print(f"Gremlin server running on http://{lan_ip}:{port}")
    print(f"(token saved at {data_dir / TOKEN_PATH_NAME} -- reused across restarts)\n")
    print("Scan this in the Gremlin Android app to pair (same Wi-Fi network required):\n")
    print_pairing_info(url)
    print()
    print("Admin token (system commands, reboot) is intentionally NOT shown here --")
    print("run `gremlin admin-token` separately to see it, and enter it manually")
    print("in the app's Admin section. Keeping it out of the QR code means")
    print("regular phone pairing never grants remote command/reboot access.")

    app.run(host="0.0.0.0", port=port, threaded=True)
