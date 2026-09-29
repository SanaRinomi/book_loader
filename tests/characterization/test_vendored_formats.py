"""Vendored format code against synthetic inputs.

- T0.4.6: ``ineptepub.decryptBook`` on a synthetic ADEPT EPUB
- T0.4.7: ``libpdf.patch_drm_into_pdf`` on a tiny EBX PDF
- T0.4.10: ``libadobeFulfill.parse_fulfillment`` and ``updateLoanReturnData`` on the
  reply fixtures, which also checks the fixtures themselves
"""

from __future__ import annotations

import base64
import re
import zlib
from pathlib import Path

import pytest
from lxml import etree

from book_loader.core.adobe import libadobe, libadobeFulfill, libpdf
from book_loader.core.drm import ineptepub
from tests.fixtures.builders._random import rsa_key
from tests.fixtures.builders.adept_epub import build_adept_epub
from tests.fixtures.builders.adobe_auth import LICENSE_URL, OPERATOR_URL, build_auth_folder
from tests.fixtures.builders.epub import build_epub, read_entries
from tests.fixtures.builders.tiny_pdf import ENCRYPT_OBJECT, build_ebx_pdf

REPLIES = Path(__file__).parents[1] / "fixtures" / "replies"


class TestAdeptEpub:
    def test_decrypts_to_the_plaintext(self, tmp_path):
        key = rsa_key("device")
        plaintext = build_adept_epub(tmp_path / "in.epub", key)

        result = ineptepub.decryptBook(
            key.export_key("DER", pkcs=8), tmp_path / "in.epub", tmp_path / "out.epub"
        )

        assert result == 0
        assert read_entries(tmp_path / "out.epub") == plaintext

    def test_mimetype_comes_first_and_stored(self, tmp_path):
        import zipfile

        key = rsa_key("device")
        build_adept_epub(tmp_path / "in.epub", key)
        ineptepub.decryptBook(
            key.export_key("DER", pkcs=8), tmp_path / "in.epub", tmp_path / "out.epub"
        )
        with zipfile.ZipFile(tmp_path / "out.epub") as zf:
            first = zf.infolist()[0]
        assert (first.filename, first.compress_type) == ("mimetype", zipfile.ZIP_STORED)

    def test_wrong_key(self, tmp_path):
        build_adept_epub(tmp_path / "in.epub", rsa_key("device"))
        other = rsa_key("other").export_key("DER", pkcs=8)
        assert ineptepub.decryptBook(other, tmp_path / "in.epub", tmp_path / "out.epub") == 2

    def test_plain_epub_is_reported_drm_free(self, tmp_path):
        build_epub(tmp_path / "plain.epub")
        key = rsa_key("device").export_key("DER", pkcs=8)
        assert ineptepub.decryptBook(key, tmp_path / "plain.epub", tmp_path / "out.epub") == 1
        assert not (tmp_path / "out.epub").exists()


class TestLibpdf:
    LICENSE = "<licenseToken>test license</licenseToken>"
    BOOK_ID = "urn:uuid:33333333-3333-4333-8333-333333333333"

    def test_patch_appends_an_incremental_update(self, tmp_path):
        original = build_ebx_pdf(tmp_path / "in.pdf")

        ok = libpdf.patch_drm_into_pdf(
            str(tmp_path / "in.pdf"), self.LICENSE, str(tmp_path / "out.pdf"), self.BOOK_ID
        )

        assert ok is True
        patched = (tmp_path / "out.pdf").read_bytes()
        assert patched.startswith(original)
        update = patched[len(original) :].decode("latin-1")

        # The encryption object is rewritten with the book ID and the license.
        assert f"\r{ENCRYPT_OBJECT} 0 obj\r" in update
        assert f"/EBX_BOOKID({self.BOOK_ID})" in update
        license_b64 = re.search(r"/ADEPT_LICENSE\(([^)]*)\)", update).group(1)
        assert zlib.decompress(base64.b64decode(license_b64), -15).decode() == self.LICENSE

        # A new xref section and trailer point back at the original one.
        old_startxref = int(re.findall(rb"startxref\n(\d+)", original)[-1])
        assert f"/Prev {old_startxref}>>" in update
        new_startxref = int(update.rsplit("startxref\r", 1)[1].split("\r")[0])
        # libpdf points startxref at the "\r" just before "xref"; readers tolerate it.
        assert patched[new_startxref:].startswith(b"\rxref\r")
        assert update.endswith("%%EOF")

    def test_pdf_without_ebx_handler_fails(self, tmp_path):
        pdf = tmp_path / "in.pdf"
        pdf.write_bytes(build_ebx_pdf(tmp_path / "x.pdf").replace(b"EBX_HANDLER", b"Standard"))
        assert (
            libpdf.patch_drm_into_pdf(str(pdf), self.LICENSE, str(tmp_path / "o.pdf"), "x") is False
        )


class TestReplyFixtures:
    @pytest.fixture(autouse=True)
    def _auth(self, tmp_path):
        auth = build_auth_folder(tmp_path / "auth")
        libadobe.update_account_path(str(auth.path))

    @pytest.mark.parametrize(
        ("reply", "book_name", "fmt", "url_part"),
        [
            (
                "fulfill_epub.xml",
                "The Test Book Volume 1",
                "application/epub+zip",
                "test-book.epub",
            ),
            ("fulfill_pdf.xml", "A PDF Book", "application/pdf", "a-pdf-book.pdf"),
            ("fulfill_loan.xml", "A Borrowed Book", "application/epub+zip", "borrowed-book.epub"),
        ],
    )
    def test_parse_fulfillment(self, reply, book_name, fmt, url_part):
        info = libadobeFulfill.parse_fulfillment((REPLIES / reply).read_bytes())

        assert info["book_name"] == book_name
        assert info["format"] == fmt
        assert info["resource"] == "urn:uuid:33333333-3333-4333-8333-333333333333"
        assert url_part in info["download_url"]
        assert "&amp;" not in info["download_url"] and "&output=" in info["download_url"]
        rights = etree.fromstring(info["rights_xml"].encode())
        ns = {"a": "http://ns.adobe.com/adept"}
        assert rights.findtext("a:licenseServiceInfo/a:licenseURL", namespaces=ns) == LICENSE_URL
        assert rights.find("a:licenseToken", namespaces=ns) is not None

    def test_error_reply_is_rejected(self):
        with pytest.raises(RuntimeError, match="no resourceItemInfo"):
            libadobeFulfill.parse_fulfillment((REPLIES / "error.xml").read_bytes())

    def test_loan_record(self):
        reply = etree.fromstring((REPLIES / "fulfill_loan.xml").read_bytes())
        record = libadobeFulfill.updateLoanReturnData(reply, forceTestBehaviour=True)
        assert record == {
            "book_name": "A Borrowed Book",
            "user": "urn:uuid:11111111-1111-4111-8111-111111111111",
            "device": "urn:uuid:22222222-2222-4222-8222-222222222222",
            "loanID": "44444444-4444-4444-8444-444444444444",
            "operatorURL": OPERATOR_URL,
            "validUntil": "2026-02-14T12:00:00+00:00",
        }

    def test_bought_book_has_no_loan_record(self):
        reply = etree.fromstring((REPLIES / "fulfill_epub.xml").read_bytes())
        assert libadobeFulfill.updateLoanReturnData(reply, forceTestBehaviour=True) is False
