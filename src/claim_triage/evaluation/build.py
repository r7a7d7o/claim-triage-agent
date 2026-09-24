"""The build, as the harness asks it for answers: one seam, filled by the increment that lands.

A run scores *the current build*, and this module is the one place that knows what the current build
is. `STAGES` is that knowledge as data: one entry per capability, added by the increment that
implements it — retrieval in ticket 10, extraction in ticket 12, classification and routing in
tickets 16 and 17. A capability with no entry is not a failure and not a zero: nothing asked the
build anything, so the run reports it as not implemented, and the gate's coverage rule is what
notices a capability the baseline scores that the build no longer answers.

Answers can also be read from a recorded file instead (`--predictions`,
`claim_triage.evaluation.answers`): that is how a run is re-scored without the build, and it is the
seam the evaluation CI job injects a regression through. Both seams end in the same `Answers`, so
everything downstream is scored the same way whichever one produced it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from claim_triage.evaluation.answers import Answers
from claim_triage.evaluation.material import Capability

if TYPE_CHECKING:
    from claim_triage.evaluation.sets import Sets

STAGES: Final[Mapping[Capability, Callable[[Sequence[Any]], Mapping[str, Any]]]] = MappingProxyType(
    {}
)
"""The build's own answer for one capability: its cases in, one answer per case identifier out.

Empty because no capability answers a golden case yet — v0.1 ships the harness, and each capability
registers here in the increment that implements it.
"""


def answers_from_build(sets: Sets) -> Answers:
    """Ask the build for this run's answers, one capability at a time."""
    return Answers(
        extraction=_stage(Capability.EXTRACTION, sets.extraction.cases),
        retrieval=_stage(Capability.RETRIEVAL, sets.retrieval.cases),
        classification=_stage(Capability.CLASSIFICATION, sets.classification.cases),
    )


def _stage[CaseT](capability: Capability, cases: Sequence[CaseT]) -> Mapping[str, Any] | None:
    """One capability's answers, or `None` when nothing in the build answers that capability."""
    stage = STAGES.get(capability)
    if stage is None or not cases:
        return None
    return stage(cases)
