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

v0.1 `walking-skeleton`. The scaffold is in place: the seven deployables below each start, resolve
and validate their configuration, and report it; lint, types and unit tests run locally and in CI.
The compose stack comes up on one command with no credentials, the simulated surrounding systems serve
claims out of Postgres, and the container job builds the image, scans it and drives one claim end to
end through the running stack. The graph, API boundary, guard and audit skeletons, replay model client
and evaluation harness arrive in the remaining v0.1 tickets.

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

Individual tasks: `uv run poe lint`, `uv run poe types`, `uv run poe unit`, `uv run poe format`. CI
runs the same three commands as three separate jobs, plus the container job described below.

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
uv run claim-triage-api        # reports its configuration and exits
uv run claim-triage-core-sim   # reports it, then serves the simulated systems on :8080
```

## Layout

```
pyproject.toml          single uv project: dependencies, console scripts, task and tool config
compose.yaml            the development stack: infrastructure, surrounding systems, image, smoke
Containerfile           the application image every service in the stack runs from
src/claim_triage/       the shared domain library imported by every deployable
src/claim_triage/services/registry.py
                        the deployables as data — name, console script, port, scaling axis, reason
src/claim_triage/core_sim/
                        the simulated surrounding systems: claim models, store port, ASGI surface
src/claim_triage/smoke.py
                        one claim end to end through a running stack — what the container job gates on
tests/                  tests, written at the seams the specification confirms
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

`core-sim` is the one deployable with a surface so far. It serves the simulated surrounding systems —
`GET /healthz`, `POST /claims`, `GET /claims/{id}`, `POST /claims/{id}/status` — over the `claims`
table it owns in Postgres. The other six report their configuration and exit until their surfaces
land. `claim-triage-smoke` is not a deployable: it is the one-shot command that drives a claim through
a running stack.

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
|`CLAIM_TRIAGE_ENVIRONMENT`|all|environment name|`local`|
|`CLAIM_TRIAGE_POSTGRES_DSN`|all|Postgres DSN (masked in the startup report)|local compose DSN|
|`CLAIM_TRIAGE_REDIS_URL`|all|Redis URL|`redis://localhost:6379/0`|
|`CLAIM_TRIAGE_QDRANT_URL`|all|Qdrant URL|`http://localhost:6333`|
|`CLAIM_TRIAGE_CORE_SIM_BASE_URL`|all|simulated surrounding systems|`http://localhost:8080`|
|`CLAIM_TRIAGE_OTEL_ENDPOINT`|all|OTLP endpoint; unset means traces go to logs|unset|
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
|`postgres`|`postgres:17-alpine`|5432|the system of record; `core-sim` owns the `claims` table in it|
|`redis`|`redis:8-alpine`|6379|the queue and the checkpointer's backing store, from the graph skeleton on|
|`qdrant`|`qdrant/qdrant:v1.19.1`|6333|the vector index, from edition-aware retrieval in v0.2|
|`core-sim`|this repository's image|8080|the simulated surrounding insurance systems|
|`smoke`|the same image|—|profile-gated one-shot: `claim-triage-smoke`|

Every long-running service has a health check, and a service that needs another waits on it
(`depends_on: … service_healthy`), so `--wait` returns only once the whole stack has passed. The
compose file records why each check is an explicit `CMD-SHELL` string rather than an argv list.

`claim-triage-smoke` is what the container job gates on. It submits one claim to the running stack,
records the status transition the pipeline will record once the graph lands in ticket 04, reads both
back, and exits 0 only if the claim survived the whole path. Every wait in it is a bounded poll: a
stack that never answers fails the run instead of hanging it.

The image is built with Podman, and the artefact CI scans is built with:

```bash
podman build --squash-all --tag localhost/claim-triage-agent:dev .
```

`--squash-all` matters to that artefact: it is what makes the build's deletions real, as the
`Containerfile` header explains. `compose.yaml` builds the same tag without squashing when the image
is missing, so the stack can still come up on its own — that image runs identically and only a scan of
it would differ.

CI runs the same file on the runner's Docker Compose: Podman builds and squashes the image, Trivy
scans it at HIGH and CRITICAL, `docker load` puts it where Compose looks for it, and the job reports
success only after the smoke has carried one claim through the stack. `docs/adr/0003` records why the
two engines differ, and why the image is named `localhost/claim-triage-agent:dev` in both.

## Licence

Repository code is [Apache-2.0](LICENSE). Third-party data, its terms and its attribution are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Generated synthetic documents are CC-BY-4.0.
