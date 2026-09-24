"""The generated client, against the running service, over every operation the contract declares.

These tests are the seam the specification names for the integration contract: the client that the
generated models and methods make of `contracts/core-sim.openapi.yaml`, calling a real service
over a real socket, with a real Postgres behind it. Each call is typed — a response whose fields
are not the ones the contract declares is refused by the models rather than quietly accepted — and
each documented failure arrives as the exception the contract's error code names.

The state a test writes is its own: every write carries an idempotency key unique to this run, so
these tests can be run again against the same database without colliding with themselves.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from claim_triage.contract.client import (
    ClaimNotFoundError,
    CoreSimClient,
    IdempotencyKeyReuseError,
    InvalidPayloadError,
    PolicyNotFoundError,
    Refusal,
)
from claim_triage.contract.models import (
    Claim,
    ClaimDocument,
    ClaimDocumentSubmission,
    ClaimParty,
    ClaimPartySubmission,
    ClaimStatus,
    ClaimStatusUpdate,
    ClaimSubmission,
    ErrorCode,
    PartyRole,
    Policy,
    Readiness,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

HELD_POLICY = "SIM-2026-0001"
"""One policy the systems hold; `claim_triage.core_sim.seed` is where it comes from."""

UNKNOWN_POLICY = "SIM-0000-0000"


def key() -> str:
    """A key no other test run has used, so replayed and fresh writes stay distinguishable."""
    return f"contract-{uuid4()}"


def submission(**overrides: Any) -> ClaimSubmission:
    """One claim against the policy the systems hold, with the fields a test wants changed."""
    fields: dict[str, Any] = {
        "policy_number": HELD_POLICY,
        "incident_date": "2026-03-14",
        "claim_amount_eur": "1840.50",
    }
    return ClaimSubmission.model_validate({**fields, **overrides})


def test_every_operation_in_the_contract_has_a_client_method(
    contract: Mapping[str, Any], systems: CoreSimClient
) -> None:
    """A generator that skipped an operation would regenerate to itself, caught nowhere else."""
    declared = {
        operation["operationId"]
        for item in contract["paths"].values()
        for operation in item.values()
    }

    assert declared
    for operation in sorted(declared):
        assert callable(getattr(systems, operation, None)), operation


def test_every_operation_answers_with_the_types_the_contract_declares(
    systems: CoreSimClient,
) -> None:
    assert isinstance(systems.read_readiness(), Readiness)

    policy = systems.read_policy(HELD_POLICY)
    assert isinstance(policy, Policy)
    assert policy.product_family == "Auto & pohoda"

    claim = systems.create_claim(submission(), idempotency_key=key())
    assert isinstance(claim, Claim)
    assert claim.status == ClaimStatus.RECEIVED
    assert claim.claim_amount_eur == Decimal("1840.50")
    assert systems.read_claim(claim.claim_id) == claim

    document = systems.attach_claim_document(
        claim.claim_id,
        ClaimDocumentSubmission(document_type="oznamenie-skody", filename="oznamenie-skody.pdf"),
        idempotency_key=key(),
    )
    assert isinstance(document, ClaimDocument)
    assert document.claim_id == claim.claim_id

    party = systems.record_claim_party(
        claim.claim_id, ClaimPartySubmission(role=PartyRole.CLAIMANT), idempotency_key=key()
    )
    assert isinstance(party, ClaimParty)
    assert systems.read_claim_parties(claim.claim_id) == [party]

    triaged = systems.record_claim_status(
        claim.claim_id, ClaimStatusUpdate(status=ClaimStatus.TRIAGED), idempotency_key=key()
    )
    assert triaged.status == ClaimStatus.TRIAGED
    assert [transition.status for transition in triaged.status_history] == [
        ClaimStatus.RECEIVED,
        ClaimStatus.TRIAGED,
    ]


def test_a_write_is_recorded_once_however_often_it_is_replayed(systems: CoreSimClient) -> None:
    replayed = key()

    first = systems.create_claim(submission(), idempotency_key=replayed)
    again = systems.create_claim(submission(), idempotency_key=replayed)
    fresh = systems.create_claim(submission(), idempotency_key=key())

    assert again == first
    assert fresh.claim_id != first.claim_id

    attachment = ClaimDocumentSubmission(document_type="oznamenie-skody", filename="one.pdf")
    attached_key = key()
    attached = systems.attach_claim_document(
        first.claim_id, attachment, idempotency_key=attached_key
    )
    reattached = systems.attach_claim_document(
        first.claim_id, attachment, idempotency_key=attached_key
    )

    assert reattached == attached

    role = ClaimPartySubmission(role=PartyRole.POLICYHOLDER)
    party_key = key()
    recorded = systems.record_claim_party(first.claim_id, role, idempotency_key=party_key)
    rerecorded = systems.record_claim_party(first.claim_id, role, idempotency_key=party_key)

    assert rerecorded == recorded
    assert systems.read_claim_parties(first.claim_id) == [recorded]


def test_a_replayed_status_transition_is_recorded_once(systems: CoreSimClient) -> None:
    claim = systems.create_claim(submission(), idempotency_key=key())
    transition = ClaimStatusUpdate(status=ClaimStatus.TRIAGED)
    replayed = key()

    recorded = systems.record_claim_status(claim.claim_id, transition, idempotency_key=replayed)
    again = systems.record_claim_status(claim.claim_id, transition, idempotency_key=replayed)

    assert again == recorded
    assert [t.status for t in systems.read_claim(claim.claim_id).status_history] == [
        ClaimStatus.RECEIVED,
        ClaimStatus.TRIAGED,
    ]


def test_a_key_reused_for_a_different_request_is_refused(systems: CoreSimClient) -> None:
    reused = key()
    systems.create_claim(submission(), idempotency_key=reused)

    with pytest.raises(IdempotencyKeyReuseError) as refused:
        systems.create_claim(submission(claim_amount_eur="10.00"), idempotency_key=reused)

    assert refused.value.status_code == 409
    assert refused.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSE
    assert reused in refused.value.detail


def test_an_unknown_claim_is_a_documented_not_found(systems: CoreSimClient) -> None:
    unknown = uuid4()
    calls: list[Callable[[], object]] = [
        lambda: systems.read_claim(unknown),
        lambda: systems.record_claim_status(
            unknown, ClaimStatusUpdate(status=ClaimStatus.TRIAGED), idempotency_key=key()
        ),
        lambda: systems.attach_claim_document(
            unknown,
            ClaimDocumentSubmission(document_type="oznamenie-skody", filename="one.pdf"),
            idempotency_key=key(),
        ),
        lambda: systems.read_claim_parties(unknown),
        lambda: systems.record_claim_party(
            unknown, ClaimPartySubmission(role=PartyRole.WITNESS), idempotency_key=key()
        ),
    ]

    for call in calls:
        with pytest.raises(ClaimNotFoundError) as refused:
            call()
        assert refused.value.status_code == 404
        assert refused.value.code == ErrorCode.CLAIM_NOT_FOUND
        assert str(unknown) in refused.value.detail


def test_an_unknown_policy_is_a_documented_not_found(systems: CoreSimClient) -> None:
    with pytest.raises(PolicyNotFoundError) as looked_up:
        systems.read_policy(UNKNOWN_POLICY)

    with pytest.raises(PolicyNotFoundError) as submitted:
        systems.create_claim(submission(policy_number=UNKNOWN_POLICY), idempotency_key=key())

    assert looked_up.value.status_code == 404
    assert looked_up.value.code == ErrorCode.POLICY_NOT_FOUND
    assert UNKNOWN_POLICY in submitted.value.detail


def test_a_request_the_service_validates_answers_with_a_documented_code(
    systems: CoreSimClient,
) -> None:
    """A path parameter the contract types as a UUID: the service refuses it, typed as its code."""
    with pytest.raises(InvalidPayloadError) as refused:
        # The service is what refuses this: the client sends what it is given.
        systems.read_claim("not-a-claim-id")  # type: ignore[arg-type]

    assert refused.value.status_code == 422
    assert refused.value.code == ErrorCode.INVALID_PAYLOAD
    assert "claim_id" in refused.value.detail

    # The document bounds the policy number; the service is what enforces the bound.
    with pytest.raises(InvalidPayloadError) as bounded:
        systems.read_policy("P" * 65)

    assert bounded.value.detail


@pytest.mark.parametrize(
    "payload",
    [
        {"claim_amount_eur": "0"},
        {"claim_amount_eur": "1840.505"},
        {"incident_date": "14. 3. 2026"},
        {"policy_number": ""},
        {"declared_document_type": "oznamenie-skody"},
    ],
    ids=[
        "amount-not-positive",
        "amount-has-sub-cent-precision",
        "date-not-iso",
        "policy-number-empty",
        "field-outside-the-contract",
    ],
)
def test_a_payload_the_contract_does_not_allow_never_leaves_the_client(
    payload: dict[str, str],
) -> None:
    """The generated models are the client's half of the contract: a bad body never leaves it."""
    with pytest.raises(ValidationError):
        submission(**payload)


def test_a_write_without_a_key_is_refused_with_a_documented_code(systems: CoreSimClient) -> None:
    """The header is required by the contract, so a call without one cannot even be built."""
    with pytest.raises(TypeError):
        systems.create_claim(submission())  # type: ignore[call-arg]

    with pytest.raises(InvalidPayloadError) as refused:
        systems.create_claim(submission(), idempotency_key="")

    assert refused.value.code == ErrorCode.INVALID_PAYLOAD
    assert "Idempotency-Key" in refused.value.detail


def test_a_write_retried_while_the_first_is_in_flight_leaves_one_record(
    systems: CoreSimClient,
) -> None:
    """The retry a client actually makes: the second attempt leaves while the first is still open.

    Both attempts must answer with the same claim, whichever of them the systems recorded first. A
    retry that fails, or that leaves a second claim behind, is what suppression exists to prevent.
    """
    shared = key()
    payload = submission()
    start = threading.Barrier(2)
    answers: list[Claim] = []
    failures: list[BaseException] = []

    def write() -> None:
        start.wait(timeout=10)
        try:
            answers.append(systems.create_claim(payload, idempotency_key=shared))
        except BaseException as failure:
            failures.append(failure)

    attempts = [threading.Thread(target=write) for _ in range(2)]
    for attempt in attempts:
        attempt.start()
    for attempt in attempts:
        attempt.join(timeout=30)

    assert failures == []
    assert len(answers) == 2
    assert answers[0] == answers[1]


def test_a_refusal_carries_what_the_service_named(systems: CoreSimClient) -> None:
    with pytest.raises(Refusal) as refused:
        systems.read_claim(uuid4())

    assert str(refused.value) == f"{refused.value.code}: {refused.value.detail}"
    assert isinstance(refused.value, ClaimNotFoundError)
