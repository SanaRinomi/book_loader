"""Synthetic Adobe authorization folders, shaped like the ones libadobeAccount writes.

Nothing here is a real Adobe credential. The certificates are placeholders; the
private key is a real, reproducible RSA key so that ADEPT test books can be decrypted.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from Crypto.PublicKey import RSA

from tests.fixtures.builders._random import DeterministicRandom, rsa_key

LICENSE_URL = "https://license.example.com/licensesign"
OPERATOR_URL = "https://acs.example.com/fulfillment"
USER_UUID = "urn:uuid:11111111-1111-4111-8111-111111111111"
DEVICE_UUID = "urn:uuid:22222222-2222-4222-8222-222222222222"
ADOBE_ID_EMAIL = "reader@example.com"

# Placeholder base64 blobs where real files hold certificates.
_FAKE_CERT = base64.b64encode(b"test certificate, not real").decode("ascii")


@dataclass(frozen=True)
class AuthFolder:
    path: Path
    key: RSA.RsaKey
    method: str
    email: str | None

    @property
    def device_key(self) -> bytes:
        """The private key as ``AdobeAccount.get_device_key()`` returns it (PKCS#8 DER)."""
        return self.key.export_key("DER", pkcs=8)


def activation_xml(key: RSA.RsaKey, method: str = "anonymous", email: str | None = None) -> str:
    private_key = base64.b64encode(key.export_key("DER", pkcs=8)).decode("ascii")
    username = (
        f'<adept:username method="{method}">{email}</adept:username>\n'
        if method != "anonymous"
        else ""
    )
    return f"""<?xml version="1.0"?>
<activationInfo xmlns="http://ns.adobe.com/adept">
  <adept:activationServiceInfo xmlns:adept="http://ns.adobe.com/adept">
    <adept:authURL>https://auth.example.com/adept</adept:authURL>
    <adept:userInfoURL>https://auth.example.com/adept</adept:userInfoURL>
    <adept:activationURL>https://auth.example.com/adept</adept:activationURL>
    <adept:certificate>{_FAKE_CERT}</adept:certificate>
  </adept:activationServiceInfo>
  <activationToken xmlns="http://ns.adobe.com/adept">
    <device>{DEVICE_UUID}</device>
    <fingerprint>ZmFrZSBmaW5nZXJwcmludA==</fingerprint>
    <deviceType>standalone</deviceType>
    <activationURL>https://auth.example.com/adept</activationURL>
    <user>{USER_UUID}</user>
    <signature>ZmFrZSBzaWduYXR1cmU=</signature>
  </activationToken>
  <adept:licenseServices xmlns:adept="http://ns.adobe.com/adept">
    <adept:licenseServiceInfo>
      <adept:licenseURL>{LICENSE_URL}</adept:licenseURL>
      <adept:certificate>{_FAKE_CERT}</adept:certificate>
    </adept:licenseServiceInfo>
  </adept:licenseServices>
<adept:credentials xmlns:adept="http://ns.adobe.com/adept">
<adept:user>{USER_UUID}</adept:user>
{username}<adept:pkcs12>{_FAKE_CERT}</adept:pkcs12>
<adept:licenseCertificate>{_FAKE_CERT}</adept:licenseCertificate>
<adept:privateLicenseKey>{private_key}</adept:privateLicenseKey>
<adept:authenticationCertificate>{_FAKE_CERT}</adept:authenticationCertificate>
</adept:credentials>
</activationInfo>
"""


DEVICE_XML = """<?xml version="1.0"?>
<adept:deviceInfo xmlns:adept="http://ns.adobe.com/adept">
<adept:deviceType>standalone</adept:deviceType>
<adept:deviceClass>Desktop</adept:deviceClass>
<adept:deviceSerial>0000000000000000000000000000000000000000</adept:deviceSerial>
<adept:deviceName>test-device</adept:deviceName>
<adept:version name="hobbes" value="9.3.58046"/>
</adept:deviceInfo>
"""


def build_auth_folder(
    path: Path,
    method: str = "anonymous",
    email: str | None = None,
    seed: str = "auth",
) -> AuthFolder:
    """Write ``activation.xml``, ``device.xml`` and ``devicesalt`` into ``path``.

    ``method`` is ``"anonymous"`` or ``"AdobeID"``; Adobe ID folders get ``email``
    (default ``ADOBE_ID_EMAIL``) as the username.
    """
    if method != "anonymous" and email is None:
        email = ADOBE_ID_EMAIL
    key = rsa_key(seed)
    path.mkdir(parents=True, exist_ok=True)
    (path / "activation.xml").write_text(activation_xml(key, method, email), encoding="utf-8")
    (path / "device.xml").write_text(DEVICE_XML, encoding="utf-8")
    (path / "devicesalt").write_bytes(DeterministicRandom(f"salt:{seed}").read(16))
    return AuthFolder(path=path, key=key, method=method, email=email)
