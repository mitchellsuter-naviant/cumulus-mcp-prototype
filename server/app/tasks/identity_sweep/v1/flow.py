"""Identity sweep, v1 — the shape this flow has run since it landed.

The body lives beside the sweep it runs it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....identity_sweep import sweep_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Reads the book the feeds wrote and proposes cross-source merges, one steward Task per doubtful edge."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="IdentityWindowClosed",
             summary="one run per window over the whole book — the first windowed trigger that is not a delivery",
             config={"event": "IdentityWindowClosed"}, next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-sweep-policy",
             summary="gathers the governed thresholds — the floor to propose at, the confidence under which a steward is asked, the name floor a pair must clear without a hard identifier",
             config={"source": "knowledge:entity-resolution-policy"},
             next=["z1"]),
    FlowStep(id="z1", type="resolve", name="score-candidates",
             summary="every pair in and across the two counterparty books and the person book, scored on shared domain, phone band, anchor word, shared employer and blended name similarity — read-only, deliberately outside the guarded write",
             config={"signals": "domain · phone · anchor · employer · name",
                     "source": "knowledge:entity-resolution-policy"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="propose-merges",
             summary="guarded write of each surviving pair as a typed identity link at `status=in_review`, with confidence, method, decidedBy, reversibleWith and the signals that produced it — edges only, never a claim on either object",
             config={"target": "entity-layer", "gateway": "action-gateway",
                     "asserts": "nothing merges; status-gated until confirmed"},
             next=["k1"]),
    FlowStep(id="k1", type="task", name="ask-a-steward",
             summary="a `merge_review` Task per proposal under the governed confidence bar, in the queue the Mapping names for that book; the run does not wait for it",
             config={"queue": "data-stewardship"}, next=["e1"]),
    FlowStep(id="e1", type="event", name="IdentityMergesProposed",
             summary="emitted on commit",
             config={"event": "IdentityMergesProposed"}),
]


def build_body(service):
    return lambda ctx, inputs: sweep_body(service, ctx, inputs)
