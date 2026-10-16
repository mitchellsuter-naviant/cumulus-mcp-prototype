"""Event, Trigger, Dispatcher: the one place a response is allowed to begin.

`gateway.py` is the chokepoint for *committing* work; this is the chokepoint
for *starting* it, and it exists for the same reason. Before it, a run began by
direct imperative call — `engine.start(FLOW_ID, inputs)` — and the flow body's
first act was to journal a `trigger` step saying why it had started. That row
is written by the run, inside the run, after the run exists: the causation was
recorded by the very thing it was supposed to cause. The doctrine's words are
"a Dispatcher evaluates Triggers — whether a response should start — before any
response exists (a thing cannot decide whether it itself should begin)", and
the order was exactly inverted.

Three things, and nothing else. An **Event** is the immutable fact, appended to
a log that can be read back rather than to one run's private journal. A
**Trigger** is a declarative binding, "when Event X occurs, start response Y",
declared in the closed table below the way `gateway.ACTIONS` is closed. The
**Dispatcher** evaluates the Triggers bound to a fact and starts what they
name — appending the fact, with the Triggers it matched and the run ids it is
about to mint, *before* the first of those runs exists.

**What this deliberately is not.** No broker, no subscriber registry, no async
fan-out, no retry topology: in-process, synchronous, one log. A Trigger binds
one event name to one flow id and carries no condition — the moment it wants
one, the honest form is a Rule the started Flow evaluates in-flight, which is
where the doctrine puts rule evaluation anyway. The response-kind vocabulary is
closed at Flow / Action / Resolution and every Trigger here starts a Flow;
there is deliberately no response-kind column, because a field with one
possible value would claim this enacts three kinds when it enacts one.

**Two holes, named rather than papered over.** `RunEngine.start` is still
public, so this is a discipline rather than a chokepoint enforced the way the
Gateway enforces `Refused`; a caller that skips it produces a run with an empty
`caused_by`, which is at least visible. And an Event emitted *by a running
Flow* (`RunContext.event`) is appended but never dispatched: a response
deciding that another response should begin is fan-out, and fan-out needs a
queue rather than a call. Nothing binds a flow-emitted event today; the first
Trigger that wants to is when to build that, and not before.
"""

from __future__ import annotations

import logging
import secrets
import threading
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .auth import Principal
from .models import utcnow
from .runs import RunEngine, RunRecord

log = logging.getLogger("cumulus.dispatch")

#: Longer values are recorded as their size rather than copied. See `_facts`.
MAX_VALUE_CHARS = 200


class Undispatched(Exception):
    """A fact emitted to get work done, with nothing bound to it. Raised
    rather than returned for `gateway.Refused`'s reason: a caller that forgot
    to look must not read "nothing happened" as success."""

    def __init__(self, name: str) -> None:
        super().__init__(f"no declared Trigger binds {name}")
        self.name = name


class Event(BaseModel):
    """An immutable record that something happened. Written once, never edited.

    `payload` is the fact, not a second copy of the artifact: a submitted
    invoice carries the document's bytes, which the run needs and the log does
    not. `triggers` and `starts` are what the fact caused, filled in *before*
    any of it exists — that ordering is the point of the module.
    """

    id: str
    name: str
    at_iso: str
    actor: str = ""
    case_ref: str = ""
    payload: dict = Field(default_factory=dict)
    # Where the fact came from. Empty at an entry point (a person submitted
    # something); the emitting run when a Flow reported its own state change.
    run_id: str = ""
    flow_id: str = ""
    # What it caused. Empty is a real answer, not a missing one: most facts
    # bind nothing, and the log is where that is visible.
    triggers: list[str] = Field(default_factory=list)
    starts: list[str] = Field(default_factory=list)


class Trigger(BaseModel):
    """"When Event X occurs, start response Y" — engine-managed causation.

    `wakes` is the per-case/windowed distinction, and it lives here because
    this is where the doctrine puts it: a Flow's shape "is read off its
    Trigger, never declared as a type". A feed delivering two hundred vendor
    rows wakes on a window and lands bulk observations; an invoice arriving
    wakes one case and commits per case. Neither Flow is typed — the same
    engine runs both — and `flows.is_windowed` reads this field.
    """

    id: str
    event: str
    starts: str
    wakes: Literal["case", "window"] = "case"


#: The closed set of bindings. Every event name here is emitted by an entry
#: point — `payables.submit`, `reimbursements.submit`, `sync.deliver` — because
#: a Trigger bound to a name nothing emits is a binding that never fires.
TRIGGERS: dict[str, Trigger] = {t.id: t for t in (
    # per case: one business fact, one run, decisions committed per case
    Trigger(id="invoice-received", event="InvoiceReceived",
            starts="accounts-payable"),
    Trigger(id="expense-submitted", event="ExpenseSubmitted",
            starts="expense-reimbursement"),
    # windowed: a source delivered a batch — bulk observations, and a Task on
    # the aggregate rather than a gate per row
    Trigger(id="vendors-delivered", event="VendorRecordsDelivered",
            starts="vendor-sync", wakes="window"),
    Trigger(id="purchase-orders-delivered", event="PurchaseOrdersDelivered",
            starts="purchase-order-sync", wakes="window"),
    Trigger(id="receiving-delivered", event="ReceivingReportsDelivered",
            starts="receiving-sync", wakes="window"),
    Trigger(id="workforce-delivered", event="WorkforceDelivered",
            starts="workforce-sync", wakes="window"),
    Trigger(id="calendars-delivered", event="CalendarsDelivered",
            starts="calendar-sync", wakes="window"),
    Trigger(id="crm-book-delivered", event="CrmBookDelivered",
            starts="crm-sync", wakes="window"),
    Trigger(id="sales-records-delivered", event="SalesRecordsDelivered",
            starts="sales-sync", wakes="window"),
    Trigger(id="mail-delivered", event="MailDelivered",
            starts="mail-sync", wakes="window"),
    Trigger(id="employment-events-delivered",
            event="EmploymentEventsDelivered",
            starts="employment-event-sync", wakes="window"),
    Trigger(id="documents-delivered", event="DocumentsDelivered",
            starts="dms-sync", wakes="window"),
    # The first fact with two responses, and the order is load-bearing:
    # `ap-intake` reads the filing tree out of the graph, so the mirror that
    # lands it has to have run. Triggers on one event fire in declaration
    # order (see `_BY_EVENT` below), which makes that a property of this table
    # rather than of luck — and it is why the pair is declared adjacently.
    Trigger(id="documents-delivered-ap", event="DocumentsDelivered",
            starts="ap-intake", wakes="window"),
    # And the third response to the same fact, declared here for the same
    # reason the second one is: `field-documents` reads the filing tree and
    # the files under it out of the graph, so the mirror that lands them has
    # to have run, and Triggers on one event fire in declaration order.
    # `DocumentsDelivered` now has **three** — `dms-sync`, `ap-intake`,
    # `field-documents`, in that order — and `keyed_run_ids` already derives a
    # distinct run id per Trigger, so an idempotent redelivery re-enters all
    # three rather than starting copies of any of them.
    #
    # A revision arriving for a job that is already scheduled is one of the
    # two facts that change this flow's answer; the other is on
    # `JobRecordsDelivered` below, and the pair is why one Flow has two
    # Triggers rather than two Flows.
    Trigger(id="documents-delivered-field", event="DocumentsDelivered",
            starts="field-documents", wakes="window"),
    Trigger(id="psa-book-delivered", event="PsaBookDelivered",
            starts="psa-sync", wakes="window"),
    # The plant floor's three deliveries. **A Trigger and its Flow's `v1`
    # package land together, in one commit** — not tidiness, a hard rule that
    # this table's shape makes load-bearing: `emit` starts every matched
    # Trigger in a list comprehension below and does *not* guard
    # `RunEngine.start`, which raises `ValueError` for a flow id nothing
    # registered. So a row landed ahead of its body would not fail quietly in
    # one run; it would make the next estate delivery 500 and take
    # `POST /api/v1/sources/deliver` down with it, for every feed.
    #
    # The *second* Trigger on `JobRecordsDelivered` is the row directly below
    # this one, and it is declared there rather than anywhere else because the
    # field-documents flow reads the `Job` rows this one lands: declaration
    # order is the dependency order. The moment this fact acquired a second
    # response, `keyed_run_ids` stopped handing the first one the verbatim
    # idempotency key and started deriving a suffix per Trigger — harmless in
    # a fresh deployment with no run history, and the reason the pair landed
    # in one commit rather than a week apart.
    Trigger(id="jobs-delivered", event="JobRecordsDelivered",
            starts="job-sync", wakes="window"),
    # A job becoming `Scheduled` is the other half of what wakes
    # `field-documents` — the first half being a revision arriving for a job
    # already scheduled, on `DocumentsDelivered` above. Two Triggers on one
    # Flow, because either half can be the new fact and both change the same
    # answer: *which scheduled job inside the horizon does not have its four
    # documents linked at their latest revision?*
    Trigger(id="jobs-delivered-field-documents", event="JobRecordsDelivered",
            starts="field-documents", wakes="window"),
    Trigger(id="credentials-delivered", event="CredentialRecordsDelivered",
            starts="credential-sync", wakes="window"),
    Trigger(id="equipment-delivered", event="EquipmentRecordsDelivered",
            starts="equipment-sync", wakes="window"),
    # The first windowed Trigger that is not a delivery. Nothing arrived; a
    # window closed over the book we already hold, and the response reads the
    # whole counterparty book against itself. It is windowed for the same
    # reason the feeds are — the unit of work is a pass, not a row — and it is
    # here rather than called directly so the sweep's causation is logged
    # before the run exists, like everything else.
    Trigger(id="identity-window-closed", event="IdentityWindowClosed",
            starts="identity-sweep", wakes="window"),
    # The second, and the sibling of the first. Same shape and the same
    # reason — a window closed over the book, nothing arrived — asking the
    # other question a pass over the whole estate can answer: not "are these
    # two rows one party" but "are these two systems, having agreed on the
    # party, saying compatible things about what was committed to".
    Trigger(id="state-window-closed", event="StateWindowClosed",
            starts="disagreement-sweep", wakes="window"),
    # The third of the windowed-but-not-a-delivery kind, and the first one a
    # *clock* raises rather than a sweep of the whole estate. Nothing arrived:
    # a window closed over the credential and inspection book Cumulus already
    # holds, and the standing question is which permissions run out soon enough
    # that somebody should be told. `POST /api/v1/compliance/sweep` is the door
    # that emits it, on the governed cadence
    # (`certification-alert-policy` carries `- run cadence: daily`), which is
    # also why the flow is safe to run every morning: `last_notice_stage`
    # makes a window with nothing due write nothing and raise nothing.
    #
    # This row and `credential-tracking`'s `v1` package land in one commit, and
    # the rule is restated here rather than left to the comment above the plant
    # feeds because the next reader needs to know why: `Dispatcher.emit` below
    # starts every matched Trigger in a list comprehension and does **not**
    # guard `RunEngine.start`, which raises `ValueError` for a flow id nothing
    # registered. A row landed ahead of its body would not fail quietly in one
    # run.
    #
    # What it does *not* carry is the delivery-door hazard the co-landing rule
    # exists for. `documents-delivered-field` and its siblings sit on an event
    # the estate pushes, so an orphan row there would 500 the next
    # `POST /api/v1/sources/deliver` for every feed. This event has one caller
    # and it is a timer: an orphan here would break the compliance sweep alone.
    # The rule still holds — the reason it holds is just narrower.
    Trigger(id="compliance-window-closed", event="ComplianceWindowClosed",
            starts="credential-tracking", wakes="window"),
    # The plant spine, and the seam in the middle of it. The window is the
    # fourth windowed-but-not-a-delivery Trigger and the second a *clock*
    # raises: 11:00, nothing arrived, and the standing question is which jobs
    # the production system holds that clear every governed eligibility clause.
    # `JobEligible` is what that pass emits, one per job, and it is a **case** —
    # one job, one run, one multi-page extraction, one commitment — which is
    # exactly where a windowed batch stops being a batch.
    #
    # **Both rows land with their flows' `v1` packages, in this lane, and the
    # reason is the one the plant feeds' comment gives**: `emit` below starts
    # every matched Trigger in a list comprehension and does not guard
    # `RunEngine.start`, which raises `ValueError` for a flow id nothing
    # registered. `plant-window-closed` has one caller and it is a timer, so an
    # orphan there would break the sweep alone; `job-eligible` is emitted from
    # **inside** that sweep, so an orphan there would fail the window that
    # raised it — and the window is what the whole spine hangs off.
    Trigger(id="plant-window-closed", event="PlantWindowClosed",
            starts="plant-eligibility-sweep", wakes="window"),
    Trigger(id="job-eligible", event="JobEligible",
            starts="shipping-list-extraction"),
    # And the third row of the plant spine, which is what makes it a spine
    # rather than three flows. The eligibility sweep emits one of these per
    # (department, business day) its extractions produced demand rows for —
    # **after** every extraction has run, so the day is filled out of a graph
    # that already holds the demand — and this is the response.
    #
    # `window` and not a case, and the shape is read off exactly this row.
    # Capacity, headcount and the incentive are properties of the *day*; "sort
    # by load date and add until capacity" is a batch decision; and the
    # approval the original design makes permanent is per department-day. The
    # ten UiPath queues it replaces were an artifact of a tool, not a fact
    # about a plant. A windowed Flow may still suspend for a person, which is
    # what this one does at its approval gate.
    #
    # **This row lands with `department_scheduling/v1`, in the same commit**,
    # for the reason the two rows above carry: `emit` starts every matched
    # Trigger in a list comprehension and does not guard `RunEngine.start`,
    # which raises for a flow id nothing registered. `DepartmentDayOpened` is
    # emitted from **inside** the eligibility sweep, so an orphan here would
    # fail the window that raised it — and the window is what the whole spine
    # hangs off.
    Trigger(id="department-day-opened", event="DepartmentDayOpened",
            starts="department-scheduling", wakes="window"),
    # Two facts, one response. A question asked of the corpus is a case — one
    # question, one run, one answer — whichever door it arrived at, and the
    # `answer-question` Flow is where the last ungoverned ModelCall in the
    # system finally has a journal. The HR agent gets its own event name
    # rather than reusing `QuestionAsked` because the fact is genuinely
    # different (it carries an employee's own balance and benefits into the
    # context) and the log should be able to say which desk was asked; that it
    # starts the same Flow is the Trigger's answer, not the caller's.
    Trigger(id="question-asked", event="QuestionAsked",
            starts="answer-question"),
    Trigger(id="hr-question-asked", event="HrQuestionAsked",
            starts="answer-question"),
    # And the answer to what that sweep found — per *case*, unlike the pass
    # that produced it, because a controller approves one order at a time and
    # the thing that happens next reaches into their ERP. The sweep does not
    # suspend waiting for this: it raises Tasks and finishes, and a decision
    # days later starts an independent run whose case is the finding. That
    # separation is why the release can re-read the world for itself rather
    # than acting on a state somebody looked at last Tuesday.
    Trigger(id="order-release-approved", event="OrderReleaseApproved",
            starts="order-release"),
    # The flywheel, and the reason it is a Trigger rather than a function call.
    # Doctrine: a human correction flows back **decoupled, via an Event, not
    # inline**, so the business path stays lean. The accounts-payable run emits
    # this when a clerk's correction is accepted and carries on posting the
    # invoice; capturing what the correction taught is an independent response
    # whose failure cannot hold up a payment, and whose runs are visible in the
    # fleet rather than buried in somebody else's step.
    Trigger(id="extraction-corrected", event="ExtractionCorrected",
            starts="ap-corrections"),
)}

#: The trigger index. The doctrine keeps it in a fast key-value store for O(1)
#: dispatch; at this size a dict built once off the closed table is that store.
_BY_EVENT: dict[str, list[Trigger]] = {}
for _trigger in TRIGGERS.values():
    _BY_EVENT.setdefault(_trigger.event, []).append(_trigger)


def triggers_for_flow(flow_id: str) -> list[Trigger]:
    """Every declared Trigger that starts this flow."""
    return [t for t in TRIGGERS.values() if t.starts == flow_id]


def keyed_run_ids(event: str, key_run_id: str) -> list[str]:
    """The run ids a keyed emission of this event will use, in Trigger order.

    A caller holding an idempotency key needs these *before* it emits, so it
    can recognize a delivery it has already run. One Trigger keeps the key
    verbatim — that is the id every feed's replay guard was written against and
    the ids already in every deployment's run store. A fact with several
    responses cannot: two runs may not share an id, so each response derives
    one from the same key, which keeps a retry re-entering both rather than
    starting a second copy of each.
    """
    matched = _BY_EVENT.get(event, [])
    if len(matched) == 1:
        return [key_run_id]
    return [f"{key_run_id}_{trigger.id}" for trigger in matched]


def is_windowed_flow(flow_id: str) -> bool:
    """True when what wakes this flow is a window rather than one case.

    An unbound flow reads False rather than raising: "nothing binds it" is not
    "it is windowed", and a caller asking about a draft wants the conservative
    answer.
    """
    return any(t.wakes == "window" for t in triggers_for_flow(flow_id))


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------


class EventLog(Protocol):
    def append(self, event: Event) -> Event: ...
    def list(self, limit: int | None = None) -> list[Event]: ...


class InMemoryEventLog:
    """Append-only by omission: there is no update and no delete to call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[Event] = []

    def append(self, event: Event) -> Event:
        with self._lock:
            self._items.append(event)
        return event

    def list(self, limit: int | None = None) -> list[Event]:
        with self._lock:
            items = list(reversed(self._items))  # newest first
        return items[:limit] if limit else items


class FirestoreEventLog:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed in dev mode

        self._col = firestore.Client(project=project).collection(collection)
        self._desc = firestore.Query.DESCENDING

    def append(self, event: Event) -> Event:
        self._col.document(event.id).set(event.model_dump(mode="json"))
        return event

    def list(self, limit: int | None = None) -> list[Event]:
        query = self._col.order_by("at_iso", direction=self._desc)
        return [
            Event.model_validate(d.to_dict())
            for d in query.limit(limit or 500).stream()
        ]


def _facts(payload: dict) -> dict:
    """The fact, in a size a log row can hold.

    An invoice submission carries a base64 document; a feed delivery carries
    two hundred records. The run needs both and the log needs neither — the
    artifact is stored once, by the run, in the document store — so a long or
    repeated value is recorded as its size.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (list, dict)):
            out[key] = f"{len(value)} item(s)"
        elif isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
            out[key] = f"{len(value)} chars"
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# The Dispatcher
# ---------------------------------------------------------------------------


class Dispatch(BaseModel):
    """One emitted fact: the Event, and the runs its Triggers started."""

    event: Event
    runs: list[RunRecord] = Field(default_factory=list)

    def one(self) -> RunRecord:
        """The single run this fact started, for a caller that awaits it.

        `submit()` emits a fact in order to get work done and has a row to
        return; an unbound event is a wiring error there, not an outcome.
        """
        if not self.runs:
            raise Undispatched(self.event.name)
        return self.runs[0]


class Dispatcher:
    """Evaluates Triggers, and starts what they name.

    It registers itself as the engine's event sink, because there is one log:
    a fact a Flow emitted and a fact that started a Flow belong in the same
    place, or neither of them is a history.
    """

    def __init__(self, engine: RunEngine, events: EventLog) -> None:
        self.engine = engine
        self.events = events
        if engine.emit is not None:
            # Two Dispatchers over one engine is two logs, and two logs is no
            # history — the second takes every emitted Event with it. Said out
            # loud, because the symptom is an empty log rather than an error.
            log.warning("Replacing the run engine's event sink")
        engine.emit = self.record

    def emit(
        self, name: str, inputs: dict | None = None, *, actor: str = "",
        run_id: str | None = None, case_ref: str = "",
        principal: Principal | None = None,
    ) -> Dispatch:
        """Record the fact, then start what its Triggers name — in that order.

        The run ids are minted here rather than left to the engine, so the
        Event can name them: the log says "this fact started run X" at a moment
        when run X does not exist, which is the ordering the doctrine asks for
        and the one the previous design had backwards.

        `case_ref` fills a field `Event` has always declared and nothing on this
        path had ever set. A windowed fact has no case and leaves it empty; a
        per-case one — an approval on one order — names the thing it is about,
        and that name reaches the run, its steps and every ledger entry they
        commit. Without it an auditor joining a PATCH back to the finding that
        justified it has only the run id, which says when but not what.

        `principal` is the human whose judgment released the gate, carried for
        `tasks_api`'s reason: the Gateway checks who is committing *at the
        write*, so a decision that arrives here without its decider commits as
        the Flow's own — accurate for a timer, a lie for an approval.
        """
        inputs = inputs or {}
        matched = _BY_EVENT.get(name, [])
        # A caller-supplied id is an idempotency key for the *fact* — a
        # re-delivered feed re-entering its own runs. `keyed_run_ids` derives
        # one id per matched Trigger from it, and the caller asks the same
        # function what to look for, so the two cannot disagree about which
        # run a redelivery is.
        derived = keyed_run_ids(name, run_id) if run_id else []
        ids = [
            derived[index] if derived
            else RunEngine.mint_run_id(t.starts, inputs, actor)
            for index, t in enumerate(matched)
        ]
        event = self.events.append(Event(
            id=f"evt_{secrets.token_hex(5)}",
            name=name, at_iso=utcnow().isoformat(), actor=actor,
            case_ref=case_ref, payload=_facts(inputs),
            triggers=[t.id for t in matched], starts=ids,
        ))
        if not matched:
            log.info(
                "Event bound no Trigger",
                extra={"eventId": event.id, "event": name, "actor": actor},
            )
            return Dispatch(event=event)
        runs = [
            self.engine.start(
                trigger.starts, inputs, actor=actor, run_id=rid,
                caused_by=event.id, case_ref=case_ref, principal=principal,
            )
            for trigger, rid in zip(matched, ids)
        ]
        log.info(
            "Event dispatched",
            extra={
                "eventId": event.id, "event": name,
                "triggers": [t.id for t in matched],
                "runs": [r.id for r in runs],
            },
        )
        return Dispatch(event=event, runs=runs)

    def record(self, name: str, summary: str, run: RunRecord) -> Event:
        """A fact a running Flow emitted: appended, never dispatched.

        The engine's sink for `RunContext.event`, one-way on purpose (see the
        module docstring). The journal already holds the row; what this adds is
        that the fact leaves the run it happened in, so `InvoicePosted` is
        finally a name something else can read.
        """
        return self.events.append(Event(
            id=f"evt_{secrets.token_hex(5)}", name=name,
            at_iso=utcnow().isoformat(), actor=run.actor,
            case_ref=run.case_ref, payload={"summary": summary},
            run_id=run.id, flow_id=run.flow_id,
        ))


def build_dispatcher(
    engine: RunEngine, backend: str, project: str | None, collection: str
) -> Dispatcher:
    events: EventLog = (
        FirestoreEventLog(project, collection) if backend == "firestore"
        else InMemoryEventLog()
    )
    return Dispatcher(engine, events)
