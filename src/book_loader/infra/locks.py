"""Lock files for auth folders and libraries (REFACTOR_PLAN §5.8).

A second book-loader run, such as a watcher plus a manual ``process``, waits briefly
for the lock and then stops with a clear message; it never corrupts pending records,
loans or ``book.json``.

The lock is a file created atomically (``O_CREAT | O_EXCL``). It records the process
ID, host name, start time and a random token. A lock left by a crashed run is stale
when its process is gone on the same host, and is then taken over. A lock from another
host (a library on a network drive) is never judged stale, since its process can't be
checked from here.

Breaking a stale lock is itself guarded by a short-lived ``<name>.break`` file, so two
runs that find the same stale lock can't both remove it and then delete each other's
new lock.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from ..domain.errors import LockedError
from .fs import retry_locked

__all__ = ["DEFAULT_TIMEOUT", "Lock", "LockInfo", "process_alive"]

DEFAULT_TIMEOUT = 10.0
POLL = 0.1
# A lock file that can't be read is being written by another run, or was left
# half-written by a crash; after this long it counts as stale.
UNREADABLE_GRACE = 10.0
# A .break file older than this was left by a run that crashed while breaking a lock.
BREAK_GRACE = 30.0


def process_alive(pid: int) -> bool:
    """Whether a process with this ID is running on this machine."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, but belongs to another user
    return True


def _windows_process_alive(pid: int) -> bool:
    if sys.platform != "win32":  # the caller checks; this tells the type checker
        raise OSError("only available on Windows")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    query_limited_information, still_active, access_denied = 0x1000, 259, 5

    handle = kernel32.OpenProcess(query_limited_information, False, pid)
    if not handle:
        # Access denied means the process exists but belongs to another user.
        return ctypes.get_last_error() == access_denied
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


@dataclass(frozen=True)
class LockInfo:
    """Who holds a lock."""

    pid: int
    host: str
    started: datetime
    token: str
    purpose: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "pid": self.pid,
                "host": self.host,
                "started": self.started.isoformat(),
                "token": self.token,
                "purpose": self.purpose,
            }
        )

    @classmethod
    def from_json(cls, text: str) -> LockInfo:
        data: dict[str, Any] = json.loads(text)
        return cls(
            pid=int(data["pid"]),
            host=str(data["host"]),
            started=datetime.fromisoformat(data["started"]),
            token=str(data["token"]),
            purpose=str(data.get("purpose", "")),
        )

    def describe(self) -> str:
        when = self.started.astimezone().strftime("%Y-%m-%d %H:%M")
        what = f" ({self.purpose})" if self.purpose else ""
        return f"process {self.pid} on {self.host}{what}, since {when}"


@dataclass(frozen=True)
class _Unreadable:
    """A lock file that exists but can't be read, and how old it is in seconds."""

    age: float


_Holder = LockInfo | _Unreadable | None


class Lock:
    """An exclusive lock held through a lock file at ``path``.

    ``acquire`` waits up to ``timeout`` seconds, taking over a stale lock, then raises
    ``LockedError``. Use it as a context manager; the lock is released on exit, also
    after an exception. ``path``'s folder must exist (the caller creates it with
    ``fs.private_dir`` when it holds keys). ``purpose`` is shown to a run that has to
    wait, for example "fulfilling book.acsm".
    """

    def __init__(
        self,
        path: Path,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        purpose: str = "",
        poll: float = POLL,
        is_alive: Callable[[int], bool] = process_alive,
        host: str | None = None,
        pid: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.purpose = purpose
        self.poll = poll
        self._is_alive = is_alive
        self._host = host or socket.gethostname()
        self._pid = pid if pid is not None else os.getpid()
        self._clock = clock
        self._sleep = sleep
        self.info: LockInfo | None = None

    @property
    def held(self) -> bool:
        return self.info is not None

    def acquire(self) -> None:
        if self.info is not None:
            raise RuntimeError(f"{self.path} is already held by this Lock")
        deadline = self._clock() + self.timeout
        while True:
            info = LockInfo(
                self._pid, self._host, datetime.now(UTC), secrets.token_hex(8), self.purpose
            )
            if self._create(info):
                self.info = info
                return
            holder = self._read()
            if self._is_stale(holder):
                self._break(holder)
                continue
            if self._clock() >= deadline:
                raise self._locked_error(holder)
            self._sleep(min(self.poll, max(0.0, deadline - self._clock())))

    def release(self) -> None:
        """Remove the lock file if it is still ours."""
        info, self.info = self.info, None
        if info is None:
            return
        current = self._read()
        if isinstance(current, LockInfo) and current.token == info.token:
            self._unlink()

    def __enter__(self) -> Lock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    # --- Internals ---------------------------------------------------------------------

    def _create(self, info: LockInfo) -> bool:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        except PermissionError:
            # Windows refuses to create a file that is being deleted; try again later.
            if sys.platform == "win32" and self.path.exists():
                return False
            raise
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(info.to_json())
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def _read(self) -> _Holder:
        """Who holds the lock; ``_Unreadable`` if the file can't be read; None if it's gone."""
        try:
            text = self.path.read_text(encoding="utf-8")
            return LockInfo.from_json(text)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            try:
                return _Unreadable(max(0.0, time.time() - self.path.stat().st_mtime))
            except OSError:
                return None

    def _is_stale(self, holder: _Holder) -> bool:
        if holder is None:
            return False  # gone already; the next attempt creates it
        if isinstance(holder, _Unreadable):
            return holder.age > UNREADABLE_GRACE
        return holder.host == self._host and not self._is_alive(holder.pid)

    def _break(self, stale: _Holder) -> None:
        """Remove the stale lock, unless someone replaced it meanwhile."""
        guard = self.path.with_name(self.path.name + ".break")
        try:
            fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except (FileExistsError, PermissionError):
            with contextlib.suppress(OSError):
                if time.time() - guard.stat().st_mtime > BREAK_GRACE:
                    guard.unlink()
            self._sleep(self.poll)
            return
        os.close(fd)
        try:
            current = self._read()
            if isinstance(stale, LockInfo):
                same = isinstance(current, LockInfo) and current.token == stale.token
            else:
                same = isinstance(current, _Unreadable) and self._is_stale(current)
            if same:
                self._unlink()
        finally:
            with contextlib.suppress(OSError):
                guard.unlink()

    def _unlink(self) -> None:
        # On Windows a waiting run may be reading the file at this moment, which blocks
        # deleting it for a few milliseconds.
        with contextlib.suppress(FileNotFoundError):
            retry_locked(self.path.unlink, self.path)

    def _locked_error(self, holder: _Holder) -> LockedError:
        if isinstance(holder, LockInfo):
            who = holder.describe()
            other_host = holder.host != self._host
        else:
            who = "another run"
            other_host = False
        hint = f"Wait for it to finish. If no other book-loader is running, delete {self.path}."
        if other_host:
            hint = (
                f"It runs on another computer, so book-loader can't check whether it is "
                f"still running. If it isn't, delete {self.path}."
            )
        return LockedError(
            self.path, f"Another book-loader run is using this folder: {who}.", hint=hint
        )
