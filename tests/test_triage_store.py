"""The triager's store: what it answers when the database it owns is not there.

Readiness is the one operation where an unreachable database is an answer rather than a failure: the
health route of the triager and the api's relay of it turn it into a 503, the compose stack's health
checks depend on that, and the smoke waits on it. Every other operation is asked for, and raises.

This needs no database: an address nothing listens on refuses a connection immediately.
"""

from __future__ import annotations

import pytest

from claim_triage.triage.store import PostgresTriageStore, TriageStoreUnavailable
from support import free_port


def test_a_store_whose_database_is_not_there_is_not_ready() -> None:
    """Asked whether it can serve, a store that cannot reach Postgres says no, not raise."""
    store = PostgresTriageStore(_nowhere())

    assert store.ready() is False


def test_an_operation_against_a_database_that_is_not_there_names_what_is_wrong() -> None:
    """Asked for something, the store raises the failure the pipeline translates into a 503."""
    store = PostgresTriageStore(_nowhere())

    with pytest.raises(TriageStoreUnavailable, match="unreachable"):
        store.entries()


def _nowhere() -> str:
    """A DSN for a Postgres that is not there: a port that was reserved and then left unbound."""
    return f"postgresql://claim_triage:claim_triage@127.0.0.1:{free_port()}/claim_triage"
