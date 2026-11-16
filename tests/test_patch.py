"""Tests for patch.parse_diff and patch.build_patch without git."""

from __future__ import annotations

from gitsense import patch

SIMPLE = """\
diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -1,4 +1,4 @@ def section():
 one
-two
+TWO
+extra
 three
-four
@@ -10,2 +10,3 @@
 ten
+ten-and-a-half
 eleven
"""


def test_parse_numbers_lines_on_both_sides():
    d = patch.parse_diff(SIMPLE)
    assert d["binary"] is False
    assert d["combined"] is False
    assert d["truncated"] is False
    assert d["header"][0] == "diff --git a/f.txt b/f.txt"
    assert len(d["header"]) == 4
    h0, h1 = d["hunks"]
    assert (h0["old_start"], h0["old_count"], h0["new_start"], h0["new_count"]) == (1, 4, 1, 4)
    assert h0["section"] == "def section():"
    assert [(ln["t"], ln["s"], ln["o"], ln["n"]) for ln in h0["lines"]] == [
        (" ", "one", 1, 1),
        ("-", "two", 2, None),
        ("+", "TWO", None, 2),
        ("+", "extra", None, 3),
        (" ", "three", 3, 4),
        ("-", "four", 4, None),
    ]
    assert [(ln["t"], ln["o"], ln["n"]) for ln in h1["lines"]] == [
        (" ", 10, 10),
        ("+", None, 11),
        (" ", 11, 12),
    ]


def test_parse_hunk_header_without_counts():
    d = patch.parse_diff("--- a/x\n+++ b/x\n@@ -3 +3 @@\n-a\n+b\n")
    h = d["hunks"][0]
    assert (h["old_start"], h["old_count"], h["new_start"], h["new_count"]) == (3, 1, 3, 1)
    assert h["section"] == ""


def test_parse_no_newline_marker():
    text = (
        "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\n-b\n\\ No newline at end of file\n"
        "+c\n\\ No newline at end of file\n"
    )
    lines = patch.parse_diff(text)["hunks"][0]["lines"]
    assert [ln["t"] for ln in lines] == [" ", "-", "\\", "+", "\\"]
    assert lines[2]["s"] == "\\ No newline at end of file"
    assert lines[2]["o"] is None and lines[2]["n"] is None
    # The marker does not advance line numbers.
    assert lines[3]["n"] == 2


def test_parse_binary():
    text = (
        "diff --git a/img.png b/img.png\nindex 1..2 100644\n"
        "Binary files a/img.png and b/img.png differ\n"
    )
    d = patch.parse_diff(text)
    assert d["binary"] is True
    assert d["hunks"] == []


def test_parse_git_binary_patch():
    d = patch.parse_diff("diff --git a/b b/b\nGIT binary patch\nliteral 3\nKcmZ?\n")
    assert d["binary"] is True


def test_parse_combined_conflict_diff():
    text = (
        "diff --cc f.txt\nindex 1,2..0000000\n--- a/f.txt\n+++ b/f.txt\n"
        "@@@ -1,1 -1,1 +1,5 @@@\n"
        "++<<<<<<< HEAD\n +main\n++=======\n+ feat\n++>>>>>>> feat\n  same\n- gone\n"
    )
    d = patch.parse_diff(text)
    assert d["combined"] is True
    hunk = d["hunks"][0]
    assert hunk["combined"] is True
    kinds = [ln["t"] for ln in hunk["lines"]]
    assert kinds == ["+", "+", "+", "+", "+", " ", "-"]
    assert hunk["lines"][1]["s"] == "main"
    assert all(ln["o"] is None and ln["n"] is None for ln in hunk["lines"])


def test_parse_stops_at_a_second_file():
    text = SIMPLE + "diff --git a/g b/g\n--- a/g\n+++ b/g\n@@ -1 +1 @@\n-x\n+y\n"
    d = patch.parse_diff(text)
    assert len(d["hunks"]) == 2


def test_parse_keeps_lines_that_look_like_headers():
    text = "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n--- not a header\n+++ also not\n same\n"
    lines = patch.parse_diff(text)["hunks"][0]["lines"]
    assert [(ln["t"], ln["s"]) for ln in lines] == [
        ("-", "-- not a header"),
        ("+", "++ also not"),
        (" ", "same"),
    ]


def test_parse_keeps_carriage_returns():
    d = patch.parse_diff("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\r\n+b\r\n")
    assert [ln["s"] for ln in d["hunks"][0]["lines"]] == ["a\r", "b\r"]


def test_parse_empty_text():
    d = patch.parse_diff("")
    assert d == {"header": [], "hunks": [], "binary": False, "combined": False, "truncated": False}


def test_parse_truncates_long_diffs(monkeypatch):
    monkeypatch.setattr(patch, "MAX_DIFF_LINES", 10)
    body = "".join(f"+line {i}\n" for i in range(25))
    d = patch.parse_diff(f"--- a/x\n+++ b/x\n@@ -0,0 +1,25 @@\n{body}")
    assert d["truncated"] is True
    assert len(d["hunks"][0]["lines"]) == 10


def test_parse_exactly_at_limit_is_not_truncated(monkeypatch):
    monkeypatch.setattr(patch, "MAX_DIFF_LINES", 5)
    body = "".join(f"+line {i}\n" for i in range(5))
    d = patch.parse_diff(f"--- a/x\n+++ b/x\n@@ -0,0 +1,5 @@\n{body}")
    assert d["truncated"] is False
    assert len(d["hunks"][0]["lines"]) == 5


# -- build_patch ------------------------------------------------------------------


def _hunk_text(p: str) -> list[str]:
    lines = p.split("\n")
    at = next(i for i, ln in enumerate(lines) if ln.startswith("@@"))
    return lines[at:]


def test_build_whole_hunk_drops_index_line():
    d = patch.parse_diff(SIMPLE)
    p = patch.build_patch(d["header"], d["hunks"][0])
    assert "index 1111111..2222222 100644" not in p
    assert p.startswith("diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n")
    assert _hunk_text(p) == ["@@ -1,4 +1,4 @@", " one", "-two", "+TWO", "+extra", " three", "-four", ""]


def test_build_forward_selection_neutralises_unselected_lines():
    d = patch.parse_diff(SIMPLE)
    # Select only "+TWO" (index 2): "-two" stays as context, "+extra" is dropped.
    p = patch.build_patch(d["header"], d["hunks"][0], {2})
    assert _hunk_text(p) == ["@@ -1,4 +1,5 @@", " one", " two", "+TWO", " three", " four", ""]


def test_build_reverse_selection_neutralises_the_other_way():
    d = patch.parse_diff(SIMPLE)
    # Select only "+extra" (index 3) for a reverse apply: "+TWO" becomes context, "-two" is dropped.
    p = patch.build_patch(d["header"], d["hunks"][0], {3}, reverse=True)
    assert _hunk_text(p) == ["@@ -1,3 +1,4 @@", " one", " TWO", "+extra", " three", ""]


def test_build_returns_empty_when_nothing_changes():
    d = patch.parse_diff(SIMPLE)
    assert patch.build_patch(d["header"], d["hunks"][0], {0, 4}) == ""
    assert patch.build_patch(d["header"], d["hunks"][0], set()) == ""


def test_build_drops_no_newline_marker_of_a_dropped_line():
    text = "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n\\ No newline at end of file\n"
    d = patch.parse_diff(text)
    p = patch.build_patch(d["header"], d["hunks"][0], {1})
    assert _hunk_text(p) == ["@@ -1,2 +1,1 @@", " a", "-b", ""]


def test_build_keeps_no_newline_marker_of_a_kept_line():
    text = "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n\\ No newline at end of file\n"
    d = patch.parse_diff(text)
    p = patch.build_patch(d["header"], d["hunks"][0], {2})
    assert _hunk_text(p) == ["@@ -1,2 +1,3 @@", " a", " b", "+c", "\\ No newline at end of file", ""]


def test_build_partial_patch_of_new_file_becomes_a_modification():
    text = (
        "diff --git a/n b/n\nnew file mode 100644\nindex 0000000..1234567\n"
        "--- /dev/null\n+++ b/n\n@@ -0,0 +1,3 @@\n+a\n+b\n+c\n"
    )
    d = patch.parse_diff(text)
    # Forward: only added lines remain, so it is still a file creation.
    fwd = patch.build_patch(d["header"], d["hunks"][0], {1})
    assert "new file mode 100644" in fwd and "--- /dev/null" in fwd
    assert _hunk_text(fwd) == ["@@ -0,0 +1,1 @@", "+b", ""]
    # Reverse with some lines kept: the file survives, so it is a modification.
    rev = patch.build_patch(d["header"], d["hunks"][0], {1}, reverse=True)
    assert rev.split("\n")[:3] == ["diff --git a/n b/n", "--- a/n", "+++ b/n"]
    assert _hunk_text(rev) == ["@@ -1,2 +1,3 @@", " a", "+b", " c", ""]
    # Reverse with every line selected is still a deletion of the new file.
    full = patch.build_patch(d["header"], d["hunks"][0], None, reverse=True)
    assert "--- /dev/null" in full


def test_build_partial_patch_of_deleted_file_becomes_a_modification():
    text = (
        'diff --git "a/sp\\"q" "b/sp\\"q"\ndeleted file mode 100644\nindex 1234567..0000000\n'
        '--- "a/sp\\"q"\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n'
    )
    d = patch.parse_diff(text)
    p = patch.build_patch(d["header"], d["hunks"][0], {0})
    assert p.split("\n")[:3] == ['diff --git "a/sp\\"q" "b/sp\\"q"', '--- "a/sp\\"q"', '+++ "b/sp\\"q"']
    assert _hunk_text(p) == ["@@ -1,2 +1,1 @@", "-a", " b", ""]
    whole = patch.build_patch(d["header"], d["hunks"][0])
    assert "deleted file mode 100644" in whole and "+++ /dev/null" in whole


RENAME = """\
diff --git a/old name.txt b/new name.txt
similarity index 90%
rename from old name.txt
rename to new name.txt
index 1111111..2222222 100644
--- a/old name.txt\t
+++ b/new name.txt\t
@@ -1,2 +1,3 @@
 a
 b
+c
"""


def test_build_reverse_rename_patch_targets_the_new_path_only():
    d = patch.parse_diff(RENAME)
    p = patch.build_patch(d["header"], d["hunks"][0], reverse=True)
    assert p.split("\n")[:3] == [
        "diff --git a/new name.txt b/new name.txt",
        "--- a/new name.txt\t",
        "+++ b/new name.txt\t",
    ]
    assert "rename" not in p


def test_build_forward_rename_patch_keeps_the_rename():
    d = patch.parse_diff(RENAME)
    p = patch.build_patch(d["header"], d["hunks"][0])
    assert "rename from old name.txt" in p and "rename to new name.txt" in p
    assert "similarity index" not in p
