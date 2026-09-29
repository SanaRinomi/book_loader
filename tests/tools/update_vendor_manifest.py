"""Regenerate the vendor manifests (T1.4).

Each ``_vendor/`` folder has a ``MANIFEST.sha256`` with one SHA-256 per vendored file.
``tests/unit/test_vendor_manifest.py`` fails when a file no longer matches, so a vendored
file can only change on purpose: change it, record the change in that folder's
``PATCHES.md``, and run this script, all in the same commit.

    uv run python tests/tools/update_vendor_manifest.py           # rewrite the manifests
    uv run python tests/tools/update_vendor_manifest.py --check   # report only; exit 1 if stale

Files are hashed with CRLF replaced by LF. ``core.autocrlf`` gives Windows checkouts CRLF
and macOS and Linux checkouts LF, and both must give the same hashes. The manifest uses
the ``sha256sum`` format (sorted, ``/`` separators, LF line endings), so on an LF checkout
``sha256sum -c MANIFEST.sha256`` works too.

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "book_loader"
VENDOR_DIRS = (PACKAGE / "adobe" / "_vendor", PACKAGE / "drm" / "_vendor")
MANIFEST = "MANIFEST.sha256"
# Not vendored code: the manifest itself, and the notes that describe the changes.
NOT_HASHED = frozenset({MANIFEST, "PATCHES.md"})


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def vendored_files(folder: Path) -> list[str]:
    """Paths of the hashed files in ``folder``, relative, with ``/`` separators, sorted."""
    names = []
    for path in folder.rglob("*"):
        relative = path.relative_to(folder)
        if not path.is_file() or "__pycache__" in relative.parts:
            continue
        if relative.as_posix() in NOT_HASHED:
            continue
        names.append(relative.as_posix())
    return sorted(names)


def compute(folder: Path) -> dict[str, str]:
    return {name: file_hash(folder / name) for name in vendored_files(folder)}


def render(hashes: dict[str, str]) -> str:
    return "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items()))


def parse(text: str) -> dict[str, str]:
    hashes = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name:
            raise ValueError(f"{MANIFEST} line {number} is not '<sha256>  <path>': {line!r}")
        hashes[name] = digest
    return hashes


def read(folder: Path) -> dict[str, str]:
    """The manifest in ``folder``; empty when there is none yet."""
    path = folder / MANIFEST
    return parse(path.read_text(encoding="utf-8")) if path.exists() else {}


def differences(recorded: dict[str, str], actual: dict[str, str]) -> list[str]:
    """How the files differ from the manifest, one line per file."""
    problems = []
    for name in sorted(recorded.keys() | actual.keys()):
        if name not in actual:
            problems.append(f"missing: {name}")
        elif name not in recorded:
            problems.append(f"not in the manifest: {name}")
        elif recorded[name] != actual[name]:
            problems.append(f"changed: {name}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="only report differences; exit 1 if any"
    )
    parser.add_argument(
        "folders", nargs="*", type=Path, help="_vendor folders (default: both in book_loader)"
    )
    args = parser.parse_args(argv)

    stale = False
    for folder in args.folders or VENDOR_DIRS:
        actual = compute(folder)
        problems = differences(read(folder), actual)
        label = (folder.relative_to(ROOT) if folder.is_relative_to(ROOT) else folder).as_posix()
        if not problems:
            print(f"{label}: {len(actual)} files, manifest up to date")
            continue
        stale = True
        print(f"{label}:")
        for problem in problems:
            print(f"  {problem}")
        if not args.check:
            (folder / MANIFEST).write_bytes(render(actual).encode("utf-8"))
            print(f"  wrote {MANIFEST}; record these changes in PATCHES.md")
    return 1 if stale and args.check else 0


if __name__ == "__main__":
    sys.exit(main())
