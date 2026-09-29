"""T2.10: ``infra/logging.py``: vendored print capture, redaction and run log files."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from book_loader.infra import logging as runlog
from book_loader.infra.logging import (
    LATEST,
    RedactingFilter,
    RunLog,
    capture_vendored_print,
    clean_old_logs,
    forget_secrets,
    redact_log_text,
    register_secret,
    rotated_name,
    setup_run_logging,
)
from book_loader.infra.secrets import Secret

TZ = timezone(timedelta(hours=2))
T1 = datetime(2026, 9, 29, 12, 0, 5, tzinfo=TZ)
T2 = datetime(2026, 9, 30, 8, 30, 0, tzinfo=TZ)

URL = "https://acs.example.com/fulfillment/Fulfill?transaction=abcdef1234567890abcdef12&x=1"
UUID = "urn:uuid:0a1b2c3d-1111-2222-3333-444455556666"
EMAIL = "reader@example.com"


@pytest.fixture(autouse=True)
def no_secrets() -> Iterator[None]:
    forget_secrets()
    yield
    forget_secrets()


@pytest.fixture
def logger_name(request) -> str:
    return f"test_logging.{request.node.name}"


@pytest.fixture
def logs(tmp_path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def opened(logs, logger_name) -> Iterator[list[RunLog]]:
    """Runs to close at the end of the test."""
    runs: list[RunLog] = []
    yield runs
    for run in runs:
        run.close()


def start(logs, logger_name, opened, when=T1, **kwargs) -> RunLog:
    run = setup_run_logging(logs, now=lambda: when, logger_name=logger_name, **kwargs)
    opened.append(run)
    return run


def record(message: str, *args, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("x", logging.DEBUG, __file__, 1, message, args, exc_info)


# --- Vendored print() ------------------------------------------------------------------


class TestCaptureVendoredPrint:
    def test_lines_become_debug_records(self, caplog, capsys, logger_name):
        logger = logging.getLogger(logger_name)
        with caplog.at_level(logging.DEBUG, logger=logger_name):
            with capture_vendored_print(logger):
                print("Fulfilling book...")
                print("HTTP 200", "OK")
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.DEBUG, "Fulfilling book..."),
            (logging.DEBUG, "HTTP 200 OK"),
        ]
        assert capsys.readouterr().out == ""

    def test_partial_lines_and_blank_lines(self, caplog, logger_name):
        with caplog.at_level(logging.DEBUG, logger=logger_name):
            with capture_vendored_print(logging.getLogger(logger_name)):
                print("Downloading", end="")
                print("... done")
                print()
                print("windows line\r")
                print("no newline at the end", end="")
        assert [r.getMessage() for r in caplog.records] == [
            "Downloading... done",
            "windows line",
            "no newline at the end",
        ]

    def test_stdout_is_restored_after_an_exception(self, caplog, logger_name):
        real = sys.stdout
        with caplog.at_level(logging.DEBUG, logger=logger_name):
            with pytest.raises(RuntimeError):
                with capture_vendored_print(logging.getLogger(logger_name)):
                    print("before the error", end="")
                    raise RuntimeError
        assert sys.stdout is real
        assert [r.getMessage() for r in caplog.records] == ["before the error"]

    def test_default_logger(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="book_loader.vendor"):
            with capture_vendored_print():
                print("from vendored code")
        assert caplog.records[0].name == "book_loader.vendor"

    def test_a_percent_sign_is_not_a_format(self, caplog, logger_name):
        with caplog.at_level(logging.DEBUG, logger=logger_name):
            with capture_vendored_print(logging.getLogger(logger_name)):
                print("100% done %s")
        assert caplog.records[0].getMessage() == "100% done %s"

    def test_the_stream_is_writable(self, logger_name):
        with capture_vendored_print(logging.getLogger(logger_name)):
            assert sys.stdout.writable()


# --- Redaction -------------------------------------------------------------------------


class TestRedaction:
    def test_message_and_arguments(self):
        item = record("GET %s for %s (%s)", URL, EMAIL, UUID)
        RedactingFilter().filter(item)
        message = item.getMessage()
        assert item.args is None
        assert "acs.example.com" in message  # the host stays, for diagnosing
        assert "abcdef1234567890abcdef12" not in message
        assert EMAIL not in message and "<email>" in message
        assert "0a1b2c3d" not in message and "<uuid>" in message

    def test_registered_secrets_are_blanked(self):
        register_secret(Secret("hunter2!"))
        register_secret("correct horse")
        register_secret("")  # ignored
        text = redact_log_text("tried hunter2! then correct horse battery")
        assert text == "tried ******** then ******** battery"

    def test_the_longest_secret_is_blanked_first(self):
        register_secret("abc")
        register_secret("abcdef")
        assert redact_log_text("abcdef abc") == "******** ********"

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("password=hunter2", "password=********"),
            ("Password: hunter2 next", "Password: ******** next"),
            ('passphrase = "two words"', "passphrase = ********"),
            ("login?user=x&password=hunter2&y=1", "login?user=x&password=********&y=1"),
            (
                "<adept:password>hunter2</adept:password>",
                "<adept:password>********</adept:password>",
            ),
        ],
    )
    def test_password_fields(self, text, expected):
        assert redact_log_text(text) == expected

    def test_exceptions_are_redacted(self):
        try:
            raise ValueError(f"download failed: {URL} for {EMAIL}")
        except ValueError:
            item = record("failed", exc_info=sys.exc_info())
        RedactingFilter().filter(item)
        assert item.exc_text is not None
        assert "ValueError" in item.exc_text
        assert EMAIL not in item.exc_text
        assert "abcdef1234567890abcdef12" not in item.exc_text

    def test_stack_info_is_redacted(self):
        item = record("x")
        item.stack_info = f"Stack for {EMAIL}"
        RedactingFilter().filter(item)
        assert item.stack_info == "Stack for <email>"

    def test_a_broken_format_keeps_the_record(self):
        item = record("%d items", "not a number")
        assert RedactingFilter().filter(item) is True
        assert "items" in item.getMessage()


# --- Cleanup ---------------------------------------------------------------------------


def make_file(folder: Path, name: str, days_old: float, now: datetime) -> Path:
    path = folder / name
    path.write_text(name)
    stamp = (now - timedelta(days=days_old)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


class TestCleanOldLogs:
    def test_by_modification_time(self, logs):
        logs.mkdir()
        now = datetime.now().astimezone()
        old_log = make_file(logs, "book-loader-20250101-000000.log", 31, now)
        old_page = make_file(logs, "download_error_429.html", 40, now)
        new_log = make_file(logs, "book-loader-20260920-000000.log", 29, now)
        new_page = make_file(logs, "download_error.html", 1, now)
        other = make_file(logs, "notes.txt", 400, now)
        deleted = clean_old_logs(logs, 30, now)
        assert sorted(deleted) == sorted([old_log, old_page])
        assert new_log.exists() and new_page.exists() and other.exists()

    def test_zero_keeps_everything(self, logs):
        logs.mkdir()
        now = datetime.now().astimezone()
        old = make_file(logs, "book-loader-20200101-000000.log", 3000, now)
        assert clean_old_logs(logs, 0, now) == []
        assert old.exists()

    def test_missing_folder(self, logs):
        assert clean_old_logs(logs, 30, datetime.now().astimezone()) == []

    def test_negative(self, logs):
        with pytest.raises(ValueError):
            clean_old_logs(logs, -1, datetime.now().astimezone())

    def test_a_file_that_cannot_be_deleted_is_skipped(self, logs, monkeypatch):
        logs.mkdir()
        now = datetime.now().astimezone()
        busy = make_file(logs, "busy.log", 60, now)
        old = make_file(logs, "old.log", 60, now)
        real_unlink = Path.unlink

        def unlink(self, missing_ok=False):
            if self == busy:
                raise PermissionError(13, "in use")
            real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", unlink)
        assert clean_old_logs(logs, 30, now) == [old]
        assert busy.exists()


# --- Run logs --------------------------------------------------------------------------


class TestRunLog:
    def test_disabled_writes_nothing(self, logs, logger_name):
        run = setup_run_logging(logs, enabled=False, logger_name=logger_name)
        assert run.path is None and run.handlers == []
        run.close()
        assert not logs.exists()

    def test_first_run(self, logs, logger_name, opened):
        run = start(logs, logger_name, opened, pid=1234)
        assert run.path == logs / LATEST
        assert run.rotated is None
        logging.getLogger(f"{logger_name}.step").debug("full detail %d", 42)
        run.close()
        lines = (logs / LATEST).read_text(encoding="utf-8").splitlines()
        assert lines[0] == f"# book-loader run log, started {T1.isoformat()}, process 1234"
        assert lines[1].endswith(f"DEBUG   {logger_name}.step: full detail 42")

    def test_close_detaches_and_releases(self, logs, logger_name, opened):
        run = start(logs, logger_name, opened)
        handlers = list(run.handlers)
        run.close()
        logger = logging.getLogger(logger_name)
        assert not set(handlers) & set(logger.handlers)
        assert not (logs / f"{LATEST}.lock").exists()
        run.close()  # twice is fine

    def test_context_manager(self, logs, logger_name):
        with setup_run_logging(logs, now=lambda: T1, logger_name=logger_name) as run:
            logging.getLogger(logger_name).info("inside")
        assert run.handlers == []
        assert "inside" in (logs / LATEST).read_text(encoding="utf-8")

    def test_rotation_uses_the_previous_start_time(self, logs, logger_name, opened):
        first = start(logs, logger_name, opened, when=T1)
        logging.getLogger(logger_name).info("first run")
        first.close()
        second = start(logs, logger_name, opened, when=T2)
        logging.getLogger(logger_name).info("second run")
        second.close()

        rotated = logs / f"book-loader-{T1.astimezone():%Y%m%d-%H%M%S}.log"
        assert second.rotated == rotated
        assert rotated_name(T1) == rotated.name
        assert "first run" in rotated.read_text(encoding="utf-8")
        latest = (logs / LATEST).read_text(encoding="utf-8")
        assert "second run" in latest and "first run" not in latest
        assert T2.isoformat() in latest.splitlines()[0]

    def test_rotation_without_a_header_uses_the_modification_time(self, logs, logger_name, opened):
        logs.mkdir()
        old = logs / LATEST
        old.write_text("written by something else\n")
        stamp = datetime(2026, 1, 2, 3, 4, 5).astimezone()
        os.utime(old, (stamp.timestamp(), stamp.timestamp()))
        run = start(logs, logger_name, opened, when=T2, keep_days=0)
        assert run.rotated == logs / "book-loader-20260102-030405.log"

    def test_a_damaged_header_uses_the_modification_time(self, logs, logger_name, opened):
        logs.mkdir()
        old = logs / LATEST
        old.write_text("# book-loader run log, started not-a-date, process 1\n")
        stamp = datetime(2026, 1, 2, 3, 4, 5).astimezone()
        os.utime(old, (stamp.timestamp(), stamp.timestamp()))
        run = start(logs, logger_name, opened, when=T2, keep_days=0)
        assert run.rotated == logs / "book-loader-20260102-030405.log"

    def test_same_second_gets_a_number(self, logs, logger_name, opened):
        start(logs, logger_name, opened, when=T1).close()
        start(logs, logger_name, opened, when=T1).close()
        third = start(logs, logger_name, opened, when=T1)
        assert third.rotated is not None and third.rotated.name.endswith(" (2).log")

    def test_a_second_run_writes_its_own_file(self, logs, logger_name, opened):
        first = start(logs, logger_name, opened, when=T1)
        second = start(logs, logger_name, opened, when=T2, pid=4321)
        assert second.path == logs / "latest-book-loader-4321.log"
        assert second.rotated is None
        assert first.path == logs / LATEST
        second.close()
        # The first run's file was neither moved nor touched.
        assert (
            (logs / LATEST)
            .read_text(encoding="utf-8")
            .startswith(f"# book-loader run log, started {T1.isoformat()}")
        )

    def test_latest_that_cannot_be_renamed(self, logs, logger_name, opened, monkeypatch):
        start(logs, logger_name, opened, when=T1).close()

        def refuse(src, dst):
            raise PermissionError(13, "in use")

        monkeypatch.setattr(runlog.os, "replace", refuse)
        run = start(logs, logger_name, opened, when=T2, pid=777)
        assert run.path == logs / "latest-book-loader-777.log"
        assert run.rotated is None

    def test_cleanup_runs_at_start(self, logs, logger_name, opened):
        logs.mkdir()
        now = datetime.now().astimezone()
        old = make_file(logs, "book-loader-20200101-000000.log", 90, now)
        run = start(logs, logger_name, opened, when=now, keep_days=30)
        assert run.deleted == [old] and not old.exists()

    def test_extra_copy(self, logs, logger_name, opened, tmp_path):
        extra = tmp_path / "bug-report.log"
        run = start(logs, logger_name, opened, extra_file=extra)
        logging.getLogger(logger_name).warning("for %s", EMAIL)
        run.close()
        assert run.extra_path == extra
        text = extra.read_text(encoding="utf-8")
        assert "for <email>" in text and EMAIL not in text

    def test_lines_are_written_redacted(self, logs, logger_name, opened):
        run = start(logs, logger_name, opened)
        register_secret(Secret("hunter2!"))
        log = logging.getLogger(f"{logger_name}.adobe")
        log.debug("Signing in as %s with password hunter2!", EMAIL)
        log.debug("GET %s", URL)
        log.debug("Device %s", UUID)
        log.debug("form: password=s3cret&email=%s", EMAIL)
        run.close()
        text = (logs / LATEST).read_text(encoding="utf-8")
        for leaked in ("hunter2!", "s3cret", EMAIL, "abcdef1234567890abcdef12", "0a1b2c3d"):
            assert leaked not in text
        assert "acs.example.com" in text

    def test_vendored_print_reaches_the_log(self, logs, logger_name, opened):
        run = start(logs, logger_name, opened)
        with capture_vendored_print(logging.getLogger(f"{logger_name}.vendor")):
            print(f"Downloading {URL}")
        run.close()
        text = (logs / LATEST).read_text(encoding="utf-8")
        assert "vendor: Downloading https://acs.example.com/" in text
        assert "abcdef1234567890abcdef12" not in text

    def test_debug_is_recorded_whatever_the_console_shows(self, logs, logger_name, opened):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
        run = start(logs, logger_name, opened)
        logging.getLogger(logger_name).debug("protocol step")
        run.close()
        assert "protocol step" in (logs / LATEST).read_text(encoding="utf-8")

    def test_a_failing_extra_file_closes_the_run(self, logs, logger_name, tmp_path):
        with pytest.raises(OSError):
            setup_run_logging(
                logs,
                now=lambda: T1,
                logger_name=logger_name,
                extra_file=tmp_path / "missing" / "x.log",
            )
        assert not (logs / f"{LATEST}.lock").exists()
