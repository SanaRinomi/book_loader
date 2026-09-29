"""Reading PKCS#12 bundles without oscrypto (REFACTOR_PLAN decision 21, §16).

The vendored ``libadobe`` reads the PKCS#12 bundle in ``activation.xml`` through three
oscrypto names. This module provides the same three, with the same arguments and
results, in pure Python: ``asn1crypto`` parses, ``hashlib`` derives keys, and
``pycryptodome`` decrypts. No native OpenSSL is loaded, so oscrypto's failure to detect
OpenSSL 3 on some Linux systems can't happen here.

- ``keys.parse_pkcs12(data, password)`` returns ``(private_key, certificate, others)``:
  an ``asn1crypto.keys.PrivateKeyInfo``, an ``asn1crypto.x509.Certificate`` and a list of
  the other certificates. The key and certificate are paired as oscrypto pairs them.
  One deliberate difference: a key in an unencrypted key bag is returned without the bag's
  ``[0]`` tag, so it dumps as PKCS#8. oscrypto keeps the tag. Adobe's key bags are always
  encrypted, so libadobe never sees this.
- ``dump_certificate(certificate, encoding)`` and ``dump_private_key(key, None, encoding)``
  return DER or PEM bytes. The private key is unencrypted PKCS#8.

Supported encryption, which covers what Adobe's servers and OpenSSL write (Adobe uses
RC2-40 for the certificate and 3DES for the key):

- PKCS#12 PBE with SHA-1: 3DES (3-key and 2-key), RC2-128 and RC2-40
- PBES2: PBKDF2 (HMAC with SHA-1 or SHA-2) with AES-128, AES-192 or AES-256 in CBC mode

Anything else raises ``Pkcs12UnsupportedError`` naming the algorithm. The MAC is checked
when present; a wrong password or a changed MAC raises ``Pkcs12PasswordError``. Both are
``ValueError`` subclasses, as oscrypto's errors were.

Key derivation follows RFC 7292 Appendix B.2 and gives the same bytes as OpenSSL's
PKCS12KDF (see the test vectors). oscrypto 1.3.0 uses its own pure-Python derivation on
Windows, and it gets keys longer than one hash wrong when the first hash block starts with
a zero byte. For a 3DES key that happens for about one salt in 465 (measured), and oscrypto
then can't decrypt the key. ``tests/fixtures/pkcs12/oscrypto_kdf_bug.p12`` is such a file.
"""

from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace

from asn1crypto import pem
from asn1crypto.algos import EncryptionAlgorithm
from asn1crypto.cms import EncryptedData
from asn1crypto.core import OctetString
from asn1crypto.keys import EncryptedPrivateKeyInfo, PrivateKeyInfo, PublicKeyInfo
from asn1crypto.pkcs12 import CertBag, Pfx, SafeContents
from asn1crypto.x509 import Certificate
from Crypto.Cipher import AES, ARC2, DES3
from Crypto.Util.Padding import unpad

__all__ = [
    "Pkcs12Error",
    "Pkcs12PasswordError",
    "Pkcs12UnsupportedError",
    "dump_certificate",
    "dump_private_key",
    "keys",
    "parse_pkcs12",
    "pkcs12_kdf",
]


class Pkcs12Error(ValueError):
    """The PKCS#12 data can't be read."""


class Pkcs12PasswordError(Pkcs12Error):
    """The MAC doesn't match or decryption fails: a wrong password, or changed data."""


class Pkcs12UnsupportedError(Pkcs12Error):
    """The data uses an algorithm or structure this module doesn't implement."""

    def __init__(self, what: str, algorithm: str):
        super().__init__(f"Unsupported {what} in PKCS#12 data: {algorithm}")
        self.algorithm = algorithm


# Hash block sizes in bytes (the "v" of RFC 7292 Appendix B).
_KDF_BLOCK_SIZE = {"sha1": 64, "sha224": 64, "sha256": 64, "sha384": 128, "sha512": 128}
_PBKDF2_HASHES = frozenset(_KDF_BLOCK_SIZE)

# PKCS#12 PBE schemes: cipher and key length in bytes. All use SHA-1 and 8-byte blocks.
_PKCS12_PBE = {
    "pkcs12_sha1_tripledes_3key": ("tripledes", 24),
    "pkcs12_sha1_tripledes_2key": ("tripledes", 16),
    "pkcs12_sha1_rc2_128": ("rc2", 16),
    "pkcs12_sha1_rc2_40": ("rc2", 5),
}
_PBES2_AES_KEY_LENGTH = {"aes128_cbc": 16, "aes192_cbc": 24, "aes256_cbc": 32}


def pkcs12_kdf(
    hash_name: str, password: bytes, salt: bytes, iterations: int, length: int, purpose: int
) -> bytes:
    """RFC 7292 Appendix B.2. ``purpose`` is 1 for a key, 2 for an IV, 3 for a MAC key.

    ``password`` is UTF-8; it is used as a NUL-terminated UTF-16BE string, as oscrypto
    and OpenSSL do (so an empty password is two zero bytes).
    """
    if hash_name not in _KDF_BLOCK_SIZE:
        raise Pkcs12UnsupportedError("PKCS#12 key derivation hash", hash_name)
    new_hash = getattr(hashlib, hash_name)
    v = _KDF_BLOCK_SIZE[hash_name]
    diversifier = bytes([purpose]) * v
    text = password.decode("utf-8").encode("utf-16-be") + b"\0\0"
    i = bytearray(_fill(salt, v) + _fill(text, v))
    output = b""
    while True:
        a = new_hash(diversifier + i).digest()
        for _ in range(iterations - 1):
            a = new_hash(a).digest()
        output += a
        if len(output) >= length:
            return output[:length]
        # Step 6: I_j = (I_j + B + 1) mod 2^(8v), kept v bytes wide.
        b = int.from_bytes(_fill(a, v), "big") + 1
        modulus = 1 << (8 * v)
        for start in range(0, len(i), v):
            block = (int.from_bytes(i[start : start + v], "big") + b) % modulus
            i[start : start + v] = block.to_bytes(v, "big")


def _fill(data: bytes, v: int) -> bytes:
    """``data`` repeated to the next multiple of ``v`` bytes (empty stays empty)."""
    if not data:
        return b""
    length = v * -(-len(data) // v)
    return (data * -(-length // len(data)))[:length]


def _decrypt(algorithm: EncryptionAlgorithm, ciphertext: bytes, password: bytes) -> bytes:
    name = algorithm["algorithm"].native
    if name in _PKCS12_PBE:
        cipher, key_length = _PKCS12_PBE[name]
        params = algorithm["parameters"]
        salt, iterations = params["salt"].native, params["iterations"].native
        key = pkcs12_kdf("sha1", password, salt, iterations, key_length, 1)
        iv = pkcs12_kdf("sha1", password, salt, iterations, 8, 2)
        if cipher == "tripledes":
            decryptor = DES3.new(key, DES3.MODE_CBC, iv=iv)
        else:
            decryptor = ARC2.new(key, ARC2.MODE_CBC, iv=iv, effective_keylen=8 * key_length)
        return _unpad(decryptor.decrypt(ciphertext), 8, name)

    if name == "pbes2":
        kdf = algorithm["parameters"]["key_derivation_func"]["algorithm"].native
        if kdf != "pbkdf2":
            raise Pkcs12UnsupportedError("PBES2 key derivation", kdf)
        scheme = algorithm["parameters"]["encryption_scheme"]["algorithm"].native
        if scheme not in _PBES2_AES_KEY_LENGTH:
            raise Pkcs12UnsupportedError("PBES2 cipher", scheme)
        prf = algorithm.kdf_hmac
        if prf not in _PBKDF2_HASHES:
            raise Pkcs12UnsupportedError("PBKDF2 hash", prf)
        key_length = _PBES2_AES_KEY_LENGTH[scheme]
        key = hashlib.pbkdf2_hmac(
            prf, password, algorithm.kdf_salt, algorithm.kdf_iterations, key_length
        )
        decryptor = AES.new(key, AES.MODE_CBC, iv=algorithm.encryption_iv)
        return _unpad(decryptor.decrypt(ciphertext), 16, f"pbes2/{scheme}")

    raise Pkcs12UnsupportedError("encryption", name)


def _unpad(data: bytes, block_size: int, algorithm: str) -> bytes:
    try:
        return unpad(data, block_size)
    except ValueError:
        raise Pkcs12PasswordError(
            f"Could not decrypt PKCS#12 data ({algorithm}): wrong password, or changed data"
        ) from None


def _check_mac(pfx: Pfx, password: bytes) -> None:
    mac_data = pfx["mac_data"]
    if not mac_data:
        return
    hash_name = mac_data["mac"]["digest_algorithm"]["algorithm"].native
    if hash_name not in _KDF_BLOCK_SIZE:
        raise Pkcs12UnsupportedError("MAC algorithm", hash_name)
    new_hash = getattr(hashlib, hash_name)
    key = pkcs12_kdf(
        hash_name,
        password,
        mac_data["mac_salt"].native,
        mac_data["iterations"].native,
        new_hash().digest_size,
        3,
    )
    computed = hmac.new(key, pfx["auth_safe"]["content"].contents, new_hash).digest()
    if not hmac.compare_digest(computed, mac_data["mac"]["digest"].native):
        raise Pkcs12PasswordError(
            "PKCS#12 MAC check failed: wrong password, or the data was changed"
        )


def _fingerprint(key: PrivateKeyInfo | PublicKeyInfo) -> bytes:
    """oscrypto's key fingerprint, used to pair each private key with its certificate."""
    if isinstance(key, PrivateKeyInfo):
        if key.algorithm != "rsa":
            raise Pkcs12UnsupportedError("private key type", key.algorithm)
        parsed = key["private_key"].parsed
        text = "%d:%d" % (parsed["modulus"].native, parsed["public_exponent"].native)
        return hashlib.sha256(text.encode("utf-8")).digest()

    if key.algorithm == "rsa":
        parsed = key["public_key"].parsed
        text = "%d:%d" % (parsed["modulus"].native, parsed["public_exponent"].native)
        return hashlib.sha256(text.encode("utf-8")).digest()
    if key.algorithm == "dsa":
        params = key["algorithm"]["parameters"]
        text = "%d:%d:%d:%d" % (
            params["p"].native,
            params["q"].native,
            params["g"].native,
            key["public_key"].parsed.native,
        )
        return hashlib.sha256(text.encode("utf-8")).digest()
    if key.algorithm == "ec":
        return hashlib.sha256(
            ("%s:" % key.curve[1]).encode("utf-8") + key["public_key"].native
        ).digest()
    # oscrypto fails on other certificate key types; any stable value will do here.
    return hashlib.sha256(key.dump()).digest()


def _read_safe_contents(
    contents: SafeContents,
    password: bytes,
    certs: dict[bytes, Certificate],
    private_keys: dict[bytes, PrivateKeyInfo],
) -> None:
    for bag in contents:
        value = bag["bag_value"]
        if isinstance(value, CertBag):
            if value["cert_id"].native == "x509":
                cert = value["cert_value"].parsed
                public_key = cert["tbs_certificate"]["subject_public_key_info"]
                certs[_fingerprint(public_key)] = cert
        elif isinstance(value, PrivateKeyInfo):
            # Without the bag's explicit [0] tag, so it dumps as PKCS#8.
            private_keys[_fingerprint(value)] = value.untag()
        elif isinstance(value, EncryptedPrivateKeyInfo):
            data = _decrypt(value["encryption_algorithm"], value["encrypted_data"].native, password)
            private_key = PrivateKeyInfo.load(data)
            private_keys[_fingerprint(private_key)] = private_key
        elif isinstance(value, SafeContents):
            _read_safe_contents(value, password, certs, private_keys)
        # CRL and secret bags aren't needed.


def parse_pkcs12(
    data: bytes, password: bytes | None = None
) -> tuple[PrivateKeyInfo | None, Certificate | None, list[Certificate]]:
    """The private key, its certificate and the other certificates in a PKCS#12 bundle.

    Same arguments and results as ``oscrypto.keys.parse_pkcs12``.
    """
    if not isinstance(data, bytes):
        raise TypeError(f"data must be bytes, not {type(data).__name__}")
    if password is None:
        password = b""
    elif not isinstance(password, bytes):
        raise TypeError(f"password must be bytes, not {type(password).__name__}")

    try:
        pfx = Pfx.load(data)
        content_type = pfx["auth_safe"]["content_type"].native
    except ValueError as err:
        raise Pkcs12Error(f"Not valid PKCS#12 data: {err}") from err
    if content_type != "data":
        raise Pkcs12UnsupportedError("integrity mode", f"public-key ({content_type})")
    _check_mac(pfx, password)

    certs: dict[bytes, Certificate] = {}
    private_keys: dict[bytes, PrivateKeyInfo] = {}
    for content_info in pfx.authenticated_safe:
        content = content_info["content"]
        if isinstance(content, OctetString):
            contents = SafeContents.load(content.native)
        elif isinstance(content, EncryptedData):
            info = content["encrypted_content_info"]
            plaintext = _decrypt(
                info["content_encryption_algorithm"], info["encrypted_content"].native, password
            )
            contents = SafeContents.load(plaintext)
        else:
            kind = content_info["content_type"].native
            raise Pkcs12UnsupportedError("privacy mode", f"public-key ({kind})")
        _read_safe_contents(contents, password, certs, private_keys)

    # Pairing and ordering as oscrypto does them, so the results are the same.
    common = sorted(private_keys.keys() & certs.keys())
    if common:
        fingerprint = common[0]
        others = [cert for f, cert in certs.items() if f != fingerprint]
        return private_keys[fingerprint], certs[fingerprint], others

    key = private_keys[min(private_keys)] if private_keys else None
    cert = certs.pop(min(certs)) if certs else None
    others = sorted(certs.values(), key=lambda c: c.subject.human_friendly)
    return key, cert, others


def dump_certificate(certificate: Certificate, encoding: str = "pem") -> bytes:
    """The certificate as DER or PEM bytes, like ``oscrypto.asymmetric.dump_certificate``."""
    if encoding not in ("pem", "der"):
        raise ValueError(f'encoding must be "pem" or "der", not {encoding!r}')
    if not isinstance(certificate, Certificate):
        raise TypeError(
            f"certificate must be an asn1crypto Certificate, not {type(certificate).__name__}"
        )
    output = certificate.dump()
    return pem.armor("CERTIFICATE", output) if encoding == "pem" else output


def dump_private_key(
    private_key: PrivateKeyInfo, passphrase: str | None, encoding: str = "pem"
) -> bytes:
    """The key as unencrypted PKCS#8 DER or PEM bytes, like ``oscrypto``'s function.

    Only ``passphrase=None`` is supported; ``libadobe`` never encrypts the key.
    """
    if encoding not in ("pem", "der"):
        raise ValueError(f'encoding must be "pem" or "der", not {encoding!r}')
    if passphrase is not None:
        raise Pkcs12UnsupportedError("private key export", "encryption with a passphrase")
    if not isinstance(private_key, PrivateKeyInfo):
        raise TypeError(
            f"private_key must be an asn1crypto PrivateKeyInfo, not {type(private_key).__name__}"
        )
    output = private_key.dump()
    return pem.armor("PRIVATE KEY", output) if encoding == "pem" else output


# libadobe calls ``keys.parse_pkcs12``, as in ``from oscrypto import keys``.
keys = SimpleNamespace(parse_pkcs12=parse_pkcs12)
