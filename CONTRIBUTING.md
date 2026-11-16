# Contributing to GitSense

GitSense is written in Python 3.14, managed by [`uv`](https://docs.astral.sh/uv/). The UI is plain HTML, CSS and ES modules, with no build step.

## Set up a clone

```sh
git clone https://github.com/digitalosmosis/GitSense.git
```

```sh
cd GitSense
```

```sh
uv sync
```

## Run from the clone

```sh
uv run gitsense --debug
```

`--debug` logs every request to the terminal. Edit a file under `src/gitsense/web/` and reload the window to see it.

### Make the `gitsense` command run your clone

```sh
uv tool install --editable .
```

- Code changes then apply the next time GitSense starts.
- Run it again with `--force` if the dependencies in `pyproject.toml` change.

### Make GitSense.app run your clone (macOS)

```sh
scripts/install-macos-app.sh --dev
```

- The app runs this checkout, so code edits apply on its next launch.
- Pass a folder to put the app somewhere other than `/Applications`.

## Tests

The unit and server tests:

```sh
uv run pytest -q
```

The browser test of the main workflows:

```sh
uv run --with playwright python tests/e2e/run_e2e.py
```

- Both build throwaway repositories in a temp folder and use their own config.
- They never touch your real repositories or settings.
- To run GitSense by hand without touching your settings, set `GITSENSE_CONFIG_DIR` to a scratch folder.

## Layout

| Path | What |
|---|---|
| `src/gitsense/gitcmd.py` | Runs git and gh without prompts |
| `src/gitsense/repo.py` | Read side: refs, status, history, diffs |
| `src/gitsense/actions.py` | Write side: every operation that changes a repository |
| `src/gitsense/graph.py` | Graph lane layout |
| `src/gitsense/patch.py` | Diff parsing and partial patches |
| `src/gitsense/github.py` | GitHub features through `gh` |
| `src/gitsense/server.py` | The local HTTP API |
| `src/gitsense/cli.py` | The `gitsense` command and the window |
| `src/gitsense/macos_app.py` | Builds GitSense.app |
| `src/gitsense/mods.py` | Mods: checking, installing and serving them |
| `src/gitsense/web/` | The UI |
| `tools/gen_icons.py` | Regenerates `web/js/icons.js` from Lucide |
| `tools/build_icon.py` | Builds `assets/GitSense.icns` from `assets/icon.svg` (needs `rsvg-convert`) |

## Building a wheel to share

```sh
uv build --wheel
```

The wheel lands in `dist/`. Someone can install it with `uv tool install <path to the .whl>`.
