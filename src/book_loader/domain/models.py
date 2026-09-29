"""Value types shared by the services, the adapters and the CLI (REFACTOR_PLAN §4).

Every model is a frozen dataclass and checks its own invariants when it is built, so a
model that exists is valid. Timestamps must carry a time zone (§9.8), and titles and
names are stored in NFC (§10.4).

Models that hold a download URL or a key-bound license keep it out of ``repr``, so they
can be logged and shown in tracebacks. Turning a model into JSON is the CLI's job, and
it leaves those fields out too.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .conflicts import ConflictPolicy

__all__ = [
    "AdobeSource",
    "AuthInfo",
    "AuthType",
    "BatchItem",
    "BatchResult",
    "BookFile",
    "BookFiles",
    "BookFormat",
    "BookLoan",
    "BookMetadata",
    "BookRecord",
    "Identifier",
    "ItemStatus",
    "LoanRecord",
    "OutputSettings",
    "PendingRecord",
    "ProcessRequest",
    "ProcessResult",
    "StepResult",
    "StepStatus",
    "normalize_isbn",
]

PENDING_ID = re.compile(r"[0-9a-f]{16}")


def _require_aware(name: str, value: datetime | None) -> None:
    if value is not None and value.utcoffset() is None:
        raise ValueError(f"{name} must have a time zone: {value!r}")


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _nfc_or_none(text: str | None) -> str | None:
    return None if text is None else _nfc(text)


ISBN_PREFIX = re.compile(r"^\s*(?:urn:isbn:|isbn(?:-1[03])?:?)\s*", re.IGNORECASE)


def normalize_isbn(text: str) -> str | None:
    """The ISBN in ``text`` as bare digits (and a final ``X`` for ISBN-10), or None.

    Accepts the forms found in OPF files and shops: ``urn:isbn:`` and ``ISBN`` prefixes,
    hyphens and spaces. Returns None when the check digit is wrong or the text isn't an
    ISBN, so a reader can drop a bad value instead of failing.
    """
    digits = re.sub(r"[\s-]", "", ISBN_PREFIX.sub("", text)).upper()
    if re.fullmatch(r"\d{9}[\dX]", digits):
        values = [10 if c == "X" else int(c) for c in digits]
        total = sum(weight * value for weight, value in zip(range(10, 0, -1), values))
        return digits if total % 11 == 0 else None
    if re.fullmatch(r"97[89]\d{10}", digits):
        total = sum((3 if index % 2 else 1) * int(c) for index, c in enumerate(digits))
        return digits if total % 10 == 0 else None
    return None


# --- Authorization ---------------------------------------------------------------------


class AuthType(StrEnum):
    """What kind of Adobe authorization a folder holds."""

    ANONYMOUS = "anonymous"
    ADOBE_ID = "adobe_id"
    ADE_UNUSABLE = "ade_unusable"  # only ADE's activation.dat, which can't sign requests
    NONE = "none"
    UNKNOWN = "unknown"  # files exist, but activation.xml can't be read

    @property
    def usable(self) -> bool:
        return self in (AuthType.ANONYMOUS, AuthType.ADOBE_ID)


@dataclass(frozen=True)
class AuthInfo:
    """The state of one authorization folder."""

    type: AuthType
    folder: Path
    email: str | None = None
    device_uuid: str | None = None
    key_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.email is not None and self.type is not AuthType.ADOBE_ID:
            raise ValueError(f"an email only belongs to an Adobe ID authorization, not {self.type}")
        if self.type is AuthType.NONE and (self.device_uuid or self.key_fingerprint):
            raise ValueError("a folder without an authorization has no device or key")

    @property
    def usable(self) -> bool:
        return self.type.usable


# --- Processing an ACSM ----------------------------------------------------------------


class BookFormat(StrEnum):
    EPUB = "epub"
    PDF = "pdf"

    @property
    def suffix(self) -> str:
        return f".{self.value}"

    @classmethod
    def from_path(cls, path: Path) -> BookFormat:
        """The format named by a file's extension, in any case."""
        try:
            return cls(path.suffix.lower().removeprefix("."))
        except ValueError:
            raise ValueError(f"not an EPUB or PDF file name: {path.name}") from None


@dataclass(frozen=True)
class OutputSettings:
    """What ``process`` makes from a book. Pending records keep these for ``pending resume``."""

    to_pdf: bool = False
    convert_engine: str = "python"
    optimize: bool = False
    keep_encrypted: bool = False
    keep_epub: bool = False


@dataclass(frozen=True)
class ProcessRequest:
    """One ACSM to process. ``downloaded_file`` skips the download (``--downloaded-file``)."""

    acsm: Path
    output_dir: Path
    settings: OutputSettings = OutputSettings()
    downloaded_file: Path | None = None
    conflicts: ConflictPolicy = ConflictPolicy.ASK


class StepStatus(StrEnum):
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class StepResult:
    """How one pipeline step ended, for example ("Checking for auth", done, "Anonymous")."""

    name: str
    status: StepStatus
    detail: str = ""


@dataclass(frozen=True)
class ProcessResult:
    """What ``process`` produced. ``outputs[0]`` is the main output, in ``format``."""

    outputs: tuple[Path, ...]
    format: BookFormat
    size: int
    title: str | None = None
    loan_until: datetime | None = None
    acsm_expires: datetime | None = None
    steps: tuple[StepResult, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.outputs:
            raise ValueError("a finished process has at least one output")
        if BookFormat.from_path(self.outputs[0]) is not self.format:
            raise ValueError(f"the main output {self.outputs[0].name} is not {self.format}")
        if self.size < 0:
            raise ValueError("size can't be negative")
        _require_aware("loan_until", self.loan_until)
        _require_aware("acsm_expires", self.acsm_expires)

    @property
    def output(self) -> Path:
        return self.outputs[0]


# --- Batches ---------------------------------------------------------------------------


class ItemStatus(StrEnum):
    DONE = "done"
    SKIPPED = "skipped"
    PENDING = "pending"
    FAILED = "failed"


@dataclass(frozen=True)
class BatchItem:
    """One book of a batch. A failure has a reason; a pending book has its pending ID."""

    name: str
    status: ItemStatus
    outputs: tuple[Path, ...] = ()
    reason: str | None = None
    pending_id: str | None = None

    def __post_init__(self) -> None:
        if self.status is ItemStatus.DONE and not self.outputs:
            raise ValueError(f"{self.name}: a finished book has at least one output")
        if self.status is ItemStatus.FAILED and not self.reason:
            raise ValueError(f"{self.name}: a failed book needs a reason")
        if (self.status is ItemStatus.PENDING) != (self.pending_id is not None):
            raise ValueError(f"{self.name}: exactly the pending books have a pending ID")


@dataclass(frozen=True)
class BatchResult:
    """The books of a batch in the order they were handled.

    ``cancelled`` means the user stopped the batch; books after that point aren't listed.
    Cancelling alone doesn't fail the batch, as in 0.1.0's ``kobo dedrm``.
    """

    items: tuple[BatchItem, ...] = ()
    cancelled: bool = False

    def count(self, status: ItemStatus) -> int:
        return sum(1 for item in self.items if item.status is status)

    def with_status(self, status: ItemStatus) -> tuple[BatchItem, ...]:
        return tuple(item for item in self.items if item.status is status)

    @property
    def exit_code(self) -> int:
        """0 when nothing failed or is still pending, otherwise 1 (REFACTOR_PLAN §5.9)."""
        unfinished = self.count(ItemStatus.FAILED) + self.count(ItemStatus.PENDING)
        return 1 if unfinished else 0


# --- Stored records --------------------------------------------------------------------


@dataclass(frozen=True)
class BookFile:
    """One file of a book in ``book.json``. ``optimized`` is for EPUBs, ``engine`` for PDFs."""

    name: str
    sha256: str
    optimized: bool | None = None
    engine: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _nfc(self.name))


@dataclass(frozen=True)
class BookFiles:
    acsm: BookFile | None = None
    encrypted: BookFile | None = None
    epub: BookFile | None = None
    pdf: BookFile | None = None


@dataclass(frozen=True)
class AdobeSource:
    """Where an Adobe book came from. ``resource`` finds duplicates (REFACTOR_PLAN §10.4)."""

    resource: str
    acsm_sha256: str
    fulfilled_at: datetime
    auth_fingerprint: str

    def __post_init__(self) -> None:
        _require_aware("fulfilled_at", self.fulfilled_at)


@dataclass(frozen=True)
class BookLoan:
    """The loan part of ``book.json``; the full record is a ``LoanRecord``."""

    id: str
    valid_until: datetime
    returned: bool = False

    def __post_init__(self) -> None:
        _require_aware("valid_until", self.valid_until)


@dataclass(frozen=True)
class Identifier:
    """An identifier other than the ISBN, for example ("doi", "10.1000/182")."""

    scheme: str
    value: str


@dataclass(frozen=True)
class BookMetadata:
    """What is known about a book: from its OPF, the Kobo database, or added later.

    Only the title is required, so a field missing from the source can be filled in
    later with ``dataclasses.replace``. Text is stored in NFC. ``isbn`` holds a valid
    ISBN-10 or ISBN-13 as bare digits (see ``normalize_isbn``); other identifiers go in
    ``identifiers``.
    """

    title: str
    authors: tuple[str, ...] = ()
    publisher: str | None = None
    isbn: str | None = None
    language: str | None = None
    year: int | None = None
    series: str | None = None
    identifiers: tuple[Identifier, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _nfc(self.title))
        object.__setattr__(self, "authors", tuple(_nfc(author) for author in self.authors))
        object.__setattr__(self, "publisher", _nfc_or_none(self.publisher))
        object.__setattr__(self, "series", _nfc_or_none(self.series))
        object.__setattr__(self, "identifiers", tuple(self.identifiers))
        if self.isbn is not None:
            isbn = normalize_isbn(self.isbn)
            if isbn is None:
                raise ValueError(f"not a valid ISBN: {self.isbn!r}")
            object.__setattr__(self, "isbn", isbn)


@dataclass(frozen=True)
class BookRecord:
    """One book in a library: the contents of its ``book.json`` (REFACTOR_PLAN §10.4)."""

    metadata: BookMetadata
    source: str
    added_at: datetime
    updated_at: datetime
    adobe: AdobeSource | None = None
    loan: BookLoan | None = None
    files: BookFiles = BookFiles()
    version: int = 1

    def __post_init__(self) -> None:
        _require_aware("added_at", self.added_at)
        _require_aware("updated_at", self.updated_at)
        if self.updated_at < self.added_at:
            raise ValueError("updated_at can't be before added_at")
        if self.source == "adobe" and self.adobe is None:
            raise ValueError("an Adobe book records where it came from")


@dataclass(frozen=True)
class PendingRecord:
    """A fulfilled book whose download still has to be finished (REFACTOR_PLAN §9.5, §11.2).

    ``id`` is the first 16 hex characters of the ACSM's SHA-256. Records written by 0.1.0
    (version 0) have no ACSM contents, path, settings or folders. ``info`` is the vendored
    ``parse_fulfillment`` result; it holds the download URL and the license, so it and the
    ACSM contents stay out of ``repr``.
    """

    id: str
    saved_at: datetime
    acsm_name: str
    key_fingerprint: str
    info: Mapping[str, Any] = field(repr=False)
    link_page: Path | None = None
    title: str | None = None
    acsm_path: Path | None = None
    acsm_content: bytes | None = field(default=None, repr=False)
    acsm_expires: datetime | None = None
    library: Path | None = None
    output_dir: Path | None = None
    settings: OutputSettings | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not PENDING_ID.fullmatch(self.id):
            raise ValueError(f"a pending ID is 16 lower-case hex characters: {self.id!r}")
        _require_aware("saved_at", self.saved_at)
        _require_aware("acsm_expires", self.acsm_expires)
        if self.title is not None:
            object.__setattr__(self, "title", _nfc(self.title))

    @property
    def has_acsm(self) -> bool:
        """Whether the record can be fulfilled again without the ACSM file."""
        return self.acsm_content is not None

    def matches_key(self, fingerprint: str) -> bool:
        """Whether the license was issued to the authorization with this key."""
        return self.key_fingerprint == fingerprint


@dataclass(frozen=True)
class LoanRecord:
    """A borrowed book that can be returned early (REFACTOR_PLAN §9.7).

    ``loan_id``, ``user``, ``device``, ``operator_url`` and ``valid_until`` come from the
    vendored ``updateLoanReturnData``; the rest is added when the loan is recorded.
    """

    loan_id: str
    user: str
    device: str
    operator_url: str
    valid_until: datetime
    title: str
    borrowed_at: datetime
    library: Path | None = None
    files: tuple[Path, ...] = ()
    returned: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _nfc(self.title))
        _require_aware("valid_until", self.valid_until)
        _require_aware("borrowed_at", self.borrowed_at)

    def expired(self, now: datetime) -> bool:
        _require_aware("now", now)
        return now >= self.valid_until
