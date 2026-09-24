"""The harness a run is made with: read the sets, ask, measure, judge, print, and exit.

`claim-triage-eval` does exactly this, in that order, and its exit codes say which of three things
happened: the run passed (`0`), a declared rule fired (`1`), or the run could not be made at all
(`2`) — a set that is not a set, answers about cases the set does not hold, a baseline that is not
one. Only the middle one is a judgement about the build, which is what "exits non-zero only through
the declared gate rules" means: a broken artefact is reported as a broken artefact.

What it prints is the metric table: one row per metric per capability, with the value, the baseline
it was compared to, the difference and the verdict, then the rules that fired. A capability the
build does not answer is in the table too, as `not implemented`, because a capability missing from
the table is a capability whose regression nobody sees.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from claim_triage.evaluation.answers import read_answers
from claim_triage.evaluation.baselines import (
    Settings,
    path_for,
    read_baseline,
    recorded,
    release_tag,
    unrecorded,
    write_baseline,
)
from claim_triage.evaluation.build import STAGES, answers_from_build
from claim_triage.evaluation.gate import DECLARED_RULES, Outcome, Row, judge, read_rules
from claim_triage.evaluation.material import Unreadable
from claim_triage.evaluation.scoring import HIT_RATE_K, measure
from claim_triage.evaluation.sets import Sets, read_sets

if TYPE_CHECKING:
    from collections.abc import Sequence

    from claim_triage.evaluation.baselines import Baseline

EVALUATION_ROOT: Final = Path(__file__).parents[3] / "evaluation"
"""Where the harness's material is committed: beside the repository, not inside the package.

Three levels up from this module, because the package sits one level deeper than
`claim_triage.codegen`, whose own path to `contracts/` is two.
"""

DEFAULT_SETS: Final = EVALUATION_ROOT / "golden"
DEFAULT_RULES: Final = EVALUATION_ROOT / "gate.json"
DEFAULT_BASELINES: Final = EVALUATION_ROOT / "baselines"

PASSED: Final = 0
"""No declared rule fired: the build is what the tag's baseline says it is."""

FAILED: Final = 1
"""A declared rule fired. The only judgement the gate makes, and the only way to it."""

UNRUNNABLE: Final = 2
"""The run could not be made: an artefact is not what it claims to be.

`claim_triage.bootstrap` answers `2` for a configuration it refuses; this is the same answer for the
same kind of reason, and it is deliberately not `1`, so that a job cannot report a broken set as a
regression.
"""

NOTHING: Final = "-"
"""How the table writes a number a run did not measure: nothing, rather than zero."""


def main(argv: Sequence[str] | None = None) -> int:
    """Score the sets against the build, judge the result, and say which of the three happened."""
    arguments = _parser().parse_args(argv, namespace=Arguments())
    try:
        return _run(arguments)
    except Unreadable as broken:
        print(f"evaluation could not be made: {broken}", file=sys.stderr)
        return UNRUNNABLE


class Arguments(Namespace):
    """The options a run was made with, named as `argparse` fills them in."""

    sets: Path
    rules: Path
    predictions: Path | None
    baseline: Path | None
    tag: str | None
    record: bool
    top_k: int


def _parser() -> ArgumentParser:
    """The command line, as the README and the CI job invoke it."""
    parser = ArgumentParser(
        prog="claim-triage-eval",
        description=(
            "Score the committed golden sets, compare them to a release tag's baseline, and fail"
            " only through the declared gate rules."
        ),
    )
    parser.add_argument(
        "--sets",
        type=Path,
        default=DEFAULT_SETS,
        metavar="DIR",
        help=f"the directory holding one set per capability (default: {DEFAULT_SETS})",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=DEFAULT_RULES,
        metavar="PATH",
        help=f"the declared tolerance and floors (default: {DEFAULT_RULES})",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "score recorded answers at DIR instead of asking the current build, one file per"
            " capability, as a run records them"
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        metavar="PATH",
        help="judge against this baseline file instead of the release tag's own",
    )
    parser.add_argument(
        "--tag",
        default=None,
        metavar="TAG",
        help="the release tag whose baseline to read, and to record to (default: this build's)",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="record this run's metrics as the tag's baseline, after judging against the old one",
    )
    parser.add_argument(
        "--top-k",
        type=_depth,
        default=HIT_RATE_K,
        metavar="N",
        help=f"how deep a ranked list is read for a retrieval hit (default: {HIT_RATE_K})",
    )
    return parser


def _depth(text: str) -> int:
    """A ranked-list depth, refused by the command line rather than by the settings that hold it.

    `argparse` answers `2` for an argument it refuses, which is this harness's own "the run could
    not be made": a depth below one is a broken invocation, not a rule that fired.
    """
    depth = int(text)
    if depth < 1:
        raise ArgumentTypeError(f"a ranked list is read at least one deep, not {depth}")
    return depth


def _run(arguments: Arguments) -> int:
    """Read, ask, measure, judge, print, and record when the run was asked to."""
    sets = read_sets(arguments.sets)
    rules = read_rules(arguments.rules)
    settings = Settings(top_k=arguments.top_k)
    answers = (
        read_answers(arguments.predictions, sets)
        if arguments.predictions is not None
        else answers_from_build(sets)
    )
    measurements = measure(sets, answers, top_k=arguments.top_k)
    tag = arguments.tag if arguments.tag is not None else release_tag()
    baseline, destination = _baseline(arguments, tag, sets, settings)
    outcome = judge(measurements, baseline, rules, settings=settings)

    print(_report(arguments, sets, baseline, destination, outcome))
    if arguments.record:
        refusal = _refusal(outcome, destination)
        if refusal is None:
            write_baseline(destination, recorded(tag, sets, measurements, settings))
            print(f"recorded {destination}")
        else:
            print(refusal, file=sys.stderr)
    return PASSED if outcome.passed else FAILED


def _refusal(outcome: Outcome, destination: Path) -> str | None:
    """Why a failing run is not recorded as a tag's baseline, or `None` when it may be.

    A baseline is a release record, so a run that broke a declared rule must not quietly become one:
    recording a regression would move the bar down and take the evidence with it, and recording a
    floor breach would put a build the floor refuses at the head of the tag's history. A tag nothing
    was recorded for has nothing to *degrade* against, but the floor needs no baseline — a run under
    it fails to be recorded either way.
    """
    if outcome.passed:
        return None
    fired = ", ".join(sorted({line.split(":", 1)[0] for line in outcome.failures}))
    advice = (
        "delete the file to start the tag's record over"
        if destination.is_file()
        else "a tag's first baseline is recorded from a run that passes"
    )
    return f"refusing to record {destination}: {fired} fired ({advice})"


def _baseline(
    arguments: Arguments, tag: str, sets: Sets, settings: Settings
) -> tuple[Baseline, Path]:
    """The baseline to judge against, and where this tag's own would be written.

    A tag nothing has been recorded for is refused unless the run is recording one: a run that only
    judges has to have something to judge against, and a missing file is a missing artefact rather
    than a build that regressed to nothing.
    """
    destination = (
        arguments.baseline if arguments.baseline is not None else path_for(DEFAULT_BASELINES, tag)
    )
    if not destination.is_file():
        if not arguments.record:
            raise Unreadable(
                f"nothing was recorded for {tag!r} at {destination}; --record writes one"
            )
        return unrecorded(tag, sets, settings), destination

    baseline = read_baseline(destination)
    if arguments.baseline is None and baseline.tag != tag:
        raise Unreadable(f"{destination} records {baseline.tag!r}, not {tag!r}")
    if arguments.record and baseline.tag != tag:
        raise Unreadable(f"{destination} records {baseline.tag!r} where this run records {tag!r}")
    return baseline, destination


def _report(
    arguments: Arguments,
    sets: Sets,
    baseline: Baseline,
    baseline_path: Path,
    outcome: Outcome,
) -> str:
    """The report: what was scored, against what, under which rules, and what they said."""
    lines = [
        f"sets: {_sets(sets)}",
        _answers(arguments),
        f"baseline: {baseline.tag} at {baseline_path}, {len(baseline.metrics)} metrics recorded"
        f" at top_k={baseline.settings.top_k}",
        f"rules: {outcome.rules.describe()}",
        "",
        *_table(outcome.rows),
        "",
        _summary(outcome),
    ]
    return "\n".join(lines)


def _sets(sets: Sets) -> str:
    """Each set, its version and how many cases it holds, as one line."""
    return ", ".join(
        f"{one.capability.value} v{one.version} ({len(one.cases)} cases)" for one in sets.all
    )


def _answers(arguments: Arguments) -> str:
    """Where a run's answers came from, as this build stands.

    Read from `build.STAGES` rather than stated here: the seam fills in the increment that lands a
    capability, and a line that said "which answers no capability yet" would go on saying it after
    the first one did.
    """
    if arguments.predictions is not None:
        return f"answers: recorded at {arguments.predictions}"
    answering = ", ".join(capability.value for capability in STAGES)
    if not answering:
        return "answers: the current build, which answers no capability yet"
    return f"answers: the current build, answering {answering}"


def _table(rows: Sequence[Row]) -> list[str]:
    """The metric table: one row per metric, aligned, with the capability named once per group."""
    headers = ("capability", "metric", "cases", "answered", "value", "baseline", "delta", "verdict")
    cells = [
        (
            "" if index and rows[index - 1].capability == row.capability else row.capability.value,
            row.metric or NOTHING,
            str(row.cases),
            str(row.answered),
            NOTHING if row.value is None else f"{row.value:.4f}",
            NOTHING if row.baseline is None else f"{row.baseline:.4f}",
            NOTHING if row.delta is None else f"{row.delta * 100:+.2f}pp",
            row.verdict.value,
        )
        for index, row in enumerate(rows)
    ]
    widths = [
        max(len(header), *(len(cell[column]) for cell in cells))
        for column, header in enumerate(headers)
    ]
    return [
        "  ".join(header.ljust(widths[column]) for column, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
        *(
            "  ".join(cell[column].ljust(widths[column]) for column in range(len(headers)))
            for cell in cells
        ),
    ]


def _summary(outcome: Outcome) -> str:
    """The rules that fired, or the count of rules that did not, as the run's conclusion."""
    if outcome.passed:
        return f"gate: passed - {len(DECLARED_RULES)} declared rules, none fired"
    fired = {line.split(":", 1)[0] for line in outcome.failures}
    return "\n".join(
        [
            *(f"gate failed - {line}" for line in outcome.failures),
            f"gate: failed - {len(fired)} of {len(DECLARED_RULES)} declared rules fired"
            f" ({', '.join(sorted(fired))})",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
