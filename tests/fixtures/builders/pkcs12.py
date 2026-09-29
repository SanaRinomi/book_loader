"""PKCS#12 bundles for the oscrypto shim tests (T1.5), built with ``cryptography``.

The keys come from ``rsa_key(seed)`` and the certificates have fixed names, serials and
dates, so the contents are the same every run. ``cryptography`` picks random salts, so the
encrypted bytes are not.

``cryptography`` is independent of the shim, so its reading of a bundle is the reference.
The RC2-40 bundles that Adobe's servers write come from ``openssl pkcs12 -export -legacy``
instead, and are committed under ``fixtures/pkcs12/`` (``make_fixtures pkcs12``).
"""

from __future__ import annotations

import base64
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from tests.fixtures.builders._random import rsa_key

PASSWORD = b"book-loader test password"
FIXTURES = Path(__file__).resolve().parents[1] / "pkcs12"
LEGACY_PASSWORD = b"legacy-test"

# scheme name: (algorithm for both bags, MAC hash)
SCHEMES = {
    "3des": (pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC, hashes.SHA1()),
    "aes256": (pkcs12.PBES.PBESv2SHA256AndAES256CBC, hashes.SHA256()),
}
_VALID_FROM = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


@dataclass(frozen=True)
class Reference:
    """What ``cryptography`` reads from a bundle, as DER."""

    key: bytes
    certificate: bytes
    others: tuple[bytes, ...]


def private_key(seed: str) -> rsa.RSAPrivateKey:
    der = rsa_key(seed).export_key("DER", pkcs=8)
    key = serialization.load_der_private_key(der, password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    return key


def certificate(
    seed: str, name: str, issuer_seed: str | None = None, issuer_name: str | None = None
) -> x509.Certificate:
    """A certificate for ``seed``'s key, signed by the issuer's key (self-signed by default)."""
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name or name)])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key(seed).public_key())
        .serial_number(int.from_bytes(seed.encode("utf-8")[:8], "big") or 1)
        .not_valid_before(_VALID_FROM)
        .not_valid_after(_VALID_FROM + datetime.timedelta(days=3650))
        .sign(private_key(issuer_seed or seed), hashes.SHA256())
    )


def ec_certificate(name: str) -> x509.Certificate:
    """A self-signed certificate with a new EC key (random: only its reading is compared)."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(2)
        .not_valid_before(_VALID_FROM)
        .not_valid_after(_VALID_FROM + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )


def build_pkcs12(
    scheme: str | None = "3des",
    password: bytes = PASSWORD,
    seed: str = "pkcs12",
    extra_certificates: int = 0,
    iterations: int = 2048,
    with_key: bool = True,
    ec_certificates: int = 0,
) -> bytes:
    """A bundle with ``seed``'s key, its certificate and some CA certificates.

    ``scheme=None`` writes it unencrypted (the MAC then uses an empty password). ``with_key=False`` leaves out the
    key and its certificate, so only the CA certificates are in it.
    """
    cas = [
        certificate(f"{seed}-ca{n}", f"book-loader test CA {n}") for n in range(extra_certificates)
    ]
    cas += [ec_certificate(f"book-loader EC CA {n}") for n in range(ec_certificates)]
    if scheme is None:
        encryption: serialization.KeySerializationEncryption = serialization.NoEncryption()
    else:
        algorithm, mac_hash = SCHEMES[scheme]
        encryption = (
            serialization.PrivateFormat.PKCS12.encryption_builder()
            .kdf_rounds(iterations)
            .key_cert_algorithm(algorithm)
            .hmac_hash(mac_hash)
            .build(password)
        )
    return pkcs12.serialize_key_and_certificates(
        b"book-loader test",
        private_key(seed) if with_key else None,
        certificate(seed, "book-loader test") if with_key else None,
        cas,
        encryption,
    )


def reference(data: bytes, password: bytes | None) -> Reference:
    key, cert, others = pkcs12.load_key_and_certificates(data, password)
    assert key is not None and cert is not None
    der = serialization.Encoding.DER
    return Reference(
        key=key.private_bytes(der, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        certificate=cert.public_bytes(der),
        others=tuple(c.public_bytes(der) for c in others),
    )


def with_pkcs12(auth_dir: Path, data: bytes) -> None:
    """Put ``data`` in ``activation.xml`` as the account's PKCS#12 bundle.

    ``libadobe`` opens it with the base64 of ``devicesalt`` (the device key) as password.
    """
    path = auth_dir / "activation.xml"
    tree = etree.parse(str(path))
    node = tree.find(".//{http://ns.adobe.com/adept}credentials/{http://ns.adobe.com/adept}pkcs12")
    assert node is not None
    node.text = base64.b64encode(data).decode("ascii")
    tree.write(str(path), xml_declaration=True, encoding="utf-8")


def device_password(auth_dir: Path) -> bytes:
    return base64.b64encode((auth_dir / "devicesalt").read_bytes())


def oscrypto() -> tuple[Any, Any] | None:
    """oscrypto's ``keys`` and ``asymmetric`` modules, or None when they can't be used.

    None when oscrypto isn't installed, and also when it can't load its crypto library:
    1.3.0 fails that way on Linux with OpenSSL 3 ("Error detecting the version of
    libcrypto"), the bug the shim removes (REFACTOR_PLAN §16).
    """
    try:
        from oscrypto import errors
    except ImportError:
        return None
    try:
        from oscrypto import asymmetric, keys
    except errors.LibraryNotFoundError:
        return None
    return keys, asymmetric


def oscrypto_or_skip() -> tuple[Any, Any]:
    """Like ``oscrypto()``, but skips the test when oscrypto can't be used."""
    modules = oscrypto()
    if modules is None:
        pytest.skip("oscrypto isn't installed or can't load its crypto library")
    return modules
