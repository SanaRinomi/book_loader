"""Folders the operating system knows about (REFACTOR_PLAN §4.1, §9.4).

On Windows, known folders such as Local AppData and Downloads can be moved by the user
or by policy, so they are asked from the shell with ``SHGetKnownFolderPath`` instead of
being built from the home folder.

``downloads_dir`` finds the Downloads folder, first match wins:

1. the override from settings (``[downloads] dir``)
2. Windows: the Known Folder API (``FOLDERID_Downloads``)
3. Linux: ``XDG_DOWNLOAD_DIR`` in ``user-dirs.dirs`` (in ``$XDG_CONFIG_HOME``, by default
   ``~/.config``)
4. ``~/Downloads`` (macOS, and whenever the above find nothing)

The folder isn't checked or created; whoever watches it does that.
"""

from __future__ import annotations

import re
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # paths.py imports this module, so Host is only needed for typing
    from .paths import Host

__all__ = [
    "FOLDERID_DOWNLOADS",
    "FOLDERID_LOCAL_APP_DATA",
    "DownloadsDir",
    "downloads_dir",
    "parse_user_dirs",
    "windows_known_folder",
]

FOLDERID_LOCAL_APP_DATA = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"
FOLDERID_DOWNLOADS = "374DE290-123F-4565-9164-39C4925E467B"

_USER_DIR_LINE = re.compile(
    r'^\s*(?:export\s+)?(XDG_[A-Z]+_DIR)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|(\S+))\s*(?:#.*)?$'
)


def windows_known_folder(folder_id: str) -> Path | None:
    """The path of a Windows known folder, or None when it can't be found.

    ``folder_id`` is a ``FOLDERID_*`` GUID. Off Windows this always returns None. The
    folder is never created (``KF_FLAG_DEFAULT``).
    """
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    guid = GUID.from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    get_path = shell32.SHGetKnownFolderPath
    get_path.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    get_path.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    buffer = ctypes.c_wchar_p()
    result = get_path(ctypes.byref(guid), 0, None, ctypes.byref(buffer))
    try:
        if result != 0 or not buffer.value:
            return None
        return Path(buffer.value)
    finally:
        # The shell allocates the string even on some failures; freeing NULL is allowed.
        ole32.CoTaskMemFree(ctypes.cast(buffer, ctypes.c_void_p))


# --- Downloads -------------------------------------------------------------------------


@dataclass(frozen=True)
class DownloadsDir:
    """The Downloads folder and where it came from: ``"override"``, ``"known folder"``,
    ``"user-dirs.dirs"`` or ``"default"``."""

    path: Path
    source: str


def parse_user_dirs(text: str, home: Path) -> dict[str, Path]:
    """The folders in an XDG ``user-dirs.dirs`` file, by variable name.

    Values are ``"$HOME/…"`` or an absolute path, as ``xdg-user-dirs-update`` writes
    them; anything else (a relative path, another variable) is skipped. ``$HOME`` or
    ``${HOME}`` alone means the folder is turned off, and is skipped too.
    """
    folders: dict[str, Path] = {}
    for line in text.splitlines():
        match = _USER_DIR_LINE.match(line)
        if not match:
            continue
        name, quoted, bare = match.groups()
        value = re.sub(r"\\(.)", r"\1", quoted) if quoted is not None else bare
        for prefix in ("$HOME", "${HOME}"):
            if value == prefix or value.startswith(prefix + "/"):
                rest = value[len(prefix) :].strip("/")
                if rest:
                    folders[name] = home.joinpath(*rest.split("/"))
                break
        else:
            if value.startswith("/"):
                folders[name] = Path(value)
    return folders


def _expand_home(value: Path, home: Path) -> Path:
    parts = value.parts
    if parts and parts[0] == "~":
        return home.joinpath(*parts[1:])
    return value


def downloads_dir(
    host: Host,
    override: Path | None = None,
    *,
    known_folder: Callable[[str], Path | None] = windows_known_folder,
    read_text: Callable[[Path], str] = lambda path: path.read_text(encoding="utf-8"),
) -> DownloadsDir:
    """The Downloads folder for ``host`` (see the module docstring for the order)."""
    if override is not None:
        return DownloadsDir(_expand_home(override, host.home), "override")
    system = str(host.os)
    if system == "windows":
        found = known_folder(FOLDERID_DOWNLOADS)
        if found is not None:
            return DownloadsDir(found, "known folder")
    elif system == "linux":
        # A POSIX rule, so it also holds when Linux is tested on another OS.
        config = host.env.get("XDG_CONFIG_HOME")
        absolute = config and PurePosixPath(config).is_absolute()
        base = Path(config) if config and absolute else host.home / ".config"
        try:
            folders = parse_user_dirs(read_text(base / "user-dirs.dirs"), host.home)
        except (OSError, UnicodeDecodeError):
            folders = {}
        if "XDG_DOWNLOAD_DIR" in folders:
            return DownloadsDir(folders["XDG_DOWNLOAD_DIR"], "user-dirs.dirs")
    return DownloadsDir(host.home / "Downloads", "default")
