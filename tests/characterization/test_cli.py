"""T0.4.9: the 0.1.0 CLI through Click's ``CliRunner``.

Anything that would reach Adobe is replaced with a fake; the network guard in
``tests/conftest.py`` fails the test if something slips through.
"""

from __future__ import annotations

import tarfile
from datetime import datetime

import pytest

from book_loader.cli import cli
from book_loader.core.adobe.fulfill import ACSMFulfiller
from book_loader.core.drm.remover import DRMRemover
from book_loader.utils.errors import ManualDownloadRequired
from tests.fixtures.builders.adobe_auth import build_auth_folder


@pytest.fixture
def acsm(tmp_path):
    path = tmp_path / "book.acsm"
    path.write_bytes(b"<fulfillmentToken/>")
    return path


class TestInfo:
    def test_info_on_an_empty_auth_folder(self, cli_runner, auth_dir):
        result = cli_runner.invoke(cli, ["info"])
        assert result.exit_code == 0, result.output
        assert f"Authorization directory: {auth_dir}" in result.stdout
        assert "Authorization status: Not authorized" in result.stdout

    def test_auth_info_on_an_empty_auth_folder(self, cli_runner, auth_dir):
        result = cli_runner.invoke(cli, ["auth", "info"])
        assert result.exit_code == 0, result.output
        assert "Authorization status: Not authorized" in result.stdout
        assert "Hint: Run 'book-loader auth create' to create authorization" in result.stdout

    def test_auth_info_names_an_adobe_id(self, cli_runner, auth_dir):
        build_auth_folder(auth_dir, method="AdobeID", email="reader@example.com")
        result = cli_runner.invoke(cli, ["auth", "info"])
        assert result.exit_code == 0, result.output
        assert "Authorization type: Adobe ID" in result.stdout
        assert "Adobe ID: reader@example.com" in result.stdout


class TestAuth:
    def test_create_when_already_authorized_warns(self, cli_runner, auth_dir):
        build_auth_folder(auth_dir)
        result = cli_runner.invoke(cli, ["auth", "create"])
        assert result.exit_code == 0, result.output
        assert "Authorization already exists" in result.stdout

    def test_backup_writes_an_archive_named_after_the_type(
        self, cli_runner, auth_dir, tmp_path, frozen_time
    ):
        build_auth_folder(auth_dir)
        result = cli_runner.invoke(cli, ["auth", "backup", "-o", str(tmp_path / "bk")])

        assert result.exit_code == 0, result.output
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        assert [p.name for p in (tmp_path / "bk").iterdir()] == [f"auth_anonymous_{stamp}.tar.gz"]

    def test_reset_backs_up_then_deletes(self, cli_runner, auth_dir, tmp_home, frozen_time):
        build_auth_folder(auth_dir)
        result = cli_runner.invoke(cli, ["auth", "reset", "--yes"])

        assert result.exit_code == 0, result.output
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = tmp_home / "adobe-ade-auth-bk" / f"auth_backup_{stamp}.tar.gz"
        with tarfile.open(backup) as tar:
            assert f"{auth_dir.name}/activation.xml" in tar.getnames()
        assert list(auth_dir.iterdir()) == []

    @pytest.mark.filterwarnings("ignore:Python 3.14 will:DeprecationWarning")
    def test_restore_from_a_file(self, cli_runner, auth_dir, tmp_path):
        """--file restores into the auth folder when the names match (see T0.4.4)."""
        from book_loader.cli import backup_auth

        source = build_auth_folder(tmp_path / "elsewhere" / auth_dir.name)
        archive = backup_auth(source.path, tmp_path / "b.tar.gz")

        result = cli_runner.invoke(cli, ["auth", "restore", "--file", str(archive)])

        assert result.exit_code == 0, result.output
        assert (auth_dir / "activation.xml").read_bytes() == (
            source.path / "activation.xml"
        ).read_bytes()


class TestKobo:
    def test_missing_source_folder(self, cli_runner, tmp_path):
        result = cli_runner.invoke(cli, ["kobo", "--source", str(tmp_path / "none"), "list"])
        assert result.exit_code == 1
        assert "Kobo Desktop Edition directory not found" in result.stderr

    def test_overwrite_and_skip_existing_together(self, cli_runner):
        result = cli_runner.invoke(cli, ["kobo", "dedrm", "--overwrite", "--skip-existing"])
        assert result.exit_code == 1
        assert "--overwrite and --skip-existing cannot be used together" in result.stderr


class TestConvert:
    def test_missing_file_is_rejected_by_click(self, cli_runner, tmp_path):
        result = cli_runner.invoke(cli, ["convert", str(tmp_path / "missing.epub")])
        assert result.exit_code == 2
        assert "does not exist" in result.stderr


class TestProcess:
    def test_blocked_download_without_a_terminal(
        self, cli_runner, auth_dir, acsm, tmp_path, monkeypatch
    ):
        build_auth_folder(auth_dir)

        def blocked(self, acsm_path, output_dir, verbose=False, status=None):
            link = output_dir / "Title - download link.html"
            raise ManualDownloadRequired("HTTP 429", "https://dl.example.com/x", link, acsm_path)

        monkeypatch.setattr(ACSMFulfiller, "fulfill", blocked)
        result = cli_runner.invoke(cli, ["process", str(acsm), "-o", str(tmp_path / "out")])

        assert result.exit_code == 1
        assert "[2/3] Downloading Encrypted File... ERROR!" in result.stdout
        assert "[ERROR 2/3] Download failed: HTTP 429" in result.stderr
        assert "Run the same command again, adding:  --downloaded-file" in result.stderr

    def test_success(self, cli_runner, auth_dir, acsm, tmp_path, monkeypatch):
        build_auth_folder(auth_dir)

        def fulfill(self, acsm_path, output_dir, verbose=False, status=None):
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "Title.epub"
            path.write_bytes(b"encrypted")
            return path

        def remove_drm(self, encrypted_path, output_path, user_key):
            output_path.write_bytes(b"decrypted")

        monkeypatch.setattr(ACSMFulfiller, "fulfill", fulfill)
        monkeypatch.setattr(DRMRemover, "remove_drm", remove_drm)
        out = tmp_path / "out"

        result = cli_runner.invoke(cli, ["process", str(acsm), "-o", str(out)])

        assert result.exit_code == 0, result.output
        assert (out / "Title.epub").read_bytes() == b"decrypted"
        assert f"Success! Output file: {out / 'Title.epub'}" in result.stdout
