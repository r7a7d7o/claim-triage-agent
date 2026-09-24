"""The api's boundary: the stateless entry point a claim is submitted at (`docs/adr/0001`).

It holds nothing and decides nothing. It mints the run identifier — so every span, log line and
audit entry along the path can be joined on one identifier that exists before the first call — opens
the span a trace starts at, and hands the claim to the triager. What it answers is what the pipeline
emitted, relayed unchanged, refusals included.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from uuid import uuid4

from fastapi import FastAPI, Request
from opentelemetry import propagate

from claim_triage import boundary
from claim_triage.contract.models import Readiness
from claim_triage.telemetry import run_attributes
from claim_triage.triage.client import RunClient
from claim_triage.triage.run import IntakeRequest, RunRequest, RunResult

if TYPE_CHECKING:
    from claim_triage.telemetry import Telemetry

CLAIM_SPAN: Final = "api.claim"
"""The span one submission is: everything this deployable does for a claim happens inside it."""


def create_app(runs: RunClient, *, telemetry: Telemetry) -> FastAPI:
    """Build the entry point's ASGI surface over the run boundary it forwards to."""
    app = FastAPI(title="Claim triage: the entry point", version="0.1.0")
    boundary.install(app)

    @app.get("/healthz", response_model=Readiness)
    def read_readiness() -> Readiness:
        """Report readiness: this deployable is ready when the boundary behind it is."""
        return runs.read_readiness()

    @app.post("/claims", response_model=RunResult, status_code=201)
    def submit_claim(claim: IntakeRequest, request: Request) -> RunResult:
        """Take one claim in, and answer with the status the pipeline emitted for it."""
        run_id = uuid4()
        attributes = run_attributes(
            run_id=run_id, experiment=claim.experiment, variant=claim.variant
        )
        with telemetry.span(
            CLAIM_SPAN, attributes=attributes, context=propagate.extract(request.headers)
        ):
            return runs.submit(RunRequest(run_id=run_id, **claim.model_dump()))

    return app
