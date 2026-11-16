"""Command line entry point: start the server and show the window."""

from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from . import __version__, macos_app, mods, repo as repo_mod, system
from .config import Config
from .gitcmd import GitError, find_tool
from .server import DEFAULT_PORT, App, Server


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gitsense",
        description="GitSense: a Git and GitHub client for your local repositories.",
    )
    parser.add_argument("paths", nargs="*", help="repositories to add and open")
    parser.add_argument("--browser", action="store_true", help="open in the web browser instead of a window")
    parser.add_argument("--no-open", action="store_true", help="start the server without opening anything")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to listen on (default {DEFAULT_PORT})")
    parser.add_argument("--debug", action="store_true", help="log requests to stderr")
    parser.add_argument(
        "--install-app",
        nargs="?",
        const="",
        metavar="DIR",
        help="macOS: add GitSense.app to /Applications (or ~/Applications, or DIR)",
    )
    parser.add_argument("--uninstall-app", nargs="?", const="", metavar="DIR", help="macOS: remove GitSense.app")
    parser.add_argument("--install-mod", metavar="PATH", help="install a mod from a folder or .zip archive")
    parser.add_argument("--uninstall-mod", metavar="ID", help="remove an installed mod")
    parser.add_argument("--list-mods", action="store_true", help="list the installed mods")
    parser.add_argument("--version", action="version", version=f"gitsense {__version__}")
    return parser.parse_args(argv)


def _set_macos_app_name(name: str) -> None:
    """Show the app's name in the menu bar instead of "Python"."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle  # type: ignore[import-not-found]

        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


DEFAULT_WINDOW = (1480, 940)
MIN_WINDOW = (1000, 640)
# The default theme's background, shown before any theme was remembered.
DEFAULT_BACKGROUND = "#1E1F22"
# Show the window this many seconds after start even if the page has not loaded.
SHOW_TIMEOUT = 3.0
_FULL_SCREEN_MASK = 1 << 14  # NSWindowStyleMaskFullScreen


def fit_window_size(
    size: tuple[int, int] | None,
    screen: tuple[float, float] | None,
    minimum: tuple[int, int] = MIN_WINDOW,
) -> tuple[int, int]:
    """Pick the window size for this launch.

    - size is the saved size, or None for the default.
    - The result is never smaller than minimum, unless the screen is.
    - The result always fits inside screen, the visible area of the display.
    """
    width, height = size or DEFAULT_WINDOW
    width, height = max(width, minimum[0]), max(height, minimum[1])
    if screen is not None:
        width, height = min(width, int(screen[0])), min(height, int(screen[1]))
    return width, height


def startup_background(last_theme: dict[str, Any] | None) -> str:
    """The color the window shows before the page paints: the last theme's background."""
    if last_theme and isinstance(last_theme.get("colors"), dict) and last_theme["colors"].get("bg"):
        return str(last_theme["colors"]["bg"])
    return DEFAULT_BACKGROUND


def _visible_screen() -> tuple[float, float] | None:
    """Width and height of the main display, without the menu bar and Dock."""
    if sys.platform != "darwin":
        return None
    try:
        import AppKit  # type: ignore[import-not-found]

        frame = AppKit.NSScreen.mainScreen().visibleFrame()
        return frame.size.width, frame.size.height
    except Exception:  # noqa: BLE001 - fall back to the unclamped size
        return None


def app_icon(config: Config) -> Path | None:
    """The icon an enabled mod supplies for the Dock and GitSense.app, or None for the standard one."""
    states = config.settings()["mods"]
    directory = mods.mods_dir(config.dir)
    return mods.active_icon(directory, states, "icns") or mods.active_icon(directory, states, "png")


def _set_macos_dock_icon(mod_icon: Path | None) -> None:
    """Show the GitSense icon in the Dock.

    - Started from a terminal, the Dock would show the Python icon instead.
    - Inside GitSense.app the bundle already has the standard icon, so only a mod's icon is set.
    - Called before pywebview makes the process a regular app, so the Python icon never shows.
    """
    icon = mod_icon or macos_app.ICON
    if sys.platform != "darwin" or not icon.is_file():
        return
    try:
        import AppKit  # type: ignore[import-not-found]

        if mod_icon is None and AppKit.NSBundle.mainBundle().bundleIdentifier() in macos_app.OUR_BUNDLE_IDS:
            return
        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon))
        if image is not None:
            AppKit.NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def _prepare_native_window(window: Any) -> None:
    """Get the hidden window ready so its first visible frame is final.

    - Runs on the main thread just before pywebview would show the window.
    - The web view goes into the window now, so the page lays out once, at its final size.
    - The web view draws no white background; the window's color shows until the page paints.
    - The window is centred in the visible area, so macOS does not move it on first show.
    """
    try:
        import AppKit  # type: ignore[import-not-found]
        from webview.platforms.cocoa import BrowserView  # type: ignore[import-not-found]

        browser = BrowserView.instances[window.uid]
        native, web = browser.window, browser.webview
        web.setValue_forKey_(False, "drawsBackground")
        area = AppKit.NSScreen.mainScreen().visibleFrame()
        frame = native.frame()
        frame.origin.x = area.origin.x + (area.size.width - frame.size.width) / 2
        frame.origin.y = area.origin.y + (area.size.height - frame.size.height) / 2
        native.setFrame_display_(frame, False)
        native.setContentView_(web)
        native.makeFirstResponder_(web)
    except Exception as exc:  # noqa: BLE001 - pywebview still shows the page, just less smoothly
        print(f"gitsense: could not prepare the window ({exc})", file=sys.stderr)


def _match_window_chrome(window: Any, theme: dict[str, Any] | None) -> None:
    """Make the title bar light or dark to match the theme.

    - A theme the user picked sets the window's appearance to its kind.
    - A theme that follows the system clears the override, so the page keeps
      seeing the real OS light/dark setting.
    - Must run on the main thread.
    """
    try:
        import AppKit  # type: ignore[import-not-found]
        from webview.platforms.cocoa import BrowserView  # type: ignore[import-not-found]

        native = BrowserView.instances[window.uid].window
        if not theme or theme.get("follow_system"):
            native.setAppearance_(None)
            return
        name = AppKit.NSAppearanceNameDarkAqua if theme.get("kind") == "dark" else AppKit.NSAppearanceNameAqua
        native.setAppearance_(AppKit.NSAppearance.appearanceNamed_(name))
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def _on_main_thread(func: Any, *args: Any) -> None:
    try:
        from PyObjCTools import AppHelper  # type: ignore[import-not-found]

        AppHelper.callAfter(func, *args)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


class _SizeKeeper:
    """Remember the window size and save it when the window closes.

    - Sizes seen while in full screen are ignored.
    """

    def __init__(self, config: Config, size: tuple[int, int]):
        self.config = config
        self.size = size

    def resized(self, window: Any, width: float, height: float) -> None:
        native = window.native
        if native is not None and native.styleMask() & _FULL_SCREEN_MASK:
            return
        self.size = (int(width), int(height))

    def save(self) -> None:
        try:
            self.config.set_window_size(*self.size)
        except OSError:
            pass


def _open_window(server: Server, config: Config, debug: bool) -> bool:
    """Show the native window. Returns False when pywebview is not usable.

    - The window starts hidden and appears when the page has loaded, or after SHOW_TIMEOUT.
    - It opens at the size it had when last closed, fitted to the screen.
    """
    try:
        import webview  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - any import failure falls back to the browser
        return False

    _set_macos_app_name("GitSense")
    _set_macos_dock_icon(app_icon(config))
    screen = _visible_screen()
    width, height = fit_window_size(config.window_size(), screen)
    minimum = fit_window_size(MIN_WINDOW, screen)
    window = webview.create_window(
        "GitSense",
        server.launch_url,
        width=width,
        height=height,
        min_size=minimum,
        text_select=True,
        hidden=sys.platform == "darwin",
        background_color=startup_background(config.last_theme()),
    )
    sizes = _SizeKeeper(config, (width, height))
    ready = threading.Event()
    reveal_lock = threading.Lock()

    def reveal() -> None:
        """Show the window once: on the first page load or when the timer runs out."""
        with reveal_lock:
            if ready.is_set():
                return
            ready.set()
        window.show()

    def pick(start: str) -> str | None:
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
        if kind is None:
            kind = getattr(webview, "FOLDER_DIALOG", 20)
        result = window.create_file_dialog(kind, directory=start)
        if not result:
            return None
        return str(result[0] if isinstance(result, (list, tuple)) else result)

    system.native_folder_picker = pick

    def closes_tab_instead() -> bool:
        """Cmd+W closes the open repository tab; only then the window.

        - The red close button and Cmd+Q always close the window.
        """
        try:
            import AppKit  # type: ignore[import-not-found]

            event = AppKit.NSApp.currentEvent()
            key_down = event is not None and event.type() == AppKit.NSEventTypeKeyDown
            if not key_down or (event.charactersIgnoringModifiers() or "").lower() != "w":
                return False
        except Exception:  # noqa: BLE001 - fall back to closing the window
            return False
        if not server.app.config.current_tab():
            return False
        threading.Thread(
            target=lambda: window.run_js("window.gitsense && window.gitsense.closeActiveTab()"),
            daemon=True,
        ).start()
        return True

    def on_closing() -> bool | None:
        """Returning False cancels the close; otherwise save the window size."""
        if closes_tab_instead():
            return False
        sizes.save()
        return None

    def before_show() -> None:
        _prepare_native_window(window)
        _match_window_chrome(window, config.last_theme())
        timer = threading.Timer(SHOW_TIMEOUT, reveal)
        timer.daemon = True
        timer.start()

    if sys.platform == "darwin":
        server.app.on_theme_painted = lambda theme: _on_main_thread(_match_window_chrome, window, theme)
        window.events.before_show += before_show
        window.events.loaded += reveal
        window.events.resized += sizes.resized
        window.events.closing += on_closing
    try:
        webview.start(debug=debug)
    except Exception as exc:  # noqa: BLE001
        print(f"gitsense: the window could not be opened ({exc}); using the browser", file=sys.stderr)
        system.native_folder_picker = None
        return False
    return True

def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.install_app is not None:
        return macos_app.install(args.install_app or None, icon=app_icon(Config()))
    if args.uninstall_app is not None:
        return macos_app.uninstall(args.uninstall_app or None)
    if args.install_mod is not None:
        return mods.cli_install(args.install_mod)
    if args.uninstall_mod is not None:
        return mods.cli_uninstall(args.uninstall_mod)
    if args.list_mods:
        return mods.cli_list(Config().settings()["mods"])
    if find_tool("git") is None:
        print("gitsense: git is not installed or not on PATH", file=sys.stderr)
        return 1

    config = Config()
    opened: list[str] = []
    for path in args.paths:
        try:
            entry = config.add_repo(repo_mod.repo_root(path))
            opened.append(entry["id"])
        except GitError as exc:
            print(f"gitsense: {path}: {exc.message}", file=sys.stderr)
    if opened:
        snap = config.snapshot()
        config.set_tabs([*snap["open_tabs"], *opened], opened[-1])

    use_window = not (args.browser or args.no_open)
    app = App(config, native=use_window)
    server = Server(app, args.port, debug=args.debug)
    server.start()
    print(f"GitSense {__version__} at {server.launch_url}")

    try:
        if use_window and _open_window(server, config, args.debug):
            return 0
        app.native = False
        if not args.no_open:
            webbrowser.open(server.launch_url)
        print("Press Ctrl+C to quit.")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0
    finally:
        server.stop()
