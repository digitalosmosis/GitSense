"""Tests for the read layer in repo.py against real repositories."""

from __future__ import annotations

import pytest
from test_graph import check_invariants

from gitsense import actions, repo as repo_mod
from gitsense.gitcmd import GitError


@pytest.fixture
def history(remotes):
    """Clone `a` with a merge, tags, a stash and a remote-only branch.

    - main: Initial commit, m1, merge of topic
    - topic: t1, t2 (merged)
    - origin/remote-only: one commit pushed from clone b
    - tags: v1 (annotated, on t1), light (lightweight, on m1)
    - one stash made on the merge commit, holding an untracked file
    """
    a = remotes.a
    a.git("switch", "-q", "-c", "topic")
    t1 = a.commit("t1", {"topic.txt": "t1\n"})
    a.commit("t2", {"topic.txt": "t2\n"})
    a.git("switch", "-q", "main")
    m1 = a.commit("m1", {"main.txt": "m1\n"})
    a.git("merge", "-q", "--no-ff", "--no-edit", "topic")
    a.git("tag", "-a", "-m", "version one", "v1", t1)
    a.git("tag", "light", m1)
    remotes.b.git("switch", "-q", "-c", "remote-only")
    remotes.b.commit("remote work", {"r.txt": "r\n"})
    remotes.b.git("push", "-q", "origin", "remote-only")
    a.git("fetch", "-q")
    a.write("main.txt", "dirty\n")
    a.write("stashed-untracked.txt", "u\n")
    a.git("stash", "push", "-q", "--include-untracked", "-m", "saved work")
    return remotes


# -- log --------------------------------------------------------------------------


def test_log_lists_every_ref_with_stash_and_no_wip(history):
    a = history.a
    data = repo_mod.log(str(a))
    rows = data["commits"]
    subjects = [r["subject"] for r in rows]
    for expected in ("Initial commit", "t1", "t2", "m1", "Merge branch 'topic'", "remote work"):
        assert expected in subjects
    assert all(r.get("kind") != "wip" for r in rows)
    assert data["has_more"] is False
    assert data["head"]["branch"] == "main"

    stash_rows = [r for r in rows if r.get("kind") == "stash"]
    assert len(stash_rows) == 1
    stash = stash_rows[0]
    assert stash["parents"] == [a.head()]
    assert stash["stash_index"] == 0
    assert stash["subject"].endswith("saved work")
    # The stash's index and untracked commits are not shown as rows.
    shas = [r["sha"] for r in rows]
    assert len(shas) == len(set(shas))
    assert a.rev("stash^2") not in shas and a.rev("stash^3") not in shas
    # A stash row sits above the commit it was made on.
    assert shas.index(stash["sha"]) < shas.index(a.head())

    merge = next(r for r in rows if r["subject"] == "Merge branch 'topic'")
    assert len(merge["parents"]) == 2
    root = next(r for r in rows if r["subject"] == "Initial commit")
    assert root["parents"] == []
    check_invariants(rows, data["width"])


def test_log_without_remote_branches(history):
    rows = repo_mod.log(str(history.a), include_remotes=False)["commits"]
    assert "remote work" not in [r["subject"] for r in rows]


def test_log_wip_row(repo):
    assert repo_mod.log(str(repo))["commits"][0]["sha"] != "WIP"
    repo.write("README.md", "changed\n")
    data = repo_mod.log(str(repo))
    wip = data["commits"][0]
    assert wip["sha"] == "WIP" and wip["kind"] == "wip"
    assert wip["parents"] == [repo.head()]
    assert data["commits"][1]["sha"] == repo.head()
    assert wip["col"] == data["commits"][1]["col"]
    assert repo_mod.log(str(repo), wip=False)["commits"][0]["sha"] == repo.head()


def test_log_limit_and_has_more(repo):
    for i in range(5):
        repo.commit(f"c{i}")
    data = repo_mod.log(str(repo), limit=3)
    assert len(data["commits"]) == 3
    assert data["has_more"] is True
    data = repo_mod.log(str(repo), limit=6)
    assert len(data["commits"]) == 6 and data["has_more"] is False


def test_log_unborn(empty_repo):
    data = repo_mod.log(str(empty_repo))
    assert data["commits"] == []
    assert data["head"] == {"branch": "main", "sha": None, "detached": False, "unborn": True}
    empty_repo.write("a.txt", "a\n")
    rows = repo_mod.log(str(empty_repo))["commits"]
    assert [r["sha"] for r in rows] == ["WIP"]
    assert rows[0]["parents"] == []


def test_log_includes_detached_head_commits(repo):
    repo.git("switch", "-q", "--detach")
    sha = repo.commit("detached work")
    repo.git("switch", "-q", "main")
    assert sha not in [r["sha"] for r in repo_mod.log(str(repo))["commits"]]
    repo.git("switch", "-q", "--detach", sha)
    data = repo_mod.log(str(repo))
    assert data["commits"][0]["sha"] == sha
    assert data["head"]["detached"] is True


def test_log_handles_odd_subjects(repo):
    repo.commit("tabs\tand | pipes, ünïcode 🎉 and %s %H")
    row = repo_mod.log(str(repo))["commits"][0]
    assert row["subject"] == "tabs\tand | pipes, ünïcode 🎉 and %s %H"
    assert row["author"] == "Test Author" and row["email"] == "author@example.com"


# -- commit detail ------------------------------------------------------------------


def test_commit_detail_root_commit(repo):
    root = repo.head()
    d = repo_mod.commit_detail(str(repo), root)
    assert d["parents"] == []
    assert d["base"] == repo_mod.empty_tree(str(repo))
    assert sorted((f["path"], f["status"], f["added"], f["deleted"]) for f in d["files"]) == [
        ("README.md", "A", 1, 0),
        ("src/app.py", "A", 1, 0),
    ]


def test_commit_detail_stats_for_a_path_with_a_tab(repo):
    sha = repo.commit("tab", {"a\tb.txt": "one\ntwo\n"})
    d = repo_mod.commit_detail(str(repo), sha)
    assert [(f["path"], f["added"], f["deleted"]) for f in d["files"]] == [("a\tb.txt", 2, 0)]


def test_commit_detail_message_and_people(repo):
    repo.write("x", "x\n")
    repo.git("add", "x")
    repo.git("commit", "-q", "-F", "-", input="Subject line\n\nBody one\n\nBody two\n")
    d = repo_mod.commit_detail(str(repo), repo.head())
    assert d["subject"] == "Subject line"
    assert d["body"] == "Body one\n\nBody two"
    assert d["author"] == "Test Author"
    assert d["committer_email"] == "committer@example.com"
    assert "kind" not in d


def test_commit_detail_merge_uses_first_parent(history):
    a = history.a
    merge = a.rev("main")
    d = repo_mod.commit_detail(str(a), merge)
    assert len(d["parents"]) == 2
    assert d["base"] == d["parents"][0]
    assert [f["path"] for f in d["files"]] == ["topic.txt"]


def test_commit_detail_rename_and_binary(repo):
    repo.commit("payload", {"old name.txt": "".join(f"line {i}\n" for i in range(20)), "img.bin": b"\x00\x01"})
    repo.git("mv", "old name.txt", "new näme.txt")
    repo.write("img.bin", b"\x00\x02\x03")
    sha = repo.commit("rename and binary")
    files = {f["path"]: f for f in repo_mod.commit_detail(str(repo), sha)["files"]}
    assert files["new näme.txt"]["status"] == "R"
    assert files["new näme.txt"]["orig_path"] == "old name.txt"
    assert (files["new näme.txt"]["added"], files["new näme.txt"]["deleted"]) == (0, 0)
    assert files["img.bin"]["status"] == "M"
    assert files["img.bin"]["added"] is None and files["img.bin"]["deleted"] is None


def test_commit_detail_stash_with_untracked(history):
    a = history.a
    stash = repo_mod.list_stashes(str(a))[0]
    d = repo_mod.commit_detail(str(a), stash["sha"])
    assert d["kind"] == "stash"
    assert d["stash_index"] == 0
    files = {f["path"]: f for f in d["files"]}
    assert files["main.txt"]["status"] == "M"
    extra = files["stashed-untracked.txt"]
    assert extra["status"] == "?"
    assert extra["from_sha"] == a.rev("stash^3")


def test_commit_detail_unknown_sha(repo):
    with pytest.raises(GitError):
        repo_mod.commit_detail(str(repo), "deadbeef")


# -- refs, stashes and overview -----------------------------------------------------


def test_list_refs_tags_and_remote_branches(history):
    a = history.a
    refs = repo_mod.list_refs(str(a))
    tags = {t["name"]: t for t in refs["tags"]}
    assert tags["v1"]["annotated"] is True
    assert tags["v1"]["sha"] == a.rev("topic~1")
    assert tags["light"]["annotated"] is False
    assert tags["light"]["sha"] == a.rev("main^1")
    remote = {rb["full"] for rb in refs["remote_branches"]}
    assert remote == {"origin/main", "origin/remote-only"}
    branches = {b["name"]: b for b in refs["branches"]}
    assert branches["main"]["current"] is True
    assert branches["main"]["upstream"] == "origin/main"
    assert branches["main"]["ahead"] == 4
    assert branches["topic"]["upstream"] is None


def test_list_refs_upstream_gone(remotes):
    a = remotes.a
    a.git("switch", "-q", "-c", "topic")
    a.git("push", "-q", "-u", "origin", "topic")
    remotes.b.git("push", "-q", "origin", "--delete", "topic")
    a.git("fetch", "-q", "--prune")
    branches = {b["name"]: b for b in repo_mod.list_refs(str(a))["branches"]}
    assert branches["topic"]["gone"] is True
    assert branches["topic"]["upstream"] == "origin/topic"
    assert branches["main"]["gone"] is False


def test_list_refs_behind(remotes):
    remotes.b.commit("b1")
    remotes.b.commit("b2")
    remotes.b.git("push", "-q")
    remotes.a.git("fetch", "-q")
    main = next(b for b in repo_mod.list_refs(str(remotes.a))["branches"] if b["name"] == "main")
    assert (main["ahead"], main["behind"], main["gone"]) == (0, 2, False)


def test_overview(history):
    a = history.a
    ov = repo_mod.overview(str(a))
    assert ov["path"] == str(a) and ov["name"] == "a"
    assert ov["head"]["branch"] == "main"
    assert {b["name"] for b in ov["branches"]} == {"main", "topic"}
    origin = next(r for r in ov["remotes"] if r["name"] == "origin")
    assert origin["url"] == str(history.bare.path)
    assert {b["name"] for b in origin["branches"]} == {"main", "remote-only"}
    assert [t["name"] for t in ov["tags"]] and {t["name"] for t in ov["tags"]} == {"v1", "light"}
    assert len(ov["stashes"]) == 1 and ov["stashes"][0]["has_untracked"] is True
    assert ov["status"]["staged"] == [] and ov["status"]["unstaged"] == []
    assert ov["operation"] is None
    assert ov["github"] is None
    assert len(ov["fingerprint"]) == 16


def test_overview_github_and_stale_remote_branches(remotes):
    a = remotes.a
    a.git("remote", "set-url", "origin", "git@github.com:acme/widgets.git")
    # Remote-tracking refs of a remote that no longer exists still get a group.
    a.git("update-ref", "refs/remotes/old/feature", a.head())
    ov = repo_mod.overview(str(a))
    assert ov["github"] == {
        "host": "github.com",
        "slug": "acme/widgets",
        "remote": "origin",
        "url": "https://github.com/acme/widgets",
        "default_branch": "main",
    }
    old = next(r for r in ov["remotes"] if r["name"] == "old")
    assert old["url"] == "" and [b["name"] for b in old["branches"]] == ["feature"]
    assert old["default_branch"] is None
    assert next(r for r in ov["remotes"] if r["name"] == "origin")["default_branch"] == "main"


def test_overview_without_remotes_unborn(empty_repo):
    ov = repo_mod.overview(str(empty_repo))
    assert ov["remotes"] == [] and ov["branches"] == [] and ov["tags"] == []
    assert ov["head"]["unborn"] is True
    assert ov["github"] is None


def test_remote_origin_head_pointer_is_hidden(remotes):
    remotes.a.git("remote", "set-head", "origin", "main")
    refs = repo_mod.list_refs(str(remotes.a))
    assert [rb["name"] for rb in refs["remote_branches"]] == ["main"]
    assert refs["default_branches"] == {"origin": "main"}
    remotes.a.git("remote", "set-head", "origin", "-d")
    assert repo_mod.list_refs(str(remotes.a))["default_branches"] == {}


# -- operation state and fingerprint -----------------------------------------------


def test_operation_state_bisect(repo):
    repo.commit("c2")
    repo.git("bisect", "start")
    assert repo_mod.operation_state(str(repo)) == {"kind": "bisect"}
    actions.perform(str(repo), "op_abort", {})
    assert repo_mod.operation_state(str(repo)) is None


def test_operation_state_outside_a_repo(tmp_path):
    assert repo_mod.operation_state(str(tmp_path)) is None


def test_fingerprint_tracks_changes(repo):
    fp = repo_mod.fingerprint(str(repo))
    assert repo_mod.fingerprint(str(repo)) == fp
    repo.write("new.txt", "n\n")
    fp2 = repo_mod.fingerprint(str(repo))
    assert fp2 != fp
    repo.git("add", "new.txt")
    fp3 = repo_mod.fingerprint(str(repo))
    assert fp3 != fp2
    repo.git("tag", "t")
    assert repo_mod.fingerprint(str(repo)) != fp3


# -- diffs --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["plain.txt", "with space.txt", "dïr/ünï cøde.txt", "-dash.txt", '"quoted".txt'])
def test_file_diff_kinds_with_odd_names(repo, name):
    repo.commit("add", {name: "a\nb\n"})
    repo.write(name, "a\nB\n")
    d = repo_mod.file_diff(str(repo), "unstaged", name)
    assert d["path"] == name and d["kind"] == "unstaged" and d["empty"] is False
    assert [(ln["t"], ln["s"]) for ln in d["hunks"][0]["lines"]] == [(" ", "a"), ("-", "b"), ("+", "B")]
    repo.git("add", "--", name)
    assert repo_mod.file_diff(str(repo), "unstaged", name)["empty"] is True
    staged = repo_mod.file_diff(str(repo), "staged", name)
    assert staged["hunks"][0]["lines"][2] == {"t": "+", "s": "B", "o": None, "n": 2}
    sha = repo.commit("change")
    committed = repo_mod.file_diff(str(repo), "commit", name, sha=sha)
    assert [ln["s"] for ln in committed["hunks"][0]["lines"]] == ["a", "b", "B"]


def test_file_diff_untracked_file(repo):
    repo.write("brand new.txt", "x\ny\n")
    d = repo_mod.file_diff(str(repo), "unstaged", "brand new.txt")
    assert any(h.startswith("new file mode") for h in d["header"])
    assert [(ln["t"], ln["s"], ln["n"]) for ln in d["hunks"][0]["lines"]] == [("+", "x", 1), ("+", "y", 2)]


def test_file_diff_untracked_folder_is_empty(repo):
    repo.write("newdir/x.txt", "x\n")
    assert repo_mod.file_diff(str(repo), "unstaged", "newdir")["empty"] is True


def test_file_diff_root_commit_and_rename(repo):
    root = repo.head()
    d = repo_mod.file_diff(str(repo), "commit", "README.md", sha=root)
    assert d["hunks"][0]["lines"] == [{"t": "+", "s": "# Demo", "o": None, "n": 1}]
    body = "".join(f"line {i}\n" for i in range(10))
    repo.commit("long", {"long.txt": body})
    repo.git("mv", "long.txt", "renamed.txt")
    repo.write("renamed.txt", body + "extra\n")
    sha = repo.commit("rename")
    d = repo_mod.file_diff(str(repo), "commit", "renamed.txt", sha=sha, orig_path="long.txt")
    assert "rename from long.txt" in d["header"]
    assert [ln["s"] for ln in d["hunks"][0]["lines"] if ln["t"] == "+"] == ["extra"]


def test_file_diff_context_and_whitespace(repo):
    body = "".join(f"l{i}\n" for i in range(20))
    repo.commit("base", {"c.txt": body})
    repo.write("c.txt", body.replace("l10\n", "l10 \n"))
    d = repo_mod.file_diff(str(repo), "unstaged", "c.txt", context=0)
    assert len(d["hunks"][0]["lines"]) == 2
    assert repo_mod.file_diff(str(repo), "unstaged", "c.txt", ignore_ws=True)["empty"] is True


def test_file_diff_errors(repo):
    with pytest.raises(GitError, match="Unknown diff kind"):
        repo_mod.file_diff(str(repo), "sideways", "README.md")
    with pytest.raises(GitError, match="commit is required"):
        repo_mod.file_diff(str(repo), "commit", "README.md")


def test_file_history_follows_renames(repo):
    repo.commit("v2", {"README.md": "# Demo\nmore\n"})
    repo.git("mv", "README.md", "DOCS.md")
    repo.commit("rename")
    history = repo_mod.file_history(str(repo), "DOCS.md")
    assert [(c["subject"], c["path"]) for c in history] == [
        ("rename", "DOCS.md"),
        ("v2", "README.md"),
        ("Initial commit", "README.md"),
    ]
    older = repo_mod.file_diff(str(repo), "commit", history[1]["path"], sha=history[1]["sha"])
    assert [ln["t"] + ln["s"] for ln in older["hunks"][0]["lines"] if ln["t"] != " "] == ["+more"]


def test_file_history_keeps_a_leading_newline_in_the_path(repo):
    repo.commit("odd", {"\nnotes.txt": "n\n"})
    assert repo_mod.file_history(str(repo), "\nnotes.txt")[0]["path"] == "\nnotes.txt"


# -- summary, roots and scanning ----------------------------------------------------


def test_summary(remotes):
    a = remotes.a
    a.commit("ahead", {"x.txt": "x\n"})
    a.write("x.txt", "changed\n")
    a.write("u.txt", "u\n")
    s = repo_mod.summary(str(a))
    assert s["missing"] is False
    assert s["branch"] == "main" and s["detached"] is False
    assert (s["upstream"], s["ahead"], s["behind"]) == ("origin/main", 1, 0)
    assert (s["staged"], s["unstaged"], s["conflicted"]) == (0, 2, 0)
    assert s["last_commit"]["subject"] == "ahead"
    assert s["remote_url"] == str(remotes.bare.path)
    assert s["operation"] is None


def test_summary_missing_and_not_a_repo(tmp_path):
    assert repo_mod.summary(str(tmp_path / "gone"))["missing"] is True
    plain = tmp_path / "plain"
    plain.mkdir()
    s = repo_mod.summary(str(plain))
    assert s["missing"] is True and "not a git repository" in s["error"].lower()


def test_summary_unborn_and_detached(empty_repo, repo):
    s = repo_mod.summary(str(empty_repo))
    assert s["last_commit"] is None and s["detached"] is False and s["remote_url"] is None
    repo.git("switch", "-q", "--detach")
    assert repo_mod.summary(str(repo))["detached"] is True


def test_repo_root(repo, tmp_path):
    assert repo_mod.repo_root(repo.path / "src") == str(repo.path)
    with pytest.raises(GitError, match="Not a folder"):
        repo_mod.repo_root(repo.path / "README.md")
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GitError):
        repo_mod.repo_root(plain)


def test_repo_root_keeps_a_trailing_space(make_repo, tmp_path):
    make_repo("project")
    spaced = make_repo("project ")
    assert repo_mod.repo_root(spaced.path) == str(spaced.path)


def test_repo_root_rejects_bare(remotes):
    with pytest.raises(GitError):
        repo_mod.repo_root(remotes.bare.path)


def test_scan_for_repos(tmp_path, make_repo):
    root = tmp_path / "scan"
    make_repo("scan/alpha")
    make_repo("scan/group/beta")
    make_repo("scan/group/beta/nested")
    make_repo("scan/.hidden/gamma")
    make_repo("scan/node_modules/delta")
    make_repo("scan/a/b/c/too-deep")
    make_repo("scan/a/b/edge")
    (root / "empty").mkdir()
    found = repo_mod.scan_for_repos(root)
    names = [p.removeprefix(str(root.resolve()) + "/") for p in found]
    assert names == ["a/b/edge", "alpha", "group/beta"]
    assert repo_mod.scan_for_repos(root, depth=1) == [str((root / "alpha").resolve())]
    assert len(repo_mod.scan_for_repos(root, limit=1)) == 1
    with pytest.raises(GitError, match="Not a folder"):
        repo_mod.scan_for_repos(root / "nope")


def test_scan_root_that_is_a_repo(repo):
    assert repo_mod.scan_for_repos(repo.path) == [str(repo.path)]
