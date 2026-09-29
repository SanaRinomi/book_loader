"""Which files a ``process`` run keeps (REFACTOR_PLAN §7, decisions 3, 12 and 13).

book-loader never overwrites, changes or deletes a file the user gave it. It deletes
only files it made itself, once they aren't needed. ``RetentionPolicy`` says which of
those it keeps:

=========================  ===========================  ==================================
File                       Plain mode                   Library mode
=========================  ===========================  ==================================
ACSM                       left where it is             copied into the book folder;
                                                        moved from ``Inbox/`` or with
                                                        ``--move-acsm``
Licensed encrypted book    kept with --keep-encrypted   kept (``[keep] encrypted``)
Decrypted EPUB, when       kept with --keep-epub        kept when ``"epub"`` is in
converting to PDF                                       ``[output] formats``
=========================  ===========================  ==================================

The files the policy doesn't cover are handled the same way every time: the final
output is always written (the conflict policy applies), a download link page is
deleted when its book finishes, diagnostic pages go to the logs folder, and the
workspace is always removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .errors import ConfigError
from .models import BookFormat

__all__ = ["AcsmHandling", "LibraryRetention", "RetentionPolicy"]


class AcsmHandling(StrEnum):
    LEAVE = "leave"
    COPY = "copy"  # into the book folder
    MOVE = "move"  # into the book folder


@dataclass(frozen=True)
class LibraryRetention:
    """The parts of ``library.toml`` that decide retention, with their defaults (§10.2)."""

    formats: frozenset[BookFormat] = frozenset({BookFormat.EPUB})
    keep_acsm: bool = True
    keep_encrypted: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "formats", frozenset(self.formats))
        if not self.formats:
            raise ConfigError("[output] formats is empty", hint='List "epub", "pdf" or both.')


@dataclass(frozen=True)
class RetentionPolicy:
    """What one run keeps.

    ``formats`` are the DRM-free formats kept for an EPUB book: PDF in it means the
    book is converted, EPUB in it means the decrypted EPUB is kept. A PDF book is kept
    as its PDF whatever ``formats`` says (see ``outputs``).

    ``keep_epub_notice`` asks the CLI to say, for one release, that ``--keep-encrypted``
    no longer keeps the decrypted EPUB when converting to PDF (§7, §11.2).
    """

    acsm: AcsmHandling
    keep_encrypted: bool
    formats: frozenset[BookFormat]
    keep_epub_notice: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "formats", frozenset(self.formats))
        if not self.formats:
            raise ValueError("a run keeps at least one format")

    @property
    def converts_to_pdf(self) -> bool:
        return BookFormat.PDF in self.formats

    @property
    def keeps_epub(self) -> bool:
        return BookFormat.EPUB in self.formats

    def outputs(self, book: BookFormat) -> frozenset[BookFormat]:
        """The DRM-free files to keep for a book fulfilled as ``book``.

        ``--to-pdf`` on a PDF book is skipped, as in 0.1.0: the PDF is the output.
        """
        if book is BookFormat.PDF:
            return frozenset({BookFormat.PDF})
        return self.formats

    @classmethod
    def from_options(
        cls,
        *,
        to_pdf: bool = False,
        keep_encrypted: bool = False,
        keep_epub: bool = False,
        move_acsm: bool = False,
        library: LibraryRetention | None = None,
    ) -> RetentionPolicy:
        """The policy for the command-line options, in plain mode or in ``library``.

        ``move_acsm`` is set for ``--move-acsm`` and for ACSMs taken from ``Inbox/``.
        """
        if library is None:
            if move_acsm:
                raise ConfigError(
                    "--move-acsm only works in a library",
                    hint="Outside a library the ACSM is always left where it is.",
                )
            formats = {BookFormat.PDF} if to_pdf else {BookFormat.EPUB}
            if to_pdf and keep_epub:
                formats.add(BookFormat.EPUB)
            return cls(
                acsm=AcsmHandling.LEAVE,
                keep_encrypted=keep_encrypted,
                formats=frozenset(formats),
                keep_epub_notice=keep_encrypted and to_pdf and not keep_epub,
            )

        # In a library, [output] formats decides; --keep-epub has no effect there.
        formats = set(library.formats)
        if to_pdf:
            formats.add(BookFormat.PDF)
        if move_acsm:
            acsm = AcsmHandling.MOVE
        elif library.keep_acsm:
            acsm = AcsmHandling.COPY
        else:
            acsm = AcsmHandling.LEAVE
        return cls(
            acsm=acsm,
            keep_encrypted=library.keep_encrypted or keep_encrypted,
            formats=frozenset(formats),
        )
