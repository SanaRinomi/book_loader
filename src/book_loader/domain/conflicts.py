"""What to do when an output file already exists (REFACTOR_PLAN §6, §14).

``ConflictPolicy`` comes from ``--overwrite`` / ``--skip-existing`` or from
``[conflicts] policy`` in ``library.toml``. ``ConflictResolver`` applies it to one
output path at a time and asks through the ``Prompter`` when the policy is ``ask``. In
a batch it remembers an "all" answer for the books that follow.

The resolver only decides. The ``Store`` step writes, and for ``RENAME`` it picks the
free name (``Title (2).epub``) with ``infra.fs.unique_path`` when it moves the file, so
the name can't be taken in between.

0.1.0 applied a remembered "skip all" before checking whether the file exists, so
``kobo dedrm --skip-existing`` skipped every book. Here a path that doesn't exist is
always written.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import ConfigError, OperationCancelled

if TYPE_CHECKING:
    from .prompts import Prompter

__all__ = ["ConflictAction", "ConflictPolicy", "ConflictResolver", "Resolution"]


class ConflictAction(StrEnum):
    """An answer to "the output file already exists", as the ``Prompter`` returns it."""

    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"
    OVERWRITE_ALL = "overwrite_all"
    SKIP_ALL = "skip_all"
    CANCEL = "cancel"


class ConflictPolicy(StrEnum):
    ASK = "ask"
    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"

    @classmethod
    def from_flags(
        cls, overwrite: bool, skip_existing: bool, default: ConflictPolicy | None = None
    ) -> ConflictPolicy:
        """The policy for ``--overwrite`` / ``--skip-existing``; ``default`` (from the
        library, or ``ask``) when neither is given."""
        if overwrite and skip_existing:
            raise ConfigError(
                "--overwrite and --skip-existing cannot be used together",
                hint="Give only one of them.",
            )
        if overwrite:
            return cls.OVERWRITE
        if skip_existing:
            return cls.SKIP
        return default or cls.ASK


class Resolution(StrEnum):
    """What to do with one output path."""

    WRITE = "write"  # nothing is there
    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"  # write next to it under a free name

    @property
    def writes(self) -> bool:
        return self is not Resolution.SKIP


_FIXED = {
    ConflictPolicy.OVERWRITE: Resolution.OVERWRITE,
    ConflictPolicy.SKIP: Resolution.SKIP,
    ConflictPolicy.RENAME: Resolution.RENAME,
}

_ANSWERS = {
    ConflictAction.OVERWRITE: Resolution.OVERWRITE,
    ConflictAction.SKIP: Resolution.SKIP,
    ConflictAction.RENAME: Resolution.RENAME,
    ConflictAction.OVERWRITE_ALL: Resolution.OVERWRITE,
    ConflictAction.SKIP_ALL: Resolution.SKIP,
}


class ConflictResolver:
    """Resolves conflicts for one command run.

    ``batch`` decides which question the prompter asks: a yes/no for a single book, the
    menu with the "all" answers and cancel for a batch. ``exists`` is replaceable for
    tests.
    """

    def __init__(
        self,
        policy: ConflictPolicy,
        prompter: Prompter,
        batch: bool,
        exists: Callable[[Path], bool] = Path.exists,
    ) -> None:
        self.policy = policy
        self.prompter = prompter
        self.batch = batch
        self._exists = exists
        self.remembered: Resolution | None = None

    def resolve(self, path: Path, title: str) -> Resolution:
        """Decide what to do with ``path``. Raises ``OperationCancelled`` on cancel."""
        if not self._exists(path):
            return Resolution.WRITE
        if self.remembered is not None:
            return self.remembered
        if self.policy is not ConflictPolicy.ASK:
            return _FIXED[self.policy]

        answer = self.prompter.resolve_conflict(path, title, self.batch)
        if answer is ConflictAction.CANCEL:
            raise OperationCancelled()
        resolution = _ANSWERS[answer]
        if answer in (ConflictAction.OVERWRITE_ALL, ConflictAction.SKIP_ALL):
            self.remembered = resolution
        return resolution
