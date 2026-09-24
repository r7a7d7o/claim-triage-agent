# The evaluation gate is declared as data, and its sets are empty until there is something to annotate

**Status:** accepted · 2026-09-24

v0.1 needs "continuously evaluated" to be machinery rather than a promise, at the point where no
capability exists to evaluate. That is an awkward place to start, and the shape of the answer is the
decision: **the harness is complete and the sets are empty**. Everything that reads a set, scores
answers, compares them to a release tag's baseline and refuses a regression is real, tested and run
in CI; the cases are the document-intake increment's work, because a set of cases is an annotation of
material that does not exist yet.

## The seam, and what crosses it

Four things, in one direction:

```
evaluation/golden/*.jsonl   the cases: what a correct answer holds
        ↓  answers, from the build or from a recorded run
evaluation/… metrics        named numbers, scored per capability
        ↓  compared to the tag's baseline
evaluation/gate.json        the declared rules: a tolerance, and the metrics with a floor
        ↓
exit 0 / 1 / 2              passed, a rule fired, or the run could not be made
```

A metric has a **name** — `extraction.f1`, `retrieval.citation_validity`,
`classification.fraud_pr_auc`, one F1 per extracted field — because a baseline is a record of named
numbers and a gate compares them by name. The vocabulary is code (`claim_triage.evaluation.scoring`)
and the thresholds are data (`evaluation/gate.json`): a release may change a number in a reviewed
file, and cannot invent a kind of failure. A baseline or a floor naming a metric the harness does not
measure is refused when it is read, because a rule that can never fire is worse than no rule at all.

## Where the answers come from

`claim_triage.evaluation.build.STAGES` is the build's answers as data: one entry per capability,
added by the increment that implements it — retrieval in ticket 10, extraction in ticket 12,
classification and routing in tickets 16 and 17. A capability with no entry is reported as
`not implemented`, never scored as zero, because nothing asked the build anything: a zero would be a
number this harness invented.

Answers can also be read from a recorded directory (`--predictions`), in the same envelope as a
recorded model call: one line per case, the answer validated against that capability's own shape.
That seam is not a test hook. It is how a run is re-scored without the build, how a result is
reproduced from committed material, and — because a case with no recorded answer is a miss rather
than a skip — how the evaluation CI job injects a regression by installing worse answers over the
fixtures.

## The rules, and the two that are easy to get wrong

Four ways a run fails, each naming itself in its message: **degradation** (more than the two-point
tolerance below the tag's baseline), **floor** (below a declared floor, whether or not it improved),
**coverage** (a metric the baseline records that this run did not measure) and **comparability**
(a baseline recorded under other settings). Three consequences are deliberate:

- **Adoption is not failure.** A metric with no baseline value is `adopted`: the sets grow a field,
  the classifier grows an axis, and the next baseline records it. The alternative — failing a metric
  the build has never been measured on — would make every increment's first run red for a reason
  nobody could act on.
- **Coverage is what makes disappearance loud.** Without it, a capability that stopped answering would
  simply leave the table, and its regression would leave with it. This is also what makes the empty
  placeholder sets honest: a tag's baseline records what it measured, and a set that loses cases fails
  the gate rather than quietly shrinking the evidence.
- **The citation floor is a requirement, not a threshold.** `retrieval.citation_validity` above 0.98
  is data; that it *has* a floor is enforced by the rules model, so no rules file can drop the one
  metric the specification gives an absolute floor (user stories 50 and 91). A metric may improve
  against its baseline and still fail here — which is the property the two rules exist to have
  separately.

The degradation comparison rounds the drop to nine decimals before comparing it. A baseline is a
record to six, and `1.0 - 0.98` is `0.020000000000000018` in binary floating point: without that,
whether "two percentage points" is exceeded would depend on which numbers happened to be involved.

Two metric vocabularies are fixed here rather than left to the increments that fill the sets, because
a baseline and a floor are keyed by name. A wrong label is a miss on the case's own label **and** a
false positive on the label it named, so `precision` means here what it means for extracted fields —
a classifier that bands everything `high` loses precision as well as recall. And the fraud score is
measured as the area under the **precision-recall** curve, computed as average precision, under
`classification.fraud_pr_auc`: that is what the specification names for a population where fraud is
the minority, and where a ROC curve's false-positive rate is diluted by the negatives such a
population has in abundance.

## Baselines are files, keyed by the release tag

One committed file per tag in `evaluation/baselines/`, named by the tag, holding what each set scored
at which version and under which settings. The tag is the version the distribution declares (`v0.1.0`),
which is the same string the increment's annotated tag carries, so recording a baseline needs no git
call, no clock and no network, and works in a container. `--record` judges against the old baseline
and then writes the new one; a run that *only* judges refuses a tag with no baseline, because a
missing artefact is not a build that regressed to nothing — while a run that records one for a new tag
adopts every metric, which is what starting a tag means.

Settings travel in the file (`top_k`) because a hit rate at five and a hit rate at ten are two
questions, not two answers. A baseline recorded at another depth fails comparability rather than being
compared.

## Considered Options

- **Committing placeholder *cases*.** Rejected: a case is an annotation — a field set, a clause under
  the edition that applied, a band a claim belongs in — and inventing them before the corpus and the
  schemas exist would fix the annotation rules of v0.2 by accident, then be inherited by every
  increment that trusts them. An empty set states the format and claims nothing.
- **Injecting the regression with a CI script that mutates the fixtures.** Rejected: the regressed
  answers are then invisible in review, whereas a committed `regressed/` directory is a diffable
  statement of what "worse" means — and the injection stays a copy, which is what the ticket's
  wording asks for.
- **A pytest module instead of a CI job.** Rejected: the specification calls this a verification gate
  rather than a unit, and a metric comparison buried in the unit suite is invisible in the log of the
  job it fails. The gate is a command whose table is the evidence.
- **Thresholds as Python constants.** Rejected: a tolerance is a release decision, and user story 71's
  principle — safety and policy thresholds stored as data — applies to the evaluation's thresholds as
  much as to the policy gate's.
- **Scoring against the latest baseline rather than the tag's.** Rejected: user story 91 asks for
  per-tag baselines so that improvement and decay across increments are visible; a single moving
  baseline loses exactly that history.
- **Storing baselines in git tags or a branch.** Rejected: a committed file per tag is diffable, reads
  in the same checkout as the code it judges, and needs no git in the container.

## Consequences

- `uv run poe eval` measures nothing until v0.2 fills the extraction and retrieval sets. The job is
  green because the sets are empty and nothing fired — and the coverage rule is what makes that
  visible rather than silent, since the tag's baseline starts recording the moment a set has a case.
- The teeth of the CI job in v0.1 are the fixtures: the harness's own exercise set is scored at the
  quality `fixtures/baseline.json` records, then again with `fixtures/regressed/` installed over it,
  and the job fails unless the gate refuses the second. A gate that refused everything would fail the
  step before it.
- Every per-field metric is gated. That is intended — the report says *which* field regressed — but it
  means a single case flipping a field can move a per-field F1 by tens of points on a small set. The
  committed sets will be large enough for that not to be noise, and the fixture set is small because
  it is built to trip.
- Renaming a metric invalidates the baselines that record it: they are refused on read rather than
  silently compared. That is the cost of names carrying meaning, and it is paid once per rename, by
  the increment that renames — which is also the increment that has to re-record.
- What this does not cover is named rather than implied: the red-team injection gate (user story 65)
  is a gate of its own and arrives with v1.0; the LLM-as-judge summary score (user story 94) is
  supplementary and never gates, which is why no rule here can express it. Only the three
  capabilities in the specification are scored, and a fourth would be a set, a scorer and its metrics
  rather than a change to the gate.
