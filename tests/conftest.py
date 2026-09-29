"""Shared fixtures for the whole test suite.

Every test runs with a temporary home folder (``tmp_home`` is autouse), so no test can
read or change the real authorization, Kobo library, Downloads or backups. Tests not
marked ``network`` or ``live`` also can't open network connections, except to this
machine, so nothing reaches Adobe by accident.
"""

from __future__ import annotations

import importlib
import inspect
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest
import time_machine
from click.testing import CliRunner

# The real home, captured before any fixture patches it.
REAL_HOME = Path.home()

# The instant ``frozen_time`` stops the clock at.
FROZEN_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

# Home-based paths that old code computes once, at import time, as class attributes.
# Changing HOME does not reach them, so ``tmp_home`` re-points each one to the same
# place inside the temporary home. The place is listed here rather than derived from
# the current value, because that value depends on which test imported the module
# first. Remove the entries when Phase 6 deletes the old modules.
_IMPORT_TIME_HOME_PATHS = [
    ("book_loader.cli", "_ManualDownloadPrompt", "DOWNLOADS", ("Downloads",)),
    (
        "book_loader.core.kobo.library",
        "KoboLibrary",
        "DEFAULT_KOBODIR",
        ("Library", "Application Support", "Kobo", "Kobo Desktop Edition"),
    ),
]

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_OS_MARKERS = {
    "windows_only": sys.platform == "win32",
    "macos_only": sys.platform == "darwin",
    "posix_only": os.name == "posix",
}


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip tests whose OS marker does not match the running platform."""
    for marker, matches in _OS_MARKERS.items():
        if item.get_closest_marker(marker) and not matches:
            pytest.skip(f"{marker} test, running on {sys.platform}")


def _repoint_import_time_paths(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for module_name, class_name, attr, parts in _IMPORT_TIME_HOME_PATHS:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            # A module that can't be imported can't leak the real home either. The test
            # that needs it fails on the import with the real error.
            continue
        monkeypatch.setattr(getattr(module, class_name), attr, home.joinpath(*parts))


@pytest.fixture(autouse=True)
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty home folder that ``Path.home()`` and ``~`` resolve to on every OS.

    Points HOME, USERPROFILE, LOCALAPPDATA and APPDATA into it and removes every
    ``BOOK_LOADER_*`` variable, so settings from the developer's shell don't leak in.
    """
    home = tmp_path / "home"
    local_appdata = home / "AppData" / "Local"
    roaming_appdata = home / "AppData" / "Roaming"
    for folder in (local_appdata, roaming_appdata):
        folder.mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("APPDATA", str(roaming_appdata))
    for name in list(os.environ):
        if name.startswith("BOOK_LOADER_"):
            monkeypatch.delenv(name)

    _repoint_import_time_paths(home, monkeypatch)
    return home


class NetworkBlockedError(RuntimeError):
    """A test tried to reach another machine without the ``network`` or ``live`` marker."""


def _host_of(address) -> str | None:
    return address[0] if isinstance(address, tuple) and address else None


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("network") or request.node.get_closest_marker("live"):
        return

    def refuse(host) -> None:
        raise NetworkBlockedError(
            f"Test tried to connect to {host!r}; mark it network or live if that is intended"
        )

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        host = _host_of(address)
        if host is not None and host not in _LOCAL_HOSTS:
            refuse(host)
        return real_connect(self, address)

    def connect_ex(self, address):
        host = _host_of(address)
        if host is not None and host not in _LOCAL_HOSTS:
            refuse(host)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name is not None and name not in _LOCAL_HOSTS:
            refuse(name)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture
def auth_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty authorization folder, with ``BOOK_LOADER_AUTH_DIR`` set to it."""
    path = tmp_path / "auth"
    path.mkdir(mode=0o700)
    monkeypatch.setenv("BOOK_LOADER_AUTH_DIR", str(path))
    return path


@pytest.fixture
def downloads_dir(tmp_home: Path) -> Path:
    """An empty Downloads folder inside the temporary home."""
    path = tmp_home / "Downloads"
    path.mkdir()
    return path


@pytest.fixture
def frozen_time():
    """Stops the clock at ``FROZEN_NOW``.

    Covers ``time.time``, ``time.strftime`` and ``datetime.now``, including names
    imported with ``from ... import``. Yields the time-machine traveller, so a test can
    move the clock with ``frozen_time.shift(timedelta(...))`` or ``move_to(...)``.
    Local-time output such as ``time.strftime`` still depends on the host's time zone.
    """
    with time_machine.travel(FROZEN_NOW, tick=False) as traveller:
        yield traveller


@pytest.fixture
def cli_runner() -> CliRunner:
    """A Click ``CliRunner`` whose results keep stderr separate from stdout."""
    # Click 8.2 always separates them and dropped ``mix_stderr``; 8.1 mixes by default.
    if "mix_stderr" in inspect.signature(CliRunner.__init__).parameters:
        return cast(Any, CliRunner)(mix_stderr=False)  # Click 8.2's types don't have it
    return CliRunner()
