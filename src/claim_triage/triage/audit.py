"""The audit log's chain: one entry, what it hashes to, and what tampering with it looks like.

The log is append-only and hash-chained: every entry carries the hash of the entry before it and a
digest of everything it holds, so an entry that was changed, moved or removed after it was written
stops matching. `verify` is the whole of the detection and is a pure function over entries, so what
a tampered chain looks like is testable without a database, and the store that appends the chain
reuses the same digest rather than restating it.

Honest limit, stated rather than implied: a chain proves that what is present is what was written.
Without an external anchor, an attacker who rewrites the whole chain — every entry, and the hashes
that link them — leaves nothing to disagree with. Anchoring the chain head outside the database is
ticket 42's work; what this increment gives is that no single entry, and no gap between them, can be
changed or removed quietly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from claim_triage.contract.models import ClaimStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

GENESIS: str = "0" * 64
"""What the first entry in a chain carries as the hash before it: there is no entry before it."""


class Actor(StrEnum):
    """Who caused the state change an entry records."""

    AGENT = "agent"
    SYSTEM = "system"
    HUMAN = "human"


class AuditEntryContent(BaseModel):
    """What the pipeline submits for the log to record: everything but the chain itself."""

    model_config = ConfigDict(extra="forbid")

    claim_id: UUID
    run_id: UUID
    # The graph node that acted, as the topology names it.
    node: str
    actor: Actor
    # Where the claim stands now: the state change this entry is the evidence for.
    status: ClaimStatus
    experiment: str
    variant: str
    trace_id: str
    recorded_at: datetime


class AuditEntry(AuditEntryContent):
    """One stored entry: what it records, where it sits in the chain, and what it hashes to."""

    seq: int
    prev_hash: str
    hash: str


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """What verifying a chain found: whether it is intact, and what broke if not."""

    ok: bool
    problem: str | None = None


def digest(entry: AuditEntry) -> str:
    """The hash one entry has: its position, its predecessor and everything it records."""
    return hashlib.sha256(_canonical(entry).encode()).hexdigest()


def append(prev_hash: str, seq: int, content: AuditEntryContent) -> AuditEntry:
    """The entry an append produces: the content, where it sits, and the hash that links it on."""
    entry = AuditEntry(
        **content.model_dump(),
        seq=seq,
        prev_hash=prev_hash,
        hash=GENESIS,
    )
    return entry.model_copy(update={"hash": digest(entry)})


def continues(previous: tuple[int, str] | None) -> tuple[int, str]:
    """Where the next entry sits: one past the last one, linked onto its hash.

    A chain that was emptied starts again at the genesis. Both stores that append a chain derive the
    next entry's position through this, so the database and the double the unit tests use cannot
    disagree about where an entry belongs or what it hashes onto.
    """
    if previous is None:
        return 1, GENESIS
    seq, previous_hash = previous
    return seq + 1, previous_hash


def verify(entries: Sequence[AuditEntry]) -> ChainVerification:
    """Whether a chain is intact: every entry where it was written, and hashing to what it says."""
    previous: AuditEntry | None = None
    for entry in entries:
        if previous is None:
            if entry.prev_hash != GENESIS:
                return _broken(
                    entry.seq, "the chain starts at an entry whose predecessor is missing"
                )
        elif entry.seq <= previous.seq:
            return _broken(entry.seq, f"it does not follow seq {previous.seq}")
        elif entry.prev_hash != previous.hash:
            return _broken(
                entry.seq, f"the entry after seq {previous.seq} is missing, or this moved"
            )
        if digest(entry) != entry.hash:
            return _broken(entry.seq, "it no longer hashes to what it says it holds")
        previous = entry
    return ChainVerification(ok=True)


def _broken(seq: int, problem: str) -> ChainVerification:
    return ChainVerification(ok=False, problem=f"the entry at seq {seq}: {problem}")


def _canonical(entry: AuditEntry) -> str:
    """Everything an entry holds, in one deterministic form: the hash covers all of it."""
    recorded: dict[str, object] = {
        "claim_id": str(entry.claim_id),
        "run_id": str(entry.run_id),
        "node": entry.node,
        "actor": str(entry.actor),
        "status": str(entry.status),
        "experiment": entry.experiment,
        "variant": entry.variant,
        "trace_id": entry.trace_id,
        "recorded_at": entry.recorded_at.isoformat(),
        "seq": entry.seq,
        "prev_hash": entry.prev_hash,
    }
    return json.dumps(recorded, sort_keys=True, separators=(",", ":"))
