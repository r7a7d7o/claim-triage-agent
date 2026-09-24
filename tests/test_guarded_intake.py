"""The ingress at the boundary a caller talks to: refused there, or admitted and recorded.

What `tests/test_guard_ingress.py` asserts as data, this module asserts on the wire. A document
outside a limit comes back from the served entry point as a client error that names the document
and the check that refused it, and nothing is left anywhere: no claim in the surrounding systems,
no run, no audit entry. A document inside every limit is carried, and the verdicts that let it in
are on the run and in the audit entry it was recorded with.

The rate limit is here for the same reason: it is answered by the boundary rather than by a check
over a document, so what it produces is a status, a code and a header that asks the caller to come
back. Both the refusal and the limit are checked for what they *do not* leave behind as well,
because a rejected submission that half-happened is worse than one that failed.

The stack is the skeleton's — three served apps over the in-memory doubles — with guard thresholds
small enough that a test's document is small too.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import httpx2
import pytest

from claim_triage.boundary import BoundaryCode
from claim_triage.config import GuardSettings
from claim_triage.contract.models import ClaimStatus
from claim_triage.guards.verdict import Check
from claim_triage.triage import audit
from support import (
    CLAIM,
    PDF,
    TEST_GUARD,
    Skeleton,
    encrypted_pdf,
    pdf,
    upload,
    zip_bomb,
    zip_of,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from claim_triage.guards.ingress import Upload

BOUNDARY: Final = "a-boundary-the-transport-never-declares"
"""The multipart boundary a test writes by hand, to send a body with no declared length."""

LIMITED: GuardSettings = GuardSettings(
    media_types=TEST_GUARD.media_types,
    max_submission_bytes=TEST_GUARD.max_submission_bytes,
    max_document_bytes=TEST_GUARD.max_document_bytes,
    max_document_pages=TEST_GUARD.max_document_pages,
    max_archive_expansion_ratio=TEST_GUARD.max_archive_expansion_ratio,
    rate_limit_burst=2,
    rate_limit_refill_per_second=1.0,
)
"""The same guard with a burst a test can spend by hand: two submissions, and the third waits."""


def test_a_document_inside_every_limit_is_carried_and_its_verdicts_recorded(
    skeleton: Callable[..., Skeleton],
) -> None:
    wired = skeleton()

    run = wired.upload_ok(upload(pdf(2)))

    (recorded,) = wired.triage.runs
    assert recorded.run_id == run.run_id
    assert recorded.status is ClaimStatus.TRIAGED

    (document,) = recorded.guard_verdicts
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

    # The audit entry carries what the run was admitted with, and the chain still verifies over it.
    (entry,) = wired.triage.entries()
    assert entry.guard_verdicts == recorded.guard_verdicts
    assert audit.verify(wired.triage.entries()).ok


def test_a_document_bundle_is_screened_when_it_is_a_bundle(
    skeleton: Callable[..., Skeleton],
) -> None:
    """A zip is asked the checks an archive can answer, and the page count is not among them."""
    wired = skeleton()
    bundle = upload(zip_of({"oznamenie-skody.pdf": pdf(2)}), media_type="application/zip")

    wired.upload_ok(bundle)

    (recorded,) = wired.triage.runs
    (document,) = recorded.guard_verdicts
    assert [verdict.check for verdict in document.verdicts] == [
        Check.SIZE,
        Check.MEDIA_TYPE,
        Check.STRUCTURE,
        Check.ENCRYPTION,
        Check.ARCHIVE,
    ]


@pytest.mark.parametrize(
    ("document", "expected_status", "expected_code", "expected_check"),
    [
        (
            upload(b"%PDF-1.4" + b" " * 5_000),
            413,
            BoundaryCode.PAYLOAD_TOO_LARGE,
            Check.SIZE,
        ),
        (
            upload(pdf(1), media_type="text/plain"),
            415,
            BoundaryCode.UNSUPPORTED_MEDIA_TYPE,
            Check.MEDIA_TYPE,
        ),
        (upload(b"not a pdf at all"), 422, BoundaryCode.DOCUMENT_REFUSED, Check.STRUCTURE),
        (
            upload(b"PK\x03\x04 truncated", media_type="application/zip", filename="claim.zip"),
            422,
            BoundaryCode.DOCUMENT_REFUSED,
            Check.STRUCTURE,
        ),
        (upload(encrypted_pdf()), 422, BoundaryCode.DOCUMENT_REFUSED, Check.ENCRYPTION),
        (upload(pdf(4)), 422, BoundaryCode.DOCUMENT_REFUSED, Check.PAGE_COUNT),
        (
            upload(zip_bomb(), media_type="application/zip", filename="claim.zip"),
            422,
            BoundaryCode.DOCUMENT_REFUSED,
            Check.ARCHIVE,
        ),
    ],
    ids=[
        "oversize",
        "wrong-content-type",
        "malformed",
        "malformed-archive",
        "encrypted",
        "over-page-count",
        "archive-bomb",
    ],
)
def test_a_document_outside_one_limit_leaves_the_boundary_with_its_own_reason(
    skeleton: Callable[..., Skeleton],
    document: Upload,
    expected_status: int,
    expected_code: BoundaryCode,
    expected_check: Check,
) -> None:
    wired = skeleton()

    response = wired.upload(document)

    assert response.status_code == expected_status, response.text
    assert response.json()["code"] == expected_code.value
    # Which document, and which check refused it: the two things a caller needs to act on it.
    assert document.filename in response.json()["detail"]
    assert expected_check.value in response.json()["detail"]

    # A refused submission is not a partial one: nothing was created, run or recorded.
    assert wired.systems.claims == ()
    assert wired.triage.runs == ()
    assert wired.triage.entries() == ()


def test_a_submission_with_one_bad_document_is_refused_whole(
    skeleton: Callable[..., Skeleton],
) -> None:
    """A good document does not carry a bad one in, and leaves no claim behind either."""
    wired = skeleton()

    response = wired.upload(
        upload(pdf(1), filename="good.pdf"), upload(zip_bomb(), filename="claim.zip")
    )

    assert response.status_code == 422
    assert "claim.zip" in response.json()["detail"]
    assert wired.systems.claims == ()
    assert wired.triage.runs == ()


def test_the_burst_is_spent_and_the_next_submission_is_asked_to_wait(
    skeleton: Callable[..., Skeleton],
) -> None:
    wired = skeleton(guard=LIMITED)

    for _ in range(LIMITED.rate_limit_burst):
        assert wired.submit().status_code == 201

    refused = wired.submit()

    assert refused.status_code == 429
    assert refused.json()["code"] == BoundaryCode.RATE_LIMITED.value
    assert refused.json()["detail"].startswith(
        "rate_limit: the burst of 2 submissions is spent, and the next token arrives in"
    )
    # The header rounds the remaining wait up, because a caller that comes back too soon is refused
    # again: the detail carries the exact fraction, the header what to do about it.
    assert refused.headers["Retry-After"] == "1"
    # The wait is what a caller is asked for, not a change to what it sent: nothing was recorded.
    assert len(wired.triage.runs) == LIMITED.rate_limit_burst


def test_readiness_is_not_a_submission_and_is_never_limited(
    skeleton: Callable[..., Skeleton],
) -> None:
    """A health check throttled by the limiter would be a limiter that takes the service down."""
    wired = skeleton(guard=LIMITED)

    for _ in range(LIMITED.rate_limit_burst):
        assert wired.submit().status_code == 201
    assert wired.submit().status_code == 429

    for _ in range(LIMITED.rate_limit_burst * 2):
        assert wired.readiness().status_code == 200


def test_a_submission_that_declares_more_than_it_may_carry_is_refused_before_it_is_read(
    skeleton: Callable[..., Skeleton],
) -> None:
    """The body is not multipart at all: a 413 for its declared size proves it was never parsed."""
    wired = skeleton()
    body = b"x" * (TEST_GUARD.max_submission_bytes * 2)

    with httpx2.Client(base_url=wired.api_url) as client:
        response = client.post(
            "/claims",
            content=body,
            headers={"content-type": "multipart/form-data; boundary=nothing-parses-this"},
        )

    assert response.status_code == 413, response.text
    assert response.json()["code"] == BoundaryCode.PAYLOAD_TOO_LARGE.value
    assert str(TEST_GUARD.max_submission_bytes) in response.json()["detail"]
    assert wired.systems.claims == ()
    assert wired.triage.runs == ()


def test_a_body_that_is_not_a_submission_at_all_is_refused_in_the_same_shape(
    skeleton: Callable[..., Skeleton],
) -> None:
    """The encoding layer's own refusal is still an answer a caller's client can parse."""
    wired = skeleton()

    with httpx2.Client(base_url=wired.api_url) as client:
        response = client.post(
            "/claims",
            content=b"this is not a multipart body",
            headers={"content-type": "multipart/form-data; boundary=nothing-parses-this"},
        )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == BoundaryCode.INVALID_PAYLOAD.value
    assert response.json()["detail"]
    assert wired.triage.runs == ()


def test_a_submission_with_no_claim_part_is_refused_naming_the_part(
    skeleton: Callable[..., Skeleton],
) -> None:
    """A document with no claim beside it is outside the wire, and the wire's binding says so."""
    wired = skeleton()

    with httpx2.Client(base_url=wired.api_url) as client:
        response = client.post("/claims", files=[("documents", ("a.pdf", pdf(1), PDF))])

    assert response.status_code == 422, response.text
    assert response.json()["code"] == BoundaryCode.INVALID_PAYLOAD.value
    assert "claim" in response.json()["detail"]
    assert wired.systems.claims == ()
    assert wired.triage.runs == ()


def test_a_submission_that_declares_no_length_is_carried_rather_than_refused(
    skeleton: Callable[..., Skeleton],
) -> None:
    """A chunked submission cannot be judged by a length it never declared, so it is not refused for
    one: what bounds it is the per-document ceiling as each document is read, which `docs/adr/0007`
    states rather than leaves to be discovered."""
    wired = skeleton()

    with httpx2.Client(base_url=wired.api_url) as client:
        response = client.post(
            "/claims",
            content=iter([chunked_submission(claim=json.dumps(CLAIM))]),
            headers={"content-type": f"multipart/form-data; boundary={BOUNDARY}"},
        )

    assert response.status_code == 201, response.text
    (recorded,) = wired.triage.runs
    assert recorded.status == ClaimStatus.TRIAGED


def chunked_submission(**parts: str) -> bytes:
    """One multipart body, written by hand so a test can send it without a declared length."""
    rendered = "".join(
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in parts.items()
    )
    return f"{rendered}--{BOUNDARY}--\r\n".encode()


def test_a_claim_outside_the_wire_is_still_refused_naming_the_field(
    skeleton: Callable[..., Skeleton],
) -> None:
    """The claim travels as a part of the submission now, and a bad field is still one refusal."""
    wired = skeleton()

    response = wired.submit(claim_amount_eur="-1.00")

    assert response.status_code == 422
    assert response.json()["code"] == BoundaryCode.INVALID_PAYLOAD.value
    assert "claim_amount_eur" in response.json()["detail"]
    assert wired.systems.claims == ()
