"""The surrounding systems' contract, driven through the ASGI interface.

This is the seam the specification names for the API boundary: request and response contracts,
schema validation and error mapping, exercised through the app rather than through its internals.
The store is a double because the unit job has no database; the container job runs the same surface
over the Postgres store the service owns.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from claim_triage.core_sim.app import create_app
from support import InMemoryClaims

if TYPE_CHECKING:
    from collections.abc import Iterator

SUBMISSION = {
    "policy_number": "SIM-2026-0001",
    "incident_date": "2026-03-14",
    "claim_amount_eur": "1840.50",
}


@pytest.fixture
def claims() -> InMemoryClaims:
    return InMemoryClaims()


@pytest.fixture
def client(claims: InMemoryClaims) -> Iterator[TestClient]:
    with TestClient(create_app(claims)) as bound_client:
        yield bound_client


def test_a_submitted_claim_is_held_and_readable_as_received(client: TestClient) -> None:
    submitted = client.post("/claims", json=SUBMISSION)

    assert submitted.status_code == 201
    claim = submitted.json()
    assert claim["status"] == "received"
    assert claim["policy_number"] == SUBMISSION["policy_number"]
    assert claim["incident_date"] == SUBMISSION["incident_date"]
    assert Decimal(claim["claim_amount_eur"]) == Decimal("1840.50")

    held = client.get(f"/claims/{claim['claim_id']}")

    assert held.status_code == 200
    assert held.json() == claim


def test_a_claim_moves_to_triaged_and_a_reader_sees_it(client: TestClient) -> None:
    claim_id = client.post("/claims", json=SUBMISSION).json()["claim_id"]

    recorded = client.post(f"/claims/{claim_id}/status", json={"status": "triaged"})

    assert recorded.status_code == 200
    assert recorded.json()["status"] == "triaged"
    assert client.get(f"/claims/{claim_id}").json()["status"] == "triaged"


def test_readiness_follows_the_claims_the_systems_hold() -> None:
    reachable = TestClient(create_app(InMemoryClaims())).get("/healthz")
    unreachable = TestClient(create_app(InMemoryClaims(ready=False))).get("/healthz")

    assert reachable.status_code == 200
    assert reachable.json() == {"status": "ok", "claims": "reachable"}
    assert unreachable.status_code == 503
    assert unreachable.json() == {"status": "unavailable", "claims": "unreachable"}


def test_an_unknown_claim_is_a_not_found(client: TestClient) -> None:
    unknown = uuid4()

    read = client.get(f"/claims/{unknown}")
    recorded = client.post(f"/claims/{unknown}/status", json={"status": "triaged"})

    assert read.status_code == 404
    assert str(unknown) in read.json()["detail"]
    assert recorded.status_code == 404


@pytest.mark.parametrize(
    "submission",
    [
        {"claim_amount_eur": "1840.50", "incident_date": "2026-03-14"},
        {**SUBMISSION, "claim_amount_eur": "0"},
        {**SUBMISSION, "claim_amount_eur": "1840.505"},
        {**SUBMISSION, "incident_date": "14. 3. 2026"},
        {**SUBMISSION, "policy_number": ""},
        {**SUBMISSION, "declared_document_type": "oznamenie-skody"},
    ],
    ids=[
        "policy-number-missing",
        "amount-not-positive",
        "amount-has-sub-cent-precision",
        "date-not-iso",
        "policy-number-empty",
        "field-outside-the-contract",
    ],
)
def test_a_submission_outside_the_contract_is_rejected(
    client: TestClient, submission: dict[str, str]
) -> None:
    response = client.post("/claims", json=submission)

    assert response.status_code == 422


def test_a_status_outside_the_contract_is_rejected(client: TestClient) -> None:
    claim_id = client.post("/claims", json=SUBMISSION).json()["claim_id"]

    response = client.post(f"/claims/{claim_id}/status", json={"status": "paid"})

    assert response.status_code == 422
