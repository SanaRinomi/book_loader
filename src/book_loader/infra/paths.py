"""The global folder per OS (REFACTOR_PLAN §4.1, decision 8). Only this module decides it.

========  ===================================  ========================
OS        Global folder                        Auth folder inside it
========  ===================================  ========================
Windows   ``%LOCALAPPDATA%\\book-loader\\``      ``adobe\\``
macOS     ``~/.config/book-loader/``           ``.adobe/``
Linux     ``~/.config/book-loader/``           ``.adobe/``
========  ===================================  ========================

On Windows, when ``LOCALAPPDATA`` is unset, the Known Folder API gives Local AppData.
macOS and Linux keep 0.1.0's folder exactly; ``XDG_CONFIG_HOME`` is not used, so an
existing authorization is always found.

0.1.0 used ``~\\.config\\book-loader\\.adobe\\`` on Windows too. Until ``migrate``
copies it (§11.2), that folder is the auth folder whenever the new one doesn't exist
and the old one does; ``GlobalPaths.legacy_auth`` tells the CLI to show its notice.

Resolving paths only reads: nothing is created here. ``--auth-dir`` and
``BOOK_LOADER_AUTH_DIR`` are applied on top by ``infra/settings.py`` (T2.5).

Everything that depends on the machine comes in through a ``Host``, so each OS's
behaviour is tested on any OS.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .known_dirs import FOLDERID_LOCAL_APP_DATA, windows_known_folder

__all__ = ["APP_NAME", "GlobalPaths", "Host", "OS", "legacy_windows_auth_dir"]

APP_NAME = "book-loader"


class OS(StrEnum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"  # and any other POSIX system

    @classmethod
    def current(cls, platform: str = sys.platform) -> OS:
        if platform == "win32":
            return cls.WINDOWS
        if platform == "darwin":
            return cls.MACOS
        return cls.LINUX


def _no_known_folder() -> Path | None:
    return None


@dataclass(frozen=True)
class Host:
    """What paths depend on: the OS, the environment and the home folder.

    ``local_appdata`` asks the Windows Known Folder API for Local AppData; it's only
    called on Windows when ``LOCALAPPDATA`` is unset or empty.
    """

    os: OS
    env: Mapping[str, str]
    home: Path
    local_appdata: Callable[[], Path | None] = field(default=_no_known_folder, compare=False)

    @classmethod
    def current(cls) -> Host:
        return cls(
            os=OS.current(),
            env=dict(os.environ),
            home=Path.home(),
            local_appdata=lambda: windows_known_folder(FOLDERID_LOCAL_APP_DATA),
        )


def legacy_windows_auth_dir(home: Path) -> Path:
    """Where 0.1.0 kept the authorization on Windows (and still keeps it elsewhere)."""
    return home / ".config" / APP_NAME / ".adobe"


def _windows_root(host: Host) -> Path:
    local = host.env.get("LOCALAPPDATA") or host.local_appdata()
    base = Path(local) if local else host.home / "AppData" / "Local"
    return base / APP_NAME


@dataclass(frozen=True)
class GlobalPaths:
    """The global folder and what it holds.

    ``auth_dir`` is where the authorization is read from: normally ``default_auth_dir``,
    or on Windows the 0.1.0 folder while it hasn't been migrated (``legacy_auth``).
    """

    root: Path
    auth_dir: Path
    default_auth_dir: Path
    legacy_auth: bool = False

    @property
    def config_file(self) -> Path:
        return self.root / "config.toml"

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @classmethod
    def resolve(cls, host: Host | None = None) -> GlobalPaths:
        host = host or Host.current()
        if host.os is not OS.WINDOWS:
            root = host.home / ".config" / APP_NAME
            auth = root / ".adobe"
            return cls(root=root, auth_dir=auth, default_auth_dir=auth)

        root = _windows_root(host)
        auth = root / "adobe"
        legacy = legacy_windows_auth_dir(host.home)
        if not auth.exists() and legacy.is_dir():
            return cls(root=root, auth_dir=legacy, default_auth_dir=auth, legacy_auth=True)
        return cls(root=root, auth_dir=auth, default_auth_dir=auth)
