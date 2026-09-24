"""The model behind a port: one call in, one structured answer out, and no way around it.

Every use of a model in this repository crosses this module. A stage builds a `ModelCall` — the task
it is asking, the text a model reads, and the shape the answer must take — and the port answers with
an instance of that shape, or raises. The two implementations live beside this one, and a stage
reaches them only through `claim_triage.model.select`: `tests/test_model_port.py` checks that no
other module in the tree imports an adapter, or an HTTP client, to speak to an endpoint of its own.

The shape travels *in* the call rather than being checked afterwards, so structured output is the
type's business rather than a convention: a caller cannot ask for a schema and receive unvalidated
text, and the schema a stage asks for is written where the call is made.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class ModelCall[AnswerT: BaseModel]:
    """One call into a model: what is asked, what the answer must be, and what the model reads.

    `task` is ours — the instruction a stage authors. `content` is the text of a document, which is
    data and never an instruction: it is carried as its own value rather than folded into the task's
    phrasing, so an implementation can tell the two apart when it puts them on the wire (the
    untrusted-content wall builds on that in ticket 06). `answer_as` is the schema the answer is
    validated against, and the type the caller gets back.
    """

    task: str
    answer_as: type[AnswerT]
    content: str = ""

    @property
    def schema(self) -> str:
        """The shape this call asks for, named the way a recorded answer records it."""
        return f"{self.answer_as.__module__}.{self.answer_as.__qualname__}"

    @property
    def fingerprint(self) -> str:
        """What this call is, as one digest."""
        return call_fingerprint(self.task, self.content, self.schema)


class ModelPort(Protocol):
    """A model, as a stage uses it: one call in, the answer in the shape the call asked for."""

    def answer[AnswerT: BaseModel](self, call: ModelCall[AnswerT]) -> AnswerT:
        """Answer one call, or raise. An answer is always an instance of `call.answer_as`."""
        ...


class ModelUnavailable(Exception):
    """No answer arrived: nothing could answer this call.

    The provider adapter raises it when the endpoint cannot be reached at all; the replay adapter
    raises `NoFixture`, which is this failure with the reason a fixture set gives.
    """


class NoFixture(ModelUnavailable):
    """The replay adapter holds no answer for this call: nothing here has recorded it."""


class ModelRefused(Exception):
    """The endpoint answered with an error instead of an answer, and named what it was."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class UnusableAnswer(Exception):
    """Something answered, and the answer is not the shape the call asked for.

    That is a defect rather than a condition to route around: it means an endpoint ignores the shape
    it was sent, or that what it answered is being read wrongly. It stays a defect so that neither
    possibility is dressed up as a refusal the caller could retry.
    """


class UnusableFixture(UnusableAnswer):
    """A recorded answer cannot answer as it stands: it is malformed, or no longer fits its schema.

    Which file, and what about it, is in the message: the fixture is what has to be recorded again.
    """


def call_fingerprint(task: str, content: str, schema: str) -> str:
    """The identity of one call: what it asks, what it reads, and the shape of the answer.

    This is what a recorded answer is looked up by, so it is also what makes a recording independent
    of where it sits: renaming the file changes nothing, and editing its `task`, `content` or
    `answer_as` makes it answer a call that is no longer the one it was recorded for.
    """
    identity = json.dumps(
        {"task": task, "content": content, "schema": schema},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
