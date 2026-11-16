"""Tests for repo.parse_remote_url and repo.github_info."""

from __future__ import annotations

import pytest

from gitsense import repo as repo_mod


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/acme/widgets.git", ("github.com", "acme", "widgets")),
        ("https://github.com/acme/widgets", ("github.com", "acme", "widgets")),
        ("https://github.com/acme/widgets/", ("github.com", "acme", "widgets")),
        ("http://github.com/acme/widgets.git", ("github.com", "acme", "widgets")),
        ("https://user:tok@github.com/acme/widgets.git", ("github.com", "acme", "widgets")),
        ("https://github.com:443/acme/widgets.git", ("github.com", "acme", "widgets")),
        ("git@github.com:acme/widgets.git", ("github.com", "acme", "widgets")),
        ("git@github.com:acme/widgets", ("github.com", "acme", "widgets")),
        ("github.com:acme/widgets.git", ("github.com", "acme", "widgets")),
        ("ssh://git@github.com/acme/widgets.git", ("github.com", "acme", "widgets")),
        ("ssh://git@github.com:22/acme/widgets.git", ("github.com", "acme", "widgets")),
        ("ssh://git@ssh.github.com:443/acme/widgets.git", ("github.com", "acme", "widgets")),
        ("git://github.com/acme/widgets.git", ("github.com", "acme", "widgets")),
        ("git@github.com-work:acme/widgets.git", ("github.com", "acme", "widgets")),
        ("git@github.com-personal:me/dots", ("github.com", "me", "dots")),
        ("  git@github.com:acme/widgets.git\n", ("github.com", "acme", "widgets")),
        ("git@github.com:acme/my.dotted.repo.git", ("github.com", "acme", "my.dotted.repo")),
        ("https://github.example.com/team/svc.git", ("github.example.com", "team", "svc")),
        ("git@github.corp.internal:team/svc.git", ("github.corp.internal", "team", "svc")),
        ("https://gitlab.com/group/project.git", ("gitlab.com", "group", "project")),
        ("git@bitbucket.org:team/repo.git", ("bitbucket.org", "team", "repo")),
    ],
)
def test_parse_remote_url(url, expected):
    info = repo_mod.parse_remote_url(url)
    assert info is not None
    assert (info["host"], info["owner"], info["repo"]) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "/srv/git/widgets.git",
        "file:///srv/git/widgets.git",
        "../widgets.git",
        "https://gitlab.com/group/sub/project.git",
        "https://github.com/acme",
    ],
)
def test_parse_remote_url_rejects(url):
    assert repo_mod.parse_remote_url(url) is None


def remotes_of(*pairs: tuple[str, str]) -> list[dict]:
    return [{"name": n, "url": u} for n, u in pairs]


def test_github_info_prefers_origin():
    info = repo_mod.github_info(
        remotes_of(
            ("aaa", "git@github.com:fork/widgets.git"),
            ("origin", "https://github.com/acme/widgets.git"),
        )
    )
    assert info == {
        "host": "github.com",
        "slug": "acme/widgets",
        "remote": "origin",
        "url": "https://github.com/acme/widgets",
    }


def test_github_info_falls_back_to_another_github_remote():
    info = repo_mod.github_info(
        remotes_of(
            ("origin", "git@gitlab.com:acme/widgets.git"),
            ("zeta", "git@github.com:z/w.git"),
            ("beta", "git@github.com-work:b/w.git"),
        )
    )
    assert info["remote"] == "beta"
    assert info["slug"] == "b/w"
    assert info["url"] == "https://github.com/b/w"


def test_github_info_enterprise_host():
    info = repo_mod.github_info(remotes_of(("origin", "git@github.example.com:team/svc.git")))
    assert info["host"] == "github.example.com"
    assert info["url"] == "https://github.example.com/team/svc"


@pytest.mark.parametrize(
    "url",
    ["git@gitlab.com:acme/widgets.git", "https://bitbucket.org/a/b.git", "/srv/git/x.git", ""],
)
def test_github_info_non_github(url):
    assert repo_mod.github_info(remotes_of(("origin", url))) is None


def test_github_info_no_remotes():
    assert repo_mod.github_info([]) is None
