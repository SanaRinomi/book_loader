"""T1.5: ``adobe/pkcs12.py``, the pure-Python replacement for the oscrypto calls in libadobe.

The shim is checked against ``cryptography`` (always) and against oscrypto (while it is
installed). Tests comparing with oscrypto skip when it can't be imported.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from asn1crypto.algos import EncryptionAlgorithm
from asn1crypto.keys import PrivateKeyInfo
from asn1crypto.pkcs12 import Pfx
from asn1crypto.x509 import Certificate

from book_loader.adobe import pkcs12 as shim
from book_loader.adobe.pkcs12 import (
    Pkcs12Error,
    Pkcs12PasswordError,
    Pkcs12UnsupportedError,
    dump_certificate,
    dump_private_key,
    parse_pkcs12,
    pkcs12_kdf,
)
from tests.fixtures.builders._random import rsa_key
from tests.fixtures.builders.pkcs12 import (
    FIXTURES,
    LEGACY_PASSWORD,
    PASSWORD,
    SCHEMES,
    build_pkcs12,
    reference,
)

LEGACY = FIXTURES / "legacy_rc2_40.p12"
KDF_BUG = FIXTURES / "oscrypto_kdf_bug.p12"

# From `openssl kdf ... PKCS12KDF` (OpenSSL 3.5), with the password given as UTF-16BE plus
# two zero bytes. The first two are salts where oscrypto 1.3.0 gets the last bytes wrong.
KDF_VECTORS = [
    (
        "sha1",
        "pw",
        "00490d247ccd37d0",
        1,
        24,
        1,
        "007a09d18b14573b37d648062d3c6e015aa137c6ee72759a",
    ),
    (
        "sha1",
        "pw",
        "df6af1d8303e61cd",
        1,
        24,
        1,
        "004314874c454aad0be8142ce3473983b3bff1c1ed75d703",
    ),
    ("sha1", "", "0001020304050607", 2048, 8, 2, "a6e6462d035fd485"),
    (
        "sha1",
        "book-loader",
        "73616c7473616c74",
        100000,
        20,
        3,
        "3275e12303046872868802d1ee494fbf2fb818b1",
    ),
    (
        "sha256",
        "pässwörd",
        "000102030405060708090a0b0c0d0e0f",
        10,
        32,
        3,
        "1ea3e64a34633a6b53809f2fb0482f78268d29b942b5459ee70eab55df4df315",
    ),
    (
        "sha512",
        "pw",
        "ffffffffffffffff",
        5,
        100,
        1,
        "df87bb55ec17fb28160fe62ff215fabda5e91ff93733e4e98640710fc22e34e601e2ec4f513f8fd7b0"
        "8e9ceff33e43c2e88aa96cb06b8eb04426680960640e5b22541727c83fa04ce52bb571ce4c41b676f7c5"
        "53b826859a29587d8c8792abcd46f5fa73",
    ),
]


def results(data: bytes, password: bytes) -> tuple[bytes, bytes, list[bytes]]:
    """The shim's three results as DER, the way libadobe uses them."""
    key, cert, others = shim.keys.parse_pkcs12(data, password)
    assert key is not None and cert is not None
    return (
        dump_private_key(key, None, "der"),
        dump_certificate(cert, encoding="der"),
        [dump_certificate(c, encoding="der") for c in others],
    )


def oscrypto_results(data: bytes, password: bytes) -> tuple[bytes, bytes, list[bytes]]:
    keys = pytest.importorskip("oscrypto.keys")
    asymmetric = pytest.importorskip("oscrypto.asymmetric")
    key, cert, others = keys.parse_pkcs12(data, password)
    return (
        asymmetric.dump_private_key(key, None, "der"),
        asymmetric.dump_certificate(cert, encoding="der"),
        [asymmetric.dump_certificate(c, encoding="der") for c in others],
    )


def oscrypto_can_read(data: bytes, password: bytes) -> bool:
    keys = pytest.importorskip("oscrypto.keys")
    try:
        keys.parse_pkcs12(data, password)
    except (ValueError, OSError):  # its 3DES key derivation bug; OSError from Windows CNG
        return False
    return True


# ---------------------------------------------------------------- key derivation


@pytest.mark.parametrize(
    "hash_name, password, salt, iterations, length, purpose, expected", KDF_VECTORS
)
def test_kdf_matches_openssl(hash_name, password, salt, iterations, length, purpose, expected):
    derived = pkcs12_kdf(
        hash_name, password.encode("utf-8"), bytes.fromhex(salt), iterations, length, purpose
    )
    assert derived.hex() == expected


def test_kdf_rejects_unknown_hashes():
    with pytest.raises(Pkcs12UnsupportedError, match="md5"):
        pkcs12_kdf("md5", b"pw", b"salt", 1, 16, 1)


# ---------------------------------------------------------------- reading bundles


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
@pytest.mark.parametrize("extra", [0, 2], ids=["alone", "with_cas"])
def test_results_match_cryptography(scheme: str, extra: int):
    data = build_pkcs12(scheme, extra_certificates=extra)
    key, cert, others = results(data, PASSWORD)
    ref = reference(data, PASSWORD)
    assert key == ref.key
    assert cert == ref.certificate
    assert sorted(others) == sorted(ref.others)
    assert len(others) == extra


@pytest.mark.parametrize("path", [LEGACY, KDF_BUG], ids=["legacy_rc2_40", "oscrypto_kdf_bug"])
def test_openssl_legacy_files_match_cryptography(path: Path):
    """RC2-40 certificate bag, 3DES key bag and SHA-1 MAC, as Adobe's servers write them."""
    data = path.read_bytes()
    key, cert, others = results(data, LEGACY_PASSWORD)
    ref = reference(data, LEGACY_PASSWORD)
    assert (key, cert, others) == (ref.key, ref.certificate, [])
    assert (
        PrivateKeyInfo.load(key)["private_key"].parsed["modulus"].native
        == rsa_key("pkcs12-legacy").n
    )


def test_results_are_asn1crypto_objects():
    key, cert, others = parse_pkcs12(build_pkcs12(extra_certificates=1), PASSWORD)
    assert isinstance(key, PrivateKeyInfo)
    assert isinstance(cert, Certificate)
    assert [type(c) for c in others] == [Certificate]
    assert shim.keys.parse_pkcs12 is parse_pkcs12


def test_the_key_is_paired_with_its_certificate():
    key, cert, _ = parse_pkcs12(build_pkcs12(extra_certificates=2), PASSWORD)
    assert key is not None and cert is not None
    public = cert["tbs_certificate"]["subject_public_key_info"]["public_key"].parsed
    assert key["private_key"].parsed["modulus"].native == public["modulus"].native


# ---------------------------------------------------------------- same as oscrypto


@pytest.fixture(scope="module", params=[(s, n) for s in sorted(SCHEMES) for n in (0, 2)])
def oscrypto_bundle(request) -> bytes:
    """A bundle oscrypto can read: its key derivation fails for about one salt in 465."""
    scheme, extra = request.param
    for _ in range(20):
        data = build_pkcs12(scheme, extra_certificates=extra)
        if oscrypto_can_read(data, PASSWORD):
            return data
    raise AssertionError("oscrypto could not read 20 bundles in a row")


def test_results_match_oscrypto(oscrypto_bundle: bytes):
    assert results(oscrypto_bundle, PASSWORD) == oscrypto_results(oscrypto_bundle, PASSWORD)


def test_legacy_file_matches_oscrypto():
    data = LEGACY.read_bytes()
    assert results(data, LEGACY_PASSWORD) == oscrypto_results(data, LEGACY_PASSWORD)


def test_oscrypto_cannot_read_the_kdf_bug_file():
    """The shim reads it (above); oscrypto 1.3.0 derives the wrong 3DES key."""
    assert not oscrypto_can_read(KDF_BUG.read_bytes(), LEGACY_PASSWORD)


def test_pem_output_matches_oscrypto():
    asymmetric = pytest.importorskip("oscrypto.asymmetric")
    key, cert, _ = parse_pkcs12(LEGACY.read_bytes(), LEGACY_PASSWORD)
    assert key is not None and cert is not None
    assert dump_certificate(cert) == asymmetric.dump_certificate(cert)
    assert dump_private_key(key, None) == asymmetric.dump_private_key(key, None)


# ---------------------------------------------------------------- errors


def test_wrong_password():
    with pytest.raises(Pkcs12PasswordError, match="MAC check failed"):
        parse_pkcs12(build_pkcs12(), b"not the password")


def change_mac(data: bytes) -> bytes:
    pfx = Pfx.load(data)
    digest = bytearray(pfx["mac_data"]["mac"]["digest"].native)
    digest[0] ^= 0x01
    pfx["mac_data"]["mac"]["digest"] = bytes(digest)
    return pfx.dump(force=True)


def test_changed_mac():
    with pytest.raises(Pkcs12PasswordError, match="MAC check failed"):
        parse_pkcs12(change_mac(build_pkcs12()), PASSWORD)


def without_mac(data: bytes) -> bytes:
    pfx = Pfx.load(data)
    return Pfx({"version": pfx["version"], "auth_safe": pfx["auth_safe"]}).dump()


def test_wrong_password_without_a_mac_fails_to_decrypt():
    data = without_mac(LEGACY.read_bytes())
    assert results(data, LEGACY_PASSWORD) == results(LEGACY.read_bytes(), LEGACY_PASSWORD)
    with pytest.raises(Pkcs12PasswordError, match="pkcs12_sha1_rc2_40"):
        parse_pkcs12(data, b"wrong password")


def edit_certificate_bag_algorithm(data: bytes, edit) -> bytes:
    """``data`` with its encrypted certificate bag's algorithm changed by ``edit``, and the MAC
    dropped so that it doesn't fail first. ``edit`` changes the algorithm in place, or
    returns a replacement."""
    pfx = Pfx.load(without_mac(data))
    safe = pfx.authenticated_safe
    for content_info in safe:
        if content_info["content_type"].native == "encrypted_data":
            info = content_info["content"]["encrypted_content_info"]
            algorithm = info["content_encryption_algorithm"]
            info["content_encryption_algorithm"] = edit(algorithm) or algorithm
    pfx["auth_safe"]["content"] = safe.dump(force=True)
    return pfx.dump(force=True)


@pytest.mark.parametrize("algorithm", ["pkcs12_sha1_rc4_128", "pbes1_sha1_des"])
def test_unsupported_encryption(algorithm: str):
    data = edit_certificate_bag_algorithm(
        LEGACY.read_bytes(),
        lambda old: EncryptionAlgorithm(
            {"algorithm": algorithm, "parameters": old["parameters"].native}
        ),
    )
    with pytest.raises(Pkcs12UnsupportedError, match=algorithm) as error:
        parse_pkcs12(data, LEGACY_PASSWORD)
    assert error.value.algorithm == algorithm


def test_unsupported_pbes2_cipher():
    def edit(algorithm):
        algorithm["parameters"]["encryption_scheme"]["algorithm"] = "tripledes_3key"

    data = edit_certificate_bag_algorithm(build_pkcs12("aes256"), edit)
    with pytest.raises(Pkcs12UnsupportedError, match="PBES2 cipher.*tripledes_3key"):
        parse_pkcs12(data, PASSWORD)


def test_unsupported_pbkdf2_hash():
    def edit(algorithm):
        prf = algorithm["parameters"]["key_derivation_func"]["parameters"]["prf"]
        prf["algorithm"] = "sha3_256"

    data = edit_certificate_bag_algorithm(build_pkcs12("aes256"), edit)
    with pytest.raises(Pkcs12UnsupportedError, match="PBKDF2 hash.*sha3_256"):
        parse_pkcs12(data, PASSWORD)


def test_unsupported_mac_algorithm():
    pfx = Pfx.load(build_pkcs12())
    pfx["mac_data"]["mac"]["digest_algorithm"] = {"algorithm": "sha3_256"}
    with pytest.raises(Pkcs12UnsupportedError, match="MAC algorithm.*sha3_256"):
        parse_pkcs12(pfx.dump(force=True), PASSWORD)


def test_no_password_for_a_protected_bundle():
    with pytest.raises(Pkcs12PasswordError):
        parse_pkcs12(build_pkcs12(), None)


# ---------------------------------------------------------------- other layouts


def dump_all(parsed) -> tuple[bytes | None, bytes | None, list[bytes]]:
    key, cert, others = parsed
    return (
        key.dump() if key is not None else None,
        cert.dump() if cert is not None else None,
        [c.dump() for c in others],
    )


def test_unencrypted_bundle():
    """No encryption: a plain key bag, and a MAC made with an empty password."""
    data = build_pkcs12(None, extra_certificates=1)
    key, cert, others = dump_all(parse_pkcs12(data, None))
    ref = reference(data, None)
    assert (key, cert, others) == (ref.key, ref.certificate, list(ref.others))
    # oscrypto keeps the key bag's [0] tag; without it, the key is the same.
    keys = pytest.importorskip("oscrypto.keys")
    o_key, o_cert, o_others = keys.parse_pkcs12(data, None)
    assert o_key.dump() != key and o_key.untag().dump() == key
    assert (cert, others) == dump_all((None, o_cert, o_others))[1:]


def test_certificates_only_ordered_like_oscrypto():
    """No key: the first certificate is picked, and the rest ordered, as oscrypto does."""
    data = build_pkcs12("aes256", with_key=False, extra_certificates=3)
    key, cert, others = dump_all(parse_pkcs12(data, PASSWORD))
    assert key is None and cert is not None and len(others) == 2
    keys = pytest.importorskip("oscrypto.keys")
    assert (key, cert, others) == dump_all(keys.parse_pkcs12(data, PASSWORD))


def test_ec_ca_certificates_ordered_like_oscrypto():
    data = build_pkcs12("aes256", extra_certificates=1, ec_certificates=2)
    parsed = dump_all(parse_pkcs12(data, PASSWORD))
    assert sorted(parsed[2]) == sorted(reference(data, PASSWORD).others)
    keys = pytest.importorskip("oscrypto.keys")
    assert parsed == dump_all(keys.parse_pkcs12(data, PASSWORD))


def test_not_pkcs12():
    with pytest.raises(Pkcs12Error, match="Not valid PKCS#12"):
        parse_pkcs12(b"\x30\x03\x02\x01\x05", PASSWORD)


def test_errors_are_value_errors_like_oscrypto():
    assert issubclass(Pkcs12PasswordError, ValueError)
    assert issubclass(Pkcs12UnsupportedError, ValueError)


def test_argument_types():
    with pytest.raises(TypeError):
        parse_pkcs12("not bytes", PASSWORD)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_pkcs12(build_pkcs12(), "a str password")  # type: ignore[arg-type]
    key, cert, _ = parse_pkcs12(LEGACY.read_bytes(), LEGACY_PASSWORD)
    assert key is not None and cert is not None
    with pytest.raises(ValueError, match="encoding"):
        dump_certificate(cert, encoding="text")
    with pytest.raises(Pkcs12UnsupportedError, match="passphrase"):
        dump_private_key(key, "secret", "der")
    with pytest.raises(TypeError):
        dump_certificate(key, "der")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="encoding"):
        dump_private_key(key, None, "text")
    with pytest.raises(TypeError):
        dump_private_key(cert, None, "der")  # type: ignore[arg-type]
