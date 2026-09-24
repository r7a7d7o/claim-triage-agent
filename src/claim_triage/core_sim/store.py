"""Where the surrounding systems keep their claims: one port, and the Postgres implementation.

`core-sim` owns this table and no other service writes to it (see `docs/adr/0001`). The port is what
the ASGI surface depends on, so the surface can be driven without a database in unit tests; the
Postgres implementation is the one the container smoke job exercises against the running stack.

The schema is applied by the service that owns it, on the first connection that succeeds, rather
than by the Postgres image or a migration tool: there is one table and no version to migrate yet.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from claim_triage.core_sim.models import Claim, ClaimStatus, ClaimSubmission

if TYPE_CHECKING:
    from collections.abc import Iterator

CONNECT_TIMEOUT_SECONDS: Final = 5
"""How long one connection attempt may take before it counts as unreachable."""

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id         uuid PRIMARY KEY,
    policy_number    text NOT NULL,
    incident_date    date NOT NULL,
    claim_amount_eur numeric(12, 2) NOT NULL,
    status           text NOT NULL,
    updated_at       timestamptz NOT NULL
)
"""

COLUMNS: Final = "claim_id, policy_number, incident_date, claim_amount_eur, status, updated_at"


class ClaimNotFound(LookupError):
    """Raised for an operation that names a claim the surrounding systems do not hold."""


class ClaimStore(Protocol):
    """The claims the surrounding systems hold, as the ASGI surface needs them."""

    def create(self, submission: ClaimSubmission) -> Claim:
        """Record a submission as a claim the systems have received."""
        ...

    def read(self, claim_id: UUID) -> Claim:
        """Return the claim, or raise `ClaimNotFound`."""
        ...

    def record_status(self, claim_id: UUID, status: ClaimStatus) -> Claim:
        """Move a claim to `status` and return the updated record, or raise `ClaimNotFound`."""
        ...

    def ready(self) -> bool:
        """Whether the claims are reachable right now."""
        ...


class PostgresClaimStore:
    """The claims table in Postgres, one connection per operation."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._schema_applied = False
        self._schema_lock = threading.Lock()

    def create(self, submission: ClaimSubmission) -> Claim:
        claim = submission.as_received(uuid4(), datetime.now(UTC))
        with self._connection() as connection:
            connection.execute(
                f"INSERT INTO claims ({COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    claim.claim_id,
                    claim.policy_number,
                    claim.incident_date,
                    claim.claim_amount_eur,
                    claim.status,
                    claim.updated_at,
                ),
            )
        return claim

    def read(self, claim_id: UUID) -> Claim:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {COLUMNS} FROM claims WHERE claim_id = %s", (claim_id,)
            ).fetchone()
        return _claim(claim_id, row)

    def record_status(self, claim_id: UUID, status: ClaimStatus) -> Claim:
        with self._connection() as connection:
            row = connection.execute(
                f"UPDATE claims SET status = %s, updated_at = %s WHERE claim_id = %s"
                f" RETURNING {COLUMNS}",
                (status, datetime.now(UTC), claim_id),
            ).fetchone()
        return _claim(claim_id, row)

    def ready(self) -> bool:
        try:
            with self._connection() as connection:
                connection.execute("SELECT 1")
        except psycopg.Error:
            return False
        return True

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
                connection.execute(SCHEMA)
                self._schema_applied = True


def _claim(claim_id: UUID, row: dict[str, Any] | None) -> Claim:
    if row is None:
        raise ClaimNotFound(f"unknown claim {claim_id}")
    return Claim(**row)
