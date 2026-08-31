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
"""

import logging
from typing import Any, Callable

from pydantic import BaseModel, Field

from .anomaly import AnomalyService, InMemoryProfileStore
from .answering import AnswerService
from .calendars import CalendarService
from .dispatch import Dispatcher, InMemoryEventLog
from .documents import DocumentService, InMemoryDocumentStore
from .gateway import ActionGateway, InMemoryLedgerStore
from .graph import GraphService, InMemoryGraphStore
from .runs import InMemoryRunStore, RunEngine
from .service import KnowledgeService
from .storage import InMemoryRepository
from .sync import SyncService
from .tasks import InMemoryTaskDirectory, TaskService

log = logging.getLogger("cumulus.eval_world")


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
    calendars: Any = None
    #: The answering stack, with no generative backend — building it is what
    #: registers the `answer-question` body, so every world can start the flow
    #: a question becomes. A world whose backend is None still runs it and
    #: still records that a question was asked; what it does not record is a
    #: model call, because none was made.
    answers: Any = None
    knowledge_revisions: dict[str, int] = Field(default_factory=dict)


def build_world(source_knowledge=None, *, seed_dir: str | None = None) -> EvalWorld:
    """An isolated world, with the corpus copied in rather than shared.

    Copied rather than shared: a graded run reads governed knowledge and may
    write it, and neither should reach the corpus the console is showing. The
    revisions copied are recorded, so two results are only compared when they
    read the same knowledge.
    """
    # Deferred: the flow modules import this one for their seed helpers.
    from .payables import (
        InMemoryPayableDirectory,
        PayableService,
        StubInvoiceReader,
    )
    from .platform_assets import publish_generated_assets
    from .reimbursements import (
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
    sync = SyncService(graph, knowledge, tasks, engine, gateway, dispatcher)
    expenses = ReimbursementService(
        InMemoryReimbursementDirectory(), StubReceiptReader(), knowledge, tasks,
        engine, graph, documents, gateway, dispatcher,
    )
    payables = PayableService(
        InMemoryPayableDirectory(), StubInvoiceReader(), knowledge, tasks,
        engine, graph, documents, anomaly, gateway, dispatcher,
    )
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
        anomaly=anomaly, sync=sync,
        expenses=expenses, payables=payables, calendars=calendars,
        answers=answers,
        knowledge_revisions=revisions,
    )


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

# How a case's trigger reaches its flow. Kept as an explicit table rather than
# reflection: a flow that wants to be evaluated has to say how it is started,
# and a typo in a case's `trigger` should be an error rather than a silent
# no-op.
FLOW_ENTRYPOINTS: dict[str, Callable[[EvalWorld, dict], Any]] = {
    "expense-reimbursement": lambda world, trigger: world.expenses.submit(**trigger),
    "accounts-payable": lambda world, trigger: world.payables.submit(**trigger),
    # The first graded flow with no case to submit. Its trigger is a window
    # closing over the world the fixture's deliveries built, which is why its
    # `trigger` dict is usually empty: the precondition *is* the fixture.
    "identity-sweep": lambda world, trigger: world.sync.sweep_identities(**trigger),
}

