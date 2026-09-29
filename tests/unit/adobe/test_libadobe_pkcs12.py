"""T1.5: libadobe signs and reads certificates through the PKCS#12 shim, not oscrypto."""

from __future__ import annotations

import base64
import importlib
import sys
from pathlib import Path

import pytest
from lxml import etree

import book_loader.adobe._vendor as vendor
from book_loader.adobe import pkcs12 as shim
from book_loader.adobe._vendor import libadobe
from tests.fixtures.builders._random import rsa_key
from tests.fixtures.builders.adobe_auth import build_auth_folder
from tests.fixtures.builders.pkcs12 import build_pkcs12, device_password, reference, with_pkcs12

ACCOUNT_SEED = "pkcs12-account"
# Module globals that libadobe keeps between calls.
_STATE = ("FILE_DEVICEKEY", "FILE_DEVICEXML", "FILE_ACTIVATIONXML", "devkey_bytes", "pkcs12")


def request_node() -> etree._Element:
    return etree.fromstring(
        '<adept:fulfill xmlns:adept="http://ns.adobe.com/adept">'
        "<adept:user>urn:uuid:11111111-1111-4111-8111-111111111111</adept:user>"
        "<adept:nonce>AAECAwQFBgc=</adept:nonce>"
        "</adept:fulfill>"
    )


def oscrypto_reads(data: bytes, password: bytes) -> bool:
    try:
        from oscrypto import keys
    except ImportError:
        return True  # nothing to compare with
    try:
        keys.parse_pkcs12(data, password)
    except (ValueError, OSError):  # its 3DES key derivation bug (about one salt in 465)
        return False
    return True


@pytest.fixture
def account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An authorization folder whose activation.xml has a real PKCS#12 bundle."""
    for name in _STATE:
        monkeypatch.setattr(libadobe, name, getattr(libadobe, name, None), raising=False)
    auth = build_auth_folder(tmp_path / ".adobe")
    password = device_password(auth.path)
    data = b""
    for _ in range(20):
        data = build_pkcs12("3des", password=password, seed=ACCOUNT_SEED)
        if oscrypto_reads(data, password):
            break
    with_pkcs12(auth.path, data)
    libadobe.update_account_path(str(auth.path))
    return auth.path


def pkcs12_of(account: Path) -> bytes:
    tree = etree.parse(str(account / "activation.xml"))
    return base64.b64decode(tree.findtext(".//{http://ns.adobe.com/adept}pkcs12") or "")


def test_libadobe_uses_the_shim():
    assert libadobe.keys is shim.keys
    assert libadobe.dump_certificate is shim.dump_certificate
    assert libadobe.dump_private_key is shim.dump_private_key


def test_signature_verifies_with_the_certificate_key(account: Path):
    node = request_node()
    signed = libadobe.sign_node(node)
    assert signed is not None
    signature = base64.b64decode(signed)
    # Adobe's signature: textbook RSA on a PKCS#1 v1.5 type 1 block around the bare SHA-1.
    key = rsa_key(ACCOUNT_SEED)
    size = (key.n.bit_length() + 7) // 8
    block = pow(int.from_bytes(signature, "big"), key.e, key.n).to_bytes(size, "big")
    digest = libadobe.hash_node(node).digest()
    assert block == b"\x00\x01" + b"\xff" * (size - 3 - len(digest)) + b"\x00" + digest


def test_signature_matches_oscrypto(account: Path, monkeypatch: pytest.MonkeyPatch):
    keys = pytest.importorskip("oscrypto.keys")
    asymmetric = pytest.importorskip("oscrypto.asymmetric")
    through_shim = libadobe.sign_node(request_node())
    monkeypatch.setattr(libadobe, "keys", keys)
    monkeypatch.setattr(libadobe, "dump_private_key", asymmetric.dump_private_key)
    assert libadobe.sign_node(request_node()) == through_shim


def test_certificate_matches_cryptography_and_oscrypto(account: Path):
    data, password = pkcs12_of(account), device_password(account)
    cert = libadobe.get_cert_from_pkcs12(data, password)
    assert cert == reference(data, password).certificate
    keys = pytest.importorskip("oscrypto.keys")
    asymmetric = pytest.importorskip("oscrypto.asymmetric")
    _, oscrypto_cert, _ = keys.parse_pkcs12(data, password)
    assert cert == asymmetric.dump_certificate(oscrypto_cert, encoding="der")


def test_libadobe_imports_and_signs_without_oscrypto(
    account: Path, monkeypatch: pytest.MonkeyPatch
):
    expected = libadobe.sign_node(request_node())
    for name in [m for m in sys.modules if m == "oscrypto" or m.startswith("oscrypto.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "oscrypto", None)
    # A fresh copy of libadobe, imported while oscrypto can't be; the original comes back.
    monkeypatch.delitem(sys.modules, libadobe.__name__)
    monkeypatch.setattr(vendor, "libadobe", libadobe)
    fresh = importlib.import_module(libadobe.__name__)
    assert fresh is not libadobe

    fresh.update_account_path(str(account))
    assert fresh.sign_node(request_node()) == expected
