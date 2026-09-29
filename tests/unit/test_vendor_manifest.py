"""Vendor guard (T1.4): vendored files match ``_vendor/MANIFEST.sha256``.

A vendored file may change only in a task that says so, together with its ``PATCHES.md``
entry and a regenerated manifest (``tests/tools/update_vendor_manifest.py``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.tools.update_vendor_manifest import (
    MANIFEST,
    VENDOR_DIRS,
    compute,
    differences,
    main,
    parse,
    read,
    render,
)


@pytest.mark.parametrize("folder", VENDOR_DIRS, ids=lambda f: f.parent.name)
def test_vendored_files_match_the_manifest(folder: Path):
    problems = differences(read(folder), compute(folder))
    assert problems == [], (
        f"Vendored code in {folder.parent.name}/_vendor differs from {MANIFEST}:\n  "
        + "\n  ".join(problems)
        + "\nIf this is intended, record it in PATCHES.md and run "
        "`uv run python tests/tools/update_vendor_manifest.py` in the same commit."
    )


@pytest.mark.parametrize("folder", VENDOR_DIRS, ids=lambda f: f.parent.name)
def test_manifest_is_in_canonical_form(folder: Path):
    """Sorted, ``/`` separators and LF line endings, so it is the same on every OS."""
    data = (folder / MANIFEST).read_bytes()
    assert b"\r" not in data
    assert data.decode("utf-8") == render(parse(data.decode("utf-8")))


def test_manifest_lists_the_vendored_code():
    listed = {folder.parent.name: set(read(folder)) for folder in VENDOR_DIRS}
    assert {"libadobe.py", "libadobeFulfill.py", "__init__.py"} <= listed["adobe"]
    assert {"ineptepub.py", "ineptpdf.py", "__init__.py"} <= listed["drm"]
    assert not any(MANIFEST in names or "PATCHES.md" in names for names in listed.values())


@pytest.fixture
def vendor(tmp_path: Path) -> Path:
    """A copy of ``adobe/_vendor`` with an up-to-date manifest."""
    folder = tmp_path / "_vendor"
    shutil.copytree(VENDOR_DIRS[0], folder, ignore=shutil.ignore_patterns("__pycache__"))
    assert main([str(folder)]) == 0
    assert differences(read(folder), compute(folder)) == []
    return folder


def test_changing_one_byte_fails(vendor: Path):
    path = vendor / "customRSA.py"
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    assert differences(read(vendor), compute(vendor)) == ["changed: customRSA.py"]


def test_added_and_removed_files_fail(vendor: Path):
    (vendor / "libpdf.py").unlink()
    (vendor / "extra.py").write_text("x = 1\n", encoding="utf-8")
    assert differences(read(vendor), compute(vendor)) == [
        "not in the manifest: extra.py",
        "missing: libpdf.py",
    ]


@pytest.mark.parametrize("ending", [b"\r\n", b"\n"], ids=["crlf", "lf"])
def test_line_endings_do_not_change_hashes(vendor: Path, ending: bytes):
    for path in vendor.glob("*.py"):
        lines = path.read_bytes().replace(b"\r\n", b"\n").split(b"\n")
        path.write_bytes(ending.join(lines))
    assert differences(read(vendor), compute(vendor)) == []


def test_notes_and_caches_are_not_hashed(vendor: Path):
    (vendor / "PATCHES.md").write_text("edited notes\n", encoding="utf-8")
    (vendor / "__pycache__").mkdir(exist_ok=True)
    (vendor / "__pycache__" / "libadobe.cpython-314.pyc").write_bytes(b"\0")
    assert differences(read(vendor), compute(vendor)) == []


def test_check_mode_reports_without_writing(vendor: Path, capsys):
    (vendor / "customRSA.py").write_text("changed\n", encoding="utf-8")
    before = (vendor / MANIFEST).read_bytes()
    assert main(["--check", str(vendor)]) == 1
    assert (vendor / MANIFEST).read_bytes() == before
    assert "changed: customRSA.py" in capsys.readouterr().out
    assert main([str(vendor)]) == 0
    assert main(["--check", str(vendor)]) == 0


def test_parse_rejects_malformed_lines():
    with pytest.raises(ValueError, match="line 1"):
        parse("not a manifest line\n")
