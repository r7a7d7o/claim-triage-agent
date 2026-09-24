"""The audit transaction against a real Postgres: the promise the in-memory store only doubles.

The walking skeleton's second criterion is that the audit entry exists if and only if the state
change does, and that is a property of a database transaction rather than of the code that asks for
one. Forcing a failure between the two writes is only convincing against the database, so these
tests need the Postgres the contract job starts before it runs anything:

    podman compose up --detach --wait postgres
    uv run poe postgres

The tables the triager owns are cleared before each test, so every chain starts at its genesis, and
what is asserted is read back through the store rather than through the objects that wrote it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from claim_triage.config import InfrastructureSettings
from claim_triage.contract.models import ClaimStatus
from claim_triage.guards.verdict import Check, DocumentVerdicts, Verdict
from claim_triage.triage import audit
from claim_triage.triage.audit import Actor, AuditEntryContent
from claim_triage.triage.run import TriageRun
from claim_triage.triage.store import PostgresTriageStore, TriageStoreUnavailable

pytestmark = pytest.mark.postgres

CLAIM: UUID = UUID("9d7c5b21-4e8f-4a36-b0d2-71f3c6e95a48")
RECORDED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

VERDICTS: list[DocumentVerdicts] = [
    DocumentVerdicts(
        filename="oznamenie-skody.pdf",
        media_type="application/pdf",
        verdicts=[Verdict(check=Check.SIZE, passed=True, detail="1841 bytes, within the ceiling")],
    )
]
"""What the run was admitted with, so these tests carry verdicts through the columns as well."""


class ForcedFailure(Exception):
    """What a test raises where a failure would happen if the writes were not one transaction."""


@pytest.fixture
def dsn() -> str:
    """The DSN the deployables resolve from the environment, which is the one under test."""
    return InfrastructureSettings().postgres_dsn.get_secret_value()


@pytest.fixture
def store(dsn: str) -> PostgresTriageStore:
    """The store the triager serves from, over the empty tables this service owns."""
    store = PostgresTriageStore(dsn)
    with store.transaction():
        pass  # the tables exist before anything clears them
    with psycopg.connect(dsn) as connection:
        connection.execute("TRUNCATE triage_runs, audit_log")
    return store


def a_run() -> TriageRun:
    """One run, with the identifiers fixed so a failure names something readable."""
    return TriageRun(
        run_id=uuid4(),
        claim_id=CLAIM,
        experiment="baseline",
        variant="replay",
        status=ClaimStatus.TRIAGED,
        guard_verdicts=VERDICTS,
        trace_id="ab" * 16,
        started_at=RECORDED_AT,
        completed_at=RECORDED_AT,
    )


def content(run: TriageRun, node: str = "noop") -> AuditEntryContent:
    return AuditEntryContent(
        claim_id=run.claim_id,
        run_id=run.run_id,
        node=node,
        actor=Actor.AGENT,
        status=run.status,
        guard_verdicts=run.guard_verdicts,
        experiment=run.experiment,
        variant=run.variant,
        trace_id=run.trace_id,
        recorded_at=RECORDED_AT,
    )


def test_the_store_reports_ready_over_the_database_it_owns(store: PostgresTriageStore) -> None:
    """What the triager's health route asks: a store that can read its own tables is ready."""
    assert store.ready() is True


def test_a_state_change_and_its_audit_entry_are_one_commit(
    store: PostgresTriageStore, dsn: str
) -> None:
    """What one transaction wrote is what a later process reads, chain and all."""
    run = a_run()

    with store.transaction() as transaction:
        transaction.record_run(run)
        appended = transaction.append_entry(content(run))

    restarted = PostgresTriageStore(dsn)
    assert restarted.read_run(run.run_id) == run

    entries = restarted.entries()
    assert entries == (appended,)
    assert appended.prev_hash == audit.GENESIS
    assert audit.verify(entries).ok


def _both_writes_then_a_failure(store: PostgresTriageStore, run: TriageRun) -> None:
    """Both writes, then a failure before the commit: what a non-transactional log would keep."""
    with store.transaction() as transaction:
        transaction.record_run(run)
        transaction.append_entry(content(run))
        raise ForcedFailure("a failure between the two writes and the commit")


def test_a_failure_between_the_two_writes_and_the_commit_leaves_neither(
    store: PostgresTriageStore,
) -> None:
    run = a_run()

    with pytest.raises(ForcedFailure):
        _both_writes_then_a_failure(store, run)

    assert store.read_run(run.run_id) is None
    assert store.entries() == ()


def _entry_then_a_refused_state_change(store: PostgresTriageStore, run: TriageRun) -> None:
    """The entry first, then a state change the database refuses: a run already recorded."""
    with store.transaction() as transaction:
        transaction.append_entry(content(run, node="summarise"))
        transaction.record_run(run)


def test_a_state_change_that_cannot_be_written_takes_its_audit_entry_with_it(
    store: PostgresTriageStore,
) -> None:
    """The other direction: the entry is written first, and the state change is refused."""
    recorded = a_run()
    with store.transaction() as transaction:
        transaction.record_run(recorded)
        transaction.append_entry(content(recorded))

    with pytest.raises(psycopg.errors.UniqueViolation):
        _entry_then_a_refused_state_change(store, recorded)

    assert [entry.node for entry in store.entries()] == ["noop"]


def test_the_chain_continues_over_what_the_log_already_holds(store: PostgresTriageStore) -> None:
    """Three transactions, one chain: each entry is linked onto the committed one before it."""
    runs = [a_run() for _ in range(3)]
    for run in runs:
        with store.transaction() as transaction:
            transaction.record_run(run)
            transaction.append_entry(content(run, node=f"node-{run.run_id}"))

    entries = store.entries()

    assert [entry.seq for entry in entries] == [1, 2, 3]
    assert [entry.prev_hash for entry in entries[1:]] == [entry.hash for entry in entries[:-1]]
    assert audit.verify(entries).ok


def test_a_store_that_fails_inside_the_transaction_leaves_neither_write(
    store: PostgresTriageStore, dsn: str
) -> None:
    """The database going away between the two writes is a store failure, not a half-written run."""
    run = a_run()

    with pytest.raises(TriageStoreUnavailable):
        _both_writes_over_a_store_that_dies(store, run, dsn)

    assert store.read_run(run.run_id) is None
    assert store.entries() == ()


def _both_writes_over_a_store_that_dies(
    store: PostgresTriageStore, run: TriageRun, dsn: str
) -> None:
    """Both writes, with the connection killed between them: the database's own failure."""
    with store.transaction() as transaction:
        transaction.record_run(run)
        _terminate_the_other_backends(dsn)
        transaction.append_entry(content(run))


def _terminate_the_other_backends(dsn: str) -> None:
    """Kill every backend on this database but this one, which is the store's own connection."""
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE pid <> pg_backend_pid() AND datname = current_database()"
        )


def test_an_entry_changed_in_the_database_is_detected(store: PostgresTriageStore, dsn: str) -> None:
    """Someone editing the log directly — not through the store — is what the chain is for."""
    for _ in range(2):
        with store.transaction() as transaction:
            run = a_run()
            transaction.record_run(run)
            transaction.append_entry(content(run))

    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE audit_log SET status = 'received' WHERE seq = 1")

    verification = audit.verify(store.entries())

    assert not verification.ok
    assert verification.problem is not None
    assert "seq 1" in verification.problem


def test_an_entry_removed_from_the_database_is_detected(
    store: PostgresTriageStore, dsn: str
) -> None:
    for _ in range(3):
        with store.transaction() as transaction:
            run = a_run()
            transaction.record_run(run)
            transaction.append_entry(content(run))

    with psycopg.connect(dsn) as connection:
        connection.execute("DELETE FROM audit_log WHERE seq = 2")

    verification = audit.verify(store.entries())

    assert not verification.ok
    assert verification.problem is not None
    assert "seq 3" in verification.problem
