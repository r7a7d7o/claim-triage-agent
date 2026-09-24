"""The audit chain: an intact chain verifies, and every way of changing one does not.

The walking skeleton's third criterion is that tampering with any audit entry is detectable. What
detection means is fixed here, without a database: the chain is verified as data, and each test
takes an intact chain apart in one of the ways an attacker or a careless operator would.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from claim_triage.contract.models import ClaimStatus
from claim_triage.triage.audit import (
    GENESIS,
    Actor,
    AuditEntry,
    AuditEntryContent,
    append,
    digest,
    verify,
)

CLAIM: UUID = UUID("9d7c5b21-4e8f-4a36-b0d2-71f3c6e95a48")
RUN: UUID = UUID("3f2a1c48-6b3d-4e7a-9f21-0c5d8e4b7a96")
RECORDED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def content(node: str = "noop") -> AuditEntryContent:
    """One entry's content: the same claim and run every time, so only the test's change differs."""
    return AuditEntryContent(
        claim_id=CLAIM,
        run_id=RUN,
        node=node,
        actor=Actor.AGENT,
        status=ClaimStatus.TRIAGED,
        experiment="baseline",
        variant="replay",
        trace_id="ab" * 16,
        recorded_at=RECORDED_AT,
    )


def chain(*contents: AuditEntryContent) -> list[AuditEntry]:
    """The chain an append-only log would hold for these entries, oldest first."""
    entries: list[AuditEntry] = []
    prev_hash = GENESIS
    for seq, entry_content in enumerate(contents, start=1):
        entry = append(prev_hash, seq, entry_content)
        entries.append(entry)
        prev_hash = entry.hash
    return entries


def test_a_chain_the_log_appended_verifies() -> None:
    entries = chain(content(), content("summarise"), content("emit"))

    assert [entry.seq for entry in entries] == [1, 2, 3]
    assert entries[0].prev_hash == GENESIS

    verification = verify(entries)

    assert verification.ok
    assert verification.problem is None


def test_an_empty_chain_verifies() -> None:
    """Nothing to disagree with: a claim no run has touched has no chain, not a broken one."""
    assert verify([]).ok


def test_an_entry_changed_after_it_was_written_is_detected() -> None:
    entries = chain(content(), content())
    entries[0] = entries[0].model_copy(update={"status": ClaimStatus.RECEIVED})

    verification = verify(entries)

    assert not verification.ok
    assert verification.problem is not None
    assert "seq 1" in verification.problem


def test_an_entry_rewritten_with_its_hash_recomputed_is_detected() -> None:
    """A tamperer who fixes the entry's own hash still has the next entry's link to explain."""
    entries = chain(content(), content())
    changed = entries[0].model_copy(update={"status": ClaimStatus.RECEIVED})
    entries[0] = changed.model_copy(update={"hash": digest(changed)})

    verification = verify(entries)

    assert not verification.ok
    assert verification.problem is not None
    assert "seq 2" in verification.problem


def test_an_entry_removed_from_the_middle_is_detected() -> None:
    entries = chain(content(), content("summarise"), content("emit"))

    verification = verify([entries[0], entries[2]])

    assert not verification.ok
    assert verification.problem is not None
    assert "seq 3" in verification.problem


def test_the_first_entry_removed_is_detected() -> None:
    entries = chain(content(), content())

    verification = verify(entries[1:])

    assert not verification.ok
    assert verification.problem is not None
    assert "seq 2" in verification.problem


def test_entries_moved_out_of_order_are_detected() -> None:
    entries = chain(content(), content(), content("emit"))

    verification = verify([entries[0], entries[2], entries[1]])

    assert not verification.ok
    assert verification.problem is not None
    assert "seq 3" in verification.problem
    assert "seq 1" in verification.problem


def test_an_entry_that_does_not_follow_the_one_before_it_is_detected() -> None:
    """One entry held twice: the second is not after the first, so a position is held twice."""
    entries = chain(content())

    verification = verify([entries[0], entries[0]])

    assert not verification.ok
    assert verification.problem is not None
    assert "seq 1" in verification.problem
    assert "does not follow seq 1" in verification.problem


def test_an_entry_that_claims_a_position_it_does_not_hold_is_detected() -> None:
    """An entry renumbered to hide a removal is a change to the content the hash covers."""
    entries = chain(content(), content())
    entries[1] = entries[1].model_copy(update={"seq": 7})

    verification = verify(entries)

    assert not verification.ok
    assert verification.problem is not None
    assert "seq 7" in verification.problem


def test_the_digest_covers_every_field_an_entry_holds() -> None:
    """One changed byte anywhere changes the hash: nothing about an entry is outside the chain."""
    entry = chain(content())[0]

    for field, changed in (
        ("claim_id", UUID(int=0)),
        ("run_id", UUID(int=1)),
        ("node", "emit"),
        ("actor", Actor.SYSTEM),
        ("status", ClaimStatus.RECEIVED),
        ("experiment", "extraction-ocr"),
        ("variant", "provider"),
        ("trace_id", "cd" * 16),
        ("recorded_at", RECORDED_AT + timedelta(seconds=1)),
        ("seq", 2),
    ):
        altered = entry.model_copy(update={field: changed})
        assert digest(altered) != entry.hash, f"{field} is outside the digest"
