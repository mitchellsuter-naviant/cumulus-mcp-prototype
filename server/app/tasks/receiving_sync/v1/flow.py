"""Receiving sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep

VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands receiving reports and books goods against their purchase orders."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger",
             name="ReceivingReportsDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "ReceivingReportsDelivered"},
             next=["s1"]),
    FlowStep(id="s1", type="sync", name="land-records",
             summary="the reports as delivered, kept before anything is derived from them",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["a1"]),
    FlowStep(id="a1", type="action",
             name="apply-receiving-reports",
             summary="guarded write of each report, its lines and the edge to the PO it books in against",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="ReceivingReportsSynced",
             summary="emitted on commit",
             config={"event": "ReceivingReportsSynced"}),
]


def build_body(service):
    return service._receiving_body
