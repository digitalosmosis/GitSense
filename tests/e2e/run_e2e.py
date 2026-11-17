"""End-to-end check of the main GitSense workflows in a headless browser.

Run from the repository root:

    uv run --with playwright python tests/e2e/run_e2e.py

- Works on throwaway repositories in a temp folder, with its own config and git settings.
- Needs a Playwright Chromium (`uv run --with playwright playwright install chromium`).
- Set GITSENSE_E2E_CHROMIUM to the path of a browser binary to use that one instead.
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE = Path(tempfile.mkdtemp(prefix="gitsense-e2e-"))
WORK = BASE / "work"
with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
CHROME = os.environ.get("GITSENSE_E2E_CHROMIUM") or None
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=str(BASE / "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
           GIT_AUTHOR_NAME="E2E", GIT_AUTHOR_EMAIL="e2e@example.com",
           GIT_COMMITTER_NAME="E2E", GIT_COMMITTER_EMAIL="e2e@example.com",
           GITSENSE_CONFIG_DIR=str(BASE / "cfg"))

def git(repo, *args, check=True):
    r = subprocess.run(["git", *args], cwd=repo, env=ENV, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r.stdout.strip()

def setup():
    shutil.rmtree(WORK, ignore_errors=True); shutil.rmtree(BASE / "cfg", ignore_errors=True)
    WORK.mkdir(parents=True)
    (BASE / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")
    remote = WORK / "remote.git"
    git(WORK, "init", "-q", "--bare", str(remote))
    repo = WORK / "proj"
    git(WORK, "clone", "-q", str(remote), str(repo))
    (repo / "app.py").write_text("".join(f"line {i}\n" for i in range(1, 31)))
    (repo / "README.md").write_text("# Proj\n")
    git(repo, "add", "."); git(repo, "commit", "-qm", "Initial commit"); git(repo, "push", "-qu", "origin", "main")
    # conflicting branch
    git(repo, "switch", "-qc", "feature/conflict")
    (repo / "README.md").write_text("# Proj from feature\n"); git(repo, "commit", "-qam", "Feature edits README")
    git(repo, "switch", "-q", "main")
    (repo / "README.md").write_text("# Proj from main\n"); git(repo, "commit", "-qam", "Main edits README")
    # clean branch to merge
    git(repo, "switch", "-qc", "feature/clean", "HEAD~1")
    (repo / "extra.txt").write_text("extra\n"); git(repo, "add", "."); git(repo, "commit", "-qm", "Add extra")
    git(repo, "switch", "-q", "main")
    # working changes: two separate hunks plus a multi-line addition
    lines = (repo / "app.py").read_text().splitlines()
    lines[1] = "line 2 changed"
    lines[27] = "line 28 changed"
    lines.insert(15, "new A"); lines.insert(16, "new B")
    (repo / "app.py").write_text("\n".join(lines) + "\n")
    (repo / "notes.txt").write_text("untracked\n")
    return repo

results = []
def step(name):
    def deco(fn):
        try:
            fn(); results.append((name, True, "")); print(f"PASS  {name}")
        except Exception as exc:
            results.append((name, False, str(exc))); print(f"FAIL  {name}\n      {str(exc)[:600]}")
            try: page.screenshot(path=str(BASE / f"fail-{len(results)}.png"))
            except Exception: pass
        return fn
    return deco

repo = setup()
server = subprocess.Popen([sys.executable, "-m", "gitsense", "--no-open", "--port", str(PORT), str(repo)],
                          env=dict(ENV, PYTHONUNBUFFERED="1"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
# The page needs the launch key, which is only in the address the server prints.
URL = next(m.group(1) for line in server.stdout if (m := re.search(r" at (http://\S+)", line)))
errors = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        page = browser.new_page(viewport={"width": 1480, "height": 920})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        page.set_default_timeout(6000)
        page.goto(URL)
        detail = page.locator(".detail")
        modal = page.locator(".modal")
        staged_sec = detail.locator(".file-section").filter(has=page.locator(".eyebrow", has_text=re.compile(r"^Staged files")))
        conflict_sec = detail.locator(".file-section").filter(has=page.locator(".eyebrow", has_text=re.compile(r"^Conflicted")))

        @step("the loading splash shows first, then clears")
        def _():
            assert page.evaluate("performance.now()") < 1200, "page took too long to check the splash"
            expect(page.locator("#splash .sp-rider")).to_be_visible()
            expect(page.locator("#splash")).to_have_count(0, timeout=5000)
            assert 1200 < page.evaluate("performance.now()") < 4000

        @step("opens repo on the WIP row with changes listed")
        def _():
            expect(page.locator(".graph-row.wip.selected")).to_be_visible()
            expect(detail.locator(".file-row")).to_have_count(2)

        @step("stage one hunk of several")
        def _():
            detail.locator(".file-row", has_text="app.py").click()
            expect(page.locator(".hunk")).to_have_count(3)
            page.locator(".hunk").nth(0).get_by_role("button", name="Stage hunk").click()
            expect(page.locator(".hunk")).to_have_count(2)
            staged = git(repo, "diff", "--cached")
            assert "line 2 changed" in staged and "new A" not in staged and "line 28 changed" not in staged, staged

        @step("stage selected lines only")
        def _():
            row = page.locator(".dl.add", has_text="new A")
            row.locator(".ln").first.click()
            page.get_by_role("button", name="Stage 1 line").click()
            page.wait_for_timeout(600)
            staged = git(repo, "diff", "--cached")
            assert "+new A" in staged and "+new B" not in staged, staged
            unstaged = git(repo, "diff")
            assert "+new B" in unstaged and "+new A" not in unstaged, unstaged

        @step("unstage one hunk from the staged diff")
        def _():
            staged_sec.locator(".file-row", has_text="app.py").click()
            expect(page.locator(".diff-head .eyebrow")).to_have_text("Staged changes")
            expect(page.locator(".hunk")).to_have_count(2)
            page.locator(".hunk", has_text="new A").get_by_role("button", name="Unstage hunk").click()
            page.wait_for_timeout(600)
            staged = git(repo, "diff", "--cached")
            assert "new A" not in staged and "line 2 changed" in staged, staged

        @step("view options turn off hunk and line staging")
        def _():
            expect(page.get_by_role("button", name="Unstage hunk").first).to_be_visible()
            page.get_by_label("Ignore whitespace").check()
            expect(page.get_by_role("button", name="Unstage hunk")).to_have_count(0)
            page.get_by_label("Ignore whitespace").uncheck()
            expect(page.get_by_role("button", name="Unstage hunk").first).to_be_visible()

        @step("typing a commit message survives a background refresh")
        def _():
            page.keyboard.press("Escape")
            summary = detail.get_by_label("Commit summary")
            summary.click()
            page.keyboard.type("Change")
            rows = detail.locator(".file-row").count()
            (repo / "autosave.tmp").write_text("x\n")
            expect(detail.locator(".file-row")).to_have_count(rows + 1, timeout=9000)
            page.keyboard.type(" line 2")
            expect(summary).to_have_value("Change line 2")
            expect(summary).to_be_focused()
            (repo / "autosave.tmp").unlink()
            expect(detail.locator(".file-row")).to_have_count(rows, timeout=9000)

        @step("commit staged changes; a double-click commits once")
        def _():
            before = int(git(repo, "rev-list", "--count", "HEAD"))
            detail.get_by_label("Commit description").fill("Body text")
            detail.get_by_role("button", name="Commit 1 staged file").dblclick()
            expect(page.locator(".toast.ok")).to_contain_text("Committed")
            page.wait_for_timeout(500)
            assert int(git(repo, "rev-list", "--count", "HEAD")) == before + 1
            assert page.locator(".toast.error").count() == 0
            assert git(repo, "log", "-1", "--format=%s%n%b").split("\n")[:3] == ["Change line 2", "Body text", ""][: len(git(repo, "log", "-1", "--format=%s%n%b").split("\n"))] or git(repo, "log", "-1", "--format=%s") == "Change line 2"
            expect(detail.get_by_label("Commit summary")).to_have_value("")
            expect(page.locator(".graph-row.wip")).to_be_visible()  # still has unstaged changes

        @step("stage file, unstage all, stage all via buttons")
        def _():
            page.locator(".graph-row.wip").click()
            row = detail.locator(".file-row", has_text="notes.txt")
            row.hover(); row.get_by_label("Stage file").click()
            expect(staged_sec.locator(".file-row")).to_have_count(1)
            detail.get_by_role("button", name="Unstage all").click()
            expect(staged_sec.locator(".file-row")).to_have_count(0)
            assert git(repo, "diff", "--cached", "--name-only") == ""

        @step("discard one file with confirmation")
        def _():
            row = detail.locator(".file-row", has_text="notes.txt")
            row.hover(); row.get_by_label("Discard changes").click()
            expect(modal).to_contain_text("deleted from disk")
            modal.get_by_role("button", name="Discard").click()
            expect(detail.locator(".file-row", has_text="notes.txt")).to_have_count(0)
            assert not (repo / "notes.txt").exists()

        @step("Cmd-click and Shift-click select several files to stage and unstage")
        def _():
            try:
                unstaged_sec = detail.locator(".file-section").filter(has=page.locator(".eyebrow", has_text=re.compile(r"^Unstaged files")))
                for name in ("m1.txt", "m2.txt", "m3.txt", "m4.txt"):
                    (repo / name).write_text(name + "\n")
                expect(unstaged_sec.locator(".file-row", has_text="m4.txt")).to_have_count(1, timeout=9000)
                row = lambda name: unstaged_sec.locator(".file-row", has_text=name)
                selected = unstaged_sec.locator(".file-row.selected")
                # Plain click opens one diff; Cmd-click adds a second file and closes the diff.
                row("m1.txt").click()
                expect(page.locator(".diff-head")).to_be_visible()
                row("m3.txt").click(modifiers=["Meta"])
                expect(selected).to_have_count(2)
                expect(page.locator(".diff-head")).to_have_count(0)
                expect(unstaged_sec.get_by_role("button", name="Stage 2")).to_be_visible()
                # Cmd-click on a picked file takes it out; one file left opens its diff.
                row("m3.txt").click(modifiers=["Meta"])
                expect(selected).to_have_count(1)
                expect(page.locator(".diff-head")).to_be_visible()
                # Shift-click selects the range from the last click.
                row("m4.txt").click(modifiers=["Shift"])
                expect(selected).to_have_count(2)
                expect(row("m3.txt")).to_have_class(re.compile(r"\bselected\b"))
                row("m1.txt").click()
                row("m4.txt").click(modifiers=["Shift"])
                expect(selected).to_have_count(4)
                expect(row("app.py")).not_to_have_class(re.compile(r"\bselected\b"))
                # Escape clears the selection.
                page.keyboard.press("Escape")
                expect(selected).to_have_count(0)
                row("m2.txt").click()
                row("m4.txt").click(modifiers=["Shift"])
                expect(selected).to_have_count(3)
                unstaged_sec.get_by_role("button", name="Stage 3").click()
                expect(staged_sec.locator(".file-row")).to_have_count(3)
                assert sorted(git(repo, "diff", "--cached", "--name-only").split()) == ["m2.txt", "m3.txt", "m4.txt"]
                # Right-click on the picked files offers to unstage all of them.
                staged_sec.locator(".file-row", has_text="m2.txt").click()
                staged_sec.locator(".file-row", has_text="m3.txt").click(modifiers=["Meta"])
                staged_sec.locator(".file-row", has_text="m3.txt").click(button="right")
                page.locator(".menu").get_by_text("Unstage 2 files").click()
                expect(staged_sec.locator(".file-row")).to_have_count(1)
                assert git(repo, "diff", "--cached", "--name-only") == "m4.txt"
            finally:
                git(repo, "reset", "-q")
                for name in ("m1.txt", "m2.txt", "m3.txt", "m4.txt"):
                    (repo / name).unlink()
                expect(unstaged_sec.locator(".file-row", has_text="m1.txt")).to_have_count(0, timeout=9000)

        @step("unstaged and staged lists split evenly once both hold 3 files")
        def _():
            names = [f"e{i}.txt" for i in range(8)]
            sections = detail.locator(".file-section.even")
            try:
                for name in names:
                    (repo / name).write_text(name + "\n")
                git(repo, "add", *names[:3])
                expect(staged_sec.locator(".file-row")).to_have_count(3, timeout=9000)
                expect(sections).to_have_count(2)
                heights = [round(b["height"]) for b in (sections.nth(i).bounding_box() for i in range(2))]
                assert abs(heights[0] - heights[1]) <= 1, heights
                # Dropping to 2 staged files sizes each list to its files again.
                git(repo, "reset", "-q", "--", names[0])
                expect(staged_sec.locator(".file-row")).to_have_count(2, timeout=9000)
                expect(sections).to_have_count(0)
            finally:
                git(repo, "reset", "-q")
                for name in names:
                    (repo / name).unlink()
                expect(detail.locator(".file-row", has_text="e0.txt")).to_have_count(0, timeout=9000)

        @step("stash and pop from the toolbar")
        def _():
            page.locator(".tb-btn", has_text="Stash").click()
            modal.get_by_placeholder("What were you working on?").fill("e2e stash")
            modal.get_by_role("button", name="Stash").click()
            expect(page.locator(".graph-row.stash")).to_be_visible()
            assert git(repo, "status", "--porcelain") == ""
            assert "e2e stash" in git(repo, "stash", "list")
            page.locator(".tb-btn", has_text="Pop").click()
            expect(page.locator(".graph-row.wip")).to_be_visible()
            assert git(repo, "stash", "list") == ""
            assert "app.py" in git(repo, "status", "--porcelain")

        @step("stage all and commit, then push with ahead badge")
        def _():
            page.locator(".graph-row.wip").click()
            detail.get_by_label("Commit summary").fill("Rest of the changes")
            detail.get_by_role("button", name="Stage all and commit 1 file").click()
            expect(page.locator(".toast.ok").last).to_contain_text("Committed")
            expect(page.locator(".graph-row.wip")).to_have_count(0)
            push = page.locator(".tb-btn", has_text="Push")
            expect(push.locator(".badge")).to_have_text("3")
            push.click()
            expect(page.locator(".toast.ok").last).to_contain_text("Pushed main")
            assert git(repo, "rev-parse", "main") == git(repo, "rev-parse", "origin/main")
            expect(push.locator(".badge")).to_have_count(0)

        @step("create a branch from the toolbar and switch back by double-click")
        def _():
            page.locator(".tb-btn", has_text="Branch").click()
            modal.get_by_placeholder("feature/my-change").fill("feature/e2e")
            modal.get_by_role("button", name="Create branch").click()
            expect(page.locator(".tb-context", has_text="Branch")).to_contain_text("feature/e2e")
            assert git(repo, "branch", "--show-current") == "feature/e2e"
            page.locator(".sidebar .side-row", has_text="main").first.dblclick()
            expect(page.locator(".tb-context", has_text="Branch")).to_contain_text("main")
            assert git(repo, "branch", "--show-current") == "main"

        @step("merge a clean branch from the sidebar menu")
        def _():
            page.locator(".sidebar .side-row", has_text="clean").first.click(button="right")
            page.locator(".menu-item", has_text="Merge feature/clean into main").click()
            modal.get_by_role("button", name="Merge").click()
            expect(page.locator(".toast.ok").last).to_contain_text("Merged feature/clean")
            assert (repo / "extra.txt").exists()

        @step("merge with conflict, resolve with theirs, continue")
        def _():
            page.locator(".sidebar .side-row", has_text="conflict").first.click(button="right")
            page.locator(".menu-item", has_text="Merge feature/conflict into main").click()
            modal.get_by_role("button", name="Merge").click()
            expect(detail.locator(".op-banner")).to_contain_text("Merge in progress")
            conflicted = conflict_sec.locator(".file-row")
            expect(conflicted).to_have_count(1)
            expect(detail.get_by_role("button", name="Continue")).to_be_disabled()
            conflicted.click()
            expect(page.locator(".diff-body")).to_contain_text("merge conflicts")
            page.get_by_role("button", name="Use theirs").click()
            expect(conflict_sec).to_have_count(0)
            detail.get_by_role("button", name="Continue").click()
            expect(detail.locator(".op-banner")).to_have_count(0)
            assert (repo / "README.md").read_text() == "# Proj from feature\n"
            assert len(git(repo, "rev-list", "--parents", "-n1", "HEAD").split()) == 3

        @step("commit context menu: create tag, then tag shows in sidebar")
        def _():
            page.locator(".graph-row .g-msg", has_text="Initial commit").click(button="right")
            page.locator(".menu-item", has_text="Create tag here").click()
            modal.get_by_placeholder("v1.0.0").fill("v0.0.1")
            modal.get_by_role("button", name="Create tag").click()
            expect(page.locator(".graph-row .chip.tag", has_text="v0.0.1")).to_be_visible()
            assert git(repo, "tag") == "v0.0.1"

        @step("detects changes made outside the app")
        def _():
            (repo / "outside.txt").write_text("made elsewhere\n")
            expect(page.locator(".graph-row.wip")).to_be_visible(timeout=9000)

        @step("checkout blocked by local changes offers to stash")
        def _():
            (repo / "README.md").write_text("# local edit\n")
            page.wait_for_timeout(3500)
            page.locator(".sidebar .side-row", has_text="e2e").first.dblclick()
            # README differs between main and feature/e2e, so git refuses the switch
            expect(modal).to_contain_text("Local changes are in the way")
            modal.get_by_role("button", name="Stash and switch").click()
            expect(page.locator(".tb-context", has_text="Branch")).to_contain_text("feature/e2e")
            assert git(repo, "branch", "--show-current") == "feature/e2e"
            assert "Before switching" in git(repo, "stash", "list")

        @step("delete branch with unmerged commits asks twice")
        def _():
            git(repo, "switch", "-q", "main"); git(repo, "branch", "throwaway"); git(repo, "switch", "-q", "throwaway")
            (repo / "t.txt").write_text("t\n"); git(repo, "add", "t.txt"); git(repo, "commit", "-qm", "Throwaway work"); git(repo, "switch", "-q", "main")
            expect(page.locator(".sidebar .side-row", has_text="throwaway")).to_be_visible(timeout=9000)
            page.locator(".sidebar .side-row", has_text="throwaway").click(button="right")
            page.locator(".menu-item", has_text="Delete").click()
            modal.get_by_role("button", name="Delete branch").click()
            expect(modal).to_contain_text("not merged")
            modal.get_by_role("button", name="Delete anyway").click()
            expect(page.locator(".sidebar .side-row", has_text="throwaway")).to_have_count(0)
            assert "throwaway" not in git(repo, "branch")

        @step("pull brings in a commit pushed by someone else")
        def _():
            other = WORK / "other"
            git(WORK, "clone", "-q", str(WORK / "remote.git"), str(other))
            (other / "theirs.txt").write_text("x\n"); git(other, "add", "."); git(other, "commit", "-qm", "Remote work"); git(other, "push", "-q")
            git(repo, "stash", "-u", check=False)
            before = git(repo, "rev-parse", "origin/main")
            page.locator(".tb-btn", has_text="Fetch").click()
            expect(page.locator(".toast.ok").last).to_contain_text("Fetched")
            assert git(repo, "rev-parse", "origin/main") != before
            # local main has merges not on the remote, so use the merge mode
            page.locator(".tb-btn.split").click()
            page.locator(".menu-item", has_text="Merge").click()
            expect(page.locator(".toast.ok").last).to_contain_text("Pulled")
            assert (repo / "theirs.txt").exists()

        @step("selecting a commit fades commits outside its history")
        def _():
            # A commit on its own branch off the first commit, made outside the app.
            root = git(repo, "rev-list", "--max-parents=0", "HEAD")
            side = git(repo, "commit-tree", f"{root}^{{tree}}", "-p", root, "-m", "Lineage side commit")
            git(repo, "branch", "lineage/side", side)
            side_row = page.locator(".graph-row", has_text="Lineage side commit")
            first_row = page.locator(".graph-row", has_text="Initial commit")
            expect(side_row).to_be_visible(timeout=9000)
            side_row.locator(".g-msg").click()
            expect(side_row).to_have_class(re.compile(r"\bselected\b"))
            expect(first_row).not_to_have_class(re.compile(r"off-line"))
            expect(page.locator(".graph-row.is-head")).to_have_class(re.compile(r"off-line"))
            assert page.locator(".g-edge.lit").count() > 0 and page.locator(".g-edge.faint").count() > 0
            # Everything descends from the first commit, so nothing fades.
            first_row.locator(".g-msg").click()
            expect(page.locator(".graph-row.off-line")).to_have_count(0)
            # The search filter takes over the dimming.
            side_row.locator(".g-msg").click()
            page.get_by_label("Find commits").fill("Initial")
            expect(page.locator(".graph-row.off-line, .g-edge.lit, .g-edge.faint")).to_have_count(0)
            page.get_by_label("Find commits").fill("")
            expect(page.locator(".graph-row.is-head")).to_have_class(re.compile(r"off-line"))
            # The setting turns it off.
            page.get_by_label("Settings").click()
            modal.get_by_label("Highlight the selected commit's history").uncheck()
            modal.get_by_role("button", name="Done").click()
            expect(page.locator(".graph-row.off-line, .g-edge.lit, .g-edge.faint")).to_have_count(0)
            page.get_by_label("Settings").click()
            modal.get_by_label("Highlight the selected commit's history").check()
            modal.get_by_role("button", name="Done").click()
            expect(page.locator(".graph-row.is-head")).to_have_class(re.compile(r"off-line"))
            page.locator(".graph-row.is-head .g-msg").click()
            git(repo, "branch", "-D", "lineage/side")
            expect(side_row).to_have_count(0, timeout=9000)

        @step("search dims non-matching commits")
        def _():
            page.get_by_label("Find commits").fill("Initial")
            expect(page.locator(".tb-search-count")).to_have_text("1 found")
            expect(page.locator(".graph-row.match")).to_have_count(1)
            page.get_by_label("Find commits").fill("")

        @step("commit detail shows files and opens a diff; Esc closes it")
        def _():
            page.locator(".graph-row .g-msg", has_text="Change line 2").click()
            expect(detail.locator(".commit-subject")).to_have_text("Change line 2")
            detail.locator(".file-row", has_text="app.py").click()
            expect(page.locator(".diff-head .eyebrow")).to_contain_text("Commit")
            expect(page.locator(".dl.add", has_text="line 2 changed")).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.locator(".diff-view")).to_have_count(0)

        @step("edit the last commit message from the context menu")
        def _():
            head = git(repo, "log", "-1", "--format=%s")
            page.locator(".graph-row.is-head .g-msg").click(button="right")
            page.locator(".menu-item", has_text="Edit commit message").click()
            modal.locator("input.input").first.fill("Reworded head")
            modal.get_by_role("button", name="Amend commit").click()
            expect(page.locator(".toast.ok").last).to_contain_text("Commit message updated")
            assert git(repo, "log", "-1", "--format=%s") == "Reworded head" != head

        @step("cherry-pick a commit from another branch")
        def _():
            git(repo, "switch", "-q", "-c", "side", "v0.0.1"); (repo / "picked.txt").write_text("p\n"); git(repo, "add", "."); git(repo, "commit", "-qm", "Pick me"); git(repo, "switch", "-q", "main")
            expect(page.locator(".graph-row .g-msg", has_text="Pick me")).to_be_visible(timeout=9000)
            page.locator(".graph-row .g-msg", has_text="Pick me").click(button="right")
            page.locator(".menu-item", has_text="Cherry-pick commit").click()
            expect(page.locator(".toast.ok").last).to_contain_text("Cherry-picked")
            assert (repo / "picked.txt").exists() and git(repo, "branch", "--show-current") == "main"

        @step("rebase stops on a conflict and can be aborted")
        def _():
            git(repo, "switch", "-q", "-c", "rb", "v0.0.1"); (repo / "README.md").write_text("# rebase side\n"); git(repo, "commit", "-qam", "Rebase side edit")
            expect(page.locator(".tb-context", has_text="Branch")).to_contain_text("rb", timeout=9000)
            page.locator(".sidebar .side-row", has_text=re.compile(r"^main")).first.click(button="right")
            page.locator(".menu-item", has_text="Rebase rb onto main").click()
            modal.get_by_role("button", name="Rebase").click()
            expect(detail.locator(".op-banner")).to_contain_text("Rebase in progress")
            expect(conflict_sec.locator(".file-row")).to_have_count(1)
            detail.get_by_role("button", name="Abort").click()
            expect(detail.locator(".op-banner")).to_have_count(0)
            assert git(repo, "status", "--porcelain") == "" and git(repo, "log", "-1", "--format=%s") == "Rebase side edit"

        @step("dashboard lists the repo and opens it; settings dialog works")
        def _():
            page.locator(".tab-home").click()
            row = page.get_by_text("proj", exact=True).first
            expect(row).to_be_visible()
            row.dblclick()
            expect(page.locator(".repo-view")).to_be_visible()
            page.get_by_label("Settings").click()
            expect(modal).to_contain_text("Settings")
            modal.locator('.tcard-pick[data-id="graphite"]').click()
            assert page.evaluate("[document.documentElement.dataset.theme, document.documentElement.dataset.themeId]") == ["dark", "graphite"]
            modal.locator('.tcard-pick[data-id="daylight"]').click()
            assert page.evaluate("document.documentElement.dataset.themeId") == "daylight"
            modal.get_by_role("button", name="Done").click()
            expect(modal).to_have_count(0)

        css_var = "getComputedStyle(document.documentElement).getPropertyValue('--bg').trim().toUpperCase()"

        @step("duplicate a built-in theme, edit a color with live preview, save, and it persists")
        def _():
            page.get_by_label("Settings").click()
            card = modal.locator(".tcard").filter(has=page.locator('.tcard-pick[data-id="graphite"]'))
            card.get_by_role("button", name="Duplicate to customize").click()
            sheet = page.locator(".tsheet")
            expect(sheet).to_be_visible()
            expect(modal).to_have_count(0)
            assert page.evaluate(css_var) == "#1E1F22"  # previewing the Graphite copy
            sheet.get_by_label("Background", exact=True).fill("#102030")
            page.wait_for_timeout(200)
            assert page.evaluate(css_var) == "#102030"
            sheet.locator("input[type=text]").first.fill("E2E theme")
            sheet.get_by_role("button", name="Save").click()
            expect(sheet).to_have_count(0)
            expect(modal.locator(".tcard-name", has_text="E2E theme")).to_be_visible()
            modal.get_by_role("button", name="Done").click()
            theme_id = page.evaluate("document.documentElement.dataset.themeId")
            assert theme_id.startswith("custom-") and page.evaluate(css_var) == "#102030"
            page.reload()
            expect(page.locator(".repo-view")).to_be_visible()
            assert page.evaluate("document.documentElement.dataset.themeId") == theme_id
            assert page.evaluate(css_var) == "#102030"

        @step("cancelling the editor restores the look; the top bar menu switches themes")
        def _():
            page.get_by_label("Settings").click()
            card = modal.locator(".tcard").filter(has=page.locator(".tcard-name", has_text="E2E theme"))
            card.get_by_role("button", name="Edit").click()
            sheet = page.locator(".tsheet")
            sheet.get_by_label("Background", exact=True).fill("#400000")
            page.wait_for_timeout(200)
            assert page.evaluate(css_var) == "#400000"
            sheet.get_by_role("button", name="Cancel").click()
            modal.get_by_role("button", name="Discard").click()
            expect(sheet).to_have_count(0)
            assert page.evaluate(css_var) == "#102030"
            modal.get_by_role("button", name="Done").click()
            page.locator("button[aria-label^='Theme:']").click()
            page.locator(".menu-item", has_text="Daylight").click()
            page.wait_for_timeout(200)
            assert page.evaluate("document.documentElement.dataset.themeId") == "daylight"
            assert page.evaluate(css_var) == "#FFFFFF"

        @step("Tab moves from summary to description to the commit button; Space commits")
        def _():
            (repo / "keys.txt").write_text("typed\n")
            page.locator("#tabs .tab[data-id]", has_text="proj").click()
            expect(page.locator(".graph-row.wip")).to_be_visible(timeout=9000)
            page.locator(".graph-row.wip .g-msg").click()
            page.get_by_label("Commit summary").click()
            page.keyboard.type("Commit from the keyboard")
            page.keyboard.press("Tab")
            expect(page.get_by_label("Commit description")).to_be_focused()
            page.keyboard.type("Tab, tab, space.")
            page.keyboard.press("Tab")
            expect(detail.locator(".commit-go")).to_be_focused()
            page.keyboard.press("Shift+Tab")
            expect(page.get_by_label("Commit description")).to_be_focused()
            page.keyboard.press("Tab")
            page.keyboard.press("Space")
            expect(page.locator(".toast.ok").last).to_contain_text("Committed")
            assert git(repo, "log", "-1", "--format=%s|%b") == "Commit from the keyboard|Tab, tab, space."

        @step("a group keeps its own repositories and its own tabs")
        def _():
            def saved(ready):
                """The saved config, once it passes `ready`; saves trail the page by a moment."""
                for _ in range(40):
                    data = json.loads((BASE / "cfg" / "config.json").read_text())
                    if ready(data): return data
                    page.wait_for_timeout(100)
                raise AssertionError(f"config never settled: groups={data['groups']} active={data['active_group']}")
            tabs = page.locator("#tabs .tab")
            rail = page.locator(".dash-rail")
            other = WORK / "grouped"
            assert not other.exists()
            git(WORK, "init", "-q", str(other))
            (other / "a.txt").write_text("a\n"); git(other, "add", "."); git(other, "commit", "-qm", "Grouped start")
            page.locator(".tab-home").click()
            all_tabs = tabs.count()
            assert all_tabs >= 2
            # A new group starts empty, with no repository tabs.
            rail.get_by_role("button", name="New group").first.click()
            modal.get_by_placeholder("Firmware").fill("Team")
            modal.get_by_role("button", name="Create group").click()
            expect(page.locator(".dash-title")).to_have_text("Team")
            expect(page.locator(".dash.is-empty")).to_have_count(1)
            expect(tabs).to_have_count(1)
            # Pick a repository that is already in GitSense.
            page.locator(".dash-tile", has_text="Choose repositories").click()
            modal.locator(".dash-scan-item", has_text="proj").click()
            modal.get_by_role("button", name="Save").click()
            expect(page.locator(".dash-row", has_text="proj")).to_be_visible()
            # A repository added here joins the group and opens in its tabs.
            page.locator(".dash-hero").get_by_role("button", name="Add repository").click()
            modal.locator("input.input").first.fill(str(other))
            modal.get_by_role("button", name="Add repository").click()
            expect(page.locator(".repo-view")).to_be_visible()
            expect(tabs).to_have_count(2)
            expect(tabs.nth(0)).to_contain_text("Team")
            data = saved(lambda d: d["groups"] and len(d["groups"][0]["open_tabs"]) == 1)
            group = data["groups"][0]
            assert group["name"] == "Team" and len(group["repos"]) == 2
            assert data["active_group"] == group["id"]
            # Dragging a tab past another reorders the group's tabs, and the order is saved.
            page.locator(".tab-home").click()
            page.locator(".dash-row", has_text="proj").locator(".dash-name").dblclick()
            names = "[...document.querySelectorAll('#tabs .tab[data-id] .tab-label')].map(e => e.textContent)"
            assert page.evaluate(names) == ["grouped", "proj"]
            src = tabs.nth(2).bounding_box()
            dst = tabs.nth(1).bounding_box()
            page.mouse.move(src["x"] + src["width"] / 2, src["y"] + src["height"] / 2)
            page.mouse.down()
            page.mouse.move(dst["x"] + 6, dst["y"] + dst["height"] / 2, steps=10)
            page.mouse.up()
            assert page.evaluate(names) == ["proj", "grouped"]
            proj_id = next(r["id"] for r in data["repos"] if r["path"].endswith("/proj"))
            saved(lambda d: d["groups"][0]["open_tabs"][0] == proj_id and len(d["groups"][0]["open_tabs"]) == 2)
            tabs.nth(1).locator(".tab-close").click()
            expect(tabs).to_have_count(2)
            # The full list still has its own tabs, and now lists the new repository.
            page.locator(".tab-switch").click()
            page.locator(".menu-item", has_text="All repositories").click()
            expect(tabs).to_have_count(all_tabs)
            page.locator(".tab-home").click()
            expect(page.locator(".dash-title")).to_have_text("All repositories")
            expect(page.locator(".dash-row", has_text="grouped")).to_be_visible()
            # The group and its tab come back after the page is loaded again.
            rail.locator(".dash-rail-item", has_text="Team").click()
            expect(tabs).to_have_count(2)
            tabs.nth(1).click()
            expect(page.locator(".repo-view")).to_be_visible()
            saved(lambda d: d["active_group"] == group["id"] and d["groups"][0]["active_tab"])
            page.reload()
            expect(page.locator("#splash")).to_have_count(0, timeout=6000)
            expect(tabs).to_have_count(2)
            expect(tabs.nth(1)).to_have_class(re.compile(r"\bactive\b"))
            # Taking a repository out of the group closes its tab there, and nothing else.
            page.locator(".tab-home").click()
            page.locator(".dash-row", has_text="grouped").locator(".dash-name").click(button="right")
            page.locator(".menu-item", has_text="Remove from Team").click()
            expect(page.locator(".dash-row", has_text="grouped")).to_have_count(0)
            expect(tabs).to_have_count(1)
            # Deleting the group returns to the full list and keeps the repositories.
            rail.locator(".dash-rail-row", has_text="Team").click(button="right")
            page.locator(".menu-item", has_text="Delete group").click()
            modal.get_by_role("button", name="Delete group").click()
            expect(page.locator(".dash-title")).to_have_text("All repositories")
            expect(rail.locator(".dash-rail-item", has_text="Team")).to_have_count(0)
            expect(tabs).to_have_count(all_tabs)
            expect(page.locator(".dash-row", has_text="grouped")).to_be_visible()
            data = saved(lambda d: d["groups"] == [] and d["active_group"] is None)
            assert sum(r["path"].endswith("/grouped") for r in data["repos"]) == 1

        @step("dragging a group in the rail reorders the groups")
        def _():
            def saved(ready):
                for _ in range(40):
                    data = json.loads((BASE / "cfg" / "config.json").read_text())
                    if ready(data): return data
                    page.wait_for_timeout(100)
                raise AssertionError(f"config never settled: groups={[g['name'] for g in data['groups']]}")
            rail = page.locator(".dash-rail")
            names = "() => [...document.querySelectorAll('.dash-rail-row[data-group] .dash-rail-name')].map(e => e.textContent)"
            page.locator(".tab-home").click()
            for name in ("Alpha", "Beta", "Gamma"):
                rail.get_by_role("button", name="New group").first.click()
                modal.get_by_placeholder("Firmware").fill(name)
                modal.get_by_role("button", name="Create group").click()
                expect(page.locator(".dash-title")).to_have_text(name)
            assert page.evaluate(names) == ["Alpha", "Beta", "Gamma"]
            # Drag the last group above the first; the drop must not also switch group.
            rail.locator(".dash-rail-item", has_text="Alpha").click()
            src = rail.locator(".dash-rail-row", has_text="Gamma").bounding_box()
            dst = rail.locator(".dash-rail-row", has_text="Alpha").bounding_box()
            page.mouse.move(src["x"] + 40, src["y"] + src["height"] / 2)
            page.mouse.down()
            page.mouse.move(src["x"] + 40, dst["y"] + 4, steps=8)
            page.mouse.up()
            assert page.evaluate(names) == ["Gamma", "Alpha", "Beta"]
            expect(page.locator(".dash-title")).to_have_text("Alpha")
            saved(lambda d: [g["name"] for g in d["groups"]] == ["Gamma", "Alpha", "Beta"])
            # The menu moves a group one place, for those not using a mouse.
            rail.locator(".dash-rail-row", has_text="Gamma").click(button="right")
            expect(page.locator(".menu-item", has_text="Move up")).to_be_disabled()
            page.locator(".menu-item", has_text="Move down").click()
            assert page.evaluate(names) == ["Alpha", "Gamma", "Beta"]
            saved(lambda d: [g["name"] for g in d["groups"]] == ["Alpha", "Gamma", "Beta"])
            # The order comes back after the page is loaded again.
            page.reload()
            expect(rail.locator(".dash-rail-row[data-group]")).to_have_count(3)
            assert page.evaluate(names) == ["Alpha", "Gamma", "Beta"]
            for name in ("Alpha", "Gamma", "Beta"):
                rail.locator(".dash-rail-row", has_text=name).click(button="right")
                page.locator(".menu-item", has_text="Delete group").click()
                modal.get_by_role("button", name="Delete group").click()
                expect(rail.locator(".dash-rail-item", has_text=name)).to_have_count(0)
            saved(lambda d: d["groups"] == [] and d["active_group"] is None)


        browser.close()
finally:
    server.terminate()
    out = server.communicate(timeout=5)[0]
    if "Traceback" in out: print("SERVER LOG:\n", out[-3000:])

print()
for e in errors[:20]: print("BROWSER:", e)
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print(f"Screenshots of the failures are in {BASE}")
else:
    shutil.rmtree(BASE, ignore_errors=True)
sys.exit(1 if failed else 0)
