"""The ASGI surface of the simulated surrounding insurance systems.

`create_app` takes the store it works over, so the surface can be driven through the ASGI interface
in unit tests and against the Postgres store in the running stack. The operations here are the two a
v0.1 claim needs — take a claim in, and record where the pipeline got to with it; ticket 03 turns
this surface into the contract-first service the pipeline integrates with (policy lookup, document
attachment, status update, party history, idempotent writes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from claim_triage.core_sim.models import Claim, ClaimStatusUpdate, ClaimSubmission
from claim_triage.core_sim.store import ClaimNotFound

if TYPE_CHECKING:
    from claim_triage.core_sim.store import ClaimStore


def create_app(store: ClaimStore) -> FastAPI:
    """Build the surrounding systems' ASGI app over `store`."""
    app = FastAPI(title="Simulated surrounding insurance systems")

    @app.exception_handler(ClaimNotFound)
    def unknown_claim(_request: Request, missing: ClaimNotFound) -> JSONResponse:
        """One place maps a claim the systems do not hold onto a 404."""
        return JSONResponse({"detail": str(missing)}, status_code=status.HTTP_404_NOT_FOUND)

    @app.get("/healthz")
    def health(response: Response) -> dict[str, str]:
        """Report readiness: the systems are ready when the claims they hold are reachable."""
        if not store.ready():
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unavailable", "claims": "unreachable"}
        return {"status": "ok", "claims": "reachable"}

    @app.post("/claims", status_code=status.HTTP_201_CREATED)
    def create_claim(submission: ClaimSubmission) -> Claim:
        """Take one claim into the surrounding systems."""
        return store.create(submission)

    @app.get("/claims/{claim_id}")
    def read_claim(claim_id: UUID) -> Claim:
        """Return one claim as the surrounding systems hold it."""
        return store.read(claim_id)

    @app.post("/claims/{claim_id}/status")
    def record_status(claim_id: UUID, update: ClaimStatusUpdate) -> Claim:
        """Record where the pipeline got to with a claim, and return the updated record."""
        return store.record_status(claim_id, update.status)

    return app
