"""``infra/known_dirs.py``: the Windows Known Folder lookup used by T2.4 (T2.13 adds more)."""

from __future__ import annotations

import sys

import pytest

from book_loader.infra.known_dirs import FOLDERID_LOCAL_APP_DATA, windows_known_folder

UNKNOWN_FOLDER_ID = "00000000-0000-0000-0000-000000000001"


@pytest.mark.windows_only
def test_local_appdata_is_an_existing_folder():
    path = windows_known_folder(FOLDERID_LOCAL_APP_DATA)
    assert path is not None
    assert path.is_absolute()
    assert path.is_dir()


@pytest.mark.windows_only
def test_unknown_folder_id():
    assert windows_known_folder(UNKNOWN_FOLDER_ID) is None


def test_none_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert windows_known_folder(FOLDERID_LOCAL_APP_DATA) is None
