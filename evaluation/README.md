# The evaluation material

What each capability is scored against, what it is judged by, and what the harness's own exercise set
is for. The machinery that reads all of this is `src/claim_triage/evaluation/`, and the runner is
`uv run poe eval` (`claim-triage-eval`).

```
gate.json               the declared thresholds: the tolerance, and the metrics that carry a floor
golden/                 the committed sets, one JSONL file per capability
baselines/v0.1.0.json   what each set scored at a release tag, read back by the gate
fixtures/               the harness's own exercise set: cases, recorded answers, regressed answers
```

## The committed sets are placeholders

`golden/*.jsonl` holds a header line and no case, deliberately. The cases are annotations of real
material — synthesized claim documents, the poistné podmienky corpus, the held-out classification
sample — and they arrive with the capabilities that answer them: v0.2's extraction and retrieval
(ticket 14) and v0.3's classification and routing. What is real here is everything that reads,
scores and judges a set, and `fixtures/` is what exercises it.

A capability with no case is reported as `no cases`, and the run passes: an empty set is not a
regression. A capability the *build* does not answer is reported as `not implemented` — nothing asked
the build anything — unless the baseline records metrics for it, in which case the coverage rule
fires.

## A set

One JSONL file per capability, in `golden/`, named after the capability. The first non-blank line is
the header, and every line after it is a case:

```json
{"capability": "extraction", "version": 1, "description": "Placeholder."}
{"case_id": "synthetic-notification-0001", "document": "synthetic-notification-0001", "fields": {"policy_number": "SIM-2026-0001", "incident_date": "2026-03-14", "claim_amount_eur": "1840.50", "registration_plate": null}}
```

* **The header** names the capability (which must match the file's name), the set's `version` — read
  back by a baseline, so a changed set is visible in the gate's history — and what the set is for.
* **`case_id`** is unique within the set. A duplicate is refused: a case scored twice would weigh
  twice.
* **`document`** is the identifier the corpus generator gave the document a case is about, not a
  path. The harness never reads documents; the build does, and the harness scores its answers.
* **A field set to `null`** asserts the document does not carry that field. Answering it with nothing
  is correct and is not credited as a hit; answering it with a value is a false positive and a false
  negative.

Extraction fields are the fields a case *scores*, and a field name is lower case, digits and
underscores (`^[a-z][a-z0-9_]*$`) because it becomes part of a metric's name.

Retrieval cases carry the question, the `incident_date` that selects the edition (which is why an
adversarial pair differs in nothing else), the `product_family`, and the clauses that answer it:

```json
{"case_id": "retrieval-technical-check-2024", "question": "…", "incident_date": "2026-03-14", "product_family": "auto-pohoda", "expected": [{"clause_id": "USK/PVO/24-4.2", "edition": "USK/PVO/24", "page": 7}]}
```

Classification cases carry the claim's features, the population's own label (`fraud_positive`, what the
calibrated score is measured against), and the three names a correct run produces — a risk band, a
severity band and the queue:

```json
{"case_id": "classification-0001", "claim": {"claim_amount_eur": "1840.50"}, "fraud_positive": false, "fraud_risk": "low", "severity": "minor", "queue": "fast-lane"}
```

The bands are the classifier's own vocabulary, so the harness holds none: a name the case set never
labels with is a miss on the case it answered, not a class of its own.

## The metrics

|Metric|What it is|
|---|---|
|`extraction.precision`, `.recall`, `.f1`|micro-averaged over every field every case states|
|`extraction.field.<name>.f1`|the same for one field, so a field read worse than the rest is visible|
|`retrieval.hit_rate`|the share of cases whose expected clause is among the first `top_k` retrieved|
|`retrieval.mrr`|one over the rank the first expected clause appeared at, averaged over cases|
|`retrieval.citation_validity`|the share of citations that resolved to a clause the same answer retrieved, on the same page|
|`classification.<axis>.precision`, `.recall`, `.f1`|macro-averaged over the labels the cases use, per axis (`fraud_risk`, `severity`)|
|`classification.routing_accuracy`|the share of cases routed to the queue the case names|
|`classification.fraud_pr_auc`|the area under the precision-recall curve of `fraud_score` against `fraud_positive`, computed as average precision — the curve the specification names for a population where fraud is the minority|

Three conventions are worth knowing before reading a number. A count whose denominator is zero scores
`0.0` rather than nothing, so a build that answered nothing is scored on that instead of skipped; the
one exception is citation validity, which is omitted when no citation exists at all, because citing
nothing is not citing wrongly — the coverage rule is what catches a capability that stops citing. A
wrong band is a miss on the case's own band *and* a false positive on the band it named, when the
cases label with that name, so precision falls when a claim is banded wrongly rather than only when
one is missed. And how deep a ranked list is read (`top_k`) is a run's setting, recorded in a
baseline: a baseline recorded at another depth is not compared.

## The declared rules

`gate.json` holds the thresholds; `claim_triage.evaluation.gate` holds the kinds of failure, because a
threshold is a number a release may change and a rule is not:

|Rule|Fires when|
|---|---|
|degradation|a measured metric is more than `tolerance` below the value the baseline records for it, comparing drops rounded to nine decimals so that "two percentage points" does not depend on binary floating point|
|floor|a measured metric is below the floor declared for it, whether or not it improved against the baseline — a tag's first run has no baseline at all, and the floor still fires|
|coverage|the baseline records a metric this run did not measure — a capability that stopped answering, a set that lost its cases, a build that stopped citing|
|comparability|the baseline was recorded under other settings than this run, so nothing is compared|

A metric with no baseline value is **adopted**, not failed: the sets grow a field, the classifier grows
an axis, and the next baseline records it. A baseline naming a metric this harness no longer measures
is refused when it is read, and so is a floor on one: a rule that can never fire is worse than no
rule. `retrieval.citation_validity` must carry a floor — that requirement is a rule rather than a
threshold, so the file cannot leave it out.

Exit codes: `0` nothing fired, `1` a declared rule fired, `2` the run could not be made at all (a set
that is not a set, answers about the wrong cases, a baseline that is not one, a tag with no baseline
unless the run is recording one). A broken artefact is never reported as a regression.

## Baselines

One file per release tag in `baselines/`, named by the tag. The tag is the version the distribution
declares (`v0.1.0`), so an increment's release records its own:

```bash
uv run poe eval --record            # judge against the tag's baseline, then record this run's
uv run poe eval --tag v0.2.0 --record
```

A baseline records each set's version and case count, the settings the run was measured under, and
every metric it measured — rounded to six decimals, which is finer than any rule compares. A run that
records a tag nothing was recorded for adopts every metric, because there is nothing to have
regressed from; a run that only judges refuses that same tag, because a missing artefact is not a
build that regressed to nothing.

A run that broke a declared rule against the baseline it is judged by is **not** recorded: a baseline
is a release record, and moving the bar down would take the evidence with it. Starting a tag's record
over is therefore deliberate — delete the file and record again — and the run that does it says so
rather than reporting a regression as a new normal.

## The fixtures

`fixtures/` is the harness's own exercise set, in the same three formats, with three cases for
extraction and retrieval and four for classification:

* `fixtures/golden/` — the cases, with the fields, questions and bands a correct run produces.
* `fixtures/predictions/` — the answers of a build at the quality `fixtures/baseline.json` records.
* `fixtures/regressed/` — the same build, answered worse: a date written the way a document writes it,
  an amount read wrong, an invented registration plate, a clause retrieved under the wrong edition, a
  citation that resolves to nothing, two bands swapped, a queue wrong, and a fraudulent claim scored
  below two clean ones. It exists to trip the gate, and to trip it on the rules rather than by
  accident: it fails degradation on many metrics and the citation floor independently.
* `fixtures/baseline.json` — recorded from `predictions/`, so the three fixture runs mean something.

```bash
uv run poe eval-fixtures                                                            # passes
uv run claim-triage-eval --sets evaluation/fixtures/golden \\
  --predictions evaluation/fixtures/regressed --baseline evaluation/fixtures/baseline.json
```

The evaluation CI job runs both, and fails unless the second refuses the run — which is what makes
"a regression injected into the fixtures trips the CI job" a property of every pull request rather
than a claim in a ticket.
