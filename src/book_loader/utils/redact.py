"""Redaction moved to ``book_loader.infra.redact`` (T2.12).

This re-export keeps the old code working until Phase 6 deletes ``utils/``.
"""

from ..infra.redact import redact_header, redact_text, redact_url

__all__ = ["redact_header", "redact_text", "redact_url"]
