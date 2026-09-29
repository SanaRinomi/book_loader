"""Generate the Phase 0 golden files, v0 data and OS output fixtures (T0.4).

    uv run python -m tests.tools.make_fixtures             # everything
    uv run python -m tests.tools.make_fixtures redact v0   # some targets

Targets:
    redact          golden/redact.json          (T0.4.1)
    safe_filename   golden/safe_filename.json   (T0.4.2)
    kobo_keys       golden/kobo_keys.json       (T0.4.3)
    os_output       os_output/*_captured.txt    (T0.4.3; this OS only)
    v0              v0/ auth archives, pending record and link page (T0.4.4, T0.4.5)
    pkcs12          pkcs12/*.p12 made by OpenSSL -legacy (T1.5; needs openssl and oscrypto)

Golden values come from the 0.1.0 code and are the contract later phases must keep.
An existing golden file is never changed unless ``--force`` is given, and only for a
change decided in REFACTOR_PLAN.md. Archives and captures differ on every run, so they
are only created when missing.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import tempfile
import types
from collections.abc import Callable
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# --------------------------------------------------------------------------- redact

REDACT_URLS = [
    "",
    "not a url",
    "http://[invalid",
    "https://acs.example.com/fulfillment/Fulfill",
    "http://adeactivate.adobe.com/adept/SignInDirect",
    "https://adeactivate.adobe.com/adept/InitLicenseService",
    "https://acs4.example.org/fulfillment/URLLink.acsm?action=enterorder&ordersource=Store"
    "&orderid=1234567890&resid=urn%3Auuid%3A33333333-3333-4333-8333-333333333333"
    "&gbauthdate=09%2F28%2F2026&dateval=1790000000&gblver=4&auth=0a1b2c3d4e5f60718293a4b5c6d7e8f9",
    "https://books.googleusercontent.com/download/ebook/AbCdEfGhIjKlMnOp/9780000000001.epub"
    "?id=AbCdEfGh123&output=epub&source=gbs_api&sig=ACfU3U0aBcDeFgHiJkLmNoPqRsTuVwXyZ",
    "https://download.example.com/books/test-book.epub?id=AbCdEf123456&amp;output=epub"
    "&amp;sig=ZmFrZXNpZ25hdHVyZXZhbHVl",
    "https://example.com:8443/books/My%20Secret%20Title.pdf#page=3",
    "https://reader:secret@example.com/private/area",
    "http://192.168.1.20/books/x.epub",
    "http://ns.adobe.com/adept/1.1",
    "https://example.com/a/UPPER/lower/12345/verylongpathsegmentname/file.tar.gz",
    "https://example.com/?format=pdf&type=book&hl=en&token=abc&Output=EPUB",
    "https://example.com/path/;params?q=1",
]

REDACT_HEADERS = [
    ("Set-Cookie", "SID=abcdef0123456789; Path=/; HttpOnly"),
    ("Cookie", "NID=511=abcdef; 1P_JAR=2026-09-28"),
    ("Authorization", "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl"),
    ("Proxy-Authorization", "Basic dXNlcjpwYXNz"),
    ("Location", "https://download.example.com/books/test-book.epub?id=AbCdEf123456&output=epub"),
    ("Content-Location", "https://example.com/secret/Title.epub"),
    ("Referer", "https://www.google.com/books/edition/_/AbCdEfGh123?gbpv=1"),
    ("Content-Type", "application/vnd.adobe.adept+xml"),
    ("Content-Length", "123456"),
    ("X-Request-Id", "33333333-3333-4333-8333-333333333333"),
    ("Date", "Mon, 28 Sep 2026 10:00:00 GMT"),
    ("X-Forwarded-For", "203.0.113.7, 2001:db8::1"),
    ("Server", "Apache"),
    ("Empty", ""),
]

REDACT_TEXTS = [
    "",
    "Connection reset by peer",
    """<?xml version="1.0"?>
<adept:fulfill xmlns:adept="http://ns.adobe.com/adept">
<adept:user>urn:uuid:11111111-1111-4111-8111-111111111111</adept:user>
<adept:device>urn:uuid:22222222-2222-4222-8222-222222222222</adept:device>
<adept:deviceType>standalone</adept:deviceType>
<adept:fingerprint>ZmFrZSBmaW5nZXJwcmludCB2YWx1ZQ==</adept:fingerprint>
<adept:nonce>AAAAAAAAAAAAAAAA</adept:nonce>
<adept:expiration>2026-09-28T10:10:00Z</adept:expiration>
<adept:signature>c2lnbmF0dXJlIGJ5dGVzIGdvIGhlcmUgYW5kIGFyZSBsb25n</adept:signature>
</adept:fulfill>""",
    """<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>My Private Reading</dc:title><dc:creator>Jane Q. Author</dc:creator>
<dc:publisher>Some House</dc:publisher><dc:identifier>urn:isbn:9780000000001</dc:identifier>
<dc:description>A long description of the book.</dc:description>
<dc:format>application/epub+zip</dc:format><dc:language>en</dc:language>
</metadata>""",
    "<adept:user/><adept:signature></adept:signature><Title>Case Insensitive</Title>",
    '<adept:username method="AdobeID">reader@example.com</adept:username>',
    '<adept:encryptedKey keyInfo="x">ZmFrZSBlbmNyeXB0ZWQga2V5IHZhbHVl</adept:encryptedKey>',
    "Error for reader@example.com from 203.0.113.7 and 2001:db8:85a3::8a2e:370:7334 and ::1",
    "Link: https://acs.example.com/fulfillment/URLLink.acsm?orderid=12345&resid=abc in text",
    "Google account 123456789012345678 blocked; ref 12345678901 is too short",
    "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz",
    "base64 blob QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo= and short QUJD",
    "UUID 33333333-3333-4333-8333-333333333333 and uppercase ABCDEF01-2345-6789-ABCD-EF0123456789",
    "Version 1.2.3 at 10.0.0.1:8080 and 999.999.999.999",
    """<!DOCTYPE html><html><head><title>Sorry...</title></head><body>
<p>Our systems have detected unusual traffic from your computer network (IP address: 198.51.100.23).</p>
<form action="https://www.google.com/sorry/index?continue=https://books.google.com/download&q=EgQKAQIDGJ">
<input type="hidden" name="q" value="EgQKAQIDGJaBmK8GIjAqyZ5T4Hq9aBcDeFgHiJkLmNoPqRsT"></form>
</body></html>""",
    "Multi\nline\ttext with reader.name+tag@sub.example.co.uk inside",
]


def gen_redact() -> dict[str, bytes]:
    from book_loader.utils.redact import redact_header, redact_text, redact_url

    data = {
        "urls": [{"input": u, "output": redact_url(u)} for u in REDACT_URLS],
        "headers": [
            {"name": n, "value": v, "output": redact_header(n, v)} for n, v in REDACT_HEADERS
        ],
        "texts": [{"input": t, "output": redact_text(t)} for t in REDACT_TEXTS],
    }
    return {"golden/redact.json": _json(data)}


# -------------------------------------------------------------------- safe_filename

SAFE_FILENAME_TITLES = [
    # ASCII and punctuation
    "Simple Title",
    "The Lord of the Rings",
    "A",
    "1984",
    "  Leading and trailing spaces  ",
    "Tabs\tand\nnewlines",
    "Harry Potter & the Philosopher's Stone",
    "What? Why! How.",
    "C++ Primer (5th Edition)",
    "Title: Subtitle",
    "Part 1/2",
    "Back\\slash",
    "Quotes \"double\" and 'single'",
    "Pipe | Star * Less < More >",
    "Hash #1 and 100%",
    "Dots... and --- dashes",
    "under_score",
    "Semi;colon,comma",
    "[Brackets] {Braces}",
    "Money $5 @ home",
    "Plus+Equals=Tilde~Caret^Backtick`",
    "R.U.R.",
    "trailing dot.",
    "!!!",
    "...",
    "CON",
    "nul",
    # CJK
    "三體",
    "ノルウェイの森",
    "해리 포터",
    "中文：標題（上冊）",
    "書名/副書名",
    # Accented and other scripts
    "Les Misérables",
    "Über den Fluß",
    "Crème brûlée",
    "Ångström",
    "Ελληνικά",
    "Русский текст",
    "كتاب",
    "ספר",
    "हिन्दी",
    "Cafe\u0301 (combining accent)",
    # Emoji and special characters
    "Emoji 📚 Book",
    "🙂",
    "Family 👨\u200d👩\u200d👧",
    "Zero\u200bWidth",
    "No\u00a0Break",
    "ＡＢＣ１２３",
    "Ⅻ and x²",
    # Long and empty
    "A" * 300,
    "Very long title " * 20,
    "",
    "   ",
]


def gen_safe_filename() -> dict[str, bytes]:
    from book_loader.core.kobo.decryptor import safe_filename

    data = [{"title": t, "filename": safe_filename(t)} for t in SAFE_FILENAME_TITLES]
    return {"golden/safe_filename.json": _json(data)}


# ------------------------------------------------------------------------ kobo_keys

KOBO_MACS = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "00:1B:63:84:45:E6"]
KOBO_USER_IDS = ["aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "12345678-1234-1234-1234-123456789abc"]


def gen_kobo_keys() -> dict[str, bytes]:
    from book_loader.core.kobo.library import KOBO_HASH_KEYS, KoboLibrary

    cases = []
    for mac in KOBO_MACS:
        for user_id in KOBO_USER_IDS:
            stub = types.SimpleNamespace(_get_user_ids=lambda uid=user_id: [uid])
            keys = KoboLibrary._compute_userkeys(stub, mac)
            for hash_key, key in zip(KOBO_HASH_KEYS, keys, strict=True):
                cases.append(
                    {"mac": mac, "user_id": user_id, "hash_key": hash_key, "userkey": key.hex()}
                )
    return {"golden/kobo_keys.json": _json({"hash_keys": KOBO_HASH_KEYS, "cases": cases})}


# ------------------------------------------------------------------------ os_output

_MAC_RE = re.compile(rb"\b[0-9A-Fa-f]{2}([-:])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}\b")
_GUID_RE = re.compile(rb"\{?[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\}?")


def sanitize_os_output(raw: bytes) -> bytes:
    """Replace real MAC addresses and GUIDs with fakes, keeping their format.

    MAC addresses are Kobo key material, so real ones never go into the repository.
    The all-zero address is kept, because parsers must learn to skip it.
    """
    macs: dict[bytes, bytes] = {}
    guids: dict[bytes, bytes] = {}

    def fake_mac(m: re.Match) -> bytes:
        value, sep = m.group(0), m.group(1)
        if set(value) <= set(b"0" + sep):
            return value
        key = value.upper().replace(b"-", b":")
        if key not in macs:
            macs[key] = b"02:00:5E:10:00:%02X" % (len(macs) + 1)
        fake = macs[key].replace(b":", sep)
        return fake.lower() if value.islower() else fake

    def fake_guid(m: re.Match) -> bytes:
        value = m.group(0)
        key = value.upper().strip(b"{}")
        if key not in guids:
            guids[key] = b"00000000-0000-4000-8000-%012X" % (len(guids) + 1)
        fake = guids[key]
        return b"{" + fake + b"}" if value.startswith(b"{") else fake

    return _GUID_RE.sub(fake_guid, _MAC_RE.sub(fake_mac, raw))


def _run(command: list[str]) -> bytes:
    return subprocess.run(command, capture_output=True, check=True).stdout


def gen_os_output() -> dict[str, bytes]:
    system = platform.system()
    if system == "Windows":
        captures = {
            "getmac": _run(["getmac", "/fo", "csv", "/nh", "/v"]),
            "get_netadapter": _run(
                ["powershell", "-NoProfile", "-Command", "Get-NetAdapter | Select MacAddress"]
            ),
            "get_netadapter_csv": _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-NetAdapter | Select-Object Name, Status, MacAddress"
                    " | ConvertTo-Csv -NoTypeInformation",
                ]
            ),
        }
    elif system == "Darwin":
        captures = {"ifconfig_macos": _run(["/sbin/ifconfig", "-a"])}
    elif system == "Linux":
        lines = [
            f"{p.name} {(p / 'address').read_text().strip()}\n".encode()
            for p in sorted(Path("/sys/class/net").iterdir())
            if (p / "address").exists()
        ]
        captures = {"sys_class_net_linux": b"".join(lines)}
    else:
        return {}
    return {
        f"os_output/{name}_captured.txt": sanitize_os_output(raw) for name, raw in captures.items()
    }


# ------------------------------------------------------------------------------- v0

V0_TIMESTAMP = "20260101_000000"
V0_PLACEHOLDER_DIR = r"C:\Users\reader\book-loader-v0"


def gen_v0() -> dict[str, bytes]:
    import time_machine

    from book_loader.cli import backup_auth
    from book_loader.adobe._vendor import libadobe, libadobeFulfill
    from book_loader.core.adobe.fulfill import ACSMFulfiller
    from tests.fixtures.builders.adobe_auth import build_auth_folder

    out: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        auth = build_auth_folder(tmp / ".adobe", seed="v0")

        # T0.4.4: what `auth backup` and `auth reset` wrote in 0.1.0.
        for name in (f"auth_anonymous_{V0_TIMESTAMP}", f"auth_backup_{V0_TIMESTAMP}"):
            archive = backup_auth(auth.path, tmp / "backups" / f"{name}.tar.gz")
            out[f"v0/{name}.tar.gz"] = archive.read_bytes()

        # T0.4.5: a pending record and link page, as written after a blocked download.
        libadobe.update_account_path(str(auth.path))
        reply = (FIXTURES / "replies" / "fulfill_epub.xml").read_bytes()
        info = libadobeFulfill.parse_fulfillment(reply)
        acsm = tmp / "Books" / "The Test Book.acsm"
        acsm.parent.mkdir()
        acsm.write_bytes((FIXTURES / "v0" / "pending_book.acsm").read_bytes())
        account = types.SimpleNamespace(auth_dir=auth.path, get_device_key=lambda: auth.device_key)
        with time_machine.travel("2026-01-01 00:00:00", tick=False):
            link_file = ACSMFulfiller(account)._save_pending(acsm, acsm.parent, info)
        record = next((auth.path / "pending").glob("*.json"))

        # Absolute paths from this temp folder are replaced with a neutral placeholder.
        def neutral(data: bytes) -> bytes:
            text = data.decode("utf-8")
            for form in (str(tmp.resolve()), str(tmp)):
                text = text.replace(json.dumps(form)[1:-1], json.dumps(V0_PLACEHOLDER_DIR)[1:-1])
                text = text.replace(form, V0_PLACEHOLDER_DIR)
            return text.encode("utf-8")

        out[f"v0/pending/{record.name}"] = neutral(record.read_bytes())
        out[f"v0/{link_file.name}"] = neutral(link_file.read_bytes())
    return out


# --------------------------------------------------------------------------- pkcs12

PKCS12_LEGACY_SEED = "pkcs12-legacy"


def gen_pkcs12() -> dict[str, bytes]:
    """T1.5: PKCS#12 files as Adobe's servers write them (RC2-40 certificate bag, 3DES key
    bag, SHA-1 MAC), made by OpenSSL, which ``cryptography`` can't write.

    ``oscrypto_kdf_bug.p12`` is one that oscrypto 1.3.0 can't read: its pure-Python
    PKCS#12 key derivation gets the 3DES key wrong for about one salt in 465.
    """
    import shutil

    from cryptography.hazmat.primitives import serialization
    from oscrypto import keys as oscrypto_keys

    from tests.fixtures.builders.pkcs12 import LEGACY_PASSWORD, certificate, private_key

    openssl = shutil.which("openssl")
    if openssl is None:
        raise SystemExit("pkcs12 needs the openssl command (Git Bash has one)")
    key = private_key(PKCS12_LEGACY_SEED).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert = certificate(PKCS12_LEGACY_SEED, "book-loader legacy test")
    out: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        (tmp / "key.pem").write_bytes(key)
        (tmp / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        def export() -> bytes:
            # -legacy: RC2-40 for the certificate, 3DES for the key, as OpenSSL 1.x did.
            subprocess.run(
                [openssl, "pkcs12", "-export", "-legacy", "-in", "cert.pem"]
                + ["-inkey", "key.pem", "-name", "book-loader legacy test", "-out", "out.p12"]
                + ["-passout", "pass:" + LEGACY_PASSWORD.decode("ascii")],
                cwd=tmp,
                check=True,
            )
            return (tmp / "out.p12").read_bytes()

        out["pkcs12/legacy_rc2_40.p12"] = export()
        for _ in range(20_000):
            data = export()
            try:
                oscrypto_keys.parse_pkcs12(data, LEGACY_PASSWORD)
            except (ValueError, OSError):  # on Windows, CNG rejects the padding: OSError
                out["pkcs12/oscrypto_kdf_bug.p12"] = data
                break
        else:
            raise SystemExit("no file oscrypto can't read after 20000 tries")
    return out


# --------------------------------------------------------------------------- common

GENERATORS: dict[str, tuple[Callable[[], dict[str, bytes]], bool]] = {
    # name: (generator, compare) -- compare=False means create only when missing
    "redact": (gen_redact, True),
    "safe_filename": (gen_safe_filename, True),
    "kobo_keys": (gen_kobo_keys, True),
    "os_output": (gen_os_output, False),
    "v0": (gen_v0, False),
    "pkcs12": (gen_pkcs12, False),
}


def _json(data) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("targets", nargs="*", help=f"any of: {', '.join(GENERATORS)}")
    parser.add_argument("--force", action="store_true", help="overwrite changed golden files")
    args = parser.parse_args(argv)
    unknown = set(args.targets) - set(GENERATORS)
    if unknown:
        parser.error(f"unknown targets: {', '.join(sorted(unknown))}")

    failed = False
    for name in args.targets or GENERATORS:
        generate, compare = GENERATORS[name]
        for rel, data in generate().items():
            path = FIXTURES / rel
            if path.exists():
                if not compare or path.read_bytes() == data:
                    print(f"kept      {rel}")
                    continue
                if not args.force:
                    print(f"CHANGED   {rel} (not written; use --force for a decided change)")
                    failed = True
                    continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            print(f"written   {rel}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
