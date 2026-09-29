"""T0.4.2: plain-mode Kobo file names of 0.1.0, pinned by ``golden/safe_filename.json``.

The golden file is the contract for plain-mode names (REFACTOR_PLAN §5.11); T2.6's
``names.kobo_plain_name`` must reproduce it exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_loader.core.kobo.decryptor import safe_filename

GOLDEN = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "golden" / "safe_filename.json").read_text("utf-8")
)


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: repr(c["title"][:30]))
def test_safe_filename(case):
    assert safe_filename(case["title"]) == case["filename"]


def test_golden_file_covers_the_plan():
    titles = [c["title"] for c in GOLDEN]
    assert len(titles) >= 40
    assert "" in titles
    assert any(len(t) > 255 for t in titles)
    assert any("/" in t for t in titles) and any(":" in t for t in titles)
    assert any("一" <= ch <= "鿿" for t in titles for ch in t)  # CJK
    assert any(ord(ch) > 0xFFFF for t in titles for ch in t)  # emoji
