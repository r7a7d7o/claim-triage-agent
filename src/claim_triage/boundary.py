"""What our own boundaries answer when a call cannot be served, in one error vocabulary.

The shape is the contract's: `ErrorResponse`, whose `code` comes from the same `ErrorCode` the
surrounding systems refuse with. A caller of our boundary therefore handles one error shape, and the
code a relayed refusal carries is the systems' own rather than renamed on the way through — which is
what lets a caller act on `policy_not_found` whether it came from the systems or from us.

`install` puts the handlers on a FastAPI surface. They are what a route raises rather than what a
route catches: domain code raises `Unavailable` or `Refused`, and nothing else about a surface
concerns errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from claim_triage.contract.models import ErrorCode, ErrorResponse

if TYPE_CHECKING:
    from fastapi import FastAPI


class Unavailable(Exception):
    """The call cannot be served right now: something the boundary needs is unreachable."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class Refused(Exception):
    """A refusal the caller can act on, relayed with the status and code the systems answered."""

    def __init__(self, status_code: int, response: ErrorResponse) -> None:
        super().__init__(f"{response.code}: {response.detail}")
        self.status_code = status_code
        self.response = response


def install(app: FastAPI) -> None:
    """Answer `Unavailable`, `Refused` and a request outside the wire in the boundary's shape."""

    @app.exception_handler(Unavailable)
    def unavailable(_request: Request, failure: Unavailable) -> JSONResponse:
        return refusal(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            ErrorCode.SERVICE_UNAVAILABLE,
            failure.detail,
        )

    @app.exception_handler(Refused)
    def refused(_request: Request, relayed: Refused) -> JSONResponse:
        return refusal(relayed.status_code, relayed.response.code, relayed.response.detail)

    @app.exception_handler(RequestValidationError)
    def outside_the_wire(_request: Request, invalid: RequestValidationError) -> JSONResponse:
        return refusal(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCode.INVALID_PAYLOAD,
            rejected_locations(invalid),
        )


def refusal(status_code: int, code: ErrorCode, detail: str) -> JSONResponse:
    """One error shape for every failure, in the contract's own model."""
    return JSONResponse(
        content=ErrorResponse(code=code, detail=detail).model_dump(mode="json"),
        status_code=status_code,
    )


def rejected_locations(invalid: RequestValidationError) -> str:
    """What exactly was wrong: one rejected location per statement, naming the field."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in invalid.errors()
    )
