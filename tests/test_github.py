"""Tests for the pure helpers in github.py. Nothing here runs gh or touches the network."""

from __future__ import annotations

import threading

import pytest

from gitsense import github
from gitsense.gitcmd import repo_lock


@pytest.mark.parametrize(
    "rollup,expected",
    [
        (None, None),
        ([], None),
        ([{"conclusion": "SUCCESS"}, {"state": "SUCCESS"}], "passing"),
        ([{"conclusion": "success"}], "passing"),
        ([{"conclusion": "NEUTRAL"}, {"conclusion": "SKIPPED"}], "passing"),
        ([{"conclusion": "SUCCESS"}, {"status": "IN_PROGRESS", "conclusion": ""}], "pending"),
        ([{"state": "PENDING"}], "pending"),
        ([{"status": "QUEUED"}], "pending"),
        ([{}], "pending"),
        ([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}], "failing"),
        ([{"status": "IN_PROGRESS"}, {"state": "ERROR"}], "failing"),
        ([{"conclusion": "TIMED_OUT"}], "failing"),
        ([{"conclusion": "CANCELLED"}], "failing"),
        ([{"conclusion": "ACTION_REQUIRED"}], "failing"),
    ],
)
def test_checks_summary(rollup, expected):
    assert github._checks_summary(rollup) == expected


def test_shape_pr_full():
    pr = {
        "number": 12,
        "title": "Add widgets",
        "headRefName": "feature/widgets",
        "baseRefName": "main",
        "author": {"login": "octocat"},
        "isDraft": True,
        "url": "https://github.com/acme/widgets/pull/12",
        "state": "OPEN",
        "updatedAt": "2026-01-02T03:04:05Z",
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [{"conclusion": "SUCCESS"}],
        "isCrossRepository": True,
        "headRepositoryOwner": {"login": "forker"},
        "mergeable": "MERGEABLE",
    }
    assert github._shape_pr(pr) == {
        "number": 12,
        "title": "Add widgets",
        "head": "feature/widgets",
        "base": "main",
        "author": "octocat",
        "draft": True,
        "url": "https://github.com/acme/widgets/pull/12",
        "state": "OPEN",
        "updated": "2026-01-02T03:04:05Z",
        "review": "APPROVED",
        "checks": "passing",
        "fork": True,
        "head_owner": "forker",
        "mergeable": "MERGEABLE",
    }


def test_shape_pr_sparse():
    shaped = github._shape_pr({"number": 1, "author": None, "reviewDecision": "", "headRepositoryOwner": None})
    assert shaped["author"] is None
    assert shaped["head_owner"] is None
    assert shaped["review"] is None
    assert shaped["checks"] is None
    assert shaped["draft"] is False and shaped["fork"] is False


def test_gh_missing_is_reported_without_running_anything(monkeypatch):
    monkeypatch.setattr(github, "find_tool", lambda name: None)
    assert github.status() == {"installed": False, "authenticated": False, "user": None}
    with pytest.raises(github.GitError, match="not installed"):
        github.list_prs("/nowhere")


@pytest.mark.parametrize("key", ["base", "head", "slug"])
def test_create_pr_rejects_dash_values(monkeypatch, key):
    monkeypatch.setattr(github, "_gh", lambda *a, **k: pytest.fail("gh must not run"))
    with pytest.raises(github.GitError, match="Invalid"):
        github.create_pr("/nowhere", {"title": "t", key: "--web"})


def test_create_pr_requires_title(monkeypatch):
    monkeypatch.setattr(github, "_gh", lambda *a, **k: pytest.fail("gh must not run"))
    with pytest.raises(github.GitError, match="title is required"):
        github.create_pr("/nowhere", {"title": "  "})


def test_create_pr_builds_arguments(monkeypatch):
    seen = {}

    def fake(args, **kw):
        seen["args"] = args
        return "Creating pull request\nhttps://github.com/acme/widgets/pull/7\n"

    monkeypatch.setattr(github, "_gh", fake)
    res = github.create_pr("/r", {"title": "T", "body": "B", "base": "main", "draft": True})
    assert res["url"] == "https://github.com/acme/widgets/pull/7"
    assert seen["args"] == ["pr", "create", "--title", "T", "--body", "B", "--base", "main", "--draft"]


def test_checkout_pr_holds_the_repository_lock(monkeypatch, tmp_path):
    lock = repo_lock(str(tmp_path))
    free_for_others = []

    def fake_gh(args, **kw):
        # Another thread must not get the lock while gh runs.
        def probe():
            got = lock.acquire(blocking=False)
            free_for_others.append(got)
            if got:
                lock.release()

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        return "Switched to branch 'pr'\n"

    monkeypatch.setattr(github, "_gh", fake_gh)
    assert github.checkout_pr(str(tmp_path), 7)["output"] == "Switched to branch 'pr'"
    assert free_for_others == [False]
