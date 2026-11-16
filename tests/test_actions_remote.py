"""Tests for actions that talk to a remote, using a local bare repository."""

from __future__ import annotations

import pytest

from gitsense import actions, repo as repo_mod
from gitsense.gitcmd import GitError


def do(r, action: str, **args):
    return actions.perform(str(r), action, args)


def push_from_b(remotes, message: str, files: dict) -> str:
    sha = remotes.b.commit(message, files)
    remotes.b.git("push", "-q", "origin", "HEAD")
    return sha


# -- fetch ------------------------------------------------------------------------


def test_fetch_all_and_named_remote(remotes):
    sha = push_from_b(remotes, "from b", {"b.txt": "b\n"})
    do(remotes.a, "fetch")
    assert remotes.a.rev("origin/main") == sha
    sha2 = push_from_b(remotes, "from b again", {"b.txt": "b2\n"})
    do(remotes.a, "fetch", remote="origin")
    assert remotes.a.rev("origin/main") == sha2


def test_fetch_prune(remotes):
    remotes.b.git("push", "-q", "origin", "HEAD:refs/heads/temp")
    do(remotes.a, "fetch")
    assert remotes.a.rev("origin/temp")
    remotes.b.git("push", "-q", "origin", "--delete", "temp")
    do(remotes.a, "fetch", prune=False)
    assert "origin/temp" in remotes.a.git("branch", "-r")
    do(remotes.a, "fetch", prune=True)
    assert "origin/temp" not in remotes.a.git("branch", "-r")


def test_fetch_without_remotes_is_harmless(repo):
    do(repo, "fetch")


def test_fetch_unknown_remote_fails(remotes):
    with pytest.raises(GitError):
        do(remotes.a, "fetch", remote="nope")


# -- pull -------------------------------------------------------------------------


def test_pull_fast_forward(remotes):
    sha = push_from_b(remotes, "from b", {"b.txt": "b\n"})
    res = do(remotes.a, "pull")
    assert "conflict" not in res
    assert remotes.a.head() == sha


def test_pull_ff_only_refuses_diverged(remotes):
    push_from_b(remotes, "from b", {"b.txt": "b\n"})
    before = remotes.a.commit("from a", {"a.txt": "a\n"})
    with pytest.raises(GitError):
        do(remotes.a, "pull", mode="ff-only")
    assert remotes.a.head() == before


def test_pull_rebase(remotes):
    theirs = push_from_b(remotes, "from b", {"b.txt": "b\n"})
    remotes.a.commit("from a", {"a.txt": "a\n"})
    do(remotes.a, "pull", mode="rebase")
    assert remotes.a.rev("HEAD~1") == theirs
    assert remotes.a.git("log", "-1", "--format=%s").strip() == "from a"
    assert remotes.a.exists("a.txt") and remotes.a.exists("b.txt")


def test_pull_merge(remotes):
    theirs = push_from_b(remotes, "from b", {"b.txt": "b\n"})
    ours = remotes.a.commit("from a", {"a.txt": "a\n"})
    do(remotes.a, "pull", mode="merge")
    assert remotes.a.git("rev-list", "--parents", "-n1", "HEAD").split()[1:] == [ours, theirs]


def test_pull_merge_conflict(remotes):
    push_from_b(remotes, "from b", {"file.txt": "b\n"})
    remotes.a.commit("from a", {"file.txt": "a\n"})
    res = do(remotes.a, "pull", mode="merge")
    assert res["conflict"] is True
    assert repo_mod.operation_state(str(remotes.a))["kind"] == "merge"
    do(remotes.a, "op_abort")
    assert repo_mod.operation_state(str(remotes.a)) is None


def test_pull_rebase_conflict(remotes):
    push_from_b(remotes, "from b", {"file.txt": "b\n"})
    remotes.a.commit("from a", {"file.txt": "a\n"})
    res = do(remotes.a, "pull", mode="rebase")
    assert res["conflict"] is True
    assert repo_mod.operation_state(str(remotes.a))["kind"] == "rebase"
    do(remotes.a, "op_abort")


def test_pull_unknown_mode(remotes):
    with pytest.raises(GitError, match="Unknown pull mode"):
        do(remotes.a, "pull", mode="yolo")


def test_pull_without_upstream_fails(repo):
    with pytest.raises(GitError):
        do(repo, "pull")


# -- push -------------------------------------------------------------------------


def test_push_existing_upstream(remotes):
    sha = remotes.a.commit("from a", {"a.txt": "a\n"})
    do(remotes.a, "push")
    assert remotes.bare.rev("main") == sha


def test_push_refuses_an_upstream_remote_that_looks_like_an_option(remotes, tmp_path):
    marker = tmp_path / "ran"
    remotes.a.git("config", "branch.main.remote", f"--receive-pack=touch {marker}; false; #")
    with pytest.raises(GitError, match="Invalid remote"):
        do(remotes.a, "push")
    assert not marker.exists()


def test_push_new_branch_sets_upstream(remotes):
    remotes.a.git("switch", "-q", "-c", "topic")
    sha = remotes.a.commit("topic work", {"t.txt": "t\n"})
    do(remotes.a, "push")
    assert remotes.bare.rev("topic") == sha
    up = remotes.a.git("rev-parse", "--abbrev-ref", "topic@{upstream}").strip()
    assert up == "origin/topic"
    branches = {b["name"]: b for b in repo_mod.list_refs(str(remotes.a))["branches"]}
    assert branches["topic"]["upstream"] == "origin/topic"


def test_push_to_a_differently_named_branch(remotes):
    remotes.a.git("switch", "-q", "-c", "local-name")
    sha = remotes.a.commit("work", {"w.txt": "w\n"})
    do(remotes.a, "push", remote="origin", remote_branch="remote-name")
    assert remotes.bare.rev("remote-name") == sha
    # Later pushes follow the upstream, not the local name.
    sha2 = remotes.a.commit("more", {"w.txt": "w2\n"})
    do(remotes.a, "push")
    assert remotes.bare.rev("remote-name") == sha2
    assert remotes.bare.git("branch", "--list", "local-name") == ""


def test_push_rejected_then_force_with_lease(remotes):
    remotes.a.commit("from a", {"a.txt": "a\n"})
    do(remotes.a, "push")
    remotes.a.git("commit", "-q", "--amend", "-m", "rewritten")
    with pytest.raises(GitError):
        do(remotes.a, "push")
    do(remotes.a, "push", force=True)
    assert remotes.bare.rev("main") == remotes.a.head()


def test_force_with_lease_refuses_unseen_remote_work(remotes):
    theirs = push_from_b(remotes, "from b", {"b.txt": "b\n"})
    # a has not fetched b's commit, so its lease is stale.
    remotes.a.commit("from a", {"a.txt": "a\n"})
    with pytest.raises(GitError):
        do(remotes.a, "push", force=True)
    assert remotes.bare.rev("main") == theirs


def test_push_detached_head_fails(remotes):
    remotes.a.git("switch", "-q", "--detach")
    with pytest.raises(GitError, match="Check out a branch"):
        do(remotes.a, "push")


def test_push_without_remote_fails(repo):
    with pytest.raises(GitError, match="no remote"):
        do(repo, "push")


def test_remote_branch_delete(remotes):
    remotes.a.git("push", "-q", "origin", "HEAD:refs/heads/doomed")
    do(remotes.a, "remote_branch_delete", remote="origin", name="doomed")
    assert remotes.bare.git("branch", "--list", "doomed") == ""


def test_checkout_remote_creates_tracking_branch(remotes):
    remotes.b.git("switch", "-q", "-c", "feature/x")
    sha = remotes.b.commit("feature", {"f.txt": "f\n"})
    remotes.b.git("push", "-q", "origin", "feature/x")
    do(remotes.a, "fetch")
    do(remotes.a, "checkout_remote", full="origin/feature/x")
    assert remotes.a.branch() == "feature/x"
    assert remotes.a.head() == sha
    assert remotes.a.git("rev-parse", "--abbrev-ref", "@{upstream}").strip() == "origin/feature/x"
    # A second checkout switches to the existing local branch.
    remotes.a.git("switch", "-q", "main")
    do(remotes.a, "checkout_remote", full="origin/feature/x")
    assert remotes.a.branch() == "feature/x"


def test_checkout_remote_with_custom_local_name(remotes):
    do(remotes.a, "checkout_remote", full="origin/main", name="mirror")
    assert remotes.a.branch() == "mirror"


def test_set_upstream(remotes):
    remotes.a.git("push", "-q", "origin", "HEAD:refs/heads/other")
    do(remotes.a, "fetch")
    remotes.a.git("switch", "-q", "-c", "local")
    do(remotes.a, "set_upstream", branch="local", upstream="origin/other")
    assert remotes.a.git("rev-parse", "--abbrev-ref", "local@{upstream}").strip() == "origin/other"


# -- tags on the remote -----------------------------------------------------------


def test_tag_push_and_delete_remote(remotes):
    do(remotes.a, "tag_create", name="v1.0", message="one")
    do(remotes.a, "tag_create", name="light")
    do(remotes.a, "tag_push", name="v1.0")
    do(remotes.a, "tag_push", name="light", remote="origin")
    tags = remotes.bare.git("tag", "-l").split()
    assert tags == ["light", "v1.0"]
    do(remotes.a, "tag_delete_remote", name="v1.0")
    assert remotes.bare.git("tag", "-l").split() == ["light"]
    assert "v1.0" in remotes.a.git("tag", "-l")


# -- remotes ----------------------------------------------------------------------


def test_remote_add_set_url_remove(remotes, tmp_path):
    other = tmp_path / "other remote.git"
    remotes.a.git("init", "-q", "--bare", str(other))
    do(remotes.a, "remote_add", name="upstream", url=str(other))
    listed = {r["name"]: r for r in repo_mod.list_remotes(str(remotes.a))}
    assert listed["upstream"]["url"] == str(other)
    assert listed["upstream"]["push_url"] == str(other)

    do(remotes.a, "remote_set_url", name="upstream", url="https://github.com/acme/widgets.git")
    listed = {r["name"]: r for r in repo_mod.list_remotes(str(remotes.a))}
    assert listed["upstream"]["url"] == "https://github.com/acme/widgets.git"

    do(remotes.a, "remote_remove", name="upstream")
    assert [r["name"] for r in repo_mod.list_remotes(str(remotes.a))] == ["origin"]


def test_remote_add_duplicate_fails(remotes):
    with pytest.raises(GitError, match="already exists"):
        do(remotes.a, "remote_add", name="origin", url="/elsewhere")


def test_default_remote_is_origin_or_first(remotes, tmp_path):
    remotes.a.git("remote", "rename", "origin", "zzz")
    remotes.a.git("remote", "add", "aaa", str(remotes.bare.path))
    remotes.a.git("switch", "-q", "-c", "fresh")
    do(remotes.a, "push")
    up = remotes.a.git("rev-parse", "--abbrev-ref", "fresh@{upstream}").strip()
    assert up == "aaa/fresh"
