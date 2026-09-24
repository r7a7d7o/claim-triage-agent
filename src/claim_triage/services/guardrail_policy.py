"""The `guardrail-policy` deployable: safety logic ships on its own (see `docs/adr/0001`)."""

from __future__ import annotations

from typing import Final

from claim_triage.bootstrap import bootstrap

SERVICE: Final = "guardrail-policy"


def main() -> int:
    """Resolve this deployable's configuration, report it, and return the process exit code."""
    return bootstrap(SERVICE)
