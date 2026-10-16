"""Job sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....connectors.production import job_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands the production system's jobs, resolves the customer each one names "
    "against the counterparty book, binds the crew the HRIS holds by its cost "
    "centre, turns the source's own `Yes`/`No`/`Week 3` words into the "
    "governed facts the plant declared them to be, and moves a reassigned job "
    "by withdrawing its old crew edge rather than deleting it."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="JobRecordsDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "JobRecordsDelivered"}, next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-resolution-policy",
             summary="gathers the governed auto-link and steward-review thresholds the customer match runs under — the same policy the CRM, the sell side and the PSA resolve against, so four systems cannot disagree about what counts as one company",
             config={"source": "knowledge:entity-resolution-policy"},
             next=["x2"]),
    FlowStep(id="x2", type="transform", name="read-source-vocabulary",
             summary="reads the `## Source vocabulary` table off the plant's own governed page: which of the production system's picklist words means released, which means held and what a week phrase is worth as a number. The estate connector delivers `Yes`, `No` and `Week 3` verbatim on purpose — translating in a puller would put a governed decision below the Mapping — and this is the step that is allowed to know what they mean. It names the revision it read, and where the page is absent it says out loud that the run translated with the words the flow shipped with rather than the plant's",
             config={"source": "knowledge:plant-scheduling-parameters",
                     "section": "Source vocabulary"},
             next=["s1"]),
    FlowStep(id="s1", type="sync", name="land-records",
             summary="every job row kept as delivered — including the `Yes`/`No` flags and the week phrase nothing here translates, so the governed vocabulary can be re-applied without re-pulling — plus one landing row per distinct customer name the join produced, which is the row a steward rules on",
             config={"target": "source-landing", "gateway": "action-gateway"},
             next=["z1"]),
    FlowStep(id="z1", type="resolve", name="resolve-records",
             summary="scores each customer name against the counterparty book and asserts nothing — linked, new, or a steward's to decide",
             config={"entity": "Company",
                     "source": "knowledge:entity-resolution-policy"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-customer-identities",
             summary="guarded write of the identity decisions: the job feed's name claim onto known counterparties, a new one minted where nothing came close, and a `match_review` Task per ambiguous customer — the run does not wait for it",
             config={"target": "company-master", "gateway": "action-gateway",
                     "queue": "data-stewardship"},
             next=["a2"]),
    FlowStep(id="a2", type="action", name="apply-jobs",
             summary="guarded write of each job as a root with the three edges the source lets us draw: `job_for` to the customer that resolved, `assigned_to` to the crew whose HRIS cost centre the id names — withdrawing the previous crew's edge rather than deleting it — and `job_folder` to every folder already filed under the job number. The same write claims `release_state`, `hold_reason` and `week_position` from the vocabulary `x2` read, so the eligibility rule reads declared fields instead of raw strings out of a landed payload. A crew id matching no team is counted, a source word the table does not hold is counted, and the job lands with its strings either way",
             config={"target": "production-records", "gateway": "action-gateway",
                     "source": "knowledge:plant-scheduling-parameters"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="JobsSynced",
             summary="emitted on commit", config={"event": "JobsSynced"}),
]


def build_body(service):
    return lambda ctx, inputs: job_body(service, ctx, inputs)
