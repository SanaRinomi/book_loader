"""T2.3.2: ``domain/retention.py``, every row of the REFACTOR_PLAN §7 table."""

from __future__ import annotations

import dataclasses

import pytest

from book_loader.domain.errors import ConfigError
from book_loader.domain.models import BookFormat
from book_loader.domain.retention import AcsmHandling, LibraryRetention, RetentionPolicy

EPUB = BookFormat.EPUB
PDF = BookFormat.PDF


def plain(**options) -> RetentionPolicy:
    return RetentionPolicy.from_options(**options)


def library(library: LibraryRetention | None = None, **options) -> RetentionPolicy:
    return RetentionPolicy.from_options(library=library or LibraryRetention(), **options)


class TestPlainMode:
    def test_acsm_is_left_where_it_is(self):
        assert plain().acsm is AcsmHandling.LEAVE
        assert plain(to_pdf=True, keep_encrypted=True, keep_epub=True).acsm is AcsmHandling.LEAVE

    def test_move_acsm_needs_a_library(self):
        with pytest.raises(ConfigError, match="--move-acsm"):
            plain(move_acsm=True)

    @pytest.mark.parametrize("keep", [False, True])
    def test_encrypted_book_is_kept_only_with_keep_encrypted(self, keep):
        assert plain(keep_encrypted=keep).keep_encrypted is keep
        assert plain(keep_encrypted=keep, to_pdf=True).keep_encrypted is keep

    # (to_pdf, keep_epub) -> formats kept for an EPUB book
    @pytest.mark.parametrize(
        "to_pdf, keep_epub, formats",
        [
            (False, False, {EPUB}),
            (False, True, {EPUB}),  # nothing to convert, so the EPUB is the output
            (True, False, {PDF}),  # the decrypted EPUB is deleted after conversion
            (True, True, {PDF, EPUB}),
        ],
    )
    def test_decrypted_epub_when_converting(self, to_pdf, keep_epub, formats):
        policy = plain(to_pdf=to_pdf, keep_epub=keep_epub)
        assert policy.formats == formats
        assert policy.converts_to_pdf is to_pdf
        assert policy.keeps_epub is (EPUB in formats)

    def test_keep_encrypted_no_longer_keeps_the_epub(self):
        # Decision 3: in 0.1.0 --keep-encrypted also kept the decrypted EPUB.
        assert plain(keep_encrypted=True, to_pdf=True).formats == {PDF}


class TestNotice:
    @pytest.mark.parametrize("keep_encrypted", [False, True])
    @pytest.mark.parametrize("to_pdf", [False, True])
    @pytest.mark.parametrize("keep_epub", [False, True])
    def test_only_for_keep_encrypted_to_pdf_without_keep_epub(
        self, keep_encrypted, to_pdf, keep_epub
    ):
        policy = plain(keep_encrypted=keep_encrypted, to_pdf=to_pdf, keep_epub=keep_epub)
        expected = keep_encrypted and to_pdf and not keep_epub
        assert policy.keep_epub_notice is expected

    @pytest.mark.parametrize("formats", [{EPUB}, {PDF}, {EPUB, PDF}])
    def test_never_in_a_library(self, formats):
        policy = library(
            LibraryRetention(formats=frozenset(formats)), keep_encrypted=True, to_pdf=True
        )
        assert policy.keep_epub_notice is False


class TestLibraryMode:
    def test_defaults(self):
        policy = library()
        assert policy.acsm is AcsmHandling.COPY
        assert policy.keep_encrypted is True
        assert policy.formats == {EPUB}
        assert not policy.converts_to_pdf

    @pytest.mark.parametrize(
        "keep_acsm, move_acsm, expected",
        [
            (True, False, AcsmHandling.COPY),
            (True, True, AcsmHandling.MOVE),  # from Inbox/ or --move-acsm
            (False, False, AcsmHandling.LEAVE),
            (False, True, AcsmHandling.MOVE),  # a move was asked for explicitly
        ],
    )
    def test_acsm(self, keep_acsm, move_acsm, expected):
        policy = library(LibraryRetention(keep_acsm=keep_acsm), move_acsm=move_acsm)
        assert policy.acsm is expected

    @pytest.mark.parametrize(
        "config, flag, expected",
        [
            (True, False, True),
            (True, True, True),
            (False, False, False),
            (False, True, True),  # --keep-encrypted adds it for this run
        ],
    )
    def test_encrypted_book(self, config, flag, expected):
        policy = library(LibraryRetention(keep_encrypted=config), keep_encrypted=flag)
        assert policy.keep_encrypted is expected

    @pytest.mark.parametrize(
        "formats, to_pdf, expected",
        [
            ({EPUB}, False, {EPUB}),
            ({EPUB}, True, {EPUB, PDF}),  # --to-pdf adds "pdf"; the EPUB is still kept
            ({PDF}, False, {PDF}),  # converts without --to-pdf; the EPUB is not kept
            ({PDF}, True, {PDF}),
            ({EPUB, PDF}, False, {EPUB, PDF}),
        ],
    )
    def test_formats_decide_which_files_are_kept(self, formats, to_pdf, expected):
        policy = library(LibraryRetention(formats=frozenset(formats)), to_pdf=to_pdf)
        assert policy.formats == expected

    @pytest.mark.parametrize("formats", [{EPUB}, {PDF}])
    def test_keep_epub_has_no_effect(self, formats):
        config = LibraryRetention(formats=frozenset(formats))
        assert library(config, keep_epub=True) == library(config, keep_epub=False)

    def test_empty_formats_are_a_config_error(self):
        with pytest.raises(ConfigError, match="formats"):
            LibraryRetention(formats=frozenset())

    def test_formats_become_a_frozenset(self):
        config = LibraryRetention(formats={EPUB, PDF})  # type: ignore[arg-type]
        assert isinstance(config.formats, frozenset)


class TestOutputs:
    @pytest.mark.parametrize(
        "policy",
        [plain(), plain(to_pdf=True), plain(to_pdf=True, keep_epub=True), library(to_pdf=True)],
    )
    def test_a_pdf_book_is_kept_as_its_pdf(self, policy):
        # --to-pdf on a PDF book is skipped, as in 0.1.0.
        assert policy.outputs(PDF) == {PDF}

    def test_an_epub_book_follows_the_formats(self):
        assert plain().outputs(EPUB) == {EPUB}
        assert plain(to_pdf=True).outputs(EPUB) == {PDF}
        assert plain(to_pdf=True, keep_epub=True).outputs(EPUB) == {EPUB, PDF}


def test_policy_is_frozen_and_needs_a_format():
    with pytest.raises(dataclasses.FrozenInstanceError):
        plain().keep_encrypted = True  # type: ignore[misc]
    with pytest.raises(ValueError, match="at least one format"):
        RetentionPolicy(AcsmHandling.LEAVE, False, frozenset())
