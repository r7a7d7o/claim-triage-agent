"""The smoke run: one claim end to end through a served stack, and the ways it fails.

The smoke is what the container job runs inside the running stack, so these tests wire the same
three surfaces it drives — the entry point, the triager behind it, and the simulated surrounding
systems — and serve them for real over loopback ports. The stores are the in-memory doubles, and
nothing here waits on a fixed sleep for readiness to become true.

The failures get as much attention as the happy path, because this command is the only thing
between a broken stack and a green container job: a stack that answers nonsense, or refuses the
claim, has to end the run non-zero with the reason it refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from claim_triage import smoke
from claim_triage.api.surface import create_app as create_api
from claim_triage.contract.client import CoreSimClient
from claim_triage.contract.models import Claim, ClaimStatus, ClaimStatusTransition
from claim_triage.core_sim.app import create_app as create_systems
from claim_triage.telemetry import configure
from claim_triage.triage.client import RunClient
from claim_triage.triage.graph import build_graph
from claim_triage.triage.pipeline import TriagePipeline
from claim_triage.triage.run import RunResult
from claim_triage.triage.surface import create_app as create_triager
from support import TEST_GUARD, InMemoryCoreSim, InMemoryTriageStore, free_port

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from fastapi import FastAPI as App

POLL_SECONDS = 0.01
"""How long to wait between readiness polls: polled for, never assumed after a fixed sleep."""

CLAIM_ID: UUID = uuid4()
"""The claim every scripted answer is about, so one fixture serves every case."""

RUN: dict[str, Any] = RunResult(
    run_id=uuid4(),
    claim_id=CLAIM_ID,
    status=ClaimStatus.TRIAGED,
    experiment="baseline",
    variant="replay",
    trace_id="ab" * 16,
).model_dump(mode="json")

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
"""What a well-behaved surrounding system answers with, before and after the emission."""

TRIAGED: dict[str, Any] = {
    **CLAIM,
    "status": ClaimStatus.TRIAGED.value,
    "status_history": [
        *CLAIM["status_history"],
        {"status": ClaimStatus.TRIAGED.value, "recorded_at": "2026-09-24T12:00:01Z"},
    ],
}

UNAVAILABLE: dict[str, Any] = {"code": "service_unavailable", "detail": "not ready yet"}

READY: dict[str, Any] = {"status": "ok"}


@pytest.fixture(autouse=True)
def poll_quickly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry immediately: the wait between attempts is what the deadline is for, not the test."""
    monkeypatch.setattr(smoke, "POLL_SECONDS", POLL_SECONDS)


@dataclass(frozen=True, slots=True)
class Stack:
    """A served stack: where the smoke reaches each surface, and the stores behind them."""

    entry_point: str
    systems_url: str
    systems: InMemoryCoreSim
    triage: InMemoryTriageStore


@pytest.fixture
def stack(serve: Callable[[App], str]) -> Callable[[], Stack]:
    """Serve the three surfaces the smoke drives, and answer with where to reach them."""
    traces = configure("smoke-tests", environment="test")

    def _stack() -> Stack:
        systems = InMemoryCoreSim()
        triage = InMemoryTriageStore()
        systems_url = serve(create_systems(systems))
        pipeline = TriagePipeline(
            graph=build_graph(),
            systems=CoreSimClient(systems_url),
            store=triage,
            telemetry=traces,
        )
        runs = RunClient(serve(create_triager(pipeline)))
        return Stack(
            entry_point=serve(create_api(runs, telemetry=traces, guard=TEST_GUARD)),
            systems_url=systems_url,
            systems=systems,
            triage=triage,
        )

    return _stack


def test_one_claim_reaches_the_surrounding_systems(
    stack: Callable[[], Stack], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wired = stack()
    _pointed_at(monkeypatch, tmp_path, entry_point=wired.entry_point, systems=wired.systems_url)

    assert smoke.main() == 0

    assert len(wired.systems.claims) == 1
    assert wired.systems.claims[0].status == ClaimStatus.TRIAGED
    assert [entry.status for entry in wired.triage.entries()] == [ClaimStatus.TRIAGED]


def test_the_smoke_waits_out_a_stack_that_is_not_ready_yet(
    serve: Callable[[App], str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scripted = ScriptedStack(
        (503, UNAVAILABLE),
        (503, UNAVAILABLE),
        (200, READY),
        (201, RUN),
        (200, TRIAGED),
    )
    url = serve(scripted.app())
    _pointed_at(monkeypatch, tmp_path, entry_point=url, systems=url)

    assert smoke.main() == 0

    assert scripted.paths[:3] == ["GET /healthz", "GET /healthz", "GET /healthz"]


def test_a_stack_that_never_answers_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(smoke, "DEADLINE_SECONDS", 0.5)
    _pointed_at(monkeypatch, tmp_path, entry_point=f"http://127.0.0.1:{free_port()}")

    assert smoke.main() == 1

    assert "did not answer in time" in capsys.readouterr().err


def test_a_refused_claim_fails_the_run(
    serve: Callable[[App], str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    refusal = {"code": "policy_not_found", "detail": "unknown policy SIM-2026-0001"}
    scripted = ScriptedStack((200, READY), (404, refusal))
    url = serve(scripted.app())
    _pointed_at(monkeypatch, tmp_path, entry_point=url, systems=url)

    assert smoke.main() == 1

    assert "policy_not_found" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        (((200, READY), (201, "not json at all")), "answered non-JSON"),
        (((200, READY), (201, {**RUN, "status": "paid"})), "the wire does not cover"),
        (((200, READY), (202, RUN)), "outside the boundary's vocabulary"),
        (((200, READY), (201, {**RUN, "status": ClaimStatus.RECEIVED.value})), "to emit"),
        (((200, READY), (201, RUN), (200, CLAIM)), "surrounding systems hold"),
    ],
    ids=[
        "body-not-json",
        "status-outside-the-wire",
        "unexpected-status",
        "run-in-the-wrong-state",
        "claim-never-became-triaged",
    ],
)
def test_an_answer_the_stack_should_never_give_fails_the_run(
    serve: Callable[[App], str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    answers: tuple[tuple[int, object], ...],
    expected: str,
) -> None:
    url = serve(ScriptedStack(*answers).app())
    _pointed_at(monkeypatch, tmp_path, entry_point=url, systems=url)

    assert smoke.main() == 1

    assert expected in capsys.readouterr().err


class ScriptedStack:
    """A stack that answers exactly what a test scripts.

    The real stack cannot produce these answers, and the smoke's behaviour on them is what decides
    whether the container job could go green on a stack that is not carrying the claim. One app
    stands in for both services: the paths tell them apart.
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


def _pointed_at(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    entry_point: str,
    systems: str | None = None,
) -> None:
    """Run the smoke against what this test serves, under test alone: no ambient variables."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAIM_TRIAGE_API_BASE_URL", entry_point)
    monkeypatch.setenv("CLAIM_TRIAGE_CORE_SIM_BASE_URL", systems or entry_point)
