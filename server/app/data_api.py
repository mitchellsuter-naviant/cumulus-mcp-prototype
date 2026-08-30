"""REST surface for the data layer's governance views.

Four things the console's Data plane needs, and one write:

- **Collections** — the declared registry, with live counts. Retention, custody
  and authoritativeness live here, not on rows.
- **Documents** — the artifacts, their extracted regions, and what is past its
  retention horizon. Bytes come back only from the explicit `/content` route.
- **Sources** — what each feed has delivered, assembled from the sync runs
  themselves rather than a second bookkeeping store.
- **Vendor profiles** — the derived anomaly baseline, plus a refresh that runs
  the windowed Flow on demand.

The write is `POST /sources/deliver`: a push from an external system. It is the
one endpoint here that an API key *should* be able to call — a feed is a
machine act, the opposite of a Task decision — so it takes a key with writer
authority and refuses nothing else.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from . import data_collections, deps
from .anomaly import VendorProfile
from .auth import Principal, get_principal, require_reader, require_role
from .documents import DocumentRecord, DocumentSummary, Region
from .sync import SourceStatus, SyncRequest

log = logging.getLogger("cumulus.data.api")

router = APIRouter(prefix="/api/v1", tags=["data"])


class CollectionView(BaseModel):
    id: str
    name: str
    summary: str
    role: str
    kind: str
    origin: str
    container: str
    custody: str
    retention: str
    retention_note: str
    authoritative: bool
    legal_hold: bool
    object_types: list[str] = Field(default_factory=list)
    written_by: list[str] = Field(default_factory=list)
    read_by: list[str] = Field(default_factory=list)
    # Live counts, where the console can get them honestly. Null when it
    # cannot — which is different from zero and is shown differently.
    rows: int | None = None
    rows_caption: str = ""
    asset_id: str = "data-collections"


def _live_counts() -> dict[str, tuple[int, str]]:
    """Counts per Collection, from the stores that actually hold the rows."""
    counts: dict[str, tuple[int, str]] = {}
    try:
        assets = deps.get_service().repo.all()
        counts["knowledge-library"] = (len(assets), "documents")
    except Exception:  # noqa: BLE001 — a registry view must not 500
        log.exception("Could not count the corpus")
    try:
        graph_stats = deps.get_graph_service().stats()
        by_type = graph_stats.get("by_type", {})
        for collection in data_collections.COLLECTIONS:
            if not collection.object_types:
                continue
            counts[collection.id] = (
                sum(by_type.get(t, 0) for t in collection.object_types), "objects"
            )
    except Exception:  # noqa: BLE001
        log.exception("Could not count the graph")
    try:
        counts["task-ledger"] = (
            len(deps.get_task_service().directory.list()), "tasks"
        )
    except Exception:  # noqa: BLE001
        log.exception("Could not count tasks")
    try:
        counts["audit-documents"] = (
            deps.get_document_service().stats()["documents"], "artifacts"
        )
    except Exception:  # noqa: BLE001
        log.exception("Could not count documents")
    try:
        counts["vendor-profiles"] = (
            deps.get_anomaly_service().stats()["vendors_profiled"], "profiles"
        )
    except Exception:  # noqa: BLE001
        log.exception("Could not count profiles")
    try:
        counts["flow-definitions"] = (
            len(deps.get_flow_service().list()), "flows"
        )
    except Exception:  # noqa: BLE001
        log.exception("Could not count flows")
    return counts


@router.get("/collections", response_model=list[CollectionView])
def list_collections(
    role: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> list[CollectionView]:
    require_reader(principal)
    counts = _live_counts()
    out: list[CollectionView] = []
    for collection in data_collections.COLLECTIONS:
        if role is not None and collection.role != role:
            continue
        rows, caption = counts.get(collection.id, (None, ""))
        out.append(CollectionView(
            id=collection.id, name=collection.name, summary=collection.summary,
            role=collection.role, kind=collection.kind, origin=collection.origin,
            container=collection.container, custody=collection.custody,
            retention=collection.retention,
            retention_note=collection.retention_note,
            authoritative=collection.authoritative,
            legal_hold=collection.legal_hold,
            object_types=list(collection.object_types),
            written_by=list(collection.written_by),
            read_by=list(collection.read_by),
            rows=rows, rows_caption=caption,
        ))
    return out


@router.get("/collections/{collection_id}", response_model=CollectionView)
def get_collection(
    collection_id: str, principal: Principal = Depends(get_principal)
) -> CollectionView:
    require_reader(principal)
    for view in list_collections(role=None, principal=principal):
        if view.id == collection_id:
            return view
    raise HTTPException(
        status_code=404, detail=f"Collection not found: {collection_id}"
    )


# ---- documents -------------------------------------------------------------


@router.get("/documents", response_model=list[DocumentSummary])
def list_documents(
    kind: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> list[DocumentSummary]:
    require_reader(principal)
    return deps.get_document_service().list(kind=kind, run_id=run_id)


@router.get("/documents/stats")
def document_stats(principal: Principal = Depends(get_principal)) -> dict:
    require_reader(principal)
    return deps.get_document_service().stats()


@router.get("/documents/expiring", response_model=list[DocumentSummary])
def expiring_documents(
    principal: Principal = Depends(get_principal),
) -> list[DocumentSummary]:
    """Past their Collection's retention horizon.

    Reported, not swept: the policy is declared and computed, the purge pass is
    not built, and saying so is better than implying otherwise.
    """
    require_reader(principal)
    return deps.get_document_service().expiring()


@router.get("/documents/{document_id}", response_model=DocumentRecord)
def get_document(
    document_id: str,
    include_bytes: bool = Query(default=False),
    principal: Principal = Depends(get_principal),
) -> DocumentRecord:
    require_reader(principal)
    record = deps.get_document_service().get(document_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {document_id}")
    if not include_bytes:
        # The regions are the interesting part for a reviewer; the bytes are
        # megabytes, so they are opt-in rather than free.
        return record.model_copy(update={"content_b64": ""})
    return record


@router.get("/documents/{document_id}/regions", response_model=list[Region])
def get_document_regions(
    document_id: str, principal: Principal = Depends(get_principal)
) -> list[Region]:
    require_reader(principal)
    record = deps.get_document_service().get(document_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {document_id}")
    return record.regions


@router.get("/documents/{document_id}/content")
def get_document_content(
    document_id: str, principal: Principal = Depends(get_principal)
) -> Response:
    """The artifact itself, for the review screen to draw boxes over."""
    require_reader(principal)
    import base64

    record = deps.get_document_service().get(document_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {document_id}")
    if not record.content_b64:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Document {document_id} has no stored bytes (it was a "
                "hint-driven run)"
            ),
        )
    return Response(
        content=base64.b64decode(record.content_b64),
        media_type=record.media_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


# ---- sources ---------------------------------------------------------------


@router.get("/sources", response_model=list[SourceStatus])
def list_sources(
    principal: Principal = Depends(get_principal),
) -> list[SourceStatus]:
    require_reader(principal)
    return deps.get_sync_service().sources()


@router.post("/sources/deliver", status_code=202)
def deliver(
    body: SyncRequest, principal: Principal = Depends(get_principal)
) -> dict:
    """A push from an external system.

    Writer authority, and API keys are welcome: a feed is a machine act. Pass
    `idempotency_key` and a retried delivery re-enters the same run, replays
    its steps and changes nothing — which is the property that makes an
    at-least-once transport safe to point at this.
    """
    require_role(principal, "admin")
    outcomes = deps.get_sync_service().deliver(body, actor=principal.uid)
    log.info(
        "Source delivery accepted",
        extra={
            "source": body.source, "actor": principal.uid,
            "runs": [o.get("run_id") for o in outcomes],
        },
    )
    return {"source": body.source, "runs": outcomes}


@router.post("/identity/sweep", status_code=202)
def sweep_identities(
    window: str = Query(default="", max_length=200),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Close an identity window: score the counterparty book against itself.

    Safe to call at any time, and for a stronger reason than the profile
    refresh below is: this one changes no canonical object at all. It writes
    proposals at `in_review` — visible, queryable, and refused by the Gateway
    until a steward confirms them — so the worst a spurious sweep costs is the
    pass itself and a few edges a human can withdraw.

    Admin, because it raises work into somebody's queue.
    """
    require_role(principal, "admin")
    outcome = deps.get_sync_service().sweep_identities(
        actor=principal.uid, window=window,
    )
    log.info(
        "Identity sweep run",
        extra={
            "actor": principal.uid, "runId": outcome.get("run_id"),
            "proposed": outcome.get("proposed"),
            "stewardTasks": outcome.get("steward_tasks"),
        },
    )
    return outcome


# ---- the derived anomaly baseline -----------------------------------------


@router.get("/vendor-profiles", response_model=list[VendorProfile])
def list_vendor_profiles(
    principal: Principal = Depends(get_principal),
) -> list[VendorProfile]:
    require_reader(principal)
    profiles = deps.get_anomaly_service().store.list()
    return sorted(profiles, key=lambda p: p.invoices, reverse=True)


@router.post("/vendor-profiles/refresh")
def refresh_vendor_profiles(
    window_days: int = Query(default=365, ge=1, le=3650),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Run the windowed profile Flow now.

    Safe to call at any time because the output is derived: it is recomputed
    from the invoice graph, so the worst a spurious refresh costs is the pass
    itself.
    """
    require_role(principal, "admin")
    profiles = deps.get_anomaly_service().rebuild(window_days)
    log.info(
        "Vendor profiles refreshed via API",
        extra={"vendors": len(profiles), "actor": principal.uid},
    )
    return {
        "vendors_profiled": len(profiles),
        "invoices_covered": sum(p.invoices for p in profiles),
        "window_days": window_days,
    }
