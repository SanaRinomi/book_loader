"""T2.1: ``domain/errors.py``, the error hierarchy with hints."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from book_loader.domain import errors
from book_loader.domain.errors import (
    ACSMFulfillmentError,
    ArchiveError,
    AuthorizationError,
    BookLoaderError,
    CalibreNotFoundError,
    ConfigError,
    ConversionError,
    ConversionFailedError,
    DRMRemovalError,
    KoboDecryptionError,
    KoboLibraryNotFoundError,
    LibraryError,
    LockedError,
    ManualDownloadRequired,
    SecretUnavailableError,
    WeasyPrintUnavailableError,
)

URL = "https://dl.example.com/fulfill/book.epub?token=secret"
LINK = Path("out") / "Book - download link.html"
ACSM = Path("in") / "book.acsm"
PENDING_ID = "0123456789abcdef"


def manual_download() -> ManualDownloadRequired:
    return ManualDownloadRequired("HTTP 429", URL, LINK, ACSM, PENDING_ID)


# One example of every subclass, built the way callers will build it.
EXAMPLES = {
    AuthorizationError: lambda: AuthorizationError("Not authorized"),
    ACSMFulfillmentError: lambda: ACSMFulfillmentError("E_LIC_ALREADY_FULFILLED"),
    ManualDownloadRequired: manual_download,
    DRMRemovalError: lambda: DRMRemovalError("Wrong key"),
    KoboLibraryNotFoundError: lambda: KoboLibraryNotFoundError("Kobo.sqlite not found"),
    KoboDecryptionError: lambda: KoboDecryptionError("No key decrypts this book"),
    ConversionError: lambda: ConversionError("Conversion failed"),
    CalibreNotFoundError: CalibreNotFoundError,
    WeasyPrintUnavailableError: lambda: WeasyPrintUnavailableError("libpango-1.0-0"),
    ConversionFailedError: lambda: ConversionFailedError("python", "bad CSS"),
    ConfigError: lambda: ConfigError("Invalid value", path=Path("config.toml"), line=3),
    LockedError: lambda: LockedError(Path("activation.xml")),
    SecretUnavailableError: lambda: SecretUnavailableError("No backup passphrase"),
    ArchiveError: lambda: ArchiveError("Truncated archive"),
    LibraryError: lambda: LibraryError("Not a library"),
}


def test_every_subclass_has_an_example():
    subclasses = {
        cls
        for _, cls in inspect.getmembers(errors, inspect.isclass)
        if issubclass(cls, BookLoaderError) and cls is not BookLoaderError
    }
    assert subclasses == set(EXAMPLES)
    assert set(errors.__all__) == {cls.__name__ for cls in subclasses | {BookLoaderError}}


@pytest.mark.parametrize("cls", EXAMPLES, ids=lambda cls: cls.__name__)
def test_every_error_builds_a_message_and_a_hint(cls):
    error = EXAMPLES[cls]()
    assert isinstance(error, BookLoaderError)
    assert error.message
    assert str(error) == error.message
    assert error.hint
    assert error.hint == cls.default_hint or cls is ConversionFailedError
    assert error.step is None


@pytest.mark.parametrize("cls", EXAMPLES, ids=lambda cls: cls.__name__)
def test_every_error_takes_a_hint_and_a_step(cls):
    error = EXAMPLES[cls]()
    signature = inspect.signature(cls)
    assert {"hint", "step"} <= set(signature.parameters)

    error.step = "Downloading"
    assert error.step == "Downloading"


def test_base_error():
    error = BookLoaderError("Something went wrong")
    assert str(error) == error.message == "Something went wrong"
    assert error.args == ("Something went wrong",)
    assert error.hint is None
    assert error.step is None

    error = BookLoaderError("Something went wrong", hint="Do this", step="Decrypting")
    assert error.hint == "Do this"
    assert error.step == "Decrypting"


def test_explicit_hint_replaces_the_default():
    assert AuthorizationError("x").hint == AuthorizationError.default_hint
    assert AuthorizationError("x", hint="Other").hint == "Other"
    assert AuthorizationError("x", hint="").hint == ""
    assert manual_download().hint == ManualDownloadRequired.default_hint
    assert ManualDownloadRequired("r", URL, LINK, ACSM, PENDING_ID, hint="h").hint == "h"


def test_hierarchy():
    assert issubclass(ManualDownloadRequired, ACSMFulfillmentError)
    for cls in (CalibreNotFoundError, WeasyPrintUnavailableError, ConversionFailedError):
        assert issubclass(cls, ConversionError)


def test_workflow_error_is_gone():
    assert not hasattr(errors, "WorkflowError")


class TestManualDownloadRequired:
    def test_keeps_its_fields(self):
        error = manual_download()
        assert error.reason == "HTTP 429"
        assert error.url == URL
        assert error.link_file == LINK
        assert error.acsm_path == ACSM
        assert error.pending_id == PENDING_ID

    def test_message_has_the_reason_link_page_and_both_ways_to_finish(self):
        message = manual_download().message
        assert message.startswith("Download failed: HTTP 429")
        assert str(LINK) in message
        assert "--downloaded-file" in message
        assert f"pending resume {PENDING_ID}" in message

    def test_the_url_is_never_shown(self):
        error = manual_download()
        for text in (
            str(error),
            repr(error),
            error.message,
            error.hint or "",
            *map(str, error.args),
        ):
            assert URL not in text
            assert "dl.example.com" not in text

    def test_step_is_passed_on(self):
        error = ManualDownloadRequired("r", URL, LINK, ACSM, PENDING_ID, step="Downloading")
        assert error.step == "Downloading"


class TestConversionErrors:
    def test_calibre_not_found_has_a_default_message(self):
        error = CalibreNotFoundError()
        assert "ebook-convert" in error.message
        assert "--convert-engine python" in (error.hint or "")
        assert CalibreNotFoundError("Custom").message == "Custom"

    def test_weasyprint_names_the_missing_library(self):
        error = WeasyPrintUnavailableError("libpango-1.0-0")
        assert error.missing == "libpango-1.0-0"
        assert "libpango-1.0-0" in error.message
        assert "--convert-engine calibre" in (error.hint or "")
        assert errors.WEASYPRINT_INSTALL_URL in (error.hint or "")

    def test_weasyprint_without_a_known_library(self):
        error = WeasyPrintUnavailableError()
        assert error.missing is None
        assert "native libraries" in error.message

    @pytest.mark.parametrize("engine, other", [("python", "calibre"), ("calibre", "python")])
    def test_failed_conversion_suggests_the_other_engine(self, engine, other):
        error = ConversionFailedError(engine, "exit status 1")
        assert error.engine == engine
        assert error.detail == "exit status 1"
        assert error.message == f"PDF conversion with {engine} failed: exit status 1"
        assert error.hint == f"Try the other engine with --convert-engine {other}."

    def test_failed_conversion_with_an_unknown_engine_keeps_the_generic_hint(self):
        assert ConversionFailedError("other", "x").hint == ConversionError.default_hint
        assert ConversionFailedError("python", "x", hint="h").hint == "h"


class TestConfigError:
    def test_file_and_line_lead_the_message(self):
        error = ConfigError("Expected '='", path=Path("config.toml"), line=3)
        assert error.message == "config.toml, line 3: Expected '='"
        assert error.path == Path("config.toml")
        assert error.line == 3

    def test_file_without_a_line(self):
        error = ConfigError("Unknown key 'x'", path=Path("config.toml"))
        assert error.message == "config.toml: Unknown key 'x'"
        assert error.line is None

    def test_without_a_file(self):
        error = ConfigError("BOOK_LOADER_AUTH_DIR is empty")
        assert error.message == "BOOK_LOADER_AUTH_DIR is empty"
        assert error.path is None


class TestLockedError:
    def test_names_the_file(self):
        path = Path("auth") / "activation.xml"
        error = LockedError(path)
        assert error.path == path
        assert str(path) in error.message
        assert error.hint == LockedError.default_hint

    def test_lock_files_give_their_own_message_and_hint(self):
        error = LockedError(Path(".lock"), "Another run is using this library.", hint="Wait")
        assert error.message == "Another run is using this library."
        assert error.hint == "Wait"
