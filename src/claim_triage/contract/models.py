"""The wire models of the surrounding systems' contract.

Generated from `contracts/core-sim.openapi.yaml` by `uv run poe generate`. Do not edit: the
`contract` CI job regenerates this package and fails when it differs from what is committed.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    """What the systems refused a call for."""

    # The request is outside the contract, or a required header is missing.
    INVALID_PAYLOAD = "invalid_payload"
    # The systems hold no such claim.
    CLAIM_NOT_FOUND = "claim_not_found"
    # The systems hold no such policy.
    POLICY_NOT_FOUND = "policy_not_found"
    # The key was already used for a different request.
    IDEMPOTENCY_KEY_REUSE = "idempotency_key_reuse"
    # The systems are up, but the claims they hold are not reachable.
    SERVICE_UNAVAILABLE = "service_unavailable"


class ErrorResponse(BaseModel):
    """The shape of every failure the systems answer with."""

    model_config = ConfigDict(extra="forbid")

    # What the systems refused a call for.
    code: ErrorCode
    # What exactly was wrong, naming the field or the key that caused it.
    detail: str


class Readiness(BaseModel):
    """What a ready system of record answers with."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


class Policy(BaseModel):
    """A policy as the system of record holds it."""

    model_config = ConfigDict(extra="forbid")

    # The number the claim is made against.
    policy_number: str
    # The product line the policy belongs to, e.g. `Auto & pohoda`.
    product_family: str
    # The day cover started.
    valid_from: date


class ClaimStatus(StrEnum):
    """Where the claim stands as the surrounding systems see it. v0.1 has two positions:
    `received` for a claim the systems have taken in, and `triaged` for one the pipeline
    has finished with. The routing queues and human-review states of increment v0.3
    extend this.
    """

    RECEIVED = "received"
    TRIAGED = "triaged"


class ClaimStatusTransition(BaseModel):
    """One status the claim has held, and when it started holding it."""

    model_config = ConfigDict(extra="forbid")

    # Where the claim stands as the surrounding systems see it. v0.1 has two positions:
    # `received` for a claim the systems have taken in, and `triaged` for one the pipeline has
    # finished with. The routing queues and human-review states of increment v0.3 extend this.
    status: ClaimStatus
    recorded_at: datetime


class ClaimSubmission(BaseModel):
    """A claim as the surrounding systems accept it."""

    model_config = ConfigDict(extra="forbid")

    policy_number: str = Field(min_length=1, max_length=64)
    # The day the loss happened, which decides the edition a clause is read from.
    incident_date: date
    # An amount in euro, at most two decimal places, as a decimal string.
    claim_amount_eur: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class ClaimStatusUpdate(BaseModel):
    """The status the pipeline reports back to the surrounding systems."""

    model_config = ConfigDict(extra="forbid")

    # Where the claim stands as the surrounding systems see it. v0.1 has two positions:
    # `received` for a claim the systems have taken in, and `triaged` for one the pipeline has
    # finished with. The routing queues and human-review states of increment v0.3 extend this.
    status: ClaimStatus


class Claim(BaseModel):
    """A stored claim, the record the systems return for every read."""

    model_config = ConfigDict(extra="forbid")

    claim_id: UUID
    policy_number: str
    incident_date: date
    # An amount in euro, at most two decimal places, as a decimal string.
    claim_amount_eur: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    # Where the claim stands as the surrounding systems see it. v0.1 has two positions:
    # `received` for a claim the systems have taken in, and `triaged` for one the pipeline has
    # finished with. The routing queues and human-review states of increment v0.3 extend this.
    status: ClaimStatus
    # Every status this claim has held, oldest first; `status` is the last of them.
    status_history: list[ClaimStatusTransition] = Field(min_length=1)


class ClaimDocumentSubmission(BaseModel):
    """A document the pipeline read out of a claim, as it attaches it."""

    model_config = ConfigDict(extra="forbid")

    # What the pipeline classified the document as. v0.2's schema registry owns this
    # vocabulary; until then it is free text.
    document_type: str = Field(min_length=1, max_length=64)
    filename: str = Field(min_length=1, max_length=255)


class ClaimDocument(BaseModel):
    """A document the surrounding systems hold against a claim."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    claim_id: UUID
    document_type: str
    filename: str
    attached_at: datetime


class PartyRole(StrEnum):
    """What a party is to the claim."""

    POLICYHOLDER = "policyholder"
    CLAIMANT = "claimant"
    DRIVER = "driver"
    WITNESS = "witness"
    OTHER = "other"


class ClaimPartySubmission(BaseModel):
    """A party the pipeline identified, as it records it."""

    model_config = ConfigDict(extra="forbid")

    # What a party is to the claim.
    role: PartyRole


class ClaimParty(BaseModel):
    """A party the surrounding systems hold against a claim."""

    model_config = ConfigDict(extra="forbid")

    # The systems' own identifier; the pipeline's identification of the party stays with the
    # pipeline.
    party_id: UUID
    claim_id: UUID
    # What a party is to the claim.
    role: PartyRole
    recorded_at: datetime
