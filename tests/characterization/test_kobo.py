"""T0.4.3: Kobo library listing, key derivation and decryption in 0.1.0."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from book_loader.core.kobo import KoboDecryptor, KoboLibrary
from book_loader.utils.errors import KoboDecryptionError, KoboLibraryNotFoundError
from tests.fixtures.builders.epub import read_entries
from tests.fixtures.builders.kobo import (
    FAKE_MAC,
    KOBO_HASH_KEYS,
    OTHER_MAC,
    USER_ID,
    KoboBookSpec,
    add_book_in_wal,
    build_kobo_library,
    default_kepub_files,
    derive_userkey,
)

GOLDEN_KEYS = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "golden" / "kobo_keys.json").read_text("utf-8")
)

BOOKS = [
    KoboBookSpec("vol-beta", "Beta Book", "Author B"),
    KoboBookSpec("vol-alpha", "Alpha Book", "Author A"),
    KoboBookSpec("vol-free", "Gamma Free Book", None, drm=False),
]


@pytest.fixture
def macs(monkeypatch):
    """The MAC addresses KoboLibrary sees; the fake one first, as on a real machine."""
    found = [OTHER_MAC, FAKE_MAC]
    monkeypatch.setattr(KoboLibrary, "_get_mac_addrs", lambda self: list(found))
    return found


@pytest.fixture
def kobodir(tmp_path):
    return tmp_path / "Kobo Desktop Edition"


def open_library(kobodir: Path):
    lib = KoboLibrary(kobodir=kobodir)
    return lib


class TestLibrary:
    def test_lists_every_book_sorted_by_title(self, kobodir, macs):
        build_kobo_library(kobodir, BOOKS)
        lib = open_library(kobodir)
        try:
            books = lib.books
        finally:
            lib.close()
        assert [(b.volumeid, b.title, b.author, b.has_drm) for b in books] == [
            ("vol-alpha", "Alpha Book", "Author A", True),
            ("vol-beta", "Beta Book", "Author B", True),
            ("vol-free", "Gamma Free Book", None, False),
        ]

    def test_book_after_the_first_keeps_its_keys(self, kobodir, macs):
        """The old cursor bug stopped the outer loop after the first DRM book."""
        build_kobo_library(kobodir, BOOKS)
        lib = open_library(kobodir)
        try:
            by_id = {b.volumeid: b for b in lib.books}
        finally:
            lib.close()
        for volumeid in ("vol-alpha", "vol-beta"):
            assert set(by_id[volumeid].encrypted_files) == {
                "OEBPS/chapter1.xhtml",
                "OEBPS/chapter2.xhtml",
                "OEBPS/images/cover.jpg",
            }
            assert by_id[volumeid].filename == kobodir / "kepub" / volumeid

    def test_userkeys_include_the_right_key(self, kobodir, macs):
        userkey = build_kobo_library(kobodir, BOOKS)
        lib = open_library(kobodir)
        try:
            keys = lib.userkeys
        finally:
            lib.close()
        assert userkey in keys
        # 4 hash keys x 2 MACs x 1 user ID, in MAC order.
        assert len(keys) == 8
        assert keys.index(userkey) == 4

    def test_temp_database_is_deleted_on_close(self, kobodir, macs):
        build_kobo_library(kobodir, BOOKS)
        lib = open_library(kobodir)
        temp_db = Path(lib._tmpdb.name)
        assert temp_db.exists()
        lib.close()
        assert not temp_db.exists()

    def test_wal_mode_database_is_read(self, kobodir, macs):
        build_kobo_library(kobodir, BOOKS, wal=True)
        lib = open_library(kobodir)
        try:
            assert len(lib.books) == 3
        finally:
            lib.close()

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="0.1.0 copies the raw database file and misses changes still in the WAL "
        "(REFACTOR_PLAN §3, §6). T4.2 must make this pass.",
    )
    def test_change_still_in_wal_is_listed(self, kobodir, macs):
        userkey = build_kobo_library(kobodir, BOOKS, wal=True)
        writer = add_book_in_wal(kobodir, KoboBookSpec("vol-new", "New Book"), userkey)
        try:
            assert Path(f"{kobodir / 'Kobo.sqlite'}-wal").stat().st_size > 0
            lib = open_library(kobodir)
            try:
                titles = [b.title for b in lib.books]
            finally:
                lib.close()
        finally:
            writer.close()
        assert "New Book" in titles

    def test_missing_folder(self, tmp_path):
        with pytest.raises(KoboLibraryNotFoundError, match="directory not found"):
            KoboLibrary(kobodir=tmp_path / "missing")

    def test_missing_database(self, kobodir):
        kobodir.mkdir()
        with pytest.raises(KoboLibraryNotFoundError, match="database not found"):
            KoboLibrary(kobodir=kobodir)


class TestDecryptor:
    def _book(self, kobodir, volumeid):
        lib = open_library(kobodir)
        try:
            return next(b for b in lib.books if b.volumeid == volumeid), lib.userkeys
        finally:
            lib.close()

    def test_decrypts_to_the_plaintext(self, kobodir, macs, tmp_path):
        build_kobo_library(kobodir, BOOKS)
        book, keys = self._book(kobodir, "vol-beta")
        out = tmp_path / "out"
        out.mkdir()
        result = KoboDecryptor().decrypt_book(book, keys, out)
        assert result == out / "Beta Book.epub"
        assert read_entries(result) == default_kepub_files()

    def test_drm_free_book_is_copied_as_is(self, kobodir, macs, tmp_path):
        build_kobo_library(kobodir, BOOKS)
        book, keys = self._book(kobodir, "vol-free")
        out = tmp_path / "out"
        out.mkdir()
        result = KoboDecryptor().decrypt_book(book, keys, out)
        assert result.read_bytes() == (kobodir / "kepub" / "vol-free").read_bytes()

    def test_wrong_keys_only(self, kobodir, macs, tmp_path):
        build_kobo_library(kobodir, BOOKS)
        book, _ = self._book(kobodir, "vol-alpha")
        out = tmp_path / "out"
        out.mkdir()
        wrong = [derive_userkey(OTHER_MAC, USER_ID, h) for h in KOBO_HASH_KEYS]
        with pytest.raises(KoboDecryptionError, match="no valid key found"):
            KoboDecryptor().decrypt_book(book, wrong, out)
        assert os.listdir(out) == []

    @pytest.mark.xfail(
        strict=True,
        raises=KoboDecryptionError,
        reason="0.1.0 requires printable ASCII at the start of XHTML, so a UTF-8 BOM "
        "rejects the right key (REFACTOR_PLAN §6). T4.2 must make this pass.",
    )
    def test_xhtml_with_bom_is_decrypted(self, kobodir, macs, tmp_path):
        spec = KoboBookSpec("vol-bom", "BOM Book", files=default_kepub_files(bom=True))
        build_kobo_library(kobodir, [spec])
        book, keys = self._book(kobodir, "vol-bom")
        out = tmp_path / "out"
        out.mkdir()
        result = KoboDecryptor().decrypt_book(book, keys, out)
        assert read_entries(result) == default_kepub_files(bom=True)


class TestKeyVectors:
    @pytest.mark.parametrize(
        "case", GOLDEN_KEYS["cases"], ids=lambda c: f"{c['mac']}-{c['hash_key']}"
    )
    def test_old_code_matches_golden(self, case, monkeypatch):
        monkeypatch.setattr(KoboLibrary, "_get_user_ids", lambda self: [case["user_id"]])
        keys = KoboLibrary._compute_userkeys(KoboLibrary.__new__(KoboLibrary), case["mac"])
        index = GOLDEN_KEYS["hash_keys"].index(case["hash_key"])
        assert keys[index].hex() == case["userkey"]

    @pytest.mark.parametrize(
        "case", GOLDEN_KEYS["cases"], ids=lambda c: f"{c['mac']}-{c['hash_key']}"
    )
    def test_independent_derivation_matches_golden(self, case):
        """The builder's own implementation of Kobo's scheme agrees with the golden file."""
        key = derive_userkey(case["mac"], case["user_id"], case["hash_key"])
        assert key.hex() == case["userkey"]

    def test_hash_keys_unchanged(self):
        assert GOLDEN_KEYS["hash_keys"] == KOBO_HASH_KEYS
