"""Vendor sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep

VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands an ERP vendor delivery, scores each row against the vendor master, and links, mints, or raises a steward Task per row."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="VendorRecordsDelivered",
             summary="one run per delivery, woken by a window rather than a case",
             config={"event": "VendorRecordsDelivered"}, next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-resolution-policy",
             summary="gathers the governed auto-link and steward-review thresholds — the resolver never fetches",
             config={"source": "knowledge:entity-resolution-policy"},
             next=["s1"]),
    FlowStep(id="s1", type="sync", name="land-records",
             summary="every row kept as delivered, so a change to the matching rules can re-resolve without re-fetching; byte-identical redeliveries are suppressed by payload hash",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["z1"]),
    FlowStep(id="z1", type="resolve", name="resolve-records",
             summary="scores each landed row against the vendor master and asserts nothing — linked, new, or a steward's to decide",
             config={"entity": "Vendor",
                     "source": "knowledge:entity-resolution-policy"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-resolutions",
             summary="guarded write of the identity decisions: claims onto known vendors, newly minted ones, and a `match_review` Task per ambiguous row — the run does not wait for it",
             config={"target": "vendor-master", "gateway": "action-gateway",
                     "queue": "vendor-stewardship"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="VendorsSynced",
             summary="emitted on commit", config={"event": "VendorsSynced"}),
]


def build_body(service):
    return service._vendor_body
