"""The service the contract tests drive, the two documents they compare, and the marker they carry.

These tests drive the generated client against a real `core-sim` process over a real socket,
backed by a real Postgres — which is what the `contract` CI job starts before it runs them:

    podman compose up --detach --wait postgres
    uv run poe contract

The service is started by this fixture rather than expected to be running, so the tests cannot pass
against a stale process, and the port is picked per run so a development stack on 8080 does not
collide with it. Everything else the tests need is the contract itself: the committed document, and
what the running service says the contract is.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest
import yaml

from claim_triage.contract.client import CoreSimClient

if TYPE_CHECKING:
    from collections.abc import Iterator

SERVICE: Final = "claim-triage-core-sim"
"""The console script the service is started by: the same one the compose stack runs."""

REPOSITORY: Final = Path(__file__).parents[2]
CONTRACT: Final = REPOSITORY / "contracts" / "core-sim.openapi.yaml"

STARTUP_DEADLINE_SECONDS: Final = 30.0
"""How long the service may take to report readiness before the tests fail."""

POLL_SECONDS: Final = 0.1
"""How long to wait between readiness polls: polled for, never assumed after a fixed sleep."""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark this directory's tests: each drives a running service, so none belongs in the unit job.

    The hook is called with every collected item, so the directory is checked per item rather than
    assumed: marking the whole list would take the rest of the suite out of the unit job with it.
    """
    here = Path(__file__).parent
    for item in items:
        if item.path.is_relative_to(here):
            item.add_marker(pytest.mark.contract)


@pytest.fixture(scope="session")
def contract() -> dict[str, Any]:
    """The committed contract: the document the client and the service were generated from."""
    document: object = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


@pytest.fixture(scope="session")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """One real service process on a free port, over the Postgres the environment names."""
    port = _free_port()
    environment = {
        **os.environ,
        "CLAIM_TRIAGE_CORE_SIM_HOST": "127.0.0.1",
        "CLAIM_TRIAGE_CORE_SIM_PORT": str(port),
    }
    url = f"http://127.0.0.1:{port}"
    log = tmp_path_factory.mktemp("core-sim") / "service.log"
    with log.open("w") as output:
        service = subprocess.Popen(
            [_command()], env=environment, stdout=output, stderr=subprocess.STDOUT
        )
        try:
            _wait_until_ready(url, log)
            yield url
        finally:
            service.terminate()
            service.wait(timeout=STARTUP_DEADLINE_SECONDS)


@pytest.fixture
def systems(base_url: str) -> Iterator[CoreSimClient]:
    """The generated client, pointed at the running service."""
    with CoreSimClient(base_url) as client:
        yield client


@pytest.fixture(scope="session")
def served(base_url: str) -> dict[str, Any]:
    """What the running service says the contract is, as FastAPI serves it."""
    document: object = _get(f"{base_url}/openapi.json")
    assert isinstance(document, dict)
    return document


def _command() -> str:
    """The service's console script, from the environment the tests themselves run in."""
    installed = Path(sys.executable).parent / SERVICE
    assert installed.exists(), f"{installed} is missing: run `uv sync` before the contract tests"
    return str(installed)


def _healthz(url: str) -> bool:
    """Whether the service reports readiness right now, whether or not anything is listening yet.

    Anything other than a 200 — a 503 while the store is unreachable, a connection that has not been
    accepted yet — means not ready, and the caller waits for another attempt.
    """
    try:
        _get(f"{url}/healthz", timeout=1.0)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False
    return True


def _get(url: str, timeout: float = 5.0) -> object:
    """One GET against the service, decoded as JSON."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        document: object = json.loads(response.read().decode())
    return document


def _wait_until_ready(url: str, log: Path) -> None:
    deadline = time.monotonic() + STARTUP_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        if _healthz(url):
            return
        time.sleep(POLL_SECONDS)
    raise AssertionError(
        f"{url}/healthz never reported readiness within {STARTUP_DEADLINE_SECONDS}s.\n"
        "The contract tests need the stack's Postgres:\n"
        "    podman compose up --detach --wait postgres\n"
        f"what the service said:\n{log.read_text()}"
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
