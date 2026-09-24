"""The `extraction` deployable: OCR and vision work is CPU-heavy (see `docs/adr/0001`)."""

from __future__ import annotations

from typing import Final

from claim_triage.bootstrap import bootstrap

SERVICE: Final = "extraction"


def main() -> int:
    """Resolve this deployable's configuration, report it, and return the process exit code."""
    return bootstrap(SERVICE)
