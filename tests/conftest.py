"""Shared fixtures for the GitSense backend tests.

- Every test runs against throwaway repositories under pytest's tmp_path.
- Git is isolated from the developer's global and system config.
- HOME and the GitSense config dir point at temp folders.
"""

from __future__ import annotations

import itertools
import os
import subprocess
from pathlib import Path

import pytest

_CLOCK = itertools.count(1_700_000_000, 60)


@pytest.fixture(scope="session", autouse=True)
def isolated_environment(tmp_path_factory: pytest.TempPathFactory):
    """Isolate git and GitSense from the machine for the whole session.

    - Inherited GIT_* variables are dropped first.
    - GIT_CEILING_DIRECTORIES stops git from finding a repository above the temp root.
    """
    base = Path(os.path.realpath(tmp_path_factory.getbasetemp()))
    env_dir = base / "_env"
    home = env_dir / "home"
    home.mkdir(parents=True)
    gitconfig = env_dir / "gitconfig"
    gitconfig.write_text("")
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith(("GIT_", "GH_")):
            del os.environ[key]
    os.environ.update(
        GIT_CONFIG_GLOBAL=str(gitconfig),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Test Author",
        GIT_AUTHOR_EMAIL="author@example.com",
        GIT_COMMITTER_NAME="Test Committer",
        GIT_COMMITTER_EMAIL="committer@example.com",
        GIT_CEILING_DIRECTORIES=str(base),
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        GH_CONFIG_DIR=str(env_dir / "gh"),
        GITSENSE_CONFIG_DIR=str(env_dir / "gitsense"),
    )
    yield
    os.environ.clear()
    os.environ.update(saved)


class GitRepo:
    """A small helper around a repository in tmp_path."""

    def __init__(self, path: Path):
        self.path = Path(os.path.realpath(path))

    def __str__(self) -> str:
        return str(self.path)

    def git(self, *args: str, check: bool = True, input: str | None = None, env: dict | None = None) -> str:
        full_env = dict(os.environ)
        full_env.update(env or {})
        res = subprocess.run(
            ["git", *args],
            cwd=self.path,
            input=input,
            capture_output=True,
            text=True,
            env=full_env,
        )
        if check and res.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed:\n{res.stdout}\n{res.stderr}")
        return res.stdout

    def write(self, rel: str, content: str | bytes) -> Path:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_bytes(content.encode("utf-8"))
        return target

    def read(self, rel: str) -> str:
        return (self.path / rel).read_bytes().decode("utf-8")

    def exists(self, rel: str) -> bool:
        return (self.path / rel).exists()

    def index_blob(self, rel: str) -> str | None:
        """The staged content of a file, or None when it is not in the index."""
        res = subprocess.run(
            ["git", "show", f":{rel}"], cwd=self.path, capture_output=True, env=dict(os.environ)
        )
        return res.stdout.decode("utf-8") if res.returncode == 0 else None

    def head_blob(self, rel: str, rev: str = "HEAD") -> str:
        return self.git("show", f"{rev}:{rel}")

    def commit(self, message: str, files: dict[str, str | bytes] | None = None) -> str:
        """Write files, stage everything and commit with a deterministic date."""
        for rel, content in (files or {}).items():
            self.write(rel, content)
        self.git("add", "-A")
        when = f"@{next(_CLOCK)} +0000"
        self.git(
            "commit", "-q", "--allow-empty", "-m", message,
            env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
        )
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def rev(self, ref: str) -> str:
        return self.git("rev-parse", ref).strip()

    def branch(self) -> str:
        return self.git("symbolic-ref", "--short", "-q", "HEAD", check=False).strip()

    def staged_names(self) -> list[str]:
        return [p for p in self.git("diff", "--cached", "--name-only", "-z").split("\0") if p]

    def unstaged_names(self) -> list[str]:
        return [p for p in self.git("diff", "--name-only", "-z").split("\0") if p]

    def porcelain(self) -> str:
        return self.git("status", "--porcelain", "-uall")


def init_repo(path: Path) -> GitRepo:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=dict(os.environ))
    return GitRepo(path)


@pytest.fixture
def make_repo(tmp_path: Path):
    """Factory: make_repo(name) returns an empty (unborn) repository."""

    def factory(name: str = "repo") -> GitRepo:
        return init_repo(tmp_path / name)

    return factory


@pytest.fixture
def empty_repo(make_repo) -> GitRepo:
    return make_repo("empty")


@pytest.fixture
def repo(make_repo) -> GitRepo:
    """A repository with one commit holding README.md and src/app.py."""
    r = make_repo("repo")
    r.commit("Initial commit", {"README.md": "# Demo\n", "src/app.py": "print('hi')\n"})
    return r


class RemoteSetup:
    """A bare remote with two clones, `a` and `b`, both tracking origin/main."""

    def __init__(self, bare: GitRepo, a: GitRepo, b: GitRepo):
        self.bare = bare
        self.a = a
        self.b = b


def make_bare(path: Path) -> GitRepo:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True, env=dict(os.environ)
    )
    return GitRepo(path)


def clone(url: str | Path, dest: Path) -> GitRepo:
    subprocess.run(["git", "clone", "-q", str(url), str(dest)], check=True, env=dict(os.environ))
    return GitRepo(dest)


@pytest.fixture
def remotes(tmp_path: Path) -> RemoteSetup:
    bare = make_bare(tmp_path / "origin.git")
    seed = init_repo(tmp_path / "seed")
    seed.commit("Initial commit", {"file.txt": "one\ntwo\nthree\n"})
    seed.git("remote", "add", "origin", str(bare.path))
    seed.git("push", "-q", "-u", "origin", "main")
    a = clone(bare.path, tmp_path / "a")
    b = clone(bare.path, tmp_path / "b")
    return RemoteSetup(bare, a, b)
