"""The run: one pass of a claim through the pipeline, and what crosses its boundaries.

A run is the unit the pipeline, the audit log, the trace and — later — the evaluation all join on.
It carries the arm it ran as (`experiment`, `variant`) because that is what joins online behaviour
to offline evaluation, and replay is the default arm so a run needs no credentials.

The two request shapes here are the internal wire, and they are deliberately built on the contract's
claim shape: a claim that arrives at our boundary is the same claim we hand to the surrounding
systems, so the fields exist once.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from claim_triage.contract.models import ClaimStatus, ClaimSubmission

DEFAULT_EXPERIMENT: Final = "baseline"
"""The arm that runs when nothing else is asked for."""

DEFAULT_VARIANT: Final = "replay"
"""Replay, not a provider: the pipeline runs end to end with no credentials configured."""


class IntakeRequest(ClaimSubmission):
    """A claim as our boundary accepts it: the systems' claim, plus the arm to run it as."""

    experiment: str = DEFAULT_EXPERIMENT
    variant: str = DEFAULT_VARIANT


class RunRequest(IntakeRequest):
    """One run, asked for at the triager's boundary: the intake, and the run it is being run as.

    The run identifier is minted at the entry point rather than inside the pipeline, so the whole
    path — every span, every log line, every audit entry — can be joined on one identifier that
    exists before the first call is made.
    """

    run_id: UUID


class RunResult(BaseModel):
    """What a run emitted: which claim moved, where it moved to, and what to look it up by."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    claim_id: UUID
    status: ClaimStatus
    experiment: str
    variant: str
    trace_id: str


class TriageRun(BaseModel):
    """One pass of a claim through the graph, as the triager records it.

    This is the state change the audit entry is written with: it exists only where the entry does.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    claim_id: UUID
    experiment: str
    variant: str
    status: ClaimStatus
    trace_id: str
    started_at: datetime
    completed_at: datetime
