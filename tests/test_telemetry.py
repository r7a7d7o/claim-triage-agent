"""The observability seam: what a span carries, and what one structured log line is.

These are the tracing and log-fallback halves of the walking skeleton's evidence, tested where they
can be tested without a collector: spans go to an in-memory exporter instead of over OTLP, and the
log line is read back through `caplog` and the mapping the handler writes. The container stack runs
exactly this code with `CLAIM_TRIAGE_OTEL_ENDPOINT` set, and without it.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from claim_triage import telemetry
from claim_triage.telemetry import CLAIM_ID, EXPERIMENT, RUN_ID, STATUS, VARIANT
from support import free_port

if TYPE_CHECKING:
    from collections.abc import Iterator

STARTUP_TIMEOUT_SECONDS: float = 5.0
"""How long the stand-in collector may take to stop before the test fails."""

RUN: UUID = UUID("3f2a1c48-6b3d-4e7a-9f21-0c5d8e4b7a96")
CLAIM: UUID = UUID("9d7c5b21-4e8f-4a36-b0d2-71f3c6e95a48")
"""Fixed identifiers, so a failure names what it expected instead of two random UUIDs."""


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def traces(exporter: InMemorySpanExporter) -> Iterator[telemetry.Telemetry]:
    """Telemetry over an exporter that keeps spans, shut down so nothing outlives the test."""
    configured = telemetry.configure("triager", environment="test", exporter=exporter)
    yield configured
    configured.shutdown()


def test_a_span_carries_the_run_claim_experiment_and_variant(
    traces: telemetry.Telemetry, exporter: InMemorySpanExporter
) -> None:
    with traces.span(
        "claim.run",
        attributes={
            **telemetry.run_attributes(
                run_id=RUN, claim_id=CLAIM, experiment="baseline", variant="replay"
            ),
            STATUS: "triaged",
        },
    ):
        pass

    traces.shutdown()

    (span,) = exporter.get_finished_spans()
    assert span.attributes == {
        RUN_ID: str(RUN),
        CLAIM_ID: str(CLAIM),
        EXPERIMENT: "baseline",
        VARIANT: "replay",
        STATUS: "triaged",
    }


def test_the_span_of_a_run_whose_claim_is_not_known_yet_carries_what_is_known(
    traces: telemetry.Telemetry, exporter: InMemorySpanExporter
) -> None:
    """The boundary mints the run before the pipeline mints the claim, so the claim is optional."""
    with traces.span(
        "api.claim",
        attributes=telemetry.run_attributes(run_id=RUN, experiment="baseline", variant="replay"),
    ):
        pass

    traces.shutdown()

    (span,) = exporter.get_finished_spans()
    assert span.attributes is not None
    assert CLAIM_ID not in span.attributes
    assert span.attributes[RUN_ID] == str(RUN)


def test_a_span_has_a_trace_identifier_that_logs_can_carry(traces: telemetry.Telemetry) -> None:
    assert telemetry.current_trace_id() is None

    with traces.span("claim.run"):
        trace_id = telemetry.current_trace_id()

    assert trace_id is not None
    assert len(trace_id) == 32
    assert int(trace_id, 16) > 0
    assert telemetry.current_trace_id() is None


def test_a_dead_endpoint_leaves_the_run_alone() -> None:
    """The stack being down is a configuration, not a failure: the run still traces, and stops."""
    dead = f"http://127.0.0.1:{free_port()}"
    configured = telemetry.configure("triager", environment="test", endpoint=dead, otlp_timeout=0.2)

    with configured.span(
        "claim.run",
        attributes=telemetry.run_attributes(
            run_id=RUN, claim_id=CLAIM, experiment="baseline", variant="replay"
        ),
    ):
        trace_id = telemetry.current_trace_id()

    configured.shutdown()

    assert trace_id is not None


def test_a_logged_event_is_one_json_object_carrying_the_runs_identifiers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="claim_triage"):
        telemetry.log_event(
            "run.completed",
            run_id=str(RUN),
            claim_id=str(CLAIM),
            experiment="baseline",
            variant="replay",
            status="triaged",
        )

    (record,) = [record for record in caplog.records if record.name == "claim_triage"]
    line = telemetry.log_line(record)

    assert json.loads(json.dumps(line)) == line  # one JSON object, nothing else in the line
    assert line["event"] == "run.completed"
    assert line["level"] == "INFO"
    assert line["run_id"] == str(RUN)
    assert line["claim_id"] == str(CLAIM)
    assert line["experiment"] == "baseline"
    assert line["variant"] == "replay"
    assert line["status"] == "triaged"
    assert line["trace_id"] is None


def test_a_logged_event_inside_a_span_carries_the_trace_identifier(
    traces: telemetry.Telemetry, caplog: pytest.LogCaptureFixture
) -> None:
    with traces.span("claim.run"), caplog.at_level(logging.INFO, logger="claim_triage"):
        telemetry.log_event("run.completed", run_id=str(RUN))

    (record,) = [record for record in caplog.records if record.name == "claim_triage"]
    assert telemetry.log_line(record)["trace_id"] is not None


class Receiver(BaseHTTPRequestHandler):
    """A stand-in for the collector: it keeps the path and the body of everything posted to it."""

    paths: ClassVar[list[str]] = []
    bodies: ClassVar[list[bytes]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        type(self).bodies.append(self.rfile.read(length))
        type(self).paths.append(self.path)
        self.send_response(200)
        self.send_header("content-type", "application/x-protobuf")
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Say nothing: what was received is asserted on, not logged."""


@pytest.fixture
def collector() -> Iterator[str]:
    """A receiver on a loopback port, and the endpoint to point a deployable at it with."""
    Receiver.paths = []
    Receiver.bodies = []
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    thread.join(STARTUP_TIMEOUT_SECONDS)


def test_a_span_is_exported_to_the_collector_over_http(collector: str) -> None:
    """The whole export path, over a socket: an endpoint that receives what the run was about."""
    configured = telemetry.configure("triager", environment="test", endpoint=collector)

    with configured.span(
        "claim.run",
        attributes=telemetry.run_attributes(
            run_id=RUN, claim_id=CLAIM, experiment="baseline", variant="replay"
        ),
    ):
        pass
    configured.shutdown()

    assert Receiver.paths == ["/v1/traces"]
    assert len(Receiver.bodies) == 1
    exported = Receiver.bodies[0]
    for attribute in (RUN_ID, CLAIM_ID, EXPERIMENT, VARIANT):
        assert attribute.encode() in exported


def test_a_log_line_is_the_record_it_was_made_from() -> None:
    record = logging.LogRecord(
        name="claim_triage",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="run.completed",
        args=(),
        exc_info=None,
    )
    record.created = datetime(2026, 9, 24, 12, 0, tzinfo=UTC).timestamp()
    record.fields = {"run_id": str(RUN)}

    assert telemetry.log_line(record) == {
        "timestamp": "2026-09-24T12:00:00.000Z",
        "level": "INFO",
        "logger": "claim_triage",
        "event": "run.completed",
        "run_id": str(RUN),
    }
