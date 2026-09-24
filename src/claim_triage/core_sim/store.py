"""Where the surrounding systems keep what they hold: one port, and the Postgres implementation.

`core-sim` owns these tables and no other service writes to them (see `docs/adr/0001`). The port is
what the ASGI surface depends on, so the surface can be driven without a database in unit tests; the
Postgres implementation is the one the contract job exercises through the running service.

Every write here is deduplicated by the `Idempotency-Key` the contract requires for it: the key, a
fingerprint of the request that carried it and the response that write produced are recorded in the
same transaction as the write itself. A retry therefore answers with the first call's response and
changes nothing, while a key that arrives carrying a different request is refused rather than
answered with an outcome belonging to another request.

The schema is applied by the service that owns it, on the first connection that succeeds, rather
than by the Postgres image or a migration tool: there is one increment's worth of tables and no
version to migrate yet. The policies the systems hold are seeded at that same moment, from
`claim_triage.core_sim.seed`.
"""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel

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

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

CONNECT_TIMEOUT_SECONDS: Final = 5
"""How long one connection attempt may take before it counts as unreachable."""

SCHEMA: Final = (
    """
    CREATE TABLE IF NOT EXISTS policies (
        policy_number  text PRIMARY KEY,
        product_family text NOT NULL,
        valid_from     date NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claims (
        claim_id         uuid PRIMARY KEY,
        policy_number    text NOT NULL,
        incident_date    date NOT NULL,
        claim_amount_eur numeric(12, 2) NOT NULL,
        status           text NOT NULL,
        status_history   jsonb NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claim_documents (
        document_id   uuid PRIMARY KEY,
        claim_id      uuid NOT NULL REFERENCES claims (claim_id),
        document_type text NOT NULL,
        filename      text NOT NULL,
        attached_at   timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claim_parties (
        party_id    uuid PRIMARY KEY,
        claim_id    uuid NOT NULL REFERENCES claims (claim_id),
        role        text NOT NULL,
        recorded_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_keys (
        key         text PRIMARY KEY,
        fingerprint text NOT NULL,
        response    jsonb NOT NULL,
        created_at  timestamptz NOT NULL
    )
    """,
)
"""Every table this service owns, applied one statement at a time before its first use."""

SEED_POLICY: Final = """
INSERT INTO policies (policy_number, product_family, valid_from) VALUES (%s, %s, %s)
ON CONFLICT (policy_number) DO NOTHING
"""

CLAIM_COLUMNS: Final = (
    "claim_id, policy_number, incident_date, claim_amount_eur, status, status_history"
)

DOCUMENT_COLUMNS: Final = "document_id, claim_id, document_type, filename, attached_at"

PARTY_COLUMNS: Final = "party_id, claim_id, role, recorded_at"


class ClaimNotFound(LookupError):
    """Raised for an operation that names a claim the surrounding systems do not hold."""


class PolicyNotFound(LookupError):
    """Raised for an operation that names a policy the surrounding systems do not hold."""


class IdempotencyKeyReuse(Exception):
    """Raised when a key that already answered one request arrives carrying a different one."""


class CoreSimStore(Protocol):
    """Everything the surrounding systems hold, as their ASGI surface needs it."""

    def ready(self) -> bool:
        """Whether the records the systems hold are reachable right now."""
        ...

    def read_policy(self, policy_number: str) -> Policy:
        """Return the policy, or raise `PolicyNotFound`."""
        ...

    def create(self, submission: ClaimSubmission, key: str) -> Claim:
        """Record a submission as a claim the systems have received."""
        ...

    def read(self, claim_id: UUID) -> Claim:
        """Return the claim, or raise `ClaimNotFound`."""
        ...

    def record_status(self, claim_id: UUID, status: ClaimStatus, key: str) -> Claim:
        """Append a transition and return the updated claim, or raise `ClaimNotFound`."""
        ...

    def attach_document(
        self, claim_id: UUID, attachment: ClaimDocumentSubmission, key: str
    ) -> ClaimDocument:
        """Record a document against a claim, or raise `ClaimNotFound`."""
        ...

    def read_parties(self, claim_id: UUID) -> tuple[ClaimParty, ...]:
        """Every party the claim carries, oldest first, or raise `ClaimNotFound`."""
        ...

    def record_party(
        self, claim_id: UUID, registration: ClaimPartySubmission, key: str
    ) -> ClaimParty:
        """Record a party against a claim, or raise `ClaimNotFound`."""
        ...


def request_fingerprint(operation: str, request: Mapping[str, Any]) -> str:
    """What identifies one write: the operation, and the request that carried it.

    The store, and the double the unit tests use in its place, fingerprint through this one
    function, so neither can decide that two different requests are the same write.
    """
    canonical = json.dumps({"operation": operation, "request": dict(request)}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class PostgresCoreSimStore:
    """The tables the surrounding systems own, one connection per operation."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._schema_applied = False
        self._schema_lock = threading.Lock()

    def ready(self) -> bool:
        try:
            with self._connection() as connection:
                connection.execute("SELECT 1")
        except psycopg.Error:
            return False
        return True

    def read_policy(self, policy_number: str) -> Policy:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT policy_number, product_family, valid_from FROM policies"
                " WHERE policy_number = %s",
                (policy_number,),
            ).fetchone()
        if row is None:
            raise PolicyNotFound(f"unknown policy {policy_number}")
        return Policy(**row)

    def create(self, submission: ClaimSubmission, key: str) -> Claim:
        with self._connection() as connection:
            return self._idempotent(
                connection,
                key=key,
                fingerprint=request_fingerprint("create_claim", submission.model_dump(mode="json")),
                model=Claim,
                perform=lambda: self._create(connection, submission),
            )

    def read(self, claim_id: UUID) -> Claim:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {CLAIM_COLUMNS} FROM claims WHERE claim_id = %s", (claim_id,)
            ).fetchone()
        return _claim(claim_id, row)

    def record_status(self, claim_id: UUID, status: ClaimStatus, key: str) -> Claim:
        with self._connection() as connection:
            return self._idempotent(
                connection,
                key=key,
                fingerprint=request_fingerprint(
                    "record_claim_status", {"claim_id": str(claim_id), "status": status}
                ),
                model=Claim,
                perform=lambda: self._record_status(connection, claim_id, status),
            )

    def attach_document(
        self, claim_id: UUID, attachment: ClaimDocumentSubmission, key: str
    ) -> ClaimDocument:
        with self._connection() as connection:
            return self._idempotent(
                connection,
                key=key,
                fingerprint=request_fingerprint(
                    "attach_claim_document",
                    {"claim_id": str(claim_id), **attachment.model_dump(mode="json")},
                ),
                model=ClaimDocument,
                perform=lambda: self._attach_document(connection, claim_id, attachment),
            )

    def read_parties(self, claim_id: UUID) -> tuple[ClaimParty, ...]:
        with self._connection() as connection:
            _require_claim(connection, claim_id)
            rows = connection.execute(
                f"SELECT {PARTY_COLUMNS} FROM claim_parties WHERE claim_id = %s"
                " ORDER BY recorded_at, party_id",
                (claim_id,),
            ).fetchall()
        return tuple(ClaimParty(**row) for row in rows)

    def record_party(
        self, claim_id: UUID, registration: ClaimPartySubmission, key: str
    ) -> ClaimParty:
        with self._connection() as connection:
            return self._idempotent(
                connection,
                key=key,
                fingerprint=request_fingerprint(
                    "record_claim_party",
                    {"claim_id": str(claim_id), **registration.model_dump(mode="json")},
                ),
                model=ClaimParty,
                perform=lambda: self._record_party(connection, claim_id, registration),
            )

    def _create(self, connection: psycopg.Connection[Any], submission: ClaimSubmission) -> Claim:
        """The claim the systems hold once they have taken a submission in, and its first status."""
        if (
            connection.execute(
                "SELECT 1 FROM policies WHERE policy_number = %s", (submission.policy_number,)
            ).fetchone()
            is None
        ):
            raise PolicyNotFound(f"unknown policy {submission.policy_number}")
        claim = Claim(
            claim_id=uuid4(),
            status=ClaimStatus.RECEIVED,
            status_history=[
                ClaimStatusTransition(status=ClaimStatus.RECEIVED, recorded_at=datetime.now(UTC))
            ],
            **submission.model_dump(),
        )
        connection.execute(
            f"INSERT INTO claims ({CLAIM_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                claim.claim_id,
                claim.policy_number,
                claim.incident_date,
                claim.claim_amount_eur,
                claim.status,
                _json(claim.status_history),
            ),
        )
        return claim

    def _record_status(
        self, connection: psycopg.Connection[Any], claim_id: UUID, status: ClaimStatus
    ) -> Claim:
        """One accepted report of where the pipeline got to, appended to the claim's history."""
        row = connection.execute(
            f"SELECT {CLAIM_COLUMNS} FROM claims WHERE claim_id = %s FOR UPDATE", (claim_id,)
        ).fetchone()
        claim = _claim(claim_id, row)
        updated = claim.model_copy(
            update={
                "status": status,
                "status_history": [
                    *claim.status_history,
                    ClaimStatusTransition(status=status, recorded_at=datetime.now(UTC)),
                ],
            }
        )
        connection.execute(
            "UPDATE claims SET status = %s, status_history = %s WHERE claim_id = %s",
            (updated.status, _json(updated.status_history), claim_id),
        )
        return updated

    def _attach_document(
        self,
        connection: psycopg.Connection[Any],
        claim_id: UUID,
        attachment: ClaimDocumentSubmission,
    ) -> ClaimDocument:
        _require_claim(connection, claim_id)
        document = ClaimDocument(
            document_id=uuid4(),
            claim_id=claim_id,
            attached_at=datetime.now(UTC),
            **attachment.model_dump(),
        )
        connection.execute(
            f"INSERT INTO claim_documents ({DOCUMENT_COLUMNS}) VALUES (%s, %s, %s, %s, %s)",
            (
                document.document_id,
                document.claim_id,
                document.document_type,
                document.filename,
                document.attached_at,
            ),
        )
        return document

    def _record_party(
        self,
        connection: psycopg.Connection[Any],
        claim_id: UUID,
        registration: ClaimPartySubmission,
    ) -> ClaimParty:
        _require_claim(connection, claim_id)
        party = ClaimParty(
            party_id=uuid4(),
            claim_id=claim_id,
            recorded_at=datetime.now(UTC),
            **registration.model_dump(),
        )
        connection.execute(
            f"INSERT INTO claim_parties ({PARTY_COLUMNS}) VALUES (%s, %s, %s, %s)",
            (party.party_id, party.claim_id, party.role, party.recorded_at),
        )
        return party

    def _idempotent[T: BaseModel](
        self,
        connection: psycopg.Connection[Any],
        *,
        key: str,
        fingerprint: str,
        model: type[T],
        perform: Callable[[], T],
    ) -> T:
        """Run one write once per key: a retry answers what the first call answered."""
        remembered = self._remember(connection, key)
        if remembered is not None:
            return self._replayed(remembered, key=key, fingerprint=fingerprint, model=model)
        written = perform()
        try:
            connection.execute(
                "INSERT INTO idempotency_keys (key, fingerprint, response, created_at)"
                " VALUES (%s, %s, %s, %s)",
                (key, fingerprint, json.dumps(written.model_dump(mode="json")), datetime.now(UTC)),
            )
        except psycopg.errors.UniqueViolation:
            # Another attempt with this key committed between this one's read and its insert. That
            # attempt's write is the one that counts, so this one rolls back everything it wrote and
            # answers with what the other recorded, rather than failing a retry that did its job.
            connection.rollback()
            concurrent = self._remember(connection, key)
            if concurrent is None:
                raise
            return self._replayed(concurrent, key=key, fingerprint=fingerprint, model=model)
        return written

    def _remember(self, connection: psycopg.Connection[Any], key: str) -> dict[str, Any] | None:
        """What this key already recorded, if it recorded anything."""
        remembered = connection.execute(
            "SELECT fingerprint, response FROM idempotency_keys WHERE key = %s", (key,)
        ).fetchone()
        return remembered

    def _replayed[T: BaseModel](
        self, remembered: Mapping[str, Any], *, key: str, fingerprint: str, model: type[T]
    ) -> T:
        """The answer a key already gave, or the refusal that says it answered another request."""
        if remembered["fingerprint"] != fingerprint:
            raise IdempotencyKeyReuse(
                f"Idempotency-Key {key} was already used for a different request"
            )
        return model.model_validate(remembered["response"])

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        """One committed connection, owning the schema this service applies before its first use."""
        with psycopg.connect(
            self._dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS, row_factory=dict_row
        ) as connection:
            self._apply_schema(connection)
            yield connection

    def _apply_schema(self, connection: psycopg.Connection[Any]) -> None:
        if self._schema_applied:
            return
        with self._schema_lock:
            if not self._schema_applied:
                for statement in SCHEMA:
                    connection.execute(statement)
                for policy in SEED_POLICIES:
                    seed = (policy.policy_number, policy.product_family, policy.valid_from)
                    connection.execute(SEED_POLICY, seed)
                self._schema_applied = True


def _require_claim(connection: psycopg.Connection[Any], claim_id: UUID) -> None:
    if (
        connection.execute("SELECT 1 FROM claims WHERE claim_id = %s", (claim_id,)).fetchone()
        is None
    ):
        raise ClaimNotFound(f"unknown claim {claim_id}")


def _claim(claim_id: UUID, row: dict[str, Any] | None) -> Claim:
    if row is None:
        raise ClaimNotFound(f"unknown claim {claim_id}")
    return Claim(**row)


def _json(history: list[ClaimStatusTransition]) -> str:
    return json.dumps([transition.model_dump(mode="json") for transition in history])
