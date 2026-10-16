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
from .modelcall import (
    GovernedModelClient,
    ModelCallStore,
    build_modelcall_store,
    vertex_client,
)
from .runs import RunEngine, build_run_engine
from .connectors.sync import SyncService, build_sync_service
from .service import KnowledgeService, seed_if_empty
from .storage import build_embedding_repository, build_repository
from .teams import TeamService, build_team_directory
from .usage import AssetUsageStore, build_asset_usage_store
from .users import UserDirectory, build_user_directory
from .flows import (
    FlowService,
    build_flow_service,
    fleet_model_rates,
    seed_flows_if_empty,
)
from .calendars import CalendarService, build_calendar_service
from .queues import QueueService, build_queue_service
from .task_queue import TaskService, build_task_service
from .tasks.expense_reimbursement.domain import (
    ReimbursementService,
    build_receipt_reader,
    build_reimbursement_service,
)
from .employee import EmployeeService, build_employee_service
from .tasks.accounts_payable.domain import PayableService, build_invoice_reader, build_payable_service
from .tasks.accounts_payable.v8.locate import build_field_locator
from .tasks.ap_corrections.domain import CorrectionService, build_correction_service
from .tasks.ap_intake.domain import ApIntakeService, build_intake_service
from .tasks.credential_tracking.domain import (
    CredentialTrackingService,
    build_credential_tracking_service,
)
from .tasks.department_scheduling.domain import (
    DepartmentSchedulingService,
    build_department_scheduling_service,
)
from .tasks.field_documents.domain import (
    FieldDocumentsService,
    build_field_documents_service,
)
from .tasks.plant_eligibility.domain import (
    PlantEligibilityService,
    build_eligibility_service,
)
from .tasks.shipping_list_extraction.domain import (
    ShippingListExtractionService,
    build_extraction_service,
)
from .tasks.shipping_list_extraction.readers import build_shipping_list_reader

log = logging.getLogger("cumulus.deps")

# Guards first-use construction: without it two concurrent cold requests can
# both build a service (and both run the seed pass).
_lock = threading.Lock()
# The ModelCall ledger gets its own, and this is not tidiness. `_lock` is not
# reentrant, and the embedder — which needs the ledger — is built inside
# `get_service`'s critical section; sharing the lock would deadlock the first
# cold request on a deployment with semantic search enabled.
_modelcall_lock = threading.Lock()
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
_correction_service: CorrectionService | None = None
_ap_intake_service: ApIntakeService | None = None
_credential_tracking_service: CredentialTrackingService | None = None
_field_documents_service: FieldDocumentsService | None = None
_extraction_service: ShippingListExtractionService | None = None
_plant_eligibility_service: PlantEligibilityService | None = None
_department_scheduling_service: DepartmentSchedulingService | None = None
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
_modelcall_store: ModelCallStore | None = None
# The generative backend is expensive to construct (a Vertex client) and holds
# no request state, so it is cached; the AnswerService wrapper around it is
# cheap and is rebuilt per call so it can never hold a stale KnowledgeService.
# `_MISSING` distinguishes "not built yet" from "built, and there is none".
_MISSING = object()
_answer_backend: object = _MISSING


def get_modelcall_store() -> ModelCallStore:
    """The ModelCall ledger: one per deployment, its own collection.

    One, for the curation ledger's reason — a second store would be a second
    account of what the models cost, and two accounts is none.
    """
    global _modelcall_store
    if _modelcall_store is None:
        store = build_modelcall_store(
            settings.storage_backend,
            settings.firestore_project,
            settings.modelcalls_collection,
        )
        with _modelcall_lock:
            if _modelcall_store is None:
                _modelcall_store = store
    return _modelcall_store


def get_model_client() -> GovernedModelClient | None:
    """The governed client every generation call site shares.

    None unless Vertex is actually selected, and that is not a convenience:
    `MODEL_BACKEND=none` is meant to be a deployment that touches google-genai
    not at all — no credential resolved, no connection pool — which is what the
    test suite and every local box run on. Every factory that takes this also
    ignores it on the stub path, so None never reaches a call.

    Cheap when it is built: the expensive part, the google-genai client, is
    cached per configuration inside `modelcall`.
    """
    if settings.model_backend != "vertex":
        return None
    return vertex_client(
        settings.firestore_project,
        settings.model_location,
        store=get_modelcall_store(),
        rates=fleet_model_rates,
    )


def get_embedding_client() -> GovernedModelClient | None:
    """The governed client the encoder uses — the same ledger, a different wire.

    Retrying and serialized, which the generation client must not be: a bulk
    backfill against a quota that answers 429 needs the retries, and the
    backfill thread overlapping a query on the request path needs the lock.
    Serializing the readers and the planner behind that same lock would turn a
    shared client into a shared queue.

    None off the Vertex backend, for `get_model_client`'s reason.
    """
    if settings.embedding_backend != "vertex":
        return None
    return vertex_client(
        settings.firestore_project,
        settings.embedding_location,
        store=get_modelcall_store(),
        rates=fleet_model_rates,
        retry_attempts=5,
        serialize=True,
    )


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
            client=get_embedding_client(),
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
        # The stored definition owns which shipped version is live; the engine
        # asks at start time. A structural attribute (like `emit`), because the
        # flows module imports `runs` and the engine must not import it back.
        # Lazy on both sides: resolved per start, so a version activated from
        # the console takes effect without a restart.
        engine.live_version = _live_flow_version
        with _lock:
            if _run_engine is None:
                _run_engine = engine
    return _run_engine


def _live_flow_version(flow_id: str) -> int:
    defn = get_flow_service().get(flow_id)
    return defn.version if defn is not None else 0


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

    Five live in `connectors/sync.py` and the rest in `connectors/commerce.py`,
    `connectors/office.py` and `connectors/dms.py`, which register through
    `SyncService.__init__` so this stays the one call that puts every feed on
    the engine.
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
        # No fallback. See `get_payable_service` for the whole argument; it is
        # the same one, and a fabricated expense is no better than a
        # fabricated invoice.
        # `expense_model`, not `model`: reading a photographed receipt is a
        # harder job than reading a clean vendor PDF, and the flow package
        # declares this same string in its `extract-receipt` step so the map
        # names the model the run actually calls. See `config.expense_model`.
        reader = build_receipt_reader(
            settings.model_backend,
            settings.firestore_project,
            settings.model_location,
            settings.expense_model,
            client=get_model_client(),
        )
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
            queues=get_queue_service(),
        )
        # Before the reimbursement service is published, for the same reason
        # `get_payable_service` does it: a submitter's correction emits a fact
        # the `ap-corrections` flow answers, and a fact whose response is not
        # registered starts nothing at all — the flywheel would silently stop
        # turning on this side while turning on the other.
        get_correction_service()
        with _lock:
            if _reimbursement_service is None:
                _reimbursement_service = service
    return _reimbursement_service


def get_payable_service() -> PayableService:
    """The AP process, and the one construction that is allowed to kill startup.

    There used to be a `try/except` here that logged a Vertex construction
    failure and substituted the stub reader. The result was a deployment that
    came up healthy, served every route, and answered every invoice with an
    extraction fabricated from a hash of its filename — indistinguishable, in
    the ledger and on the screen, from one a model had read. Nobody would have
    looked, because nothing was broken.

    So it raises, and the lifespan builds this service at startup, so what it
    kills is the *start*. That is deliberately the loudest available failure
    and also the safest one: a revision that will not start never takes
    traffic, so the previous revision — whose extractor did construct — keeps
    serving. The alternative, starting and raising an operations Task on first
    use, buys a working search page at the price of an AP desk that looks open
    and is not, and of one invoice having to be the canary.

    Nothing here is a fallback. `MODEL_BACKEND=none` and `stub` still build the
    stub reader, because that is a *chosen* configuration rather than a
    consolation prize; what is gone is choosing it on the deployment's behalf.
    """
    global _payable_service
    if _payable_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        get_anomaly_service()
        reader = build_invoice_reader(
            settings.model_backend,
            settings.firestore_project,
            settings.model_location,
            settings.model,
            client=get_model_client(),
        )
        # Built here and not lazily, for the reader's reason one line up: a
        # locator that will not construct is a deployment that would draw no
        # boxes, and finding that out on the first disputed invoice is finding
        # it out from a clerk.
        locator = build_field_locator(
            settings.model_backend,
            settings.firestore_project,
            settings.model_location,
            settings.locate_model,
            client=get_model_client(),
        )
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
            locator=locator,
            # The desk registry, so v9 can route a review back to the person
            # who submitted the invoice — `ensure_personal` mints their own
            # desk the first time something routes to it.
            queues=get_queue_service(),
        )
        # Before the payables service is published, because a clerk's
        # correction emits a fact this flow answers, and a fact whose response
        # is not registered starts nothing at all.
        get_correction_service()
        with _lock:
            if _payable_service is None:
                _payable_service = service
    return _payable_service


def get_correction_service() -> CorrectionService:
    """The flywheel's own service: the correction set, and the flow that fills it."""
    global _correction_service
    if _correction_service is None:
        service = build_correction_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.corrections_collection,
            engine=get_run_engine(),
            gateway=get_action_gateway(),
        )
        with _lock:
            if _correction_service is None:
                _correction_service = service
    return _correction_service


def get_ap_intake_service() -> ApIntakeService:
    """The intake flow's service, and the estate connector it fetches through.

    Built after the payables service, deliberately: intake starts
    accounts-payable runs, and a run started against a flow whose body is not
    registered yet has nothing to run.

    The connector is handed in whether or not an estate is registered — an
    unregistered one refuses to fetch and the flow reports that as a count,
    which is a working deployment (a Cumulus with no document system to read
    from) rather than a broken one.
    """
    global _ap_intake_service
    if _ap_intake_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        get_payable_service()
        service = build_intake_service(
            get_graph_service(),
            knowledge,
            get_document_service(),
            tasks,
            engine=get_run_engine(),
            gateway=get_action_gateway(),
            dispatcher=get_dispatcher(),
            fetcher=get_estate_connector(),
        )
        with _lock:
            if _ap_intake_service is None:
                _ap_intake_service = service
    return _ap_intake_service


def get_credential_tracking_service() -> CredentialTrackingService:
    """The compliance sweep's service.

    Needs no document store, no reader and no connector: everything it decides
    from is already in the graph, and everything it decides against is one
    governed asset. It reaches no network at all, which is why there is nothing
    here that could be unregistered.

    Built at startup like every other flow service — a Task decided against a
    flow whose service was never built would have nothing to resume into —
    though this one raises Tasks that resume nothing: a corrected date is a
    `human` claim the next window reads.
    """
    global _credential_tracking_service
    if _credential_tracking_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        service = build_credential_tracking_service(
            get_graph_service(),
            knowledge,
            tasks,
            engine=get_run_engine(),
            gateway=get_action_gateway(),
            dispatcher=get_dispatcher(),
        )
        with _lock:
            if _credential_tracking_service is None:
                _credential_tracking_service = service
    return _credential_tracking_service


def get_field_documents_service() -> FieldDocumentsService:
    """The field-documents window's service.

    Needs no document store, no reader and no connector, for the compliance
    sweep's reason directly above: everything it decides from is already in
    the graph and everything it decides against is one governed asset. It
    fetches no bytes — a packet is which revision is current, not what is
    inside it — so there is nothing here that could be unregistered.

    Built at startup like every other flow service, and here that is not
    optional: its two Triggers sit on `JobRecordsDelivered` and
    `DocumentsDelivered`, which the estate pushes through
    `POST /api/v1/sources/deliver`, and `Dispatcher.emit` does not guard a
    missing body. An unregistered flow would 500 the next delivery for every
    feed rather than fail quietly in one run.
    """
    global _field_documents_service
    if _field_documents_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        service = build_field_documents_service(
            get_graph_service(),
            knowledge,
            tasks,
            engine=get_run_engine(),
            gateway=get_action_gateway(),
            dispatcher=get_dispatcher(),
        )
        with _lock:
            if _field_documents_service is None:
                _field_documents_service = service
    return _field_documents_service


def get_extraction_service() -> ShippingListExtractionService:
    """The shipping-list extraction's service, and the two models it runs on.

    Built before the eligibility sweep, deliberately: the sweep emits one
    `JobEligible` per eligible job and each of those starts an extraction run,
    so a body that is not registered yet has nothing to run — `ap-intake` and
    `accounts-payable` in the same order, for the same reason.

    The reader and the locator are constructed here rather than lazily, for
    `get_payable_service`'s reason: a reader that will not construct is a
    deployment that would read no ticket, and finding that out on the first
    plant window is finding it out from a supervisor. `MODEL_BACKEND=none` and
    `stub` still build the stub reader, because that is a *chosen*
    configuration and not a consolation prize.

    The connector is handed in whether or not an estate is registered — an
    unregistered one refuses to fetch and the flow reports that as a count,
    which is a working deployment (a Cumulus with no document system to read
    from) rather than a broken one.
    """
    global _extraction_service
    if _extraction_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        reader = build_shipping_list_reader(
            settings.model_backend,
            settings.firestore_project,
            settings.model_location,
            settings.model,
            client=get_model_client(),
        )
        locator = build_field_locator(
            settings.model_backend,
            settings.firestore_project,
            settings.model_location,
            settings.locate_model,
            client=get_model_client(),
        )
        service = build_extraction_service(
            get_graph_service(),
            knowledge,
            get_document_service(),
            tasks,
            engine=get_run_engine(),
            gateway=get_action_gateway(),
            reader=reader,
            locator=locator,
            fetcher=get_estate_connector(),
        )
        with _lock:
            if _extraction_service is None:
                _extraction_service = service
    return _extraction_service


def get_plant_eligibility_service() -> PlantEligibilityService:
    """The plant window's service. Writes nothing, so it holds no Gateway.

    Built after the extraction service **and** after the department-scheduling
    one, because both of the facts it emits are answered by them — and built at
    startup like every other flow service, which here is not optional for a
    second reason: `job-eligible` and `department-day-opened` are both declared
    Triggers, and `Dispatcher.emit` does not guard a missing body. An
    unregistered body on either would fail the window that raised it rather
    than fail quietly.
    """
    global _plant_eligibility_service
    if _plant_eligibility_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        get_extraction_service()
        get_department_scheduling_service()
        service = build_eligibility_service(
            get_graph_service(),
            knowledge,
            tasks,
            engine=get_run_engine(),
            dispatcher=get_dispatcher(),
        )
        with _lock:
            if _plant_eligibility_service is None:
                _plant_eligibility_service = service
    return _plant_eligibility_service


def get_department_scheduling_service() -> DepartmentSchedulingService:
    """The flow that fills a plant department's day, and signs it.

    Built **before** the eligibility sweep and not after, which is the same
    dependency rule the extraction follows one function up and matters for the
    same reason: the sweep emits one `DepartmentDayOpened` per department-day
    it opened, `department-day-opened` is a declared Trigger, and
    `Dispatcher.emit` does not guard a missing body. An unregistered scheduling
    flow would fail the window that raised it rather than fail quietly.

    It holds a Gateway, unlike the sweep: this is where the plant spine finally
    writes, and both of its writes — the day at `pending_approval`, and the
    signature that makes it exportable — are guarded Actions.
    """
    global _department_scheduling_service
    if _department_scheduling_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        service = build_department_scheduling_service(
            get_graph_service(),
            knowledge,
            tasks,
            engine=get_run_engine(),
            gateway=get_action_gateway(),
            dispatcher=get_dispatcher(),
        )
        with _lock:
            if _department_scheduling_service is None:
                _department_scheduling_service = service
    return _department_scheduling_service


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
            client=get_model_client(),
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
