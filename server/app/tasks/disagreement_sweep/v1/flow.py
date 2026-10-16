"""Disagreement sweep, v1 — the shape this flow has run since it landed.

The body lives beside the sweep it runs it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....disagreement_sweep import sweep_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Joins what the identity sweep confirmed and raises a controller Task per state disagreement between systems."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="StateWindowClosed",
             summary="one run per window over every held order — the sibling of IdentityWindowClosed, and windowed for the same reason: the unit of work is a pass, not a row",
             config={"event": "StateWindowClosed"}, next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-state-policy",
             summary="gathers the governed class table and its thresholds — which pairs of states are a disagreement, how far either side of a close date an order may sit, the name floor and the amount band a corroborator must clear",
             config={"source": "knowledge:state-agreement-policy"},
             next=["z1"]),
    FlowStep(id="z1", type="resolve", name="correlate-states",
             summary="every held order walked to its counterparty and out to that company's won deals, scored on the window, name proximity, amount containment, sole candidacy and a delivery engagement — read-only, deliberately outside the guarded write; a company reachable only through an unconfirmed merge is not read, and is counted",
             config={"signals": "window · name · amount · sole candidate · engagement",
                     "source": "knowledge:state-agreement-policy",
                     "gate": "company_same_as must read confirmed"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="record-disagreements",
             summary="guarded write of each finding as a typed link at `status=standing`, carrying its class, correlation confidence, signals, policy ref and the states both rows held when it was found — edges only, never a claim on the deal or the order",
             config={"target": "entity-layer", "gateway": "action-gateway",
                     "asserts": "nothing is released; no external system is written to"},
             next=["k1"]),
    FlowStep(id="k1", type="task", name="ask-the-order-desk",
             summary="a `state_disagreement` Task per standing finding, in the order desk's queue — releasing revenue is the controller's call and nobody else's; a finding already dismissed is never re-raised",
             config={"queue": "order-desk"}, next=["e1"]),
    FlowStep(id="e1", type="event", name="DisagreementsFound",
             summary="emitted on commit",
             config={"event": "DisagreementsFound"}),
]


def build_body(service):
    return lambda ctx, inputs: sweep_body(service, ctx, inputs)
