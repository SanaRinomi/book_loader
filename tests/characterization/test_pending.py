"""T0.4.5: the pending store of 0.1.0 (``ACSMFulfiller._save/_load/_clear_pending``)."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import time
import types
from pathlib import Path

import pytest

from book_loader.core.adobe import libadobe
from book_loader.core.adobe.fulfill import ACSMFulfiller
from tests.fixtures.builders.adobe_auth import build_auth_folder

V0 = Path(__file__).parents[1] / "fixtures" / "v0"
DEVICE_KEY = b"fixed device key bytes"
INFO = {
    "download_url": "https://download.example.com/b.epub?id=1&output=epub",
    "rights_xml": "<adept:rights/>",
    # parse_fulfillment keeps only letters, digits, spaces, "-" and "_" in names.
    "book_name": "Tom and Jerry - Part_1",
    "resource": "urn:uuid:33333333-3333-4333-8333-333333333333",
    "format": "application/epub+zip",
}


def fulfiller(auth_dir: Path, key: bytes = DEVICE_KEY) -> ACSMFulfiller:
    account = types.SimpleNamespace(auth_dir=auth_dir, get_device_key=lambda: key)
    return ACSMFulfiller(account)


@pytest.fixture
def acsm(tmp_path) -> Path:
    path = tmp_path / "books" / "book.acsm"
    path.parent.mkdir()
    path.write_bytes(b"<fulfillmentToken>test</fulfillmentToken>")
    return path


@pytest.fixture
def out_dir(tmp_path) -> Path:
    path = tmp_path / "out"
    path.mkdir()
    return path


def pending_file(auth_dir: Path, acsm: Path) -> Path:
    digest = hashlib.sha256(acsm.read_bytes()).hexdigest()[:16]
    return auth_dir / "pending" / f"{digest}.json"


class TestSave:
    def test_record_fields(self, auth_dir, acsm, out_dir, frozen_time):
        link = fulfiller(auth_dir)._save_pending(acsm, out_dir, INFO)

        record = json.loads(pending_file(auth_dir, acsm).read_text("utf-8"))
        assert record == {
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "acsm": "book.acsm",
            "key_fingerprint": hashlib.sha256(DEVICE_KEY).hexdigest(),
            "link_file": str(link),
            "info": INFO,
        }

    def test_link_page_is_written_into_the_folder_passed_in(self, auth_dir, acsm, out_dir):
        link = fulfiller(auth_dir)._save_pending(acsm, out_dir, INFO)

        assert link == (out_dir / "Tom and Jerry - Part_1 - download link.html").resolve()
        page = link.read_text("utf-8")
        assert "<title>Download: Tom and Jerry - Part_1</title>" in page
        assert 'href="https://download.example.com/b.epub?id=1&amp;output=epub"' in page
        assert f'book-loader process "{acsm.resolve()}" --downloaded-file' in page

    def test_record_is_named_after_the_acsm_content(self, auth_dir, acsm, out_dir):
        fulfiller(auth_dir)._save_pending(acsm, out_dir, INFO)
        assert [p.name for p in (auth_dir / "pending").iterdir()] == [
            pending_file(auth_dir, acsm).name
        ]

    def test_fingerprint_is_empty_when_the_key_cannot_be_read(self, auth_dir, acsm, out_dir):
        def broken():
            raise OSError("no key")

        account = types.SimpleNamespace(auth_dir=auth_dir, get_device_key=broken)
        ACSMFulfiller(account)._save_pending(acsm, out_dir, INFO)
        record = json.loads(pending_file(auth_dir, acsm).read_text("utf-8"))
        assert record["key_fingerprint"] == ""

    @pytest.mark.posix_only
    def test_file_modes(self, auth_dir, acsm, out_dir):
        fulfiller(auth_dir)._save_pending(acsm, out_dir, INFO)
        assert stat.S_IMODE(pending_file(auth_dir, acsm).stat().st_mode) == 0o600
        assert stat.S_IMODE((auth_dir / "pending").stat().st_mode) == 0o700


class TestLoad:
    def test_round_trip(self, auth_dir, acsm, out_dir):
        fulfiller(auth_dir)._save_pending(acsm, out_dir, INFO)
        assert fulfiller(auth_dir)._load_pending(acsm) == INFO

    def test_mismatched_fingerprint_is_ignored_with_a_warning(self, auth_dir, acsm, out_dir):
        fulfiller(auth_dir)._save_pending(acsm, out_dir, INFO)
        events = []
        libadobe.set_status_callback(lambda event, **data: events.append((event, data)))

        assert fulfiller(auth_dir, key=b"another key")._load_pending(acsm) is None
        assert events == [
            (
                "warning",
                {"message": "Ignoring a saved license that belongs to a different authorization"},
            )
        ]

    def test_missing_record(self, auth_dir, acsm):
        assert fulfiller(auth_dir)._load_pending(acsm) is None

    def test_corrupt_record(self, auth_dir, acsm):
        path = pending_file(auth_dir, acsm)
        path.parent.mkdir()
        path.write_text("{not json", encoding="utf-8")
        assert fulfiller(auth_dir)._load_pending(acsm) is None

    def test_v0_record_from_the_fixtures(self, auth_dir, tmp_path):
        """A record 0.1.0 wrote (T0.4.5 fixture) loads with the key it was saved under."""
        auth = build_auth_folder(auth_dir, seed="v0")
        acsm = tmp_path / "The Test Book.acsm"
        shutil.copyfile(V0 / "pending_book.acsm", acsm)
        record = next((V0 / "pending").glob("*.json"))
        (auth_dir / "pending").mkdir()
        shutil.copyfile(record, auth_dir / "pending" / record.name)

        info = fulfiller(auth_dir, key=auth.device_key)._load_pending(acsm)

        assert info == json.loads(record.read_text("utf-8"))["info"]
        assert info["book_name"] == "The Test Book Volume 1"


class TestClear:
    def test_deletes_record_and_link_page(self, auth_dir, acsm, out_dir):
        link = fulfiller(auth_dir)._save_pending(acsm, out_dir, INFO)

        fulfiller(auth_dir)._clear_pending(acsm)

        assert not link.exists()
        assert not pending_file(auth_dir, acsm).exists()

    def test_nothing_to_clear(self, auth_dir, acsm):
        fulfiller(auth_dir)._clear_pending(acsm)
