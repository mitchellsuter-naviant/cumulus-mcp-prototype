"""Sales sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....connectors.commerce import sales_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands sales orders and invoices from the commerce system."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="SalesRecordsDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "SalesRecordsDelivered"}, next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-resolution-policy",
             summary="gathers the governed thresholds the counterparty match runs under",
             config={"source": "knowledge:entity-resolution-policy"},
             next=["s1"]),
    FlowStep(id="s1", type="sync", name="land-records",
             summary="the ERP's customer rows as delivered, under their legal spellings",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["z1"]),
    FlowStep(id="z1", type="resolve", name="resolve-records",
             summary="scores each customer against the counterparty book — `Northwind Traders Inc.` against the `Northwind Traders` the CRM already minted",
             config={"entity": "Company",
                     "source": "knowledge:entity-resolution-policy"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-resolutions",
             summary="guarded write of the ERP's claims onto known companies and its identity links back to each landed row; an ambiguous one raises a Task and asserts nothing",
             config={"target": "company-master", "gateway": "action-gateway",
                     "queue": "data-stewardship"},
             next=["a2"]),
    FlowStep(id="a2", type="action", name="apply-items",
             summary="guarded write of the catalogue, keyed by SKU and carrying the ERP's internal id — the bridge an order line is resolved through",
             config={"target": "sales-records", "gateway": "action-gateway"},
             next=["a3"]),
    FlowStep(id="a3", type="action", name="apply-sales-orders",
             summary="guarded write of the orders, their lines, the customer edge and the catalogue edge; a line naming an item nobody has seen is counted rather than minting a SKU-less item the real one could never merge with",
             config={"target": "sales-records", "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="SalesRecordsSynced",
             summary="emitted on commit",
             config={"event": "SalesRecordsSynced"}),
]


def build_body(service):
    return lambda ctx, inputs: sales_body(service, ctx, inputs)
