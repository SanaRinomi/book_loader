"""T1.5, live and local only: this machine's real authorization reads the same through the
PKCS#12 shim as through oscrypto.

    uv run pytest -m live tests/live/test_pkcs12_live.py

Everything stays in memory: nothing from the authorization is written, logged or
compared against a stored value. Skips when there is no authorization or no oscrypto.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from lxml import etree

from book_loader.adobe import pkcs12 as shim
from book_loader.adobe._vendor import libadobe

pytestmark = pytest.mark.live

# Worked out at import, before ``tmp_home`` hides the real home and BOOK_LOADER_AUTH_DIR.
REAL_AUTH_DIR = Path(
    os.environ.get("BOOK_LOADER_AUTH_DIR") or Path.home() / ".config" / "book-loader" / ".adobe"
)
ADEPT = "{http://ns.adobe.com/adept}"


@pytest.fixture
def real_account() -> tuple[bytes, bytes]:
    activation = REAL_AUTH_DIR / "activation.xml"
    device_key = REAL_AUTH_DIR / "devicesalt"
    if not (activation.is_file() and device_key.is_file()):
        pytest.skip(f"no activation.xml and devicesalt in {REAL_AUTH_DIR}")
    node = etree.parse(str(activation)).find(f".//{ADEPT}credentials/{ADEPT}pkcs12")
    if node is None or not node.text:
        pytest.skip("activation.xml has no PKCS#12 bundle")
    return base64.b64decode(node.text), base64.b64encode(device_key.read_bytes())


def test_real_bundle_reads_the_same(real_account: tuple[bytes, bytes]):
    keys = pytest.importorskip("oscrypto.keys")
    asymmetric = pytest.importorskip("oscrypto.asymmetric")
    data, password = real_account

    key, cert, others = shim.keys.parse_pkcs12(data, password)
    o_key, o_cert, o_others = keys.parse_pkcs12(data, password)

    assert shim.dump_private_key(key, None, "der") == asymmetric.dump_private_key(
        o_key, None, "der"
    )
    assert shim.dump_certificate(cert, encoding="der") == asymmetric.dump_certificate(
        o_cert, encoding="der"
    )
    assert [shim.dump_certificate(c, encoding="der") for c in others] == [
        asymmetric.dump_certificate(c, encoding="der") for c in o_others
    ]


def test_real_signature_is_the_same(
    real_account: tuple[bytes, bytes], monkeypatch: pytest.MonkeyPatch
):
    keys = pytest.importorskip("oscrypto.keys")
    asymmetric = pytest.importorskip("oscrypto.asymmetric")
    for name in (
        "FILE_DEVICEKEY",
        "FILE_DEVICEXML",
        "FILE_ACTIVATIONXML",
        "devkey_bytes",
        "pkcs12",
    ):
        monkeypatch.setattr(libadobe, name, getattr(libadobe, name, None), raising=False)
    libadobe.update_account_path(str(REAL_AUTH_DIR))
    node = etree.fromstring(f'<adept:test xmlns:adept="{ADEPT[1:-1]}">book-loader</adept:test>')

    through_shim = libadobe.sign_node(node)
    monkeypatch.setattr(libadobe, "keys", keys)
    monkeypatch.setattr(libadobe, "dump_private_key", asymmetric.dump_private_key)
    assert through_shim is not None
    assert libadobe.sign_node(node) == through_shim
