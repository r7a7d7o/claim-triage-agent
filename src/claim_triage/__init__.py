"""Shared domain library: types, ports and policy imported by every deployable."""

from typing import Final

DISTRIBUTION: Final = "claim-triage-agent"
"""The one installed distribution: what `importlib.metadata` is asked about, and what spans name."""
