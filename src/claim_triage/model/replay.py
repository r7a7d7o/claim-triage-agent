"""The replay adapter: answers from the fixtures committed beside it.

A fixture is one recorded call and the answer it got. It is read from its own contents — the task,
the content and the schema it answers — rather than from its filename, so a fixture set can be named
for what it holds, two files cannot quietly answer the same call, and a file edited into a different
call is simply a file answering that different call.

The directory is read once, when the adapter is built, and every answer afterwards is validated
against the schema the caller asked for. That is what makes repeated runs reproduce identical
values: nothing here reads a clock, a random source or a socket, and an answer that no longer fits
its schema fails at the call rather than being replayed into a stage that cannot use it.

Recording one is an edit, not a command: the file holds what the call was and what a model answered,
so it is reviewable in a pull request like any other artefact. `docs/adr/0006` records what it buys
and what it costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ConfigDict, ValidationError

from claim_triage.model.port import ModelCall, NoFixture, UnusableFixture, call_fingerprint

if TYPE_CHECKING:
    from pathlib import Path

FIXTURE_SUFFIX: Final = ".json"
"""What a fixture is called. Everything else in the directory is not one and is not read."""


class ReplayFixture(BaseModel):
    """One recorded call, and the answer a model gave it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: str
    content: str
    answer_as: str
    """The schema this answer was recorded for, as `ModelCall.schema` names it."""
    answer: dict[str, Any]
    """The answer as it arrived. Whether it is the shape `answer_as` names is the caller's check."""


@dataclass(frozen=True, slots=True)
class _Recorded:
    """One fixture as it was read, and the file it came from, which a failure has to name."""

    fixture: ReplayFixture
    path: Path


class ReplayModel:
    """The model, answered from committed fixtures instead of an endpoint."""

    def __init__(self, fixtures: Path) -> None:
        """Read a fixture set once: every answer after this is the bytes that were read."""
        self._directory = fixtures
        self._recorded = _read(fixtures)

    def answer[AnswerT: BaseModel](self, call: ModelCall[AnswerT]) -> AnswerT:
        """Answer one call with the fixture recorded for it, as the shape the call asks for."""
        recorded = self._recorded.get(call.fingerprint)
        if recorded is None:
            raise NoFixture(
                f"no fixture answers {call.task!r}: {self._directory} records "
                f"{len(self._recorded)} calls, for {self._tasks()}"
            )
        try:
            return call.answer_as.model_validate(recorded.fixture.answer)
        except ValidationError as does_not_fit:
            raise UnusableFixture(
                f"{recorded.path} does not fit {call.schema}: {does_not_fit}"
            ) from does_not_fit

    def _tasks(self) -> str:
        """What this fixture set does answer, so a call it does not answer can be placed."""
        tasks = sorted({recorded.fixture.task for recorded in self._recorded.values()})
        return ", ".join(repr(task) for task in tasks) if tasks else "nothing yet"


def _read(directory: Path) -> dict[str, _Recorded]:
    """Every fixture in one directory, by the call it answers: an unreadable set is refused."""
    if not directory.is_dir():
        raise LookupError(f"no replay fixtures at {directory}")
    recorded: dict[str, _Recorded] = {}
    for path in sorted(directory.glob(f"*{FIXTURE_SUFFIX}")):
        fixture = _fixture(path)
        fingerprint = call_fingerprint(fixture.task, fixture.content, fixture.answer_as)
        if fingerprint in recorded:
            raise UnusableFixture(f"{path} and {recorded[fingerprint].path} record the same call")
        recorded[fingerprint] = _Recorded(fixture=fixture, path=path)
    return recorded


def _fixture(path: Path) -> ReplayFixture:
    """One file as a fixture, or the failure that says which file is not one."""
    try:
        return ReplayFixture.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as malformed:
        raise UnusableFixture(f"{path} is not a fixture: {malformed}") from malformed
