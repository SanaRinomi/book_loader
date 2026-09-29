"""Fixtures for the Phase 0 characterization tests of the old code."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.builders.adobe_auth import AuthFolder, build_auth_folder

# Module globals that libadobe keeps between calls: file paths, cached keys and hooks.
_LIBADOBE_STATE = (
    "FILE_DEVICEKEY",
    "FILE_DEVICEXML",
    "FILE_ACTIVATIONXML",
    "devkey_bytes",
    "pkcs12",
    "VERBOSE",
    "_status_callback",
)


@pytest.fixture(autouse=True)
def _restore_libadobe_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo any change a test makes to libadobe's globals."""
    from book_loader.core.adobe import libadobe

    for name in _LIBADOBE_STATE:
        # Re-setting the current value registers it for restore; raising=False covers
        # globals that only exist after first use, which are then deleted again.
        monkeypatch.setattr(libadobe, name, getattr(libadobe, name, None), raising=False)


@pytest.fixture
def authorized(auth_dir: Path) -> AuthFolder:
    """``auth_dir`` holding a synthetic anonymous authorization."""
    return build_auth_folder(auth_dir)
