"""The walking skeleton: one claim through the graph, recorded, and emitted to the systems.

The order here is deliberate, and it is the ticket's second criterion. The graph decides; the state
change and the audit entry that is its evidence commit together; only then are the surrounding
systems told where the claim got to. Committing first means a decision never exists without its
evidence: if the systems cannot be told, the run is recorded with the status it decided and the
caller is told the emission failed, rather than a status appearing in the system of record that
nothing here can account for. `docs/adr/0005` records that choice and the alternative it rejected.

What the surrounding systems and the store raise is translated here, once, into what our own
boundaries answer: a refusal the systems gave is relayed with its code, and anything unreachable —
the systems, the store, the audit log — is `Unavailable`. Anything else stays a defect rather than
being dressed up as a refusal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from uuid import UUID

from claim_triage import telemetry
from claim_triage.boundary import BoundaryError, Refused, Unavailable
from claim_triage.contract.client import CoreSimClient, CoreSimUnreachable, Refusal
from claim_triage.contract.models import Claim, ClaimStatus, ClaimStatusUpdate, ClaimSubmission
from claim_triage.triage import graph as topology
from claim_triage.triage.audit import Actor, AuditEntryContent
from claim_triage.triage.run import RunRequest, RunResult, TriageRun
from claim_triage.triage.store import TriageStore, TriageStoreUnavailable

if TYPE_CHECKING:
    from opentelemetry.context import Context

    from claim_triage.telemetry import Telemetry
    from claim_triage.triage.graph import ClaimGraph

RUN_SPAN: Final = "claim.run"
"""The span one run is: everything the pipeline does to a claim happens inside it."""


class TriagePipeline:
    """One run of a claim: the graph, the state change with its entry, and the emission."""

    def __init__(
        self,
        *,
        graph: ClaimGraph,
        systems: CoreSimClient,
        store: TriageStore,
        telemetry: Telemetry,
    ) -> None:
        self._graph = graph
        self._systems = systems
        self._store = store
        self._telemetry = telemetry

    def ready(self) -> bool:
        """Whether this pipeline can serve right now: the store it records into is reachable."""
        return self._store.ready()

    def run(self, request: RunRequest, *, context: Context | None = None) -> RunResult:
        """Carry one claim to the end of the graph, and answer with the status that was emitted.

        `context` is the caller's span context, extracted from the request that arrived, so the
        boundary's span and this run's span are one trace rather than two.
        """
        started_at = datetime.now(UTC)
        attributes = telemetry.run_attributes(
            run_id=request.run_id, experiment=request.experiment, variant=request.variant
        )
        with self._telemetry.span(RUN_SPAN, attributes=attributes, context=context) as span:
            try:
                claim = self._taken_in(request)
                span.set_attribute(telemetry.CLAIM_ID, str(claim.claim_id))

                status = self._decided(request)
                span.set_attribute(telemetry.STATUS, str(status))

                run = TriageRun(
                    run_id=request.run_id,
                    claim_id=claim.claim_id,
                    experiment=request.experiment,
                    variant=request.variant,
                    status=status,
                    guard_verdicts=request.guard_verdicts,
                    trace_id=telemetry.current_trace_id() or "",
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                )
                self._recorded(run)
                emitted = self._emitted(request, claim.claim_id, status)
            except CoreSimUnreachable as unreachable:
                raise Unavailable(
                    f"the surrounding systems are unreachable: {unreachable}"
                ) from None
            except Refusal as refusal:
                raise Refused(
                    refusal.status_code, BoundaryError.relayed(refusal.response)
                ) from refusal
            except TriageStoreUnavailable as unavailable:
                raise Unavailable(str(unavailable)) from None

            telemetry.log_event(
                "run.completed",
                run_id=str(run.run_id),
                claim_id=str(run.claim_id),
                experiment=run.experiment,
                variant=run.variant,
                status=str(emitted.status),
                duration_ms=round((run.completed_at - run.started_at).total_seconds() * 1000),
            )

        return RunResult(
            run_id=run.run_id,
            claim_id=run.claim_id,
            status=emitted.status,
            experiment=run.experiment,
            variant=run.variant,
            trace_id=run.trace_id,
        )

    def _taken_in(self, request: RunRequest) -> Claim:
        """Hand the claim to the surrounding systems, which are the system of record for it."""
        return self._systems.create_claim(
            submission(request), idempotency_key=f"{request.run_id}/claim"
        )

    def _decided(self, request: RunRequest) -> ClaimStatus:
        """Run the graph to its end and read where it left the claim."""
        final = self._graph.invoke(
            {
                "claim": submission(request),
                "run_id": request.run_id,
                "experiment": request.experiment,
                "variant": request.variant,
            }
        )
        # Read as the contract's own enum rather than trusted as one: a node that ends without a
        # status, or with something the contract does not name, is a defect that fails loudly here
        # rather than being recorded as a state the systems cannot hold.
        return ClaimStatus(str(final.get("status", "")))

    def _recorded(self, run: TriageRun) -> None:
        """Write the state change and its audit entry in one transaction, or neither."""
        content = AuditEntryContent(
            claim_id=run.claim_id,
            run_id=run.run_id,
            node=topology.NODE,
            actor=Actor.AGENT,
            status=run.status,
            guard_verdicts=run.guard_verdicts,
            experiment=run.experiment,
            variant=run.variant,
            trace_id=run.trace_id,
            recorded_at=run.completed_at,
        )
        with self._store.transaction() as transaction:
            transaction.record_run(run)
            transaction.append_entry(content)

    def _emitted(self, request: RunRequest, claim_id: UUID, status: ClaimStatus) -> Claim:
        """Tell the systems where the claim got to, and answer with what they now hold."""
        return self._systems.record_claim_status(
            claim_id,
            ClaimStatusUpdate(status=status),
            idempotency_key=f"{request.run_id}/status",
        )


def submission(request: RunRequest) -> ClaimSubmission:
    """The claim itself, as the contract takes it: the run's arms are ours, not the systems'."""
    return ClaimSubmission(
        policy_number=request.policy_number,
        incident_date=request.incident_date,
        claim_amount_eur=request.claim_amount_eur,
    )
