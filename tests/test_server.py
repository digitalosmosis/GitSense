"""Tests for the HTTP server and its API routes.

- WEB_ROOT points at a temp folder, so the tests do not depend on the real frontend.
- system launchers are replaced with recorders, so nothing is ever opened.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlencode

import pytest

from gitsense import server as server_mod
from gitsense.config import Config
from gitsense.server import App, Server


class Client:
    def __init__(self, srv: Server):
        self.srv = srv
        self.host = f"127.0.0.1:{srv.port}"

    def request(self, method, path, body=None, *, token=True, host=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=60)
        hdrs = {"Host": host if host is not None else self.host}
        if token is True:
            hdrs["X-GitSense-Token"] = self.srv.app.token
        elif token:
            hdrs["X-GitSense-Token"] = token
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, resp.headers, payload

    def get(self, route, **params):
        status, _, payload = self.request("GET", f"{route}?{urlencode(params)}")
        return status, json.loads(payload)

    def post(self, route, body):
        status, _, payload = self.request("POST", route, body)
        return status, json.loads(payload)


@pytest.fixture
def launched(monkeypatch):
    calls: list[tuple] = []
    for name in ("open_terminal", "reveal", "open_in_editor", "open_url", "pick_folder"):
        monkeypatch.setattr(
            server_mod.system, name, lambda *a, _n=name: calls.append((_n, *a))
        )
    return calls


@pytest.fixture
def client(tmp_path, monkeypatch, launched):
    web = (tmp_path / "web").resolve()
    (web / "js").mkdir(parents=True)
    (web / "index.html").write_text('<html><meta name="gitsense-token" content="__GITSENSE_TOKEN__"></html>')
    (web / "js" / "app.js").write_text("console.log('app')\n")
    (tmp_path / "secret.txt").write_text("top secret\n")
    (web / "escape").symlink_to(tmp_path / "secret.txt")
    monkeypatch.setattr(server_mod, "WEB_ROOT", web)
    srv = Server(App(Config(tmp_path / "cfg")), port=0)
    srv.start()
    yield Client(srv)
    srv.stop()


# -- static files and guards --------------------------------------------------------


def test_index_gets_the_token(client):
    key = client.srv.app.launch_key
    assert client.srv.launch_url == f"{client.srv.url}?k={key}"
    for path in (f"/?k={key}", f"/index.html?k={key}"):
        status, headers, body = client.request("GET", path, token=False)
        assert status == 200
        text = body.decode()
        assert client.srv.app.token in text
        assert "__GITSENSE_TOKEN__" not in text
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("query", ["", "?k=", "?k=wrong", "?key=x"])
def test_index_needs_the_launch_key(client, query):
    for path in ("/", "/index.html"):
        status, _, body = client.request("GET", path + query, token=False)
        assert status == 403
        assert client.srv.app.token.encode() not in body


def test_static_file_and_head(client):
    status, headers, body = client.request("GET", "/js/app.js", token=False)
    assert status == 200
    assert headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert "Content-Security-Policy" not in headers
    status, headers, body = client.request("HEAD", "/js/app.js", token=False)
    assert status == 200 and body == b""
    assert headers["Content-Length"] == str(len("console.log('app')\n"))


@pytest.mark.parametrize(
    "path",
    [
        "/../secret.txt",
        "/js/../../secret.txt",
        "/%2e%2e/secret.txt",
        "/..%2fsecret.txt",
        "/escape",
        "/js",
        "/js/",
        "/missing.html",
        "//etc/passwd",
        "/../../../../../../etc/passwd",
    ],
)
def test_static_path_traversal_is_refused(client, path):
    status, _, body = client.request("GET", path, token=False)
    assert status == 404
    assert b"top secret" not in body


@pytest.mark.parametrize("host", ["evil.example:80", "127.0.0.1", "127.0.0.1:1", "", "attacker.com"])
def test_bad_host_header_is_refused(client, host):
    for method, path, body in (("GET", "/", None), ("GET", "/api/state", None), ("POST", "/api/tabs", {})):
        status, _, payload = client.request(method, path, body, host=host)
        assert status == 403
        assert json.loads(payload) == {"error": "Bad host"}


def test_localhost_host_header_is_accepted(client):
    status, _, _ = client.request("GET", "/api/state", host=f"localhost:{client.srv.port}")
    assert status == 200


@pytest.mark.parametrize("token", [False, "wrong", "x" * 200])
def test_api_needs_the_token(client, token):
    for method, path, body in (("GET", "/api/state", None), ("POST", "/api/settings", {"theme": "daylight"})):
        status, _, payload = client.request(method, path, body, token=token)
        assert status == 403
        assert json.loads(payload) == {"error": "Not authorized"}
    assert client.srv.app.config.settings()["theme"] == "graphite"


def test_unknown_routes(client):
    assert client.request("GET", "/api/nope")[0] == 404
    assert client.request("POST", "/api/nope", {})[0] == 404
    assert client.request("POST", "/index.html", {})[0] == 404


@pytest.mark.parametrize("raw", [b"{not json", b"[1, 2]", b'"text"', b"\xff\xfe"])
def test_invalid_json_body(client, raw):
    status, _, payload = client.request(
        "POST", "/api/settings", raw=raw, headers={"Content-Type": "application/json"}
    )
    assert status == 400
    assert json.loads(payload)["error"] == "Invalid JSON"


# -- API happy path ------------------------------------------------------------------


def test_state(client):
    status, data = client.get("/api/state")
    assert status == 200
    assert data["repos"] == [] and data["settings"]["theme"] == "graphite"
    assert data["mods"] == [] and data["mods_dir"].endswith("mods")
    assert data["version"] and data["config_path"].endswith("config.json")


def test_repo_round_trip(client, repo):
    status, data = client.post("/api/repos/add", {"path": str(repo.path / "src")})
    assert status == 200
    rid = data["repo"]["id"]
    assert data["repo"]["path"] == str(repo.path)

    status, ov = client.get("/api/repo/overview", id=rid)
    assert status == 200
    assert ov["id"] == rid and ov["head"]["branch"] == "main"

    repo.write("src/app.py", "print('changed')\n")
    status, log = client.get("/api/repo/log", id=rid)
    assert status == 200
    assert log["commits"][0]["sha"] == "WIP"
    assert log["commits"][1]["subject"] == "Initial commit"

    status, res = client.post(
        "/api/repo/action", {"id": rid, "action": "stage", "args": {"paths": ["src/app.py"]}}
    )
    assert status == 200 and "output" in res
    assert repo.staged_names() == ["src/app.py"]

    status, diff = client.get("/api/repo/diff", id=rid, kind="staged", path="src/app.py")
    assert status == 200
    assert [ln["s"] for ln in diff["hunks"][0]["lines"]] == ["print('hi')", "print('changed')"]

    status, res = client.post(
        "/api/repo/action", {"id": rid, "action": "commit", "args": {"message": "Change app"}}
    )
    assert status == 200
    status, detail = client.get("/api/repo/commit", id=rid, sha=repo.head())
    assert status == 200 and detail["subject"] == "Change app"

    status, fp = client.get("/api/repo/fingerprint", id=rid)
    assert status == 200 and len(fp["fingerprint"]) == 16
    status, summary = client.get("/api/repos/summary", id=rid)
    assert status == 200 and summary["last_commit"]["subject"] == "Change app"
    status, hist = client.get("/api/repo/file_history", id=rid, path="src/app.py")
    assert [c["subject"] for c in hist["commits"]] == ["Change app", "Initial commit"]


def test_git_error_shape(client, repo):
    rid = client.post("/api/repos/add", {"path": str(repo.path)})[1]["repo"]["id"]
    status, data = client.post(
        "/api/repo/action", {"id": rid, "action": "checkout", "args": {"ref": "no-such-branch"}}
    )
    assert status == 400
    assert set(data) == {"error", "detail"}
    assert "no-such-branch" in data["error"]
    status, data = client.post("/api/repo/action", {"id": rid, "action": "explode"})
    assert status == 400 and data["error"] == "Unknown action: explode"


def test_unknown_or_missing_repo(client, repo, tmp_path):
    assert client.get("/api/repo/overview", id="ffffffffffff") == (404, {"error": "Unknown repository"})
    assert client.get("/api/repo/overview")[0] == 404
    assert client.post("/api/repo/action", {"action": "stage_all"})[0] == 404
    assert client.get("/api/repos/summary", id="nope")[0] == 404

    gone = tmp_path / "gone"
    r = client.post("/api/repos/add", {"path": str(repo.path)})[1]["repo"]
    os.rename(repo.path, gone)
    status, data = client.get("/api/repo/overview", id=r["id"])
    assert status == 404 and "Folder not found" in data["error"]
    status, data = client.get("/api/repos/summary", id=r["id"])
    assert status == 200 and data["missing"] is True


def test_add_keeps_a_trailing_space_only_when_that_folder_exists(client, make_repo):
    plain = make_repo("project")
    spaced = make_repo("project ")
    assert client.post("/api/repos/add", {"path": str(spaced.path)})[1]["repo"]["path"] == str(spaced.path)
    assert client.post("/api/repos/add", {"path": f" {plain.path}  "})[1]["repo"]["path"] == str(plain.path)
    assert client.post("/api/repos/add", {"path": "   "}) == (400, {"error": "Choose a folder"})


def test_a_repo_that_lost_its_git_folder_is_not_its_parent(client, make_repo):
    parent = make_repo("parent")
    parent.commit("base", {"keep.txt": "keep\n"})
    child = make_repo("parent/child")
    child.commit("child", {"c.txt": "c\n"})
    rid = client.post("/api/repos/add", {"path": str(child.path)})[1]["repo"]["id"]
    shutil.rmtree(child.path / ".git")
    parent.write("keep.txt", "unsaved\n")
    status, data = client.post("/api/repo/action", {"id": rid, "action": "discard_all", "args": {}})
    assert status == 404 and "Not a Git repository" in data["error"]
    assert parent.read("keep.txt") == "unsaved\n"
    assert client.get("/api/repos/summary", id=rid)[1]["missing"] is True


def test_actions_refuse_a_broken_git_folder_that_resolves_to_the_parent(client, make_repo):
    parent = make_repo("parent")
    parent.commit("base", {"keep.txt": "keep\n"})
    child = make_repo("parent/child")
    child.commit("child", {"c.txt": "c\n"})
    rid = client.post("/api/repos/add", {"path": str(child.path)})[1]["repo"]["id"]
    (child.path / ".git" / "HEAD").unlink()
    parent.write("keep.txt", "unsaved\n")
    status, data = client.post("/api/repo/action", {"id": rid, "action": "discard_all", "args": {}})
    assert status == 404 and "Not a Git repository" in data["error"]
    assert parent.read("keep.txt") == "unsaved\n"


@pytest.mark.skipif(not os.path.exists(__file__.upper()), reason="needs a case-insensitive file system")
def test_actions_accept_a_saved_path_in_different_letter_case(client, make_repo):
    child = make_repo("parent/child")
    child.commit("child", {"c.txt": "c\n"})
    rid = client.post("/api/repos/add", {"path": str(child.path)})[1]["repo"]["id"]
    for entry in client.srv.app.config.data["repos"]:
        if entry["id"] == rid:
            entry["path"] = str(child.path.parent / "CHILD")  # as typed in other letter case
    status, data = client.post("/api/repo/action", {"id": rid, "action": "stage_all", "args": {}})
    assert status == 200, data


def test_add_rejects_non_repositories(client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert client.post("/api/repos/add", {"path": str(plain)})[0] == 400
    assert client.post("/api/repos/add", {"path": ""}) == (400, {"error": "Choose a folder"})
    status, data = client.post("/api/repos/add_many", {"paths": [str(plain)]})
    assert status == 200 and data["repos"] == [] and data["failed"][0]["path"] == str(plain)


def test_commit_and_diff_validate_sha(client, repo):
    rid = client.post("/api/repos/add", {"path": str(repo.path)})[1]["repo"]["id"]
    assert client.get("/api/repo/commit", id=rid, sha="HEAD")[0] == 400
    assert client.get("/api/repo/commit", id=rid, sha="--all")[0] == 400
    assert client.get("/api/repo/diff", id=rid, kind="commit", sha="HEAD~1", path="x")[0] == 400
    assert client.get("/api/repo/diff", id=rid, path="")[0] == 400
    assert client.get("/api/repo/log", id=rid, limit="lots")[0] == 400


def test_settings_tabs_update_remove(client, repo):
    rid = client.post("/api/repos/add", {"path": str(repo.path)})[1]["repo"]["id"]
    status, data = client.post("/api/settings", {"commit_limit": "300", "nope": 1})
    assert data["settings"]["commit_limit"] == 300 and "nope" not in data["settings"]
    assert client.post("/api/tabs", {"open_tabs": [rid, "ghost"], "active": rid}) == (200, {"ok": True})
    status, data = client.post("/api/repos/update", {"id": rid, "name": "Nice", "path": "/etc"})
    assert data["repo"]["name"] == "Nice" and data["repo"]["path"] == str(repo.path)
    assert client.post("/api/repos/update", {"id": "ghost", "name": "x"})[0] == 404
    client.post("/api/repos/remove", {"id": rid})
    state = client.get("/api/state")[1]
    assert state["repos"] == [] and state["open_tabs"] == [] and state["active_tab"] is None


def test_scan_init_and_clone(client, tmp_path, make_repo, remotes):
    make_repo("area/one")
    status, data = client.post("/api/repos/scan", {"path": str(tmp_path / "area"), "depth": 99})
    assert status == 200 and [f["name"] for f in data["found"]] == ["one"]
    assert data["found"][0]["known"] is False

    target = tmp_path / "fresh" / "made"
    status, data = client.post("/api/repos/init", {"path": str(target)})
    assert status == 200 and (target / ".git").is_dir()
    assert data["repo"]["path"] == str(target.resolve())

    parent = tmp_path / "clones"
    parent.mkdir()
    status, data = client.post("/api/repos/clone", {"url": str(remotes.bare.path), "parent": str(parent)})
    assert status == 200
    assert data["repo"]["name"] == "origin"
    assert (parent / "origin" / "file.txt").read_text() == "one\ntwo\nthree\n"
    status, data = client.post("/api/repos/clone", {"url": str(remotes.bare.path), "parent": str(parent)})
    assert status == 400 and "already exists" in data["error"]
    for bad in ({"url": "--upload-pack=x", "parent": str(parent)}, {"url": "", "parent": str(parent)}):
        assert client.post("/api/repos/clone", bad)[0] == 400
    assert client.post("/api/repos/clone", {"url": "x", "parent": str(tmp_path / "missing")})[0] == 400
    for name in ("..", "a/b", "../outside"):
        assert client.post("/api/repos/clone", {"url": "x", "parent": str(parent), "name": name})[0] == 400


# -- /api/repo/open -------------------------------------------------------------------


def test_open_rejects_paths_outside_the_repo(client, repo, tmp_path, launched):
    rid = client.post("/api/repos/add", {"path": str(repo.path)})[1]["repo"]["id"]
    sibling = Path(str(repo.path) + "-sibling")
    sibling.mkdir()
    (repo.path / "link-out").symlink_to(tmp_path)
    for bad in ("../", "..", "/etc", "/etc/passwd", str(sibling), "src/../../", "link-out"):
        for what in ("folder", "editor", "terminal"):
            status, data = client.post("/api/repo/open", {"id": rid, "what": what, "path": bad})
            assert status == 400, (bad, what)
            assert data["error"] == "That path is outside the repository"
    assert launched == []


def test_open_inside_the_repo(client, repo, launched):
    rid = client.post("/api/repos/add", {"path": str(repo.path)})[1]["repo"]["id"]
    assert client.post("/api/repo/open", {"id": rid, "what": "folder", "path": "src/app.py"})[0] == 200
    assert client.post("/api/repo/open", {"id": rid, "what": "editor", "path": "README.md"})[0] == 200
    assert client.post("/api/repo/open", {"id": rid, "what": "terminal"})[0] == 200
    assert client.post("/api/repo/open", {"id": rid, "what": "spaceship"})[0] == 400
    assert launched == [
        ("reveal", str(repo.path / "src" / "app.py")),
        ("open_in_editor", str(repo.path / "README.md"), ""),
        ("open_terminal", str(repo.path)),
    ]


def test_groups_create_update_tabs_delete(client, make_repo):
    a = client.post("/api/repos/add", {"path": str(make_repo("one").path)})[1]["repo"]["id"]
    b = client.post("/api/repos/add", {"path": str(make_repo("two").path)})[1]["repo"]["id"]
    status, data = client.post("/api/groups/create", {"name": "Work", "repos": [a, "ghost"]})
    assert status == 200 and data["group"]["name"] == "Work" and data["group"]["repos"] == [a]
    gid = data["group"]["id"]
    assert client.post("/api/groups/create", {"name": "work"})[0] == 400
    assert client.post("/api/groups/create", {})[0] == 400

    status, data = client.post("/api/groups/update", {"id": gid, "name": "Day job", "add": [b]})
    assert data["group"]["name"] == "Day job" and data["group"]["repos"] == [a, b]
    assert client.post("/api/groups/update", {"id": "g-00000000", "name": "x"})[0] == 404
    assert client.post("/api/groups/update", {"id": gid, "name": " "})[0] == 400

    assert client.post("/api/tabs", {"open_tabs": [a], "active": a}) == (200, {"ok": True})
    assert client.post("/api/tabs", {"open_tabs": [b], "active": b, "group": gid}) == (200, {"ok": True})
    assert client.post("/api/tabs", {"open_tabs": [b], "active": b, "group": "g-00000000"})[0] == 404
    state = client.get("/api/state")[1]
    assert state["active_group"] == gid and state["open_tabs"] == [a] and state["active_tab"] == a
    assert state["groups"] == [{"id": gid, "name": "Day job", "repos": [a, b], "open_tabs": [b], "active_tab": b}]

    client.post("/api/groups/update", {"id": gid, "remove": [b]})
    assert client.get("/api/state")[1]["groups"][0]["open_tabs"] == []
    assert client.post("/api/groups/delete", {"id": gid}) == (200, {"ok": True})
    assert client.post("/api/groups/delete", {"id": gid})[0] == 404
    state = client.get("/api/state")[1]
    assert state["groups"] == [] and state["active_group"] is None and len(state["repos"]) == 2



def test_groups_order(client):
    ids = [client.post("/api/groups/create", {"name": name})[1]["group"]["id"] for name in ("Alpha", "Beta", "Gamma")]
    a, b, c = ids
    assert client.post("/api/groups/order", {"ids": [c, a, b]}) == (200, {"order": [c, a, b]})
    assert client.post("/api/groups/order", {"ids": [b, "g-00000000"]}) == (200, {"order": [b, c, a]})
    assert client.post("/api/groups/order", {}) == (200, {"order": [b, c, a]})
    assert [g["name"] for g in client.get("/api/state")[1]["groups"]] == ["Beta", "Gamma", "Alpha"]
