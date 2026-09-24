"""The walking skeleton, from the ASGI boundary a claim is posted at to the audit entry it leaves.

This is the seam the specification names for the API boundary, driven end to end: a claim goes in at
the entry point, the graph carries it, the surrounding systems are told where it got to, the state
change and its audit entry are written together, and the trace that came out carries the run, claim,
experiment and variant. What the ticket asks for as separate criteria are separate tests here — the
emitted status, the audit entry that exists only with its state change, the trace, and the run that
completes with the observability stack stopped.

The stores are doubles and the apps are served for real on loopback ports, so the sockets, the
serialisation and the headers a hop between two deployables crosses are all covered, and no
database, container or credential is involved. The same path runs in the container job over the
real stores; `tests/test_audit_transaction.py` holds the store's own promise to a real Postgres.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx2
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from claim_triage import telemetry
from claim_triage.api.surface import create_app as create_api
from claim_triage.contract.client import CoreSimClient
from claim_triage.contract.models import ClaimStatus
from claim_triage.core_sim.app import create_app as create_systems
from claim_triage.telemetry import CLAIM_ID, EXPERIMENT, RUN_ID, STATUS, VARIANT
from claim_triage.triage import audit, graph
from claim_triage.triage.audit import Actor, AuditEntry, AuditEntryContent
from claim_triage.triage.client import RunClient
from claim_triage.triage.pipeline import TriagePipeline
from claim_triage.triage.run import RunResult, TriageRun
from claim_triage.triage.store import TriageStoreUnavailable
from claim_triage.triage.surface import create_app as create_triager
from support import InMemoryCoreSim, InMemoryTriageStore, free_port

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from fastapi import FastAPI
    from opentelemetry.sdk.trace import ReadableSpan

    from claim_triage.telemetry import Telemetry

POLICY = "SIM-2026-0001"
"""A policy the systems hold: a claim against any other is a documented refusal."""

CLAIM: dict[str, object] = {
    "policy_number": POLICY,
    "incident_date": "2026-03-14",
    "claim_amount_eur": "1840.50",
}


@dataclass(frozen=True, slots=True)
class Skeleton:
    """A walking skeleton, wired the way the stack wires it, and the pieces to assert on."""

    api_url: str
    systems: InMemoryCoreSim
    triage: InMemoryTriageStore
    exporter: InMemorySpanExporter
    _traces: tuple[Telemetry, ...] = field(default_factory=tuple)

    def submit(self, **claim: object) -> httpx2.Response:
        """Post a claim at the entry point, and answer with whatever the boundary answered."""
        with httpx2.Client(base_url=self.api_url) as client:
            return client.post("/claims", json={**CLAIM, **claim})

    def submit_ok(self, **claim: object) -> RunResult:
        """Post a claim the skeleton is expected to carry, and parse the run it answered with."""
        response = self.submit(**claim)
        assert response.status_code == 201, response.text
        return RunResult.model_validate(response.json())

    def readiness(self) -> httpx2.Response:
        """What the entry point answers when asked whether it can serve right now."""
        with httpx2.Client(base_url=self.api_url) as client:
            return client.get("/healthz")

    def drained(self) -> tuple[ReadableSpan, ...]:
        """Every span the run produced: they are batched, so this is what flushes them out."""
        for traces in self._traces:
            traces.flush()
        return tuple(self.exporter.get_finished_spans())


@pytest.fixture
def skeleton(serve: Callable[[FastAPI], str]) -> Iterator[Callable[..., Skeleton]]:
    """Build skeletons whose pieces a test can replace, and stop their telemetry afterwards."""
    started: list[Telemetry] = []

    def _skeleton(
        *,
        triage: InMemoryTriageStore | None = None,
        systems: InMemoryCoreSim | None = None,
        core_sim_url: str | None = None,
        endpoint: str | None = None,
        otlp_timeout: float = 5.0,
    ) -> Skeleton:
        exporter = InMemorySpanExporter()
        # An endpoint means the real exporter, so nothing is kept in memory: that is what the
        # stack-down case is about.
        collected = None if endpoint is not None else exporter
        systems = InMemoryCoreSim() if systems is None else systems
        triage = InMemoryTriageStore() if triage is None else triage
        url = serve(create_systems(systems)) if core_sim_url is None else core_sim_url
        configured = telemetry.configure(
            "triager",
            environment="test",
            exporter=collected,
            endpoint=endpoint,
            otlp_timeout=otlp_timeout,
        )
        entrypoint = telemetry.configure(
            "api",
            environment="test",
            exporter=collected,
            endpoint=endpoint,
            otlp_timeout=otlp_timeout,
        )
        pipeline = TriagePipeline(
            graph=graph.build_graph(),
            systems=CoreSimClient(url),
            store=triage,
            telemetry=configured,
        )
        runs = RunClient(serve(create_triager(pipeline)))
        api_url = serve(create_api(runs, telemetry=entrypoint))
        started.extend((configured, entrypoint))
        return Skeleton(
            api_url=api_url,
            systems=systems,
            triage=triage,
            exporter=exporter,
            _traces=(configured, entrypoint),
        )

    yield _skeleton

    for traces in started:
        traces.shutdown()


def test_a_claim_posted_at_the_boundary_reaches_the_end_of_the_graph(
    skeleton: Callable[..., Skeleton],
) -> None:
    wired = skeleton()

    run = wired.submit_ok()

    assert run.status is ClaimStatus.TRIAGED
    assert run.experiment == "baseline"
    assert run.variant == "replay"

    (claim,) = wired.systems.claims
    assert claim.claim_id == run.claim_id
    assert claim.status is ClaimStatus.TRIAGED
    assert [transition.status for transition in claim.status_history] == [
        ClaimStatus.RECEIVED,
        ClaimStatus.TRIAGED,
    ]

    (recorded,) = wired.triage.runs
    assert recorded == TriageRun(
        run_id=run.run_id,
        claim_id=claim.claim_id,
        experiment="baseline",
        variant="replay",
        status=ClaimStatus.TRIAGED,
        trace_id=run.trace_id,
        started_at=recorded.started_at,
        completed_at=recorded.completed_at,
    )

    (entry,) = wired.triage.entries()
    assert entry.claim_id == claim.claim_id
    assert entry.run_id == run.run_id
    assert entry.node == graph.NODE
    assert entry.actor is Actor.AGENT
    assert entry.status is ClaimStatus.TRIAGED
    assert entry.experiment == "baseline"
    assert entry.variant == "replay"
    assert entry.trace_id == run.trace_id
    assert audit.verify(wired.triage.entries()).ok


def test_the_trace_of_a_run_carries_the_run_claim_experiment_and_variant(
    skeleton: Callable[..., Skeleton],
) -> None:
    wired = skeleton()

    run = wired.submit_ok()

    spans = {span.name: span for span in wired.drained()}
    assert set(spans) == {"api.claim", "claim.run"}

    assert spans["claim.run"].attributes == {
        RUN_ID: str(run.run_id),
        CLAIM_ID: str(run.claim_id),
        EXPERIMENT: "baseline",
        VARIANT: "replay",
        STATUS: str(ClaimStatus.TRIAGED),
    }
    assert spans["api.claim"].attributes is not None
    assert spans["api.claim"].attributes[RUN_ID] == str(run.run_id)
    assert spans["api.claim"].attributes[VARIANT] == "replay"

    one_trace = {span.context.trace_id for span in spans.values()}
    assert len(one_trace) == 1
    assert format(next(iter(one_trace)), "032x") == run.trace_id


class _RefusingStore(InMemoryTriageStore):
    """A store that cannot do one of the two writes a run ends with, and nothing else wrong."""

    def __init__(self, *, refusing: str) -> None:
        super().__init__()
        self._refusing = refusing

    def _record(self, run: TriageRun) -> None:
        if self._refusing == "the state change":
            raise TriageStoreUnavailable("the run could not be recorded")
        super()._record(run)

    def _append(self, content: AuditEntryContent) -> AuditEntry:
        if self._refusing == "the audit entry":
            raise TriageStoreUnavailable("the audit entry could not be appended")
        return super()._append(content)


@pytest.mark.parametrize("refusing", ["the state change", "the audit entry"])
def test_a_state_change_and_its_audit_entry_are_either_both_there_or_neither(
    skeleton: Callable[..., Skeleton], refusing: str
) -> None:
    """A failure between the two writes: the claim is not carried, and nothing is left behind."""
    store = _RefusingStore(refusing=refusing)
    wired = skeleton(triage=store)

    response = wired.submit()

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert store.runs == ()
    assert store.entries() == ()


def test_a_run_completes_and_logs_when_no_tracing_backend_is_configured(
    skeleton: Callable[..., Skeleton], caplog: pytest.LogCaptureFixture
) -> None:
    wired = skeleton()

    with caplog.at_level(logging.INFO, logger="claim_triage"):
        run = wired.submit_ok()

    assert run.status is ClaimStatus.TRIAGED

    (record,) = [record for record in caplog.records if record.name == "claim_triage"]
    line = telemetry.log_line(record)
    assert line["event"] == "run.completed"
    assert line["run_id"] == str(run.run_id)
    assert line["claim_id"] == str(run.claim_id)
    assert line["experiment"] == "baseline"
    assert line["variant"] == "replay"
    assert line["trace_id"] == run.trace_id


def test_a_run_completes_on_structured_logs_when_the_tracing_backend_is_stopped(
    skeleton: Callable[..., Skeleton], caplog: pytest.LogCaptureFixture
) -> None:
    """A collector that is down is not the run's problem: it reaches the end, on its logs alone."""
    dead = f"http://127.0.0.1:{free_port()}"
    wired = skeleton(endpoint=dead, otlp_timeout=0.2)

    with caplog.at_level(logging.INFO, logger="claim_triage"):
        run = wired.submit_ok()

    assert run.status is ClaimStatus.TRIAGED
    assert len(run.trace_id) == 32

    (record,) = [record for record in caplog.records if record.name == "claim_triage"]
    assert telemetry.log_line(record)["trace_id"] == run.trace_id


def test_a_claim_naming_an_unknown_policy_is_refused_with_the_systems_own_code(
    skeleton: Callable[..., Skeleton],
) -> None:
    wired = skeleton()

    response = wired.submit(policy_number="SIM-2026-9999")

    assert response.status_code == 404
    assert response.json()["code"] == "policy_not_found"
    assert wired.systems.claims == ()
    assert wired.triage.runs == ()
    assert wired.triage.entries() == ()


def test_a_boundary_whose_surrounding_systems_are_unreachable_answers_service_unavailable(
    skeleton: Callable[..., Skeleton],
) -> None:
    wired = skeleton(core_sim_url=f"http://127.0.0.1:{free_port()}")

    response = wired.submit()

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert wired.triage.runs == ()
    assert wired.triage.entries() == ()


@pytest.mark.parametrize(
    ("field", "value", "named"),
    [
        ("claim_amount_eur", "-1.00", "claim_amount_eur"),
        ("claim_amount_eur", "0", "claim_amount_eur"),
        ("incident_date", "not-a-date", "incident_date"),
        ("policy_number", "", "policy_number"),
    ],
)
def test_a_claim_outside_the_wire_is_refused_naming_the_field_and_runs_nothing(
    skeleton: Callable[..., Skeleton], field: str, value: str, named: str
) -> None:
    wired = skeleton()

    response = wired.submit(**{field: value})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_payload"
    assert named in response.json()["detail"]
    assert wired.systems.claims == ()
    assert wired.triage.runs == ()
    assert wired.triage.entries() == ()


def test_readiness_is_answered_at_the_boundary_and_relayed_from_the_store(
    skeleton: Callable[..., Skeleton],
) -> None:
    assert skeleton().readiness().status_code == 200

    unreachable = skeleton(triage=InMemoryTriageStore(ready=False))

    assert unreachable.readiness().status_code == 503
    assert unreachable.readiness().json()["code"] == "service_unavailable"
