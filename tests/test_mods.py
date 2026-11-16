"""Tests for mods: manifests, install and remove, serving their files, and their effect on settings.

- Mods are built under tmp_path.
- Servers listen on a free port and use a config of their own.
"""

from __future__ import annotations

import http.client
import json
import shutil
import stat
import zipfile
from pathlib import Path

import pytest

from gitsense import cli, mods
from gitsense.config import Config
from gitsense.mods import ModError
from gitsense.server import WEB_ROOT, App, Server

COLORS = {"bg": "#101010", "text": "#EEEEEE", "accent": "#3366CC"}
SPLASH = (
    "<!-- splash:rider --><svg class=\"rider\"><circle r=\"4\"/></svg><!-- /splash:rider -->\n"
    "<!-- splash:lines --><span class=\"sp-type sp-type-1\" style=\"--n:9\">$ git hi</span><!-- /splash:lines -->\n"
)


def make_mod(folder: Path, **over) -> Path:
    """Write a complete, valid mod into folder and return it."""
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": folder.name,
        "name": "Test mod",
        "version": "1.2.3",
        "description": "For tests.",
        "themes": [
            {"id": f"{folder.name}-light", "name": "Test Light", "kind": "light", "colors": COLORS},
            {"id": f"{folder.name}-dark", "name": "Test Dark", "kind": "dark", "colors": COLORS},
        ],
        "default_themes": {"light": f"{folder.name}-light", "dark": f"{folder.name}-dark"},
        "stylesheets": ["style.css"],
        "splash": "splash.html",
        "icon": {"svg": "icon.svg", "icns": "App.icns"},
    }
    manifest.update(over)
    (folder / "mod.json").write_text(json.dumps(manifest))
    (folder / "style.css").write_text(".rider { color: red; }\n")
    (folder / "splash.html").write_text(SPLASH)
    (folder / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (folder / "App.icns").write_bytes(b"icns")
    (folder / "README.md").write_text("notes\n")
    return folder


# -- manifests ----------------------------------------------------------------------


def test_a_valid_mod_loads(tmp_path):
    mod = mods.load(make_mod(tmp_path / "test"))
    assert mod.name == "Test mod" and mod.version == "1.2.3"
    assert mod.themes[0] == {"id": "test-light", "name": "Test Light", "kind": "light", "colors": COLORS}
    assert mod.stylesheets == ["style.css"] and mod.splash == "splash.html"
    assert mod.icon == {"svg": "icon.svg", "icns": "App.icns"}


def test_only_an_id_is_required(tmp_path):
    folder = tmp_path / "bare"
    folder.mkdir()
    (folder / "mod.json").write_text('{"id": "bare"}')
    mod = mods.load(folder)
    assert mod.name == "bare" and not mod.themes and not mod.stylesheets and mod.splash is None


def theme(theme_id="test-x", kind="dark", colors=None):
    return {"id": theme_id, "name": "X", "kind": kind, "colors": COLORS if colors is None else colors}


@pytest.mark.parametrize(
    "over, message",
    [
        ({"id": "Bad Id"}, "mod id"),
        ({"id": "../up"}, "mod id"),
        ({"version": "1.0; rm -rf"}, "version"),
        ({"themes": [theme("graphite")]}, "taken by GitSense"),
        ({"themes": [theme("custom-mine")]}, "taken by GitSense"),
        ({"themes": [theme(), theme()]}, "used twice"),
        ({"themes": [theme(kind="sepia")]}, "kind"),
        ({"themes": [theme(colors={"bg": "red", "nope": "#FFFFFF"})]}, "no usable colors"),
        ({"themes": "x"}, "must be a list"),
        ({"default_themes": {"dark": "test-light"}}, "default_themes.dark"),
        ({"default_themes": {"light": "graphite"}}, "default_themes.light"),
        ({"stylesheets": ["../outside.css"]}, "plain relative path"),
        ({"stylesheets": ["/etc/passwd.css"]}, "plain relative path"),
        ({"stylesheets": [".hidden.css"]}, "plain relative path"),
        ({"stylesheets": ["missing.css"]}, "missing"),
        ({"stylesheets": ["icon.svg"]}, "must end in .css"),
        ({"splash": "style.css"}, "must end in .html"),
        ({"icon": {"svg": "App.icns"}}, "must end in .svg"),
        ({"icon": "icon.svg"}, "icon must be an object"),
    ],
)
def test_bad_manifests_are_refused(tmp_path, over, message):
    with pytest.raises(ModError, match=message):
        mods.load(make_mod(tmp_path / "test", **over))


def test_a_manifest_that_is_not_json_is_refused(tmp_path):
    folder = make_mod(tmp_path / "test")
    (folder / "mod.json").write_text("{nope")
    with pytest.raises(ModError, match="not valid JSON"):
        mods.load(folder)


def test_files_reached_through_a_symlink_are_refused(tmp_path):
    folder = make_mod(tmp_path / "test")
    (tmp_path / "secret.css").write_text("secret")
    (folder / "style.css").unlink()
    (folder / "style.css").symlink_to(tmp_path / "secret.css")
    with pytest.raises(ModError, match="missing or not a regular file"):
        mods.load(folder)
    shutil.rmtree(folder)
    folder = make_mod(tmp_path / "test", stylesheets=["sub/style.css"])
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "style.css").write_text("x")
    (folder / "sub").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ModError, match="missing or not a regular file"):
        mods.load(folder)


@pytest.mark.parametrize(
    "fragment, message",
    [
        ("<!-- splash:rider --><script>alert(1)</script><!-- /splash:rider -->", "<script>"),
        ('<!-- splash:rider --><svg onload="alert(1)"></svg><!-- /splash:rider -->', "onload"),
        ('<!-- splash:rider --><img src="https://example.com/x.png"><!-- /splash:rider -->', "relative address"),
        ('<!-- splash:rider --><a href="javascript:alert(1)">x</a><!-- /splash:rider -->', "relative address"),
        ("<!-- splash:rider --><style>body{}</style><!-- /splash:rider -->", "<style>"),
        ("<!-- splash:rider --><iframe></iframe><!-- /splash:rider -->", "<iframe>"),
        ('<!-- splash:rider --><svg><foreignObject><div/></foreignObject></svg><!-- /splash:rider -->', "foreignobject"),
        ("<!-- splash:rider --></div><div><!-- /splash:rider -->", "does not close"),
        ("<!-- splash:rider --><div><!-- /splash:rider -->", "never closed"),
        ('<!-- splash:rider --><div id="splash"></div><!-- /splash:rider -->', "taken by the page"),
        ("<p>no regions</p>", "fills no region"),
    ],
)
def test_unsafe_splashes_are_refused(fragment, message):
    with pytest.raises(ModError, match=message):
        mods.splash_regions(fragment)


def test_splash_regions_are_found():
    assert mods.splash_regions(SPLASH) == {
        "rider": '<svg class="rider"><circle r="4"/></svg>',
        "lines": '<span class="sp-type sp-type-1" style="--n:9">$ git hi</span>',
    }


def test_installed_skips_bad_mods_and_theme_clashes(tmp_path, capsys):
    make_mod(tmp_path / "alpha")
    make_mod(tmp_path / "beta", themes=[theme("alpha-dark"), theme("beta-own")], default_themes={"dark": "alpha-dark"})
    make_mod(tmp_path / "gamma", id="other")  # folder and id differ
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "mod.json").write_text("[]")
    (tmp_path / "loose.txt").write_text("not a mod")
    found = mods.installed(tmp_path)
    assert [m.id for m in found] == ["alpha", "beta"]
    assert [t["id"] for t in found[1].themes] == ["beta-own"]
    assert found[1].default_themes == {}
    err = capsys.readouterr().err
    assert "gamma" in err and "broken" in err and "alpha-dark" in err


def test_a_missing_mods_folder_is_empty(tmp_path):
    assert mods.installed(tmp_path / "none") == []


# -- install and remove -------------------------------------------------------------------


def test_install_from_a_folder(tmp_path):
    source = make_mod(tmp_path / "src" / "test")
    (source / ".DS_Store").write_bytes(b"\0")
    dest = tmp_path / "mods"
    mod = mods.install(source, dest)
    assert mod.folder == dest / "test"
    assert sorted(p.name for p in (dest / "test").iterdir()) == [
        "App.icns", "README.md", "icon.svg", "mod.json", "splash.html", "style.css",
    ]  # fmt: skip
    assert not list(dest.glob(".install-*")), "the staging folder is cleaned up"


def test_install_replaces_an_earlier_copy(tmp_path):
    dest = tmp_path / "mods"
    mods.install(make_mod(tmp_path / "v1" / "test"), dest)
    (dest / "test" / "stale.css").write_text("old")
    mods.install(make_mod(tmp_path / "v2" / "test", version="2.0.0"), dest)
    assert mods.load(dest / "test").version == "2.0.0"
    assert not (dest / "test" / "stale.css").exists()


def test_a_failed_install_keeps_the_earlier_copy(tmp_path):
    dest = tmp_path / "mods"
    mods.install(make_mod(tmp_path / "v1" / "test"), dest)
    bad = make_mod(tmp_path / "v2" / "test", themes=[theme("graphite")])
    with pytest.raises(ModError):
        mods.install(bad, dest)
    assert mods.load(dest / "test").version == "1.2.3"


@pytest.mark.parametrize("name", ["run.js", "page.htm", "tool.py", "binary.exe"])
def test_install_refuses_files_a_mod_may_not_hold(tmp_path, name):
    source = make_mod(tmp_path / "test")
    (source / name).write_text("x")
    with pytest.raises(ModError, match="mods may only hold"):
        mods.install(source, tmp_path / "mods")
    assert not (tmp_path / "mods" / "test").exists()


def test_install_refuses_a_folder_with_links(tmp_path):
    source = make_mod(tmp_path / "test")
    (source / "link.css").symlink_to("/etc/hosts")
    with pytest.raises(ModError, match="symbolic link"):
        mods.install(source, tmp_path / "mods")


def test_install_refuses_a_large_mod(tmp_path, monkeypatch):
    monkeypatch.setattr(mods, "MAX_FILE", 10)
    with pytest.raises(ModError, match="too large"):
        mods.install(make_mod(tmp_path / "test"), tmp_path / "mods")


def zip_mod(folder: Path, archive: Path, prefix: str = "") -> Path:
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(folder.rglob("*")):
            zf.write(path, prefix + path.relative_to(folder).as_posix())
    return archive


@pytest.mark.parametrize("prefix", ["", "test/"])
def test_install_from_a_zip(tmp_path, prefix):
    archive = zip_mod(make_mod(tmp_path / "src" / "test"), tmp_path / "test.zip", prefix)
    mod = mods.install(archive, tmp_path / "mods")
    assert mod.id == "test" and (tmp_path / "mods" / "test" / "style.css").is_file()


def evil_zip(tmp_path: Path, name: str, data: bytes = b"x", mode: int | None = None) -> Path:
    archive = tmp_path / "evil.zip"
    good = make_mod(tmp_path / "src" / "test")
    zip_mod(good, archive)
    with zipfile.ZipFile(archive, "a") as zf:
        info = zipfile.ZipInfo(name)
        if mode is not None:
            info.external_attr = mode << 16
        zf.writestr(info, data)
    return archive


@pytest.mark.parametrize(
    "name, mode, message",
    [
        ("../escape.css", None, r"\.\. is not allowed"),
        ("sub/../../escape.css", None, r"\.\. is not allowed"),
        ("/tmp/abs.css", None, "absolute paths"),
        ("C:/abs.css", None, "absolute paths"),
        ("sub\\win.css", None, "absolute paths"),
        ("link.css", stat.S_IFLNK | 0o777, "links and special files"),
        ("run.js", None, "mods may only hold"),
    ],
)
def test_install_refuses_malicious_zips(tmp_path, name, mode, message):
    archive = evil_zip(tmp_path, name, b"/etc/passwd", mode)
    with pytest.raises(ModError, match=message):
        mods.install(archive, tmp_path / "mods")
    assert not (tmp_path / "mods" / "test").exists()
    assert not (tmp_path / "escape.css").exists() and not Path("/tmp/abs.css").exists()


def test_install_refuses_a_zip_bomb(tmp_path, monkeypatch):
    monkeypatch.setattr(mods, "MAX_FILE", 1000)
    archive = tmp_path / "bomb.zip"
    zip_mod(make_mod(tmp_path / "src" / "test"), archive)
    with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.css", b" " * 100_000)
    with pytest.raises(ModError, match="too large"):
        mods.install(archive, tmp_path / "mods")


def test_install_refuses_other_files(tmp_path):
    (tmp_path / "mod.tar").write_text("not a zip")
    with pytest.raises(ModError, match="neither a folder nor a .zip"):
        mods.install(tmp_path / "mod.tar", tmp_path / "mods")
    with pytest.raises(ModError, match="does not exist"):
        mods.install(tmp_path / "nothing", tmp_path / "mods")


def test_uninstall(tmp_path):
    dest = tmp_path / "mods"
    mods.install(make_mod(tmp_path / "src" / "test"), dest)
    assert mods.uninstall("test", dest) == dest / "test"
    assert not (dest / "test").exists()
    with pytest.raises(ModError, match="no mod named test"):
        mods.uninstall("test", dest)
    with pytest.raises(ModError, match="not a mod id"):
        mods.uninstall("../cfg", dest)


def test_mod_commands(tmp_path, capsys):
    """--install-mod, --list-mods and --uninstall-mod use the config folder (isolated by conftest)."""
    base = mods.config_dir()
    source = make_mod(tmp_path / "test")
    try:
        assert cli.main(["--install-mod", str(source)]) == 0
        out = capsys.readouterr().out
        assert f"Installed Test mod 1.2.3 in {base / 'mods' / 'test'}" in out
        assert cli.main(["--list-mods"]) == 0
        out = capsys.readouterr().out
        assert "test  Test mod 1.2.3  (on)" in out and "For tests." in out
        assert cli.main(["--uninstall-mod", "test"]) == 0
        assert "Removed" in capsys.readouterr().out
        assert cli.main(["--uninstall-mod", "test"]) == 1
        assert cli.main(["--install-mod", str(tmp_path / "missing")]) == 1
        assert "could not install" in capsys.readouterr().err
        assert cli.main(["--list-mods"]) == 0
        assert "No mods installed" in capsys.readouterr().out
    finally:
        shutil.rmtree(base / "mods", ignore_errors=True)


# -- settings ---------------------------------------------------------------------------


def app_with(tmp_path: Path, *folders: str, settings: dict | None = None) -> App:
    cfg = tmp_path / "cfg"
    for name in folders:
        make_mod(cfg / "mods" / name)
    if settings is not None:
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.json").write_text(json.dumps({"settings": settings}))
    return App(Config(cfg))


def test_a_new_mod_is_switched_on_with_its_default_themes(tmp_path):
    app = app_with(tmp_path, "test")
    s = app.config.settings()
    assert s["mods"] == {"test": True}
    assert (s["theme"], s["theme_light"], s["theme_dark"]) == ("system", "test-light", "test-dark")
    state = app.state({})
    assert state["mods"][0]["enabled"] is True and state["mods"][0]["has_splash"] is True


def test_defaults_are_applied_once_so_the_users_choice_sticks(tmp_path):
    app = app_with(tmp_path, "test")
    app.config.update_settings({"theme": "paper"})
    app.refresh_mods()
    assert app.config.settings()["theme"] == "paper"


def test_a_mod_with_one_default_theme_uses_it_always(tmp_path):
    cfg = tmp_path / "cfg"
    make_mod(cfg / "mods" / "test", default_themes={"dark": "test-dark"})
    s = App(Config(cfg)).config.settings()
    assert (s["theme"], s["theme_light"], s["theme_dark"]) == ("test-dark", "daylight", "test-dark")


def test_switching_a_mod_off_falls_back_to_the_defaults(tmp_path):
    app = app_with(tmp_path, "test")
    app.config.set_last_theme({"kind": "dark", "colors": {"bg": "#101010"}})
    result = app.mods_set({"id": "test", "enabled": False})
    s = result["settings"]
    assert s["mods"] == {"test": False}
    assert (s["theme"], s["theme_light"], s["theme_dark"]) == ("graphite", "daylight", "graphite")
    assert app.config.last_theme() is None, "the mod's colors are not painted on the next start"
    assert result["mods"][0]["enabled"] is False
    # On again: its themes come back.
    s = app.mods_set({"id": "test", "enabled": True})["settings"]
    assert (s["theme"], s["theme_light"], s["theme_dark"]) == ("system", "test-light", "test-dark")


def test_switching_off_keeps_choices_that_still_exist(tmp_path):
    app = app_with(tmp_path, "test")
    app.config.update_settings({"theme": "system", "theme_light": "paper"})
    s = app.mods_set({"id": "test", "enabled": False})["settings"]
    assert (s["theme"], s["theme_light"], s["theme_dark"]) == ("system", "paper", "graphite")


def test_switching_an_unknown_mod(tmp_path):
    from gitsense.server import ApiError

    with pytest.raises(ApiError):
        app_with(tmp_path).mods_set({"id": "nope", "enabled": True})


def test_a_removed_mod_falls_back_and_counts_as_new_when_reinstalled(tmp_path):
    app = app_with(tmp_path, "test")
    app.config.update_settings({"theme": "test-dark"})
    app.mods_set({"id": "test", "enabled": False})
    shutil.rmtree(app.mods_dir / "test")
    app.refresh_mods()
    s = app.config.settings()
    assert s["mods"] == {} and s["theme"] == "graphite"
    make_mod(app.mods_dir / "test")
    app.refresh_mods()
    assert app.config.settings()["mods"] == {"test": True}


def test_an_old_config_without_the_mod_falls_back(tmp_path):
    """A config may name themes from a mod that is no longer installed."""
    app = app_with(tmp_path, settings={"theme": "gone-dark", "theme_light": "gone-light", "theme_dark": "gone-dark"})
    s = app.config.settings()
    assert (s["theme"], s["theme_light"], s["theme_dark"]) == ("graphite", "daylight", "graphite")
    app = app_with(tmp_path / "b", settings={"theme": "system", "theme_light": "gone-light", "theme_dark": "gone-dark"})
    assert app.config.settings()["theme"] == "graphite", "both system picks were gone, so the whole choice resets"


def test_legacy_light_and_dark_values_are_migrated(tmp_path):
    assert app_with(tmp_path, settings={"theme": "light"}).config.settings()["theme"] == "daylight"
    assert app_with(tmp_path / "b", settings={"theme": "dark"}).config.settings()["theme"] == "graphite"


def test_custom_themes_are_not_dropped(tmp_path):
    custom = {"id": "custom-mine", "name": "Mine", "kind": "dark", "colors": {"bg": "#000000"}}
    app = app_with(tmp_path, settings={"custom_themes": [custom], "theme": "custom-mine"})
    assert app.config.settings()["theme"] == "custom-mine"


def test_a_bad_mod_never_stops_the_app(tmp_path):
    cfg = tmp_path / "cfg"
    (cfg / "mods" / "broken").mkdir(parents=True)
    (cfg / "mods" / "broken" / "mod.json").write_text("{")
    (cfg / "mods" / "mod.json").write_text("stray")
    app = App(Config(cfg))
    assert app.state({})["mods"] == []
    assert b"__GITSENSE_TOKEN__" not in app.render_index((WEB_ROOT / "index.html").read_bytes())


# -- the page ---------------------------------------------------------------------------


def test_index_carries_the_enabled_mods(tmp_path):
    app = app_with(tmp_path, "test")
    html = app.render_index((WEB_ROOT / "index.html").read_bytes()).decode()
    head = html.split("</head>", 1)[0]
    assert head.index("css/themes.css") < head.index('<link rel="stylesheet" href="/mods/test/style.css">')
    assert '<link rel="icon" href="/mods/test/icon.svg" type="image/svg+xml">' in head
    rider = html.split("<!-- splash:rider -->", 1)[1].split("<!-- /splash:rider -->", 1)[0]
    assert rider.strip() == '<svg class="rider"><circle r="4"/></svg>'
    assert "$ git hi" in html and "--hang-ten" not in html
    assert "sp-river" in html, "the river stays"


def test_index_without_mods_keeps_the_core_splash(tmp_path):
    app = app_with(tmp_path, "test")
    app.mods_set({"id": "test", "enabled": False})
    html = app.render_index((WEB_ROOT / "index.html").read_bytes()).decode()
    assert "/mods/" not in html and "sp-rider" in html and "--hang-ten" in html
    assert 'href="img/favicon.svg"' in html


class Client:
    def __init__(self, srv: Server):
        self.srv = srv

    def get(self, path, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=30)
        conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{self.srv.port}", **(headers or {})})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, resp.headers, body


@pytest.fixture
def served(tmp_path):
    cfg = tmp_path / "cfg"
    make_mod(cfg / "mods" / "test")
    (tmp_path / "secret.css").write_text("secret")
    srv = Server(App(Config(cfg)), port=0)
    srv.start()
    yield Client(srv)
    srv.stop()


def cookie_of(client: Client) -> dict[str, str]:
    status, headers, _ = client.get(f"/?k={client.srv.app.launch_key}")
    assert status == 200
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/mods/" in cookie
    return {"Cookie": cookie.split(";", 1)[0]}


def test_mod_files_need_the_page_cookie_or_the_launch_key(served):
    assert served.get("/mods/test/style.css")[0] == 403
    assert served.get("/mods/test/style.css", {"Cookie": f"gitsense_mods_{served.srv.port}=wrong"})[0] == 403
    assert served.get("/mods/test/style.css?k=wrong")[0] == 403
    # The cookie of another port is not this server's.
    assert served.get("/mods/test/style.css", {"Cookie": f"gitsense_mods_1={served.srv.app.mods_key}"})[0] == 403
    status, headers, body = served.get("/mods/test/style.css", cookie_of(served))
    assert status == 200 and body == b".rider { color: red; }\n"
    assert headers["Content-Type"] == "text/css; charset=utf-8"
    assert "sandbox" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert served.get(f"/mods/test/icon.svg?k={served.srv.app.launch_key}")[0] == 200


def test_mod_files_check_the_host(served):
    cookie = cookie_of(served)
    assert served.get("/mods/test/style.css", cookie, host="evil.example")[0] == 403


@pytest.mark.parametrize(
    "path",
    [
        "/mods/test/mod.json/../style.css",
        "/mods/test/../cfg/config.json",
        "/mods/test/%2e%2e/config.json",
        "/mods/test/..%2fconfig.json",
        "/mods/../config.json",
        "/mods/test/splash.html",  # put into the page by the server, never served
        "/mods/test/App.icns",
        "/mods/test/README.md",
        "/mods/test/missing.css",
        "/mods/test/",
        "/mods/test",
        "/mods/nope/style.css",
        "/mods/test/link.css",
        "/mods/test/.hidden.css",
    ],
)
def test_mod_files_outside_the_allowlist_are_not_served(served, tmp_path, path):
    folder = served.srv.app.mods_dir / "test"
    (folder / "link.css").symlink_to(tmp_path / "secret.css")
    (folder / ".hidden.css").write_text("hidden")
    status, _, body = served.get(path, cookie_of(served))
    assert status == 404 and b"secret" not in body


def test_files_of_a_switched_off_mod_are_not_served(served):
    cookie = cookie_of(served)
    served.srv.app.mods_set({"id": "test", "enabled": False})
    assert served.get("/mods/test/style.css", cookie)[0] == 404


def test_the_api_route_switches_mods(served):
    conn = http.client.HTTPConnection("127.0.0.1", served.srv.port, timeout=30)
    body = json.dumps({"id": "test", "enabled": False})
    host = {"Host": f"127.0.0.1:{served.srv.port}", "Content-Type": "application/json"}
    conn.request("POST", "/api/mods/set", body=body, headers=host)
    assert conn.getresponse().status == 403, "the API token is needed"
    conn.close()
    conn = http.client.HTTPConnection("127.0.0.1", served.srv.port, timeout=30)
    conn.request("POST", "/api/mods/set", body=body, headers={**host, "X-GitSense-Token": served.srv.app.token})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    assert resp.status == 200 and data["mods"][0]["enabled"] is False


def test_the_app_icon_comes_from_an_enabled_mod(tmp_path):
    app = app_with(tmp_path, "test")
    assert cli.app_icon(app.config) == app.mods_dir / "test" / "App.icns"
    app.mods_set({"id": "test", "enabled": False})
    assert cli.app_icon(app.config) is None
