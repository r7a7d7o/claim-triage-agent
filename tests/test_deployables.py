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

from claim_triage.bootstrap import resolve_and_report
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
    "API_BASE_URL",
    "TRIAGER_BASE_URL",
    "OTEL_ENDPOINT",
    "LANGFUSE_HOST",
)

SERVES: tuple[str, ...] = ("core-sim", "triager", "api")
"""The deployables that serve a surface, so their console scripts run until they are stopped.

`core-sim` joined in ticket 02, `triager` and `api` in ticket 04: the graph, its records and the
entry point in front of them. What each one serves is driven by its own test module, and
`test_serving_deployable_reports_before_it_serves` checks they all report before they serve.
"""

REPORTS_AND_EXITS: tuple[tuple[str, str, int, str], ...] = tuple(
    row for row in CONTRACT if row[0] not in SERVES
)
"""The deployables whose surface has not landed: they report their configuration and exit."""


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


def test_unknown_deployable_is_rejected_naming_the_known_ones() -> None:
    with pytest.raises(LookupError) as raised:
        deployable("apii")

    message = str(raised.value)
    assert "apii" in message
    for name, _, _, _ in CONTRACT:
        assert name in message


@pytest.mark.parametrize(
    ("name", "console_script", "default_port"),
    [row[:3] for row in REPORTS_AND_EXITS],
    ids=[row[0] for row in REPORTS_AND_EXITS],
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


@pytest.mark.parametrize("name", SERVES)
def test_serving_deployable_reports_before_it_serves(
    name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    default_port = deployable(name).default_port
    host_variable = f"CLAIM_TRIAGE_{name.upper().replace('-', '_')}_HOST"
    _isolated(monkeypatch, tmp_path, **{host_variable: "0.0.0.0"})

    resolution, exit_code = resolve_and_report(name)

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["service"] == name
    assert report["host"] == "0.0.0.0"
    assert report["port"] == default_port
    assert resolution is not None
    assert (resolution.host, resolution.port) == ("0.0.0.0", default_port)


def test_environment_overrides_win_and_credentials_are_masked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dsn = "postgresql://scout:s3cret@db.internal:5432/triage"
    _isolated(
        monkeypatch,
        tmp_path,
        CLAIM_TRIAGE_EXTRACTION_PORT="9123",
        CLAIM_TRIAGE_QDRANT_URL="http://qdrant.internal:6333",
        CLAIM_TRIAGE_POSTGRES_DSN=dsn,
    )

    assert _console_script("claim-triage-extraction")() == 0
    captured = capsys.readouterr().out

    report = json.loads(captured)
    assert report["port"] == 9123
    assert report["triager_base_url"] == "http://localhost:8001"
    assert report["qdrant_url"] == "http://qdrant.internal:6333"
    assert report["postgres_dsn"] != dsn
    assert "s3cret" not in captured


def test_rejected_configuration_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolated(monkeypatch, tmp_path, CLAIM_TRIAGE_EXTRACTION_PORT="not-a-port")

    assert _console_script("claim-triage-extraction")() == 2

    assert "CLAIM_TRIAGE_EXTRACTION_PORT" in capsys.readouterr().err


def test_an_empty_observability_endpoint_means_the_stack_is_down(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Compose passes its own variables through as empty strings; empty means unset, not ""."""
    _isolated(
        monkeypatch,
        tmp_path,
        CLAIM_TRIAGE_OTEL_ENDPOINT="",
        CLAIM_TRIAGE_LANGFUSE_HOST="",
    )

    assert _console_script("claim-triage-extraction")() == 0

    report = json.loads(capsys.readouterr().out)
    assert report["otel_endpoint"] is None
    assert report["langfuse_host"] is None
