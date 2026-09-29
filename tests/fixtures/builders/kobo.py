"""Synthetic Kobo Desktop libraries: ``Kobo.sqlite`` plus encrypted KEPUBs (T0.4.3).

Kobo's scheme, implemented here independently of the code under test:
- ``deviceid = SHA256(hash_key + MAC)`` as hex
- ``userkey = SHA256(deviceid + UserID)``, last 16 bytes
- each encrypted entry has a 16-byte page key; the database stores the page key
  encrypted with the user key (AES-ECB, base64), and the entry is encrypted with the
  page key (AES-ECB, PKCS#7 padding)
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from Crypto.Cipher import AES

from tests.fixtures.builders.epub import FAKE_JPEG, chapter

KOBO_HASH_KEYS = ["88b3a2e13", "XzUhGYdFp", "NoCanLook", "QJhwzAtXL"]

FAKE_MAC = "AA:BB:CC:DD:EE:01"
OTHER_MAC = "AA:BB:CC:DD:EE:02"
USER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

SCHEMA = """
CREATE TABLE content (ContentID TEXT, Title TEXT, Attribution TEXT);
CREATE TABLE content_keys (volumeid TEXT, elementid TEXT, elementkey TEXT);
CREATE TABLE user (UserID TEXT);
"""


def derive_userkey(mac: str, user_id: str, hash_key: str = KOBO_HASH_KEYS[0]) -> bytes:
    deviceid = hashlib.sha256((hash_key + mac).encode("ascii")).hexdigest()
    return hashlib.sha256((deviceid + user_id).encode("ascii")).digest()[16:]


def default_kepub_files(bom: bool = False) -> dict[str, bytes]:
    return {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": b"<container/>",
        "OEBPS/content.opf": b"<package/>",
        "OEBPS/chapter1.xhtml": chapter(1, bom=bom),
        "OEBPS/chapter2.xhtml": chapter(2),
        "OEBPS/images/cover.jpg": FAKE_JPEG,
    }


def _is_encrypted(name: str) -> bool:
    return name.lower().endswith((".xhtml", ".html", ".jpg", ".jpeg"))


@dataclass
class KoboBookSpec:
    volumeid: str
    title: str
    author: str | None = None
    drm: bool = True
    files: dict[str, bytes] = field(default_factory=default_kepub_files)

    def page_key(self, name: str) -> bytes:
        return hashlib.sha256(f"page:{self.volumeid}:{name}".encode()).digest()[:16]


def _pkcs7(data: bytes) -> bytes:
    pad = 16 - len(data) % 16
    return data + bytes([pad]) * pad


def write_kepub(kobodir: Path, book: KoboBookSpec) -> Path:
    path = kobodir / "kepub" / book.volumeid
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in book.files.items():
            if book.drm and _is_encrypted(name):
                data = AES.new(book.page_key(name), AES.MODE_ECB).encrypt(_pkcs7(data))
            zf.writestr(name, data)
    return path


def insert_book(conn: sqlite3.Connection, book: KoboBookSpec, userkey: bytes) -> None:
    conn.execute("INSERT INTO content VALUES (?, ?, ?)", (book.volumeid, book.title, book.author))
    if not book.drm:
        return
    for name in book.files:
        if _is_encrypted(name):
            wrapped = AES.new(userkey, AES.MODE_ECB).encrypt(book.page_key(name))
            conn.execute(
                "INSERT INTO content_keys VALUES (?, ?, ?)",
                (book.volumeid, name, base64.b64encode(wrapped).decode("ascii")),
            )


def build_kobo_library(
    kobodir: Path,
    books: list[KoboBookSpec],
    mac: str = FAKE_MAC,
    user_ids: tuple[str, ...] = (USER_ID,),
    hash_key: str = KOBO_HASH_KEYS[0],
    wal: bool = False,
) -> bytes:
    """Write ``Kobo.sqlite`` and ``kepub/`` into ``kobodir``; returns the user key used.

    Books are encrypted for the first user ID. With ``wal=True`` the database is in WAL
    mode; everything is checkpointed when the builder's connection closes.
    """
    kobodir.mkdir(parents=True, exist_ok=True)
    userkey = derive_userkey(mac, user_ids[0], hash_key)
    conn = sqlite3.connect(kobodir / "Kobo.sqlite")
    try:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO user VALUES (?)", [(u,) for u in user_ids])
        for book in books:
            insert_book(conn, book, userkey)
            write_kepub(kobodir, book)
        conn.commit()
    finally:
        conn.close()
    return userkey


def add_book_in_wal(kobodir: Path, book: KoboBookSpec, userkey: bytes) -> sqlite3.Connection:
    """Add ``book`` so that it exists only in the WAL file, as while Kobo Desktop runs.

    Returns the open connection; closing it checkpoints the change into the database.
    """
    conn = sqlite3.connect(kobodir / "Kobo.sqlite")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    insert_book(conn, book, userkey)
    write_kepub(kobodir, book)
    conn.commit()
    return conn
