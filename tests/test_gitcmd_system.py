"""Tests for the subprocess runner in gitcmd.py and the safe parts of system.py."""

from __future__ import annotations

import pytest

from gitsense import gitcmd, system
from gitsense.gitcmd import GitError


def test_git_returns_stdout(repo):
    assert gitcmd.git(repo.path, ["rev-parse", "HEAD"]).strip() == repo.head()


def test_git_error_keeps_the_fatal_line(repo):
    with pytest.raises(GitError) as info:
        gitcmd.git(repo.path, ["rev-parse", "--verify", "no-such-ref"])
    assert info.value.message.startswith("fatal:")
    assert info.value.code != 0
    assert info.value.stderr.startswith("fatal:")


def test_git_result_does_not_raise(repo):
    res = gitcmd.git_result(repo.path, ["rev-parse", "--verify", "nope"])
    assert res.returncode != 0
    res = gitcmd.git_result(repo.path, ["status"], write=True)
    assert res.returncode == 0


def test_ok_codes(tmp_path):
    out = gitcmd.git(tmp_path, ["diff", "--no-index", "--quiet", "/dev/null", "/dev/null"], ok_codes=(0, 1))
    assert out == ""


def test_missing_tool():
    with pytest.raises(GitError) as info:
        gitcmd.run(["gitsense-no-such-tool-xyz"])
    assert info.value.code == 127


def test_timeout():
    with pytest.raises(GitError, match="timed out") as info:
        gitcmd.run(["sleep", "5"], timeout=0.3)
    assert info.value.code == 124


def test_commands_never_prompt(repo):
    # A credential prompt would hang; with GIT_TERMINAL_PROMPT=0 it fails at once.
    env = gitcmd._base_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_EDITOR"] == "true"
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env


def test_git_dir_from_the_environment_is_ignored(repo, monkeypatch, tmp_path):
    head = repo.head()
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    assert gitcmd.git(repo.path, ["rev-parse", "HEAD"]).strip() == head


def test_clean_error_prefers_marked_lines():
    text = "hint: try this\nerror: it broke\nmore noise\nfatal: really broke\n"
    assert gitcmd._clean_error(text, "", 1, "git") == "error: it broke\nfatal: really broke"
    assert gitcmd._clean_error("", "", 3, "git") == "git exited with status 3"
    assert gitcmd._clean_error("", "only stdout\n", 1, "git") == "only stdout"


def test_repo_lock_is_shared_per_path():
    assert gitcmd.repo_lock("/a") is gitcmd.repo_lock("/a")
    assert gitcmd.repo_lock("/a") is not gitcmd.repo_lock("/b")


def test_non_utf8_output_is_replaced_not_fatal(repo):
    repo.commit("latin", {"l.txt": b"caf\xe9\n"})
    out = gitcmd.git(repo.path, ["show", "HEAD:l.txt"])
    assert out == "caf�\n"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "", "ftp://x", "/local"])
def test_open_url_only_allows_web_links(url, monkeypatch):
    monkeypatch.setattr(system.webbrowser, "open", lambda u: pytest.fail("browser must not open"))
    with pytest.raises(GitError, match="Only web links"):
        system.open_url(url)


def test_applescript_string_escapes():
    assert system._applescript_string('a "b" \\ c') == '"a \\"b\\" \\\\ c"'


# -- launchers never run repository files ---------------------------------------------


@pytest.fixture
def spawned(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(system, "_spawn", lambda argv, cwd=None: calls.append(list(argv)))
    monkeypatch.setattr(system.sys, "platform", "darwin")
    return calls


def test_open_in_editor_opens_scripts_as_text(tmp_path, spawned):
    script = tmp_path / "setup.command"
    script.write_text("#!/bin/sh\necho pwned\n")
    script.chmod(0o755)
    system.open_in_editor(str(script))
    assert spawned == [["open", "-t", str(script)]]


def test_open_in_editor_uses_the_configured_command(tmp_path, spawned):
    target = tmp_path / "a b.txt"
    target.write_text("x\n")
    system.open_in_editor(str(target), "code --wait")
    assert spawned == [["code", "--wait", str(target)]]


def test_folders_that_could_be_bundles_are_revealed_not_opened(tmp_path, spawned):
    bundle = tmp_path / "Evil.app"
    bundle.mkdir()
    plain = tmp_path / "src"
    plain.mkdir()
    system.reveal(str(bundle))
    system.open_in_editor(str(bundle))
    system.reveal(str(plain))
    assert spawned == [["open", "-R", str(bundle)], ["open", "-R", str(bundle)], ["open", str(plain)]]


def test_reveal_selects_files(tmp_path, spawned):
    f = tmp_path / "notes.txt"
    f.write_text("n\n")
    system.reveal(str(f))
    assert spawned == [["open", "-R", str(f)]]


def test_zenity_folder_keeps_a_trailing_space(monkeypatch):
    import subprocess

    monkeypatch.setattr(system, "native_folder_picker", None)
    monkeypatch.setattr(system.sys, "platform", "linux")
    monkeypatch.setattr(system.shutil, "which", lambda name: "/usr/bin/zenity" if name == "zenity" else None)
    monkeypatch.setattr(
        system.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "/home/me/project \n", ""),
    )
    assert system.pick_folder("/home/me")["path"] == "/home/me/project "
