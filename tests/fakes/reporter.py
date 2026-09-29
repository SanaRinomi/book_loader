"""A ``Reporter`` that records events, for asserting on what a service reported."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from book_loader.domain.events import Event


class RecordingReporter:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.suspended = 0  # how many times a prompt paused the display
        self.active = True

    def emit(self, event: Event) -> None:
        assert self.active, f"event emitted while suspended for a prompt: {event!r}"
        self.events.append(event)

    @contextmanager
    def suspend(self) -> Iterator[None]:
        self.suspended += 1
        self.active = False
        try:
            yield
        finally:
            self.active = True

    def of_type(self, kind: type) -> list[Event]:
        return [event for event in self.events if isinstance(event, kind)]
