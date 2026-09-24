"""The observability seam: OpenTelemetry traces, and the structured logs that stand in for them.

OpenTelemetry is the seam rather than a vendor SDK, so the backend can be swapped without touching
application code (a collector fans the same spans out to whatever carries them today). Every
deployable configures one `Telemetry`: spans carry the pipeline's own identifiers — run, claim,
experiment, variant — which is what lets online behaviour be joined to offline evaluation later.

The stack is optional. With no endpoint configured nothing is exported and the run is unchanged; the
same run also completes when the endpoint is set and the collector is down, because export is
batched and off the request path. What always happens is one structured log line per event, carrying
the same identifiers and the trace identifier it belongs to, so a stack-down run is still
reconstructable from the logs alone.
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from typing import TYPE_CHECKING, Final

from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.semconv.attributes.deployment_attributes import DEPLOYMENT_ENVIRONMENT_NAME
from opentelemetry.semconv.attributes.service_attributes import SERVICE_NAME, SERVICE_VERSION
from opentelemetry.trace import Tracer, get_current_span

from claim_triage import DISTRIBUTION

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from opentelemetry.trace import Span

LOGGER: Final = "claim_triage"
"""The one logger every deployable's events go through, so a log line is found in one place."""

RUN_ID: Final = "claim_triage.run_id"
CLAIM_ID: Final = "claim_triage.claim_id"
EXPERIMENT: Final = "claim_triage.experiment"
VARIANT: Final = "claim_triage.variant"
STATUS: Final = "claim_triage.status"
"""The pipeline's own span attributes, beyond the semantic conventions: what a run is about."""

TRACES_PATH: Final = "v1/traces"
"""Where an OTLP/HTTP receiver serves spans: the path a base endpoint is completed with."""

JSON_HANDLER: Final = "_claim_triage_json_lines"
"""Marks the handler `configure_logging` installed, so configuring twice does not log twice."""

DEFAULT_OTLP_TIMEOUT_SECONDS: Final = 5.0
"""How long an export attempt may take before the collector counts as down."""


@dataclass(frozen=True, slots=True)
class Telemetry:
    """One deployable's tracing: a tracer that carries its service name, and how to drain it."""

    service: str
    provider: TracerProvider

    def tracer(self) -> Tracer:
        """A tracer that records this deployable's spans."""
        return self.provider.get_tracer(self.service)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, str] | None = None,
        context: Context | None = None,
    ) -> Iterator[Span]:
        """One span, current for the duration so nested spans and log lines join it.

        `context` is the caller's span context, extracted from the request that arrived: passing it
        here is what puts two deployables' spans in one trace rather than in two.
        """
        with self.tracer().start_as_current_span(name, context=context) as span:
            for key, value in (attributes or {}).items():
                span.set_attribute(key, value)
            yield span

    def flush(self) -> None:
        """Export what is batched and carry on: stops nothing, so a later span still gets out."""
        self.provider.force_flush()

    def shutdown(self) -> None:
        """Drain what is batched and stop exporting: what a deployable does before it exits."""
        self.flush()
        self.provider.shutdown()


def configure(
    service: str,
    *,
    environment: str,
    endpoint: str | None = None,
    exporter: SpanExporter | None = None,
    otlp_timeout: float = DEFAULT_OTLP_TIMEOUT_SECONDS,
) -> Telemetry:
    """Build one deployable's telemetry.

    `endpoint` unset, and no `exporter` given, means nothing is exported — the run then lives in the
    structured logs alone. An `exporter` is what the tests pass instead of a collector.
    """
    provider = TracerProvider(
        resource=Resource.create(
            {
                SERVICE_NAME: service,
                SERVICE_VERSION: version(DISTRIBUTION),
                DEPLOYMENT_ENVIRONMENT_NAME: environment,
            }
        )
    )
    if exporter is None and endpoint is not None:
        exporter = OTLPSpanExporter(endpoint=_traces_url(endpoint), timeout=otlp_timeout)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return Telemetry(service=service, provider=provider)


def _traces_url(endpoint: str) -> str:
    """The URL spans are posted to, from the endpoint an operator names.

    The exporter posts to whatever it is given, verbatim, so a collector's base URL alone would be
    posted to its root and answered 404 — which is a run that silently has no trace. A base URL is
    completed with the trace path, and an endpoint that already names it is left as it is, so both
    spellings mean the same thing.
    """
    base = endpoint.rstrip("/")
    return base if base.endswith(TRACES_PATH) else f"{base}/{TRACES_PATH}"


def run_attributes(
    *,
    run_id: object,
    experiment: str,
    variant: str,
    claim_id: object | None = None,
) -> dict[str, str]:
    """What a span about a run carries: the identifiers that join a trace to a run and its arms.

    The claim is optional because the boundary mints the run before the pipeline has created the
    claim in the surrounding systems. What the run turned out to be — the status it emitted — is not
    known when the span starts, so a stage that knows it sets it on the span itself.
    """
    attributes = {RUN_ID: str(run_id), EXPERIMENT: experiment, VARIANT: variant}
    if claim_id is not None:
        attributes[CLAIM_ID] = str(claim_id)
    return attributes


def current_trace_id() -> str | None:
    """The trace the current span belongs to, as a trace identifier, or None outside a span."""
    context = get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def log_event(event: str, **fields: object) -> None:
    """One structured line: what happened, and the identifiers it can be joined to a trace by."""
    logging.getLogger(LOGGER).info(
        event, extra={"fields": {**fields, "trace_id": current_trace_id()}}
    )


def log_line(record: logging.LogRecord) -> dict[str, object]:
    """The one JSON object a record becomes: what it says, and everything it carries."""
    fields: object = getattr(record, "fields", {})
    return {
        "timestamp": _timestamp(record.created),
        "level": record.levelname,
        "logger": record.name,
        "event": record.getMessage(),
        **(fields if isinstance(fields, dict) else {}),
    }


def configure_logging(level: int = logging.INFO) -> None:
    """Write one JSON object per line on stdout: the whole of what a deployable logs."""
    root = logging.getLogger()
    root.setLevel(level)
    if any(getattr(handler, JSON_HANDLER, False) for handler in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, JSON_HANDLER, True)
    handler.setFormatter(_JsonLines())
    root.addHandler(handler)


class _JsonLines(logging.Formatter):
    """The formatter `configure_logging` installs: `log_line`, serialised, one line per record."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(log_line(record), ensure_ascii=False, default=str)


def _timestamp(created: float) -> str:
    return (
        datetime.fromtimestamp(created, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
