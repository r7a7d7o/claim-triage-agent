"""Start a deployable: resolve and validate its configuration, report it, and hand over.

Every deployable resolves its configuration from the environment before it does anything else, and
reports it as one JSON line on stdout. A configuration it cannot accept is reported on stderr,
naming the environment variable that was rejected, and exits 2. `resolve_and_report` is the
handover: a deployable whose surface has not landed yet returns the exit code straight away (see
`tests/test_deployables.py`), while a deployable that serves something — `core-sim` from ticket 02,
`triager` and `api` from ticket 04 — keeps the report and starts its server.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from claim_triage import DISTRIBUTION
from claim_triage.config import (
    ENV_PREFIX,
    InfrastructureSettings,
    ServiceSettings,
    service_env_prefix,
)
from claim_triage.services.registry import Deployable, deployable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

EXIT_OK: Final = 0
EXIT_CONFIG_REJECTED: Final = 2


class ConfigurationRejected(Exception):
    """An environment this deployable cannot accept, with the variable that set each bad field."""

    def __init__(self, errors: Sequence[Mapping[str, Any]], variables: Mapping[str, str]) -> None:
        super().__init__("configuration rejected")
        self._errors = errors
        self._variables = variables

    def lines(self) -> tuple[str, ...]:
        """One line per rejected field, naming the environment variable that set it."""
        return tuple(
            f"configuration rejected: {self._variable(error)}: {error['msg']}"
            for error in self._errors
        )

    def _variable(self, error: Mapping[str, Any]) -> str:
        field = str(error["loc"][0]) if error["loc"] else "environment"
        return self._variables.get(field, field)


@dataclass(frozen=True, slots=True)
class Resolution:
    """One deployable's resolved configuration: what it is, where it binds, what it is wired to."""

    service: Deployable
    host: str
    port: int
    infrastructure: InfrastructureSettings

    def report(self) -> dict[str, Any]:
        """The startup report: what this process is, where it binds, and what it is wired to."""
        return {
            "service": self.service.name,
            "version": version(DISTRIBUTION),
            "environment": self.infrastructure.environment,
            "scaling_axis": self.service.scaling_axis,
            "host": self.host,
            "port": self.port,
            "postgres_dsn": str(self.infrastructure.postgres_dsn),
            "redis_url": self.infrastructure.redis_url,
            "qdrant_url": self.infrastructure.qdrant_url,
            "core_sim_base_url": self.infrastructure.core_sim_base_url,
            "api_base_url": self.infrastructure.api_base_url,
            "triager_base_url": self.infrastructure.triager_base_url,
            "otel_endpoint": self.infrastructure.otel_endpoint,
            "langfuse_host": self.infrastructure.langfuse_host,
        }


def resolve(name: str) -> Resolution:
    """Resolve the named deployable's configuration, or raise `ConfigurationRejected`."""
    service = deployable(name)
    prefix = service_env_prefix(service.name)

    try:
        infrastructure = InfrastructureSettings()
        bind = ServiceSettings(_env_prefix=prefix)
    except ValidationError as rejected:
        raise ConfigurationRejected(rejected.errors(), _environment_variables(prefix)) from None

    return Resolution(
        service=service,
        host=bind.host,
        port=service.default_port if bind.port is None else bind.port,
        infrastructure=infrastructure,
    )


def resolve_and_report(name: str) -> tuple[Resolution | None, int]:
    """Resolve the named deployable, and write its startup report or its rejection to the streams.

    Returns the resolution and `EXIT_OK`, or `(None, EXIT_CONFIG_REJECTED)` once the rejection has
    been written to stderr.
    """
    try:
        resolution = resolve(name)
    except ConfigurationRejected as rejected:
        print("\n".join(rejected.lines()), file=sys.stderr)
        return None, EXIT_CONFIG_REJECTED

    print(json.dumps(resolution.report()), flush=True)
    return resolution, EXIT_OK


def bootstrap(name: str) -> int:
    """Report the named deployable's configuration and return its process exit code."""
    return resolve_and_report(name)[1]


def _environment_variables(prefix: str) -> dict[str, str]:
    """Map every configured field to the variable that sets it, for naming it in a rejection."""
    return {
        **{field: f"{ENV_PREFIX}{field.upper()}" for field in InfrastructureSettings.model_fields},
        **{field: f"{prefix}{field.upper()}" for field in ServiceSettings.model_fields},
    }
