"""The error hierarchy (REFACTOR_PLAN §5.5).

Every error has a ``message``, a ``hint`` saying what the user can do about it, and a
``step`` that the pipeline fills in with the name of the step that failed. Each class
has a default hint; passing ``hint`` replaces it. ``str(error)`` is the message.

Messages may contain text from HTTP replies or vendored code. The CLI's error boundary
passes them through ``redact_text`` before showing them, so this module doesn't redact.
Where a class takes a download URL, it is kept as an attribute and never put in the
message.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

__all__ = [
    "ACSMFulfillmentError",
    "ArchiveError",
    "AuthorizationError",
    "BookLoaderError",
    "CalibreNotFoundError",
    "ConfigError",
    "ConversionError",
    "ConversionFailedError",
    "DRMRemovalError",
    "KoboDecryptionError",
    "KoboLibraryNotFoundError",
    "LibraryError",
    "LockedError",
    "ManualDownloadRequired",
    "OperationCancelled",
    "SecretUnavailableError",
    "WeasyPrintUnavailableError",
]

WEASYPRINT_INSTALL_URL = (
    "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation"
)


class BookLoaderError(Exception):
    """Base class for every error book-loader reports to the user."""

    default_hint: ClassVar[str | None] = None

    def __init__(self, message: str, hint: str | None = None, step: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint if hint is not None else self.default_hint
        self.step = step


class AuthorizationError(BookLoaderError):
    """The Adobe authorization is missing, unreadable, or was refused."""

    default_hint = (
        "Run 'book-loader auth info' to check the authorization, "
        "or 'book-loader auth create' to create one."
    )


class ACSMFulfillmentError(BookLoaderError):
    """Adobe's server couldn't fulfill the ACSM file."""

    default_hint = (
        "An ACSM file can be fulfilled only once, by one authorization, and it expires. "
        "If it was used before or is old, download a new one from the shop."
    )


class ManualDownloadRequired(ACSMFulfillmentError):
    """Fulfillment succeeded, but the automatic download failed (for example, a bot check).

    The license is saved as pending record ``pending_id``, so the user can download the
    book in a browser from the link page and finish with ``--downloaded-file`` or
    ``pending resume``. ``url`` is the download link; it stays out of the message.
    """

    default_hint = "The license is saved, so the ACSM file doesn't need to be fulfilled again."

    def __init__(
        self,
        reason: str,
        url: str,
        link_file: Path,
        acsm_path: Path,
        pending_id: str,
        hint: str | None = None,
        step: str | None = None,
    ) -> None:
        self.reason = reason
        self.url = url
        self.link_file = link_file
        self.acsm_path = acsm_path
        self.pending_id = pending_id
        super().__init__(
            f"Download failed: {reason}\n\n"
            "The fulfillment itself succeeded and its license was saved, "
            "so you can finish by hand:\n"
            f"  1. Open {link_file} in a browser and use the link to download the book\n"
            "     (complete Google's check if it asks). The file is still encrypted.\n"
            f"  2. Run:  book-loader pending resume {pending_id}\n"
            "     or run the same command again, adding:  --downloaded-file <path to that file>",
            hint,
            step,
        )


class DRMRemovalError(BookLoaderError):
    """An ADEPT-protected EPUB or PDF couldn't be decrypted."""

    default_hint = (
        "The book must have been fulfilled with the current authorization. If the "
        "authorization was reset or replaced since, restore the old one with "
        "'book-loader auth restore'."
    )


class KoboLibraryNotFoundError(BookLoaderError):
    """The Kobo Desktop library or its database can't be found."""

    default_hint = "Install Kobo Desktop and sign in, or give the library folder with --source."


class KoboDecryptionError(BookLoaderError):
    """A Kobo KEPUB couldn't be decrypted with any of the derived keys."""

    default_hint = "Open Kobo Desktop, sync, and make sure the book is downloaded, then try again."


class ConversionError(BookLoaderError):
    """EPUB to PDF conversion failed."""

    default_hint = "Try the other engine with --convert-engine."


class CalibreNotFoundError(ConversionError):
    """Calibre's ``ebook-convert`` isn't installed or can't be found."""

    default_hint = (
        "Install Calibre from https://calibre-ebook.com/download, "
        "or use --convert-engine python."
    )

    def __init__(
        self,
        message: str = "Calibre's ebook-convert was not found.",
        hint: str | None = None,
        step: str | None = None,
    ) -> None:
        super().__init__(message, hint, step)


class WeasyPrintUnavailableError(ConversionError):
    """WeasyPrint's native libraries (Pango and friends) can't be loaded.

    ``missing`` names the library that failed to load, when it is known.
    """

    default_hint = (
        "Use --convert-engine calibre, or install WeasyPrint's native libraries: "
        f"{WEASYPRINT_INSTALL_URL}"
    )

    def __init__(
        self,
        missing: str | None = None,
        hint: str | None = None,
        step: str | None = None,
    ) -> None:
        self.missing = missing
        if missing:
            message = f"WeasyPrint can't run: its native library {missing} is missing."
        else:
            message = "WeasyPrint can't run: its native libraries are missing."
        super().__init__(message, hint, step)


class ConversionFailedError(ConversionError):
    """The conversion engine ran but failed."""

    def __init__(
        self,
        engine: str,
        detail: str,
        hint: str | None = None,
        step: str | None = None,
    ) -> None:
        self.engine = engine
        self.detail = detail
        other = {"python": "calibre", "calibre": "python"}.get(engine)
        if hint is None and other:
            hint = f"Try the other engine with --convert-engine {other}."
        super().__init__(f"PDF conversion with {engine} failed: {detail}", hint, step)


class ConfigError(BookLoaderError):
    """A settings file can't be read, or holds an invalid value.

    ``path`` and ``line`` point at the problem when they are known, and lead the message.
    """

    default_hint = "Fix the setting, or remove it to use the default."

    def __init__(
        self,
        message: str,
        hint: str | None = None,
        step: str | None = None,
        *,
        path: Path | None = None,
        line: int | None = None,
    ) -> None:
        self.path = path
        self.line = line
        if path is not None:
            where = f"{path}, line {line}" if line is not None else str(path)
            message = f"{where}: {message}"
        super().__init__(message, hint, step)


class LockedError(BookLoaderError):
    """A file is in use: held open by another program, or locked by another book-loader run.

    ``path`` names the file. The default message covers the first case; lock files pass
    their own message and hint.
    """

    default_hint = (
        "Close the program using it (an ebook reader, a sync or backup tool, or antivirus) "
        "and try again."
    )

    def __init__(
        self,
        path: Path,
        message: str | None = None,
        hint: str | None = None,
        step: str | None = None,
    ) -> None:
        self.path = path
        super().__init__(message or f"{path} is in use by another program.", hint, step)


class SecretUnavailableError(BookLoaderError):
    """A password or passphrase is needed, but no source provides one."""

    default_hint = "Give it with a file option, on stdin, or in an environment variable."


class ArchiveError(BookLoaderError):
    """A backup archive can't be written, read, decrypted or verified."""

    default_hint = (
        "Check that the file is a complete book-loader backup and that the passphrase " "is right."
    )


class LibraryError(BookLoaderError):
    """A library folder is missing, damaged, or can't be used for this command."""

    default_hint = "Run 'book-loader library status' to check the library."


class OperationCancelled(BookLoaderError):
    """The user cancelled a question (Ctrl+C, Esc, or "Cancel" in a menu)."""

    default_hint = "Nothing more was done. Run the command again to carry on."

    def __init__(
        self,
        message: str = "Cancelled.",
        hint: str | None = None,
        step: str | None = None,
    ) -> None:
        super().__init__(message, hint, step)
