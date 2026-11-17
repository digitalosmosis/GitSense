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
- **Mods:** add themes, a loading splash and an app icon that someone else made. See [Mods](#mods).

## Requirements

- macOS (tested). Linux and Windows are expected to work but are not tested yet.
- `git` 2.30 or later.
- [`uv`](https://docs.astral.sh/uv/). It installs Python 3.14 for you.
- Optional: the [GitHub CLI](https://cli.github.com) (`gh`), for the GitHub features.

If you don't have uv yet, install it with Homebrew:

```sh
brew install uv
```

Or with its installer, on macOS or Linux:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows, see the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

## Install

```sh
uv tool install git+https://github.com/digitalosmosis/GitSense.git
```

Then start it:

```sh
gitsense
```

If your shell says `command not found: gitsense`, let uv add its tools folder to your PATH, then open a new terminal:

```sh
uv tool update-shell
```

### Add GitSense to Applications (macOS)

To open GitSense from Finder, Launchpad or Spotlight:

```sh
gitsense --install-app
```

- The app goes in `/Applications`, or in `~/Applications` if that is not writable.
- To put it somewhere else, pass a folder: `gitsense --install-app ~/Desktop`.
- The app runs the `gitsense` you installed, so updating GitSense updates the app too.
- Its log goes to `~/Library/Logs/GitSense/gitsense.log`.

### Native window on Linux and Windows

On macOS GitSense opens in its own window. On Linux and Windows it opens in your web browser, unless you install it with the window extra instead:

```sh
uv tool install "gitsense[window] @ git+https://github.com/digitalosmosis/GitSense.git"
```

### GitHub features

The pull request features use the GitHub CLI. Install it, then sign in once:

```sh
gh auth login
```

## Update

```sh
uv tool upgrade gitsense
```

On macOS, after an update, run `gitsense --install-app` again if the app icon changed.

## Use

```sh
gitsense
```

| Command | What it does |
|---|---|
| `gitsense` | Open GitSense |
| `gitsense ~/Repos/MyProject` | Add a repository and open it in a tab |
| `gitsense --browser` | Open in your web browser instead of a window |
| `gitsense --version` | Show the installed version |
| `gitsense --help` | List every option |

### Keyboard

| Keys | What they do |
|---|---|
| Cmd/Ctrl+1 | The repository list |
| Cmd/Ctrl+2 to 9 | Switch to a tab |
| Cmd/Ctrl+W | Close the tab |
| Cmd/Ctrl+F | Find commits |
| Cmd/Ctrl+Enter | Commit |
| Tab, in the commit box | Summary, then description, then the commit button |
| Esc | Close a diff, or clear a file selection |
| Cmd/Ctrl+click a staged or unstaged file | Add it to the selection, or take it out |
| Shift+click a staged or unstaged file | Select the files from the last click to this one |
| Space, on a file | Stage or unstage it, or every selected file |

Drag a tab sideways to reorder the tabs.

### Fetch, Pull and Push

- A running Fetch, Pull or Push button shows a spinner.
- Right-click a running button to cancel it.

## Mods

A mod changes how GitSense looks: extra themes, a different loading splash, a different app icon. Mods hold no code.

Install one from a folder or a .zip someone gave you:

```sh
gitsense --install-mod ~/Downloads/my-mod.zip
```

See what is installed:

```sh
gitsense --list-mods
```

Remove one by its id, as `--list-mods` shows it:

```sh
gitsense --uninstall-mod my-mod
```

- A new mod is switched on, and its themes are applied.
- Settings > Mods switches each mod on or off.
- If a mod changes the app icon, run `gitsense --install-app` again to update GitSense.app.

To make a mod, see [docs/mods.md](docs/mods.md).

## Uninstall

On macOS, remove the app first:

```sh
gitsense --uninstall-app
```

Then remove GitSense:

```sh
uv tool uninstall gitsense
```

Your settings stay in `~/.config/gitsense/`. Delete that folder too if you want them gone.

## Privacy and security

- GitSense runs the `git` and `gh` command line tools you already have, so your SSH keys, credential helper, commit signing and hooks all apply.
- It never stores a password or token. GitHub access goes through `gh`.
- The window talks to a small local server on `127.0.0.1` only, protected by a per-launch key.
- It makes no network requests of its own. No telemetry, no avatars, no update checks.
- Settings, groups and the repository list live in `~/.config/gitsense/config.json`, readable only by you.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for running GitSense from a clone, the tests and the code layout.

## Licenses

- GitSense is under the MIT License. See [LICENSE](LICENSE).
- Montserrat is bundled under the SIL Open Font License (`src/gitsense/web/fonts/OFL.txt`).
- Icons are from Lucide under the ISC license (`src/gitsense/web/LICENSE-lucide.txt`).
