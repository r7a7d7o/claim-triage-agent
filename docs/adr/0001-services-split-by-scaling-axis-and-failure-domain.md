# Services split by scaling axis and failure domain, over one shared domain library

**Status:** accepted · 2026-09-24

A deployable boundary is justified only where the scaling axis or the failure domain genuinely
differs. The seven deployables that satisfy that test — with the axis each one scales on, and the
reason it is not part of another — are listed in the repository's README. Every one of them imports
the same `claim_triage` domain library. Each service owns its own tables; there are no cross-service
database writes; services communicate over the queue and REST only.

## Considered Options

- **One service per domain concept** (intake, extraction, retrieval, decisioning, correspondence),
  each owning its own copy of the domain types and rules. Rejected: the scaling axes inside those
  concepts do not differ — intake and retrieval, for instance, both scale on requests — so the split
  would buy no operational capability while forcing the same business rule to exist in more than one
  place, and the boundary would become a code-duplication decision rather than a deployment one.
- **A single deployable** for the whole pipeline. Rejected: a burst of scanned documents would force
  scaling of the request path with it, and it removes the failure-domain separation that lets the
  policy gate and the reviewer surface stay up when the graph or OCR work is saturated.
- **Separate repositories per service**, each with its own copy of the domain library. Rejected: the
  domain rules are the part that changes most and is most safety-relevant; duplicating them across
  repositories makes divergence invisible and unreviewable.

## Consequences

The domain library is a single Python distribution with one lockfile and one CI pipeline; the
deployables are console scripts over it, so a boundary can be re-cut later without moving code.
A service may only read the tables it owns, which makes a new cross-service read a deliberate,
reviewable decision rather than an accidental one.
