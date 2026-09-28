"""
Adobe account authorization management.
"""

import base64
import shutil
from pathlib import Path
from lxml import etree

from ...utils.errors import AuthorizationError


class AdobeAccount:
    """Manages Adobe authorization workflow."""

    def __init__(self, auth_dir: Path):
        """
        Args:
            auth_dir: Authorization file storage directory (~/.config/book-loader/.adobe/)
        """
        self.auth_dir = auth_dir
        self.auth_dir.mkdir(parents=True, exist_ok=True)

        self.activation_xml = auth_dir / "activation.xml"
        self.activation_dat = auth_dir / "activation.dat"  # ADE format
        self.device_xml = auth_dir / "device.xml"
        self.devicesalt = auth_dir / "devicesalt"

    def is_authorized(self) -> bool:
        """Check if already authorized (supports standard and ADE formats)."""
        # Standard format: all three files must exist
        standard_format = all(
            [
                self.activation_xml.exists(),
                self.device_xml.exists(),
                self.devicesalt.exists(),
            ]
        )
        # ADE format: only activation.dat is needed
        ade_format = self.activation_dat.exists()

        return standard_format or ade_format

    def get_auth_type(self) -> str:
        """
        Get authorization type: 'anonymous' or 'AdobeID'.

        Returns:
            Authorization type string
        """
        if not self.is_authorized():
            return "none"

        try:
            tree = etree.parse(str(self.activation_xml))
            root = tree.getroot()
            # Find <username method="..."> element (Adobe ID stores method here)
            username = root.find(".//{http://ns.adobe.com/adept}username")
            if username is not None:
                method = username.get("method")
                if method:
                    return method
            # Fallback to anonymous if no method attribute found
            return "anonymous"
        except Exception:
            return "unknown"

    def get_adobe_id_email(self) -> str:
        """
        Get Adobe ID email address.

        Returns:
            Email address if using Adobe ID, empty string otherwise
        """
        if not self.is_authorized():
            return ""

        try:
            tree = etree.parse(str(self.activation_xml))
            root = tree.getroot()
            # Find <username method="...">email</username>
            username = root.find(".//{http://ns.adobe.com/adept}username")
            if username is not None and username.get("method") == "AdobeID":
                return username.text or ""
            return ""
        except Exception:
            return ""

    def authorize_anonymous(self) -> None:
        """Execute anonymous authorization (default method)."""
        self._authorize("anonymous", "", "", "Anonymous authorization failed")

    def authorize_adobe_id(self, email: str, password: str) -> None:
        """
        Execute Adobe ID authorization (requires account credentials).

        Args:
            email: Adobe ID account (email)
            password: Adobe ID password
        """
        self._authorize("AdobeID", email, password, "Adobe ID authorization failed")

    def _authorize(self, method: str, email: str, password: str, failure: str) -> None:
        """Run the five-step authorization; each libadobeAccount step returns (ok, message)."""
        try:
            self._run_authorization(method, email, password, failure)
        except Exception:
            # Don't leave a half-written authorization that is_authorized() would accept.
            for file in [self.activation_xml, self.device_xml, self.devicesalt]:
                file.unlink(missing_ok=True)
            raise

    def _run_authorization(self, method: str, email: str, password: str, failure: str) -> None:
        try:
            # Set authorization directory path
            from . import libadobe
            from . import libadobeAccount

            libadobe.update_account_path(str(self.auth_dir))

            libadobe.createDeviceKeyFile()
            steps = [
                ("creating the device", lambda: libadobeAccount.createDeviceFile(randomSerial=True, useVersionIndex=1)),
                ("creating the user", lambda: libadobeAccount.createUser(useVersionIndex=1)),
                ("signing in", lambda: libadobeAccount.signIn(method, email, password)),
                ("activating the device", lambda: libadobeAccount.activateDevice(useVersionIndex=1)),
            ]
            for name, run in steps:
                result = run()
                ok, message = (result[0], result[1]) if isinstance(result, tuple) else (result, "")
                if not ok:
                    raise AuthorizationError(f"{failure} while {name}" + (f": {message}" if message else ""))

        except AuthorizationError:
            raise
        except Exception as e:
            raise AuthorizationError(f"{failure}: {e}")

    def get_device_key(self) -> bytes:
        """
        Extract RSA private key (DER format) from activation.xml.

        Returns:
            RSA private key (bytes, DER format) for DRM removal
        """
        if not self.activation_xml.exists():
            raise AuthorizationError("Authorization file not found, please authorize first")

        try:
            tree = etree.parse(str(self.activation_xml))
            root = tree.getroot()

            # Find <privateLicenseKey>
            key_element = root.find(
                ".//{http://ns.adobe.com/adept}privateLicenseKey"
            )
            if key_element is None or not key_element.text:
                raise AuthorizationError("Cannot extract private key from authorization file")

            # Base64 decode
            key_b64 = key_element.text.strip()
            key_der = base64.b64decode(key_b64)

            return key_der

        except Exception as e:
            raise AuthorizationError(f"Failed to extract private key: {e}")

    def reset(self) -> None:
        """Reset authorization (delete all authorization files)."""
        for file in [self.activation_xml, self.activation_dat, self.device_xml, self.devicesalt]:
            if file.exists():
                file.unlink()
        # Licenses saved after blocked downloads only work with the old key.
        shutil.rmtree(self.auth_dir / "pending", ignore_errors=True)
