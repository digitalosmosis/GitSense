"""Run the git (and gh) command line tools safely from a GUI process.

- Commands never prompt: no terminal, no editor, no credential questions.
- Output is returned as text; failures raise GitError with git's own message.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

LOCAL_TIMEOUT = 120
NETWORK_TIMEOUT = 600

# GUI launches (Dock, Spotlight) get a minimal PATH, so add the usual tool dirs.
_EXTRA_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]


class GitError(Exception):
    """A git or gh command failed."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "", code: int = 1):
        super().__init__(message)
        self.message = message
        self.stdout = stdout
        self.stderr = stderr
        self.code = code


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    parts = env.get("PATH", "").split(os.pathsep)
    for extra in _EXTRA_PATH:
        if extra not in parts and os.path.isdir(extra):
            parts.append(extra)
    env["PATH"] = os.pathsep.join(p for p in parts if p)
    env.update(
        GIT_TERMINAL_PROMPT="0",  # fail instead of asking for a username or password
        GIT_EDITOR="true",  # accept default messages (merge, rebase --continue)
        GIT_SEQUENCE_EDITOR="true",
        GIT_MERGE_AUTOEDIT="no",
        GIT_PAGER="cat",
        GH_PROMPT_DISABLED="1",
        GH_NO_UPDATE_NOTIFIER="1",
        NO_COLOR="1",
    )
    # Variables that point git at one particular repository must not leak in
    # (they are set when GitSense is started from a git hook or alias).
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
    ):
        env.pop(name, None)
    return env


def find_tool(name: str) -> str | None:
    return shutil.which(name, path=_base_env()["PATH"])


# One lock per repository so two writes never race for .git/index.lock.
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def repo_lock(repo: str | Path) -> threading.RLock:
    key = str(repo)
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


# -- cancelling -------------------------------------------------------------------


class Cancelled(GitError):
    """The user cancelled the action before it finished."""

    def __init__(self) -> None:
        super().__init__("Cancelled", code=130)


# How long git gets to clean up after SIGTERM before it is killed outright.
_TERM_GRACE = 5.0


def _stop(proc: subprocess.Popen, *, signal_kill: bool = False) -> None:
    """Stop a command and everything it started (ssh, index-pack).

    - Commands run in their own session, so their process group is theirs alone.
    - SIGTERM lets git remove its lock files; SIGKILL does not.
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL if signal_kill else signal.SIGTERM)
        elif signal_kill:
            proc.kill()
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError):
        pass


class CancelToken:
    """Lets another thread stop an action that is waiting or running.

    - Commands started while the token is current stop when it is cancelled.
    - Inside protect(), cancel() is refused: the action is past its safe point.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._procs: set[subprocess.Popen] = set()
        self._protected = False
        self.cancelled = False

    def cancel(self) -> bool:
        """Ask the action to stop. False when it can no longer stop safely."""
        with self._guard:
            if self._protected:
                return False
            self.cancelled = True
            procs = list(self._procs)
        for proc in procs:
            _stop(proc)
        if procs:
            # Anything still running after the grace period is killed outright.
            timer = threading.Timer(_TERM_GRACE, lambda: [_stop(p, signal_kill=True) for p in procs])
            timer.daemon = True
            timer.start()
        return True

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled()

    @contextmanager
    def protect(self) -> Iterator[None]:
        """Run a step that must not be cut short. Raises Cancelled if it is already too late."""
        with self._guard:
            if self.cancelled:
                raise Cancelled()
            self._protected = True
        try:
            yield
        finally:
            with self._guard:
                self._protected = False

    def _attach(self, proc: subprocess.Popen) -> None:
        with self._guard:
            self._procs.add(proc)
            stop = self.cancelled and not self._protected
        if stop:
            _stop(proc)

    def _detach(self, proc: subprocess.Popen) -> None:
        with self._guard:
            self._procs.discard(proc)


_current_token: ContextVar[CancelToken | None] = ContextVar("gitsense_cancel_token", default=None)


@contextmanager
def cancellable(token: CancelToken | None) -> Iterator[None]:
    """Make token the one every command in this thread listens to."""
    reset = _current_token.set(token)
    try:
        yield
    finally:
        _current_token.reset(reset)


@contextmanager
def protected() -> Iterator[None]:
    """Mark a step of the current action as one that must run to the end."""
    token = _current_token.get()
    if token is None:
        yield
        return
    with token.protect():
        yield


@contextmanager
def locked(repo: str | Path) -> Iterator[None]:
    """Hold the repository lock, giving up if the current action is cancelled while waiting."""
    lock = repo_lock(repo)
    token = _current_token.get()
    if token is None:
        with lock:
            yield
        return
    while not lock.acquire(timeout=0.2):
        token.check()
    try:
        token.check()
        yield
    finally:
        lock.release()


def _clean_error(stderr: str, stdout: str, code: int, tool: str) -> str:
    text = (stderr or "").strip() or (stdout or "").strip()
    if not text:
        return f"{tool} exited with status {code}"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # Prefer the lines git marks as the actual problem.
    marked = [ln for ln in lines if ln.startswith(("fatal:", "error:", "CONFLICT", "! "))]
    picked = marked or lines
    return "\n".join(picked[:12])


def run(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    input_text: str | None = None,
    timeout: float = LOCAL_TIMEOUT,
    check: bool = True,
    ok_codes: tuple[int, ...] = (0,),
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a tool and return the completed process with text output."""
    env = _base_env()
    if env_extra:
        env.update(env_extra)
    exe = shutil.which(argv[0], path=env["PATH"])
    if exe is None:
        raise GitError(f"{argv[0]} is not installed or not on PATH", code=127)
    kwargs: dict = {}
    if os.name == "posix":
        # A new session has no controlling terminal, so ssh and gpg cannot
        # stop to ask a question on the terminal GitSense was started from.
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    token = _current_token.get()
    if token is not None:
        token.check()
    try:
        proc = subprocess.Popen(
            [exe, *argv[1:]],
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL if input_text is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **kwargs,
        )
    except OSError as exc:
        raise GitError(f"could not run {argv[0]}: {exc}", code=126) from exc
    with proc:
        if token is not None:
            token._attach(proc)
        try:
            raw_out, raw_err = proc.communicate(
                input_text.encode("utf-8") if input_text is not None else None, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            _stop(proc, signal_kill=True)
            proc.communicate()
            raise GitError(f"{argv[0]} timed out after {int(timeout)} s", code=124) from exc
        except BaseException:
            _stop(proc, signal_kill=True)
            raise
        finally:
            if token is not None:
                token._detach(proc)
    out = raw_out.decode("utf-8", errors="replace")
    err = raw_err.decode("utf-8", errors="replace")
    # A command that finished before the cancel landed keeps its real result.
    if token is not None and token.cancelled and proc.returncode != 0:
        raise Cancelled()
    done = subprocess.CompletedProcess(argv, proc.returncode, out, err)
    if check and proc.returncode not in ok_codes:
        raise GitError(
            _clean_error(err, out, proc.returncode, argv[0]),
            stdout=out,
            stderr=err,
            code=proc.returncode,
        )
    return done


_GIT_PREFIX = ["git", "-c", "core.quotepath=false", "-c", "color.ui=false"]


def git(
    repo: str | Path | None,
    args: list[str],
    *,
    input_text: str | None = None,
    timeout: float = LOCAL_TIMEOUT,
    check: bool = True,
    ok_codes: tuple[int, ...] = (0,),
    write: bool = False,
    env_extra: dict[str, str] | None = None,
) -> str:
    """Run git in a repository and return stdout.

    - Read commands skip optional locks so background refreshes never block a write.
    - Write commands hold the repository lock.
    """
    env = dict(env_extra or {})
    if not write:
        env.setdefault("GIT_OPTIONAL_LOCKS", "0")
        return run(
            [*_GIT_PREFIX, *args],
            cwd=repo,
            input_text=input_text,
            timeout=timeout,
            check=check,
            ok_codes=ok_codes,
            env_extra=env,
        ).stdout
    with repo_lock(repo or ""):
        return run(
            [*_GIT_PREFIX, *args],
            cwd=repo,
            input_text=input_text,
            timeout=timeout,
            check=check,
            ok_codes=ok_codes,
            env_extra=env,
        ).stdout


def git_result(
    repo: str | Path | None,
    args: list[str],
    *,
    input_text: str | None = None,
    timeout: float = LOCAL_TIMEOUT,
    write: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run git without raising; the caller inspects the return code."""
    env = {} if write else {"GIT_OPTIONAL_LOCKS": "0"}
    if write:
        with repo_lock(repo or ""):
            return run(
                [*_GIT_PREFIX, *args],
                cwd=repo,
                input_text=input_text,
                timeout=timeout,
                check=False,
                env_extra=env,
            )
    return run(
        [*_GIT_PREFIX, *args],
        cwd=repo,
        input_text=input_text,
        timeout=timeout,
        check=False,
        env_extra=env,
    )
