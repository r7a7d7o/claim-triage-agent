"""The fixtures more than one test module needs: an app served for real, and a wired stack.

Driving a deployed app through a socket rather than calling the ASGI callable in process is what
makes a test cover the parts a hop between two deployables actually crosses — serialisation, status
codes, and the headers a trace context travels in. The two helpers this builds on live in `support`,
which a test module can import directly, and so does the `Skeleton` the fixture below serves.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
import uvicorn
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from claim_triage import telemetry
from claim_triage.api.surface import create_app as create_api
from claim_triage.contract.client import CoreSimClient
from claim_triage.core_sim.app import create_app as create_systems
from claim_triage.triage import graph
from claim_triage.triage.client import RunClient
from claim_triage.triage.pipeline import TriagePipeline
from claim_triage.triage.surface import create_app as create_triager
from support import (
    STARTUP_DEADLINE_SECONDS,
    TEST_GUARD,
    InMemoryCoreSim,
    InMemoryTriageStore,
    Skeleton,
    free_port,
    wait_until_listening,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from fastapi import FastAPI

    from claim_triage.config import GuardSettings
    from claim_triage.telemetry import Telemetry


@pytest.fixture
def serve() -> Iterator[Callable[[FastAPI], str]]:
    """Serve apps for real on loopback ports, and stop every one of them afterwards."""
    served: list[tuple[uvicorn.Server, threading.Thread]] = []

    def _serve(app: FastAPI) -> str:
        port = free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        served.append((server, thread))
        wait_until_listening(port)
        return f"http://127.0.0.1:{port}"

    yield _serve

    for server, _ in served:
        server.should_exit = True
    for _, thread in served:
        thread.join(STARTUP_DEADLINE_SECONDS)


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
        guard: GuardSettings | None = None,
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
        api_url = serve(
            create_api(runs, telemetry=entrypoint, guard=TEST_GUARD if guard is None else guard)
        )
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
