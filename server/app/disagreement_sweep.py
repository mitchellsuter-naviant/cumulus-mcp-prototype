"""The cross-system disagreement detector: two books, one commitment, two answers.

`identity_sweep` asks whether two rows are the same party. This asks the next
question, and it only becomes askable once the first one has been answered:
*given that we know who the customer is, are the systems saying compatible
things about what was committed to?*

The CRM is the book of record for a pipeline and says a deal closed won on the
14th. The ERP is the book of record for an order and says the order raised
three days later is still sitting at Pending Approval. Neither system is wrong.
Neither system can see the other. The disagreement exists only in the join, and
the join exists only in Cumulus — which is the whole argument for a
counterparty book that both feeds reify onto:

- **A name join would not have worked.** `Yahara Gas & Electric`'s deals and
  `Yahara Gas and Electric Co.`'s orders do not share a spelling, and neither
  do Dane County's or Prairie Records'. The edges do:
  `Opportunity —pursued_with→ Company ←sold_to— SalesOrder` lands both rows on
  one object because resolution already did the work names cannot.
- **And where resolution has *not* finished, the sweep says so instead of
  guessing.** A company_same_as still at `in_review` does not carry the join.
  The evidence behind it is counted as `blocked_on_identity` and never used —
  status-gating in the doctrine's sense, and the number that says out loud what
  unfinished identity work is costing.

**It finds. It never resolves.** The finding is a typed Link between the deal
and the order carrying its class, its confidence, the signals that produced it
and the states both rows held when it was found. No object is touched, no
external system is written to, nothing is released: the sweep's whole output is
edges and a Task, which is a property you can check rather than a promise you
have to trust — every ref in its ledger entry starts `link:`.

**Why the finding is an edge and not an object.** A disagreement is not a new
thing in the world; it is a relationship between two things that already exist,
and it stops being true the moment either end moves. Reifying it would mint a
row that outlives the fact it describes and would need its own lifecycle to say
so. As a link it is withdrawn the way every other assertion is, and a reader
walking either row finds it without knowing to look for it.

**Why rules and not a model.** Every signal is a date comparison, a ratio, a
state lookup or a name similarity, and the blend is addition. A model would add
cost, non-determinism and an unarguable number to a decision whose entire value
is that a controller can read the evidence and disagree with it. Zero
ModelCalls, asserted in the tests.

**What is deliberately not here yet.** One class, `won-not-released`, because
one is what the estate can currently demonstrate — a rule with no live instance
is a rule nobody has seen fire. The class table is governed data
(`state-agreement-policy`), so the second class is an edit to a document; a
class between *different* types (a completed engagement against an unbilled
order) needs its own LinkType and is a second declaration, not a widened one.
And a won deal with no order at all is deliberately out: it is one-ended, and
an edge needs two ends. That one wants a reified finding type, and reifying it
is a decision worth making on its own rather than smuggling in here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel, Field

from .graph import GraphService, GraphWriter, LinkRecord, ObjectRecord, link_id
from .identity_sweep import Signal
from .models import utcnow
from .resolution import StatePolicy, name_similarity, read_state_policy
from .runs import RunContext
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.disagreement_sweep")

DISAGREEMENT_SWEEP_FLOW_ID = "disagreement-sweep"
#: The fact that starts it: a window closed over the book. The sibling of
#: `IdentityWindowClosed`, and windowed for the same reason — the unit of work
#: is a pass over two books, not a row.
STATE_WINDOW_EVENT = "StateWindowClosed"

#: Where a finding is asked. Not read off a Mapping, and that is a considered
#: difference from `identity_sweep`: a Mapping's `steward_queue` answers "whose
#: book is this row escalating out of", and a disagreement escalates out of no
#: book — it is a fact about two feeds that each landed correctly. Asking
#: `steward_queue_for_type("SalesOrder")` would return `data-stewardship`,
#: which is the desk that decides who the customer *is*, not the desk that
#: decides whether to bill them.
ORDER_DESK_QUEUE = "order-desk"

#: The identity link the counterparty join walks, and the status it must hold.
#: A proposal a steward has not confirmed does not carry a join — see the
#: module docstring, and `_reachable` below.
IDENTITY_LINK = "company_same_as"
CONFIRMED = "confirmed"

#: The lifecycle of a finding, declared in one place because three stages read
#: it. `standing` is the only one this module writes; the order desk's decision
#: moves it to `resolved` or `dismissed` (the release flow, S2b-2), and a later
#: pass moves a finding whose rows have changed underneath it to `superseded`.
#: Declared now rather than when the first mover lands, so that the set a
#: reader has to reason about is complete from the start and `raise_desk_tasks`
#: below can say precisely which member it refuses to re-raise.
FINDING_STATUSES = ("standing", "resolved", "dismissed", "superseded")
STANDING = "standing"


@dataclass(frozen=True)
class DisagreementScan:
    """The code half of one disagreement class: which books, which edge.

    The *states* are governed data and live in `state-agreement-policy`; this
    is the machinery that goes with them — which two types are walked, which
    edges reach the counterparty, and which declaration carries the answer. A
    table rather than a code path, so a class between two other types is a row
    here and a LinkType there rather than a second copy of the pass.
    """

    link_type: str
    from_type: str
    to_type: str
    #: The edge from each end to the shared counterparty.
    from_edge: str
    to_edge: str
    #: Where each end keeps the date the window is measured between.
    from_date_field: str
    to_date_field: str
    #: And the money, for the containment band.
    from_amount_field: str
    to_amount_field: str


#: One row today, and the shape is what makes the second row cheap.
SCAN = DisagreementScan(
    link_type="deal_disagrees_with_order",
    from_type="Opportunity", to_type="SalesOrder",
    from_edge="pursued_with", to_edge="sold_to",
    from_date_field="close_date", to_date_field="ordered_date",
    from_amount_field="amount_usd", to_amount_field="total_usd",
)

#: What each piece of evidence is worth. The governed prose table lives in
#: `state-agreement-policy`; this is the code half, exactly as
#: `identity_sweep.WEIGHTS` is.
#:
#: The asymmetry with the identity sweep's weights is the load-bearing part.
#: There, every signal is optional and the blend decides whether a pair is
#: *considered at all*. Here the three conditions are required — a pair that
#: fails one is not a finding at any score — so `states-agree` is a flat base
#: that says "this is a disagreement" and everything above it answers the only
#: question left: which two rows is it between.
WEIGHTS: dict[str, float] = {
    "required-conditions": 0.50,
    "name-proximity": 0.25,
    "amount-containment": 0.12,
    "sole-candidate": 0.10,
    "delivery-engagement": 0.08,
}

#: The corroborators, by name. At least one of these fires or the pair is
#: `ambiguous` rather than raised — see `state-agreement-policy`. A delivery
#: engagement is deliberately *not* on this list: it is a third system's
#: opinion and welcome evidence, but a pair whose only support is that somebody
#: opened a project is a pair the two commercial systems say nothing about.
CORROBORATORS = ("name-proximity", "amount-containment", "sole-candidate")

#: Everything before the first em/en dash in a deal name is the account prefix
#: the CRM puts on every one of its deals. It comes off before the memo
#: comparison, because leaving it on scores every deal at a company alike — the
#: prefix is the half the two rows are guaranteed to share and the half that
#: therefore carries no information about *which* deal this order is.
_ACCOUNT_PREFIX = re.compile(r"^[^—–-]{1,40}\s*[—–]\s*")


class Finding(BaseModel):
    """One disagreement, scored, with the evidence that scored it."""

    link_type: str
    finding_class: str
    from_id: str
    to_id: str
    from_label: str
    to_label: str
    counterparty: str
    from_state: str
    to_state: str
    confidence: float
    signals: list[Signal] = Field(default_factory=list)

    @property
    def reason(self) -> str:
        return " · ".join(s.detail for s in self.signals) or "no evidence"


# ---------------------------------------------------------------------------
# Reading evidence off the two books
# ---------------------------------------------------------------------------


@dataclass
class Side:
    """One row of either book, reduced to the facts a finding turns on.

    Built once per object per pass, for `identity_sweep.Party`'s reason: the
    scan walks every held order against every won deal at its counterparty, and
    re-reading an opportunity's edges inside that loop is how a pass that
    should take a second takes a minute.
    """

    record: ObjectRecord
    #: The counterparty this row reaches, or "" when the feed landed the row
    #: with nothing to hang it on.
    company_id: str
    state: str
    on: date | None
    amount: float

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def label(self) -> str:
        return self.record.label or self.record.natural_key


@dataclass
class Counterparty:
    """One company, and what an unconfirmed merge is hiding behind it."""

    id: str
    label: str
    #: This company plus every company joined to it by a **confirmed** merge.
    #: The set a deal must land in for the counterparty condition to hold.
    admissible: frozenset[str] = frozenset()
    #: Companies joined to it by a merge nobody has confirmed, which hold
    #: records this pass is not allowed to read. Labels, for the counter's
    #: sake — the number is only useful beside the name it is about.
    withheld: tuple[str, ...] = ()


def _as_date(value: object) -> date | None:
    """An ISO day out of a prop, or None. Timestamps are truncated to the day.

    None rather than a raised error, and counted by the caller: a feed that
    landed an order with no date is a data-quality fact for the Sources screen,
    not a reason for the sweep to stop.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _amount(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_side(
    graph: GraphService, record: ObjectRecord, edge: str,
    state_field: str, date_field: str, amount_field: str,
) -> Side:
    """Everything the scorer needs about one row, read once."""
    company_id = next(
        (other.id for _link, other in graph.neighbors(record.id, edge, "out")),
        "",
    )
    return Side(
        record=record,
        company_id=company_id,
        state=str(record.props.get(state_field) or ""),
        on=_as_date(record.props.get(date_field)),
        amount=_amount(record.props.get(amount_field)),
    )


def _reachable(graph: GraphService, company_id: str) -> Counterparty:
    """One counterparty, split into what may be read and what may not.

    The whole status gate, in one function. A `company_same_as` at `confirmed`
    is a steward's answer and widens the join; one at `in_review` is a machine's
    proposal and does not, however confident it is — the Gateway refuses to act
    on an unconfirmed proposal and so does this.

    One hop and not a transitive closure, deliberately. Chaining unreviewed
    merges is how a book collapses: A~B and B~C confirmed says nothing about
    A~C, and a sweep that walked the chain would be asserting a merge nobody
    proposed in order to raise a finding nobody could check.
    """
    record = graph.get(company_id)
    if record is None:
        return Counterparty(id=company_id, label=company_id)
    admissible = {company_id}
    withheld: list[str] = []
    for link, other in graph.neighbors(company_id, IDENTITY_LINK, "both"):
        if str(link.props.get("status") or "") == CONFIRMED:
            admissible.add(other.id)
        else:
            withheld.append(other.id)
    return Counterparty(
        id=company_id,
        label=record.label or record.natural_key,
        admissible=frozenset(admissible),
        withheld=tuple(dict.fromkeys(withheld)),
    )


def _holds_evidence(
    graph: GraphService, company_id: str, stages: frozenset[str]
) -> bool:
    """True when a company nobody may read holds something this pass wanted.

    The `blocked_on_identity` count is only honest if it counts *withheld
    evidence* rather than withheld companies. An unconfirmed merge to a shell
    with no deals and no engagements costs the sweep nothing and must not be
    reported as if it did — the number exists to say what finishing the
    identity work would buy, and inflating it with empty rows would make it
    say nothing.
    """
    for _link, other in graph.neighbors(company_id, SCAN.from_edge, "in"):
        if str(other.props.get("stage") or "") in stages:
            return True
    return bool(graph.neighbors(company_id, "delivered_for", "in"))


# ---------------------------------------------------------------------------
# Scoring one pair
# ---------------------------------------------------------------------------


def _in_window(deal: Side, order: Side, policy: StatePolicy) -> bool:
    if deal.on is None or order.on is None:
        return False
    return (
        deal.on - timedelta(days=policy.lead_days)
        <= order.on
        <= deal.on + timedelta(days=policy.lag_days)
    )


def deal_subject(name: str) -> str:
    """A deal's name with the account prefix stripped, or the name itself."""
    return _ACCOUNT_PREFIX.sub("", name or "").strip() or (name or "")


def score_pair(
    deal: Side, order: Side, counterparty: Counterparty,
    policy: StatePolicy, finding_class: str, *, sole: bool,
    engagement: ObjectRecord | None = None,
) -> tuple[float, list[Signal]]:
    """Confidence and evidence for one candidate pair.

    Called only for a pair that has already cleared all three required
    conditions, which is why the base weight is unconditional: everything here
    is answering "which two rows", not "is this a disagreement".
    """
    signals = [
        Signal(
            name="required-conditions",
            weight=WEIGHTS["required-conditions"],
            detail=(
                f"one counterparty ({counterparty.label}), "
                f"'{deal.state}' against '{order.state}', and an order "
                f"{abs((order.on - deal.on).days) if deal.on and order.on else 0}"
                f" day(s) from the close"
            ),
        ),
    ]

    similarity = name_similarity(
        deal_subject(str(deal.record.props.get("name") or deal.label)),
        str(order.record.props.get("memo") or ""),
    )
    if similarity >= policy.name_floor:
        signals.append(Signal(
            name="name-proximity",
            weight=round(WEIGHTS["name-proximity"] * similarity, 4),
            detail=(
                f"the deal's subject and the order's memo agree at "
                f"{similarity:.2f} — "
                f"'{deal_subject(str(deal.record.props.get('name') or ''))}' ~ "
                f"'{order.record.props.get('memo') or ''}'"
            ),
        ))

    # One-sided on purpose: an order is routinely a phase of a deal, and one
    # larger than the deal that produced it is a different commitment rather
    # than a strong match. Voss's phase-1 order is 27% of its deal and this is
    # the signal that says so is *normal*.
    if deal.amount > 0:
        ratio = order.amount / deal.amount
        if policy.amount_floor <= ratio <= policy.amount_ceiling:
            signals.append(Signal(
                name="amount-containment",
                weight=WEIGHTS["amount-containment"],
                detail=(
                    f"the order is {ratio:.0%} of the deal "
                    f"(${order.amount:,.0f} of ${deal.amount:,.0f}), inside "
                    f"the {policy.amount_floor:.0%}–{policy.amount_ceiling:.0%} band"
                ),
            ))

    if sole:
        signals.append(Signal(
            name="sole-candidate",
            weight=WEIGHTS["sole-candidate"],
            detail=(
                "the only won deal in the window for this counterparty — "
                "there is nothing to confuse it with"
            ),
        ))

    if engagement is not None:
        props = engagement.props
        signals.append(Signal(
            name="delivery-engagement",
            weight=WEIGHTS["delivery-engagement"],
            detail=(
                f"a third system agrees: {props.get('project_code') or ''} "
                f"'{props.get('name') or engagement.label}' is "
                f"{props.get('status') or 'open'} in the delivery book, "
                f"started {props.get('start_date') or 'in the window'}"
            ),
        ))

    score = min(policy.ceiling, max(0.0, sum(s.weight for s in signals)))
    return round(score, 4), signals


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


@dataclass
class _Counts:
    """The pass's own arithmetic, kept out of the loop's readability.

    Every one of these is a number somebody asks for: the console's Judgment
    panel reads `standing` and `blocked_on_identity`, the Sources screen reads
    `undated`, and `ambiguous` is the honest answer to "why did you not raise
    this one" that a silent sweep cannot give.
    """

    orders_examined: int = 0
    candidates: int = 0
    outside_window: int = 0
    ambiguous: int = 0
    found: int = 0
    blocked_on_identity: int = 0
    order_without_deal: int = 0
    order_without_counterparty: int = 0
    undated: int = 0

    def as_dict(self) -> dict:
        return dict(vars(self))


def _engagement_in_window(
    graph: GraphService, counterparty: Counterparty,
    deal: Side, policy: StatePolicy,
) -> ObjectRecord | None:
    """A delivery engagement that started inside the deal's window, if any.

    Only over the **admissible** companies, which is what makes the demo beat
    real: the Voss engagement hangs off the PSA's `Voss Auto`, and until a
    steward confirms that merge this returns None and the finding is raised
    without it. Confirm the merge and a third system's evidence appears on the
    Task — the same pass, the same policy, one more admissible company.
    """
    if deal.on is None:
        return None
    opens = deal.on - timedelta(days=policy.lead_days)
    closes = deal.on + timedelta(days=policy.lag_days)
    for company_id in sorted(counterparty.admissible):
        for _link, project in graph.neighbors(company_id, "delivered_for", "in"):
            started = _as_date(project.props.get("start_date"))
            if started is not None and opens <= started <= closes:
                return project
    return None


def scan(
    graph: GraphService, policy: StatePolicy
) -> tuple[list[Finding], dict]:
    """Walk the held orders against the won deals. Reads only — nothing written.

    Orders first, and there are two reasons rather than one. The cheap reason
    is selectivity: two of thirty-eight orders are held where twenty-seven of
    sixty-one deals are won, so starting from the held order is the smaller
    outer loop. The real reason is that the *question* starts there — a
    disagreement is noticed because something is stuck, and an order nobody
    released is the stuck thing.

    Separated from the writing so the pass is testable without a run, a Gateway
    or a Task queue, and so the flow body reads as what it is: read the policy,
    scan, record, ask.
    """
    counts = _Counts()
    stages = frozenset(stage for stage, _s, _n in policy.disagreements)
    held = frozenset(policy.held_statuses)
    findings: list[Finding] = []

    deals = [
        build_side(graph, record, SCAN.from_edge, "stage",
                   SCAN.from_date_field, SCAN.from_amount_field)
        for record in graph.find(SCAN.from_type)
        if record.status != "superseded"
        and str(record.props.get("stage") or "") in stages
    ]
    deals_by_company: dict[str, list[Side]] = {}
    for deal in deals:
        if deal.company_id:
            deals_by_company.setdefault(deal.company_id, []).append(deal)

    for record in graph.find(SCAN.to_type):
        if record.status == "superseded":
            continue
        order = build_side(graph, record, SCAN.to_edge, "status",
                           SCAN.to_date_field, SCAN.to_amount_field)
        if order.state not in held:
            continue
        counts.orders_examined += 1
        if not order.company_id:
            # The feed landed an order it could not place. Real, and not this
            # module's problem: an unplaceable row is the resolver's escalation
            # and it already has a desk.
            counts.order_without_counterparty += 1
            continue
        if order.on is None:
            counts.undated += 1
            continue

        counterparty = _reachable(graph, order.company_id)
        for hidden in counterparty.withheld:
            if _holds_evidence(graph, hidden, stages):
                counts.blocked_on_identity += 1

        reachable = [
            deal
            for company_id in sorted(counterparty.admissible)
            for deal in deals_by_company.get(company_id, [])
        ]
        if not reachable:
            counts.order_without_deal += 1
            continue

        in_window = [d for d in reachable if _in_window(d, order, policy)]
        counts.outside_window += len(reachable) - len(in_window)
        if not in_window:
            # Won deals exist, none of them in the window. Not a finding and
            # not a `order_without_deal` either: the two are different facts,
            # and a screen that conflated them would report an estate with
            # perfect deal coverage as one with none.
            continue

        for deal in in_window:
            counts.candidates += 1
            finding_class = policy.classify(deal.state, order.state)
            if not finding_class:
                continue
            engagement = _engagement_in_window(graph, counterparty, deal, policy)
            confidence, signals = score_pair(
                deal, order, counterparty, policy, finding_class,
                sole=len(in_window) == 1, engagement=engagement,
            )
            if not any(s.name in CORROBORATORS for s in signals):
                # Every required condition met and nothing to say which deal
                # this is. Counted rather than raised: an order desk handed a
                # question the machine cannot narrow has been handed the
                # machine's job.
                counts.ambiguous += 1
                continue
            counts.found += 1
            findings.append(Finding(
                link_type=SCAN.link_type,
                finding_class=finding_class,
                from_id=deal.id, to_id=order.id,
                from_label=deal.label, to_label=order.label,
                counterparty=counterparty.label,
                from_state=deal.state, to_state=order.state,
                confidence=confidence, signals=signals,
            ))

    findings.sort(key=lambda f: f.confidence, reverse=True)
    return findings, counts.as_dict()


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def write_findings(
    service: GraphService,
    graph: GraphWriter,
    findings: list[Finding],
    policy: StatePolicy,
    *,
    flow_id: str,
    run_id: str,
) -> dict:
    """Each finding as a typed link at `standing`. Neither end is touched.

    Link ids derive from type and endpoints, so a second pass over an unchanged
    estate addresses the same edges: the sweep is idempotent by construction.
    An edge that is already there is left exactly as it is rather than
    restamped — `dismissed` is a judgment that the two systems are allowed to
    disagree here, and a scheduled pass that re-opened it would be a machine
    overruling a person once a night, quietly, forever.
    """
    counts = {"recorded": 0, "already_recorded": 0}
    for finding in findings:
        existing = service.store.get_link(link_id(
            finding.link_type, finding.from_id, finding.to_id
        ))
        if existing is not None:
            counts["already_recorded"] += 1
            continue
        graph.link(
            finding.link_type, finding.from_id, finding.to_id,
            props={
                "class": finding.finding_class,
                "status": STANDING,
                "correlation_confidence": finding.confidence,
                "signals": [s.model_dump() for s in finding.signals],
                "detected_at": utcnow().isoformat(),
                "policy_ref": policy.source_ref,
                "run_id": run_id,
                "decided_by": f"flow:{flow_id}",
                # What both rows said at the moment of the finding. Carried
                # because the finding stops being true the instant either end
                # moves, and a desk reading it a week later has to be able to
                # tell "this is still the case" from "this was the case" —
                # without which the release Action would be approved against a
                # state nobody re-read.
                "state_when_found": {
                    "opportunity_stage": finding.from_state,
                    "order_status": finding.to_state,
                },
            },
            confidence=finding.confidence,
            method="derived",
            provenance=finding.reason,
        )
        counts["recorded"] += 1
    return counts


def standing_findings(service: GraphService) -> list[LinkRecord]:
    """Every live finding the estate currently holds at `standing`."""
    return [
        link for link in service.store.links()
        if link.type == SCAN.link_type and link.live
        and str(link.props.get("status") or "") == STANDING
    ]


def raise_desk_tasks(
    tasks: TaskService,
    service: GraphService,
    findings: list[Finding],
    policy: StatePolicy,
    *,
    flow_id: str,
    run_id: str,
) -> dict:
    """A Task per standing finding, in the order desk's queue.

    Task ids derive from the edge and `TaskService.create` is a put, so raising
    one that already exists would reset a Task somebody had answered back to
    `open` and drop their verdict — `identity_sweep.raise_merge_tasks`'s
    property, for its reason, and it matters more here: the decision this Task
    authorizes writes to somebody else's ERP.

    Two guards rather than one, because they fail differently. The Task id
    guard leaves a decided Task alone. The link-status guard is what makes
    "`dismissed` is never re-raised" structural rather than incidental: it
    holds even for a finding whose Task has been purged, and it is the property
    the reject button's copy promises out loud.
    """
    raised = 0
    standing = 0
    dismissed = 0
    for finding in findings:
        edge = link_id(finding.link_type, finding.from_id, finding.to_id)
        link = service.store.get_link(edge)
        if link is not None and (
            not link.live or str(link.props.get("status") or "") != STANDING
        ):
            dismissed += 1
            continue
        task_id = f"task_disagreement_{edge[-12:]}"
        if tasks.get(task_id) is not None:
            standing += 1
            continue
        tasks.create(Task(
            id=task_id,
            kind="state_disagreement",
            title=(
                f"{finding.counterparty}: the CRM says won, the ERP has not "
                f"released {finding.to_label}"
            ),
            summary=(
                f"'{finding.from_label}' is {finding.from_state} in the CRM, "
                f"and {finding.to_label} is still {finding.to_state} in the "
                f"ERP {policy.lag_days} days of allowance later. Cumulus "
                f"correlated the two at {finding.confidence:.2f} on the "
                "evidence below; it has changed nothing and written to no "
                "system. Approve to release the order in the ERP, or leave it "
                "as it is — a dismissal stands until the two systems "
                "themselves change."
            ),
            queue=ORDER_DESK_QUEUE,
            priority="normal",
            flow_id=flow_id,
            run_id=run_id,
            # The finding is the case, exactly as a merge proposal is: the
            # decision is about an assertion that already exists in the graph
            # and needs its status moved, not about a row that landed.
            case_ref=f"link:{finding.link_type}/{edge}",
            refs=[
                policy.source_ref,
                f"graph:{finding.from_id}", f"graph:{finding.to_id}",
            ],
            evidence=[
                TaskEvidence(label="Correlation",
                             value=f"{finding.confidence:.2f} "
                                   f"({finding.finding_class})"),
                TaskEvidence(label="The CRM says",
                             value=f"{finding.from_label} — "
                                   f"{finding.from_state}"),
                TaskEvidence(label="The ERP says",
                             value=f"{finding.to_label} — {finding.to_state}"),
            ] + [
                TaskEvidence(label=s.name, value=f"{s.detail} ({s.weight:+.2f})")
                for s in finding.signals
            ],
            proposal=TaskProposal(
                action="release-sales-order",
                target="sales-records",
                summary=(
                    f"Release {finding.to_label} in the ERP, moving it out of "
                    f"{finding.to_state}"
                ),
            ),
            resumes=(
                "the finding's status moves on decision; the release itself "
                "is a separate, approved write"
            ),
            created_at_iso=utcnow().isoformat(),
        ))
        raised += 1
    return {
        "desk_tasks": raised,
        "desk_tasks_standing": standing,
        "desk_tasks_withheld": dismissed,
    }


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------


def register(service) -> None:
    """Bind the body to the engine `SyncService` was handed."""
    if service.engine is None:
        return
    service.engine.register(
        DISAGREEMENT_SWEEP_FLOW_ID,
        lambda ctx, inputs: sweep_body(service, ctx, inputs),
    )


def sweep_body(service, ctx: RunContext, inputs: dict) -> dict:
    """Read the policy, walk the two books, record, ask.

    Five journal rows and one write: only `a1` touches the graph, and what it
    writes is edges. A reader checking the claim this module opens with — that
    the sweep cannot release anything — has one step to read.
    """
    window = inputs.get("window", "the whole book")

    ctx.event(
        "t1", STATE_WINDOW_EVENT,
        f"state window closed over {window}", kind="trigger",
    )

    raw = ctx.step(
        "x1", "read-state-policy", "transform",
        fn=lambda: read_state_policy(service.knowledge).model_dump(),
        summary=lambda out: (
            f"{out['source_ref']}: {len(out['disagreements'])} class(es) · "
            f"window −{out['lead_days']}/+{out['lag_days']} days · names "
            f"corroborate ≥ {out['name_floor']:.2f} · an order "
            f"{out['amount_floor']:.0%}–{out['amount_ceiling']:.0%} of its deal"
        ),
    )
    policy = StatePolicy.model_validate(raw)
    ctx.note_refs(policy.source_ref)

    def walk() -> dict:
        found, counts = scan(service.graph, policy)
        return {"findings": [f.model_dump() for f in found], "counts": counts}

    scanned = ctx.step(
        "z1", "correlate-states", "resolve",
        # Outside the Action for `identity_sweep.score-candidates`'s reason:
        # the walk reads two whole books and writes nothing, and wrapping it in
        # a guarded write would put a thousand comparisons on one ledger entry
        # that recorded no change.
        fn=walk,
        summary=lambda out: (
            f"{out['counts']['orders_examined']} held order(s) · "
            f"{out['counts']['candidates']} pair(s) in window · "
            f"{out['counts']['found']} disagreement(s) · "
            f"{out['counts']['blocked_on_identity']} blocked on an "
            f"unconfirmed merge · "
            f"{out['counts']['order_without_deal']} order(s) with no won deal"
        ),
        refs=[policy.source_ref],
    )
    findings = [Finding.model_validate(f) for f in scanned["findings"]]

    act = service._act(ctx, "record-disagreements", "a1")
    written = ctx.step(
        "a1", "record-disagreements", "action",
        fn=lambda: act.commit(lambda w: write_findings(
            service.graph, w, findings, policy,
            flow_id=DISAGREEMENT_SWEEP_FLOW_ID, run_id=ctx.record.id,
        )),
        writes=lambda _out: act.writes,
        summary=lambda out: (
            f"{out['recorded']} finding(s) written at `standing` · "
            f"{out['already_recorded']} already on the graph · nothing "
            "released, no object touched"
        ),
    )

    asked = ctx.step(
        "k1", "ask-the-order-desk", "sync",
        fn=lambda: raise_desk_tasks(
            service.tasks, service.graph, findings, policy,
            flow_id=DISAGREEMENT_SWEEP_FLOW_ID, run_id=ctx.record.id,
        ),
        summary=lambda out: (
            f"{out['desk_tasks']} Task(s) raised to {ORDER_DESK_QUEUE} · "
            f"{out['desk_tasks_standing']} already open or answered · "
            f"{out['desk_tasks_withheld']} dismissed and not re-raised"
            if out["desk_tasks"] or out["desk_tasks_standing"]
            or out["desk_tasks_withheld"]
            else "nothing to report, so nobody to ask"
        ),
    )

    ctx.event(
        "e1", "DisagreementsFound",
        f"{written['recorded']} disagreement(s) recorded, "
        f"{asked['desk_tasks']} sent to the order desk",
    )
    return {
        "window": window, "kind": "disagreement-sweep",
        **scanned["counts"], **written, **asked,
        # Live findings after the pass, which is a different number from
        # `recorded` on every run but the first, and the one the Judgment panel
        # actually shows.
        "standing": len(standing_findings(service.graph)),
        # What the sweep thought, on the run, for `identity_sweep`'s reason: a
        # windowed flow's output is the only place its findings are legible
        # without re-querying the graph.
        "top": [
            {
                "class": f.finding_class, "confidence": f.confidence,
                "counterparty": f.counterparty,
                "deal": f.from_label, "order": f.to_label, "why": f.reason,
            }
            for f in findings[:10]
        ],
    }
