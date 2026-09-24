"""The smoke run: one claim end to end through a served stack, and the ways it fails.

The smoke is what the container job runs inside the running stack, so these tests drive it over real
HTTP against the real ASGI app — the store and the process boundary are the only things doubled, and
nothing here waits on a fixed sleep for readiness to become true.

The failures get as much attention as the happy path, because this command is the only thing between
a broken stack and a green container job: a stack that answers nonsense, or refuses the claim,
has to end the run non-zero with the reason it refused.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from claim_triage import smoke
from claim_triage.contract.models import Claim, ClaimStatus, ClaimStatusTransition
from claim_triage.core_sim.app import create_app
from support import InMemoryCoreSim

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

STARTUP_DEADLINE_SECONDS = 10.0
"""How long the served stack may take to accept connections before the test fails."""

POLL_SECONDS = 0.01
"""How long to wait between readiness polls: polled for, never assumed after a fixed sleep."""

CLAIM_ID: UUID = uuid4()
"""The claim every scripted answer is about, so one fixture serves every case."""

CLAIM: dict[str, Any] = Claim(
    claim_id=CLAIM_ID,
    status=ClaimStatus.RECEIVED,
    status_history=[
        ClaimStatusTransition(
            status=ClaimStatus.RECEIVED, recorded_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
        )
    ],
    policy_number="SIM-2026-0001",
    incident_date=date(2026, 3, 14),
    claim_amount_eur=Decimal("1840.50"),
).model_dump(mode="json")
"""What a well-behaved surrounding system answers with, for tests about the answers themselves."""

TRIAGED: dict[str, Any] = {
    **CLAIM,
    "status": ClaimStatus.TRIAGED.value,
    "status_history": [
        *CLAIM["status_history"],
        {"status": ClaimStatus.TRIAGED.value, "recorded_at": "2026-09-24T12:00:01Z"},
    ],
}


@pytest.fixture
def systems() -> InMemoryCoreSim:
    return InMemoryCoreSim()


@pytest.fixture
def serve() -> Iterator[Callable[[FastAPI], str]]:
    """Serve apps for real on loopback ports, and stop every one of them afterwards."""
    served: list[tuple[uvicorn.Server, threading.Thread]] = []

    def _serve(app: FastAPI) -> str:
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        served.append((server, thread))
        _wait_until_listening(port)
        return f"http://127.0.0.1:{port}"

    yield _serve

    for server, _ in served:
        server.should_exit = True
    for _, thread in served:
        thread.join(STARTUP_DEADLINE_SECONDS)


@pytest.fixture
def base_url(serve: Callable[[FastAPI], str], systems: InMemoryCoreSim) -> str:
    """The surrounding systems, served for real on a loopback port."""
    return serve(create_app(systems))


@pytest.fixture(autouse=True)
def poll_quickly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry immediately: the wait between attempts is what the deadline is for, not the test."""
    monkeypatch.setattr(smoke, "POLL_SECONDS", POLL_SECONDS)


def test_one_claim_reaches_the_surrounding_systems(
    base_url: str, systems: InMemoryCoreSim, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pointed_at(monkeypatch, tmp_path, base_url)

    assert smoke.main() == 0

    assert len(systems.claims) == 1
    assert systems.claims[0].status == ClaimStatus.TRIAGED


def test_the_smoke_waits_out_a_stack_that_is_not_ready_yet(
    serve: Callable[[FastAPI], str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    starting: tuple[int, object] = (
        503,
        {"code": "service_unavailable", "detail": "the claims are unreachable"},
    )
    scripted = ScriptedStack(
        starting,
        starting,
        (200, {"status": "ok"}),
        (201, CLAIM),
        (200, CLAIM),
        (200, TRIAGED),
        (200, TRIAGED),
    )
    _pointed_at(monkeypatch, tmp_path, serve(scripted.app()))

    assert smoke.main() == 0

    assert scripted.paths[:3] == ["GET /healthz", "GET /healthz", "GET /healthz"]


def test_a_stack_that_never_answers_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(smoke, "DEADLINE_SECONDS", 0.5)
    _pointed_at(monkeypatch, tmp_path, f"http://127.0.0.1:{_free_port()}")

    assert smoke.main() == 1

    assert "did not answer in time" in capsys.readouterr().err


def test_a_refused_write_fails_the_run(
    serve: Callable[[FastAPI], str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    refusal = {"code": "invalid_payload", "detail": "body.claim_amount_eur: greater than 0"}
    scripted = ScriptedStack((200, {"status": "ok"}), (422, refusal))
    _pointed_at(monkeypatch, tmp_path, serve(scripted.app()))

    assert smoke.main() == 1

    assert "invalid_payload" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ((201, "not json at all"), "answered non-JSON"),
        ((201, {**CLAIM, "status": "paid"}), "the contract does not cover"),
        ((202, CLAIM), "outside the contract"),
    ],
    ids=["body-not-json", "status-outside-the-contract", "unexpected-status"],
)
def test_an_answer_the_contract_does_not_allow_fails_the_run(
    serve: Callable[[FastAPI], str],
    answer: tuple[int, object],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scripted = ScriptedStack((200, {"status": "ok"}), answer)
    _pointed_at(monkeypatch, tmp_path, serve(scripted.app()))

    assert smoke.main() == 1

    assert expected in capsys.readouterr().err


class ScriptedStack:
    """A stack that answers exactly what a test scripts.

    The real surrounding systems cannot produce these answers, and the smoke's behaviour on them is
    what decides whether the container job could go green on a stack that is not carrying the claim.
    """

    def __init__(self, *responses: tuple[int, object]) -> None:
        self._responses = list(responses)
        self.paths: list[str] = []

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.api_route("/{path:path}", methods=["GET", "POST"])
        def answer(request: Request) -> Response:
            self.paths.append(f"{request.method} /{request.url.path.lstrip('/')}")
            last = len(self._responses) - 1
            status_code, body = self._responses[min(len(self.paths) - 1, last)]
            if isinstance(body, str):
                return Response(content=body, status_code=status_code, media_type="text/plain")
            return JSONResponse(content=body, status_code=status_code)

        return app


def _pointed_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base_url: str) -> None:
    """Run the smoke against `base_url` under test alone: no ambient .env file or variables."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAIM_TRIAGE_CORE_SIM_BASE_URL", base_url)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_until_listening(port: int) -> None:
    deadline = time.monotonic() + STARTUP_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(POLL_SECONDS)
    raise AssertionError(f"nothing listened on 127.0.0.1:{port} within {STARTUP_DEADLINE_SECONDS}s")
