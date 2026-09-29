"""T2.4 / T2.13: ``infra/known_dirs.py``, Windows known folders and the Downloads folder.

The OS and environment come in through a ``Host``, so the Linux and macOS lookups are
tested here with fakes. CI runs them for real on macOS and on a Linux server without
``user-dirs.dirs``; a real Linux desktop is deferred (D5).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from book_loader.infra.known_dirs import (
    FOLDERID_DOWNLOADS,
    FOLDERID_LOCAL_APP_DATA,
    DownloadsDir,
    downloads_dir,
    parse_user_dirs,
    windows_known_folder,
)
from book_loader.infra.paths import OS, Host

UNKNOWN_FOLDER_ID = "00000000-0000-0000-0000-000000000001"
HOME = Path("/home/reader")

# What xdg-user-dirs-update writes on a fresh Ubuntu install.
UBUNTU = """\
# This file is written by xdg-user-dirs-update
# If you want to change or add directories, just edit the line you're
# interested in. All local changes will be retained on the next run.
# Format is XDG_xxx_DIR="$HOME/yyy", where yyy is a shell-escaped
# homedir-relative path, or XDG_xxx_DIR="/yyy", where /yyy is an
# absolute path. No other format is supported.
#
XDG_DESKTOP_DIR="$HOME/Desktop"
XDG_DOWNLOAD_DIR="$HOME/Downloads"
XDG_TEMPLATES_DIR="$HOME/Templates"
XDG_PUBLICSHARE_DIR="$HOME/Public"
XDG_DOCUMENTS_DIR="$HOME/Documents"
XDG_MUSIC_DIR="$HOME/Music"
XDG_PICTURES_DIR="$HOME/Pictures"
XDG_VIDEOS_DIR="$HOME/Videos"
"""


# --- Windows known folders -------------------------------------------------------------


@pytest.fixture
def downloads_in_tmp_home(tmp_home) -> Path:
    """Windows expands the default Downloads location from USERPROFILE, which tmp_home
    points at an empty folder, and returns only folders that exist."""
    path = tmp_home / "Downloads"
    path.mkdir()
    return path


@pytest.mark.windows_only
@pytest.mark.parametrize("folder_id", [FOLDERID_LOCAL_APP_DATA, FOLDERID_DOWNLOADS])
def test_known_folder_is_an_existing_folder(folder_id, downloads_in_tmp_home):
    path = windows_known_folder(folder_id)
    assert path is not None
    assert path.is_absolute()


@pytest.mark.windows_only
def test_unknown_folder_id():
    assert windows_known_folder(UNKNOWN_FOLDER_ID) is None


def test_none_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert windows_known_folder(FOLDERID_LOCAL_APP_DATA) is None


# --- user-dirs.dirs --------------------------------------------------------------------


class TestParseUserDirs:
    def test_ubuntu_default(self):
        folders = parse_user_dirs(UBUNTU, HOME)
        assert folders["XDG_DOWNLOAD_DIR"] == HOME / "Downloads"
        assert folders["XDG_DESKTOP_DIR"] == HOME / "Desktop"
        assert len(folders) == 8

    @pytest.mark.parametrize(
        "line, expected",
        [
            ('XDG_DOWNLOAD_DIR="$HOME/Descargas"', HOME / "Descargas"),  # localized
            ('XDG_DOWNLOAD_DIR="${HOME}/dl"', HOME / "dl"),
            ('XDG_DOWNLOAD_DIR="$HOME/My Files/Downloads/"', HOME / "My Files" / "Downloads"),
            ('XDG_DOWNLOAD_DIR="/media/data/Downloads"', Path("/media/data/Downloads")),
            ('XDG_DOWNLOAD_DIR="$HOME/say \\"hi\\""', HOME / 'say "hi"'),
            ("XDG_DOWNLOAD_DIR=$HOME/unquoted", HOME / "unquoted"),
            ('export XDG_DOWNLOAD_DIR="$HOME/exported"', HOME / "exported"),
            ('  XDG_DOWNLOAD_DIR = "$HOME/spaced"  # note', HOME / "spaced"),
            ('XDG_DOWNLOAD_DIR="$HOME/crlf"\r', HOME / "crlf"),
        ],
    )
    def test_forms(self, line, expected):
        assert parse_user_dirs(line, HOME) == {"XDG_DOWNLOAD_DIR": expected}

    @pytest.mark.parametrize(
        "line",
        [
            'XDG_DOWNLOAD_DIR="$HOME"',  # turned off
            'XDG_DOWNLOAD_DIR="$HOME/"',
            'XDG_DOWNLOAD_DIR="${HOME}"',
            'XDG_DOWNLOAD_DIR="Downloads"',  # relative: not supported by the format
            'XDG_DOWNLOAD_DIR="$OTHER/Downloads"',
            '# XDG_DOWNLOAD_DIR="$HOME/commented"',
            "XDG_DOWNLOAD_DIR=",
            "not a setting",
            "",
        ],
    )
    def test_skipped(self, line):
        assert parse_user_dirs(line, HOME) == {}

    def test_the_last_line_wins(self):
        text = 'XDG_DOWNLOAD_DIR="$HOME/a"\nXDG_DOWNLOAD_DIR="$HOME/b"\n'
        assert parse_user_dirs(text, HOME)["XDG_DOWNLOAD_DIR"] == HOME / "b"


# --- downloads_dir ---------------------------------------------------------------------


def files(mapping: dict[Path, str]):
    """A read_text that serves ``mapping`` and raises FileNotFoundError otherwise."""

    def read_text(path: Path) -> str:
        if path not in mapping:
            raise FileNotFoundError(path)
        return mapping[path]

    return read_text


def no_known_folder(folder_id: str) -> Path | None:
    raise AssertionError("the Known Folder API was asked off Windows")


class TestLinux:
    def host(self, env: dict[str, str] | None = None) -> Host:
        return Host(OS.LINUX, env or {}, HOME)

    def test_user_dirs(self):
        read = files({HOME / ".config" / "user-dirs.dirs": 'XDG_DOWNLOAD_DIR="$HOME/Descargas"'})
        result = downloads_dir(self.host(), known_folder=no_known_folder, read_text=read)
        assert result == DownloadsDir(HOME / "Descargas", "user-dirs.dirs")

    def test_xdg_config_home(self):
        read = files({Path("/etc/reader-config/user-dirs.dirs"): 'XDG_DOWNLOAD_DIR="/srv/dl"'})
        host = self.host({"XDG_CONFIG_HOME": "/etc/reader-config"})
        assert downloads_dir(host, read_text=read).path == Path("/srv/dl")

    def test_a_relative_xdg_config_home_is_ignored(self):
        read = files({HOME / ".config" / "user-dirs.dirs": 'XDG_DOWNLOAD_DIR="$HOME/x"'})
        host = self.host({"XDG_CONFIG_HOME": "relative/config"})
        assert downloads_dir(host, read_text=read).path == HOME / "x"

    def test_a_server_without_the_file(self):
        result = downloads_dir(self.host(), read_text=files({}))
        assert result == DownloadsDir(HOME / "Downloads", "default")

    def test_a_file_without_the_entry(self):
        read = files({HOME / ".config" / "user-dirs.dirs": 'XDG_MUSIC_DIR="$HOME/Music"'})
        assert downloads_dir(self.host(), read_text=read).source == "default"

    def test_an_unreadable_file(self):
        def broken(path: Path) -> str:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        assert downloads_dir(self.host(), read_text=broken).source == "default"

    def test_reads_the_real_file(self, tmp_path):
        config = tmp_path / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Real"\n')
        assert downloads_dir(Host(OS.LINUX, {}, tmp_path)).path == tmp_path / "Real"


class TestWindows:
    def test_known_folder(self):
        moved = Path("D:/Users/reader/Downloads")
        host = Host(OS.WINDOWS, {}, HOME)
        asked = []

        def known(folder_id: str) -> Path | None:
            asked.append(folder_id)
            return moved

        assert downloads_dir(host, known_folder=known) == DownloadsDir(moved, "known folder")
        assert asked == [FOLDERID_DOWNLOADS]

    def test_known_folder_fails(self):
        host = Host(OS.WINDOWS, {}, HOME)
        result = downloads_dir(host, known_folder=lambda folder_id: None)
        assert result == DownloadsDir(HOME / "Downloads", "default")

    @pytest.mark.windows_only
    def test_this_machine(self, downloads_in_tmp_home):
        # Windows caches a known folder's path in the process after the first lookup,
        # so an earlier test's temporary home may be what comes back.
        result = downloads_dir(Host.current())
        assert result.source == "known folder"
        assert result.path.is_absolute()


class TestMacOS:
    def test_home_downloads(self):
        read = files({HOME / ".config" / "user-dirs.dirs": 'XDG_DOWNLOAD_DIR="$HOME/Other"'})
        host = Host(OS.MACOS, {}, HOME)
        result = downloads_dir(host, known_folder=no_known_folder, read_text=read)
        assert result == DownloadsDir(HOME / "Downloads", "default")


class TestOverride:
    @pytest.mark.parametrize("system", list(OS))
    def test_override_wins(self, system):
        def unused(*args):
            raise AssertionError("looked further than the override")

        host = Host(system, {}, HOME)
        result = downloads_dir(host, Path("/data/dl"), known_folder=unused, read_text=unused)
        assert result == DownloadsDir(Path("/data/dl"), "override")

    def test_tilde_is_the_home_folder(self):
        host = Host(OS.LINUX, {}, HOME)
        assert downloads_dir(host, Path("~/My Downloads")).path == HOME / "My Downloads"
        assert downloads_dir(host, Path("~")).path == HOME

    def test_other_paths_are_kept(self):
        host = Host(OS.WINDOWS, {}, HOME)
        assert downloads_dir(host, Path("D:/Downloads")).path == Path("D:/Downloads")
