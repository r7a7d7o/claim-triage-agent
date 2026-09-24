"""Prove the running stack can carry one claim end to end, from inside the stack.

Until ticket 04 wires the graph, this command stands in for the `triager`: it submits a claim to the
simulated surrounding systems over the compose network, records the status transition the pipeline
will later record, and reads both back. It exits 0 only once the claim has survived the whole path,
so the container job cannot report success on containers merely being up.

Every wait is a bounded poll against a deadline: an unreachable service or a 503 means the stack is
not ready yet, and anything else is a failure this command reports and exits 1 on.
"""

from __future__ import annotations

import json
import sys
import time
from typing import TYPE_CHECKING, Any, Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from claim_triage.config import InfrastructureSettings

if TYPE_CHECKING:
    from collections.abc import Mapping

DEADLINE_SECONDS: Final = 120.0
"""How long the stack may take to carry the claim before this run fails."""

POLL_SECONDS: Final = 0.25
"""How long to wait between readiness polls: readiness is polled for, never assumed by sleeping."""

REQUEST_TIMEOUT_SECONDS: Final = 5.0
"""How long one HTTP attempt may take before the service counts as unreachable."""

SUBMISSION: Final[dict[str, Any]] = {
    "policy_number": "SIM-2026-0001",
    "incident_date": "2026-03-14",
    "claim_amount_eur": "1840.50",
}
"""The one claim this smoke run submits.

A literal, not a generated document: ticket 13 generates the synthetic claim corpus, and this claim
exists only to prove the stack moves one from end to end.
"""


class SmokeFailed(Exception):
    """What the stack did instead of carrying the claim."""


def main() -> int:
    """Drive one claim through the running stack, printing what the stack did."""
    base_url = InfrastructureSettings().core_sim_base_url.rstrip("/")
    deadline = time.monotonic() + DEADLINE_SECONDS
    started = time.monotonic()

    try:
        submitted = _call(base_url, "POST", "/claims", deadline, body=SUBMISSION, creates=True)
        claim_id = str(submitted["claim_id"])
        _expect(submitted, "status", "received")
        amount = submitted["claim_amount_eur"]
        print(f"submitted claim {claim_id}: status received, amount {amount}")

        stored = _call(base_url, "GET", f"/claims/{claim_id}", deadline)
        _expect(stored, "status", "received")
        print(f"read claim {claim_id} back from the stack: status received")

        _call(base_url, "POST", f"/claims/{claim_id}/status", deadline, body={"status": "triaged"})

        settled = _call(base_url, "GET", f"/claims/{claim_id}", deadline)
        _expect(settled, "status", "triaged")
        print(f"recorded and read back status triaged for claim {claim_id}")
    except SmokeFailed as failure:
        print(f"smoke failed: {failure}", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    print(f"one claim processed end to end through {base_url} in {elapsed:.2f}s")
    return 0


def _call(
    base_url: str,
    method: str,
    path: str,
    deadline: float,
    *,
    body: Mapping[str, Any] | None = None,
    creates: bool = False,
) -> dict[str, Any]:
    """One call against the stack, polling while it is not ready and failing on any other answer."""
    payload = None if body is None else json.dumps(body).encode()
    request = Request(
        f"{base_url}{path}",
        data=payload,
        method=method,
        headers={"content-type": "application/json"} if payload is not None else {},
    )
    expected = {201} if creates else {200}

    while True:
        try:
            status_code, document = _attempt(request)
        except HTTPError as refused:
            if refused.code == 503 and _wait_for_another_attempt(deadline):
                continue
            raise SmokeFailed(
                f"{method} {request.full_url} answered {refused.code} {refused.reason}:"
                f" {_body(refused)}"
            ) from None
        except (URLError, OSError) as unreachable:
            if _wait_for_another_attempt(deadline):
                continue
            raise SmokeFailed(f"{method} {request.full_url} unreachable: {unreachable}") from None

        if status_code not in expected:
            raise SmokeFailed(
                f"{method} {request.full_url} answered {status_code}, expected {sorted(expected)}"
            )
        return document


def _attempt(request: Request) -> tuple[int, dict[str, Any]]:
    """Perform one HTTP attempt, requiring an object back."""
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        status_code = response.status
        body = response.read().decode(errors="replace")

    try:
        document = json.loads(body)
    except ValueError as not_json:
        raise SmokeFailed(f"{request.full_url} answered non-JSON: {not_json}") from None
    if not isinstance(document, dict):
        raise SmokeFailed(f"{request.full_url} answered {type(document).__name__}, not an object")
    return status_code, document


def _wait_for_another_attempt(deadline: float) -> bool:
    """Wait one poll interval while the deadline allows another attempt."""
    if time.monotonic() >= deadline:
        return False
    time.sleep(POLL_SECONDS)
    return True


def _expect(document: Mapping[str, Any], field: str, value: str) -> None:
    if document.get(field) != value:
        got = document.get(field)
        raise SmokeFailed(f"expected {field}={value!r}, got {got!r} in {dict(document)}")


def _body(refused: HTTPError) -> str:
    return refused.read().decode(errors="replace")[:200]
