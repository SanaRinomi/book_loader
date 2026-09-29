"""T2.2.1: ``domain/models.py``, the frozen models and their invariants."""

from __future__ import annotations

import dataclasses
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from book_loader.domain.models import (
    AdobeSource,
    AuthInfo,
    AuthType,
    BatchItem,
    BatchResult,
    BookFile,
    BookFiles,
    BookFormat,
    BookLoan,
    BookRecord,
    ConflictAction,
    ItemStatus,
    LoanRecord,
    OutputSettings,
    PendingRecord,
    ProcessRequest,
    ProcessResult,
    StepResult,
    StepStatus,
)

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
NAIVE = datetime(2026, 9, 29, 12, 0)
URL = "https://dl.example.com/fulfill/book.epub?token=secret"
DECOMPOSED = unicodedata.normalize("NFD", "Café Noël")
COMPOSED = unicodedata.normalize("NFC", "Café Noël")


def adobe_source() -> AdobeSource:
    return AdobeSource("urn:uuid:1234", "ab" * 32, NOW, "cd" * 32)


def book(**changes) -> BookRecord:
    base = BookRecord("Book", "adobe", NOW, NOW, adobe=adobe_source())
    return dataclasses.replace(base, **changes)


def pending(**changes) -> PendingRecord:
    base = PendingRecord(
        "0123456789abcdef",
        NOW,
        "book.acsm",
        "cd" * 32,
        info={"download_url": URL, "rights_xml": "<rights/>", "book_name": "Book"},
    )
    return dataclasses.replace(base, **changes)


def loan(**changes) -> LoanRecord:
    base = LoanRecord(
        "loan-1",
        "urn:uuid:user",
        "urn:uuid:device",
        "https://operator.example.com/fulfill",
        NOW + timedelta(days=14),
        "Borrowed",
        NOW,
    )
    return dataclasses.replace(base, **changes)


# One example of each model, for the checks that apply to all of them.
EXAMPLES = [
    AuthInfo(AuthType.ANONYMOUS, Path("auth")),
    OutputSettings(),
    ProcessRequest(Path("book.acsm"), Path("out")),
    StepResult("Checking for auth", StepStatus.DONE, "Anonymous"),
    ProcessResult((Path("out/Book.epub"),), BookFormat.EPUB, 1024),
    BatchItem("Book", ItemStatus.SKIPPED),
    BatchResult(),
    BookFile("Book.epub", "ab" * 32),
    BookFiles(),
    adobe_source(),
    BookLoan("loan-1", NOW),
    book(),
    pending(),
    loan(),
]


@pytest.mark.parametrize("model", EXAMPLES, ids=lambda model: type(model).__name__)
def test_every_model_is_frozen(model):
    name = dataclasses.fields(model)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(model, name, None)


class TestAuth:
    @pytest.mark.parametrize(
        "auth_type, usable",
        [
            (AuthType.ANONYMOUS, True),
            (AuthType.ADOBE_ID, True),
            (AuthType.ADE_UNUSABLE, False),
            (AuthType.NONE, False),
            (AuthType.UNKNOWN, False),
        ],
    )
    def test_usable(self, auth_type, usable):
        assert auth_type.usable is usable
        assert AuthInfo(auth_type, Path("auth")).usable is usable

    def test_values_are_strings(self):
        assert AuthType("adobe_id") is AuthType.ADOBE_ID
        assert f"{AuthType.ADE_UNUSABLE}" == "ade_unusable"

    def test_adobe_id_has_an_email(self):
        info = AuthInfo(AuthType.ADOBE_ID, Path("auth"), email="reader@example.com")
        assert info.email == "reader@example.com"

    @pytest.mark.parametrize("auth_type", [t for t in AuthType if t is not AuthType.ADOBE_ID])
    def test_only_adobe_id_has_an_email(self, auth_type):
        with pytest.raises(ValueError, match="email"):
            AuthInfo(auth_type, Path("auth"), email="reader@example.com")

    def test_no_authorization_has_no_key(self):
        with pytest.raises(ValueError):
            AuthInfo(AuthType.NONE, Path("auth"), key_fingerprint="cd" * 32)
        with pytest.raises(ValueError):
            AuthInfo(AuthType.NONE, Path("auth"), device_uuid="urn:uuid:device")


class TestBookFormat:
    @pytest.mark.parametrize(
        "name, expected",
        [("a.epub", BookFormat.EPUB), ("a.PDF", BookFormat.PDF), ("a.b.Epub", BookFormat.EPUB)],
    )
    def test_from_path(self, name, expected):
        assert BookFormat.from_path(Path(name)) is expected

    @pytest.mark.parametrize("name", ["a.html", "a", "a.epub.tmp"])
    def test_from_path_rejects_other_files(self, name):
        with pytest.raises(ValueError, match="not an EPUB or PDF"):
            BookFormat.from_path(Path(name))

    def test_suffix(self):
        assert BookFormat.EPUB.suffix == ".epub"
        assert BookFormat.PDF.suffix == ".pdf"


def test_conflict_actions():
    assert {action.value for action in ConflictAction} == {
        "overwrite",
        "skip",
        "rename",
        "overwrite_all",
        "skip_all",
        "cancel",
    }


class TestProcess:
    def test_request_defaults_match_0_1_0(self):
        request = ProcessRequest(Path("book.acsm"), Path("out"))
        assert request.settings == OutputSettings(
            to_pdf=False, convert_engine="python", optimize=False, keep_encrypted=False
        )
        assert request.settings.keep_epub is False
        assert request.downloaded_file is None

    def test_result(self):
        result = ProcessResult(
            (Path("out/Book.pdf"), Path("out/Book.epub")),
            BookFormat.PDF,
            2048,
            title="Book",
            loan_until=NOW,
            steps=(StepResult("Converting", StepStatus.DONE),),
            warnings=("ACSM expires soon",),
        )
        assert result.output == Path("out/Book.pdf")

    def test_result_needs_an_output(self):
        with pytest.raises(ValueError, match="at least one output"):
            ProcessResult((), BookFormat.EPUB, 0)

    def test_main_output_matches_the_format(self):
        with pytest.raises(ValueError, match="is not pdf"):
            ProcessResult((Path("Book.epub"),), BookFormat.PDF, 0)

    def test_size_is_not_negative(self):
        with pytest.raises(ValueError, match="negative"):
            ProcessResult((Path("Book.epub"),), BookFormat.EPUB, -1)

    @pytest.mark.parametrize("name", ["loan_until", "acsm_expires"])
    def test_dates_need_a_time_zone(self, name):
        with pytest.raises(ValueError, match="time zone"):
            dataclasses.replace(
                ProcessResult((Path("Book.epub"),), BookFormat.EPUB, 0), **{name: NAIVE}
            )


class TestBatch:
    def done(self, name="A"):
        return BatchItem(name, ItemStatus.DONE, outputs=(Path(f"{name}.epub"),))

    def test_empty_batch_succeeds(self):
        assert BatchResult().exit_code == 0

    @pytest.mark.parametrize(
        "statuses, exit_code",
        [
            ([ItemStatus.DONE], 0),
            ([ItemStatus.SKIPPED], 0),
            ([ItemStatus.DONE, ItemStatus.SKIPPED], 0),
            ([ItemStatus.FAILED], 1),
            ([ItemStatus.PENDING], 1),
            ([ItemStatus.DONE, ItemStatus.SKIPPED, ItemStatus.PENDING], 1),
            ([ItemStatus.DONE, ItemStatus.FAILED, ItemStatus.SKIPPED], 1),
        ],
    )
    def test_exit_code_is_0_only_when_nothing_failed_or_is_pending(self, statuses, exit_code):
        items = []
        for index, status in enumerate(statuses):
            name = f"book{index}"
            if status is ItemStatus.DONE:
                items.append(self.done(name))
            elif status is ItemStatus.FAILED:
                items.append(BatchItem(name, status, reason="Wrong key"))
            elif status is ItemStatus.PENDING:
                items.append(BatchItem(name, status, pending_id="0123456789abcdef"))
            else:
                items.append(BatchItem(name, status))
        assert BatchResult(tuple(items)).exit_code == exit_code

    def test_cancelling_alone_does_not_fail(self):
        assert BatchResult((self.done(),), cancelled=True).exit_code == 0
        failed = BatchItem("B", ItemStatus.FAILED, reason="x")
        assert BatchResult((self.done(), failed), cancelled=True).exit_code == 1

    def test_counts(self):
        result = BatchResult(
            (
                self.done("A"),
                self.done("B"),
                BatchItem("C", ItemStatus.SKIPPED),
                BatchItem("D", ItemStatus.FAILED, reason="not downloaded in Kobo Desktop"),
            )
        )
        assert result.count(ItemStatus.DONE) == 2
        assert result.count(ItemStatus.PENDING) == 0
        assert [item.name for item in result.with_status(ItemStatus.FAILED)] == ["D"]

    def test_done_needs_an_output(self):
        with pytest.raises(ValueError, match="output"):
            BatchItem("A", ItemStatus.DONE)

    def test_failed_needs_a_reason(self):
        with pytest.raises(ValueError, match="reason"):
            BatchItem("A", ItemStatus.FAILED)

    def test_only_pending_has_a_pending_id(self):
        with pytest.raises(ValueError, match="pending ID"):
            BatchItem("A", ItemStatus.PENDING)
        with pytest.raises(ValueError, match="pending ID"):
            BatchItem("A", ItemStatus.SKIPPED, pending_id="0123456789abcdef")


class TestBookRecord:
    def test_text_is_stored_in_nfc(self):
        record = book(title=DECOMPOSED, authors=(DECOMPOSED, "Plain"), series=DECOMPOSED)
        assert record.title == COMPOSED
        assert record.authors == (COMPOSED, "Plain")
        assert record.series == COMPOSED
        assert BookFile(DECOMPOSED + ".epub", "ab" * 32).name == COMPOSED + ".epub"

    def test_authors_become_a_tuple(self):
        assert book(authors=["A", "B"]).authors == ("A", "B")

    @pytest.mark.parametrize("name", ["added_at", "updated_at"])
    def test_dates_need_a_time_zone(self, name):
        with pytest.raises(ValueError, match="time zone"):
            book(**{name: NAIVE})

    def test_updated_is_not_before_added(self):
        with pytest.raises(ValueError, match="updated_at"):
            book(updated_at=NOW - timedelta(seconds=1))

    def test_adobe_books_record_their_source(self):
        with pytest.raises(ValueError, match="Adobe"):
            book(adobe=None)
        assert book(source="kobo", adobe=None).adobe is None

    def test_nested_dates_need_a_time_zone(self):
        with pytest.raises(ValueError, match="time zone"):
            AdobeSource("urn:uuid:1234", "ab" * 32, NAIVE, "cd" * 32)
        with pytest.raises(ValueError, match="time zone"):
            BookLoan("loan-1", NAIVE)

    def test_defaults(self):
        record = book()
        assert record.version == 1
        assert record.files == BookFiles()
        assert record.loan is None


class TestPendingRecord:
    def test_v0_record_has_no_acsm(self):
        record = pending(version=0)
        assert not record.has_acsm
        assert record.settings is None and record.output_dir is None

    def test_v1_record_can_be_fulfilled_again(self):
        record = pending(acsm_content=b"<fulfillmentToken/>", settings=OutputSettings(True))
        assert record.has_acsm

    @pytest.mark.parametrize(
        "bad", ["0123456789ABCDEF", "0123456789abcde", "0123456789abcdefa", ""]
    )
    def test_id_is_16_lower_case_hex(self, bad):
        with pytest.raises(ValueError, match="pending ID"):
            pending(id=bad)

    @pytest.mark.parametrize("name", ["saved_at", "acsm_expires"])
    def test_dates_need_a_time_zone(self, name):
        with pytest.raises(ValueError, match="time zone"):
            pending(**{name: NAIVE})

    def test_key_match(self):
        record = pending()
        assert record.matches_key("cd" * 32)
        assert not record.matches_key("ef" * 32)

    def test_title_is_stored_in_nfc(self):
        assert pending(title=DECOMPOSED).title == COMPOSED

    def test_repr_hides_the_url_license_and_acsm(self):
        text = repr(pending(acsm_content=b"<fulfillmentToken>secret</fulfillmentToken>"))
        assert "dl.example.com" not in text
        assert "rights" not in text
        assert "fulfillmentToken" not in text
        assert "0123456789abcdef" in text


class TestLoanRecord:
    def test_expired(self):
        record = loan()
        assert not record.expired(NOW)
        assert not record.expired(NOW + timedelta(days=14, seconds=-1))
        assert record.expired(NOW + timedelta(days=14))

    def test_expired_needs_a_time_zone(self):
        with pytest.raises(ValueError, match="time zone"):
            loan().expired(NAIVE)

    @pytest.mark.parametrize("name", ["valid_until", "borrowed_at"])
    def test_dates_need_a_time_zone(self, name):
        with pytest.raises(ValueError, match="time zone"):
            loan(**{name: NAIVE})

    def test_title_is_stored_in_nfc(self):
        assert loan(title=DECOMPOSED).title == COMPOSED

    def test_defaults(self):
        record = loan()
        assert record.returned is False
        assert record.files == ()
        assert record.library is None
