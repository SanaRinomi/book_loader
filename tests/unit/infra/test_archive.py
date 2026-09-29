"""T2.11: ``infra/archive.py``, the backup format: manifest, encryption, safe extraction."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
import unicodedata
from datetime import UTC, datetime
from collections.abc import Sequence
from pathlib import Path

import pytest

from book_loader.domain.errors import ArchiveError
from book_loader.infra import archive
from book_loader.infra.archive import (
    CHUNK_SIZE,
    MANIFEST_NAME,
    TAG_SIZE,
    ArchiveKind,
    ArchiveReader,
    Entry,
    Manifest,
    ScryptParams,
    detect,
    write_archive,
)
from book_loader.infra.secrets import Secret

V0 = Path(__file__).parents[2] / "fixtures" / "v0"
FAST = ScryptParams(log2_n=10)  # the default takes about a third of a second
PASSPHRASE = Secret("correct horse battery staple")
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
HEADER = archive._HEADER_SIZE
BLOCK = CHUNK_SIZE + TAG_SIZE


@pytest.fixture
def book(tmp_path) -> Path:
    path = tmp_path / "source" / "Book.epub"
    path.parent.mkdir()
    path.write_bytes(os.urandom(2 * CHUNK_SIZE + 12345))  # incompressible: 3 chunks
    return path


def entries(book: Path) -> list[Entry]:
    return [
        Entry("Books/Author/Book.epub", book),
        Entry("auth/activation.xml", b"<activation/>"),
        Entry("library.toml", "title = 'Caf\u00e9'\n".encode()),
    ]


def write(path: Path, book: Path, passphrase: Secret | None = PASSPHRASE, **kwargs) -> Manifest:
    kwargs.setdefault("scrypt_params", FAST)
    kwargs.setdefault("now", lambda: NOW)
    return write_archive(
        path, entries(book), {"parts": ["auth", "epub"]}, passphrase=passphrase, **kwargs
    )


def tree(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def raw_tar(path: Path, members: Sequence[tuple[tarfile.TarInfo, bytes | None]]) -> Path:
    """A hand-made .tar.gz, for archives book-loader would never write."""
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        for info, data in members:
            if data is not None:
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def file_member(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    return tarfile.TarInfo(name), data


def manifest_member(files: dict[str, bytes], **overrides) -> tuple[tarfile.TarInfo, bytes]:
    data = {
        "format": "book-loader-archive",
        "version": 1,
        "created": NOW.isoformat(),
        "metadata": {},
        "files": [
            {"name": n, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
            for n, d in files.items()
        ],
    } | overrides
    return file_member(MANIFEST_NAME, json.dumps(data).encode())


# --- Round trips -----------------------------------------------------------------------


@pytest.mark.parametrize("compression", ["gz", "xz"])
@pytest.mark.parametrize("encrypted", [False, True], ids=["plain", "encrypted"])
def test_round_trip(tmp_path, book, compression, encrypted):
    path = tmp_path / "backup.blbackup"
    written = write(path, book, PASSPHRASE if encrypted else None, compression=compression)
    reader = ArchiveReader(path, PASSPHRASE if encrypted else None)

    expected_kind = ArchiveKind.ENCRYPTED if encrypted else ArchiveKind(f"tar.{compression}")
    assert reader.kind is expected_kind and detect(path) is expected_kind
    assert reader.encrypted is encrypted
    assert reader.read_manifest() == written
    assert reader.names() == ["Books/Author/Book.epub", "auth/activation.xml", "library.toml"]

    out = tmp_path / "out"
    result = reader.extract(out)
    assert tree(out) == {
        "Books/Author/Book.epub": book.read_bytes(),
        "auth/activation.xml": b"<activation/>",
        "library.toml": "title = 'Caf\u00e9'\n".encode(),
    }
    assert result.files["auth/activation.xml"] == out / "auth" / "activation.xml"
    assert result.renamed == {}


def test_manifest_contents(tmp_path, book):
    manifest = write(tmp_path / "b", book, None)
    assert manifest.created == NOW
    assert manifest.metadata == {"parts": ["auth", "epub"]}
    record = manifest.file("Books/Author/Book.epub")
    assert record is not None
    assert record.size == book.stat().st_size
    assert record.sha256 == hashlib.sha256(book.read_bytes()).hexdigest()
    assert manifest.file("missing") is None
    assert Manifest.from_json(manifest.to_json()) == manifest


def test_the_manifest_is_the_first_member(tmp_path, book):
    path = tmp_path / "b.tar.gz"
    write(path, book, None)
    with tarfile.open(path) as tar:  # any tar tool opens an unencrypted backup
        assert tar.getnames()[0] == MANIFEST_NAME


def test_empty_archive(tmp_path):
    path = tmp_path / "b"
    manifest = write_archive(path, [], passphrase=PASSPHRASE, scrypt_params=FAST)
    reader = ArchiveReader(path, PASSPHRASE)
    assert reader.read_manifest() == manifest and manifest.files == ()
    assert reader.extract(tmp_path / "out").files == {}


def test_exactly_one_chunk_of_data(tmp_path):
    # The final chunk is then empty; the reader must still accept it.
    path = tmp_path / "b"
    write_archive(path, [Entry("x", b"")], passphrase=PASSPHRASE, scrypt_params=FAST)
    data = path.read_bytes()
    assert len(data) > HEADER
    assert ArchiveReader(path, PASSPHRASE).names() == ["x"]


def test_passphrase_is_compared_in_nfc(tmp_path, book):
    path = tmp_path / "b"
    write(path, book, Secret(unicodedata.normalize("NFC", "Caf\u00e9 secret")))
    typed_on_a_mac = Secret(unicodedata.normalize("NFD", "Caf\u00e9 secret"))
    assert ArchiveReader(path, typed_on_a_mac).read_manifest() is not None


def test_default_scrypt_settings(tmp_path):
    path = tmp_path / "b"
    write_archive(path, [Entry("x", b"1")], passphrase=PASSPHRASE)
    assert path.read_bytes()[9:12] == bytes([17, 8, 1])


def test_progress(tmp_path, book):
    seen: list[int] = []
    path = tmp_path / "b"
    write(path, book, progress=seen.append)
    assert seen == sorted(seen) and seen[-1] == path.stat().st_size


@pytest.mark.posix_only
def test_mode_0600(tmp_path, book):
    path = tmp_path / "b"
    write(path, book)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --- Writing ---------------------------------------------------------------------------


class TestWriting:
    @pytest.mark.parametrize(
        "name",
        ["", "/etc/passwd", "../x", "a/../b", "a\\b", "C:/x", "a//b", "./a", MANIFEST_NAME],
    )
    def test_bad_names(self, tmp_path, name):
        with pytest.raises(ValueError):
            write_archive(tmp_path / "b", [Entry(name, b"x")])
        assert not (tmp_path / "b").exists()

    def test_names_that_differ_only_in_case(self, tmp_path):
        with pytest.raises(ValueError, match="twice"):
            write_archive(tmp_path / "b", [Entry("Book.epub", b"1"), Entry("book.EPUB", b"2")])

    def test_bad_compression(self, tmp_path):
        with pytest.raises(ValueError, match="compression"):
            write_archive(tmp_path / "b", [], compression="zip")

    def test_a_failed_write_leaves_no_file(self, tmp_path, book):
        path = tmp_path / "b.blbackup"
        path.write_bytes(b"the previous backup")
        missing = tmp_path / "gone.epub"
        with pytest.raises(FileNotFoundError):
            write_archive(path, [Entry("a", book), Entry("b", missing)], scrypt_params=FAST)
        assert path.read_bytes() == b"the previous backup"
        assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []

    def test_a_failure_while_writing_leaves_no_file(self, tmp_path, book, monkeypatch):
        path = tmp_path / "b.blbackup"
        calls = []

        def fail_late(tar, name, source, mtime):
            calls.append(name)
            if len(calls) == 3:
                raise OSError(28, "No space left on device")
            real_add(tar, name, source, mtime)

        real_add = archive._add
        monkeypatch.setattr(archive, "_add", fail_late)
        with pytest.raises(OSError, match="No space"):
            write(path, book)
        assert not path.exists()
        assert list(tmp_path.glob("*.tmp")) == []


# --- Encryption ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def encrypted_bytes(tmp_path_factory) -> bytes:
    """One encrypted backup of three chunks, built once for the tests that damage it."""
    folder = tmp_path_factory.mktemp("encrypted")
    source = folder / "Book.epub"
    source.write_bytes(os.urandom(2 * CHUNK_SIZE + 12345))  # incompressible
    path = folder / "b.blbackup"
    write(path, source, compression="gz")
    data = path.read_bytes()
    assert len(data) > HEADER + 2 * BLOCK
    return data


class TestEncryption:
    @pytest.fixture
    def encrypted(self, tmp_path, encrypted_bytes) -> Path:
        path = tmp_path / "b.blbackup"
        path.write_bytes(encrypted_bytes)
        return path

    def test_wrong_passphrase(self, encrypted):
        with pytest.raises(ArchiveError, match="Wrong passphrase") as info:
            ArchiveReader(encrypted, Secret("wrong")).read_manifest()
        assert "passphrase" in (info.value.hint or "")

    def test_no_passphrase(self, encrypted):
        reader = ArchiveReader(encrypted)
        assert reader.encrypted
        with pytest.raises(ArchiveError, match="is encrypted") as info:
            reader.read_manifest()
        assert "--passphrase-file" in (info.value.hint or "")

    def test_the_key_is_derived_once_per_reader(self, encrypted, monkeypatch):
        calls = []
        real = archive._keys

        def counting(passphrase, salt, params):
            calls.append(1)
            return real(passphrase, salt, params)

        monkeypatch.setattr(archive, "_keys", counting)
        reader = ArchiveReader(encrypted, PASSPHRASE)
        reader.read_manifest()
        reader.names()
        reader.extract(encrypted.parent / "out")
        assert len(calls) == 1

    @pytest.mark.parametrize("chunk", [0, 1, 2])
    @pytest.mark.parametrize("where", ["start", "middle", "tag"])
    def test_a_flipped_byte_in_any_chunk(self, encrypted, tmp_path, chunk, where):
        data = bytearray(encrypted.read_bytes())
        start = HEADER + chunk * BLOCK
        end = min(start + BLOCK, len(data))
        offset = {"start": start, "middle": (start + end) // 2, "tag": end - 1}[where]
        data[offset] ^= 0x01
        encrypted.write_bytes(bytes(data))
        with pytest.raises(ArchiveError, match="damaged|ends too early"):
            ArchiveReader(encrypted, PASSPHRASE).extract(tmp_path / "out")

    @pytest.mark.parametrize("offset", range(0, HEADER, 5))
    def test_a_flipped_byte_in_the_header(self, encrypted, offset):
        data = bytearray(encrypted.read_bytes())
        data[offset] ^= 0x01
        encrypted.write_bytes(bytes(data))
        with pytest.raises(ArchiveError):
            ArchiveReader(encrypted, PASSPHRASE).read_manifest()

    def test_a_changed_header_that_passes_its_checksum(self, encrypted):
        # Someone recomputes the digest after editing the salt: the key check catches it.
        data = bytearray(encrypted.read_bytes())
        data[15] ^= 0x01
        head_and_check = bytes(data[: HEADER - 8])
        data[HEADER - 8 : HEADER] = hashlib.sha256(head_and_check).digest()[:8]
        encrypted.write_bytes(bytes(data))
        with pytest.raises(ArchiveError, match="Wrong passphrase"):
            ArchiveReader(encrypted, PASSPHRASE).read_manifest()

    @pytest.mark.parametrize("cut", ["boundary", "mid-chunk", "tag"])
    def test_a_truncated_file(self, encrypted, tmp_path, cut):
        data = encrypted.read_bytes()
        length = {
            "boundary": HEADER + 2 * BLOCK,
            "mid-chunk": HEADER + 2 * BLOCK + 100,
            "tag": HEADER + 2 * BLOCK + 5,
        }[cut]
        encrypted.write_bytes(data[:length])
        with pytest.raises(ArchiveError, match="ends too early"):
            ArchiveReader(encrypted, PASSPHRASE).extract(tmp_path / "out")

    def test_cut_inside_the_header(self, encrypted):
        encrypted.write_bytes(encrypted.read_bytes()[: HEADER - 3])
        with pytest.raises(ArchiveError):
            ArchiveReader(encrypted, PASSPHRASE).read_manifest()

    def test_reordered_chunks(self, encrypted, tmp_path):
        data = encrypted.read_bytes()
        head, first, second = (
            data[:HEADER],
            data[HEADER : HEADER + BLOCK],
            data[HEADER + BLOCK : HEADER + 2 * BLOCK],
        )
        encrypted.write_bytes(head + second + first + data[HEADER + 2 * BLOCK :])
        with pytest.raises(ArchiveError, match="damaged"):
            ArchiveReader(encrypted, PASSPHRASE).extract(tmp_path / "out")

    def test_an_added_chunk(self, encrypted, tmp_path):
        data = encrypted.read_bytes()
        encrypted.write_bytes(data + data[HEADER : HEADER + BLOCK])
        with pytest.raises(ArchiveError):
            ArchiveReader(encrypted, PASSPHRASE).extract(tmp_path / "out")

    def test_unsupported_settings_are_refused(self, encrypted):
        data = bytearray(encrypted.read_bytes())
        data[9] = 30  # N = 2**30 would need 128 GiB of memory
        data[HEADER - 8 : HEADER] = hashlib.sha256(bytes(data[: HEADER - 8])).digest()[:8]
        encrypted.write_bytes(bytes(data))
        with pytest.raises(ArchiveError, match="Unsupported encryption settings"):
            ArchiveReader(encrypted, PASSPHRASE).read_manifest()

    def test_unknown_format_version(self, encrypted):
        data = bytearray(encrypted.read_bytes())
        data[8] = 9
        data[HEADER - 8 : HEADER] = hashlib.sha256(bytes(data[: HEADER - 8])).digest()[:8]
        encrypted.write_bytes(bytes(data))
        with pytest.raises(ArchiveError, match="format 9") as info:
            ArchiveReader(encrypted, PASSPHRASE).read_manifest()
        assert "Update book-loader" in (info.value.hint or "")

    def test_a_bad_chunk_size(self, encrypted):
        data = bytearray(encrypted.read_bytes())
        data[35:39] = (0).to_bytes(4, "big")
        data[HEADER - 8 : HEADER] = hashlib.sha256(bytes(data[: HEADER - 8])).digest()[:8]
        encrypted.write_bytes(bytes(data))
        with pytest.raises(ArchiveError, match="header is damaged"):
            ArchiveReader(encrypted, PASSPHRASE).read_manifest()

    @pytest.mark.parametrize(
        "params", [ScryptParams(9), ScryptParams(21), ScryptParams(r=0), ScryptParams(p=5)]
    )
    def test_writer_refuses_bad_settings(self, tmp_path, params):
        with pytest.raises(ArchiveError, match="Unsupported"):
            write_archive(tmp_path / "b", [], passphrase=PASSPHRASE, scrypt_params=params)
        assert not (tmp_path / "b").exists()

    def test_each_backup_uses_a_new_salt_and_nonce(self, tmp_path):
        first, second = tmp_path / "1", tmp_path / "2"
        for path in (first, second):
            write_archive(path, [Entry("x", b"same")], passphrase=PASSPHRASE, scrypt_params=FAST)
        assert first.read_bytes()[12:35] != second.read_bytes()[12:35]
        assert first.read_bytes()[HEADER:] != second.read_bytes()[HEADER:]


# --- The manifest without the rest ----------------------------------------------------


class TestReadManifestOnly:
    def test_plain_archive_cut_after_the_manifest(self, tmp_path, book):
        path = tmp_path / "b.tar.gz"
        written = write(path, book, None)
        path.write_bytes(path.read_bytes()[:4096])
        reader = ArchiveReader(path)
        assert reader.read_manifest() == written
        with pytest.raises(ArchiveError):
            reader.extract(tmp_path / "out")

    def test_encrypted_archive_reads_only_the_start(self, tmp_path, book):
        path = tmp_path / "b.blbackup"
        written = write(path, book)
        # Keep the first chunk and the start of the second, which tells it isn't final.
        path.write_bytes(path.read_bytes()[: HEADER + BLOCK + 10])
        assert ArchiveReader(path, PASSPHRASE).read_manifest() == written


# --- Extraction ------------------------------------------------------------------------


class TestExtraction:
    @pytest.fixture
    def out(self, tmp_path) -> Path:
        return tmp_path / "out"

    def test_select(self, tmp_path, book, out):
        path = tmp_path / "b"
        write(path, book)
        result = ArchiveReader(path, PASSPHRASE).extract(out, lambda n: n.startswith("auth/"))
        assert list(result.files) == ["auth/activation.xml"]
        assert tree(out) == {"auth/activation.xml": b"<activation/>"}

    def test_nothing_is_overwritten(self, tmp_path, book, out):
        path = tmp_path / "b"
        write(path, book, None)
        (out / "auth").mkdir(parents=True)
        (out / "auth" / "activation.xml").write_bytes(b"current")
        with pytest.raises(ArchiveError, match="would overwrite") as info:
            ArchiveReader(path).extract(out)
        assert (out / "auth" / "activation.xml").read_bytes() == b"current"
        assert "empty folder" in (info.value.hint or "")

    @pytest.mark.parametrize(
        "info, why",
        [
            (tarfile.TarInfo("/etc/evil"), "absolute path"),
            (tarfile.TarInfo("../evil"), "leading outside"),
            (tarfile.TarInfo("a/../../evil"), "leading outside"),
        ],
    )
    def test_unsafe_names(self, tmp_path, out, info, why):
        path = raw_tar(tmp_path / "evil.tar.gz", [manifest_member({}), (info, b"x")])
        with pytest.raises(ArchiveError, match="unsafe name"):
            ArchiveReader(path).extract(out)
        assert not (tmp_path / "evil").exists() and not Path("/etc/evil").exists()

    @pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE])
    def test_links_and_devices(self, tmp_path, out, kind):
        link = tarfile.TarInfo("escape")
        link.type = kind
        link.linkname = "../../outside"
        path = raw_tar(tmp_path / "evil.tar.gz", [manifest_member({}), (link, None)])
        with pytest.raises(ArchiveError, match="link or a special file"):
            ArchiveReader(path).extract(out)
        assert not (out / "escape").exists()

    def test_names_invalid_on_windows_or_in_nfd(self, tmp_path, out):
        files = {
            "Books/Title: Part 1?.epub": b"1",
            "Books/CON.epub": b"2",
            "Books/trailing dot./x.epub": b"3",
            unicodedata.normalize("NFD", "Books/Caf\u00e9.epub"): b"4",
        }
        members = [manifest_member(files)] + [file_member(n, d) for n, d in files.items()]
        path = raw_tar(tmp_path / "b.tar.gz", members)
        result = ArchiveReader(path).extract(out)
        assert tree(out) == {
            "Books/Title_ Part 1_.epub": b"1",
            "Books/CON_.epub": b"2",
            "Books/trailing dot/x.epub": b"3",
            "Books/Caf\u00e9.epub": b"4",
        }
        assert set(result.renamed) == set(files)

    def test_case_collisions(self, tmp_path, out):
        files = {"Books/Book.epub": b"upper", "Books/book.epub": b"lower", "Books/BOOK.epub": b"x"}
        members = [manifest_member(files)] + [file_member(n, d) for n, d in files.items()]
        result = ArchiveReader(raw_tar(tmp_path / "b.tar.gz", members)).extract(out)
        assert tree(out) == {
            "Books/Book.epub": b"upper",
            "Books/book (2).epub": b"lower",
            "Books/BOOK (3).epub": b"x",
        }
        assert set(result.renamed) == {"Books/book.epub", "Books/BOOK.epub"}

    def test_case_collision_without_an_extension(self, tmp_path, out):
        files = {"README": b"1", "readme": b"2"}
        members = [manifest_member(files)] + [file_member(n, d) for n, d in files.items()]
        ArchiveReader(raw_tar(tmp_path / "b.tar.gz", members)).extract(out)
        assert tree(out) == {"README": b"1", "readme (2)": b"2"}

    def test_a_file_that_does_not_match_the_manifest(self, tmp_path, out):
        manifest = manifest_member({"a.epub": b"original"})
        path = raw_tar(tmp_path / "b.tar.gz", [manifest, file_member("a.epub", b"tampered")])
        with pytest.raises(ArchiveError, match="doesn't match"):
            ArchiveReader(path).extract(out)
        assert not (out / "a.epub").exists()

    def test_a_file_the_manifest_does_not_list(self, tmp_path, out):
        path = raw_tar(tmp_path / "b.tar.gz", [manifest_member({}), file_member("extra", b"x")])
        with pytest.raises(ArchiveError, match="isn't listed"):
            ArchiveReader(path).extract(out)

    def test_a_file_the_manifest_lists_is_missing(self, tmp_path, out):
        manifest = manifest_member({"a": b"1", "b": b"2"})
        path = raw_tar(tmp_path / "b.tar.gz", [manifest, file_member("a", b"1")])
        with pytest.raises(ArchiveError, match="missing 1 file"):
            ArchiveReader(path).extract(out)
        # With a selection, only the selected files have to be there.
        assert list(ArchiveReader(path).extract(out / "2", lambda n: n == "a").files) == ["a"]

    def test_folders_are_created_not_trusted(self, tmp_path, out):
        folder = tarfile.TarInfo("Books")
        folder.type = tarfile.DIRTYPE
        files = {"Books/a.epub": b"1"}
        members = [manifest_member(files), (folder, None), file_member("Books/a.epub", b"1")]
        ArchiveReader(raw_tar(tmp_path / "b.tar.gz", members)).extract(out)
        assert tree(out) == {"Books/a.epub": b"1"}

    def test_a_write_error_is_reported_as_one(self, tmp_path, book, out, monkeypatch):
        path = tmp_path / "b"
        write(path, book, None)

        def full(source, target, record):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(archive, "_write_checked", full)
        with pytest.raises(ArchiveError, match="Can't write") as info:
            ArchiveReader(path).extract(out)
        assert "space" in (info.value.hint or "")

    def test_newer_manifest_version(self, tmp_path, out):
        path = raw_tar(tmp_path / "b.tar.gz", [manifest_member({}, version=99)])
        with pytest.raises(ArchiveError, match="newer book-loader"):
            ArchiveReader(path).read_manifest()

    @pytest.mark.parametrize(
        "raw", [b"not json", b'{"format": "other"}', b'{"format": "book-loader-archive"}']
    )
    def test_unreadable_manifest(self, tmp_path, raw):
        path = raw_tar(tmp_path / "b.tar.gz", [(tarfile.TarInfo(MANIFEST_NAME), raw)])
        with pytest.raises(ArchiveError, match="manifest can't be read"):
            ArchiveReader(path).read_manifest()

    def test_empty_tar(self, tmp_path):
        path = raw_tar(tmp_path / "b.tar.gz", [])
        with pytest.raises(ArchiveError, match="empty"):
            ArchiveReader(path).read_manifest()

    def test_not_an_archive(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("hello")
        with pytest.raises(ArchiveError, match="isn't a book-loader backup"):
            ArchiveReader(path)

    def test_corrupt_gzip(self, tmp_path, book):
        path = tmp_path / "b.tar.gz"
        write(path, book, None)
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 0xFF
        path.write_bytes(bytes(data))
        with pytest.raises(ArchiveError, match="can't be read|doesn't match"):
            ArchiveReader(path).extract(tmp_path / "out")


# --- Original (0.1.0) archives ---------------------------------------------------------


class TestOriginalArchives:
    @pytest.mark.parametrize(
        "name", ["auth_backup_20260101_000000.tar.gz", "auth_anonymous_20260101_000000.tar.gz"]
    )
    def test_v0_fixture(self, tmp_path, name):
        reader = ArchiveReader(V0 / name)
        assert reader.kind is ArchiveKind.TAR_GZ
        assert reader.read_manifest() is None
        assert reader.legacy_root() == ".adobe"
        out = tmp_path / "custom-auth"
        result = reader.extract(out)
        with tarfile.open(V0 / name) as tar:
            expected = {
                m.name.split("/", 1)[1]: tar.extractfile(m).read()  # type: ignore[union-attr]
                for m in tar
                if m.isfile()
            }
        assert tree(out) == expected
        assert set(expected) == {"activation.xml", "device.xml", "devicesalt"}
        assert result.renamed == {}

    def test_any_top_folder_name(self, tmp_path):
        folder = tarfile.TarInfo("my-auth")
        folder.type = tarfile.DIRTYPE
        path = raw_tar(
            tmp_path / "old.tar.gz",
            [(folder, None), file_member("my-auth/device.xml", b"<d/>")],
        )
        reader = ArchiveReader(path)
        assert reader.legacy_root() == "my-auth"
        reader.extract(tmp_path / "out")
        assert tree(tmp_path / "out") == {"device.xml": b"<d/>"}

    def test_more_than_one_top_folder(self, tmp_path):
        path = raw_tar(
            tmp_path / "other.tar.gz", [file_member("a/x", b"1"), file_member("b/y", b"2")]
        )
        with pytest.raises(ArchiveError, match="doesn't hold one folder"):
            ArchiveReader(path).extract(tmp_path / "out")
