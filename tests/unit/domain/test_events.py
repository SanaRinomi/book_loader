"""T2.2.2: ``domain/events.py``, the typed events and the ``Reporter`` protocol."""

from __future__ import annotations

import builtins
import dataclasses
import typing

import pytest

from book_loader.domain import events
from book_loader.domain.events import (
    Event,
    Redirected,
    Reporter,
    Retrying,
    ServerContact,
    StepDone,
    StepFailed,
    StepNote,
    StepProgress,
    StepStarted,
)
from tests.fakes.reporter import RecordingReporter

EXAMPLES = [
    StepStarted(1, 4, "Checking for auth"),
    StepProgress(10, 100),
    StepNote("Creating anonymous authorization"),
    StepDone("Auth detected: Anonymous"),
    StepFailed("Download failed"),
    events.Warning("ACSM expires in 2 days"),
    ServerContact("fulfill", "acs.example.com"),
    Redirected("cdn.example.com"),
    Retrying("HTTP 503, retrying"),
]


def test_every_event_has_an_example():
    assert {type(event) for event in EXAMPLES} == set(typing.get_args(Event))


@pytest.mark.parametrize("event", EXAMPLES, ids=lambda event: type(event).__name__)
def test_every_event_is_immutable(event):
    for field in dataclasses.fields(event):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(event, field.name, None)


@pytest.mark.parametrize("event", EXAMPLES, ids=lambda event: type(event).__name__)
def test_events_compare_by_value(event):
    assert event == dataclasses.replace(event)
    assert hash(event) == hash(dataclasses.replace(event))


def test_warning_does_not_replace_the_builtin():
    assert events.Warning is not builtins.Warning
    assert not issubclass(events.Warning, BaseException)


class TestStepStarted:
    @pytest.mark.parametrize("number, total", [(1, 1), (2, 4), (4, 4)])
    def test_in_range(self, number, total):
        assert StepStarted(number, total, "x").number == number

    @pytest.mark.parametrize("number, total", [(0, 4), (5, 4), (1, 0), (-1, 4)])
    def test_out_of_range(self, number, total):
        with pytest.raises(ValueError, match="out of range"):
            StepStarted(number, total, "x")


class TestStepProgress:
    def test_fraction(self):
        assert StepProgress(25, 100).fraction == 0.25
        assert StepProgress(100, 100).fraction == 1.0

    def test_fraction_is_capped(self):
        # Servers sometimes send more bytes than Content-Length announced.
        assert StepProgress(150, 100).fraction == 1.0

    @pytest.mark.parametrize("total", [None, 0])
    def test_unknown_total(self, total):
        assert StepProgress(10, total).fraction is None

    @pytest.mark.parametrize("done, total", [(-1, 10), (1, -10)])
    def test_not_negative(self, done, total):
        with pytest.raises(ValueError, match="negative"):
            StepProgress(done, total)


def test_step_done_default_summary():
    assert StepDone().summary == "Done"


class TestRecordingReporter:
    def test_is_a_reporter(self):
        reporter: Reporter = RecordingReporter()
        reporter.emit(StepNote("x"))

    def test_records_in_order(self):
        reporter = RecordingReporter()
        for event in EXAMPLES:
            reporter.emit(event)
        assert reporter.events == EXAMPLES
        assert reporter.of_type(StepStarted) == [EXAMPLES[0]]

    def test_suspend(self):
        reporter = RecordingReporter()
        with reporter.suspend():
            with pytest.raises(AssertionError, match="suspended"):
                reporter.emit(StepNote("drawn over a prompt"))
        reporter.emit(StepNote("after the prompt"))
        assert reporter.suspended == 1
        assert reporter.events == [StepNote("after the prompt")]

    def test_suspend_ends_on_an_exception(self):
        reporter = RecordingReporter()
        with pytest.raises(RuntimeError):
            with reporter.suspend():
                raise RuntimeError
        reporter.emit(StepDone())
        assert reporter.events == [StepDone()]
