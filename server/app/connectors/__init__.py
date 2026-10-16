"""Connectors: the machinery that carries external systems' state in and out.

Per the doctrine, Sync is an execution pattern (an inbound Flow + an outbound
Flow + echo suppression + a reconciliation policy) and a Connector is a
standing integration handle — machinery, never a primitive. This package holds
that machinery: `sync` is the orchestrating service and the first-party feed
bodies; `commerce`, `office`, `dms` and `psa` are the per-system sidecars that
register their feed flows with it.
"""
