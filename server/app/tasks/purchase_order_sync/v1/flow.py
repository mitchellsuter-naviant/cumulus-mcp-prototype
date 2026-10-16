"""Purchase order sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep

VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands ERP purchase orders with their lines, bound to the Vendor the order was cut against."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="PurchaseOrdersDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "PurchaseOrdersDelivered"}, next=["s1"]),
    FlowStep(id="s1", type="sync", name="land-records",
             summary="the orders as delivered, kept before anything is derived from them",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-purchase-orders",
             summary="guarded write of each order, its lines and the `ordered_from` edge — the three-way match reads these legs out of the graph rather than re-fetching them",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="PurchaseOrdersSynced",
             summary="emitted on commit",
             config={"event": "PurchaseOrdersSynced"}),
]


def build_body(service):
    return service._po_body
