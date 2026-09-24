"""Test doubles, at the boundaries the specification names.

The claim store is one of those boundaries: the real implementation is the Postgres table `core-sim`
owns, which the container job exercises through the running stack. The double here keeps claims in a
dictionary and answers in exactly the shapes the store port promises — nothing more, so a test
cannot pass on behaviour the database would not have.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from claim_triage.core_sim.models import Claim, ClaimStatus, ClaimSubmission
from claim_triage.core_sim.store import ClaimNotFound


class InMemoryClaims:
    """A `ClaimStore` over a dictionary, with the same answers as the Postgres one."""

    def __init__(self, *, ready: bool = True) -> None:
        self._ready = ready
        self._claims: dict[UUID, Claim] = {}

    @property
    def claims(self) -> tuple[Claim, ...]:
        """What the surrounding systems hold, for tests that assert on the outcome."""
        return tuple(self._claims.values())

    def create(self, submission: ClaimSubmission) -> Claim:
        claim = submission.as_received(uuid4(), datetime.now(UTC))
        self._claims[claim.claim_id] = claim
        return claim

    def read(self, claim_id: UUID) -> Claim:
        try:
            return self._claims[claim_id]
        except KeyError:
            raise ClaimNotFound(f"unknown claim {claim_id}") from None

    def record_status(self, claim_id: UUID, status: ClaimStatus) -> Claim:
        updated = self.read(claim_id).model_copy(
            update={"status": status, "updated_at": datetime.now(UTC)}
        )
        self._claims[claim_id] = updated
        return updated

    def ready(self) -> bool:
        return self._ready
