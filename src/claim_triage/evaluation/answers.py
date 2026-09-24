"""What answered a set's cases: the shape of an answer, and where recorded answers are read from.

An answer is the build's own output for one case, in the shape of the capability that produced it:
extracted fields, a ranked clause list with the clauses it cited, or a score with the bands and the
queue. `claim_triage.evaluation.build` is where the current build is asked for answers; this module
is what validates them, whichever seam they arrived through, and it reads a directory of recorded
answers — the shape a run's answers are written and re-scored in, and the seam the CI job injects a
regression through.

A recorded answer is one JSON line per case: the case's identifier and the answer for it. The
envelope is the same for every capability and the answer is validated against that capability's own
shape, the way a fixture is validated against the schema the call asked for
(`claim_triage.model.replay`). A case the set does not hold, a case answered twice, or a capability
the set holds no case for is refused, naming the line: answers and the set they are scored against
have to be about the same cases, once each, or a metric would be measured over the wrong population.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from claim_triage.evaluation.material import (
    SET_FILES,
    Capability,
    Unreadable,
    case_ids,
    lines,
    only_known,
    parsed,
    refuse_repeats,
    validated,
)
from claim_triage.evaluation.sets import ClauseRef, Fields, Sets

if TYPE_CHECKING:
    from pathlib import Path


class ExtractionAnswer(BaseModel):
    """What an extraction produced for one document: the fields it read, by name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fields: Fields = Field(default_factory=dict)


class RetrievalAnswer(BaseModel):
    """What a retrieval returned for one case: the clauses it retrieved, and the ones it cited.

    `retrieved` is ranked, best first — the order is the ranking, so it is not a field of its own —
    and `cited` is what a decision-support pack would point a reviewer at. A citation is valid when
    it resolves to a clause of this same answer's retrieved list; anything else is the harness's
    citation-validity metric, and the runtime's hard failure (user story 50).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieved: list[ClauseRef] = Field(default_factory=list)
    cited: list[ClauseRef] = Field(default_factory=list)


class ClassificationAnswer(BaseModel):
    """What a classification and routing concluded about one claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fraud_score: float = Field(ge=0.0, le=1.0)
    """The calibrated probability the claim is fraudulent, which the risk score is measured on."""
    fraud_risk: str
    severity: str
    queue: str


class RecordedAnswer(BaseModel):
    """One line of a recorded answers file: the case it answers, and the answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    answer: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Answers:
    """What answered a run's cases, one mapping per capability, keyed by case identifier.

    A capability whose mapping is `None` is one nothing answered: no build stage produces it yet, or
    no answers were recorded for it. That is reported as *not implemented* rather than scored as
    zero, because a build that was never asked has not failed anything.
    """

    extraction: Mapping[str, ExtractionAnswer] | None = None
    retrieval: Mapping[str, RetrievalAnswer] | None = None
    classification: Mapping[str, ClassificationAnswer] | None = None


def read_answers(directory: Path, sets: Sets) -> Answers:
    """Read a directory as the recorded answers for these sets, one file per capability.

    A capability the sets hold cases for has to have a file; a capability they hold none for must
    not, because there is nothing there for it to have answered. A file with no line is not a
    missing file: it is a run that answered none of the cases, and every case is scored as the miss
    it was.
    """
    only_known(directory, "an answers file")
    return Answers(
        extraction=_read_one(
            directory / SET_FILES[Capability.EXTRACTION],
            Capability.EXTRACTION,
            case_ids(sets.extraction.cases),
            ExtractionAnswer,
        ),
        retrieval=_read_one(
            directory / SET_FILES[Capability.RETRIEVAL],
            Capability.RETRIEVAL,
            case_ids(sets.retrieval.cases),
            RetrievalAnswer,
        ),
        classification=_read_one(
            directory / SET_FILES[Capability.CLASSIFICATION],
            Capability.CLASSIFICATION,
            case_ids(sets.classification.cases),
            ClassificationAnswer,
        ),
    )


def _read_one[AnswerT: BaseModel](
    path: Path, capability: Capability, held: set[str], model: type[AnswerT]
) -> Mapping[str, AnswerT] | None:
    """One capability's answers, or `None` when the sets hold no case for it to have answered."""
    if not held:
        if path.is_file():
            raise Unreadable(f"{path} answers {capability}, which the sets hold no case for")
        return None
    if not path.is_file():
        raise Unreadable(f"no answers at {path}: {capability} holds {len(held)} cases")

    recorded: list[RecordedAnswer] = []
    answers: dict[str, AnswerT] = {}
    for line, number in lines(path):
        answer = parsed(RecordedAnswer, line, path, number)
        if answer.case_id not in held:
            raise Unreadable(
                f"{path}:{number} answers {answer.case_id!r}, which {capability} does not hold"
            )
        recorded.append(answer)
        answers[answer.case_id] = _answer(model, answer, path, number)
    refuse_repeats(recorded, path)
    return MappingProxyType(answers)


def _answer[AnswerT: BaseModel](
    model: type[AnswerT], recorded: RecordedAnswer, path: Path, number: int
) -> AnswerT:
    """One recorded answer as its capability's shape, or the refusal naming the line."""
    return validated(model, recorded.answer, f"{path}:{number}")
