"""Where the triager keeps what only it knows: the runs it has done, and their audit entries.

`triager` owns these tables and no other service writes to them (`docs/adr/0001`). The port is what
the pipeline depends on, so the pipeline can be driven without a database; the Postgres
implementation is the one the stack runs, and `tests/test_audit_transaction.py` holds it to the
promise below against a real Postgres.

The promise is the ticket's: a state change and the audit entry that is its evidence are written in
one transaction. `transaction()` hands out both writes, commits once and rolls back once, so an
entry cannot exist without the change it records, and a change cannot exist without its entry.

The chain is global and single-writer. Each append takes a transaction-scoped advisory lock, reads
the entry before it and links itself on, so two writers cannot fork the chain — which a per-claim
chain would not have needed, and a global one cannot do without.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final, Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from claim_triage.triage import audit
from claim_triage.triage.audit import AuditEntry, AuditEntryContent
from claim_triage.triage.run import TriageRun

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager
    from typing import Any

CONNECT_TIMEOUT_SECONDS: Final = 5
"""How long one connection attempt may take before the store counts as unreachable."""

CHAIN_LOCK: Final = 6_297_291
"""The one advisory lock every append takes: an arbitrary constant, shared by every writer."""

SCHEMA: Final = (
    """
    CREATE TABLE IF NOT EXISTS triage_runs (
        run_id       uuid PRIMARY KEY,
        claim_id     uuid NOT NULL,
        experiment   text NOT NULL,
        variant      text NOT NULL,
        status       text NOT NULL,
        trace_id     text NOT NULL,
        started_at   timestamptz NOT NULL,
        completed_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        seq         integer PRIMARY KEY,
        claim_id    uuid NOT NULL,
        run_id      uuid NOT NULL,
        node        text NOT NULL,
        actor       text NOT NULL,
        status      text NOT NULL,
        experiment  text NOT NULL,
        variant     text NOT NULL,
        trace_id    text NOT NULL,
        recorded_at timestamptz NOT NULL,
        prev_hash   text NOT NULL,
        hash        text NOT NULL
    )
    """,
)
"""Every table this service owns, applied one statement at a time before its first use."""

RUN_COLUMNS: Final = (
    "run_id, claim_id, experiment, variant, status, trace_id, started_at, completed_at"
)

ENTRY_COLUMNS: Final = (
    "seq, claim_id, run_id, node, actor, status, experiment, variant, trace_id, recorded_at,"
    " prev_hash, hash"
)


class TriageStoreUnavailable(Exception):
    """The store is up but cannot be written to or read from right now."""


class TriageTransaction(Protocol):
    """One transaction's writes: what a run ends with, or nothing at all."""

    def record_run(self, run: TriageRun) -> None:
        """Record the state change: where this run left the claim, or fail the transaction."""
        ...

    def append_entry(self, content: AuditEntryContent) -> AuditEntry:
        """Append the entry that is the state change's evidence, linked onto the chain."""
        ...


class TriageStore(Protocol):
    """The triager's own tables, as the pipeline needs them."""

    def ready(self) -> bool:
        """Whether the records this service owns are reachable right now.

        Unreachable is an answer here rather than a failure: the health route of every deployable in
        front of this asks, and turns "no" into a 503.
        """
        ...

    def read_run(self, run_id: UUID) -> TriageRun | None:
        """The run this identifier names, if it was recorded at all."""
        ...

    def entries(self) -> tuple[AuditEntry, ...]:
        """The whole audit chain, oldest first."""
        ...

    def transaction(self) -> AbstractContextManager[TriageTransaction]:
        """One transaction: everything written inside it commits together, or none of it does."""
        ...


class PostgresTriageStore:
    """The tables the triager owns, one connection per operation."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._schema_applied = False
        self._schema_lock = threading.Lock()

    def ready(self) -> bool:
        """Whether the records this service owns are reachable right now."""
        try:
            with self._connection() as connection:
                connection.execute("SELECT 1")
        except (psycopg.Error, TriageStoreUnavailable):
            # A store that cannot reach Postgres answers "no" rather than raising: readiness is
            # asked as a question by the health route, and an unanswered question is a 500 where
            # the contract promises a 503.
            return False
        return True

    def read_run(self, run_id: UUID) -> TriageRun | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {RUN_COLUMNS} FROM triage_runs WHERE run_id = %s", (run_id,)
            ).fetchone()
        return None if row is None else TriageRun(**row)

    def entries(self) -> tuple[AuditEntry, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {ENTRY_COLUMNS} FROM audit_log ORDER BY seq"
            ).fetchall()
        return tuple(AuditEntry(**row) for row in rows)

    @contextmanager
    def transaction(self) -> Iterator[TriageTransaction]:
        """One connection, one commit: the state change and the entry that records it, together."""
        try:
            connection = psycopg.connect(
                self._dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS, row_factory=dict_row
            )
        except psycopg.OperationalError as unreachable:
            raise TriageStoreUnavailable(
                f"the triage store is unreachable: {unreachable}"
            ) from None
        with connection:
            self._apply_schema(connection)
            try:
                yield _PostgresTriageTransaction(connection)
            except (psycopg.OperationalError, psycopg.InterfaceError) as failure:
                raise TriageStoreUnavailable(f"the triage store failed: {failure}") from None

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        """One read-only connection, owning the schema this service applies before its first use."""
        try:
            connection = psycopg.connect(
                self._dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS, row_factory=dict_row
            )
        except psycopg.OperationalError as unreachable:
            raise TriageStoreUnavailable(
                f"the triage store is unreachable: {unreachable}"
            ) from None
        with connection:
            self._apply_schema(connection)
            yield connection

    def _apply_schema(self, connection: psycopg.Connection[Any]) -> None:
        if self._schema_applied:
            return
        with self._schema_lock:
            if not self._schema_applied:
                for statement in SCHEMA:
                    connection.execute(statement)
                self._schema_applied = True


class _PostgresTriageTransaction:
    """The writes one transaction makes, over the one connection it was opened with."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def record_run(self, run: TriageRun) -> None:
        """Insert the run; a run already recorded fails the transaction rather than overwriting."""
        self._connection.execute(
            f"INSERT INTO triage_runs ({RUN_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run.run_id,
                run.claim_id,
                run.experiment,
                run.variant,
                run.status,
                run.trace_id,
                run.started_at,
                run.completed_at,
            ),
        )

    def append_entry(self, content: AuditEntryContent) -> AuditEntry:
        """Link one entry onto the chain: what was before it, and its own hash over all of it."""
        self._connection.execute("SELECT pg_advisory_xact_lock(%s)", (CHAIN_LOCK,))
        previous = self._connection.execute(
            "SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq, prev_hash = audit.continues(
            None if previous is None else (int(previous["seq"]), str(previous["hash"]))
        )
        entry = audit.append(prev_hash, seq, content)
        self._connection.execute(
            f"INSERT INTO audit_log ({ENTRY_COLUMNS})"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                entry.seq,
                entry.claim_id,
                entry.run_id,
                entry.node,
                entry.actor,
                entry.status,
                entry.experiment,
                entry.variant,
                entry.trace_id,
                entry.recorded_at,
                entry.prev_hash,
                entry.hash,
            ),
        )
        return entry
