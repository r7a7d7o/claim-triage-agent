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

v0.1 `walking-skeleton`. The thinnest complete path is in place. A claim posted at the entry point is
carried through the graph skeleton to the surrounding systems, which are told where it got to; the
run's state change and its hash-chained audit entry commit in one transaction; and one OpenTelemetry
trace spans the entry point and the run, carrying the run, claim, experiment and variant. With the
tracing backend down the same run completes on structured JSON logs, and replay is the default arm,
so nothing needs a credential. The seven deployables each start, resolve and validate their
configuration and report it — the three with a surface (`core-sim`, `triager`, `api`) serve it. The
compose stack comes up on one command with no credentials; the container job builds the image, scans
it and drives one claim through the running stack at its entry point. The guard seam, the replay
model client and the evaluation harness arrive in the remaining v0.1 tickets.

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
`generate`, `contract` and `postgres` (below). CI runs the first three as three separate jobs, a
fourth for the contract and the audit transaction, and the container job described below.

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
  --header 'content-type: application/json' \
  --data '{"policy_number":"SIM-2026-0001","incident_date":"2026-03-14","claim_amount_eur":"1840.50"}'
```

## Layout

```
pyproject.toml          single uv project: dependencies, console scripts, task and tool config
compose.yaml            the development stack: infrastructure, surrounding systems, services, smoke
Containerfile           the application image every service in the stack runs from
contracts/              the OpenAPI document the surrounding systems are defined by
observability/          the tracing backend of the stack, under the `observability` profile
src/claim_triage/       the shared domain library imported by every deployable
src/claim_triage/contract/
                        generated from contracts/: the wire models and the typed client
src/claim_triage/codegen.py
                        the generator `uv run poe generate` runs over that document
src/claim_triage/boundary.py
                        what our own surfaces answer when a call cannot be served
src/claim_triage/telemetry.py
                        the tracing and structured-logging seam every deployable configures
src/claim_triage/services/registry.py
                        the deployables as data — name, console script, port, scaling axis, reason
src/claim_triage/core_sim/
                        the simulated surrounding systems: their store, their seed, their ASGI surface
src/claim_triage/triage/
                        one run: the graph skeleton, the run and audit tables, the pipeline, its
                        surface and the client the entry point reaches it with
src/claim_triage/api/   the entry point's ASGI surface
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
                │           │                          │
                │           ├─ state change + audit entry, one transaction
                │           └─ status update ──► core-sim (the system of record)
                └─ one span, joined to the run's span ──► OTLP, or the structured logs
```

1. **The entry point** mints the run identifier — so every span, log line and audit entry along the
   path can be joined on it — opens the span the trace starts at, and forwards the claim.
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
|`CLAIM_TRIAGE_<DEPLOYABLE>_HOST`|per deployable, e.g. `CLAIM_TRIAGE_API_HOST`|bind host|`127.0.0.1`|
|`CLAIM_TRIAGE_<DEPLOYABLE>_PORT`|per deployable, e.g. `CLAIM_TRIAGE_API_PORT`|bind port|the table above|

A deployable's name is upper-cased and its dashes become underscores in the variable, so
`reviewer-ui` takes `CLAIM_TRIAGE_REVIEWER_UI_PORT` and `core-sim` takes `CLAIM_TRIAGE_CORE_SIM_PORT`.

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
