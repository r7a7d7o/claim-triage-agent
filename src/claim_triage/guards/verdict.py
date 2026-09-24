"""What every guard answers with, in one vocabulary shared by every layer.

A verdict is deliberately a value rather than an exception: a guard that refuses is answering, not
failing, so the caller decides what a refusal means at its own boundary — the ingress turns one into
a 413, a model-output guard turns one into a retry — and the run carries the answers into the audit
log either way.

`Check` names the layer each check belongs to by its own name rather than by a prefix, because the
audit log records a flat list and the check is what an auditor searches for. `DocumentVerdicts`
groups the answers about one document with the document they are about, which is what makes a
recorded run readable months later.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Check(StrEnum):
    """The checks this repository runs. The layer is named by the ticket that adds the check."""

    # Ingress: the rate limit in front of the boundary, and the document checks behind it.
    RATE_LIMIT = "rate_limit"
    SIZE = "size"
    MEDIA_TYPE = "media_type"
    STRUCTURE = "structure"
    ENCRYPTION = "encryption"
    ARCHIVE = "archive"
    PAGE_COUNT = "page_count"


class Verdict(BaseModel):
    """One check's answer: what it checked, whether it let it through, and what it found.

    `detail` is written for the auditor rather than for a parser — it says what was found and
    against what limit — because the parsable part of a refusal is `check` and `passed`.
    """

    model_config = ConfigDict(extra="forbid")

    check: Check
    passed: bool
    detail: str


class DocumentVerdicts(BaseModel):
    """Every check's answer about one uploaded document, and which document it was.

    `filename` and `media_type` are recorded as the caller declared them, not as the bytes turned
    out: the audit log has to show what arrived, and the checks say what it really was.
    """

    model_config = ConfigDict(extra="forbid")

    filename: str
    media_type: str
    verdicts: list[Verdict]
