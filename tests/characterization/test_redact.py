"""T0.4.1: redaction output of 0.1.0, pinned by ``golden/redact.json``.

T2.12 moves these tests to ``infra.redact``; the golden file stays the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_loader.utils.redact import redact_header, redact_text, redact_url

GOLDEN = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "golden" / "redact.json").read_text("utf-8")
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
