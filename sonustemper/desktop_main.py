from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn


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


def _start_server(port: int) -> None:
    uvicorn.run(
        "sonustemper.server:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )


def main() -> None:
    data_dir = os.getenv("DATA_DIR")
    if not data_dir:
        data_dir = str(_default_data_dir())
        os.environ["DATA_DIR"] = data_dir
    _ensure_data_dirs(Path(data_dir))

    port = _find_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"SonusTemper running at {url} (data: {data_dir})")

    thread = threading.Thread(target=_start_server, args=(port,), daemon=True)
    thread.start()

    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

    webbrowser.open(url)
    thread.join()


if __name__ == "__main__":
    main()
