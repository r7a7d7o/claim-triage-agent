"""The claim records the surrounding systems hold, and the payloads they accept.

`core-sim` stands in for the insurance systems the pipeline integrates with; the fields here are the
minimum a v0.1 claim needs to reach the simulated system of record. The schema registry of increment
v0.2 is where the full claim field set becomes data.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ClaimStatus = Literal["received", "triaged"]
"""Where the claim stands as the surrounding systems see it.

v0.1 has two positions: `received` for a claim the systems have taken in, and `triaged` for one the
pipeline has finished with and pushed back out. The routing queues and human-review states of
increment v0.3 extend this.
"""


class ClaimSubmission(BaseModel):
    """A claim as the surrounding systems accept it."""

    model_config = ConfigDict(extra="forbid")

    policy_number: str = Field(min_length=1)
    incident_date: date
    claim_amount_eur: Decimal = Field(gt=0, max_digits=12, decimal_places=2)

    def as_received(self, claim_id: UUID, received_at: datetime) -> Claim:
        """The record the surrounding systems hold once they have taken this submission in.

        This is the one place the status a claim starts in is chosen, so the store and anything
        standing in for it cannot disagree about it.
        """
        return Claim(
            claim_id=claim_id,
            status="received",
            updated_at=received_at,
            **self.model_dump(),
        )


class ClaimStatusUpdate(BaseModel):
    """The status the pipeline reports back to the surrounding systems."""

    model_config = ConfigDict(extra="forbid")

    status: ClaimStatus


class Claim(BaseModel):
    """A stored claim: the record the surrounding systems return for every read."""

    claim_id: UUID
    status: ClaimStatus
    policy_number: str
    incident_date: date
    claim_amount_eur: Decimal
    updated_at: datetime
