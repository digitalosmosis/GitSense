"""Parse unified diffs and build partial patches for hunk and line staging."""

from __future__ import annotations

import re
from typing import Any

MAX_DIFF_LINES = 20000
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_COMBINED_RE = re.compile(r"^@@@+ .* @@@+")


def parse_diff(text: str) -> dict[str, Any]:
    """Parse the diff of a single file.

    Returns header lines, hunks with numbered lines, and flags:
    - binary: git reported a binary change
    - combined: a merge conflict diff (shown read-only)
    - truncated: the diff was cut at MAX_DIFF_LINES
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    header: list[str] = []
    hunks: list[dict[str, Any]] = []
    binary = False
    combined = False
    truncated = False
    total = 0
    i = 0
    n = len(lines)

    while i < n and not lines[i].startswith("@@"):
        line = lines[i]
        if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            binary = True
        if line.startswith(("diff --cc ", "diff --combined ")):
            combined = True
        header.append(line)
        i += 1

    current: dict[str, Any] | None = None
    old_no = new_no = 0
    while i < n:
        line = lines[i]
        i += 1
        if combined:
            if _COMBINED_RE.match(line):
                current = {"header": line, "lines": [], "combined": True}
                hunks.append(current)
                continue
            if current is None:
                continue
            marks, body = line[:2], line[2:]
            kind = "+" if "+" in marks else "-" if "-" in marks else " "
            current["lines"].append({"t": kind, "s": body, "o": None, "n": None})
            total += 1
        else:
            m = _HUNK_RE.match(line)
            if m:
                old_no = int(m.group(1))
                new_no = int(m.group(3))
                current = {
                    "header": line,
                    "old_start": old_no,
                    "old_count": int(m.group(2)) if m.group(2) is not None else 1,
                    "new_start": new_no,
                    "new_count": int(m.group(4)) if m.group(4) is not None else 1,
                    "section": m.group(5).strip(),
                    "lines": [],
                }
                hunks.append(current)
                continue
            if current is None:
                continue
            if line.startswith("diff --git "):
                break  # a second file: callers ask for one file at a time
            tag = line[:1]
            body = line[1:]
            if tag == "+":
                current["lines"].append({"t": "+", "s": body, "o": None, "n": new_no})
                new_no += 1
            elif tag == "-":
                current["lines"].append({"t": "-", "s": body, "o": old_no, "n": None})
                old_no += 1
            elif tag == "\\":
                current["lines"].append({"t": "\\", "s": line, "o": None, "n": None})
            else:
                current["lines"].append({"t": " ", "s": body, "o": old_no, "n": new_no})
                old_no += 1
                new_no += 1
            total += 1
        if total >= MAX_DIFF_LINES:
            truncated = i < n
            break

    return {
        "header": header,
        "hunks": hunks,
        "binary": binary,
        "combined": combined,
        "truncated": truncated,
    }


def build_patch(
    header: list[str],
    hunk: dict[str, Any],
    selected: set[int] | None = None,
    *,
    reverse: bool = False,
) -> str:
    """Build a patch holding one hunk, or only some of its changed lines.

    - selected holds indexes into hunk["lines"]; None keeps every change.
    - reverse is True when the patch will be applied with `git apply --reverse`
      (unstaging, or discarding from the working tree).
    - An unselected change is neutralised so the patch still applies:
      forward patches drop unselected '+' and keep unselected '-' as context;
      reverse patches do the opposite.
    """
    out: list[str] = []
    old_count = new_count = 0
    changed = False
    last_dropped = False

    for idx, line in enumerate(hunk["lines"]):
        tag, body = line["t"], line["s"]
        if tag == "\\":
            if not last_dropped:
                out.append(body)
            continue
        last_dropped = False
        chosen = selected is None or idx in selected
        if tag == " ":
            out.append(" " + body)
            old_count += 1
            new_count += 1
        elif tag == "+":
            if chosen:
                out.append("+" + body)
                new_count += 1
                changed = True
            elif reverse:
                # Present in the "new" side that a reverse apply starts from.
                out.append(" " + body)
                old_count += 1
                new_count += 1
            else:
                last_dropped = True
        elif tag == "-":
            if chosen:
                out.append("-" + body)
                old_count += 1
                changed = True
            elif not reverse:
                out.append(" " + body)
                old_count += 1
                new_count += 1
            else:
                last_dropped = True

    if not changed:
        return ""

    file_header = [ln for ln in header if not ln.startswith(("index ", "similarity index"))]
    if reverse and any(ln.startswith(("rename from ", "copy from ")) for ln in file_header):
        file_header = _as_new_path_only(file_header)
    old_start, new_start = hunk["old_start"], hunk["new_start"]
    # A partial patch of a created or deleted file leaves content on both sides.
    # - It must be applied as a plain modification, or git refuses it.
    # - A side that now has lines starts at line 1, not 0.
    if (old_count and "--- /dev/null" in file_header) or (
        new_count and "+++ /dev/null" in file_header
    ):
        file_header = _as_modification(file_header)
    if old_count and old_start == 0:
        old_start = 1
    if new_count and new_start == 0:
        new_start = 1
    hunk_header = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@"
    return "\n".join([*file_header, hunk_header, *out]) + "\n"


def _as_modification(header: list[str]) -> list[str]:
    """Rewrite a 'new file' or 'deleted file' header as a modification of the same path."""
    minus = next((ln for ln in header if ln.startswith("--- ")), "")
    plus = next((ln for ln in header if ln.startswith("+++ ")), "")
    out = []
    for ln in header:
        if ln.startswith(("new file mode ", "deleted file mode ")):
            continue
        if ln == "--- /dev/null":
            ln = "--- " + _swap_prefix(plus[4:], "b/", "a/")
        elif ln == "+++ /dev/null":
            ln = "+++ " + _swap_prefix(minus[4:], "a/", "b/")
        out.append(ln)
    return out


def _as_new_path_only(header: list[str]) -> list[str]:
    """Rewrite a rename or copy header as a change to the new path alone.

    - A hunk or line patch then changes content without undoing the rename.
    - The rename itself is staged or unstaged with the whole file.
    """
    plus = next((ln for ln in header if ln.startswith("+++ ")), "")
    new_b = plus[4:]
    new_a = _swap_prefix(new_b, "b/", "a/")
    out = []
    for ln in header:
        if ln.startswith(("rename from ", "rename to ", "copy from ", "copy to ", "dissimilarity index ")):
            continue
        if ln.startswith("diff --git "):
            ln = f"diff --git {new_a.rstrip(chr(9))} {new_b.rstrip(chr(9))}"
        elif ln.startswith("--- "):
            ln = "--- " + new_a
        out.append(ln)
    return out


def _swap_prefix(name: str, old: str, new: str) -> str:
    if name.startswith('"' + old):
        return '"' + new + name[len(old) + 1 :]
    if name.startswith(old):
        return new + name[len(old) :]
    return name
