"""Dms sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....connectors.dms import dms_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands DMS folders and document metadata; bytes stay referenced in place."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="DocumentsDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "DocumentsDelivered"}, next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-folders",
             summary="walks the parent pointers into full paths — through the batch first, then the tree already in the graph — and guards a write per folder plus the nesting edge; a folder neither answers for is counted, not filed at the root",
             config={"target": "document-library", "gateway": "action-gateway"},
             next=["a2"]),
    FlowStep(id="a2", type="action", name="apply-documents",
             summary="guarded write of each file as a reference: filename, media type, size, the Collection that governs it and the version-pinned `storage_ref` — then the edge to the folder the customer filed it in",
             config={"target": "document-library", "gateway": "action-gateway",
                     "custody": "reference"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="DocumentsSynced",
             summary="emitted on commit",
             config={"event": "DocumentsSynced"}),
]


def build_body(service):
    return lambda ctx, inputs: dms_body(service, ctx, inputs)
