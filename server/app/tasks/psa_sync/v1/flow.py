"""Psa sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....connectors.psa import psa_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands PSA projects, engagements and time entries into the counterparty book."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="PsaBookDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "PsaBookDelivered"}, next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-resolution-policy",
             summary="gathers the governed thresholds the counterparty match runs under — the same policy the CRM and the ERP resolve against, so three systems cannot disagree about what counts as one company",
             config={"source": "knowledge:entity-resolution-policy"},
             next=["s1"]),
    FlowStep(id="s1", type="sync", name="land-records",
             summary="the client list as delivered, under the nicknames Delivery uses; only the clients land, because a project code is exact and has nothing to adjudicate",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["z1"]),
    FlowStep(id="z1", type="resolve", name="resolve-records",
             summary="scores each client against the counterparty book — `Apex Mfg` against the `Apex Manufacturing` the CRM minted; a nickname usually falls below the floor and mints, which is what leaves the join for the identity sweep to propose with evidence",
             config={"entity": "Company",
                     "source": "knowledge:entity-resolution-policy"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-resolutions",
             summary="guarded write of the PSA's name claim onto known counterparties and its identity links back to each landed row, plus the contact's mail domain — the one hard identifier an informal client list carries; an ambiguous client raises a Task and asserts nothing",
             config={"target": "company-master", "gateway": "action-gateway",
                     "queue": "data-stewardship"},
             next=["a2"]),
    FlowStep(id="a2", type="action", name="apply-delivery-book",
             summary="guarded write of the projects, their phases, the counterparty edge and the hours — summed onto each phase through the task an entry names, with time booked past every phase counted rather than spread",
             config={"target": "delivery-records", "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="PsaBookSynced",
             summary="emitted on commit",
             config={"event": "PsaBookSynced"}),
]


def build_body(service):
    return lambda ctx, inputs: psa_body(service, ctx, inputs)
