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
from .evals import EvalService
from .eval_specs import DeclaredSpecStore
from .graph import GraphService, build_graph_service
from .runs import RunEngine, build_run_engine
from .sync import SyncService, build_sync_service
from .service import KnowledgeService, seed_if_empty
from .storage import build_embedding_repository, build_repository
from .teams import TeamService, build_team_directory
from .usage import AssetUsageStore, build_asset_usage_store
from .users import UserDirectory, build_user_directory
from .flows import FlowService, build_flow_service, seed_flows_if_empty
from .tasks import TaskService, build_task_service, seed_tasks_if_empty
from .reimbursements import (
    ReimbursementService,
    build_receipt_reader,
    build_reimbursement_service,
    seed_reimbursements_if_empty,
)
from .employee import (
    EmployeeService,
    build_employee_service,
    seed_employee_if_empty,
)
from .payables import (
    PayableService,
    build_invoice_reader,
    build_payable_service,
    seed_payables_if_empty,
)

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
_reimbursement_service: ReimbursementService | None = None
_employee_service: EmployeeService | None = None
_payable_service: PayableService | None = None
_graph_service: GraphService | None = None
_document_service: DocumentService | None = None
_run_engine: RunEngine | None = None
_anomaly_service: AnomalyService | None = None
_sync_service: SyncService | None = None
_eval_service: EvalService | None = None
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
            settings.golden_sets_collection,
            settings.test_runs_collection,
        )
        with _lock:
            if _flow_service is None:
                seed_flows_if_empty(service)
                _flow_service = service
    return _flow_service


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
                seed_tasks_if_empty(service)
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
    """The inbound feeds. Registers three flow bodies on the engine."""
    global _sync_service
    if _sync_service is None:
        graph = get_graph_service()
        knowledge = get_service()
        tasks = get_task_service()
        engine = get_run_engine()
        service = build_sync_service(graph, knowledge, tasks, engine)
        with _lock:
            if _sync_service is None:
                _sync_service = service
    return _sync_service


def get_eval_service() -> EvalService:
    """Graph-shaped evals over the declared specs.

    Holds the live corpus only to *copy* it into each eval world: a case runs
    against a frozen, writable copy so a flow that promotes an exemplar cannot
    promote a real one.
    """
    global _eval_service
    if _eval_service is None:
        knowledge = get_service()  # resolved before the lock; not reentrant
        service = EvalService(DeclaredSpecStore(), knowledge=knowledge)
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
                settings.receipt_backend,
                settings.firestore_project,
                settings.answer_location,
                settings.receipt_model,
            )
        except Exception:
            log.exception(
                "Receipt backend unavailable; falling back to the stub reader",
                extra={"backend": settings.receipt_backend},
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
        )
        with _lock:
            if _reimbursement_service is None:
                seed_reimbursements_if_empty(service)
                _reimbursement_service = service
    return _reimbursement_service


def get_payable_service() -> PayableService:
    global _payable_service
    if _payable_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        sync = get_sync_service()
        get_anomaly_service()
        try:
            reader = build_invoice_reader(
                settings.receipt_backend,
                settings.firestore_project,
                settings.answer_location,
                settings.receipt_model,
            )
        except Exception:
            log.exception(
                "Invoice reader backend unavailable; falling back to the stub",
                extra={"backend": settings.receipt_backend},
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
        )
        with _lock:
            if _payable_service is None:
                # The sync service seeds the vendor master, the purchase orders
                # and the receiving reports the invoices match against, so it
                # has to exist before the AP seed runs.
                seed_payables_if_empty(service, sync=sync)
                _payable_service = service
    return _payable_service


def get_employee_service() -> EmployeeService:
    global _employee_service
    if _employee_service is None:
        # Dependencies resolved before the lock: `_lock` is not reentrant.
        knowledge = get_service()
        tasks = get_task_service()
        teams = get_team_service()
        if _answer_backend is _MISSING:
            backend = _build_answer_backend()
            with _lock:
                if globals()["_answer_backend"] is _MISSING:
                    globals()["_answer_backend"] = backend
        service = build_employee_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.employee_collection,
            knowledge,
            tasks,
            answer_backend=_answer_backend,
            teams=teams,
        )
        with _lock:
            if _employee_service is None:
                seed_employee_if_empty(service)
                _employee_service = service
    return _employee_service


def _build_answer_backend():
    """A missing generation backend degrades /ask, it never breaks startup.

    Same posture as _build_embedder: without this the endpoint still answers,
    it just hands back ranked documents instead of prose.
    """
    try:
        return build_answer_backend(
            settings.answer_backend,
            settings.firestore_project,
            settings.answer_location,
            settings.answer_model,
            settings.answer_max_steps,
            settings.answer_max_output_tokens,
        )
    except Exception:
        log.exception(
            "Answer backend unavailable; /ask will return ranked documents",
            extra={"backend": settings.answer_backend},
        )
        return None


def get_answer_service() -> AnswerService:
    global _answer_backend
    # Resolved before the lock: `_lock` is not reentrant and both take it.
    service = get_service()
    teams = get_team_service()
    if _answer_backend is _MISSING:
        backend = _build_answer_backend()
        with _lock:
            if _answer_backend is _MISSING:
                _answer_backend = backend
    return AnswerService(service, teams, _answer_backend, graph=get_graph_service())


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
