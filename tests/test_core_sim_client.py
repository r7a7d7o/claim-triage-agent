"""The client's own contract: what it does with answers the running service cannot give.

The contract tests (`poe contract`) drive the client against a real service, which can only answer
what the service is able to produce. These tests drive it with a transport that answers anything at
all, so its behaviour on a stack that is not listening, a proxy that answers HTML, and a service
whose body has drifted from the contract is covered here instead of surfacing as an unhandled
`ValidationError` inside a caller. Nothing here needs a socket or a database.
"""

from __future__ import annotations

from uuid import uuid4

import httpx2
import pytest

from claim_triage.contract.client import (
    ClaimNotFoundError,
    CoreSimClient,
    CoreSimUnreachable,
    IdempotencyKeyReuseError,
    InvalidPayloadError,
    PolicyNotFoundError,
    Refusal,
    ServiceUnavailableError,
    UnexpectedResponse,
)
from claim_triage.contract.models import ErrorCode


def answering(*answers: tuple[int, object]) -> CoreSimClient:
    """A client whose transport answers each call with the next scripted answer."""
    scripted = iter(answers)

    def handle(_request: httpx2.Request) -> httpx2.Response:
        status_code, body = next(scripted)
        if isinstance(body, str):
            return httpx2.Response(status_code, text=body)
        return httpx2.Response(status_code, json=body)

    return CoreSimClient("http://systems", transport=httpx2.MockTransport(handle))


@pytest.mark.parametrize(
    ("answer", "exception"),
    [
        ((404, {"code": "claim_not_found", "detail": "unknown claim 1"}), ClaimNotFoundError),
        ((404, {"code": "policy_not_found", "detail": "unknown policy P"}), PolicyNotFoundError),
        (
            (409, {"code": "idempotency_key_reuse", "detail": "Idempotency-Key k"}),
            IdempotencyKeyReuseError,
        ),
        ((422, {"code": "invalid_payload", "detail": "body: bad"}), InvalidPayloadError),
        (
            (503, {"code": "service_unavailable", "detail": "the claims are unreachable"}),
            ServiceUnavailableError,
        ),
    ],
    ids=["claim-not-found", "policy-not-found", "key-reuse", "invalid-payload", "unavailable"],
)
def test_a_documented_refusal_is_the_exception_its_code_names(
    answer: tuple[int, object], exception: type[Refusal]
) -> None:
    systems = answering(answer)

    with pytest.raises(exception) as refused:
        systems.read_readiness()

    status_code, body = answer
    assert isinstance(body, dict)
    assert refused.value.status_code == status_code
    assert refused.value.code == ErrorCode(body["code"])
    assert refused.value.detail == body["detail"]
    assert str(refused.value) == f"{body['code']}: {body['detail']}"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ((500, {"detail": "Internal Server Error"}), "outside the contract"),
        ((503, "<html>the proxy is down</html>"), "answered non-JSON"),
        ((202, {"status": "ok"}), "outside the contract"),
        ((200, {"status": "not-a-state-the-contract-declares"}), "does not cover"),
        ((200, {"unexpected": "field"}), "does not cover"),
    ],
    ids=[
        "undocumented-error-body",
        "not-json",
        "status-outside-the-contract",
        "enum-outside-the-contract",
        "field-outside-the-contract",
    ],
)
def test_an_answer_the_contract_does_not_cover_is_refused(
    answer: tuple[int, object], expected: str
) -> None:
    systems = answering(answer)

    with pytest.raises(UnexpectedResponse) as refused:
        systems.read_readiness()

    assert expected in str(refused.value)


def test_a_stack_that_is_not_listening_is_unreachable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    systems = CoreSimClient("http://systems", transport=httpx2.MockTransport(refuse))

    with pytest.raises(CoreSimUnreachable) as refused:
        systems.read_claim(uuid4())

    assert refused.value.status_code is None


def test_every_call_reuses_one_transport_and_closes_with_the_client() -> None:
    closed: list[bool] = []

    def handle(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"status": "ok"})

    transport = httpx2.MockTransport(handle)
    with CoreSimClient("http://systems", transport=transport) as systems:
        assert systems.read_readiness().status == "ok"
        assert systems.read_readiness().status == "ok"
        closed.append(True)

    assert closed == [True]
