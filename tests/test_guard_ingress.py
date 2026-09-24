"""The guard seam as a unit: typed checks over an upload, asserted without a socket or a database.

The ingress layer's four acceptance criteria are answered here as data. A document inside every
limit passes every check the ingress runs for its type; a document outside one is refused by that
check and no other, with the reason it refused; and the rate limiter's burst is a value a test
drives with a clock it owns, so nothing sleeps and nothing is observed through a boundary.

The documents are built by `support`, which is where the modules that serve them read them from
too. What a refusal looks like on the wire is `tests/test_guarded_intake.py`'s subject, not this
module's.
"""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO

import pytest

from claim_triage.guards.ingress import (
    IngressLimits,
    Upload,
    declared_size,
    first_refusal,
    read_bounded,
    screen,
)
from claim_triage.guards.rate_limit import Admission, Bucket, Limiters, RateLimit
from claim_triage.guards.verdict import Check, DocumentVerdicts, Verdict
from support import (
    PDF,
    ZIP,
    encrypted_pdf,
    pdf,
    pdf_with_a_lost_page_object,
    pdf_with_no_pages,
    upload,
    zip_bomb,
    zip_marked_encrypted,
    zip_of,
)

LIMITS: IngressLimits = IngressLimits(
    media_types=frozenset({PDF, ZIP}),
    max_submission_bytes=32_768,
    max_bytes=16_384,
    max_pages=3,
    max_expansion_ratio=100.0,
)
"""Deliberately small, so a test's document is small too and the ceiling is still the ceiling."""


def screened(*uploads: Upload, limits: IngressLimits = LIMITS) -> list[DocumentVerdicts]:
    return screen(list(uploads), limits)


def refusal(verdicts: list[DocumentVerdicts]) -> tuple[str, Verdict]:
    """The refusal these verdicts hold, or a failure naming what passed instead."""
    refused = first_refusal(verdicts)
    assert refused is not None, [document.verdicts for document in verdicts]
    return refused


def test_a_document_inside_every_limit_passes_every_check() -> None:
    document, *rest = screened(upload(pdf(2)))

    assert rest == []
    assert document.filename == "oznamenie-skody.pdf"
    assert document.media_type == PDF
    assert [verdict.check for verdict in document.verdicts] == [
        Check.SIZE,
        Check.MEDIA_TYPE,
        Check.STRUCTURE,
        Check.ENCRYPTION,
        Check.PAGE_COUNT,
    ]
    assert all(verdict.passed for verdict in document.verdicts)
    assert first_refusal([document]) is None


def test_an_archive_passes_the_checks_that_apply_to_an_archive() -> None:
    """A page count is not a check a zip can answer, so it is not one the ingress asks it."""
    document, *rest = screened(
        upload(zip_of({"oznamenie-skody.pdf": pdf(2)}), media_type=ZIP, filename="claim.zip")
    )

    assert rest == []
    assert [verdict.check for verdict in document.verdicts] == [
        Check.SIZE,
        Check.MEDIA_TYPE,
        Check.STRUCTURE,
        Check.ENCRYPTION,
        Check.ARCHIVE,
    ]
    assert all(verdict.passed for verdict in document.verdicts)


def test_a_content_type_the_allow_list_takes_away_is_refused_before_it_is_read() -> None:
    """Configuring a type away removes it without removing its checks: a zip is still a zip."""
    narrowed = replace(LIMITS, media_types=frozenset({PDF}))
    bundle = upload(zip_of({"oznamenie-skody.pdf": pdf(1)}), media_type=ZIP, filename="claim.zip")

    (document,) = screened(bundle, limits=narrowed)

    assert [verdict.check for verdict in document.verdicts] == [Check.SIZE, Check.MEDIA_TYPE]
    assert document.verdicts[-1].passed is False
    assert ZIP in document.verdicts[-1].detail


@pytest.mark.parametrize(
    ("content", "media_type", "expected"),
    [
        (b"%PDF-1.4" + b" " * 20_000, PDF, Check.SIZE),
        (pdf(1), "text/plain", Check.MEDIA_TYPE),
        (b"not a pdf at all", PDF, Check.STRUCTURE),
        (b"PK\x03\x04 truncated", ZIP, Check.STRUCTURE),
        (encrypted_pdf(), PDF, Check.ENCRYPTION),
        (pdf(4), PDF, Check.PAGE_COUNT),
        (pdf_with_a_lost_page_object(), PDF, Check.STRUCTURE),
        (pdf_with_no_pages(), PDF, Check.STRUCTURE),
        (zip_marked_encrypted({"oznamenie-skody.pdf": pdf(1)}), ZIP, Check.ENCRYPTION),
        (zip_bomb(), ZIP, Check.ARCHIVE),
    ],
    ids=[
        "oversize",
        "wrong-content-type",
        "malformed",
        "malformed-archive",
        "encrypted",
        "over-page-count",
        "unresolvable-page-tree",
        "no-pages",
        "encrypted-archive",
        "archive-bomb",
    ],
)
def test_a_document_outside_one_limit_is_refused_by_that_check(
    content: bytes, media_type: str, expected: Check
) -> None:
    document, *rest = screened(upload(content, media_type=media_type))

    assert rest == []
    filename, verdict = refusal([document])

    assert filename == "oznamenie-skody.pdf"
    assert verdict.check is expected
    assert verdict.passed is False
    assert verdict.detail
    # The check that refused is the last one asked: a refusal stops the screening of that document.
    assert document.verdicts[-1] == verdict
    assert all(earlier.passed for earlier in document.verdicts[:-1])


def test_every_document_of_one_submission_is_screened() -> None:
    """One bad document does not hide what the others are: each is answered for itself."""
    verdicts = screened(upload(pdf(1), filename="good.pdf"), upload(b"junk", filename="bad.pdf"))

    assert [document.filename for document in verdicts] == ["good.pdf", "bad.pdf"]
    assert first_refusal(verdicts) == ("bad.pdf", verdicts[1].verdicts[-1])
    assert all(verdict.passed for verdict in verdicts[0].verdicts)


def test_a_submission_that_declares_more_than_it_may_carry_is_refused_unread() -> None:
    """The one check that runs before anything is read: a declared length past the ceiling."""
    too_much = declared_size(LIMITS.max_submission_bytes + 1, LIMITS)

    assert too_much.passed is False
    assert too_much.check is Check.SIZE
    assert str(LIMITS.max_submission_bytes) in too_much.detail

    assert declared_size(LIMITS.max_submission_bytes, LIMITS).passed


def test_a_submission_that_declares_no_length_cannot_be_judged_and_says_so() -> None:
    """A chunked request has no length to refuse it on, and the verdict says that rather than
    pretending it was checked."""
    undeclared = declared_size(None, LIMITS)

    assert undeclared.passed
    assert "no length" in undeclared.detail


def test_the_read_stops_one_byte_past_the_ceiling() -> None:
    """The boundary must not buffer what it is about to refuse: one byte past is enough to know."""
    longer_than_the_ceiling = b"x" * (LIMITS.max_bytes * 3)

    assert read_bounded(BytesIO(longer_than_the_ceiling), LIMITS.max_bytes) == b"x" * (
        LIMITS.max_bytes + 1
    )

    truncated = upload(b"x" * (LIMITS.max_bytes + 1))
    _, verdict = refusal(screened(truncated))

    assert verdict.check is Check.SIZE


class Clock:
    """A clock a test owns: the limiter's refill is then a value, not a wait."""

    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_a_burst_is_taken_and_the_submission_after_it_is_refused() -> None:
    clock = Clock()
    bucket = Bucket(RateLimit(burst=3, refill_per_second=1.0), clock=clock)

    taken = [bucket.take() for _ in range(3)]
    refused = bucket.take()

    assert [admission.verdict.passed for admission in taken] == [True, True, True]
    assert refused.verdict.passed is False
    assert refused.verdict.check is Check.RATE_LIMIT
    assert refused.wait_seconds > 0


def test_a_token_returns_one_refill_interval_after_it_was_spent() -> None:
    clock = Clock()
    bucket = Bucket(RateLimit(burst=1, refill_per_second=2.0), clock=clock)

    assert bucket.take().verdict.passed
    refused = bucket.take()
    assert refused.verdict.passed is False
    assert refused.wait_seconds == pytest.approx(0.5)

    clock.advance(0.5)

    assert bucket.take().verdict.passed


def test_a_bucket_refills_only_up_to_its_burst() -> None:
    """An idle caller is not owed the tokens it did not spend: the burst is the ceiling."""
    clock = Clock()
    bucket = Bucket(RateLimit(burst=2, refill_per_second=1.0), clock=clock)

    clock.advance(60.0)

    assert [bucket.take().verdict.passed for _ in range(3)] == [True, True, False]


def test_one_callers_burst_is_not_anothers() -> None:
    limiters = Limiters(RateLimit(burst=1, refill_per_second=1.0), clock=Clock())

    assert limiters.take("10.0.0.7").verdict.passed
    assert limiters.take("10.0.0.7").verdict.passed is False

    assert limiters.take("10.0.0.8").verdict.passed


def test_the_table_of_buckets_is_bounded_and_evicts_the_caller_seen_first() -> None:
    """A hostile caller cannot grow the table without bound: the oldest bucket is dropped for it."""
    limiters = Limiters(RateLimit(burst=1, refill_per_second=1.0), keys=2, clock=Clock())

    assert limiters.take("first").verdict.passed
    assert limiters.take("first").verdict.passed is False

    assert limiters.take("second").verdict.passed
    assert limiters.take("third").verdict.passed

    # `first` was evicted rather than the table grown, so it starts on a full bucket again.
    assert limiters.take("first").verdict.passed


def test_a_verdict_is_the_shape_that_is_recorded_with_the_run() -> None:
    """Verdicts are written into the audit log, so the names they hold are a wire, not internals."""
    (document,) = screened(upload(pdf(2)))

    assert document.model_dump() == {
        "filename": "oznamenie-skody.pdf",
        "media_type": PDF,
        "verdicts": [
            {
                "check": verdict.check.value,
                "passed": verdict.passed,
                "detail": verdict.detail,
            }
            for verdict in document.verdicts
        ],
    }
    assert DocumentVerdicts.model_validate_json(document.model_dump_json()) == document


def test_a_rate_limit_verdict_names_the_wait_it_needs() -> None:
    clock = Clock()
    bucket = Bucket(RateLimit(burst=1, refill_per_second=4.0), clock=clock)
    assert bucket.take().verdict.passed

    admission: Admission = bucket.take()

    assert admission.wait_seconds == pytest.approx(0.25)
    assert "0.25" in admission.verdict.detail
