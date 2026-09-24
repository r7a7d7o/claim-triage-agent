"""The scaffold's operator-facing contract: documented deployables start and report themselves.

The names, console scripts, ports and scaling axes below are pinned as literals — the contract
README's deployables table states — rather than read back from the registry, so that the test can
disagree with the code and the two cannot drift together.
"""

from __future__ import annotations

import importlib.metadata
import json
from typing import TYPE_CHECKING, cast

import pytest

from claim_triage.services.registry import DEPLOYABLES, deployable

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# name, console script, default port, scaling axis
CONTRACT: tuple[tuple[str, str, int, str], ...] = (
    ("api", "claim-triage-api", 8000, "requests per second"),
    ("triager", "claim-triage-triager", 8001, "queue depth"),
    ("extraction", "claim-triage-extraction", 8002, "queue depth and CPU"),
    ("retrieval", "claim-triage-retrieval", 8003, "requests per second"),
    ("guardrail-policy", "claim-triage-guardrail-policy", 8004, "replica count"),
    ("reviewer-ui", "claim-triage-reviewer-ui", 8005, "human sessions"),
    ("core-sim", "claim-triage-core-sim", 8080, "replica count"),
)

INFRASTRUCTURE_VARIABLES = (
    "ENVIRONMENT",
    "POSTGRES_DSN",
    "REDIS_URL",
    "QDRANT_URL",
    "CORE_SIM_BASE_URL",
    "OTEL_ENDPOINT",
    "LANGFUSE_HOST",
)


def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str) -> None:
    """Resolve configuration from the environment under test alone: no ambient .env or variables."""
    monkeypatch.chdir(tmp_path)
    for name, _, _, _ in CONTRACT:
        prefix = f"CLAIM_TRIAGE_{name.upper().replace('-', '_')}_"
        for field in ("HOST", "PORT"):
            monkeypatch.delenv(f"{prefix}{field}", raising=False)
    for field in INFRASTRUCTURE_VARIABLES:
        monkeypatch.delenv(f"CLAIM_TRIAGE_{field}", raising=False)
    for variable, value in overrides.items():
        monkeypatch.setenv(variable, value)


def _console_script(name: str) -> Callable[[], int]:
    scripts = {
        entry.name: entry for entry in importlib.metadata.entry_points(group="console_scripts")
    }
    assert name in scripts, f"{name} is not an installed console script"
    return cast("Callable[[], int]", scripts[name].load())


def test_registry_states_the_documented_contract() -> None:
    assert [deployable.name for deployable in DEPLOYABLES] == [name for name, _, _, _ in CONTRACT]

    for name, console_script, default_port, scaling_axis in CONTRACT:
        subject = deployable(name)
        assert subject.console_script == console_script
        assert subject.default_port == default_port
        assert subject.scaling_axis == scaling_axis


@pytest.mark.parametrize(
    ("name", "console_script", "default_port"),
    [row[:3] for row in CONTRACT],
    ids=[row[0] for row in CONTRACT],
)
def test_deployable_starts_and_reports_its_configuration(
    name: str,
    console_script: str,
    default_port: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolated(monkeypatch, tmp_path)

    assert _console_script(console_script)() == 0

    report = json.loads(capsys.readouterr().out)
    assert report["service"] == name
    assert report["port"] == default_port
    assert report["qdrant_url"] == "http://localhost:6333"


def test_environment_overrides_win_and_credentials_are_masked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dsn = "postgresql://scout:s3cret@db.internal:5432/triage"
    _isolated(
        monkeypatch,
        tmp_path,
        CLAIM_TRIAGE_API_PORT="9123",
        CLAIM_TRIAGE_QDRANT_URL="http://qdrant.internal:6333",
        CLAIM_TRIAGE_POSTGRES_DSN=dsn,
    )

    assert _console_script("claim-triage-api")() == 0
    captured = capsys.readouterr().out

    report = json.loads(captured)
    assert report["port"] == 9123
    assert report["qdrant_url"] == "http://qdrant.internal:6333"
    assert report["postgres_dsn"] != dsn
    assert "s3cret" not in captured


def test_rejected_configuration_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolated(monkeypatch, tmp_path, CLAIM_TRIAGE_API_PORT="not-a-port")

    assert _console_script("claim-triage-api")() == 2

    assert "CLAIM_TRIAGE_API_PORT" in capsys.readouterr().err
