"""The `core-sim` deployable: stands in for the surrounding systems (see `docs/adr/0001`)."""

from __future__ import annotations

from typing import Final

import uvicorn

from claim_triage.bootstrap import EXIT_OK, resolve_and_report
from claim_triage.core_sim.app import create_app
from claim_triage.core_sim.store import PostgresCoreSimStore

SERVICE: Final = "core-sim"


def main() -> int:
    """Hand over to the ASGI server, over the Postgres store this deployable owns."""
    resolution, exit_code = resolve_and_report(SERVICE)
    if resolution is None:
        return exit_code

    store = PostgresCoreSimStore(resolution.infrastructure.postgres_dsn.get_secret_value())
    uvicorn.run(create_app(store), host=resolution.host, port=resolution.port)
    return EXIT_OK
