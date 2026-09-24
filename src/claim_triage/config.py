"""Typed configuration: environment in, validated settings out."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_PREFIX: Final = "CLAIM_TRIAGE_"
DEFAULT_ENV_FILE: Final = ".env"
MODEL_ENV_PREFIX: Final = f"{ENV_PREFIX}MODEL_"
"""The model's configuration shares one prefix across every deployable: `CLAIM_TRIAGE_MODEL_`."""

DEFAULT_GUARD_MEDIA_TYPES: Final = ("application/pdf", "application/zip")
"""The document classes the ingress takes: a claim document, and a bundle of them. Images arrive
with scanned intake in v0.4, and a content type with no checks behind it is refused rather than
admitted.
"""

DEFAULT_MAX_SUBMISSION_BYTES: Final = 32 * 1024 * 1024
"""Thirty-two mebibytes for one whole submission: four documents at the ceiling, with room for the
multipart framing and the claim. The entry point refuses a submission that declares more than this
without reading it, which is what keeps a hostile upload from being spooled to disk first."""

DEFAULT_MAX_DOCUMENT_BYTES: Final = 8 * 1024 * 1024
"""Eight mebibytes: a claim form with its attachments at scan resolution, well short of what a
careless uploader would send and well short of what a hostile one would like to."""

DEFAULT_MAX_DOCUMENT_PAGES: Final = 40
"""More pages than a single Slovak claim document has, and far fewer than the poistné podmienky."""

DEFAULT_MAX_ARCHIVE_EXPANSION_RATIO: Final = 100.0
"""A bundle that declares a hundred times its own size is a bomb: scanned pages compress at a few
times their size at most, and the contents are never decompressed to find this out."""

DEFAULT_RATE_LIMIT_BURST: Final = 20
"""How many submissions one caller may make back to back. A demonstration, a smoke run and a test
suite all fit inside it; a loop that does not is a loop."""

DEFAULT_RATE_LIMIT_REFILL: Final = 5.0
"""Submissions per second, per caller, once the burst is spent."""

DEFAULT_MODEL_FIXTURES: Final = Path(__file__).parent / "model" / "fixtures"
"""Where the replay adapter reads its answers from, unless configuration points somewhere else."""

DEFAULT_MODEL_TIMEOUT_SECONDS: Final = 30.0
"""How long one call to an endpoint may take before it counts as unreachable. A model is slower than
the surrounding systems, whose own client waits 5 seconds (`claim_triage.contract.client`)."""

GUARD_ENV_PREFIX: Final = f"{ENV_PREFIX}GUARD_"
"""The guard's configuration shares one prefix across every deployable, as the model's does: what a
check allows is a property of the check rather than of whichever deployable runs it."""


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


class GuardSettings(BaseSettings):
    """What the guards allow, as data: the thresholds a deployment can be reviewed for and change.

    The ingress reads these — how large a document may be, how many pages it may hold, what it may
    be and what a bundle of them may expand to — and so does the rate limit in front of the
    boundary. They are configuration rather than constants so that changing a ceiling is a
    deployment change, and so that a compliance owner can read them without reading Python.

    The defaults are chosen for a claim document rather than for the poistné podmienky corpus: the
    conditions are ingested from disk in v0.2, and a document uploaded by a likvidátor is a form and
    its attachments. `max_archive_expansion_ratio` is the ratio a zip bomb is caught by, and the
    upload's own size ceiling is what bounds a legitimate archive, because nothing is decompressed
    at the boundary.

    A threshold of zero, or a negative one, is rejected where it is configured: it would refuse
    every document, which is a mistake rather than a policy. An empty value means unset, as the
    observability endpoints and the model's settings do, because compose passes its own variables
    through as empty strings.
    """

    model_config = SettingsConfigDict(
        env_prefix=GUARD_ENV_PREFIX,
        env_file=DEFAULT_ENV_FILE,
        extra="ignore",
        # An empty variable is unset, as everywhere else in this repository: compose passes its own
        # variables through as empty strings, and a guard threshold nobody set is the default.
        env_ignore_empty=True,
    )

    # `NoDecode` because this one is a list: pydantic-settings would JSON-decode it before any
    # validator could see it, and a value it cannot decode is a `SettingsError` rather than the
    # rejected-configuration exit this repository answers. Decoded here, a bad list is a named
    # rejection like every other bad value.
    media_types: Annotated[frozenset[str], NoDecode] = frozenset(DEFAULT_GUARD_MEDIA_TYPES)
    """What the ingress takes, as a JSON array. It can only narrow the checks that exist: naming a
    type no ticket has implemented refuses every upload of it rather than admitting them."""
    max_submission_bytes: int = Field(default=DEFAULT_MAX_SUBMISSION_BYTES, gt=0)
    max_document_bytes: int = Field(default=DEFAULT_MAX_DOCUMENT_BYTES, gt=0)
    max_document_pages: int = Field(default=DEFAULT_MAX_DOCUMENT_PAGES, gt=0)
    max_archive_expansion_ratio: float = Field(default=DEFAULT_MAX_ARCHIVE_EXPANSION_RATIO, gt=0)
    rate_limit_burst: int = Field(default=DEFAULT_RATE_LIMIT_BURST, gt=0)
    rate_limit_refill_per_second: float = Field(default=DEFAULT_RATE_LIMIT_REFILL, gt=0)

    @field_validator("media_types", mode="before")
    @classmethod
    def _a_json_array_of_media_types(cls, value: object) -> object:
        """The list as the environment states it: a JSON array of media types.

        No separator is safe inside a media type, so the value is JSON. An empty variable never
        arrives here: `env_ignore_empty` drops it before validation, which leaves the field's own
        default — the same answer, one layer up, and nothing to keep alive here.
        """
        if not isinstance(value, str):
            return value
        try:
            stated = json.loads(value)
        except ValueError as malformed:
            raise ValueError(
                f"a JSON array of media types, e.g. ['application/pdf']: {malformed}"
            ) from None
        if not isinstance(stated, list) or not all(isinstance(item, str) for item in stated):
            raise ValueError("a JSON array of media types, e.g. ['application/pdf']")
        return frozenset(stated)


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
        if not isinstance(values, dict):
            return values
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
