"""Prove the running stack can carry one claim end to end, from inside the stack.

The claim goes in at the entry point — the api's ASGI boundary, the surface a caller of this system
actually talks to — and the run it answers with has to have reached the surrounding systems: the
smoke reads the claim back from `core-sim` through the contract's own client and requires the status
the pipeline emitted for it. So the container job cannot go green on containers merely being up, or
on a graph that was never run.

Every wait is a bounded retry against a deadline: a stack that is not up yet, or not reachable yet,
is waited out, while anything the wire does not allow ends the run with its reason. Readiness is
waited for before the claim is submitted, and the submission itself is made once: a new request is a
new run — the entry point mints its identifier — so retrying one would leave a second claim in the
system of record rather than carrying the first. What is retried after that is the read-back, which
changes nothing.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from claim_triage.boundary import Refused, Unavailable
from claim_triage.config import InfrastructureSettings
from claim_triage.contract.client import (
    CoreSimClient,
    CoreSimError,
    CoreSimUnreachable,
    ServiceUnavailableError,
)
from claim_triage.contract.models import Claim, ClaimStatus
from claim_triage.triage.client import RunClient, UnexpectedAnswer
from claim_triage.triage.run import IntakeRequest, RunResult

if TYPE_CHECKING:
    from collections.abc import Callable

DEADLINE_SECONDS: Final = 120.0
"""How long the stack may take to carry the claim before this run fails."""

POLL_SECONDS: Final = 0.25
"""How long to wait between attempts: readiness is waited out, never assumed by sleeping."""

REQUEST_TIMEOUT_SECONDS: Final = 5.0
"""How long one call may take before a service counts as unreachable."""

SUBMISSION: Final = IntakeRequest(
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
    settings = InfrastructureSettings()
    entry_point = settings.api_base_url.rstrip("/")
    deadline = time.monotonic() + DEADLINE_SECONDS
    started = time.monotonic()

    try:
        with (
            RunClient(entry_point, timeout=REQUEST_TIMEOUT_SECONDS) as api,
            CoreSimClient(
                settings.core_sim_base_url.rstrip("/"), timeout=REQUEST_TIMEOUT_SECONDS
            ) as systems,
        ):
            _waited(api.read_readiness, deadline)
            run = api.intake(SUBMISSION)
            print(f"submitted claim {run.claim_id} as run {run.run_id}: status {run.status}")
            _expect_emitted(run)

            claim = _waited(lambda: systems.read_claim(run.claim_id), deadline)
            _expect_stored(claim, run)
            print(f"read claim {claim.claim_id} back from the surrounding systems: {claim.status}")
    except (SmokeFailed, Unavailable, Refused, UnexpectedAnswer, CoreSimError) as failure:
        print(f"smoke failed: {failure}", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    print(f"one claim processed end to end through {entry_point} in {elapsed:.2f}s")
    return 0


def _waited[T](call: Callable[[], T], deadline: float) -> T:
    """One call, retried while the stack is not up yet, and given up on past the deadline."""
    while True:
        try:
            return call()
        except (Unavailable, CoreSimUnreachable, ServiceUnavailableError) as not_ready:
            if not _wait_for_another_attempt(deadline):
                raise SmokeFailed(f"the stack did not answer in time: {not_ready}") from None


def _wait_for_another_attempt(deadline: float) -> bool:
    """Wait one poll interval while the deadline allows another attempt."""
    if time.monotonic() >= deadline:
        return False
    time.sleep(POLL_SECONDS)
    return True


def _expect_emitted(run: RunResult) -> None:
    if run.status != ClaimStatus.TRIAGED:
        raise SmokeFailed(
            f"expected run {run.run_id} to emit {ClaimStatus.TRIAGED}, not {run.status}"
        )


def _expect_stored(claim: Claim, run: RunResult) -> None:
    if claim.status != ClaimStatus.TRIAGED:
        raise SmokeFailed(
            f"the surrounding systems hold claim {claim.claim_id} as {claim.status},"
            f" not the {run.status} the run emitted"
        )
