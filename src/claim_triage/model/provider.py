"""The provider adapter: a hosted or local endpoint, asked for an answer in a schema.

OpenAI-compatible: a call is one `POST` to the endpoint's `/chat/completions`, carrying the JSON
schema of the shape the caller asked for as a strict `response_format`. The endpoint is therefore
asked for that shape rather than for prose that has to be parsed back out of an answer, and the same
schema is enforced again when the answer comes back — the endpoint's own decoding is not trusted to
be the only check.

Our task and a document's content travel as separate messages, so what we instruct and what a
document says stay distinguishable on the wire as well as in `ModelCall`.

The transport is injected, which is what lets this adapter be driven without a provider: the tests
serve a socket that answers the way a provider would, and assert what was sent to it. No test and no
CI job in this repository reaches a model provider or holds a credential for one — replay is what
configuration selects until it is told otherwise, and this adapter refuses to be built without an
endpoint and a model name that were configured explicitly.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

import httpx2
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from claim_triage.model.port import ModelCall, ModelRefused, ModelUnavailable, UnusableAnswer

if TYPE_CHECKING:
    from types import TracebackType

COMPLETIONS_PATH: Final = "/chat/completions"
"""Where an OpenAI-compatible endpoint answers a call. `base_url` is the endpoint's own root, so it
carries the version prefix if the endpoint has one — `http://localhost:11434/v1`, for instance."""

STATUS_OK: Final = 200
"""The only answer that is an answer."""

DETAIL_LIMIT: Final = 500
"""How much of an endpoint's error body is carried into a refusal: enough to act on, bounded."""


class _Message(BaseModel):
    """The message a choice carries: the one field of what an endpoint sends that counts."""

    model_config = ConfigDict(extra="ignore")

    content: str | None = None


class _Choice(BaseModel):
    """One answer an endpoint offers, and its message."""

    model_config = ConfigDict(extra="ignore")

    message: _Message


class _Completion(BaseModel):
    """What an endpoint answers with: choices, of which the first is the answer.

    Everything an endpoint sends beyond this is ignored rather than rejected, because it is not our
    wire to pin: models, usage and finish reasons are the endpoint's business, and a field it adds
    must not read as a broken adapter here.
    """

    model_config = ConfigDict(extra="ignore")

    choices: tuple[_Choice, ...] = ()


class ProviderModel:
    """A model behind an OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        name: str,
        timeout: float,
        api_key: SecretStr | None = None,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """Point the adapter at one endpoint, and at one model that endpoint serves.

        How long a call may take is the caller's number rather than the adapter's: configuration
        carries it (`CLAIM_TRIAGE_MODEL_TIMEOUT_SECONDS`), so there is one default in the tree.
        """
        if not base_url:
            raise LookupError(
                "the provider adapter needs an endpoint: set CLAIM_TRIAGE_MODEL_BASE_URL"
            )
        if not name:
            raise LookupError("the provider adapter needs a model: set CLAIM_TRIAGE_MODEL_NAME")
        self._name = name
        self._url = f"{base_url.rstrip('/')}{COMPLETIONS_PATH}"
        self._http = httpx2.Client(
            timeout=timeout,
            headers={} if api_key is None else {"Authorization": _bearer(api_key)},
            transport=transport,
        )

    def __enter__(self) -> ProviderModel:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the connections the adapter holds."""
        self._http.close()

    def answer[AnswerT: BaseModel](self, call: ModelCall[AnswerT]) -> AnswerT:
        """Ask the endpoint for one answer in the shape the call asks for, and validate it."""
        try:
            response = self._http.post(self._url, json=_payload(self._name, call))
        except httpx2.TransportError as unreachable:
            raise ModelUnavailable(f"{self._url} unreachable: {unreachable}") from None
        if response.status_code != STATUS_OK:
            raise ModelRefused(
                response.status_code, f"{self._url} answered {_short(response.text)}"
            )
        try:
            return call.answer_as.model_validate(_document(response))
        except ValidationError as not_the_shape:
            raise UnusableAnswer(
                f"{self._url} answered something {call.schema} does not accept: {not_the_shape}"
            ) from not_the_shape


def _payload[AnswerT: BaseModel](name: str, call: ModelCall[AnswerT]) -> dict[str, Any]:
    """The request one call becomes: the model, the two messages, and the shape asked for."""
    return {
        "model": name,
        "messages": [
            {"role": "system", "content": call.task},
            {"role": "user", "content": call.content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": call.answer_as.__name__,
                "schema": call.answer_as.model_json_schema(),
                "strict": True,
            },
        },
    }


def _document(response: httpx2.Response) -> object:
    """The answer the endpoint sent, decoded: the JSON its message carries, not the message."""
    try:
        completion = _Completion.model_validate(response.json())
    except (ValueError, ValidationError) as outside_the_wire:
        raise UnusableAnswer(f"{response.request.url} answered {outside_the_wire}") from None
    if not completion.choices or completion.choices[0].message.content is None:
        raise UnusableAnswer(f"{response.request.url} answered no choice carrying an answer")
    try:
        return json.loads(completion.choices[0].message.content)
    except ValueError as not_json:
        raise UnusableAnswer(
            f"{response.request.url} answered content that is not JSON: {not_json}"
        ) from None


def _bearer(api_key: SecretStr) -> str:
    """The header an endpoint that wants a key is given: the key itself, once unwrapped."""
    return f"Bearer {api_key.get_secret_value()}"


def _short(detail: str) -> str:
    """An endpoint's error body, bounded: a refusal is read by a human, not parsed by one."""
    stripped = detail.strip()
    return stripped if len(stripped) <= DETAIL_LIMIT else f"{stripped[:DETAIL_LIMIT]}…"
