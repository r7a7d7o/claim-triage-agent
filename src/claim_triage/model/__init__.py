"""The model behind a port: the call, the port, and the two adapters that answer one.

A stage imports `claim_triage.model.port` for the shape it asks in and
`claim_triage.model.select` for the model itself; the adapters are reached through neither of those,
which is what keeps a provider out of reach of anything but the port (see `docs/adr/0006`).
"""
