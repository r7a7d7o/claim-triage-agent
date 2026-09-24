"""The ASGI surface of the simulated surrounding insurance systems.

`create_app` takes the store it works over, so the surface can be driven through the ASGI interface
in unit tests and against the Postgres store in the running stack. Every operation here is one the
contract declares, and every request and response is validated with the models generated from it
(`contracts/core-sim.openapi.yaml`), so the surface cannot drift from the contract without the
`contract` job saying so.

Every failure answers in the contract's one error shape, carrying a code from its one enum, so a
client maps a code onto typed errors rather than reading prose out of a status.
"""

from __future__ import annotations

from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import FastAPI, Header, Path, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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
from claim_triage.core_sim.store import (
    ClaimNotFound,
    CoreSimStore,
    IdempotencyKeyReuse,
    PolicyNotFound,
)

IDEMPOTENCY_KEY: Final = "Idempotency-Key"
"""The header every write in the contract carries; the store deduplicates the write by it."""

Responses = dict[int | str, dict[str, Any]]
"""How FastAPI declares the documented responses of a route."""

CLAIM_NOT_FOUND: Final[Responses] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "The systems hold no such claim.",
    }
}

POLICY_NOT_FOUND: Final[Responses] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "The systems hold no such policy.",
    }
}

IDEMPOTENCY_KEY_REUSE: Final[Responses] = {
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "The key was already used for a different request.",
    }
}

INVALID_PAYLOAD: Final[Responses] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ErrorResponse,
        "description": "The request is outside the contract, or the required header is missing.",
    }
}

SERVICE_UNAVAILABLE: Final[Responses] = {
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": ErrorResponse,
        "description": "The systems are up but the claims they hold are not reachable.",
    }
}

IdempotencyKey = Annotated[str, Header(alias=IDEMPOTENCY_KEY, min_length=1, max_length=128)]
"""The key a write is deduplicated by, as the contract's header parameter declares it."""

PolicyNumber = Annotated[str, Path(min_length=1, max_length=64)]
"""The policy a claim is made against, bounded as the contract's path parameter declares it."""


class ServiceUnavailable(RuntimeError):
    """Raised when the systems are up but the records they hold cannot be reached."""


def create_app(store: CoreSimStore) -> FastAPI:
    """Build the surrounding systems' ASGI app over `store`."""
    app = FastAPI(title="Simulated surrounding insurance systems", version="0.1.0")

    @app.exception_handler(ClaimNotFound)
    def unknown_claim(_request: Request, missing: ClaimNotFound) -> JSONResponse:
        return _refusal(status.HTTP_404_NOT_FOUND, ErrorCode.CLAIM_NOT_FOUND, str(missing))

    @app.exception_handler(PolicyNotFound)
    def unknown_policy(_request: Request, missing: PolicyNotFound) -> JSONResponse:
        return _refusal(status.HTTP_404_NOT_FOUND, ErrorCode.POLICY_NOT_FOUND, str(missing))

    @app.exception_handler(IdempotencyKeyReuse)
    def reused_key(_request: Request, reused: IdempotencyKeyReuse) -> JSONResponse:
        return _refusal(status.HTTP_409_CONFLICT, ErrorCode.IDEMPOTENCY_KEY_REUSE, str(reused))

    @app.exception_handler(RequestValidationError)
    def outside_the_contract(_request: Request, invalid: RequestValidationError) -> JSONResponse:
        """Every rejected field and every missing header answers in the contract's error shape."""
        return _refusal(
            status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.INVALID_PAYLOAD, _locations(invalid)
        )

    @app.exception_handler(ServiceUnavailable)
    def unreachable(_request: Request, failure: ServiceUnavailable) -> JSONResponse:
        return _refusal(
            status.HTTP_503_SERVICE_UNAVAILABLE, ErrorCode.SERVICE_UNAVAILABLE, str(failure)
        )

    @app.get(
        "/healthz",
        operation_id="read_readiness",
        response_model=Readiness,
        responses=SERVICE_UNAVAILABLE,
    )
    def read_readiness() -> Readiness:
        """Report readiness: the systems are ready when the claims they hold are reachable."""
        if not store.ready():
            raise ServiceUnavailable("the claims the systems hold are unreachable")
        return Readiness(status="ok")

    @app.get(
        "/policies/{policy_number}",
        operation_id="read_policy",
        response_model=Policy,
        responses={**POLICY_NOT_FOUND, **INVALID_PAYLOAD},
    )
    def read_policy(policy_number: PolicyNumber) -> Policy:
        """Look a policy up as the system of record holds it."""
        return store.read_policy(policy_number)

    @app.post(
        "/claims",
        operation_id="create_claim",
        status_code=status.HTTP_201_CREATED,
        response_model=Claim,
        responses={**POLICY_NOT_FOUND, **IDEMPOTENCY_KEY_REUSE, **INVALID_PAYLOAD},
    )
    def create_claim(submission: ClaimSubmission, idempotency_key: IdempotencyKey) -> Claim:
        """Take a claim into the systems."""
        return store.create(submission, idempotency_key)

    @app.get(
        "/claims/{claim_id}",
        operation_id="read_claim",
        response_model=Claim,
        responses={**CLAIM_NOT_FOUND, **INVALID_PAYLOAD},
    )
    def read_claim(claim_id: UUID) -> Claim:
        """Read a claim back."""
        return store.read(claim_id)

    @app.post(
        "/claims/{claim_id}/status",
        operation_id="record_claim_status",
        response_model=Claim,
        responses={**CLAIM_NOT_FOUND, **IDEMPOTENCY_KEY_REUSE, **INVALID_PAYLOAD},
    )
    def record_claim_status(
        claim_id: UUID, update: ClaimStatusUpdate, idempotency_key: IdempotencyKey
    ) -> Claim:
        """Record where the pipeline got to with a claim."""
        return store.record_status(claim_id, update.status, idempotency_key)

    @app.post(
        "/claims/{claim_id}/documents",
        operation_id="attach_claim_document",
        status_code=status.HTTP_201_CREATED,
        response_model=ClaimDocument,
        responses={**CLAIM_NOT_FOUND, **IDEMPOTENCY_KEY_REUSE, **INVALID_PAYLOAD},
    )
    def attach_claim_document(
        claim_id: UUID, attachment: ClaimDocumentSubmission, idempotency_key: IdempotencyKey
    ) -> ClaimDocument:
        """Attach a document the pipeline read out of a claim."""
        return store.attach_document(claim_id, attachment, idempotency_key)

    @app.get(
        "/claims/{claim_id}/parties",
        operation_id="read_claim_parties",
        response_model=list[ClaimParty],
        responses={**CLAIM_NOT_FOUND, **INVALID_PAYLOAD},
    )
    def read_claim_parties(claim_id: UUID) -> tuple[ClaimParty, ...]:
        """Read the parties a claim has so far."""
        return store.read_parties(claim_id)

    @app.post(
        "/claims/{claim_id}/parties",
        operation_id="record_claim_party",
        status_code=status.HTTP_201_CREATED,
        response_model=ClaimParty,
        responses={**CLAIM_NOT_FOUND, **IDEMPOTENCY_KEY_REUSE, **INVALID_PAYLOAD},
    )
    def record_claim_party(
        claim_id: UUID, party: ClaimPartySubmission, idempotency_key: IdempotencyKey
    ) -> ClaimParty:
        """Record a party the pipeline identified."""
        return store.record_party(claim_id, party, idempotency_key)

    return app


def _refusal(status_code: int, code: ErrorCode, detail: str) -> JSONResponse:
    """One error shape for every failure, in the contract's own model."""
    return JSONResponse(
        content=ErrorResponse(code=code, detail=detail).model_dump(mode="json"),
        status_code=status_code,
    )


def _locations(invalid: RequestValidationError) -> str:
    """What exactly was wrong: one rejected location per statement, naming the field."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in invalid.errors()
    )
