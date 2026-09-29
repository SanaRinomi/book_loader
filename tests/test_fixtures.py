"""Smoke tests for the shared fixtures in conftest.py (T0.2)."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import pytest

from tests.conftest import FROZEN_NOW, REAL_HOME


class TestTmpHome:
    def test_home_and_tilde_resolve_to_tmp_home(self, tmp_home: Path):
        assert Path.home() == tmp_home
        assert Path("~").expanduser() == tmp_home
        assert os.path.expanduser("~") == str(tmp_home)
        assert tmp_home != REAL_HOME

    def test_app_data_variables_point_inside(self, tmp_home: Path):
        for name in ("LOCALAPPDATA", "APPDATA"):
            path = Path(os.environ[name])
            assert path.is_dir()
            assert path.is_relative_to(tmp_home)

    def test_home_starts_empty_apart_from_app_data(self, tmp_home: Path):
        assert [p.name for p in tmp_home.iterdir()] == ["AppData"]

    def test_book_loader_variables_are_cleared(self):
        assert not [name for name in os.environ if name.startswith("BOOK_LOADER_")]

    def test_default_auth_dir_is_inside(self, tmp_home: Path):
        from book_loader.utils.config import Config

        assert Config().auth_dir == tmp_home / ".config" / "book-loader" / ".adobe"

    def test_import_time_paths_are_repointed(self, tmp_home: Path):
        from book_loader.cli import _ManualDownloadPrompt
        from book_loader.core.kobo.library import KoboLibrary

        assert _ManualDownloadPrompt.DOWNLOADS == tmp_home / "Downloads"
        assert KoboLibrary.DEFAULT_KOBODIR == (
            tmp_home / "Library" / "Application Support" / "Kobo" / "Kobo Desktop Edition"
        )


class TestAuthDir:
    def test_is_empty_and_set_in_environment(self, auth_dir: Path):
        assert auth_dir.is_dir()
        assert list(auth_dir.iterdir()) == []
        assert os.environ["BOOK_LOADER_AUTH_DIR"] == str(auth_dir)

    def test_old_config_uses_it(self, auth_dir: Path):
        from book_loader.utils.config import Config

        assert Config().auth_dir == auth_dir

    @pytest.mark.posix_only
    def test_is_private(self, auth_dir: Path):
        assert auth_dir.stat().st_mode & 0o077 == 0


class TestDownloadsDir:
    def test_is_empty_folder_in_home(self, downloads_dir: Path, tmp_home: Path):
        assert downloads_dir == tmp_home / "Downloads"
        assert downloads_dir.is_dir()
        assert list(downloads_dir.iterdir()) == []

    def test_old_manual_download_prompt_uses_it(self, downloads_dir: Path):
        from book_loader.cli import _ManualDownloadPrompt

        assert _ManualDownloadPrompt.DOWNLOADS == downloads_dir


class TestFrozenTime:
    def test_clock_reads_frozen_instant(self, frozen_time):
        assert time.time() == FROZEN_NOW.timestamp()
        assert datetime.now(timezone.utc) == FROZEN_NOW

    def test_local_time_agrees(self, frozen_time):
        local = datetime.fromtimestamp(FROZEN_NOW.timestamp())
        assert datetime.now() == local
        assert time.strftime("%Y-%m-%dT%H:%M:%S") == local.strftime("%Y-%m-%dT%H:%M:%S")

    def test_names_imported_by_old_code_are_frozen(self, frozen_time):
        import book_loader.cli as old_cli

        assert old_cli.datetime.now() == datetime.fromtimestamp(FROZEN_NOW.timestamp())
        assert old_cli.time.time() == FROZEN_NOW.timestamp()

    def test_clock_does_not_tick(self, frozen_time):
        first = time.time()
        sum(range(100_000))
        assert time.time() == first

    def test_clock_can_be_moved(self, frozen_time):
        frozen_time.shift(timedelta(hours=1))
        assert time.time() == FROZEN_NOW.timestamp() + 3600


class TestCliRunner:
    def test_keeps_stderr_separate(self, cli_runner):
        @click.command()
        def speak():
            click.echo("to stdout")
            click.echo("to stderr", err=True)

        result = cli_runner.invoke(speak)
        assert result.exit_code == 0
        assert result.stdout == "to stdout\n"
        assert result.stderr == "to stderr\n"

    def test_runs_the_real_cli(self, cli_runner):
        from book_loader.cli import cli

        result = cli_runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.stdout
        assert result.stderr == ""


class TestOsMarkers:
    @pytest.mark.windows_only
    def test_windows_only_runs_on_windows(self):
        assert sys.platform == "win32"

    @pytest.mark.macos_only
    def test_macos_only_runs_on_macos(self):
        assert sys.platform == "darwin"

    @pytest.mark.posix_only
    def test_posix_only_runs_on_posix(self):
        assert os.name == "posix"


class TestNetworkGuard:
    def test_remote_connections_are_refused(self):
        import socket
        import urllib.request

        from tests.conftest import NetworkBlockedError

        with pytest.raises(NetworkBlockedError):
            socket.create_connection(("example.com", 443), timeout=1)
        with pytest.raises(NetworkBlockedError):
            urllib.request.urlopen("https://adeactivate.adobe.com/adept", timeout=1)

    def test_loopback_is_allowed(self):
        import socket

        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            with socket.create_connection(server.getsockname(), timeout=1):
                pass
