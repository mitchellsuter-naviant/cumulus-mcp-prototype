"""Singleton wiring shared by REST, MCP, and A2A layers."""

import threading

import logging

from .answering import AnswerService, build_answer_backend
from .api_keys import ApiKeyDirectory, build_api_key_directory
from .config import settings
from .embeddings import build_embedder
from .identities import IdentityService, build_machine_identity_directory
from .service import KnowledgeService, seed_if_empty
from .storage import build_embedding_repository, build_repository
from .teams import TeamService, build_team_directory
from .usage import AssetUsageStore, build_asset_usage_store
from .users import UserDirectory, build_user_directory
from .workflows import WorkflowService, build_workflow_service, seed_workflows_if_empty

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
_workflow_service: WorkflowService | None = None
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


def get_workflow_service() -> WorkflowService:
    global _workflow_service
    if _workflow_service is None:
        service = build_workflow_service(
            settings.storage_backend,
            settings.firestore_project,
            settings.workflows_collection,
            settings.golden_sets_collection,
            settings.test_runs_collection,
        )
        with _lock:
            if _workflow_service is None:
                seed_workflows_if_empty(service)
                _workflow_service = service
    return _workflow_service


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
    return AnswerService(service, teams, _answer_backend)


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
