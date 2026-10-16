"""Event, Trigger, Dispatcher: causation recorded before the thing it caused.

The doctrinal assertions, each of them previously unfalsifiable because the
mechanism did not exist:

- an Event is appended to a log something other than one run's journal can
  read;
- a Trigger starts the run it names, and the run says which fact started it;
- the fact is logged **before** the run exists — the ordering the whole module
  is for, and the one the old design had backwards;
- an event nothing binds starts nothing and says so, rather than failing
  silently or starting something by default;
- a resumed run replays its emitted Events without appending them twice;
- and the closed table is closed: every Trigger names a flow the engine can
  actually run.
"""

import pytest

from app import personas
from app.auth import principal_for
from app.dispatch import (
    TRIGGERS,
    Dispatcher,
    InMemoryEventLog,
    Undispatched,
    is_windowed_flow,
)
from app.runs import InMemoryRunStore, RunEngine, RunRecord
from app.connectors.sync import SyncRequest
from app.task_queue import InMemoryTaskDirectory, Task, TaskService
from tests import world as test_world

CLERK = personas.get("customer-user:ap")


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def bare():
    """An engine and a Dispatcher over it, with no flow bodies registered.

    Bodies are registered per test, under the flow ids the *declared* Triggers
    name: the table is closed, so a test that wanted its own binding would be
    testing a table that does not ship.
    """
    engine = RunEngine(InMemoryRunStore(), TaskService(InMemoryTaskDirectory()))
    return Dispatcher(engine, InMemoryEventLog())


# ---- the declared set ------------------------------------------------------


def test_every_trigger_names_a_flow_the_engine_can_run(world):
    """A Trigger bound to a flow nothing registered would fire into nothing,
    and the failure would surface as a missing run rather than a typo."""
    named = {t.starts for t in TRIGGERS.values()}
    assert named and named <= set(world.engine.registered())


# ---- the ordering ----------------------------------------------------------


def test_the_cause_is_recorded_before_the_run_exists(bare):
    """The claim the module is for.

    A watching store snapshots the log at the moment it is first asked to
    persist the run. If the Event is not already there — naming this run id in
    `starts` — then the causation was recorded by the thing it caused, which
    is what `DOCTRINE.md:209` forbids and what the flow bodies used to do.
    """
    log = bare.events
    first_sight: dict[str, list] = {}

    class Watching(InMemoryRunStore):
        def put(self, record: RunRecord) -> RunRecord:
            first_sight.setdefault(record.id, list(log.list()))
            return super().put(record)

    bare.engine.store = Watching()
    bare.engine.register("accounts-payable", lambda ctx, inputs: {})

    dispatch = bare.emit("InvoiceReceived", {"submitted_by": "ap@x.example"})
    run = dispatch.one()
    [at_first_put] = first_sight[run.id]
    assert at_first_put.id == dispatch.event.id
    assert at_first_put.starts == [run.id]
    # And the run points back, so the link is navigable from either end.
    assert run.caused_by == dispatch.event.id


def test_a_trigger_starts_the_run_it_names(bare):
    started: list[str] = []
    bare.engine.register(
        "expense-reimbursement",
        lambda ctx, inputs: started.append(inputs["employee"]) or {},
    )

    dispatch = bare.emit("ExpenseSubmitted", {"employee": "kim@x.example"})
    assert started == ["kim@x.example"]
    assert dispatch.one().flow_id == "expense-reimbursement"
    assert dispatch.event.triggers == ["expense-submitted"]


def test_an_event_nothing_binds_starts_nothing_and_says_so(bare):
    dispatch = bare.emit("SomethingNobodyBound", {"n": 1})
    assert dispatch.runs == []
    # Recorded, not swallowed: the fact happened whether or not anything wanted
    # it, and an empty `triggers` is the readable form of "nothing responded".
    [event] = bare.events.list()
    assert event.name == "SomethingNobodyBound"
    assert event.triggers == [] and event.starts == []
    # A caller that emitted in order to get work done hears about it.
    with pytest.raises(Undispatched):
        dispatch.one()


# ---- the log ---------------------------------------------------------------


def test_a_flow_emitted_event_leaves_the_run(world):
    """`InvoicePosted` used to be a row in one journal that nothing read."""
    world.sync.deliver(SyncRequest.model_validate({
        "source": "erp-vendors",
        "vendors": [{"external_id": "V-10041", "name": "Acme Industrial Inc.",
                     "tax_id": "94-2711883"}],
    }), actor="system:test")
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="mer.pdf",
        hint="newvendor=meridian-supply nopo invoice=MER-1 lines=PPE-GLV:2 "
             "total=190.00",
    )
    world.payables.resume_for_task(
        world.tasks.decide(record.task_id, "approved", CLERK.email),
        principal_for(CLERK),
    )

    posted = [e for e in world.dispatcher.events.list() if e.name == "InvoicePosted"]
    assert len(posted) == 1
    # It carries where it came from, which is the half a journal row cannot.
    assert posted[0].run_id == record.run_id
    assert posted[0].flow_id == "accounts-payable"
    # And it bound nothing: a response deciding another response should begin
    # is fan-out, and this deliberately does not do that.
    assert posted[0].triggers == []


def test_a_resumed_run_replays_its_events_without_appending_them_twice(bare):
    """The event log rides the journal's replay guard, so driving a run three
    times emits what happened once."""
    def body(ctx, inputs):
        ctx.event("e1", "ThingHappened", "once, ever")
        ctx.gate("k1", "gate", build_task=lambda: Task(
            id="", kind="decision", title="decide", queue="ap-clerk",
        ))
        return {}

    bare.engine.register("accounts-payable", body)
    run = bare.emit("InvoiceReceived", {"submitted_by": "ap@x.example"}).one()
    assert run.status == "suspended"

    bare.engine.tasks.decide(run.open_task_id, "approved", "ap@x.example")
    assert bare.engine.resume(run.id).status == "succeeded"
    bare.engine.resume(run.id)  # a finished run, driven again

    assert [e.name for e in bare.events.list() if e.name == "ThingHappened"] == [
        "ThingHappened"
    ]


def test_the_payload_is_the_fact_and_not_a_second_copy_of_the_artifact(bare):
    bare.engine.register("accounts-payable", lambda ctx, inputs: {})
    dispatch = bare.emit("InvoiceReceived", {
        "submitted_by": "ap@x.example",
        "image_b64": "x" * 5000,
        "filename": "mer.pdf",
    })
    payload = dispatch.event.payload
    assert payload["filename"] == "mer.pdf"
    assert payload["image_b64"] == "5000 chars"


# ---- the feeds -------------------------------------------------------------


def test_every_sync_feed_starts_through_the_dispatcher(world):
    """All five inbound feeds, dispatched from what arrived rather than from a
    flow id the delivery picked."""
    world.sync.deliver(SyncRequest.model_validate({
        "source": "erp",
        "vendors": [{"external_id": "V-1", "name": "Acme Industrial Inc."}],
        "purchase_orders": [{"external_id": "P-1", "po_number": "PO-1",
                             "vendor_name": "Acme Industrial Inc."}],
        "receiving_reports": [{"external_id": "R-1", "receipt_number": "RC-1",
                               "po_number": "PO-1"}],
        "teams": [{"external_id": "T-1", "slug": "finance-ops",
                   "name": "Finance Ops"}],
        "calendars": [{"external_id": "C-1", "address": "ops@naviant.com",
                       "name": "Ops"}],
    }), actor="system:test")

    dispatched = {
        e.name: e for e in world.dispatcher.events.list() if e.triggers
    }
    assert set(dispatched) == {
        "VendorRecordsDelivered", "PurchaseOrdersDelivered",
        "ReceivingReportsDelivered", "WorkforceDelivered", "CalendarsDelivered",
    }
    for event in dispatched.values():
        [run_id] = event.starts
        assert world.engine.get(run_id).caused_by == event.id


# ---- one fact, two responses -----------------------------------------------


def test_one_fact_with_two_responses_starts_both_in_declared_order(world):
    """`DocumentsDelivered` is the event with the most responses, and the
    order is load-bearing: `ap-intake` and `field-documents` both read the
    filing tree `dms-sync` lands, so a Dispatcher that started them the other
    way round would hand the readers an empty graph. Read off the table's
    declaration order, which is why the three are declared adjacently.
    """
    world.sync.deliver(SyncRequest.model_validate({
        "source": "dms-library",
        "folders": [{"external_id": "f1", "name": "Accounts Payable"}],
        "documents": [],
    }), actor="system:test")

    [event] = [
        e for e in world.dispatcher.events.list()
        if e.name == "DocumentsDelivered" and e.triggers
    ]
    assert event.triggers == [
        "documents-delivered", "documents-delivered-ap",
        "documents-delivered-field",
    ]
    assert [world.engine.get(r).flow_id for r in event.starts] == [
        "dms-sync", "ap-intake", "field-documents"
    ]
    # Both name the fact that caused them, and it was logged before either ran.
    assert all(world.engine.get(r).caused_by == event.id for r in event.starts)


def test_a_keyed_redelivery_re_enters_every_run_the_key_named(world):
    """The idempotency key names a *fact*, and a fact with two responses has
    two runs.

    Before the second Trigger existed the key was the run id outright, which
    is what every feed's replay guard was written against and what is already
    in every deployment's run store — so one response still keeps it verbatim
    and only a fan-out derives. A retry that started a second copy of each run
    would double-count a delivery, which is exactly what the key is for.
    """
    from app.dispatch import keyed_run_ids

    assert keyed_run_ids("VendorRecordsDelivered", "run_sync_abc") == [
        "run_sync_abc"
    ]
    assert keyed_run_ids("DocumentsDelivered", "run_sync_abc") == [
        "run_sync_abc_documents-delivered",
        "run_sync_abc_documents-delivered-ap",
        "run_sync_abc_documents-delivered-field",
    ]
    # And the production fact, which acquired its second response with
    # `field-documents`: from one Trigger to two is where a verbatim key stops
    # being the run id, which is harmless in a fresh deployment with no run
    # history and the reason the pair landed in one commit.
    assert keyed_run_ids("JobRecordsDelivered", "run_sync_abc") == [
        "run_sync_abc_jobs-delivered",
        "run_sync_abc_jobs-delivered-field-documents",
    ]

    request = SyncRequest.model_validate({
        "source": "dms-library",
        "idempotency_key": "dms-library:2026-09-01T00:00:00Z:0",
        "folders": [{"external_id": "f1", "name": "Accounts Payable"}],
        "documents": [],
    })
    first = world.sync.deliver(request, actor="system:test")
    assert [o["replayed"] for o in first] == [False]
    again = world.sync.deliver(request, actor="system:test")
    assert [o["replayed"] for o in again] == [True]
    assert len(world.engine.list(flow_id="dms-sync")) == 1
    assert len(world.engine.list(flow_id="ap-intake")) == 1


# ---- the shape the doctrine reads off a Trigger ----------------------------


def test_windowedness_is_read_off_the_trigger(world):
    """The per-case/windowed distinction, off a record with a shape rather
    than off a `timer:` prefix no flow ever carried.

    A feed wakes on a window: it lands bulk observations and raises Tasks on
    aggregates. An invoice wakes one case and commits per case. Neither Flow
    is typed — the same engine runs both.
    """
    from app.flows import is_windowed

    assert is_windowed_flow("vendor-sync")
    assert is_windowed_flow("calendar-sync")
    assert not is_windowed_flow("accounts-payable")
    assert not is_windowed_flow("expense-reimbursement")

    # The registry reads the same answer through a definition.
    from app.flows import build_flow_service, seed_flows_if_empty

    flows = build_flow_service("memory", None, "flows")
    seed_flows_if_empty(flows)
    assert not is_windowed(flows.require("accounts-payable"))
