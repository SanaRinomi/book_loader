"""Typed events and the ``Reporter`` that receives them (REFACTOR_PLAN §4, §5.1, §12).

Everything the lower layers want to show leaves them as one of these events, so no code
below ``cli/`` prints. A pipeline step is reported as ``StepStarted``, then any number
of ``StepProgress``, ``StepNote``, ``ServerContact``, ``Redirected`` and ``Retrying``,
and ends with ``StepDone`` or ``StepFailed``. ``Warning`` can come at any time.

Events are frozen. A URL in an event has already been reduced for display: the host
alone in normal mode, the redacted URL in verbose mode (``AdeptSession``, T3.3).
``Warning`` shadows the built-in of that name inside this module only; import the
module (``from book_loader.domain import events``) and write ``events.Warning``.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "Event",
    "Redirected",
    "Reporter",
    "Retrying",
    "ServerContact",
    "StepDone",
    "StepFailed",
    "StepNote",
    "StepProgress",
    "StepStarted",
    "Warning",
]


@dataclass(frozen=True)
class StepStarted:
    """Step ``number`` of ``total`` begins, for example (1, 4, "Checking for auth")."""

    number: int
    total: int
    name: str

    def __post_init__(self) -> None:
        if not 1 <= self.number <= self.total:
            raise ValueError(f"step {self.number} of {self.total} is out of range")


@dataclass(frozen=True)
class StepProgress:
    """``done`` units (bytes, pages) of ``total``; ``total`` is None when it isn't known."""

    done: int
    total: int | None = None

    def __post_init__(self) -> None:
        if self.done < 0 or (self.total is not None and self.total < 0):
            raise ValueError(f"progress can't be negative: {self.done}/{self.total}")

    @property
    def fraction(self) -> float | None:
        """Between 0 and 1, or None when the total isn't known."""
        if not self.total:
            return None
        return min(self.done / self.total, 1.0)


@dataclass(frozen=True)
class StepNote:
    """A detail of the current step, for example "Creating anonymous authorization"."""

    text: str


@dataclass(frozen=True)
class StepDone:
    """The current step finished; ``summary`` ends its line, for example "Auth detected"."""

    summary: str = "Done"


@dataclass(frozen=True)
class StepFailed:
    """The current step failed. The error itself reaches the CLI's error boundary."""

    message: str


@dataclass(frozen=True)
class Warning:
    """Something the user should know that doesn't stop the run."""

    message: str


@dataclass(frozen=True)
class ServerContact:
    """A request to Adobe's servers. ``kind`` is "fulfill", "notify" or "download"."""

    kind: str
    url: str


@dataclass(frozen=True)
class Redirected:
    """The download was redirected to ``url``."""

    url: str


@dataclass(frozen=True)
class Retrying:
    """A request failed and is tried again; ``message`` says why."""

    message: str


Event = (
    StepStarted
    | StepProgress
    | StepNote
    | StepDone
    | StepFailed
    | Warning
    | ServerContact
    | Redirected
    | Retrying
)


class Reporter(Protocol):
    """Receives events. The CLI's ``RichReporter`` draws them; tests record them."""

    def emit(self, event: Event) -> None: ...

    def suspend(self) -> AbstractContextManager[None]:
        """Pause any live display while a prompt is shown (REFACTOR_PLAN §12)."""
        ...
