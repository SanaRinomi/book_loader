"""File and folder naming rules (REFACTOR_PLAN §5.11).

Plain mode keeps 0.1.0's names exactly, so conflict checks and ``--skip-existing``
still recognise earlier output:

- ``kobo_plain_name`` is 0.1.0's ``safe_filename``, pinned by ``golden/safe_filename.json``.
  (Adobe names come from the vendored ``parse_fulfillment`` and aren't built here.)
- ``assign_kobo_names`` tells apart books whose plain names collide: first by adding
  the author, then an 8-character volume ID. The result depends only on the set of
  books, never on their order, so every run gives the same names.

Library mode builds names that are valid on Windows, macOS and Linux:

- ``library_component`` makes one folder or file name portable: NFC, no characters
  Windows rejects, no leading or trailing dots and spaces, no reserved device names,
  at most 100 characters and 255 UTF-8 bytes, cut only between whole characters.
- ``render_template`` turns ``[output] naming`` (``"{author}/{title}"``) and a book's
  metadata into a relative path, and keeps the full path within Windows' 260-character
  limit when long-path support is off.

The same rules apply when names are restored from a backup (§10.7).
"""

from __future__ import annotations

import hashlib
import re
import string
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TypeVar

from ..domain.errors import ConfigError
from ..domain.models import BookMetadata

__all__ = [
    "MAX_NAME_BYTES",
    "MAX_NAME_CHARS",
    "WINDOWS_MAX_PATH",
    "KoboNameRequest",
    "assign_kobo_names",
    "disambiguate",
    "kobo_plain_name",
    "library_component",
    "render_template",
    "short_volume_id",
    "windows_long_paths_enabled",
]

K = TypeVar("K", bound=Hashable)

MAX_NAME_CHARS = 100
MAX_NAME_BYTES = 255  # the per-name limit of ext4, APFS and NTFS (NTFS counts UTF-16 units)
WINDOWS_MAX_PATH = 259  # MAX_PATH is 260 including the terminating NUL
MIN_SHORTENED = 16  # a name is never shortened below this many characters

TEMPLATE_FIELDS = frozenset({"author", "title", "year", "series"})
UNKNOWN_AUTHOR = "Unknown Author"
UNTITLED = "Untitled"

_WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])", re.IGNORECASE)
_SPACES = re.compile(r"\s{2,}")


# --- Plain mode ------------------------------------------------------------------------


def kobo_plain_name(title: str) -> str:
    """0.1.0's Kobo file name for ``title``, byte for byte.

    Everything but letters, digits, ``_`` and whitespace becomes ``_``. The result
    isn't always portable (it can keep tabs, or be ``CON.epub``); that is 0.1.0's
    behaviour, kept so earlier output is recognised.
    """
    return re.sub(r"[^\s\w]", "_", title, flags=re.UNICODE).strip() + ".epub"


def short_volume_id(volume_id: str) -> str:
    """8 characters that identify a Kobo volume ID.

    Kobo volume IDs are UUIDs, so their first 8 hex digits are used. Anything else is
    hashed, so two IDs that share a prefix still differ.
    """
    compact = volume_id.replace("-", "").lower()
    if re.fullmatch(r"[0-9a-f]{32}", compact):
        return compact[:8]
    return hashlib.sha256(volume_id.encode("utf-8")).hexdigest()[:8]


def disambiguate(
    name: str, author: str | None, volume_id: str | None = None, *, full_id: bool = False
) -> str:
    """``name`` with the author added, and the volume ID when it is given.

    ``Title.epub`` becomes ``Title - Author.epub``, then ``Title - Author [0a1b2c3d].epub``.
    The added parts follow the plain-mode character rule. ``full_id`` uses the whole
    volume ID, for the (practically impossible) case of two IDs with the same 8
    characters.
    """
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    parts = [stem]
    if author and author.strip():
        parts.append(_plain(author))
    result = " - ".join(parts)
    if volume_id is not None:
        tag = _plain(volume_id) if full_id else short_volume_id(volume_id)
        result += f" [{tag}]"
    return f"{result}.{suffix}" if dot else result


def _plain(text: str) -> str:
    return re.sub(r"[^\s\w]", "_", text).strip()


@dataclass(frozen=True)
class KoboNameRequest:
    title: str
    author: str | None
    volume_id: str


def assign_kobo_names(books: Mapping[K, KoboNameRequest]) -> dict[K, str]:
    """A distinct plain-mode file name for each book.

    A book whose plain name no other book shares keeps it. Books that share one
    (compared without case, as Windows and macOS do) get the author added; books that
    still share one also get the short volume ID, and the full ID if even that
    collides. Only the set of books decides the names, never their order.
    """
    names = {key: kobo_plain_name(book.title) for key, book in books.items()}
    for step in ("author", "short_id", "full_id"):
        for group in _collisions(names):
            for key in group:
                book = books[key]
                plain = kobo_plain_name(book.title)
                if step == "author":
                    names[key] = disambiguate(plain, book.author)
                else:
                    names[key] = disambiguate(
                        plain, book.author, book.volume_id, full_id=step == "full_id"
                    )
    return names


def _collisions(names: Mapping[K, str]) -> list[list[K]]:
    groups: dict[str, list[K]] = defaultdict(list)
    for key, name in names.items():
        groups[name.casefold()].append(key)
    return [group for group in groups.values() if len(group) > 1]


# --- Library mode ----------------------------------------------------------------------


def _is_extender(char: str) -> bool:
    """A character that belongs to the one before it (a rough grapheme-cluster rule)."""
    code = ord(char)
    return (
        unicodedata.combining(char) != 0
        or unicodedata.category(char) in ("Mn", "Me")
        or code == 0x200D  # zero-width joiner
        or 0xFE00 <= code <= 0xFE0F  # variation selectors
        or 0xE0100 <= code <= 0xE01EF
        or 0x1F3FB <= code <= 0x1F3FF  # skin tone modifiers
        or 0xE0020 <= code <= 0xE007F  # tag characters (flag sequences)
    )


def _is_regional_indicator(char: str) -> bool:
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


def _safe_cut(text: str, cut: int) -> int:
    """The largest position <= ``cut`` that doesn't split a character."""
    while 0 < cut < len(text) and (_is_extender(text[cut]) or text[cut - 1] == "\u200d"):
        cut -= 1
    # Flags are pairs of regional indicators; don't keep half of one.
    run = 0
    while run < cut and _is_regional_indicator(text[cut - 1 - run]):
        run += 1
    if run % 2 and cut < len(text) and _is_regional_indicator(text[cut]):
        cut -= 1
    return cut


def _truncate(text: str, max_chars: int, max_bytes: int) -> str:
    cut = min(len(text), max_chars)
    while len(text[:cut].encode("utf-8")) > max_bytes:
        cut -= 1
    return text[: _safe_cut(text, cut)]


def _strip_edges(text: str) -> str:
    return text.strip(" .\t\n\r\f\v\u3000")


def library_component(
    text: str, suffix: str = "", *, max_chars: int = MAX_NAME_CHARS, fallback: str = "_"
) -> str:
    """``text`` as one folder or file name that is valid on Windows, macOS and Linux.

    - converted to NFC
    - characters Windows rejects (``<>:"/\\|?*`` and control characters) and characters
      that can't be encoded become ``_``
    - leading and trailing dots and spaces are removed (a leading dot would hide the
      file on macOS and Linux)
    - a reserved device name (``CON``, ``NUL``, ``COM1``, …, with any extension) gets
      ``_`` added to it
    - at most ``max_chars`` characters and 255 UTF-8 bytes including ``suffix``, cut
      only between whole characters
    - an empty result becomes ``fallback``

    ``suffix`` (``".epub"``) is appended unchanged and never cut. The function is
    idempotent: applying it to its own output changes nothing.
    """
    name = unicodedata.normalize("NFC", text)
    name = _WINDOWS_INVALID.sub("_", name)
    name = "".join(c if not 0xD800 <= ord(c) <= 0xDFFF else "_" for c in name)
    room_chars = max_chars - len(suffix)
    room_bytes = MAX_NAME_BYTES - len(suffix.encode("utf-8"))
    name = _strip_edges(_truncate(_strip_edges(name), room_chars, room_bytes))
    if not name:
        name = fallback
    base, dot, rest = (name + suffix).partition(".")
    if _RESERVED.fullmatch(base.rstrip(" ")):
        name = _strip_edges(
            _truncate(base.rstrip(" ") + "_" + name[len(base) :], room_chars, room_bytes)
        )
    return name + suffix


def _field_values(metadata: BookMetadata) -> dict[str, str]:
    return {
        "author": metadata.authors[0] if metadata.authors else UNKNOWN_AUTHOR,
        "title": metadata.title or UNTITLED,
        "year": str(metadata.year) if metadata.year is not None else "",
        "series": metadata.series or "",
    }


def _check_template(template: str) -> None:
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as error:
        raise ConfigError(f"[output] naming {template!r} is not a valid template: {error}")
    for _, field, spec, conversion in parsed:
        if field is None:
            continue
        if field not in TEMPLATE_FIELDS or spec or conversion:
            known = ", ".join(f"{{{name}}}" for name in sorted(TEMPLATE_FIELDS))
            raise ConfigError(
                f"[output] naming {template!r} uses {{{field}}}, which isn't known",
                hint=f"Use {known}.",
            )


def render_template(
    template: str,
    metadata: BookMetadata,
    *,
    suffix: str = "",
    base: Path | None = None,
    max_path: int | None = None,
) -> PurePath:
    """The relative path for a book: ``"{author}/{title}"`` → ``Author/Title.epub``.

    ``{author}`` is the first author (``Unknown Author`` without one), ``{title}`` the
    title, ``{year}`` and ``{series}`` are empty when unknown. The template is split on
    ``/`` and ``\\`` before the values go in, so a ``/`` in a title never makes a
    folder. Brackets and separators around a missing value are removed, each part goes
    through ``library_component``, and a part that ends up empty is dropped. ``suffix``
    is added to the last part.

    ``max_path`` is the longest full path allowed (``base`` joined with the result),
    for Windows without long-path support (``WINDOWS_MAX_PATH``). Longer paths are
    shortened, longest part first but never below 16 characters; when that isn't
    enough, a ``ConfigError`` says so.
    """
    _check_template(template)
    values = _field_values(metadata)
    empty = [field for field, value in values.items() if not value]
    rendered: list[str] = []
    for part in re.split(r"[/\\]", template):
        for field in empty:
            # "{title} ({year})" without a year becomes "{title}", not "Title ()".
            part = re.sub(rf"\(\s*\{{{field}\}}\s*\)|\[\s*\{{{field}\}}\s*\]", "", part)
        text = _SPACES.sub(" ", part.format(**values))
        if any("{" + field + "}" in part for field in empty):
            text = text.strip(" -\u2013\u2014,;:")  # "{series} - {title}" without a series
        if _strip_edges(text):
            rendered.append(text)
    if not rendered:
        rendered = [UNTITLED]

    parts = [library_component(text) for text in rendered[:-1]]
    parts.append(library_component(rendered[-1], suffix, fallback=UNTITLED))
    if max_path is not None:
        parts = _fit(parts, rendered, suffix, base, max_path)
    return PurePath(*parts)


def _fit(
    parts: list[str], rendered: list[str], suffix: str, base: Path | None, max_path: int
) -> list[str]:
    def length() -> int:
        return len(str(PurePath(base or "", *parts)))

    limits = [len(p) - (len(suffix) if i == len(parts) - 1 else 0) for i, p in enumerate(parts)]
    while length() > max_path:
        longest = max(range(len(parts)), key=lambda i: limits[i])
        if limits[longest] <= MIN_SHORTENED:
            raise ConfigError(
                f"The path for {rendered[-1]!r} would be {length()} characters long, over "
                f"Windows' limit of {max_path + 1}",
                hint="Turn on long path support in Windows, or use a library folder with "
                "a shorter path, or a shorter [output] naming template.",
            )
        limits[longest] = max(MIN_SHORTENED, limits[longest] - (length() - max_path))
        last = longest == len(parts) - 1
        parts[longest] = library_component(
            rendered[longest],
            suffix if last else "",
            max_chars=limits[longest] + (len(suffix) if last else 0),
            fallback=UNTITLED if last else "_",
        )
    return parts


def windows_long_paths_enabled() -> bool:
    """Whether Windows allows paths over 260 characters (``LongPathsEnabled``).

    Python itself is built long-path aware, so the registry setting decides. Always
    True off Windows, where the limit doesn't exist.
    """
    if sys.platform != "win32":
        return True
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return False
    return value == 1
