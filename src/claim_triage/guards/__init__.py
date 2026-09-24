"""The guard seam: what a check answers, and the ingress layer that is the first of the six.

A guard is a typed function over a typed value — an upload, a model answer, a tool call — that
returns a `Verdict`: which check answered, whether it let the thing through, and what it found.
Nothing here opens a socket, reads a clock other than the one it was handed, or reaches a model, so
every guard is unit-tested through the same loop as business logic, and a run carries its verdicts
into the audit log (see `docs/adr/0007`).

The layers land one ticket at a time. `ingress` is the first: content type, size, page count,
archive expansion and encryption, plus the rate limit in front of the whole boundary. The
untrusted-content wall, the retrieval guards, the model I/O guards, the action guards and the egress
guards follow in the increments whose work they check, and each answers in this same vocabulary.

Callers reach a layer through this package, never through an adapter: `claim_triage.api.surface`
screens an intake with `ingress`, and the triager records what it answered without importing a PDF
reader or a decompressor.
"""
