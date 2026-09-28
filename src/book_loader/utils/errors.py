"""
Custom exception classes for book-loader.
"""


class BookLoaderError(Exception):
    """Base exception class for all book-loader errors."""

    pass


class AuthorizationError(BookLoaderError):
    """Raised when Adobe authorization fails."""

    pass


class ACSMFulfillmentError(BookLoaderError):
    """Raised when ACSM fulfillment fails."""

    pass


class ManualDownloadRequired(ACSMFulfillmentError):
    """
    Raised when fulfillment succeeded but the automatic download failed (e.g. a bot check).

    The license is saved, so the book can be downloaded in a browser from `url` and
    finished with ACSMFulfiller.fulfill_from_file().
    """

    def __init__(self, reason: str, url: str, link_file, acsm_path):
        self.reason = reason
        self.url = url
        self.link_file = link_file
        self.acsm_path = acsm_path
        super().__init__(
            f"Download failed: {reason}\n\n"
            "The fulfillment itself succeeded and its license was saved, so you can finish by hand:\n"
            f"  1. Open {link_file} in a browser and use the link to download the book\n"
            "     (complete Google's check if it asks). The file is still encrypted.\n"
            "  2. Run the same command again, adding:  --downloaded-file <path to that file>"
        )


class DRMRemovalError(BookLoaderError):
    """Raised when DRM removal fails."""

    pass


class CalibreNotFoundError(BookLoaderError):
    """Raised when Calibre is not installed."""

    pass


class WorkflowError(BookLoaderError):
    """Raised when workflow processing encounters an error."""

    pass


class KoboLibraryNotFoundError(BookLoaderError):
    """Raised when the Kobo Desktop Edition library or database cannot be found."""

    pass


class KoboDecryptionError(BookLoaderError):
    """Raised when Kobo KEPUB decryption fails."""

    pass
