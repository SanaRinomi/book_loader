"""The backup archive format (REFACTOR_PLAN §10.7, decision 10).

**Layout.** A backup is a tar stream, compressed with gzip (or xz on request), whose
first member is ``manifest.json``. The manifest lists every other file with its size
and SHA-256, next to the caller's metadata (parts, books, auth type and fingerprint,
never a key), so it can be read without reading the rest. Without encryption the
file is an ordinary ``.tar.gz`` / ``.tar.xz`` that any tar tool opens.

**Encryption.** An encrypted backup starts with a header, then the compressed tar
stream in chunks:

=========  =====  ==============================================================
Field      Bytes  Contents
=========  =====  ==============================================================
magic      8      ``BLBACKUP``
version    1      1
scrypt     3      log2(N), r, p
salt       16     random
nonce      7      random prefix of every chunk's nonce
chunk      4      plaintext bytes per chunk (1 MiB), big-endian
check      16     derived from the key: tells a wrong passphrase from damage
digest     8      SHA-256 of all the above: tells a damaged header from the rest
=========  =====  ==============================================================

The passphrase (in NFC) gives a key through scrypt. Each chunk is AES-256-GCM with
its own 16-byte tag. Its nonce is the prefix, the chunk's index and a final-chunk
flag, and the header is authenticated with every chunk, so reordered, removed or
added chunks, a cut-off end, and a changed header all fail to decrypt.

**Extraction** never trusts the archive: only regular files are written (no links or
devices), names are checked with ``tarfile.data_filter`` and rejected when absolute or
leading outside, every part of a name follows ``names.library_component`` (so NFD
names, characters Windows rejects and reserved names are fixed, and the new name is
reported), names that differ only by case get a number, nothing is overwritten, and
every file's SHA-256 is checked against the manifest.

**Original archives.** 0.1.0 wrote plain ``.tar.gz`` files holding one folder (named
like the auth folder, usually ``.adobe``). They have no manifest; the reader
recognises them by that single top folder and can extract its contents.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import lzma
import os
import re
import struct
import tarfile
import unicodedata
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import IO, Any, BinaryIO

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import scrypt

from ..domain.errors import ArchiveError
from .fs import atomic_write
from .names import library_component
from .secrets import Secret

__all__ = [
    "MANIFEST_NAME",
    "ArchiveKind",
    "ArchiveReader",
    "Entry",
    "ExtractResult",
    "FileRecord",
    "Manifest",
    "ScryptParams",
    "detect",
    "write_archive",
]

MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = "book-loader-archive"
MANIFEST_VERSION = 1

MAGIC = b"BLBACKUP"
VERSION = 1
CHUNK_SIZE = 1 << 20
TAG_SIZE = 16
_HEAD = struct.Struct(">8sBBBB16s7sI")  # magic .. chunk size: authenticated with every chunk
_HEADER_SIZE = _HEAD.size + 16 + 8
_GZIP_MAGIC = b"\x1f\x8b"
_XZ_MAGIC = b"\xfd7zXZ\x00"
_COPY = 1 << 16

DAMAGED_HINT = "The file is damaged or incomplete. Try another copy of the backup."


# --- Types -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ScryptParams:
    """How hard the passphrase is to guess: N = 2**log2_n, r and p (RFC 7914)."""

    log2_n: int = 17
    r: int = 8
    p: int = 1

    # A header asking for more than this is refused, so a crafted file can't make the
    # reader allocate gigabytes.
    MAX_LOG2_N = 20
    MAX_R = 16
    MAX_P = 4

    def check(self) -> None:
        if not (
            10 <= self.log2_n <= self.MAX_LOG2_N
            and 1 <= self.r <= self.MAX_R
            and 1 <= self.p <= self.MAX_P
        ):
            raise ArchiveError(
                f"Unsupported encryption settings (N=2^{self.log2_n}, r={self.r}, p={self.p})",
                hint="The backup was made by a newer or unknown program.",
            )


class ArchiveKind(StrEnum):
    ENCRYPTED = "encrypted"
    TAR_GZ = "tar.gz"
    TAR_XZ = "tar.xz"


@dataclass(frozen=True)
class Entry:
    """A file to put in an archive: its name there (``/``-separated) and its content."""

    name: str
    source: Path | bytes


@dataclass(frozen=True)
class FileRecord:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    """``manifest.json``: the caller's ``metadata`` plus the archive's own records."""

    created: datetime
    files: tuple[FileRecord, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    version: int = MANIFEST_VERSION

    def file(self, name: str) -> FileRecord | None:
        return next((record for record in self.files if record.name == name), None)

    def to_json(self) -> bytes:
        data = {
            "format": MANIFEST_FORMAT,
            "version": self.version,
            "created": self.created.isoformat(),
            "metadata": dict(self.metadata),
            "files": [{"name": f.name, "size": f.size, "sha256": f.sha256} for f in self.files],
        }
        return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> Manifest:
        try:
            data = json.loads(raw.decode("utf-8"))
            if data.get("format") != MANIFEST_FORMAT:
                raise ValueError("not a book-loader manifest")
            version = int(data["version"])
            files = tuple(
                FileRecord(str(f["name"]), int(f["size"]), str(f["sha256"])) for f in data["files"]
            )
            created = datetime.fromisoformat(data["created"])
            metadata = dict(data.get("metadata", {}))
        except (ValueError, KeyError, TypeError) as error:
            raise ArchiveError(
                f"The backup's manifest can't be read: {error}", DAMAGED_HINT
            ) from error
        if version > MANIFEST_VERSION:
            raise ArchiveError(
                f"The backup was made by a newer book-loader (format {version})",
                hint="Update book-loader to restore it.",
            )
        return cls(created, files, metadata, version)


@dataclass
class ExtractResult:
    """What ``extract`` wrote: archive name -> file on disk. ``renamed`` holds the
    entries whose name had to change (see the module docstring)."""

    files: dict[str, Path] = field(default_factory=dict)
    renamed: dict[str, Path] = field(default_factory=dict)


# --- Encryption ------------------------------------------------------------------------


def _keys(passphrase: Secret, salt: bytes, params: ScryptParams) -> tuple[bytes, bytes]:
    text = unicodedata.normalize("NFC", passphrase.reveal()).encode("utf-8")
    master = scrypt(text, salt, 32, 2**params.log2_n, params.r, params.p)  # type: ignore[arg-type]
    assert isinstance(master, bytes)
    encryption = hmac.new(master, b"book-loader archive encryption", hashlib.sha256).digest()
    check = hmac.new(master, b"book-loader archive key check", hashlib.sha256).digest()
    return encryption, check


def _nonce(prefix: bytes, index: int, final: bool) -> bytes:
    return prefix + index.to_bytes(4, "big") + (b"\x01" if final else b"\x00")


class _EncryptingWriter:
    """Writes the header, then the data in authenticated chunks. ``finish`` writes the
    final chunk (which may be empty) but leaves ``target`` open. (Not an ``IOBase``:
    that would write a final chunk on garbage collection after a failed backup.)"""

    def __init__(self, target: Any, passphrase: Secret, params: ScryptParams) -> None:
        params.check()
        salt, prefix = os.urandom(16), os.urandom(7)
        head = _HEAD.pack(
            MAGIC, VERSION, params.log2_n, params.r, params.p, salt, prefix, CHUNK_SIZE
        )
        self._key, check_key = _keys(passphrase, salt, params)
        check = hmac.new(check_key, head, hashlib.sha256).digest()[:16]
        digest = hashlib.sha256(head + check).digest()[:8]
        target.write(head + check + digest)
        self._target = target
        self._head = head
        self._prefix = prefix
        self._buffer = bytearray()
        self._index = 0
        self._finished = False

    def write(self, data: Any) -> int:
        view = memoryview(data).cast("B")
        self._buffer += view
        # Keep at least one byte back: only close() knows which chunk is the last.
        while len(self._buffer) > CHUNK_SIZE:
            self._emit(bytes(self._buffer[:CHUNK_SIZE]), final=False)
            del self._buffer[:CHUNK_SIZE]
        return len(view)

    def _emit(self, chunk: bytes, final: bool) -> None:
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=_nonce(self._prefix, self._index, final))
        cipher.update(self._head)
        encrypted, tag = cipher.encrypt_and_digest(chunk)
        self._target.write(encrypted + tag)
        self._index += 1

    def finish(self) -> None:
        if not self._finished:
            self._emit(bytes(self._buffer), final=True)
            self._buffer.clear()
            self._finished = True


_Derive = Callable[[bytes, ScryptParams], tuple[bytes, bytes]]


class _DecryptingReader(io.RawIOBase):
    """Reads an encrypted archive's data, checking every chunk."""

    def __init__(self, source: BinaryIO, derive: _Derive) -> None:
        header = source.read(_HEADER_SIZE)
        if len(header) < _HEADER_SIZE or not header.startswith(MAGIC):
            raise ArchiveError("This isn't an encrypted book-loader backup", DAMAGED_HINT)
        head, check, digest = header[: _HEAD.size], header[_HEAD.size : -8], header[-8:]
        if hashlib.sha256(head + check).digest()[:8] != digest:
            raise ArchiveError("The backup's header is damaged", DAMAGED_HINT)
        _, version, log2_n, r, p, salt, prefix, chunk_size = _HEAD.unpack(head)
        if version != VERSION:
            raise ArchiveError(
                f"The backup uses encryption format {version}, which this book-loader "
                "doesn't know",
                hint="Update book-loader to restore it.",
            )
        params = ScryptParams(log2_n, r, p)
        params.check()
        if not 1 <= chunk_size <= 16 * CHUNK_SIZE:
            raise ArchiveError("The backup's header is damaged", DAMAGED_HINT)
        self._key, check_key = derive(salt, params)
        expected = hmac.new(check_key, head, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(expected, check):
            raise ArchiveError(
                "Wrong passphrase for this backup",
                hint="Check the passphrase and try again.",
            )
        self._source = source
        self._head = head
        self._prefix = prefix
        self._block = chunk_size + TAG_SIZE
        self._index = 0
        self._pending = source.read(self._block)
        self._buffer = bytearray()
        self._done = False

    def readable(self) -> bool:
        return True

    def _next_chunk(self) -> None:
        block = self._pending
        self._pending = self._source.read(self._block)
        final = not self._pending
        if len(block) < TAG_SIZE:
            raise ArchiveError("The backup ends too early", DAMAGED_HINT)
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=_nonce(self._prefix, self._index, final))
        cipher.update(self._head)
        try:
            data = cipher.decrypt_and_verify(block[:-TAG_SIZE], block[-TAG_SIZE:])
        except ValueError:
            where = "ends too early" if final else f"is damaged (at chunk {self._index})"
            raise ArchiveError(f"The backup {where}", DAMAGED_HINT) from None
        self._buffer += data
        self._index += 1
        self._done = final

    def readinto(self, target: Any) -> int:
        view = memoryview(target).cast("B")
        while not self._buffer and not self._done:
            self._next_chunk()
        count = min(len(view), len(self._buffer))
        view[:count] = self._buffer[:count]
        del self._buffer[:count]
        return count


# --- Detecting and writing -------------------------------------------------------------


def detect(path: Path) -> ArchiveKind:
    """What kind of archive ``path`` is, from its first bytes."""
    with open(path, "rb") as handle:
        start = handle.read(len(MAGIC))
    if start.startswith(MAGIC):
        return ArchiveKind.ENCRYPTED
    if start.startswith(_GZIP_MAGIC):
        return ArchiveKind.TAR_GZ
    if start.startswith(_XZ_MAGIC):
        return ArchiveKind.TAR_XZ
    raise ArchiveError(
        f"{path.name} isn't a book-loader backup",
        hint="Backups are .blbackup files, or .tar.gz files from book-loader 0.1.0.",
    )


def _check_name(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or ".." in path.parts
        or ":" in path.parts[0]
        or str(path) != name
    ):
        raise ValueError(f"not a relative archive name: {name!r}")
    if name == MANIFEST_NAME:
        raise ValueError(f"{MANIFEST_NAME} is written by the archive itself")
    return name


def _hash_source(source: Path | bytes) -> tuple[int, str]:
    if isinstance(source, bytes):
        return len(source), hashlib.sha256(source).hexdigest()
    digest = hashlib.sha256()
    size = 0
    with open(source, "rb") as handle:
        while chunk := handle.read(_COPY):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


class _Counter:
    """Passes writes on and reports the running total."""

    def __init__(self, target: Any, progress: Callable[[int], None]) -> None:
        self._target = target
        self._progress = progress
        self.count = 0

    def write(self, data: Any) -> int:
        written = self._target.write(data)
        self.count += len(memoryview(data))
        self._progress(self.count)
        return written if written is not None else len(memoryview(data))


def write_archive(
    dest: Path,
    entries: Iterable[Entry],
    metadata: Mapping[str, Any] | None = None,
    *,
    compression: str = "gz",
    passphrase: Secret | None = None,
    scrypt_params: ScryptParams = ScryptParams(),
    progress: Callable[[int], None] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Manifest:
    """Write a backup to ``dest``: encrypted when ``passphrase`` is given.

    The archive is written to a temporary file next to ``dest`` and renamed at the end,
    with mode ``0600``, so a failed backup never leaves a partial file under the final
    name. ``progress`` receives the number of bytes written so far.
    """
    if compression not in ("gz", "xz"):
        raise ValueError(f"compression must be 'gz' or 'xz', not {compression!r}")
    entries = list(entries)
    seen: set[str] = set()
    records = []
    for entry in entries:
        name = _check_name(entry.name)
        if name.casefold() in seen:
            raise ValueError(f"{name!r} is in the archive twice (names are compared without case)")
        seen.add(name.casefold())
        records.append(FileRecord(name, *_hash_source(entry.source)))
    manifest = Manifest(now(), tuple(records), dict(metadata or {}))
    created = manifest.created.timestamp()

    with atomic_write(dest, "wb", file_mode=0o600) as handle:
        target: Any = handle
        if progress is not None:
            target = _Counter(target, progress)
        encrypting = None
        if passphrase is not None:
            encrypting = _EncryptingWriter(target, passphrase, scrypt_params)
            target = encrypting
        # typeshed lists only "w|gz" and "w|bz2" for stream mode; "w|xz" works too.
        mode: Any = f"w|{compression}"
        with tarfile.open(fileobj=target, mode=mode, format=tarfile.PAX_FORMAT) as tar:
            _add(tar, MANIFEST_NAME, manifest.to_json(), created)
            for entry in entries:
                _add(tar, entry.name, entry.source, created)
        if encrypting is not None:
            encrypting.finish()
    return manifest


def _add(tar: tarfile.TarFile, name: str, source: Path | bytes, mtime: float) -> None:
    info = tarfile.TarInfo(name)
    info.mode = 0o600
    if isinstance(source, bytes):
        info.size = len(source)
        info.mtime = int(mtime)
        tar.addfile(info, io.BytesIO(source))
        return
    stat = source.stat()
    info.size = stat.st_size
    info.mtime = int(stat.st_mtime)
    with open(source, "rb") as handle:
        tar.addfile(info, handle)


# --- Reading ---------------------------------------------------------------------------


class ArchiveReader:
    """Reads a backup: a new one (with a manifest, maybe encrypted) or a 0.1.0 archive.

    Each method reads the file from the start, as a stream. ``passphrase`` is needed
    only when ``kind`` is ``ENCRYPTED``.
    """

    def __init__(self, path: Path, passphrase: Secret | None = None) -> None:
        self.path = path
        self.kind = detect(path)
        self.passphrase = passphrase
        self._keys: dict[tuple[bytes, ScryptParams], tuple[bytes, bytes]] = {}

    @property
    def encrypted(self) -> bool:
        return self.kind is ArchiveKind.ENCRYPTED

    def _derive(self, salt: bytes, params: ScryptParams) -> tuple[bytes, bytes]:
        # scrypt is slow on purpose; every read of this file shares one derivation.
        assert self.passphrase is not None
        if (salt, params) not in self._keys:
            self._keys[salt, params] = _keys(self.passphrase, salt, params)
        return self._keys[salt, params]

    @contextmanager
    def _open(self) -> Iterator[tarfile.TarFile]:
        """The archive as a tar stream. Errors while reading it become ``ArchiveError``;
        the caller turns its own errors (writing files) into clear ones itself."""
        with open(self.path, "rb") as raw:
            stream: IO[bytes] = raw
            if self.encrypted:
                if self.passphrase is None:
                    raise ArchiveError(
                        f"{self.path.name} is encrypted",
                        hint="Give its passphrase with --passphrase-file, --passphrase-stdin "
                        "or BOOK_LOADER_BACKUP_PASSPHRASE.",
                    )
                stream = io.BufferedReader(_DecryptingReader(raw, self._derive), _COPY)
            try:
                with tarfile.open(fileobj=stream, mode="r|*") as tar:
                    yield tar
            except ArchiveError:
                raise
            except (tarfile.TarError, EOFError, zlib.error, lzma.LZMAError) as error:
                raise ArchiveError(f"The backup can't be read: {error}", DAMAGED_HINT) from error

    def read_manifest(self) -> Manifest | None:
        """The manifest, reading only as far as the first member; None for an original
        (0.1.0) archive, which has none."""
        with self._open() as tar:
            first = tar.next()
            if first is None:
                raise ArchiveError("The backup is empty", DAMAGED_HINT)
            if first.name != MANIFEST_NAME or not first.isfile():
                return None
            handle = tar.extractfile(first)
            assert handle is not None
            return Manifest.from_json(handle.read())

    def names(self) -> list[str]:
        """Every member's name, in archive order (without the manifest)."""
        with self._open() as tar:
            return [m.name for m in tar if m.name != MANIFEST_NAME]

    def legacy_root(self) -> str:
        """The single top folder of an original (0.1.0) archive."""
        tops = {PurePosixPath(name).parts[0] for name in self.names() if name.strip("/")}
        if len(tops) != 1:
            raise ArchiveError(
                f"{self.path.name} isn't a book-loader backup: it doesn't hold one folder",
                hint="Backups from book-loader 0.1.0 hold a single folder, usually .adobe.",
            )
        return tops.pop()

    def extract(
        self,
        dest: Path,
        select: Callable[[str], bool] | None = None,
    ) -> ExtractResult:
        """Write the archive's files (those ``select`` accepts) under ``dest``.

        For an original (0.1.0) archive the top folder is left out, so its contents
        land directly in ``dest``, whatever the folder was called. Names are the
        archive names; ``select`` sees the same names. See the module docstring for
        what is checked. Raises ``ArchiveError`` on the first problem; files written
        until then stay, so extract into a fresh folder (a ``Workspace``).
        """
        manifest = self.read_manifest()
        root = self.legacy_root() if manifest is None else None
        dest.mkdir(parents=True, exist_ok=True)
        result = ExtractResult()
        taken: set[str] = set()
        with self._open() as tar:
            for member in tar:
                if manifest is not None and member.name == MANIFEST_NAME:
                    continue
                _refuse_unsafe(member.name)
                name = self._archive_name(member, root)
                if name is None or member.isdir():
                    continue
                if not member.isfile():
                    raise ArchiveError(
                        f"The backup holds {member.name!r}, which is a link or a special "
                        "file; it wasn't restored",
                        hint="Backups made by book-loader hold only regular files.",
                    )
                try:
                    tarfile.data_filter(member, str(dest))
                except tarfile.FilterError as error:
                    raise ArchiveError(
                        f"The backup holds an unsafe name: {member.name!r}",
                        hint="Backups made by book-loader never do; the file may have "
                        "been tampered with.",
                    ) from error
                if select is not None and not select(name):
                    continue
                record = manifest.file(name) if manifest is not None else None
                if manifest is not None and record is None:
                    raise ArchiveError(f"{name!r} isn't listed in the manifest", DAMAGED_HINT)
                target = self._target(dest, name, taken)
                if target.relative_to(dest).as_posix() != name:
                    result.renamed[name] = target
                source = tar.extractfile(member)
                assert source is not None
                try:
                    _write_checked(source, target, record)
                except OSError as error:
                    raise ArchiveError(
                        f"Can't write {target}: {error.strerror or error}",
                        hint="Check that the folder is writable and the drive has space.",
                    ) from error
                result.files[name] = target
        if manifest is not None:
            missing = [
                f.name
                for f in manifest.files
                if f.name not in result.files and (select is None or select(f.name))
            ]
            if missing:
                raise ArchiveError(
                    f"The backup is missing {len(missing)} file(s) its manifest lists, "
                    f"such as {missing[0]!r}",
                    DAMAGED_HINT,
                )
        return result

    @staticmethod
    def _archive_name(member: tarfile.TarInfo, root: str | None) -> str | None:
        name = member.name.rstrip("/")
        if root is None:
            return name
        parts = PurePosixPath(name).parts
        if len(parts) <= 1:
            return None  # the top folder itself
        return PurePosixPath(*parts[1:]).as_posix()

    @staticmethod
    def _target(dest: Path, name: str, taken: set[str]) -> Path:
        parts = PurePosixPath(name).parts
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise ArchiveError(f"The backup holds an unsafe name: {name!r}", DAMAGED_HINT)
        safe = [library_component(part) for part in parts]
        candidate = "/".join(safe)
        if candidate.casefold() in taken:
            stem, dot, suffix = safe[-1].rpartition(".")
            if not dot:
                stem, suffix = safe[-1], ""
            number = 2
            while candidate.casefold() in taken:
                last = library_component(f"{stem} ({number}){'.' + suffix if dot else ''}")
                candidate = "/".join([*safe[:-1], last])
                number += 1
        taken.add(candidate.casefold())
        target = dest.joinpath(*candidate.split("/"))
        if target.exists():
            raise ArchiveError(
                f"Restoring {name!r} would overwrite {target}",
                hint="Restore into an empty folder.",
            )
        return target


_DRIVE = re.compile(r"^[A-Za-z]:")


def _refuse_unsafe(name: str) -> None:
    """Absolute names, drive letters, backslashes and ``..`` never come from book-loader.

    ``tarfile.data_filter`` would quietly strip a leading ``/``; a backup that holds one
    was not made by book-loader, so it is refused instead.
    """
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or name.startswith("\\")
        or "\\" in name
        or _DRIVE.match(name)
        or ".." in path.parts
    ):
        raise ArchiveError(
            f"The backup holds an unsafe name: {name!r}",
            hint="Backups made by book-loader never do; the file may have been tampered with.",
        )


def _write_checked(source: IO[bytes], target: Path, record: FileRecord | None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with open(target, "xb") as handle:
            while chunk := source.read(_COPY):
                digest.update(chunk)
                size += len(chunk)
                handle.write(chunk)
        if record is not None and (size != record.size or digest.hexdigest() != record.sha256):
            raise ArchiveError(f"{record.name!r} doesn't match the backup's manifest", DAMAGED_HINT)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
