"""Credential sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....connectors.compliance import credential_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands everything a person holds that expires — training, certifications, "
    "CDL, medical card, clearances — as one type, and hangs each on the "
    "employee whose work email the feed joined. Derives no status: the notice "
    "stages are the compliance sweep's."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="CredentialRecordsDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "CredentialRecordsDelivered"}, next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-credentials",
             summary="guarded write of each dated permission and the `holds_credential` edge to the employee the joined work email resolves to. A bind contract, so there is no landing step and no steward: the key is exact, an unmapped category is counted rather than rounded to a declared value, and a holder the workforce does not contain is counted rather than minted",
             config={"target": "compliance-records", "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="CredentialsSynced",
             summary="emitted on commit",
             config={"event": "CredentialsSynced"}),
]


def build_body(service):
    return lambda ctx, inputs: credential_body(service, ctx, inputs)
