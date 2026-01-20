from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

os.environ.setdefault("SONUSTEMPER_DESKTOP", "1")

from sonustemper.desktop_paths import desktop_data_dir, desktop_logs_dir, ensure_desktop_dirs  # noqa: E402

_desktop_data = desktop_data_dir()
_desktop_logs = desktop_logs_dir()
if not os.getenv("DATA_DIR") or os.getenv("DATA_DIR") == "/data":
    os.environ["DATA_DIR"] = str(_desktop_data)
if not os.getenv("SONUSTEMPER_DATA_ROOT") or os.getenv("SONUSTEMPER_DATA_ROOT") == "/data":
    os.environ["SONUSTEMPER_DATA_ROOT"] = str(_desktop_data)
ensure_desktop_dirs()

try:
    import objc
    from AppKit import (
        NSAlert,
        NSApp,
        NSApplication,
        NSApplicationActivationPolicyRegular,
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
    from Foundation import NSObject
    HAS_PYOBJC = True
except Exception as exc:
    HAS_PYOBJC = False
    _PYOBJC_ERR = exc

from sonustemper.desktop_main import _ServerController, _find_port  # noqa: E402
from sonustemper.tools import resolve_tool  # noqa: E402


def _check_tools() -> tuple[bool, str]:
    try:
        ffmpeg = resolve_tool("ffmpeg")
        ffprobe = resolve_tool("ffprobe")
        subprocess.run([ffmpeg, "-version"], check=True, capture_output=True, text=True)
        subprocess.run([ffprobe, "-version"], check=True, capture_output=True, text=True)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _log_path() -> str:
    return str(_desktop_logs / "desktop.log")


def _log_line(message: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {message}\n"
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def wait_for_http_ok(url: str, timeout_s: float = 45.0, interval_s: float = 0.25) -> None:
    deadline = time.time() + timeout_s
    last_err: str | None = None
    while time.time() < deadline:
        try:
            req = Request(url, headers={"Cache-Control": "no-cache"})
            with urlopen(req, timeout=1.5) as resp:
                status = getattr(resp, "status", 0)
                if 200 <= status < 300:
                    return
                last_err = f"HTTP {status}"
        except (URLError, HTTPError, socket.timeout, ConnectionError, OSError) as exc:
            last_err = str(exc)
        time.sleep(interval_s)
    raise TimeoutError(f"Timed out waiting for {url}. Last error: {last_err}")


if HAS_PYOBJC:
    class SonusTemperApp(NSObject):
        def init(self):
            self = objc.super(SonusTemperApp, self).init()
            if self is None:
                return None
            self.controller = _ServerController()
            self.port = None
            self.status_item = None
            return self

        def start_server(self):
            if self.port is None:
                self.port = _find_port()
                self.controller.start(self.port)
            self._set_status_title("Starting SonusTemper…")

        def quit_(self, _sender=None):
            self.controller.stop()
            self.controller.join(timeout_s=5.0)
            NSApp.terminate_(None)

        def setup_menus(self):
            main_menu = NSMenu.alloc().init()
            app_menu_item = NSMenuItem.alloc().init()
            main_menu.addItem_(app_menu_item)
            app_menu = NSMenu.alloc().init()

            open_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Open SonusTemper", "openMain:", ""
            )
            app_menu.addItem_(open_item)
            app_menu.addItem_(NSMenuItem.separatorItem())
            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit SonusTemper", "quit:", "q"
            )
            app_menu.addItem_(quit_item)
            app_menu_item.setSubmenu_(app_menu)
            NSApp.setMainMenu_(main_menu)

        def setup_status_item(self):
            status_bar = NSStatusBar.systemStatusBar()
            self.status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
            if self.status_item:
                self.status_item.setTitle_("SonusTemper")
                menu = NSMenu.alloc().init()
                menu.addItemWithTitle_action_keyEquivalent_("Open SonusTemper", "openMain:", "")
                menu.addItem_(NSMenuItem.separatorItem())
                menu.addItemWithTitle_action_keyEquivalent_("Quit SonusTemper", "quit:", "q")
                self.status_item.setMenu_(menu)

        def _set_status_title(self, title: str):
            if self.status_item:
                self.status_item.setTitle_(title)

        def _wait_and_open(self):
            if self.port is None:
                return
            timeout_s = float(os.getenv("SONUSTEMPER_STARTUP_TIMEOUT", "45"))
            url = f"http://127.0.0.1:{self.port}/"
            health_url = f"http://127.0.0.1:{self.port}/health"
            self._set_status_title("Starting SonusTemper…")
            _log_line(f"waiting for health: {health_url}")
            try:
                wait_for_http_ok(health_url, timeout_s=timeout_s, interval_s=0.25)
                _log_line("health ok; opening browser")
                self._set_status_title("SonusTemper")
                webbrowser.open(url)
            except Exception as exc:
                msg = f"Server failed to become ready within {timeout_s:.0f}s.\n\n{exc}\n\nLogs: {_log_path()}"
                _log_line(f"startup timeout: {exc}")
                alert = NSAlert.alloc().init()
                alert.setMessageText_("SonusTemper failed to start")
                alert.setInformativeText_(msg)
                alert.addButtonWithTitle_("Open Logs")
                alert.addButtonWithTitle_("Quit")
                response = alert.runModal()
                if response == 1000:  # NSAlertFirstButtonReturn
                    subprocess.run(["open", str(_desktop_logs)], check=False)
                else:
                    self.quit_(None)

        def openMain_(self, _sender=None):
            if self.port is None:
                self.start_server()
            else:
                url = f"http://127.0.0.1:{self.port}/"
                webbrowser.open(url)


def _headless_main() -> None:
    print("PyObjC not available; falling back to headless desktop mode:", _PYOBJC_ERR)
    data_dir = os.getenv("DATA_DIR") or str(_desktop_data)

    port = _find_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"SonusTemper running at {url} (data: {data_dir})")

    ok, err = _check_tools()
    if not ok:
        print(f"ERROR: ffmpeg/ffprobe not available: {err}")
        sys.exit(1)

    controller = _ServerController()
    controller.start(port)
    try:
        wait_for_http_ok(f"http://127.0.0.1:{port}/health", timeout_s=45.0, interval_s=0.25)
        webbrowser.open(url)
    except Exception as exc:
        print(f"ERROR: Server did not become ready: {exc}")
        print(f"Logs: {_log_path()}")
        while True:
            time.sleep(1)
    while True:
        time.sleep(1)


def main():
    if not HAS_PYOBJC:
        _headless_main()
        return
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    ok, err = _check_tools()
    if not ok:
        alert = NSAlert.alloc().init()
        alert.setMessageText_("SonusTemper failed to start")
        alert.setInformativeText_(f"ffmpeg/ffprobe not available: {err}")
        alert.runModal()
        sys.exit(1)
    delegate = SonusTemperApp.alloc().init()
    app.setDelegate_(delegate)
    delegate.setup_menus()
    delegate.setup_status_item()
    delegate.start_server()
    threading.Thread(target=delegate._wait_and_open, daemon=True).start()
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
