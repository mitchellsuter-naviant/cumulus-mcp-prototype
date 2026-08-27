"""REST admin API used by the Flutter app (and any other system integration).

Every route requires an authenticated principal. Reads need any role (`user`+).
Writes need either the platform `admin` role, or admin of the team that owns
the asset — see teams.py for why authority has those two axes.
"""

import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import (
    Principal,
    get_principal,
    is_team_admin,
    require_reader,
    require_role,
)
from .deps import (
    get_answer_service,
    get_asset_usage,
    get_service,
    get_team_service,
)
from .models import (
    Asset,
    AssetCreate,
    AssetFilter,
    AssetKind,
    AssetSummary,
    AssetUpdate,
    AskAnswer,
    AskRequest,
    AuthorityLevel,
    SearchFacets,
    SearchHit,
    SearchSort,
    Status,
)
from .service import AssetExistsError, AssetInvalidError, AssetNotFoundError
from .usage import CITED, READ, RETRIEVED, AssetUsageSummary

log = logging.getLogger("cumulus.rest")

router = APIRouter(prefix="/api/v1", tags=["assets"])


def _require_owner_team(owner: str | None) -> None:
    """An owner string must name a real team, or be empty.

    `Asset.owner` is the team slug (see teams.py). Letting it hold free text
    again would quietly reintroduce ownership that resolves to nothing.
    """
    if not owner:
        return
    if get_team_service().get(owner) is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown owning team: '{owner}'. Create the team first, or "
                "leave the owner empty."
            ),
        )


def _require_can_write(principal: Principal, owner: str | None) -> None:
    """Platform admins write anywhere; a team admin writes what their team owns.

    This is the self-service half of the two-axis model: a plain `user` who
    administers `platform-eng` may edit that team's documents and nothing else.
    """
    if principal.outranks_or_is("admin"):
        return
    if owner and is_team_admin(principal, owner):
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "Requires admin role, or admin of the team that owns this asset"
        ),
    )


@router.get("/assets", response_model=list[AssetSummary])
def list_assets(
    prefix: str | None = None,
    status: str | None = None,
    kind: AssetKind | None = None,
    principal: Principal = Depends(get_principal),
) -> list[AssetSummary]:
    require_reader(principal)
    return [
        a.summary()
        for a in get_service().list_assets(prefix=prefix, status=status)
        if kind is None or a.kind == kind
    ]


@router.get("/assets/tree")
def asset_tree(principal: Principal = Depends(get_principal)) -> dict:
    require_reader(principal)
    return get_service().tree()


@router.get("/refs/{ref:path}")
def resolve_ref(ref: str, principal: Principal = Depends(get_principal)) -> dict:
    """Resolve a read-set-namespace reference (`knowledge:{id}@rev{n}`).

    This is the seam that joins the two halves of the prototype: pinned
    read-sets, task refs, and run journals all speak in these strings, and
    this endpoint dereferences them. Only the `knowledge:` namespace has a
    store today; `vendor:`/`graph:`/`po:` answer honestly that theirs lands
    with the entity store. A requested revision older than the current one
    resolves to the current document with `pinned_available: false` — the
    counter is real, retained history is not yet.
    """
    require_reader(principal)
    match = re.fullmatch(r"([a-z]+):([a-z0-9\-]+)(?:@rev(\d+)|@(\d{4}))?", ref)
    if match is None:
        raise HTTPException(status_code=422, detail=f"Unparseable ref: {ref}")
    namespace, ref_id, rev, year = match.groups()
    requested = int(rev) if rev else None
    if namespace != "knowledge":
        return {
            "ref": ref,
            "namespace": namespace,
            "id": ref_id,
            "resolved": False,
            "note": (
                f"No store answers the '{namespace}:' namespace yet — it "
                "lands with the entity graph."
            ),
        }
    asset = get_service().repo.get(ref_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Unknown knowledge ref: {ref}")
    return {
        "ref": ref,
        "namespace": namespace,
        "id": ref_id,
        "resolved": True,
        "requested_revision": requested,
        "current_revision": asset.revision,
        "pinned_available": requested is None or requested == asset.revision,
        "note": (
            ""
            if requested is None or requested == asset.revision
            else (
                f"Revision {requested} is not retained; serving the current "
                f"rev{asset.revision}. Retained history is the next seam."
            )
        ),
        "asset": asset.summary().model_dump(mode="json"),
    }


@router.get("/assets/{asset_id}", response_model=Asset)
def get_asset(
    asset_id: str, principal: Principal = Depends(get_principal)
) -> Asset:
    require_reader(principal)
    try:
        asset = get_service().get(asset_id)
    except AssetNotFoundError:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")
    # Recorded here rather than in KnowledgeService.get, which is also called
    # by the answer planner, the context view and supersession checks. Counting
    # those would measure the plumbing rather than what anyone consumed.
    get_asset_usage().record(asset_id, READ, "rest", principal.uid)
    return asset


@router.get("/assets/{asset_id}/context")
def get_asset_context(
    asset_id: str, principal: Principal = Depends(get_principal)
) -> dict:
    require_reader(principal)
    try:
        return get_service().context(asset_id)
    except AssetNotFoundError:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")


@router.post("/assets", response_model=Asset, status_code=201)
def create_asset(
    payload: AssetCreate, principal: Principal = Depends(get_principal)
) -> Asset:
    _require_owner_team(payload.owner)
    _require_can_write(principal, payload.owner)
    try:
        asset = get_service().create(payload)
    except AssetExistsError:
        raise HTTPException(
            status_code=409, detail=f"Asset already exists: {payload.id}"
        )
    log.info(
        "Asset created",
        extra={"assetId": asset.id, "path": asset.path, "actor": principal.uid},
    )
    return asset


@router.put("/assets/{asset_id}", response_model=Asset)
def update_asset(
    asset_id: str, payload: AssetUpdate, principal: Principal = Depends(get_principal)
) -> Asset:
    service = get_service()
    try:
        current = service.get(asset_id)
    except AssetNotFoundError:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")
    if current.kind == "schema-export":
        # Regenerated from Python on every startup — an edit here would be
        # silently overwritten, which is worse than a refusal with the fix.
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{asset_id}' is generated from code and republished on every "
                "startup; edit the declaration in server/app/ instead."
            ),
        )
    # Both ends of a move are gated: you must be able to write the asset where
    # it is, and to write it where it is going.
    _require_can_write(principal, current.owner)
    if payload.owner is not None and payload.owner != current.owner:
        _require_owner_team(payload.owner)
        _require_can_write(principal, payload.owner)
    try:
        asset = service.update(asset_id, payload)
    except AssetNotFoundError:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")
    except AssetInvalidError as exc:
        # Same status POST returns for the same bad value.
        raise HTTPException(status_code=422, detail=exc.errors)
    log.info(
        "Asset updated",
        extra={
            "assetId": asset.id,
            "actor": principal.uid,
            "fields": sorted(payload.model_dump(exclude_unset=True)),
        },
    )
    return asset


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: str, principal: Principal = Depends(get_principal)) -> None:
    service = get_service()
    try:
        current = service.get(asset_id)
        if current.kind == "schema-export":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{asset_id}' is generated from code; deleting it here "
                    "only lasts until the next startup republishes it."
                ),
            )
        _require_can_write(principal, current.owner)
        service.delete(asset_id)
    except AssetNotFoundError:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")
    # Otherwise a future asset reusing this id would inherit its predecessor's
    # traffic, and the unused report would quietly under-count.
    get_asset_usage().forget(asset_id)
    log.info("Asset deleted", extra={"assetId": asset_id, "actor": principal.uid})


@router.get("/assets/{asset_id}/usage", response_model=AssetUsageSummary)
def asset_usage(
    asset_id: str,
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
) -> AssetUsageSummary:
    """Reads and citations for one document over a window."""
    require_reader(principal)
    try:
        get_service().get(asset_id)
    except AssetNotFoundError:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")
    return get_asset_usage().usage(asset_id, days)


@router.get("/usage/assets", response_model=list[AssetUsageSummary])
def corpus_usage(
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
) -> list[AssetUsageSummary]:
    """Usage for every asset, including the ones with none.

    Zero-filled deliberately. Assets with no traffic have no rollup documents,
    so returning only what the store holds would omit exactly the documents the
    dead-weight report exists to find — the absence is the finding.

    One call serves the list column, the corpus stats and the unused report
    together, rather than making the console issue a request per row.
    """
    require_reader(principal)
    recorded = get_asset_usage().usage_all(days)
    return [
        recorded.get(asset.id, AssetUsageSummary(asset_id=asset.id))
        for asset in get_service().list_assets()
    ]


@router.get("/search", response_model=list[SearchHit])
def search(
    q: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
    include_drafts: bool = False,
    mode: Literal["hybrid", "lexical", "semantic"] = Query(
        default="hybrid",
        description=(
            "hybrid fuses BM25 and vector rankings; lexical and semantic force "
            "a single retriever. hybrid degrades to lexical when embeddings are "
            "unavailable."
        ),
    ),
    owner: str | None = Query(
        default=None, description="Restrict to assets owned by this team slug."
    ),
    authority_level: AuthorityLevel | None = Query(default=None),
    kind: AssetKind | None = Query(
        default=None,
        description=(
            "Restrict by asset kind, so governed-context retrieval and "
            "exemplar lookup cannot cross."
        ),
    ),
    status: Status | None = Query(default=None),
    tag: str | None = Query(default=None),
    path_prefix: str | None = Query(
        default=None, description="Restrict to a taxonomy subtree."
    ),
    sort: SearchSort = Query(
        default="relevance",
        description=(
            "Applied to the page after ranking, so it reorders the best "
            "matches rather than selecting different ones."
        ),
    ),
    principal: Principal = Depends(get_principal),
) -> list[SearchHit]:
    """Ranked hits, optionally narrowed by metadata before anything is scored.

    The filter arguments are the structured plane of search. They are applied
    to the candidate set rather than to the results, so `owner=hr&limit=10`
    returns the ten best HR documents, not whichever of the ten best documents
    happen to be HR's.
    """
    require_reader(principal)
    filters = AssetFilter(
        owner=owner,
        authority_level=authority_level,
        kind=kind,
        status=status,
        tag=tag,
        path_prefix=path_prefix,
    )
    hits = get_service().search(
        q,
        limit=limit,
        include_drafts=include_drafts,
        mode=mode,
        filters=None if filters.is_empty() else filters,
        sort=sort,
    )
    # Impressions only for hits that stand out from the ranking. Counting the
    # weak tail would hand every document an impression on every search, and
    # the metric would track how busy the service is rather than which
    # documents actually surface as answers.
    usage = get_asset_usage()
    for hit in hits:
        if not hit.weak:
            usage.record(hit.asset.id, RETRIEVED, "rest", principal.uid)
    return hits


@router.get("/search/facets", response_model=SearchFacets)
def search_facets(
    q: str = Query(default="", description="Blank counts the whole corpus."),
    include_drafts: bool = False,
    owner: str | None = Query(default=None),
    authority_level: AuthorityLevel | None = Query(default=None),
    kind: AssetKind | None = Query(default=None),
    status: Status | None = Query(default=None),
    tag: str | None = Query(default=None),
    path_prefix: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> SearchFacets:
    """Counts for refining a search, one dimension at a time.

    Separate from `/search` rather than folded into it so the hit list keeps
    its shape for the MCP tools and every other existing caller.
    """
    require_reader(principal)
    filters = AssetFilter(
        owner=owner,
        authority_level=authority_level,
        kind=kind,
        status=status,
        tag=tag,
        path_prefix=path_prefix,
    )
    return get_service().search_facets(
        q,
        include_drafts=include_drafts,
        filters=None if filters.is_empty() else filters,
    )


@router.post("/ask", response_model=AskAnswer)
def ask(
    request: AskRequest,
    principal: Principal = Depends(get_principal),
) -> AskAnswer:
    """Answer a question against the corpus, with citations.

    Distinct from `/search` in kind, not just in presentation. Search ranks
    documents by similarity to a query; this plans over both the text and the
    metadata planes, so it can answer "which assets does the HR team manage" —
    a question whose answer is in the `owner` field and appears in no
    document's text.

    Degrades rather than fails: with `ANSWER_BACKEND=none` it returns the
    ranked documents and says so in `backend`, so callers can always render
    something honest.
    """
    require_reader(principal)
    answer = get_answer_service().ask(
        request.question,
        scope_asset_id=request.scope_asset_id,
        include_drafts=request.include_drafts,
    )
    # A citation is the strongest usage signal there is: the document did not
    # merely match, it backed a claim in an answer.
    #
    # Only when an answer was actually generated. The retrieval-only fallback
    # returns its top hits *as* citations, so counting those would record one
    # citation per document per question — a search impression wearing the
    # label of the strongest signal in the system, quietly poisoning the very
    # metric the dead-weight report depends on.
    if answer.backend != "retrieval":
        for citation in answer.citations:
            get_asset_usage().record(
                citation.asset_id, CITED, "rest", principal.uid
            )
    log.info(
        "Question answered",
        extra={
            "actor": principal.uid,
            "backend": answer.backend,
            "steps": len(answer.steps),
            "citations": len(answer.citations),
        },
    )
    return answer


@router.get("/ask/status")
def ask_status(principal: Principal = Depends(get_principal)) -> dict:
    """Whether questions get a generated answer or ranked documents."""
    require_reader(principal)
    return get_answer_service().status()


@router.get("/search/status")
def search_status(principal: Principal = Depends(get_principal)) -> dict:
    """What the retrieval stack is doing: mode, model, and embedding coverage."""
    require_reader(principal)
    return get_service().retrieval_status()


@router.post("/search/reindex")
def search_reindex(principal: Principal = Depends(get_principal)) -> dict:
    """Re-embed whatever has drifted from its stored vector.

    Synchronous and idempotent. Only documents whose text or model changed are
    sent to the encoder, so calling this when nothing has changed is free —
    which is what makes it safe to reach for after a model or weight change.
    """
    require_role(principal, "admin")
    service = get_service()
    service.reindex()
    result = service.warm_embeddings()
    log.info("Search reindex requested", extra={"actor": principal.uid, **result})
    return result
