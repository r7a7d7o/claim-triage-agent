"""The arithmetic a set is scored with: what agreement means, and how it is aggregated.

Four things are measured here, and nothing else in the harness does arithmetic:

* **fields** — an extracted value against the value the case states, counted as true positives,
  false positives and false negatives, and micro-averaged over cases so that one case with many
  fields does not weigh the same as one with one;
* **labels** — a risk band, a severity band, a queue: counted per label and macro-averaged over the
  labels the case set labels with, so a rare band counts as much as a common one. A wrong band is a
  miss on the case's own label *and* a false positive on the label it named, when the cases label
  with that name at all, so precision falls when a claim is banded wrongly and not only when one is
  missed;
* **ranks** — where the expected clause first appears in a ranked list: hit rate at `k`, and mean
  reciprocal rank;
* **a score** — the calibrated fraud score against the population's own label, as the area under
  the precision-recall curve, read from highest score to lowest with equal scores taken as one step,
  so a tie is never read as an ordering.

Two conventions are stated rather than implied, because both change a number a reader will see:

* **A hit that was not made scores zero, not nothing.** A count whose denominator is zero — no field
  was predicted, no citation was made — scores `0.0` rather than being left out, so a build that
  answered nothing is scored on that, and not merely skipped. The one exception is citation
  validity, which is omitted when no citation exists at all: a build that cites nothing has not made
  a wrong citation, and the coverage rule catches a capability that stops citing.
* **A field the case states as `null` is asserted to be absent.** Answering it with nothing is
  correct and is *not* credited as a hit; answering it with a value is a false positive and a false
  negative. Crediting absence would make silence score well on a document whose fields are mostly
  absent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from claim_triage.evaluation.sets import ClauseRef, Scalar

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class Counts:
    """True positives, false positives and false negatives, added up as cases are scored."""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    def __add__(self, other: Counts) -> Counts:
        return Counts(
            true_positives=self.true_positives + other.true_positives,
            false_positives=self.false_positives + other.false_positives,
            false_negatives=self.false_negatives + other.false_negatives,
        )

    @property
    def precision(self) -> float:
        """Of what was predicted, the share that was right. Zero when nothing was predicted."""
        return _ratio(self.true_positives, self.true_positives + self.false_positives)

    @property
    def recall(self) -> float:
        """Of what the cases state, the share that was found."""
        return _ratio(self.true_positives, self.true_positives + self.false_negatives)

    @property
    def f1(self) -> float:
        """The harmonic mean of the two, which is what a single number per capability is read as."""
        total = 2 * self.true_positives + self.false_positives + self.false_negatives
        return _ratio(2 * self.true_positives, total)


def compare(expected: Scalar, predicted: Scalar) -> bool:
    """Whether an extracted value is the value the case states.

    Case and whitespace are not differences: `"SIM-2026-0001"` and `"sim-2026-0001"` are one value,
    as are `"1 840,50"` written with a line break in it and `"1840,50"`. Anything that parses as a
    decimal is compared as one, so `1840.5`, `1840.50` and `"1840.50"` are one amount. Slovak
    diacritics are *not* folded away: they are part of the value in this domain.
    """
    if expected is None or predicted is None:
        return expected is None and predicted is None
    if isinstance(expected, bool) or isinstance(predicted, bool):
        return isinstance(expected, bool) and isinstance(predicted, bool) and expected == predicted
    expected_number, predicted_number = _number(expected), _number(predicted)
    if expected_number is not None and predicted_number is not None:
        return expected_number == predicted_number
    return _text(expected) == _text(predicted)


def field_counts(expected: Mapping[str, Scalar], predicted: Mapping[str, Scalar]) -> Counts:
    """One case's fields scored: what was found, what was wrong, and what was missed.

    A field the answer leaves out and a field it answers with `null` are one thing here: no value
    was produced for it, which is a miss where the case states a value and correct where it states
    the field is absent.
    """
    counts = Counts()
    for name, truth in expected.items():
        answer = predicted.get(name)
        if truth is None:
            # The case asserts this field is absent from the document: a value here was invented.
            if answer is not None:
                counts += Counts(false_positives=1, false_negatives=1)
        elif answer is None:
            counts += Counts(false_negatives=1)
        elif compare(truth, answer):
            counts += Counts(true_positives=1)
        else:
            counts += Counts(false_positives=1, false_negatives=1)
    return counts


def label_counts(pairs: Iterable[tuple[str, str | None]]) -> dict[str, Counts]:
    """One axis' answers scored per label: the truth in the pair, and the name answered, if any.

    A wrong answer is a miss on the case's own label and a false positive on the label it named, so
    a classifier that bands everything `high` loses precision as well as recall. A label the cases
    never label with is not a label this measures: a name outside the set's own vocabulary is a miss
    on the case it answered, not a class of its own.
    """
    answered_pairs = list(pairs)
    labels = {truth for truth, _ in answered_pairs}
    counted: dict[str, Counts] = {label: Counts() for label in labels}
    for truth, answered in answered_pairs:
        if answered is None or answered != truth:
            counted[truth] += Counts(false_negatives=1)
            if answered in labels:
                counted[answered] += Counts(false_positives=1)
        else:
            counted[truth] += Counts(true_positives=1)
    return counted


def macro_average(counted: Mapping[str, Counts], metric: Callable[[Counts], float]) -> float:
    """One metric per label, averaged over the labels so that each one weighs the same."""
    if not counted:
        return 0.0
    return sum(metric(counts) for counts in counted.values()) / len(counted)


def hit_at(ranked: Sequence[ClauseRef], expected: Sequence[ClauseRef], k: int) -> bool:
    """Whether any expected clause is among the first `k` a retrieval returned."""
    return bool(set(identities(ranked[:k])) & set(identities(expected)))


def reciprocal_rank(ranked: Sequence[ClauseRef], expected: Sequence[ClauseRef]) -> float:
    """One over the rank of the first expected clause, or zero when none was retrieved.

    The *first* expected clause, because the metric is about how high an answer was ranked; a
    retrieval that returns several relevant clauses scores on the best of them, as MRR defines.
    """
    wanted = set(identities(expected))
    for rank, clause in enumerate(ranked, start=1):
        if clause.identity in wanted:
            return 1.0 / rank
    return 0.0


def citation_counts(retrieved: Sequence[ClauseRef], cited: Sequence[ClauseRef]) -> tuple[int, int]:
    """How many citations resolved to a clause of the retrieved set, and how many there were.

    A citation resolves when the same clause, under the same edition, is in the retrieved list and
    the two agree on the page when both carry one: a citation that names the right clause of the
    wrong page is not one a reviewer can check.
    """
    resolved = {clause.identity: clause for clause in retrieved}
    valid = 0
    for citation in cited:
        found = resolved.get(citation.identity)
        if found is None:
            continue
        if citation.page is None or found.page is None or citation.page == found.page:
            valid += 1
    return valid, len(cited)


def average_precision(positives: Sequence[float], negatives: Sequence[float]) -> float | None:
    """The area under the precision-recall curve of a score against a binary label.

    Read from the highest score to the lowest: each step's precision is weighted by the recall it
    adds, which is average precision — the same area, without interpolating between thresholds that
    no score separates. Equal scores are one step, so a tie is never counted as an ordering.

    This is the curve to read on a claim population, where fraud is the minority: a ROC curve's
    false-positive rate is diluted by the negatives such a population has in abundance. `None` when
    no case is positive or none is negative, because there is then nothing to rank one against the
    other.
    """
    if not positives or not negatives:
        return None
    ranked = sorted(
        [(score, True) for score in positives] + [(score, False) for score in negatives],
        key=lambda scored: scored[0],
        reverse=True,
    )
    positive_count = float(len(positives))
    found = 0
    seen = 0
    area = 0.0
    previous_recall = 0.0
    position = 0
    while position < len(ranked):
        threshold = ranked[position][0]
        while position < len(ranked) and ranked[position][0] == threshold:
            found += ranked[position][1]
            seen += 1
            position += 1
        recall = found / positive_count
        area += (recall - previous_recall) * (found / seen)
        previous_recall = recall
    return area


def identities(clauses: Sequence[ClauseRef]) -> tuple[tuple[str, str], ...]:
    """The clause identities of a list, in the order they appear."""
    return tuple(clause.identity for clause in clauses)


def _ratio(numerator: int, denominator: int) -> float:
    """A proportion of two counts, and zero when there is nothing to be a proportion of."""
    return 0.0 if denominator == 0 else numerator / denominator


def _number(value: Scalar) -> Decimal | None:
    """A value as a decimal, or `None` when it is not a finite number at all.

    A flag reaches this as a string and does not parse as a number, which is what keeps `True` from
    comparing equal to `1`: `compare` answers a boolean pair before it gets here.
    """
    try:
        number = Decimal(str(value).replace(" ", ""))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _text(value: Scalar) -> str:
    """A value as comparable text: case and whitespace folded away, everything else kept."""
    return " ".join(str(value).split()).casefold()
