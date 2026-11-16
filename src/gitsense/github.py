"""GitHub features, through the `gh` command line tool.

- GitSense never stores a GitHub token: `gh auth login` owns the credentials.
- Every function raises GitError when gh is missing or not signed in.
"""

from __future__ import annotations

import json
from typing import Any

from .gitcmd import NETWORK_TIMEOUT, GitError, find_tool, repo_lock, run

_PR_FIELDS = (
    "number,title,headRefName,baseRefName,author,isDraft,url,state,updatedAt,"
    "reviewDecision,statusCheckRollup,isCrossRepository,headRepositoryOwner,mergeable"
)


def _gh(args: list[str], *, cwd: str | None = None, timeout: float = 60, input_text: str | None = None) -> str:
    if find_tool("gh") is None:
        raise GitError("The GitHub CLI (gh) is not installed. Install it from https://cli.github.com")
    return run(["gh", *args], cwd=cwd, timeout=timeout, input_text=input_text).stdout


def status() -> dict[str, Any]:
    """Whether gh is installed and signed in, and as whom."""
    if find_tool("gh") is None:
        return {"installed": False, "authenticated": False, "user": None}
    res = run(["gh", "api", "user", "--jq", ".login"], check=False, timeout=30)
    user = res.stdout.strip() if res.returncode == 0 else None
    return {"installed": True, "authenticated": bool(user), "user": user or None}


def _checks_summary(rollup: list[dict[str, Any]] | None) -> str | None:
    if not rollup:
        return None
    states = []
    for check in rollup:
        state = (check.get("conclusion") or check.get("state") or check.get("status") or "").upper()
        states.append(state)
    if any(s in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED") for s in states):
        return "failing"
    if any(s in ("PENDING", "IN_PROGRESS", "QUEUED", "EXPECTED", "WAITING", "") for s in states):
        return "pending"
    return "passing"


def _shape_pr(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "head": pr.get("headRefName"),
        "base": pr.get("baseRefName"),
        "author": (pr.get("author") or {}).get("login"),
        "draft": bool(pr.get("isDraft")),
        "url": pr.get("url"),
        "state": pr.get("state"),
        "updated": pr.get("updatedAt"),
        "review": pr.get("reviewDecision") or None,
        "checks": _checks_summary(pr.get("statusCheckRollup")),
        "fork": bool(pr.get("isCrossRepository")),
        "head_owner": (pr.get("headRepositoryOwner") or {}).get("login"),
        "mergeable": pr.get("mergeable"),
    }


def list_prs(repo: str, slug: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    args = ["pr", "list", "--state", "open", "--limit", str(limit), "--json", _PR_FIELDS]
    if slug:
        args += ["--repo", slug]
    try:
        data = json.loads(_gh(args, cwd=repo) or "[]")
    except GitError as exc:
        if "Could not resolve to a Repository" in exc.message:
            raise GitError(
                "The GitHub account signed in to gh cannot see this repository. "
                "Switch accounts with `gh auth switch`, or sign in with `gh auth login`.",
                stderr=exc.stderr,
                code=exc.code,
            ) from exc
        raise
    return [_shape_pr(pr) for pr in data]


def create_pr(repo: str, a: dict[str, Any]) -> dict[str, Any]:
    title = (a.get("title") or "").strip()
    if not title:
        raise GitError("A pull request title is required")
    args = ["pr", "create", "--title", title, "--body", a.get("body") or ""]
    for key, flag in (("base", "--base"), ("head", "--head"), ("slug", "--repo")):
        value = a.get(key)
        if value:
            if str(value).startswith("-"):
                raise GitError(f"Invalid {key}")
            args += [flag, str(value)]
    if a.get("draft"):
        args.append("--draft")
    out = _gh(args, cwd=repo, timeout=NETWORK_TIMEOUT)
    url = next((ln.strip() for ln in reversed(out.splitlines()) if ln.startswith("http")), "")
    return {"url": url, "output": out.strip()}


def checkout_pr(repo: str, number: int) -> dict[str, Any]:
    # It switches branches, so it holds the lock that every other action holds.
    with repo_lock(repo):
        out = _gh(["pr", "checkout", str(int(number))], cwd=repo, timeout=NETWORK_TIMEOUT)
    return {"output": out.strip()}


def list_repos(owner: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Repositories the signed-in user (or a named owner) has on GitHub."""
    args = ["repo", "list"]
    if owner:
        if owner.startswith("-"):
            raise GitError("Invalid owner")
        args.append(owner)
    args += [
        "--limit",
        str(limit),
        "--json",
        "nameWithOwner,description,isPrivate,isFork,isArchived,updatedAt,sshUrl,url",
    ]
    data = json.loads(_gh(args, timeout=NETWORK_TIMEOUT) or "[]")
    return [
        {
            "slug": r.get("nameWithOwner"),
            "description": r.get("description") or "",
            "private": bool(r.get("isPrivate")),
            "fork": bool(r.get("isFork")),
            "archived": bool(r.get("isArchived")),
            "updated": r.get("updatedAt"),
            "ssh_url": r.get("sshUrl"),
            "url": r.get("url"),
        }
        for r in data
    ]


def list_orgs() -> list[str]:
    out = _gh(["api", "user/orgs", "--jq", ".[].login"], timeout=30)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]
