"""The `api` deployable: the stateless entry point (see `docs/adr/0001`)."""

from __future__ import annotations

from typing import Final

import uvicorn

from claim_triage.api.surface import create_app
from claim_triage.bootstrap import EXIT_OK, resolve_and_report
from claim_triage.config import InfrastructureSettings
from claim_triage.telemetry import configure, configure_logging
from claim_triage.triage.client import RunClient

SERVICE: Final = "api"


def main() -> int:
    """Hand over to the ASGI server, over the run boundary this deployable forwards to."""
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
    runs = RunClient(infrastructure.triager_base_url)
    try:
        uvicorn.run(
            create_app(runs, telemetry=traces, guard=resolution.guard),
            host=resolution.host,
            port=resolution.port,
        )
    finally:
        runs.close()
        traces.shutdown()
    return EXIT_OK
