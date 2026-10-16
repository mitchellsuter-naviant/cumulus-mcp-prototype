"""Crm sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....connectors.commerce import crm_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands CRM companies and deals into the counterparty book."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="CrmBookDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "CrmBookDelivered"}, next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-resolution-policy",
             summary="gathers the governed auto-link and steward-review thresholds — the same policy the vendor master resolves under, so the two books cannot disagree about what counts as a match",
             config={"source": "knowledge:entity-resolution-policy"},
             next=["s1"]),
    FlowStep(id="s1", type="sync", name="land-records",
             summary="accounts and contacts land as they arrived, tagged `company` and `person` — the field a steward's decision is later routed on",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["z1"]),
    FlowStep(id="z1", type="resolve", name="resolve-records",
             summary="scores accounts against the counterparty book by name and contacts by address; asserts nothing",
             config={"entity": "Company,Person",
                     "source": "knowledge:entity-resolution-policy"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-resolutions",
             summary="guarded write of the companies, the people in them, the reversible identity links back to each landed row, and a `match_review` Task per ambiguous company",
             config={"target": "company-master", "gateway": "action-gateway",
                     "queue": "data-stewardship"},
             next=["a2"]),
    FlowStep(id="a2", type="action", name="apply-opportunities",
             summary="guarded write of the pipeline, each deal bound to the company it is being pursued with — a deal naming an account this delta did not carry is counted, never minted",
             config={"target": "sales-records", "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="CrmBookSynced",
             summary="emitted on commit", config={"event": "CrmBookSynced"}),
]


def build_body(service):
    return lambda ctx, inputs: crm_body(service, ctx, inputs)
