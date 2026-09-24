"""The deployables as data: one entry per process boundary (see `docs/adr/0001`)."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class Deployable:
    """A deployable: its name, its own scaling axis, and why it is not part of another one."""

    name: str
    default_port: int
    scaling_axis: str
    why_separate: str

    @property
    def console_script(self) -> str:
        """The console script that starts this deployable; declared in `pyproject.toml`."""
        return f"claim-triage-{self.name}"


DEPLOYABLES: Final[tuple[Deployable, ...]] = (
    Deployable(
        name="api",
        default_port=8000,
        scaling_axis="requests per second",
        why_separate="stateless entry point",
    ),
    Deployable(
        name="triager",
        default_port=8001,
        scaling_axis="queue depth",
        why_separate="holds the graph and checkpointing",
    ),
    Deployable(
        name="extraction",
        default_port=8002,
        scaling_axis="queue depth and CPU",
        why_separate="OCR and vision work is CPU- and latency-heavy",
    ),
    Deployable(
        name="retrieval",
        default_port=8003,
        scaling_axis="requests per second",
        why_separate="owns the vector and lexical indexes",
    ),
    Deployable(
        name="guardrail-policy",
        default_port=8004,
        scaling_axis="replica count",
        why_separate="safety logic must be versioned and deployed independently",
    ),
    Deployable(
        name="reviewer-ui",
        default_port=8005,
        scaling_axis="human sessions",
        why_separate="the human decision surface",
    ),
    Deployable(
        name="core-sim",
        default_port=8080,
        scaling_axis="replica count",
        why_separate="stands in for the surrounding insurance systems",
    ),
)

BY_NAME: Final[MappingProxyType[str, Deployable]] = MappingProxyType(
    {deployable.name: deployable for deployable in DEPLOYABLES}
)


def deployable(name: str) -> Deployable:
    """Look up a deployable by name, failing loudly on a name that is not one."""
    try:
        return BY_NAME[name]
    except KeyError:
        known = ", ".join(sorted(BY_NAME))
        raise LookupError(f"unknown deployable {name!r}; known deployables: {known}") from None
