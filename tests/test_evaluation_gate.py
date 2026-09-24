"""The gate and the runner: the metric table, the declared rules, and the exit codes.

Ticket 07 asks for four things of this harness, and each is asserted here from the outside — through
`claim_triage.evaluation.runner.main`, which is what the CI job runs, or through `judge`, which is
where a rule's arithmetic lives:

* the runner prints the metric table for a committed set, and leaves non-zero only through a
  declared rule: a broken set, answers about the wrong cases, or a baseline that is not one is `2`,
  a rule that fired is `1`, and nothing else is anything;
* a regression injected into the fixtures trips it — the fixture material is scored at the
  quality its baseline records, then with the regressed answers, and only the second fails;
* baselines are stored per release tag and read back by the gate — one file per tag, recording
  the sets' versions and what they scored, which is what `evaluation/baselines/` holds;
* citation validity has an absolute floor independent of the two-point rule — a metric may
  improve against its baseline and still fail, and no rules file may leave that floor out.

The arithmetic of every rule is judged on measurements built here rather than on the fixture
material, so a boundary is asserted at the boundary: a drop of exactly the tolerance passes.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Final

import pytest

from claim_triage.evaluation import runner
from claim_triage.evaluation.baselines import (
    Baseline,
    SetRecord,
    Settings,
    path_for,
    read_baseline,
    release_tag,
)
from claim_triage.evaluation.gate import (
    DECLARED_RULES,
    Rule,
    Rules,
    Verdict,
    judge,
)
from claim_triage.evaluation.material import Capability
from claim_triage.evaluation.runner import (
    DEFAULT_BASELINES,
    EVALUATION_ROOT,
    FAILED,
    PASSED,
    UNRUNNABLE,
    main,
)
from claim_triage.evaluation.scoring import Measurement, Reading

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

FIXTURES: Final = EVALUATION_ROOT / "fixtures"
"""The harness's own exercise material: cases, recorded answers, and the regressed ones."""

FIXTURE_GOLDEN: Final = FIXTURES / "golden"
FIXTURE_ANSWERS: Final = FIXTURES / "predictions"
FIXTURE_REGRESSED: Final = FIXTURES / "regressed"
FIXTURE_BASELINE: Final = FIXTURES / "baseline.json"

COMMITTED_SETS: Final = EVALUATION_ROOT / "golden"
DECLARED_RULES_PATH: Final = EVALUATION_ROOT / "gate.json"

TOLERANCE: Final = 0.02
CITATION_FLOOR: Final = 0.98


def fixture_run(*arguments: str) -> int:
    """One run over the fixture material, as the CI job makes it."""
    return main(
        [
            "--sets",
            str(FIXTURE_GOLDEN),
            "--predictions",
            str(FIXTURE_ANSWERS),
            "--baseline",
            str(FIXTURE_BASELINE),
            *arguments,
        ]
    )


def a_baseline(metrics: Mapping[str, float], *, top_k: int = 5, tag: str = "v9.9.9") -> Baseline:
    """A baseline recording what a metric scored, for a rule to be judged against."""
    return Baseline(
        tag=tag,
        settings=Settings(top_k=top_k),
        sets={capability: SetRecord(version=1, cases=3) for capability in Capability},
        metrics=dict(metrics),
    )


def a_measurement(
    metrics: Mapping[str, float],
    *,
    capability: Capability = Capability.RETRIEVAL,
    reading: Reading = Reading.SCORED,
    version: int = 1,
    cases: int = 3,
    answered: int = 3,
) -> Measurement:
    """What one capability measured in a run, for a rule to be judged on."""
    return Measurement(
        capability=capability,
        version=version,
        reading=reading,
        cases=cases,
        answered=answered,
        metrics=dict(metrics),
    )


def rewritten_fixtures(
    tmp_path: Path,
    capability: Capability,
    *,
    version: int | None = None,
    cases: int | None = None,
) -> tuple[Path, Path]:
    """The fixture material, with one capability's set re-versioned or cut down to fewer cases.

    The answers move with it: a case the set no longer holds may not be answered, so the recorded
    answers are cut to the cases that are left.
    """
    golden = tmp_path / "golden"
    answers = tmp_path / "predictions"
    shutil.copytree(FIXTURE_GOLDEN, golden)
    shutil.copytree(FIXTURE_ANSWERS, answers)

    set_file = golden / f"{capability.value}.jsonl"
    header, *held = set_file.read_text(encoding="utf-8").splitlines()
    if version is not None:
        named = json.loads(header)
        named["version"] = version
        header = json.dumps(named)
    if cases is not None:
        held = held[:cases]
        kept = {json.loads(line)["case_id"] for line in held}
        answered = answers / f"{capability.value}.jsonl"
        answered.write_text(
            "".join(
                f"{line}\n"
                for line in answered.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["case_id"] in kept
            ),
            encoding="utf-8",
        )
    set_file.write_text("".join(f"{line}\n" for line in [header, *held]), encoding="utf-8")
    return golden, answers


def the_rules(**overrides: object) -> Rules:
    """The declared rules, as `evaluation/gate.json` declares them, unless a test changes one."""
    declared: dict[str, object] = {
        "tolerance": TOLERANCE,
        "floors": {"retrieval.citation_validity": CITATION_FLOOR},
    }
    declared.update(overrides)
    return Rules.model_validate(declared)


def test_the_committed_sets_print_a_table_and_pass(capsys: pytest.CaptureFixture[str]) -> None:
    """The run over the committed sets: a table with a row per capability, and nothing fired."""
    assert main([]) == PASSED

    printed = capsys.readouterr().out
    assert "extraction v1 (0 cases)" in printed
    assert "retrieval v1 (0 cases)" in printed
    assert "classification v1 (0 cases)" in printed
    assert "no cases" in printed
    assert f"gate: passed - {len(DECLARED_RULES)} declared rules, none fired" in printed


def test_the_answers_line_follows_the_seam(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report names what the build answers, read from the seam rather than stated in the report.

    The line said "which answers no capability yet" until this was asked of `build.STAGES`; an
    increment that registers a stage is what makes it say otherwise, so a stage is registered here.
    """
    monkeypatch.setattr(runner, "STAGES", {Capability.RETRIEVAL: lambda cases: {}})

    assert main(["--sets", str(COMMITTED_SETS)]) == PASSED

    assert "answers: the current build, answering retrieval" in capsys.readouterr().out


def test_the_baseline_a_run_reads_is_the_one_its_release_tag_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A baseline is stored per tag, and the tag this build declares is the file the run reads."""
    assert main([]) == PASSED

    assert release_tag() == "v0.1.0"
    assert str(path_for(DEFAULT_BASELINES, "v0.1.0")) in capsys.readouterr().out
    recorded = read_baseline(DEFAULT_BASELINES / "v0.1.0.json")
    assert recorded.tag == release_tag()
    assert set(recorded.sets) == set(Capability)
    assert recorded.sets[Capability.EXTRACTION].cases == 0


def test_a_set_the_build_does_not_answer_is_reported_rather_than_failed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nothing answered the fixture cases, so nothing is scored: not implemented, not zero."""
    assert main(["--sets", str(FIXTURE_GOLDEN)]) == PASSED

    printed = capsys.readouterr().out
    assert "not implemented" in printed
    assert "extraction" in printed


def test_the_fixture_material_passes_the_baseline_it_recorded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The quality the baseline records is the quality that passes: the tripwire's green half."""
    assert fixture_run() == PASSED

    printed = capsys.readouterr().out
    assert "gate: passed" in printed
    assert "regressed" not in printed


def test_a_regression_injected_into_the_fixtures_trips_the_gate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other half: the same run with the regressed answers fails, naming the rules it broke."""
    assert (
        main(
            [
                "--sets",
                str(FIXTURE_GOLDEN),
                "--predictions",
                str(FIXTURE_REGRESSED),
                "--baseline",
                str(FIXTURE_BASELINE),
            ]
        )
        == FAILED
    )

    printed = capsys.readouterr().out
    assert "degradation: extraction.f1 fell" in printed
    assert "floor: retrieval.citation_validity is" in printed
    assert "classification.fraud_pr_auc" in printed
    assert "gate: failed - 2 of 4 declared rules fired (degradation, floor)" in printed


def test_a_case_the_answers_leave_out_is_scored_as_a_miss(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An answer that is not there is not skipped: the case's fields were still not read."""
    answers = tmp_path / "answers"
    answers.mkdir()
    for path in sorted(FIXTURE_ANSWERS.glob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        text = "".join(lines[:-1] if path.stem == "extraction" else lines)
        (answers / path.name).write_text(text, encoding="utf-8")

    assert (
        main(
            [
                "--sets",
                str(FIXTURE_GOLDEN),
                "--predictions",
                str(answers),
                "--baseline",
                str(FIXTURE_BASELINE),
            ]
        )
        == FAILED
    )

    printed = capsys.readouterr().out
    assert "extraction.recall" in printed
    assert "degradation: extraction.recall fell" in printed


def test_the_floor_fires_where_the_two_points_would_not() -> None:
    """A metric that improved against its baseline still fails beneath its absolute floor."""
    outcome = judge(
        [a_measurement({"retrieval.citation_validity": 0.97})],
        a_baseline({"retrieval.citation_validity": 0.50}),
        the_rules(),
        settings=Settings(top_k=5),
    )

    assert not outcome.passed
    assert outcome.rows[0].verdict is Verdict.BELOW_FLOOR
    assert outcome.rows[0].delta == pytest.approx(0.47)
    assert Rule.FLOOR in outcome.failures[0]
    assert not any(Rule.DEGRADATION in failure for failure in outcome.failures)


def test_a_metric_may_fall_exactly_the_tolerance() -> None:
    """Two points is the allowance: the boundary passes, and anything beyond it does not."""
    at_the_boundary = judge(
        [a_measurement({"retrieval.mrr": 0.98})],
        a_baseline({"retrieval.mrr": 1.0}),
        the_rules(),
        settings=Settings(top_k=5),
    )
    beyond_it = judge(
        [a_measurement({"retrieval.mrr": 0.9799})],
        a_baseline({"retrieval.mrr": 1.0}),
        the_rules(),
        settings=Settings(top_k=5),
    )

    assert at_the_boundary.passed
    assert at_the_boundary.rows[0].verdict is Verdict.OK
    assert not beyond_it.passed
    assert beyond_it.rows[0].verdict is Verdict.REGRESSED


def test_a_metric_no_baseline_records_is_adopted_rather_than_failed() -> None:
    """The sets grow a field and the next baseline records it: a first number is not one."""
    outcome = judge(
        [a_measurement({"classification.fraud_pr_auc": 0.75})],
        a_baseline({}),
        the_rules(),
        settings=Settings(top_k=5),
    )

    assert outcome.passed
    assert outcome.rows[0].verdict is Verdict.ADOPTED


def test_a_capability_the_baseline_scores_and_the_run_does_not_measure_fails_coverage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A metric that disappears from the table would take its regression with it."""
    assert main(["--sets", str(FIXTURE_GOLDEN), "--baseline", str(FIXTURE_BASELINE)]) == FAILED

    printed = capsys.readouterr().out
    assert "coverage: extraction.f1 was recorded at 1.0000" in printed
    assert "and this run did not measure it" in printed
    assert "not measured" in printed
    assert "gate: failed - 1 of 4 declared rules fired (coverage)" in printed


def test_a_set_that_lost_cases_fails_coverage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A set that shrank is less evidence rather than the same evidence, and the gate says so.

    The baseline records how many cases each set held at the tag, so a capability answered over a
    third of its material cannot pass on the numbers of the set it used to be.
    """
    golden, answers = rewritten_fixtures(tmp_path, Capability.EXTRACTION, cases=1)

    assert (
        main(
            [
                "--sets",
                str(golden),
                "--predictions",
                str(answers),
                "--baseline",
                str(FIXTURE_BASELINE),
            ]
        )
        == FAILED
    )

    printed = capsys.readouterr().out
    assert "coverage: extraction holds 1 case where the baseline records 3" in printed
    assert "extraction.f1" in printed
    assert "gate: failed - 1 of 4 declared rules fired (coverage)" in printed


def test_a_set_that_grew_is_compared_rather_than_refused() -> None:
    """Only losing cases is a failure: a set that grew asks the same question of more material."""
    outcome = judge(
        [a_measurement({"retrieval.mrr": 1.0}, cases=4)],
        a_baseline({"retrieval.mrr": 1.0}),
        the_rules(),
        settings=Settings(top_k=5),
    )

    assert outcome.passed
    assert outcome.rows[0].verdict is Verdict.OK


def test_a_metric_over_another_version_of_a_set_is_not_compared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same metrics over another version of a set are another question: nothing is compared."""
    golden, answers = rewritten_fixtures(tmp_path, Capability.CLASSIFICATION, version=2)

    assert (
        main(
            [
                "--sets",
                str(golden),
                "--predictions",
                str(answers),
                "--baseline",
                str(FIXTURE_BASELINE),
            ]
        )
        == FAILED
    )

    printed = capsys.readouterr().out
    assert (
        "comparability: the baseline records classification v1, and this run scores"
        " classification v2" in printed
    )
    assert "incomparable" in printed


def test_a_baseline_recorded_under_other_settings_is_not_compared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two runs at different depths are two questions: nothing is compared, and the run fails."""
    other = tmp_path / "v9.9.9.json"
    assert (
        main(
            [
                "--sets",
                str(FIXTURE_GOLDEN),
                "--predictions",
                str(FIXTURE_ANSWERS),
                "--baseline",
                str(other),
                "--tag",
                "v9.9.9",
                "--top-k",
                "3",
                "--record",
            ]
        )
        == PASSED
    )

    assert (
        main(
            [
                "--sets",
                str(FIXTURE_GOLDEN),
                "--predictions",
                str(FIXTURE_ANSWERS),
                "--baseline",
                str(other),
            ]
        )
        == FAILED
    )

    printed = capsys.readouterr().out
    assert "comparability: the baseline was recorded at top_k=3 and this run is top_k=5" in printed
    assert "incomparable" in printed


def test_a_baseline_is_recorded_for_a_tag_and_read_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Recording a tag writes what the run measured, and the next run is judged against it."""
    recorded = tmp_path / "v9.9.9.json"

    assert fixture_run("--baseline", str(recorded), "--tag", "v9.9.9", "--record") == PASSED
    capsys.readouterr()

    baseline = read_baseline(recorded)
    assert baseline.tag == "v9.9.9"
    assert baseline.settings == Settings(top_k=5)
    assert baseline.sets[Capability.EXTRACTION] == SetRecord(version=1, cases=3)
    assert baseline.metrics["extraction.f1"] == 1.0
    assert baseline.metrics["retrieval.mrr"] == 0.833333

    assert fixture_run("--baseline", str(recorded), "--tag", "v9.9.9") == PASSED
    assert "gate: passed" in capsys.readouterr().out


def test_a_tag_nothing_was_recorded_for_is_refused_unless_the_run_records_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tag with no baseline is a missing artefact, not a build that regressed to nothing."""
    absent = tmp_path / "v9.9.9.json"
    assert fixture_run("--baseline", str(absent), "--tag", "v9.9.9") == UNRUNNABLE

    assert "nothing was recorded for 'v9.9.9'" in capsys.readouterr().err


def test_recording_to_a_file_that_records_another_tag_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The file and the tag it is recorded for have to be the same tag, or the store lies."""
    other = tmp_path / "v9.9.9.json"
    other.write_text(a_baseline({}, tag="v8.8.8").model_dump_json(), encoding="utf-8")

    assert fixture_run("--baseline", str(other), "--tag", "v9.9.9", "--record") == UNRUNNABLE

    assert "records 'v8.8.8' where this run records 'v9.9.9'" in capsys.readouterr().err


def test_a_baseline_that_records_no_set_for_a_capability_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A baseline is a record of all three: one that forgot a capability is not one."""
    incomplete = tmp_path / "v9.9.9.json"
    document = json.loads(a_baseline({}, tag="v9.9.9").model_dump_json())
    del document["sets"]["classification"]
    incomplete.write_text(json.dumps(document), encoding="utf-8")

    assert fixture_run("--baseline", str(incomplete), "--tag", "v9.9.9") == UNRUNNABLE

    assert "no set recorded for classification" in capsys.readouterr().err


def test_a_baseline_recording_a_value_that_is_not_a_proportion_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A baseline value outside zero to one is a typo, not something to do arithmetic with."""
    impossible = tmp_path / "v9.9.9.json"
    document = json.loads(a_baseline({}, tag="v9.9.9").model_dump_json())
    document["metrics"] = {"extraction.f1": 1.2}
    impossible.write_text(json.dumps(document), encoding="utf-8")

    assert fixture_run("--baseline", str(impossible), "--tag", "v9.9.9") == UNRUNNABLE

    assert "which is not a proportion" in capsys.readouterr().err


def test_the_store_refuses_a_file_that_records_another_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One file per tag: a file recording another is refused, not judged as this tag's."""
    monkeypatch.setattr(runner, "DEFAULT_BASELINES", tmp_path)
    (tmp_path / "v9.9.9.json").write_text(
        a_baseline({}, tag="v8.8.8").model_dump_json(), encoding="utf-8"
    )

    assert main(["--sets", str(FIXTURE_GOLDEN), "--tag", "v9.9.9"]) == UNRUNNABLE

    assert "records 'v8.8.8', not 'v9.9.9'" in capsys.readouterr().err


def test_rules_declaring_a_floor_outside_zero_and_one_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A floor above one can never be met; one below zero can never be broken. Neither is a rule."""
    rules = tmp_path / "gate.json"
    rules.write_text(
        json.dumps({"tolerance": TOLERANCE, "floors": {"retrieval.citation_validity": 1.5}}),
        encoding="utf-8",
    )

    assert fixture_run("--rules", str(rules)) == UNRUNNABLE

    assert "which is not a proportion" in capsys.readouterr().err


def test_a_baseline_recording_a_metric_nobody_measures_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A metric this harness stopped measuring would make its comparison mean nothing."""
    stale = tmp_path / "v9.9.9.json"
    stale.write_text(
        json.dumps(
            {
                "tag": "v9.9.9",
                "settings": {"top_k": 5},
                "sets": {capability.value: {"version": 1, "cases": 3} for capability in Capability},
                "metrics": {"extraction.accuracy": 0.9},
            }
        ),
        encoding="utf-8",
    )

    assert fixture_run("--baseline", str(stale), "--tag", "v9.9.9") == UNRUNNABLE

    assert "is not a metric this harness measures" in capsys.readouterr().err


def test_rules_that_declare_a_floor_for_no_metric_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A floor nobody measures never fires, which is worse than no floor at all."""
    rules = tmp_path / "gate.json"
    rules.write_text(
        json.dumps({"tolerance": TOLERANCE, "floors": {"extraction.accuracy": 0.9}}),
        encoding="utf-8",
    )

    assert fixture_run("--rules", str(rules)) == UNRUNNABLE

    assert "is not a metric this harness measures" in capsys.readouterr().err


def test_rules_without_a_floor_for_citation_validity_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Citation validity carries an absolute floor: rules that leave it out are not the rules."""
    rules = tmp_path / "gate.json"
    rules.write_text(json.dumps({"tolerance": TOLERANCE, "floors": {}}), encoding="utf-8")

    assert fixture_run("--rules", str(rules)) == UNRUNNABLE

    assert "no floor for retrieval.citation_validity" in capsys.readouterr().err


def test_answers_that_cannot_be_read_are_reported_as_unrunnable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreadable answers file could not be made a run, and is never read as a regression."""
    answers = tmp_path / "answers"
    answers.mkdir()
    for path in sorted(FIXTURE_ANSWERS.glob("*.jsonl")):
        (answers / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (answers / "extraction.jsonl").write_text(
        '{"case_id": "no-such-case", "answer": {"fields": {}}}\n', encoding="utf-8"
    )

    assert (
        main(
            [
                "--sets",
                str(FIXTURE_GOLDEN),
                "--predictions",
                str(answers),
                "--baseline",
                str(FIXTURE_BASELINE),
            ]
        )
        == UNRUNNABLE
    )

    assert "evaluation could not be made" in capsys.readouterr().err


def test_a_set_that_is_not_a_set_is_reported_as_unrunnable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broken artefact is `2`, not `1`: a job must not report it as a regression."""
    broken = tmp_path / "golden"
    broken.mkdir()
    for capability in Capability:
        (broken / f"{capability.value}.jsonl").write_text(
            json.dumps({"capability": capability.value, "version": 1, "description": "x"}) + "\n",
            encoding="utf-8",
        )
    (broken / "extraction.jsonl").write_text("not a set at all\n", encoding="utf-8")

    assert fixture_run("--sets", str(broken)) == UNRUNNABLE

    assert "evaluation could not be made" in capsys.readouterr().err


def test_a_depth_below_one_is_refused_by_the_command_line() -> None:
    """A ranked list read zero deep is a broken invocation: `2`, refused before anything is read."""
    with pytest.raises(SystemExit) as refused:
        main(["--top-k", "0"])

    assert refused.value.code == UNRUNNABLE


def test_a_run_that_broke_a_rule_is_not_recorded_as_the_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A baseline is a release record: a regression must not overwrite what it regressed on."""
    recorded = tmp_path / "fixtures.json"
    recorded.write_text(
        a_baseline({"retrieval.citation_validity": 1.0}, tag="fixtures").model_dump_json(),
        encoding="utf-8",
    )
    before = recorded.read_text(encoding="utf-8")

    assert (
        main(
            [
                "--sets",
                str(FIXTURE_GOLDEN),
                "--predictions",
                str(FIXTURE_REGRESSED),
                "--baseline",
                str(recorded),
                "--tag",
                "fixtures",
                "--record",
            ]
        )
        == FAILED
    )

    assert recorded.read_text(encoding="utf-8") == before
    assert "refusing to record" in capsys.readouterr().err


def test_a_first_recording_that_broke_the_floor_is_refused_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The floor needs no baseline: a tag's first baseline is a run that passes as well."""
    absent = tmp_path / "v9.9.9.json"

    assert (
        main(
            [
                "--sets",
                str(FIXTURE_GOLDEN),
                "--predictions",
                str(FIXTURE_REGRESSED),
                "--baseline",
                str(absent),
                "--tag",
                "v9.9.9",
                "--record",
            ]
        )
        == FAILED
    )

    assert not absent.exists()
    assert "a tag's first baseline is recorded from a run that passes" in capsys.readouterr().err


def test_the_committed_rules_are_the_declared_ones() -> None:
    """The committed file is the single source of the thresholds the run is measured against."""
    declared = the_rules()
    committed = Rules.model_validate_json(DECLARED_RULES_PATH.read_text(encoding="utf-8"))

    assert committed == declared
    assert committed.floors["retrieval.citation_validity"] == CITATION_FLOOR
    assert "more than 2.00 points below its baseline fails" in committed.describe()
