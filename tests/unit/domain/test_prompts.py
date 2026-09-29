"""T2.2.3: ``domain/prompts.py``, the ``Prompter`` protocol and ``Choice``."""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from book_loader.domain.errors import ManualDownloadRequired, OperationCancelled
from book_loader.domain.models import ConflictAction
from book_loader.domain.prompts import Choice, Prompter
from tests.fakes.prompter import FakePrompter

METHODS = [
    "manual_download",
    "resolve_conflict",
    "select_books",
    "select_many",
    "confirm",
    "choose",
    "text",
    "secret",
]


def test_protocol_methods():
    defined = {
        name
        for name, member in vars(Prompter).items()
        if inspect.isfunction(member) and not name.startswith("_")
    }
    assert defined == set(METHODS)


def test_fake_prompter_has_the_same_signatures():
    for name in METHODS:
        expected = inspect.signature(getattr(Prompter, name))
        assert inspect.signature(getattr(FakePrompter, name)) == expected, name


def test_fake_prompter_is_a_prompter():
    prompter: Prompter = FakePrompter(confirm=[True])
    assert prompter.confirm("Continue?") is True


class TestChoice:
    def test_defaults(self):
        choice = Choice(1, "One")
        assert (choice.detail, choice.disabled, choice.checked) == ("", None, False)

    def test_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            Choice(1, "One").label = "Two"  # type: ignore[misc]


class TestFakePrompter:
    def test_answers_in_order_and_records_questions(self):
        choices = [Choice("a", "A"), Choice("b", "B")]
        prompter = FakePrompter(
            choose=["b"],
            select_books=[["a"]],
            resolve_conflict=[ConflictAction.SKIP, ConflictAction.OVERWRITE_ALL],
        )
        assert prompter.choose("Pick one", choices) == "b"
        assert prompter.select_books("Books", choices) == ["a"]
        assert prompter.resolve_conflict(Path("A.epub"), "A", True) is ConflictAction.SKIP
        assert prompter.resolve_conflict(Path("B.epub"), "B", True) is ConflictAction.OVERWRITE_ALL
        assert prompter.asked("resolve_conflict") == [
            (Path("A.epub"), "A", True),
            (Path("B.epub"), "B", True),
        ]

    def test_raises_a_queued_exception(self):
        prompter = FakePrompter(secret=[OperationCancelled()])
        with pytest.raises(OperationCancelled):
            prompter.secret("Passphrase", confirm=True)

    def test_an_unexpected_question_fails(self):
        with pytest.raises(AssertionError, match="unexpected prompt: confirm"):
            FakePrompter().confirm("Continue?")

    def test_manual_download(self):
        blocked = ManualDownloadRequired(
            "HTTP 429", "https://x", Path("l.html"), Path("b.acsm"), "0123456789abcdef"
        )
        prompter = FakePrompter(manual_download=[Path("Book.epub"), None])
        assert prompter.manual_download(blocked, None) == Path("Book.epub")
        assert prompter.manual_download(blocked, blocked) is None
