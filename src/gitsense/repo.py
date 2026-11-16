"""Read-only views of a repository: refs, status, history, commits and diffs."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from . import graph, patch
from .gitcmd import GitError, git, git_result

US = "\x1f"  # field separator in git format strings
RS = "\x1e"  # record separator
MAX_STATUS_FILES = 5000

_GITHUB_RE = re.compile(
    r"^(?:https?://(?:[^@/]+@)?|ssh://(?:[^@/]+@)?|git://|(?:[^@/]+@)?)"
    r"(?P<host>[^/:]+)(?::\d+)?[/:](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
_REMOTE_LINE_RE = re.compile(r"^([^\t]+)\t(.*) \((fetch|push)\)(?: \[[^\]]*\])?$")


def literal(paths: list[str]) -> list[str]:
    """Pathspecs that match exactly these paths.

    - Glob characters such as * ? [ are not expanded.
    - A leading ':' is not read as pathspec magic.
    """
    return [f":(literal){p}" for p in paths]


def repo_root(path: str | Path) -> str:
    """Return the working tree root for a path, or raise GitError."""
    p = Path(path).expanduser()
    if not p.is_dir():
        raise GitError(f"Not a folder: {path}")
    # Only the newline: a folder name may end in a space.
    out = git(p, ["rev-parse", "--show-toplevel"]).rstrip("\n")
    if not out:
        raise GitError(f"Not a git working tree: {path}")
    return os.path.realpath(out)


def is_repo(path: str | Path) -> bool:
    p = Path(path)
    return (p / ".git").exists()


def scan_for_repos(root: str | Path, depth: int = 3, limit: int = 500) -> list[str]:
    """Find git working trees under a folder without descending into them."""
    found: list[str] = []
    base = Path(root).expanduser()
    if not base.is_dir():
        raise GitError(f"Not a folder: {root}")
    skip = {"node_modules", ".venv", "venv", "__pycache__", "Library", ".Trash", "target"}

    def walk(folder: Path, level: int) -> None:
        if len(found) >= limit:
            return
        if is_repo(folder):
            found.append(os.path.realpath(folder))
            return
        if level >= depth:
            return
        try:
            children = sorted(
                (c for c in folder.iterdir() if c.is_dir() and not c.is_symlink()),
                key=lambda c: c.name.lower(),
            )
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or child.name in skip:
                continue
            walk(child, level + 1)

    walk(base, 0)
    return found


# -- remotes and hosting ----------------------------------------------------


def parse_remote_url(url: str) -> dict[str, str] | None:
    """Split a remote URL into host, owner and repo. Handles ssh host aliases."""
    m = _GITHUB_RE.match(url.strip())
    if not m:
        return None
    host = m.group("host")
    # ssh config aliases such as "github.com-work" still point at github.com
    alias = "." not in host and "github" in host.lower()  # e.g. "github-work"
    if host.startswith("github.com-") or host.endswith(".github.com") or alias:
        host = "github.com"
    return {"host": host, "owner": m.group("owner"), "repo": m.group("repo")}


def github_info(remotes: list[dict[str, Any]]) -> dict[str, str] | None:
    ordered = sorted(remotes, key=lambda r: (r["name"] != "origin", r["name"]))
    for remote in ordered:
        info = parse_remote_url(remote.get("url") or "")
        if info and ("github" in info["host"].lower() or info["host"].lower().endswith(".ghe.com")):
            slug = f"{info['owner']}/{info['repo']}"
            return {
                "host": info["host"],
                "slug": slug,
                "remote": remote["name"],
                "url": f"https://{info['host']}/{slug}",
            }
    return None


def list_remotes(repo: str) -> list[dict[str, Any]]:
    remotes: dict[str, dict[str, Any]] = {}
    for line in git(repo, ["remote", "-v"]).splitlines():
        # "name<TAB>url (fetch)"; the URL may hold spaces, and partial clones add " [filter]".
        m = _REMOTE_LINE_RE.match(line)
        if not m:
            continue
        name, url, kind = m.group(1), m.group(2), m.group(3)
        entry = remotes.setdefault(name, {"name": name, "url": "", "push_url": "", "branches": []})
        if kind == "fetch":
            entry["url"] = url
        else:
            entry["push_url"] = url
    return list(remotes.values())


# -- status -------------------------------------------------------------------


def parse_status(raw: str) -> dict[str, Any]:
    """Parse `git status --porcelain=v2 --branch -z`."""
    result: dict[str, Any] = {
        "branch": None,
        "oid": None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
        "staged": [],
        "unstaged": [],
        "conflicted": [],
        "truncated": False,
    }
    tokens = raw.split("\0")
    i = 0
    count = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        if tok.startswith("# "):
            key, _, value = tok[2:].partition(" ")
            if key == "branch.oid":
                result["oid"] = None if value == "(initial)" else value
            elif key == "branch.head":
                result["branch"] = None if value == "(detached)" else value
            elif key == "branch.upstream":
                result["upstream"] = value
            elif key == "branch.ab":
                for part in value.split():
                    if part.startswith("+"):
                        result["ahead"] = int(part[1:])
                    elif part.startswith("-"):
                        result["behind"] = int(part[1:])
            continue
        count += 1
        if count > MAX_STATUS_FILES:
            result["truncated"] = True
            if tok[:1] == "2":
                i += 1
            continue
        kind = tok[0]
        if kind == "1":
            fields = tok.split(" ", 8)
            xy, path = fields[1], fields[8]
            submodule = fields[2] != "N..."
            _add_changed(result, xy, path, None, submodule)
        elif kind == "2":
            fields = tok.split(" ", 9)
            xy, path = fields[1], fields[9]
            orig = tokens[i] if i < len(tokens) else None
            i += 1
            _add_changed(result, xy, path, orig, fields[2] != "N...")
        elif kind == "u":
            fields = tok.split(" ", 10)
            result["conflicted"].append({"path": fields[10], "status": "U", "xy": fields[1]})
        elif kind == "?":
            result["unstaged"].append({"path": tok[2:], "status": "?"})
    return result


def _add_changed(
    result: dict[str, Any], xy: str, path: str, orig: str | None, submodule: bool
) -> None:
    x, y = xy[0], xy[1]
    if x != ".":
        entry: dict[str, Any] = {"path": path, "status": x}
        if orig and x in "RC":
            entry["orig_path"] = orig
        if submodule:
            entry["submodule"] = True
        result["staged"].append(entry)
    if y != ".":
        entry = {"path": path, "status": y}
        if orig and y in "RC":
            entry["orig_path"] = orig
        if submodule:
            entry["submodule"] = True
        result["unstaged"].append(entry)


def status(repo: str, *, untracked: str = "all") -> dict[str, Any]:
    raw = git(
        repo,
        ["status", "--porcelain=v2", "--branch", "-z", f"--untracked-files={untracked}"],
    )
    return parse_status(raw)


def git_dir(repo: str) -> Path:
    out = git(repo, ["rev-parse", "--absolute-git-dir"]).strip()
    return Path(out)


def operation_state(repo: str) -> dict[str, Any] | None:
    """Report a merge, rebase, cherry-pick, revert, am or bisect that is in progress."""
    try:
        gd = git_dir(repo)
    except GitError:
        return None

    def read(name: str) -> str:
        try:
            return (gd / name).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    for folder in ("rebase-merge", "rebase-apply"):
        if (gd / folder).is_dir():
            if folder == "rebase-apply" and (gd / folder / "applying").exists():
                return {"kind": "am"}  # `git am` shares the folder with rebase
            head_name = read(f"{folder}/head-name").removeprefix("refs/heads/")
            step = read(f"{folder}/msgnum") or read(f"{folder}/next")
            total = read(f"{folder}/end") or read(f"{folder}/last")
            return {
                "kind": "rebase",
                "branch": head_name,
                "onto": read(f"{folder}/onto"),
                "step": int(step) if step.isdigit() else None,
                "total": int(total) if total.isdigit() else None,
            }
    if (gd / "MERGE_HEAD").exists():
        return {"kind": "merge", "target": read("MERGE_HEAD").splitlines()[0:1], "message": read("MERGE_MSG")}
    if (gd / "CHERRY_PICK_HEAD").exists():
        return {"kind": "cherry-pick", "target": read("CHERRY_PICK_HEAD").splitlines()[0:1]}
    if (gd / "REVERT_HEAD").exists():
        return {"kind": "revert", "target": read("REVERT_HEAD").splitlines()[0:1]}
    if (gd / "BISECT_LOG").exists():
        return {"kind": "bisect"}
    return None


# -- refs -----------------------------------------------------------------------


def _parse_track(track: str) -> tuple[int, int, bool]:
    ahead = behind = 0
    gone = "gone" in track
    m = re.search(r"ahead (\d+)", track)
    if m:
        ahead = int(m.group(1))
    m = re.search(r"behind (\d+)", track)
    if m:
        behind = int(m.group(1))
    return ahead, behind, gone


def list_refs(repo: str) -> dict[str, Any]:
    fmt = "%00".join(
        [
            "%(refname)",
            "%(objectname)",
            "%(*objectname)",
            "%(upstream:short)",
            "%(upstream:track)",
            "%(creatordate:unix)",
            "%(HEAD)",
            "%(objecttype)",
            "%(symref)",
        ]
    )
    out = git(repo, ["for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes", "refs/tags"])
    branches: list[dict[str, Any]] = []
    remote_branches: list[dict[str, Any]] = []
    tags: list[dict[str, Any]] = []
    default_branches: dict[str, str] = {}  # remote name -> branch its HEAD points at
    for line in out.split("\n"):
        parts = line.split("\0")
        if len(parts) < 9:
            continue
        refname, sha, peeled, upstream, track, date, head, objtype, symref = parts[:9]
        when = int(date) if date.isdigit() else 0
        if refname.startswith("refs/heads/"):
            ahead, behind, gone = _parse_track(track)
            branches.append(
                {
                    "name": refname[len("refs/heads/") :],
                    "sha": sha,
                    "upstream": upstream or None,
                    "ahead": ahead,
                    "behind": behind,
                    "gone": gone,
                    "current": head == "*",
                    "date": when,
                }
            )
        elif refname.startswith("refs/remotes/"):
            short = refname[len("refs/remotes/") :]
            remote, _, name = short.partition("/")
            if symref or name == "HEAD":
                # origin/HEAD is only a pointer; it names the remote's default branch
                prefix = f"refs/remotes/{remote}/"
                if symref.startswith(prefix):
                    default_branches[remote] = symref[len(prefix) :]
                continue
            remote_branches.append(
                {"remote": remote, "name": name, "full": short, "sha": sha, "date": when}
            )
        elif refname.startswith("refs/tags/"):
            tags.append(
                {
                    "name": refname[len("refs/tags/") :],
                    "sha": peeled or sha,
                    "annotated": objtype == "tag",
                    "date": when,
                }
            )
    tags.sort(key=lambda t: t["date"], reverse=True)
    return {
        "branches": branches,
        "remote_branches": remote_branches,
        "tags": tags,
        "default_branches": default_branches,
    }


def list_stashes(repo: str) -> list[dict[str, Any]]:
    res = git_result(
        repo, ["stash", "list", f"--format=%H{US}%P{US}%gd{US}%gs{US}%ct{RS}"]
    )
    if res.returncode != 0:
        return []
    stashes = []
    for record in res.stdout.split(RS):
        record = record.strip("\n")
        if not record:
            continue
        sha, parents, selector, subject, date = (record.split(US) + [""] * 5)[:5]
        m = re.search(r"\{(\d+)\}", selector)
        plist = parents.split()
        stashes.append(
            {
                "sha": sha,
                "index": int(m.group(1)) if m else len(stashes),
                "message": subject,
                "date": int(date) if date.isdigit() else 0,
                "parent": plist[0] if plist else None,
                "has_untracked": len(plist) >= 3,
            }
        )
    return stashes


def head_info(repo: str) -> dict[str, Any]:
    branch = git_result(repo, ["symbolic-ref", "-q", "--short", "HEAD"]).stdout.strip() or None
    res = git_result(repo, ["rev-parse", "-q", "--verify", "HEAD^{commit}"])
    sha = res.stdout.strip() if res.returncode == 0 else None
    return {
        "branch": branch,
        "sha": sha,
        "detached": branch is None and sha is not None,
        "unborn": sha is None,
    }


FINGERPRINT_STAT_LIMIT = 400


def fingerprint(repo: str) -> str:
    """A cheap hash that changes whenever refs, the index or the working tree change.

    - Status alone misses a second edit to an already-modified file.
    - So the size and mtime of each changed file, and of the index, are mixed in.
    """
    raw = git_result(repo, ["status", "--porcelain=v2", "--branch", "-z"]).stdout
    parts = [
        raw,
        git_result(repo, ["for-each-ref", "--format=%(objectname) %(refname)"]).stdout,
        str(operation_state(repo)),
    ]
    st = parse_status(raw)
    seen: set[str] = set()
    for entry in [*st["conflicted"], *st["unstaged"], *st["staged"]]:
        path = entry["path"]
        if path in seen or len(seen) >= FINGERPRINT_STAT_LIMIT:
            continue
        seen.add(path)
        try:
            info = os.stat(os.path.join(repo, path))
            parts.append(f"{path}:{info.st_mtime_ns}:{info.st_size}")
        except OSError:
            parts.append(f"{path}:gone")
    try:
        parts.append(str(os.stat(git_dir(repo) / "index").st_mtime_ns))
    except (OSError, GitError):
        pass
    return hashlib.sha1("\x00".join(parts).encode("utf-8", "replace")).hexdigest()[:16]


def overview(repo: str) -> dict[str, Any]:
    refs = list_refs(repo)
    remotes = list_remotes(repo)
    by_name = {r["name"]: r for r in remotes}
    for rb in refs["remote_branches"]:
        by_name.setdefault(
            rb["remote"], {"name": rb["remote"], "url": "", "push_url": "", "branches": []}
        )["branches"].append(rb)
    for name, entry in by_name.items():
        entry["default_branch"] = refs["default_branches"].get(name)
    st = status(repo)
    github = github_info(remotes)
    if github:
        github["default_branch"] = refs["default_branches"].get(github["remote"])
    return {
        "path": repo,
        "name": Path(repo).name,
        "head": head_info(repo),
        "branches": refs["branches"],
        "remotes": list(by_name.values()),
        "tags": refs["tags"],
        "stashes": list_stashes(repo),
        "status": st,
        "operation": operation_state(repo),
        "github": github,
        "fingerprint": fingerprint(repo),
    }


def summary(repo: str) -> dict[str, Any]:
    """The small per-repository card shown on the dashboard."""
    if not Path(repo).is_dir():
        return {"missing": True, "error": "Folder not found"}
    if not os.path.lexists(os.path.join(repo, ".git")):
        # git would otherwise report on a repository in a parent folder
        return {"missing": True, "error": "Not a Git repository"}
    try:
        st = status(repo, untracked="normal")
    except GitError as exc:
        return {"missing": True, "error": exc.message}
    head = None
    res = git_result(repo, ["log", "-1", f"--format=%H{US}%s{US}%an{US}%ct"])
    if res.returncode == 0 and res.stdout.strip():
        sha, subject, author, date = (res.stdout.strip("\n").split(US) + [""] * 4)[:4]
        head = {
            "sha": sha,
            "subject": subject,
            "author": author,
            "date": int(date) if date.isdigit() else 0,
        }
    remotes = list_remotes(repo)
    origin = next((r for r in remotes if r["name"] == "origin"), remotes[0] if remotes else None)
    return {
        "missing": False,
        "branch": st["branch"],
        "detached": st["branch"] is None and st["oid"] is not None,
        "upstream": st["upstream"],
        "ahead": st["ahead"],
        "behind": st["behind"],
        "staged": len(st["staged"]),
        "unstaged": len(st["unstaged"]),
        "conflicted": len(st["conflicted"]),
        "last_commit": head,
        "remote_url": origin["url"] if origin else None,
        "github": github_info(remotes),
        "operation": (operation_state(repo) or {}).get("kind"),
    }


# -- history --------------------------------------------------------------------

_LOG_FORMAT = US.join(["%H", "%P", "%an", "%ae", "%at", "%ct", "%s"]) + RS


def log(
    repo: str,
    *,
    limit: int = 2000,
    include_remotes: bool = True,
    wip: bool | None = None,
) -> dict[str, Any]:
    """History of every branch and tag, laid out as a graph.

    - A WIP row is added on top when the working tree has changes.
    - Each stash is shown as one row attached to the commit it was made on.
    """
    head = head_info(repo)
    args = ["log", "--date-order", f"-n{limit + 1}", f"--format={_LOG_FORMAT}", "--branches", "--tags"]
    if include_remotes:
        args.append("--remotes")
    if head["sha"]:
        args.append("HEAD")
    rows: list[dict[str, Any]] = []
    res = git_result(repo, [*args, "--"])
    if res.returncode == 0:
        for record in res.stdout.split(RS):
            record = record.lstrip("\n")
            if not record:
                continue
            f = (record.split(US) + [""] * 7)[:7]
            rows.append(
                {
                    "sha": f[0],
                    "parents": f[1].split(),
                    "author": f[2],
                    "email": f[3],
                    "date": int(f[4]) if f[4].isdigit() else 0,
                    "cdate": int(f[5]) if f[5].isdigit() else 0,
                    "subject": f[6],
                }
            )
    elif head["sha"]:
        raise GitError(res.stderr.strip() or "git log failed")
    has_more = len(rows) > limit
    rows = rows[:limit]

    index = {r["sha"]: i for i, r in enumerate(rows)}
    for stash in reversed(list_stashes(repo)):
        parent = stash["parent"]
        if parent not in index:
            continue
        at = index[parent]
        for i in range(at):
            if rows[i]["cdate"] <= stash["date"] and rows[i].get("kind") != "wip":
                at = i
                break
        rows.insert(
            at,
            {
                "sha": stash["sha"],
                "parents": [parent],
                "author": "",
                "email": "",
                "date": stash["date"],
                "cdate": stash["date"],
                "subject": stash["message"],
                "kind": "stash",
                "stash_index": stash["index"],
            },
        )
        index = {r["sha"]: i for i, r in enumerate(rows)}

    if wip is None:
        st = status(repo, untracked="normal")
        wip = bool(st["staged"] or st["unstaged"] or st["conflicted"])
    if wip:
        rows.insert(
            0,
            {
                "sha": "WIP",
                "parents": [head["sha"]] if head["sha"] else [],
                "author": "",
                "email": "",
                "date": 0,
                "cdate": 0,
                "subject": "Uncommitted changes",
                "kind": "wip",
            },
        )
    width = graph.layout(rows)
    return {"commits": rows, "width": width, "has_more": has_more, "head": head}


def empty_tree(repo: str) -> str:
    return git(repo, ["hash-object", "-t", "tree", "--stdin"], input_text="").strip()


def _parse_name_status(raw: str) -> list[dict[str, Any]]:
    tokens = raw.split("\0")
    files: list[dict[str, Any]] = []
    i = 0
    while i < len(tokens):
        code = tokens[i]
        i += 1
        if not code:
            continue
        letter = code[0]
        if letter in "RC" and i + 1 < len(tokens):
            files.append({"status": letter, "orig_path": tokens[i], "path": tokens[i + 1]})
            i += 2
        elif i < len(tokens):
            files.append({"status": letter, "path": tokens[i]})
            i += 1
    return files


def _parse_numstat(raw: str) -> dict[str, tuple[int | None, int | None]]:
    tokens = raw.split("\0")
    stats: dict[str, tuple[int | None, int | None]] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        parts = tok.split("\t", 2)  # a path may hold a tab
        if len(parts) < 3:
            continue
        added = int(parts[0]) if parts[0].isdigit() else None
        deleted = int(parts[1]) if parts[1].isdigit() else None
        path = parts[2]
        if path == "" and i + 1 < len(tokens):
            path = tokens[i + 1]  # rename: old path, then new path
            i += 2
        stats[path] = (added, deleted)
    return stats


def changed_files(repo: str, base: str, target: str) -> list[dict[str, Any]]:
    names = git(repo, ["diff", "--name-status", "-z", "-M", base, target, "--"])
    nums = git(repo, ["diff", "--numstat", "-z", "-M", base, target, "--"])
    files = _parse_name_status(names)
    stats = _parse_numstat(nums)
    for f in files:
        added, deleted = stats.get(f["path"], (None, None))
        f["added"] = added
        f["deleted"] = deleted
    return files


def commit_detail(repo: str, sha: str) -> dict[str, Any]:
    fmt = US.join(["%H", "%P", "%an", "%ae", "%at", "%cn", "%ce", "%ct", "%B"])
    raw = git(repo, ["show", "-s", f"--format={fmt}", sha, "--"])
    f = (raw.rstrip("\n").split(US) + [""] * 9)[:9]
    parents = f[1].split()
    message = f[8].strip("\n")
    subject, _, body = message.partition("\n")
    detail: dict[str, Any] = {
        "sha": f[0],
        "parents": parents,
        "author": f[2],
        "email": f[3],
        "date": int(f[4]) if f[4].isdigit() else 0,
        "committer": f[5],
        "committer_email": f[6],
        "cdate": int(f[7]) if f[7].isdigit() else 0,
        "subject": subject,
        "body": body.strip("\n"),
    }
    stash_shas = {s["sha"]: s for s in list_stashes(repo)}
    base = parents[0] if parents else empty_tree(repo)
    detail["base"] = base
    detail["files"] = changed_files(repo, base, f[0])
    if f[0] in stash_shas:
        detail["kind"] = "stash"
        detail["stash_index"] = stash_shas[f[0]]["index"]
        if len(parents) >= 3:
            # The third parent of a stash holds the untracked files.
            extra = changed_files(repo, empty_tree(repo), parents[2])
            for item in extra:
                item["status"] = "?"
                item["from_sha"] = parents[2]
            detail["files"].extend(extra)
    return detail


# -- diffs ----------------------------------------------------------------------


def is_untracked(repo: str, path: str) -> bool:
    out = git(repo, ["ls-files", "--others", "--exclude-standard", "-z", "--", *literal([path])])
    return any(tok == path for tok in out.split("\0"))


def raw_file_diff(
    repo: str,
    kind: str,
    path: str,
    *,
    sha: str | None = None,
    orig_path: str | None = None,
    context: int = 3,
    ignore_ws: bool = False,
) -> str:
    """Return the unified diff text for one file.

    - kind is "unstaged", "staged" or "commit" (commit needs sha).
    - Only commit diffs use textconv. Staged and unstaged hunks become patches, which need the real bytes.
    """
    # Fixed a/ and b/ prefixes, so diff.noprefix or diff.mnemonicPrefix cannot change
    # the paths that `git apply` strips when a hunk is staged or discarded.
    opts = ["--no-ext-diff", "--no-color", "--src-prefix=a/", "--dst-prefix=b/", f"-U{context}", "-M"]
    if ignore_ws:
        opts.append("-w")
    if kind != "commit":
        opts.append("--no-textconv")
    paths = literal([path] if not orig_path else [orig_path, path])
    if kind == "unstaged":
        if is_untracked(repo, path):
            full = Path(repo) / path
            if full.is_dir():
                return ""
            res = git_result(
                repo, ["diff", *opts, "--no-index", "--", "/dev/null", path]
            )
            if res.returncode not in (0, 1):
                raise GitError(res.stderr.strip() or "git diff failed")
            return res.stdout
        return git(repo, ["diff", *opts, "--", *paths])
    if kind == "staged":
        return git(repo, ["diff", "--cached", *opts, "--", *paths])
    if kind == "commit":
        if not sha:
            raise GitError("A commit is required")
        parents = git(repo, ["rev-list", "--parents", "-n1", sha, "--"]).split()[1:]
        base = parents[0] if parents else empty_tree(repo)
        return git(repo, ["diff", *opts, base, sha, "--", *paths])
    raise GitError(f"Unknown diff kind: {kind}")


def file_diff(repo: str, kind: str, path: str, **kwargs: Any) -> dict[str, Any]:
    text = raw_file_diff(repo, kind, path, **kwargs)
    parsed = patch.parse_diff(text)
    parsed["path"] = path
    parsed["kind"] = kind
    parsed["empty"] = not text.strip()
    return parsed


def file_history(repo: str, path: str, limit: int = 200) -> list[dict[str, Any]]:
    """Commits that changed a file, following renames.

    - Each row's path is the file's name in that commit, which differs before a rename.
    - Record layout with -z: RS, the fields, NUL, newline, the path, NUL.
    """
    fmt = RS + US.join(["%H", "%an", "%at", "%s"])
    out = git(
        repo,
        ["log", "--follow", f"-n{limit}", f"--format={fmt}", "--name-only", "-z", "--", *literal([path])],
    )
    rows = []
    for record in out.split(RS):
        fields, _, names = record.partition("\0")
        if not fields:
            continue
        f = (fields.split(US) + [""] * 4)[:4]
        name = names.removeprefix("\n").split("\0")[0] or path  # only git's newline
        rows.append(
            {
                "sha": f[0],
                "author": f[1],
                "date": int(f[2]) if f[2].isdigit() else 0,
                "subject": f[3],
                "path": name,
            }
        )
    return rows
