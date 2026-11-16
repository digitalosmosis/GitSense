"""Mods: folders of themes, stylesheets, a splash and an icon that change how GitSense looks.

- A mod is a folder with a mod.json manifest and asset files. It holds no code.
- Installed mods live in <config dir>/mods/<id>/.
- Every manifest is checked before use; a bad mod is reported and skipped, never fatal.
- Files are served only from inside the mod's folder, and only with an allowed extension.

The manifest, with every key but id optional:

    {
      "id": "my-mod", "name": "My mod", "version": "1.0.0", "description": "...",
      "themes": [{"id": "my-dark", "name": "My Dark", "kind": "dark", "colors": {...}}],
      "default_themes": {"light": "my-light", "dark": "my-dark"},
      "stylesheets": ["style.css"],
      "splash": "splash.html",
      "icon": {"svg": "icon.svg", "png": "icon-512.png", "icns": "GitSense.icns"}
    }
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, TextIO

from .config import BUILTIN_THEME_IDS, THEME_TOKENS, clean_theme_colors, config_dir

MANIFEST = "mod.json"
MOD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# One path segment of a file a manifest names: no hidden files, no separators.
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,99}$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,31}$")

MAX_FILE = 4 * 1024 * 1024  # largest single file in a mod, in bytes
MAX_TOTAL = 16 * 1024 * 1024  # largest mod, all files together
MAX_FILES = 200
MAX_SPLASH = 64 * 1024
MAX_THEMES = 20

# Files the server hands to the page, by extension.
SERVED_TYPES: dict[str, str] = {
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".json": "application/json; charset=utf-8",
}
# Files a mod may hold but that are never served.
# - .html is only the splash fragment, which the server writes into the page itself.
# - .icns is the macOS app icon.
# - .md and .txt are notes and licenses.
KEPT_ONLY = frozenset({".html", ".icns", ".md", ".txt"})
ALLOWED = frozenset(SERVED_TYPES) | KEPT_ONLY

ICON_KINDS = {"svg": ".svg", "png": ".png", "icns": ".icns"}


class ModError(ValueError):
    """A mod that cannot be used or installed. The message is for the user."""


@dataclass
class Mod:
    """A checked mod. Paths inside it are relative POSIX paths."""

    id: str
    folder: Path
    name: str
    version: str = ""
    description: str = ""
    themes: list[dict[str, Any]] = field(default_factory=list)
    default_themes: dict[str, str] = field(default_factory=dict)
    stylesheets: list[str] = field(default_factory=list)
    splash: str | None = None
    icon: dict[str, str] = field(default_factory=dict)

    def public(self, enabled: bool) -> dict[str, Any]:
        """What the page is told about this mod."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "enabled": enabled,
            "themes": self.themes,
            "default_themes": self.default_themes,
            "has_splash": self.splash is not None,
            "has_icon": bool(self.icon),
        }

    def icon_file(self, kind: str) -> Path | None:
        rel = self.icon.get(kind)
        return self.folder / rel if rel else None

    def splash_html(self) -> str:
        if not self.splash:
            return ""
        return (self.folder / self.splash).read_text(encoding="utf-8")


def mods_dir(base: Path | None = None) -> Path:
    """Where installed mods live: <config dir>/mods."""
    return (base or config_dir()) / "mods"


# -- paths --------------------------------------------------------------------------


def _safe_rel(value: Any) -> str | None:
    """A relative POSIX path made only of plain segments, or None."""
    if not isinstance(value, str) or not value or len(value) > 300:
        return None
    parts = value.split("/")
    if not all(_SEGMENT.match(p) for p in parts):
        return None
    return value


def inside(folder: Path, rel: str) -> Path | None:
    """The regular file folder/rel, or None when it is missing, a symlink, or outside folder.

    - Every segment is checked, so a symlinked folder on the way is refused too.
    """
    if _safe_rel(rel) is None:
        return None
    path = folder
    for part in rel.split("/"):
        path = path / part
        if path.is_symlink():
            return None
    try:
        resolved = path.resolve(strict=True)
        root = folder.resolve(strict=True)
    except OSError:
        return None
    if root not in resolved.parents or not resolved.is_file():
        return None
    return resolved


def _file(folder: Path, value: Any, what: str, extensions: set[str] | frozenset[str]) -> str:
    rel = _safe_rel(value)
    if rel is None:
        raise ModError(f"{what} must be a plain relative path, not {value!r}")
    if PurePosixPath(rel).suffix.lower() not in extensions:
        raise ModError(f"{what} must end in {', '.join(sorted(extensions))}")
    path = inside(folder, rel)
    if path is None:
        raise ModError(f"{what} {rel} is missing or not a regular file inside the mod")
    if path.stat().st_size > MAX_FILE:
        raise ModError(f"{what} {rel} is too large")
    return rel


def _text(raw: Any, limit: int) -> str:
    return " ".join(raw.split())[:limit] if isinstance(raw, str) else ""


# -- the splash fragment ------------------------------------------------------------

# Tags a splash may not hold: anything that runs code, loads a page, or changes the document.
_BANNED_TAGS = frozenset({
    "script", "iframe", "frame", "frameset", "object", "embed", "applet", "link", "meta", "base",
    "form", "input", "button", "textarea", "select", "style", "template", "html", "head", "body",
    "foreignobject", "animate", "set", "animatemotion", "animatetransform",
})  # fmt: skip
_VOID = frozenset({"area", "br", "col", "hr", "img", "source", "track", "wbr"})
_URL_ATTRS = frozenset({"href", "src", "xlink:href", "srcset", "action", "formaction", "poster"})
SPLASH_REGIONS = ("rider", "lines")


class _SplashChecker(HTMLParser):
    """Refuse a splash fragment that could run code or break the page around it.

    - No banned tags, no on* handlers, no URLs with a scheme.
    - Every tag that opens must close, so the fragment cannot escape its slot.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.problem: str | None = None

    def _fail(self, message: str) -> None:
        if self.problem is None:
            self.problem = message

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BANNED_TAGS:
            self._fail(f"<{tag}> is not allowed")
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                self._fail(f"the {name} attribute is not allowed")
            if name == "id" and value == "splash":
                self._fail('id="splash" is taken by the page')
            if name in _URL_ATTRS and value and ":" in value and not value.startswith("data:image/"):
                self._fail(f"{name} may only hold a relative address")
            if value and "javascript:" in value.lower().replace(" ", ""):
                self._fail("javascript: is not allowed")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._attrs(tag, attrs)
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._attrs(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self._fail(f"</{tag}> does not close the tag before it")
            return
        self.stack.pop()

    def handle_decl(self, decl: str) -> None:
        self._fail("declarations are not allowed")

    def handle_pi(self, data: str) -> None:
        self._fail("processing instructions are not allowed")


def splash_regions(html: str) -> dict[str, str]:
    """The regions a splash fragment fills, by name.

    - A region is the text between <!-- splash:NAME --> and <!-- /splash:NAME -->.
    - Raises ModError when the fragment is unsafe or fills no region.
    """
    checker = _SplashChecker()
    checker.feed(html)
    checker.close()
    if checker.problem is None and checker.stack:
        checker.problem = f"<{checker.stack[-1]}> is never closed"
    if checker.problem:
        raise ModError(f"the splash cannot be used: {checker.problem}")
    regions = {}
    for name in SPLASH_REGIONS:
        found = re.search(rf"<!--\s*splash:{name}\s*-->(.*?)<!--\s*/splash:{name}\s*-->", html, re.S)
        if found:
            regions[name] = found.group(1).strip()
    if not regions:
        raise ModError("the splash fills no region; mark one with <!-- splash:rider --> or <!-- splash:lines -->")
    return regions


# -- manifests ------------------------------------------------------------------------


def _themes(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ModError("themes must be a list")
    if len(raw) > MAX_THEMES:
        raise ModError(f"a mod holds at most {MAX_THEMES} themes")
    themes, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ModError("each theme must be an object")
        theme_id = item.get("id")
        if not isinstance(theme_id, str) or not MOD_ID.match(theme_id):
            raise ModError(f"theme id {theme_id!r} must be lowercase letters, digits and dashes")
        if theme_id in BUILTIN_THEME_IDS or theme_id.startswith("custom-"):
            raise ModError(f"theme id {theme_id} is taken by GitSense")
        if theme_id in seen:
            raise ModError(f"theme id {theme_id} is used twice")
        seen.add(theme_id)
        if item.get("kind") not in ("light", "dark"):
            raise ModError(f'theme {theme_id} needs a kind: "light" or "dark"')
        colors = clean_theme_colors(item.get("colors"))
        if not colors:
            raise ModError(f"theme {theme_id} has no usable colors")
        name = _text(item.get("name"), 60) or theme_id
        themes.append({"id": theme_id, "name": name, "kind": item["kind"], "colors": colors})
    return themes


def load(folder: Path) -> Mod:
    """Read and check the mod in folder. Raises ModError."""
    manifest = inside(folder, MANIFEST)
    if manifest is None:
        raise ModError(f"no {MANIFEST} in {folder}")
    if manifest.stat().st_size > MAX_SPLASH:
        raise ModError(f"{MANIFEST} is too large")
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ModError(f"{MANIFEST} is not valid JSON ({exc})") from None
    if not isinstance(raw, dict):
        raise ModError(f"{MANIFEST} must hold an object")
    mod_id = raw.get("id")
    if not isinstance(mod_id, str) or not MOD_ID.match(mod_id):
        raise ModError("the mod id must be 1 to 40 lowercase letters, digits and dashes")
    version = raw.get("version", "")
    if version and (not isinstance(version, str) or not _VERSION.match(version)):
        raise ModError("version must look like 1.0.0")
    mod = Mod(
        id=mod_id,
        folder=folder,
        name=_text(raw.get("name"), 60) or mod_id,
        version=version or "",
        description=_text(raw.get("description"), 300),
        themes=_themes(raw.get("themes")),
    )

    defaults = raw.get("default_themes")
    if defaults is not None:
        if not isinstance(defaults, dict):
            raise ModError("default_themes must be an object")
        by_id = {t["id"]: t for t in mod.themes}
        for kind in ("light", "dark"):
            if kind not in defaults:
                continue
            theme = by_id.get(defaults[kind])
            if theme is None or theme["kind"] != kind:
                raise ModError(f"default_themes.{kind} must name a {kind} theme of this mod")
            mod.default_themes[kind] = theme["id"]

    sheets = raw.get("stylesheets")
    if sheets is not None:
        if not isinstance(sheets, list) or len(sheets) > 20:
            raise ModError("stylesheets must be a list of up to 20 files")
        mod.stylesheets = [_file(folder, s, "stylesheet", {".css"}) for s in sheets]

    if raw.get("splash") is not None:
        mod.splash = _file(folder, raw["splash"], "splash", {".html"})
        if (folder / mod.splash).stat().st_size > MAX_SPLASH:
            raise ModError("the splash is too large")
        try:
            splash_regions(mod.splash_html())
        except UnicodeDecodeError:
            raise ModError("the splash must be UTF-8 text") from None

    icon = raw.get("icon")
    if icon is not None:
        if not isinstance(icon, dict):
            raise ModError("icon must be an object")
        for kind, ext in ICON_KINDS.items():
            if icon.get(kind) is not None:
                mod.icon[kind] = _file(folder, icon[kind], f"icon.{kind}", {ext})
    return mod


def installed(directory: Path, *, report: bool = True) -> list[Mod]:
    """Every usable mod in directory, sorted by id.

    - A mod whose folder name differs from its id is skipped.
    - A theme id another mod already uses is dropped from the later mod.
    - Problems are printed to stderr when report is True.
    """
    mods: list[Mod] = []
    try:
        folders = sorted(p for p in directory.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return []
    taken: set[str] = set()
    for folder in folders:
        if folder.name.startswith("."):
            continue
        try:
            mod = load(folder)
            if mod.id != folder.name:
                raise ModError(f"its id is {mod.id}, but its folder is named {folder.name}")
        except Exception as exc:  # noqa: BLE001 - a bad mod is skipped, never fatal
            if report:
                print(f"gitsense: skipped the mod in {folder}: {exc}", file=sys.stderr)
            continue
        clashes = [t["id"] for t in mod.themes if t["id"] in taken]
        if clashes:
            if report:
                print(f"gitsense: mod {mod.id}: skipped themes another mod defines: {', '.join(clashes)}", file=sys.stderr)
            mod.themes = [t for t in mod.themes if t["id"] not in taken]
            mod.default_themes = {k: v for k, v in mod.default_themes.items() if v not in clashes}
        taken.update(t["id"] for t in mod.themes)
        mods.append(mod)
    return mods


def is_enabled(mod_id: str, states: dict[str, bool]) -> bool:
    """A mod is on unless the user switched it off."""
    return states.get(mod_id, True) is not False


def enabled(directory: Path, states: dict[str, bool], *, report: bool = False) -> list[Mod]:
    return [m for m in installed(directory, report=report) if is_enabled(m.id, states)]


def active_icon(directory: Path, states: dict[str, bool], kind: str) -> Path | None:
    """The icon file of the first enabled mod that has one of this kind."""
    for mod in enabled(directory, states):
        path = mod.icon_file(kind)
        if path is not None:
            return path
    return None


# -- install and remove ---------------------------------------------------------------


def _check_member(rel: str) -> bool:
    """True for a file to copy. Raises ModError for one that is not allowed.

    - Hidden files and macOS archive folders are skipped silently.
    """
    parts = rel.split("/")
    if any(p.startswith(".") for p in parts) or parts[0] == "__MACOSX":
        return False
    if _safe_rel(rel) is None:
        raise ModError(f"{rel} is not a plain file name")
    if PurePosixPath(rel).suffix.lower() not in ALLOWED:
        raise ModError(f"{rel}: mods may only hold {', '.join(sorted(ALLOWED))} files")
    return True


def _copy_folder(source: Path, dest: Path) -> None:
    """Copy the allowed files of a mod folder. Raises ModError for anything unexpected."""
    count = total = 0
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source).as_posix()
        if path.is_symlink():
            raise ModError(f"{rel} is a symbolic link; mods may not hold links")
        if path.is_dir():
            continue
        if not stat.S_ISREG(path.stat().st_mode) or not _check_member(rel):
            continue
        size = path.stat().st_size
        count, total = count + 1, total + size
        if size > MAX_FILE or total > MAX_TOTAL or count > MAX_FILES:
            raise ModError("the mod is too large")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def _extract_zip(archive: Path, dest: Path) -> None:
    """Unpack a mod archive. Raises ModError for unsafe or oversized entries.

    - The files may sit at the top of the archive or in one folder.
    - Absolute paths, .. segments, links and special files are refused.
    """
    try:
        zf = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ModError(f"{archive} is not a zip archive ({exc})") from None
    with zf:
        infos = zf.infolist()
        for info in infos:
            name = info.filename
            if name.startswith(("/", "\\")) or "\\" in name or re.match(r"^[A-Za-z]:", name):
                raise ModError(f"{name}: absolute paths are not allowed")
            if ".." in name.split("/"):
                raise ModError(f"{name}: .. is not allowed")
            # The file type, when the archive records one (zip tools on Windows do not).
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind and kind not in (stat.S_IFREG, stat.S_IFDIR):
                raise ModError(f"{name}: links and special files are not allowed")
        files = [i for i in infos if not i.is_dir()]
        prefix = ""
        if not any(i.filename == MANIFEST for i in files):
            tops = {i.filename.split("/", 1)[0] for i in files if "/" in i.filename and not i.filename.startswith("__MACOSX/")}
            if len(tops) == 1 and any(i.filename == f"{next(iter(tops))}/{MANIFEST}" for i in files):
                prefix = f"{next(iter(tops))}/"
        count = total = 0
        for info in files:
            if not info.filename.startswith(prefix):
                continue
            rel = info.filename[len(prefix):]
            if not rel or not _check_member(rel):
                continue
            count += 1
            if info.file_size > MAX_FILE or count > MAX_FILES:
                raise ModError("the mod is too large")
            # Read with a limit: the sizes in the archive header are not trusted.
            with zf.open(info) as src:
                data = src.read(MAX_FILE + 1)
            total += len(data)
            if len(data) > MAX_FILE or total > MAX_TOTAL:
                raise ModError("the mod is too large")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def install(source: Path, directory: Path) -> Mod:
    """Install the mod in a folder or .zip archive, replacing an earlier copy of it.

    - The mod is unpacked and checked in a staging folder first.
    - Only then is it moved to directory/<id>.
    """
    source = Path(source).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".install-", dir=directory))
    try:
        unpacked = staging / "mod"
        unpacked.mkdir()
        if source.is_dir():
            _copy_folder(source, unpacked)
        elif source.is_file() and zipfile.is_zipfile(source):
            _extract_zip(source, unpacked)
        elif source.exists():
            raise ModError(f"{source} is neither a folder nor a .zip archive")
        else:
            raise ModError(f"{source} does not exist")
        mod = load(unpacked)
        target = directory / mod.id
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise ModError(f"{target} is in the way")
        if target.exists():
            target.rename(staging / "old")  # removed with the staging folder
        unpacked.rename(target)
        return load(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def uninstall(mod_id: str, directory: Path) -> Path:
    """Delete an installed mod. Raises ModError when there is none with that id."""
    if not MOD_ID.match(mod_id or ""):
        raise ModError(f"{mod_id!r} is not a mod id")
    target = directory / mod_id
    if target.is_symlink() or not target.is_dir():
        raise ModError(f"no mod named {mod_id} is installed")
    shutil.rmtree(target)
    return target


def read_served(mod: Mod, rel: str) -> tuple[bytes, str] | None:
    """The bytes and content type of a file the page may load, or None."""
    content_type = SERVED_TYPES.get(PurePosixPath(rel).suffix.lower())
    if content_type is None:
        return None
    path = inside(mod.folder, rel)
    if path is None or path.stat().st_size > MAX_FILE:
        return None
    return path.read_bytes(), content_type


# -- command line ---------------------------------------------------------------------


def _app_bundle_hint() -> str | None:
    """A hint to rebuild GitSense.app, when one is installed on this Mac."""
    if sys.platform != "darwin":
        return None
    for folder in (Path("/Applications"), Path.home() / "Applications"):
        if (folder / "GitSense.app").is_dir():
            return "GitSense.app keeps its old icon until you run: gitsense --install-app"
    return None


def cli_install(source: str, base: Path | None = None) -> int:
    """`gitsense --install-mod PATH`."""
    try:
        mod = install(Path(source), mods_dir(base))
    except (ModError, OSError) as exc:
        print(f"gitsense: could not install the mod: {exc}", file=sys.stderr)
        return 1
    print(f"Installed {mod.name} {mod.version}".rstrip() + f" in {mod.folder}")
    print("It is switched on and shows the next time GitSense starts or reloads.")
    if mod.icon.get("icns"):
        hint = _app_bundle_hint()
        if hint:
            print(hint)
    return 0


def cli_uninstall(mod_id: str, base: Path | None = None) -> int:
    """`gitsense --uninstall-mod ID`."""
    try:
        target = uninstall(mod_id, mods_dir(base))
    except (ModError, OSError) as exc:
        print(f"gitsense: {exc}", file=sys.stderr)
        return 1
    print(f"Removed {target}")
    hint = _app_bundle_hint()
    if hint:
        print(hint)
    return 0


def cli_list(states: dict[str, bool], base: Path | None = None, out: TextIO | None = None) -> int:
    """`gitsense --list-mods`."""
    out = out or sys.stdout
    directory = mods_dir(base)
    mods = installed(directory)
    if not mods:
        print(f"No mods installed in {directory}", file=out)
        return 0
    for mod in mods:
        state = "on" if is_enabled(mod.id, states) else "off"
        line = f"{mod.id}  {mod.name} {mod.version}".rstrip()
        print(f"{line}  ({state})", file=out)
        if mod.description:
            print(f"    {mod.description}", file=out)
    return 0


__all__ = [
    "Mod", "ModError", "THEME_TOKENS", "active_icon", "enabled", "install", "installed", "is_enabled",
    "load", "mods_dir", "read_served", "splash_regions", "uninstall",
]  # fmt: skip
