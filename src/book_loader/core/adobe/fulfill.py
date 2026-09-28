"""
ACSM fulfillment and download.
"""

import contextlib
import hashlib
import html
import json
import shutil
import time
from pathlib import Path
from typing import Callable, Optional
from .account import AdobeAccount
from ...utils.console import quiet
from ...utils.errors import ACSMFulfillmentError, ManualDownloadRequired
from ...utils.redact import redact_text

# Receives libadobe.report() events, e.g. StepReporter.on_event.
StatusCallback = Optional[Callable[..., None]]


class ACSMFulfiller:
    """ACSM file fulfiller."""

    def __init__(self, account: AdobeAccount):
        """
        Args:
            account: AdobeAccount instance
        """
        self.account = account

    def fulfill(
        self, acsm_path: Path, output_dir: Path, verbose: bool = False, status: StatusCallback = None
    ) -> Path:
        """
        Fulfill ACSM file and download encrypted ebook.

        If the download fails after a successful fulfillment, the license is saved so the
        book can be downloaded by hand and finished with fulfill_from_file().

        Args:
            acsm_path: .acsm file path
            output_dir: Output directory
            verbose: Print detailed (redacted) protocol and HTTP logs
            status: Receives progress events (see libadobe.report)

        Returns:
            Downloaded encrypted file path (.epub or .pdf)

        Raises:
            ManualDownloadRequired: fulfilled, but the download failed; the license is saved
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        with self._adobe_session(verbose, status):
            info = self._fulfill_acsm(acsm_path)

            from . import libadobeFulfill

            try:
                output_path = libadobeFulfill.download_book(info, str(output_dir))
            except Exception as e:
                reason = redact_text(str(e))
                if not info.get("download_url"):
                    raise ACSMFulfillmentError(f"ACSM processing failed: {reason}")
                link_file = self._save_pending(acsm_path, output_dir, info)
                raise ManualDownloadRequired(reason, info["download_url"], link_file, acsm_path)

        self._clear_pending(acsm_path)
        return Path(output_path)

    def fulfill_from_file(
        self,
        acsm_path: Path,
        downloaded_file: Path,
        output_dir: Path,
        verbose: bool = False,
        status: StatusCallback = None,
    ) -> Path:
        """
        Add the license for an ACSM to an encrypted book that was downloaded by hand.

        Uses the license saved when the automatic download failed; if there is none (or it
        belongs to a different authorization), fulfills the ACSM again to get one.

        Args:
            acsm_path: .acsm file path
            downloaded_file: Encrypted .epub / .pdf downloaded from the fulfillment link
            output_dir: Output directory
            verbose: Print detailed (redacted) protocol and HTTP logs
            status: Receives progress events (see libadobe.report)

        Returns:
            Licensed encrypted file path (.epub or .pdf), ready for DRM removal
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        with self._adobe_session(verbose, status):
            from . import libadobe
            from . import libadobeFulfill

            info = self._load_pending(acsm_path)
            if info:
                libadobe.report("info", message="Using saved license")
                libadobe.report("info", message="Adding license")
            else:
                libadobe.report("info", message="No saved license, fulfilling the ACSM again")
                info = self._fulfill_acsm(acsm_path)
                libadobe.report("start", message="Adding license")

            # Work on a copy; apply_license consumes its input and the user's file must stay.
            work_copy = output_dir / (info["book_name"] + ".manual.tmp")
            shutil.copyfile(downloaded_file, work_copy)
            try:
                output_path = libadobeFulfill.apply_license(
                    str(work_copy), info, str(output_dir), source=f"{downloaded_file.name} is"
                )
            except Exception as e:
                work_copy.unlink(missing_ok=True)
                raise ACSMFulfillmentError(f"Could not use the downloaded file: {redact_text(str(e))}")

        self._clear_pending(acsm_path)
        return Path(output_path)

    @contextlib.contextmanager
    def _adobe_session(self, verbose: bool, status: StatusCallback):
        """Point libadobe at this account and route its output: events to `status`,
        its plain prints nowhere unless verbose."""
        from . import libadobe

        libadobe.update_account_path(str(self.account.auth_dir))
        libadobe.set_verbose(verbose)
        libadobe.set_status_callback(status)
        try:
            with quiet(not verbose):
                yield
        finally:
            libadobe.set_status_callback(None)

    def _fulfill_acsm(self, acsm_path: Path) -> dict:
        """Fulfill the ACSM with Adobe and return libadobeFulfill.parse_fulfillment() data.

        Call inside _adobe_session()."""
        if not self.account.is_authorized():
            raise ACSMFulfillmentError("Not authorized, please authorize first")

        if not acsm_path.exists():
            raise ACSMFulfillmentError(f"ACSM file not found: {acsm_path}")

        try:
            from . import libadobeFulfill

            success, result = libadobeFulfill.fulfill(str(acsm_path), do_notify=True)

            if not success:
                error_msg = result if isinstance(result, str) else "Unknown error"
                raise ACSMFulfillmentError(f"ACSM fulfillment failed: {redact_text(error_msg)}")

            return libadobeFulfill.parse_fulfillment(result)

        except ACSMFulfillmentError:
            raise
        except Exception as e:
            raise ACSMFulfillmentError(f"ACSM processing failed: {redact_text(str(e))}")

    # Saved fulfillments live in the auth directory: the license only works with this
    # authorization's key, and the directory is already private (0700).

    def _pending_path(self, acsm_path: Path) -> Path:
        digest = hashlib.sha256(acsm_path.read_bytes()).hexdigest()[:16]
        return self.account.auth_dir / "pending" / f"{digest}.json"

    def _key_fingerprint(self) -> str:
        try:
            return hashlib.sha256(self.account.get_device_key()).hexdigest()
        except Exception:
            return ""

    def _save_pending(self, acsm_path: Path, output_dir: Path, info: dict) -> Path:
        """Save the fulfillment and write a page with the download link; returns the page path."""
        link_file = (output_dir / f"{info['book_name']} - download link.html").resolve()
        link_file.write_text(_LINK_PAGE.format(
            title=html.escape(info["book_name"]),
            url=html.escape(info["download_url"], quote=True),
            acsm=html.escape(str(acsm_path.resolve())),
        ), encoding="utf-8")

        pending = self._pending_path(acsm_path)
        pending.parent.mkdir(mode=0o700, exist_ok=True)
        pending.write_text(json.dumps({
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "acsm": acsm_path.name,
            "key_fingerprint": self._key_fingerprint(),
            "link_file": str(link_file),
            "info": info,
        }), encoding="utf-8")
        pending.chmod(0o600)
        return link_file

    def _load_pending(self, acsm_path: Path):
        pending = self._pending_path(acsm_path)
        try:
            data = json.loads(pending.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("key_fingerprint") != self._key_fingerprint():
            # Saved under a different authorization; its license can't be decrypted with this key.
            from . import libadobe

            libadobe.report("warning", message="Ignoring a saved license that belongs to a different authorization")
            return None
        return data.get("info")

    def _clear_pending(self, acsm_path: Path) -> None:
        pending = self._pending_path(acsm_path)
        try:
            data = json.loads(pending.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("link_file"):
            Path(data["link_file"]).unlink(missing_ok=True)
        pending.unlink(missing_ok=True)


_LINK_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Download: {title}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:40em;margin:2em auto;padding:0 1em;line-height:1.5}}
code{{background:#8882;padding:.1em .3em;border-radius:3px}}</style></head>
<body>
<h1>{title}</h1>
<p>The automatic download was blocked. Download the encrypted book with your browser instead:</p>
<p><a href="{url}"><strong>Download the book</strong></a></p>
<p>If Google shows a check ("unusual traffic"), complete it; the download starts afterwards.
If book-loader is still waiting, enter the saved file at its prompt. Otherwise run the same
<code>book-loader process</code> command again, adding <code>--downloaded-file</code> with the
path of the saved file, for example:</p>
<p><code>book-loader process "{acsm}" --downloaded-file "PATH/TO/DOWNLOADED/FILE"</code></p>
<p>This link belongs to your purchase. Don't share it. This page is deleted once the book is processed.</p>
</body></html>
"""
