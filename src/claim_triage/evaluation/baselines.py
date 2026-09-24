"""The recorded baselines: what the build scored at a release tag, read back by the gate.

A baseline is committed, one file per release tag, and holds the named metrics of every capability
the sets scored at that tag — plus the sets' versions and the settings the run was measured under,
which is what makes a comparison reproducible rather than approximate. Ticket 14 requires the sets
to be versioned for exactly this reason, and the gate refuses a baseline recorded under other
settings rather than comparing two runs that were not the same run.

The tag is the version the distribution declares, prefixed with `v`, because the increments are
released as annotated tags and the version is the same string. Nothing here reads a clock: a
baseline is a statement about a build, and when it was recorded is the repository's history.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from claim_triage import DISTRIBUTION
from claim_triage.evaluation.material import Capability, Unreadable, document
from claim_triage.evaluation.scoring import Reading, is_metric
from claim_triage.evaluation.sets import Sets

if TYPE_CHECKING:
    from pathlib import Path

    from claim_triage.evaluation.scoring import Measurement

RECORDED_PLACES: Final = 6
"""How many decimals a recorded metric keeps. Finer than any rule compares, so nothing is lost."""


class Settings(BaseModel):
    """What a run was measured under, so a baseline and the run compared to it are the same run.

    Only what changes a metric belongs here: how deep a ranked list is read decides what a hit is,
    and a comparison across two depths would compare two different questions.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    top_k: int = Field(ge=1)


class SetRecord(BaseModel):
    """One set as it was at the tag: its version, and how many cases it held."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    cases: int = Field(ge=0)


class Baseline(BaseModel):
    """One release tag's measurements: what every capability scored, and on what."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tag: str = Field(min_length=1)
    settings: Settings
    sets: dict[Capability, SetRecord]
    metrics: dict[str, float]

    @field_validator("sets")
    @classmethod
    def _every_capability(
        cls, recorded: dict[Capability, SetRecord]
    ) -> dict[Capability, SetRecord]:
        missing = sorted(
            capability.value for capability in Capability if capability not in recorded
        )
        if missing:
            raise ValueError(f"no set recorded for {', '.join(missing)}")
        return recorded

    @field_validator("metrics")
    @classmethod
    def _known_metrics(cls, metrics: dict[str, float]) -> dict[str, float]:
        for name, value in metrics.items():
            if not is_metric(name):
                raise ValueError(f"{name!r} is not a metric this harness measures")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} is {value}, which is not a proportion")
        return metrics


def recorded(
    tag: str, sets: Sets, measurements: Sequence[Measurement], settings: Settings
) -> Baseline:
    """A tag's baseline, as this run measured it: what the build scores is what it scores."""
    metrics = {
        name: round(value, RECORDED_PLACES)
        for measurement in measurements
        if measurement.reading is Reading.SCORED
        for name, value in measurement.metrics.items()
    }
    return Baseline(
        tag=tag,
        settings=settings,
        sets={
            one.capability: SetRecord(version=one.version, cases=len(one.cases)) for one in sets.all
        },
        metrics=metrics,
    )


def read_baseline(path: Path) -> Baseline:
    """One baseline file, or the failure that says which file is not one."""
    baseline = document(path, Baseline, "a baseline")
    return baseline


def unrecorded(tag: str, sets: Sets, settings: Settings) -> Baseline:
    """A tag nothing has been recorded for yet: no metric, so every metric is adopted from it on.

    This is what a release starting a new tag is judged against: there is nothing to have regressed
    from, and the run that records the tag's baseline is the one that creates it.
    """
    return recorded(tag, sets, (), settings)


def path_for(directory: Path, tag: str) -> Path:
    """Where a tag's baseline is committed: one file per tag, named by the tag."""
    return directory / f"{tag}.json"


def write_baseline(path: Path, baseline: Baseline) -> None:
    """Record a baseline where the tag's file is, as the JSON a reader and a diff can both read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(baseline.model_dump_json(indent=2) + "\n", encoding="utf-8")


def release_tag() -> str:
    """The release tag this build belongs to: the version the distribution declares."""
    try:
        return f"v{version(DISTRIBUTION)}"
    except PackageNotFoundError as not_installed:
        raise Unreadable(
            f"the release tag comes from the installed {DISTRIBUTION} distribution: {not_installed}"
        ) from None


def metrics_of(baseline: Baseline, capability: Capability) -> Mapping[str, float]:
    """The metrics a baseline records for one capability, by the prefix its names carry."""
    prefix = f"{capability.value}."
    return {name: value for name, value in baseline.metrics.items() if name.startswith(prefix)}
