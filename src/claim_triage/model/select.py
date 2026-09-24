"""Which model a configuration selects, built in one place.

Two adapters exist — `claim_triage.model.replay` and `claim_triage.model.provider` — and this module
is the only one that imports either. A stage asks for `model_from(settings)` and gets the port, so
"no caller can reach a model provider without going through the port" is a property of the imports,
checked as one (`tests/test_model_port.py`), rather than a convention somebody has to remember.

Replay is what configuration selects until it is told otherwise: the provider adapter is turned on
by naming an endpoint and a model, which is exactly what no test and no CI job does.
"""

from __future__ import annotations

from claim_triage.config import ModelProvider, ModelSettings
from claim_triage.model.port import ModelPort
from claim_triage.model.provider import ProviderModel
from claim_triage.model.replay import ReplayModel


def model_from(settings: ModelSettings) -> ModelPort:
    """The model this configuration selects, with everything it needs to answer a call."""
    if settings.provider is ModelProvider.PROVIDER:
        return ProviderModel(
            settings.base_url,
            name=settings.name,
            api_key=settings.api_key,
            timeout=settings.timeout_seconds,
        )
    return ReplayModel(settings.fixtures)
