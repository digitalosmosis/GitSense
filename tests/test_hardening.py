"""Regression tests for input hardening and stale-state checks."""

from __future__ import annotations

import os
import time

import pytest

from gitsense import actions, repo as repo_mod
from gitsense.gitcmd import GitError

from test_server import client, launched  # noqa: F401 - fixtures


def do(r, action: str, **args):
    return actions.perform(str(r), action, args)


@pytest.fixture
def diverged(repo):
    """main and feature both changed shared.txt since their fork."""
    repo.commit("base", {"shared.txt": "base\n"})
    repo.git("switch", "-q", "-c", "feature")
    repo.commit("feature change", {"shared.txt": "feature\n"})
    repo.git("switch", "-q", "main")
    repo.commit("main change", {"shared.txt": "main\n"})
    return repo


# -- fingerprint ------------------------------------------------------------------


def test_fingerprint_sees_a_second_edit_to_a_modified_file(repo):
    repo.write("README.md", "# Demo\nfirst edit\n")
    first = repo_mod.fingerprint(str(repo))
    assert repo_mod.fingerprint(str(repo)) == first
    repo.write("README.md", "# Demo\nsecond edit, longer\n")
    assert repo_mod.fingerprint(str(repo)) != first


def test_fingerprint_sees_a_same_size_edit(repo):
    path = repo.write("README.md", "# Demo\naaaa\n")
    first = repo_mod.fingerprint(str(repo))
    repo.write("README.md", "# Demo\nbbbb\n")
    later = time.time_ns() + 5_000_000_000
    os.utime(path, ns=(later, later))
    assert repo_mod.fingerprint(str(repo)) != first


def test_fingerprint_sees_restaging_of_a_partly_staged_file(repo):
    repo.write("README.md", "# Demo\none\n")
    repo.git("add", "README.md")
    repo.write("README.md", "# Demo\none\ntwo\nthree\n")
    first = repo_mod.fingerprint(str(repo))
    blob = repo.git("hash-object", "-w", "--stdin", input="# Demo\none\ntwo\n").strip()
    time.sleep(0.01)
    repo.git("update-index", "--cacheinfo", f"100644,{blob},README.md")
    assert repo.porcelain().startswith("MM")
    assert repo_mod.fingerprint(str(repo)) != first


# -- operations in progress -------------------------------------------------------


def test_new_operation_during_a_stopped_merge_is_an_error_not_a_conflict(diverged):
    assert do(diverged, "merge", ref="feature").get("conflict") is True
    assert repo_mod.operation_state(str(diverged))["kind"] == "merge"
    with pytest.raises(GitError):
        do(diverged, "cherry_pick", sha=diverged.rev("feature"))
    with pytest.raises(GitError):
        do(diverged, "rebase", onto="feature")


def test_git_am_is_reported_as_am_and_can_be_aborted(diverged):
    patch_text = diverged.git("format-patch", "-1", "--stdout", "feature")
    before = diverged.head()
    diverged.git("am", check=False, input=patch_text)
    state = repo_mod.operation_state(str(diverged))
    assert state == {"kind": "am"}
    do(diverged, "op_abort")
    assert repo_mod.operation_state(str(diverged)) is None
    assert diverged.head() == before


# -- hunk staging -----------------------------------------------------------------


def test_apply_hunk_refuses_when_the_shown_text_is_stale(repo):
    repo.write("src/app.py", "print('hi')\nprint('one')\n")
    shown = repo_mod.file_diff(str(repo), "unstaged", "src/app.py")["hunks"][0]
    expect = "\n".join(ln["t"] + ln["s"] for ln in shown["lines"])
    # Same hunk header, different added text.
    repo.write("src/app.py", "print('hi')\nprint('two')\n")
    with pytest.raises(GitError, match="changed on disk"):
        do(repo, "apply_hunk", path="src/app.py", mode="stage", hunk=0, header=shown["header"], expect=expect)
    assert repo.staged_names() == []
    now = repo_mod.file_diff(str(repo), "unstaged", "src/app.py")["hunks"][0]
    do(repo, "apply_hunk", path="src/app.py", mode="stage", hunk=0, header=now["header"],
       expect="\n".join(ln["t"] + ln["s"] for ln in now["lines"]))
    assert repo.staged_names() == ["src/app.py"]


# -- push targets -----------------------------------------------------------------


def test_push_when_the_upstream_is_a_local_branch(remotes):
    a = remotes.a
    a.git("switch", "-q", "-c", "topic", "--track", "main")
    sha = a.commit("topic work", {"t.txt": "t\n"})
    do(a, "push")
    assert remotes.bare.rev("topic") == sha
    assert a.git("rev-parse", "--abbrev-ref", "topic@{upstream}").strip() == "origin/topic"


def test_push_with_a_slash_in_the_remote_name(remotes):
    a = remotes.a
    a.git("remote", "rename", "origin", "team/origin")
    sha = a.commit("more", {"m.txt": "m\n"})
    do(a, "push")
    assert remotes.bare.rev("main") == sha


def test_push_to_a_branch_whose_name_differs_from_the_upstream(remotes):
    a = remotes.a
    a.git("switch", "-q", "-c", "local-name", "--track", "origin/main")
    sha = a.commit("renamed work", {"r.txt": "r\n"})
    do(a, "push")
    assert remotes.bare.rev("main") == sha


@pytest.mark.parametrize("name", ["+main", "a:b", "x..y", "has space", "-x"])
def test_refspec_names_are_validated(remotes, name):
    with pytest.raises(GitError):
        do(remotes.a, "push", branch=name)
    with pytest.raises(GitError):
        do(remotes.a, "tag_push", name=name)
    with pytest.raises(GitError):
        do(remotes.a, "tag_delete_remote", name=name)
    with pytest.raises(GitError):
        do(remotes.a, "remote_branch_delete", remote="origin", name=name)


def test_tag_push_cannot_target_a_branch(remotes):
    a = remotes.a
    before = remotes.bare.rev("main")
    a.commit("local only", {"l.txt": "l\n"})
    a.git("tag", "v1")
    with pytest.raises(GitError):
        do(a, "tag_push", name="v1:refs/heads/main")
    assert remotes.bare.rev("main") == before


# -- GitHub host detection --------------------------------------------------------


@pytest.mark.parametrize(
    "url, host",
    [
        ("git@github-work:owner/repo.git", "github.com"),
        ("git@github.com-work:owner/repo.git", "github.com"),
        ("https://acme.ghe.com/owner/repo.git", "acme.ghe.com"),
        ("git@github.acme.example:owner/repo.git", "github.acme.example"),
    ],
)
def test_github_hosts(url, host):
    info = repo_mod.github_info([{"name": "origin", "url": url}])
    assert info is not None and info["host"] == host


def test_other_hosts_are_not_github():
    assert repo_mod.github_info([{"name": "origin", "url": "git@gitlab.com:owner/repo.git"}]) is None


# -- server -----------------------------------------------------------------------


def test_action_args_must_be_an_object(client, repo):  # noqa: F811
    _, added = client.post("/api/repos/add", {"path": str(repo)})
    status, body = client.post("/api/repo/action", {"id": added["repo"]["id"], "action": "stage", "args": ["x"]})
    assert status == 400 and "object" in body["error"]


def test_post_without_token_is_refused_before_the_body_is_used(client, repo):  # noqa: F811
    status, _, _ = client.request("POST", "/api/repos/add", {"path": str(repo)}, token=False)
    assert status == 403
    status, state = client.get("/api/state")
    assert status == 200 and state["repos"] == []


def test_oversized_body_is_refused(client):  # noqa: F811
    status, _, _ = client.request("POST", "/api/settings", raw=b"", headers={"Content-Length": str(64 * 1024 * 1024)})
    assert status == 413
