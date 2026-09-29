"""Where passwords and passphrases come from (REFACTOR_PLAN §9.9, §10.7).

The first source that has one wins; a prompt is the last resort:

====  ==============================  =================================
      Adobe ID password               Backup passphrase
====  ==============================  =================================
1     ``--password-file F``           ``--passphrase-file F``
2     ``--password-stdin``            ``--passphrase-stdin``
3     ``BOOK_LOADER_ADOBE_PASSWORD``  ``BOOK_LOADER_BACKUP_PASSPHRASE``
4     ``--password`` (with a warning)
5     a prompt, only in a terminal    a prompt, only in a terminal
====  ==============================  =================================

A file gives its first line; stdin gives one line and nothing else. Without any
source and without a terminal, ``SecretUnavailableError`` says how to give one, before
anything is written.

The value is wrapped in a ``Secret`` straight away. It prints as ``********``, in
``repr``, ``str`` and f-strings alike, so it can't end up in a log, an exception
message or a traceback by accident; ``reveal()`` is the only way to read it.
"""

from __future__ import annotations

import hmac
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from ..domain.errors import SecretUnavailableError
from ..domain.prompts import Prompter

__all__ = ["ResolvedSecret", "Secret", "SecretKind", "resolve_secret"]

MASK = "********"


class Secret:
    """A password or passphrase that never shows itself."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Secret({MASK!r})"

    def __str__(self) -> str:
        return MASK

    def __format__(self, spec: str) -> str:
        return format(MASK, spec)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    __hash__ = None  # type: ignore[assignment]

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("a Secret can't be pickled")


@dataclass(frozen=True)
class _Spec:
    label: str
    file_flag: str
    stdin_flag: str
    env_var: str
    legacy_flag: str | None
    confirm: bool
    unencrypted_hint: bool


class SecretKind(Enum):
    ADOBE_PASSWORD = _Spec(
        "Adobe ID password",
        "--password-file",
        "--password-stdin",
        "BOOK_LOADER_ADOBE_PASSWORD",
        "--password",
        confirm=False,
        unencrypted_hint=False,
    )
    # A passphrase for a backup being written: asked twice, and --no-encrypt is the way out.
    NEW_BACKUP_PASSPHRASE = _Spec(
        "backup passphrase",
        "--passphrase-file",
        "--passphrase-stdin",
        "BOOK_LOADER_BACKUP_PASSPHRASE",
        None,
        confirm=True,
        unencrypted_hint=True,
    )
    # A passphrase to open an existing backup.
    BACKUP_PASSPHRASE = _Spec(
        "backup passphrase",
        "--passphrase-file",
        "--passphrase-stdin",
        "BOOK_LOADER_BACKUP_PASSPHRASE",
        None,
        confirm=False,
        unencrypted_hint=False,
    )

    @property
    def spec(self) -> _Spec:
        return self.value


@dataclass(frozen=True)
class ResolvedSecret:
    """The secret, where it came from (a flag, a variable, or ``"prompt"``), and any
    warnings to show."""

    secret: Secret
    source: str
    warnings: tuple[str, ...] = ()


def resolve_secret(
    kind: SecretKind,
    *,
    env: Mapping[str, str],
    file: Path | None = None,
    use_stdin: bool = False,
    legacy_value: str | None = None,
    prompter: Prompter | None = None,
    interactive: bool = False,
    stdin: TextIO | None = None,
    stdin_taken_by: str | None = None,
) -> ResolvedSecret:
    """Find the secret of ``kind`` in the order at the top of this module.

    ``interactive`` says whether prompts are possible (REFACTOR_PLAN §5.10); without it
    ``prompter`` is never called. ``stdin_taken_by`` names another option that also
    reads stdin, which can't be combined with the stdin flag.
    """
    spec = kind.spec
    if legacy_value is not None and spec.legacy_flag is None:
        raise ValueError(f"the {spec.label} has no legacy command-line option")

    if file is not None:
        return ResolvedSecret(_from_file(spec, file), spec.file_flag)
    if use_stdin:
        return ResolvedSecret(
            _from_stdin(spec, stdin or sys.stdin, stdin_taken_by), spec.stdin_flag
        )
    if env.get(spec.env_var):
        return ResolvedSecret(Secret(env[spec.env_var]), spec.env_var)
    if legacy_value is not None and spec.legacy_flag is not None:
        if not legacy_value:
            raise SecretUnavailableError(
                f"{spec.legacy_flag} was given an empty {spec.label}", hint=_hint(spec)
            )
        warning = (
            f"{spec.legacy_flag} shows the {spec.label} in your shell history and the "
            f"process list. Use {spec.file_flag}, {spec.stdin_flag} or {spec.env_var} "
            "instead."
        )
        return ResolvedSecret(Secret(legacy_value), spec.legacy_flag, (warning,))
    if interactive and prompter is not None:
        value = prompter.secret(spec.label[0].upper() + spec.label[1:], confirm=spec.confirm)
        if not value:
            raise SecretUnavailableError(f"No {spec.label} was entered.", hint=_hint(spec))
        return ResolvedSecret(Secret(value), "prompt")
    raise SecretUnavailableError(f"The {spec.label} is needed, but none was given.", _hint(spec))


def _hint(spec: _Spec) -> str:
    sources = f"{spec.file_flag} FILE, {spec.stdin_flag}, or the {spec.env_var} variable"
    hint = f"Give it with {sources}, or run the command in a terminal to be asked for it."
    if spec.unencrypted_hint:
        hint += " To write the backup without encryption, add --no-encrypt."
    return hint


def _from_file(spec: _Spec, path: Path) -> Secret:
    try:
        # utf-8-sig drops the byte order mark Windows Notepad may add.
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise SecretUnavailableError(
            f"Can't read {spec.file_flag} {path}: {error.strerror or error}", hint=_hint(spec)
        ) from error
    except UnicodeDecodeError as error:
        raise SecretUnavailableError(
            f"{spec.file_flag} {path} isn't UTF-8 text", hint=_hint(spec)
        ) from error
    line = text.splitlines()[0] if text else ""
    if not line:
        raise SecretUnavailableError(
            f"The first line of {spec.file_flag} {path} is empty", hint=_hint(spec)
        )
    return Secret(line)


def _from_stdin(spec: _Spec, stdin: TextIO, taken_by: str | None) -> Secret:
    if taken_by is not None:
        raise SecretUnavailableError(
            f"{spec.stdin_flag} can't be used together with {taken_by}, which also reads "
            "standard input",
            hint=f"Give the {spec.label} with {spec.file_flag} or {spec.env_var} instead.",
        )
    if stdin.isatty():
        raise SecretUnavailableError(
            f"{spec.stdin_flag} reads the {spec.label} from a pipe, but standard input is "
            "a terminal",
            hint=f"Leave out {spec.stdin_flag} to be asked for it, or pipe it in.",
        )
    text = stdin.read()
    first, _, rest = text.partition("\n")
    first = first.removesuffix("\r")
    if not first:
        raise SecretUnavailableError(
            f"{spec.stdin_flag} was given, but standard input had no {spec.label}",
            hint=_hint(spec),
        )
    if rest.strip():
        raise SecretUnavailableError(
            f"{spec.stdin_flag} expects only the {spec.label} on standard input, one line, "
            "but there was more",
            hint="Pipe in just that one line.",
        )
    return Secret(first)
