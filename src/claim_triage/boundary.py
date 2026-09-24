"""What our own boundaries answer when a call cannot be served, in one error vocabulary.

The shape is the contract's (`ErrorResponse`: a `code` and a `detail`), and the codes the
surrounding systems refuse with are in `BoundaryCode` as they are — under the same strings the
contract names — so a caller of our boundary handles one error shape, and the code a relayed
refusal carries is the systems' own rather than renamed on the way through. That is what lets a
caller act on `policy_not_found` whether it came from the systems or from us.

The codes after those five are ours, and they are here rather than in the surrounding systems'
contract because the systems never answer them: a document the ingress refused never reaches them,
and a rate limit is our own front door. Adding them to that contract would document a promise it
does not keep. `tests/test_boundary.py` holds the two vocabularies together: every code the contract
names relays into this one, which is what keeps a relayed refusal a shape our client understands.

`install` puts the handlers on a FastAPI surface. They are what a route raises rather than what a
route catches: domain code raises `Unavailable` or `Refused`, and nothing else about a surface
concerns errors.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from claim_triage.contract.models import ErrorCode, ErrorResponse

if TYPE_CHECKING:
    from fastapi import FastAPI


class BoundaryCode(StrEnum):
    """What a caller of our own boundaries can be refused with, in one vocabulary.

    The first five are the contract's own codes, and they are the same strings: they are repeated
    here because the systems' enum is generated from their document and ours is what our own
    boundaries answer with, and a test holds every one of theirs to one of ours.
    """

    # The request is outside the wire, or a required part of it is missing.
    INVALID_PAYLOAD = "invalid_payload"
    CLAIM_NOT_FOUND = "claim_not_found"
    POLICY_NOT_FOUND = "policy_not_found"
    IDEMPOTENCY_KEY_REUSE = "idempotency_key_reuse"
    SERVICE_UNAVAILABLE = "service_unavailable"

    # Ours: nothing here serves the path the caller asked for.
    NOT_FOUND = "not_found"
    # Ours: an upload whose content type this boundary does not take.
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    # Ours: an upload, or the request carrying it, past the size ceiling.
    PAYLOAD_TOO_LARGE = "payload_too_large"
    # Ours: an upload the ingress refused. `detail` names the check that refused it and what it
    # found, because the checks are the ingress's own vocabulary rather than the caller's.
    DOCUMENT_REFUSED = "document_refused"
    # Ours: the caller has spent its burst and is being asked to wait rather than to change the
    # request. The answer carries `Retry-After`.
    RATE_LIMITED = "rate_limited"


_REFUSED_BEFORE_A_ROUTE: Final[dict[int, BoundaryCode]] = {
    status.HTTP_404_NOT_FOUND: BoundaryCode.NOT_FOUND,
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: BoundaryCode.UNSUPPORTED_MEDIA_TYPE,
}
"""Which of our codes the framework's own refusals answer with, by status. Everything else it
refuses before a route is a request outside the wire, which is one code."""


class BoundaryError(BaseModel):
    """The shape of every failure our own boundaries answer with."""

    model_config = ConfigDict(extra="forbid")

    code: BoundaryCode
    # What exactly was wrong, naming the part, the field or the check that caused it.
    detail: str

    @classmethod
    def relayed(cls, refusal: ErrorResponse) -> BoundaryError:
        """A refusal the surrounding systems answered, carried into our vocabulary unchanged.

        The code keeps its own value: which boundary refused is not something a caller should have
        to tell apart, and the string it acts on is the one the systems documented.
        """
        return cls(code=BoundaryCode(refusal.code.value), detail=refusal.detail)


class Unavailable(Exception):
    """The call cannot be served right now: something the boundary needs is unreachable."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class Refused(Exception):
    """A refusal the caller can act on, with the status and code the boundary answered."""

    def __init__(self, status_code: int, error: BoundaryError) -> None:
        super().__init__(f"{error.code}: {error.detail}")
        self.status_code = status_code
        self.error = error


def install(app: FastAPI) -> None:
    """Answer `Unavailable`, `Refused` and a request outside the wire in the boundary's shape."""

    @app.exception_handler(Unavailable)
    def unavailable(_request: Request, failure: Unavailable) -> JSONResponse:
        return refusal(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            BoundaryCode.SERVICE_UNAVAILABLE,
            failure.detail,
        )

    @app.exception_handler(Refused)
    def refused(_request: Request, relayed: Refused) -> JSONResponse:
        return answer(relayed)

    @app.exception_handler(StarletteHTTPException)
    def refused_before_a_route(_request: Request, failure: StarletteHTTPException) -> JSONResponse:
        """A request the framework itself refused, answered in this boundary's own shape.

        A body that cannot be parsed as multipart, a path nothing serves, a method no route takes:
        the encoding layer answers these without any route of ours seeing them, and a caller must
        not have to tell our vocabulary from the framework's to know it was refused.
        `invalid_payload` is what the contract calls a request outside the wire, which is what these
        are; the status and the detail stay the framework's own.
        """
        return refusal(
            failure.status_code,
            _REFUSED_BEFORE_A_ROUTE.get(failure.status_code, BoundaryCode.INVALID_PAYLOAD),
            str(failure.detail),
        )

    @app.exception_handler(RequestValidationError)
    def outside_the_wire(_request: Request, invalid: RequestValidationError) -> JSONResponse:
        return refusal(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            BoundaryCode.INVALID_PAYLOAD,
            rejected_locations(invalid),
        )


def refusal(
    status_code: int,
    code: BoundaryCode | ErrorCode,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """One error shape for every failure, whoever is answering it.

    The systems answer their own five codes and our boundaries answer those plus the ones only we
    have, so this takes either vocabulary: the value is what crosses the wire, and running one
    through the other leaves the string alone.
    """
    return JSONResponse(
        content=BoundaryError(code=BoundaryCode(code.value), detail=detail).model_dump(mode="json"),
        status_code=status_code,
        headers=headers,
    )


def answer(refused: Refused, *, headers: dict[str, str] | None = None) -> JSONResponse:
    """The response a refusal is, for the callers that answer one without raising it.

    A route raises `Refused` and the handler below answers it; the middleware in front of the routes
    answers one directly, because it refuses before a route is reached.
    """
    return refusal(refused.status_code, refused.error.code, refused.error.detail, headers=headers)


def rejected_locations(invalid: RequestValidationError) -> str:
    """What exactly was wrong: one rejected location per statement, naming the field."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in invalid.errors()
    )


def rejected_fields(invalid: ValidationError) -> str:
    """The same statement for a model validated by hand rather than by the wire's own binding.

    A part of a multipart request is parsed where the route reads it, so its rejection is raised by
    pydantic rather than by FastAPI; a caller cannot tell the two apart, because both are answered
    as `invalid_payload` naming the field that was wrong.
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in invalid.errors()
    )
