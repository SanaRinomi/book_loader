"""T0.4.4: auth backup helpers of 0.1.0 (``backup_auth``, ``list_backups``, ``restore_auth``)."""

from __future__ import annotations

import os
import tarfile
from pathlib import Path

import pytest

from book_loader.cli import backup_auth, list_backups, restore_auth
from tests.fixtures.builders.adobe_auth import build_auth_folder

# 0.1.0 calls extractall() without filter="data" (REFACTOR_PLAN §6, fixed in T3.8).
pytestmark = pytest.mark.filterwarnings("ignore:Python 3.14 will:DeprecationWarning")

V0 = Path(__file__).parents[1] / "fixtures" / "v0"
AUTH_FILES = {"activation.xml", "device.xml", "devicesalt"}


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}


def test_round_trip_into_a_folder_with_the_same_name(tmp_path):
    auth = build_auth_folder(tmp_path / ".adobe")
    before = _snapshot(auth.path)

    archive = backup_auth(auth.path, tmp_path / "backups" / "auth_anonymous_x.tar.gz")
    (auth.path / "activation.xml").write_text("changed", encoding="utf-8")
    restore_auth(archive, auth.path)

    assert _snapshot(auth.path) == before


def test_archive_holds_the_folder_under_its_own_name(tmp_path):
    auth = build_auth_folder(tmp_path / "my-auth")
    archive = backup_auth(auth.path, tmp_path / "b.tar.gz")
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert names == {"my-auth"} | {f"my-auth/{f}" for f in AUTH_FILES}


def test_backup_creates_missing_parent_folders(tmp_path):
    auth = build_auth_folder(tmp_path / ".adobe")
    archive = backup_auth(auth.path, tmp_path / "a" / "b" / "c.tar.gz")
    assert archive.is_file()


def test_backups_are_listed_newest_first(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    for name, mtime in [("old", 1_000_000), ("new", 3_000_000), ("mid", 2_000_000)]:
        path = folder / f"{name}.tar.gz"
        path.write_bytes(b"")
        os.utime(path, (mtime, mtime))
    (folder / "other.zip").write_bytes(b"")

    assert [p.name for p in list_backups(folder)] == ["new.tar.gz", "mid.tar.gz", "old.tar.gz"]


def test_missing_backup_folder_lists_nothing(tmp_path):
    assert list_backups(tmp_path / "missing") == []


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="0.1.0 extracts into the auth folder's parent under the archive's own folder "
    "name (REFACTOR_PLAN §3, §6). T3.8 must make this pass.",
)
def test_restore_into_a_folder_with_a_different_name(tmp_path):
    source = build_auth_folder(tmp_path / ".adobe")
    archive = backup_auth(source.path, tmp_path / "backup.tar.gz")
    target = tmp_path / "custom-auth"

    restore_auth(archive, target)

    # Today the files land in tmp_path/.adobe instead, and target is never created.
    assert target.is_dir()
    assert _snapshot(target) == _snapshot(source.path)


@pytest.mark.parametrize(
    "archive", ["auth_anonymous_20260101_000000.tar.gz", "auth_backup_20260101_000000.tar.gz"]
)
def test_v0_archives_restore_into_the_default_folder_name(tmp_path, archive):
    """The archives 0.1.0 wrote (T0.4.4 fixtures) hold a ``.adobe`` folder."""
    target = tmp_path / ".adobe"
    restore_auth(V0 / archive, target)
    assert set(_snapshot(target)) == AUTH_FILES
