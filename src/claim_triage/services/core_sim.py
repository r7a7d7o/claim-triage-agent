"""The `core-sim` deployable: stands in for the surrounding systems (see `docs/adr/0001`)."""

from __future__ import annotations

from typing import Final

from claim_triage.bootstrap import bootstrap

SERVICE: Final = "core-sim"


def main() -> int:
    """Resolve this deployable's configuration, report it, and return the process exit code."""
    return bootstrap(SERVICE)
