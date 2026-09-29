"""T2.8: ``infra/locks.py``, lock files for auth folders and libraries.

The real-subprocess tests run on every OS; their first POSIX run is deferred (D8).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from book_loader.domain.errors import LockedError
from book_loader.infra import locks
from book_loader.infra.locks import Lock, LockInfo, process_alive

HOST = "this-host"


@pytest.fixture
def path(tmp_path) -> Path:
    return tmp_path / "auth.lock"


def lock(path: Path, timeout: float = 0.3, **kwargs) -> Lock:
    kwargs.setdefault("host", HOST)
    kwargs.setdefault("poll", 0.02)
    return Lock(path, timeout, **kwargs)


def write_holder(path: Path, pid: int, host: str = HOST, token: str = "t0ken") -> LockInfo:
    info = LockInfo(pid, host, datetime(2026, 9, 29, 12, 0, tzinfo=UTC), token, "fulfilling")
    path.write_text(info.to_json(), encoding="utf-8")
    return info


def age(path: Path, seconds: float) -> None:
    past = time.time() - seconds
    os.utime(path, (past, past))


def dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class TestAcquireAndRelease:
    def test_file_records_the_holder(self, path):
        with lock(path, purpose="fulfilling book.acsm") as held:
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["pid"] == os.getpid()
            assert data["host"] == HOST
            assert data["purpose"] == "fulfilling book.acsm"
            assert datetime.fromisoformat(data["started"]).utcoffset() is not None
            assert held.info is not None and data["token"] == held.info.token
            assert held.held
        assert not path.exists()
        assert not held.held

    def test_released_on_an_exception(self, path):
        with pytest.raises(RuntimeError):
            with lock(path):
                raise RuntimeError("step failed")
        assert not path.exists()

    def test_can_be_taken_again_after_release(self, path):
        first = lock(path)
        first.acquire()
        first.release()
        first.acquire()
        first.release()
        assert not path.exists()

    def test_acquiring_twice_is_a_mistake(self, path):
        with lock(path) as held:
            with pytest.raises(RuntimeError, match="already held"):
                held.acquire()

    def test_release_without_acquire_does_nothing(self, path):
        lock(path).release()

    def test_release_leaves_someone_elses_lock(self, path):
        held = lock(path)
        held.acquire()
        write_holder(path, pid=1234, token="another")  # replaced behind our back
        held.release()
        assert json.loads(path.read_text())["token"] == "another"

    def test_the_folder_must_exist(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            lock(tmp_path / "missing" / "auth.lock").acquire()


class TestWaiting:
    def test_second_lock_waits_then_fails(self, path):
        with lock(path, purpose="fulfilling book.acsm"):
            start = time.monotonic()
            with pytest.raises(LockedError) as info:
                lock(path, timeout=0.3).acquire()
            assert time.monotonic() - start >= 0.3
        error = info.value
        assert error.path == path
        assert f"process {os.getpid()} on {HOST} (fulfilling book.acsm)" in error.message
        assert str(path) in (error.hint or "") and "Wait for it to finish" in (error.hint or "")

    def test_second_lock_gets_it_when_the_first_is_released(self, path):
        first = lock(path)
        first.acquire()
        timer = threading.Timer(0.2, first.release)
        timer.start()
        try:
            with lock(path, timeout=5) as second:
                assert second.held
        finally:
            timer.join()

    def test_zero_timeout_fails_at_once(self, path):
        with lock(path):
            with pytest.raises(LockedError):
                lock(path, timeout=0).acquire()


class TestStaleLocks:
    def test_a_dead_process_is_taken_over(self, path):
        write_holder(path, pid=4242)
        with lock(path, is_alive=lambda pid: False) as held:
            assert held.info is not None
            assert json.loads(path.read_text())["token"] == held.info.token

    def test_a_live_process_is_not(self, path):
        write_holder(path, pid=4242)
        with pytest.raises(LockedError, match="process 4242"):
            lock(path, is_alive=lambda pid: True).acquire()

    def test_real_dead_process_id(self, path):
        write_holder(path, pid=dead_pid())
        with lock(path, timeout=2) as held:
            assert held.held

    def test_another_host_is_never_stale(self, path):
        write_holder(path, pid=4242, host="other-pc")
        with pytest.raises(LockedError) as info:
            lock(path, is_alive=lambda pid: False).acquire()
        assert "on other-pc" in info.value.message
        assert "another computer" in (info.value.hint or "")

    def test_a_fresh_unreadable_lock_is_being_written(self, path):
        path.write_text("{", encoding="utf-8")
        with pytest.raises(LockedError, match="another run"):
            lock(path).acquire()

    def test_an_old_unreadable_lock_is_stale(self, path):
        path.write_bytes(b"\x00\x00")
        age(path, locks.UNREADABLE_GRACE + 5)
        with lock(path) as held:
            assert held.held

    def test_break_keeps_a_lock_replaced_meanwhile(self, path):
        stale = write_holder(path, pid=4242, token="stale")
        write_holder(path, pid=5151, token="fresh")
        lock(path)._break(stale)
        assert json.loads(path.read_text())["token"] == "fresh"

    def test_only_one_run_breaks_at_a_time(self, path):
        stale = write_holder(path, pid=4242)
        guard = path.with_name(path.name + ".break")
        guard.write_bytes(b"")
        lock(path)._break(stale)
        assert path.exists()  # the other breaker's job
        assert guard.exists()

    def test_a_left_over_break_file_is_cleared(self, path):
        write_holder(path, pid=4242)
        guard = path.with_name(path.name + ".break")
        guard.write_bytes(b"")
        age(guard, locks.BREAK_GRACE + 5)
        with lock(path, timeout=2, is_alive=lambda pid: False) as held:
            assert held.held
        assert not guard.exists()


class TestProcessAlive:
    def test_this_process(self):
        assert process_alive(os.getpid())

    def test_a_finished_process(self):
        assert not process_alive(dead_pid())

    @pytest.mark.parametrize("pid", [0, -1])
    def test_invalid_ids(self, pid):
        assert not process_alive(pid)

    @pytest.mark.windows_only
    def test_a_process_of_another_user(self):
        assert process_alive(4)  # the System process

    @pytest.mark.parametrize(
        "outcome, alive",
        [(None, True), (ProcessLookupError(), False), (PermissionError(), True)],
    )
    def test_posix_signal_zero(self, monkeypatch, outcome, alive):
        def kill(pid, signal):
            assert signal == 0
            if outcome is not None:
                raise outcome

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(locks.os, "kill", kill)
        assert process_alive(1234) is alive


HOLDER = textwrap.dedent("""
    import os
    import sys
    from pathlib import Path
    from book_loader.infra.locks import Lock

    with Lock(Path(sys.argv[1]), purpose="child"):
        print("held", os.getpid(), flush=True)
        sys.stdin.readline()
    """)


class TestRealProcesses:
    def start_holder(self, path: Path) -> tuple[subprocess.Popen[str], int]:
        """Start a process that holds the lock; returns it and the interpreter's own ID.

        On Windows a venv's python.exe is a launcher that runs the real interpreter as
        a child process, so Popen.pid isn't the process holding the lock.
        """
        child = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert child.stdout is not None
        word, pid = child.stdout.readline().split()
        assert word == "held"
        return child, int(pid)

    def test_a_subprocess_holds_the_lock(self, path):
        child, pid = self.start_holder(path)
        try:
            with pytest.raises(LockedError) as info:
                Lock(path, 0.3, poll=0.02).acquire()
            assert f"process {pid} " in info.value.message
            assert "(child)" in info.value.message
        finally:
            assert child.stdin is not None
            child.stdin.write("done\n")
            child.stdin.close()
            child.wait(10)
        assert not path.exists()
        with Lock(path, 1):
            pass

    def test_a_killed_subprocess_leaves_a_stale_lock(self, path):
        child, pid = self.start_holder(path)
        os.kill(pid, signal.SIGTERM)  # TerminateProcess on Windows; no cleanup runs
        child.wait(10)
        deadline = time.monotonic() + 10
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not process_alive(pid)
        assert path.exists()
        with Lock(path, 2, poll=0.02) as held:
            assert held.held


class TestEdgeCases:
    def test_a_lock_that_vanished_is_not_stale(self, path):
        held = lock(path)
        assert held._read() is None
        assert not held._is_stale(None)

    def test_a_file_being_deleted_on_windows_is_retried(self, path, monkeypatch):
        path.write_text("{}")

        def refuse(*args, **kwargs):
            raise PermissionError(13, "Access is denied")

        monkeypatch.setattr(locks.os, "open", refuse)
        info = LockInfo(1, HOST, datetime.now(UTC), "t")
        monkeypatch.setattr(sys, "platform", "win32")
        assert lock(path)._create(info) is False
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(PermissionError):
            lock(path)._create(info)

    def test_a_real_permission_error_is_raised(self, path, monkeypatch):
        def refuse(*args, **kwargs):
            raise PermissionError(13, "Access is denied")

        monkeypatch.setattr(locks.os, "open", refuse)
        monkeypatch.setattr(sys, "platform", "win32")
        with pytest.raises(PermissionError):
            lock(path).acquire()
