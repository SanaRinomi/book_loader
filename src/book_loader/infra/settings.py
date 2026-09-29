"""Settings resolution (REFACTOR_PLAN §10.3). Settings only work out values; they never
write to disk or create folders (§5.6).

A setting is looked up in these layers; the first one that has it wins:

1. a command-line flag
2. an environment variable
3. ``.book-loader/local.toml`` (the library's device-specific settings)
4. ``library.toml``
5. the global ``config.toml`` (§4.1)
6. the built-in default

Every value comes back as a ``Setting`` that records its layer and where exactly it was
found (the flag, the variable, or the file and key), so ``info`` can show it. The two
library layers are empty until Phase 9 passes their files in.

The authorization folder has its own order: ``--auth-dir``, ``BOOK_LOADER_AUTH_DIR``,
the library's ``[auth]``, then the global folder (see ``Settings.auth_location``).
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, TypeVar

from ..domain.errors import ConfigError
from .paths import GlobalPaths, Host

__all__ = ["AUTH_DIR_ENV", "AuthLocation", "Setting", "Settings", "Source", "TomlFile"]

T = TypeVar("T")

AUTH_DIR_ENV = "BOOK_LOADER_AUTH_DIR"

_LINE = re.compile(r"\s*\(at line (\d+), column \d+\)$")
_MISSING: Any = object()


class Source(StrEnum):
    FLAG = "flag"
    ENV = "environment"
    LOCAL = "local.toml"
    LIBRARY = "library.toml"
    GLOBAL = "config.toml"
    DEFAULT = "default"


@dataclass(frozen=True)
class Setting(Generic[T]):
    """A value and where it came from. ``origin`` names the flag, the environment
    variable, or the file and key; it is None for a default."""

    value: T
    source: Source
    origin: str | None = None


@dataclass(frozen=True)
class AuthLocation:
    """The authorization folder, where the choice came from, and whether it is the
    0.1.0 Windows folder that hasn't been migrated yet (the CLI shows a notice)."""

    path: Path
    source: Source
    origin: str | None = None
    legacy: bool = False


class TomlFile:
    """One settings file, read once when a value is first needed.

    A missing file is empty. A file that can't be read or parsed raises ``ConfigError``
    naming the file and, for syntax errors, the line.
    """

    def __init__(self, path: Path, source: Source) -> None:
        self.path = path
        self.source = source
        self._data: dict[str, Any] | None = None

    def data(self) -> dict[str, Any]:
        if self._data is None:
            self._data = self._load()
        return self._data

    def _load(self) -> dict[str, Any]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise ConfigError(f"Can't read the file: {error.strerror}", path=self.path) from error
        try:
            return tomllib.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise ConfigError("The file isn't UTF-8 text", path=self.path) from error
        except tomllib.TOMLDecodeError as error:
            message, line = _describe(error)
            raise ConfigError(message, path=self.path, line=line) from error

    def get(self, key: str) -> Any:
        """The value at dotted ``key`` (``"conflicts.policy"``), or ``_MISSING``."""
        node: Any = self.data()
        walked = []
        for part in key.split("."):
            if not isinstance(node, dict):
                raise ConfigError(
                    f"'{'.'.join(walked)}' must be a table to hold '{key}'", path=self.path
                )
            if part not in node:
                return _MISSING
            node = node[part]
            walked.append(part)
        return node


def _describe(error: tomllib.TOMLDecodeError) -> tuple[str, int | None]:
    # Python 3.14 has .msg and .lineno; 3.11 only puts them in the message.
    line = getattr(error, "lineno", None)
    message = getattr(error, "msg", None) or str(error)
    match = _LINE.search(message)
    if match:
        line = line or int(match.group(1))
        message = message[: match.start()]
    return message, line


def _flag_name(name: str) -> str:
    return "--" + name.replace("_", "-")


class Settings:
    """Settings for one run.

    ``flags`` maps flag names (``"auth_dir"``) to values; None means the flag wasn't
    given. ``env`` is the environment. ``local_file`` and ``library_file`` are the
    library's ``local.toml`` and ``library.toml``, and ``library_auth_dir`` the auth
    folder its ``[auth]`` section chose; all three stay None outside a library.
    """

    def __init__(
        self,
        paths: GlobalPaths,
        *,
        env: Mapping[str, str],
        flags: Mapping[str, Any] | None = None,
        local_file: Path | None = None,
        library_file: Path | None = None,
        library_auth_dir: Path | None = None,
    ) -> None:
        self.paths = paths
        self.env = env
        self.flags: dict[str, Any] = dict(flags or {})
        self.library_auth_dir = library_auth_dir
        files = [
            (local_file, Source.LOCAL),
            (library_file, Source.LIBRARY),
            (paths.config_file, Source.GLOBAL),
        ]
        self.files = [TomlFile(path, source) for path, source in files if path is not None]

    @classmethod
    def for_host(cls, host: Host, flags: Mapping[str, Any] | None = None) -> Settings:
        return cls(GlobalPaths.resolve(host), env=host.env, flags=flags)

    def lookup(
        self,
        key: str,
        default: T,
        *,
        flag: str | None = None,
        env: str | None = None,
        convert: Callable[[Any], T] | None = None,
    ) -> Setting[T]:
        """The value of ``key`` from the first layer that has it.

        ``flag`` and ``env`` name the flag and environment variable for this setting,
        if it has them. An empty environment variable counts as unset. ``convert``
        turns a raw value (a string from the environment, any TOML value) into the
        setting's type; a ``ValueError`` or ``TypeError`` from it becomes a
        ``ConfigError`` naming where the value came from. Flag values are already
        typed by the CLI and aren't converted. The default isn't converted either.
        """
        if flag is not None and self.flags.get(flag) is not None:
            return Setting(self.flags[flag], Source.FLAG, _flag_name(flag))
        if env is not None and self.env.get(env):
            raw = self.env[env]
            return Setting(_convert(convert, raw, env, None), Source.ENV, env)
        for file in self.files:
            raw = file.get(key)
            if raw is not _MISSING:
                value = _convert(convert, raw, key, file.path)
                return Setting(value, file.source, f"{file.path}: {key}")
        return Setting(default, Source.DEFAULT)

    def auth_location(self) -> AuthLocation:
        """The authorization folder: ``--auth-dir``, then ``BOOK_LOADER_AUTH_DIR``, then
        the library's ``[auth]``, then the global folder (§10.3)."""
        flag = self.flags.get("auth_dir")
        if flag is not None:
            return AuthLocation(Path(str(flag)), Source.FLAG, "--auth-dir")
        if self.env.get(AUTH_DIR_ENV):
            return AuthLocation(Path(self.env[AUTH_DIR_ENV]), Source.ENV, AUTH_DIR_ENV)
        if self.library_auth_dir is not None:
            return AuthLocation(self.library_auth_dir, Source.LIBRARY, "[auth]")
        # The global folder (§4.1) is the built-in default, not a value in config.toml.
        return AuthLocation(self.paths.auth_dir, Source.DEFAULT, legacy=self.paths.legacy_auth)


def _convert(convert: Callable[[Any], T] | None, raw: Any, name: str, path: Path | None) -> Any:
    if convert is None:
        return raw
    try:
        return convert(raw)
    except (ValueError, TypeError) as error:
        raise ConfigError(f"Invalid value for {name} ({raw!r}): {error}", path=path) from error
