"""The compliance clock: what expires, who is told, and how long a row is kept.

Five sub-processes in the original design, three Excel trackers between them —
a training matrix, a DOT tracker, an inspection tracker — each with a
`Last Notified` column somebody hand-edited and a `Last Run Status` cell the
bot wrote back so the *next* run would know where it got to. This flow is all
five, once, as a windowed pass over rows the graph already holds.

**The trackers are not read and not written.** Not once, anywhere in this
package. There is no workbook, no `Last Notified` column and no
`Last Run Status` cell, because the state those cells carried is now
`Credential.last_notified_on` / `last_notice_stage` / `status` /
`retain_until` and `Inspection.last_notified_on` / `last_notice_stage` — graph
claims with a source, a timestamp and a run id on them — and the record of the
pass itself is the run journal. A cell that says "60-day notice sent" is a
number nobody can date, attribute or argue with; a claim is all three.

**Windowed, and nothing arrives.** Its Trigger is a *timer*:
`ComplianceWindowClosed`. No delivery woke it and no case is waiting on it —
the standing work list is the credential and inspection book, and the question
is the one the graph can already answer: *whose permission runs out soon enough
that somebody should be told?* So it lands one bulk observation for the whole
window rather than one write per row, reports what it could not do as counts,
and raises aggregate Tasks without waiting for any of them. `ap_intake` is the
shape; `disagreement_sweep` is the nearest sibling.

**The policy decides whether any work happens at all.** The window opens on a
clock, but `certification-alert-policy` carries the cadence, the stages, the
categories, the retention horizon and the desk — so the run's whole read-set is
one asset at one revision, and an edit to it changes what the flow does with no
deploy. A window whose policy finds nothing due writes nothing, sends nothing
and raises nothing, and the run says so.

**The one thing this must get right** is that a stage already sent this cycle
is not sent again. That single guard is what makes a *daily* cadence safe, and
daily is what resolves the original design's own contradiction — §5.1 says
monthly-on-the-1st, the architecture section says daily. A monthly run can miss
the 30-day stage outright: a card expiring on the 20th passes its 30-day mark
on the 21st of the month before, eleven days after the last run. Daily costs an
empty query a day and cannot skip a stage — but only because
`last_notice_stage` makes the other 364 windows no-ops.

**Counts, not gates.** A skid steer with no due date is not an overdue skid
steer; it is an asset nobody inspects, and the register says so on the row
(`inspection_tracked`). Those are excluded where the rows are gathered and
**counted**, never carried to the Rule to be rejected — a rising untracked
count is how a register that quietly stopped carrying due dates becomes
visible, which a silent absence of alerts is not.

**What it deliberately does not do**, because the customer scoped it out:
download a DOT document, update a date in a spreadsheet, retrieve an inspection
photo. It tracks the row and asks. Widening the automation past what was signed
off would be a decision taken by whoever wrote the code, which is the
arrangement the governed policy exists to replace. Sending the notices is also
not here: the desks and the workspace *are* the notification in this release.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date

from pydantic import BaseModel, Field

from ... import desks, routing
from ...auth import Principal, flow_principal
from ...dispatch import Dispatcher, InMemoryEventLog
from ...gateway import ActionGateway, InMemoryLedgerStore
from ...graph import Claim, GraphService, GraphWriter, InMemoryGraphStore
from ...models import utcnow
from ...ontology import names as onto
from ...routing import RoutingPolicy
from ...runs import RunContext, RunEngine
from ...task_queue import Task, TaskEvidence, TaskService

log = logging.getLogger("cumulus.credential_tracking")

FLOW_ID = "credential-tracking"
DEFAULT_VERSION = 1
POLICY_ASSET_ID = "certification-alert-policy"

#: The fact that wakes it: a window closed over the book. Nothing was
#: delivered — see the module docstring and the Trigger's own comment.
COMPLIANCE_WINDOW_EVENT = "ComplianceWindowClosed"

#: The fact it emits. Nothing binds it; it is the run's own record of the pass,
#: carrying the per-stage counts so the Flows page can chart a cadence.
ALERT_EVENT = "CredentialAlertsRecorded"

#: The one guarded mutation, declared in `gateway.ACTIONS`. One per window.
ALERT_ACTION = "record-compliance-alert"

#: Where the two types live, and the Action's target.
COMPLIANCE_RECORDS = "compliance-records"

#: Who commits it. No queue: noticing that a date is close is nobody's work —
#: the flow does it on its own authority, unasked — and a Flow principal owns
#: only the queues its declaration binds it to, so a queue here would refuse
#: the write outright. `record-disagreements` carries none for the same reason.
TRACKING_FLOW = flow_principal(FLOW_ID)

#: Where the aggregates land when the governed table cannot be read.
DEFAULT_DESK = desks.HR_SAFETY

# ---------------------------------------------------------------------------
# The built-in defaults, and why there are any
# ---------------------------------------------------------------------------
#
# A fresh deployment with no corpus still has to work, and an empty answer here
# is a flow that silently does nothing — the failure that looks exactly like
# success. Every value below is the governed document's own, restated so the
# run can fall back to it *and say that it did*.

#: Widest first, which is also the order the document writes them in.
DEFAULT_STAGES: tuple[int, ...] = (60, 30, 15)

#: The promise, not a stage: *notify supervisors at least 30 days before
#: expiry*. Kept separately because it is what a stage list can be checked
#: against — a list whose widest entry is under 30 breaks the promise, and the
#: run says so rather than quietly honouring the edit.
DEFAULT_SLA_FLOOR_DAYS = 30

DEFAULT_CATEGORIES: tuple[str, ...] = (
    "certification", "training", "licence", "medical", "clearance",
)

DEFAULT_RETENTION_YEARS = 7

DEFAULT_CADENCE = "daily"

#: How many rows one window may alert on when the asset does not say. The
#: bound exists for `ap_intake.DEFAULT_WINDOW_LIMIT`'s reason — a window is
#: answered inside the request that opened it — and the remainder is not
#: dropped: the most urgent rows are taken first and the rest is the next
#: window's work, stated on the run so a backlog is never a backlog nobody
#: knows about. Generous, because this window writes claims rather than
#: calling a model.
DEFAULT_WINDOW_LIMIT = 500

# ---------------------------------------------------------------------------
# The four exception classes
# ---------------------------------------------------------------------------

MISSING_DATE = "missing-or-unreadable-date"
UNRESOLVED_HOLDER = "unresolved-holder"
TERMINATED_HOLDER = "terminated-holder-still-active"
RETAINED_PAST_HORIZON = "retained-past-horizon"

#: In precedence order, and the order is a decision rather than a listing. One
#: row belongs to exactly one class, because four aggregates that each carry
#: the same row are four people asking about it. The order is what makes that
#: assignment defensible:
#:
#: - a date nobody can read is asked about first, because every judgment below
#:   it is a judgment about a date;
#: - then a holder nobody can resolve, because "whose is this" precedes "what
#:   should happen to them";
#: - then the retention horizon, because past it the question stops being
#:   "take them off the roster" and becomes "may we delete this";
#: - and a terminated holder inside the horizon last, which is the one of the
#:   four that is somebody's roster to fix today.
EXCEPTION_CLASSES: tuple[str, ...] = (
    MISSING_DATE, UNRESOLVED_HOLDER, RETAINED_PAST_HORIZON, TERMINATED_HOLDER,
)


class ExceptionClass(BaseModel):
    """The copy one aggregate Task is written from."""

    id: str
    #: Filled with the row count.
    title: str
    question: str
    priority: str = "normal"


EXCEPTION_COPY: dict[str, ExceptionClass] = {c.id: c for c in (
    ExceptionClass(
        id=MISSING_DATE,
        title="{n} compliance record(s) carry no date anyone can read",
        question=(
            "Each row below is tracked and has no expiry or next-due date, or "
            "one that cannot be read as a date. Nothing has been alerted on "
            "them and nothing has been guessed: a date that cannot be read is "
            "the first exception rather than an estimate, because the "
            "trackers this replaces were colour-coded and the original design "
            "was explicit that a bot must never rely on colour coding. What "
            "this needs is the real date, from the certificate or the "
            "register."
        ),
    ),
    ExceptionClass(
        id=UNRESOLVED_HOLDER,
        title=(
            "{n} compliance record(s) name a holder nobody can be "
            "resolved to"
        ),
        question=(
            "The issuing system named a holder the workforce does not "
            "contain, so there is no supervisor to notify and no roster to "
            "check this against. The record was landed and never attached — a "
            "training record is not evidence of employment, so no person was "
            "minted for it. What this needs is somebody who can say who these "
            "rows belong to."
        ),
    ),
    ExceptionClass(
        id=RETAINED_PAST_HORIZON,
        title="{n} retained record(s) are past the {years}-year horizon",
        priority="low",
        question=(
            "The customer's own rule, kept verbatim because it is the part "
            "that is easy to get wrong: *flag records older than 7 years for "
            "deletion — no automatic deletion, reported for manual review.* "
            "So this is a report and not a proposal. **Nothing has been "
            "deleted and nothing here can delete anything** — there is no "
            "Action in this system that deletes a compliance record, because "
            "a deletion is a decision and a decision needs somebody's name on "
            "it. Deciding one of these is records management, not a click."
        ),
    ),
    ExceptionClass(
        id=TERMINATED_HOLDER,
        title="{n} record(s) are still active for people who have left",
        priority="high",
        question=(
            "The holder's status in the HRIS is terminated and the record is "
            "still on the active roster. Two different answers are right for "
            "different rows and only a person can tell them apart: a "
            "permission that should be withdrawn, or a record that is simply "
            "being retained and should say so. Nothing has been withdrawn "
            "here."
        ),
    ),
)}

#: The review screen the console opens for one of these. Declared in the
#: version package's `UI`; the manifest degrades to the generic approve/reject
#: pane in a build that does not hold the widget yet.
EXCEPTION_SCREEN = "compliance/exception-review"


# ---------------------------------------------------------------------------
# x1 · the governed read
# ---------------------------------------------------------------------------


class AlertPolicy(BaseModel):
    """Everything `certification-alert-policy` decides, at one revision.

    One read for the whole run, so the journal carries a single ref and two
    steps of this flow cannot be looking at two revisions of the same
    document.
    """

    #: Widest first.
    stages: list[int] = Field(default_factory=lambda: list(DEFAULT_STAGES))
    sla_floor_days: int = DEFAULT_SLA_FLOOR_DAYS
    categories: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CATEGORIES)
    )
    #: The document's own sentence for why an asset is untracked. Carried as
    #: prose because it is what the run says out loud when it excludes one, and
    #: because the *rule* is already a property of the row
    #: (`Equipment.inspection_tracked`) rather than a list of machine types.
    untracked_when: str = "the source holds no next-due date"
    retention_years: int = DEFAULT_RETENTION_YEARS
    past_horizon: str = "reported for manual review"
    cadence: str = DEFAULT_CADENCE
    window_limit: int = DEFAULT_WINDOW_LIMIT
    routing: RoutingPolicy = Field(
        default_factory=lambda: RoutingPolicy(fallback=DEFAULT_DESK)
    )
    source_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    found_asset: bool = False

    @property
    def widest_stage(self) -> int:
        """How far ahead the window looks. Nothing is due beyond it."""
        return max(self.stages) if self.stages else 0

    @property
    def honors_sla_floor(self) -> bool:
        """Does the stage list keep the promise the floor states?

        Surfaced rather than enforced. An edit that narrows every stage below
        the floor is a service-level change somebody made in the open, and the
        honest response is a run that says the promise is broken — not a flow
        that substitutes a stage nobody wrote down.
        """
        return self.widest_stage >= self.sla_floor_days


def _section(content: str, heading: str) -> str:
    """The body under one `##` heading, up to the next one.

    A local copy of `routing._section`, deliberately, on the terms
    `connectors.compliance._site_slug` sets: the canonical one is private to a
    module this package should not reach into for one regex, and the two are
    pinned to agree by a test rather than left to drift.

    Everything below is section-scoped through this, and that is not tidiness.
    This document *discusses* its own values in prose — the cadence section
    argues about stages, the exceptions table mentions the retention horizon —
    so an unscoped search would read a sentence about a number as the number.
    """
    match = re.search(
        rf"^#{{1,4}}\s*{re.escape(heading)}\s*$(.*?)(?=^#{{1,4}}\s|\Z)",
        content or "", re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _value(section: str, key: str) -> str:
    """One `- key: value` line out of a section, or "".

    Line-oriented and forgiving in `parse_intake_policy`'s way: a customer
    edits prose in a console, and a policy that stopped parsing because
    somebody rewrapped a paragraph would take the flow down with it.
    """
    match = re.search(
        rf"^\s*[-*]\s*{re.escape(key)}\s*:\s*(.+?)\s*$",
        section, re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1).strip().strip("`*_ ") if match else ""


def parse_alert_policy(content: str) -> AlertPolicy:
    """Read the whole governed read-set out of the document.

    Every value degrades on its own: a section somebody deleted leaves that
    one default standing and the rest read from the asset, which is a more
    useful failure than an all-or-nothing parse.
    """
    values = AlertPolicy(found_asset=True)

    cadence = _value(_section(content, "run cadence"), "run cadence")
    if cadence:
        values.cadence = cadence

    stages_section = _section(content, "notice stages")
    stages = [
        int(n) for n in re.findall(
            r"\d+", _value(stages_section, "notice stages")
        )
    ]
    if stages:
        # Widest first and deduplicated, so the Rule can take the *narrowest*
        # stage a row has reached without re-sorting per row, and so a
        # document that wrote `60, 30, 30, 15` alerts three times and not four.
        values.stages = sorted(set(stages), reverse=True)
    floor = re.search(r"\d+", _value(stages_section, "notice SLA floor"))
    if floor:
        values.sla_floor_days = int(floor.group(0))

    tracked = _section(content, "what is tracked")
    categories = [
        word.strip().lower()
        for word in _value(tracked, "categories tracked").split(",")
        if word.strip()
    ]
    if categories:
        # Only when the document lists some. A truncated section must fall
        # back to the declared five rather than to *none*, which would read as
        # a clean sweep of a book nobody looked at.
        values.categories = categories
    untracked = _value(tracked, "untracked when")
    if untracked:
        values.untracked_when = untracked

    retention = _section(content, "retention")
    horizon = re.search(r"\d+", _value(retention, "retention horizon"))
    if horizon:
        values.retention_years = int(horizon.group(0))
    past = _value(retention, "past the horizon")
    if past:
        values.past_horizon = past

    bound = re.search(
        r"records per compliance window:?\s*(\d+)", content, re.IGNORECASE
    )
    if bound:
        # Zero is a real answer — "stop alerting" — and is honored.
        values.window_limit = int(bound.group(1))

    # The approval table, through the one parser that already reads approval
    # tables. Its own section scoping, its own resolver vocabulary, and its own
    # refusal to slugify prose into a desk nobody created.
    values.routing = routing.parse_routing(
        content, default_fallback=DEFAULT_DESK
    )
    return values


def read_alert_policy(knowledge) -> AlertPolicy:
    """The asset at its revision, or the built-in default, said out loud."""
    asset = knowledge.repo.get(POLICY_ASSET_ID) if knowledge else None
    if asset is None:
        log.warning(
            "Certification alert policy missing; using the built-in stages",
            extra={"assetId": POLICY_ASSET_ID},
        )
        return AlertPolicy(found_asset=False)
    values = parse_alert_policy(asset.content)
    values.source_ref = (
        f"knowledge:{asset.id}@rev{getattr(asset, 'revision', 1)}"
    )
    return values


# ---------------------------------------------------------------------------
# x2 · the rows, and what was excluded on the way
# ---------------------------------------------------------------------------


class TrackedRow(BaseModel):
    """One credential or one inspection, with everything a decision needs.

    This is the fetching half, so the Rule below never reads the graph: the
    holder, the supervisor who would be notified, the dates and the notice
    stamps are all resolved here and travel as values. It is also the step's
    output, which means it is what a person reading the journal sees before
    anything was written.
    """

    #: `credential` | `inspection`.
    kind: str
    object_type: str
    object_ref: str
    natural_key: str
    label: str

    #: The issuing system's word, and the governed category it fell under.
    credential_type: str = ""
    category: str = ""

    #: Whose permission this is — a person for a credential, a unit for an
    #: inspection.
    subject_label: str = ""
    holder_email: str = ""
    holder_status: str = ""
    holder_resolved: bool = False

    #: Who would be told. The holder's manager off the org chart for a
    #: credential; the foreman of the department that runs the unit for an
    #: inspection. Empty is a real answer and a count, never a failure.
    supervisor_email: str = ""
    supervisor_name: str = ""

    #: What the source wrote, and what could be made of it. Both, because the
    #: first exception class is about the difference.
    date_text: str = ""
    due_on: str = ""
    days_remaining: int | None = None

    last_notice_stage: int | None = None
    last_notified_on: str = ""
    status: str = ""
    retain_until: str = ""

    #: Inside the widest stage, so a stage may be due. A row outside it is
    #: still gathered — three of the four exception classes are not about a
    #: date at all — and simply never reaches the Rule.
    in_window: bool = False

    @property
    def date_readable(self) -> bool:
        return self.days_remaining is not None


def _read_date(value: object) -> date | None:
    """A date, or None — never a guess, and never an exception.

    None is counted by the caller and becomes the first exception class. The
    original design's own instruction is the reason: *never rely on colour
    coding* — the date is read directly, and one that cannot be read is a
    question for a person rather than an estimate.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _read_stage(value: object) -> int | None:
    """The last stage sent, off a string claim. None means none was."""
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _plus_years(anchor: date, years: int) -> date:
    """The horizon date. February 29 lands on the 28th rather than raising."""
    try:
        return anchor.replace(year=anchor.year + years)
    except ValueError:
        return anchor.replace(month=2, day=28, year=anchor.year + years)


def status_for(days_remaining: int | None, widest: int) -> str:
    """`Credential.status`, from the dates against the governed stages.

    The connector deliberately does not write this — it would be the feed
    deciding a policy question — so this is the one place it is derived, and
    `withdrawn` is never derived at all: that is somebody's decision.
    """
    if days_remaining is None:
        return ""
    if days_remaining < 0:
        return "expired"
    return "expiring" if days_remaining <= widest else "valid"


# ---------------------------------------------------------------------------
# r1 · the Rule
# ---------------------------------------------------------------------------


class StageDecision(BaseModel):
    """What one row was at, what it moves to, and why — per row, in the
    journal."""

    object_ref: str
    natural_key: str
    object_type: str
    kind: str
    label: str
    subject_label: str = ""
    supervisor_email: str = ""
    days_remaining: int | None = None
    from_stage: int | None = None
    to_stage: int | None = None
    status_from: str = ""
    status_to: str = ""
    retain_until: str = ""
    sent: bool = False
    #: Why nothing is being sent, when nothing is. Named rather than implied:
    #: "already sent" and "not yet due" are different facts about a row and a
    #: reader of the journal has to be able to tell them apart.
    reason: str = ""


def stage_due(
    days_remaining: int | None,
    last_stage: int | None,
    stages: list[int],
) -> tuple[int | None, str]:
    """Which stage is due for one row — pure, and the heart of the flow.

    Three rules, and the third is the one everything else rests on:

    1. **The narrowest stage reached wins.** A row 25 days out has passed both
       60 and 30; the notice it is owed is the 30-day one, not the 60-day one
       it should have had a month ago. Taking the widest would re-walk a
       backlog from the top every window, and a row already expired would
       start at 60 forever.
    2. **A row beyond the widest stage is not due.** Nothing to send, no write,
       no count of a problem.
    3. **A stage at or below the one already sent is not re-sent.** `<=` and
       not `<`, because the stages are narrowing: 60 was sent, the row is still
       55 days out, and the due stage is 60 again — that is the same
       notice, and
       re-sending it every day is what a daily cadence would do without this
       line. It is also what makes an empty window a genuine no-op.

    A recertification resets the cycle for free, and it is worth saying why
    rather than leaving it to be discovered: a renewal is a **new record** in
    the issuing system, so it lands as a new `Credential` under a new natural
    key with no stamps on it — and a re-examination is a new `Inspection` part
    keyed by its own date. Nothing has to be cleared. A row whose date is
    pushed out on the *same* record falls past the widest stage and is simply
    not due, its stamps untouched, until the date comes back around.
    """
    if days_remaining is None:
        return None, "no date this row could be measured from"
    reached = [stage for stage in stages if days_remaining <= stage]
    if not reached:
        return None, f"{days_remaining} day(s) out — beyond every stage"
    due = min(reached)
    if last_stage is not None and last_stage <= due:
        return None, f"stage {last_stage} already sent this cycle"
    return due, ""


def decide_stages(
    rows: list[TrackedRow], policy: AlertPolicy
) -> tuple[list[StageDecision], dict]:
    """The Rule over the window: one decision per row, no fetching.

    Pure in the doctrine's sense — it reads its arguments and nothing else,
    which is why `x2` above did the joining. Every row in gets a decision out,
    including the ones nothing happens to, because a row the journal does not
    mention is a row nobody can check.

    `unreadable` reads as zero from this flow, and that is a property rather
    than an oversight: `x2` never hands the Rule a row whose date it could not
    read — such a row is not in the window and goes straight to the exception
    screen. The counter stands because this function is public and pure, and a
    caller that does hand it one gets an answer and a number rather than a
    silent `None`.
    """
    decisions: list[StageDecision] = []
    counts = {
        "due": 0, "already_sent": 0, "not_yet_due": 0, "unreadable": 0,
        "by_stage": {str(stage): 0 for stage in policy.stages},
    }
    for row in rows:
        due, reason = stage_due(
            row.days_remaining, row.last_notice_stage, policy.stages
        )
        moved_to = status_for(row.days_remaining, policy.widest_stage)
        decision = StageDecision(
            object_ref=row.object_ref, natural_key=row.natural_key,
            object_type=row.object_type, kind=row.kind, label=row.label,
            subject_label=row.subject_label,
            supervisor_email=row.supervisor_email,
            days_remaining=row.days_remaining,
            from_stage=row.last_notice_stage, to_stage=due,
            status_from=row.status,
            status_to=moved_to if row.kind == "credential" else "",
            retain_until=row.retain_until,
            sent=due is not None, reason=reason,
        )
        decisions.append(decision)
        if due is not None:
            counts["due"] += 1
            key = str(due)
            counts["by_stage"][key] = counts["by_stage"].get(key, 0) + 1
        elif row.days_remaining is None:
            counts["unreadable"] += 1
        elif "already sent" in reason:
            counts["already_sent"] += 1
        else:
            counts["not_yet_due"] += 1
    return decisions, counts


# ---------------------------------------------------------------------------
# a1 · the one guarded write
# ---------------------------------------------------------------------------


def record_alerts(
    graph: GraphWriter,
    decisions: list[StageDecision],
    policy: AlertPolicy,
    today: date,
    run_id: str,
) -> dict:
    """Stamp the window's notices — one Action, however many rows.

    A windowed flow lands bulk observations, and four hundred guarded writes
    would be four hundred ledger entries saying the same thing about one pass.
    The regime is `decision` rather than `observation` and the distinction is
    load-bearing: **no source reported any of this.** Cumulus determined it,
    from a rule over a governed policy, and must answer for it —
    `record-disagreements`' reasoning exactly.

    Two types, two write-sets, because the ontology says so and not because
    one was forgotten. A `Credential` carries `status` and `retain_until`; an
    `Inspection` carries neither — it is a part of a unit, its result is what
    the examination found, and a regulator's horizon is not a fact about one
    examination. Claiming an undeclared field would be refused at the Gateway,
    which is the ontology doing its job rather than a caller remembering.
    """
    counts = {"alerted": 0, "credentials": 0, "inspections": 0,
              "status_moved": 0, "retention_claimed": 0}
    stamped_on = today.isoformat()
    source = f"flow:{FLOW_ID}"
    for decision in decisions:
        if not decision.sent or decision.to_stage is None:
            continue
        claims = [
            Claim(field="last_notified_on", value=stamped_on, source=source,
                  run_id=run_id, step_id="a1"),
            # A string, as the ontology declares it, and read back through
            # `_read_stage`. The stage *number* is the governed value; the
            # field is where the run says which one it sent.
            Claim(field="last_notice_stage", value=str(decision.to_stage),
                  source=source, run_id=run_id, step_id="a1"),
        ]
        if decision.kind == "credential":
            if decision.status_to:
                claims.append(Claim(
                    field="status", value=decision.status_to, source=source,
                    run_id=run_id, step_id="a1",
                ))
                counts["status_moved"] += 1
            if decision.retain_until:
                # Claimed on every credential this window alerts on, not only
                # a leaver's. The horizon is measured from the date the record
                # stops mattering to anybody's roster — its expiry — and that
                # is knowable now; waiting for a termination would mean the
                # answer arrives only for the rows somebody already left.
                claims.append(Claim(
                    field="retain_until", value=decision.retain_until,
                    source=source, run_id=run_id, step_id="a1",
                ))
                counts["retention_claimed"] += 1
            counts["credentials"] += 1
        else:
            counts["inspections"] += 1
        graph.upsert(
            decision.object_type, decision.natural_key, claims,
            label=decision.label or decision.natural_key,
            provenance=(
                f"{decision.to_stage}-day notice recorded by {run_id} "
                f"per {policy.source_ref}"
            ),
        )
        counts["alerted"] += 1
    return counts


# ---------------------------------------------------------------------------
# c1 · the screen
# ---------------------------------------------------------------------------


def screen_exceptions(
    rows: list[TrackedRow], policy: AlertPolicy, today: date
) -> dict:
    """The four classes, pure, one class per row.

    Read over every row the window gathered rather than only the ones inside a
    stage, because three of the four are not questions about a date: a holder
    nobody can resolve, a leaver still on the roster and a record past its
    horizon are all true of a credential that expires next year.

    A row can be alerted *and* be an exception, and that is not a
    contradiction — the notice is what the dates say and the exception is what
    the record says about itself. The journal shows both.
    """
    found: dict[str, list[dict]] = {name: [] for name in EXCEPTION_CLASSES}
    horizon = _plus_years(today, -policy.retention_years)
    for row in rows:
        due = _read_date(row.due_on)
        if not row.date_readable:
            name = MISSING_DATE
        elif not row.holder_resolved:
            name = UNRESOLVED_HOLDER
        elif due is not None and due < horizon:
            name = RETAINED_PAST_HORIZON
        elif _terminated(row.holder_status) and row.status != "withdrawn":
            name = TERMINATED_HOLDER
        else:
            continue
        found[name].append({
            "object_ref": row.object_ref,
            "natural_key": row.natural_key,
            "label": row.label,
            "kind": row.kind,
            "category": row.category,
            "subject": row.subject_label,
            "holder_email": row.holder_email,
            "holder_status": row.holder_status,
            "date": row.date_text or "—",
            "days_remaining": row.days_remaining,
        })
    return {
        "classes": found,
        "counts": {name: len(items) for name, items in found.items()},
        "total": sum(len(items) for items in found.values()),
        "horizon": horizon.isoformat(),
    }


def _terminated(status: str) -> bool:
    """The HRIS's own word for having left, however it was cased.

    Matched on the word rather than on a termination date, for the reason the
    ontology gives on `Employee.status`: the status is claimed and the date is
    not always there.
    """
    return "terminat" in (status or "").strip().lower()


def banded_days(rows: list[dict]) -> float:
    """The days-remaining an aggregate is routed on: its most urgent row.

    The governed table bands on days remaining, and a class of records has no
    single number — so the aggregate is routed by the row that is closest to
    running out, which is the one that decides how soon somebody has to look.
    A class with no readable date at all routes on 0, which is the fallback
    band and exactly where the document sends it.
    """
    numbers = [
        float(row["days_remaining"]) for row in rows
        if row.get("days_remaining") is not None
    ]
    return min(numbers) if numbers else 0.0


def desk_for(policy: AlertPolicy, rows: list[dict]) -> tuple[str, str]:
    """Which desk one aggregate lands on, and the sentence that says why.

    Read through the governed approval table like any elevated request. Two
    honest outcomes: a queue slug the table names, or the fallback — including
    every case where the table names a *resolver*. `submitter` and
    `manager-of(submitter)` cannot be answered here and the document says why
    in its own voice: these Tasks are aggregates about records rather than
    requests from people, so there is no submitter to route back to. Falling
    back is stated rather than swallowed, because a deployment quietly routing
    everything to the fallback is a broken table wearing a working flow.
    """
    target = policy.routing.target_for(banded_days(rows))
    fallback = policy.routing.fallback or DEFAULT_DESK
    if target in (routing.SUBMITTER, routing.MANAGER_OF):
        return fallback, (
            f"`{target}` cannot be answered by an aggregate about records — "
            f"there is no submitter to route back to — so the governed "
            f"fallback desk `{fallback}` stands"
        )
    if not target:
        return fallback, (
            f"the approval table named no desk, so the governed fallback "
            f"`{fallback}` stands"
        )
    return target, "the governed approval table's desk for these rows"


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class CredentialTrackingService:
    """Reads the book, decides the stages, records them, and asks.

    Holds no directory of its own, for `ApIntakeService`'s reason: what it
    produces is claims on rows that already exist and Tasks on a desk. A table
    here would be a second account of the same compliance state, free to
    disagree with the graph the moment somebody edited one of them.
    """

    def __init__(
        self,
        graph: GraphService,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        tasks: TaskService,
        engine: RunEngine | None = None,
        gateway: ActionGateway | None = None,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self.graph = graph
        self.knowledge = knowledge
        self.tasks = tasks
        self.engine = engine
        # Never None, for the reason given at the same line in `payables`: a
        # service wired without a Gateway still writes through one.
        self.gateway = gateway or ActionGateway(
            graph or GraphService(InMemoryGraphStore()), InMemoryLedgerStore()
        )
        # Never bypassed, same reason: a run nothing recorded a cause for is a
        # governance problem, not a wiring one.
        self.dispatcher = dispatcher or (
            Dispatcher(engine, InMemoryEventLog())
            if engine is not None else None
        )
        if engine is not None:
            from .. import shipped_versions

            for number, module in sorted(shipped_versions(FLOW_ID).items()):
                # No `modelcall.metered`: this flow makes no model call and
                # starts no nested run, so there is no frame below it whose
                # ledger rows would lose their run coordinates.
                engine.register(
                    FLOW_ID, module.build_body(self),
                    version=number, default=(number == DEFAULT_VERSION),
                )

    # -- governed reads ------------------------------------------------------

    def read_policy(self) -> AlertPolicy:
        return read_alert_policy(self.knowledge)

    # -- step internals ------------------------------------------------------

    def _act(self, ctx: RunContext, step_id: str):
        """The window's one guarded write, committed by the Flow itself."""
        return self.gateway.action(
            ALERT_ACTION, TRACKING_FLOW, run=ctx.record, step_id=step_id,
        )

    def gather(self, policy: AlertPolicy, today: date) -> dict:
        """The standing work list, asked of the graph.

        Two walks, and each is shaped by what it has to be able to count.

        Credentials are walked from the rows: every one the book holds, because
        a category the policy does not track is an exclusion this run has to
        state a number for.

        Inspections are walked from the **units**, not from the examinations,
        and that is the whole skid-steer exclusion. A unit whose
        `inspection_tracked` is not `yes` is excluded here and counted; its
        examinations are never carried to the Rule to be rejected. Walking the
        `Inspection` rows instead would have made an untracked unit invisible
        rather than counted — and a register that quietly stopped carrying due
        dates would look like a fleet with nothing coming due.
        """
        rows: list[TrackedRow] = []
        counts = {
            "credentials": 0, "inspections": 0, "equipment": 0,
            "untracked_assets": 0, "tracked_without_examination": 0,
            "category_untracked": 0,
            "holder_unresolved": 0, "supervisor_unresolved": 0,
        }
        notes: list[str] = []
        tracked = {name.lower() for name in policy.categories}

        for record in sorted(
            self.graph.find(onto.CREDENTIAL), key=lambda o: o.natural_key
        ):
            if record.status == "superseded":
                continue
            props = record.props
            category = str(props.get("category") or "").lower()
            if category not in tracked:
                # Carried in the graph and never alerted on — the document's
                # own words. Adding a category is a line there, not here.
                #
                # One honest limit, worth naming because the document's "one
                # line here" is only true up to it: a row whose issuing-system
                # word `connectors.compliance.CREDENTIAL_CATEGORIES` does not
                # hold lands with **no** `category` at all (the feed counts it
                # as `category_unmapped`), so widening the governed list cannot
                # reach it — the second line is the connector's table. Such a
                # row arrives here as an empty category and is counted, which
                # is what makes that gap a number somebody can see.
                counts["category_untracked"] += 1
                if len(notes) < 10:
                    notes.append(
                        f"{record.label}: category "
                        f"`{category or 'none'}` is not tracked"
                    )
                continue
            counts["credentials"] += 1
            holder = next(
                (
                    other for _, other in self.graph.neighbors(
                        record.id, "holds_credential", "in"
                    )
                ),
                None,
            )
            supervisor = self._manager(holder)
            if holder is None:
                counts["holder_unresolved"] += 1
            elif supervisor is None:
                counts["supervisor_unresolved"] += 1
            rows.append(self._row(
                record, "credential", policy, today,
                date_text=str(props.get("expires_on") or ""),
                credential_type=str(props.get("credential_type") or ""),
                category=category,
                subject=holder,
                holder_email=str(
                    (holder.props.get("email") if holder else "")
                    or props.get("holder_email") or ""
                ),
                holder_status=str(holder.props.get("status") or "")
                if holder else "",
                holder_resolved=holder is not None,
                supervisor=supervisor,
            ))

        for unit in sorted(
            self.graph.find(onto.EQUIPMENT), key=lambda o: o.natural_key
        ):
            if unit.status == "superseded":
                continue
            counts["equipment"] += 1
            if str(unit.props.get("inspection_tracked") or "") != "yes":
                counts["untracked_assets"] += 1
                if len(notes) < 10:
                    notes.append(
                        f"{unit.label}: no tracked inspection — "
                        f"{policy.untracked_when}"
                    )
                continue
            examinations = [
                other for _, other in self.graph.neighbors(
                    unit.id, "has_inspection", "out"
                )
            ]
            if not examinations:
                # Due, and never examined. The feed already counts this as
                # `due_without_history` on the way in and the honest fix is
                # the register's, not the sweep's: there is no examination row
                # here to carry a notice stamp.
                counts["tracked_without_examination"] += 1
                continue
            latest = max(
                examinations,
                key=lambda o: str(o.props.get("inspected_on") or ""),
            )
            counts["inspections"] += 1
            supervisor = self._foreman(unit)
            if supervisor is None:
                counts["supervisor_unresolved"] += 1
            rows.append(self._row(
                latest, "inspection", policy, today,
                date_text=str(latest.props.get("next_due_on") or ""),
                credential_type="annual inspection",
                category="inspection",
                subject=unit,
                holder_email="",
                holder_status="",
                holder_resolved=True,
                supervisor=supervisor,
            ))

        # Most urgent first, so a window that stops at its bound and the window
        # after it agree about where the boundary was, and so what waits is
        # what has the most time left. One bound for the whole pass rather than
        # one per step: everything downstream reads the rows this window took,
        # and the remainder is reported on the run because a count nobody
        # states is a backlog nobody knows about.
        ordered = sorted(rows, key=_urgency)
        limit = max(0, policy.window_limit)
        kept = ordered[:limit]
        candidates = [row for row in kept if row.in_window]
        counts["unreadable_dates"] = sum(
            1 for row in kept if not row.date_readable
        )
        return {
            "rows": [row.model_dump() for row in kept],
            "candidates": [row.model_dump() for row in candidates],
            "considered": len(rows),
            "taken": len(kept),
            "deferred": max(0, len(rows) - len(kept)),
            "in_window": len(candidates),
            "notes": notes,
            **counts,
        }

    def _row(
        self,
        record,
        kind: str,
        policy: AlertPolicy,
        today: date,
        *,
        date_text: str,
        credential_type: str,
        category: str,
        subject,
        holder_email: str,
        holder_status: str,
        holder_resolved: bool,
        supervisor,
    ) -> TrackedRow:
        """One row, with its arithmetic already done."""
        props = record.props
        due = _read_date(date_text)
        days = (due - today).days if due is not None else None
        retain_until = (
            _plus_years(due, policy.retention_years).isoformat()
            if due is not None and kind == "credential" else ""
        )
        return TrackedRow(
            kind=kind,
            object_type=record.type,
            object_ref=record.ref(),
            natural_key=record.natural_key,
            label=record.label or record.natural_key,
            credential_type=credential_type,
            category=category,
            subject_label=(
                (subject.label or subject.natural_key) if subject else ""
            ),
            holder_email=holder_email,
            holder_status=holder_status,
            holder_resolved=holder_resolved,
            supervisor_email=(
                str(supervisor.props.get("email") or "") if supervisor else ""
            ),
            supervisor_name=(
                str(supervisor.props.get("name") or supervisor.label)
                if supervisor else ""
            ),
            date_text=date_text,
            due_on=due.isoformat() if due is not None else "",
            days_remaining=days,
            last_notice_stage=_read_stage(props.get("last_notice_stage")),
            last_notified_on=str(props.get("last_notified_on") or ""),
            status=str(props.get("status") or ""),
            retain_until=retain_until,
            in_window=days is not None and days <= policy.widest_stage,
        )

    def _manager(self, holder):
        """The supervisor a credential's notice would go to.

        The `reports_to` edge the workforce feed already lands, walked here
        rather than through `routing.manager_of` because the holder object is
        already in hand — asking by address would cost a second lookup of a row
        this step is holding.
        """
        if holder is None:
            return None
        return next(
            (other for _, other in self.graph.neighbors(
                holder.id, "reports_to", "out"
            )),
            None,
        )

    def _foreman(self, unit):
        """The supervisor an inspection's notice would go to.

        `Equipment —operated_by→ Team —led_by→ Employee`: the department that
        runs the unit, and the person who leads it. This is the traversal the
        `operated_by` edge exists for — an overdue unit finding the person who
        has to hear about it, rather than a string match on a home-site column.
        """
        if unit is None:
            return None
        for _, team in self.graph.neighbors(unit.id, "operated_by", "out"):
            lead = next(
                (other for _, other in self.graph.neighbors(
                    team.id, "led_by", "out"
                )),
                None,
            )
            if lead is not None:
                return lead
        return None

    # -- the door -----------------------------------------------------------

    def sweep(self, actor: str = "system:scheduler", as_of: str = "") -> object:
        """Close a compliance window. The Trigger table is what answers.

        `PlantEligibilityService.sweep`'s shape, and through the Dispatcher for
        the same reason: the causation is logged before the run exists, which
        is the ordering the doctrine asks for and the one every windowed flow
        here already keeps. Calling the body directly would produce a run whose
        `caused_by` points at nothing.

        `as_of` is the window's own day rather than the moment the call lands.
        A window that closes at 23:59 and is answered at 00:01 must still be
        answered as of the window, or a credential expiring today is graded
        against tomorrow — which is the difference between a notice that goes
        out on time and one that goes out a day late, on the one flow whose
        whole purpose is being early.
        """
        if self.dispatcher is None:
            raise RuntimeError(
                "The credential tracking sweep has no dispatcher wired"
            )
        return self.dispatcher.emit(
            COMPLIANCE_WINDOW_EVENT,
            {"window": utcnow().isoformat(), "as_of": as_of},
            actor=actor,
        )

    # -- k1 -----------------------------------------------------------------

    def resume_for_task(self, task: Task, principal: Principal | None = None):
        """`principal` is the human who decided, carried to the commit.

        `DepartmentSchedulingService.resume_for_task`'s shape, and it answers
        `None` today for a reason worth reading rather than fixing: this flow
        does not gate. `k1` raises at most two aggregate Tasks and the run
        walks on to `e1`, so no run is ever suspended on one of them and
        `find_by_task` has nothing to find. The hook is here anyway because
        the console has to have somewhere to send a decision — the routing
        table in `tasks_api.RESUME_HOOKS` is the one place a reader can learn
        which flow answers for which Task, and a flow missing from it is a
        judgment that reaches nothing with no line anywhere saying so. A later
        version that does suspend on its own Task resumes through this without
        another edit.
        """
        if task.flow_id != FLOW_ID or self.engine is None:
            return None
        return self.engine.resume_for_task(task.id, principal=principal)

    def apply_task_decision(
        self, task: Task, principal: Principal | None = None,
    ):
        """The console's door, shaped like every other flow's."""
        return self.resume_for_task(task, principal)

    def raise_exception_tasks(
        self, ctx: RunContext, screened: dict, policy: AlertPolicy
    ) -> dict:
        """One aggregate Task per class that has rows — at most four, never N.

        The document's own arrangement, quoted because it is the point: *one
        task per class per window carrying its rows, never one task per row. A
        window that finds eleven unreadable dates asks one question eleven
        times over, which is one question.*

        The id is derived from the class and the **set of rows** rather than
        from the run, and that is a deliberate reading of "per window". A
        window is the unit of work, but the *question* is the rows: the same
        eleven dates found again tomorrow is the same question, and a daily
        cadence that opened a new copy of it every morning would bury the desk
        it is trying to help. A changed row set is a different question and
        gets its own Task. `TaskService.create` is a put, so the existence
        guard is what keeps an answered Task from being reset to `open` —
        `raise_desk_tasks`' property, for its reason.
        """
        raised: list[str] = []
        standing = 0
        for name in EXCEPTION_CLASSES:
            rows = screened["classes"].get(name) or []
            if not rows:
                # A class with nothing in it asks nobody anything. This is what
                # makes a clean book a silent window.
                continue
            copy = EXCEPTION_COPY[name]
            queue, why = desk_for(policy, rows)
            task_id = _aggregate_task_id(name, rows)
            if self.tasks.get(task_id) is not None:
                standing += 1
                continue
            self.tasks.create(Task(
                id=task_id,
                kind="exception",
                title=copy.title.format(
                    n=len(rows), years=policy.retention_years
                ),
                summary=(
                    f"{copy.question}\n\n"
                    f"{len(rows)} row(s), aggregated: one question per class "
                    f"per window and never one per row. Routed to `{queue}` — "
                    f"{why}. Read against {policy.source_ref}."
                ),
                queue=queue,
                priority=copy.priority,
                flow_id=FLOW_ID,
                flow_version=ctx.record.flow_version,
                run_id=ctx.record.id,
                case_ref=f"compliance-exception:{name}",
                refs=[policy.source_ref] + [
                    row["object_ref"] for row in rows[:20]
                ],
                evidence=[
                    TaskEvidence(label="Class", value=name),
                    TaskEvidence(label="Rows", value=str(len(rows))),
                ] + ([
                    TaskEvidence(
                        label="Retention",
                        value=(
                            f"{policy.retention_years} years — "
                            f"{policy.past_horizon}. Nothing is deleted."
                        ),
                    ),
                ] if name == RETAINED_PAST_HORIZON else []) + [
                    TaskEvidence(
                        label=row["label"],
                        value=" · ".join(filter(None, (
                            row["kind"],
                            row["subject"] or row["holder_email"],
                            f"date {row['date']}",
                            (
                                f"{row['days_remaining']} day(s)"
                                if row["days_remaining"] is not None else ""
                            ),
                            (
                                f"holder {row['holder_status']}"
                                if row["holder_status"] else ""
                            ),
                        ))),
                    )
                    for row in rows[:12]
                ],
                # No proposal, and for all four classes. There is no Action in
                # this system that deletes a compliance record, corrects a date
                # or withdraws a permission on a reviewer's behalf — the answer
                # to each of these is a fact somebody supplies, not a mutation
                # somebody authorizes.
                resumes=(
                    "nothing resumes; a date corrected on the review "
                    "screen is "
                    "a `human` claim on the row, which outranks the feed, and "
                    "the next window reads it"
                ),
                created_at_iso=utcnow().isoformat(),
            ))
            raised.append(task_id)
        return {
            "exception_tasks": len(raised),
            "exception_task_ids": raised,
            "exception_tasks_standing": standing,
        }


def _urgency(row: TrackedRow) -> tuple:
    """The order a window works in: soonest to run out, first.

    A row whose date could not be read sorts after every dated one — not
    because it matters less but because it is not a *notice*, and the half of
    this flow the bound protects is the notices. It is still gathered, still
    screened and still asked about, at the same bound.
    """
    return (
        0 if row.date_readable else 1,
        row.days_remaining if row.days_remaining is not None else 0,
        row.natural_key,
    )


def _aggregate_task_id(name: str, rows: list[dict]) -> str:
    """One id per (class, row set). Stable across windows, by construction."""
    digest = hashlib.sha256(
        "|".join(sorted(row["natural_key"] for row in rows)).encode()
    ).hexdigest()[:10]
    return f"task_compliance_{name}_{digest}"


def build_credential_tracking_service(
    graph: GraphService,
    knowledge,
    tasks: TaskService,
    engine: RunEngine | None = None,
    gateway: ActionGateway | None = None,
    dispatcher: Dispatcher | None = None,
) -> CredentialTrackingService:
    return CredentialTrackingService(
        graph, knowledge, tasks, engine, gateway, dispatcher,
    )
