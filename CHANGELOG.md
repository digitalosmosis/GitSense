# Changelog

## 1.0.0 (unreleased)

First version.

- Repository list
  - Shows branch, ahead/behind, uncommitted changes and last commit for every repo
  - Add a folder, scan a folder for repos, clone from a URL or from GitHub
  - Fetch all repositories at once
- Repository groups
  - A group holds the repositories you pick; a repository can be in several
  - Each group keeps its own open tabs, and the last group used reopens at launch
  - Drag a tab or a group to a new place to reorder them
  - Add repositories from a tick list, a row's menu, or by dragging a row onto the group
- Commit graph
  - Branches, tags, stashes and uncommitted changes in one history
  - Find commits by message, author, SHA or branch name
  - Selecting a commit highlights its ancestors and descendants and fades the rest
- Working directory
  - Stage, unstage and discard by file, hunk or line
  - Commit and amend
  - Tab goes from summary to description to the commit button, so Space commits
- Diff viewer
  - Unified and side-by-side layouts
  - Whole-file view, ignore whitespace, file history
- Branch, tag and stash operations from context menus
  - Check out, merge, rebase, cherry-pick, revert, reset, push, pull
  - A checkout blocked by local changes offers to stash them first
  - A rejected push offers a force push with lease
- Fetch, Pull and Push buttons show when they are running
  - A running button shows a spinner, and more clicks do not queue another request
  - Right-click a running button to cancel it
  - A pull can be cancelled while it fetches, but not once it starts updating the branch
- Merge, rebase and cherry-pick conflicts
  - Take one side or mark files resolved
  - Continue, skip or abort
- GitHub pull requests through the `gh` CLI
  - List with check and review status, check out, create
- Themes
  - Graphite by default, plus Daylight, Paper, Midnight and Evergreen
  - Follows the system light or dark setting, or stays on one theme
  - Duplicate any theme and edit its colors with a live preview
  - Export and import themes to share them
- Runs in a native window on macOS, or in the browser with `--browser`
  - `gitsense --install-app` adds GitSense to Applications, with its own icon
  - The window reopens at the size it was closed
  - The app opens only from its window or the address it prints, so other users on the Mac cannot use it
- Loading splash with the GitSense icon surfing the commit graph, in the colors of the last theme used
- Mods add themes, a loading splash and an app icon, with no code
  - `gitsense --install-mod` installs one from a folder or a .zip
  - `--uninstall-mod` and `--list-mods` manage installed mods
  - Settings switches each mod on or off
