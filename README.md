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
The graph, API boundary, compose stack, guard and audit skeletons, replay model client and
evaluation harness arrive in the remaining v0.1 tickets.

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

Prerequisites: [`uv`](https://docs.astral.sh/uv/). No credentials, no infrastructure.

```bash
uv sync                                    # Python 3.13 is fetched and pinned; installs the venv
uv run poe check                           # lint + types + unit tests — the gate CI runs
```

Individual tasks: `uv run poe lint`, `uv run poe types`, `uv run poe unit`, `uv run poe format`.
CI runs the same three commands as three separate jobs.

Start a deployable — it resolves its configuration from the environment, prints it as one JSON line
and exits 0, or exits 2 naming the variable that was rejected:

```bash
uv run claim-triage-api
uv run claim-triage-core-sim
```

## Layout

```
pyproject.toml          single uv project: dependencies, console scripts, task and tool config
src/claim_triage/       the shared domain library imported by every deployable
src/claim_triage/services/registry.py
                        the deployables as data — name, console script, port, scaling axis, reason
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

## Configuration

Configuration is environment-based, per deployable. `.env` is read when present (gitignored); copy
[`.env.example`](.env.example) to start. Compose endpoints are the defaults, so nothing is required
to run locally.

|Variable|Applies to|Meaning|Default|
|---|---|---|---|
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

## Licence

Repository code is [Apache-2.0](LICENSE). Third-party data, its terms and its attribution are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Generated synthetic documents are CC-BY-4.0.
