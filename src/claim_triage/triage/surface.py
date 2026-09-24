"""The triager's boundary: the graph and its records, behind one run operation.

One operation, because a run is one: the caller asks for a claim to be carried, and is answered with
where it got to. Everything a caller can do to this deployable is here, and the errors it can get
back are the boundary's two — `Unavailable` when the store this service records into is unreachable,
and the surrounding systems' own refusals, relayed.

The span context is extracted from the request: a caller that traced its own call gets one trace
across both deployables rather than two traces that cannot be joined.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from opentelemetry import propagate

from claim_triage import boundary
from claim_triage.contract.models import Readiness
from claim_triage.triage.run import RunRequest, RunResult

if TYPE_CHECKING:
    from claim_triage.triage.pipeline import TriagePipeline


def create_app(pipeline: TriagePipeline) -> FastAPI:
    """Build the triager's ASGI surface over `pipeline`."""
    app = FastAPI(title="Claim triage: the graph and its records", version="0.1.0")
    boundary.install(app)

    @app.get("/healthz", response_model=Readiness)
    def read_readiness() -> Readiness:
        """Report readiness: this deployable is ready when the store it records into is."""
        if not pipeline.ready():
            raise boundary.Unavailable("the triage store is unreachable")
        return Readiness(status="ok")

    @app.post("/runs", response_model=RunResult, status_code=201)
    def create_run(run: RunRequest, request: Request) -> RunResult:
        """Run one claim through the graph, and answer with the status that was emitted."""
        return pipeline.run(run, context=propagate.extract(request.headers))

    return app
