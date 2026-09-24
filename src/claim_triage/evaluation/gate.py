"""The declared rules, and the verdicts they produce for one run's measurements.

A run exits non-zero only through these rules, so they are declared twice over: **as data** in
`evaluation/gate.json` — the tolerance every metric is held to, and the metrics that carry an
absolute floor — and **as code**, one `Rule` per way a run can fail. The data decides the
thresholds, which is what lets a release change them in a reviewed file; the code decides what
kinds of failure exist at all, which is why a floor on a metric nobody measures is refused when
the rules are read rather than quietly never firing.

Four rules, and each names itself in the failure it produces:

* **degradation** — a measured metric more than the tolerance below the value the baseline records
  for it. Two percentage points, which is the specification's gate (user story 90).
* **floor** — a measured metric below the floor the rules declare for it, whether or not the
  baseline agrees. Citation validity carries this one: a decision pack whose citations do not
  resolve is wrong however much it improved (user stories 50 and 91).
* **coverage** — a metric the baseline records that this run did not measure. A capability that
  stops answering, a set that loses its cases, or a build that stops citing all show up here,
  because the alternative is a metric that disappears from the table and takes its regression
  with it.
* **comparability** — the baseline was recorded under other settings than this run, so the two
  numbers are not the same question. Nothing is compared, and the run fails rather than decide.

A metric with no baseline value is *adopted*, not failed: the sets grow a field, the classifier
grows an axis, and the next baseline records it. What that leaves uncovered is stated rather than
implied — a build that never measured a metric has no baseline for it and therefore nothing to
degrade, which is why coverage fails a metric that *was* recorded and is now gone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from claim_triage.evaluation.baselines import metrics_of
from claim_triage.evaluation.material import Capability, document
from claim_triage.evaluation.scoring import Measurement, Reading, is_metric

if TYPE_CHECKING:
    from pathlib import Path

    from claim_triage.evaluation.baselines import Baseline, Settings

CITATION_VALIDITY: Final = "retrieval.citation_validity"
"""The metric that carries an absolute floor, independent of how the baseline moved."""


class Rule(StrEnum):
    """Every way a run can fail. A failure message names the rule it came from."""

    DEGRADATION = "degradation"
    FLOOR = "floor"
    COVERAGE = "coverage"
    COMPARABILITY = "comparability"


DECLARED_RULES: Final[tuple[Rule, ...]] = (
    Rule.DEGRADATION,
    Rule.FLOOR,
    Rule.COVERAGE,
    Rule.COMPARABILITY,
)
"""The rules, as the run's own summary counts them."""


class Verdict(StrEnum):
    """One row of the table: what the rules made of one metric of one capability."""

    OK = "ok"
    ADOPTED = "adopted"
    REGRESSED = "regressed"
    BELOW_FLOOR = "below floor"
    NOT_MEASURED = "not measured"
    NO_CASES = "no cases"
    NOT_ANSWERED = "not implemented"
    INCOMPARABLE = "incomparable"


class Rules(BaseModel):
    """The gate's thresholds, as the file a release reviews declares them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tolerance: float = Field(ge=0.0, le=1.0)
    """How far a metric may fall below its baseline before that is a regression, as a rate."""
    floors: dict[str, float]

    @field_validator("floors")
    @classmethod
    def _floors_are_measurable(cls, floors: dict[str, float]) -> dict[str, float]:
        for name, value in floors.items():
            if not is_metric(name):
                raise ValueError(f"{name!r} is not a metric this harness measures")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} is {value}, which is not a proportion")
        if CITATION_VALIDITY not in floors:
            raise ValueError(
                f"the rules declare no floor for {CITATION_VALIDITY}, which is the one metric"
                " that has an absolute floor"
            )
        return floors

    def describe(self) -> str:
        """The rules as one line of the report, so a run states what it is judged by."""
        floors = ", ".join(f"{name} >= {value:.4f}" for name, value in sorted(self.floors.items()))
        return (
            f"a metric more than {self.tolerance * 100:.2f} points below its baseline fails;"
            f" floors: {floors}"
        )


def read_rules(path: Path) -> Rules:
    """The declared rules, or the failure that says which file does not declare them."""
    return document(path, Rules, "the declared rules")


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the metric table: what was measured, against what, and the verdict on it."""

    capability: Capability
    metric: str | None
    cases: int
    answered: int
    value: float | None
    baseline: float | None
    delta: float | None
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class Outcome:
    """Every row, every rule that fired, and whether the run passed."""

    rules: Rules
    rows: tuple[Row, ...]
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """Whether no declared rule fired: the only thing an exit code is decided by."""
        return not self.failures


def judge(
    measurements: Sequence[Measurement],
    baseline: Baseline,
    rules: Rules,
    *,
    settings: Settings,
) -> Outcome:
    """Judge a run's measurements against a tag's baseline, under the declared rules."""
    if baseline.metrics and baseline.settings != settings:
        return Outcome(
            rules=rules,
            rows=tuple(_incomparable(measurement) for measurement in measurements),
            failures=(
                f"{Rule.COMPARABILITY}: the baseline was recorded at "
                f"{_settings(baseline.settings)} and this run is {_settings(settings)}",
            ),
        )

    rows: list[Row] = []
    failures: list[str] = []
    for measurement in measurements:
        recorded = metrics_of(baseline, measurement.capability)
        names = [
            *measurement.metrics,
            *(name for name in sorted(recorded) if name not in measurement.metrics),
        ]
        if not names:
            rows.append(
                Row(
                    capability=measurement.capability,
                    metric=None,
                    cases=measurement.cases,
                    answered=measurement.answered,
                    value=None,
                    baseline=None,
                    delta=None,
                    verdict=(
                        Verdict.NO_CASES
                        if measurement.reading is Reading.NO_CASES
                        else Verdict.NOT_ANSWERED
                    ),
                )
            )
            continue
        for name in names:
            value = measurement.metrics.get(name)
            at_baseline = recorded.get(name)
            floor = rules.floors.get(name)
            rows.append(
                Row(
                    capability=measurement.capability,
                    metric=name,
                    cases=measurement.cases,
                    answered=measurement.answered,
                    value=value,
                    baseline=at_baseline,
                    delta=None if value is None or at_baseline is None else value - at_baseline,
                    verdict=_verdict(value, at_baseline, floor, rules.tolerance),
                )
            )
            failures.extend(_failures(name, value, at_baseline, floor, rules.tolerance))
    return Outcome(rules=rules, rows=tuple(rows), failures=tuple(failures))


def _verdict(
    value: float | None, at_baseline: float | None, floor: float | None, tolerance: float
) -> Verdict:
    """What one metric's numbers make of it: the floor first, because it needs no baseline."""
    if value is None:
        return Verdict.NOT_MEASURED
    if floor is not None and value < floor:
        return Verdict.BELOW_FLOOR
    if at_baseline is None:
        return Verdict.ADOPTED
    return Verdict.REGRESSED if regressed(value, at_baseline, tolerance) else Verdict.OK


def regressed(value: float, at_baseline: float, tolerance: float) -> bool:
    """Whether a metric fell by more than the tolerance, as the numbers were actually recorded.

    The drop is rounded to nine decimals before it is compared. A baseline is a record to six, and
    `1.0 - 0.98` is `0.020000000000000018` in binary floating point: without the rounding, whether
    "two percentage points" is exceeded would depend on which numbers happened to be involved.
    """
    return round(at_baseline - value, 9) > tolerance


def _failures(
    name: str,
    value: float | None,
    at_baseline: float | None,
    floor: float | None,
    tolerance: float,
) -> list[str]:
    """Every rule one metric breaks, in the order the rules are declared."""
    if value is None:
        return [
            f"{Rule.COVERAGE}: {name} was recorded at {at_baseline:.4f} and this run did not"
            " measure it"
        ]
    messages: list[str] = []
    if floor is not None and value < floor:
        messages.append(f"{Rule.FLOOR}: {name} is {value:.4f}, below its floor {floor:.4f}")
    if at_baseline is not None and regressed(value, at_baseline, tolerance):
        messages.append(
            f"{Rule.DEGRADATION}: {name} fell {(at_baseline - value) * 100:.2f} points to"
            f" {value:.4f}, from {at_baseline:.4f} (tolerance {tolerance * 100:.2f})"
        )
    return messages


def _incomparable(measurement: Measurement) -> Row:
    """One capability's row when nothing was compared: the numbers are there, the verdict is not."""
    return Row(
        capability=measurement.capability,
        metric=None,
        cases=measurement.cases,
        answered=measurement.answered,
        value=None,
        baseline=None,
        delta=None,
        verdict=Verdict.INCOMPARABLE,
    )


def _settings(settings: Settings) -> str:
    """A run's settings as the failure line states them."""
    return f"top_k={settings.top_k}"
