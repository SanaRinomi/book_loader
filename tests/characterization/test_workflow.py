"""T0.4.8: ``BookLoader.process_acsm`` in 0.1.0, with fulfillment, DRM removal and
conversion replaced by fakes. The step lines it prints are pinned as well."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

import book_loader.core.workflow as workflow
from book_loader.core.workflow import BookLoader
from book_loader.utils.config import Config
from book_loader.utils.console import StepReporter
from book_loader.utils.errors import ACSMFulfillmentError, ManualDownloadRequired
from tests.fixtures.builders.adobe_auth import build_auth_folder

ENCRYPTED = b"encrypted book"


class Fakes:
    """Records what the workflow asked of the fulfiller, DRM remover and converter."""

    def __init__(self, loader: BookLoader, monkeypatch, fmt: str = "epub"):
        self.fmt = fmt
        self.drm_keys: list[bytes] = []
        self.conversions: list[tuple[Path, Path]] = []
        self.convert_error: Exception | None = None
        self.block_download = False
        monkeypatch.setattr(loader.fulfiller, "fulfill", self.fulfill)
        monkeypatch.setattr(loader.fulfiller, "fulfill_from_file", self.fulfill_from_file)
        monkeypatch.setattr(loader.drm_remover, "remove_drm", self.remove_drm)
        monkeypatch.setattr(workflow, "ConversionEngine", self._engine)

    def _write_encrypted(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"Title.{self.fmt}"
        path.write_bytes(ENCRYPTED)
        return path

    def fulfill(self, acsm_path, output_dir, verbose=False, status=None):
        if self.block_download:
            link = output_dir / "Title - download link.html"
            raise ManualDownloadRequired("HTTP 429", "https://dl.example.com/x", link, acsm_path)
        return self._write_encrypted(output_dir)

    def fulfill_from_file(self, acsm_path, downloaded_file, output_dir, verbose=False, status=None):
        if downloaded_file.suffix == ".html":
            raise ACSMFulfillmentError("Could not use the downloaded file: not an ebook")
        return self._write_encrypted(output_dir)

    def remove_drm(self, encrypted_path, output_path, user_key):
        self.drm_keys.append(user_key)
        output_path.write_bytes(b"decrypted " + encrypted_path.read_bytes())

    def _engine(self, engine="python"):
        fakes = self

        class Engine:
            def convert_epub_to_pdf(self, epub_path, pdf_path):
                if fakes.convert_error:
                    raise fakes.convert_error
                fakes.conversions.append((epub_path, pdf_path))
                pdf_path.write_bytes(b"%PDF from " + epub_path.read_bytes())

        return Engine()


@pytest.fixture
def auth(auth_dir):
    return build_auth_folder(auth_dir)


@pytest.fixture
def loader(auth):
    return BookLoader(Config(auth_dir=auth.path))


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out"


@pytest.fixture
def acsm(tmp_path):
    path = tmp_path / "book.acsm"
    path.write_bytes(b"<fulfillmentToken/>")
    return path


def run(loader, acsm, out, to_pdf=False, **kwargs):
    stream = io.StringIO()
    reporter = StepReporter(BookLoader.step_count(to_pdf), stream=stream)
    out.mkdir(exist_ok=True)
    result = loader.process_acsm(acsm, out, to_pdf=to_pdf, reporter=reporter, **kwargs)
    return result, stream.getvalue()


class TestSteps:
    def test_three_steps(self, loader, acsm, out, monkeypatch):
        fakes = Fakes(loader, monkeypatch)
        result, output = run(loader, acsm, out)

        assert result == out / "Title.epub"
        assert result.read_bytes() == b"decrypted " + ENCRYPTED
        assert output == (
            "[1/3] Checking for Auth... Auth Detected: Anonymous.\n"
            "[2/3] Downloading Encrypted File... Done (EPUB, 0.0 MB).\n"
            "[3/3] Removing DRM... Done.\n"
        )
        assert fakes.conversions == []

    def test_four_steps_with_to_pdf(self, loader, acsm, out, monkeypatch):
        fakes = Fakes(loader, monkeypatch)
        result, output = run(loader, acsm, out, to_pdf=True)

        assert result == out / "Title.pdf"
        assert fakes.conversions == [(out / "Title.epub", out / "Title.pdf")]
        assert output == (
            "[1/4] Checking for Auth... Auth Detected: Anonymous.\n"
            "[2/4] Downloading Encrypted File... Done (EPUB, 0.0 MB).\n"
            "[3/4] Removing DRM... Done.\n"
            "[4/4] Converting to PDF (python)... Done.\n"
        )

    def test_adobe_id_is_named_in_step_one(self, auth_dir, acsm, out, monkeypatch):
        build_auth_folder(auth_dir, method="AdobeID")
        loader = BookLoader(Config(auth_dir=auth_dir))
        Fakes(loader, monkeypatch)
        _, output = run(loader, acsm, out)
        assert output.startswith("[1/3] Checking for Auth... Auth Detected: Adobe ID.\n")

    def test_missing_authorization_is_created(self, tmp_path, acsm, out, monkeypatch):
        loader = BookLoader(Config(auth_dir=tmp_path / "new-auth"))
        Fakes(loader, monkeypatch)
        created = []

        def authorize():
            created.append(True)
            build_auth_folder(tmp_path / "new-auth")

        monkeypatch.setattr(loader.account, "authorize_anonymous", authorize)
        _, output = run(loader, acsm, out)

        assert created == [True]
        assert output.startswith(
            "[1/3] Checking for Auth... None found. Creating anonymous authorization... Done.\n"
        )

    def test_drm_is_removed_with_the_activation_key(self, loader, auth, acsm, out, monkeypatch):
        fakes = Fakes(loader, monkeypatch)
        run(loader, acsm, out)
        assert fakes.drm_keys == [auth.device_key]

    def test_optimize_is_a_note_only(self, loader, acsm, out, monkeypatch):
        Fakes(loader, monkeypatch)
        _, output = run(loader, acsm, out, optimize=True)
        assert output.endswith("[3/3] Optimization is not implemented yet, skipped.\n")

    def test_downloaded_file_skips_the_download(self, loader, acsm, out, tmp_path, monkeypatch):
        Fakes(loader, monkeypatch)
        downloaded = tmp_path / "from-browser.epub"
        downloaded.write_bytes(ENCRYPTED)
        _, output = run(loader, acsm, out, downloaded_file=downloaded)
        assert "[2/3] Using downloaded file: from-browser.epub... Done (EPUB, 0.0 MB).\n" in output


class TestRetention:
    def test_encrypted_file_and_temp_folder_removed_on_success(
        self, loader, acsm, out, monkeypatch
    ):
        Fakes(loader, monkeypatch)
        run(loader, acsm, out)
        assert sorted(p.name for p in out.iterdir()) == ["Title.epub"]

    def test_keep_encrypted_keeps_them(self, loader, acsm, out, monkeypatch):
        Fakes(loader, monkeypatch)
        run(loader, acsm, out, keep_encrypted=True)
        assert (out / ".temp" / "Title.epub").read_bytes() == ENCRYPTED

    def test_to_pdf_deletes_the_decrypted_epub(self, loader, acsm, out, monkeypatch):
        Fakes(loader, monkeypatch)
        run(loader, acsm, out, to_pdf=True)
        assert sorted(p.name for p in out.iterdir()) == ["Title.pdf"]

    def test_keep_encrypted_also_keeps_the_decrypted_epub(self, loader, acsm, out, monkeypatch):
        """--keep-encrypted has two jobs in 0.1.0 (REFACTOR_PLAN §3)."""
        Fakes(loader, monkeypatch)
        run(loader, acsm, out, to_pdf=True, keep_encrypted=True)
        assert sorted(p.name for p in out.iterdir()) == [".temp", "Title.epub", "Title.pdf"]

    def test_failed_conversion_keeps_the_epub_and_warns(self, loader, acsm, out, monkeypatch):
        fakes = Fakes(loader, monkeypatch)
        fakes.convert_error = RuntimeError("boom")

        result, output = run(loader, acsm, out, to_pdf=True)

        assert result == out / "Title.epub" and result.exists()
        assert output.endswith(
            "[4/4] Converting to PDF (python)... ERROR!\n"
            "[4/4] WARNING: PDF conversion failed: boom\n"
            f"[4/4] Keeping the EPUB: {out / 'Title.epub'}\n"
        )

    def test_to_pdf_on_a_pdf_is_skipped(self, loader, acsm, out, monkeypatch):
        fakes = Fakes(loader, monkeypatch, fmt="pdf")
        result, output = run(loader, acsm, out, to_pdf=True)

        assert result == out / "Title.pdf"
        assert result.read_bytes() == b"decrypted " + ENCRYPTED
        assert fakes.conversions == []
        assert output.endswith(
            "[4/4] Converting to PDF (python)... Skipped, the book is already a PDF.\n"
        )

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="Data-loss bug: --to-pdf writes the decrypted EPUB over the user's own "
        "Title.epub and then deletes it (REFACTOR_PLAN §3). T5.1 must make this pass.",
    )
    def test_to_pdf_keeps_an_existing_epub_of_the_same_name(self, loader, acsm, out, monkeypatch):
        Fakes(loader, monkeypatch)
        out.mkdir()
        (out / "Title.epub").write_bytes(b"the user's own file")

        run(loader, acsm, out, to_pdf=True)

        assert (out / "Title.epub").exists()
        assert (out / "Title.epub").read_bytes() == b"the user's own file"


class TestManualDownload:
    def test_wrong_file_then_right_file(self, loader, acsm, out, tmp_path, monkeypatch):
        fakes = Fakes(loader, monkeypatch)
        fakes.block_download = True
        wrong, right = tmp_path / "check-page.html", tmp_path / "book.epub"
        wrong.write_text("<html>captcha</html>")
        right.write_bytes(ENCRYPTED)
        calls = []

        def handler(blocked, last_error):
            calls.append((blocked, last_error))
            return wrong if len(calls) == 1 else right

        result, output = run(loader, acsm, out, manual_download=handler)

        assert result == out / "Title.epub"
        assert len(calls) == 2
        assert isinstance(calls[0][0], ManualDownloadRequired) and calls[0][1] is None
        assert calls[1][0] is calls[0][0]
        assert isinstance(calls[1][1], ACSMFulfillmentError)
        assert output == (
            "[1/3] Checking for Auth... Auth Detected: Anonymous.\n"
            "[2/3] Downloading Encrypted File... ERROR!\n"
            "[2/3] Using downloaded file: check-page.html... ERROR!\n"
            "[2/3] Using downloaded file: book.epub... Done (EPUB, 0.0 MB).\n"
            "[3/3] Removing DRM... Done.\n"
        )

    def test_without_a_handler_the_error_is_raised(self, loader, acsm, out, monkeypatch):
        Fakes(loader, monkeypatch).block_download = True
        with pytest.raises(ManualDownloadRequired) as info:
            run(loader, acsm, out, manual_download=None)
        assert info.value.url == "https://dl.example.com/x"

    def test_handler_giving_up_raises_the_original_error(self, loader, acsm, out, monkeypatch):
        Fakes(loader, monkeypatch).block_download = True
        with pytest.raises(ManualDownloadRequired, match="HTTP 429"):
            run(loader, acsm, out, manual_download=lambda blocked, error: None)
