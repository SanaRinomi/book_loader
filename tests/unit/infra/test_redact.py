"""T0.4.1 / T2.12: ``infra/redact.py``, pinned by ``golden/redact.json``.

The golden file records 0.1.0's redaction output and stays the contract. The module
moved from ``utils/redact.py`` in T2.12; the old path re-exports it until Phase 6.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_loader.adobe._vendor import libadobe, libadobeFulfill
from book_loader.infra import redact
from book_loader.infra.redact import redact_header, redact_text, redact_url

GOLDEN = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "golden" / "redact.json").read_text("utf-8")
)


@pytest.mark.parametrize("case", GOLDEN["urls"], ids=lambda c: c["input"][:40] or "empty")
def test_redact_url(case):
    assert redact_url(case["input"]) == case["output"]


@pytest.mark.parametrize("case", GOLDEN["headers"], ids=lambda c: c["name"])
def test_redact_header(case):
    assert redact_header(case["name"], case["value"]) == case["output"]


@pytest.mark.parametrize("case", GOLDEN["texts"], ids=lambda c: c["input"][:40] or "empty")
def test_redact_text(case):
    assert redact_text(case["input"]) == case["output"]


def test_golden_file_covers_the_plan():
    """The table has URLs, header pairs and text with UUIDs, emails, IPs and tokens."""
    outputs = "\n".join(c["output"] for c in GOLDEN["texts"])
    for marker in ("<uuid>", "<email>", "<ip>", "<hidden:"):
        assert marker in outputs
    assert len(GOLDEN["urls"]) >= 10 and len(GOLDEN["headers"]) >= 10


def test_the_old_path_re_exports_the_same_functions():
    from book_loader.utils import redact as old

    assert (old.redact_url, old.redact_text, old.redact_header) == (
        redact_url,
        redact_text,
        redact_header,
    )


@pytest.mark.parametrize("module", [libadobe, libadobeFulfill], ids=lambda m: m.__name__)
def test_vendored_code_uses_the_moved_module(module):
    for name in ("redact_url", "redact_text", "redact_header"):
        assert getattr(module, name) is getattr(redact, name)
