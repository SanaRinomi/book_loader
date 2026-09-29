"""The ``Prompter``: every question book-loader asks goes through it (REFACTOR_PLAN §5.4).

The CLI picks one of three implementations (§5.10): ``RichPrompter`` in a real
terminal, ``PlainPrompter`` where prompt_toolkit can't run, and
``NonInteractivePrompter``, which asks nothing and fails with a hint instead.

When the user cancels a question (Ctrl+C, Esc), a prompter raises
``OperationCancelled``. A prompter shows its question on stderr and pauses the
reporter's live display while it does (``Reporter.suspend``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from .conflicts import ConflictAction
from .errors import BookLoaderError, ManualDownloadRequired

__all__ = ["Choice", "Prompter"]

T = TypeVar("T")


@dataclass(frozen=True)
class Choice(Generic[T]):
    """One entry of a menu or checklist.

    ``detail`` is a second column, such as an author or a size. A choice with a
    ``disabled`` reason is shown but can't be picked, for example a Kobo book that isn't
    downloaded. ``checked`` pre-selects it in a checklist.
    """

    value: T
    label: str
    detail: str = ""
    disabled: str | None = None
    checked: bool = False


class Prompter(Protocol):
    def manual_download(
        self, blocked: ManualDownloadRequired, previous_error: BookLoaderError | None
    ) -> Path | None:
        """Ask for the book the user downloaded in a browser after a blocked download.

        ``previous_error`` is why the last file given was refused, or None on the first
        call. Returns the file, or None when the user gives up for now.
        """
        ...

    def resolve_conflict(self, path: Path, title: str, batch: bool) -> ConflictAction:
        """``path`` exists. A single book gets a yes/no question, a batch gets the menu
        with the "all" answers and cancel (REFACTOR_PLAN §14)."""
        ...

    def select_books(self, message: str, choices: Sequence[Choice[T]]) -> list[T]:
        """Pick books from a list. Returns the picked values in list order."""
        ...

    def select_many(self, message: str, choices: Sequence[Choice[T]]) -> list[T]:
        """Tick any number of entries, for example backup parts."""
        ...

    def confirm(self, message: str, default: bool = False) -> bool: ...

    def choose(self, message: str, choices: Sequence[Choice[T]], default: T | None = None) -> T:
        """Pick exactly one entry."""
        ...

    def text(self, message: str, default: str | None = None) -> str: ...

    def secret(self, message: str, confirm: bool = False) -> str:
        """Read a password or passphrase without echoing it or keeping any history.

        ``confirm`` asks for it twice and asks again until both match. The caller wraps
        the result in a ``Secret`` straight away (T2.9).
        """
        ...
