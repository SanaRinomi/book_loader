"""
Main workflow orchestration.
"""

from pathlib import Path
from typing import Callable, Optional
from .adobe import AdobeAccount, ACSMFulfiller
from .drm import DRMRemover
from .conversion import ConversionEngine
from ..utils.config import Config
from ..utils.console import StepReporter, quiet
from ..utils.errors import ACSMFulfillmentError, ManualDownloadRequired

# Called when the automatic download fails; returns the file the user downloaded
# by hand, or None to give up. The second argument is the error of the previous
# attempt with a hand-downloaded file (None on the first call).
ManualDownloadHandler = Callable[
    [ManualDownloadRequired, Optional[ACSMFulfillmentError]], Optional[Path]
]

AUTH_TYPE_NAMES = {"anonymous": "Anonymous", "AdobeID": "Adobe ID"}


class BookLoader:
    """Main ebook loading workflow."""

    def __init__(self, config: Config):
        self.config = config
        self.account = AdobeAccount(config.auth_dir)
        self.fulfiller = ACSMFulfiller(self.account)
        self.drm_remover = DRMRemover()

    def process_acsm(
        self,
        acsm_path: Path,
        output_dir: Path,
        optimize: bool = False,
        to_pdf: bool = False,
        convert_engine: str = "python",
        keep_encrypted: bool = False,
        verbose: bool = False,
        downloaded_file: Path = None,
        reporter: StepReporter = None,
        manual_download: ManualDownloadHandler = None,
    ) -> Path:
        """
        Complete processing workflow.

        Args:
            acsm_path: ACSM file path
            output_dir: Output directory
            optimize: Whether to optimize EPUB (not yet implemented)
            to_pdf: Whether to convert to PDF
            convert_engine: PDF conversion engine ('python' or 'calibre')
            keep_encrypted: Whether to keep encrypted file
            verbose: Print detailed (redacted) protocol and HTTP logs
            downloaded_file: Encrypted book already downloaded by hand; skips the download
            reporter: Status output; create with total=self.step_count(to_pdf)
            manual_download: Asks the user for a hand-downloaded file when the download fails

        Returns:
            Final output file path
        """
        r = reporter or StepReporter(self.step_count(to_pdf), verbose)

        # 1. Ensure authorization
        r.step(1, "Checking for Auth")
        if self.account.is_authorized():
            auth_type = self.account.get_auth_type()
            r.done(f"Auth Detected: {AUTH_TYPE_NAMES.get(auth_type, auth_type)}.")
        else:
            r.add("None found. Creating anonymous authorization")
            with quiet(not verbose):
                self.account.authorize_anonymous()
            r.done()

        # 2. Fulfill ACSM, download encrypted file
        temp_dir = output_dir / ".temp"
        if downloaded_file:
            encrypted_path = self._use_downloaded_file(
                acsm_path, downloaded_file, temp_dir, verbose, r
            )
        else:
            r.step(2, "Downloading Encrypted File")
            try:
                encrypted_path = self.fulfiller.fulfill(
                    acsm_path, temp_dir, verbose=verbose, status=r.on_event
                )
            except ManualDownloadRequired as blocked:
                r.fail()
                if manual_download is None:
                    raise
                encrypted_path = self._finish_by_hand(
                    acsm_path, blocked, manual_download, temp_dir, verbose, r
                )
            else:
                r.done(f"Done ({_describe(encrypted_path)}).")

        # 3. Remove DRM
        r.step(3, "Removing DRM")
        decrypted_path = output_dir / encrypted_path.name
        user_key = self.account.get_device_key()
        with quiet(not verbose):
            self.drm_remover.remove_drm(encrypted_path, decrypted_path, user_key)
        r.done()

        # Clean up encrypted file if not keeping intermediate files
        # Note: keep_encrypted controls deletion of both encrypted source and intermediate files
        if not keep_encrypted:
            encrypted_path.unlink()
            # Clean up temporary directory
            try:
                temp_dir.rmdir()
            except OSError:
                pass

        current_path = decrypted_path

        # Optional: Optimize EPUB
        if optimize and current_path.suffix == ".epub":
            r.note("Optimization is not implemented yet, skipped.")
            # TODO: Implement optimization feature

        # 4. Optional: Convert to PDF
        if to_pdf:
            r.step(4, f"Converting to PDF ({convert_engine})")
            if current_path.suffix != ".epub":
                r.done("Skipped, the book is already a PDF.")
                return current_path

            pdf_path = current_path.with_suffix(".pdf")
            try:
                engine = ConversionEngine(engine=convert_engine)
                engine.convert_epub_to_pdf(current_path, pdf_path)
            except Exception as e:
                r.fail()
                r.note(f"WARNING: PDF conversion failed: {e}")
                r.note(f"Keeping the EPUB: {current_path}")
                return current_path

            r.done()
            # Delete intermediate decrypted EPUB after PDF conversion (controlled by keep_encrypted flag)
            # Note: current_path here is the decrypted EPUB, not the encrypted file
            if not keep_encrypted:
                current_path.unlink()
            current_path = pdf_path

        return current_path

    @staticmethod
    def step_count(to_pdf: bool) -> int:
        return 4 if to_pdf else 3

    def _use_downloaded_file(
        self, acsm_path: Path, downloaded_file: Path, temp_dir: Path, verbose: bool, r: StepReporter
    ) -> Path:
        r.step(2, f"Using downloaded file: {downloaded_file.name}")
        path = self.fulfiller.fulfill_from_file(
            acsm_path, downloaded_file, temp_dir, verbose=verbose, status=r.on_event
        )
        r.done(f"Done ({_describe(path)}).")
        return path

    def _finish_by_hand(
        self,
        acsm_path: Path,
        blocked: ManualDownloadRequired,
        manual_download: ManualDownloadHandler,
        temp_dir: Path,
        verbose: bool,
        r: StepReporter,
    ) -> Path:
        """Let the user download the book in a browser, then carry on with that file."""
        last_error = None
        while True:
            downloaded_file = manual_download(blocked, last_error)
            if downloaded_file is None:
                raise blocked
            try:
                return self._use_downloaded_file(acsm_path, downloaded_file, temp_dir, verbose, r)
            except ACSMFulfillmentError as e:
                # Wrong file (e.g. the check page instead of the book); the license is still saved.
                r.fail()
                last_error = e


def _describe(path: Path) -> str:
    size = path.stat().st_size / 1048576
    return f"{path.suffix.lstrip('.').upper()}, {size:.1f} MB"
