"""Persistent app state: the repository list, groups, open tabs and settings.

- Stored as JSON in ~/.config/gitsense/config.json (mode 0600).
- A group is a named set of repositories with its own open tabs.
- The top-level open_tabs and active_tab belong to the "All repositories" list.
- GITSENSE_CONFIG_DIR overrides the directory (used by tests).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "graphite",  # "system" or a theme id
    "theme_light": "daylight",  # used when theme is "system" and the OS is light
    "theme_dark": "graphite",  # used when theme is "system" and the OS is dark
    "custom_themes": [],  # user-made themes: {id, name, kind, colors}
    "mods": {},  # mod id -> False when the user switched it off; true once its defaults were applied
    "pull_mode": "ff-only",  # ff-only | rebase | merge
    "fetch_prune": True,
    "auto_fetch_minutes": 0,  # 0 disables background fetch
    "commit_limit": 2000,
    "clone_dir": "",
    "editor": "",  # command used to open files; empty uses the OS default
    "diff_view": "unified",  # unified | split
    "sidebar_width": 250,
    "detail_width": 380,
    "show_remote_branches_in_graph": True,
    "highlight_lineage": True,  # fade commits unrelated to the selected one
}


SETTING_CHOICES: dict[str, tuple[str, ...]] = {
    "pull_mode": ("ff-only", "rebase", "merge"),
    "diff_view": ("unified", "split"),
}

SETTING_RANGES: dict[str, tuple[int, int]] = {
    "auto_fetch_minutes": (0, 1440),
    "commit_limit": (50, 50000),
    "sidebar_width": (120, 1200),
    "detail_width": (200, 1600),
}


# Color tokens a theme may set. Must match THEME_TOKENS in web/js/themes.js.
THEME_TOKENS: tuple[str, ...] = (
    "bg", "panel", "panel-strong", "raised", "line", "line-soft", "hover", "selected", "highlight",
    "text", "text-strong", "muted", "title", "link",
    "accent", "accent-hover", "on-accent", "accent-2", "on-accent-2",
    "brand-1", "brand-2", "on-brand", "on-brand-muted", "on-brand-accent",
    "lane-0", "lane-1", "lane-2", "lane-3", "lane-4", "lane-5",
    "ok", "ok-bg", "ok-bg-strong", "danger", "danger-bg", "danger-bg-strong", "on-danger",
    "warn", "warn-bg",
)  # fmt: skip
MAX_CUSTOM_THEMES = 50
_THEME_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_LEGACY_THEMES = {"light": "daylight", "dark": "graphite"}
# Ids of the built-in themes. Must match BUILTIN_THEMES in web/js/themes.js.
BUILTIN_THEME_IDS: frozenset[str] = frozenset({"daylight", "paper", "graphite", "midnight", "evergreen"})
THEME_SETTINGS = ("theme", "theme_light", "theme_dark")
_MOD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_MODS = 100


MAX_GROUPS = 100
MAX_GROUP_NAME = 60
_GROUP_ID = re.compile(r"g-[0-9a-f]{8}")


class GroupError(ValueError):
    """A group change that cannot be made, with a message for the user."""


def clean_group_name(raw: Any) -> str:
    """A group name with outer and repeated spaces removed, or "" when unusable."""
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.split())[:MAX_GROUP_NAME].strip()


def _known(items: Any, allowed: set[str] | list[str]) -> list[str]:
    """The strings in items that are in allowed, in order, without repeats."""
    if not isinstance(items, (list, tuple)):
        return []
    return [i for i in dict.fromkeys(i for i in items if isinstance(i, str)) if i in allowed]


def clean_theme_colors(raw: Any) -> dict[str, str]:
    """The #RRGGBB colors of known tokens in raw, upper-cased. Anything else is dropped."""
    if not isinstance(raw, dict):
        return {}
    return {
        key: value.upper()
        for key, value in raw.items()
        if key in THEME_TOKENS and isinstance(value, str) and _HEX_COLOR.match(value)
    }


def clean_theme(raw: Any) -> dict[str, Any] | None:
    """Return a custom theme with only valid fields, or None when it is unusable.

    - The id must start with "custom-", so it can never replace a built-in theme.
    - Colors must be #RRGGBB and belong to THEME_TOKENS; anything else is dropped.
    """
    if not isinstance(raw, dict):
        return None
    theme_id = raw.get("id")
    if not isinstance(theme_id, str) or not _THEME_ID.match(theme_id) or not theme_id.startswith("custom-"):
        return None
    kind = raw.get("kind")
    if kind not in ("light", "dark"):
        return None
    clean = clean_theme_colors(raw.get("colors"))
    if not clean:
        return None
    name = raw.get("name")
    name = name.strip()[:60] if isinstance(name, str) and name.strip() else "Custom theme"
    theme: dict[str, Any] = {"id": theme_id, "name": name, "kind": kind, "colors": clean}
    based_on = raw.get("based_on")
    if isinstance(based_on, str) and _THEME_ID.match(based_on):
        theme["based_on"] = based_on
    return theme


def clean_painted_theme(raw: Any) -> dict[str, Any] | None:
    """Validate the record of the theme last shown: {kind, colors, follow_system}.

    - follow_system is True when the theme was picked by the OS light/dark setting.
    """
    if not isinstance(raw, dict) or raw.get("kind") not in ("light", "dark"):
        return None
    clean = clean_theme_colors(raw.get("colors"))
    if "bg" not in clean:
        return None
    return {"kind": raw["kind"], "colors": clean, "follow_system": raw.get("follow_system") is True}


def clean_window(raw: Any) -> dict[str, int] | None:
    """Validate a saved window size: {width, height} in points."""
    if not isinstance(raw, dict):
        return None
    try:
        width, height = int(raw["width"]), int(raw["height"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not (600 <= width <= 10000 and 400 <= height <= 10000):
        return None
    return {"width": width, "height": height}


def coerce_setting(key: str, value: Any) -> tuple[bool, Any]:
    """Validate one setting. Returns (accepted, cleaned value)."""
    if key not in DEFAULT_SETTINGS:
        return False, None
    default = DEFAULT_SETTINGS[key]
    if key == "custom_themes":
        if not isinstance(value, list):
            return False, None
        themes: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in value[:MAX_CUSTOM_THEMES]:
            theme = clean_theme(item)
            if theme and theme["id"] not in seen:
                seen.add(theme["id"])
                themes.append(theme)
        return True, themes
    if key == "mods":
        if not isinstance(value, dict):
            return False, None
        states = {k: v for k, v in value.items() if isinstance(k, str) and _MOD_ID.match(k) and isinstance(v, bool)}
        return True, dict(list(states.items())[:MAX_MODS])
    if key in THEME_SETTINGS:
        if not isinstance(value, str):
            return False, None
        value = _LEGACY_THEMES.get(value, value)
        if value == "system":
            return key == "theme", value
        return bool(_THEME_ID.match(value)), value
    if isinstance(default, bool):
        if isinstance(value, str):
            return True, value.strip().lower() in ("1", "true", "yes", "on")
        return True, bool(value)
    if isinstance(default, int):
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return False, None
        low, high = SETTING_RANGES.get(key, (number, number))
        return True, max(low, min(high, number))
    if isinstance(default, str):
        text = str(value)
        if key in SETTING_CHOICES and text not in SETTING_CHOICES[key]:
            return False, None
        return True, text
    return False, None


def config_dir() -> Path:
    override = os.environ.get("GITSENSE_CONFIG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "gitsense"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "gitsense"


def repo_id(path: str | Path) -> str:
    real = os.path.realpath(str(path))
    return hashlib.sha1(real.encode("utf-8")).hexdigest()[:12]


class Config:
    """Thread-safe config store with atomic saves."""

    def __init__(self, directory: Path | None = None):
        self.dir = directory or config_dir()
        self.path = self.dir / "config.json"
        self._lock = threading.RLock()
        self.data: dict[str, Any] = {
            "repos": [],
            "open_tabs": [],
            "active_tab": None,
            "groups": [],  # {id, name, repos, open_tabs, active_tab}
            "active_group": None,  # the group on screen last, or None for all repositories
            "settings": copy.deepcopy(DEFAULT_SETTINGS),
            # Facts about the last session, used to start without a flash.
            "ui": {"last_theme": None, "window": None},
        }
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        # A hand-edited file may hold any JSON, so check types before use.
        stored_repos = raw.get("repos")
        repos = [
            r
            for r in (stored_repos if isinstance(stored_repos, list) else [])
            if isinstance(r, dict) and isinstance(r.get("path"), str) and r["path"]
        ]
        for r in repos:
            if not isinstance(r.get("id"), str):
                r["id"] = repo_id(r["path"])
            r.setdefault("name", Path(r["path"]).name)
            r.setdefault("pinned", False)
            r.pop("group", None)  # an unused label from before groups existed
        self.data["repos"] = repos
        ids = {r["id"] for r in repos}
        tabs = raw.get("open_tabs")
        self.data["open_tabs"] = [
            t for t in (tabs if isinstance(tabs, list) else []) if isinstance(t, str) and t in ids
        ]
        active = raw.get("active_tab")
        self.data["active_tab"] = active if isinstance(active, str) and active in ids else None
        groups: list[dict[str, Any]] = []
        stored_groups = raw.get("groups")
        for g in (stored_groups if isinstance(stored_groups, list) else [])[:MAX_GROUPS]:
            if not isinstance(g, dict):
                continue
            gid, name = g.get("id"), clean_group_name(g.get("name"))
            if not isinstance(gid, str) or not _GROUP_ID.fullmatch(gid) or not name:
                continue
            # Ids and names stay unique, as they are for groups made in the app.
            if any(gid == other["id"] or name.casefold() == other["name"].casefold() for other in groups):
                continue
            members = _known(g.get("repos"), ids)
            group_active = g.get("active_tab")
            groups.append(
                {
                    "id": gid,
                    "name": name,
                    "repos": members,
                    "open_tabs": _known(g.get("open_tabs"), members),
                    "active_tab": group_active if isinstance(group_active, str) and group_active in members else None,
                }
            )
        self.data["groups"] = groups
        active_group = raw.get("active_group")
        self.data["active_group"] = active_group if any(g["id"] == active_group for g in groups) else None
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        stored = raw.get("settings")
        if isinstance(stored, dict):
            for key, value in stored.items():
                accepted, cleaned = coerce_setting(key, value)
                if accepted:
                    settings[key] = cleaned
        self.data["settings"] = settings
        ui = raw.get("ui")
        if isinstance(ui, dict):
            self.data["ui"] = {
                "last_theme": clean_painted_theme(ui.get("last_theme")),
                "window": clean_window(ui.get("window")),
            }

    # -- last session ---------------------------------------------------------

    def last_theme(self) -> dict[str, Any] | None:
        """The colors that were on screen last time: {kind, colors}, or None."""
        with self._lock:
            return copy.deepcopy(self.data["ui"]["last_theme"])

    def set_last_theme(self, raw: Any) -> dict[str, Any] | None:
        theme = clean_painted_theme(raw)
        with self._lock:
            if theme is not None and theme != self.data["ui"]["last_theme"]:
                self.data["ui"]["last_theme"] = theme
                self.save()
        return theme

    def window_size(self) -> tuple[int, int] | None:
        """Width and height of the native window when it was last closed."""
        with self._lock:
            window = self.data["ui"]["window"]
            return (window["width"], window["height"]) if window else None

    def set_window_size(self, width: Any, height: Any) -> None:
        window = clean_window({"width": width, "height": height})
        with self._lock:
            if window is not None and window != self.data["ui"]["window"]:
                self.data["ui"]["window"] = window
                self.save()

    def save(self) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.dir, 0o700)
            except OSError:
                pass
            fd, tmp = tempfile.mkstemp(dir=str(self.dir), prefix=".config-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self.data, fh, indent=2)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    # -- repositories -----------------------------------------------------

    def repos(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.data["repos"]]

    def get_repo(self, rid: str) -> dict[str, Any] | None:
        with self._lock:
            for r in self.data["repos"]:
                if r["id"] == rid:
                    return dict(r)
        return None

    def add_repo(self, path: str) -> dict[str, Any]:
        real = os.path.realpath(path)
        rid = repo_id(real)
        with self._lock:
            for r in self.data["repos"]:
                if r["id"] == rid:
                    return dict(r)
            entry = {
                "id": rid,
                "path": real,
                "name": Path(real).name,
                "pinned": False,
            }
            self.data["repos"].append(entry)
            self.save()
            return dict(entry)

    def remove_repo(self, rid: str) -> None:
        with self._lock:
            self.data["repos"] = [r for r in self.data["repos"] if r["id"] != rid]
            for scope in (self.data, *self.data["groups"]):
                scope["open_tabs"] = [t for t in scope["open_tabs"] if t != rid]
                if scope["active_tab"] == rid:
                    scope["active_tab"] = None
            for group in self.data["groups"]:
                group["repos"] = [r for r in group["repos"] if r != rid]
            self.save()

    def update_repo(self, rid: str, **changes: Any) -> dict[str, Any] | None:
        allowed = {"name", "pinned"}
        with self._lock:
            for r in self.data["repos"]:
                if r["id"] == rid:
                    r.update({k: v for k, v in changes.items() if k in allowed})
                    self.save()
                    return dict(r)
        return None

    # -- groups -----------------------------------------------------------

    def groups(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self.data["groups"])

    def _group(self, gid: Any) -> dict[str, Any] | None:
        return next((g for g in self.data["groups"] if g["id"] == gid), None)

    def _free_group_name(self, raw: Any, *, skip: str | None = None) -> str:
        """A cleaned group name that no other group uses. Raises GroupError."""
        name = clean_group_name(raw)
        if not name:
            raise GroupError("Give the group a name")
        taken = {g["name"].casefold() for g in self.data["groups"] if g["id"] != skip}
        if name.casefold() in taken:
            raise GroupError(f"There is already a group named {name}")
        return name

    def add_group(self, name: Any, repos: Any = ()) -> dict[str, Any]:
        """Create a group, optionally with repositories already in it. Raises GroupError."""
        with self._lock:
            name = self._free_group_name(name)
            if len(self.data["groups"]) >= MAX_GROUPS:
                raise GroupError(f"GitSense holds at most {MAX_GROUPS} groups")
            gid = f"g-{secrets.token_hex(4)}"
            while self._group(gid) is not None:
                gid = f"g-{secrets.token_hex(4)}"
            ids = [r["id"] for r in self.data["repos"]]
            group = {"id": gid, "name": name, "repos": _known(repos, ids), "open_tabs": [], "active_tab": None}
            self.data["groups"].append(group)
            self.save()
            return copy.deepcopy(group)

    def update_group(self, gid: Any, *, name: Any = None, add: Any = (), remove: Any = ()) -> dict[str, Any] | None:
        """Rename a group or change which repositories are in it.

        - Returns None when the group does not exist. Raises GroupError for a bad name.
        - A repository taken out of the group also loses its tab there.
        """
        with self._lock:
            group = self._group(gid)
            if group is None:
                return None
            if name is not None:
                group["name"] = self._free_group_name(name, skip=group["id"])
            ids = [r["id"] for r in self.data["repos"]]
            gone = set(_known(remove, ids))
            members = [r for r in group["repos"] if r not in gone]
            members += [r for r in _known(add, ids) if r not in members and r not in gone]
            group["repos"] = members
            group["open_tabs"] = [t for t in group["open_tabs"] if t in members]
            if group["active_tab"] not in members:
                group["active_tab"] = None
            self.save()
            return copy.deepcopy(group)

    def order_groups(self, order: Any) -> list[str]:
        """Put the groups in a new order and return the ids in the order kept.

        - Groups named in `order` come first, in that order.
        - Groups it leaves out follow, in the order they already had.
        - Unknown and repeated ids are ignored.
        """
        with self._lock:
            groups = self.data["groups"]
            wanted = _known(order, [g["id"] for g in groups])
            place = {gid: i for i, gid in enumerate(wanted)}
            groups.sort(key=lambda g: place.get(g["id"], len(place)))
            self.save()
            return [g["id"] for g in groups]

    def remove_group(self, gid: Any) -> bool:
        """Delete a group. Its repositories stay in GitSense."""
        with self._lock:
            group = self._group(gid)
            if group is None:
                return False
            self.data["groups"].remove(group)
            if self.data["active_group"] == gid:
                self.data["active_group"] = None
            self.save()
            return True

    # -- tabs and settings ------------------------------------------------

    def set_tabs(self, open_tabs: list[str], active: str | None, group: Any = None) -> bool:
        """Save the open tabs of one list and make that list the one on screen.

        - group is a group id, or None for the "All repositories" list.
        - A group only keeps tabs for repositories that are in it.
        - Returns False, changing nothing, when the group does not exist.
        """
        with self._lock:
            if group is None:
                scope, allowed = self.data, [r["id"] for r in self.data["repos"]]
            else:
                scope = self._group(group)
                if scope is None:
                    return False
                allowed = scope["repos"]
            scope["open_tabs"] = _known(list(open_tabs), allowed)
            scope["active_tab"] = active if isinstance(active, str) and active in allowed else None
            self.data["active_group"] = None if group is None else scope["id"]
            self.save()
            return True

    def current_tab(self) -> str | None:
        """The repository tab on screen: the active tab of the list last shown."""
        with self._lock:
            scope = self._group(self.data["active_group"]) or self.data
            return scope["active_tab"]

    def settings(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.data["settings"])

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            for key, value in changes.items():
                accepted, cleaned = coerce_setting(key, value)
                if accepted:
                    self.data["settings"][key] = cleaned
            self.save()
            return copy.deepcopy(self.data["settings"])

    def drop_missing_themes(self, known: set[str]) -> bool:
        """Point theme settings that name a theme no longer there back at the defaults.

        - known holds every theme id that exists now: built-in, custom and from mods.
        - When both system picks were missing, a fixed "system" choice goes back to the default too.
        - The colors remembered for the first frame are dropped, so the old theme does not flash.
        - Returns True when anything changed.
        """
        with self._lock:
            settings = self.data["settings"]
            gone = {k for k in THEME_SETTINGS if settings[k] != "system" and settings[k] not in known}
            if not gone:
                return False
            if {"theme_light", "theme_dark"} <= gone and settings["theme"] == "system":
                gone.add("theme")
            for key in gone:
                settings[key] = DEFAULT_SETTINGS[key]
            self.data["ui"]["last_theme"] = None
            self.save()
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.data))
