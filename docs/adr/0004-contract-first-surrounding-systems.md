# The surrounding systems are contract-first, and their client is generated in this repository

**Status:** accepted · 2026-09-24

`contracts/core-sim.openapi.yaml` is the single source of truth for the simulated surrounding insurance
systems: eight operations over policies, claims, documents and parties, one error taxonomy
(`ErrorResponse` with a code from `ErrorCode`), and one idempotency header that every write carries.
`src/claim_triage/contract/` is generated from that document by `src/claim_triage/codegen.py` and
committed: pydantic models for the wire, and a typed client with one method per `operationId` and one
exception per error code the document declares.

Both sides of the integration use the generated models — the client to send and parse, and `core-sim` to
validate what it accepts and returns — so request and response *payloads* cannot drift. What can still
drift is the shape of the API around them: which routes exist, what they are called, which parameters
they take and which statuses they answer with. That is what the contract tests compare, in their own CI
job, against a service the tests start themselves over the Postgres the job starts. The `contract` job
also regenerates the package and fails if `git status` shows any change, which is what makes the
document and the code that speaks it impossible to drift apart — including a file the generator stopped
writing, which a plain `git diff` would miss.

Two decisions inside the contract are worth stating as decisions rather than as implementation detail:

- **Money is a decimal string** (`format: decimal`, with `x-max-digits` and `x-decimal-places`). A JSON
  number cannot carry a fixed scale without a round trip through a float, and a claim amount is the last
  value in this system that should round. The database column stays `numeric(12, 2)`, which is the fixed
  point both sides agree on.
- **A write is deduplicated by its `Idempotency-Key`.** The service records the key, a fingerprint of the
  request that carried it, and the response that write produced, in the same transaction as the write
  itself. A retry answers with the response the first call recorded — verbatim, whatever has happened to
  the claim since — and changes nothing; the same key with a different request is a documented 409 rather
  than an answer belonging to somebody else's request. Writes are therefore safe to retry, which is what
  ticket 28's retry policy and circuit breaker will rely on.

The projection the contract tests compare is deliberately about names and field sets, not about rendered
types: pydantic derives the latter from the same models the generator emits, so a type disagreement is
not reachable, while a field-set disagreement is — and the client's models refuse any answer carrying a
field the document does not declare, because every generated model is closed.

## Considered Options

- **A hand-written client and models, with the document as documentation.** Rejected: the acceptance
  criterion is that generation runs in CI so the two cannot drift, and documentation nothing enforces is
  what drifts.
- **`openapi-python-client`.** Rejected: it generates an attrs-and-httpx package whose response unions and
  formatting are a second style in this repository, and it does not turn the contract's error codes into
  typed exceptions without hand-written glue sitting next to generated code — the glue being exactly the
  part that would drift.
- **`datamodel-code-generator` for the models, and a hand-written client.** Rejected: it generates models
  in a style this repository cannot control, and it leaves the client — the half that maps documented
  failures — hand-written, so the drift gate would cover half the package.
- **Generating at image build time, or at import time, instead of committing.** Rejected: a module that
  exists only inside an image cannot be reviewed in a diff, and the review is this repository's quality
  gate. Committing it also makes the drift check a plain `git status` rather than a comparison of two
  builds.
- **Letting the service keep its own hand-written request models and checking them against the document
  in the tests.** Rejected: the payloads would then be defined twice with a test between them, instead of
  once with no gap to test.

## Consequences

- A contract change is a two-step change: edit the document, run `uv run poe generate`. The `contract`
  job fails until the generated package matches what the document produces, so a forgotten second step
  cannot be merged.
- The generator reads a documented subset of OpenAPI and refuses anything outside it by name, so a
  construct added to the document fails the build rather than being quietly ignored.
- The generated package is held to the same gate as hand-written code — the generator formats its own
  output with the repository's ruff, and `poe types` checks it like anything else.
- The service and the document are still two descriptions of the routes, and the `contract` job is the
  thing that notices when they disagree; that job therefore runs against a service started from the
  repository, never against one that happens to be running.
