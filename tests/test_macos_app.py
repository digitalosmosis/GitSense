"""Tests for the macOS app bundle and the window startup helpers.

- Bundles are built under tmp_path; nothing is registered with Launch Services.
- No test opens a window or starts a GUI process.
"""

from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from gitsense import __version__, cli, macos_app
from gitsense.macos_app import BUNDLE_ID, AppBundleError, build_bundle, remove_bundle

PYTHON = "/opt/example/venv/bin/python"
GUI_PYTHON = "/opt/example/Python.app/Contents/MacOS/Python"


@pytest.fixture(autouse=True)
def no_launch_services(monkeypatch):
    """Never touch the real Launch Services database from a test."""
    calls: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        macos_app, "refresh_launch_services", lambda target, unregister=False: calls.append((target, unregister))
    )
    return calls


def build(dest: Path, python: str = PYTHON) -> Path:
    return build_bundle(dest, python=python, gui_python=GUI_PYTHON)


def foreign_bundle(path: Path, bundle_id: str = "com.example.other") -> Path:
    (path / "Contents").mkdir(parents=True)
    with (path / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleIdentifier": bundle_id}, fh)
    (path / "Contents" / "keep.txt").write_text("not ours")
    return path


# -- building ---------------------------------------------------------------------


def test_bundle_layout(tmp_path):
    app = build(tmp_path)
    assert app == tmp_path / "GitSense.app"
    contents = app / "Contents"
    assert (contents / "Info.plist").is_file()
    assert (contents / "PkgInfo").read_text() == "APPL????"
    assert (contents / "MacOS" / "gitsense-launcher").is_file()
    assert (contents / "MacOS" / "GitSense").is_symlink()
    assert os.readlink(contents / "MacOS" / "GitSense") == GUI_PYTHON
    assert not list(tmp_path.glob(".GitSense-*")), "the staging folder is cleaned up"


def test_info_plist(tmp_path):
    app = build(tmp_path)
    with (app / "Contents" / "Info.plist").open("rb") as fh:
        info = plistlib.load(fh)
    assert info["CFBundleName"] == "GitSense"
    assert info["CFBundleDisplayName"] == "GitSense"
    assert info["CFBundleIdentifier"] == "app.gitsense.GitSense"
    assert info["CFBundleExecutable"] == "gitsense-launcher"
    assert info["CFBundleIconFile"] == "GitSense.icns"
    assert info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleShortVersionString"] == __version__
    assert info["NSHighResolutionCapable"] is True
    assert info["LSApplicationCategoryType"] == "public.app-category.developer-tools"
    assert info["LSMinimumSystemVersion"]


def test_launcher_script(tmp_path):
    launcher = build(tmp_path) / "Contents" / "MacOS" / "gitsense-launcher"
    mode = launcher.stat().st_mode
    assert mode & stat.S_IXUSR and mode & stat.S_IXGRP and mode & stat.S_IXOTH
    text = launcher.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert f"PYTHON={PYTHON}\n" in text
    assert 'export __PYVENV_LAUNCHER__="$PYTHON"' in text
    assert 'exec "$HERE/GitSense" -m gitsense "$@" >>"$LOG" 2>&1' in text
    assert 'LOG_DIR="$HOME/Library/Logs/GitSense"' in text
    assert "osascript" in text, "a missing interpreter shows a dialog"
    subprocess.run(["/bin/sh", "-n", str(launcher)], check=True)


def test_launcher_quotes_paths_with_spaces(tmp_path):
    launcher = build(tmp_path, python="/Users/a b/venv's/bin/python") / "Contents" / "MacOS" / "gitsense-launcher"
    text = launcher.read_text()
    assert "PYTHON='/Users/a b/venv'\"'\"'s/bin/python'" in text
    subprocess.run(["/bin/sh", "-n", str(launcher)], check=True)


def test_icon_is_copied(tmp_path):
    assert macos_app.ICON.is_file(), "GitSense.icns ships in the package"
    icon = build(tmp_path) / "Contents" / "Resources" / "GitSense.icns"
    assert icon.read_bytes() == macos_app.ICON.read_bytes()


def test_a_mod_icon_replaces_the_standard_one(tmp_path):
    mod_icon = tmp_path / "Mod.icns"
    mod_icon.write_bytes(b"icns from a mod")
    (tmp_path / "apps").mkdir()
    app = build_bundle(tmp_path / "apps", python=PYTHON, gui_python=GUI_PYTHON, icon=mod_icon)
    assert (app / "Contents" / "Resources" / "GitSense.icns").read_bytes() == b"icns from a mod"


def test_default_python_is_the_running_interpreter(tmp_path):
    app = build_bundle(tmp_path, gui_python=GUI_PYTHON)
    assert f"PYTHON={sys.executable}\n" in (app / "Contents" / "MacOS" / "gitsense-launcher").read_text()


def test_gui_interpreter_exists():
    assert macos_app.gui_interpreter().is_file()


def test_missing_folder_is_refused(tmp_path):
    with pytest.raises(AppBundleError):
        build(tmp_path / "missing")


@pytest.mark.skipif(sys.platform != "darwin", reason="runs the launcher with the real interpreter")
def test_launcher_runs_and_starts_a_fresh_log(tmp_path):
    """The launcher execs the linked interpreter with the venv, and rotates a big log."""
    app = build_bundle(tmp_path)
    home = tmp_path / "home"
    logs = home / "Library" / "Logs" / "GitSense"
    logs.mkdir(parents=True)
    (logs / "gitsense.log").write_bytes(b"x" * (macos_app.LOG_LIMIT + 1))
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    launcher = app / "Contents" / "MacOS" / "gitsense-launcher"
    subprocess.run([str(launcher), "-psn_0_123", "--version"], env=env, check=True, timeout=60)
    assert (logs / "gitsense.previous.log").stat().st_size == macos_app.LOG_LIMIT + 1
    log = (logs / "gitsense.log").read_text()
    assert f"gitsense {__version__}" in log


# -- replacing and removing -------------------------------------------------------


def test_reinstall_replaces_our_bundle(tmp_path):
    build(tmp_path, python="/old/bin/python")
    (tmp_path / "GitSense.app" / "Contents" / "stale.txt").write_text("from the old copy")
    app = build(tmp_path, python="/new/bin/python")
    assert "PYTHON=/new/bin/python\n" in (app / "Contents" / "MacOS" / "gitsense-launcher").read_text()
    assert not (app / "Contents" / "stale.txt").exists()
    assert not list(tmp_path.glob(".GitSense-*"))


def test_reinstall_refuses_a_foreign_bundle(tmp_path):
    other = foreign_bundle(tmp_path / "GitSense.app")
    with pytest.raises(AppBundleError, match="com.example.other"):
        build(tmp_path)
    assert (other / "Contents" / "keep.txt").read_text() == "not ours"
    assert not list(tmp_path.glob(".GitSense-*"))


def test_reinstall_refuses_a_folder_without_info_plist(tmp_path):
    (tmp_path / "GitSense.app" / "Contents").mkdir(parents=True)
    with pytest.raises(AppBundleError, match="not a GitSense app"):
        build(tmp_path)
    assert (tmp_path / "GitSense.app" / "Contents").is_dir()


def test_reinstall_refuses_a_file(tmp_path):
    (tmp_path / "GitSense.app").write_text("a file")
    with pytest.raises(AppBundleError):
        build(tmp_path)
    assert (tmp_path / "GitSense.app").read_text() == "a file"


def test_reinstall_refuses_a_symlink_even_to_our_bundle(tmp_path):
    (tmp_path / "real").mkdir()
    real = build(tmp_path / "real")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "GitSense.app").symlink_to(real)
    with pytest.raises(AppBundleError):
        build(dest)
    with pytest.raises(AppBundleError):
        remove_bundle(dest)
    assert (real / "Contents" / "Info.plist").is_file()


def test_uninstall_removes_only_our_bundle(tmp_path):
    app = build(tmp_path)
    (tmp_path / "Other.app").mkdir()
    assert remove_bundle(tmp_path) == app
    assert not app.exists()
    assert (tmp_path / "Other.app").is_dir()


def test_uninstall_keeps_the_interpreter(tmp_path):
    interpreter = tmp_path / "python"
    interpreter.write_text("interpreter")
    (tmp_path / "apps").mkdir()
    build_bundle(tmp_path / "apps", gui_python=str(interpreter))
    remove_bundle(tmp_path / "apps")
    assert interpreter.read_text() == "interpreter"


def test_uninstall_refuses_a_foreign_bundle(tmp_path):
    other = foreign_bundle(tmp_path / "GitSense.app")
    with pytest.raises(AppBundleError):
        remove_bundle(tmp_path)
    assert (other / "Contents" / "keep.txt").exists()


def test_uninstall_without_a_bundle(tmp_path):
    with pytest.raises(AppBundleError, match="There is no GitSense.app"):
        remove_bundle(tmp_path)


# -- command line -----------------------------------------------------------------


def test_install_and_uninstall_commands(tmp_path, monkeypatch, capsys, no_launch_services):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert cli.main(["--install-app", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    app = tmp_path / "GitSense.app"
    assert f"Installed {app}" in out
    assert "Library/Logs/GitSense/gitsense.log" in out
    assert "gitsense --uninstall-app" in out
    assert macos_app.bundle_id_of(app) == BUNDLE_ID
    assert no_launch_services == [(app, False)]

    assert cli.main(["--uninstall-app", str(tmp_path)]) == 0
    assert f"Removed {app}" in capsys.readouterr().out
    assert not app.exists()
    assert no_launch_services[-1] == (app, True)


def test_install_reports_a_refusal(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    foreign_bundle(tmp_path / "GitSense.app")
    assert cli.main(["--install-app", str(tmp_path)]) == 1
    assert "left alone" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--install-app", "--uninstall-app"])
def test_non_macos_is_refused(tmp_path, monkeypatch, capsys, flag):
    monkeypatch.setattr(sys, "platform", "linux")
    assert cli.main([flag, str(tmp_path)]) == 1
    assert "only for macOS" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_default_dir_falls_back_to_home_applications(monkeypatch):
    monkeypatch.setattr(macos_app.os, "access", lambda path, mode: False)
    folder = macos_app.default_dir()
    assert folder == Path.home() / "Applications"
    assert folder.is_dir()


# -- window startup ---------------------------------------------------------------


def test_fit_window_size_default_and_saved():
    assert cli.fit_window_size(None, None) == (1480, 940)
    assert cli.fit_window_size((1200, 800), (2560, 1400)) == (1200, 800)


def test_fit_window_size_clamps_to_the_screen():
    assert cli.fit_window_size(None, (1440, 875)) == (1440, 875)
    assert cli.fit_window_size((3000, 2000), (1512, 949)) == (1512, 949)
    assert cli.fit_window_size((1300, 2000), (1512, 949.5)) == (1300, 949)


def test_fit_window_size_keeps_the_minimum_unless_the_screen_is_smaller():
    assert cli.fit_window_size((600, 400), (1512, 949)) == cli.MIN_WINDOW
    assert cli.fit_window_size((600, 400), (900, 600)) == (900, 600)


def test_startup_background():
    theme = {"kind": "dark", "colors": {"bg": "#101820", "text": "#FFFFFF"}}
    assert cli.startup_background(theme) == "#101820"
    assert cli.startup_background(None) == "#1E1F22", "the default theme's background"
