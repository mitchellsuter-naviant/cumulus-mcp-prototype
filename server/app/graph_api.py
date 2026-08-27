"""REST surface for the entity layer: objects, links, the ontology, analytics.

Reads only. Nothing writes to the graph through this API on purpose — objects
and links are written by Flows, through Actions, which is the doctrine's rule
that reaching the data layer is a question of route rather than permission. A
console that could POST an object would be a console that could invent a
vendor with no run behind it.

The one endpoint that is not a plain read is `/graph/employees/{email}/expenses`:
it is a *traversal*, and it exists because that question ("how much has this
person submitted in six months") is the whole reason the graph exists. It is a
two-hop walk from a known root, which is what this store is good at, and it is
declared here rather than assembled in the client so the shape of the question
stays reviewable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import deps
from .auth import Principal, get_principal, require_reader
from .graph import Claim, ObjectRecord, TBoxViolation, normalize_key, object_id
from .models import utcnow
from .ontology import ONTOLOGY

log = logging.getLogger("cumulus.graph.api")

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])


class ObjectSummary(BaseModel):
    id: str
    type: str
    label: str
    natural_key: str
    status: str
    props: dict
    revision: int
    updated_at_iso: str
    ref: str
    claim_count: int
    conflict_fields: list[str] = Field(default_factory=list)


class NeighborEdge(BaseModel):
    link_id: str
    type: str
    direction: str
    confidence: float
    method: str
    props: dict
    live: bool
    other: ObjectSummary


class ObjectDetail(BaseModel):
    object: ObjectSummary
    claims: list[Claim]
    conflicts: dict[str, list[Claim]]
    neighbors: list[NeighborEdge]


class EntityHit(BaseModel):
    """One search result. `match` says which handle the query caught —
    label, natural key, id — and `via` is set when the hit was reached
    through an identity Link: typing an ERP id finds the canonical entity,
    which is the MDM mapping made searchable."""

    entity: ObjectSummary
    match: str
    via: str = ""


class LinkEndpoint(BaseModel):
    id: str
    type: str
    label: str
    ref: str


class LinkRow(BaseModel):
    """One Link as an auditable row: both endpoints, the identity metadata
    (confidence, method), liveness, and the run/step that wrote it."""

    id: str
    type: str
    from_entity: LinkEndpoint
    to_entity: LinkEndpoint
    confidence: float
    method: str
    props: dict
    live: bool
    reversal_reason: str = ""
    run_id: str = ""
    step_id: str = ""


class OntologyObjectType(BaseModel):
    name: str
    label: str
    summary: str
    natural_key: str
    authoritative: bool
    collection: str
    fields: list[dict]
    instances: int = 0
    # Derived from compositional links, never declared: a subordinate type is
    # identified through its owner and folds under it in every view.
    owner_type: str = ""
    part_types: list[str] = Field(default_factory=list)
    # Effective filter facets — a part inherits its owner's.
    tags: list[str] = Field(default_factory=list)
    # `{label}` substitutes the selected instance's label.
    competency_questions: list[str] = Field(default_factory=list)


class OntologyLinkType(BaseModel):
    name: str
    nature: str = "association"  # association | composition | identity
    from_type: str
    to_type: str
    cardinality: str
    summary: str
    identity: bool
    instances: int = 0
    # True when the cardinality is actually constrained on write, as opposed
    # to many-to-many, which is declared deliberately where a second edge is
    # legitimate rather than merely unobserved.
    enforced: bool = False


class OntologyView(BaseModel):
    objects: list[OntologyObjectType]
    links: list[OntologyLinkType]
    precedence: list[str]
    asset_id: str


class VendorView(BaseModel):
    """A vendor as the AP workspace needs it: identity, sources, exemplar."""

    id: str
    label: str
    slug: str
    status: str
    tax_id: str = ""
    payment_terms: str = ""
    aliases: list[str] = Field(default_factory=list)
    source_records: list[dict] = Field(default_factory=list)
    invoices: int = 0
    invoiced_usd: float = 0.0
    purchase_orders: int = 0
    has_exemplar: bool = False
    exemplar_ref: str = ""
    conflict_fields: list[str] = Field(default_factory=list)
    revision: int = 1
    ref: str = ""


class ExpenseRollup(BaseModel):
    """The answer to the analytical question, with the path that produced it."""

    employee: str
    employee_object_id: str = ""
    window_days: int
    requests: int = 0
    lines: int = 0
    requested_usd: float = 0.0
    approved_usd: float = 0.0
    by_category: dict[str, float] = Field(default_factory=dict)
    traversal: list[str] = Field(default_factory=list)
    detail: list[dict] = Field(default_factory=list)


def _summary(record: ObjectRecord) -> ObjectSummary:
    return ObjectSummary(
        id=record.id, type=record.type, label=record.label,
        natural_key=record.natural_key, status=record.status, props=record.props,
        revision=record.revision, updated_at_iso=record.updated_at_iso,
        ref=record.ref(), claim_count=len(record.claims),
        conflict_fields=sorted(record.conflicts().keys()),
    )


@router.get("/stats")
def graph_stats(principal: Principal = Depends(get_principal)) -> dict:
    require_reader(principal)
    return deps.get_graph_service().stats()


class InvariantReport(BaseModel):
    """TBox conformance over the whole graph.

    The universal tier of testing: these hold for every object and edge, so no
    case has to declare them, and they catch the shape of bug a field assertion
    never sees — a second vendor minted for one company, a line with no parent,
    an edge pointing at the wrong type.
    """

    ok: bool
    violations: list[TBoxViolation] = Field(default_factory=list)
    objects_checked: int = 0
    links_checked: int = 0
    by_kind: dict[str, int] = Field(default_factory=dict)
    strict_writes: bool = True


@router.get("/invariants", response_model=InvariantReport)
def invariants(
    principal: Principal = Depends(get_principal),
) -> InvariantReport:
    require_reader(principal)
    graph = deps.get_graph_service()
    violations = graph.check_invariants()
    by_kind: dict[str, int] = {}
    for violation in violations:
        by_kind[violation.kind] = by_kind.get(violation.kind, 0) + 1
    report = InvariantReport(
        ok=not violations,
        violations=violations,
        objects_checked=len(graph.store.objects()),
        links_checked=sum(1 for link in graph.store.links() if link.live),
        by_kind=by_kind,
        strict_writes=graph.strict,
    )
    if violations:
        log.warning(
            "Graph invariants violated",
            extra={"violations": len(violations), "byKind": by_kind},
        )
    return report


@router.get("/ontology", response_model=OntologyView)
def ontology(principal: Principal = Depends(get_principal)) -> OntologyView:
    """The declared ontology, with live instance counts beside each type.

    Declaration and reality in one payload: a type with zero instances is a
    promise nothing has kept yet, and that is worth being able to see.
    """
    require_reader(principal)
    stats = deps.get_graph_service().stats()
    by_type = stats.get("by_type", {})
    by_link = stats.get("by_link_type", {})
    return OntologyView(
        objects=[
            OntologyObjectType(
                name=o.name, label=o.label, summary=o.summary,
                natural_key=o.natural_key, authoritative=o.authoritative,
                collection=o.collection,
                fields=[
                    {"name": f.name, "kind": f.kind, "note": f.note}
                    for f in o.fields
                ],
                instances=by_type.get(o.name, 0),
                owner_type=ONTOLOGY.owner_of(o.name) or "",
                part_types=ONTOLOGY.parts_of(o.name),
                tags=list(ONTOLOGY.effective_tags(o.name)),
                competency_questions=list(o.competency_questions),
            )
            for o in ONTOLOGY.objects
        ],
        links=[
            OntologyLinkType(
                name=linked.name, nature=linked.nature,
                from_type=linked.from_type,
                to_type=linked.to_type, cardinality=linked.cardinality,
                summary=linked.summary, identity=linked.identity,
                instances=by_link.get(linked.name, 0),
                enforced=linked.cardinality != "many-to-many",
            )
            for linked in ONTOLOGY.links
        ],
        precedence=list(ONTOLOGY.precedence),
        asset_id="enterprise-ontology",
    )


@router.get("/instances")
def instances(
    type: str | None = Query(default=None),
    limit: int = Query(default=400, ge=1, le=2000),
    principal: Principal = Depends(get_principal),
):
    """The live ABox in the same shape an authored fragment comes back in.

    One renderer for both, so the running graph and a declared fixture are
    peers in the console rather than two views that merely resemble each other.
    """
    require_reader(principal)
    from .worlds import project_live

    return project_live(deps.get_graph_service(), type, limit)


@router.get("/search", response_model=list[EntityHit])
def search_entities(
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    principal: Principal = Depends(get_principal),
) -> list[EntityHit]:
    """Find entities by any handle a person actually holds.

    Matches labels, natural keys and ids — and when the match is a
    SourceRecord, the canonical entity it `identified_as` is surfaced too,
    so an external system's id resolves to the thing it means here.
    """
    require_reader(principal)
    graph = deps.get_graph_service()
    raw = q.strip().lower()
    needle = normalize_key(q)

    def rank(record: ObjectRecord) -> tuple[int, str] | None:
        label = normalize_key(record.label)
        key = record.natural_key.lower()
        if raw == record.id.lower() or needle == label or raw == key:
            return (0, "exact")
        # The thing *named* what you typed beats the things merely keyed
        # under it: "acme" is the Vendor before it is any of its invoices.
        if needle and label.startswith(needle):
            return (1, "label")
        if key.startswith(raw):
            return (2, "natural key")
        if needle and needle in label:
            return (3, "label")
        if raw in key:
            return (4, "natural key")
        if raw in record.id.lower():
            return (5, "id")
        return None

    from .ontology import ONTOLOGY as _ontology

    subordinate = {
        o.name for o in _ontology.objects if _ontology.owner_of(o.name)
    }
    scored: list[tuple[int, int, ObjectRecord, str]] = []
    for record in graph.find(None):
        hit = rank(record)
        if hit is not None:
            # A part defers to independent entities on principle: searching
            # "acme" should find the Vendor and the Invoices before any of
            # their lines.
            scored.append(
                (1 if record.type in subordinate else 0, hit[0], record, hit[1])
            )
    scored.sort(
        key=lambda t: (t[0], t[1], len(t[2].label or t[2].natural_key),
                       t[2].type, t[2].label.lower())
    )

    out: list[EntityHit] = []
    seen: set[str] = set()
    for _, _, record, match in scored[: limit * 2]:
        if record.id not in seen:
            seen.add(record.id)
            out.append(EntityHit(entity=_summary(record), match=match))
        # A matched source row also surfaces what it identifies: the person
        # typing "V-10041" wants the Vendor, not the landing record.
        if record.type == "SourceRecord":
            for link, other in graph.neighbors(record.id, "identified_as", "in"):
                if other.id not in seen:
                    seen.add(other.id)
                    out.append(EntityHit(
                        entity=_summary(other),
                        match="external id",
                        via=record.natural_key,
                    ))
        if len(out) >= limit:
            break
    return out[:limit]


@router.get("/links", response_model=list[LinkRow])
def list_links(
    type: str | None = Query(default=None),
    include_reversed: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    principal: Principal = Depends(get_principal),
) -> list[LinkRow]:
    """Links as rows — the edge table for one Link type.

    This is where an identity Link becomes auditable: confidence, method,
    liveness and the run that wrote it, per pair, instead of a dashed line
    on a diagram.
    """
    require_reader(principal)
    graph = deps.get_graph_service()
    rows: list[LinkRow] = []
    for link in graph.store.links():
        if type is not None and link.type != type:
            continue
        if not include_reversed and not link.live:
            continue
        source = graph.store.get_object(link.from_id)
        target = graph.store.get_object(link.to_id)
        if source is None or target is None:
            continue

        def endpoint(record: ObjectRecord) -> LinkEndpoint:
            return LinkEndpoint(
                id=record.id, type=record.type,
                label=record.label or record.natural_key, ref=record.ref(),
            )

        rows.append(LinkRow(
            id=link.id, type=link.type,
            from_entity=endpoint(source), to_entity=endpoint(target),
            confidence=link.confidence, method=link.method, props=link.props,
            live=link.live, reversal_reason=link.reversal_reason,
            run_id=link.run_id, step_id=link.step_id,
        ))
        if len(rows) >= limit:
            break
    return rows


@router.get("/objects", response_model=list[ObjectSummary])
def list_objects(
    type: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_principal),
) -> list[ObjectSummary]:
    require_reader(principal)
    records = deps.get_graph_service().find(type)
    if q:
        needle = normalize_key(q)
        records = [
            r for r in records
            if needle in normalize_key(f"{r.label} {r.natural_key}")
        ]
    return [_summary(r) for r in records[:limit]]


@router.get("/objects/{object_ref}", response_model=ObjectDetail)
def get_object(
    object_ref: str,
    link_type: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> ObjectDetail:
    require_reader(principal)
    graph = deps.get_graph_service()
    record = graph.get(object_ref)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Object not found: {object_ref}")
    neighbors: list[NeighborEdge] = []
    for direction in ("out", "in"):
        for link, other in graph.neighbors(
            record.id, link_type, direction, include_reversed=True
        ):
            neighbors.append(NeighborEdge(
                link_id=link.id, type=link.type, direction=direction,
                confidence=link.confidence, method=link.method, props=link.props,
                live=link.live, other=_summary(other),
            ))
    return ObjectDetail(
        object=_summary(record), claims=record.claims,
        conflicts=record.conflicts(), neighbors=neighbors,
    )


@router.get("/vendors", response_model=list[VendorView])
def list_vendors(
    principal: Principal = Depends(get_principal),
) -> list[VendorView]:
    """The vendor master, assembled for the AP workspace.

    One row per canonical vendor, carrying every source system's id for it —
    which is the thing that was impossible when a vendor was a slug.
    """
    require_reader(principal)
    graph = deps.get_graph_service()
    knowledge = deps.get_service()
    out: list[VendorView] = []
    for vendor in graph.find("Vendor"):
        if vendor.status == "superseded":
            continue
        sources = [
            {
                "source": str(other.props.get("source") or ""),
                "external_id": str(other.props.get("external_id") or ""),
                "confidence": link.confidence,
                "method": link.method,
                "live": link.live,
                "link_id": link.id,
            }
            for link, other in graph.neighbors(
                vendor.id, "identified_as", "out", include_reversed=True
            )
        ]
        invoices = [o for _, o in graph.neighbors(vendor.id, "issued", "out")]
        orders = graph.neighbors(vendor.id, "ordered_from", "in")
        slug = str(vendor.props.get("slug") or normalize_key(vendor.label))
        exemplar = next(
            (
                a for a in knowledge.repo.all()
                if "exemplar" in a.tags and slug in a.id
            ),
            None,
        )
        out.append(VendorView(
            id=vendor.id, label=vendor.label, slug=slug,
            status=str(vendor.props.get("status") or vendor.status),
            tax_id=str(vendor.props.get("tax_id") or ""),
            payment_terms=str(vendor.props.get("payment_terms") or ""),
            aliases=list(vendor.props.get("aliases") or []),
            source_records=sources,
            invoices=len(invoices),
            invoiced_usd=round(
                sum(float(i.props.get("total_usd") or 0) for i in invoices), 2
            ),
            purchase_orders=len(orders),
            has_exemplar=exemplar is not None,
            exemplar_ref=(
                f"knowledge:{exemplar.id}@rev{exemplar.revision}" if exemplar else ""
            ),
            conflict_fields=sorted(vendor.conflicts().keys()),
            revision=vendor.revision, ref=vendor.ref(),
        ))
    return sorted(out, key=lambda v: v.label.lower())


@router.get("/employees/{email}/expenses", response_model=ExpenseRollup)
def employee_expenses(
    email: str,
    days: int = Query(default=180, ge=1, le=3650),
    principal: Principal = Depends(get_principal),
) -> ExpenseRollup:
    """`Employee --submitted--> ExpenseRequest --has_expense_line--> ExpenseLine`.

    Two hops and a sum. Worth noticing what this is *not*: retrieval. No
    document contains this number, so no amount of search would find it — the
    answer lives in the edges, which is the argument for having them.
    """
    require_reader(principal)
    graph = deps.get_graph_service()
    rollup = ExpenseRollup(
        employee=email, window_days=days,
        traversal=[
            "Employee(email)",
            "--submitted-->ExpenseRequest",
            "--has_expense_line-->ExpenseLine",
        ],
    )
    employee = graph.get(object_id("Employee", email))
    if employee is None:
        return rollup
    rollup.employee_object_id = employee.id
    cutoff = utcnow() - timedelta(days=days)
    for _, request in graph.neighbors(employee.id, "submitted", "out"):
        submitted_at = str(request.props.get("submitted_at") or "")
        try:
            when = datetime.fromisoformat(submitted_at)
        except ValueError:
            when = None
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=cutoff.tzinfo)
            if when < cutoff:
                continue
        rollup.requests += 1
        rollup.requested_usd = round(
            rollup.requested_usd + float(request.props.get("requested_usd") or 0), 2
        )
        rollup.approved_usd = round(
            rollup.approved_usd + float(request.props.get("approved_usd") or 0), 2
        )
        lines = []
        for _, line in graph.neighbors(request.id, "has_expense_line", "out"):
            rollup.lines += 1
            category = str(line.props.get("category") or "other")
            amount = float(line.props.get("requested_usd") or 0)
            rollup.by_category[category] = round(
                rollup.by_category.get(category, 0.0) + amount, 2
            )
            lines.append({
                "category": category, "requested_usd": amount,
                "vendor": str(line.props.get("vendor") or ""),
                "origin": str(line.props.get("origin") or ""),
            })
        rollup.detail.append({
            "request_ref": request.ref(),
            "submitted_at": submitted_at,
            "state": str(request.props.get("state") or ""),
            "requested_usd": float(request.props.get("requested_usd") or 0),
            "lines": lines,
        })
    rollup.detail.sort(key=lambda d: str(d.get("submitted_at")), reverse=True)
    return rollup
