# claim-triage-agent

An end-to-end motor-claim triage pipeline for the Slovak motor insurance product line
(`Auto & pohoda`, poistné podmienky editions `USK/PVO/21`, `/24`, `/25`): Slovak claim documents
arrive, structured claim data is extracted, the poistné podmienky clauses **effective on the claim's
incident date** are retrieved, the claim is classified and routed, and a decision-support pack is
produced whose citations resolve to real clauses in the right edition. A deterministic policy gate —
never the model — decides which claims may proceed without a human. Every step is traced and
evaluated against committed golden sets.

[![CI](https://github.com/r7a7d7o/claim-triage-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/r7a7d7o/claim-triage-agent/actions/workflows/ci.yml)

## Status

v0.1 `walking-skeleton`. The thinnest complete path is in place. A claim posted at the entry point,
with whatever documents it carries, is screened at ingress and carried through the graph skeleton to
the surrounding systems, which are told where it got to; the run's state change and its hash-chained
audit entry commit in one transaction, with the guard verdicts the claim was admitted on; and one
OpenTelemetry trace spans the entry point and the run, carrying the run, claim, experiment and
variant. With the
tracing backend down the same run completes on structured JSON logs, and replay is the default arm,
so nothing needs a credential. The seven deployables each start, resolve and validate their
configuration and report it — the three with a surface (`core-sim`, `triager`, `api`) serve it. The
compose stack comes up on one command with no credentials; the container job builds the image, scans
it and drives one claim through the running stack at its entry point. The model sits behind a port
with a replay adapter and a provider adapter, selected by configuration, so nothing needs a credential
to run — no stage asks it for anything yet, which is what v0.2's extraction does. The guard seam is in
place: the entry point screens the documents of a submission — content type, size, page count,
encryption and archive expansion — refuses a caller past its burst, and records the verdicts that let
a claim in on the run and in its audit entry. The evaluation seam is in place: three golden-set
formats, a metric table, baselines per release tag, and a gate whose four declared rules are the only
way a run exits non-zero — with the sets themselves empty placeholders, and fixture material that
makes CI refuse a regression on every pull request. Nothing is intelligent yet, which is v0.2's
extraction.

## Unaffiliated, and synthetic or openly licensed data

This repository is a personal engineering demonstration. It is **not affiliated with, endorsed by,
or connected to UNIQA Group Services, UNIQA insurance group, or any of their subsidiaries.** The
product and edition names appear only to describe the Slovak motor-insurance process the
demonstration models, which the author read about in a public job posting. No brand assets, logos or
page content of any insurer are reproduced.

**All data in this repository is synthetic, openly licensed, or a publicly published terms document
reproduced unmodified.** Claim documents are generated deterministically from seeds; the retrieval
corpus is the insurer-published poistné podmienky; the sample datasets are synthetic and openly
licensed. Third-party sources, their terms and their handling are recorded in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The system produces routing, decision support and correspondence. It never settles, pays out or
legally decides a claim.

## Quickstart

Prerequisites: [`uv`](https://docs.astral.sh/uv/), and Podman with a compose provider
(`podman compose`). No credentials and no accounts.

```bash
uv sync                                    # Python 3.13 is fetched and pinned; installs the venv
uv run poe check                           # lint + types + unit tests — the gate CI runs
```

Individual tasks: `uv run poe lint`, `uv run poe types`, `uv run poe unit`, `uv run poe format`, plus
`generate`, `contract`, `postgres`, `provider` and `eval` (below). CI runs the first three as three
separate jobs, a fourth for the contract and the audit transaction, a fifth for the evaluation gate,
and the container job described below.

The stack — Postgres, Qdrant, Redis and the simulated surrounding systems — comes up on one command
and returns only once every service reports healthy, rather than sleeping and hoping:

```bash
podman compose up --detach --wait             # the whole stack, every service healthy
podman compose --profile smoke run --rm smoke # one claim end to end through the running stack
podman compose down --volumes                 # stop it, and drop what it wrote
```

`podman compose up` builds the application image if it is missing, so a first run is those commands
and nothing else. [Container stack](#container-stack) has the details, including the build CI uses
for the artefact it scans.

Start a deployable — it resolves its configuration from the environment and prints it as one JSON
line, or exits 2 naming the variable that was rejected:

```bash
uv run claim-triage-retrieval   # reports its configuration and exits (surface still to land)
uv run claim-triage-core-sim    # serves the simulated systems on :8080
uv run claim-triage-triager     # serves the run boundary on :8001, over the stack's Postgres
uv run claim-triage-api         # serves the entry point on :8000, forwarding to the triager
```

Three commands bring the same three surfaces up against the local stack — Postgres for the triager,
`core-sim` for the surrounding systems — and one claim can then be carried through them:

```bash
podman compose up --detach --wait postgres core-sim
uv run claim-triage-triager &        # :8001
uv run claim-triage-api &            # :8000
uv run claim-triage-smoke            # one claim through the entry point, end to end
curl --request POST localhost:8000/claims \
  --form 'claim={"policy_number":"SIM-2026-0001","incident_date":"2026-03-14","claim_amount_eur":"1840.50"}'

# With a document, which the entry point screens before the pipeline is reached. Any PDF inside the
# ingress ceilings stands in for one: the synthetic claim-document corpus arrives in v0.2 (ticket 13),
# and the committed sample below is a 10-page, 107 KiB document that passes every check.
curl --request POST localhost:8000/claims \
  --form 'claim={"policy_number":"SIM-2026-0001","incident_date":"2026-03-14","claim_amount_eur":"1840.50"}' \
  --form documents=@sample/pzp_vseobecneobchodne_podmienky_vpp_platne_od1-10-2019.pdf
```

## Layout

```
pyproject.toml          single uv project: dependencies, console scripts, task and tool config
compose.yaml            the development stack: infrastructure, surrounding systems, services, smoke
Containerfile           the application image every service in the stack runs from
contracts/              the OpenAPI document the surrounding systems are defined by
observability/          the tracing backend of the stack, under the `observability` profile
evaluation/             the golden sets, the declared gate rules, the baselines per release tag, and
                        the fixture material the evaluation CI job injects a regression into
src/claim_triage/       the shared domain library imported by every deployable
src/claim_triage/contract/
                        generated from contracts/: the wire models and the typed client
src/claim_triage/codegen.py
                        the generator `uv run poe generate` runs over that document
src/claim_triage/boundary.py
                        what our own surfaces answer when a call cannot be served
src/claim_triage/guards/
                        the guard layers: the verdict vocabulary, the ingress checks over an
                        upload, and the rate limit in front of the boundary
src/claim_triage/telemetry.py
                        the tracing and structured-logging seam every deployable configures
src/claim_triage/services/registry.py
                        the deployables as data — name, console script, port, scaling axis, reason
src/claim_triage/core_sim/
                        the simulated surrounding systems: their store, their seed, their ASGI surface
src/claim_triage/triage/
                        one run: the graph skeleton, the run and audit tables, the pipeline, its
                        surface and the client the entry point reaches it with
src/claim_triage/model/
                        the model behind one port: the call, the replay and provider adapters, the
                        fixtures the replay adapter answers from, and the one place that selects
                        between them
src/claim_triage/api/   the entry point's ASGI surface
src/claim_triage/evaluation/
                        the evaluation harness: the set formats, the metrics, the gate, the per-tag
                        baseline store, and the runner behind `uv run poe eval`
src/claim_triage/smoke.py
                        one claim end to end through a running stack — what the container job gates on
tests/                  tests, written at the seams the specification confirms
tests/contract/         the generated client against a running service, and it against the document
tests/test_audit_transaction.py
                        the audit transaction, against a real Postgres (poe postgres)
docs/adr/               architecture decision records
.github/workflows/      the CI pipeline
```

## Deployables

Seven deployables over one shared domain library. A boundary is a deployment decision, not a code
duplication decision: split only where the scaling axis or the failure domain genuinely differs.

|Deployable|Console script|Default port|Scaling axis|Why it exists separately|
|---|---|---|---|---|
|`api`|`claim-triage-api`|8000|requests per second|stateless entry point|
|`triager`|`claim-triage-triager`|8001|queue depth|holds the graph and checkpointing|
|`extraction`|`claim-triage-extraction`|8002|queue depth and CPU|OCR and vision work is CPU- and latency-heavy|
|`retrieval`|`claim-triage-retrieval`|8003|requests per second|owns the vector and lexical indexes|
|`guardrail-policy`|`claim-triage-guardrail-policy`|8004|replica count|safety logic must be versioned and deployable independently|
|`reviewer-ui`|`claim-triage-reviewer-ui`|8005|human sessions|the human decision surface|
|`core-sim`|`claim-triage-core-sim`|8080|replica count|stands in for the surrounding insurance systems|

Three of them have a surface so far. `core-sim` serves the contract below out of the tables it owns in
Postgres; `triager` serves the run boundary the pipeline sits behind; `api` serves the entry point a
claim is submitted at. The other four report their configuration and exit until their surfaces land.
`claim-triage-smoke` is not a deployable: it is the one-shot command that drives a claim through a
running stack, at the entry point.

## One claim, end to end

The thinnest complete path, and the surfaces it crosses:

```
POST /claims ──► api ──► triager ──► graph skeleton ──┐
(multipart)      │           │                          │
                 │           ├─ state change + audit entry, one transaction
                 │           └─ status update ──► core-sim (the system of record)
                 ├─ ingress: every document screened, its verdicts travel with the run
                 └─ one span, joined to the run's span ──► OTLP, or the structured logs
```

1. **The entry point** screens the submission at ingress — the claim is parsed, every document is
   checked, and a caller past its burst waits — then mints the run identifier, so every span, log
   line and audit entry along the path can be joined on it, opens the span the trace starts at, and
   forwards the claim with the verdicts of what was let in. A refusal stops here: the triager is
   never reached and nothing is recorded.
2. **The triager** hands the claim to the surrounding systems, which are the system of record for it,
   carries it through the graph skeleton, and reads the status it ended with. The skeleton is one
   no-op node: the topology's stages land in that node one ticket at a time.
3. **The state change and its audit entry commit in one transaction** over the two tables the triager
   owns. Neither can exist without the other; `docs/adr/0005` records why, and what it costs.
4. **The surrounding systems are told** the status, and the answer to the caller is the claim as they
   now hold it. A refusal they give is relayed with its own code.

The audit log is append-only and hash-chained: every entry carries the hash of the entry before it and
a digest of everything it holds, so an entry that was changed, moved or removed after it was written
stops matching `claim_triage.triage.audit.verify`. What that does not cover is a chain whose end was
cut off, because nothing outside the database holds the head it should have ended at; ticket 42 owns
the anchor, and the limitation is stated where the chain is implemented rather than only in a
document.

Every run is traced. With `CLAIM_TRIAGE_OTEL_ENDPOINT` unset — the default, and what CI runs — the
spans are recorded, their trace identifiers appear in the structured logs, and nothing is exported.
With the observability profile up, the same run exports over OTLP:

```bash
CLAIM_TRIAGE_OTEL_ENDPOINT=http://otel-collector:4318 \
  podman compose --profile observability up --detach --wait
podman compose --profile smoke run --rm smoke
podman compose logs otel-collector            # the spans, with run, claim, experiment, variant
```

## The guard seam

The guard layers answer with **values**: a check returns a `Verdict` — which check asked, whether it
let the thing through, and what it found — grouped per document, and the run carries the verdicts it
was admitted on into its audit entry, where the chain covers them like every other field. Nothing
about a refusal is known only to the code that raised it, and a check is a typed function over a
typed value, so `tests/test_guard_ingress.py` asserts every one of them without a socket, a database
or a clock it does not own. `docs/adr/0007` records the design and what it costs.

Six layers are specified; **v0.1 ships the first, ingress**, and the rest arrive with the work they
check. What it does now, per uploaded document:

|Check|Refuses|As|
|---|---|---|
|size|an upload past `CLAIM_TRIAGE_GUARD_MAX_DOCUMENT_BYTES`, read one byte past the ceiling and no further|413 `payload_too_large`|
|media type|a declared type the boundary does not take|415 `unsupported_media_type`|
|structure|bytes that are not the document they claim to be: anything the PDF reader raises, and a PDF whose page tree resolves to nothing|422 `document_refused`|
|encryption|a PDF the reader reports as encrypted — whether or not an empty password would open it, because the boundary does not guess passwords|422 `document_refused`|
|archive|a zip whose own directory declares an expansion past `…_MAX_ARCHIVE_EXPANSION_RATIO`|422 `document_refused`|
|page count|more pages than `CLAIM_TRIAGE_GUARD_MAX_DOCUMENT_PAGES`|422 `document_refused`|

One check runs before any of those, because it is about the submission rather than a document in it:
a request that **declares** more bytes than `CLAIM_TRIAGE_GUARD_MAX_SUBMISSION_BYTES` is refused
unread, with the multipart parser never having seen the body. That is what keeps a hostile upload from
being spooled to disk to be refused afterwards. A submission that declares no length — a chunked one —
cannot be judged that way: the framework spools it before a route sees it, and the per-document
ceiling is what bounds it from there. The honest reading is in `docs/adr/0007`.

The first refusal of a document stops its remaining checks, every refusal names the document and the
check in `detail`, and a refused submission leaves nothing behind — no claim in the surrounding
systems, no run, no audit entry. A document inside every limit is carried, and the verdicts that let
it in are on the run and in its audit entry, so a claim can be read back months later together with
the evidence it was admitted on.

The rate limit in front of the boundary is a token bucket per caller: `…_RATE_LIMIT_BURST`
submissions back to back, then one every `1 / …_RATE_LIMIT_REFILL_PER_SECOND` seconds, answered
`429 rate_limited` with `Retry-After`. It is **per replica and in-process** — N replicas allow N times
the burst, and a restart forgets the counts — which `docs/adr/0007` states rather than leaves to be
discovered. Readiness is not limited: a throttled health check would be an outage the limiter caused.

What the ingress deliberately does not do yet, so that it is not assumed: no ceiling on the number of
documents in one submission (their bytes are bounded, their count is not), no early refusal of a
chunked submission, no content inspection beyond the declared type and the document's own structure,
and no image document classes — those arrive with scanned intake in v0.4.

## The surrounding systems' contract

`contracts/core-sim.openapi.yaml` is the single source of truth for the simulated surrounding
insurance systems: the operations the pipeline integrates with — policy lookup, claim creation and
lookup, status update, document attachment, and reading and recording claim parties — each with an
`operationId`, one error taxonomy (`ErrorResponse`, whose `code` comes from `ErrorCode`), and an
`Idempotency-Key` that every write carries and is deduplicated by.
[ADR 0004](docs/adr/0004-contract-first-surrounding-systems.md) records what those conventions cost and
why.

`src/claim_triage/contract/` is generated from that document and committed; `core-sim`, the client and
the tests all speak the generated models, so nothing restates the wire in a second place. `poe generate`
is the only way that package is written, and the `contract` CI job fails on any change it makes:

```bash
uv run poe generate    # writes src/claim_triage/contract/ from contracts/core-sim.openapi.yaml
uv run poe contract    # the generated client against a running service (needs Postgres, below)
```

`poe contract` is its own job for the same reason it is its own task: it drives a real service over a
real socket with a real Postgres behind it, and the unit job has none of those. It needs the stack's
database first:

```bash
podman compose up --detach --wait postgres
uv run poe contract
```

The tests start the service themselves, on a port of their own, so a stale process on 8080 can never
pass for a live one. What they compare is the shape of the API — routes, operation ids, parameters,
request and response field sets — projected out of both documents, plus the client's typed behaviour
over every operation: the exception each documented failure code maps to, and that a write replayed with
its key is recorded once.

The same Postgres is all the audit transaction needs, so the same job proves that too:

```bash
uv run poe postgres    # the state change and its entry, and what tampering with the chain looks like
```

That one is not a unit test: whether two writes commit together is a property of the database, so it is
forced — a failure between the two writes, and a second write the database refuses — against the real
thing rather than against a double that agrees with itself.

## The model port

Every use of a model crosses one seam: a stage builds a `ModelCall` — the task it is asking, the
document text a model reads, and the schema the answer must validate against — and the port answers
with an instance of that schema, or raises. Two adapters sit behind it and `claim_triage.model.select`
is the only module that imports either, so a stage cannot reach a model endpoint without going through
the port; `tests/test_model_port.py` checks that, and pins by name the modules that open their own
HTTP connections.

|Selection|Adapter|What it needs|
|---|---|---|
|`replay` (default)|answers from the fixtures committed under `src/claim_triage/model/fixtures/`|nothing|
|`provider`|`POST {base_url}/chat/completions`, with the answer's own JSON schema as a strict `response_format`|an endpoint and a model name|

Replay is what an unconfigured environment gets, so a run, a test, a demo and a CI job need no
endpoint and no credential. A fixture is one recorded call and the answer it got, and it is identified
by what it holds rather than by where it sits:

```json
{
  "task": "Fill in the claim's own fields from this claim notification: …",
  "content": "Oznámenie škody - motorové vozidlo (synthetic sample)\n…",
  "answer_as": "claim_triage.contract.models.ClaimSubmission",
  "answer": {
    "policy_number": "SIM-2026-0001",
    "incident_date": "2026-03-14",
    "claim_amount_eur": "1840.50"
  }
}
```

So renaming a fixture changes nothing, two files recording the same call are refused when the set is
read, and a document edited after its answer was recorded is a call nothing answers rather than a call
that gets a stale answer. The answer is validated against the caller's schema on every read, so a
schema that changed fails at the call and names the file that has to be recorded again.

The selection is part of every deployable's startup report, and configuration is what changes it:

```bash
uv run claim-triage-triager            # … "model_provider": "replay" …
CLAIM_TRIAGE_MODEL_PROVIDER=provider \
CLAIM_TRIAGE_MODEL_BASE_URL=http://localhost:11434/v1 \
CLAIM_TRIAGE_MODEL_NAME=local-model \
  uv run claim-triage-triager          # … the endpoint it would answer through, and no key …
```

`docs/adr/0006` records the decision, what the fixtures cost, and what is exercised where. The
provider adapter is selected by configuration and never exercised in CI, which is what ticket 05 asks
for: everything that drives it carries the `provider` marker, the default run leaves those out, and
`uv run poe provider` is what asks for them. They drive it against a socket on loopback they serve
themselves — no provider, no credential, and nothing that leaves the machine.

## The evaluation seam

Three capabilities are scored against committed golden sets, and one command does it:

```bash
uv run poe eval            # the committed sets, against the release tag's own baseline
uv run poe eval-fixtures   # the harness's own exercise set, against the baseline it recorded
```

`claim-triage-eval` prints a metric table — one row per metric per capability, with the value, the
baseline it was compared to, the difference and the verdict — and leaves non-zero only through the
rules `evaluation/gate.json` declares: a **degradation** of more than two points below the tag's
baseline, a metric under its absolute **floor** (citation validity carries one, and no rules file may
leave it out), a **coverage** failure when a metric the baseline records is not measured or a set has
lost cases against the tag's record of it, and an **incomparable** baseline recorded under other
settings or for another version of a set. Exit `1` means one of those fired, exit `2` means the run
could not be made at all, and nothing else is non-zero. A capability the build does not answer yet is
reported as `not implemented` rather than scored zero.

The three sets are JSONL, one file per capability: a header naming the capability, the version and
what the set is for, then one case per line. They are **placeholders** — the format, the version, no
case — because the cases are the document-intake increment's work (ticket 14). Baselines are committed
one file per release tag (`evaluation/baselines/v0.1.0.json`); `uv run poe eval --record` writes the
tag's own after judging against the old one, and refuses to write a run that broke a rule against the
baseline it holds — a baseline is a release record, not a moving average.

Everything that reads, scores and judges those sets is exercised: `evaluation/fixtures/` holds the
same three formats with cases, the answers of a build at the quality its baseline records, and the
same build regressed past both rules. The evaluation CI job scores the first, then installs the
regressed answers over a copy and fails unless the gate refuses them with exit `1` — a gate that
refused everything would fail the step before it. The build's answers arrive through one seam
(`claim_triage.evaluation.build`), which the increment implementing a capability fills — the entries
and the tickets they arrive with are listed there and in `docs/adr/0008`. Answers can also be read
from a recorded directory (`--predictions`), which is how a run is re-scored without the build and how
the CI job injects its regression.

The formats, every metric's meaning, the declared rules and the fixture material are documented where
the material is: [`evaluation/README.md`](evaluation/README.md). `docs/adr/0008` records the decision
and what the placeholder sets cost.

## Configuration

Configuration is environment-based, per deployable. `.env` is read when present (gitignored); copy
[`.env.example`](.env.example) to start. Compose endpoints are the defaults, so nothing is required
to run locally.

|Variable|Applies to|Meaning|Default|
|---|---|---|---|
|`CLAIM_TRIAGE_POSTGRES_HOST_PORT`|compose only|host side of Postgres's published port|`5432`|
|`CLAIM_TRIAGE_REDIS_HOST_PORT`|compose only|host side of Redis's published port|`6379`|
|`CLAIM_TRIAGE_QDRANT_HOST_PORT`|compose only|host side of Qdrant's HTTP port|`6333`|
|`CLAIM_TRIAGE_CORE_SIM_HOST_PORT`|compose only|host side of the simulated systems' port|`8080`|
|`CLAIM_TRIAGE_TRIAGER_HOST_PORT`|compose only|host side of the triager's port|`8001`|
|`CLAIM_TRIAGE_API_HOST_PORT`|compose only|host side of the entry point's port|`8000`|
|`CLAIM_TRIAGE_OTEL_HOST_PORT`|compose only|host side of the collector's OTLP/HTTP port|`4318`|
|`CLAIM_TRIAGE_ENVIRONMENT`|all|environment name|`local`|
|`CLAIM_TRIAGE_POSTGRES_DSN`|all|Postgres DSN (masked in the startup report)|local compose DSN|
|`CLAIM_TRIAGE_REDIS_URL`|all|Redis URL|`redis://localhost:6379/0`|
|`CLAIM_TRIAGE_QDRANT_URL`|all|Qdrant URL|`http://localhost:6333`|
|`CLAIM_TRIAGE_CORE_SIM_BASE_URL`|all|simulated surrounding systems|`http://localhost:8080`|
|`CLAIM_TRIAGE_API_BASE_URL`|all|this system's entry point (read by the smoke run)|`http://localhost:8000`|
|`CLAIM_TRIAGE_TRIAGER_BASE_URL`|all|the triager the entry point forwards to|`http://localhost:8001`|
|`CLAIM_TRIAGE_OTEL_ENDPOINT`|all|OTLP/HTTP endpoint; unset (or empty) means nothing is exported|unset|
|`CLAIM_TRIAGE_LANGFUSE_HOST`|all|Langfuse host; unset means no Langfuse consumer|unset|
|`CLAIM_TRIAGE_MODEL_PROVIDER`|all|which model answers a call: `replay` or `provider`|`replay`|
|`CLAIM_TRIAGE_MODEL_BASE_URL`|all|the provider endpoint's root, version prefix included; required by `provider`|unset|
|`CLAIM_TRIAGE_MODEL_NAME`|all|the model the endpoint is asked for; required by `provider`|unset|
|`CLAIM_TRIAGE_MODEL_API_KEY`|all|bearer key for the endpoint, if it wants one; never reported|unset|
|`CLAIM_TRIAGE_MODEL_FIXTURES`|all|where the replay adapter reads its answers|`src/claim_triage/model/fixtures`|
|`CLAIM_TRIAGE_MODEL_TIMEOUT_SECONDS`|all|how long one call to an endpoint may take|`30`|
|`CLAIM_TRIAGE_GUARD_MEDIA_TYPES`|all|what the ingress takes, as a JSON array of media types|`["application/pdf","application/zip"]`|
|`CLAIM_TRIAGE_GUARD_MAX_SUBMISSION_BYTES`|all|largest submission the ingress accepts, refused unread past it|`33554432`|
|`CLAIM_TRIAGE_GUARD_MAX_DOCUMENT_BYTES`|all|largest document the ingress takes|`8388608`|
|`CLAIM_TRIAGE_GUARD_MAX_DOCUMENT_PAGES`|all|most pages one PDF may hold|`40`|
|`CLAIM_TRIAGE_GUARD_MAX_ARCHIVE_EXPANSION_RATIO`|all|largest expansion a zip may declare|`100`|
|`CLAIM_TRIAGE_GUARD_RATE_LIMIT_BURST`|all|submissions one caller may make back to back|`20`|
|`CLAIM_TRIAGE_GUARD_RATE_LIMIT_REFILL_PER_SECOND`|all|submissions per second, per caller, once the burst is spent|`5`|
|`CLAIM_TRIAGE_<DEPLOYABLE>_HOST`|per deployable, e.g. `CLAIM_TRIAGE_API_HOST`|bind host|`127.0.0.1`|
|`CLAIM_TRIAGE_<DEPLOYABLE>_PORT`|per deployable, e.g. `CLAIM_TRIAGE_API_PORT`|bind port|the table above|

A deployable's name is upper-cased and its dashes become underscores in the variable, so
`reviewer-ui` takes `CLAIM_TRIAGE_REVIEWER_UI_PORT` and `core-sim` takes `CLAIM_TRIAGE_CORE_SIM_PORT`.

The `CLAIM_TRIAGE_GUARD_*` variables are the guard layers' thresholds, and the ones read by whichever
deployable runs a guard — the entry point, today. `…_MEDIA_TYPES` is a JSON array, because a list has
no separator that cannot appear in a media type; every other threshold is a number, and a threshold
that could not be enforced (zero, or negative) is rejected at startup naming its variable.

The `…_HOST_PORT` variables are read by Compose, not by the application, and only the host side of a
published port moves: the stack keeps using the standard port across its own network.

## Container stack

`compose.yaml` is the development loop at every increment. Kubernetes is the deployment target from
v0.5, and a cluster — tens of seconds per restart — never becomes the inner loop.

|Service|Image|Host port|What it is|
|---|---|---|---|
|`postgres`|`postgres:17-alpine`|5432|the system of record; `core-sim` owns its tables in it, and the triager the runs and the audit log|
|`redis`|`redis:8-alpine`|6379|the queue, from v0.1; the graph checkpointer's backing store joins it with the interruptions in v0.3|
|`qdrant`|`qdrant/qdrant:v1.19.1`|6333|the vector index, from edition-aware retrieval in v0.2|
|`core-sim`|this repository's image|8080|the simulated surrounding insurance systems|
|`triager`|the same image|8001|the graph and its records: one run boundary, and the tables it owns|
|`api`|the same image|8000|the entry point: stateless, and ready when the triager behind it is|
|`otel-collector`|`otel/opentelemetry-collector-contrib:0.161.0`|4318|the tracing backend, under the `observability` profile|
|`smoke`|the same image|—|profile-gated one-shot: `claim-triage-smoke`|

Every long-running service has a health check, and a service that needs another waits on it
(`depends_on: … service_healthy`), so `--wait` returns only once the whole stack has passed. The
compose file records why each check is an explicit `CMD-SHELL` string rather than an argv list.

`claim-triage-smoke` is what the container job gates on. It submits one claim at the entry point,
requires the run to have emitted `triaged`, and then reads the claim back from the surrounding systems
through the contract's own generated client — so it fails on a container stack that is up while the
pipeline behind it did nothing. Every wait in it is a bounded retry: a stack that never answers fails
the run instead of hanging it. The entry point is waited for before the claim is submitted and the
submission is made once — a new request is a new run, so retrying one would leave a second claim
behind — while the read-back that follows is retried, because it changes nothing.

The image is built with Podman, and the artefact CI scans is built with:

```bash
podman build --squash-all --tag localhost/claim-triage-agent:dev .
```

`--squash-all` matters to that artefact: it is what makes the build's deletions real, as the
`Containerfile` header explains. `compose.yaml` builds the same tag without squashing when the image
is missing, so the stack can still come up on its own — that image runs identically and only a scan of
it would differ.

CI runs five jobs. `lint`, `types` and `unit` are the three commands above. `contract` regenerates the
contract package and fails on any change to it, drives the generated client against a service it
starts itself, and proves the audit transaction against the same Postgres. `container` runs the same
compose file on the runner's Docker
Compose: Podman builds and squashes the image, Trivy scans it at HIGH and CRITICAL, `docker load` puts
it where Compose looks for it, and the job reports success only after the smoke has carried one claim
through the stack. `docs/adr/0003` records why the two engines differ, and why the image is named
`localhost/claim-triage-agent:dev` in both.

## Licence

Repository code is [Apache-2.0](LICENSE). Third-party data, its terms and its attribution are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Generated synthetic documents are CC-BY-4.0.
