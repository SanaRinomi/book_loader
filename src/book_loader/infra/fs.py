"""File-system helpers: safe writes and moves, private folders, locked files, and the
per-run workspace (REFACTOR_PLAN §5.3, §7, §4.1).

- ``atomic_write`` writes to a temporary file in the same folder, then replaces the
  target, so a failed or interrupted write leaves the old file intact.
- ``unique_path`` finds a free name: ``Title (2).epub``, ``Title (3).epub``, …
- ``private_dir`` creates a folder for keys and licenses: mode ``0700`` on macOS and
  Linux; on Windows, where modes don't apply, it reports groups such as Everyone that
  were given access.
- ``retry_locked`` retries an operation that Windows refuses because another program
  (an ebook reader, antivirus, a sync tool) has the file open, then raises
  ``LockedError`` naming the file.
- ``Workspace`` is a hidden temporary folder inside the output folder. Everything a run
  writes goes there first; on exit, files matching its ``preserve`` rules are moved out
  and the folder is removed.
- ``safe_move`` renames within a drive and copies, checks and deletes across drives.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Literal, TypeVar, overload

from ..domain import events
from ..domain.errors import BookLoaderError, LockedError
from ..domain.events import Reporter

__all__ = [
    "Workspace",
    "atomic_write",
    "atomic_write_bytes",
    "atomic_write_text",
    "is_locked_error",
    "private_dir",
    "retry_locked",
    "safe_move",
    "unique_path",
    "windows_broad_access",
]

T = TypeVar("T")

LOCK_TIMEOUT = 2.0
WORKSPACE_PREFIX = ".book-loader-work-"

_WINDOWS_LOCKED = {5, 32, 33}  # access denied, sharing violation, lock violation
_ERROR_NOT_SAME_DEVICE = 17
_CHUNK = 1024 * 1024


# --- Locked files ----------------------------------------------------------------------


def is_locked_error(error: BaseException, platform: str = sys.platform) -> bool:
    """Whether ``error`` means another program has the file open (Windows only)."""
    if platform != "win32" or not isinstance(error, OSError):
        return False
    return getattr(error, "winerror", None) in _WINDOWS_LOCKED


def retry_locked(
    operation: Callable[[], T],
    path: Path,
    *,
    timeout: float = LOCK_TIMEOUT,
    platform: str = sys.platform,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Run ``operation``; on Windows, retry it while ``path`` is locked.

    Retries with a growing pause (50 ms up to 400 ms) for about ``timeout`` seconds,
    then raises ``LockedError`` naming ``path``. Other errors, and every error off
    Windows, are raised at once.
    """
    deadline = clock() + timeout
    pause = 0.05
    while True:
        try:
            return operation()
        except OSError as error:
            if not is_locked_error(error, platform) or clock() >= deadline:
                if is_locked_error(error, platform):
                    raise LockedError(path) from error
                raise
        sleep(min(pause, max(0.0, deadline - clock())))
        pause = min(pause * 2, 0.4)


# --- Writing ---------------------------------------------------------------------------


def _fsync_dir(folder: Path) -> None:
    # Makes the rename itself durable. Windows can't open a folder for this.
    if os.name != "posix":
        return
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@overload
def atomic_write(
    path: Path, mode: Literal["wb"] = "wb", *, file_mode: int | None = None
) -> contextlib.AbstractContextManager[IO[bytes]]: ...


@overload
def atomic_write(
    path: Path, mode: Literal["w"], *, file_mode: int | None = None, encoding: str = "utf-8"
) -> contextlib.AbstractContextManager[IO[str]]: ...


@contextlib.contextmanager
def atomic_write(
    path: Path, mode: str = "wb", *, file_mode: int | None = None, encoding: str = "utf-8"
) -> Iterator[IO]:
    """Write ``path`` all at once: to a temporary file next to it, then ``os.replace``.

    If the block raises, the temporary file is deleted and ``path`` is unchanged.
    ``file_mode`` (``0o600`` for key-holding files) is applied on macOS and Linux;
    without it an existing file keeps its mode. Replacing a file another program has
    open is retried (``retry_locked``).

    Text is written exactly as given: ``\\n`` stays ``\\n`` on Windows too, so settings
    and records are the same on every OS and a library can move between them.
    """
    if mode not in ("w", "wb"):
        raise ValueError(f"atomic_write writes new content; mode {mode!r} isn't supported")
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp = Path(temp_name)
    text = mode == "w"
    try:
        with open(
            fd, mode, encoding=encoding if text else None, newline="" if text else None
        ) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            if file_mode is None and path.exists():
                file_mode = path.stat().st_mode & 0o777
            if file_mode is not None:
                os.chmod(temp, file_mode)
        retry_locked(lambda: os.replace(temp, path), path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    _fsync_dir(path.parent)


def atomic_write_bytes(path: Path, data: bytes, *, file_mode: int | None = None) -> None:
    with atomic_write(path, "wb", file_mode=file_mode) as handle:
        handle.write(data)


def atomic_write_text(
    path: Path, text: str, *, file_mode: int | None = None, encoding: str = "utf-8"
) -> None:
    with atomic_write(path, "w", file_mode=file_mode, encoding=encoding) as handle:
        handle.write(text)


def unique_path(path: Path, exists: Callable[[Path], bool] = Path.exists) -> Path:
    """``path`` if it is free, otherwise ``Stem (2).ext``, ``Stem (3).ext``, …

    The number goes before the last extension (``Book.encrypted (2).epub``). The stem
    is shortened if needed so the name stays within 255 UTF-8 bytes.
    """
    if not exists(path):
        return path
    stem, suffix = path.stem, path.suffix
    number = 2
    while True:
        tag = f" ({number})"
        room = 255 - len((tag + suffix).encode("utf-8"))
        short = stem
        while len(short.encode("utf-8")) > room:
            short = short[:-1]
        candidate = path.with_name(f"{short}{tag}{suffix}")
        if not exists(candidate):
            return candidate
        number += 1


# --- Private folders -------------------------------------------------------------------

# Groups that must never get access to a folder holding keys (well-known SIDs, so the
# check doesn't depend on the language Windows is set to).
_BROAD_SIDS = {
    "S-1-1-0": "Everyone",
    "S-1-5-4": "Interactive users",  # anyone logged on to this machine
    "S-1-5-7": "Anonymous Logon",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-32-545": "Users",
    "S-1-5-32-546": "Guests",
}
_READ_OR_WRITE = 0x1 | 0x2 | 0x10000000 | 0x40000000 | 0x80000000  # data, GENERIC_*


def windows_broad_access(path: Path) -> list[str]:
    """The broad groups (Everyone, Users, …) that may read or write ``path``.

    Reads the folder's access list; changes nothing. Returns an empty list off Windows
    or when the list can't be read.
    """
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32")
    kernel32 = ctypes.WinDLL("kernel32")
    get_info = advapi32.GetNamedSecurityInfoW
    get_info.restype = wintypes.DWORD
    get_info.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_ace = advapi32.GetAce
    get_ace.restype = wintypes.BOOL
    get_ace.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    sid_to_string = advapi32.ConvertSidToStringSidW
    sid_to_string.restype = wintypes.BOOL
    sid_to_string.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    class ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", ctypes.c_ubyte),
            ("Sbz1", ctypes.c_ubyte),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class ACCESS_ALLOWED_ACE(ctypes.Structure):  # header, mask, then the SID
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    se_file_object, dacl_information = 1, 4
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = get_info(
        str(path),
        se_file_object,
        dacl_information,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0:
        return []
    found: list[str] = []
    try:
        if not dacl.value:  # a NULL DACL gives everyone full access
            return ["Everyone"]
        count = ACL.from_address(dacl.value).AceCount
        for index in range(count):
            ace_pointer = ctypes.c_void_p()
            if not get_ace(dacl, index, ctypes.byref(ace_pointer)) or not ace_pointer.value:
                continue
            ace = ACCESS_ALLOWED_ACE.from_address(ace_pointer.value)
            if ace.AceType != 0 or not ace.Mask & _READ_OR_WRITE:  # 0: access allowed
                continue
            sid = ace_pointer.value + ACCESS_ALLOWED_ACE.SidStart.offset
            text = wintypes.LPWSTR()
            if sid_to_string(sid, ctypes.byref(text)):
                try:
                    name = _BROAD_SIDS.get(text.value or "")
                finally:
                    kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
                if name and name not in found:
                    found.append(name)
    finally:
        kernel32.LocalFree(descriptor)
    return found


def private_dir(path: Path) -> list[str]:
    """Create ``path`` (and its parents) to hold keys or licenses; return warnings.

    On macOS and Linux the folder gets mode ``0700``, even if it already existed. On
    Windows modes don't apply; the user's profile folders already allow only that
    user, so this only checks that no broad group was given access, and returns a
    warning naming the groups if one was. Only code that writes calls this (§5.6).
    """
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
        return []
    groups = windows_broad_access(path)
    if groups:
        return [
            f"{path} can be read by {', '.join(groups)}. It holds private keys; "
            "remove those permissions in the folder's Security settings."
        ]
    return []


# --- Moving ----------------------------------------------------------------------------


def _is_cross_device(error: OSError) -> bool:
    return error.errno == errno.EXDEV or getattr(error, "winerror", None) == (
        _ERROR_NOT_SAME_DEVICE
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def safe_move(src: Path, dst: Path) -> Path:
    """Move the file ``src`` to ``dst``, replacing ``dst``; returns ``dst``.

    On the same drive this is one ``os.replace``. Across drives the file is copied to a
    temporary file next to ``dst``, flushed to disk, compared with the source by
    SHA-256, renamed into place, and only then is the source deleted. If the copy
    doesn't match, the source is kept and an error is raised.
    """
    try:
        retry_locked(lambda: os.replace(src, dst), dst)
    except OSError as error:
        if not _is_cross_device(error):
            raise
        _copy_move(src, dst)
    return dst


def _copy_move(src: Path, dst: Path) -> None:
    fd, temp_name = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with open(src, "rb") as source, open(fd, "wb") as target:
            shutil.copyfileobj(source, target, _CHUNK)
            target.flush()
            os.fsync(target.fileno())
        if _sha256(temp) != _sha256(src):
            raise BookLoaderError(
                f"Copying {src} to {dst} gave a different file",
                hint="The original was kept. Check the destination drive and try again.",
            )
        shutil.copystat(src, temp)
        retry_locked(lambda: os.replace(temp, dst), dst)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    _fsync_dir(dst.parent)
    retry_locked(src.unlink, src)


# --- Workspace -------------------------------------------------------------------------


def _hide(path: Path) -> None:
    """Set the hidden attribute on Windows; the leading dot hides it elsewhere."""
    if sys.platform != "win32":
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32")
    attributes = kernel32.GetFileAttributesW(str(path))
    if attributes != -1 and attributes != 0xFFFFFFFF:
        kernel32.SetFileAttributesW(str(path), attributes | 0x2)  # FILE_ATTRIBUTE_HIDDEN


def _remove_tree(path: Path) -> None:
    def clear_read_only(function: Callable[..., object], name: str, _: object) -> None:
        # Windows refuses to delete read-only files; clear the flag and try once more.
        os.chmod(name, 0o700)
        function(name)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=clear_read_only)
    else:
        shutil.rmtree(path, onerror=clear_read_only)


@dataclass(frozen=True)
class _Rule:
    pattern: str
    destination: Callable[[Path], Path | None]


class Workspace:
    """A hidden temporary folder in ``parent`` for one run (REFACTOR_PLAN §5.3).

    Being in the output folder, it is on the same drive, so moving results out is a
    rename. On exit, whether the run succeeded or not:

    1. each ``preserve`` rule moves its matching files to the path its ``destination``
       gives (None keeps a file in the workspace, so it is deleted). A taken name gets
       a number (``unique_path``). ``preserved`` maps each file's workspace path to
       where it went, so an error message can point at the new place;
    2. the folder is removed. A file another program keeps open is retried for a
       while, then left behind with a warning instead of an error.

    Warnings are collected in ``warnings`` and also sent to ``reporter`` if given.
    """

    def __init__(
        self,
        parent: Path,
        *,
        reporter: Reporter | None = None,
        lock_timeout: float = LOCK_TIMEOUT,
    ) -> None:
        self.parent = parent
        self.reporter = reporter
        self.lock_timeout = lock_timeout
        self.warnings: list[str] = []
        self.preserved: dict[Path, Path] = {}
        self._rules: list[_Rule] = []
        self._path: Path | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            raise RuntimeError("the workspace is only available inside its with block")
        return self._path

    def preserve(self, pattern: str, destination: Callable[[Path], Path | None]) -> None:
        """Keep files matching ``pattern`` (a glob, searched in every subfolder) by
        moving them to ``destination(file)`` when the workspace closes."""
        self._rules.append(_Rule(pattern, destination))

    def __enter__(self) -> Workspace:
        self.parent.mkdir(parents=True, exist_ok=True)
        self._path = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX, dir=self.parent))
        _hide(self._path)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        path = self.path
        try:
            self._run_rules(path)
        finally:
            try:
                retry_locked(lambda: _remove_tree(path), path, timeout=self.lock_timeout)
            except (LockedError, OSError) as error:
                self._warn(
                    f"The temporary folder {path} couldn't be removed ({_reason(error)}); "
                    "delete it once the program using it is closed."
                )
            self._path = None

    def _run_rules(self, path: Path) -> None:
        for rule in self._rules:
            for found in sorted(path.rglob(rule.pattern)):
                if not found.is_file() or found in self.preserved:
                    continue
                target = rule.destination(found)
                if target is None:
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    self.preserved[found] = safe_move(found, unique_path(target))
                except (BookLoaderError, OSError) as error:
                    self._warn(f"{found.name} couldn't be kept at {target}: {_reason(error)}")

    def _warn(self, message: str) -> None:
        self.warnings.append(message)
        if self.reporter is not None:
            self.reporter.emit(events.Warning(message))


def _reason(error: BaseException) -> str:
    if isinstance(error, BookLoaderError):
        return error.message
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return str(error)
