"""The surrounding systems' contract, driven through the ASGI interface.

This is the seam the specification names for the API boundary: request and response contracts,
schema validation, error mapping and the idempotency of writes, exercised through the app rather
than through its internals. The store is a double because the unit job has no database — except in
the one test below where the store's own answer to a database it cannot reach is the behaviour under
test; the `contract` job runs the same surface, and the same generated client, against the Postgres
store the service owns.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from claim_triage.core_sim.app import create_app
from claim_triage.core_sim.store import PostgresCoreSimStore
from support import InMemoryCoreSim

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from httpx2 import Response

SUBMISSION = {
    "policy_number": "SIM-2026-0001",
    "incident_date": "2026-03-14",
    "claim_amount_eur": "1840.50",
}

DOCUMENT: Final = {"document_type": "oznamenie-skody", "filename": "oznamenie-skody.pdf"}


@pytest.fixture
def systems() -> InMemoryCoreSim:
    return InMemoryCoreSim()


@pytest.fixture
def client(systems: InMemoryCoreSim) -> Iterator[TestClient]:
    with TestClient(create_app(systems)) as bound_client:
        yield bound_client


def key() -> str:
    """A fresh idempotency key: every write in the contract carries one."""
    return uuid4().hex


def submit(
    client: TestClient, *, key_value: str, submission: Mapping[str, object] | None = None
) -> Response:
    """Submit one claim, with the header the contract requires for every write."""
    return client.post(
        "/claims", json=submission or SUBMISSION, headers={"Idempotency-Key": key_value}
    )


def test_a_submitted_claim_is_held_and_readable_as_received(client: TestClient) -> None:
    submitted = submit(client, key_value="k1")

    assert submitted.status_code == 201
    claim = submitted.json()
    assert claim["status"] == "received"
    assert claim["policy_number"] == SUBMISSION["policy_number"]
    assert claim["incident_date"] == SUBMISSION["incident_date"]
    assert Decimal(claim["claim_amount_eur"]) == Decimal("1840.50")
    assert [transition["status"] for transition in claim["status_history"]] == ["received"]

    held = client.get(f"/claims/{claim['claim_id']}")

    assert held.status_code == 200
    assert held.json() == claim


def test_a_claim_moves_to_triaged_and_a_reader_sees_the_transition(client: TestClient) -> None:
    claim_id = submit(client, key_value="k1").json()["claim_id"]

    recorded = client.post(
        f"/claims/{claim_id}/status",
        json={"status": "triaged"},
        headers={"Idempotency-Key": "k2"},
    )

    assert recorded.status_code == 200
    assert recorded.json()["status"] == "triaged"
    assert [transition["status"] for transition in recorded.json()["status_history"]] == [
        "received",
        "triaged",
    ]
    assert client.get(f"/claims/{claim_id}").json() == recorded.json()


def test_a_replayed_write_leaves_one_record_and_the_answer_it_first_gave(
    client: TestClient,
) -> None:
    first = submit(client, key_value="replayed")
    replayed = submit(client, key_value="replayed")
    another = submit(client, key_value="another-key")

    assert replayed.status_code == 201
    assert replayed.json() == first.json()
    assert another.json()["claim_id"] != first.json()["claim_id"]


def test_a_status_transition_is_replayed_but_recorded_once(client: TestClient) -> None:
    claim_id = submit(client, key_value="k1").json()["claim_id"]
    recorded = client.post(
        f"/claims/{claim_id}/status", json={"status": "triaged"}, headers={"Idempotency-Key": "k2"}
    )

    replayed = client.post(
        f"/claims/{claim_id}/status", json={"status": "triaged"}, headers={"Idempotency-Key": "k2"}
    )

    assert replayed.json() == recorded.json()
    assert [t["status"] for t in client.get(f"/claims/{claim_id}").json()["status_history"]] == [
        "received",
        "triaged",
    ]


def test_a_key_reused_for_a_different_request_is_refused(client: TestClient) -> None:
    submit(client, key_value="k1")

    reused = submit(client, key_value="k1", submission={**SUBMISSION, "claim_amount_eur": "10.00"})

    assert reused.status_code == 409
    assert reused.json()["code"] == "idempotency_key_reuse"
    assert "k1" in reused.json()["detail"]


def test_a_policy_is_readable_with_the_product_family_it_belongs_to(client: TestClient) -> None:
    found = client.get("/policies/SIM-2026-0001")

    assert found.status_code == 200
    assert found.json()["product_family"] == "Auto & pohoda"
    assert found.json()["valid_from"] == "2026-01-01"


def test_a_document_is_attached_once_however_often_it_is_retried(client: TestClient) -> None:
    claim_id = submit(client, key_value="k1").json()["claim_id"]

    attached = client.post(
        f"/claims/{claim_id}/documents", json=DOCUMENT, headers={"Idempotency-Key": "k2"}
    )
    replayed = client.post(
        f"/claims/{claim_id}/documents", json=DOCUMENT, headers={"Idempotency-Key": "k2"}
    )

    assert attached.status_code == 201
    assert attached.json()["document_type"] == DOCUMENT["document_type"]
    assert replayed.json() == attached.json()


def test_parties_are_recorded_and_read_back_in_order(client: TestClient) -> None:
    claim_id = submit(client, key_value="k1").json()["claim_id"]

    recorded = client.post(
        f"/claims/{claim_id}/parties", json={"role": "claimant"}, headers={"Idempotency-Key": "k2"}
    )
    replayed = client.post(
        f"/claims/{claim_id}/parties", json={"role": "claimant"}, headers={"Idempotency-Key": "k2"}
    )
    client.post(
        f"/claims/{claim_id}/parties", json={"role": "witness"}, headers={"Idempotency-Key": "k3"}
    )

    assert recorded.status_code == 201
    assert replayed.json() == recorded.json()
    assert [party["role"] for party in client.get(f"/claims/{claim_id}/parties").json()] == [
        "claimant",
        "witness",
    ]


def test_readiness_follows_the_records_the_systems_hold() -> None:
    reachable = TestClient(create_app(InMemoryCoreSim())).get("/healthz")
    unreachable = TestClient(create_app(InMemoryCoreSim(ready=False))).get("/healthz")

    assert reachable.status_code == 200
    assert reachable.json() == {"status": "ok"}
    assert unreachable.status_code == 503
    assert unreachable.json()["code"] == "service_unavailable"


def test_the_systems_report_unavailable_when_the_database_is_unreachable() -> None:
    """What an operator sees when Postgres is down: the systems answer, their claims do not.

    The real store, pointed at a port nothing listens on — port 1 is never a Postgres, so the
    refusal is immediate and the test needs no database of its own.
    """
    unreachable = PostgresCoreSimStore(
        "postgresql://claim_triage:claim_triage@127.0.0.1:1/claim_triage"
    )

    readiness = TestClient(create_app(unreachable)).get("/healthz")

    assert readiness.status_code == 503
    assert readiness.json()["code"] == "service_unavailable"


@pytest.mark.parametrize(
    "path",
    ["/claims/{claim_id}", "/claims/{claim_id}/parties"],
    ids=["read", "parties"],
)
def test_an_unknown_claim_is_a_not_found(client: TestClient, path: str) -> None:
    unknown = uuid4()

    response = client.get(path.format(claim_id=unknown))

    assert response.status_code == 404
    assert response.json()["code"] == "claim_not_found"
    assert str(unknown) in response.json()["detail"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/claims/{claim_id}/status", {"status": "triaged"}),
        ("/claims/{claim_id}/documents", DOCUMENT),
        ("/claims/{claim_id}/parties", {"role": "claimant"}),
    ],
    ids=["status", "documents", "parties"],
)
def test_an_unknown_claim_refuses_a_write(
    client: TestClient, path: str, body: dict[str, str]
) -> None:
    response = client.post(
        path.format(claim_id=uuid4()), json=body, headers={"Idempotency-Key": key()}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "claim_not_found"


def test_an_unknown_policy_is_a_not_found(client: TestClient) -> None:
    read = client.get("/policies/SIM-0000-0000")
    submitted = submit(client, key_value="k1", submission={**SUBMISSION, "policy_number": "NOPE"})

    assert read.status_code == 404
    assert read.json()["code"] == "policy_not_found"
    assert submitted.status_code == 404
    assert submitted.json()["code"] == "policy_not_found"


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
    response = submit(client, key_value=key(), submission=submission)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_payload"


def test_a_parameter_outside_the_bounds_the_contract_declares_is_rejected(
    client: TestClient,
) -> None:
    long_number = client.get(f"/policies/{'P' * 65}")
    empty_key = client.post("/claims", json=SUBMISSION, headers={"Idempotency-Key": ""})

    assert long_number.status_code == 422
    assert long_number.json()["code"] == "invalid_payload"
    assert empty_key.status_code == 422
    assert empty_key.json()["code"] == "invalid_payload"


def test_a_status_outside_the_contract_is_rejected(client: TestClient) -> None:
    claim_id = submit(client, key_value="k1").json()["claim_id"]

    response = client.post(
        f"/claims/{claim_id}/status", json={"status": "paid"}, headers={"Idempotency-Key": key()}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_payload"


def test_a_write_without_an_idempotency_key_is_rejected(client: TestClient) -> None:
    response = client.post("/claims", json=SUBMISSION)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_payload"
    assert "Idempotency-Key" in response.json()["detail"]
