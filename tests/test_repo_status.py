"""Tests for repo.parse_status and repo.status."""

from __future__ import annotations

from gitsense import actions, repo as repo_mod

H = "1" * 40


def porcelain(*records: str) -> str:
    return "\0".join(records) + "\0"


def test_parse_headers_and_every_entry_kind():
    raw = porcelain(
        f"# branch.oid {H}",
        "# branch.head main",
        "# branch.upstream origin/main",
        "# branch.ab +3 -2",
        f"1 .M N... 100644 100644 100644 {H} {H} file with spaces.txt",
        f"1 A. N... 000000 100644 100644 {'0' * 40} {H} added.txt",
        f"1 MD N... 100644 100644 000000 {H} {H} both.txt",
        f"2 R. N... 100644 100644 100644 {H} {H} R100 new name.txt",
        "old name.txt",
        f"2 RM N... 100644 100644 100644 {H} {H} R087 dir/moved.txt",
        "dir/orig.txt",
        f"u UU N... 100644 100644 100644 100644 {H} {H} {H} conflict file.txt",
        f"u DU N... 100644 000000 100644 100644 {H} {'0' * 40} {H} deleted-by-us.txt",
        f"1 .M S.M. 160000 160000 160000 {H} {H} vendor/sub",
        "? untracked/naïve.txt",
        "! ignored.log",
    )
    st = repo_mod.parse_status(raw)
    assert st["oid"] == H
    assert st["branch"] == "main"
    assert st["upstream"] == "origin/main"
    assert (st["ahead"], st["behind"]) == (3, 2)
    assert st["truncated"] is False
    assert st["staged"] == [
        {"path": "added.txt", "status": "A"},
        {"path": "both.txt", "status": "M"},
        {"path": "new name.txt", "status": "R", "orig_path": "old name.txt"},
        {"path": "dir/moved.txt", "status": "R", "orig_path": "dir/orig.txt"},
    ]
    assert st["unstaged"] == [
        {"path": "file with spaces.txt", "status": "M"},
        {"path": "both.txt", "status": "D"},
        {"path": "dir/moved.txt", "status": "M"},
        {"path": "vendor/sub", "status": "M", "submodule": True},
        {"path": "untracked/naïve.txt", "status": "?"},
    ]
    assert st["conflicted"] == [
        {"path": "conflict file.txt", "status": "U", "xy": "UU"},
        {"path": "deleted-by-us.txt", "status": "U", "xy": "DU"},
    ]


def test_parse_detached_and_initial():
    st = repo_mod.parse_status(porcelain(f"# branch.oid {H}", "# branch.head (detached)"))
    assert st["branch"] is None and st["oid"] == H
    st = repo_mod.parse_status(porcelain("# branch.oid (initial)", "# branch.head main"))
    assert st["branch"] == "main" and st["oid"] is None


def test_parse_path_with_spaces_after_the_fixed_fields():
    raw = porcelain(f"1 M. N... 100644 100644 100644 {H} {H} a  b   c.txt")
    assert repo_mod.parse_status(raw)["staged"] == [{"path": "a  b   c.txt", "status": "M"}]


def test_parse_truncates(monkeypatch):
    monkeypatch.setattr(repo_mod, "MAX_STATUS_FILES", 2)
    raw = porcelain(
        "? a",
        "? b",
        f"2 R. N... 100644 100644 100644 {H} {H} R100 c",
        "c-orig",
        "? d",
    )
    st = repo_mod.parse_status(raw)
    assert st["truncated"] is True
    assert [e["path"] for e in st["unstaged"]] == ["a", "b"]
    assert st["staged"] == []


def test_parse_empty():
    st = repo_mod.parse_status("")
    assert st["branch"] is None and st["staged"] == [] and st["unstaged"] == []


# -- real repositories -------------------------------------------------------------


def test_status_with_rename_untracked_and_unicode(repo):
    repo.git("mv", "README.md", "READ ME.md")
    repo.write("src/app.py", "changed\n")
    repo.write("dir/ünïcødé file.txt", "x\n")
    st = repo_mod.status(str(repo))
    assert st["branch"] == "main"
    assert st["oid"] == repo.head()
    assert st["upstream"] is None
    assert st["staged"] == [{"path": "READ ME.md", "status": "R", "orig_path": "README.md"}]
    assert {"path": "src/app.py", "status": "M"} in st["unstaged"]
    assert {"path": "dir/ünïcødé file.txt", "status": "?"} in st["unstaged"]


def test_status_lists_files_inside_untracked_folders(repo):
    repo.write("new/a.txt", "a\n")
    repo.write("new/b/c.txt", "c\n")
    paths = sorted(e["path"] for e in repo_mod.status(str(repo))["unstaged"])
    assert paths == ["new/a.txt", "new/b/c.txt"]
    normal = repo_mod.status(str(repo), untracked="normal")
    assert [e["path"] for e in normal["unstaged"]] == ["new/"]


def test_status_conflicts(repo):
    repo.commit("base", {"c.txt": "base\n", "d.txt": "keep\n"})
    repo.git("switch", "-q", "-c", "other")
    repo.commit("other", {"c.txt": "other\n"})
    repo.git("rm", "-q", "d.txt")
    repo.commit("other deletes d")
    repo.git("switch", "-q", "main")
    repo.commit("main", {"c.txt": "main\n", "d.txt": "changed\n"})
    assert actions.perform(str(repo), "merge", {"ref": "other"})["conflict"] is True
    st = repo_mod.status(str(repo))
    assert {c["path"]: c["xy"] for c in st["conflicted"]} == {"c.txt": "UU", "d.txt": "UD"}


def test_status_detached(repo):
    repo.commit("second")
    repo.git("switch", "-q", "--detach", "HEAD~1")
    st = repo_mod.status(str(repo))
    assert st["branch"] is None
    assert st["oid"] == repo.head()


def test_status_unborn(empty_repo):
    empty_repo.write("a.txt", "a\n")
    st = repo_mod.status(str(empty_repo))
    assert st["branch"] == "main"
    assert st["oid"] is None
    assert st["unstaged"] == [{"path": "a.txt", "status": "?"}]
    empty_repo.git("add", "a.txt")
    st = repo_mod.status(str(empty_repo))
    assert st["staged"] == [{"path": "a.txt", "status": "A"}]


def test_status_upstream_ahead_behind(remotes):
    remotes.b.commit("b1", {"b.txt": "1\n"})
    remotes.b.git("push", "-q")
    remotes.a.commit("a1", {"a.txt": "1\n"})
    remotes.a.commit("a2", {"a.txt": "2\n"})
    remotes.a.git("fetch", "-q")
    st = repo_mod.status(str(remotes.a))
    assert st["upstream"] == "origin/main"
    assert (st["ahead"], st["behind"]) == (2, 1)
