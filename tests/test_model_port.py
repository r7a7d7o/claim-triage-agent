"""The model port: one call in, a structured answer out, and no way around it.

Ticket 05 asks for four things, and each is answered by tests here. Nothing reaches a model provider
without going through the port: the adapters have exactly one importer, and the modules that open
their own HTTP connections are pinned by name. Replay reproduces the same answer byte for byte on
every run: it is driven against the fixture committed with the port, and its failures — a call that
was never recorded, a fixture that no longer fits its schema, a set that cannot be read — are read
for what they name. The provider adapter is what configuration selects and nothing else is, and no
test and no job performs a live model call: replay is what an unconfigured environment selects, and
the provider adapter refuses to exist without an endpoint and a model name that were configured
explicitly.

The provider adapter is where the ticket's third criterion bites. It is selected by configuration,
and it is never exercised in CI: everything that drives it is marked `provider`, the default run
leaves those out (`pyproject.toml`), and `uv run poe provider` is what asks for them. They drive it
against a socket on loopback that this module serves — never against a provider, never with a
credential, and never over an address that leaves the machine — so the adapter stays checkable where
it is changed without any job depending on an endpoint. `docs/adr/0006` records that reading of the
criterion and what it costs.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest
from fastapi import FastAPI, Request, Response
from pydantic import SecretStr, ValidationError

import claim_triage
from claim_triage.config import (
    DEFAULT_MODEL_FIXTURES,
    DEFAULT_MODEL_TIMEOUT_SECONDS,
    MODEL_ENV_PREFIX,
    ModelProvider,
    ModelSettings,
)
from claim_triage.contract.models import ClaimSubmission
from claim_triage.model.port import (
    ModelCall,
    ModelRefused,
    ModelUnavailable,
    NoFixture,
    UnusableAnswer,
    UnusableFixture,
)
from claim_triage.model.provider import ProviderModel
from claim_triage.model.replay import ReplayModel
from claim_triage.model.select import model_from
from support import free_port

if TYPE_CHECKING:
    from collections.abc import Callable

TASK: Final = (
    "Fill in the claim's own fields from this claim notification: the policy the claim is made "
    "against, the day the loss happened, and the amount claimed."
)
"""The task the committed fixture answers, as a literal: a fixture is data, so the test that drives
it states the call rather than reading it back out of the file it is testing."""

NOTIFICATION: Final = (
    "Oznámenie škody - motorové vozidlo (synthetic sample)\n"
    "Poistná zmluva: SIM-2026-0001\n"
    "Dátum škodovej udalosti: 14. 03. 2026\n"
    "Nahlásená výška škody: 1 840,50 EUR\n"
    "EČV: BL-123XY"
)
"""The content the committed fixture answers, likewise."""

ANSWER: Final = {
    "policy_number": "SIM-2026-0001",
    "incident_date": "2026-03-14",
    "claim_amount_eur": "1840.50",
}

KEY: Final = "s3cret-key"
"""A key that exists only in this test: no endpoint behind any of it ever sees a real one."""

TIMEOUT_SECONDS: Final = 5.0
"""How long a test lets a socket it serves itself take. The configured default is the settings'."""

PACKAGE: Final = Path(claim_triage.__file__).parent
"""The installed package, which the two structural tests read."""


def claim_from_a_notification(content: str = NOTIFICATION) -> ModelCall[ClaimSubmission]:
    """The call the fixture answers: a claim notification, read into the claim's own fields."""
    return ModelCall(task=TASK, answer_as=ClaimSubmission, content=content)


def recording(directory: Path, *, name: str = "recorded.json", **overrides: object) -> Path:
    """One fixture on disk, answering the committed call unless a case says otherwise."""
    fixture: dict[str, object] = {
        "task": TASK,
        "content": NOTIFICATION,
        "answer_as": claim_from_a_notification().schema,
        "answer": dict(ANSWER),
        **overrides,
    }
    path = directory / name
    path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    return path


def completing(answer: object) -> str:
    """What an OpenAI-compatible endpoint answers with: choices, and one message carrying it."""
    content = answer if isinstance(answer, str) else json.dumps(answer)
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]})


def an_environment_that_says_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No `.env` to read and no model variable set, so a settings class is built from nothing."""
    monkeypatch.chdir(tmp_path)
    for field in ModelSettings.model_fields:
        monkeypatch.delenv(f"{MODEL_ENV_PREFIX}{field.upper()}", raising=False)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModelSettings:
    """Configuration from an environment with nothing in it."""
    an_environment_that_says_nothing(monkeypatch, tmp_path)
    return ModelSettings()


class FakeProvider:
    """A socket that answers the way an OpenAI-compatible endpoint would, and remembers the call.

    Nothing here is a provider: it is a loopback port this test serves, on the one path an endpoint
    of that shape answers on, so what the adapter puts on the wire can be read back and asserted.
    """

    def __init__(self, *, status_code: int = 200, body: str = "") -> None:
        self.requests: list[dict[str, Any]] = []
        self.authorization: str | None = None
        self._status_code = status_code
        self._body = body

    def app(self) -> FastAPI:
        """The endpoint: one route, and a record of every call that reached it."""
        endpoint = FastAPI()

        @endpoint.post("/v1/chat/completions")
        def answer(payload: dict[str, Any], request: Request) -> Response:
            self.requests.append(payload)
            self.authorization = request.headers.get("authorization")
            return Response(
                content=self._body, status_code=self._status_code, media_type="application/json"
            )

        return endpoint


def test_a_call_is_answered_in_the_shape_it_asked_for() -> None:
    """The committed fixture answers the call it was recorded for, in the schema's own types."""
    claim = ReplayModel(DEFAULT_MODEL_FIXTURES).answer(claim_from_a_notification())

    assert isinstance(claim, ClaimSubmission)
    assert claim.policy_number == ANSWER["policy_number"]
    assert claim.incident_date.isoformat() == ANSWER["incident_date"]
    assert str(claim.claim_amount_eur) == ANSWER["claim_amount_eur"]


def test_replay_answers_with_the_same_bytes_on_every_run() -> None:
    """Two adapters, two reads, one answer — serialised to the same bytes both times."""
    call = claim_from_a_notification()

    first = ReplayModel(DEFAULT_MODEL_FIXTURES).answer(call)
    second = ReplayModel(DEFAULT_MODEL_FIXTURES).answer(call)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert first.model_dump_json() == (
        '{"policy_number":"SIM-2026-0001","incident_date":"2026-03-14",'
        '"claim_amount_eur":"1840.50"}'
    )


def test_a_call_no_fixture_answers_is_refused_naming_what_is_recorded() -> None:
    """A fixture answers a call by its content, so an edited document is a call nothing answers.

    It fails as the port's own unavailability, which is all a stage imports: `NoFixture` is that
    failure with the reason a fixture set gives, and nothing outside the port has to be named.
    """
    edited = claim_from_a_notification(content=f"{NOTIFICATION}\nDodatočná poznámka: žiadna.")

    with pytest.raises(ModelUnavailable) as refused:
        ReplayModel(DEFAULT_MODEL_FIXTURES).answer(edited)

    assert isinstance(refused.value, NoFixture)
    message = str(refused.value)
    assert TASK in message
    assert str(DEFAULT_MODEL_FIXTURES) in message


def test_a_fixture_that_no_longer_fits_the_schema_is_refused_naming_the_file(
    tmp_path: Path,
) -> None:
    """A schema whose fields changed fails where the answer is read, in the stage that asked."""
    path = recording(tmp_path, answer={"policy_number": "SIM-2026-0001"})

    with pytest.raises(UnusableAnswer) as unusable:
        ReplayModel(tmp_path).answer(claim_from_a_notification())

    assert isinstance(unusable.value, UnusableFixture)
    assert str(path) in str(unusable.value)


def test_a_fixtures_directory_that_is_not_there_is_refused_naming_it(tmp_path: Path) -> None:
    with pytest.raises(LookupError) as refused:
        ReplayModel(tmp_path / "absent")

    assert str(tmp_path / "absent") in str(refused.value)


def test_a_file_that_is_not_a_fixture_is_refused_naming_it(tmp_path: Path) -> None:
    (tmp_path / "notes.json").write_text('{"task": "half a fixture"}', encoding="utf-8")

    with pytest.raises(UnusableAnswer) as unusable:
        ReplayModel(tmp_path)

    assert "notes.json" in str(unusable.value)


def test_two_files_recording_the_same_call_are_refused(tmp_path: Path) -> None:
    """A call has one answer in a set: two would make which one answers a matter of file order."""
    first = recording(tmp_path, name="first.json")
    second = recording(tmp_path, name="second.json")

    with pytest.raises(UnusableAnswer) as unusable:
        ReplayModel(tmp_path)

    assert str(first) in str(unusable.value)
    assert str(second) in str(unusable.value)


def test_an_empty_variable_leaves_its_field_at_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A variable exported empty is one nobody set, as compose's own pass-throughs are.

    Read as a value, an empty fixtures directory would be the working directory: replay would answer
    from whatever `.json` the process happened to be started beside.
    """
    an_environment_that_says_nothing(monkeypatch, tmp_path)
    monkeypatch.setenv(f"{MODEL_ENV_PREFIX}FIXTURES", "")
    monkeypatch.setenv(f"{MODEL_ENV_PREFIX}TIMEOUT_SECONDS", "")
    monkeypatch.setenv(f"{MODEL_ENV_PREFIX}PROVIDER", "")

    settings = ModelSettings()

    assert settings.fixtures == DEFAULT_MODEL_FIXTURES
    assert settings.timeout_seconds == DEFAULT_MODEL_TIMEOUT_SECONDS
    assert settings.provider is ModelProvider.REPLAY


def test_a_provider_is_only_reachable_when_configuration_names_one(
    configured: ModelSettings,
) -> None:
    """Replay until configuration says otherwise: an endpoint and a model name are what it takes,
    and a selection naming only one of them is rejected where it is written, not at a call."""
    selected = model_from(configured)

    assert isinstance(selected, ReplayModel)
    assert selected.answer(claim_from_a_notification()).policy_number == ANSWER["policy_number"]

    with pytest.raises(ValidationError) as incomplete:
        ModelSettings(provider=ModelProvider.PROVIDER, base_url="http://model.internal:8080/v1")
    assert f"{MODEL_ENV_PREFIX}NAME" in str(incomplete.value)


@pytest.mark.provider
def test_the_provider_adapter_asks_for_the_shape_and_answers_in_it(
    serve: Callable[[FastAPI], str],
) -> None:
    """One call over a real socket: the wire carries the schema, and the answer comes back typed."""
    provider = FakeProvider(body=completing(ANSWER))

    with ProviderModel(
        f"{serve(provider.app())}/v1", name="local-model", timeout=TIMEOUT_SECONDS
    ) as model:
        claim = model.answer(claim_from_a_notification())

    assert claim.policy_number == ANSWER["policy_number"]
    assert claim.incident_date.isoformat() == ANSWER["incident_date"]
    assert str(claim.claim_amount_eur) == ANSWER["claim_amount_eur"]

    sent = provider.requests[0]
    assert sent["model"] == "local-model"
    # Ours and a document's stay apart on the wire: the task instructs, the content is what is read.
    assert sent["messages"] == [
        {"role": "system", "content": TASK},
        {"role": "user", "content": NOTIFICATION},
    ]
    asked = sent["response_format"]
    assert asked["type"] == "json_schema"
    assert asked["json_schema"]["name"] == "ClaimSubmission"
    assert asked["json_schema"]["schema"] == ClaimSubmission.model_json_schema()
    assert asked["json_schema"]["strict"] is True
    # No key is configured, so none is sent: a local endpoint has no credential to leak.
    assert provider.authorization is None


@pytest.mark.provider
def test_a_configured_key_is_sent_as_a_bearer(serve: Callable[[FastAPI], str]) -> None:
    provider = FakeProvider(body=completing(ANSWER))

    with ProviderModel(
        f"{serve(provider.app())}/v1",
        name="local-model",
        timeout=TIMEOUT_SECONDS,
        api_key=SecretStr(KEY),
    ) as model:
        model.answer(claim_from_a_notification())

    assert provider.authorization == f"Bearer {KEY}"


@pytest.mark.provider
@pytest.mark.parametrize("said", ["model is overloaded", "x" * 900], ids=["brief", "endless"])
def test_a_refusal_is_relayed_with_the_status_and_what_the_endpoint_said(
    serve: Callable[[FastAPI], str], said: str
) -> None:
    provider = FakeProvider(status_code=429, body=json.dumps({"error": {"message": said}}))

    with (
        ProviderModel(
            f"{serve(provider.app())}/v1", name="local-model", timeout=TIMEOUT_SECONDS
        ) as model,
        pytest.raises(ModelRefused) as refused,
    ):
        model.answer(claim_from_a_notification())

    assert refused.value.status_code == 429
    assert said[:20] in refused.value.detail
    # A refusal is read by a human, so what the endpoint said is carried bounded rather than whole.
    assert len(refused.value.detail) <= 600


@pytest.mark.provider
@pytest.mark.parametrize(
    "body",
    [
        "<html>a gateway answered, not the endpoint</html>",
        '{"choices": []}',
        '{"choices": [{"message": {"content": null}}]}',
    ],
    ids=["not-the-wire", "no-choices", "no-content"],
)
def test_a_body_outside_the_completions_wire_is_a_defect(
    serve: Callable[[FastAPI], str], body: str
) -> None:
    """An endpoint that answers 200 with something other than a choice carrying an answer has not
    answered the call: a defect, named as one, rather than a refusal or something to retry."""
    provider = FakeProvider(body=body)

    with (
        ProviderModel(
            f"{serve(provider.app())}/v1", name="local-model", timeout=TIMEOUT_SECONDS
        ) as model,
        pytest.raises(UnusableAnswer),
    ):
        model.answer(claim_from_a_notification())


@pytest.mark.provider
@pytest.mark.parametrize(
    "content",
    ["not JSON at all", json.dumps({"policy_number": "SIM-2026-0001"})],
    ids=["not-json", "not-the-schema"],
)
def test_an_answer_outside_the_schema_is_a_defect_rather_than_a_refusal(
    serve: Callable[[FastAPI], str], content: str
) -> None:
    provider = FakeProvider(body=completing(content))

    with (
        ProviderModel(
            f"{serve(provider.app())}/v1", name="local-model", timeout=TIMEOUT_SECONDS
        ) as model,
        pytest.raises(UnusableAnswer),
    ):
        model.answer(claim_from_a_notification())


@pytest.mark.provider
def test_an_endpoint_that_answers_nothing_is_unavailable() -> None:
    """Nothing listens where the endpoint is configured: the call was not made, which is not a
    refusal the endpoint gave."""
    with (
        ProviderModel(
            f"http://127.0.0.1:{free_port()}/v1", name="local-model", timeout=TIMEOUT_SECONDS
        ) as model,
        pytest.raises(ModelUnavailable),
    ):
        model.answer(claim_from_a_notification())


@pytest.mark.provider
def test_the_provider_adapter_refuses_to_be_built_without_a_destination() -> None:
    with pytest.raises(LookupError):
        ProviderModel("", name="local-model", timeout=TIMEOUT_SECONDS)

    with pytest.raises(LookupError):
        ProviderModel("http://model.internal:8080/v1", name="", timeout=TIMEOUT_SECONDS)


@pytest.mark.provider
def test_the_provider_adapter_is_what_configuration_selects_when_one_is_named(
    serve: Callable[[FastAPI], str],
) -> None:
    """The selection is configuration's, and it reaches the endpoint that configuration names."""
    provider = FakeProvider(body=completing(ANSWER))
    settings = ModelSettings(
        provider=ModelProvider.PROVIDER,
        base_url=f"{serve(provider.app())}/v1",
        name="local-model",
    )
    model = model_from(settings)

    assert isinstance(model, ProviderModel)
    try:
        answer = model.answer(claim_from_a_notification())
    finally:
        model.close()

    assert answer.policy_number == ANSWER["policy_number"]
    assert provider.requests[0]["model"] == "local-model"


def test_only_the_module_that_selects_a_model_imports_an_adapter() -> None:
    """A stage reaches a model through `model_from`, so an adapter has exactly one importer: nothing
    can hold one of them without having gone through the port to get it."""
    for adapter in ("claim_triage.model.provider", "claim_triage.model.replay"):
        assert _importers(adapter) == ("claim_triage.model.select",)


def test_nothing_opens_its_own_connection_to_a_model() -> None:
    """The tree speaks HTTP from three modules, and each is a port's adapter: the generated contract
    client, the client the entry point reaches the triager with, and the model provider. A stage
    that opened its own connection would be a second way to a model, and this is what says so."""
    assert _importers("httpx2") == (
        "claim_triage.contract.client",
        "claim_triage.model.provider",
        "claim_triage.triage.client",
    )


@pytest.mark.parametrize(
    "statement",
    [
        "import claim_triage.model.provider",
        "from claim_triage.model import provider",
        "from claim_triage.model.provider import ProviderModel",
        "from .model import provider",
    ],
)
def test_an_import_is_read_however_it_is_written(statement: str) -> None:
    """The pin above is only as good as the reading it rests on: every way of reaching a module has
    to count, including reaching it out of the package that holds it."""
    assert "claim_triage.model.provider" in _imports(f"{statement}\n", "claim_triage")


def _importers(module: str) -> tuple[str, ...]:
    """The modules of the package that import one module, or anything inside it.

    Anything inside it counts, because the ways to reach a module from another are the ways to reach
    it at all: `import claim_triage.model.provider` and `from claim_triage.model import provider`
    have to read the same here.
    """
    return tuple(
        sorted(
            ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts)
            for path in PACKAGE.rglob("*.py")
            if any(
                imported == module or imported.startswith(f"{module}.")
                for imported in _imported_by(path)
            )
        )
    )


def _imported_by(path: Path) -> set[str]:
    """Every module one file imports, by name, with its own package to resolve relatives against."""
    return _imports(path.read_text(encoding="utf-8"), _package_of(path))


def _package_of(path: Path) -> str:
    """The package a module sits in, named the way a relative import in it is resolved."""
    return ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts[:-1])


def _imports(source: str, package: str) -> set[str]:
    """Every module one file's source imports, by name, read from its syntax rather than its text.

    An `ImportFrom` names both the module it comes from and, per alias, what it takes out of it:
    `from a.b import c` can be the only reference a file has to `a.b.c`, which is a way of reaching
    that module as surely as `import a.b.c` is.
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            source_module = _resolved(package, node.level, node.module or "")
            imported.add(source_module)
            imported.update(
                f"{source_module}.{alias.name}" for alias in node.names if alias.name != "*"
            )
    return imported


def _resolved(package: str, level: int, module: str) -> str:
    """The module an import names: absolute as written, or a relative import resolved against the
    package the importing file sits in."""
    if level == 0:
        return module
    parts = package.split(".") if package else []
    kept = parts if level == 1 else parts[: len(parts) - (level - 1)]
    return ".".join([*kept, module]) if module else ".".join(kept)
