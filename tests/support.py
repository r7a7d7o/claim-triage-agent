"""Test doubles, at the boundaries the specification names, and the ways a test serves an app.

The surrounding systems are one of those boundaries: the real implementation is the Postgres tables
`core-sim` owns, which the contract job exercises through the running service. The triager's tables
are the other: the real implementation is the Postgres store the pipeline records through, held to
the same promise by `tests/test_audit_transaction.py`, which needs the database the unit job has
none of. Both doubles keep what they hold in dictionaries and answer in exactly the shapes their
ports promise — nothing more, so a test cannot pass on behaviour the database would not have. Both
fingerprint writes through the same functions the stores do, so the two cannot disagree about what
counts as the same request, or about what an entry hashes to.

`free_port` and `wait_until_listening` are what serving an app for real needs, and live here rather
than in one test module because more than one test module serves one.
"""

from __future__ import annotations

import socket
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from claim_triage.contract.models import (
    Claim,
    ClaimDocument,
    ClaimDocumentSubmission,
    ClaimParty,
    ClaimPartySubmission,
    ClaimStatus,
    ClaimStatusTransition,
    ClaimSubmission,
    Policy,
)
from claim_triage.core_sim.seed import SEED_POLICIES
from claim_triage.core_sim.store import (
    ClaimNotFound,
    IdempotencyKeyReuse,
    PolicyNotFound,
    request_fingerprint,
)
from claim_triage.triage import audit
from claim_triage.triage.audit import AuditEntry, AuditEntryContent
from claim_triage.triage.run import TriageRun
from claim_triage.triage.store import TriageStoreUnavailable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

STARTUP_DEADLINE_SECONDS: float = 10.0
"""How long a served app may take to accept connections before a test fails."""

LISTEN_POLL_SECONDS: float = 0.01
"""How long to wait between connection attempts: polled for, never assumed after a fixed sleep."""

Remembered = dict[str, object]
"""One recorded write: the fingerprint of its request, and the response it answered with."""


class InMemoryCoreSim:
    """A `CoreSimStore` over dictionaries, with the same answers as the Postgres one."""

    def __init__(self, *, ready: bool = True) -> None:
        self._ready = ready
        self._policies: dict[str, Policy] = {
            policy.policy_number: policy for policy in SEED_POLICIES
        }
        self._claims: dict[UUID, Claim] = {}
        self._documents: dict[UUID, ClaimDocument] = {}
        self._parties: dict[UUID, ClaimParty] = {}
        self._remembered: dict[str, Remembered] = {}

    @property
    def claims(self) -> tuple[Claim, ...]:
        """What the surrounding systems hold, for tests that assert on the outcome."""
        return tuple(self._claims.values())

    def ready(self) -> bool:
        return self._ready

    def read_policy(self, policy_number: str) -> Policy:
        try:
            return self._policies[policy_number]
        except KeyError:
            raise PolicyNotFound(f"unknown policy {policy_number}") from None

    def create(self, submission: ClaimSubmission, key: str) -> Claim:
        def perform() -> Claim:
            self.read_policy(submission.policy_number)
            claim = Claim(
                claim_id=uuid4(),
                status=ClaimStatus.RECEIVED,
                status_history=[
                    ClaimStatusTransition(
                        status=ClaimStatus.RECEIVED, recorded_at=datetime.now(UTC)
                    )
                ],
                **submission.model_dump(),
            )
            self._claims[claim.claim_id] = claim
            return claim

        return self._once("create_claim", submission.model_dump(mode="json"), key, perform)

    def read(self, claim_id: UUID) -> Claim:
        try:
            return self._claims[claim_id]
        except KeyError:
            raise ClaimNotFound(f"unknown claim {claim_id}") from None

    def record_status(self, claim_id: UUID, status: ClaimStatus, key: str) -> Claim:
        def perform() -> Claim:
            claim = self.read(claim_id)
            updated = claim.model_copy(
                update={
                    "status": status,
                    "status_history": [
                        *claim.status_history,
                        ClaimStatusTransition(status=status, recorded_at=datetime.now(UTC)),
                    ],
                }
            )
            self._claims[claim_id] = updated
            return updated

        request = {"claim_id": str(claim_id), "status": status}
        return self._once("record_claim_status", request, key, perform)

    def attach_document(
        self, claim_id: UUID, attachment: ClaimDocumentSubmission, key: str
    ) -> ClaimDocument:
        def perform() -> ClaimDocument:
            self.read(claim_id)
            document = ClaimDocument(
                document_id=uuid4(),
                claim_id=claim_id,
                attached_at=datetime.now(UTC),
                **attachment.model_dump(),
            )
            self._documents[document.document_id] = document
            return document

        request = {"claim_id": str(claim_id), **attachment.model_dump(mode="json")}
        return self._once("attach_claim_document", request, key, perform)

    def read_parties(self, claim_id: UUID) -> tuple[ClaimParty, ...]:
        self.read(claim_id)
        return tuple(party for party in self._parties.values() if party.claim_id == claim_id)

    def record_party(
        self, claim_id: UUID, registration: ClaimPartySubmission, key: str
    ) -> ClaimParty:
        def perform() -> ClaimParty:
            self.read(claim_id)
            party = ClaimParty(
                party_id=uuid4(),
                claim_id=claim_id,
                recorded_at=datetime.now(UTC),
                **registration.model_dump(),
            )
            self._parties[party.party_id] = party
            return party

        request = {"claim_id": str(claim_id), **registration.model_dump(mode="json")}
        return self._once("record_claim_party", request, key, perform)

    def _once[T](
        self, operation: str, request: Mapping[str, object], key: str, perform: Callable[[], T]
    ) -> T:
        """Run one write once per key, as the transaction in the store does."""
        fingerprint = request_fingerprint(operation, request)
        remembered = self._remembered.get(key)
        if remembered is not None:
            if remembered["fingerprint"] != fingerprint:
                raise IdempotencyKeyReuse(
                    f"Idempotency-Key {key} was already used for a different request"
                )
            return cast("T", remembered["response"])
        written = perform()
        self._remembered[key] = {"fingerprint": fingerprint, "response": written}
        return written


def free_port() -> int:
    """A port nothing is listening on right now, for a test to serve an app on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_until_listening(port: int) -> None:
    """Wait until something accepts connections on `port`, failing rather than sleeping forever."""
    deadline = time.monotonic() + STARTUP_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(LISTEN_POLL_SECONDS)
    raise AssertionError(f"nothing listened on 127.0.0.1:{port} within {STARTUP_DEADLINE_SECONDS}s")


class InMemoryTriageStore:
    """A `TriageStore` over dictionaries, with the Postgres one's transaction semantics.

    The transaction is real in the only way a dictionary can hold one: everything it writes is taken
    back when anything inside it raises, so a test can force a failure between the state change and
    the audit entry and observe that neither survives. The chain is appended through the same
    function the Postgres store appends through, so the two cannot disagree about what an entry
    hashes to.
    """

    def __init__(self, *, ready: bool = True) -> None:
        self._ready = ready
        self._runs: dict[UUID, TriageRun] = {}
        self._entries: list[AuditEntry] = []

    @property
    def runs(self) -> tuple[TriageRun, ...]:
        """What has been recorded, for tests that assert on the outcome."""
        return tuple(self._runs.values())

    def ready(self) -> bool:
        return self._ready

    def read_run(self, run_id: UUID) -> TriageRun | None:
        return self._runs.get(run_id)

    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    @contextmanager
    def transaction(self) -> Iterator[InMemoryTriageTransaction]:
        """Everything this transaction writes, or nothing: what the store's port promises."""
        held = (dict(self._runs), list(self._entries))
        try:
            yield InMemoryTriageTransaction(self)
        except BaseException:
            self._runs, self._entries = held
            raise

    def _record(self, run: TriageRun) -> None:
        if run.run_id in self._runs:
            raise TriageStoreUnavailable(f"run {run.run_id} is already recorded")
        self._runs[run.run_id] = run

    def _append(self, content: AuditEntryContent) -> AuditEntry:
        last = None if not self._entries else (self._entries[-1].seq, self._entries[-1].hash)
        seq, prev_hash = audit.continues(last)
        entry = audit.append(prev_hash, seq, content)
        self._entries.append(entry)
        return entry


class InMemoryTriageTransaction:
    """One transaction over the dictionary store: both writes, or neither."""

    def __init__(self, store: InMemoryTriageStore) -> None:
        self._store = store

    def record_run(self, run: TriageRun) -> None:
        self._store._record(run)

    def append_entry(self, content: AuditEntryContent) -> AuditEntry:
        return self._store._append(content)
