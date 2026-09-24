"""Prove the running stack can carry one claim end to end, from inside the stack.

Until ticket 04 wires the graph, this command stands in for the `triager`: it submits a claim to the
simulated surrounding systems through the client generated from their contract, records the status
transition the pipeline will later record, and reads both back. It exits 0 only once the claim has
survived the whole path, so the container job cannot report success on containers merely being up.

Every wait is a bounded retry against a deadline: systems that are not ready yet, or not reachable
yet, are waited out, while anything the contract does not allow ends the run with its reason. A
retry reuses the write's idempotency key, so waiting the stack out can never duplicate a claim.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from claim_triage.config import InfrastructureSettings
from claim_triage.contract.client import (
    CoreSimClient,
    CoreSimError,
    CoreSimUnreachable,
    ServiceUnavailableError,
)
from claim_triage.contract.models import (
    Claim,
    ClaimStatus,
    ClaimStatusUpdate,
    ClaimSubmission,
)

if TYPE_CHECKING:
    from collections.abc import Callable

DEADLINE_SECONDS: Final = 120.0
"""How long the stack may take to carry the claim before this run fails."""

POLL_SECONDS: Final = 0.25
"""How long to wait between attempts: readiness is waited out, never assumed by sleeping."""

REQUEST_TIMEOUT_SECONDS: Final = 5.0
"""How long one call may take before the systems count as unreachable."""

SUBMISSION: Final = ClaimSubmission(
    policy_number="SIM-2026-0001",
    incident_date=date(2026, 3, 14),
    claim_amount_eur=Decimal("1840.50"),
)
"""The one claim this smoke run submits, against one of the policies the systems hold.

A literal, not a generated document: ticket 13 generates the synthetic claim corpus, and this claim
exists only to prove the stack moves one from end to end.
"""


class SmokeFailed(Exception):
    """What the stack did instead of carrying the claim."""


def main() -> int:
    """Drive one claim through the running stack, printing what the stack did."""
    base_url = InfrastructureSettings().core_sim_base_url.rstrip("/")
    deadline = time.monotonic() + DEADLINE_SECONDS
    started = time.monotonic()

    try:
        with CoreSimClient(base_url, timeout=REQUEST_TIMEOUT_SECONDS) as client:
            _retried(client.read_readiness, deadline)
            key = f"smoke-{uuid4()}"
            submitted = _retried(
                lambda: client.create_claim(SUBMISSION, idempotency_key=key), deadline
            )
            _expect(submitted, ClaimStatus.RECEIVED)
            print(f"submitted claim {submitted.claim_id}: status received")

            stored = _retried(lambda: client.read_claim(submitted.claim_id), deadline)
            _expect(stored, ClaimStatus.RECEIVED)
            print(f"read claim {submitted.claim_id} back from the stack: status received")

            _retried(
                lambda: client.record_claim_status(
                    submitted.claim_id,
                    ClaimStatusUpdate(status=ClaimStatus.TRIAGED),
                    idempotency_key=f"{key}-triaged",
                ),
                deadline,
            )

            settled = _retried(lambda: client.read_claim(submitted.claim_id), deadline)
            _expect(settled, ClaimStatus.TRIAGED)
            print(f"recorded and read back status triaged for claim {submitted.claim_id}")
    except CoreSimError as refused:
        print(f"smoke failed: {refused}", file=sys.stderr)
        return 1
    except SmokeFailed as failure:
        print(f"smoke failed: {failure}", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    print(f"one claim processed end to end through {base_url} in {elapsed:.2f}s")
    return 0


def _retried[T](call: Callable[[], T], deadline: float) -> T:
    """One call, retried while the stack is not up yet, and given up on past the deadline."""
    while True:
        try:
            return call()
        except (ServiceUnavailableError, CoreSimUnreachable) as not_ready:
            if not _wait_for_another_attempt(deadline):
                raise SmokeFailed(f"the stack did not answer in time: {not_ready}") from None


def _wait_for_another_attempt(deadline: float) -> bool:
    """Wait one poll interval while the deadline allows another attempt."""
    if time.monotonic() >= deadline:
        return False
    time.sleep(POLL_SECONDS)
    return True


def _expect(claim: Claim, status: ClaimStatus) -> None:
    if claim.status != status:
        raise SmokeFailed(f"expected claim {claim.claim_id} to be {status}, got {claim.status}")
