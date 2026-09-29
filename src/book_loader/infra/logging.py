"""Run logs and the vendored code's ``print`` output (REFACTOR_PLAN §9.11).

- ``capture_vendored_print`` sends what the vendored Adobe and DeDRM code prints to a
  logger at DEBUG, one record per line, instead of throwing it away.
- ``RedactingFilter`` goes on every handler, console and file alike. Each record's
  message, with its arguments filled in, goes through ``redact_text`` (which reduces
  URLs with ``redact_url``), and so do tracebacks. On top of that it blanks every
  password or passphrase registered with ``register_secret`` and anything written as
  ``password=…`` or ``<password>…</password>``, so they are never written.
- ``setup_run_logging`` gives each run a log file with full DEBUG detail, whatever the
  console shows:

  - the run writes ``latest-book-loader.log``. The one left by the previous run is
    renamed ``book-loader-<YYYYMMDD-HHMMSS>.log`` first, after the time that run
    started (read from the file's first line);
  - if another run still owns ``latest-book-loader.log``, this run writes
    ``latest-book-loader-<pid>.log`` instead;
  - ``*.log`` files and saved ``download_error*.html`` pages older than ``keep_days``
    are deleted; ``keep_days = 0`` keeps everything;
  - ``extra_file`` (``--log-file``) gets a second copy; ``enabled=False``
    (``--no-log``) writes nothing and deletes nothing.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType

from ..domain.errors import LockedError
from .redact import redact_text
from .fs import unique_path
from .locks import Lock
from .secrets import Secret

__all__ = [
    "LATEST",
    "LOGGER_NAME",
    "RedactingFilter",
    "RunLog",
    "capture_vendored_print",
    "clean_old_logs",
    "forget_secrets",
    "redact_log_text",
    "register_secret",
    "rotated_name",
    "setup_run_logging",
]

LOGGER_NAME = "book_loader"
VENDOR_LOGGER_NAME = "book_loader.vendor"
LATEST = "latest-book-loader.log"
MASK = "********"
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_HEADER = "# book-loader run log, started {started}, process {pid}"
_HEADER_RE = re.compile(r"^# book-loader run log, started (\S+), process \d+")
_PASSWORD_VALUE_RE = re.compile(
    r"((?:password|passphrase|passwd|pwd)\w*\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s&\"'<>,;]+)",
    re.IGNORECASE,
)
_PASSWORD_TAG_RE = re.compile(
    r"(<(?:[\w-]+:)?(?:password|passphrase)\b[^>]*(?<!/)>)([^<]*)(</)", re.IGNORECASE
)

_secrets: set[str] = set()
_secrets_lock = threading.Lock()


# --- Redaction -------------------------------------------------------------------------


def register_secret(secret: Secret | str) -> None:
    """Never write this value to a log, even if some code tries to."""
    value = secret.reveal() if isinstance(secret, Secret) else secret
    if value:
        with _secrets_lock:
            _secrets.add(value)


def forget_secrets() -> None:
    """Clear the registered secrets (for tests, and at the end of a run)."""
    with _secrets_lock:
        _secrets.clear()


def redact_log_text(text: str) -> str:
    """Everything the log filter does to a message."""
    with _secrets_lock:
        known = sorted(_secrets, key=len, reverse=True)
    for value in known:
        text = text.replace(value, MASK)
    text = _PASSWORD_TAG_RE.sub(lambda m: m.group(1) + MASK + m.group(3), text)
    text = _PASSWORD_VALUE_RE.sub(lambda m: m.group(1) + MASK, text)
    return redact_text(text)


class RedactingFilter(logging.Filter):
    """Redacts each record before a handler writes it (see the module docstring)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a bad format string mustn't lose the record
            message = f"{record.msg!r} {record.args!r}"
        record.msg = redact_log_text(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_log_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_log_text(record.stack_info)
        return True


# --- Vendored print() ------------------------------------------------------------------


class _LineWriter(io.TextIOBase):
    """A text stream that logs each complete line."""

    def __init__(self, logger: logging.Logger, level: int) -> None:
        self._logger = logger
        self._level = level
        self._partial = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        lines = (self._partial + text).split("\n")
        self._partial = lines.pop()
        for line in lines:
            self._emit(line)
        return len(text)

    def flush_partial(self) -> None:
        if self._partial:
            self._emit(self._partial)
            self._partial = ""

    def _emit(self, line: str) -> None:
        line = line.rstrip("\r")
        if line.strip():
            self._logger.log(self._level, "%s", line)


@contextlib.contextmanager
def capture_vendored_print(
    logger: logging.Logger | None = None, level: int = logging.DEBUG
) -> Iterator[None]:
    """Log what is printed inside the block, one record per line, instead of showing it.

    Replaces 0.1.0's ``quiet()``, which threw the output away. A line printed without
    a newline at the end is logged when the block exits.
    """
    writer = _LineWriter(logger or logging.getLogger(VENDOR_LOGGER_NAME), level)
    try:
        with contextlib.redirect_stdout(writer):  # type: ignore[type-var]
            yield
    finally:
        writer.flush_partial()


# --- Log files -------------------------------------------------------------------------


def _started_at(path: Path) -> datetime:
    """When the run that wrote ``path`` started: from its header, else its mtime."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            match = _HEADER_RE.match(handle.readline())
        if match:
            return datetime.fromisoformat(match.group(1))
    except (OSError, ValueError):
        pass
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone()


def rotated_name(started: datetime) -> str:
    return f"book-loader-{started.astimezone():%Y%m%d-%H%M%S}.log"


def clean_old_logs(logs_dir: Path, keep_days: int, now: datetime) -> list[Path]:
    """Delete ``*.log`` and ``download_error*.html`` older than ``keep_days``; return them.

    ``keep_days = 0`` keeps everything. Files another program has open are skipped.
    """
    if keep_days < 0:
        raise ValueError("keep_days can't be negative")
    if keep_days == 0 or not logs_dir.is_dir():
        return []
    cutoff = (now - timedelta(days=keep_days)).timestamp()
    deleted = []
    for pattern in ("*.log", "download_error*.html"):
        for path in sorted(logs_dir.glob(pattern)):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    deleted.append(path)
            except OSError:
                continue  # in use by another run, or already gone
    return deleted


@dataclass
class RunLog:
    """The log files of one run. ``close()`` (or leaving the ``with`` block) detaches
    and closes them."""

    path: Path | None = None
    extra_path: Path | None = None
    rotated: Path | None = None
    deleted: list[Path] = field(default_factory=list)
    handlers: list[logging.Handler] = field(default_factory=list)
    logger: logging.Logger | None = None
    lock: Lock | None = None

    def close(self) -> None:
        if self.logger is not None:
            for handler in self.handlers:
                self.logger.removeHandler(handler)
                handler.close()
        self.handlers = []
        if self.lock is not None:
            self.lock.release()
            self.lock = None

    def __enter__(self) -> RunLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def setup_run_logging(
    logs_dir: Path,
    keep_days: int = 30,
    extra_file: Path | None = None,
    enabled: bool = True,
    *,
    now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    pid: int | None = None,
    logger_name: str = LOGGER_NAME,
) -> RunLog:
    """Start this run's log files (see the module docstring)."""
    if not enabled:
        return RunLog()
    pid = os.getpid() if pid is None else pid
    started = now()
    logs_dir.mkdir(parents=True, exist_ok=True)
    latest = logs_dir / LATEST
    run = RunLog(logger=logging.getLogger(logger_name))

    # Whoever holds this lock owns latest-book-loader.log for the length of its run.
    lock = Lock(logs_dir / f"{LATEST}.lock", timeout=0, purpose="writing the run log")
    try:
        lock.acquire()
    except LockedError:
        run.path = logs_dir / f"latest-book-loader-{pid}.log"
    else:
        run.lock = lock
        run.path = latest
        if latest.exists():
            try:
                target = unique_path(logs_dir / rotated_name(_started_at(latest)))
                os.replace(latest, target)
                run.rotated = target
            except OSError:
                # Still open somewhere (a viewer on Windows): write our own file instead.
                run.path = logs_dir / f"latest-book-loader-{pid}.log"

    run.deleted = clean_old_logs(logs_dir, keep_days, started)
    try:
        run.handlers.append(
            _file_handler(run.path, _HEADER.format(started=started.isoformat(), pid=pid))
        )
        if extra_file is not None:
            run.extra_path = extra_file
            run.handlers.append(_file_handler(extra_file, None))
    except BaseException:
        run.close()
        raise

    assert run.logger is not None
    for handler in run.handlers:
        run.logger.addHandler(handler)
    run.logger.setLevel(logging.DEBUG)
    return run


def _file_handler(path: Path, header: str | None) -> logging.FileHandler:
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    if header is not None:
        # The first line tells the next run when this one started (see _started_at).
        stream = handler.stream
        if stream is None:  # pragma: no cover - FileHandler opens the file at once
            raise RuntimeError(f"{path} wasn't opened")
        stream.write(header + "\n")
        handler.flush()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(RedactingFilter())
    return handler
