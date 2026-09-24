"""Start a deployable: resolve and validate its configuration, report it, and hand over.

A v0.1 deployable is a scaffold: it proves it is wired to the right endpoints by resolving its
configuration from the environment, printing it as one JSON line on stdout, and exiting 0. A
configuration it cannot accept is reported on stderr, naming the environment variable that was
rejected, and exits 2. The graph, HTTP surface and stack that follow start from this handover.
"""

from __future__ import annotations

import json
import sys
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from claim_triage.config import (
    ENV_PREFIX,
    InfrastructureSettings,
    ServiceSettings,
    service_env_prefix,
)
from claim_triage.services.registry import deployable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import ValidationError as PydanticValidationError

EXIT_OK: Final = 0
EXIT_CONFIG_REJECTED: Final = 2
DISTRIBUTION: Final = "claim-triage-agent"


def bootstrap(name: str) -> int:
    """Resolve the named deployable's configuration, report it, and return the process exit code."""
    service = deployable(name)

    try:
        infrastructure = InfrastructureSettings()
    except ValidationError as rejected:
        return _rejected(rejected, {field: f"{ENV_PREFIX}{field.upper()}" for field in _fields()})

    try:
        bind = ServiceSettings(_env_prefix=service_env_prefix(service.name))
    except ValidationError as rejected:
        prefix = service_env_prefix(service.name)
        return _rejected(rejected, {field: f"{prefix}{field.upper()}" for field in _fields()})

    port = service.default_port if bind.port is None else bind.port
    print(json.dumps(_report(service.name, service.scaling_axis, bind.host, port, infrastructure)))
    return EXIT_OK


def _report(
    service: str,
    scaling_axis: str,
    host: str,
    port: int,
    infrastructure: InfrastructureSettings,
) -> dict[str, Any]:
    """The startup report: what this process is, where it binds, and what it is wired to."""
    return {
        "service": service,
        "version": version(DISTRIBUTION),
        "environment": infrastructure.environment,
        "scaling_axis": scaling_axis,
        "host": host,
        "port": port,
        "postgres_dsn": str(infrastructure.postgres_dsn),
        "redis_url": infrastructure.redis_url,
        "qdrant_url": infrastructure.qdrant_url,
        "core_sim_base_url": infrastructure.core_sim_base_url,
        "otel_endpoint": infrastructure.otel_endpoint,
        "langfuse_host": infrastructure.langfuse_host,
    }


def _fields() -> tuple[str, ...]:
    """Every configured field name, so a rejection can name the variable instead of the field."""
    return tuple(InfrastructureSettings.model_fields) + tuple(ServiceSettings.model_fields)


def _rejected(rejected: PydanticValidationError, variables: Mapping[str, str]) -> int:
    for error in rejected.errors():
        field = str(error["loc"][0]) if error["loc"] else "environment"
        print(
            f"configuration rejected: {variables.get(field, field)}: {error['msg']}",
            file=sys.stderr,
        )
    return EXIT_CONFIG_REJECTED
