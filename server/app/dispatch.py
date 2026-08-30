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
)}

#: The trigger index. The doctrine keeps it in a fast key-value store for O(1)
#: dispatch; at this size a dict built once off the closed table is that store.
_BY_EVENT: dict[str, list[Trigger]] = {}
for _trigger in TRIGGERS.values():
    _BY_EVENT.setdefault(_trigger.event, []).append(_trigger)


def triggers_for_flow(flow_id: str) -> list[Trigger]:
    """Every declared Trigger that starts this flow."""
    return [t for t in TRIGGERS.values() if t.starts == flow_id]


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
        run_id: str | None = None,
    ) -> Dispatch:
        """Record the fact, then start what its Triggers name — in that order.

        The run ids are minted here rather than left to the engine, so the
        Event can name them: the log says "this fact started run X" at a moment
        when run X does not exist, which is the ordering the doctrine asks for
        and the one the previous design had backwards.
        """
        inputs = inputs or {}
        matched = _BY_EVENT.get(name, [])
        # A caller-supplied id is an idempotency key for one binding — a
        # re-delivered feed re-entering its own run. With several Triggers it
        # cannot be one id, so they are minted.
        ids = [
            run_id if run_id and len(matched) == 1
            else RunEngine.mint_run_id(t.starts, inputs, actor)
            for t in matched
        ]
        event = self.events.append(Event(
            id=f"evt_{secrets.token_hex(5)}",
            name=name, at_iso=utcnow().isoformat(), actor=actor,
            payload=_facts(inputs),
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
                caused_by=event.id,
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
