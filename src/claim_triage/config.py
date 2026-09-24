"""Typed configuration: environment in, validated settings out."""

from __future__ import annotations

from typing import Final

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX: Final = "CLAIM_TRIAGE_"
DEFAULT_ENV_FILE: Final = ".env"


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
