"""The api's boundary: the stateless entry point a claim is submitted at (`docs/adr/0001`).

It holds nothing and decides nothing about the claim — but it is the one place that decides what
may come in at all. The ingress layer runs here, in front of the triager, because this is where the
bytes arrive: a document over the ceiling, of a type the boundary does not take, encrypted,
unreadable or declaring a bomb is refused before anything downstream buffers it, and a caller that
has spent its burst is told to wait rather than let through to spend someone else's capacity.

A claim arrives with its documents as one multipart submission: the claim is a JSON part named
`claim`, each document a file part named `documents`. The claim is parsed here — validated by the
model the triager would validate it with — and each document is read through `read_bounded`, so the
boundary never holds more of a document than the ceiling plus the byte that proves it is over.

What the boundary does not hold at all is a submission that declares more than it may carry: that is
refused on the declared length, before the multipart parser has read the body. A submission that
declares no length — a chunked one — cannot be judged that way, and the framework spools it to disk
before a route sees it; the per-document ceiling is what bounds it then, and `docs/adr/0007` states
that rather than leaving it to be discovered.

What the run was admitted with travels with it: the verdicts of every check over every document,
in the order they were asked, so the triager records them and the audit log carries them. The run
identifier is still minted here, before the first call, so every span, log line and audit entry
along the path can be joined on one identifier. What the pipeline answers is relayed unchanged,
refusals included, in the vocabulary every boundary of ours answers in.
"""

from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING, Annotated, Final
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile, status
from opentelemetry import propagate
from pydantic import ValidationError

from claim_triage import boundary
from claim_triage.boundary import BoundaryCode, Refused
from claim_triage.config import GuardSettings
from claim_triage.contract.models import Readiness
from claim_triage.guards import ingress
from claim_triage.guards.ingress import IngressLimits, Upload, read_bounded
from claim_triage.guards.rate_limit import Limiters, RateLimit
from claim_triage.guards.verdict import Check, Verdict
from claim_triage.telemetry import run_attributes
from claim_triage.triage.client import CLAIM_PART, CLAIMS, DOCUMENTS_PART, RunClient
from claim_triage.triage.run import IntakeRequest, RunRequest, RunResult

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.responses import Response

    from claim_triage.telemetry import Telemetry

CLAIM_SPAN: Final = "api.claim"
"""The span one submission is: everything this deployable does for a claim happens inside it."""

CALLER_UNKNOWN: Final = "unknown"
"""What the rate limit counts when the transport cannot say who called: one bucket for them all."""

REFUSALS: Final[dict[Check, tuple[int, BoundaryCode]]] = {
    Check.SIZE: (status.HTTP_413_CONTENT_TOO_LARGE, BoundaryCode.PAYLOAD_TOO_LARGE),
    Check.MEDIA_TYPE: (
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        BoundaryCode.UNSUPPORTED_MEDIA_TYPE,
    ),
    Check.STRUCTURE: (status.HTTP_422_UNPROCESSABLE_CONTENT, BoundaryCode.DOCUMENT_REFUSED),
    Check.ENCRYPTION: (status.HTTP_422_UNPROCESSABLE_CONTENT, BoundaryCode.DOCUMENT_REFUSED),
    Check.ARCHIVE: (status.HTTP_422_UNPROCESSABLE_CONTENT, BoundaryCode.DOCUMENT_REFUSED),
    Check.PAGE_COUNT: (status.HTTP_422_UNPROCESSABLE_CONTENT, BoundaryCode.DOCUMENT_REFUSED),
    Check.RATE_LIMIT: (status.HTTP_429_TOO_MANY_REQUESTS, BoundaryCode.RATE_LIMITED),
}
"""What each check a caller can fail means on the wire.

A document refused for its type or its size gets the status HTTP has for that; the structural checks
share one, because what a caller does about them is the same — send a document we can read — and
which check refused is in the detail and, for the run that was let through, in its verdicts.
"""


def create_app(runs: RunClient, *, telemetry: Telemetry, guard: GuardSettings) -> FastAPI:
    """Build the entry point's ASGI surface over the run boundary it forwards to."""
    app = FastAPI(title="Claim triage: the entry point", version="0.1.0")
    boundary.install(app)

    limits = IngressLimits(
        media_types=guard.media_types,
        max_submission_bytes=guard.max_submission_bytes,
        max_bytes=guard.max_document_bytes,
        max_pages=guard.max_document_pages,
        max_expansion_ratio=guard.max_archive_expansion_ratio,
    )
    submitters = Limiters(
        RateLimit(
            burst=guard.rate_limit_burst,
            refill_per_second=guard.rate_limit_refill_per_second,
        )
    )

    # Middleware rather than a route dependency, and both checks are here for the same reason: a
    # dependency is solved after FastAPI has parsed the submission, which is the work these two
    # exist to avoid doing. A caller past its burst is refused without its body being read at all,
    # and a submission that declares more than it may carry is refused the same way — before the
    # multipart parser has spooled a hostile upload to disk to find out. Readiness is deliberately
    # neither limited nor sized — a health check is not a submission, and throttling it would turn a
    # limiter into an outage.
    @app.middleware("http")
    async def screen_submissions(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "POST" and request.url.path == CLAIMS:
            admission = submitters.take(_caller(request))
            if not admission.verdict.passed:
                return boundary.answer(
                    _refused(None, admission.verdict),
                    headers={"Retry-After": str(max(ceil(admission.wait_seconds), 1))},
                )
            declared = ingress.declared_size(_declared_length(request), limits)
            if not declared.passed:
                return boundary.answer(_refused(None, declared))
        return await call_next(request)

    @app.get("/healthz", response_model=Readiness)
    def read_readiness() -> Readiness:
        """Report readiness: this deployable is ready when the boundary behind it is."""
        return runs.read_readiness()

    @app.post("/claims", response_model=RunResult, status_code=201)
    def submit_claim(
        request: Request,
        claim: Annotated[
            str, Form(alias=CLAIM_PART, description="The claim, as the JSON object /claims takes")
        ],
        documents: Annotated[list[UploadFile] | None, File(alias=DOCUMENTS_PART)] = None,
    ) -> RunResult:
        """Take one claim and its documents in, and answer with the status the pipeline emitted."""
        taken_in = _claim(claim)
        verdicts = ingress.screen(
            [_upload(document, limits) for document in documents or ()], limits
        )
        refused = ingress.first_refusal(verdicts)
        if refused is not None:
            raise _refused(*refused)

        run_id = uuid4()
        attributes = run_attributes(
            run_id=run_id, experiment=taken_in.experiment, variant=taken_in.variant
        )
        with telemetry.span(
            CLAIM_SPAN, attributes=attributes, context=propagate.extract(request.headers)
        ):
            return runs.submit(
                RunRequest(run_id=run_id, guard_verdicts=verdicts, **taken_in.model_dump())
            )

    return app


def _claim(part: str) -> IntakeRequest:
    """The claim as the model takes it, or the refusal a caller cannot tell from the wire's own.

    The part is JSON carried in a form field, so pydantic parses it here rather than FastAPI binding
    it; a rejection is answered in the same shape and with the same code the wire's own binding
    produces, naming the field that was wrong.
    """
    try:
        return IntakeRequest.model_validate_json(part)
    except ValidationError as invalid:
        raise Refused(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            boundary.BoundaryError(
                code=BoundaryCode.INVALID_PAYLOAD, detail=boundary.rejected_fields(invalid)
            ),
        ) from None


def _upload(document: UploadFile, limits: IngressLimits) -> Upload:
    """One file part as the ingress takes it, read no further than the ceiling allows."""
    return Upload(
        filename=document.filename or "unnamed",
        media_type=document.content_type or "",
        content=read_bounded(document.file, limits.max_bytes),
    )


def _refused(filename: str | None, verdict: Verdict) -> boundary.Refused:
    """What a failed check means on the wire: its status, its code, and what it found.

    The detail starts with which document and which check, because the checks are the ingress's own
    vocabulary: a caller reading `page_count` knows the document has too many pages, and a caller
    reading the whole line knows which of the documents it sent that was.
    """
    status_code, code = REFUSALS[verdict.check]
    where = f"{filename}: " if filename is not None else ""
    return boundary.Refused(
        status_code,
        boundary.BoundaryError(code=code, detail=f"{where}{verdict.check}: {verdict.detail}"),
    )


def _caller(request: Request) -> str:
    """Who to count this submission against, as far as the transport can say."""
    return CALLER_UNKNOWN if request.client is None else request.client.host


def _declared_length(request: Request) -> int | None:
    """How much the transport says this submission carries, if it says so at all.

    Read as a header rather than measured by reading: what is declared is what a chunked uploader
    does not provide, and a caller that states a number it does not send is answered by the
    per-document ceiling as each document is read.
    """
    declared = request.headers.get("content-length")
    if declared is None or not declared.isdigit():
        return None
    return int(declared)
