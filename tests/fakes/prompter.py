"""A ``Prompter`` that gives scripted answers and records every question.

    prompter = FakePrompter(confirm=[True], resolve_conflict=[ConflictAction.SKIP_ALL])

Each method takes the next answer queued under its name. An exception instance as an
answer is raised instead, for example ``OperationCancelled()``. A question with no
answer queued fails the test.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeVar

from book_loader.domain.conflicts import ConflictAction
from book_loader.domain.errors import BookLoaderError, ManualDownloadRequired
from book_loader.domain.prompts import Choice

T = TypeVar("T")


class FakePrompter:
    def __init__(self, **answers: list[Any]) -> None:
        self.answers = {name: list(queue) for name, queue in answers.items()}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _answer(self, method: str, *args: Any) -> Any:
        self.calls.append((method, args))
        queue = self.answers.get(method)
        if not queue:
            raise AssertionError(f"unexpected prompt: {method}{args!r}")
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def asked(self, method: str) -> list[tuple[Any, ...]]:
        return [args for name, args in self.calls if name == method]

    def manual_download(
        self, blocked: ManualDownloadRequired, previous_error: BookLoaderError | None
    ) -> Path | None:
        return self._answer("manual_download", blocked, previous_error)

    def resolve_conflict(self, path: Path, title: str, batch: bool) -> ConflictAction:
        return self._answer("resolve_conflict", path, title, batch)

    def select_books(self, message: str, choices: Sequence[Choice[T]]) -> list[T]:
        return self._answer("select_books", message, choices)

    def select_many(self, message: str, choices: Sequence[Choice[T]]) -> list[T]:
        return self._answer("select_many", message, choices)

    def confirm(self, message: str, default: bool = False) -> bool:
        return self._answer("confirm", message, default)

    def choose(self, message: str, choices: Sequence[Choice[T]], default: T | None = None) -> T:
        return self._answer("choose", message, choices, default)

    def text(self, message: str, default: str | None = None) -> str:
        return self._answer("text", message, default)

    def secret(self, message: str, confirm: bool = False) -> str:
        return self._answer("secret", message, confirm)
