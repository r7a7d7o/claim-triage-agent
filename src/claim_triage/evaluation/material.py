"""The harness's committed material: what it scores, where that lives, and how it refuses.

Four artefacts are read off disk — the golden sets, a run's recorded answers, a release tag's
baseline, the declared rules — and all four refuse the same way: an `Unreadable` naming the file,
and where there is one, the line. **One exception rather than four**, because the caller does the
same thing about every one of them (report it, exit `2`) and a fourth name is a fourth name to leave
out of an `except` clause — which is exactly how an unreadable answers file came to be reported as a
regression before this module existed.

The readers here are the shape the material shares: JSONL with one model per non-blank line, and a
whole file as one model. A set is the first with a header line, a recorded-answer file is the first
without one, a baseline and a rules file are the second.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from pathlib import Path


class Capability(StrEnum):
    """What is scored: each is a golden set, a file of answers, and a row of the metric table."""

    EXTRACTION = "extraction"
    RETRIEVAL = "retrieval"
    CLASSIFICATION = "classification"


SET_FILES: Final[Mapping[Capability, str]] = MappingProxyType(
    {capability: f"{capability.value}.jsonl" for capability in Capability}
)
"""The file each capability's set is committed as, in the sets directory a run reads."""


class Unreadable(Exception):
    """Committed material is not the shape this harness reads: which file, and which line."""


class Identified(Protocol):
    """Something that names the case it is about, which two artefacts are held to agree on."""

    @property
    def case_id(self) -> str:
        """The case this is about."""
        ...


def lines(path: Path) -> list[tuple[str, int]]:
    """Every non-blank line of a file, with the number it sits at, counted from one.

    Blank lines are skipped rather than refused, so a set can be written one case per line and read
    as such, and a line's number is its own rather than its position among the lines that hold
    something — which is the number a refusal has to name.
    """
    return [
        (line.strip(), number)
        for number, line in enumerate(_text(path).splitlines(), start=1)
        if line.strip()
    ]


def parsed[ModelT: BaseModel](model: type[ModelT], line: str, path: Path, number: int) -> ModelT:
    """One JSON line as one model, or the refusal naming the line and what about it does not fit."""
    try:
        value: object = json.loads(line)
    except json.JSONDecodeError as not_json:
        raise Unreadable(f"{path}:{number} is not JSON: {not_json}") from None
    return validated(model, value, f"{path}:{number}")


def validated[ModelT: BaseModel](model: type[ModelT], value: object, where: str) -> ModelT:
    """One parsed value as one model, or the refusal naming where it came from and what is wrong."""
    try:
        return model.model_validate(value)
    except ValidationError as does_not_fit:
        raise Unreadable(f"{where} is not a {model.__name__}: {does_not_fit}") from None


def document[ModelT: BaseModel](path: Path, model: type[ModelT], what: str) -> ModelT:
    """A whole file as one model, or the refusal that says which file is not one."""
    try:
        return model.model_validate_json(_text(path))
    except ValidationError as does_not_fit:
        raise Unreadable(f"{path} is not {what}: {does_not_fit}") from None


def only_known(directory: Path, what: str) -> None:
    """Refuse anything in a directory of capability files that is not one of them.

    Both directories this harness reads are one file per capability, so a stray file is either a
    capability nobody scores or a mistake: refusing it by name is what keeps the second from reading
    as the first.
    """
    known = ", ".join(sorted(SET_FILES.values()))
    for found in sorted(directory.glob("*.jsonl")):
        if found.name not in SET_FILES.values():
            raise Unreadable(f"{found} is not {what}: it holds {known}")


def case_ids(cases: Sequence[Identified]) -> set[str]:
    """The identifiers a set holds, which is what an answers file has to be about."""
    return {case.case_id for case in cases}


def refuse_repeats(cases: Sequence[Identified], path: Path) -> None:
    """Refuse an artefact that names the same case twice: a case counted twice weighs twice."""
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise Unreadable(f"{path} holds {case.case_id!r} twice")
        seen.add(case.case_id)


def _text(path: Path) -> str:
    """A whole file as text, or the refusal naming why it cannot be read as text at all.

    Encoding is a real failure in this domain rather than a hypothetical one: the corpus holds a
    document whose legacy CP1250 mojibake has to be repaired during ingestion (ticket 09), so a file
    saved as bytes no UTF-8 reader can decode is refused here rather than carried into a scorer.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as unreadable:
        raise Unreadable(f"{path} cannot be read: {unreadable}") from None
