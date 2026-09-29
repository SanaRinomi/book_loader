"""Plain (DRM-free) EPUBs with chosen content."""

from __future__ import annotations

import zipfile
from pathlib import Path

CONTAINER_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

CONTENT_OPF = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">urn:uuid:00000000-0000-4000-8000-000000000001</dc:identifier>
    <dc:title>Test Book</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="img" href="cover.jpg" media-type="image/jpeg"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>
"""


def chapter(number: int, bom: bool = False) -> bytes:
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter %d</title>'
        '<link rel="stylesheet" href="style.css"/></head>'
        "<body><h1>Chapter %d</h1><p>%s</p></body></html>\n"
        % (number, number, "Some text for this chapter. " * 40)
    ).encode("utf-8")
    return b"\xef\xbb\xbf" + text if bom else text


# A JPEG start-of-image marker followed by filler; enough for signature checks.
FAKE_JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4 + b"\xff\xd9"


def default_files() -> dict[str, bytes]:
    """Book entries in the order they are written, without ``mimetype``."""
    return {
        "META-INF/container.xml": CONTAINER_XML,
        "OEBPS/content.opf": CONTENT_OPF,
        "OEBPS/chapter1.xhtml": chapter(1),
        "OEBPS/chapter2.xhtml": chapter(2),
        "OEBPS/style.css": b"body { font-family: serif; }\n",
        "OEBPS/cover.jpg": FAKE_JPEG,
    }


def write_epub(path: Path, files: dict[str, bytes]) -> Path:
    """Write ``mimetype`` first and stored, then ``files`` deflated, in order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in files.items():
            zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return path


def build_epub(path: Path, files: dict[str, bytes] | None = None) -> dict[str, bytes]:
    """Write a plain EPUB and return every entry's content, including ``mimetype``."""
    files = default_files() if files is None else files
    write_epub(path, files)
    return {"mimetype": b"application/epub+zip", **files}


def read_entries(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}
