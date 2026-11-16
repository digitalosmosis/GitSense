# GitSense

A Git and GitHub desktop client for your local repositories.

It covers the everyday GitKraken workflow (commit graph, staging, branches, stashes, merges, pull requests) without an account, a subscription or built-in AI prompts. It only works with local repositories and GitHub.

## What it does

- **All repositories:** one table of every repo you add, with branch, ahead/behind, uncommitted changes and last commit. Scan a folder to add many at once, clone from a URL or from your GitHub repo list, fetch them all.
- **Groups:** keep sets of repositories together. Each group has its own list and its own open tabs, and a repository can be in more than one. Drag a group up or down in the list to reorder the groups.
- **Commit graph:** every branch and tag as colored lanes, with stashes and uncommitted changes in place. Selecting a commit highlights its ancestors and descendants. Find commits by message, author or SHA.
- **Working directory:** stage and unstage files, hunks or single lines; discard; commit and amend.
- **Diffs:** unified or side by side, whole-file view, ignore whitespace, file history.
- **Branches and tags:** create, rename, delete, check out, merge, rebase, cherry-pick, revert, reset, push, pull, set upstream.
- **Conflicts:** see the conflicted files, take one side or mark resolved, then continue, skip or abort.
- **Stashes:** save, apply, pop, drop.
- **GitHub:** open pull requests with check and review status, check a PR out, create a PR.
- **Themes:** Graphite, Daylight and three more. Duplicate any of them and change every color, or import a theme someone shared.
- **Mods:** add themes, a loading splash and an app icon from a folder of files. See [Mods](#mods).
- Opens in its own window, or in a browser with `--browser`.

## Requirements

- macOS (tested). Linux and Windows are expected to work in the browser but are not tested yet.
- `git` 2.30 or later
- [`uv`](https://docs.astral.sh/uv/) (it installs Python 3.14 for you)
- Optional: the [GitHub CLI](https://cli.github.com) (`gh`), signed in with `gh auth login`, for the GitHub features

## Install

```sh
uv tool install git+ssh://git@github.com/digitalosmosis/GitSense.git
gitsense
```

To update later: `uv tool upgrade gitsense`.

On macOS the app opens in a native window. On Linux and Windows it opens in your browser unless you install the window extra:

```sh
uv tool install "gitsense[window] @ git+ssh://git@github.com/digitalosmosis/GitSense.git"
```

### Install as a macOS app

To open GitSense from Finder, Launchpad or Spotlight, run this in a clone of the repository:

```sh
scripts/install-macos-app.sh            # installs gitsense with uv, then adds GitSense.app
scripts/install-macos-app.sh --dev      # the app runs this checkout instead
```

- The app goes in `/Applications`, or `~/Applications` if that is not writable. Pass a folder to choose.
- If `gitsense` is already installed, `gitsense --install-app` does the same. `gitsense --uninstall-app` removes it.
- Its output goes to `~/Library/Logs/GitSense/gitsense.log`.

## Use

```sh
gitsense                    # open the app
gitsense ~/Repos/MyProject  # add a repository and open it in a tab
gitsense --browser          # use the web browser instead of a window
gitsense --help
```

Keyboard: Cmd/Ctrl+1 for the repository list, Cmd/Ctrl+2-9 for tabs, Cmd/Ctrl+W to close a tab, Cmd/Ctrl+F to find commits, Cmd/Ctrl+Enter to commit, Esc to close a diff. In the commit box, Tab moves from the summary to the description to the commit button. Drag a tab sideways to reorder the tabs.

## Mods

A mod changes how GitSense looks. It is a folder holding a `mod.json` manifest and asset files. Mods hold no code: no JavaScript and no Python.

```sh
gitsense --install-mod path/to/mod      # a folder or a .zip of one
gitsense --list-mods
gitsense --uninstall-mod <id>
```

- Installed mods live in `~/.config/gitsense/mods/<id>/`.
- A new mod is switched on. Settings > Mods switches each one on or off, and reloads the window.
- A mod that cannot be read is skipped with a message on the terminal; GitSense still starts.

`mod.json`, where every key but `id` is optional:

```json
{
  "id": "my-mod",
  "name": "My mod",
  "version": "1.0.0",
  "description": "What it changes.",
  "themes": [{ "id": "my-dark", "name": "My Dark", "kind": "dark", "colors": { "bg": "#101418", "...": "..." } }],
  "default_themes": { "light": "my-light", "dark": "my-dark" },
  "stylesheets": ["style.css"],
  "splash": "splash.html",
  "icon": { "svg": "icon.svg", "png": "icon-512.png", "icns": "GitSense.icns" }
}
```

| Key | What it does |
|---|---|
| `id` | Lowercase letters, digits and dashes. It names the mod's folder. |
| `themes` | Added to the theme picker. They use the same color names as an exported theme, and cannot be edited, only duplicated. Ids may not clash with built-in themes or start with `custom-`. |
| `default_themes` | Applied when the mod is installed or switched on. With both, GitSense follows the system between them; with one, it uses that one. When the mod is switched off or removed, settings that pointed at its themes go back to Graphite and Daylight. |
| `stylesheets` | CSS files added to the page after GitSense's own. |
| `splash` | An HTML fragment for the loading splash. Text between `<!-- splash:rider -->` and `<!-- /splash:rider -->` replaces the icon riding the graph. Text between `<!-- splash:lines -->` and `<!-- /splash:lines -->` replaces the three commands; give each `<span class="sp-type sp-type-N" style="--n:LENGTH">`. Scripts, event handlers and outside addresses are refused. |
| `icon` | Replaces the window icon (`svg`, or `png`), the Dock icon and the GitSense.app icon (`icns`, or `png`). Run `gitsense --install-app` again to update GitSense.app. |

- When several mods are on, all their themes and stylesheets apply. The first mod by id with a splash or an icon supplies it.
- Mod files are served only to the GitSense window, only from inside the mod's folder, and only as CSS, images, fonts or JSON.
- A mod may hold `.css`, `.svg`, `.png`, `.jpg`, `.webp`, font, `.json`, `.html` (the splash), `.icns`, `.md` and `.txt` files, up to 4 MB each and 16 MB in all.

## How it works

- GitSense runs the `git` and `gh` command line tools you already have, so your SSH keys, credential helper, commit signing and hooks all apply.
- It never stores a password or token. GitHub access goes through `gh`.
- The UI is served by a small local server on `127.0.0.1` only, protected by a per-launch token.
- It makes no network requests of its own. No telemetry, no avatars, no update checks.
- Settings, groups and the repository list live in `~/.config/gitsense/config.json` (mode 0600).

## Development

Python 3.14, managed by `uv`.

```sh
uv sync
uv run gitsense --debug
uv run pytest -q
uv run --with playwright python tests/e2e/run_e2e.py   # browser test of the main workflows
```

Both test suites build throwaway repositories in a temp folder and use their own config, so they never touch your real repositories or settings.

Layout:

| Path | What |
|---|---|
| `src/gitsense/gitcmd.py` | Runs git and gh without prompts |
| `src/gitsense/repo.py` | Read side: refs, status, history, diffs |
| `src/gitsense/actions.py` | Write side: every operation that changes a repository |
| `src/gitsense/graph.py`, `patch.py` | Graph lane layout; diff parsing and partial patches |
| `src/gitsense/github.py` | GitHub features through `gh` |
| `src/gitsense/server.py`, `cli.py` | Local HTTP API and the launcher |
| `src/gitsense/mods.py` | Mods: checking, installing and serving them |
| `src/gitsense/web/` | The UI: plain HTML, CSS and ES modules, no build step |
| `tools/gen_icons.py` | Regenerates `web/js/icons.js` from Lucide |
| `tools/build_icon.py` | Builds `assets/GitSense.icns` from `assets/icon.svg` (needs `rsvg-convert`) |

The frontend has no build step. Edit a file under `src/gitsense/web/` and reload the window.

Config and secrets go in `~/.config/gitsense/` (mode 0600), never in this repo. The repo must stay private.

## Licenses

- Montserrat is bundled under the SIL Open Font License (`web/fonts/OFL.txt`).
- Icons are from Lucide under the ISC license (`web/LICENSE-lucide.txt`).
