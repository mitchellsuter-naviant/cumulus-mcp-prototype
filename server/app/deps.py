"""Singleton wiring shared by REST, MCP, and A2A layers."""

import threading

import logging

from .answering import AnswerService, build_answer_backend
from .api_keys import ApiKeyDirectory, build_api_key_directory
from .config import settings
from .embeddings import build_embedder
from .identities import IdentityService, build_machine_identity_directory
from .anomaly import AnomalyService, build_anomaly_service
from .documents import DocumentService, build_document_service
from .evals import EvalService, FixtureStore, build_fixture_store
from .eval_specs import DeclaredSpecStore, seed_fixtures_if_empty
from .dispatch import Dispatcher, build_dispatcher
from .estate import EstateConnector, build_estate_connector
from .estate_writes import EstateWriter, build_estate_writer
from .gateway import ActionGateway, build_action_gateway
from .graph import GraphService, build_graph_service
from .runs import RunEngine, build_run_engine
from .sync import SyncService, build_sync_service
from .service import KnowledgeService, seed_if_empty
from .storage import build_embedding_repository, build_repository
from .teams import TeamService, build_team_directory
from .usage import AssetUsageStore, build_asset_usage_store
from .users import UserDirectory, build_user_directory
from .flows import FlowService, build_flow_service, seed_flows_if_empty
from .calendars import CalendarService, build_calendar_service
from .queues import QueueService, build_queue_service
from .tasks import TaskService, build_task_service
from .reimbursements import (
    ReimbursementService,
    build_receipt_reader,
    build_reimbursement_service,
)
from .employee import EmployeeService, build_employee_service
from .payables import PayableService, build_invoice_reader, build_payable_service

log = logging.getLogger("cumulus.deps")

# Guards first-use construction: without it two concurrent cold requests can
# both build a service (and both run the seed pass).
_lock = threading.Lock()
_service: KnowledgeService | None = None
_user_directory: UserDirectory | None = None
_api_key_directory: ApiKeyDirectory | None = None
_identity_service: IdentityService | None = None
_team_service: TeamService | None = None
_asset_usage: AssetUsageStore | None = None
_flow_service: FlowService | None = None
_task_service: TaskService | None = None
_queue_service: QueueService | None = None
_reimbursement_service: ReimbursementService | None = None
_employee_service: EmployeeService | None = None
_payable_service: PayableService | None = None
_graph_service: GraphService | None = None
_action_gateway: ActionGateway | None = None
_dispatcher: Dispatcher | None = None
_document_service: DocumentService | None = None
_run_engine: RunEngine | None = None
_anomaly_service: AnomalyService | None = None
_sync_service: SyncService | None = None
_estate_connector: EstateConnector | None = None
_estate_writer: EstateWriter | None = None
_calendar_service: CalendarService | None = None
_eval_service: EvalService | None = None
_fixture_store: FixtureStore | None = None
# The generative backend is expensive to construct (a Vertex client) and holds
# no request state, so it is cached; the AnswerService wrapper around it is
# cheap and is rebuilt per call so it can never hold a stale KnowledgeService.
# `_MISSING` distinguishes "not built yet" from "built, and there is none".
_MISSING = object()
_answer_backend: object = _MISSING


def _build_embedder():
    """Never let a misconfigured encoder stop the service from starting.

    Search without semantics is a degraded service; no search at all is a dead
    one. A bad EMBEDDING_BACKEND, a missing package, or absent credentials all
    land here and fall back to lexical.
    """
    try:
        return build_embedder(
            settings.embedding_backend,
            settings.firestore_project,
            settings.embedding_location,
            settings.embedding_model,
            settings.embedding_dimensions,
            settings.embedding_max_batch,
            settings.embedding_max_input_chars,
            settings.embedding_max_batch_tokens,
        )
    except Exception:
        log.exception(
            "Embedding backend unavailable; search will be lexical only",
            extra={"backend": settings.embedding_backend},
        )
        return None


def get_service() -> KnowledgeService:
    global _service
    if _service is None:
        with _lock:
            if _service is None:
                repo = build_repository(
                    settings.storage_backend,
                    settings.firestore_project,
                    settings.firestore_collection,
                )
                embedder = _build_embedder()
                embedding_repo = (
                    build_embedding_repository(
                        settings.storage_backend,
                        settings.firestore_project,
                        settings.embeddings_collection,
                    )
                    if embedder is not None
                    else None
                )
                service = KnowledgeService(
                    repo,
                    embedder=embedder,
                    embedding_repo=embedding_repo,
                    lexical_weight=settings.hybrid_lexical_weight,
                    semantic_weight=settings.hybrid_semantic_weight,
                    candidates=settings.hybrid_candidates,
                    min_similarity=settings.hybrid_min_similarity,
                )
                seed_if_empty(service, settings.seed_dir)
                _service = service
    return _service


def get_user_directory() -> UserDirectory:
    global _user_directory
    if _user_directory is None:
        with _lock:
            if _user_directory is None:
                _user_directory = build_user_directory(settings.user_backend)
    return _user_directory


def get_api_key_directory() -> ApiKeyDirectory:
    global _api_key_directory
    if _api_key_directory is None:
        with _lock:
            if _api_key_directory is None:
                _api_key_directory = build_api_key_directory(
                    settings.storage_backend,
                    settings.firestore_project,
                    settings.api_keys_collection,
                    settings.api_key_usage_collection,
                )
    return _api_key_directory


def get_identity_service() -> IdentityService:
    global _identity_service
    if _identity_service is None:
        # Both dependencies are resolved before taking the lock: `_lock` is not
        # reentrant, and get_user_directory() takes it. A concurrent cold start
        # may build a second machine directory, but only one is ever kept.
        users = get_user_directory()
        machines = build_machine_identity_directory(
            settings.storage_backend,
            settings.firestore_project,
            settings.identities_collection,
        )
        with _lock:
            if _identity_service is None:
                _identity_service = IdentityService(users, machines)
    return _identity_service


def get_team_service() -> TeamService:
    global _team_service
    if _team_service is None:
        directory = build_team_directory(
            settings.storage_backend,
            settings.firestore_project,
            settings.teams_collection,
        )
        with _lock:
            if _team_service is None:
                _team_service = TeamService(directory)
    return _team_service


def get_flow_service() -> FlowService:
    global _flow_service
    if _flow_service is None:
        service = build_flow_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.flows_collection,
        )
        with _lock:
            if _flow_service is None:
                seed_flows_if_empty(service)
                _flow_service = service
    return _flow_service


def get_queue_service() -> QueueService:
    global _queue_service
    if _queue_service is None:
        service = build_queue_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.queues_collection,
        )
        with _lock:
            if _queue_service is None:
                _queue_service = service
    return _queue_service


def get_task_service() -> TaskService:
    global _task_service
    if _task_service is None:
        service = build_task_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.tasks_collection,
        )
        with _lock:
            if _task_service is None:
                _task_service = service
    return _task_service


def get_graph_service() -> GraphService:
    global _graph_service
    if _graph_service is None:
        service = build_graph_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.graph_objects_collection,
            settings.graph_links_collection,
        )
        with _lock:
            if _graph_service is None:
                _graph_service = service
    return _graph_service


def get_action_gateway() -> ActionGateway:
    """The one chokepoint every decision write passes through.

    One per deployment, holding the same graph everything else reads: a second
    Gateway would be a second ledger, and two ledgers is no ledger.
    """
    global _action_gateway
    if _action_gateway is None:
        graph = get_graph_service()  # resolved before the lock; not reentrant
        gateway = build_action_gateway(
            graph,
            settings.storage_backend,
            settings.firestore_project,
            settings.ledger_collection,
        )
        with _lock:
            if _action_gateway is None:
                _action_gateway = gateway
    return _action_gateway


def get_dispatcher() -> Dispatcher:
    """The one place a run is started, and the one event log.

    One per deployment for the Gateway's reason: a second Dispatcher would be
    a second log, and two logs is no history. It registers itself as the run
    engine's event sink when it is built, so a Flow's emitted Events and the
    facts that started Flows land in the same place.
    """
    global _dispatcher
    if _dispatcher is None:
        engine = get_run_engine()  # resolved before the lock; not reentrant
        dispatcher = build_dispatcher(
            engine,
            settings.storage_backend,
            settings.firestore_project,
            settings.events_collection,
        )
        with _lock:
            if _dispatcher is None:
                _dispatcher = dispatcher
    return _dispatcher


def get_document_service() -> DocumentService:
    global _document_service
    if _document_service is None:
        service = build_document_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.documents_collection,
            settings.document_max_bytes,
        )
        with _lock:
            if _document_service is None:
                _document_service = service
    return _document_service


def get_run_engine() -> RunEngine:
    """The one engine every flow registers its body with.

    Bodies register when their service is constructed, which is why the
    lifespan builds every flow service at startup: a Task decided against a
    flow whose service was never built would have nothing to resume into.
    """
    global _run_engine
    if _run_engine is None:
        # Resolved before the lock: `_lock` is not reentrant.
        tasks = get_task_service()
        engine = build_run_engine(
            settings.storage_backend,
            settings.firestore_project,
            settings.runs_collection,
            tasks,
        )
        with _lock:
            if _run_engine is None:
                _run_engine = engine
    return _run_engine


def get_anomaly_service() -> AnomalyService:
    global _anomaly_service
    if _anomaly_service is None:
        graph = get_graph_service()  # resolved before the lock; not reentrant
        service = build_anomaly_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.vendor_profiles_collection,
            graph,
        )
        with _lock:
            if _anomaly_service is None:
                _anomaly_service = service
    return _anomaly_service


def get_sync_service() -> SyncService:
    """The inbound feeds. Registers ten flow bodies on the engine.

    Five live in `sync.py` and the rest in `sync_commerce`, `sync_office` and
    `sync_dms`, which register through `SyncService.__init__` so this stays the
    one call that puts every feed on the engine.
    """
    global _sync_service
    if _sync_service is None:
        graph = get_graph_service()
        knowledge = get_service()
        tasks = get_task_service()
        engine = get_run_engine()
        gateway = get_action_gateway()
        dispatcher = get_dispatcher()
        service = build_sync_service(
            graph, knowledge, tasks, engine, gateway, dispatcher,
            # Handed in whether or not writes are enabled: a disabled writer is
            # a real object holding no way to reach a network, and the delivery
            # side wants its *journal* regardless — a deployment that wrote
            # last week and had the switch turned off since still has echoes
            # coming home.
            get_estate_writer(),
        )
        with _lock:
            if _sync_service is None:
                _sync_service = service
    return _sync_service


def get_estate_connector() -> EstateConnector:
    """The one cursor per feed, and the one thing here that calls outward.

    Deliberately holds no service: it delivers over HTTP to its own door, so
    it depends on the deployment's URL rather than on `SyncService`. That is
    what keeps the Event ordering honest — see the module docstring.
    """
    global _estate_connector
    if _estate_connector is None:
        connector = build_estate_connector(
            settings.storage_backend,
            settings.firestore_project,
            settings.connector_state_collection,
            estate_project=settings.estate_project,
            url_template=settings.estate_url_template,
            namespace=settings.estate_namespace,
            deliver_url=settings.connector_deliver_url,
            api_key=settings.connector_api_key,
            interval_seconds=settings.estate_pull_interval_seconds,
            chunk_size=settings.estate_chunk_size,
        )
        with _lock:
            if _estate_connector is None:
                _estate_connector = connector
    return _estate_connector


def get_estate_writer() -> EstateWriter:
    """The one component allowed to change a customer's system of record.

    Shares the connector's addresses and nothing else — its own journal, its
    own collection, its own switch. Constructed here even when writes are
    disabled, because a writer that is off is a real object that says so; what
    it does not construct in that case is any way to reach a network.
    """
    global _estate_writer
    if _estate_writer is None:
        writer = build_estate_writer(
            get_estate_connector(),
            settings.storage_backend,
            settings.firestore_project,
            settings.estate_write_journal_collection,
            enabled=settings.estate_writes_enabled,
        )
        with _lock:
            if _estate_writer is None:
                _estate_writer = writer
    return _estate_writer


def get_calendar_service() -> CalendarService:
    """The mirrored schedules, read out of the graph.

    Nothing is constructed but a reader: calendars arrive from the
    `office-calendar` feed, so before that feed has pulled there is no
    schedule to show and the service says so. There used to be a seed here,
    and an ordering dependency on the workforce seed to hang it off.
    """
    global _calendar_service
    if _calendar_service is None:
        service = build_calendar_service(get_graph_service())
        with _lock:
            if _calendar_service is None:
                _calendar_service = service
    return _calendar_service


def get_fixture_store() -> FixtureStore:
    """The worlds cases run in, and the demo seed's fragment beside them.

    Seeded from the declarations in code the way the flow registry is: code is
    the authoring source until a console curates them, and the seeder leaves
    anything not marked `declared` alone so curating one is not undone by the
    next deploy.
    """
    global _fixture_store
    if _fixture_store is None:
        store = build_fixture_store(
            settings.storage_backend,
            settings.firestore_project,
            settings.fixtures_collection,
        )
        with _lock:
            if _fixture_store is None:
                seed_fixtures_if_empty(store)
                _fixture_store = store
    return _fixture_store


def get_eval_service() -> EvalService:
    """Graph-shaped evals over the declared specs.

    Holds the live corpus only to *copy* it into each eval world: a case runs
    against a frozen, writable copy, so nothing a graded run does can reach
    the real shelf.
    """
    global _eval_service
    if _eval_service is None:
        # Resolved before the lock: `_lock` is not reentrant and both take it.
        knowledge = get_service()
        fixtures = get_fixture_store()
        service = EvalService(
            DeclaredSpecStore(), knowledge=knowledge, fixtures=fixtures
        )
        with _lock:
            if _eval_service is None:
                _eval_service = service
    return _eval_service


def get_reimbursement_service() -> ReimbursementService:
    global _reimbursement_service
    if _reimbursement_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        get_run_engine()
        get_graph_service()
        get_document_service()
        try:
            reader = build_receipt_reader(
                settings.model_backend,
                settings.firestore_project,
                settings.model_location,
                settings.model,
            )
        except Exception:
            log.exception(
                "Receipt backend unavailable; falling back to the stub reader",
                extra={"backend": settings.model_backend},
            )
            reader = build_receipt_reader("none", None, "", "")
        service = build_reimbursement_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.reimbursements_collection,
            reader,
            knowledge,
            tasks,
            engine=get_run_engine(),
            graph=get_graph_service(),
            documents=get_document_service(),
            gateway=get_action_gateway(),
            dispatcher=get_dispatcher(),
        )
        with _lock:
            if _reimbursement_service is None:
                _reimbursement_service = service
    return _reimbursement_service


def get_payable_service() -> PayableService:
    global _payable_service
    if _payable_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        get_anomaly_service()
        try:
            reader = build_invoice_reader(
                settings.model_backend,
                settings.firestore_project,
                settings.model_location,
                settings.model,
            )
        except Exception:
            log.exception(
                "Invoice reader backend unavailable; falling back to the stub",
                extra={"backend": settings.model_backend},
            )
            reader = build_invoice_reader("none", None, "", "")
        service = build_payable_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.payables_collection,
            reader,
            knowledge,
            tasks,
            engine=get_run_engine(),
            graph=get_graph_service(),
            documents=get_document_service(),
            anomaly=get_anomaly_service(),
            gateway=get_action_gateway(),
            dispatcher=get_dispatcher(),
        )
        with _lock:
            if _payable_service is None:
                _payable_service = service
    return _payable_service


def get_employee_service() -> EmployeeService:
    global _employee_service
    if _employee_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        teams = get_team_service()
        # No answer backend here any more: the HR agent emits an Event and the
        # `answer-question` Flow owns the call. The hub holds the Dispatcher
        # instead, which is the one thing it needs to start a run.
        service = build_employee_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.employee_collection,
            knowledge,
            tasks,
            get_graph_service(),
            teams=teams,
            dispatcher=get_dispatcher(),
        )
        with _lock:
            if _employee_service is None:
                _employee_service = service
    return _employee_service


def _build_answer_backend():
    """A missing generation backend degrades /ask, it never breaks startup.

    Same posture as _build_embedder: without this the endpoint still answers,
    it just hands back ranked documents instead of prose.
    """
    try:
        return build_answer_backend(
            settings.model_backend,
            settings.firestore_project,
            settings.model_location,
            settings.model,
            settings.answer_max_steps,
            settings.answer_max_output_tokens,
        )
    except Exception:
        log.exception(
            "Answer backend unavailable; /ask will return ranked documents",
            extra={"backend": settings.model_backend},
        )
        return None


def get_answer_service() -> AnswerService:
    """The answering stack, wired to start a run per question.

    Still built per call rather than cached — the backend behind it is cached
    because it holds no request state, but this wrapper closes over the
    knowledge and graph singletons that tests swap out from under it. Building
    it also registers the `answer-question` body on the engine, which is a
    dict assignment and idempotent by construction.
    """
    global _answer_backend
    # Resolved before the lock: `_lock` is not reentrant and both take it.
    service = get_service()
    teams = get_team_service()
    if _answer_backend is _MISSING:
        backend = _build_answer_backend()
        with _lock:
            if _answer_backend is _MISSING:
                _answer_backend = backend
    return AnswerService(
        service, teams, _answer_backend,
        graph=get_graph_service(),
        dispatcher=get_dispatcher(),
        engine=get_run_engine(),
    )


def get_asset_usage() -> AssetUsageStore:
    global _asset_usage
    if _asset_usage is None:
        store = build_asset_usage_store(
            settings.storage_backend,
            settings.firestore_project,
            settings.asset_usage_collection,
        )
        with _lock:
            if _asset_usage is None:
                _asset_usage = store
    return _asset_usage
