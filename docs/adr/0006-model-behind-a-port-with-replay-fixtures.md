# The model sits behind a port, and replay is the adapter a deployment gets until it asks for another

**Status:** accepted · 2026-09-24

Intelligence arrives in one seam. `claim_triage.model.port` defines a call — the task, the content a
model reads, and the schema the answer must validate against — and a port that answers it, and
`claim_triage.model.select` is what turns a `ModelSettings` into an implementation of that port. Two
implementations exist: `claim_triage.model.replay`, which answers from fixtures committed to this
repository, and `claim_triage.model.provider`, which speaks to an OpenAI-compatible endpoint.
Configuration selects between them, and replay is what an unconfigured environment gets.

The shape travels *in* the call rather than being checked afterwards. `ModelCall.answer_as` is a
pydantic model, the provider adapter sends its JSON schema as a strict `response_format`, and both
adapters validate the answer against that same schema before returning it. A stage therefore cannot
ask for a schema and receive unvalidated text: "structured output leaves through the port" is enforced
by the types a stage sees, not by a convention about how to call a model. The two adapters are the
only modules in the tree that can reach a model endpoint, they have one importer between them
(`select`), and the modules that import an HTTP client at all are pinned by name in
`tests/test_model_port.py` — which is what makes the ticket's first criterion a property of the tree
rather than a promise.

**A fixture is a recorded call, identified by its content.** A `ModelCall` hashes to a digest over its
task, its content and the schema it asks for, and the replay adapter indexes a fixture set by that
digest: a fixture answers by what it holds, never by where it sits. So a file can be named for what it
records, two files cannot quietly answer the same call (they are refused when the directory is read),
and a document edited after its answer was recorded is a call nothing answers rather than a call that
gets a stale answer. The answer is validated against the caller's schema on every read, so a model
whose fields changed fails at the call, naming the fixture file, rather than replaying into a stage
that cannot use it. That is the cost this decision buys: the fixtures are committed answers, and
keeping them true is a repository chore — the same chore as any other committed artefact, paid by the
schema change that invalidates them.

**The provider adapter is the only way to a model that is not in this repository, and it is off by
default.** It posts our task as the system message and a document's content as the user message, asks
for the answer with the caller's own JSON schema, and translates what comes back into the port's
vocabulary: a transport failure is `ModelUnavailable`, an error answer is `ModelRefused` carrying the
status and what the endpoint said, and an answer outside the schema is `UnusableAnswer` — a defect,
not a refusal to retry. Selecting it is an explicit act: the adapter cannot be built without an
endpoint and a model name, `ModelSettings` rejects a selection that names only one of them, and the
key is optional, because the local endpoint this repository is built to run against has none.

**What is exercised where.** The provider adapter is exercised in CI, but only against a socket the
tests serve on loopback that answers the way such an endpoint would, so what the adapter puts on the
wire is asserted and a change to it cannot rot unnoticed. No test and no CI job reaches a model
provider, holds a credential for one, or needs either: replay is what the unconfigured environment
every job runs in selects. The ticket's "never exercised in CI" is read as "never against a provider in
CI" — an adapter with no test at all would be the one piece of this repository whose behaviour nothing
checks, and that is the larger risk. Should the stricter reading be wanted, the tests that serve the
fake endpoint are one class and its fixture, and marking them out of the unit job is a small change.

## Considered Options

- **Calling a model from wherever it is needed**, with a client per stage. Rejected: "no caller can
  reach a model provider without going through the port" would then be a review rule rather than a
  structure, and the second caller is where structured output stops being guaranteed.
- **Fixture files named by the digest of the call.** Rejected in favour of names that say what they
  record: the identity is the content either way, and a readable name is what makes a fixture set
  reviewable at all — a directory of digests is not something a person browses.
- **Recording fixtures through a command that calls a provider and writes down what it answered.**
  Deferred, not rejected: the format is data a maintainer can write or paste, and the increment that
  first needs recorded answers in volume (v0.2's extraction) is where a recorder earns its tests.
  Until then it would be a third adapter with no caller in the pipeline.
- **Retries and a circuit breaker inside the provider adapter.** Rejected for this increment:
  resilience policy is ticket 28's subject, and a retry here would hide the failure counts that
  ticket's circuit breaker is supposed to act on.
- **Requiring a key whenever the provider adapter is selected.** Rejected: a hosted endpoint needs
  one, the local endpoint this repository targets does not, and requiring it would make "no
  credentials required to run the demo" false for the configuration the demo uses.

## Consequences

- Every deployable resolves and reports which model it would answer a call with (`model_provider`,
  `model_name`, `model_base_url`), so the selection is visible in the startup line rather than
  inferred from behaviour later. The key is never reported: a startup report is read out of logs.
- A schema change invalidates the fixtures recorded for it. The failure is loud, names the fixture
  file, and happens in the unit job rather than in a demo.
- The port has no caller in the pipeline yet: v0.1's graph is one no-op node, and the first stage that
  asks a model for something (v0.2's extraction) is the increment that calls it. Until then, what
  holds the port honest is the fixture set and the tests, not a stage.
- `httpx2` is imported from three modules, and the test that names those three is what stops a fourth
  from appearing quietly — a stage opening its own connection is exactly the shape this decision
  exists to prevent.
