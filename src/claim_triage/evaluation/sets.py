"""The golden sets: what each capability is scored against, as committed JSONL files.

Three capabilities are scored — extraction, retrieval and classification/routing — and each one has
a file in `evaluation/golden/`. A file is JSONL: its first line is the set's header (the capability,
the set's version and what it is for) and every line after it is one case. Blank lines are skipped,
so a set can be written one case per line and read as such.

The committed sets are **placeholders**: they declare their format, their version and nothing else,
because the cases are the document-intake increment's work (ticket 14) and the capabilities that
answer them arrive with it. Everything that reads, scores and judges a set is real here, and the
material beside them in `evaluation/fixtures/` exercises all of it.

A case names its document by identifier rather than by path: the synthetic corpus gives every
generated document an identifier and a case is written about that document. The harness never reads
the document itself — the build does, and the harness scores the answers the build produced for it.

The reader refuses what it does not read, naming the file and the line: a header that names a
capability the file is not, an unknown key, a field whose name is not a field name, a duplicate
case. Quietly dropping a case it could not parse is the one outcome a set reader must not have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from claim_triage.evaluation.material import (
    SET_FILES,
    Capability,
    Unreadable,
    lines,
    only_known,
    parsed,
    refuse_repeats,
)

if TYPE_CHECKING:
    from pathlib import Path

FIELD_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*$")
"""How a field is named in a case. A field name becomes part of a metric's name, so it stays a name
a metric can carry: lower case, digits and underscores, starting with a letter."""

type Scalar = str | int | float | bool | None
"""One extracted value. The types a document's fields hold before the schema registry gives them
semantic types (ticket 11); comparing two of them is `claim_triage.evaluation.metrics`' business."""


class ClauseRef(BaseModel):
    """One poistné podmienky clause, as a case expects it or an answer returns it.

    A clause is identified by its identifier *and* its edition: the point of the retrieval set
    is that the same question resolves differently under different editions, so a clause without its
    edition identifies nothing. `page` is where the clause sits in its edition; it is what makes a
    citation checkable, so a citation whose page contradicts the page the clause was retrieved from
    is not the same clause as far as this harness is concerned.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_id: str
    edition: str
    page: int | None = Field(default=None, ge=1)

    @property
    def identity(self) -> tuple[str, str]:
        """What makes this clause this clause: its identifier under its edition, not its page."""
        return (self.clause_id, self.edition)


class Case(BaseModel):
    """One case of a golden set: what every capability's case has in common.

    Only its identifier, because that is all the harness reads from a case generically — a case is
    refused if the set already holds one with its identifier, and a run's answers are held to answer
    the identifiers the set holds and no others.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str


class ExtractionCase(Case):
    """One document, and the fields a correct extraction of it holds.

    `fields` is only what the case *scores*: a field the document carries that the case does not
    state is not scored, so a case is as precise about its own scope as the annotation rules that
    write it (ticket 14). A field whose documented value is `null` is a field the document does not
    carry — answering it with a value is a false positive, answering it with nothing is correct.
    """

    document: str
    """The document this case is about, by the identifier the corpus generator gave it."""
    fields: dict[str, Scalar] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def _field_names(cls, fields: dict[str, Scalar]) -> dict[str, Scalar]:
        for name in fields:
            if not FIELD_NAME.match(name):
                raise ValueError(f"{name!r} is not a field name")
        return fields


class RetrievalCase(Case):
    """One question about the poistné podmienky, and the clauses that answer it.

    `incident_date` is part of the case rather than of the question's wording because it is what
    selects the edition, and the adversarial pairs the set will hold (ticket 14) differ in nothing
    else. `expected` is the set of clauses a correct retrieval returns — any of them, at any rank —
    which is what hit rate and mean reciprocal rank are computed against.
    """

    question: str
    incident_date: date
    product_family: str
    expected: list[ClauseRef] = Field(min_length=1)


class ClassificationCase(Case):
    """One claim, and what a correct classification and routing of it concludes.

    `fraud_positive` is the population's own label — the one a calibrated score is measured against
    — and the three names are what the classifier and the router are asked to produce: a risk band,
    a severity band, and the queue the claim belongs in. The harness scores agreement with the names
    a case carries rather than holding a vocabulary of its own, because those bands belong to the
    classifier that predicts them (tickets 16 and 17).
    """

    claim: dict[str, Scalar]
    fraud_positive: bool
    fraud_risk: str
    severity: str
    queue: str


class SetHeader(BaseModel):
    """What a set's first line states: which capability it scores, which version it is, and why."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: Capability
    version: int = Field(ge=1)
    description: str


@dataclass(frozen=True, slots=True)
class GoldenSet[CaseT: Case]:
    """One capability's set: its header, its cases, and the file they were read from."""

    capability: Capability
    version: int
    description: str
    cases: tuple[CaseT, ...]
    path: Path
    """Where this set was read from, so a verdict or a failure names the set it judged."""


@dataclass(frozen=True, slots=True)
class Sets:
    """The three sets one run scores, each typed by the capability that owns it."""

    extraction: GoldenSet[ExtractionCase]
    retrieval: GoldenSet[RetrievalCase]
    classification: GoldenSet[ClassificationCase]

    @property
    def all(self) -> tuple[GoldenSet[Any], ...]:
        """Every set, in the order the table lists them."""
        return (self.extraction, self.retrieval, self.classification)


def read_sets(directory: Path) -> Sets:
    """Read one directory as the three sets, refusing anything in it that is not one of them.

    A capability's file has to be there: a run scores all three, and a missing file would be a
    capability silently dropped from the table rather than a run that could not be made.
    """
    only_known(directory, "a set")
    missing = [
        SET_FILES[capability]
        for capability in Capability
        if not (directory / SET_FILES[capability]).is_file()
    ]
    if missing:
        raise Unreadable(f"{directory} holds no {' and no '.join(missing)}")
    return Sets(
        extraction=read_set(directory / SET_FILES[Capability.EXTRACTION], ExtractionCase),
        retrieval=read_set(directory / SET_FILES[Capability.RETRIEVAL], RetrievalCase),
        classification=read_set(
            directory / SET_FILES[Capability.CLASSIFICATION], ClassificationCase
        ),
    )


def read_set[CaseT: Case](path: Path, case_model: type[CaseT]) -> GoldenSet[CaseT]:
    """Read one set: its header first, then one case per line, each validated as its own shape."""
    found = lines(path)
    if not found:
        raise Unreadable(f"{path} is empty: a set states its capability and version first")
    first, first_line = found[0]
    header = parsed(SetHeader, first, path, first_line)
    if header.capability.value != path.stem:
        raise Unreadable(
            f"{path}:{first_line} declares {header.capability}; {path.name} is {path.stem}'s set"
        )
    cases = tuple(parsed(case_model, line, path, number) for line, number in found[1:])
    refuse_repeats(cases, path)
    return GoldenSet(
        capability=header.capability,
        version=header.version,
        description=header.description,
        cases=cases,
        path=path,
    )
