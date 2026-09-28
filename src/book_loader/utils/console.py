"""
Compact step-by-step status output.

Normal mode shows one line per stage, extended as work progresses:

    [2/3] Downloading: books.google.com... Redirected & Downloading: ...googleusercontent.com... 45%

Verbose mode puts every message on its own line, because detailed (redacted)
protocol logs are printed in between.
"""

import contextlib
import io
import shutil
import sys
from urllib.parse import urlsplit

from .redact import redact_url


class StepReporter:
    """Prints "[n/total] ..." status lines for a multi-step job."""

    def __init__(self, total: int, verbose: bool = False, stream=None):
        self.total = total
        self.verbose = verbose
        self.out = stream if stream is not None else sys.stdout
        try:
            self.tty = self.out.isatty()
        except (AttributeError, ValueError):
            self.tty = False
        self.current = 0
        self._line = None  # text of the unfinished line; None when no line is open
        self._suffix = 0  # length of the progress text drawn after it
        self._percent = None

    @property
    def prefix(self) -> str:
        return f"[{self.current}/{self.total}]"

    def step(self, number: int, text: str) -> None:
        """Start step `number` with a new line."""
        self.current = number
        self.start(text)

    def start(self, text: str) -> None:
        """Start a new line within the current step."""
        self.end()
        self._line = f"{self.prefix} {text}..."
        self._write(self._line)
        if self.verbose:
            self.end()

    def add(self, text: str, end: str = "...") -> None:
        """Append to the open line (or start one)."""
        if self._line is None:
            if end == "...":
                self.start(text)
            else:
                self._standalone(text + end)
            return
        self._emit(f" {text}{end}")

    def done(self, text: str = "Done.") -> None:
        """Finish the open line with `text`."""
        if self._line is None:
            self._standalone(text)
            return
        self._emit(f" {text}")
        self.end()

    def fail(self) -> None:
        """Mark the open line as failed; does nothing when no line is open."""
        if self._line is not None:
            self.done("ERROR!")

    def note(self, text: str) -> None:
        """Print a message on its own line, e.g. a warning."""
        self._standalone(text)

    def end(self) -> None:
        """Close the open line, if any."""
        if self._line is not None:
            self._write("\n")
        self._line = None
        self._suffix = 0
        self._percent = None

    def progress(self, done: int, total) -> None:
        """Show download progress after the open line (terminals only)."""
        if not self.tty or not total:
            return
        percent = min(100, done * 100 // total)
        if percent == self._percent:
            return
        if self._line is None:
            self._line = f"{self.prefix} Downloading..."
        self._percent = percent
        suffix = " %d%% (%.1f / %.1f MB)" % (percent, done / 1048576, total / 1048576)
        if len(self._line) + len(suffix) >= shutil.get_terminal_size().columns:
            return  # "\r" can't redraw a wrapped line
        pad = " " * max(0, self._suffix - len(suffix))
        self._write("\r" + self._line + suffix + pad)
        self._suffix = len(suffix)

    def on_event(self, event: str, **data) -> None:
        """Receiver for libadobe.report() events from the Adobe protocol code."""
        url = data.get("url")
        if event == "fulfill":
            self.add(f"Requesting license from {self._where(url)}")
        elif event == "fulfilled":
            self.done("OK.")
        elif event == "notify_start":
            self.start("Notifying Server")
        elif event == "notify":
            self.add(f"Notifying {self._where(url)}")
        elif event == "notify_result":
            self.add("Fulfilment Notification: " + ("Success!" if data.get("ok") else "Failed!"), end="")
        elif event == "download":
            self.start(f"Downloading: {self._where(url)}")
        elif event == "redirect":
            self.add(f"Redirected & Downloading: {self._where(url)}")
        elif event == "progress":
            self.progress(data.get("done", 0), data.get("total"))
        elif event == "retry":
            self.add(data.get("message", "Retrying"))
        elif event == "start":
            self.start(data.get("message", ""))
        elif event == "info":
            self.add(data.get("message", ""), end=data.get("end", "..."))
        elif event == "warning":
            self.note("WARNING: " + data.get("message", ""))

    def _where(self, url) -> str:
        # Host only in normal mode; verbose logs show the (redacted) full URL.
        if not url:
            return "server"
        if self.verbose:
            return redact_url(url)
        try:
            return urlsplit(url).hostname or redact_url(url)
        except ValueError:
            return "server"

    def _standalone(self, text: str) -> None:
        self.end()
        self._write(f"{self.prefix} {text}\n")

    def _emit(self, segment: str) -> None:
        if self._suffix:
            # Redraw the line without the progress text, then leave the cursor at its end.
            pad = " " * max(0, self._suffix - len(segment))
            self._write("\r" + self._line + segment + pad + "\r" + self._line + segment)
            self._suffix = 0
            self._percent = None
        else:
            self._write(segment)
        self._line += segment

    def _write(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()


@contextlib.contextmanager
def quiet(enabled: bool = True):
    """Swallow print() output (from vendored code) unless verbose logging is on."""
    if not enabled:
        yield
        return
    with contextlib.redirect_stdout(io.StringIO()):
        yield
