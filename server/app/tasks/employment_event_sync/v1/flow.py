"""Employment event sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....connectors.dms import employment_event_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands employment events from the DMS drop into the employee book."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger",
             name="EmploymentEventsDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "EmploymentEventsDelivered"},
             next=["s1"]),
    FlowStep(id="s1", type="sync", name="land-records",
             summary="every ledger row kept exactly as delivered, previous values included; a byte-identical redelivery is suppressed by payload hash",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="EmploymentEventsLanded",
             summary="emitted on commit — `Landed` and not `Synced`, because nothing downstream was asserted",
             config={"event": "EmploymentEventsLanded"}),
]


def build_body(service):
    return lambda ctx, inputs: employment_event_body(service, ctx, inputs)
