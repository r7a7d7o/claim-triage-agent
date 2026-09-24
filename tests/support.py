"""Test doubles, at the boundaries the specification names.

The surrounding systems are one of those boundaries: the real implementation is the Postgres tables
`core-sim` owns, which the contract job exercises through the running service. The double here keeps
what the systems hold in dictionaries and answers in exactly the shapes the store port promises —
nothing more, so a test cannot pass on behaviour the database would not have. It fingerprints writes
through the same function the Postgres store does, so the two cannot disagree about what counts as
the same request.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from claim_triage.contract.models import (
    Claim,
    ClaimDocument,
    ClaimDocumentSubmission,
    ClaimParty,
    ClaimPartySubmission,
    ClaimStatus,
    ClaimStatusTransition,
    ClaimSubmission,
    Policy,
)
from claim_triage.core_sim.seed import SEED_POLICIES
from claim_triage.core_sim.store import (
    ClaimNotFound,
    IdempotencyKeyReuse,
    PolicyNotFound,
    request_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

Remembered = dict[str, object]
"""One recorded write: the fingerprint of its request, and the response it answered with."""


class InMemoryCoreSim:
    """A `CoreSimStore` over dictionaries, with the same answers as the Postgres one."""

    def __init__(self, *, ready: bool = True) -> None:
        self._ready = ready
        self._policies: dict[str, Policy] = {
            policy.policy_number: policy for policy in SEED_POLICIES
        }
        self._claims: dict[UUID, Claim] = {}
        self._documents: dict[UUID, ClaimDocument] = {}
        self._parties: dict[UUID, ClaimParty] = {}
        self._remembered: dict[str, Remembered] = {}

    @property
    def claims(self) -> tuple[Claim, ...]:
        """What the surrounding systems hold, for tests that assert on the outcome."""
        return tuple(self._claims.values())

    def ready(self) -> bool:
        return self._ready

    def read_policy(self, policy_number: str) -> Policy:
        try:
            return self._policies[policy_number]
        except KeyError:
            raise PolicyNotFound(f"unknown policy {policy_number}") from None

    def create(self, submission: ClaimSubmission, key: str) -> Claim:
        def perform() -> Claim:
            self.read_policy(submission.policy_number)
            claim = Claim(
                claim_id=uuid4(),
                status=ClaimStatus.RECEIVED,
                status_history=[
                    ClaimStatusTransition(
                        status=ClaimStatus.RECEIVED, recorded_at=datetime.now(UTC)
                    )
                ],
                **submission.model_dump(),
            )
            self._claims[claim.claim_id] = claim
            return claim

        return self._once("create_claim", submission.model_dump(mode="json"), key, perform)

    def read(self, claim_id: UUID) -> Claim:
        try:
            return self._claims[claim_id]
        except KeyError:
            raise ClaimNotFound(f"unknown claim {claim_id}") from None

    def record_status(self, claim_id: UUID, status: ClaimStatus, key: str) -> Claim:
        def perform() -> Claim:
            claim = self.read(claim_id)
            updated = claim.model_copy(
                update={
                    "status": status,
                    "status_history": [
                        *claim.status_history,
                        ClaimStatusTransition(status=status, recorded_at=datetime.now(UTC)),
                    ],
                }
            )
            self._claims[claim_id] = updated
            return updated

        request = {"claim_id": str(claim_id), "status": status}
        return self._once("record_claim_status", request, key, perform)

    def attach_document(
        self, claim_id: UUID, attachment: ClaimDocumentSubmission, key: str
    ) -> ClaimDocument:
        def perform() -> ClaimDocument:
            self.read(claim_id)
            document = ClaimDocument(
                document_id=uuid4(),
                claim_id=claim_id,
                attached_at=datetime.now(UTC),
                **attachment.model_dump(),
            )
            self._documents[document.document_id] = document
            return document

        request = {"claim_id": str(claim_id), **attachment.model_dump(mode="json")}
        return self._once("attach_claim_document", request, key, perform)

    def read_parties(self, claim_id: UUID) -> tuple[ClaimParty, ...]:
        self.read(claim_id)
        return tuple(party for party in self._parties.values() if party.claim_id == claim_id)

    def record_party(
        self, claim_id: UUID, registration: ClaimPartySubmission, key: str
    ) -> ClaimParty:
        def perform() -> ClaimParty:
            self.read(claim_id)
            party = ClaimParty(
                party_id=uuid4(),
                claim_id=claim_id,
                recorded_at=datetime.now(UTC),
                **registration.model_dump(),
            )
            self._parties[party.party_id] = party
            return party

        request = {"claim_id": str(claim_id), **registration.model_dump(mode="json")}
        return self._once("record_claim_party", request, key, perform)

    def _once[T](
        self, operation: str, request: Mapping[str, object], key: str, perform: Callable[[], T]
    ) -> T:
        """Run one write once per key, as the transaction in the store does."""
        fingerprint = request_fingerprint(operation, request)
        remembered = self._remembered.get(key)
        if remembered is not None:
            if remembered["fingerprint"] != fingerprint:
                raise IdempotencyKeyReuse(
                    f"Idempotency-Key {key} was already used for a different request"
                )
            return cast("T", remembered["response"])
        written = perform()
        self._remembered[key] = {"fingerprint": fingerprint, "response": written}
        return written
