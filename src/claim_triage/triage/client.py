"""Our own boundaries, from the caller's side: the run boundary, and the intake one in front of it.

One client for both, because they are one wire: the entry point accepts a claim, the triager accepts
the same claim with the run it is being run as, and both answer with the run's result. The two
operations differ in what they submit rather than in where they point — `submit` hands the triager
a run to carry, `intake` hands the entry point a claim and the documents it arrived with, which are
the bytes only the boundary that screens them has.

What comes back is the wire's models, or the boundary's own vocabulary, raised for the caller to
relay: `Unavailable` when the boundary could not be reached at all, `Refused` when it refused the
call with a documented code. An answer outside that vocabulary is neither — it is a defect, and it
stays one rather than being dressed up as a refusal.

Every call carries the current trace context, so the boundary that answers records its span in the
same trace as its caller's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Self

import httpx2
from opentelemetry import propagate
from pydantic import ValidationError

from claim_triage.boundary import BoundaryCode, BoundaryError, Refused, Unavailable
from claim_triage.contract.models import Readiness
from claim_triage.guards.ingress import Upload
from claim_triage.triage.run import IntakeRequest, RunRequest, RunResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from types import TracebackType

DEFAULT_TIMEOUT_SECONDS: Final = 5.0
"""How long one call may take before the boundary counts as unreachable."""

RUNS: Final = "/runs"
"""The run boundary: the triager's operation, which takes a run that has already been screened."""

CLAIMS: Final = "/claims"
"""The intake boundary: the entry point's operation, which takes a claim and its documents."""

CLAIM_PART: Final = "claim"
DOCUMENTS_PART: Final = "documents"
"""The parts a submission is made of, named once so the client and the boundary cannot disagree."""


class UnexpectedAnswer(RuntimeError):
    """A boundary answered something the internal wire does not cover: a defect, not a refusal."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RunClient:
    """One of our boundaries, over both of the operations between them.

    Each operation carries its own path, so which boundary is being asked is not something a caller
    can get wrong by pointing this at the other one.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Point the client at one of our running boundaries, whichever it is."""
        self._http = httpx2.Client(base_url=base_url, timeout=timeout)

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

    def read_readiness(self) -> Readiness:
        """Ask the boundary whether it can serve right now."""
        return self._request("GET", "/healthz", parse=Readiness.model_validate, expected=200)

    def submit(self, request: RunRequest) -> RunResult:
        """Ask the triager to carry one claim, and answer with the run it answered with."""
        return self._request(
            "POST",
            RUNS,
            parse=RunResult.model_validate,
            body=request.model_dump(mode="json"),
            expected=201,
        )

    def intake(self, claim: IntakeRequest, documents: Sequence[Upload] = ()) -> RunResult:
        """Submit a claim and its documents to the entry point, which screens them before it runs.

        The claim travels as a JSON part and each document as a file part of its own, because
        that is what a boundary has to read before it can answer: the media type and the bytes of
        every document are what the ingress checks, and neither survives being folded into the
        claim.
        """
        return self._request(
            "POST",
            CLAIMS,
            parse=RunResult.model_validate,
            form={CLAIM_PART: claim.model_dump_json()},
            files=[
                (DOCUMENTS_PART, (upload.filename, upload.content, upload.media_type))
                for upload in documents
            ],
            expected=201,
        )

    def _request[T](
        self,
        method: str,
        path: str,
        *,
        parse: Callable[[object], T],
        expected: int,
        body: object | None = None,
        form: Mapping[str, str] | None = None,
        files: Sequence[tuple[str, tuple[str, bytes, str]]] | None = None,
    ) -> T:
        """One call: the answer parsed as the wire describes it, or the failure it named."""
        headers: dict[str, str] = {}
        propagate.inject(carrier=headers)
        try:
            response = self._http.request(
                method, path, json=body, data=form, files=files, headers=headers
            )
        except httpx2.TransportError as unreachable:
            raise Unavailable(f"{method} {path} unreachable: {unreachable}") from None
        document = _document(response)
        if response.status_code != expected:
            refusal = _refusal(document, response.status_code)
            if refusal.code is BoundaryCode.SERVICE_UNAVAILABLE:
                # "Cannot serve this right now" is not a refusal to act on: it is the same thing an
                # unreachable boundary is, and a caller waits it out the same way.
                raise Unavailable(refusal.detail)
            raise Refused(response.status_code, refusal)
        try:
            return parse(document)
        except ValidationError as outside_the_wire:
            raise UnexpectedAnswer(
                f"{method} {path} answered a body the wire does not cover",
                status_code=response.status_code,
            ) from outside_the_wire


def _document(response: httpx2.Response) -> object:
    """The answer as JSON, or the failure that says the boundary answered something else."""
    try:
        document: object = response.json()
    except ValueError as not_json:
        raise UnexpectedAnswer(
            f"{response.request.method} {response.request.url} answered non-JSON",
            status_code=response.status_code,
        ) from not_json
    return document


def _refusal(document: object, status_code: int) -> BoundaryError:
    """The refusal a boundary answered with, in the vocabulary every boundary here answers in."""
    try:
        return BoundaryError.model_validate(document)
    except ValidationError as undocumented:
        raise UnexpectedAnswer(
            f"a refusal outside the boundary's vocabulary, answered {status_code}",
            status_code=status_code,
        ) from undocumented
