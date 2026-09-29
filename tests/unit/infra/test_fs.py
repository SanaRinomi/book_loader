"""T2.7: ``infra/fs.py``: atomic writes, unique names, private folders, locked files,
the workspace and safe moves.

``posix_only`` tests (file modes) are written but skip on Windows; their first run is
deferred (D4, D8).
"""

from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from book_loader.domain import events
from book_loader.domain.errors import BookLoaderError, LockedError
from book_loader.infra import fs
from book_loader.infra.fs import (
    WORKSPACE_PREFIX,
    Workspace,
    atomic_write,
    atomic_write_bytes,
    atomic_write_text,
    is_locked_error,
    private_dir,
    retry_locked,
    safe_move,
    unique_path,
    windows_broad_access,
)
from tests.fakes.reporter import RecordingReporter


class Locked(PermissionError):
    """What Windows raises for a sharing violation."""

    winerror = 32


class OtherDrive(OSError):
    """What Windows raises for os.replace across drives."""

    winerror = 17


def leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp"))


def hold_open(path: Path, seconds: float) -> threading.Thread:
    """Keep ``path`` open in another thread, as another program would."""
    opened = threading.Event()

    def run() -> None:
        with open(path, "rb"):
            opened.set()
            time.sleep(seconds)

    thread = threading.Thread(target=run)
    thread.start()
    opened.wait()
    return thread


@pytest.fixture
def folder(tmp_path) -> Path:
    """An empty folder of the test's own (tmp_path also holds tmp_home's "home")."""
    path = tmp_path / "work"
    path.mkdir()
    return path


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


# --- retry_locked ----------------------------------------------------------------------


class TestRetryLocked:
    def run(self, operation, platform="win32", timeout=2.0):
        clock = FakeClock()
        result = retry_locked(
            operation,
            Path("book.epub"),
            timeout=timeout,
            platform=platform,
            sleep=clock.sleep,
            clock=clock.clock,
        )
        return result, clock

    def failing(self, times: int, error: type[OSError] = Locked):
        calls = []

        def operation():
            calls.append(1)
            if len(calls) <= times:
                raise error(13, "in use")
            return "done"

        return operation, calls

    def test_success_first_time(self):
        result, clock = self.run(lambda: 42)
        assert result == 42 and clock.sleeps == []

    def test_retries_while_locked(self):
        operation, calls = self.failing(3)
        result, clock = self.run(operation)
        assert result == "done"
        assert len(calls) == 4
        assert clock.sleeps == [0.05, 0.1, 0.2]

    def test_backoff_is_capped_and_ends_at_the_deadline(self):
        operation, calls = self.failing(1000)
        clock = FakeClock()
        with pytest.raises(LockedError) as info:
            retry_locked(
                operation, Path("x"), platform="win32", sleep=clock.sleep, clock=clock.clock
            )
        assert clock.sleeps[:5] == [0.05, 0.1, 0.2, 0.4, 0.4]
        assert clock.now == pytest.approx(2.0)
        assert isinstance(info.value.__cause__, Locked)

    def test_locked_error_names_the_file(self):
        operation, _ = self.failing(1000)
        with pytest.raises(LockedError) as info:
            self.run(operation)
        assert info.value.path == Path("book.epub")
        assert "book.epub" in info.value.message
        assert info.value.hint

    def test_other_errors_are_not_retried(self):
        operation, calls = self.failing(1, error=FileNotFoundError)
        with pytest.raises(FileNotFoundError):
            self.run(operation)
        assert len(calls) == 1

    def test_no_retry_off_windows(self):
        operation, calls = self.failing(1)
        with pytest.raises(Locked):
            self.run(operation, platform="linux")
        assert len(calls) == 1

    @pytest.mark.parametrize("code, locked", [(5, True), (32, True), (33, True), (2, False)])
    def test_which_windows_errors_count(self, code, locked):
        error = type("E", (OSError,), {"winerror": code})(13, "x")
        assert is_locked_error(error, "win32") is locked
        assert is_locked_error(error, "darwin") is False
        assert is_locked_error(ValueError(), "win32") is False

    @pytest.mark.windows_only
    def test_real_sharing_violation_clears(self, tmp_path):
        path = tmp_path / "book.epub"
        path.write_bytes(b"x")
        thread = hold_open(path, 0.3)
        retry_locked(path.unlink, path)
        thread.join()
        assert not path.exists()

    @pytest.mark.windows_only
    def test_real_sharing_violation_times_out(self, tmp_path):
        path = tmp_path / "book.epub"
        path.write_bytes(b"x")
        thread = hold_open(path, 0.6)
        try:
            with pytest.raises(LockedError) as info:
                retry_locked(path.unlink, path, timeout=0.3)
            assert info.value.path == path
        finally:
            thread.join()


# --- atomic_write ----------------------------------------------------------------------


class TestAtomicWrite:
    def test_writes_bytes(self, tmp_path):
        path = tmp_path / "a.bin"
        atomic_write_bytes(path, b"new")
        assert path.read_bytes() == b"new"
        assert leftovers(tmp_path) == []

    def test_writes_utf8_text(self, tmp_path):
        path = tmp_path / "a.json"
        atomic_write_text(path, "Café 漢\n")
        assert path.read_bytes() == "Café 漢\n".encode()

    def test_replaces_an_existing_file(self, tmp_path):
        path = tmp_path / "a.txt"
        path.write_text("old")
        with atomic_write(path, "w") as handle:
            handle.write("new")
        assert path.read_text() == "new"

    def test_an_error_in_the_block_keeps_the_old_file(self, tmp_path):
        path = tmp_path / "a.txt"
        path.write_text("old")
        with pytest.raises(RuntimeError):
            with atomic_write(path, "w") as handle:
                handle.write("half")
                raise RuntimeError("interrupted")
        assert path.read_text() == "old"
        assert leftovers(tmp_path) == []

    def test_a_failed_replace_keeps_the_old_file(self, tmp_path, monkeypatch):
        path = tmp_path / "a.txt"
        path.write_text("old")

        def fail(src, dst):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(fs.os, "replace", fail)
        with pytest.raises(OSError, match="No space"):
            atomic_write_text(path, "new")
        monkeypatch.undo()
        assert path.read_text() == "old"
        assert leftovers(tmp_path) == []

    def test_no_file_is_created_when_the_first_write_fails(self, folder):
        path = folder / "new.txt"
        with pytest.raises(RuntimeError):
            with atomic_write(path, "wb"):
                raise RuntimeError
        assert list(folder.iterdir()) == []

    def test_only_write_modes(self, tmp_path):
        with pytest.raises(ValueError, match="mode"):
            with atomic_write(tmp_path / "a", "ab"):  # type: ignore[call-overload]
                pass

    @pytest.mark.windows_only
    def test_waits_for_a_reader_to_close_the_target(self, tmp_path):
        path = tmp_path / "a.txt"
        path.write_text("old")
        thread = hold_open(path, 0.3)
        atomic_write_text(path, "new")
        thread.join()
        assert path.read_text() == "new"

    @pytest.mark.posix_only
    def test_file_mode(self, tmp_path):
        path = tmp_path / "key.json"
        atomic_write_text(path, "{}", file_mode=0o600)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    @pytest.mark.posix_only
    def test_existing_mode_is_kept(self, tmp_path):
        path = tmp_path / "key.json"
        path.write_text("{}")
        os.chmod(path, 0o640)
        atomic_write_text(path, "[]")
        assert stat.S_IMODE(path.stat().st_mode) == 0o640


# --- unique_path -----------------------------------------------------------------------


class TestUniquePath:
    def taken(self, *names: str):
        paths = {Path("out") / name for name in names}
        return paths.__contains__

    def test_free_path_is_kept(self):
        assert unique_path(Path("out/Book.epub"), self.taken()) == Path("out/Book.epub")

    def test_sequence(self):
        taken = self.taken("Book.epub", "Book (2).epub", "Book (3).epub")
        assert unique_path(Path("out/Book.epub"), taken) == Path("out/Book (4).epub")

    def test_gap_is_filled(self):
        taken = self.taken("Book.epub", "Book (3).epub")
        assert unique_path(Path("out/Book.epub"), taken) == Path("out/Book (2).epub")

    def test_number_goes_before_the_last_extension(self):
        taken = self.taken("Book.encrypted.epub")
        assert unique_path(Path("out/Book.encrypted.epub"), taken) == Path(
            "out/Book.encrypted (2).epub"
        )

    def test_folder_without_extension(self):
        assert unique_path(Path("out/Author"), self.taken("Author")) == Path("out/Author (2)")

    def test_long_names_stay_within_255_bytes(self):
        name = "漢" * 83 + ".epub"  # 249 + 5 bytes
        result = unique_path(Path("out") / name, self.taken(name))
        assert result.name.endswith(" (2).epub")
        assert len(result.name.encode("utf-8")) <= 255

    def test_real_files(self, tmp_path):
        (tmp_path / "Book.epub").write_bytes(b"")
        assert unique_path(tmp_path / "Book.epub") == tmp_path / "Book (2).epub"


# --- private_dir -----------------------------------------------------------------------


class TestPrivateDir:
    def test_creates_parents(self, tmp_path):
        path = tmp_path / "a" / "b" / "auth"
        assert private_dir(path) == []
        assert path.is_dir()

    def test_existing_folder(self, tmp_path):
        assert private_dir(tmp_path) == []

    @pytest.mark.windows_only
    def test_warns_when_everyone_can_read(self, tmp_path):
        path = tmp_path / "auth"
        path.mkdir()
        # By SID, so the test works whatever language Windows uses.
        subprocess.run(
            ["icacls", str(path), "/grant", "*S-1-1-0:(OI)(CI)(R)"],
            check=True,
            capture_output=True,
        )
        assert windows_broad_access(path) == ["Everyone"]
        warnings = private_dir(path)
        assert len(warnings) == 1
        assert "Everyone" in warnings[0] and str(path) in warnings[0]

    @pytest.mark.windows_only
    def test_deny_rules_are_not_access(self, tmp_path):
        path = tmp_path / "auth"
        path.mkdir()
        subprocess.run(
            ["icacls", str(path), "/deny", "*S-1-1-0:(W)"], check=True, capture_output=True
        )
        try:
            assert windows_broad_access(path) == []
        finally:
            # The deny rule applies to this user too; without removing it, pytest
            # can't delete the folder afterwards.
            subprocess.run(
                ["icacls", str(path), "/remove:d", "*S-1-1-0"], check=True, capture_output=True
            )

    @pytest.mark.windows_only
    def test_profile_folders_are_private(self, tmp_path):
        assert windows_broad_access(tmp_path) == []

    def test_unreadable_or_missing_folder(self, tmp_path):
        assert windows_broad_access(tmp_path / "missing") == []

    def test_no_check_off_windows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert windows_broad_access(tmp_path) == []

    @pytest.mark.posix_only
    def test_mode_0700_even_if_it_existed(self, tmp_path):
        path = tmp_path / "auth"
        path.mkdir(mode=0o755)
        os.chmod(path, 0o755)
        private_dir(path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


# --- safe_move -------------------------------------------------------------------------


class TestSafeMove:
    def test_same_drive(self, tmp_path):
        src, dst = tmp_path / "a.epub", tmp_path / "out" / "b.epub"
        dst.parent.mkdir()
        src.write_bytes(b"book")
        assert safe_move(src, dst) == dst
        assert dst.read_bytes() == b"book" and not src.exists()

    def test_replaces_the_destination(self, tmp_path):
        src, dst = tmp_path / "a", tmp_path / "b"
        src.write_bytes(b"new")
        dst.write_bytes(b"old")
        safe_move(src, dst)
        assert dst.read_bytes() == b"new"

    @pytest.fixture
    def other_drive(self, monkeypatch):
        """Make the first os.replace of each source fail as if it crossed drives."""
        real = os.replace
        sources = set()

        def replace(src, dst):
            if Path(src).suffix != ".tmp":
                sources.add(Path(src))
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            real(src, dst)

        monkeypatch.setattr(fs.os, "replace", replace)
        return sources

    def test_across_drives_copies_checks_and_deletes(self, tmp_path, other_drive):
        src, dst = tmp_path / "a.epub", tmp_path / "out" / "b.epub"
        dst.parent.mkdir()
        data = os.urandom(3 * 1024 * 1024 + 7)
        src.write_bytes(data)
        os.utime(src, (1_700_000_000, 1_700_000_000))
        safe_move(src, dst)
        assert other_drive == {src}
        assert dst.read_bytes() == data
        assert dst.stat().st_mtime == 1_700_000_000
        assert not src.exists()
        assert leftovers(dst.parent) == []

    def test_windows_cross_drive_error_is_recognised(self, tmp_path, monkeypatch):
        real = os.replace

        def replace(src, dst):
            if Path(src).suffix != ".tmp":
                raise OtherDrive(18, "The system cannot move the file to a different disk drive")
            real(src, dst)

        monkeypatch.setattr(fs.os, "replace", replace)
        src, dst = tmp_path / "a", tmp_path / "b"
        src.write_bytes(b"x")
        safe_move(src, dst)
        assert dst.read_bytes() == b"x" and not src.exists()

    def test_a_bad_copy_keeps_the_source(self, tmp_path, other_drive, monkeypatch):
        src, dst = tmp_path / "a.epub", tmp_path / "b.epub"
        src.write_bytes(b"book")
        hashes = iter(["copy", "source"])
        monkeypatch.setattr(fs, "_sha256", lambda path: next(hashes))
        with pytest.raises(BookLoaderError, match="different file") as info:
            safe_move(src, dst)
        assert "original was kept" in (info.value.hint or "")
        assert src.read_bytes() == b"book"
        assert not dst.exists()
        assert leftovers(tmp_path) == []

    def test_other_errors_are_raised(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            safe_move(tmp_path / "missing", tmp_path / "b")


# --- Workspace -------------------------------------------------------------------------


class TestWorkspace:
    def test_hidden_folder_in_the_parent(self, tmp_path):
        with Workspace(tmp_path) as work:
            assert work.path.parent == tmp_path
            assert work.path.name.startswith(WORKSPACE_PREFIX)
            assert work.path.is_dir()

    @pytest.mark.windows_only
    def test_hidden_attribute_on_windows(self, tmp_path):
        with Workspace(tmp_path) as work:
            assert os.stat(work.path).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN

    def test_no_hidden_attribute_off_windows(self, folder, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        fs._hide(folder)  # nothing to do: the leading dot hides it

    def test_creates_the_parent(self, tmp_path):
        with Workspace(tmp_path / "new" / "out") as work:
            assert work.path.is_dir()

    def test_removed_after_success(self, folder):
        with Workspace(folder) as work:
            (work.path / "sub").mkdir()
            (work.path / "sub" / "book.epub").write_bytes(b"x")
            path = work.path
        assert not path.exists()
        assert list(folder.iterdir()) == []
        assert work.warnings == []

    def test_removed_after_an_exception(self, folder):
        work = Workspace(folder)
        with pytest.raises(RuntimeError):
            with work:
                (work.path / "book.epub").write_bytes(b"x")
                raise RuntimeError("step failed")
        assert list(folder.iterdir()) == []

    def test_read_only_files_are_removed(self, tmp_path):
        with Workspace(tmp_path) as work:
            file = work.path / "ro.epub"
            file.write_bytes(b"x")
            os.chmod(file, stat.S_IREAD)
            path = work.path
        assert not path.exists()

    def test_path_only_inside_the_block(self, tmp_path):
        work = Workspace(tmp_path)
        with pytest.raises(RuntimeError, match="with block"):
            _ = work.path
        with work:
            pass
        with pytest.raises(RuntimeError):
            _ = work.path

    def test_preserved_files_survive(self, tmp_path):
        out = tmp_path / "out"
        logs = tmp_path / "logs"
        with Workspace(out) as work:
            work.preserve("* - download link.html", lambda f: out / f.name)
            work.preserve("download_error*.html", lambda f: logs / f"run-{f.name}")
            (work.path / "Book - download link.html").write_text("link")
            (work.path / ".temp").mkdir()
            (work.path / ".temp" / "download_error_429.html").write_text("429")
            (work.path / "Book.epub").write_bytes(b"encrypted")
            path = work.path
        assert (out / "Book - download link.html").read_text() == "link"
        assert (logs / "run-download_error_429.html").read_text() == "429"
        assert not path.exists()
        assert sorted(p.name for p in out.iterdir()) == ["Book - download link.html"]
        assert work.preserved == {
            path / "Book - download link.html": out / "Book - download link.html",
            path / ".temp" / "download_error_429.html": logs / "run-download_error_429.html",
        }

    def test_preserve_runs_after_an_exception(self, tmp_path):
        out = tmp_path / "out"
        with pytest.raises(RuntimeError):
            with Workspace(out) as work:
                work.preserve("*.html", lambda f: out / f.name)
                (work.path / "link.html").write_text("link")
                raise RuntimeError("download blocked")
        assert (out / "link.html").read_text() == "link"

    def test_destination_none_drops_the_file(self, folder):
        with Workspace(folder) as work:
            work.preserve("*.html", lambda f: None)
            (work.path / "page.html").write_text("x")
        assert list(folder.iterdir()) == []
        assert work.preserved == {}

    def test_folders_are_not_preserved(self, folder):
        out = folder / "out"
        with Workspace(folder) as work:
            work.preserve("*.html", lambda f: out / f.name)
            (work.path / "pages.html").mkdir()
            (work.path / "pages.html" / "inner.html").write_text("x")
        assert (out / "inner.html").exists()
        assert not (out / "pages.html").exists()

    def test_a_taken_name_gets_a_number(self, tmp_path):
        (tmp_path / "link.html").write_text("earlier")
        with Workspace(tmp_path) as work:
            work.preserve("*.html", lambda f: tmp_path / f.name)
            (work.path / "link.html").write_text("new")
        assert (tmp_path / "link.html").read_text() == "earlier"
        assert (tmp_path / "link (2).html").read_text() == "new"

    def test_a_file_matched_by_two_rules_moves_once(self, tmp_path):
        out = tmp_path / "out"
        with Workspace(out) as work:
            work.preserve("*.html", lambda f: out / "first" / f.name)
            work.preserve("page.*", lambda f: out / "second" / f.name)
            (work.path / "page.html").write_text("x")
        assert (out / "first" / "page.html").exists()
        assert not (out / "second").exists()

    def test_a_failed_preserve_is_a_warning(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, not a folder")
        reporter = RecordingReporter()
        with Workspace(tmp_path / "out", reporter=reporter) as work:
            work.preserve("*.html", lambda f: blocker / f.name)
            (work.path / "link.html").write_text("x")
            path = work.path
        assert not path.exists()
        assert len(work.warnings) == 1 and "link.html couldn't be kept" in work.warnings[0]
        assert reporter.events == [events.Warning(work.warnings[0])]

    @pytest.mark.parametrize(
        "error, text",
        [
            (BookLoaderError("Copy differs"), "Copy differs"),
            (OSError(errno.EACCES, "Access is denied"), "Access is denied"),
            (OSError("no strerror"), "no strerror"),
        ],
    )
    def test_warning_reasons(self, error, text):
        assert fs._reason(error) == text

    @pytest.mark.windows_only
    def test_a_locked_file_leaves_the_folder_with_a_warning(self, tmp_path):
        reporter = RecordingReporter()
        thread = None
        try:
            with Workspace(tmp_path, reporter=reporter, lock_timeout=0.2) as work:
                file = work.path / "book.epub"
                file.write_bytes(b"x")
                thread = hold_open(file, 0.5)
                path = work.path
            assert path.exists()
            assert len(work.warnings) == 1
            assert str(path) in work.warnings[0]
            assert reporter.events == [events.Warning(work.warnings[0])]
        finally:
            if thread is not None:
                thread.join()
