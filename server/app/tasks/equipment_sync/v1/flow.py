"""Equipment sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....connectors.compliance import equipment_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands the fleet and writes each unit's examination from the two dates the "
    "asset row holds. A null next-due date is the source saying the unit "
    "carries no tracked inspection, derived once on the way in."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="EquipmentRecordsDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "EquipmentRecordsDelivered"}, next=["s1"]),
    FlowStep(id="s1", type="sync", name="land-records",
             summary="every asset row kept as delivered, because it is the only place the pair of inspection dates exists — a change to the governed notice stages can then re-read the fleet without re-pulling it; byte-identical redeliveries are suppressed by payload hash",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-equipment",
             summary="guarded write of each unit with `inspection_tracked` derived from whether the source holds a next-due date at all, the `operated_by` edge where the home site names a team the org chart holds — the ERP's own site words (`Mazo`, `Fork West`) going through a named rename table onto the department names first, so a fleet screen shows a plant rather than a count, and a site nobody has paired yet still arriving as a number on the run — and one `Inspection` part per landed inspection date — none at all for an untracked unit, which is what stops a sweep reporting a skid steer as overdue forever",
             config={"target": "equipment-register",
                     "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="EquipmentSynced",
             summary="emitted on commit",
             config={"event": "EquipmentSynced"}),
]


def build_body(service):
    return lambda ctx, inputs: equipment_body(service, ctx, inputs)
