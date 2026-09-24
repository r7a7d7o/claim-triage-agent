"""Scoring one run: the sets, the answers, and the named metrics the two produce together.

A metric has a *name* — `extraction.f1`, `retrieval.citation_validity`,
`classification.fraud_pr_auc`, and one F1 per extracted field — because a baseline is a record of
named numbers and a gate compares
them by name. The vocabulary lives here, next to the metrics that produce it, and the gate validates
what it is asked to compare against it (`claim_triage.evaluation.gate`), so a floor on a metric
nobody measures is refused rather than silently never firing.

A capability is *measured* only from real answers: a run whose answers are `None` for a capability
has not measured it at all, and says so as `Reading.NOT_ANSWERED` rather than scoring it zero. A set
with no case is `Reading.NO_CASES` even when nothing answers it, because the empty set is the reason
there is nothing in the table. Case counts travel with every measurement because a metric over three
cases and the same metric over three thousand are the same number and not the same evidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from claim_triage.evaluation.material import Capability
from claim_triage.evaluation.metrics import (
    Counts,
    average_precision,
    citation_counts,
    field_counts,
    hit_at,
    label_counts,
    macro_average,
    reciprocal_rank,
)
from claim_triage.evaluation.sets import Case, GoldenSet

if TYPE_CHECKING:
    from claim_triage.evaluation.answers import (
        Answers,
        ClassificationAnswer,
        ExtractionAnswer,
        RetrievalAnswer,
    )
    from claim_triage.evaluation.sets import (
        ClassificationCase,
        ExtractionCase,
        RetrievalCase,
        Sets,
    )

METRIC_NAMES: Final[frozenset[str]] = frozenset(
    {
        "extraction.precision",
        "extraction.recall",
        "extraction.f1",
        "retrieval.hit_rate",
        "retrieval.mrr",
        "retrieval.citation_validity",
        "classification.fraud_risk.precision",
        "classification.fraud_risk.recall",
        "classification.fraud_risk.f1",
        "classification.severity.precision",
        "classification.severity.recall",
        "classification.severity.f1",
        "classification.routing_accuracy",
        "classification.fraud_pr_auc",
    }
)
"""Every metric a run can measure, apart from the one per-field metric matching `FIELD_F1`."""

FIELD_F1: Final = re.compile(r"^extraction\.field\.[a-z][a-z0-9_]*\.f1$")
"""How a single field's F1 is named: the field's own name, so a field that degrades is visible."""

HIT_RATE_K: Final = 5
"""How deep the ranked list is read for a hit by default, until a run is asked for another depth."""


class Reading(StrEnum):
    """What a run could read for one capability."""

    SCORED = "scored"
    NO_CASES = "no cases"
    NOT_ANSWERED = "not answered"


@dataclass(frozen=True, slots=True)
class Measurement:
    """One capability as a run read it: how many cases, how many answered, and what it measured."""

    capability: Capability
    reading: Reading
    cases: int
    answered: int
    metrics: Mapping[str, float]


def field_metric(field: str) -> str:
    """The name of one extracted field's F1."""
    return f"extraction.field.{field}.f1"


def is_metric(name: str) -> bool:
    """Whether a name is one a run can measure, which is what a baseline and a floor are held to."""
    return name in METRIC_NAMES or bool(FIELD_F1.match(name))


def measure(sets: Sets, answers: Answers, *, top_k: int = HIT_RATE_K) -> tuple[Measurement, ...]:
    """Measure every capability the sets hold, in the order the table lists them."""
    return (
        measure_extraction(sets.extraction, answers.extraction),
        measure_retrieval(sets.retrieval, answers.retrieval, top_k=top_k),
        measure_classification(sets.classification, answers.classification),
    )


def measure_extraction(
    golden: GoldenSet[ExtractionCase], answers: Mapping[str, ExtractionAnswer] | None
) -> Measurement:
    """Field-level precision, recall and F1, over the fields every case states.

    Micro-averaged across cases and fields, so the table's number is "of the values this set asks
    for, how many were read correctly", and one F1 per field beside it, so a field that is read
    worse than the rest is visible rather than averaged into it.
    """
    if not golden.cases:
        return _no_cases(Capability.EXTRACTION)
    if answers is None:
        return _unanswered(Capability.EXTRACTION, golden)

    per_field: dict[str, Counts] = {name: Counts() for case in golden.cases for name in case.fields}
    answered = 0
    for case in golden.cases:
        answer = answers.get(case.case_id)
        answered += answer is not None
        predicted = {} if answer is None else answer.fields
        for name in case.fields:
            per_field[name] += field_counts({name: case.fields[name]}, predicted)

    totals = Counts()
    for counts in per_field.values():
        totals += counts
    metrics = {
        "extraction.precision": totals.precision,
        "extraction.recall": totals.recall,
        "extraction.f1": totals.f1,
    }
    metrics.update({field_metric(name): per_field[name].f1 for name in sorted(per_field)})
    return _scored(Capability.EXTRACTION, golden, answered, metrics)


def measure_retrieval(
    golden: GoldenSet[RetrievalCase],
    answers: Mapping[str, RetrievalAnswer] | None,
    *,
    top_k: int,
) -> Measurement:
    """Hit rate at `k`, mean reciprocal rank, and how many of the citations resolved.

    Citation validity is measured only when a citation exists: a run that cited nothing has not made
    a bad citation, and what catches a capability that stopped citing is the coverage rule — the
    baseline holds the metric and this run does not measure it.
    """
    if not golden.cases:
        return _no_cases(Capability.RETRIEVAL)
    if answers is None:
        return _unanswered(Capability.RETRIEVAL, golden)

    hits = 0
    ranks = 0.0
    cited = 0
    resolved = 0
    answered = 0
    for case in golden.cases:
        answer = answers.get(case.case_id)
        answered += answer is not None
        retrieved = [] if answer is None else answer.retrieved
        hits += hit_at(retrieved, case.expected, top_k)
        ranks += reciprocal_rank(retrieved, case.expected)
        valid, total = citation_counts(retrieved, [] if answer is None else answer.cited)
        cited += total
        resolved += valid

    metrics = {
        "retrieval.hit_rate": hits / len(golden.cases),
        "retrieval.mrr": ranks / len(golden.cases),
    }
    if cited:
        metrics["retrieval.citation_validity"] = resolved / cited
    return _scored(Capability.RETRIEVAL, golden, answered, metrics)


def measure_classification(
    golden: GoldenSet[ClassificationCase],
    answers: Mapping[str, ClassificationAnswer] | None,
) -> Measurement:
    """Precision, recall and F1 per axis, routing accuracy, and the fraud score's PR AUC.

    The area under the precision-recall curve is measured over the cases an answer scored: a case
    nothing answered has no score to rank, and is a miss on the axes and on routing like every other
    unanswered case. It is the curve the specification names for this population, where fraud is the
    minority and a ROC curve's false-positive rate is diluted by the negatives.
    """
    if not golden.cases:
        return _no_cases(Capability.CLASSIFICATION)
    if answers is None:
        return _unanswered(Capability.CLASSIFICATION, golden)

    risk: list[tuple[str, str | None]] = []
    severity: list[tuple[str, str | None]] = []
    routing = 0
    positives: list[float] = []
    negatives: list[float] = []
    answered = 0
    for case in golden.cases:
        answer = answers.get(case.case_id)
        answered += answer is not None
        risk.append((case.fraud_risk, None if answer is None else answer.fraud_risk))
        severity.append((case.severity, None if answer is None else answer.severity))
        routing += answer is not None and answer.queue == case.queue
        if answer is not None:
            (positives if case.fraud_positive else negatives).append(answer.fraud_score)

    metrics: dict[str, float] = {}
    for axis, pairs in (("fraud_risk", risk), ("severity", severity)):
        counted = label_counts(pairs)
        metrics[f"classification.{axis}.precision"] = macro_average(
            counted, lambda counts: counts.precision
        )
        metrics[f"classification.{axis}.recall"] = macro_average(
            counted, lambda counts: counts.recall
        )
        metrics[f"classification.{axis}.f1"] = macro_average(counted, lambda counts: counts.f1)
    metrics["classification.routing_accuracy"] = routing / len(golden.cases)
    curve = average_precision(positives, negatives)
    if curve is not None:
        metrics["classification.fraud_pr_auc"] = curve
    return _scored(Capability.CLASSIFICATION, golden, answered, metrics)


def _scored[CaseT: Case](
    capability: Capability,
    golden: GoldenSet[CaseT],
    answered: int,
    metrics: Mapping[str, float],
) -> Measurement:
    return Measurement(
        capability=capability,
        reading=Reading.SCORED,
        cases=len(golden.cases),
        answered=answered,
        metrics=metrics,
    )


def _no_cases(capability: Capability) -> Measurement:
    return Measurement(
        capability=capability, reading=Reading.NO_CASES, cases=0, answered=0, metrics={}
    )


def _unanswered[CaseT: Case](capability: Capability, golden: GoldenSet[CaseT]) -> Measurement:
    return Measurement(
        capability=capability,
        reading=Reading.NOT_ANSWERED,
        cases=len(golden.cases),
        answered=0,
        metrics={},
    )
