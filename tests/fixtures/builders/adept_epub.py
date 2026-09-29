"""Synthetic ADEPT-encrypted EPUBs that the vendored ``ineptepub`` can decrypt (T0.4.6).

Layout, as ``ineptepub.decryptBook`` expects it:
- ``META-INF/rights.xml`` holds ``<adept:encryptedKey>``: the 16-byte AES book key,
  encrypted with RSA PKCS#1 v1.5 under the device key, base64 (172 characters for a
  1024-bit key). No ``keyType`` attribute, so no hardening applies.
- ``META-INF/encryption.xml`` lists each encrypted entry with the ``aes128-cbc`` method.
- Each listed entry is raw-deflated, PKCS#7 padded, and AES-CBC encrypted with a random
  IV that is stored in front of the ciphertext.
"""

from __future__ import annotations

import base64
import zlib
from pathlib import Path

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA

from tests.fixtures.builders._random import DeterministicRandom
from tests.fixtures.builders.epub import default_files, write_epub

AES_CBC = "http://www.w3.org/2001/04/xmlenc#aes128-cbc"

# Entries that stay in plain text, as in real ADEPT books.
PLAIN_ENTRIES = ("mimetype", "META-INF/container.xml", "OEBPS/content.opf")


def _raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def _pkcs7(data: bytes) -> bytes:
    pad = 16 - len(data) % 16
    return data + bytes([pad]) * pad


def _rights_xml(encrypted_key_b64: str) -> bytes:
    return f"""<?xml version="1.0"?>
<adept:rights xmlns:adept="http://ns.adobe.com/adept">
<adept:licenseToken>
<adept:user>urn:uuid:11111111-1111-4111-8111-111111111111</adept:user>
<adept:resource>urn:uuid:33333333-3333-4333-8333-333333333333</adept:resource>
<adept:encryptedKey>{encrypted_key_b64}</adept:encryptedKey>
</adept:licenseToken>
</adept:rights>
""".encode("utf-8")


def _encryption_xml(paths: list[str]) -> bytes:
    items = "".join(
        f'<enc:EncryptedData><enc:EncryptionMethod Algorithm="{AES_CBC}"/>'
        f'<enc:CipherData><enc:CipherReference URI="{p}"/></enc:CipherData>'
        f"</enc:EncryptedData>\n"
        for p in paths
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
        'xmlns:enc="http://www.w3.org/2001/04/xmlenc#">\n'
        f"{items}</encryption>\n"
    ).encode("utf-8")


def build_adept_epub(
    path: Path,
    device_key: RSA.RsaKey,
    files: dict[str, bytes] | None = None,
    seed: str = "adept-epub",
) -> dict[str, bytes]:
    """Write an ADEPT EPUB encrypted for ``device_key``.

    Returns the plaintext of every entry that ``decryptBook`` writes back out, which is
    every entry except ``rights.xml`` and ``encryption.xml``.
    """
    files = default_files() if files is None else files
    rng = DeterministicRandom(seed)
    book_key = rng.read(16)
    encrypted_key = PKCS1_v1_5.new(device_key.public_key(), randfunc=rng.read).encrypt(book_key)

    encrypted_paths = [name for name in files if name not in PLAIN_ENTRIES]
    stored: dict[str, bytes] = {}
    for name, data in files.items():
        if name in encrypted_paths:
            iv = rng.read(16)
            data = iv + AES.new(book_key, AES.MODE_CBC, iv).encrypt(_pkcs7(_raw_deflate(data)))
        stored[name] = data
    stored["META-INF/rights.xml"] = _rights_xml(base64.b64encode(encrypted_key).decode("ascii"))
    stored["META-INF/encryption.xml"] = _encryption_xml(encrypted_paths)

    write_epub(path, stored)
    return {"mimetype": b"application/epub+zip", **files}
