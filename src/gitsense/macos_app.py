"""Build and remove GitSense.app, so GitSense can be opened from Finder.

- The bundle runs GitSense with the Python that installed it, by absolute path.
- Contents/MacOS/gitsense-launcher is a shell script: the bundle's executable.
- Contents/MacOS/GitSense is a symlink to that Python's GUI binary.
- The launcher execs the symlink, so the running process lives inside GitSense.app.
- macOS then treats it as GitSense: one Dock icon, the GitSense name and icon.
- Output goes to ~/Library/Logs/GitSense/gitsense.log.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any

from . import __version__

BUNDLE_ID = "app.gitsense.GitSense"
OUR_BUNDLE_IDS = frozenset({BUNDLE_ID})
BUNDLE_NAME = "GitSense.app"
LAUNCHER = "gitsense-launcher"
PROCESS = "GitSense"
ICON_NAME = "GitSense.icns"
ICON = Path(__file__).resolve().parent / "assets" / ICON_NAME
LOG_DIR = "Library/Logs/GitSense"
LOG_LIMIT = 1024 * 1024
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)


class AppBundleError(Exception):
    """The bundle could not be installed or removed. The message is for the user."""


# -- where things are ----------------------------------------------------------


def default_dir() -> Path:
    """Where the app goes when no folder is given.

    - /Applications when this user can write there.
    - Otherwise ~/Applications, created when missing.
    """
    system = Path("/Applications")
    if system.is_dir() and os.access(system, os.W_OK):
        return system
    personal = Path.home() / "Applications"
    personal.mkdir(exist_ok=True)
    return personal


def gui_interpreter() -> Path:
    """The Python binary that can run a macOS app.

    - A framework build (Homebrew, python.org) only runs GUI code from its Python.app.
    - bin/python of a framework build re-execs into that Python.app, so link to it directly.
    - Any other build runs GUI code from its own binary.
    """
    if sysconfig.get_config_var("PYTHONFRAMEWORK"):
        app_binary = Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python"
        if app_binary.is_file():
            return app_binary
    return Path(getattr(sys, "_base_executable", "") or sys.executable)


def log_path() -> Path:
    return Path.home() / LOG_DIR / "gitsense.log"


# -- bundle contents -------------------------------------------------------------


def info_plist() -> dict[str, Any]:
    return {
        "CFBundleName": "GitSense",
        "CFBundleDisplayName": "GitSense",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": LAUNCHER,
        "CFBundleIconFile": ICON_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.developer-tools",
        "LSMinimumSystemVersion": "11.0",
    }


def launcher_script(python: str) -> str:
    """The shell script Finder runs to start GitSense.

    - python is the interpreter that has gitsense installed, usually a venv's bin/python.
    - __PYVENV_LAUNCHER__ makes the linked GUI binary use that venv, as bin/python does.
    - Python removes __PYVENV_LAUNCHER__ at startup, so it does not leak to git or hooks.
    """
    return f"""#!/bin/sh
# GitSense launcher, written by `gitsense --install-app`.
# - Runs GitSense with the Python that installed it.
# - Output goes to ~/{LOG_DIR}/gitsense.log.

PYTHON={shlex.quote(python)}
HERE=$(cd "$(dirname "$0")" && pwd -P)
LOG_DIR="$HOME/{LOG_DIR}"
LOG="$LOG_DIR/gitsense.log"

mkdir -p "$LOG_DIR"
# Start a fresh log when it passes about 1 MB; keep the previous one.
if [ -f "$LOG" ] && [ "$(/usr/bin/stat -f %z "$LOG")" -gt {LOG_LIMIT} ]; then
    mv -f "$LOG" "$LOG_DIR/gitsense.previous.log"
fi

if [ ! -x "$PYTHON" ] || [ ! -x "$HERE/{PROCESS}" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') cannot start: $PYTHON is missing" >>"$LOG"
    /usr/bin/osascript - "$PYTHON" >/dev/null 2>&1 <<'APPLESCRIPT'
on run argv
    display dialog "GitSense needs to be reinstalled. The Python it was installed with is missing:" & return & return & (item 1 of argv) & return & return & "To reinstall, run scripts/install-macos-app.sh from the GitSense repository." buttons {{"OK"}} default button "OK" with title "GitSense" with icon caution
end run
APPLESCRIPT
    exit 1
fi

# Older macOS versions add a -psn_ argument; drop it.
for arg in "$@"; do
    shift
    case "$arg" in
        -psn_*) ;;
        *) set -- "$@" "$arg" ;;
    esac
done

echo "$(date '+%Y-%m-%d %H:%M:%S') starting GitSense with $PYTHON" >>"$LOG"
export __PYVENV_LAUNCHER__="$PYTHON"
export PYTHONUNBUFFERED=1
exec "$HERE/{PROCESS}" -m gitsense "$@" >>"$LOG" 2>&1
"""


def bundle_id_of(path: Path) -> str | None:
    """The CFBundleIdentifier of an app bundle, or None when it is not one."""
    try:
        with (path / "Contents" / "Info.plist").open("rb") as fh:
            info = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    value = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
    return value if isinstance(value, str) else None


def check_ours(path: Path) -> None:
    """Raise unless path is a real folder holding a GitSense bundle.

    - A symlink is refused even when it points at our bundle.
    - The bundle id must be ours, now or from before 1.0.0.
    - Anything with another bundle identifier is refused.
    """
    if path.is_symlink() or not path.is_dir():
        raise AppBundleError(f"{path} is not an app bundle, so it was left alone")
    found = bundle_id_of(path)
    if found not in OUR_BUNDLE_IDS:
        what = f"belongs to {found}" if found else "is not a GitSense app"
        raise AppBundleError(f"{path} {what}, so it was left alone")


def write_bundle(target: Path, python: str, gui_python: str, icon: Path | None = None) -> None:
    """Create a complete bundle at target, which must not exist yet.

    - icon is a mod's .icns or .png, or None for the standard icon.
    """
    macos = target / "Contents" / "MacOS"
    resources = target / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    with (target / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump(info_plist(), fh)
    (target / "Contents" / "PkgInfo").write_text("APPL????", encoding="ascii")
    launcher = macos / LAUNCHER
    launcher.write_text(launcher_script(python), encoding="utf-8")
    launcher.chmod(0o755)
    (macos / PROCESS).symlink_to(gui_python)
    source = icon if icon is not None and icon.is_file() else ICON
    if source.suffix.lower() == ".png":
        _png_to_icns(source, resources / ICON_NAME)
    elif source.is_file():
        shutil.copyfile(source, resources / ICON_NAME)


def _png_to_icns(png: Path, out: Path) -> None:
    """Convert a PNG to .icns with sips, or fall back to the standard icon."""
    sips = Path("/usr/bin/sips")
    if sips.is_file():
        subprocess.run([str(sips), "-s", "format", "icns", str(png), "--out", str(out)], capture_output=True, check=False)
    if not out.is_file() and ICON.is_file():
        shutil.copyfile(ICON, out)


def build_bundle(
    dest_dir: Path, python: str | None = None, gui_python: str | None = None, icon: Path | None = None
) -> Path:
    """Install GitSense.app in dest_dir, replacing an earlier GitSense.app.

    - python defaults to the interpreter running now.
    - icon is a mod's icon, or None for the standard one.
    - The new bundle is built beside the old one, then swapped in.
    - An existing GitSense.app is replaced only after check_ours accepts it.
    """
    dest_dir = Path(dest_dir).expanduser()
    if not dest_dir.is_dir():
        raise AppBundleError(f"{dest_dir} is not a folder")
    target = dest_dir / BUNDLE_NAME
    replacing = target.exists() or target.is_symlink()
    if replacing:
        check_ours(target)

    staging = Path(tempfile.mkdtemp(prefix=".GitSense-new-", dir=dest_dir))
    try:
        write_bundle(staging / BUNDLE_NAME, python or sys.executable, gui_python or str(gui_interpreter()), icon)
        if replacing:
            retired = Path(tempfile.mkdtemp(prefix=".GitSense-old-", dir=dest_dir))
            (target).rename(retired / BUNDLE_NAME)
        (staging / BUNDLE_NAME).rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if replacing:
        shutil.rmtree(retired, ignore_errors=True)
    return target


def remove_bundle(dest_dir: Path) -> Path:
    """Delete GitSense.app from dest_dir after check_ours accepts it."""
    target = Path(dest_dir).expanduser() / BUNDLE_NAME
    if not (target.exists() or target.is_symlink()):
        raise AppBundleError(f"There is no {BUNDLE_NAME} in {dest_dir}")
    check_ours(target)
    shutil.rmtree(target)
    return target


def refresh_launch_services(target: Path, *, unregister: bool = False) -> None:
    """Tell Finder and the Dock about a new or removed bundle.

    - Touching the bundle makes Finder reload its icon.
    - lsregister updates the Launch Services database.
    """
    if not unregister:
        try:
            os.utime(target)
        except OSError:
            pass
    if Path(LSREGISTER).is_file():
        flag = "-u" if unregister else "-f"
        subprocess.run([LSREGISTER, flag, str(target)], capture_output=True, check=False)


# -- command line ----------------------------------------------------------------


def _remove_hint(dest_dir: Path) -> str:
    if dest_dir == default_dir():
        return "gitsense --uninstall-app"
    return f"gitsense --uninstall-app {shlex.quote(str(dest_dir))}"


def _check_platform() -> bool:
    if sys.platform == "darwin":
        return True
    print("gitsense: the app bundle is only for macOS. Run gitsense from a terminal instead.", file=sys.stderr)
    return False


def install(directory: str | None, icon: Path | None = None) -> int:
    """`gitsense --install-app [DIR]`: build GitSense.app and say where it went.

    - icon is the icon of an enabled mod, or None for the standard one.
    """
    if not _check_platform():
        return 1
    dest_dir = Path(directory).expanduser() if directory else default_dir()
    try:
        target = build_bundle(dest_dir, icon=icon)
    except (AppBundleError, OSError) as exc:
        print(f"gitsense: {exc}", file=sys.stderr)
        return 1
    refresh_launch_services(target)
    print(f"Installed {target}")
    print(f"It runs GitSense with {sys.executable}")
    print(f"Log: {log_path()}")
    print(f"To remove it: {_remove_hint(dest_dir.resolve())}")
    return 0


def uninstall(directory: str | None) -> int:
    """`gitsense --uninstall-app [DIR]`: remove GitSense.app if it is ours."""
    if not _check_platform():
        return 1
    dest_dir = Path(directory).expanduser() if directory else default_dir()
    try:
        target = remove_bundle(dest_dir)
    except (AppBundleError, OSError) as exc:
        print(f"gitsense: {exc}", file=sys.stderr)
        return 1
    refresh_launch_services(target, unregister=True)
    print(f"Removed {target}")
    return 0
