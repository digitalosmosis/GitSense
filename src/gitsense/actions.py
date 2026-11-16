"""Every operation that changes a repository.

- perform() dispatches one named action with a dict of arguments.
- A merge, rebase, cherry-pick, revert, pull or stash apply that stops on
  conflicts is reported as {"conflict": True}, not as an error.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from . import patch, repo as repo_mod
from .gitcmd import NETWORK_TIMEOUT, GitError, git, git_result, locked, protected


def _name(value: Any, what: str = "name") -> str:
    """Validate a ref, remote or branch name coming from the UI."""
    if not isinstance(value, str) or not value.strip():
        raise GitError(f"A {what} is required")
    value = value.strip()
    if value.startswith("-"):
        raise GitError(f"Invalid {what}: {value}")
    if any(ch in value for ch in "\0\n\r"):
        raise GitError(f"Invalid {what}")
    return value


def _ref(value: Any, what: str = "branch") -> str:
    """Validate a branch or tag name that is about to be used inside a refspec."""
    value = _name(value, what)
    if value.startswith("+") or ".." in value or any(ch in value for ch in " :~^?*[\\\t"):
        raise GitError(f"Invalid {what}: {value}")
    return value


def _paths(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise GitError("No files given")
    out = []
    for p in value:
        if not isinstance(p, str) or not p or "\0" in p:
            raise GitError("Invalid file path")
        out.append(p)
    return out


def _has_head(repo: str) -> bool:
    return git_result(repo, ["rev-parse", "-q", "--verify", "HEAD^{commit}"]).returncode == 0


def _in_conflict(repo: str) -> bool:
    if repo_mod.operation_state(repo):
        return True
    out = git_result(repo, ["diff", "--name-only", "--diff-filter=U", "-z"]).stdout
    return bool(out.strip("\0"))


def _run_may_conflict(
    repo: str, args: list[str], *, timeout: float = 120, fresh: bool = True
) -> dict[str, Any]:
    """Run a command that may stop on conflicts.

    - fresh is False for continue and skip, which run inside an operation.
    - A fresh command that fails while another operation is already stopped
      is a plain error, not a new conflict.
    """
    busy = fresh and repo_mod.operation_state(repo) is not None
    res = git_result(repo, args, write=True, timeout=timeout)
    text = (res.stdout + res.stderr).strip()
    if res.returncode == 0:
        return {"output": text}
    if not busy and _in_conflict(repo):
        return {"output": text, "conflict": True}
    raise GitError(_short(text) or f"git {args[0]} failed", stdout=res.stdout, stderr=res.stderr)


def _short(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    marked = [ln for ln in lines if ln.startswith(("fatal:", "error:", "CONFLICT", "! ", "hint:"))]
    return "\n".join((marked or lines)[:12])


def _w(repo: str, args: list[str], **kw: Any) -> dict[str, Any]:
    res = git_result(repo, args, write=True, **kw)
    text = (res.stdout + res.stderr).strip()
    if res.returncode != 0:
        raise GitError(_short(text) or f"git {args[0]} failed", stdout=res.stdout, stderr=res.stderr)
    return {"output": text}


# -- staging --------------------------------------------------------------------


def stage(repo: str, a: dict) -> dict:
    return _w(repo, ["add", "-A", "--", *repo_mod.literal(_paths(a.get("paths")))])


def unstage(repo: str, a: dict) -> dict:
    paths = repo_mod.literal(_paths(a.get("paths")))
    if _has_head(repo):
        return _w(repo, ["restore", "--staged", "--", *paths])
    # -f: the index may differ from the working file; --cached leaves the file alone.
    return _w(repo, ["rm", "--cached", "-r", "-q", "-f", "--", *paths])


def stage_all(repo: str, a: dict) -> dict:
    return _w(repo, ["add", "-A"])


def unstage_all(repo: str, a: dict) -> dict:
    if _has_head(repo):
        return _w(repo, ["reset", "-q"])
    return _w(repo, ["rm", "--cached", "-r", "-q", "-f", "."])


def discard(repo: str, a: dict) -> dict:
    """Throw away working tree changes to the given files."""
    paths = _paths(a.get("paths"))
    raw = git(repo, ["ls-files", "--others", "--exclude-standard", "-z", "--", *repo_mod.literal(paths)])
    untracked = [p for p in raw.split("\0") if p]
    # A path is restored when it holds any tracked file, even a folder that also holds untracked ones.
    raw = git(repo, ["ls-files", "-z", "--", *repo_mod.literal(paths)])
    known = [p for p in raw.split("\0") if p]
    tracked = [
        p for p in paths
        if any(k == p or k.startswith(p.rstrip("/") + "/") for k in known)
    ]
    out = []
    if tracked:
        out.append(_w(repo, ["restore", "--worktree", "--", *repo_mod.literal(tracked)])["output"])
    if untracked:
        out.append(_w(repo, ["clean", "-f", "-d", "-q", "--", *repo_mod.literal(untracked)])["output"])
    return {"output": "\n".join(o for o in out if o)}


def _in_the_way(repo: str) -> list[str]:
    """Untracked paths that `reset --hard` would delete to bring back a tracked file.

    - A folder where a tracked file was deleted.
    - A file or link where a tracked folder was deleted.
    - Only paths holding untracked or ignored files count. Staged ones are what Discard all discards.
    """
    # --no-renames: the source of a staged rename is a deleted path too.
    raw = git(repo, ["diff", "HEAD", "--no-renames", "--name-only", "--diff-filter=D", "-z"])
    found = set()
    for rel in filter(None, raw.split("\0")):
        parts = rel.split("/")
        for n in range(1, len(parts) + 1):
            here = os.path.join(repo, *parts[:n])
            if not os.path.lexists(here):
                break
            if n == len(parts) or os.path.islink(here) or not os.path.isdir(here):
                found.add("/".join(parts[:n]))
                break
    if not found:
        return []
    # On a case-insensitive file system "notes/x" sits in the way of "Notes", so match without case.
    icase = git_result(repo, ["config", "--bool", "core.ignorecase"]).stdout.strip() == "true"
    magic = ":(literal,icase)" if icase else ":(literal)"
    fold = str.casefold if icase else str
    # Without --exclude-standard, ignored files are listed too: reset deletes them as well.
    raw = git(repo, ["ls-files", "--others", "-z", "--", *(magic + f for f in sorted(found))])
    loose = [fold(p) for p in raw.split("\0") if p]
    return sorted(
        f for f in found if any(p == fold(f) or p.startswith(fold(f) + "/") for p in loose)
    )


def discard_all(repo: str, a: dict) -> dict:
    out = []
    keep_untracked = not a.get("include_untracked", True)
    if _has_head(repo):
        blocking = _in_the_way(repo) if keep_untracked else []
        if blocking:
            raise GitError(
                "Discarding would delete untracked files in the way of tracked ones: "
                + ", ".join(blocking[:5])
                + ". Move them, or also delete untracked files."
            )
        out.append(_w(repo, ["reset", "--hard", "-q", "HEAD"])["output"])
    else:
        out.append(_w(repo, ["rm", "--cached", "-r", "-q", "-f", "--ignore-unmatch", "."])["output"])
    if not keep_untracked:
        out.append(_w(repo, ["clean", "-f", "-d", "-q"])["output"])
    return {"output": "\n".join(o for o in out if o)}


def apply_hunk(repo: str, a: dict) -> dict:
    """Stage, unstage or discard one hunk, or only some of its lines.

    - The diff is recomputed and the hunk header must match what the UI showed.
    - mode is "stage", "unstage" or "discard".
    """
    path = _paths(a.get("path"))[0]
    mode = a.get("mode")
    if mode not in ("stage", "unstage", "discard"):
        raise GitError("Unknown hunk action")
    kind = "staged" if mode == "unstage" else "unstaged"
    text = repo_mod.raw_file_diff(repo, kind, path, orig_path=a.get("orig_path"))
    parsed = patch.parse_diff(text)
    if parsed["binary"] or parsed["combined"]:
        raise GitError("This file cannot be staged by hunk")
    if "�" in text:
        # Bytes that are not UTF-8 were replaced when decoding, so a patch would corrupt them.
        raise GitError("This file is not UTF-8 text, so it cannot be staged by hunk or line")
    try:
        index = int(a.get("hunk"))
        hunk = parsed["hunks"][index]
    except (TypeError, ValueError, IndexError):
        raise GitError("The file changed on disk. Refresh and try again.") from None
    if a.get("header") and a["header"] != hunk["header"]:
        raise GitError("The file changed on disk. Refresh and try again.")
    shown = a.get("expect")
    if isinstance(shown, str) and shown != "\n".join(ln["t"] + ln["s"] for ln in hunk["lines"]):
        # Same header but different text: the line numbers the UI sent are stale.
        raise GitError("The file changed on disk. Refresh and try again.")
    lines = a.get("lines")
    selected = {int(n) for n in lines} if isinstance(lines, list) else None
    reverse = mode in ("unstage", "discard")
    body = patch.build_patch(parsed["header"], hunk, selected, reverse=reverse)
    if not body:
        raise GitError("No changed lines selected")
    args = ["apply", "--whitespace=nowarn"]
    if mode in ("stage", "unstage"):
        args.append("--cached")
    if reverse:
        args.append("--reverse")
    res = git_result(repo, [*args, "-"], input_text=body, write=True)
    if res.returncode != 0:
        raise GitError(_short(res.stderr) or "The change could not be applied")
    return {"output": ""}


# -- commits --------------------------------------------------------------------


def commit(repo: str, a: dict) -> dict:
    message = (a.get("message") or "").strip()
    amend = bool(a.get("amend"))
    if not message and not amend:
        raise GitError("A commit message is required")
    if a.get("stage_all"):
        _w(repo, ["add", "-A"])
    # The message comes from a text box, so a line starting with '#' is not a comment.
    args = ["commit", "--cleanup=whitespace"]
    if amend:
        args.append("--amend")
    if a.get("no_verify"):
        args.append("--no-verify")
    if message:
        args += ["-F", "-"]
        return _w(repo, args, input_text=message + "\n")
    return _w(repo, [*args, "--no-edit"])


def cherry_pick(repo: str, a: dict) -> dict:
    shas = [_name(s, "commit") for s in (a.get("shas") or [a.get("sha")])]
    return _run_may_conflict(repo, ["cherry-pick", *shas])


def revert(repo: str, a: dict) -> dict:
    sha = _name(a.get("sha"), "commit")
    parents = git(repo, ["rev-list", "--parents", "-n1", sha, "--"]).split()[1:]
    args = ["revert", "--no-edit"]
    if len(parents) > 1:
        args += ["-m", "1"]
    return _run_may_conflict(repo, [*args, sha])


def reset(repo: str, a: dict) -> dict:
    mode = a.get("mode", "mixed")
    if mode not in ("soft", "mixed", "hard"):
        raise GitError("Unknown reset mode")
    return _w(repo, ["reset", f"--{mode}", _name(a.get("sha"), "commit"), "--"])


# -- branches -------------------------------------------------------------------


def checkout(repo: str, a: dict) -> dict:
    ref = _name(a.get("ref"), "branch")
    if a.get("detach"):
        return _w(repo, ["switch", "--detach", ref])
    return _w(repo, ["switch", ref])


def checkout_remote(repo: str, a: dict) -> dict:
    """Check out a remote branch, creating a local tracking branch when needed."""
    full = _name(a.get("full"), "remote branch")
    local = _name(a.get("name") or full.partition("/")[2], "branch")
    exists = git_result(repo, ["rev-parse", "-q", "--verify", f"refs/heads/{local}"]).returncode == 0
    if exists:
        return _w(repo, ["switch", local])
    return _w(repo, ["switch", "-c", local, "--track", full])


def branch_create(repo: str, a: dict) -> dict:
    name = _name(a.get("name"), "branch name")
    start = [_name(a["start"], "start point")] if a.get("start") else []
    if a.get("checkout", True):
        return _w(repo, ["switch", "-c", name, *start])
    return _w(repo, ["branch", name, *start])


def branch_delete(repo: str, a: dict) -> dict:
    flag = "-D" if a.get("force") else "-d"
    return _w(repo, ["branch", flag, "--", _name(a.get("name"), "branch")])


def branch_rename(repo: str, a: dict) -> dict:
    return _w(
        repo,
        ["branch", "-m", "--", _name(a.get("old"), "branch"), _name(a.get("new"), "new name")],
    )


def remote_branch_delete(repo: str, a: dict) -> dict:
    return _w(
        repo,
        ["push", _name(a.get("remote"), "remote"), "--delete", f"refs/heads/{_ref(a.get('name'), 'branch')}"],
        timeout=NETWORK_TIMEOUT,
    )


def set_upstream(repo: str, a: dict) -> dict:
    upstream = _name(a.get("upstream"), "upstream")
    return _w(
        repo, ["branch", f"--set-upstream-to={upstream}", _name(a.get("branch"), "branch")]
    )


def merge(repo: str, a: dict) -> dict:
    ref = _name(a.get("ref"), "branch")
    mode = a.get("mode", "default")
    flags = {"default": [], "no-ff": ["--no-ff"], "ff-only": ["--ff-only"], "squash": ["--squash"]}
    if mode not in flags:
        raise GitError("Unknown merge mode")
    return _run_may_conflict(repo, ["merge", "--no-edit", *flags[mode], ref])


def rebase(repo: str, a: dict) -> dict:
    return _run_may_conflict(repo, ["rebase", _name(a.get("onto"), "branch")])


def _operation(repo: str) -> str:
    state = repo_mod.operation_state(repo)
    if not state:
        raise GitError("No merge, rebase, cherry-pick or revert is in progress")
    return state["kind"]


def op_continue(repo: str, a: dict) -> dict:
    kind = _operation(repo)
    args = {
        # strip drops the "# Conflicts:" comment lines git adds to MERGE_MSG.
        "merge": ["commit", "--no-edit", "--cleanup=strip"],
        "rebase": ["rebase", "--continue"],
        "cherry-pick": ["cherry-pick", "--continue"],
        "revert": ["revert", "--continue"],
        "am": ["am", "--continue"],
    }.get(kind)
    if not args:
        raise GitError(f"Cannot continue a {kind} from here")
    return _run_may_conflict(repo, args, fresh=False)


def op_abort(repo: str, a: dict) -> dict:
    kind = _operation(repo)
    args = {
        "merge": ["merge", "--abort"],
        "rebase": ["rebase", "--abort"],
        "cherry-pick": ["cherry-pick", "--abort"],
        "revert": ["revert", "--abort"],
        "am": ["am", "--abort"],
        "bisect": ["bisect", "reset"],
    }[kind]
    return _w(repo, args)


def op_skip(repo: str, a: dict) -> dict:
    kind = _operation(repo)
    if kind not in ("rebase", "cherry-pick", "revert", "am"):
        raise GitError(f"Cannot skip during a {kind}")
    return _run_may_conflict(repo, [kind, "--skip"], fresh=False)


def resolve(repo: str, a: dict) -> dict:
    """Resolve a conflicted file by taking one side, then stage it."""
    path = _paths(a.get("path"))[0]
    side = a.get("side")
    if side not in ("ours", "theirs"):
        raise GitError("Unknown side")
    spec = repo_mod.literal([path])
    # Index stage 2 is our version and stage 3 theirs.
    # - A missing stage means that side deleted the file, so taking it deletes the file.
    unmerged = git(repo, ["ls-files", "-u", "-z", "--", *spec])
    stages = {e.split("\t", 1)[0].rsplit(" ", 1)[-1] for e in unmerged.split("\0") if e}
    if stages and ("2" if side == "ours" else "3") not in stages:
        return _w(repo, ["rm", "-q", "--", *spec])
    _w(repo, ["checkout", f"--{side}", "--", *spec])
    return _w(repo, ["add", "--", *spec])


def mark_resolved(repo: str, a: dict) -> dict:
    return _w(repo, ["add", "-A", "--", *repo_mod.literal(_paths(a.get("paths")))])


# -- remotes --------------------------------------------------------------------


def fetch(repo: str, a: dict) -> dict:
    args = ["fetch"]
    if a.get("prune", True):
        args.append("--prune")
    if a.get("remote"):
        args.append(_name(a["remote"], "remote"))
    else:
        args.append("--all")
    return _w(repo, args, timeout=NETWORK_TIMEOUT)


def pull(repo: str, a: dict) -> dict:
    """Fetch the upstream, then bring the branch up to date with it.

    - The two steps run separately, so a cancel can stop the network part.
    - Once the branch starts to change the pull is protected and runs to the end.
    """
    mode = a.get("mode", "ff-only")
    steps = {
        "ff-only": ["merge", "--ff-only", "@{upstream}"],
        "rebase": ["rebase"],  # no argument: rebases onto the upstream, with fork-point, like pull
        "merge": ["merge", "--no-edit", "@{upstream}"],
    }
    if mode not in steps:
        raise GitError("Unknown pull mode")
    branch = repo_mod.head_info(repo)["branch"]
    if not branch:
        raise GitError("Check out a branch before pulling")
    branch = _ref(branch, "branch")
    remote = git_result(repo, ["config", "--get", f"branch.{branch}.remote"]).stdout.strip()
    merge_ref = git_result(repo, ["config", "--get", f"branch.{branch}.merge"]).stdout.strip()
    if not remote or not merge_ref:
        raise GitError(f"{branch} has no upstream branch to pull from")
    if remote != ".":
        _w(repo, ["fetch", _name(remote, "remote")], timeout=NETWORK_TIMEOUT)
    with protected():
        return _run_may_conflict(repo, steps[mode])


def _default_remote(repo: str) -> str:
    names = [r["name"] for r in repo_mod.list_remotes(repo)]
    if not names:
        raise GitError("This repository has no remote. Add one first.")
    return "origin" if "origin" in names else names[0]


def push(repo: str, a: dict) -> dict:
    head = repo_mod.head_info(repo)
    branch = a.get("branch") or head["branch"]
    if not branch:
        raise GitError("Check out a branch before pushing")
    branch = _ref(branch, "branch")
    args = ["push"]
    if a.get("force"):
        args.append("--force-with-lease")
    # Read the upstream from config: "origin/x" cannot be split safely when a
    # remote name contains a slash, and an upstream may be a local branch (".").
    tracked_remote = git_result(repo, ["config", "--get", f"branch.{branch}.remote"]).stdout.strip()
    tracked_ref = git_result(repo, ["config", "--get", f"branch.{branch}.merge"]).stdout.strip()
    has_upstream = bool(tracked_remote) and tracked_remote != "." and tracked_ref.startswith("refs/heads/")
    if a.get("remote") or not has_upstream:
        remote = _name(a.get("remote") or _default_remote(repo), "remote")
        target = _ref(a.get("remote_branch") or branch, "branch")
        args += ["-u", remote, f"refs/heads/{branch}:refs/heads/{target}"]
    else:
        # The config value is checked like any other remote, so it cannot pass as an option.
        args += [_name(tracked_remote, "remote"), f"refs/heads/{branch}:{tracked_ref}"]
    return _w(repo, args, timeout=NETWORK_TIMEOUT)


def remote_add(repo: str, a: dict) -> dict:
    return _w(repo, ["remote", "add", _name(a.get("name"), "remote name"), _name(a.get("url"), "URL")])


def remote_remove(repo: str, a: dict) -> dict:
    return _w(repo, ["remote", "remove", _name(a.get("name"), "remote")])


def remote_set_url(repo: str, a: dict) -> dict:
    return _w(
        repo, ["remote", "set-url", _name(a.get("name"), "remote"), _name(a.get("url"), "URL")]
    )


# -- tags -----------------------------------------------------------------------


def tag_create(repo: str, a: dict) -> dict:
    name = _name(a.get("name"), "tag name")
    target = [_name(a["sha"], "commit")] if a.get("sha") else []
    message = (a.get("message") or "").strip()
    if message:
        return _w(repo, ["tag", "-a", "-F", "-", name, *target], input_text=message + "\n")
    return _w(repo, ["tag", name, *target])


def tag_delete(repo: str, a: dict) -> dict:
    return _w(repo, ["tag", "-d", "--", _name(a.get("name"), "tag")])


def tag_push(repo: str, a: dict) -> dict:
    remote = _name(a.get("remote") or _default_remote(repo), "remote")
    name = _ref(a.get("name"), "tag")
    return _w(repo, ["push", remote, f"refs/tags/{name}"], timeout=NETWORK_TIMEOUT)


def tag_delete_remote(repo: str, a: dict) -> dict:
    remote = _name(a.get("remote") or _default_remote(repo), "remote")
    name = _ref(a.get("name"), "tag")
    return _w(repo, ["push", remote, "--delete", f"refs/tags/{name}"], timeout=NETWORK_TIMEOUT)


# -- stashes --------------------------------------------------------------------


def stash_save(repo: str, a: dict) -> dict:
    args = ["stash", "push"]
    if a.get("include_untracked", True):
        args.append("--include-untracked")
    if a.get("keep_index"):
        args.append("--keep-index")
    message = (a.get("message") or "").strip()
    if message:
        args += ["-m", message]
    return _w(repo, args)


def _stash_selector(repo: str, sha: Any) -> str:
    sha = _name(sha, "stash")
    for stash in repo_mod.list_stashes(repo):
        if stash["sha"] == sha:
            return f"stash@{{{stash['index']}}}"
    raise GitError("That stash no longer exists")


def stash_apply(repo: str, a: dict) -> dict:
    return _run_may_conflict(repo, ["stash", "apply", _stash_selector(repo, a.get("sha"))])


def stash_pop(repo: str, a: dict) -> dict:
    sha = a.get("sha")
    if not sha:
        stashes = repo_mod.list_stashes(repo)
        if not stashes:
            raise GitError("There are no stashes")
        sha = stashes[0]["sha"]
    return _run_may_conflict(repo, ["stash", "pop", _stash_selector(repo, sha)])


def stash_drop(repo: str, a: dict) -> dict:
    return _w(repo, ["stash", "drop", _stash_selector(repo, a.get("sha"))])


ACTIONS: dict[str, Callable[[str, dict], dict]] = {
    "stage": stage,
    "unstage": unstage,
    "stage_all": stage_all,
    "unstage_all": unstage_all,
    "discard": discard,
    "discard_all": discard_all,
    "apply_hunk": apply_hunk,
    "commit": commit,
    "cherry_pick": cherry_pick,
    "revert": revert,
    "reset": reset,
    "checkout": checkout,
    "checkout_remote": checkout_remote,
    "branch_create": branch_create,
    "branch_delete": branch_delete,
    "branch_rename": branch_rename,
    "remote_branch_delete": remote_branch_delete,
    "set_upstream": set_upstream,
    "merge": merge,
    "rebase": rebase,
    "op_continue": op_continue,
    "op_abort": op_abort,
    "op_skip": op_skip,
    "resolve": resolve,
    "mark_resolved": mark_resolved,
    "fetch": fetch,
    "pull": pull,
    "push": push,
    "remote_add": remote_add,
    "remote_remove": remote_remove,
    "remote_set_url": remote_set_url,
    "tag_create": tag_create,
    "tag_delete": tag_delete,
    "tag_push": tag_push,
    "tag_delete_remote": tag_delete_remote,
    "stash_save": stash_save,
    "stash_apply": stash_apply,
    "stash_pop": stash_pop,
    "stash_drop": stash_drop,
}


def perform(repo: str, action: str, args: dict[str, Any] | None) -> dict[str, Any]:
    handler = ACTIONS.get(action)
    if handler is None:
        raise GitError(f"Unknown action: {action}")
    # Held for the whole action, so what it read (a stash index, a hunk) is
    # still true when it writes. The lock is reentrant for the git calls inside.
    with locked(repo):
        return handler(repo, args or {})
