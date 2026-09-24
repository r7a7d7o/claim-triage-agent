"""The `triager` deployable: holds the graph and checkpointing (see `docs/adr/0001`).

It serves the run boundary over the two tables it owns, and reaches the surrounding systems over the
contract's client. What it is wired to is resolved from the environment before anything else
happens, and reported as one line, so a misconfigured deployable fails before it binds a port.
"""

from __future__ import annotations

from typing import Final

import uvicorn

from claim_triage.bootstrap import EXIT_OK, resolve_and_report
from claim_triage.config import InfrastructureSettings
from claim_triage.contract.client import CoreSimClient
from claim_triage.telemetry import configure, configure_logging
from claim_triage.triage.graph import build_graph
from claim_triage.triage.pipeline import TriagePipeline
from claim_triage.triage.store import PostgresTriageStore
from claim_triage.triage.surface import create_app

SERVICE: Final = "triager"


def main() -> int:
    """Hand over to the ASGI server, over the store this deployable owns."""
    resolution, exit_code = resolve_and_report(SERVICE)
    if resolution is None:
        return exit_code

    infrastructure: InfrastructureSettings = resolution.infrastructure
    configure_logging()
    traces = configure(
        SERVICE,
        environment=infrastructure.environment,
        endpoint=infrastructure.otel_endpoint,
    )
    pipeline = TriagePipeline(
        graph=build_graph(),
        systems=CoreSimClient(infrastructure.core_sim_base_url),
        store=PostgresTriageStore(infrastructure.postgres_dsn.get_secret_value()),
        telemetry=traces,
    )
    try:
        uvicorn.run(create_app(pipeline), host=resolution.host, port=resolution.port)
    finally:
        traces.shutdown()
    return EXIT_OK
