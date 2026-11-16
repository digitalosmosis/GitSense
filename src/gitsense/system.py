"""Desktop integration: folder picker, terminal, file manager, editor, browser."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Callable

from .gitcmd import GitError

# Set by the native window at startup; returns a folder path or None.
native_folder_picker: Callable[[str], str | None] | None = None


def _spawn(argv: list[str], cwd: str | None = None) -> None:
    try:
        subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        raise GitError(f"Could not run {argv[0]}: {exc}") from exc


def pick_folder(start: str = "") -> dict[str, Any]:
    """Ask the user for a folder with a native dialog when one is available."""
    start = start or str(Path.home())
    if native_folder_picker is not None:
        return {"path": native_folder_picker(start), "supported": True}
    if sys.platform == "darwin":
        script = (
            'POSIX path of (choose folder with prompt "Choose a folder" '
            f'default location POSIX file {_applescript_string(start)})'
        )
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if res.returncode != 0:
            return {"path": None, "supported": True}
        return {"path": res.stdout.strip().rstrip("/") or None, "supported": True}
    if shutil.which("zenity"):
        res = subprocess.run(
            ["zenity", "--file-selection", "--directory", f"--filename={start}/"],
            capture_output=True,
            text=True,
        )
        # Only the newline: a folder name may end in a space.
        return {"path": res.stdout.rstrip("\n") or None, "supported": True}
    return {"path": None, "supported": False}


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def open_terminal(path: str) -> None:
    if sys.platform == "darwin":
        _spawn(["open", "-a", "Terminal", path])
    elif os.name == "nt":
        _spawn(["cmd", "/c", "start", "cmd"], cwd=path)
    else:
        for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(term):
                _spawn([term], cwd=path)
                return
        raise GitError("No terminal emulator found")


def _plain_folder(path: str) -> bool:
    """A folder that the file manager opens as a folder, not as an app bundle."""
    return os.path.isdir(path) and "." not in os.path.basename(path.rstrip("/")).lstrip(".")


def reveal(path: str) -> None:
    """Show a file or folder in the system file manager.

    - Files, and folders that could be bundles such as X.app, are selected in
      their parent folder and never opened, so nothing from a repository runs.
    """
    if sys.platform == "darwin":
        _spawn(["open", path] if _plain_folder(path) else ["open", "-R", path])
    elif os.name == "nt":
        os.startfile(path if os.path.isdir(path) else os.path.dirname(path))  # type: ignore[attr-defined]
    else:
        _spawn(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])


def open_in_editor(path: str, editor: str = "") -> None:
    """Open a file or folder in the configured editor, or the default text editor.

    - Without an editor setting, files open as text and folders are revealed.
    - The OS "open with default app" action is never used: it would run an
      executable script that a repository ships.
    """
    if editor.strip():
        _spawn([*shlex.split(editor), path])
        return
    if os.path.isdir(path):
        reveal(path)
    elif sys.platform == "darwin":
        _spawn(["open", "-t", path])
    elif os.name == "nt":
        _spawn(["notepad", path])
    else:
        _spawn(["xdg-open", path])


def open_url(url: str) -> None:
    if not url.startswith(("https://", "http://")):
        raise GitError("Only web links can be opened")
    webbrowser.open(url)
