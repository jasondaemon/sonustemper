from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

import uvicorn


def _resource_path(*parts: str) -> Path:
    """
    Return a resource path that works both in dev and when frozen by PyInstaller.
    - Frozen: resources live under sys._MEIPASS
    - Dev: repo root is one level above this file's directory (sonustemper/)
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).joinpath(*parts)
    return Path(__file__).resolve().parents[1].joinpath(*parts)


def _default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SonusTemper" / "data"
    if sys.platform.startswith("win"):
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "SonusTemper" / "data"
    return Path.home() / ".sonustemper" / "data"


def _ensure_data_dirs(data_dir: Path) -> None:
    for rel in [
        "mastering/in",
        "mastering/out",
        "tagging/in",
        "presets",
        "previews",
    ]:
        (data_dir / rel).mkdir(parents=True, exist_ok=True)


def _find_port(start: int = 8383, end: int = 8433) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no_available_port")


class _ServerController:
    """Runs uvicorn.Server in a background thread, and can stop it cleanly."""
    def __init__(self) -> None:
        self.server: Optional[uvicorn.Server] = None
        self.thread: Optional[threading.Thread] = None
        self.port: Optional[int] = None

    def start(self, port: int) -> None:
        from sonustemper.server import app  # direct import (PyInstaller-safe)

        self.port = port
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",
            access_log=False,
        )
        self.server = uvicorn.Server(config)

        def _run() -> None:
            # Blocks until should_exit becomes True
            assert self.server is not None
            self.server.run()

        self.thread = threading.Thread(target=_run, name="uvicorn-server", daemon=False)
        self.thread.start()

    def wait_until_ready(self, timeout_s: float = 5.0) -> bool:
        if self.port is None:
            return False
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return True
            except OSError:
                time.sleep(0.1)
        return False

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True

    def join(self, timeout_s: float = 5.0) -> None:
        if self.thread is not None:
            self.thread.join(timeout=timeout_s)


def _run_macos_menu_bar(url: str, controller: _ServerController) -> None:
    import rumps

    def open_ui(_: object = None) -> None:
        webbrowser.open(url)

    def quit_app(_: object = None) -> None:
        controller.stop()
        controller.join(timeout_s=5.0)
        rumps.quit_application()

    icon_path = _resource_path("images", "sonustemper-menubar.png")

    # Create as a TEMPLATE status icon so macOS tints it for light/dark mode
    if icon_path.exists():
        app = rumps.App(
            "SonusTemper",
            quit_button=None,
            icon=str(icon_path),
            template=True,   # <-- key
        )
        app.title = ""      # no text if icon is present
    else:
        app = rumps.App("SonusTemper", quit_button=None)
        app.title = "SonusTemper"  # fallback so it never "disappears"

    app.menu.clear()
    app.menu.add(rumps.MenuItem("Open SonusTemper", callback=open_ui))
    app.menu.add(rumps.separator)
    app.menu.add(rumps.MenuItem("Quit SonusTemper", callback=quit_app))

    app.run()


def main() -> None:
    data_dir = os.getenv("DATA_DIR")
    if not data_dir:
        data_dir = str(_default_data_dir())
        os.environ["DATA_DIR"] = data_dir
    _ensure_data_dirs(Path(data_dir))

    port = _find_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"SonusTemper running at {url} (data: {data_dir})")

    controller = _ServerController()
    controller.start(port)

    if controller.wait_until_ready(timeout_s=6.0):
        webbrowser.open(url)
    else:
        print("WARNING: Server did not become ready in time; still opening browser.")
        webbrowser.open(url)

    if sys.platform == "darwin":
        _run_macos_menu_bar(url, controller)
        return

    # Non-mac fallback: keep process alive (Ctrl+C quits in dev)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()