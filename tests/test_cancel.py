"""Tests for cancelling fetch, pull and push while they wait or run."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from gitsense import actions
from gitsense.gitcmd import CancelToken, Cancelled, cancellable, protected, repo_lock, run
from gitsense.server import App

from test_server import Client, client, launched  # noqa: F401 - fixtures


def in_thread(fn):
    """Run fn in a thread; return a function that waits and gives its result or exception."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()

    def wait(timeout=10.0):
        t.join(timeout)
        assert not t.is_alive(), "the action did not stop"
        return box

    return wait


def hang_remote(monkeypatch, repo):
    """Point origin at an ssh URL whose 'ssh' just sleeps, as a stalled network would."""
    repo.git("remote", "set-url", "origin", "ssh://example.invalid/x.git")
    monkeypatch.setenv("GIT_SSH_COMMAND", "sh -c 'sleep 60' --")
    monkeypatch.setenv("GIT_SSH_VARIANT", "simple")


def lock_files(repo) -> list[Path]:
    return [p for p in (Path(repo.path) / ".git").rglob("*.lock")]


# -- the token ----------------------------------------------------------------------


def test_cancel_stops_a_running_command_and_its_children():
    token = CancelToken()

    def go():
        with cancellable(token):
            # The sleep is a grandchild: only a process group kill ends it.
            run(["sh", "-c", "sleep 60; true"])

    wait = in_thread(go)
    time.sleep(0.3)
    started = time.monotonic()
    assert token.cancel() is True
    box = wait()
    assert isinstance(box.get("error"), Cancelled)
    assert time.monotonic() - started < 4


def test_cancelled_token_refuses_new_commands():
    token = CancelToken()
    token.cancel()
    with cancellable(token), pytest.raises(Cancelled):
        run(["true"])


def test_finished_command_keeps_its_result_after_a_late_cancel():
    token = CancelToken()
    with cancellable(token):
        res = run(["true"])
    token.cancel()
    assert res.returncode == 0


def test_protected_step_refuses_cancel():
    token = CancelToken()
    with cancellable(token), protected():
        assert token.cancel() is False
    assert token.cancel() is True


def test_protected_step_does_not_start_after_a_cancel():
    token = CancelToken()
    token.cancel()
    with cancellable(token), pytest.raises(Cancelled):
        with protected():
            pass


def test_no_token_runs_as_before():
    assert run(["true"]).returncode == 0
    with protected():
        pass


# -- actions --------------------------------------------------------------------------


def test_cancel_while_waiting_for_the_repository_lock(remotes):
    token = CancelToken()
    lock = repo_lock(str(remotes.a))
    lock.acquire()
    try:
        def go():
            with cancellable(token):
                return actions.perform(str(remotes.a), "fetch", {})

        wait = in_thread(go)
        time.sleep(0.3)
        token.cancel()
        assert isinstance(wait().get("error"), Cancelled)
    finally:
        lock.release()


@pytest.mark.parametrize("action", ["push", "fetch", "pull"])
def test_cancel_a_stalled_network_action(remotes, monkeypatch, action):
    remotes.a.commit("from a", {"a.txt": "a\n"})
    before = remotes.a.head()
    hang_remote(monkeypatch, remotes.a)
    token = CancelToken()

    def go():
        with cancellable(token):
            return actions.perform(str(remotes.a), action, {})

    wait = in_thread(go)
    time.sleep(0.5)
    started = time.monotonic()
    assert token.cancel() is True
    assert isinstance(wait().get("error"), Cancelled)
    assert time.monotonic() - started < 4
    assert remotes.a.head() == before
    assert lock_files(remotes.a) == []
    assert remotes.a.git("status", "--porcelain") == ""


# -- server ---------------------------------------------------------------------------


def add(client, repo) -> str:
    return client.post("/api/repos/add", {"path": str(repo.path)})[1]["repo"]["id"]


def test_server_cancels_a_running_push(client, remotes, monkeypatch):
    rid = add(client, remotes.a)
    remotes.a.commit("from a", {"a.txt": "a\n"})
    hang_remote(monkeypatch, remotes.a)
    wait = in_thread(lambda: client.post("/api/repo/action", {"id": rid, "action": "push", "args": {}, "op": "op-1"}))
    time.sleep(0.5)
    assert client.post("/api/repo/cancel", {"id": rid, "op": "op-1"}) == (200, {"cancelled": True})
    status, data = wait()["value"]
    assert status == 409
    assert data == {"error": "Cancelled", "cancelled": True}


def test_server_remembers_a_cancel_that_overtook_its_action(client, remotes):
    rid = add(client, remotes.a)
    assert client.post("/api/repo/cancel", {"id": rid, "op": "early"}) == (200, {"cancelled": True})
    status, data = client.post("/api/repo/action", {"id": rid, "action": "fetch", "args": {}, "op": "early"})
    assert status == 409 and data["cancelled"] is True
    # The early cancel is used up: the same id runs normally next time.
    status, _ = client.post("/api/repo/action", {"id": rid, "action": "fetch", "args": {}, "op": "early"})
    assert status == 200


def test_server_action_without_op_runs_as_before(client, remotes):
    rid = add(client, remotes.a)
    status, _ = client.post("/api/repo/action", {"id": rid, "action": "fetch", "args": {}})
    assert status == 200


@pytest.mark.parametrize("op", ["", "has space", "x" * 65, "../x"])
def test_server_refuses_bad_op_ids(client, remotes, op):
    rid = add(client, remotes.a)
    status, data = client.post("/api/repo/cancel", {"id": rid, "op": op})
    assert status == 400


def test_early_cancels_expire(monkeypatch, tmp_path):
    from gitsense import server as server_mod
    from gitsense.config import Config

    app = App(Config(tmp_path / "cfg"))
    clock = [1000.0]
    monkeypatch.setattr(server_mod.time, "monotonic", lambda: clock[0])
    app.repo_cancel({"id": "r", "op": "old"})
    clock[0] += server_mod.EARLY_CANCEL_TTL + 1
    app.repo_cancel({"id": "r", "op": "new"})
    assert set(app._early_cancels) == {("r", "new")}
