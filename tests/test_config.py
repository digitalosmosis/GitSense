"""Tests for config.Config."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from gitsense import config as config_mod
from gitsense.config import DEFAULT_SETTINGS, Config


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    return tmp_path / "cfg"


def test_config_dir_honours_override(tmp_path, monkeypatch):
    monkeypatch.setenv("GITSENSE_CONFIG_DIR", str(tmp_path / "x"))
    assert config_mod.config_dir() == tmp_path / "x"
    # The session fixture points the default at a temp folder.
    assert Config().dir == tmp_path / "x"


def test_session_never_uses_the_real_config_dir():
    real = Path(os.path.expanduser("~")).resolve()
    assert Path(os.environ["GITSENSE_CONFIG_DIR"]).resolve() != real / ".config" / "gitsense"
    assert "pytest" in os.environ["HOME"] or "_env" in os.environ["HOME"]


def test_defaults_without_a_file(cfg_dir):
    c = Config(cfg_dir)
    assert c.repos() == []
    assert c.settings() == DEFAULT_SETTINGS
    assert not c.path.exists()


def test_add_repo_persists_with_private_mode(cfg_dir, repo):
    c = Config(cfg_dir)
    entry = c.add_repo(str(repo.path / "src" / ".."))
    assert entry["path"] == str(repo.path)
    assert entry["name"] == "repo"
    assert entry["id"] == config_mod.repo_id(repo.path)
    assert stat.S_IMODE(c.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg_dir.stat().st_mode) == 0o700
    assert not list(cfg_dir.glob(".config-*.tmp"))
    again = Config(cfg_dir)
    assert again.repos() == [entry]


def test_add_repo_is_idempotent(cfg_dir, repo):
    c = Config(cfg_dir)
    a = c.add_repo(str(repo.path))
    b = c.add_repo(str(repo.path) + "/")
    assert a == b
    assert len(c.repos()) == 1


def test_repo_id_resolves_symlinks(tmp_path, repo):
    link = tmp_path / "link"
    link.symlink_to(repo.path)
    assert config_mod.repo_id(link) == config_mod.repo_id(repo.path)


def test_update_repo_only_allows_known_fields(cfg_dir, repo):
    c = Config(cfg_dir)
    rid = c.add_repo(str(repo.path))["id"]
    updated = c.update_repo(rid, name="Nice", pinned=True, group="work", path="/etc", id="x")
    assert updated["name"] == "Nice" and updated["pinned"] is True and "group" not in updated
    assert updated["path"] == str(repo.path) and updated["id"] == rid
    assert c.update_repo("missing", name="x") is None
    assert Config(cfg_dir).get_repo(rid)["name"] == "Nice"


def test_remove_repo_prunes_tabs(cfg_dir, make_repo):
    c = Config(cfg_dir)
    a = c.add_repo(str(make_repo("one").path))["id"]
    b = c.add_repo(str(make_repo("two").path))["id"]
    c.set_tabs([a, b, a, "unknown"], b)
    assert c.snapshot()["open_tabs"] == [a, b]
    assert c.snapshot()["active_tab"] == b
    c.remove_repo(b)
    snap = Config(cfg_dir).snapshot()
    assert [r["id"] for r in snap["repos"]] == [a]
    assert snap["open_tabs"] == [a]
    assert snap["active_tab"] is None


def test_set_tabs_rejects_unknown_active(cfg_dir, repo):
    c = Config(cfg_dir)
    rid = c.add_repo(str(repo.path))["id"]
    c.set_tabs([rid], "nope")
    assert c.snapshot()["active_tab"] is None


def test_load_ignores_unknown_and_invalid_data(cfg_dir):
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "repos": [
                    {"path": "/somewhere/proj"},
                    {"name": "no path"},
                    "not a dict",
                    {"path": "/x/y", "id": "fixed", "name": "Y", "pinned": True},
                ],
                "open_tabs": ["fixed", "ghost"],
                "active_tab": "ghost",
                "settings": {"theme": "dark", "bogus": 1, "commit_limit": 10},
                "unknown_top_level": {"a": 1},
            }
        )
    )
    c = Config(cfg_dir)
    repos = c.repos()
    assert [r["path"] for r in repos] == ["/somewhere/proj", "/x/y"]
    assert repos[0]["id"] == config_mod.repo_id("/somewhere/proj")
    assert repos[0]["name"] == "proj" and repos[0]["pinned"] is False
    assert repos[1]["id"] == "fixed" and repos[1]["pinned"] is True
    snap = c.snapshot()
    assert snap["open_tabs"] == ["fixed"]
    assert snap["active_tab"] is None
    assert "unknown_top_level" not in snap
    assert snap["settings"]["theme"] == "graphite"  # old "dark" value is migrated
    assert snap["settings"]["commit_limit"] == 50  # stored values are clamped on load too
    assert "bogus" not in snap["settings"]


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "[1, 2]",
        "null",
        '{"repos": "nope"}',
        '{"repos": 5}',
        '{"open_tabs": 5}',
        '{"repos": [{"path": 5}]}',
        '{"open_tabs": [[1]], "active_tab": [1]}',
        '{"repos": [{"path": "/x", "id": ["l"]}]}',
        '{"settings": "dark"}',
    ],
)
def test_load_survives_corrupt_files(cfg_dir, content):
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(content)
    try:
        c = Config(cfg_dir)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"Config raised {exc!r} for {content!r}")
    assert c.settings() == DEFAULT_SETTINGS


def test_update_settings_coerces_types(cfg_dir):
    c = Config(cfg_dir)
    s = c.update_settings(
        {
            "fetch_prune": 0,
            "auto_fetch_minutes": "15",
            "commit_limit": 2500.7,
            "sidebar_width": "wide",
            "theme": 7,
            "editor": "code --wait",
            "unknown": True,
        }
    )
    assert s["fetch_prune"] is False
    assert s["auto_fetch_minutes"] == 15
    assert s["commit_limit"] == 2500
    assert s["sidebar_width"] == DEFAULT_SETTINGS["sidebar_width"]
    assert s["theme"] == DEFAULT_SETTINGS["theme"]  # not one of the allowed choices
    assert s["editor"] == "code --wait"
    assert "unknown" not in s
    assert Config(cfg_dir).settings() == s


def test_bool_settings_are_not_treated_as_ints(cfg_dir):
    c = Config(cfg_dir)
    s = c.update_settings({"show_remote_branches_in_graph": 5, "commit_limit": True})
    assert s["show_remote_branches_in_graph"] is True
    assert s["commit_limit"] == 50  # True counts as 1, then is raised to the minimum


def test_snapshot_is_a_deep_copy(cfg_dir, repo):
    c = Config(cfg_dir)
    c.add_repo(str(repo.path))
    snap = c.snapshot()
    snap["repos"][0]["name"] = "mutated"
    snap["settings"]["theme"] = "mutated"
    assert c.repos()[0]["name"] == "repo"
    assert c.settings()["theme"] == "graphite"


def test_bool_settings_accept_true_false_strings(cfg_dir):
    c = Config(cfg_dir)
    assert c.update_settings({"fetch_prune": "false"})["fetch_prune"] is False
    assert c.update_settings({"fetch_prune": "true"})["fetch_prune"] is True


def test_choice_settings_reject_unknown_values(cfg_dir):
    c = Config(cfg_dir)
    s = c.update_settings({"pull_mode": "yolo", "diff_view": "split", "theme": "dark"})
    assert s["pull_mode"] == DEFAULT_SETTINGS["pull_mode"]
    assert s["diff_view"] == "split"
    assert s["theme"] == "graphite"


def test_number_settings_are_clamped(cfg_dir):
    c = Config(cfg_dir)
    s = c.update_settings({"commit_limit": 10**9, "auto_fetch_minutes": -5, "detail_width": 1})
    assert s["commit_limit"] == 50000
    assert s["auto_fetch_minutes"] == 0
    assert s["detail_width"] == 200


# -- groups ---------------------------------------------------------------------


def three_repos(cfg_dir, make_repo):
    c = Config(cfg_dir)
    return c, [c.add_repo(str(make_repo(name).path))["id"] for name in ("one", "two", "three")]


def test_add_group_keeps_only_known_repos(cfg_dir, make_repo):
    c, (a, b, _) = three_repos(cfg_dir, make_repo)
    group = c.add_group("  Firmware   tools ", [b, "ghost", a, b])
    assert group["name"] == "Firmware tools"
    assert group["repos"] == [b, a]
    assert group["open_tabs"] == [] and group["active_tab"] is None
    assert Config(cfg_dir).groups() == [group]


def test_group_names_must_be_present_and_unique(cfg_dir):
    c = Config(cfg_dir)
    first = c.add_group("Web")
    second = c.add_group("Tools")
    for bad in ("", "   ", None, 5, "web", " WEB "):
        with pytest.raises(config_mod.GroupError):
            c.add_group(bad)
    with pytest.raises(config_mod.GroupError):
        c.update_group(second["id"], name="web")
    # A group may keep its own name, in any letter case.
    assert c.update_group(first["id"], name="WEB")["name"] == "WEB"
    assert len(c.add_group("x" * 200)["name"]) == config_mod.MAX_GROUP_NAME


def test_update_group_adds_and_removes_repos(cfg_dir, make_repo):
    c, (a, b, d) = three_repos(cfg_dir, make_repo)
    gid = c.add_group("Work", [a, b])["id"]
    c.set_tabs([a, b], b, gid)
    group = c.update_group(gid, add=[d, a, "ghost"], remove=[b])
    assert group["repos"] == [a, d]
    assert group["open_tabs"] == [a] and group["active_tab"] is None
    assert c.update_group("g-00000000", name="x") is None
    assert Config(cfg_dir).groups() == [group]


def test_each_list_keeps_its_own_tabs(cfg_dir, make_repo):
    c, (a, b, d) = three_repos(cfg_dir, make_repo)
    gid = c.add_group("Work", [a, b])["id"]
    assert c.set_tabs([a, d], d) is True
    # A group only keeps tabs for its own repositories.
    assert c.set_tabs([b, d, b], d, gid) is True
    snap = Config(cfg_dir).snapshot()
    assert snap["open_tabs"] == [a, d] and snap["active_tab"] == d
    assert snap["groups"][0]["open_tabs"] == [b] and snap["groups"][0]["active_tab"] is None
    assert snap["active_group"] == gid
    c.set_tabs([a], a)
    assert c.snapshot()["active_group"] is None
    assert c.snapshot()["groups"][0]["open_tabs"] == [b]


def test_set_tabs_for_an_unknown_group_changes_nothing(cfg_dir, repo):
    c = Config(cfg_dir)
    rid = c.add_repo(str(repo.path))["id"]
    c.set_tabs([rid], rid)
    before = c.snapshot()
    assert c.set_tabs([], None, "g-00000000") is False
    assert c.snapshot() == before


def test_remove_repo_leaves_every_group(cfg_dir, make_repo):
    c, (a, b, _) = three_repos(cfg_dir, make_repo)
    gid = c.add_group("Work", [a, b])["id"]
    c.set_tabs([b, a], b)
    c.set_tabs([a, b], b, gid)
    c.remove_repo(b)
    snap = Config(cfg_dir).snapshot()
    group = snap["groups"][0]
    assert group["repos"] == [a] and group["open_tabs"] == [a] and group["active_tab"] is None
    assert snap["open_tabs"] == [a] and snap["active_tab"] is None


def test_remove_group_keeps_its_repos(cfg_dir, make_repo):
    c, (a, b, d) = three_repos(cfg_dir, make_repo)
    gid = c.add_group("Work", [a, b])["id"]
    c.set_tabs([a], a, gid)
    assert c.remove_group(gid) is True
    assert c.remove_group(gid) is False
    snap = Config(cfg_dir).snapshot()
    assert snap["groups"] == [] and snap["active_group"] is None
    assert [r["id"] for r in snap["repos"]] == [a, b, d]


def test_order_groups(cfg_dir):
    cfg = Config(cfg_dir)
    a, b, c = (cfg.add_group(name)["id"] for name in ("Alpha", "Beta", "Gamma"))
    assert cfg.order_groups([c, a, b]) == [c, a, b]
    # Groups left out keep their place after the named ones; junk is ignored.
    assert cfg.order_groups([b, "g-00000000", b, 7]) == [b, c, a]
    assert cfg.order_groups("nope") == [b, c, a]
    assert [g["name"] for g in Config(cfg_dir).groups()] == ["Beta", "Gamma", "Alpha"]


def test_group_limit(cfg_dir, monkeypatch):
    monkeypatch.setattr(config_mod, "MAX_GROUPS", 2)
    c = Config(cfg_dir)
    c.add_group("one")
    c.add_group("two")
    with pytest.raises(config_mod.GroupError):
        c.add_group("three")


def test_load_cleans_stored_groups(cfg_dir):
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "repos": [
                    {"path": "/x/a", "id": "a", "group": "old label"},
                    {"path": "/x/b", "id": "b"},
                ],
                "groups": [
                    {"id": "g-0000000a", "name": " Work ", "repos": ["b", "ghost", "b", 7], "open_tabs": ["a", "b"], "active_tab": "a"},
                    {"id": "g-0000000a", "name": "Same id"},
                    {"id": "g-0000000d", "name": "WORK"},
                    {"id": "g-0000000e\n", "name": "Newline in id"},
                    {"id": "bad id", "name": "Bad"},
                    {"id": "g-0000000b", "name": "  "},
                    {"id": "g-0000000c", "name": "Empty", "repos": "nope", "open_tabs": 3},
                    "not a dict",
                ],
                "active_group": "g-0000000b",
            }
        )
    )
    c = Config(cfg_dir)
    assert "group" not in c.repos()[0]
    assert c.groups() == [
        {"id": "g-0000000a", "name": "Work", "repos": ["b"], "open_tabs": ["b"], "active_tab": None},
        {"id": "g-0000000c", "name": "Empty", "repos": [], "open_tabs": [], "active_tab": None},
    ]
    assert c.snapshot()["active_group"] is None


def test_current_tab_follows_the_list_on_screen(cfg_dir, make_repo):
    c, (a, b, _) = three_repos(cfg_dir, make_repo)
    gid = c.add_group("Work", [b])["id"]
    c.set_tabs([a], a)
    assert c.current_tab() == a
    c.set_tabs([b], b, gid)
    assert c.current_tab() == b
    c.set_tabs([], None, gid)
    assert c.current_tab() is None  # the full list still has a tab, but it is not on screen
    c.remove_group(gid)
    assert c.current_tab() == a

