"""Folders the operating system knows about (REFACTOR_PLAN §4.1, §9.4).

On Windows, known folders such as Local AppData and Downloads can be moved by the user
or by policy, so they are asked from the shell with ``SHGetKnownFolderPath`` instead of
being built from the home folder. T2.13 adds the Downloads lookup for every OS.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

__all__ = ["FOLDERID_LOCAL_APP_DATA", "windows_known_folder"]

FOLDERID_LOCAL_APP_DATA = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"


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
