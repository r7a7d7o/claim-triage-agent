"""Our own error vocabulary, from both sides: what a relayed refusal becomes, and what we read.

Nothing here drives a document or a graph. What it pins is the vocabulary itself: every code the
surrounding systems' contract names has to be one our boundary can answer with — otherwise a
refusal they gave stops being a refusal our own client understands — and every code we can answer
with has to be one the client parses, which is the difference between a refusal a caller can act on
and a defect it can only report.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from claim_triage import boundary
from claim_triage.boundary import BoundaryCode, BoundaryError, Refused, Unavailable
from claim_triage.contract.models import ErrorCode, ErrorResponse
from claim_triage.triage.client import RunClient
from claim_triage.triage.run import RunRequest

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI as App


def test_every_code_the_systems_refuse_with_relays_into_our_own_vocabulary() -> None:
    """A relayed refusal keeps its code, and the code has to exist here for it to be relayed."""
    for code in ErrorCode:
        relayed = BoundaryError.relayed(ErrorResponse(code=code, detail=f"the systems said {code}"))

        assert relayed.code.value == code.value
        assert relayed.detail == f"the systems said {code}"


class RefusingEveryCode:
    """One refusal per call, each with the next code of our vocabulary, over one served
    boundary."""

    def __init__(self) -> None:
        self.codes: list[BoundaryCode] = list(BoundaryCode)

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/runs")
        def refuse() -> JSONResponse:
            code = self.codes.pop(0)
            return boundary.refusal(status.HTTP_400_BAD_REQUEST, code, f"refused with {code.value}")

        return app


def test_our_client_parses_every_code_our_own_vocabulary_answers_with(
    serve: Callable[[App], str],
) -> None:
    """A code we answer and the client does not parse is a defect at the caller, not a refusal."""
    refusals = RefusingEveryCode()

    with RunClient(serve(refusals.app())) as runs:
        for code in BoundaryCode:
            if code is BoundaryCode.SERVICE_UNAVAILABLE:
                # "Cannot serve this right now" is not a refusal to act on: the client reads it
                # as the same thing an unreachable boundary is, as it does with the systems'.
                with pytest.raises(Unavailable):
                    runs.submit(a_run_request())
                continue
            with pytest.raises(Refused) as refused:
                runs.submit(a_run_request())

            assert refused.value.error.code is code
            assert refused.value.status_code == status.HTTP_400_BAD_REQUEST


def a_run_request() -> RunRequest:
    return RunRequest(
        run_id=uuid4(),
        policy_number="SIM-2026-0001",
        incident_date=date(2026, 3, 14),
        claim_amount_eur=Decimal("1840.50"),
        guard_verdicts=[],
    )
