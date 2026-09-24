"""The policies the simulated surrounding systems hold.

A policy lookup that answered nothing would leave the integration contract untestable and the
pipeline without the product family it needs, so the systems hold a small, committed set of
synthetic policies. They are fixtures, not data: no real policy, policyholder or vehicle is
represented, and the numbers are the ones `claim_triage.smoke` and the contract tests submit
against.
"""

from __future__ import annotations

from datetime import date
from typing import Final

from claim_triage.contract.models import Policy

MOTOR: Final = "Auto & pohoda"
TRAVEL: Final = "Cestovné poistenie"
"""The two product families the systems hold policies for. v0.2's retrieval corpus is their poistné
podmienky, and `product_family` is what a claim's clause lookup is filtered by."""

SEED_POLICIES: Final = (
    Policy(policy_number="SIM-2026-0001", product_family=MOTOR, valid_from=date(2026, 1, 1)),
    Policy(policy_number="SIM-2025-0442", product_family=MOTOR, valid_from=date(2025, 6, 1)),
    Policy(policy_number="SIM-2026-0107", product_family=TRAVEL, valid_from=date(2026, 2, 1)),
)
"""What a policy lookup answers with. Every other number is a documented `policy_not_found`."""
