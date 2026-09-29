"""T2.4: ``infra/paths.py``, the global folder per OS and the old Windows location.

The OS and environment are passed in through a ``Host``, so the Windows, macOS and
Linux cases all run here, and CI runs the suite on real macOS and Linux as well (D5).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from book_loader.infra.paths import OS, GlobalPaths, Host, legacy_windows_auth_dir


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "Users" / "reader"
    path.mkdir(parents=True)
    return path


def windows(home: Path, env: dict[str, str] | None = None, known: Path | None = None) -> Host:
    if env is None:
        env = {"LOCALAPPDATA": str(home / "AppData" / "Local")}
    return Host(OS.WINDOWS, env, home, local_appdata=lambda: known)


def snapshot(folder: Path) -> set[Path]:
    return set(folder.rglob("*"))


class TestOS:
    @pytest.mark.parametrize(
        "platform, expected",
        [
            ("win32", OS.WINDOWS),
            ("darwin", OS.MACOS),
            ("linux", OS.LINUX),
            ("freebsd14", OS.LINUX),
        ],
    )
    def test_from_platform(self, platform, expected):
        assert OS.current(platform) is expected

    def test_current_host(self):
        host = Host.current()
        assert host.os is OS.current()
        assert host.home == Path.home()
        assert host.env == dict(os.environ)


class TestWindows:
    def test_local_appdata(self, home):
        paths = GlobalPaths.resolve(windows(home))
        root = home / "AppData" / "Local" / "book-loader"
        assert paths.root == root
        assert paths.auth_dir == root / "adobe"
        assert paths.default_auth_dir == root / "adobe"
        assert not paths.legacy_auth

    def test_files_and_folders(self, home):
        paths = GlobalPaths.resolve(windows(home))
        assert paths.config_file == paths.root / "config.toml"
        assert paths.state_file == paths.root / "state.json"
        assert paths.logs_dir == paths.root / "logs"
        assert paths.backups_dir == paths.root / "backups"

    def test_moved_local_appdata_is_followed(self, home, tmp_path):
        moved = tmp_path / "D" / "Local"
        paths = GlobalPaths.resolve(windows(home, {"LOCALAPPDATA": str(moved)}))
        assert paths.root == moved / "book-loader"

    @pytest.mark.parametrize("env", [{}, {"LOCALAPPDATA": ""}])
    def test_known_folder_when_the_variable_is_unset(self, home, tmp_path, env):
        known = tmp_path / "Known" / "Local"
        paths = GlobalPaths.resolve(windows(home, env, known=known))
        assert paths.root == known / "book-loader"

    def test_known_folder_is_not_asked_when_the_variable_is_set(self, home):
        def fail() -> Path | None:
            raise AssertionError("Known Folder API called")

        host = Host(OS.WINDOWS, {"LOCALAPPDATA": str(home / "L")}, home, local_appdata=fail)
        assert GlobalPaths.resolve(host).root == home / "L" / "book-loader"

    def test_home_based_fallback_when_everything_fails(self, home):
        paths = GlobalPaths.resolve(windows(home, {}, known=None))
        assert paths.root == home / "AppData" / "Local" / "book-loader"

    def test_host_without_a_known_folder_lookup(self, home):
        paths = GlobalPaths.resolve(Host(OS.WINDOWS, {}, home))
        assert paths.root == home / "AppData" / "Local" / "book-loader"


class TestOldWindowsLocation:
    """0.1.0's ``~\\.config\\book-loader\\.adobe\\`` is read until it is migrated."""

    def test_only_the_old_folder_exists(self, home):
        old = legacy_windows_auth_dir(home)
        old.mkdir(parents=True)
        paths = GlobalPaths.resolve(windows(home))
        assert paths.auth_dir == old
        assert paths.legacy_auth
        assert paths.default_auth_dir == home / "AppData" / "Local" / "book-loader" / "adobe"
        # Only the auth folder falls back; everything else uses the new folder.
        assert paths.logs_dir == home / "AppData" / "Local" / "book-loader" / "logs"

    def test_both_exist(self, home):
        legacy_windows_auth_dir(home).mkdir(parents=True)
        new = home / "AppData" / "Local" / "book-loader" / "adobe"
        new.mkdir(parents=True)
        paths = GlobalPaths.resolve(windows(home))
        assert paths.auth_dir == new
        assert not paths.legacy_auth

    def test_neither_exists(self, home):
        paths = GlobalPaths.resolve(windows(home))
        assert paths.auth_dir == paths.default_auth_dir
        assert not paths.legacy_auth

    def test_old_path_is_a_file(self, home):
        old = legacy_windows_auth_dir(home)
        old.parent.mkdir(parents=True)
        old.write_text("not a folder")
        assert not GlobalPaths.resolve(windows(home)).legacy_auth

    def test_old_location(self, home):
        assert legacy_windows_auth_dir(home) == home / ".config" / "book-loader" / ".adobe"


@pytest.mark.parametrize("system", [OS.MACOS, OS.LINUX])
class TestPosix:
    def test_unchanged_from_0_1_0(self, home, system):
        paths = GlobalPaths.resolve(Host(system, {}, home))
        assert paths.root == home / ".config" / "book-loader"
        assert paths.auth_dir == home / ".config" / "book-loader" / ".adobe"
        assert paths.default_auth_dir == paths.auth_dir
        assert paths.logs_dir == paths.root / "logs"

    def test_windows_variables_and_xdg_are_ignored(self, home, tmp_path, system):
        env = {"LOCALAPPDATA": str(tmp_path / "L"), "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
        assert GlobalPaths.resolve(Host(system, env, home)).root == home / ".config" / "book-loader"

    def test_never_uses_the_legacy_flag(self, home, system):
        (home / ".config" / "book-loader" / ".adobe").mkdir(parents=True)
        assert not GlobalPaths.resolve(Host(system, {}, home)).legacy_auth


@pytest.mark.parametrize(
    "make_host",
    [
        lambda home: windows(home),
        lambda home: windows(home, {}, known=None),
        lambda home: Host(OS.MACOS, {}, home),
        lambda home: Host(OS.LINUX, {}, home),
    ],
    ids=["windows", "windows-no-localappdata", "macos", "linux"],
)
@pytest.mark.parametrize("old_folder", [False, True])
def test_resolving_never_creates_folders(tmp_path, home, make_host, old_folder):
    if old_folder:
        legacy_windows_auth_dir(home).mkdir(parents=True)
    before = snapshot(tmp_path)
    paths = GlobalPaths.resolve(make_host(home))
    _ = (paths.config_file, paths.state_file, paths.logs_dir, paths.backups_dir)
    assert snapshot(tmp_path) == before


def test_resolves_the_current_host_by_default(tmp_home):
    # tmp_home points HOME, USERPROFILE and LOCALAPPDATA into the temporary home.
    paths = GlobalPaths.resolve()
    if OS.current() is OS.WINDOWS:
        assert paths.root == tmp_home / "AppData" / "Local" / "book-loader"
    else:
        assert paths.root == tmp_home / ".config" / "book-loader"
