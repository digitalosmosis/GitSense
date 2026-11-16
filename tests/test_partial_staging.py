"""Hunk and line staging round trips, applied with real git."""

from __future__ import annotations

import subprocess

import pytest

from gitsense import actions, repo as repo_mod
from gitsense.gitcmd import GitError

BASE = "".join(f"line {i}\n" for i in range(1, 31))


def lines_of(text: str | None) -> list[str]:
    return (text or "").splitlines()


def hunk_action(r, path: str, mode: str, hunk: int = 0, lines=None, **extra):
    kind = "staged" if mode == "unstage" else "unstaged"
    d = repo_mod.file_diff(str(r), kind, path, orig_path=extra.get("orig_path"))
    args = {"path": path, "mode": mode, "hunk": hunk, "header": d["hunks"][hunk]["header"], **extra}
    if lines is not None:
        args["lines"] = lines
    return actions.perform(str(r), "apply_hunk", args)


def find(d: dict, hunk: int, tag: str, text: str) -> int:
    for i, ln in enumerate(d["hunks"][hunk]["lines"]):
        if ln["t"] == tag and ln["s"] == text:
            return i
    raise AssertionError(f"{tag}{text} not in hunk {hunk}")


@pytest.fixture
def two_hunks(repo):
    repo.commit("base", {"f.txt": BASE})
    changed = BASE.replace("line 2\n", "line two\n").replace("line 25\n", "line 25\nline 25.5\n")
    repo.write("f.txt", changed)
    return repo, changed


def test_stage_one_hunk_of_several(two_hunks):
    r, changed = two_hunks
    d = repo_mod.file_diff(str(r), "unstaged", "f.txt")
    assert len(d["hunks"]) == 2
    hunk_action(r, "f.txt", "stage", hunk=1)
    staged = lines_of(r.index_blob("f.txt"))
    assert "line 25.5" in staged
    assert "line 2" in staged and "line two" not in staged
    assert r.read("f.txt") == changed
    remaining = repo_mod.file_diff(str(r), "unstaged", "f.txt")
    assert len(remaining["hunks"]) == 1
    assert "line two" in [ln["s"] for ln in remaining["hunks"][0]["lines"]]


def test_stage_then_unstage_one_hunk(two_hunks):
    r, changed = two_hunks
    actions.perform(str(r), "stage", {"paths": ["f.txt"]})
    hunk_action(r, "f.txt", "unstage", hunk=0)
    staged = lines_of(r.index_blob("f.txt"))
    assert "line two" not in staged and "line 2" in staged
    assert "line 25.5" in staged
    assert r.read("f.txt") == changed


def test_discard_one_hunk(two_hunks):
    r, _ = two_hunks
    hunk_action(r, "f.txt", "discard", hunk=0)
    work = lines_of(r.read("f.txt"))
    assert "line 2" in work and "line two" not in work
    assert "line 25.5" in work
    assert r.index_blob("f.txt") == BASE


@pytest.fixture
def mixed_hunk(repo):
    repo.commit("base", {"g.txt": "one\ntwo\nthree\nfour\nfive\n"})
    repo.write("g.txt", "one\nTWO\nextra\nthree\nfive\n")
    return repo


def test_stage_selected_added_line_only(mixed_hunk):
    r = mixed_hunk
    d = repo_mod.file_diff(str(r), "unstaged", "g.txt")
    hunk_action(r, "g.txt", "stage", lines=[find(d, 0, "+", "extra")])
    assert r.index_blob("g.txt") == "one\ntwo\nextra\nthree\nfour\nfive\n"
    assert r.read("g.txt") == "one\nTWO\nextra\nthree\nfive\n"


def test_stage_selected_removed_lines_only(mixed_hunk):
    r = mixed_hunk
    d = repo_mod.file_diff(str(r), "unstaged", "g.txt")
    sel = [find(d, 0, "-", "two"), find(d, 0, "-", "four")]
    hunk_action(r, "g.txt", "stage", lines=sel)
    assert r.index_blob("g.txt") == "one\nthree\nfive\n"


def test_stage_a_replacement_pair(mixed_hunk):
    r = mixed_hunk
    d = repo_mod.file_diff(str(r), "unstaged", "g.txt")
    sel = [find(d, 0, "-", "two"), find(d, 0, "+", "TWO")]
    hunk_action(r, "g.txt", "stage", lines=sel)
    assert r.index_blob("g.txt") == "one\nTWO\nthree\nfour\nfive\n"
    # Staging the rest by line converges on the working tree.
    d = repo_mod.file_diff(str(r), "unstaged", "g.txt")
    hunk_action(r, "g.txt", "stage", lines=list(range(len(d["hunks"][0]["lines"]))))
    assert r.index_blob("g.txt") == r.read("g.txt")


def test_unstage_selected_lines(mixed_hunk):
    r = mixed_hunk
    actions.perform(str(r), "stage", {"paths": ["g.txt"]})
    d = repo_mod.file_diff(str(r), "staged", "g.txt")
    hunk_action(r, "g.txt", "unstage", lines=[find(d, 0, "+", "extra")])
    assert r.index_blob("g.txt") == "one\nTWO\nthree\nfive\n"
    d = repo_mod.file_diff(str(r), "staged", "g.txt")
    hunk_action(r, "g.txt", "unstage", lines=[find(d, 0, "-", "four")])
    assert r.index_blob("g.txt") == "one\nTWO\nthree\nfour\nfive\n"
    assert r.read("g.txt") == "one\nTWO\nextra\nthree\nfive\n"


def test_discard_selected_lines(mixed_hunk):
    r = mixed_hunk
    d = repo_mod.file_diff(str(r), "unstaged", "g.txt")
    sel = [find(d, 0, "+", "extra"), find(d, 0, "-", "four")]
    hunk_action(r, "g.txt", "discard", lines=sel)
    assert r.read("g.txt") == "one\nTWO\nthree\nfour\nfive\n"
    assert r.index_blob("g.txt") == "one\ntwo\nthree\nfour\nfive\n"


def test_discard_keeps_staged_changes(mixed_hunk):
    r = mixed_hunk
    d = repo_mod.file_diff(str(r), "unstaged", "g.txt")
    hunk_action(r, "g.txt", "stage", lines=[find(d, 0, "+", "extra")])
    d = repo_mod.file_diff(str(r), "unstaged", "g.txt")
    hunk_action(r, "g.txt", "discard")
    assert r.read("g.txt") == r.index_blob("g.txt") == "one\ntwo\nextra\nthree\nfour\nfive\n"


def test_replace_whole_file_then_stage_only_deletions(repo):
    repo.commit("base", {"w.txt": "a\nb\n"})
    repo.write("w.txt", "c\nd\n")
    d = repo_mod.file_diff(str(repo), "unstaged", "w.txt")
    sel = [find(d, 0, "-", "a"), find(d, 0, "-", "b")]
    hunk_action(repo, "w.txt", "stage", lines=sel)
    assert repo.index_blob("w.txt") == ""


def test_no_newline_at_end_of_file(repo):
    repo.commit("base", {"n.txt": "a\nb"})
    repo.write("n.txt", "a\nb\nc")
    d = repo_mod.file_diff(str(repo), "unstaged", "n.txt")
    assert any(ln["t"] == "\\" for ln in d["hunks"][0]["lines"])
    hunk_action(repo, "n.txt", "stage")
    assert repo.index_blob("n.txt") == "a\nb\nc"


def test_stage_line_after_line_without_newline(repo):
    repo.commit("base", {"n.txt": "a\nb\n"})
    repo.write("n.txt", "a\nc")
    d = repo_mod.file_diff(str(repo), "unstaged", "n.txt")
    hunk_action(repo, "n.txt", "stage", lines=[find(d, 0, "-", "b")])
    assert repo.index_blob("n.txt") == "a\n"
    d = repo_mod.file_diff(str(repo), "unstaged", "n.txt")
    hunk_action(repo, "n.txt", "stage", lines=[find(d, 0, "+", "c")])
    assert repo.index_blob("n.txt") == "a\nc"


# -- new and deleted files ----------------------------------------------------------


def test_stage_some_lines_of_an_untracked_file(repo):
    repo.write("new file.txt", "n1\nn2\nn3\n")
    d = repo_mod.file_diff(str(repo), "unstaged", "new file.txt")
    hunk_action(repo, "new file.txt", "stage", lines=[find(d, 0, "+", "n2")])
    assert repo.index_blob("new file.txt") == "n2\n"
    # The rest is now an ordinary unstaged modification.
    d = repo_mod.file_diff(str(repo), "unstaged", "new file.txt")
    hunk_action(repo, "new file.txt", "stage")
    assert repo.index_blob("new file.txt") == "n1\nn2\nn3\n"


def test_unstage_some_lines_of_a_staged_new_file(repo):
    repo.write("n.txt", "n1\nn2\nn3\n")
    actions.perform(str(repo), "stage", {"paths": ["n.txt"]})
    d = repo_mod.file_diff(str(repo), "staged", "n.txt")
    hunk_action(repo, "n.txt", "unstage", lines=[find(d, 0, "+", "n1")])
    assert repo.index_blob("n.txt") == "n2\nn3\n"
    assert repo.read("n.txt") == "n1\nn2\nn3\n"


def test_unstage_every_line_of_a_staged_new_file_removes_it(repo):
    repo.write("n.txt", "n1\nn2\n")
    actions.perform(str(repo), "stage", {"paths": ["n.txt"]})
    hunk_action(repo, "n.txt", "unstage")
    assert repo.index_blob("n.txt") is None
    assert repo.exists("n.txt")


def test_discard_some_lines_of_an_untracked_file(repo):
    repo.write("u.txt", "u1\nu2\nu3\n")
    d = repo_mod.file_diff(str(repo), "unstaged", "u.txt")
    hunk_action(repo, "u.txt", "discard", lines=[find(d, 0, "+", "u2")])
    assert repo.read("u.txt") == "u1\nu3\n"
    assert repo.index_blob("u.txt") is None


def test_stage_some_lines_of_a_deleted_file(repo):
    repo.commit("base", {"del.txt": "d1\nd2\nd3\n"})
    (repo.path / "del.txt").unlink()
    d = repo_mod.file_diff(str(repo), "unstaged", "del.txt")
    hunk_action(repo, "del.txt", "stage", lines=[find(d, 0, "-", "d2")])
    assert repo.index_blob("del.txt") == "d1\nd3\n"
    assert not repo.exists("del.txt")


def test_stage_every_line_of_a_deleted_file_deletes_it(repo):
    repo.commit("base", {"del.txt": "d1\nd2\n"})
    (repo.path / "del.txt").unlink()
    hunk_action(repo, "del.txt", "stage")
    assert repo.index_blob("del.txt") is None
    assert "del.txt" in repo.staged_names()


def test_discard_some_lines_of_a_deleted_file(repo):
    repo.commit("base", {"del.txt": "d1\nd2\nd3\n"})
    (repo.path / "del.txt").unlink()
    d = repo_mod.file_diff(str(repo), "unstaged", "del.txt")
    hunk_action(repo, "del.txt", "discard", lines=[find(d, 0, "-", "d1")])
    assert repo.read("del.txt") == "d1\n"


def test_unstage_some_lines_of_a_staged_deletion(repo):
    repo.commit("base", {"del.txt": "d1\nd2\nd3\n"})
    repo.git("rm", "-q", "del.txt")
    d = repo_mod.file_diff(str(repo), "staged", "del.txt")
    hunk_action(repo, "del.txt", "unstage", lines=[find(d, 0, "-", "d3")])
    assert repo.index_blob("del.txt") == "d3\n"


# -- special paths and guards -------------------------------------------------------


@pytest.mark.parametrize("name", ["with space.txt", "café ünïcode.txt", "-dash.txt", "tab\there.txt"])
def test_stage_by_line_with_unusual_names(repo, name):
    repo.commit("base", {name: "x\ny\n"})
    repo.write(name, "x\nY\ny\n")
    d = repo_mod.file_diff(str(repo), "unstaged", name)
    hunk_action(repo, name, "stage", lines=[find(d, 0, "+", "Y")])
    assert repo.index_blob(name) == "x\nY\ny\n"


def test_rejects_a_stale_hunk_header(mixed_hunk):
    r = mixed_hunk
    with pytest.raises(GitError, match="changed on disk"):
        actions.perform(
            str(r), "apply_hunk", {"path": "g.txt", "mode": "stage", "hunk": 0, "header": "@@ -9,9 +9,9 @@"}
        )
    with pytest.raises(GitError, match="changed on disk"):
        actions.perform(str(r), "apply_hunk", {"path": "g.txt", "mode": "stage", "hunk": 5})
    with pytest.raises(GitError, match="changed on disk"):
        actions.perform(str(r), "apply_hunk", {"path": "g.txt", "mode": "stage", "hunk": "x"})


def test_rejects_an_empty_selection(mixed_hunk):
    with pytest.raises(GitError, match="No changed lines"):
        hunk_action(mixed_hunk, "g.txt", "stage", lines=[0])


def test_rejects_an_unknown_mode(mixed_hunk):
    with pytest.raises(GitError, match="Unknown hunk action"):
        actions.perform(str(mixed_hunk), "apply_hunk", {"path": "g.txt", "mode": "explode", "hunk": 0})


def test_binary_files_cannot_be_staged_by_hunk(repo):
    repo.commit("bin", {"b.bin": b"\x00\x01\x02"})
    repo.write("b.bin", b"\x00\x01\x03")
    d = repo_mod.file_diff(str(repo), "unstaged", "b.bin")
    assert d["binary"] is True
    with pytest.raises(GitError, match="cannot be staged by hunk"):
        actions.perform(str(repo), "apply_hunk", {"path": "b.bin", "mode": "stage", "hunk": 0})


def test_non_utf8_lines_are_never_staged_corrupted(repo):
    repo.commit("latin", {"lat.txt": b"ascii\nmore\n"})
    repo.write("lat.txt", b"ascii\ncaf\xe9\nmore\n")
    with pytest.raises(GitError, match="not UTF-8"):
        hunk_action(repo, "lat.txt", "stage")
    raw = subprocess.run(["git", "show", ":lat.txt"], cwd=repo.path, capture_output=True).stdout
    assert raw == b"ascii\nmore\n"
    # Whole-file staging still keeps the bytes.
    actions.perform(str(repo), "stage", {"paths": ["lat.txt"]})
    raw = subprocess.run(["git", "show", ":lat.txt"], cwd=repo.path, capture_output=True).stdout
    assert raw == b"ascii\ncaf\xe9\nmore\n"


def test_conflicted_file_cannot_be_staged_by_hunk(repo):
    repo.commit("base", {"c.txt": "base\n"})
    repo.git("switch", "-q", "-c", "other")
    repo.commit("other", {"c.txt": "other\n"})
    repo.git("switch", "-q", "main")
    repo.commit("main", {"c.txt": "main\n"})
    assert actions.perform(str(repo), "merge", {"ref": "other"})["conflict"] is True
    d = repo_mod.file_diff(str(repo), "unstaged", "c.txt")
    assert d["combined"] is True
    with pytest.raises(GitError, match="cannot be staged by hunk"):
        actions.perform(str(repo), "apply_hunk", {"path": "c.txt", "mode": "stage", "hunk": 0})


def test_glob_characters_in_the_path_only_touch_that_file(repo):
    # The pathspec "?.txt" also matches "0.txt", which git lists first.
    repo.commit("base", {"0.txt": "zero\n", "?.txt": "q\n"})
    repo.write("0.txt", "zero\nZERO\n")
    repo.write("?.txt", "q\nQ\n")
    d = repo_mod.file_diff(str(repo), "unstaged", "?.txt")
    assert d["header"][0] == "diff --git a/?.txt b/?.txt"
    hunk_action(repo, "?.txt", "stage")
    assert repo.staged_names() == ["?.txt"]


def test_unstage_lines_of_a_staged_rename_keeps_the_rename(repo):
    body = "".join(f"line {i}\n" for i in range(1, 21))
    repo.commit("base", {"old name.txt": body})
    repo.git("mv", "old name.txt", "new name.txt")
    repo.write("new name.txt", body + "extra1\nextra2\n")
    repo.git("add", "new name.txt")
    d = repo_mod.file_diff(str(repo), "staged", "new name.txt", orig_path="old name.txt")
    assert "rename from old name.txt" in d["header"]
    hunk_action(
        repo, "new name.txt", "unstage", lines=[find(d, 0, "+", "extra1")], orig_path="old name.txt"
    )
    st = repo_mod.status(str(repo))
    assert st["staged"] == [{"path": "new name.txt", "status": "R", "orig_path": "old name.txt"}]
    assert repo.index_blob("new name.txt") == body + "extra2\n"
    assert repo.read("new name.txt") == body + "extra1\nextra2\n"


def test_stage_lines_of_an_intent_to_add_rename(repo):
    body = "".join(f"line {i}\n" for i in range(1, 21))
    repo.commit("base", {"a.txt": body})
    repo.git("mv", "a.txt", "b.txt")
    repo.git("reset", "-q")
    repo.git("add", "-N", "b.txt")
    repo.write("b.txt", body + "more\n")
    hunk_action(repo, "b.txt", "stage", orig_path="a.txt")
    # Staging lines of an intent-to-add rename stages the rename along with them.
    st = repo_mod.status(str(repo))
    assert st["staged"] == [{"path": "b.txt", "status": "R", "orig_path": "a.txt"}]
    assert repo.index_blob("b.txt") == body + "more\n"


def test_discard_lines_of_an_intent_to_add_rename_keeps_the_new_name(repo):
    body = "".join(f"line {i}\n" for i in range(1, 21))
    repo.commit("base", {"a.txt": body})
    repo.git("mv", "a.txt", "b.txt")
    repo.git("reset", "-q")
    repo.git("add", "-N", "b.txt")
    repo.write("b.txt", body + "more\n")
    hunk_action(repo, "b.txt", "discard", orig_path="a.txt")
    assert repo.read("b.txt") == body
    assert not repo.exists("a.txt")


def test_discard_hunk_with_noprefix_config_touches_only_that_file(repo):
    repo.commit("base", {"a.txt": "x\n", "dir/a.txt": "x\n"})
    repo.git("config", "diff.noprefix", "true")
    repo.write("a.txt", "y\n")
    repo.write("dir/a.txt", "y\n")
    hunk_action(repo, "dir/a.txt", "discard")
    assert repo.read("dir/a.txt") == "x\n"
    assert repo.read("a.txt") == "y\n"


def test_staging_diffs_skip_textconv_and_commit_diffs_keep_it(repo):
    repo.commit("base", {"f.txt": "old\n", ".gitattributes": "*.txt diff=lower\n"})
    repo.git("config", "diff.lower.textconv", "tr A-Z a-z <")
    repo.write("f.txt", "NEW\n")
    shown = repo_mod.file_diff(str(repo), "unstaged", "f.txt")["hunks"][0]["lines"]
    expect = "\n".join(ln["t"] + ln["s"] for ln in shown)
    assert expect == "-old\n+NEW"
    hunk_action(repo, "f.txt", "stage", expect=expect)
    assert repo.index_blob("f.txt") == "NEW\n"
    sha = repo.commit("upper")
    shown = repo_mod.file_diff(str(repo), "commit", "f.txt", sha=sha)["hunks"][0]["lines"]
    assert [ln["t"] + ln["s"] for ln in shown] == ["-old", "+new"]
