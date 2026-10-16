"""The isolated world a graded run executes in.

Extracted from `evals.py` because it is load-bearing far beyond evaluation:
`server/tests/world.py` builds the **entire** server test suite's world from
`build_world`, and `worlds.py` uses the same isolation to preview an authored
fixture. Both would otherwise import a module that is being reshaped around
them.

**Why a world at all.** Object ids are derived from natural keys, so an eval
fixture named "Acme Industrial Inc." *is* the production vendor id. Running
against the live graph would append `model:` claims to a real vendor and —
the one that actually matters — feed eval invoices into the anomaly baseline
that real invoices are judged against, changing flow behaviour rather than a
number on a screen. So every case gets its own in-memory world with the corpus
copied in: frozen for the run and writable, so nothing a graded run writes
touches the real shelf, and the revisions it read are recorded, because two
pass rates are only comparable when they measured the same knowledge.

**And an estate, always.** One graded flow reaches out of the process and
changes a record in a customer's ERP, so isolation stops being a fact about
ids the moment `order-release` is evaluated: a world with no write plane would
either refuse to run it or, worse, run it against whatever `deps` had wired.
Every world therefore holds an `estate_fake.FakeErp` and a writer whose entire
wire is that fake — the precondition read, the PATCH, and the verification
re-read alike. It is seeded from the fixture's own recorded rows, so the graph
and the ERP are two shapes of one declaration rather than two accounts of one
estate.
"""

import logging
from typing import Any, Callable

from pydantic import BaseModel, Field

from .anomaly import AnomalyService, InMemoryProfileStore
from .answering import AnswerService
from .calendars import CalendarService
from .dispatch import Dispatcher, InMemoryEventLog
from .documents import DocumentService, InMemoryDocumentStore
from .estate import EstateConnector, InMemoryConnectorStateStore
from .estate_fake import FakeErp, FakeEstateError, as_delivery_row
from .estate_writes import EstateWriter, InMemoryEstateWriteJournal
from .gateway import ActionGateway, InMemoryLedgerStore
from .graph import GraphService, InMemoryGraphStore, parse_ref
from .runs import InMemoryRunStore, RunEngine
from .service import KnowledgeService
from .storage import InMemoryRepository
from .connectors.sync import SyncRequest, SyncService
from .queues import (
    InMemoryQueueDirectory,
    QueueService,
    bootstrap_from_roster as bootstrap_queues_from_roster,
)
from .task_queue import InMemoryTaskDirectory, TaskService

log = logging.getLogger("cumulus.eval_world")

#: The address a recorded estate answers at. Not a real project, and it is here
#: rather than blank because `EstateWriter` refuses to build a URL for an
#: unregistered estate — a graded run has an estate, it is simply one made of
#: recorded rows.
EVAL_ESTATE_PROJECT = "cumulus-eval-estate"
#: The namespace a recorded estate answers under, when nothing says otherwise.
#: It is the fallback of a `build_world` parameter rather than the answer,
#: because the namespace is part of the address (`/api/<namespace>/v1/...`) and
#: a second customer's fixture is recorded under its own: a Wick case whose
#: estate was addressed as `naviant` would be reading a URL that does not
#: exist. This deployment's own pin wins over this constant, and an explicit
#: argument wins over both.
EVAL_ESTATE_NAMESPACE = "naviant"

#: Who the harness is, on every write it makes on a case's behalf. One spelling,
#: so a run journal in a graded world never has to be read to work out whether a
#: person was involved.
EVAL_ACTOR = "system:eval-fixture"
EVAL_DECIDER = "eval@naviant.com"


class EvalWorld(BaseModel):
    """Everything a flow needs, in memory, sharing nothing writable."""

    model_config = {"arbitrary_types_allowed": True}

    knowledge: Any
    tasks: Any
    engine: Any
    graph: Any
    gateway: Any = None
    dispatcher: Any = None
    documents: Any
    anomaly: Any
    sync: Any
    expenses: Any
    payables: Any
    #: The AP intake flow's service. Holds no fetcher in a world: see
    #: `build_world`.
    intake: Any = None
    #: The plant spine's two services, held for `intake`'s reason:
    #: constructing them is what registers `shipping-list-extraction` and
    #: `plant-eligibility-sweep`, so every world can close a plant window
    #: rather than only production — and `dispatch`'s closed table asserts
    #: that every declared Trigger names a flow the engine can run.
    extraction: Any = None
    plant: Any = None
    #: The flow that fills a department's day and holds it at
    #: `pending_approval` until somebody signs. Held for the same reason as its
    #: two neighbours above, and with one edge of its own:
    #: `DepartmentDayOpened` is a declared Trigger emitted from **inside**
    #: the eligibility sweep, so a world that had not registered this body
    #: would fail the window rather than the flow.
    scheduling: Any = None
    #: The compliance sweep's service. Held on the world for `intake`'s
    #: reason: constructing it is what registers `credential-tracking`, so
    #: every world can close a compliance window rather than only production.
    credentials: Any = None
    #: The field-documents window's service. Held on the world for
    #: `credentials`' reason and one of its own: this flow's Triggers sit on
    #: two events every recorded estate delivers, so a world that had not
    #: registered its body would fail the delivery rather than the flow.
    packets: Any = None
    #: The correction set, and the flow that appends to it. Isolated like
    #: everything else here: a graded case may correct an extraction, and that
    #: pair must not land in the deployment's tuning data.
    corrections: Any = None
    calendars: Any = None
    #: This world's own desk registry. Isolated like everything else here: a
    #: graded case may add a queue member, and that must not reach the console.
    queues: Any = None
    #: The answering stack, with no generative backend — building it is what
    #: registers the `answer-question` body, so every world can start the flow
    #: a question becomes. A world whose backend is None still runs it and
    #: still records that a question was asked; what it does not record is a
    #: model call, because none was made.
    answers: Any = None
    #: The recorded ERP behind this world's write plane. Held on the world
    #: rather than reached through `sync.estate_writer._patch`, because a case
    #: that needs the ERP to be holding something else by the time the desk
    #: answers is describing the world, not the wire.
    estate: Any = None
    knowledge_revisions: dict[str, int] = Field(default_factory=dict)


def build_world(
    source_knowledge=None,
    *,
    seed_dir: str | None = None,
    estate_namespace: str | None = None,
) -> EvalWorld:
    """An isolated world, with the corpus copied in rather than shared.

    Copied rather than shared: a graded run reads governed knowledge and may
    write it, and neither should reach the corpus the console is showing. The
    revisions copied are recorded, so two results are only compared when they
    read the same knowledge.

    `estate_namespace` addresses this world's recorded estate. It defaults to
    `settings.estate_namespace or EVAL_ESTATE_NAMESPACE`, so a deployment
    pinned to a customer grades against that customer's namespace without
    every caller having to say so, and an unpinned box still grades against
    `naviant`.
    """
    # Deferred: the flow modules import this one for their seed helpers.
    from .tasks.accounts_payable.domain import (
        InMemoryPayableDirectory,
        PayableService,
        StubInvoiceReader,
    )
    from .tasks.accounts_payable.v8.locate import StubFieldLocator
    from .tasks.ap_corrections.domain import (
        CorrectionService,
        InMemoryCorrectionStore,
    )
    from .tasks.ap_intake.domain import ApIntakeService
    from .tasks.credential_tracking.domain import CredentialTrackingService
    from .tasks.department_scheduling.domain import (
        DepartmentSchedulingService,
    )
    from .tasks.plant_eligibility.domain import PlantEligibilityService
    from .tasks.shipping_list_extraction.domain import (
        ShippingListExtractionService,
    )
    from .tasks.shipping_list_extraction.readers import StubShippingListReader
    from .tasks.field_documents.domain import FieldDocumentsService
    from .platform_assets import publish_generated_assets
    from .tasks.expense_reimbursement.domain import (
        InMemoryReimbursementDirectory,
        ReimbursementService,
        StubReceiptReader,
    )
    from .config import settings
    from .service import seed_if_empty

    repo = InMemoryRepository()
    revisions: dict[str, int] = {}
    if source_knowledge is not None:
        for asset in source_knowledge.repo.all():
            repo.put(asset.model_copy(deep=True))
            revisions[asset.id] = getattr(asset, "revision", 1)
    knowledge = KnowledgeService(repo)
    if source_knowledge is None:
        seed_if_empty(knowledge, seed_dir or settings.seed_dir)
        publish_generated_assets(knowledge)
        revisions = {a.id: getattr(a, "revision", 1) for a in repo.all()}

    tasks = TaskService(InMemoryTaskDirectory())
    queues = QueueService(InMemoryQueueDirectory())
    bootstrap_queues_from_roster(queues)
    engine = RunEngine(InMemoryRunStore(), tasks)
    graph = GraphService(InMemoryGraphStore())
    # One Gateway per world, for the same reason the graph is one per world: a
    # graded run's ledger must not append to the deployment's, and an eval that
    # shared a ledger would be reading its own writes as history.
    gateway = ActionGateway(graph, InMemoryLedgerStore())
    # One Dispatcher per world, for the Gateway's reason one line up: a graded
    # run's Events must not append to the deployment's log, and the run ids a
    # Trigger mints here belong to this world's engine.
    dispatcher = Dispatcher(engine, InMemoryEventLog())
    documents = DocumentService(InMemoryDocumentStore(), 700_000)
    anomaly = AnomalyService(InMemoryProfileStore(), graph)
    # The write plane, over a recorded ERP, always. Not "when a case needs it":
    # the flow under test reaches a customer's system of record over HTTP, and
    # the only structural guarantee that a graded run cannot is that the wire it
    # is handed is never a socket. `ESTATE_WRITES_ENABLED` and the boundary test
    # on `estate_writes` are the other two, and all three are independent — this
    # one holds even on a box where somebody turned the flag on.
    estate = FakeErp()
    estate_writer = EstateWriter(
        EstateConnector(
            InMemoryConnectorStateStore(),
            project=EVAL_ESTATE_PROJECT,
            namespace=(
                estate_namespace
                or settings.estate_namespace
                or EVAL_ESTATE_NAMESPACE
            ),
            fetch=estate.fetch,
        ),
        InMemoryEstateWriteJournal(),
        fetch=estate.fetch,
        patch=estate.patch,
    )
    sync = SyncService(
        graph, knowledge, tasks, engine, gateway, dispatcher, estate_writer,
    )
    expenses = ReimbursementService(
        InMemoryReimbursementDirectory(), StubReceiptReader(), knowledge, tasks,
        engine, graph, documents, gateway, dispatcher, queues,
    )
    payables = PayableService(
        InMemoryPayableDirectory(), StubInvoiceReader(), knowledge, tasks,
        engine, graph, documents, anomaly, gateway, dispatcher,
        StubFieldLocator(), queues,
    )
    # After payables and before anything decides: a clerk's correction emits a
    # fact this flow answers, and a world where nothing had registered the
    # response would drop the flywheel on the floor with no error anywhere.
    corrections = CorrectionService(InMemoryCorrectionStore(), engine, gateway)
    # After payables, because intake's output is accounts-payable runs. No
    # fetcher: a world reaches no network, and a case that wants intake to read
    # something hands it one — which is also the honest default, since a
    # deployment with no estate registered has nothing to fetch from either.
    intake = ApIntakeService(
        graph, knowledge, documents, tasks, engine, gateway, dispatcher,
        fetcher=None,
    )
    # Reaches no network and needs no reader: everything it decides from is
    # in the graph and everything it decides against is one governed asset. It
    # is here so a world can close a compliance window at all — the body is
    # registered by construction, like every other flow service.
    credentials = CredentialTrackingService(
        graph, knowledge, tasks, engine, gateway, dispatcher,
    )
    # The same shape and the same reason, from the other direction: nothing to
    # fetch, one governed asset, and a body that has to be registered before a
    # delivery arrives — `DocumentsDelivered` wakes this world's `dms-sync`,
    # its `ap-intake` and this flow, in that order.
    packets = FieldDocumentsService(
        graph, knowledge, tasks, engine, gateway, dispatcher,
    )
    # The plant spine, in dependency order and for `intake`'s reason: the
    # window's whole output is extraction runs. The reader is the stub, like
    # every other model in a world — a graded run must never reach a live
    # model and a test world has no credential — and the locator is the one
    # `payables` already holds, because boxes on a page are boxes on a page.
    # No fetcher: a world reaches no network, and a case that wants a ticket
    # read hands it one.
    extraction = ShippingListExtractionService(
        graph, knowledge, documents, tasks, engine, gateway,
        reader=StubShippingListReader(), locator=StubFieldLocator(),
        fetcher=None,
    )
    # Before the sweep, because the sweep's last step emits the fact this
    # flow answers. It holds the Gateway the sweep does not: the plant
    # spine's only two graph writes are this flow's.
    scheduling = DepartmentSchedulingService(
        graph, knowledge, tasks, engine, gateway, dispatcher,
    )
    plant = PlantEligibilityService(graph, knowledge, tasks, engine, dispatcher)
    calendars = CalendarService(graph)
    # No backend: a graded run must never reach a live model, and a test world
    # has no credential. The Flow is registered either way, so `answer-question`
    # is a flow this world can start rather than one only production has.
    answers = AnswerService(
        knowledge, None, None, graph=graph,
        dispatcher=dispatcher, engine=engine,
    )
    return EvalWorld(
        knowledge=knowledge, tasks=tasks, engine=engine, graph=graph,
        gateway=gateway, dispatcher=dispatcher, documents=documents,
        anomaly=anomaly, sync=sync, queues=queues,
        expenses=expenses, payables=payables, intake=intake,
        extraction=extraction, plant=plant, scheduling=scheduling,
        credentials=credentials,
        packets=packets,
        corrections=corrections,
        calendars=calendars,
        answers=answers, estate=estate,
        knowledge_revisions=revisions,
    )


# ---------------------------------------------------------------------------
# Two flows that need more than a call
# ---------------------------------------------------------------------------


def _confirm_merges(world: EvalWorld, wanted: list[str]) -> int:
    """Answer identity proposals the way a steward would, before a pass runs.

    The `blocked_on_identity` half of the disagreement sweep is a claim about
    two worlds that differ by exactly one link's *status*, so the only honest
    way to grade it is to run the same fixture twice with a steward's answer in
    one of them. Through the Task and the decision hook rather than by writing
    the prop, because the beat this unlocks is a person clicking approve in the
    workbench, and a case that reached past that would not exercise the path the
    beat runs on.

    Matched on natural keys — never ids — for `FixtureObject`'s reason: a case
    naming `Voss Auto` reads the same here and in production.
    """
    if not wanted:
        return 0
    keys = set(wanted)
    confirmed = 0
    for task in world.tasks.list():
        if task.kind != "merge_review" or task.status != "open":
            continue
        ref = parse_ref(task.case_ref)
        link = world.graph.store.get_link(ref.id) if ref.id else None
        if link is None:
            continue
        ends = [world.graph.get(link.from_id), world.graph.get(link.to_id)]
        named = {o.natural_key for o in ends if o is not None}
        if not keys & named:
            continue
        decided = world.tasks.decide(
            task.id, verdict="approved", decided_by=EVAL_DECIDER,
            note="confirmed by the case, as a steward would have",
        )
        world.sync.apply_merge_decision(decided)
        confirmed += 1
    return confirmed


def start_disagreement_sweep(
    world: EvalWorld, trigger: dict, _decisions: list[dict]
) -> Any:
    """The state window closing over a book identity has already been run on.

    The identity pass is a precondition of the *estate* rather than of any one
    assertion: in production it is what has already happened by the time a state
    window closes, and the proposals it leaves at `in_review` are the state this
    sweep is written against. A fixture that skipped it would grade the flow
    against a world it never meets — and would grade the counterparty join
    against a book where the join does not exist yet.

    `confirm` is the one thing a case may change about that world: which
    proposals a steward has already answered. Everything else — the policy, the
    rows, the pass — is identical across the two cases, which is what makes the
    pair a demonstration rather than two unrelated results.
    """
    world.sync.sweep_identities(actor=EVAL_ACTOR)
    _confirm_merges(world, list(trigger.get("confirm") or []))
    return world.sync.sweep_disagreements(actor=EVAL_ACTOR)


def _door(estate: Any, door: str) -> Callable[[str, dict], dict]:
    """How the estate's PATCH route behaves for a case.

    Two named failures beside the estate's own behaviour, and they are the two
    the release flow is *written against*: a door that refuses what it was
    asked, and a door that answers cheerfully and changes nothing. The second
    is the one a 200-trusting client cannot see, and it is the whole reason the
    writer re-reads the row instead of believing the response.
    """
    if door == "refuses":
        def refuses(url: str, _body: dict) -> dict:
            estate.calls.append(f"PATCH {url}")
            raise FakeEstateError(400, "'status' must be one of: Pending Approval")
        return refuses
    if door == "lies":
        def lies(url: str, body: dict) -> dict:
            estate.calls.append(f"PATCH {url}")
            return {"id": url.rsplit("/", 1)[-1], **body}
        return lies
    return estate.patch


def _release_delta(row: dict, status: str) -> SyncRequest:
    """The feed carrying our own change home, holding `status`.

    Built out of the row the estate is holding rather than out of a second
    declaration in the case, so the delivery that comes home is the record that
    was written — which is the only way an echo can be recognized at all.
    """
    return SyncRequest.model_validate({
        "source": "erp-sales",
        "watermark": "2026-08-31T00:00:00Z",
        "sales_orders": [as_delivery_row(row, status)],
    })


def start_order_release(
    world: EvalWorld, trigger: dict, decisions: list[dict]
) -> Any:
    """The whole sequence a release is: a finding, a desk, and one PATCH.

    This flow's run does not exist until somebody answers a Task, so there is
    no trigger to fire and the `ScriptedDecision` is what starts it. The
    sequence is the real one throughout — the sweep raises the Task, the Task is
    decided through `TaskService`, and `apply_release_decision` emits the Event
    the Trigger starts the run from — because the thing worth grading is the
    path a controller's click actually takes.

    What a case may say about the world:

    - `order_status` — what the ERP holds by the time the desk answers. Not a
      wire setting: three of the five precondition outcomes are *only* reachable
      because the row moved after the finding was raised, and a fixture recorded
      days earlier cannot say that.
    - `door` — how the estate's PATCH route behaves (`_door` above).
    - `again` — the same approval arriving twice, which is the only way the
      idempotency branch is reachable and is a thing that really happens.
    - `echo` — the value the feed carries home afterwards, so the delivery-side
      pass runs inside the graded world rather than beside it.

    Every case here approves. A rejection starts no run at all — it is one
    props write on one edge, committed synchronously by the hook — so there is
    nothing for a spec anchored on a run to grade, and "dismissed stays
    dismissed" is asserted where it can be: in the unit tests, against the
    sweep's own guard.
    """
    estate = world.estate
    record_id = str(trigger.get("record_id") or "")
    if trigger.get("order_status") and record_id:
        estate.hold(record_id, status=str(trigger["order_status"]))
    if trigger.get("door"):
        world.sync.estate_writer = EstateWriter(
            world.sync.estate_writer.connector,
            world.sync.estate_writer.journal,
            fetch=estate.fetch,
            patch=_door(estate, str(trigger["door"])),
        )

    out: Any = world.sync.sweep_disagreements(actor=EVAL_ACTOR)
    payload: dict = {}
    for decision in decisions:
        task = next(
            (t for t in world.tasks.list()
             if t.kind == "state_disagreement" and t.status == "open"),
            None,
        )
        if task is None:
            break
        decided = world.tasks.decide(
            task.id,
            verdict=str(decision.get("verdict") or "approved"),
            decided_by=str(decision.get("decided_by") or EVAL_DECIDER),
            note=str(decision.get("note") or ""),
            payload=dict(decision.get("payload") or {}),
        )
        payload = {
            "finding": decided.case_ref, "task_id": decided.id,
            "decided_by": decided.decision.decided_by,
            "queue": decided.queue,
        }
        out = world.sync.apply_release_decision(decided) or out

    if trigger.get("again") and payload:
        # The same fact twice: a re-delivered approval, or a second decider
        # answering a Task the first one had already answered. It is a second
        # *run* by construction and gets its own journal row, which is exactly
        # why the precondition cannot be "have I written before" and has to be
        # "does the estate hold what I would write, and did anyone here ask".
        from .tasks.order_release.domain import ORDER_RELEASE_EVENT

        again = world.dispatcher.emit(
            ORDER_RELEASE_EVENT, payload,
            actor=payload["decided_by"], case_ref=payload["finding"],
        ).one()
        # The second run's own account, in `apply_release_decision`'s shape, so
        # the runner names the run the case is about rather than the first one.
        out = {"run_id": again.id, "status": again.status, **(again.outputs or {})}

    if trigger.get("echo") and record_id:
        world.sync.deliver(
            _release_delta(estate.record(record_id), str(trigger["echo"])),
            actor=EVAL_ACTOR,
        )
    return out


# ---------------------------------------------------------------------------
# The plant floor: five flows a fact wakes rather than a submission starts
# ---------------------------------------------------------------------------
#
# None of the five has a `submit()`. Each is started by a Trigger on an event,
# so a case's "trigger" is the fact a clock or a feed would have emitted — and
# the entrypoints below emit it through the Dispatcher rather than calling a
# body, for the reason the unit tests give: `Dispatcher.emit` does not guard
# `RunEngine.start`, so a Trigger row whose body nothing registered fails here
# loudly instead of 500-ing the next estate delivery.
#
# Two of them also need the run *picked* rather than taken. `Dispatch.one()`
# hands back the first run a fact started, and the graded flow is never the
# first: a jobs delivery lands the jobs before the packet window reads them,
# and declaration order is the dependency order. The department day is further
# out of reach still — the plant window starts it from **inside** its own body,
# so it exists only in the engine's store and no dispatch the caller holds can
# see it.


def _woke(world: EvalWorld, flow_id: str, event: str, payload: dict) -> Any:
    """Emit a fact and hand back the run of the flow the case is about.

    `Dispatch.one()` is the wrong accessor for every flow here: each waking
    event has more than one Trigger on it, and the graded flow is never the
    first — `JobRecordsDelivered` lands the jobs before the packet window reads
    them, and declaration order is the dependency order.
    """
    dispatch = world.dispatcher.emit(event, payload, actor=EVAL_ACTOR)
    started = [world.engine.get(run.id) for run in dispatch.runs]
    mine = [r for r in started if r is not None and r.flow_id == flow_id]
    if not mine:
        raise KeyError(
            f"`{event}` started no {flow_id} run — is the Trigger declared?"
        )
    return mine[-1]


class RecordedLibrary:
    """The document system a graded extraction reads its pages out of.

    The `estate_fake.FakeErp` of the DMS side, and here for the same reason:
    the flow under test fetches a document's bytes over HTTP, and the only
    structural guarantee that a graded run cannot is that the wire it is handed
    is never a socket. What it serves is a composed page carrying the case the
    stub reader parses — so "the grid for this department is absent" is a fact
    the fixture declares rather than a monkeypatch.
    """

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.calls: list[str] = []

    def fetch_content(self, url: str) -> tuple[bytes, str]:
        self.calls.append(url)
        for key, content in self.files.items():
            if key in url:
                return content, "application/pdf"
        raise RuntimeError(f"404 for {url}")


def _install_library(world: EvalWorld, trigger: dict) -> None:
    """Hand the extraction the pages a case says the plant filed.

    `{"250553": "pages=A1,B,C,D,E,LL"}` — a job number to the case written on
    its shipping list, in `readers.ShippingListScenario`'s own grammar. A world
    reaches no network and holds no fetcher by default, so a case that says
    nothing here is a case about a job whose paper cannot be read, which is
    itself a governed outcome.
    """
    from .tasks.shipping_list_extraction.readers import fixture_page

    pages = dict(trigger.get("library") or {})
    if not pages:
        return
    world.extraction.fetcher = RecordedLibrary({
        f"/{number}/": fixture_page(f"job={number} {case}")
        for number, case in pages.items()
    })


def start_field_documents(
    world: EvalWorld, trigger: dict, _decisions: list[dict]
) -> Any:
    """One or more document windows, over the world the deliveries built.

    What a case may say:

    - `as_of` — the day the window is answered as of. It is not optional in
      practice: a fourteen-day horizon over relative load dates is a different
      selection every time the suite runs.
    - `event` — which of the flow's two Triggers woke it. Either half can be
      the new fact, and the run says which.
    - `windows` — how many consecutive windows to close. Not a convenience:
      the governed recurrence threshold is *three consecutive windows with none
      of the four types*, and a claim about a threshold cannot be graded by a
      world that only ever reached the first count.
    """
    from .connectors.sync import JOB_EVENT
    from .tasks.field_documents.domain import FLOW_ID

    event = str(trigger.get("event") or JOB_EVENT)
    payload: dict = {
        "source": "erp-jobs" if event == JOB_EVENT else "dms-library",
        "watermark": "2026-09-07T06:00:00Z",
        "as_of": str(trigger.get("as_of") or ""),
    }
    # The shape the real feed sends, so `_woken_by` can say which fact it was:
    # a production delivery carries `records` and a document one `folders`.
    payload.update(
        {"records": []} if event == JOB_EVENT else {"folders": [], "documents": []}
    )
    run = None
    for _ in range(max(1, int(trigger.get("windows") or 1))):
        run = _woke(world, FLOW_ID, event, payload)
    return {"run_id": run.id}


def start_credential_tracking(
    world: EvalWorld, trigger: dict, _decisions: list[dict]
) -> Any:
    """One window per day the case names, in order.

    `days` rather than a single `as_of`, for `windows` above's reason and one
    of its own: the three governed notice stages firing *once each per cycle*
    is a claim about a row seen on three different days, and the stage on the
    row preventing a repeat is a claim about the same day twice. Neither is
    sayable by a case that closes one window, and both are what this flow is
    for.
    """
    from .tasks.credential_tracking.domain import COMPLIANCE_WINDOW_EVENT, FLOW_ID

    days = list(trigger.get("days") or [])
    if not days:
        days = [str(trigger.get("as_of") or "")]
    run = None
    for as_of in days:
        run = _woke(
            world, FLOW_ID, COMPLIANCE_WINDOW_EVENT,
            {"window": str(trigger.get("window") or "the compliance clock"),
             "as_of": as_of},
        )
    return {"run_id": run.id}


def _plant_window(world: EvalWorld, trigger: dict) -> Any:
    """The plant window, and the whole spine it drives, as one call.

    `sweep` emits `PlantWindowClosed`; the sweep's own steps then emit
    `JobEligible` per eligible job and `DepartmentDayOpened` per department-day,
    each starting a run inside this one. Driving the spine from its head rather
    than starting the two downstream flows directly is the point: the graded
    claim about the extraction is that *the window produced it*.
    """
    return world.plant.sweep(
        actor=EVAL_ACTOR, as_of=str(trigger.get("as_of") or "")
    )


def start_plant_eligibility(
    world: EvalWorld, trigger: dict, _decisions: list[dict]
) -> Any:
    """The window, and everything it drives, as the plant really runs it.

    The `library` is here for the same reason it is on the two flows below:
    the sweep starts an extraction per eligible job *inside its own step*, and
    a window whose extractions could read nothing would report four successes
    that wrote no demand — a green run over an empty floor.
    """
    _install_library(world, trigger)
    return {"run_id": _plant_window(world, trigger).one().id}


def start_shipping_list_extraction(
    world: EvalWorld, trigger: dict, _decisions: list[dict]
) -> Any:
    """One job's ticket, on the fact the eligibility sweep emits per job.

    `JobEligible` rather than the whole window, because this flow is a **case**
    — one job, one run, one multi-page extraction — and driving it from the
    sweep would make every assertion about a reading depend on which jobs the
    eligibility clauses happened to pass that day.

    A case must name a `library`: a world reaches no network and the extraction
    holds no fetcher, so the pages the plant filed are a fact the fixture
    declares. Same posture `start_order_release` takes with the ERP's door.
    """
    from .tasks.plant_eligibility.domain import JOB_ELIGIBLE_EVENT
    from .tasks.shipping_list_extraction import FLOW_ID

    _install_library(world, trigger)
    job_key = str(trigger.get("job_key") or "")
    payload = {
        "job_key": job_key,
        "job_number": str(trigger.get("job_number") or ""),
        "plant_site": str(trigger.get("plant_site") or "Mazo"),
        "source": str(trigger.get("source") or "erp-jobs"),
    }
    dispatch = world.dispatcher.emit(
        JOB_ELIGIBLE_EVENT, payload, actor=EVAL_ACTOR, case_ref=job_key,
    )
    started = [r for r in dispatch.runs if r.flow_id == FLOW_ID]
    if not started:
        raise KeyError(
            "`JobEligible` started no extraction — is the Trigger declared?"
        )
    return {"run_id": started[0].id}


def start_department_scheduling(
    world: EvalWorld, trigger: dict, _decisions: list[dict]
) -> Any:
    """The day the window opened, for the department the case names.

    `department` picks one of the ten the sweep opens and `schedule_date` one
    of its days; without them the case would be grading whichever ran last,
    which is a fact about the governed department order and the lead-time table
    rather than about the day.
    """
    from .tasks.department_scheduling import FLOW_ID

    _install_library(world, trigger)
    _plant_window(world, trigger)
    wanted = str(trigger.get("department") or "")
    day = str(trigger.get("schedule_date") or "")
    runs = [r for r in world.engine.store.list() if r.flow_id == FLOW_ID]
    runs.sort(key=lambda run: run.started_at_iso)
    if wanted:
        runs = [r for r in runs if (r.inputs or {}).get("department") == wanted]
    if day:
        runs = [r for r in runs if (r.inputs or {}).get("schedule_date") == day]
    if not runs:
        raise KeyError(
            f"the plant window opened no day for "
            f"`{wanted or 'any department'}`{f' on {day}' if day else ''}"
        )
    return {"run_id": runs[-1].id}


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

# How a case's trigger reaches its flow. Kept as an explicit table rather than
# reflection: a flow that wants to be evaluated has to say how it is started,
# and a typo in a case's `trigger` should be an error rather than a silent
# no-op.
#
# Every entry takes the case's decisions as well as its trigger, because one
# flow's run **is** a decision: `order-release` has nothing to submit and no
# window to close, and grading it means answering the Task that starts it. The
# three flows that are started by a trigger and answer their gates as they go
# ignore the third argument, and `GATE_RESUMES` below is what carries a decision
# back into a run that is already suspended.
FLOW_ENTRYPOINTS: dict[str, Callable[[EvalWorld, dict, list[dict]], Any]] = {
    "expense-reimbursement": lambda world, trigger, _d: world.expenses.submit(
        **trigger
    ),
    "accounts-payable": lambda world, trigger, _d: world.payables.submit(**trigger),
    # The first graded flow with no case to submit. Its trigger is a window
    # closing over the world the fixture's deliveries built, which is why its
    # `trigger` dict is usually empty: the precondition *is* the fixture.
    "identity-sweep": lambda world, trigger, _d: world.sync.sweep_identities(
        **trigger
    ),
    "disagreement-sweep": start_disagreement_sweep,
    "order-release": start_order_release,
    # The plant floor. Five flows and not one submission between them: each is
    # started by a fact, so each entrypoint emits one — and three of them have
    # to *pick* the run afterwards, because the waking event starts the sync
    # flow first and the plant window starts its two downstream flows from
    # inside its own body.
    "field-documents": start_field_documents,
    "credential-tracking": start_credential_tracking,
    "plant-eligibility-sweep": start_plant_eligibility,
    "shipping-list-extraction": start_shipping_list_extraction,
    "department-scheduling": start_department_scheduling,
}

#: How a scripted decision is carried back into a run that suspended waiting
#: for one. A flow absent from this table has no gate a case answers *while it
#: runs*: `identity-sweep` and `disagreement-sweep` raise Tasks without
#: suspending, and `order-release`'s decisions are what start it — its
#: entrypoint has already taken them, and a second pass over them here would
#: answer the escalation Task a failed release had just raised.
#:
#: A table rather than the `if flow_id == … else payables` it replaces, for that
#: exact reason: the fall-through was a routing decision written down nowhere,
#: and it was one new flow away from resuming the wrong service.
GATE_RESUMES: dict[str, Callable[[EvalWorld, Any], Any]] = {
    "expense-reimbursement": lambda world, task: world.expenses.resume_for_task(task),
    "accounts-payable": lambda world, task: world.payables.resume_for_task(task),
    # The plant spine's two gates. The other three raise Tasks without
    # suspending — an aggregate interrupts somebody and carries on — so a
    # resume there would answer a question the run was never waiting on.
    "shipping-list-extraction": (
        lambda world, task: world.extraction.resume_for_task(task)
    ),
    "department-scheduling": (
        lambda world, task: world.scheduling.resume_for_task(task)
    ),
}

