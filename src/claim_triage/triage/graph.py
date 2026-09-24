"""The graph skeleton: the topology every later stage lands in, with one no-op node in it.

v0.1 has no intelligence, so the graph is the shape and nothing else: one node that moves the claim
on and decides nothing. What it establishes is the seam the rest of the topology arrives in — typed
state in, typed state out — and the node name the audit log records against a state change.

The pipeline depends on the compiled graph through this module's protocol rather than through
LangGraph's own types, so the skeleton can be re-cut without the pipeline noticing, and the tests
that matter drive it through the ASGI boundary rather than through its internals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from claim_triage.contract.models import ClaimStatus, ClaimSubmission

if TYPE_CHECKING:
    from collections.abc import Mapping

    from typing_extensions import TypedDict
else:
    from typing import TypedDict

NODE: Final = "noop"
"""The one node, and the name the audit log records against the state change it causes."""


class TriageState(TypedDict, total=False):
    """What one run carries between nodes; the node that produces a field is the one that sets
    it."""

    claim: ClaimSubmission
    run_id: UUID
    experiment: str
    variant: str
    status: ClaimStatus


def noop(state: TriageState) -> TriageState:
    """The node every later stage replaces: it moves the claim to `triaged` and decides nothing."""
    return {"status": ClaimStatus.TRIAGED}


class ClaimGraph(Protocol):
    """The graph as the pipeline uses it: typed state in, the state every node produced out."""

    def invoke(self, state: TriageState) -> Mapping[str, object]:
        """Run the graph to its end over one claim's state."""
        ...


def build_graph() -> ClaimGraph:
    """Build the walking skeleton's graph: start, one no-op node, end."""
    builder: StateGraph[TriageState] = StateGraph(TriageState)
    builder.add_node(NODE, noop)
    builder.add_edge(START, NODE)
    builder.add_edge(NODE, END)
    # LangGraph types `invoke` as returning a plain mapping, so the contract above is stated as a
    # protocol and the compiled graph is cast onto it rather than the pipeline depending on
    # LangGraph's own type parameters.
    return cast("ClaimGraph", builder.compile())
