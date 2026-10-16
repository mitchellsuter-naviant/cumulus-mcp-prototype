"""Workforce sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep

VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands the HRIS workforce roster into the employee book."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="WorkforceDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "WorkforceDelivered"}, next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-teams",
             summary="guarded write of each department as a Team, keyed by slug and carrying the HRIS's own id",
             config={"target": "workforce", "gateway": "action-gateway"},
             next=["a2"]),
    FlowStep(id="a2", type="action", name="apply-employees",
             summary="guarded write of each worker, keyed by work email — with the HRIS's worker status and employment type claimed only where the source stated them",
             config={"target": "workforce", "gateway": "action-gateway"},
             next=["a3"]),
    FlowStep(id="a3", type="action", name="wire-org-chart",
             summary="guarded write of the membership, reporting, nesting and lead edges; a reference resolves by key first and by the source's own id second, and what neither answers is counted rather than minted",
             config={"target": "workforce", "gateway": "action-gateway"},
             next=["e1", "e2"],
             branch_labels=["synced", "references unresolved"]),
    FlowStep(id="e1", type="event", name="WorkforceSynced",
             summary="emitted on commit",
             config={"event": "WorkforceSynced"}),
    FlowStep(id="e2", type="event",
             name="WorkforceReferencesUnresolved",
             summary="emitted alongside e1 when the delivery named people or teams the graph does not hold — one aggregate per delivery, never a gate per row",
             config={"event": "WorkforceReferencesUnresolved"}),
]


def build_body(service):
    return service._workforce_body
