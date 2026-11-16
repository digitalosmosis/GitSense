"""Tests for actions.perform on operations that need no remote."""

from __future__ import annotations

import os
import stat
import threading

import pytest

from gitsense import actions, repo as repo_mod
from gitsense.gitcmd import GitError


def do(r, action: str, **args):
    return actions.perform(str(r), action, args)


def parents(r, rev: str = "HEAD") -> list[str]:
    return r.git("rev-list", "--parents", "-n1", rev).split()[1:]


def subject(r, rev: str = "HEAD") -> str:
    return r.git("log", "-1", "--format=%s", rev).strip()


@pytest.fixture
def diverged(repo):
    """main and feature both changed shared.txt since their fork."""
    repo.commit("base", {"shared.txt": "base\n"})
    repo.git("switch", "-q", "-c", "feature")
    repo.commit("feature change", {"shared.txt": "feature\n"})
    repo.git("switch", "-q", "main")
    repo.commit("main change", {"shared.txt": "main\n"})
    return repo


@pytest.fixture
def forked(repo):
    """main and feature changed different files since their fork."""
    repo.git("switch", "-q", "-c", "feature")
    repo.commit("feature 1", {"feat.txt": "f1\n"})
    repo.commit("feature 2", {"feat.txt": "f1\nf2\n"})
    repo.git("switch", "-q", "main")
    repo.commit("main 1", {"main.txt": "m1\n"})
    return repo


# -- dispatch and validation -----------------------------------------------------


def test_unknown_action(repo):
    with pytest.raises(GitError, match="Unknown action"):
        do(repo, "format_disk")


def test_perform_accepts_none_args(repo):
    repo.write("README.md", "changed\n")
    actions.perform(str(repo), "stage_all", None)
    assert repo.staged_names() == ["README.md"]


@pytest.mark.parametrize(
    "action,args",
    [
        ("branch_create", {"name": "-D"}),
        ("branch_create", {"name": "ok", "start": "--orphan"}),
        ("branch_delete", {"name": "--all"}),
        ("branch_rename", {"old": "main", "new": "-f"}),
        ("checkout", {"ref": "--orphan=x"}),
        ("checkout_remote", {"full": "-b"}),
        ("merge", {"ref": "--abort"}),
        ("rebase", {"onto": "-i"}),
        ("cherry_pick", {"sha": "--quit"}),
        ("cherry_pick", {"shas": ["HEAD", "--abort"]}),
        ("revert", {"sha": "--abort"}),
        ("reset", {"sha": "--hard"}),
        ("tag_create", {"name": "-d"}),
        ("tag_create", {"name": "v1", "sha": "-f"}),
        ("tag_delete", {"name": "-l"}),
        ("remote_add", {"name": "x", "url": "--upload-pack=touch /tmp/pwned"}),
        ("remote_add", {"name": "-v", "url": "/tmp/x"}),
        ("remote_remove", {"name": "-v"}),
        ("remote_set_url", {"name": "origin", "url": "--push"}),
        ("set_upstream", {"branch": "main", "upstream": "-d"}),
        ("fetch", {"remote": "--upload-pack=evil"}),
        ("push", {"branch": "main", "remote": "--receive-pack=evil"}),
        ("push", {"branch": "--mirror"}),
        ("tag_push", {"name": "--all", "remote": "origin"}),
        ("remote_branch_delete", {"remote": "origin", "name": "--all"}),
        ("stash_drop", {"sha": "--all"}),
    ],
)
def test_names_starting_with_a_dash_are_rejected(repo, action, args):
    before = repo.git("for-each-ref")
    with pytest.raises(GitError, match="Invalid"):
        do(repo, action, **args)
    assert repo.git("for-each-ref") == before


@pytest.mark.parametrize("bad", ["", "   ", None, 42, "a\nb", "a\0b"])
def test_empty_or_odd_names_are_rejected(repo, bad):
    with pytest.raises(GitError):
        do(repo, "branch_create", name=bad)


@pytest.mark.parametrize("bad", [None, [], "", [""], [1], ["a\0b"]])
def test_bad_path_lists_are_rejected(repo, bad):
    with pytest.raises(GitError):
        do(repo, "stage", paths=bad)


# -- staging --------------------------------------------------------------------


def test_stage_and_unstage_files(repo):
    repo.write("README.md", "changed\n")
    repo.write("new dir/naïve file.txt", "new\n")
    (repo.path / "src/app.py").unlink()
    do(repo, "stage", paths=["README.md", "new dir/naïve file.txt", "src/app.py"])
    assert sorted(repo.staged_names()) == ["README.md", "new dir/naïve file.txt", "src/app.py"]
    st = repo_mod.status(str(repo))
    assert {e["path"]: e["status"] for e in st["staged"]} == {
        "README.md": "M",
        "new dir/naïve file.txt": "A",
        "src/app.py": "D",
    }
    do(repo, "unstage", paths=["README.md", "src/app.py"])
    assert repo.staged_names() == ["new dir/naïve file.txt"]
    assert repo.read("README.md") == "changed\n"


def test_stage_accepts_a_single_string(repo):
    repo.write("README.md", "changed\n")
    do(repo, "stage", paths="README.md")
    assert repo.staged_names() == ["README.md"]


def test_stage_and_unstage_all(repo):
    repo.write("README.md", "changed\n")
    repo.write("u.txt", "u\n")
    do(repo, "stage_all")
    assert sorted(repo.staged_names()) == ["README.md", "u.txt"]
    do(repo, "unstage_all")
    assert repo.staged_names() == []
    assert repo.exists("u.txt")


def test_staging_on_an_unborn_branch(empty_repo):
    r = empty_repo
    r.write("a.txt", "a\n")
    r.write("b.txt", "b\n")
    do(r, "stage", paths=["a.txt", "b.txt"])
    assert sorted(r.git("ls-files").split()) == ["a.txt", "b.txt"]
    do(r, "unstage", paths=["a.txt"])
    assert r.git("ls-files").split() == ["b.txt"]
    do(r, "unstage_all")
    assert r.git("ls-files") == ""
    assert r.exists("a.txt") and r.exists("b.txt")


def test_unstage_edited_file_on_an_unborn_branch(empty_repo):
    r = empty_repo
    for action, extra in (("unstage", {"paths": ["a.txt"]}), ("unstage_all", {})):
        r.write("a.txt", "a\n")
        do(r, "stage", paths=["a.txt"])
        r.write("a.txt", "a2\n")
        do(r, action, **extra)
        assert r.git("ls-files") == ""
        assert r.read("a.txt") == "a2\n"


def test_stage_path_with_pathspec_magic_is_literal(repo):
    repo.commit("files", {"foo": "foo\n", ":foo": "colon\n"})
    repo.write("foo", "foo2\n")
    repo.write(":foo", "colon2\n")
    do(repo, "stage", paths=[":foo"])
    assert repo.staged_names() == [":foo"]


def test_discard_tracked_untracked_and_folders(repo):
    repo.write("README.md", "changed\n")
    repo.write("junk.txt", "junk\n")
    repo.write("newdir/a.txt", "a\n")
    repo.write("newdir/sub/b.txt", "b\n")
    do(repo, "discard", paths=["README.md", "junk.txt", "newdir"])
    assert repo.read("README.md") == "# Demo\n"
    assert not repo.exists("junk.txt")
    assert not repo.exists("newdir/a.txt") and not repo.exists("newdir/sub/b.txt")
    assert repo.porcelain() == ""


def test_discard_a_deleted_file_restores_it(repo):
    (repo.path / "README.md").unlink()
    do(repo, "discard", paths=["README.md"])
    assert repo.read("README.md") == "# Demo\n"


def test_discard_keeps_the_staged_version(repo):
    repo.write("README.md", "staged\n")
    repo.git("add", "README.md")
    repo.write("README.md", "staged\nand more\n")
    do(repo, "discard", paths=["README.md"])
    assert repo.read("README.md") == "staged\n"
    assert repo.staged_names() == ["README.md"]


def test_discard_folder_with_tracked_and_untracked_changes(repo):
    repo.write("src/app.py", "changed\n")
    repo.write("src/new.py", "new\n")
    do(repo, "discard", paths=["src"])
    assert repo.read("src/app.py") == "print('hi')\n"
    assert not repo.exists("src/new.py")


def test_discard_glob_like_name_touches_only_that_file(repo):
    repo.commit("files", {"a.txt": "a\n", "*.txt": "star\n"})
    repo.write("a.txt", "a changed\n")
    repo.write("*.txt", "star changed\n")
    do(repo, "discard", paths=["*.txt"])
    assert repo.read("*.txt") == "star\n"
    assert repo.read("a.txt") == "a changed\n"


def test_discard_untracked_glob_like_name(repo):
    repo.write("keep.log", "k\n")
    repo.write("*.log", "x\n")
    do(repo, "discard", paths=["*.log"])
    assert not repo.exists("*.log")
    assert repo.exists("keep.log")


def test_discard_all(repo):
    repo.write("README.md", "changed\n")
    repo.git("add", "README.md")
    repo.write("src/app.py", "changed\n")
    repo.write("junk/x.txt", "x\n")
    do(repo, "discard_all")
    assert repo.porcelain() == ""


def test_discard_all_can_keep_untracked(repo):
    repo.write("README.md", "changed\n")
    repo.write("junk.txt", "x\n")
    do(repo, "discard_all", include_untracked=False)
    assert repo.porcelain() == "?? junk.txt\n"


def test_discard_all_keeping_untracked_refuses_when_they_block_tracked_files(repo):
    repo.commit("tracked", {"notes": "n\n", "d/f.txt": "f\n"})
    (repo.path / "notes").unlink()
    repo.write("notes/draft.txt", "draft\n")  # a folder where the tracked file was
    with pytest.raises(GitError, match="in the way of tracked ones: notes"):
        do(repo, "discard_all", include_untracked=False)
    assert repo.read("notes/draft.txt") == "draft\n"

    (repo.path / "d" / "f.txt").unlink()
    (repo.path / "d").rmdir()
    repo.write("d", "mine\n")  # a file where the tracked folder was
    with pytest.raises(GitError, match="in the way of tracked ones: d, notes"):
        do(repo, "discard_all", include_untracked=False)
    assert repo.read("d") == "mine\n"

    do(repo, "discard_all")  # deleting untracked files was asked for
    assert repo.porcelain() == ""
    assert repo.read("notes") == "n\n" and repo.read("d/f.txt") == "f\n"


def test_discard_all_keeping_untracked_sees_the_source_of_a_staged_rename(repo):
    repo.commit("tracked", {"notes": "n\n"})
    repo.git("mv", "notes", "saved")
    repo.write("notes/draft.txt", "draft\n")
    with pytest.raises(GitError, match="in the way of tracked ones: notes"):
        do(repo, "discard_all", include_untracked=False)
    assert repo.read("notes/draft.txt") == "draft\n"


def test_discard_all_keeping_untracked_allows_staged_replacements(repo):
    repo.commit("tracked", {"d/f.txt": "f\n", "notes": "n\n"})
    repo.git("rm", "-q", "-r", "d", "notes")
    repo.write("d", "staged file\n")
    repo.write("notes/staged.txt", "staged\n")
    repo.git("add", "d", "notes/staged.txt")
    do(repo, "discard_all", include_untracked=False)
    assert repo.porcelain() == ""
    assert repo.read("d/f.txt") == "f\n" and repo.read("notes") == "n\n"


def test_discard_all_keeping_untracked_counts_ignored_files(repo):
    repo.commit("tracked", {"notes": "n\n", ".gitignore": "*.log\n"})
    (repo.path / "notes").unlink()
    repo.write("notes/run.log", "log\n")
    with pytest.raises(GitError, match="in the way of tracked ones: notes"):
        do(repo, "discard_all", include_untracked=False)
    assert repo.read("notes/run.log") == "log\n"


@pytest.mark.skipif(not os.path.exists(__file__.upper()), reason="needs a case-insensitive file system")
def test_discard_all_keeping_untracked_matches_blockers_without_case(repo):
    repo.git("config", "core.ignorecase", "true")
    repo.commit("tracked", {"Notes": "n\n"})
    repo.git("mv", "Notes", "saved")
    repo.write("notes/draft.txt", "draft\n")
    with pytest.raises(GitError, match="in the way of tracked ones: Notes"):
        do(repo, "discard_all", include_untracked=False)
    assert repo.read("notes/draft.txt") == "draft\n"


def test_discard_all_on_unborn_branch(empty_repo):
    empty_repo.write("a.txt", "a\n")
    empty_repo.git("add", "a.txt")
    empty_repo.write("b.txt", "b\n")
    do(empty_repo, "discard_all")
    assert empty_repo.porcelain() == ""
    assert not empty_repo.exists("a.txt")


# -- commits --------------------------------------------------------------------


def test_commit_and_amend(repo):
    repo.write("README.md", "v2\n")
    do(repo, "stage", paths=["README.md"])
    do(repo, "commit", message="  Second commit\n\nBody line\n\n")
    assert repo.git("log", "-1", "--format=%B") == "Second commit\n\nBody line\n\n"
    first = repo.head()

    repo.write("README.md", "v3\n")
    do(repo, "commit", amend=True, stage_all=True)
    assert repo.head() != first
    assert subject(repo) == "Second commit"
    assert repo.head_blob("README.md") == "v3\n"
    assert parents(repo) == parents(repo, first)

    do(repo, "commit", amend=True, message="Reworded")
    assert subject(repo) == "Reworded"


def test_commit_message_starting_with_hash_is_kept(repo):
    repo.write("README.md", "v2\n")
    do(repo, "commit", message="#42 fix the thing\n\n# not a comment either", stage_all=True)
    assert repo.git("log", "-1", "--format=%B") == "#42 fix the thing\n\n# not a comment either\n\n"


def test_commit_requires_a_message(repo):
    repo.write("README.md", "v2\n")
    with pytest.raises(GitError, match="message is required"):
        do(repo, "commit", message="   ", stage_all=True)


def test_commit_with_nothing_staged_fails(repo):
    with pytest.raises(GitError):
        do(repo, "commit", message="nothing")


def test_commit_on_unborn_branch(empty_repo):
    empty_repo.write("a.txt", "a\n")
    do(empty_repo, "commit", message="root", stage_all=True)
    assert subject(empty_repo) == "root"
    assert parents(empty_repo) == []


def test_commit_no_verify_skips_hooks(repo):
    hook = repo.path / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\necho blocked >&2\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    repo.write("README.md", "v2\n")
    with pytest.raises(GitError):
        do(repo, "commit", message="blocked", stage_all=True)
    do(repo, "commit", message="allowed", no_verify=True)
    assert subject(repo) == "allowed"


# -- branches ---------------------------------------------------------------------


def test_branch_create_checkout_rename_delete(repo):
    start = repo.head()
    repo.commit("second")
    do(repo, "branch_create", name="topic", start=start)
    assert repo.branch() == "topic"
    assert repo.head() == start

    do(repo, "branch_create", name="other", checkout=False)
    assert repo.branch() == "topic"
    assert repo.rev("other") == start

    do(repo, "branch_rename", old="other", new="renamed")
    assert repo.git("branch", "--list", "other") == ""
    assert repo.rev("renamed") == start

    do(repo, "checkout", ref="main")
    assert repo.branch() == "main"
    do(repo, "branch_delete", name="renamed")
    assert repo.git("branch", "--list", "renamed") == ""


def test_branch_create_on_unborn_branch(empty_repo):
    do(empty_repo, "branch_create", name="trunk")
    assert empty_repo.branch() == "trunk"


def test_branch_delete_unmerged_needs_force(repo):
    repo.git("switch", "-q", "-c", "wip")
    repo.commit("unmerged")
    repo.git("switch", "-q", "main")
    with pytest.raises(GitError, match="not fully merged"):
        do(repo, "branch_delete", name="wip")
    do(repo, "branch_delete", name="wip", force=True)
    assert repo.git("branch", "--list", "wip") == ""


def test_checkout_detached(repo):
    first = repo.head()
    repo.commit("second")
    do(repo, "checkout", ref=first, detach=True)
    assert repo.branch() == ""
    assert repo.head() == first
    info = repo_mod.head_info(str(repo))
    assert info["detached"] is True and info["branch"] is None


def test_checkout_refuses_to_overwrite_changes(diverged):
    diverged.write("shared.txt", "dirty\n")
    with pytest.raises(GitError):
        do(diverged, "checkout", ref="feature")
    assert diverged.read("shared.txt") == "dirty\n"


# -- merge ------------------------------------------------------------------------


def test_merge_fast_forward(repo):
    repo.git("switch", "-q", "-c", "topic")
    tip = repo.commit("topic work", {"t.txt": "t\n"})
    repo.git("switch", "-q", "main")
    res = do(repo, "merge", ref="topic")
    assert "conflict" not in res
    assert repo.head() == tip


def test_merge_no_ff_creates_a_merge_commit(repo):
    repo.git("switch", "-q", "-c", "topic")
    tip = repo.commit("topic work", {"t.txt": "t\n"})
    repo.git("switch", "-q", "main")
    before = repo.head()
    do(repo, "merge", ref="topic", mode="no-ff")
    assert parents(repo) == [before, tip]
    assert subject(repo) == "Merge branch 'topic'"


def test_merge_squash_stages_without_committing(forked):
    before = forked.head()
    do(forked, "merge", ref="feature", mode="squash")
    assert forked.head() == before
    assert forked.staged_names() == ["feat.txt"]
    assert repo_mod.operation_state(str(forked)) is None


def test_merge_ff_only_refuses_diverged(forked):
    with pytest.raises(GitError):
        do(forked, "merge", ref="feature", mode="ff-only")


def test_merge_unknown_mode(repo):
    with pytest.raises(GitError, match="Unknown merge mode"):
        do(repo, "merge", ref="main", mode="octopus")


def test_merge_conflict_then_continue(diverged):
    main_tip = diverged.head()
    feature_tip = diverged.rev("feature")
    res = do(diverged, "merge", ref="feature")
    assert res["conflict"] is True
    state = repo_mod.operation_state(str(diverged))
    assert state["kind"] == "merge"
    assert state["target"] == [feature_tip]
    st = repo_mod.status(str(diverged))
    assert [c["path"] for c in st["conflicted"]] == ["shared.txt"]

    # Continuing with the conflict unresolved reports the conflict again.
    assert do(diverged, "op_continue")["conflict"] is True

    diverged.write("shared.txt", "resolved\n")
    do(diverged, "mark_resolved", paths=["shared.txt"])
    res = do(diverged, "op_continue")
    assert "conflict" not in res
    assert parents(diverged) == [main_tip, feature_tip]
    message = diverged.git("log", "-1", "--format=%B")
    assert message.startswith("Merge branch 'feature'")
    assert "#" not in message
    assert repo_mod.operation_state(str(diverged)) is None


def test_merge_conflict_then_abort(diverged):
    before = diverged.head()
    assert do(diverged, "merge", ref="feature")["conflict"] is True
    do(diverged, "op_abort")
    assert diverged.head() == before
    assert diverged.read("shared.txt") == "main\n"
    assert repo_mod.operation_state(str(diverged)) is None


def test_squash_merge_conflict_is_reported(diverged):
    res = do(diverged, "merge", ref="feature", mode="squash")
    assert res["conflict"] is True


@pytest.mark.parametrize("side,expected", [("ours", "main\n"), ("theirs", "feature\n")])
def test_resolve_takes_one_side(diverged, side, expected):
    do(diverged, "merge", ref="feature")
    do(diverged, "resolve", path="shared.txt", side=side)
    assert diverged.read("shared.txt") == expected
    assert repo_mod.status(str(diverged))["conflicted"] == []
    assert diverged.index_blob("shared.txt") == expected


@pytest.fixture
def delete_conflict(repo):
    """main modified gone.txt while `other` deleted it, and `other` modified kept.txt main deleted."""
    repo.commit("base", {"gone.txt": "base\n", "kept.txt": "base\n"})
    repo.git("switch", "-q", "-c", "other")
    repo.git("rm", "-q", "gone.txt")
    repo.commit("other deletes gone, edits kept", {"kept.txt": "other\n"})
    repo.git("switch", "-q", "main")
    repo.git("rm", "-q", "kept.txt")
    repo.commit("main edits gone, deletes kept", {"gone.txt": "main\n"})
    assert do(repo, "merge", ref="other")["conflict"] is True
    return repo


def test_resolve_delete_conflicts_by_taking_the_deleting_side(delete_conflict):
    r = delete_conflict
    do(r, "resolve", path="gone.txt", side="theirs")
    do(r, "resolve", path="kept.txt", side="ours")
    assert not r.exists("gone.txt") and not r.exists("kept.txt")
    assert repo_mod.status(str(r))["conflicted"] == []
    do(r, "op_continue")
    assert r.git("ls-files").split() == ["README.md", "src/app.py"]


def test_resolve_delete_conflicts_by_keeping_the_file(delete_conflict):
    r = delete_conflict
    do(r, "resolve", path="gone.txt", side="ours")
    do(r, "resolve", path="kept.txt", side="theirs")
    assert r.read("gone.txt") == "main\n" and r.read("kept.txt") == "other\n"
    assert repo_mod.status(str(r))["conflicted"] == []


def test_resolve_rejects_unknown_side(diverged):
    do(diverged, "merge", ref="feature")
    with pytest.raises(GitError, match="Unknown side"):
        do(diverged, "resolve", path="shared.txt", side="both")


def test_operation_actions_need_an_operation(repo):
    for action in ("op_continue", "op_abort", "op_skip"):
        with pytest.raises(GitError, match="No merge, rebase"):
            do(repo, action)


def test_op_skip_refused_for_a_merge(diverged):
    do(diverged, "merge", ref="feature")
    with pytest.raises(GitError, match="Cannot skip"):
        do(diverged, "op_skip")


# -- rebase -----------------------------------------------------------------------


def test_rebase_clean(forked):
    main_tip = forked.head()
    forked.git("switch", "-q", "feature")
    do(forked, "rebase", onto="main")
    assert forked.rev("HEAD~2") == main_tip
    assert forked.branch() == "feature"


def test_rebase_conflict_continue(diverged):
    diverged.git("switch", "-q", "feature")
    main_tip = diverged.rev("main")
    res = do(diverged, "rebase", onto="main")
    assert res["conflict"] is True
    state = repo_mod.operation_state(str(diverged))
    assert state["kind"] == "rebase"
    assert state["branch"] == "feature"
    assert state["onto"] == main_tip
    assert state["step"] == 1 and state["total"] == 1

    diverged.write("shared.txt", "both\n")
    do(diverged, "mark_resolved", paths=["shared.txt"])
    res = do(diverged, "op_continue")
    assert "conflict" not in res
    assert repo_mod.operation_state(str(diverged)) is None
    assert diverged.branch() == "feature"
    assert parents(diverged) == [main_tip]
    assert subject(diverged) == "feature change"
    assert diverged.read("shared.txt") == "both\n"


def test_rebase_conflict_abort(diverged):
    diverged.git("switch", "-q", "feature")
    before = diverged.head()
    assert do(diverged, "rebase", onto="main")["conflict"] is True
    do(diverged, "op_abort")
    assert diverged.head() == before
    assert diverged.branch() == "feature"
    assert repo_mod.operation_state(str(diverged)) is None


def test_rebase_conflict_skip(diverged):
    diverged.git("switch", "-q", "feature")
    diverged.commit("feature extra", {"extra.txt": "x\n"})
    main_tip = diverged.rev("main")
    assert do(diverged, "rebase", onto="main")["conflict"] is True
    res = do(diverged, "op_skip")
    assert "conflict" not in res
    assert repo_mod.operation_state(str(diverged)) is None
    assert parents(diverged) == [main_tip]
    assert subject(diverged) == "feature extra"
    assert diverged.read("shared.txt") == "main\n"


# -- cherry-pick, revert, reset ---------------------------------------------------


def test_cherry_pick_one_and_many(forked):
    f1 = forked.rev("feature~1")
    f2 = forked.rev("feature")
    do(forked, "cherry_pick", sha=f1)
    assert subject(forked) == "feature 1"
    forked.git("reset", "-q", "--hard", "HEAD~1")
    do(forked, "cherry_pick", shas=[f1, f2])
    assert subject(forked) == "feature 2"
    assert subject(forked, "HEAD~1") == "feature 1"


def test_cherry_pick_conflict_abort_and_skip(diverged):
    pick = diverged.rev("feature")
    before = diverged.head()
    res = do(diverged, "cherry_pick", sha=pick)
    assert res["conflict"] is True
    assert repo_mod.operation_state(str(diverged))["kind"] == "cherry-pick"
    do(diverged, "op_abort")
    assert diverged.head() == before
    assert repo_mod.operation_state(str(diverged)) is None

    assert do(diverged, "cherry_pick", sha=pick)["conflict"] is True
    do(diverged, "op_skip")
    assert diverged.head() == before
    assert repo_mod.operation_state(str(diverged)) is None


def test_cherry_pick_conflict_continue(diverged):
    pick = diverged.rev("feature")
    assert do(diverged, "cherry_pick", sha=pick)["conflict"] is True
    diverged.write("shared.txt", "picked\n")
    do(diverged, "mark_resolved", paths=["shared.txt"])
    do(diverged, "op_continue")
    assert subject(diverged) == "feature change"
    assert "#" not in diverged.git("log", "-1", "--format=%B")
    assert repo_mod.operation_state(str(diverged)) is None


def test_revert_plain_commit(repo):
    sha = repo.commit("add file", {"x.txt": "x\n"})
    do(repo, "revert", sha=sha)
    assert not repo.exists("x.txt")
    assert subject(repo) == 'Revert "add file"'


def test_revert_merge_commit_uses_first_parent(forked):
    do(forked, "merge", ref="feature", mode="no-ff")
    merge_sha = forked.head()
    do(forked, "revert", sha=merge_sha)
    assert not forked.exists("feat.txt")
    assert forked.exists("main.txt")
    assert parents(forked) == [merge_sha]


def test_revert_conflict_then_abort(repo):
    sha = repo.commit("v1", {"r.txt": "v1\n"})
    repo.commit("v2", {"r.txt": "v2\n"})
    before = repo.head()
    assert do(repo, "revert", sha=sha)["conflict"] is True
    assert repo_mod.operation_state(str(repo))["kind"] == "revert"
    do(repo, "op_abort")
    assert repo.head() == before


@pytest.mark.parametrize("mode", ["soft", "mixed", "hard"])
def test_reset_modes(repo, mode):
    first = repo.head()
    repo.commit("second", {"README.md": "v2\n"})
    repo.write("README.md", "v3\n")
    do(repo, "reset", sha=first, mode=mode)
    assert repo.head() == first
    if mode == "soft":
        assert repo.index_blob("README.md") == "v2\n"
        assert repo.read("README.md") == "v3\n"
    elif mode == "mixed":
        assert repo.index_blob("README.md") == "# Demo\n"
        assert repo.read("README.md") == "v3\n"
    else:
        assert repo.index_blob("README.md") == "# Demo\n"
        assert repo.read("README.md") == "# Demo\n"


def test_reset_rejects_unknown_mode(repo):
    with pytest.raises(GitError, match="Unknown reset mode"):
        do(repo, "reset", sha="HEAD", mode="keep")


# -- tags -------------------------------------------------------------------------


def test_tags_lightweight_annotated_and_delete(repo):
    first = repo.head()
    repo.commit("second")
    do(repo, "tag_create", name="v1.0", sha=first)
    do(repo, "tag_create", name="v2.0", message="Release two")
    refs = {t["name"]: t for t in repo_mod.list_refs(str(repo))["tags"]}
    assert refs["v1.0"] == {**refs["v1.0"], "sha": first, "annotated": False}
    assert refs["v2.0"]["sha"] == repo.head()
    assert refs["v2.0"]["annotated"] is True
    assert repo.git("tag", "-l", "-n1", "v2.0").split(None, 1)[1].strip() == "Release two"
    do(repo, "tag_delete", name="v1.0")
    assert repo.git("tag", "-l") == "v2.0\n"


def test_tag_create_duplicate_fails(repo):
    do(repo, "tag_create", name="v1")
    with pytest.raises(GitError, match="already exists"):
        do(repo, "tag_create", name="v1")


# -- stashes ----------------------------------------------------------------------


def test_stash_save_apply_pop_drop(repo):
    repo.write("README.md", "dirty\n")
    repo.write("untracked.txt", "u\n")
    do(repo, "stash_save", message="first stash")
    assert repo.porcelain() == ""
    stashes = repo_mod.list_stashes(str(repo))
    assert len(stashes) == 1
    assert stashes[0]["message"].endswith("first stash")
    assert stashes[0]["has_untracked"] is True
    assert stashes[0]["parent"] == repo.head()

    do(repo, "stash_apply", sha=stashes[0]["sha"])
    assert repo.read("README.md") == "dirty\n"
    assert repo.read("untracked.txt") == "u\n"
    assert len(repo_mod.list_stashes(str(repo))) == 1

    repo.git("reset", "-q", "--hard")
    repo.git("clean", "-fdq")
    do(repo, "stash_pop")
    assert repo.read("README.md") == "dirty\n"
    assert repo_mod.list_stashes(str(repo)) == []

    do(repo, "stash_save", include_untracked=False)
    assert repo.exists("untracked.txt")
    sha = repo_mod.list_stashes(str(repo))[0]["sha"]
    do(repo, "stash_drop", sha=sha)
    assert repo_mod.list_stashes(str(repo)) == []
    with pytest.raises(GitError, match="no longer exists"):
        do(repo, "stash_drop", sha=sha)


def test_stash_pop_specific_stash_by_sha(repo):
    repo.write("README.md", "one\n")
    do(repo, "stash_save", message="one")
    repo.write("README.md", "two\n")
    do(repo, "stash_save", message="two")
    older = repo_mod.list_stashes(str(repo))[1]
    assert older["index"] == 1
    do(repo, "stash_pop", sha=older["sha"])
    assert repo.read("README.md") == "one\n"
    remaining = repo_mod.list_stashes(str(repo))
    assert [s["message"].split(": ")[-1] for s in remaining] == ["two"]


def test_concurrent_stash_drops_remove_the_chosen_stashes(repo, monkeypatch):
    for name in ("c", "b", "a"):
        repo.write("README.md", f"{name}\n")
        do(repo, "stash_save", message=name)
    a, b, _c = (s["sha"] for s in repo_mod.list_stashes(str(repo)))
    real_list = repo_mod.list_stashes
    other = None

    def list_then_race(path):
        # Another request drops stash a right after this one looked b up.
        nonlocal other
        found = real_list(path)
        if other is None:
            other = threading.Thread(target=do, args=(repo, "stash_drop"), kwargs={"sha": a})
            other.start()
            other.join(0.5)
        return found

    monkeypatch.setattr(repo_mod, "list_stashes", list_then_race)
    do(repo, "stash_drop", sha=b)
    other.join()
    monkeypatch.setattr(repo_mod, "list_stashes", real_list)
    assert [s["message"].split(": ")[-1] for s in real_list(str(repo))] == ["c"]


def test_stash_keep_index(repo):
    repo.write("README.md", "staged\n")
    repo.git("add", "README.md")
    do(repo, "stash_save", keep_index=True)
    assert repo.staged_names() == ["README.md"]


def test_stash_pop_with_no_stashes(repo):
    with pytest.raises(GitError, match="no stashes"):
        do(repo, "stash_pop")


def test_stash_apply_conflict(repo):
    repo.write("README.md", "stashed\n")
    do(repo, "stash_save")
    repo.commit("conflicting", {"README.md": "committed\n"})
    sha = repo_mod.list_stashes(str(repo))[0]["sha"]
    res = do(repo, "stash_pop", sha=sha)
    assert res["conflict"] is True
    assert [c["path"] for c in repo_mod.status(str(repo))["conflicted"]] == ["README.md"]
    # A failed pop keeps the stash.
    assert len(repo_mod.list_stashes(str(repo))) == 1


def test_stash_on_unborn_branch_fails(empty_repo):
    empty_repo.write("a.txt", "a\n")
    with pytest.raises(GitError):
        do(empty_repo, "stash_save")


# -- environment ------------------------------------------------------------------


def test_git_runs_without_the_developers_config():
    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
    assert open(os.environ["GIT_CONFIG_GLOBAL"]).read() == ""
