# The audit entry commits with the state change it records, before the surrounding systems are told

**Status:** accepted · 2026-09-24

One run of a claim ends in a Postgres transaction over the two tables the `triager` owns: the run
itself — where the graph left the claim — and the audit entry that is its evidence. The transaction is
offered as one thing (`TriageStore.transaction()` hands out both writes, commits once and rolls back
once), so an entry cannot exist without the state change it records, and a state change cannot exist
without its entry. `tests/test_audit_transaction.py` forges a failure between the two writes, and a
failure that makes the second one impossible, against a real Postgres: neither leaves anything behind.

The order after that is the decision: **the transaction commits before the surrounding systems are
told where the claim got to.** The pipeline's status emission to `core-sim` is the effect of the
state change, not part of it, and it happens after the evidence is durable.

There can be no distributed transaction across that boundary, and this repository does not pretend
there is one. `docs/adr/0001` fixes that each service owns its own tables and that no service writes
another's, so the two writes are two systems' and one of them can fail after the other succeeded. What
the order decides is which side of that failure an auditor is left looking at:

- **Commit first (chosen).** A decision never exists without its evidence. If the emission fails, the
  run is recorded, the audit entry says what was decided and when, and the caller is told the emission
  failed — the system of record is behind this repository's own record, which the idempotency key the
  emission carries (`<run id>/status`) lets a retry close without duplicating anything.
- **Emit first (rejected).** A failure after the emission and before the commit leaves a status in the
  system of record that nothing in the audit log accounts for: an unexplained change to a claim, made
  by something the log cannot name. That is the one direction an append-only log exists to prevent, so
  the reversed order is rejected even though it makes the more visible artefact — the system of record
  — the one that is never behind.

A transactional outbox (write the intent inside the transaction, and have a relay deliver it) is the
textbook way to close the gap entirely. It is rejected for this increment rather than in principle:
it adds a relay, a delivery table and a poison-message path, and the retry it replaces is one
idempotent call the caller already makes. It is the right shape if an emission ever needs to be
retried without the caller, which ordering here does not.

## Considered Options

- **Writing the audit entry after the emission, in its own transaction.** Rejected in both orders
  above: an entry written afterwards can be lost by a crash between the two, which is exactly the
  failure the acceptance criterion forces a test to reproduce.
- **One transaction per write, with the entry written last and a repair job reconciling.** Rejected:
  the repair job would be reconciling from the thing it is supposed to be evidence *for*, and until
  it runs, the gap is indistinguishable from an entry somebody removed.
- **A transactional outbox with a relay.** Rejected for this increment, as above.
- **Per-claim chains instead of one global chain.** Rejected: a per-claim chain would allow parallel
  appends without contention, but it also makes the whole log's integrity a property of every claim
  individually, and this increment has one writer. The global chain takes an advisory lock per append,
  which is cheap enough at this volume; the contention is a cost the increment that makes appends
  concurrent gets to pay for differently.
- **Anchoring the chain head outside the database.** Not rejected but deferred: without an anchor, a
  chain proves that what is present is what was written, not that nothing was removed from the end.
  `docs/adr` and `claim_triage.triage.audit` say so plainly, and ticket 42 owns the anchor.

## Consequences

- The store's port has two methods rather than one `record(run, entry)`, so the property under test —
  both writes or neither — is the thing a test drives, in this repository and in the double the unit
  job uses.
- Everything the pipeline does is inside one span, including the emission and the transaction, so a
  trace that shows a run also shows whether its emission happened, and how long the recording took.
- A failed emission is a failed call, answered `503 service_unavailable`, even though the run is
  recorded: the caller's claim is not yet reflected where the systems keep it, and answering success
  would be telling the caller something untrue.
- The audit log's honest limit is stated where it is implemented rather than only here: the chain
  detects an entry that was changed, moved or removed, and does not yet detect a suffix that was cut
  off, because nothing outside the database holds the head it should have ended at.
