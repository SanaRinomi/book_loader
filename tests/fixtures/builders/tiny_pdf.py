"""A minimal PDF with an ADEPT (``EBX_HANDLER``) encryption dictionary (T0.4.7).

``libpdf`` reads the file backwards line by line. It needs the trailer dictionary on one
line containing ``R/Encrypt`` and ``R/ID``, and the handler dictionary on one line
containing ``/EBX_HANDLER/`` and ending with ``>>``. This file keeps to that layout.
"""

from __future__ import annotations

from pathlib import Path

ENCRYPT_OBJECT = 4
FILE_ID = "0123456789abcdef0123456789abcdef"


def build_ebx_pdf(path: Path) -> bytes:
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>",
        b"<</Filter/EBX_HANDLER/V 4/R 4/Length 128>>",
    ]
    out = bytearray(b"%PDF-1.6\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"

    xref_offset = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n"
    out += b"<</Size %d/Root 1 0 R/Encrypt %d 0 R/ID[<%s><%s>]>>\n" % (
        len(objects) + 1,
        ENCRYPT_OBJECT,
        FILE_ID.encode(),
        FILE_ID.encode(),
    )
    out += b"startxref\n%d\n%%%%EOF\n" % xref_offset

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return bytes(out)
