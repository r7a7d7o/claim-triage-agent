"""Typed configuration: environment in, validated settings out."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX: Final = "CLAIM_TRIAGE_"
DEFAULT_ENV_FILE: Final = ".env"
MODEL_ENV_PREFIX: Final = f"{ENV_PREFIX}MODEL_"
"""The model's configuration shares one prefix across every deployable: `CLAIM_TRIAGE_MODEL_`."""

DEFAULT_MODEL_FIXTURES: Final = Path(__file__).parent / "model" / "fixtures"
"""Where the replay adapter reads its answers from, unless configuration points somewhere else."""

DEFAULT_MODEL_TIMEOUT_SECONDS: Final = 30.0
"""How long one call to an endpoint may take before it counts as unreachable. A model is slower than
the surrounding systems, whose own client waits 5 seconds (`claim_triage.contract.client`)."""


def service_env_prefix(service: str) -> str:
    """The environment prefix one deployable owns, e.g. `CLAIM_TRIAGE_GUARDRAIL_POLICY_`."""
    return f"{ENV_PREFIX}{service.upper().replace('-', '_')}_"


class InfrastructureSettings(BaseSettings):
    """Endpoints and identifiers shared by every deployable.

    The defaults are the local compose stack, so a first run needs no configuration and no
    credentials; the observability endpoints default to unset, which means traces and metrics go to
    structured logs and the pipeline runs with the observability stack down.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX, env_file=DEFAULT_ENV_FILE, extra="ignore"
    )

    environment: str = "local"
    postgres_dsn: SecretStr = SecretStr(
        "postgresql://claim_triage:claim_triage@localhost:5432/claim_triage"
    )
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    core_sim_base_url: str = "http://localhost:8080"
    api_base_url: str = "http://localhost:8000"
    triager_base_url: str = "http://localhost:8001"
    otel_endpoint: str | None = None
    langfuse_host: str | None = None

    @field_validator("otel_endpoint", "langfuse_host", mode="before")
    @classmethod
    def _unset_when_empty(cls, value: object) -> object:
        """An empty value means unset: compose passes its own variables through as empty."""
        return None if value == "" else value


class ServiceSettings(BaseSettings):
    """One deployable's bind address.

    The environment prefix is supplied per deployable at construction (see
    `claim_triage.bootstrap`). `port` unset means the registry's default for that deployable.
    """

    model_config = SettingsConfigDict(env_file=DEFAULT_ENV_FILE, extra="ignore")

    host: str = "127.0.0.1"
    port: int | None = None


class ModelProvider(StrEnum):
    """The model implementations configuration can select.

    The names are the adapters' own (`claim_triage.model.select`), so a deployment says which one it
    runs in the one vocabulary the repository uses for it.
    """

    REPLAY = "replay"
    PROVIDER = "provider"


class ModelSettings(BaseSettings):
    """Which model answers a call, and where it lives when it is not the one in this repository.

    Replay needs nothing configured, and is what an unconfigured environment gets: a run, a test and
    a CI job then need no endpoint and no credential. Naming the provider adapter is what asks for
    an endpoint and a model name, and the key stays optional, because a local endpoint has none. An
    empty value means unset, as the observability endpoints do: compose passes its own variables
    through as empty strings.
    """

    model_config = SettingsConfigDict(
        env_prefix=MODEL_ENV_PREFIX, env_file=DEFAULT_ENV_FILE, extra="ignore"
    )

    provider: ModelProvider = ModelProvider.REPLAY
    base_url: str = ""
    """The endpoint's own root, empty while none is configured, version prefix included."""
    name: str = ""
    """The model the endpoint is asked for, empty while none is configured."""
    api_key: SecretStr | None = None
    fixtures: Path = DEFAULT_MODEL_FIXTURES
    timeout_seconds: float = DEFAULT_MODEL_TIMEOUT_SECONDS

    @model_validator(mode="before")
    @classmethod
    def _an_empty_variable_is_unset(cls, values: Any) -> Any:
        """An empty variable leaves its field at the default.

        Compose passes the variables it declares through as empty strings, and an exported-but-empty
        variable is one nobody set. Read as a value, an empty fixtures directory would be the
        working directory and an empty model name a model nobody named; dropped, what is left is
        what this file says each field is.
        """
        return {field: value for field, value in values.items() if value != ""}

    @model_validator(mode="after")
    def _the_provider_adapter_needs_a_destination(self) -> Self:
        """A selection that cannot answer a call is rejected where it is configured."""
        if self.provider is ModelProvider.REPLAY:
            return self
        unset = [
            variable
            for variable, value in (
                (f"{MODEL_ENV_PREFIX}BASE_URL", self.base_url),
                (f"{MODEL_ENV_PREFIX}NAME", self.name),
            )
            if not value
        ]
        if unset:
            raise ValueError(f"the provider adapter needs {' and '.join(unset)}")
        return self
