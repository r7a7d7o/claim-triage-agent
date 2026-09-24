"""The `retrieval` deployable: owns the vector and lexical indexes (see `docs/adr/0001`)."""

from __future__ import annotations

from typing import Final

from claim_triage.bootstrap import bootstrap

SERVICE: Final = "retrieval"


def main() -> int:
    """Resolve this deployable's configuration, report it, and return the process exit code."""
    return bootstrap(SERVICE)
