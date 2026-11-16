"""Local HTTP server: a JSON API over the git layer plus the static web UI.

- Listens on 127.0.0.1 only.
- API calls need the per-launch token, sent in the X-GitSense-Token header.
- The page that holds the token needs the per-launch key in its URL (?k=), so
  another local user cannot read the token from it.
- The Host header must be the loopback address (blocks DNS rebinding).
- Files of enabled mods are served under /mods/<id>/.
- Those need a cookie that the page sets, or the launch key.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from . import __version__, actions, github, mods as mods_mod, repo as repo_mod, system
from .config import BUILTIN_THEME_IDS, Config, GroupError
from .gitcmd import NETWORK_TIMEOUT, CancelToken, Cancelled, GitError, cancellable, git, git_result

WEB_ROOT = Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 47615
MAX_BODY = 8 * 1024 * 1024  # largest request body accepted, in bytes
INDEX_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
# Mod files are data: a file opened on its own gets no script and no network.
MOD_CSP = "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; sandbox"

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("image/svg+xml", ".svg")


# Network actions the UI can cancel while they wait or run.
CANCELLABLE = frozenset({"fetch", "pull", "push"})
# Seconds a cancel waits for an action that has not reached the server yet.
EARLY_CANCEL_TTL = 30.0


def _op_id(value: Any) -> str:
    """The client's id for one action: a short token of URL-safe characters."""
    op = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", op):
        raise ApiError("Bad operation id")
    return op


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _folder(value: Any) -> str:
    """A folder path sent by the UI.

    - Used exactly as sent when that folder exists, since a name may end in a space.
    - Otherwise trimmed, for a pasted path with a stray space.
    """
    raw = os.path.expanduser(str(value or ""))
    return raw if raw.strip() and os.path.isdir(raw) else os.path.expanduser(str(value or "").strip())


class App:
    """Holds the config and implements every API route."""

    def __init__(self, config: Config | None = None, *, native: bool = False):
        self.config = config or Config()
        self.token = secrets.token_urlsafe(24)
        self.launch_key = secrets.token_urlsafe(24)
        self.native = native
        # Fetch, pull and push in flight, by (repository id, client op id), so they can be cancelled.
        self._ops: dict[tuple[str, str], CancelToken] = {}
        self._ops_guard = threading.Lock()
        # Cancels that arrived before their action did, with the time they arrived.
        self._early_cancels: dict[tuple[str, str], float] = {}
        # Called with the theme record whenever the page reports new colors.
        self.on_theme_painted: Callable[[dict[str, Any]], None] | None = None
        # Mods: the installed ones, and the secret that the page's cookie carries for their files.
        self.mods_dir = mods_mod.mods_dir(self.config.dir)
        self.mods_key = secrets.token_urlsafe(24)
        self._mods: list[mods_mod.Mod] = []
        self._mods_guard = threading.Lock()
        self.refresh_mods(report=True)
        self.get_routes: dict[str, Callable[[dict[str, str]], Any]] = {
            "/api/state": self.state,
            "/api/repos/summary": self.repo_summary,
            "/api/repo/overview": self.repo_overview,
            "/api/repo/log": self.repo_log,
            "/api/repo/fingerprint": self.repo_fingerprint,
            "/api/repo/commit": self.repo_commit,
            "/api/repo/diff": self.repo_diff,
            "/api/repo/file_history": self.repo_file_history,
            "/api/github/status": lambda q: github.status(),
            "/api/github/prs": self.github_prs,
            "/api/github/repos": lambda q: {"repos": github.list_repos(q.get("owner") or None)},
            "/api/github/orgs": lambda q: {"orgs": github.list_orgs()},
        }
        self.post_routes: dict[str, Callable[[dict[str, Any]], Any]] = {
            "/api/repos/add": self.repos_add,
            "/api/repos/add_many": self.repos_add_many,
            "/api/repos/remove": self.repos_remove,
            "/api/repos/update": self.repos_update,
            "/api/repos/scan": self.repos_scan,
            "/api/repos/clone": self.repos_clone,
            "/api/repos/init": self.repos_init,
            "/api/tabs": self.set_tabs,
            "/api/groups/create": self.groups_create,
            "/api/groups/update": self.groups_update,
            "/api/groups/delete": self.groups_delete,
            "/api/groups/order": self.groups_order,
            "/api/settings": self.set_settings,
            "/api/mods/set": self.mods_set,
            "/api/pick_folder": lambda b: system.pick_folder(str(b.get("start") or "")),
            "/api/repo/action": self.repo_action,
            "/api/repo/cancel": self.repo_cancel,
            "/api/repo/open": self.repo_open,
            "/api/open_url": self.open_url,
            "/api/ui/theme": self.ui_theme,
            "/api/github/pr_create": self.github_pr_create,
            "/api/github/pr_checkout": self.github_pr_checkout,
        }

    # -- helpers ----------------------------------------------------------

    def _repo(self, params: dict[str, Any], *, writes: bool = False) -> str:
        """The saved path of the repository a request names.

        - writes is True for requests that change the repository.
        - Those also check that git resolves the folder to itself, not to a parent repository.
        """
        rid = params.get("id")
        entry = self.config.get_repo(str(rid)) if rid else None
        if entry is None:
            raise ApiError("Unknown repository", 404)
        if not Path(entry["path"]).is_dir():
            raise ApiError(f"Folder not found: {entry['path']}", 404)
        # Without its own .git, git would use a repository in a parent folder.
        if not os.path.lexists(os.path.join(entry["path"], ".git")):
            raise ApiError(f"Not a Git repository: {entry['path']}", 404)
        if writes:
            # samefile, not string equality: macOS paths may differ in letter case.
            try:
                same = os.path.samefile(repo_mod.repo_root(entry["path"]), entry["path"])
            except (GitError, OSError):
                same = False
            if not same:
                raise ApiError(f"Not a Git repository: {entry['path']}", 404)
        return entry["path"]

    # -- app state --------------------------------------------------------

    def state(self, q: dict[str, str]) -> dict[str, Any]:
        snap = self.config.snapshot()
        snap.update(
            version=__version__,
            home=str(Path.home()),
            native=self.native,
            platform=sys.platform,
            config_path=str(self.config.path),
            mods=self.mods_public(),
            mods_dir=str(self.mods_dir),
        )
        return snap

    # -- mods -------------------------------------------------------------

    def refresh_mods(self, *, report: bool = False) -> None:
        """Read the mods folder again and bring the settings in line with it.

        - A mod seen for the first time is switched on and its default themes are applied.
        - Settings of mods no longer installed are forgotten, so a reinstall counts as new.
        - Theme settings naming a theme that is gone fall back to the defaults.
        """
        with self._mods_guard:
            found = mods_mod.installed(self.mods_dir, report=report)
            settings = self.config.settings()
            states = settings["mods"]
            ids = {m.id for m in found}
            kept = {k: v for k, v in states.items() if k in ids}
            changes: dict[str, Any] = {}
            for mod in found:
                if mod.id not in kept:
                    kept[mod.id] = True
                    if not changes:
                        changes.update(self._default_theme_settings(mod))
            if kept != states:
                changes["mods"] = kept
            if changes:
                settings = self.config.update_settings(changes)
            self._mods = found
            known = set(BUILTIN_THEME_IDS) | {t["id"] for t in settings["custom_themes"]}
            known |= {t["id"] for m in self.enabled_mods() for t in m.themes}
            self.config.drop_missing_themes(known)

    @staticmethod
    def _default_theme_settings(mod: mods_mod.Mod) -> dict[str, Any]:
        """Theme settings that put a mod's default themes on screen.

        - With a light and a dark default, GitSense follows the system between them.
        - With only one, that theme is used whatever the system setting.
        """
        light, dark = mod.default_themes.get("light"), mod.default_themes.get("dark")
        if light and dark:
            return {"theme": "system", "theme_light": light, "theme_dark": dark}
        if light or dark:
            kind, theme_id = ("light", light) if light else ("dark", dark)
            return {"theme": theme_id, f"theme_{kind}": theme_id}
        return {}

    def enabled_mods(self) -> list[mods_mod.Mod]:
        states = self.config.settings()["mods"]
        return [m for m in self._mods if mods_mod.is_enabled(m.id, states)]

    def mods_public(self) -> list[dict[str, Any]]:
        states = self.config.settings()["mods"]
        return [m.public(mods_mod.is_enabled(m.id, states)) for m in self._mods]

    def mods_set(self, b: dict[str, Any]) -> dict[str, Any]:
        """Switch a mod on or off. Switching one on applies its default themes."""
        self.refresh_mods()
        mod = next((m for m in self._mods if m.id == b.get("id")), None)
        if mod is None:
            raise ApiError("Unknown mod", 404)
        on = b.get("enabled") is True
        states = dict(self.config.settings()["mods"])
        states[mod.id] = on
        changes: dict[str, Any] = {"mods": states}
        if on:
            changes.update(self._default_theme_settings(mod))
        self.config.update_settings(changes)
        self.refresh_mods()
        return {"settings": self.config.settings(), "mods": self.mods_public()}

    def mod_file(self, mod_id: str, rel: str) -> tuple[bytes, str] | None:
        """A file of an enabled mod that the page may load, or None."""
        mod = next((m for m in self.enabled_mods() if m.id == mod_id), None)
        return mods_mod.read_served(mod, rel) if mod else None

    def render_index(self, data: bytes) -> bytes:
        """index.html as served: the token, the first-paint theme and the enabled mods.

        - Mod stylesheets go in after the core ones.
        - The first mod with a splash fills the splash regions it marks.
        - The first mod with an SVG or PNG icon supplies the favicon.
        """
        self.refresh_mods()
        html = data.decode("utf-8")
        html = html.replace("__GITSENSE_TOKEN__", self.token)
        html = html.replace('data-first-paint=""', self.first_paint_attrs())
        enabled = self.enabled_mods()
        links = "".join(
            f'<link rel="stylesheet" href="/mods/{m.id}/{sheet}">\n' for m in enabled for sheet in m.stylesheets
        )
        html = html.replace("<!-- mod stylesheets -->\n", links, 1)
        for mod in enabled:
            if not mod.splash:
                continue
            try:
                regions = mods_mod.splash_regions(mod.splash_html())
            except (mods_mod.ModError, OSError, UnicodeDecodeError) as exc:
                print(f"gitsense: mod {mod.id}: {exc}", file=sys.stderr)
                continue
            for name, fragment in regions.items():
                pattern = re.compile(rf"(<!-- splash:{name} -->).*?(<!-- /splash:{name} -->)", re.S)
                html = pattern.sub(lambda m, f=fragment: f"{m.group(1)}\n{f}\n{m.group(2)}", html, count=1)
            break
        for mod in enabled:
            kind = "svg" if mod.icon.get("svg") else "png" if mod.icon.get("png") else None
            if kind:
                ctype = "image/svg+xml" if kind == "svg" else "image/png"
                html = html.replace(
                    '<link rel="icon" href="img/favicon.svg" type="image/svg+xml">',
                    f'<link rel="icon" href="/mods/{mod.id}/{mod.icon[kind]}" type="{ctype}">',
                    1,
                )
                break
        return html.encode("utf-8")

    def set_tabs(self, b: dict[str, Any]) -> dict[str, Any]:
        tabs = b.get("open_tabs")
        if not self.config.set_tabs(tabs if isinstance(tabs, list) else [], b.get("active"), b.get("group")):
            raise ApiError("Unknown group", 404)
        return {"ok": True}

    # -- groups -----------------------------------------------------------

    def groups_create(self, b: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"group": self.config.add_group(b.get("name"), b.get("repos") or [])}
        except GroupError as exc:
            raise ApiError(str(exc)) from None

    def groups_update(self, b: dict[str, Any]) -> dict[str, Any]:
        """Rename a group, or add and remove repositories by id."""
        try:
            group = self.config.update_group(
                b.get("id"), name=b.get("name"), add=b.get("add") or [], remove=b.get("remove") or []
            )
        except GroupError as exc:
            raise ApiError(str(exc)) from None
        if group is None:
            raise ApiError("Unknown group", 404)
        return {"group": group}

    def groups_delete(self, b: dict[str, Any]) -> dict[str, Any]:
        if not self.config.remove_group(b.get("id")):
            raise ApiError("Unknown group", 404)
        return {"ok": True}

    def groups_order(self, b: dict[str, Any]) -> dict[str, Any]:
        """Reorder the groups. Answers with the order that was saved."""
        return {"order": self.config.order_groups(b.get("ids") or [])}

    def set_settings(self, b: dict[str, Any]) -> dict[str, Any]:
        return {"settings": self.config.update_settings(b)}

    def ui_theme(self, b: dict[str, Any]) -> dict[str, Any]:
        """Remember the colors on screen, so the next start paints them at once."""
        theme = self.config.set_last_theme(b)
        if theme is not None and self.on_theme_painted is not None:
            try:
                self.on_theme_painted(theme)
            except Exception:  # noqa: BLE001 - the window chrome is cosmetic
                traceback.print_exc()
        return {"ok": theme is not None}

    def first_paint_attrs(self) -> str:
        """Attributes for <html> that put the last theme on the very first frame.

        - Without them the page shows the default light colors until the script runs.
        - Values come from the validated record, so they are safe to write into HTML.
        """
        theme = self.config.last_theme()
        if not theme:
            return ""
        style = ";".join(f"--{key}:{value}" for key, value in sorted(theme["colors"].items()))
        return f'data-theme="{theme["kind"]}" style="{style}"'

    def open_url(self, b: dict[str, Any]) -> dict[str, Any]:
        system.open_url(str(b.get("url") or ""))
        return {"ok": True}

    # -- repository list --------------------------------------------------

    def repos_add(self, b: dict[str, Any]) -> dict[str, Any]:
        path = _folder(b.get("path"))
        if not path:
            raise ApiError("Choose a folder")
        root = repo_mod.repo_root(path)
        return {"repo": self.config.add_repo(root)}

    def repos_add_many(self, b: dict[str, Any]) -> dict[str, Any]:
        added, failed = [], []
        for path in b.get("paths") or []:
            try:
                added.append(self.config.add_repo(repo_mod.repo_root(str(path))))
            except GitError as exc:
                failed.append({"path": path, "error": exc.message})
        return {"repos": added, "failed": failed}

    def repos_remove(self, b: dict[str, Any]) -> dict[str, Any]:
        self.config.remove_repo(str(b.get("id")))
        return {"ok": True}

    def repos_update(self, b: dict[str, Any]) -> dict[str, Any]:
        changes = {k: b[k] for k in ("name", "pinned") if k in b}
        entry = self.config.update_repo(str(b.get("id")), **changes)
        if entry is None:
            raise ApiError("Unknown repository", 404)
        return {"repo": entry}

    def repos_scan(self, b: dict[str, Any]) -> dict[str, Any]:
        path = os.path.expanduser(str(b.get("path") or "").strip())
        if not path:
            raise ApiError("Choose a folder to scan")
        depth = max(1, min(int(b.get("depth") or 3), 6))
        known = {r["path"] for r in self.config.repos()}
        found = repo_mod.scan_for_repos(path, depth=depth)
        return {"found": [{"path": p, "name": Path(p).name, "known": p in known} for p in found]}

    def repos_clone(self, b: dict[str, Any]) -> dict[str, Any]:
        url = str(b.get("url") or "").strip()
        parent = os.path.expanduser(str(b.get("parent") or "").strip())
        if not url or url.startswith("-"):
            raise ApiError("Enter a repository URL")
        if not parent or not Path(parent).is_dir():
            raise ApiError("Choose an existing folder to clone into")
        name = str(b.get("name") or "").strip() or re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
        # Path(name).name differs when the name holds a separator or drive (/, \, C:).
        if not name or name.startswith("-") or name != Path(name).name or name in (".", ".."):
            raise ApiError("Enter a folder name for the clone")
        dest = Path(parent) / name
        if dest.exists():
            raise ApiError(f"{dest} already exists")
        git(None, ["clone", "--", url, str(dest)], timeout=NETWORK_TIMEOUT, write=True)
        return {"repo": self.config.add_repo(str(dest))}

    def repos_init(self, b: dict[str, Any]) -> dict[str, Any]:
        path = _folder(b.get("path"))
        if not path:
            raise ApiError("Choose a folder")
        Path(path).mkdir(parents=True, exist_ok=True)
        if not repo_mod.is_repo(path):
            git(path, ["init"], write=True)
        return {"repo": self.config.add_repo(repo_mod.repo_root(path))}

    def repo_summary(self, q: dict[str, str]) -> dict[str, Any]:
        entry = self.config.get_repo(q.get("id", ""))
        if entry is None:
            raise ApiError("Unknown repository", 404)
        return repo_mod.summary(entry["path"])

    # -- one repository ---------------------------------------------------

    def repo_overview(self, q: dict[str, str]) -> dict[str, Any]:
        data = repo_mod.overview(self._repo(q))
        data["id"] = q["id"]
        return data

    def repo_log(self, q: dict[str, str]) -> dict[str, Any]:
        settings = self.config.settings()
        limit = int(q.get("limit") or settings["commit_limit"])
        return repo_mod.log(
            self._repo(q),
            limit=max(50, min(limit, 50000)),
            include_remotes=bool(settings["show_remote_branches_in_graph"]),
        )

    def repo_fingerprint(self, q: dict[str, str]) -> dict[str, Any]:
        return {"fingerprint": repo_mod.fingerprint(self._repo(q))}

    def repo_commit(self, q: dict[str, str]) -> dict[str, Any]:
        sha = q.get("sha", "")
        if not re.fullmatch(r"[0-9a-fA-F]{4,64}", sha):
            raise ApiError("Invalid commit")
        return repo_mod.commit_detail(self._repo(q), sha)

    def repo_diff(self, q: dict[str, str]) -> dict[str, Any]:
        sha = q.get("sha") or None
        if sha and not re.fullmatch(r"[0-9a-fA-F]{4,64}", sha):
            raise ApiError("Invalid commit")
        path = q.get("path") or ""
        if not path:
            raise ApiError("A file path is required")
        return repo_mod.file_diff(
            self._repo(q),
            q.get("kind", "unstaged"),
            path,
            sha=sha,
            orig_path=q.get("orig_path") or None,
            context=max(0, min(int(q.get("context") or 3), 100000)),
            ignore_ws=q.get("ignore_ws") == "1",
        )

    def repo_file_history(self, q: dict[str, str]) -> dict[str, Any]:
        path = q.get("path") or ""
        if not path:
            raise ApiError("A file path is required")
        return {"commits": repo_mod.file_history(self._repo(q), path)}

    def repo_action(self, b: dict[str, Any]) -> dict[str, Any]:
        path = self._repo(b, writes=True)
        action = str(b.get("action") or "")
        args = b.get("args") or {}
        if not isinstance(args, dict):
            raise ApiError("Action arguments must be an object")
        if action in ("fetch", "pull"):
            settings = self.config.settings()
            if action == "fetch":
                args.setdefault("prune", settings["fetch_prune"])
            else:
                args.setdefault("mode", settings["pull_mode"])
        op = b.get("op")
        if action not in CANCELLABLE or not op:
            return actions.perform(path, action, args)
        key = (str(b["id"]), _op_id(op))
        token = CancelToken()
        with self._ops_guard:
            if key in self._ops:
                raise ApiError("This action is already running")
            self._ops[key] = token
            if self._early_cancels.pop(key, None) is not None:
                token.cancel()
        try:
            with cancellable(token):
                return actions.perform(path, action, args)
        finally:
            with self._ops_guard:
                self._ops.pop(key, None)

    def repo_cancel(self, b: dict[str, Any]) -> dict[str, Any]:
        """Cancel a fetch, pull or push that is waiting or running.

        - cancelled is False when the action already finished or is past its safe point.
        """
        key = (str(b.get("id") or ""), _op_id(b.get("op")))
        with self._ops_guard:
            token = self._ops.get(key)
            if token is None:
                # The cancel may have overtaken its action. Remember it briefly;
                # if the action already finished, the entry just expires.
                now = time.monotonic()
                self._early_cancels = {k: t for k, t in self._early_cancels.items() if now - t < EARLY_CANCEL_TTL}
                self._early_cancels[key] = now
                return {"cancelled": True}
        return {"cancelled": token.cancel()}

    def repo_open(self, b: dict[str, Any]) -> dict[str, Any]:
        root = self._repo(b)
        what = b.get("what")
        target = root
        if b.get("path"):
            target = os.path.realpath(os.path.join(root, str(b["path"])))
            if os.path.commonpath([target, os.path.realpath(root)]) != os.path.realpath(root):
                raise ApiError("That path is outside the repository")
        if what == "terminal":
            system.open_terminal(root)
        elif what == "folder":
            system.reveal(target)
        elif what == "editor":
            system.open_in_editor(target, self.config.settings()["editor"])
        else:
            raise ApiError("Unknown target")
        return {"ok": True}

    # -- GitHub -----------------------------------------------------------

    def github_prs(self, q: dict[str, str]) -> dict[str, Any]:
        return {"prs": github.list_prs(self._repo(q))}

    def github_pr_create(self, b: dict[str, Any]) -> dict[str, Any]:
        return github.create_pr(self._repo(b, writes=True), b)

    def github_pr_checkout(self, b: dict[str, Any]) -> dict[str, Any]:
        return github.checkout_pr(self._repo(b, writes=True), int(b.get("number") or 0))


def make_handler(app: App, allowed_hosts: set[str], *, debug: bool = False) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "GitSense"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            if debug:
                sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

        # -- plumbing -----------------------------------------------------

        def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "").lower() in allowed_hosts

        def _authorized(self) -> bool:
            sent = self.headers.get("X-GitSense-Token") or ""
            return secrets.compare_digest(sent, app.token)

        def _api(self, table: dict[str, Callable[[Any], Any]], path: str, payload: Any) -> None:
            if not self._authorized():
                self._json({"error": "Not authorized"}, 403)
                return
            handler = table.get(path)
            if handler is None:
                self._json({"error": "Not found"}, 404)
                return
            try:
                self._json(handler(payload))
            except ApiError as exc:
                self._json({"error": exc.message}, exc.status)
            except Cancelled:
                self._json({"error": "Cancelled", "cancelled": True}, 409)
            except GitError as exc:
                self._json({"error": exc.message, "detail": (exc.stderr or exc.stdout or "").strip()}, 400)
            except (ValueError, KeyError, TypeError) as exc:
                if debug:
                    traceback.print_exc()
                self._json({"error": f"Bad request: {exc}"}, 400)
            except Exception as exc:  # noqa: BLE001 - report instead of dropping the connection
                traceback.print_exc()
                self._json({"error": f"Internal error: {exc}"}, 500)

        # -- verbs --------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                self._json({"error": "Bad host"}, 403)
                return
            url = urlsplit(self.path)
            if url.path.startswith("/api/"):
                query = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                self._api(app.get_routes, url.path, query)
                return
            if url.path.startswith("/mods/"):
                self._mod_file(url.path, url.query)
                return
            self._static(url.path, url.query)

        do_HEAD = do_GET  # noqa: N815

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok():
                self._json({"error": "Bad host"}, 403)
                return
            url = urlsplit(self.path)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            # Refuse before reading anything the caller sent.
            refusal = None
            if not self._authorized():
                refusal = ("Not authorized", 403)
            elif length < 0:
                refusal = ("Bad request", 400)
            elif length > MAX_BODY:
                refusal = ("Request too large", 413)
            if refusal:
                self.close_connection = True
                self._json({"error": refusal[0]}, refusal[1])
                return
            raw = self.rfile.read(length) if length else b""
            if not url.path.startswith("/api/"):
                self._json({"error": "Not found"}, 404)
                return
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                self._json({"error": "Invalid JSON"}, 400)
                return
            if not isinstance(payload, dict):
                self._json({"error": "Invalid JSON"}, 400)
                return
            self._api(app.post_routes, url.path, payload)

        def _cookie_name(self) -> str:
            # One cookie per port: browsers share 127.0.0.1 cookies between ports.
            return f"gitsense_mods_{self.server.server_address[1]}"

        def _mods_allowed(self, query: str) -> bool:
            """A mod file needs the cookie set with the page, or the launch key."""
            key = (parse_qs(query).get("k") or [""])[-1]
            if key and secrets.compare_digest(key, app.launch_key):
                return True
            try:
                jar = SimpleCookie(self.headers.get("Cookie") or "")
            except CookieError:
                return False
            sent = jar.get(self._cookie_name())
            return sent is not None and secrets.compare_digest(sent.value, app.mods_key)

        def _mod_file(self, path: str, query: str) -> None:
            if not self._mods_allowed(query):
                self._send(403, b"Not authorized", "text/plain; charset=utf-8")
                return
            parts = path.split("/", 3)  # "", "mods", id, rest
            found = app.mod_file(parts[2], parts[3]) if len(parts) == 4 else None
            if found is None:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            data, ctype = found
            self._send(200, data, ctype, {"Content-Security-Policy": MOD_CSP})

        def _static(self, path: str, query: str = "") -> None:
            if path in ("", "/"):
                path = "/index.html"
            target = (WEB_ROOT / path.lstrip("/")).resolve()
            if WEB_ROOT not in target.parents or not target.is_file():
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            if target.name == "index.html":
                key = (parse_qs(query).get("k") or [""])[-1]
                if not secrets.compare_digest(key, app.launch_key):
                    self._send(403, b"Open GitSense from its window or the link it printed.", "text/plain; charset=utf-8")
                    return
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            extra = {}
            if target.name == "index.html":
                data = app.render_index(data)
                extra["Content-Security-Policy"] = INDEX_CSP
                extra["Set-Cookie"] = f"{self._cookie_name()}={app.mods_key}; Path=/mods/; HttpOnly; SameSite=Strict"
            if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
                ctype += "; charset=utf-8"
            self._send(200, data, ctype, extra)

    return Handler


class Server:
    """The running HTTP server and its background thread."""

    def __init__(self, app: App, port: int = DEFAULT_PORT, *, debug: bool = False):
        self.app = app
        self.httpd = self._bind(app, port, debug)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"
        # The address that opens the app; it carries the launch key.
        self.launch_url = f"{self.url}?k={app.launch_key}"
        self._thread: threading.Thread | None = None

    @staticmethod
    def _bind(app: App, port: int, debug: bool) -> ThreadingHTTPServer:
        allowed: set[str] = set()
        last_error: OSError | None = None
        for candidate in (port, 0):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", candidate), make_handler(app, allowed, debug=debug))
            except OSError as exc:
                last_error = exc
                continue
            actual = httpd.server_address[1]
            allowed.update({f"127.0.0.1:{actual}", f"localhost:{actual}"})
            httpd.daemon_threads = True
            httpd.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return httpd
        raise last_error or OSError("could not bind a port")

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="gitsense-http", daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


__all__ = ["App", "Server", "DEFAULT_PORT", "git_result"]
