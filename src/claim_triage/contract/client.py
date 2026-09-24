"""The typed client of the surrounding systems' contract.

Generated from `contracts/core-sim.openapi.yaml` by `uv run poe generate`. Do not edit:
the `contract` CI job regenerates this package and fails when it differs from what is
committed.

One method per operation, named after the operation's `operationId`, and one typed
exception per error code the contract documents: a caller handles the contract, not HTTP.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Final, NoReturn, Self
from urllib.parse import quote
from uuid import UUID

import httpx2
from pydantic import TypeAdapter, ValidationError

from claim_triage.contract.models import (
    Claim,
    ClaimDocument,
    ClaimDocumentSubmission,
    ClaimParty,
    ClaimPartySubmission,
    ClaimStatusUpdate,
    ClaimSubmission,
    ErrorCode,
    ErrorResponse,
    Policy,
    Readiness,
)

DEFAULT_TIMEOUT_SECONDS: Final = 5.0
"""How long one call may take before the surrounding systems count as unreachable."""


class CoreSimError(Exception):
    """What the surrounding systems did instead of answering the contract."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CoreSimUnreachable(CoreSimError):
    """The surrounding systems could not be reached at all."""


class UnexpectedResponse(CoreSimError):
    """The systems answered with something the contract does not allow."""


class Refusal(CoreSimError):
    """An error the contract documents: the systems refused the call and named why."""

    def __init__(self, status_code: int, response: ErrorResponse) -> None:
        super().__init__(f"{response.code}: {response.detail}", status_code=status_code)
        self.response = response

    @property
    def code(self) -> ErrorCode:
        """What the systems refused the call for."""
        return self.response.code

    @property
    def detail(self) -> str:
        """What exactly was wrong, as the systems named it."""
        return self.response.detail


class InvalidPayloadError(Refusal):
    """The request is outside the contract, or a required header is missing."""


class ClaimNotFoundError(Refusal):
    """The systems hold no such claim."""


class PolicyNotFoundError(Refusal):
    """The systems hold no such policy."""


class IdempotencyKeyReuseError(Refusal):
    """The key was already used for a different request."""


class ServiceUnavailableError(Refusal):
    """The systems are up, but the claims they hold are not reachable."""


_CODES: Final[dict[ErrorCode, type[Refusal]]] = {
    ErrorCode.INVALID_PAYLOAD: InvalidPayloadError,
    ErrorCode.CLAIM_NOT_FOUND: ClaimNotFoundError,
    ErrorCode.POLICY_NOT_FOUND: PolicyNotFoundError,
    ErrorCode.IDEMPOTENCY_KEY_REUSE: IdempotencyKeyReuseError,
    ErrorCode.SERVICE_UNAVAILABLE: ServiceUnavailableError,
}


def _document(response: httpx2.Response) -> object:
    """The answer as JSON, or the failure that says the systems answered something else."""
    try:
        document: object = response.json()
    except ValueError as not_json:
        raise UnexpectedResponse(
            f"{response.request.method} {response.request.url} answered non-JSON",
            status_code=response.status_code,
        ) from not_json
    return document


def _refusal(response: httpx2.Response) -> NoReturn:
    """Raise the typed error the contract names for what the systems answered."""
    try:
        error = ErrorResponse.model_validate(_document(response))
    except ValidationError as undocumented:
        raise UnexpectedResponse(
            f"{response.request.url} answered {response.status_code} outside the contract",
            status_code=response.status_code,
        ) from undocumented
    refusal = _CODES.get(error.code)
    if refusal is None:
        raise UnexpectedResponse(
            f"{response.request.url} refused with the undocumented code {error.code!r}",
            status_code=response.status_code,
        )
    raise refusal(response.status_code, error)


_CLAIM_PARTY: Final = TypeAdapter(list[ClaimParty])


class CoreSimClient:
    """The surrounding systems, as the contract describes them.

    One method per operation, one typed exception per error code the contract documents. Pass a
    `transport` to drive it without a socket (the unit tests do); otherwise it speaks HTTP.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """Point the client at one running set of surrounding systems."""
        self._http = httpx2.Client(base_url=base_url, timeout=timeout, transport=transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the connections the client holds."""
        self._http.close()

    def _request[T](
        self,
        method: str,
        path: str,
        *,
        parse: Callable[[object], T],
        body: object | None = None,
        headers: Mapping[str, str] | None = None,
        expected: int = 200,
    ) -> T:
        """One call: the answer parsed as the contract describes it, or the error it names."""
        try:
            response = self._http.request(method, path, json=body, headers=headers)
        except httpx2.TransportError as unreachable:
            raise CoreSimUnreachable(f"{method} {path} unreachable: {unreachable}") from None
        if response.status_code != expected:
            _refusal(response)
        document: object = _document(response)
        try:
            return parse(document)
        except ValidationError as outside_the_contract:
            raise UnexpectedResponse(
                f"{method} {path} answered a body the contract does not cover",
                status_code=response.status_code,
            ) from outside_the_contract

    def read_readiness(
        self,
    ) -> Readiness:
        """Report whether the systems are ready"""
        return self._request(
            "GET",
            "/healthz",
            parse=Readiness.model_validate,
            expected=200,
        )

    def read_policy(
        self,
        policy_number: str,
    ) -> Policy:
        """Look a policy up as the system of record holds it"""
        return self._request(
            "GET",
            f"/policies/{quote(str(policy_number), safe='')}",
            parse=Policy.model_validate,
            expected=200,
        )

    def create_claim(
        self,
        claim_submission: ClaimSubmission,
        *,
        idempotency_key: str,
    ) -> Claim:
        """Take a claim into the systems"""
        return self._request(
            "POST",
            "/claims",
            parse=Claim.model_validate,
            body=claim_submission.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key},
            expected=201,
        )

    def read_claim(
        self,
        claim_id: UUID,
    ) -> Claim:
        """Read a claim back"""
        return self._request(
            "GET",
            f"/claims/{quote(str(claim_id), safe='')}",
            parse=Claim.model_validate,
            expected=200,
        )

    def record_claim_status(
        self,
        claim_id: UUID,
        claim_status_update: ClaimStatusUpdate,
        *,
        idempotency_key: str,
    ) -> Claim:
        """Record where the pipeline got to with a claim"""
        return self._request(
            "POST",
            f"/claims/{quote(str(claim_id), safe='')}/status",
            parse=Claim.model_validate,
            body=claim_status_update.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key},
            expected=200,
        )

    def attach_claim_document(
        self,
        claim_id: UUID,
        claim_document_submission: ClaimDocumentSubmission,
        *,
        idempotency_key: str,
    ) -> ClaimDocument:
        """Attach a document the pipeline read out of a claim

        The bytes stay with the pipeline; what the surrounding systems hold is which
        document a claim carries, what the pipeline classified it as, and under which
        filename it arrived.
        """
        return self._request(
            "POST",
            f"/claims/{quote(str(claim_id), safe='')}/documents",
            parse=ClaimDocument.model_validate,
            body=claim_document_submission.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key},
            expected=201,
        )

    def read_claim_parties(
        self,
        claim_id: UUID,
    ) -> list[ClaimParty]:
        """Read the parties a claim has so far

        The order is the order the parties were recorded in, oldest first.
        """
        return self._request(
            "GET",
            f"/claims/{quote(str(claim_id), safe='')}/parties",
            parse=_CLAIM_PARTY.validate_python,
            expected=200,
        )

    def record_claim_party(
        self,
        claim_id: UUID,
        claim_party_submission: ClaimPartySubmission,
        *,
        idempotency_key: str,
    ) -> ClaimParty:
        """Record a party the pipeline identified

        Party history is a history because something appends to it: the pipeline records
        each party as extraction identifies it (v0.2), and this is where it does so.
        Without the append, the read above could only ever answer with an empty list.
        """
        return self._request(
            "POST",
            f"/claims/{quote(str(claim_id), safe='')}/parties",
            parse=ClaimParty.model_validate,
            body=claim_party_submission.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key},
            expected=201,
        )
