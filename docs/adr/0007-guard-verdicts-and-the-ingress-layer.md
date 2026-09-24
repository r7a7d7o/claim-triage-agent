# The guard seam answers with verdicts, and the ingress runs at the entry point

**Status:** accepted · 2026-09-24

v0.1 needs a safety layer that does not have to be believed. Two properties decide its shape: every
check is a typed function over a typed value, so it is unit-tested through the same loop as business
logic and needs no socket, clock or model; and every answer it gives about a claim is recorded with
the run it was given for, so an auditor can see what a decision was admitted on rather than only what
it concluded.

## What a guard answers

A guard returns a **value**, not an exception: `Verdict(check, passed, detail)`, grouped per document
as `DocumentVerdicts(filename, media_type, verdicts)`. Nothing about a refusal is known only to the
code that raised it, so a refused submission and an admitted one travel in the same shape, and the run
carries the verdicts into the audit entry it is recorded with — where the chain's digest covers them,
like every other field (`claim_triage.triage.audit`).

The boundary is what gives a refusal its wire meaning: the entry point maps a check to a status and a
code, which is why the same verdict can be a 413 here and, later, a retry there.

The layers land one ticket at a time; v0.1 ships the first. `_STEPS` in `claim_triage.guards.ingress`
is which checks a declared content type is asked, in the order they run, and the configured allow-list
can only narrow that set: a content type no ticket implements cannot be configured into acceptance.

## Where the ingress runs

At the **entry point**, because that is where the bytes arrive and because refusing there is the
whole point of the layer. Two ceilings do that work, and they are stated with what each of them can
and cannot catch:

- **A submission that declares more than `max_submission_bytes` is refused before its body is read.**
  The check is on the declared length, from middleware, and it is what stops a hostile upload from
  being accepted and spooled to disk to be refused afterwards. What it cannot catch is a submission
  that declares no length: a chunked upload is spooled by the framework before any route of ours sees
  it, and the per-document ceiling is what bounds it from there. Closing that would mean owning the
  body stream at the ASGI level, which this increment leaves to the edge that eventually sits in front
  of the service.
- **A document is read one byte past the per-document ceiling and no further** (`read_bounded`), so
  the boundary never holds a document whole to decide it does not want it, and a decoder only ever
  sees bytes that were read under a ceiling.

Two things follow, and both are deliberate:

- **A limited caller is refused before its body is read too.** The rate limit is middleware beside
  the size check, not a route dependency, for the same reason: a dependency is solved after FastAPI
  has parsed the submission, which is the work both of these exist to avoid. Readiness is neither
  limited nor sized — a throttled health check is an outage the limiter caused.
- **The verdicts travel to the triager, and it records them without re-checking.** The triager never
  sees the bytes in this increment — nothing reads documents yet — so re-checking would be a second
  read of every upload, at the one boundary whose job is to stand in front of the rest. The hop
  between the two deployables is unauthenticated here, as every hop in this stack is; a caller inside
  the boundary could send verdicts that never happened. That changes when the guardrail-policy
  deployable serves verdicts as its own versioned interface (v1.0, and user story 78), which is a
  deployment change rather than a rewrite because the checks are pure functions over values.

## Our own error vocabulary

`BoundaryCode` carries the five codes the surrounding systems' contract names, under the same strings,
plus the ones only our own boundaries answer: `unsupported_media_type` (415), `payload_too_large`
(413), `document_refused` (422), `rate_limited` (429, with `Retry-After`) and `not_found` (404, for a
path nothing serves).

- **The five are repeated rather than imported** because they are generated from the systems'
  document, and our boundary answers with its own vocabulary. A test holds every code that document
  names to one of ours, so a relayed refusal is always a shape our client parses.
- **The ingress's codes are not added to that document.** The systems never answer them — a document
  the ingress refused never reaches them — and a contract that documents codes its service cannot
  produce is a contract that lies.
- **They are separate codes rather than one.** A caller does something different about each: wait and
  retry the 429, send a document we can read for the 422, transcode for the 415, split the upload for
  the 413. Which check refused is in the detail as well, because the checks are the ingress's own
  vocabulary and naming them costs a caller nothing.

A decoder handed attacker-controlled bytes is the code an adversary attacks, so everything `pypdf` and
`zipfile` raise is caught and turned into a `structure` refusal. A malformed document is an answer; a
500 from the boundary would be a defect the caller cannot act on.

The same reasoning covers the refusals our own boundary does not raise: a body the multipart parser
cannot read, a path nothing serves, a method no route takes are the framework's answers, and they are
shaped into this same vocabulary (`not_found` and `unsupported_media_type`, and `invalid_payload` for
the rest) rather than left as the framework's `{"detail": …}`. A caller's client parses one error
shape from this boundary, whatever refused the call.

## The rate limit is in-process, and that is stated rather than implied

One bucket per caller — the address the request arrived from — over a bounded table that drops the
least recently seen caller instead of growing. The bucket is a value with an injected clock, so a test
spends a burst and reads the wait without sleeping.

What this is *not*: a distributed budget. Each replica limits on its own, so N replicas allow N times
the configured burst in aggregate, a restart forgets the counts, and an attacker cycling through
addresses is bounded per address rather than in total. Redis-backed buckets and a shared window were
both considered and rejected for this increment: they put I/O into a guard that has to be testable
without it, and the stack's Redis is the queue's and the checkpointer's. The limitation is named here
because a limiter that reads as stronger than it is, is worse than no limiter.

## Considered Options

- **Screening in the triager, with the documents forwarded.** Rejected: it reads every upload twice,
  it makes the entry point a relay of hostile bytes rather than a boundary that refuses them, and it
  moves a gateway concern into the service that holds the graph.
- **Registering the ingress codes in the systems' contract.** Rejected, as above: it would document
  codes `core-sim` cannot answer.
- **Answering every ingress refusal with `invalid_payload`.** Rejected: a caller could not tell a
  rate limit from an unreadable PDF, and would retry what it should fix or fix what it should retry.
- **Raising an exception per check.** Rejected: an exception cannot be recorded with the run, and the
  audit entry's guard verdicts are the reason this layer exists in v0.1.
- **A global bucket instead of one per caller.** Rejected: one caller's burst would spend everybody's.
- **A proxy (Envoy, NGINX) for limits and upload sizes.** Deferred rather than rejected: at the
  service-mesh increment the edge may well own the rate limit, and this in-process limiter is what the
  tests and the demonstration run against until then.

## Consequences

- The intake is multipart: the claim is a JSON part named `claim`, each document a file part named
  `documents`. The smoke, the tests and the README's example follow it, and a submission without
  documents is still one part.
- `triage_runs` and `audit_log` gained a `guard_verdicts` column, added by an idempotent
  `ALTER TABLE … IF NOT EXISTS` because the development stack's database predates it. That is the
  migration story for now, and the next increment that alters one of these tables is the one to bring
  a tool.
- A refused submission leaves nothing behind: no claim in the surrounding systems, no run, no audit
  entry. A partially applied submission was rejected as a design, not as a symptom.
- `Check.RATE_LIMIT` is the one verdict that is never recorded, because it is a verdict on a *caller*
  rather than on a document and a rate-limited submission has no run to be attached to. Everything the
  run was admitted with is in its audit entry; what never became a run is in the logs and the metric
  the dashboards will read.
- The audit format gained a field, and the chain's digest covers what an entry holds, so a chain
  written before this change no longer verifies: its entries hash without the new field and cannot be
  made to hash with it. That is the format's version, and for a database that only exists as a demo it
  is settled by `compose down --volumes`; an increment that has to read older chains would carry a
  format version in the digest instead.
- Ingress thresholds are configuration (`CLAIM_TRIAGE_GUARD_*`) and are reported at startup, because
  a safety threshold that cannot be read by the people accountable for it is not a policy.
- What the ingress still does not do, so that it is not assumed: no ceiling on the number of
  documents in one submission, no content inspection beyond structure and declared type, and no image
  document classes — those arrive with scanned intake in v0.4.
