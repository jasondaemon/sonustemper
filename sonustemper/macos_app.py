from __future__ import annotations

import os
import time
import webbrowser
from pathlib import Path

os.environ.setdefault("SONUSTEMPER_DESKTOP", "1")

try:
    import objc
    from AppKit import NSApp, NSApplication, NSApplicationActivationPolicyRegular, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength
    from Foundation import NSObject
    HAS_PYOBJC = True
except Exception as exc:
    HAS_PYOBJC = False
    _PYOBJC_ERR = exc

from sonustemper.desktop_main import _ServerController, _find_port, _default_data_dir, _ensure_data_dirs  # noqa: E402


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
            url = f"http://127.0.0.1:{self.port}/"
            webbrowser.open(url)

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

        def openMain_(self, _sender=None):
            if self.port is None:
                self.start_server()
            else:
                url = f"http://127.0.0.1:{self.port}/"
                webbrowser.open(url)


def _headless_main() -> None:
    print("PyObjC not available; falling back to headless desktop mode:", _PYOBJC_ERR)
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
    while True:
        time.sleep(1)


def main():
    if not HAS_PYOBJC:
        _headless_main()
        return
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = SonusTemperApp.alloc().init()
    app.setDelegate_(delegate)
    delegate.setup_menus()
    delegate.setup_status_item()
    delegate.start_server()
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
