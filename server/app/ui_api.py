"""The console's own API.

Two front doors, deliberately different shapes.

`/api/v1` is the **machine** API: resource-shaped, stable, one noun per route,
reachable with a minted key, and the thing MCP and A2A sit on. It is a public
contract and changing it costs a version.

`/api/ui/v1` is the **console** API: view-shaped, composed, one route per
screen, and reachable only with a human session. It is a private contract
between this server and the Flutter client, free to change whenever a screen
changes, and it exists for two reasons:

1. **Attack surface.** The console used to reach 84 distinct `/api/v1`
   endpoints directly, which meant every one of them had to be safe to expose
   to a browser. Here the browser reaches a handful of routes that return
   exactly what one screen draws, and nothing else needs to face it.
2. **Round trips.** A screen that renders from four resources made four
   requests and stitched them in Dart, which put a piece of the view's logic
   in the client where it could drift. Composition happens once, here.

**No API key may enter this door.** A key is a machine credential and a
machine has no screens; letting one through would quietly make every composed
convenience route part of the public contract.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from pydantic import BaseModel

from . import data_collections, deps, flows, payables, personas
from .auth import (
    Principal,
    get_principal,
    require_queue,
    require_reader,
    require_role,
)
from .config import settings
from .deps import get_graph_service
from .disagreement_sweep import (
    DISAGREEMENT_SWEEP_FLOW_ID,
    ORDER_DESK_QUEUE,
    SCAN,
    STANDING,
)
from .estate_api import pull_feed_now
from .flow_projection import ProjectionState, project_flow, project_flows
from .gateway import ACTIONS
from .graph import GraphService, LinkRecord, ObjectRecord, object_id
from .identity_sweep import IDENTITY_SWEEP_FLOW_ID, SCANS, SCAN_BY_LINK
from .mappings import MAPPINGS, Mapping, steward_queue_for_type, validate_mappings
from .models import utcnow
from .ontology import ONTOLOGY
from .order_release import BY_CUMULUS, DISMISSED, EXTERNALLY, read_write_policy
from .resolution import read_sweep_thresholds
from .sync import _slug

log = logging.getLogger("cumulus.ui.api")

router = APIRouter(prefix="/api/ui/v1", tags=["console"])


def get_ui_principal(principal: Principal = Depends(get_principal)) -> Principal:
    """A human session, or nothing."""
    if principal.key_id is not None:
        raise HTTPException(
            status_code=403,
            detail="The console API is for signed-in people. Use /api/v1.",
        )
    return require_reader(principal)


# ---------------------------------------------------------------------------
# Session — the one call the shell makes before it draws anything
# ---------------------------------------------------------------------------


class PersonaOption(BaseModel):
    id: str
    #: The principal uid this persona acts as. Carried so the console can
    #: resolve `impersonated_by` back to a name and say "Mitchell Suter,
    #: acting as John Smith" rather than only that impersonation is happening.
    uid: str
    label: str
    title: str
    person: str
    email: str
    authority: str
    scope: str
    queues: list[str]
    blurb: str
    #: False when this persona has no `Employee` in the graph, so the console
    #: can say why the personal surfaces are empty instead of looking broken.
    in_workforce: bool


class PersonaGroup(BaseModel):
    label: str
    personas: list[PersonaOption]


class Session(BaseModel):
    """Who you are, what you may do, and who else you could be."""

    uid: str
    email: str
    persona_id: str
    label: str
    person: str
    title: str
    authority: str
    scope: str
    queues: list[str]
    in_workforce: bool
    #: Set when this session is an impersonation; names the real operator.
    impersonated_by: str | None
    #: Whether the persona switcher should be offered at all.
    may_switch_persona: bool
    #: Whether the identity workbench has anything for this persona. Answered
    #: here because the rule is not the one the rest of the rail's governance
    #: group uses — a steward reads only its own work and still owns the
    #: counterparty desk — and a stop that can only ever 403 is worse than no
    #: stop. See `may_open_identity`.
    may_open_identity: bool
    groups: list[PersonaGroup]


def _in_workforce(email: str) -> bool:
    if not email:
        return False
    return get_graph_service().get(object_id("Employee", email)) is not None


def _option(p: personas.Persona) -> PersonaOption:
    return PersonaOption(
        id=p.id,
        uid=p.uid,
        label=p.label,
        title=p.title,
        person=p.person,
        email=p.email,
        authority=p.authority,
        scope=p.scope,
        queues=list(p.queues),
        blurb=p.blurb,
        in_workforce=_in_workforce(p.email),
    )


@router.get("/session", response_model=Session)
def session(principal: Principal = Depends(get_ui_principal)) -> Session:
    """Everything the shell needs before its first paint.

    One call rather than four: who is signed in, which persona they are acting
    as, which queues that persona owns (the rail badges depend on it), and the
    full roster for the switcher.
    """
    # A principal on no persona is described by its own credential, never by a
    # persona standing in for it. Falling back to `DEFAULT_PERSONA` labelled a
    # signed-in Firebase user "Mitchell Suter, Director of Platform &
    # Operations" — the wrong name, and the most privileged one on the roster.
    acting = personas.get(principal.persona_id)

    # Who may switch is a question about the *operator*, not about whoever they
    # are currently watching as. Asking the impersonated principal made
    # impersonation one-way: act as Kim once, and the switcher vanished with no
    # route back. `impersonated_by` names the real operator, so an impersonated
    # session is by construction one that was already allowed to switch.
    may_switch = (
        settings.auth_backend == "none"
        or principal.impersonated_by is not None
        or principal.outranks_or_is("owner")
    )
    return Session(
        uid=principal.uid,
        email=principal.email,
        persona_id=acting.id if acting else "",
        label=acting.label if acting else principal.email,
        person=acting.person if acting else "",
        title=acting.title if acting else "",
        authority=principal.role,
        scope=principal.scope,
        queues=list(principal.queues),
        in_workforce=_in_workforce(principal.email),
        impersonated_by=principal.impersonated_by,
        may_switch_persona=may_switch,
        may_open_identity=may_open_identity(principal),
        groups=[
            PersonaGroup(label=label, personas=[_option(p) for p in group])
            for label, group in personas.GROUPS
        ],
    )


# ---------------------------------------------------------------------------
# The Knowledge layer — Ontology, Library, Sources
# ---------------------------------------------------------------------------
#
# Three screens, three routes. Before this the layer rendered from a five-call
# fan-out per screen, which fetched the mapping registry three times and the
# ontology, the invariant report, the Collection registry, the source list and
# the graph stats twice each on every visit. None of that stitching was a
# decision; it was the shape of `/api/v1` showing through.


def governance_door(surface: str):
    """The governance surfaces' read reach: platform or tenant, never own.

    One rule, four sentences. The rule is shared because the argument is one
    argument — the ontology, the corpus, the feeds, the desks and the ledger
    are the shape of the whole deployment, so an own-scoped reader is refused
    outright rather than handed an empty diagram it would read as "there is
    nothing here" instead of "this is not yours".

    The *sentence* is per surface because a refusal is the only thing that
    reader sees, and one sentence for four screens can only name a generality:
    somebody who opened Sources was told that "this describes the whole map",
    which is not what they asked for. `surface` completes "<noun phrase>. Your
    access is to your own work." — second person, and never the word *persona*,
    which is our vocabulary for a thing they experience as themselves. The
    identity workbench's bespoke refusal is the model.

    Built on `get_ui_principal` so the no-keys rule is inherited in one place
    and cannot drift into a second spelling.
    """

    def door(principal: Principal = Depends(get_ui_principal)) -> Principal:
        if not principal.reads_beyond_self:
            raise HTTPException(
                status_code=403,
                detail=f"{surface} Your access is to your own work.",
            )
        return principal

    return door


#: The four doors, named once so a route reads as the surface it serves.
get_ontology_principal = governance_door(
    "The ontology is every object type in the deployment and the flows that "
    "touch them."
)
get_library_principal = governance_door(
    "The library is the whole governed corpus, across every team."
)
get_sources_principal = governance_door(
    "The source registry covers the whole deployment."
)
get_dashboard_principal = governance_door(
    "System state reads every collection, feed, desk and ledger entry at once."
)


#: The cluster an object type falls into when no Collection claims it.
#: Visibly named rather than hidden: the partition is pinned by a test, and if
#: it ever breaks the diagram should say so on screen rather than silently
#: drop a node or fail the whole screen over one bad declaration.
UNGOVERNED = "ungoverned"


# ---- Ontology --------------------------------------------------------------


class TypeField(BaseModel):
    name: str
    kind: str
    note: str


class OntologyType(BaseModel):
    """One node of the diagram."""

    name: str
    label: str
    summary: str
    natural_key: str
    fields: list[TypeField]
    tags: list[str]
    competency_questions: list[str]
    instances: int
    #: Instances where two systems assert the same field differently. Counted
    #: per type rather than once for the graph, because a badge on the node is
    #: where precedence has somewhere to be noticed.
    conflicted: int
    #: The Collection that governs its rows — the cluster this node sits in.
    collection: str
    #: Composition, derived from the link types and never declared: a part is
    #: identified through its owner, so it is drawn inside it.
    owner_type: str
    part_types: list[str]


class OntologyLink(BaseModel):
    name: str
    from_type: str
    to_type: str
    nature: str
    cardinality: str
    #: Whether the cardinality is constrained on write. A many-to-many edge is
    #: declared deliberately rather than merely unobserved, so the diagram is
    #: entitled to draw the two differently.
    enforced: bool
    instances: int


class OntologyCluster(BaseModel):
    """A Collection, as the thing the diagram groups by."""

    id: str
    label: str
    role: str
    origin: str
    type_names: list[str]


class OntologyOverlay(BaseModel):
    """One flow's footprint on the model, observed beside declared.

    `state` is carried whole rather than collapsed into an empty `writes`.
    A flow that has never run and a flow that ran and wrote nothing produce
    the same empty set and are opposite facts, and a client holding only the
    sets cannot tell them apart.

    Both halves ship because the diff is the point: observed-but-undeclared is
    a governance gap, declared-but-unobserved is a stale declaration. The
    client subtracts one set from the other to tint the diagram; there is no
    third pair of pre-diffed lists here to fall out of step with these.
    """

    flow_id: str
    state: ProjectionState
    scanned_runs: int
    total_runs: int
    reads: list[str]
    writes: list[str]
    establishes: list[str]
    knowledge: list[str]
    declared_reads: list[str]
    declared_writes: list[str]


class OntologyFlowOption(BaseModel):
    id: str
    label: str


class OntologyScreen(BaseModel):
    types: list[OntologyType]
    links: list[OntologyLink]
    collections: list[OntologyCluster]
    #: Present only when `?flow=` names one.
    overlay: OntologyOverlay | None
    flows: list[OntologyFlowOption]
    #: TBox violations over the whole graph. `None` — deliberately not zero —
    #: for a tenant-scoped persona: a violation is a Naviant bug, and an alarm
    #: a customer can take no action on is worse than no alarm.
    conformance: int | None


def _graph_counts(
    graph: GraphService,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Instances, conflicted instances and edges, in one pass over the store.

    `graph.stats()` walks every object and calls `conflicts()` on it already;
    asking it for the totals and then walking again for the per-type conflict
    split would resolve every claim twice for one number.
    """
    by_type: dict[str, int] = {}
    conflicted: dict[str, int] = {}
    for obj in graph.store.objects():
        by_type[obj.type] = by_type.get(obj.type, 0) + 1
        if obj.conflicts():
            conflicted[obj.type] = conflicted.get(obj.type, 0) + 1
    by_link: dict[str, int] = {}
    for link in graph.store.links():
        if link.live:
            by_link[link.type] = by_link.get(link.type, 0) + 1
    return by_type, conflicted, by_link


def _flow_label(flow_id: str, names: dict[str, str]) -> str:
    """The registry's name, or one read off the id.

    Five of the seven flows that write the graph are inbound syncs with no
    FlowDefinition to carry a name, so a registry lookup alone would leave
    most of the overlay picker blank.
    """
    return names.get(flow_id) or flow_id.replace("-", " ").capitalize()


@router.get("/ontology", response_model=OntologyScreen)
def ontology(
    flow: str | None = Query(
        default=None, description="Overlay this flow's observed footprint."
    ),
    principal: Principal = Depends(get_ontology_principal),
) -> OntologyScreen:
    """Everything the model diagram draws.

    The clustering is by Collection, which is the honest grouping: retention,
    custody and authoritativeness are properties of the set a row belongs to,
    so a diagram grouped any other way would be grouped by nothing that
    governs anything.
    """
    graph = deps.get_graph_service()
    by_type, conflicted, by_link = _graph_counts(graph)

    # One pass decides both the node's cluster and the cluster's membership,
    # so the two can never disagree. A type no Collection claims lands in
    # `ungoverned` instead of taking the screen down with it — the partition
    # is asserted by a test, and a route is the wrong place to relitigate it.
    cluster_of: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    for declared in ONTOLOGY.objects:
        governing = data_collections.for_object_type(declared.name)
        cluster_id = governing.id if governing is not None else UNGOVERNED
        cluster_of[declared.name] = cluster_id
        members.setdefault(cluster_id, []).append(declared.name)

    clusters = [
        OntologyCluster(
            id=collection.id, label=collection.name, role=collection.role,
            origin=collection.origin, type_names=members[collection.id],
        )
        for collection in data_collections.COLLECTIONS
        if collection.id in members
    ]
    if UNGOVERNED in members:
        log.warning(
            "Object types governed by no Collection",
            extra={"types": members[UNGOVERNED]},
        )
        clusters.append(OntologyCluster(
            id=UNGOVERNED, label="Ungoverned — no Collection claims these",
            role="", origin="", type_names=members[UNGOVERNED],
        ))

    overlay: OntologyOverlay | None = None
    engine = deps.get_run_engine()
    projections = project_flows(engine)
    if flow is not None:
        found = next((p for p in projections if p.flow_id == flow), None)
        if found is None:
            raise HTTPException(
                status_code=404, detail=f"No flow to overlay: {flow}"
            )
        overlay = OntologyOverlay(
            flow_id=found.flow_id, state=found.state,
            scanned_runs=found.scanned_runs, total_runs=found.total_runs,
            reads=found.reads, writes=found.writes,
            establishes=found.establishes, knowledge=found.knowledge,
            declared_reads=found.declared_reads,
            declared_writes=found.declared_writes,
        )

    names = {f.id: f.name for f in deps.get_flow_service().list() if f.name}
    return OntologyScreen(
        types=[
            OntologyType(
                name=o.name, label=o.label, summary=o.summary,
                natural_key=o.natural_key,
                fields=[
                    TypeField(name=f.name, kind=f.kind, note=f.note)
                    for f in o.fields
                ],
                tags=list(ONTOLOGY.effective_tags(o.name)),
                competency_questions=list(o.competency_questions),
                instances=by_type.get(o.name, 0),
                conflicted=conflicted.get(o.name, 0),
                collection=cluster_of[o.name],
                owner_type=ONTOLOGY.owner_of(o.name) or "",
                part_types=ONTOLOGY.parts_of(o.name),
            )
            for o in ONTOLOGY.objects
        ],
        links=[
            OntologyLink(
                name=edge.name, from_type=edge.from_type, to_type=edge.to_type,
                nature=edge.nature, cardinality=edge.cardinality,
                enforced=edge.cardinality != "many-to-many",
                instances=by_link.get(edge.name, 0),
            )
            for edge in ONTOLOGY.links
        ],
        collections=clusters,
        overlay=overlay,
        flows=[
            OntologyFlowOption(
                id=p.flow_id, label=_flow_label(p.flow_id, names)
            )
            for p in projections
        ],
        conformance=(
            len(graph.check_invariants()) if principal.sees_everything else None
        ),
    )


# ---- Sources ---------------------------------------------------------------

#: Artifacts listed per feed. A feed that dropped a thousand scanned receiving
#: reports should not turn a list screen into a megabyte; `artifacts_total`
#: says what the window is a window onto, the same way `scanned_runs` does.
ARTIFACT_WINDOW = 20


class SourceContract(BaseModel):
    """The Mapping a feed lands under — the declared half of the arrangement."""

    id: str
    system: str
    transport: str
    payload: str
    strategy: str
    lands_as: str
    resolves_to: list[str]
    match_policy: str
    precedence_source: str
    freshness: str
    custody: str
    collection: str
    escalation: str


class SourceArtifact(BaseModel):
    id: str
    filename: str
    kind: str
    uploaded_at_iso: str
    region_count: int
    retention_until_iso: str


class PullState(BaseModel):
    """The connector's half of a feed Cumulus *pulls*, read off `ConnectorState`.

    Only a pulled feed has one. A feed that pushes through the door keeps its
    own cadence and has no cursor here, so `None` on the row is that fact
    rather than missing data — and the screen draws no Sync now for it.

    The cursor cannot be derived from the runs the way the rest of the row is,
    which is why it is joined rather than computed: a pull that found nothing
    starts no run, and a pull that *failed* has no run at all. Both are states
    an operator needs, and both are invisible to `SyncService.sources()`.

    Registration is not on this shape. It is one fact for the whole connector,
    the `EstateCard` above the list carries it, and no `PullState` is built at
    all unless an estate is registered — so a per-row copy could only ever have
    said True.
    """

    #: Whether discovery says the namespace actually runs this feed's app. A
    #: registry row for an app the customer does not run is inert, not broken.
    active: bool
    estate_app: str
    namespace: str
    #: The list routes this feed pulls together, in delivery order.
    resources: list[str]
    #: Per resource, because they move independently — an employee change must
    #: not re-pull the jobs catalogue.
    watermarks: dict[str, str]
    last_pull_at: str
    last_outcome: str  # delivered · unchanged · failed
    last_error: str
    #: Only the dev interval loop knows when the next pull is due; under a
    #: managed scheduler the cadence belongs to the scheduler, and this stays
    #: empty rather than claiming one nothing here controls.
    next_pull_at: str
    sync_now_allowed: bool
    #: Why not, when not. An access boundary is a sentence the screen prints
    #: next to a control it does not draw — never a button that 403s.
    sync_now_refusal: str


class SourceRow(BaseModel):
    """One feed: how it is running, what it promised, what it left behind.

    `records` and `records_landed` are both here because they are different
    facts and one of them was standing in for both. See `sync.SourceStatus`:
    received is what arrived, landed is what the landing step kept, and the
    three dispositions are `None` on a feed whose contract never reports them
    rather than a confident zero.
    """

    source: str
    kind: str
    flow_id: str
    last_run_id: str
    last_run_at_iso: str
    last_status: str
    last_error: str
    watermark: str
    deliveries: int
    #: Rows the delivery carried, before anything decided what to keep.
    records: int = 0
    records_landed: int
    linked: int | None = None
    created: int | None = None
    awaiting_steward: int
    unchanged: int | None = None
    #: `None` when no Mapping claims this feed — rows arriving under no
    #: declared contract, which is exactly the finding this screen is for.
    contract: SourceContract | None
    #: `None` for a feed that pushes. See `PullState`.
    pull: PullState | None
    artifacts: list[SourceArtifact]
    artifacts_total: int


class EstateCard(BaseModel):
    """The registered estate, above the feeds it explains.

    Screen-level rather than per-row because registration is one fact for the
    whole connector: five feeds failing because no estate is registered is one
    finding, and repeating it five times down a list buries it.
    """

    registered: bool
    project: str
    namespace: str
    discovery_url: str
    #: Whether a credential is configured — never which one.
    authenticated: bool
    pull_interval_seconds: int
    #: Set when discovery itself failed. Rendered as its own state, because an
    #: estate that could not be reached is not an estate that runs no apps.
    discovery_error: str
    industry: str
    #: What discovery says this namespace runs. The feed registry proposes and
    #: this disposes, so a feed whose app is absent renders as inactive.
    apps: list[str]
    processes: list[str]
    #: `None` when the discovery payload carries no pulse block — an estate
    #: that reports no simulation is not one whose simulation is stopped.
    pulse_enabled: bool | None
    pulse_intensity: str
    #: Feeds in the connector's registry, and how many of them this namespace
    #: actually runs. Both counts are answered by rows in the list below.
    feeds_registered: int
    feeds_active: int


class SourcesScreen(BaseModel):
    sources: list[SourceRow]
    estate: EstateCard
    #: Mappings naming a type or Collection that does not exist. Same reading
    #: as `conformance`, and withheld from a tenant for the same reason.
    problems: list[str] | None


class SyncNowOutcome(BaseModel):
    """What one pull did, and the row it leaves behind.

    The row travels with the outcome so the screen redraws the feed from the
    same read that reports the result. A count in a banner disagreeing with
    the count in the list under it is the failure this shape rules out.
    """

    source: str
    outcome: str  # delivered · unchanged · failed
    #: Records read from the estate, per resource.
    pulled: dict[str, int]
    #: Records handed to the door, across every chunk of this pull.
    delivered: int
    chunks: int
    error: str
    #: The feed's row, re-read after the pull. `None` only if the feed left the
    #: registry mid-request — the screen keeps the row it has.
    row: SourceRow | None


def _artifacts_by_source(flow_ids: set[str]) -> dict[str, list[SourceArtifact]]:
    """Documents, attributed to the feed whose run left them.

    Joined through the run rather than the flow, because three of the seven
    feeds share `vendor-sync` — grouping by flow would credit each of them
    with all three feeds' artifacts.
    """
    engine = deps.get_run_engine()
    source_of_run: dict[str, str] = {}
    for flow_id in flow_ids:
        for run in engine.list(flow_id=flow_id):
            name = str(run.inputs.get("source") or run.outputs.get("source") or "")
            if name:
                source_of_run[run.id] = name

    out: dict[str, list[SourceArtifact]] = {}
    for doc in deps.get_document_service().list():
        name = next(
            (source_of_run[r] for r in doc.run_ids if r in source_of_run), None
        )
        if name is None:
            continue
        out.setdefault(name, []).append(SourceArtifact(
            id=doc.id, filename=doc.filename, kind=doc.kind,
            uploaded_at_iso=doc.uploaded_at_iso,
            region_count=doc.region_count,
            retention_until_iso=doc.retention_until_iso,
        ))
    for artifacts in out.values():
        artifacts.sort(key=lambda a: a.uploaded_at_iso, reverse=True)
    return out


def _contract(mapping: Mapping | None) -> SourceContract | None:
    """The declared half, or `None` — which is itself the screen's finding."""
    if mapping is None:
        return None
    return SourceContract(
        id=mapping.id, system=mapping.system,
        transport=mapping.transport, payload=mapping.payload,
        strategy=mapping.strategy, lands_as=mapping.lands_as,
        resolves_to=list(mapping.resolves_to),
        match_policy=mapping.match_policy,
        precedence_source=mapping.precedence_source,
        freshness=mapping.freshness, custody=mapping.custody,
        collection=mapping.collection, escalation=mapping.escalation,
    )


def _sync_now_refusal(
    *, active: bool, may_sync: bool, discovery_error: str,
    namespace: str, app: str,
) -> str:
    """The reason there is no Sync now, in the order the operator can act on.

    An unreachable estate is reported as itself rather than collapsing into
    "the namespace does not run that app" — discovery returning nothing and
    discovery answering that the app is absent are opposite findings, and only
    the second one is about the customer's estate.
    """
    if discovery_error:
        return f"The estate could not be reached: {discovery_error}"
    if not active:
        return (
            f"The `{namespace}` namespace does not run the `{app}` app, so "
            f"this feed has nothing to pull."
        )
    if not may_sync:
        return (
            "Pulling a feed delivers through the door, which writes. That "
            "needs admin authority."
        )
    return ""


def _estate_view(principal: Principal) -> tuple[EstateCard, dict[str, PullState]]:
    """The estate card, and one `PullState` per feed the connector registers.

    Read through `registration()`, which never raises by design: an estate
    that is down is a thing this screen has to draw, and a 500 here would take
    the whole Sources list — including every feed that pushes and is fine —
    down with the one connector that could not reach a network.
    """
    view = deps.get_estate_connector().registration()
    detail = view.get("namespace_detail") or {}
    pulse = detail.get("pulse")
    registered = bool(view.get("registered"))
    namespace = str(view.get("namespace") or "")
    discovery_error = str(view.get("discovery_error") or "")
    # The write ladder, asked once for the screen rather than per row: the
    # answer cannot differ between two feeds of the same connector.
    may_sync = principal.outranks_or_is("admin")

    # No estate registered means no feed *is* pulled — the registry still holds
    # the specs, but none of them names anything reachable. Reporting them as
    # rows would put a Sync now on a feed with nowhere to pull from, and make
    # the card's counts describe a list of possibilities rather than of feeds.
    states: dict[str, PullState] = {}
    for feed in (view.get("feeds") or ()) if registered else ():
        state = feed.get("state") or {}
        app = str(feed.get("app") or "")
        active = bool(feed.get("active"))
        states[str(feed.get("feed") or "")] = PullState(
            active=active,
            estate_app=app,
            namespace=namespace,
            resources=[str(r) for r in feed.get("resources") or ()],
            watermarks={
                str(k): str(v)
                for k, v in (state.get("watermarks") or {}).items()
            },
            last_pull_at=str(state.get("last_pull_at") or ""),
            last_outcome=str(state.get("last_outcome") or ""),
            last_error=str(state.get("last_error") or ""),
            next_pull_at=str(state.get("next_pull_at") or ""),
            sync_now_allowed=active and may_sync and not discovery_error,
            sync_now_refusal=_sync_now_refusal(
                active=active, may_sync=may_sync,
                discovery_error=discovery_error,
                namespace=namespace, app=app,
            ),
        )

    card = EstateCard(
        registered=registered,
        project=str(view.get("project") or ""),
        namespace=namespace,
        discovery_url=str(view.get("discovery_url") or ""),
        authenticated=bool(view.get("authenticated")),
        pull_interval_seconds=int(view.get("pull_interval_seconds") or 0),
        discovery_error=str(view.get("discovery_error") or ""),
        industry=str(detail.get("industry") or ""),
        apps=[str(a) for a in detail.get("apps") or ()],
        processes=[str(p) for p in detail.get("processes") or ()],
        pulse_enabled=(
            bool(pulse.get("enabled")) if isinstance(pulse, dict) else None
        ),
        pulse_intensity=(
            str(pulse.get("intensity") or "") if isinstance(pulse, dict) else ""
        ),
        feeds_registered=len(states),
        feeds_active=sum(1 for s in states.values() if s.active),
    )
    return card, states


def _source_rows(pulls: dict[str, PullState]) -> list[SourceRow]:
    """Every feed the screen draws: what has delivered, plus what is registered.

    The delivered half is assembled from the sync runs, as it always was. The
    registered half is the addition a pull connector forces: a feed that has
    never pulled has no run to be assembled from, and leaving it out would put
    the Sync now that starts it nowhere, and make the estate card's count of
    active feeds disagree with the list underneath it.
    """
    statuses = deps.get_sync_service().sources()
    contracts = {m.source: m for m in MAPPINGS}
    artifacts = _artifacts_by_source({s.flow_id for s in statuses})

    rows: list[SourceRow] = []
    for status in statuses:
        left = artifacts.get(status.source, [])
        rows.append(SourceRow(
            source=status.source, kind=status.kind, flow_id=status.flow_id,
            last_run_id=status.last_run_id,
            last_run_at_iso=status.last_run_at_iso,
            last_status=status.last_status, last_error=status.last_error,
            watermark=status.watermark, deliveries=status.deliveries,
            records=status.records,
            records_landed=status.records_landed, linked=status.linked,
            created=status.created, awaiting_steward=status.awaiting_steward,
            unchanged=status.unchanged,
            contract=_contract(contracts.get(status.source)),
            pull=pulls.get(status.source),
            artifacts=left[:ARTIFACT_WINDOW],
            artifacts_total=len(left),
        ))

    delivered = {s.source for s in statuses}
    for feed, pull in pulls.items():
        if feed in delivered:
            continue
        # No run means no kind and no flow: this row states that nothing has
        # happened yet rather than naming a flow that has never been asked to.
        rows.append(SourceRow(
            source=feed, kind="", flow_id="", last_run_id="",
            last_run_at_iso="", last_status="", last_error=pull.last_error,
            watermark="", deliveries=0, records=0, records_landed=0,
            linked=None, created=None, awaiting_steward=0, unchanged=None,
            contract=_contract(contracts.get(feed)),
            pull=pull, artifacts=[], artifacts_total=0,
        ))
    return sorted(rows, key=lambda r: (r.source, r.kind))


@router.get("/sources", response_model=SourcesScreen)
def sources(
    principal: Principal = Depends(get_sources_principal),
) -> SourcesScreen:
    """Every inbound feed, against the contract it lands under.

    Health and contract in one row is the whole argument for the screen: a
    feed delivering cleanly under a mapping nobody declared is a different
    problem from one that is failing, and reading them off two lists side by
    side is how the second kind goes unnoticed. The connector's cursor joins
    the same row for the same reason — a feed whose last pull failed and a
    feed nothing has pushed to are two different silences.
    """
    estate, pulls = _estate_view(principal)
    return SourcesScreen(
        sources=_source_rows(pulls),
        estate=estate,
        problems=validate_mappings() if principal.sees_everything else None,
    )


@router.post("/sources/{source}/sync-now", response_model=SyncNowOutcome)
def sync_source_now(
    source: str,
    principal: Principal = Depends(get_sources_principal),
) -> SyncNowOutcome:
    """Pull one feed now, and answer with the row the screen redraws.

    Gated on `admin` because that is what `POST /api/v1/estate/feeds/{feed}/
    sync-now` gates on — this proxies that act, and a console door that let a
    weaker principal through would be a second, quieter authorization policy.
    The answer is also given in advance: `PullState.sync_now_allowed` tells the
    screen not to draw the control, so a persona that may not pull reads a
    sentence rather than pressing a button that refuses.

    The pull itself is `estate_api.pull_feed_now` — the same function the
    machine route calls, so the 404/400/502 an operator gets is the same
    refusal whichever door they came through. What this route adds is the row.
    """
    require_role(principal, "admin")
    outcome = pull_feed_now(source, principal.uid)

    _, pulls = _estate_view(principal)
    row = next((r for r in _source_rows(pulls) if r.source == source), None)
    chunks = outcome.get("chunks") or []
    return SyncNowOutcome(
        source=source,
        outcome=str(outcome.get("outcome") or ""),
        pulled={str(k): int(v) for k, v in (outcome.get("pulled") or {}).items()},
        delivered=sum(int(c.get("records") or 0) for c in chunks),
        chunks=len(chunks),
        error=str(outcome.get("error") or ""),
        row=row,
    )


# ---- Library ---------------------------------------------------------------


class LibraryRow(BaseModel):
    """One authored document, with the window's traffic against it."""

    id: str
    path: str
    title: str
    kind: str
    origin: str
    authority_level: str | None
    scope: str
    owner: str
    tags: list[str]
    status: str
    revision: int
    updated_at_iso: str
    reads: int
    citations: int
    impressions: int
    #: Empty when nothing has ever consumed it. A document with impressions
    #: and no reads is found and ignored, which is a different problem from
    #: one nobody has found — the client tells them apart from these three
    #: counts, so neither verdict is decided here.
    last_used_at_iso: str


class LibraryScreen(BaseModel):
    #: Echoed so the list can title its own window without the screen having
    #: to remember what it asked for.
    days: int
    assets: list[LibraryRow]


@router.get("/library", response_model=LibraryScreen)
def library(
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_library_principal),
) -> LibraryScreen:
    """The corpus and its usage, joined.

    Zero-filled, because the assets with no traffic are precisely the ones the
    dead-weight report exists to find: returning only what the usage store
    holds would omit the finding.
    """
    knowledge = deps.get_service()
    recorded = deps.get_asset_usage().usage_all(days)
    rows: list[LibraryRow] = []
    for asset in knowledge.list_assets():
        used = recorded.get(asset.id)
        rows.append(LibraryRow(
            id=asset.id, path=asset.path, title=asset.title, kind=asset.kind,
            origin=asset.origin, authority_level=asset.authority_level,
            scope=asset.scope, owner=asset.owner, tags=list(asset.tags),
            status=asset.status, revision=asset.revision,
            updated_at_iso=asset.updated_at.isoformat(),
            reads=used.reads if used else 0,
            citations=used.citations if used else 0,
            impressions=used.impressions if used else 0,
            last_used_at_iso=(
                used.last_used_at.isoformat()
                if used is not None and used.last_used_at is not None
                else ""
            ),
        ))
    return LibraryScreen(days=days, assets=rows)


# ---------------------------------------------------------------------------
# Identity — the curation workbench
# ---------------------------------------------------------------------------
#
# The sweep proposes; this is where a person reads the proposal. It composes
# three things that live apart and are illegible apart: the identity Links the
# sweep wrote (what is proposed, and what a steward has since said about it),
# the two objects each one joins (the records a person compares), and the
# `merge_review` Tasks that carry the decision (whose work it is, and whether
# this persona may do it).
#
# **It writes nothing.** Confirming a proposal goes through the Task the sweep
# raised — `POST /api/v1/tasks/{id}/decide`, which resumes into
# `SyncService.apply_merge_decision` — so the workbench and My Tasks commit
# through one path and there is one place where a merge decision is authorized.
# A second write route here would be a quieter copy of that policy.


#: The decided history is a window, not a page. Standing proposals are the
#: work and are never capped — a worklist that silently stops is a lie — but
#: the record of what has been ruled on grows without bound, so the list says
#: what it is a window onto the way `artifacts_total` does.
DECIDED_WINDOW = 50


def identity_desks() -> set[str]:
    """The queues that judge identity, read off the Mappings.

    Derived rather than listed, so a Mapping that changes hands moves the gate
    and the queue together. Today it is `data-stewardship` (the counterparty
    book) and `vendor-stewardship` (who we pay).
    """
    return {
        steward_queue_for_type(type_name)
        for scan in SCANS
        for type_name in (scan.from_type, scan.to_type)
    }


def may_open_identity(principal: Principal) -> bool:
    """Whether the workbench has anything for this principal.

    Deliberately **not** the Knowledge layer's rule, and the difference is the
    whole persona story of this screen. The Knowledge layer describes the whole
    map, so an own-scoped persona is refused outright. Identity curation is
    *work*, and the person who owns it — the Controller — is own-scoped: he
    reads only his own, and both master-data desks are his. Gating this on read
    reach would refuse the one persona the screen exists for while admitting a
    tenant admin who owns neither desk.

    So: a queue binding gets you in as a curator, read reach gets you in as an
    observer, and a persona with neither is told it is not theirs rather than
    shown an empty book. Answered here rather than in the dependency because
    `/session` needs the same answer to decide whether to draw the rail stop —
    a stop that can only ever 403 is worse than no stop.
    """
    return principal.reads_beyond_self or any(
        principal.owns_queue(queue) for queue in identity_desks()
    )


def get_identity_principal(
    principal: Principal = Depends(get_ui_principal),
) -> Principal:
    """The workbench's door. See `may_open_identity` for the rule."""
    if may_open_identity(principal):
        return principal
    raise HTTPException(
        status_code=403,
        detail=(
            "The identity workbench is the counterparty book's curation desk. "
            "You hold neither stewardship queue, and your access is to your "
            "own work — so there is nothing here for you rather than "
            "something withheld."
        ),
    )


class IdentitySignal(BaseModel):
    """One piece of evidence, exactly as the sweep recorded it.

    Weight and detail travel together because the number is only useful beside
    the sentence that produced it — which is the whole reason the sweep is
    rules and not a model.
    """

    name: str
    weight: float
    detail: str


class IdentitySide(BaseModel):
    """One of the two records, as a person compares them.

    `fields` is the *resolved* props — what the object reads as under
    precedence — because that is what a steward is deciding about. The claims
    behind a field are one drill-down further and live in the ABox explorer;
    duplicating them here would be a second, staler answer.
    """

    id: str
    ref: str
    type: str
    label: str
    natural_key: str
    status: str
    fields: dict[str, str]
    #: Which systems assert this object exists, through its declared source
    #: identity edge. A record only one system has ever seen is a different
    #: kind of duplicate from one three systems agree on.
    sources: list[str]
    #: Fields where this object's own sources already disagree. Carried per
    #: side because it changes what a merge would even mean.
    conflicted: list[str]


class ProposalGate(BaseModel):
    """Whether this proposal can be decided, by whom, and where.

    Never `None`. Three states: there is a Task and it is yours, there is a
    Task and it is somebody else's, and — the one worth naming — there is **no
    Task at all**.

    That third state used to be the policy working as declared: the sweep
    raised a Task only under the governed confidence bar, so its most confident
    proposals stood at `in_review` with nobody paged. It is a finding now. A
    merge is never automatic, so every proposal the sweep writes raises a Task;
    a standing proposal without one predates that rule or lost its Task, and
    the row says so instead of explaining that nobody needed to be paged.
    """

    task_id: str
    #: `open`, or the verdict a steward already recorded.
    task_status: str
    queue: str
    may_decide: bool
    #: Why not, when not — a sentence, never a disabled button with no reason.
    why_not: str


class IdentityProposal(BaseModel):
    """One proposed merge: the pair, the evidence, and where it stands."""

    link_id: str
    link_type: str
    #: `link:<type>/<id>` — the same ref the Task carries as its `case_ref`,
    #: so the review screen can find its proposal without a second contract.
    case_ref: str
    pair_kind: str
    #: `in_review` · `confirmed` · `withdrawn`. Read from the edge's liveness
    #: first and its `status` prop second: a rejected proposal is *reversed*,
    #: and its props still say `in_review` because withdrawing an assertion
    #: does not rewrite what it said.
    status: str
    #: The machine's score and method, untouched by any decision — confirming
    #: does not promote it to 1.0, because that would erase how good the
    #: proposal was before a human looked at it.
    confidence: float
    method: str
    signals: list[IdentitySignal]
    reversible_with: str
    policy_ref: str
    proposed_at: str
    run_id: str
    #: Who ruled, and when. Empty on a standing proposal — the sweep records
    #: itself as the decider of record, and rendering that as a decision would
    #: be inventing history.
    decided_by: str
    decided_at: str
    #: The reversal reason, on a withdrawn one. The steward's own words.
    withdrawn_because: str
    left: IdentitySide
    right: IdentitySide
    #: Fields both records hold and agree on / disagree on. Computed once here
    #: so the screen cannot count them differently from the table it draws.
    agree: list[str]
    conflict: list[str]
    gate: ProposalGate


class IdentityBook(BaseModel):
    """One counterparty book, and what is outstanding against it.

    A proposal is booked to the type it starts from — the same rule the sweep
    uses to pick a queue — so a Company↔Vendor proposal counts against the
    company book and lands on the counterparty desk, not on AP.
    """

    type: str
    label: str
    steward_queue: str
    objects: int
    standing: int
    confirmed: int
    withdrawn: int
    #: Whether this persona owns the desk that judges this book.
    yours: bool


class SweepPolicy(BaseModel):
    """The governed thresholds, as the screen quotes them."""

    propose_at: float
    #: Where an ask becomes a ratification. Not where one starts: every
    #: proposal raises a Task, and this only says in what words.
    ratify_at: float
    name_floor: float
    ceiling: float
    source_ref: str
    #: False when the policy asset is missing and the defaults are in force —
    #: a fact about the deployment, not a number to be quoted as governed.
    found_asset: bool


class SweepRun(BaseModel):
    """The last pass over the book. `None` when there has never been one."""

    run_id: str
    at_iso: str
    status: str
    compared: int
    considered: int
    proposed: int
    #: **Not** `IdentityBook.standing` or `IdentityState.standing`, which count
    #: proposals waiting on a steward. This is the sweep's own word for edges
    #: it found already on the graph and therefore left alone — a measure of
    #: idempotence, not of backlog. Kept rather than renamed because it is the
    #: run's output key; no screen renders it, and this note is what stops the
    #: first one that tries from adding the two together.
    standing: int
    steward_tasks: int
    #: How many of those Tasks were ratifications rather than questions. The
    #: split matters to whoever holds the desk: "five landed on you" and "four
    #: of them are formalities" are different mornings.
    ratifications: int


class IdentityScreen(BaseModel):
    standing: list[IdentityProposal]
    decided: list[IdentityProposal]
    #: What `decided` is a window onto. Equal to `len(decided)` until the
    #: history outgrows `DECIDED_WINDOW`.
    decided_total: int
    books: list[IdentityBook]
    policy: SweepPolicy
    last_sweep: SweepRun | None
    #: The identity desks this persona holds. Empty for an observer, which is
    #: what turns every gate's `may_decide` false with a reason.
    desks: list[str]
    #: True when this persona is here as a curator rather than an observer.
    curates: bool


def _proposal_status(link: LinkRecord) -> str:
    """`in_review` · `confirmed` · `withdrawn`, from the edge itself.

    Liveness is asked first because a rejection reverses the link and leaves
    its `status` prop saying `in_review` — correctly, since withdrawing an
    assertion does not rewrite what the assertion said. Reading the prop alone
    would show every rejected proposal as still standing.
    """
    if not link.live:
        return "withdrawn"
    return str(link.props.get("status") or "in_review")


def _side(graph: GraphService, record: ObjectRecord | None, object_id_: str) -> IdentitySide:
    """One object as the comparison shows it, or an honest stub if it is gone.

    An object can leave the book under a proposal that still points at it — a
    supersede, a purge between sweeps. The row says the record is missing
    rather than dropping the proposal, because a proposal pointing at nothing
    is a finding and a silently shorter list is not.
    """
    if record is None:
        return IdentitySide(
            id=object_id_, ref=f"graph:{object_id_}", type="", label="",
            natural_key="", status="missing", fields={}, sources=[],
            conflicted=[],
        )
    return IdentitySide(
        id=record.id,
        ref=record.ref(),
        type=record.type,
        label=record.label or record.natural_key,
        natural_key=record.natural_key,
        status=record.status,
        fields={k: str(v) for k, v in record.props.items()},
        sources=_asserted_by(graph, record),
        conflicted=sorted(record.conflicts()),
    )


def _proposal_gate(task, principal: Principal) -> ProposalGate:
    """Where this proposal's decision lives, and whether it is this persona's.

    Three states: there is a Task and it is yours, there is a Task and it is
    somebody else's, and there is no Task at all. Only the first draws
    controls; the other two are sentences.

    The third is no longer a confidence story — every proposal raises a Task
    now — so it reads as the anomaly it is, with what closes it. It takes no
    threshold argument any more precisely so that this sentence cannot quietly
    go back to quoting a bar that does not gate anything.
    """
    if task is None:
        return ProposalGate(
            task_id="", task_status="", queue="", may_decide=False,
            why_not=(
                "No Task carries this proposal, so there is nowhere to decide "
                "it. The sweep raises one for every proposal it writes, in "
                "the queue that owns the book — this one predates that or its "
                "Task is gone. The next sweep over this book raises it."
            ),
        )
    status = task.status if task.status == "open" else (
        task.decision.verdict if task.decision is not None else task.status
    )
    if task.status != "open":
        return ProposalGate(
            task_id=task.id, task_status=status, queue=task.queue,
            may_decide=False,
            why_not=f"Already decided in {task.queue}; the record is below.",
        )
    mine = principal.owns_queue(task.queue)
    bound = ", ".join(principal.queues) or "no queue"
    return ProposalGate(
        task_id=task.id, task_status=status, queue=task.queue,
        may_decide=mine,
        why_not=(
            "" if mine
            else f"this is {task.queue}'s work; you are bound to {bound}"
        ),
    )


def _compare(left: IdentitySide, right: IdentitySide) -> tuple[list[str], list[str]]:
    """Fields both records hold, split into agreeing and disagreeing.

    Only fields *both* sides carry are compared: one side holding a phone the
    other has never had is not a disagreement, and counting it as one would
    make every pair look conflicted.
    """
    shared = sorted(set(left.fields) & set(right.fields))
    agree = [f for f in shared if left.fields[f].strip().lower() == right.fields[f].strip().lower()]
    return agree, [f for f in shared if f not in set(agree)]


def _last_sweep() -> SweepRun | None:
    """The most recent pass, or nothing. Never a zeroed row standing in for one."""
    runs = deps.get_run_engine().list(flow_id=IDENTITY_SWEEP_FLOW_ID)
    if not runs:
        return None
    latest = max(runs, key=lambda r: r.started_at_iso)
    outputs = latest.outputs or {}
    return SweepRun(
        run_id=latest.id,
        at_iso=latest.started_at_iso,
        status=latest.status,
        compared=int(outputs.get("compared", 0) or 0),
        considered=int(outputs.get("considered", 0) or 0),
        proposed=int(outputs.get("proposed", 0) or 0),
        standing=int(outputs.get("standing", 0) or 0),
        steward_tasks=int(outputs.get("steward_tasks", 0) or 0),
        ratifications=int(outputs.get("steward_ratifications", 0) or 0),
    )


@router.get("/identity", response_model=IdentityScreen)
def identity(
    principal: Principal = Depends(get_identity_principal),
) -> IdentityScreen:
    """What is proposed, what was decided, and what the evidence said.

    One call, because the three are one question. The proposal is an edge, the
    evidence is on the edge, the records are the two objects it joins, and
    whether you may answer it is a property of a Task somewhere else entirely —
    a screen that fetched those separately would be stitching the argument
    together in the client, where it could drift.
    """
    graph = deps.get_graph_service()
    thresholds = read_sweep_thresholds(deps.get_service())
    tasks_by_case = {
        task.case_ref: task
        for task in deps.get_task_service().list(kind="merge_review")
        if task.case_ref
    }
    desks = sorted(q for q in identity_desks() if principal.owns_queue(q))

    standing: list[IdentityProposal] = []
    decided: list[IdentityProposal] = []
    for link in graph.store.links():
        scan = SCAN_BY_LINK.get(link.type)
        if scan is None:
            continue
        status = _proposal_status(link)
        left = _side(graph, graph.store.get_object(link.from_id), link.from_id)
        right = _side(graph, graph.store.get_object(link.to_id), link.to_id)
        agree, conflict = _compare(left, right)
        case_ref = f"link:{link.type}/{link.id}"
        proposal = IdentityProposal(
            link_id=link.id,
            link_type=link.type,
            case_ref=case_ref,
            pair_kind=str(link.props.get("pair_kind") or scan.kind),
            status=status,
            confidence=link.confidence,
            method=link.method,
            signals=[
                IdentitySignal(
                    name=str(s.get("name") or ""),
                    weight=float(s.get("weight") or 0),
                    detail=str(s.get("detail") or ""),
                )
                for s in (link.props.get("signals") or [])
                if isinstance(s, dict)
            ],
            reversible_with=str(link.props.get("reversible_with") or ""),
            policy_ref=str(link.props.get("policy_ref") or ""),
            proposed_at=str(link.props.get("proposed_at") or link.valid_from_iso),
            run_id=str(link.props.get("run_id") or link.run_id),
            # A standing proposal has no decider. The sweep stamps itself as
            # `decided_by` so the edge always names who stands behind it, and
            # reporting that as a *decision* would put a machine's name where a
            # person's belongs.
            decided_by=(
                "" if status == "in_review"
                else (
                    link.reversed_by if status == "withdrawn"
                    else str(link.props.get("decided_by") or "")
                )
            ),
            decided_at=(
                "" if status == "in_review"
                else (
                    link.reversed_at_iso if status == "withdrawn"
                    else str(link.props.get("decided_at") or "")
                )
            ),
            withdrawn_because=(
                link.reversal_reason if status == "withdrawn" else ""
            ),
            left=left, right=right, agree=agree, conflict=conflict,
            gate=_proposal_gate(tasks_by_case.get(case_ref), principal),
        )
        (standing if status == "in_review" else decided).append(proposal)

    standing.sort(key=lambda p: (-p.confidence, p.left.label))
    decided.sort(key=lambda p: p.decided_at, reverse=True)

    # Every count on a book is answered by rows in the lists above it: the
    # books are counted *from* the proposals rather than re-queried, so the
    # two cannot disagree.
    booked_to = {
        link_type: scan.from_type for link_type, scan in SCAN_BY_LINK.items()
    }
    books: list[IdentityBook] = []
    for type_name in dict.fromkeys(scan.from_type for scan in SCANS):
        declared = ONTOLOGY.object_type(type_name)
        queue = steward_queue_for_type(type_name)
        mine = [p for p in standing if booked_to[p.link_type] == type_name]
        ruled = [p for p in decided if booked_to[p.link_type] == type_name]
        books.append(IdentityBook(
            type=type_name,
            label=declared.label if declared is not None else type_name,
            steward_queue=queue,
            objects=sum(
                1 for o in graph.find(type_name) if o.status != "superseded"
            ),
            standing=len(mine),
            confirmed=sum(1 for p in ruled if p.status == "confirmed"),
            withdrawn=sum(1 for p in ruled if p.status == "withdrawn"),
            yours=principal.owns_queue(queue),
        ))

    return IdentityScreen(
        standing=standing,
        decided=decided[:DECIDED_WINDOW],
        decided_total=len(decided),
        books=books,
        policy=SweepPolicy(
            propose_at=thresholds.propose_at,
            ratify_at=thresholds.ratify_at,
            name_floor=thresholds.name_floor,
            ceiling=thresholds.ceiling,
            source_ref=thresholds.source_ref,
            found_asset=thresholds.found_asset,
        ),
        last_sweep=_last_sweep(),
        desks=desks,
        curates=bool(desks),
    )


# ---------------------------------------------------------------------------
# Cross-system disagreement — the order desk's case
# ---------------------------------------------------------------------------
#
# The identity workbench's sibling, and the difference between the two is the
# whole of the write plane. There, two rows *might* be one party and a steward
# says whether they are; what changes is what Cumulus believes. Here the two
# rows are certainly about one commitment, the customer's own two systems say
# different things about it, and the decision reaches out of Cumulus into an
# ERP that is not ours.
#
# So this composition carries one thing the identity one does not: **what the
# write would do**, read off `estate_writes.WRITABLE` through
# `order_release.read_write_policy` rather than described in prose here. The
# before/after a controller reads is the same declaration the release flow
# commits through, which makes "the screen promised a transition the table does
# not permit" a thing that cannot happen rather than a thing we remember not to
# do.
#
# Deliberately **not** a Workspace and not a rail stop. One class with one live
# instance does not earn a Seeing Space; that arrives when the class table has
# three rows. This is the payload behind two surfaces — the `state_disagreement`
# review screen and the dashboard's Judgment panel — for the merge review's
# reason: one composition, so the modal a controller decides in and the panel
# that counts the backlog can never describe one finding differently.


def may_open_disagreements(principal: Principal) -> bool:
    """Whether there is a disagreement case for this principal to read.

    `may_open_identity`'s rule, for `may_open_identity`'s reason, over a
    different desk: the one persona who may release an order is the Controller,
    and the Controller is own-scoped. Gating on read reach alone would refuse
    the screen's only user.
    """
    return principal.reads_beyond_self or principal.owns_queue(ORDER_DESK_QUEUE)


def get_disagreement_principal(
    principal: Principal = Depends(get_ui_principal),
) -> Principal:
    """The order desk's door. See `may_open_disagreements` for the rule."""
    if may_open_disagreements(principal):
        return principal
    raise HTTPException(
        status_code=403,
        detail=(
            "A disagreement between two of the customer's systems is the "
            f"{ORDER_DESK_QUEUE}'s to settle. You do not hold that desk, and "
            "your access is to your own work — so there is nothing here for "
            "you rather than something withheld."
        ),
    )


class DisagreementFact(BaseModel):
    """One labelled fact off one book's row.

    A list of label/value pairs rather than a dict of props, because the two
    columns this screen draws do **not** share a vocabulary — a deal has a
    stage, an amount and a close date; an order has a status, a total and an
    order date — so there is no shared key to compare on, and pretending there
    is one would produce a table of empty cells.
    """

    label: str
    value: str


class DisagreementSide(BaseModel):
    """One system's account of the commitment, as a person reads it."""

    id: str
    ref: str
    type: str
    label: str
    natural_key: str
    #: Which book this is, in the words the screen heads the column with.
    book: str
    #: The state the finding turns on, in the ontology's slugged vocabulary —
    #: `closed-won`, `pending-approval`.
    state: str
    #: What this side held when the finding was written. Equal to `state` on a
    #: finding that is still true, and the whole point when it is not: a
    #: decision made a week later has to be able to tell "this is the case"
    #: from "this was the case".
    state_when_found: str
    facts: list[DisagreementFact]
    #: Which feeds assert this row exists. The identity workbench's derivation.
    sources: list[str]
    #: True when the finding points at a row the book no longer holds.
    missing: bool


class DisagreementChange(BaseModel):
    """What approving writes, and what it leaves exactly as it is.

    Every field here is read off the write table, not written here. `unchanged`
    is *derived* — the ERP facts beside it minus the one field the PATCH sets —
    so it cannot drift from the column it is about, and a second writable field
    would appear in both places at once or in neither.
    """

    #: The one field the PATCH sets.
    field: str
    #: The ERP's own words on both sides of the arrow, off the table's
    #: transitions — not the graph's slug, because the slug is Cumulus's
    #: vocabulary and this sentence is a promise about somebody else's system.
    from_value: str
    to_value: str
    #: Everything the screen shows about the order that this write does not
    #: touch, in the labels the column used.
    unchanged: list[str]
    target: str
    app: str
    resource: str
    #: How the ERP addresses this row. Off `external_id`, never off the
    #: transaction id.
    record_id: str
    #: Whether the write plane permits this write today.
    permitted: bool
    #: Why not, when not — always a sentence. Three sources, and they are
    #: different kinds of fact: the write table refusing its own declaration,
    #: the order carrying no ERP address, or the book's copy of the row having
    #: moved out of a state the table may move an order out of.
    why_not: str


class DisagreementGate(BaseModel):
    """Whether this finding can be decided, by whom, and where.

    `ProposalGate`'s sibling and not `ProposalGate` itself: the three states
    are the same three and the sentences are not. A merge proposal with no Task
    is an anomaly the next sweep repairs; a *finding* with no Task may equally
    be one the desk already settled, and telling a controller to sweep again
    would be telling them to re-raise a decision they made.
    """

    task_id: str
    #: `open`, or the verdict the desk already recorded.
    task_status: str
    queue: str
    may_decide: bool
    why_not: str


class DisagreementCase(BaseModel):
    """One finding: the two rows, the evidence, the write, and where it stands."""

    link_id: str
    link_type: str
    #: `link:<type>/<id>` — the ref the Task carries as its `case_ref`, so the
    #: review screen finds its case without a second contract.
    case_ref: str
    finding_class: str
    #: `standing` · `resolved` · `dismissed` · `superseded`, off the edge.
    status: str
    #: The correlation score and the evidence behind it, untouched by any
    #: decision — closing a finding does not rewrite how good it was.
    confidence: float
    method: str
    signals: list[IdentitySignal]
    policy_ref: str
    detected_at: str
    run_id: str
    counterparty: str
    #: The two books, named for what they are rather than left/right: this
    #: screen is read across a labelled boundary, not down a symmetric pair.
    crm: DisagreementSide
    erp: DisagreementSide
    change: DisagreementChange
    #: True when either row has moved since the finding was written. Not a
    #: refusal — the release re-reads the ERP itself and escalates — but the
    #: one fact a controller must have before pressing anything.
    moved_since_found: bool
    #: How it ended, on a resolved one: `by_cumulus` or `externally`. Empty on
    #: every other status, `dismissed` included — that one ended by a judgment
    #: rather than by the ERP holding the released value.
    resolved_how: str
    decided_at: str
    decided_by: str
    #: The desk's own words, off the edge — the note they typed, or the
    #: precondition's reason on a release that found the work already done.
    resolution: str
    gate: DisagreementGate


class StateSweepRun(BaseModel):
    """The last pass over the two books. `None` when there has never been one.

    Every field is a number the sweep already published in its run outputs.
    Nothing here is re-derived, and `blocked_on_identity` could not be:
    it counts evidence a pass **declined to read**, which leaves no trace on
    the graph to walk afterwards, and it is the number that says out loud what
    unfinished identity work is costing.
    """

    run_id: str
    at_iso: str
    status: str
    orders_examined: int
    candidates: int
    found: int
    blocked_on_identity: int
    order_without_deal: int
    ambiguous: int
    desk_tasks: int


class DisagreementScreen(BaseModel):
    standing: list[DisagreementCase]
    decided: list[DisagreementCase]
    #: What `decided` is a window onto. Equal to `len(decided)` until the
    #: history outgrows `DECIDED_WINDOW`.
    decided_total: int
    last_sweep: StateSweepRun | None
    #: True when this persona holds the desk rather than reading over its
    #: shoulder.
    yours: bool
    desk: str


def _asserted_by(graph: GraphService, record: ObjectRecord) -> list[str]:
    """Which feeds say this row exists. A record only one system has ever seen
    is a different kind of row from one three systems agree on.

    Two readings, because two kinds of type answer differently and only one of
    them used to be asked. A **counterparty** is bound to the rows its sources
    sent through a declared identity edge, and walking that edge is the answer.
    A **transaction** — an order, a deal — has no such edge and never will:
    nothing proposes merges between sales orders, so no `SourceRecord` link was
    ever declared for one. What it has is claims, and every claim carries the
    namespaced feed that made it.

    Reading only the edge left this blank for an order, which the console then
    drew as "no source record" beside a row that had plainly arrived from the
    ERP. The bare feed name is taken off the claim's `authority:feed` source so
    both readings render in one vocabulary.
    """
    declared = ONTOLOGY.source_identity_link(record.type)
    if declared is not None:
        return sorted({
            str(other.props.get("source") or "")
            for _link, other in graph.neighbors(record.id, declared.name, "out")
            if other.props.get("source")
        })
    return sorted({
        claim.source.split(":", 1)[-1]
        for claim in record.claims
        if claim.source
    })


def _finding_status(link: LinkRecord) -> str:
    """Where the finding stands, off the edge itself.

    Liveness first, for `_proposal_status`' reason — and what a dead edge means
    here is the difference. A withdrawn merge proposal is a steward saying "not
    one party"; a finding is never withdrawn by a decision at all, since both
    verdicts write a status onto a live edge. So an edge that is not live is
    one a later pass superseded.
    """
    if not link.live:
        return "superseded"
    return str(link.props.get("status") or STANDING)


def _money(value: object) -> str:
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return str(value or "")


def _missing_side(
    object_id_: str, book: str, state_when_found: str
) -> DisagreementSide:
    """A row the book no longer holds. Named rather than dropped, for `_side`'s
    reason: a finding pointing at nothing is itself a finding."""
    return DisagreementSide(
        id=object_id_, ref=f"graph:{object_id_}", type="", label="",
        natural_key="", book=book, state="",
        state_when_found=state_when_found, facts=[], sources=[], missing=True,
    )


def _deal_side(
    graph: GraphService, record: ObjectRecord | None, object_id_: str,
    state_when_found: str,
) -> DisagreementSide:
    """The CRM's half: what the pipeline says was committed to.

    Three facts, and they are the three the correlation turned on — the stage
    that made it a disagreement, and the amount and close date the window and
    the containment band were measured against. A column of everything the deal
    holds would bury the evidence in the row beside it.
    """
    if record is None:
        return _missing_side(object_id_, "the CRM", state_when_found)
    props = record.props
    return DisagreementSide(
        id=record.id, ref=record.ref(), type=record.type,
        label=record.label or record.natural_key,
        natural_key=record.natural_key,
        book="the CRM",
        state=str(props.get("stage") or ""),
        state_when_found=state_when_found,
        facts=[
            DisagreementFact(label="Stage", value=str(props.get("stage") or "")),
            DisagreementFact(label="Amount", value=_money(props.get("amount_usd"))),
            DisagreementFact(
                label="Close date", value=str(props.get("close_date") or "")
            ),
        ],
        sources=_asserted_by(graph, record),
        missing=False,
    )


def _order_side(
    graph: GraphService, record: ObjectRecord | None, object_id_: str,
    state_when_found: str,
) -> DisagreementSide:
    """The ERP's half: what the order book says was released.

    The line count is read rather than the lines. What the release does *not*
    touch is the question this column answers, and "3" answers it where three
    rows of SKUs would bury it.
    """
    if record is None:
        return _missing_side(object_id_, "the ERP", state_when_found)
    props = record.props
    lines = len(graph.neighbors(record.id, "has_so_line", "out"))
    return DisagreementSide(
        id=record.id, ref=record.ref(), type=record.type,
        label=record.label or record.natural_key,
        natural_key=record.natural_key,
        book="the ERP",
        state=str(props.get("status") or ""),
        state_when_found=state_when_found,
        facts=[
            DisagreementFact(label="Status", value=str(props.get("status") or "")),
            DisagreementFact(label="Total", value=_money(props.get("total_usd"))),
            DisagreementFact(label="Lines", value=f"{lines}"),
            DisagreementFact(
                label="Order date", value=str(props.get("ordered_date") or "")
            ),
            DisagreementFact(
                label="Customer", value=str(props.get("company_name") or "")
            ),
            DisagreementFact(label="Memo", value=str(props.get("memo") or "")),
        ],
        sources=_asserted_by(graph, record),
        missing=False,
    )


def _change(service, case_ref: str, erp: DisagreementSide) -> DisagreementChange:
    """What approving would write, off the write table.

    `read_write_policy` is the release flow's own first step, called here for
    exactly the reason the merge review calls the identity route: the sentence
    on the button and the declaration the Action commits through have to be one
    thing. It reads the graph and the table and touches neither, and it reaches
    no customer system — the live status is re-read at release time, by the run,
    which is why nothing below claims to know what the ERP holds right now.
    """
    blank = DisagreementChange(
        field="", from_value="", to_value="", unchanged=[], target="",
        app="", resource="", record_id="", permitted=False, why_not="",
    )
    try:
        policy = read_write_policy(service, case_ref)
    except LookupError as exc:
        return blank.model_copy(update={"why_not": str(exc)})

    field = str(policy.get("field") or "")
    unchanged = [
        fact.label.lower() for fact in erp.facts
        if fact.label.lower() != field.lower() and fact.value
    ]
    change = blank.model_copy(update={
        "field": field,
        "target": policy["target"],
        "app": str(policy.get("app") or ""),
        "resource": str(policy.get("resource") or ""),
        "record_id": policy["record_id"],
        "unchanged": unchanged,
    })
    if not policy["writable"]:
        return change.model_copy(update={"why_not": policy["refusal"]})

    # Which row of the table this order is standing on. Matched by slugging the
    # table's own keys with `sync._slug` — the function that turned the ERP's
    # `Pending Approval` into the graph's `pending-approval` on the way in is
    # the only honest thing to map back with.
    observed = policy["graph_status"]
    for permitted, destinations in policy.get("transitions", {}).items():
        if _slug(permitted) == observed and len(destinations) == 1:
            return change.model_copy(update={
                "from_value": permitted,
                "to_value": destinations[0],
                "permitted": True,
            })
    return change.model_copy(update={
        "why_not": (
            f"Cumulus's copy of {policy['order_number']} reads "
            f"`{observed or 'nothing'}`, which the write table may not move an "
            "order out of. Approving would re-read the ERP and raise what it "
            "finds to the desk rather than write."
        ),
    })


def _disagreement_gate(task, principal: Principal) -> DisagreementGate:
    """Where this finding's decision lives, and whether it is this persona's.

    `_proposal_gate`'s three states, with the third one saying something else.
    A finding whose Task is gone is not necessarily an anomaly: the sweep
    refuses to re-raise a finding that is not `standing`, which is exactly what
    makes a dismissal stick — so the sentence names both readings rather than
    telling a controller to sweep again.
    """
    if task is None:
        return DisagreementGate(
            task_id="", task_status="", queue="", may_decide=False,
            why_not=(
                "No Task carries this finding, so there is nowhere to decide "
                "it. The sweep raises one for every finding it writes at "
                f"`{STANDING}`, and never re-raises one that has been settled "
                "— so this either predates that or its Task was purged."
            ),
        )
    status = task.status if task.status == "open" else (
        task.decision.verdict if task.decision is not None else task.status
    )
    if task.status != "open":
        return DisagreementGate(
            task_id=task.id, task_status=status, queue=task.queue,
            may_decide=False,
            why_not=f"Already decided in {task.queue}; the record is below.",
        )
    mine = principal.owns_queue(task.queue)
    bound = ", ".join(principal.queues) or "no queue"
    return DisagreementGate(
        task_id=task.id, task_status=status, queue=task.queue,
        may_decide=mine,
        why_not=(
            "" if mine
            else f"this is {task.queue}'s work; you are bound to {bound}"
        ),
    )


def _state_sweep() -> StateSweepRun | None:
    """The most recent disagreement pass, or nothing.

    `_last_sweep`'s sibling, and never a zeroed row standing in for a pass that
    did not happen — a `blocked_on_identity` of 0 over a book nothing has swept
    is the most misleading number this panel could draw.
    """
    runs = deps.get_run_engine().list(flow_id=DISAGREEMENT_SWEEP_FLOW_ID)
    if not runs:
        return None
    latest = max(runs, key=lambda r: r.started_at_iso)
    out = latest.outputs or {}
    return StateSweepRun(
        run_id=latest.id,
        at_iso=latest.started_at_iso,
        status=latest.status,
        orders_examined=int(out.get("orders_examined", 0) or 0),
        candidates=int(out.get("candidates", 0) or 0),
        found=int(out.get("found", 0) or 0),
        blocked_on_identity=int(out.get("blocked_on_identity", 0) or 0),
        order_without_deal=int(out.get("order_without_deal", 0) or 0),
        ambiguous=int(out.get("ambiguous", 0) or 0),
        desk_tasks=int(out.get("desk_tasks", 0) or 0),
    )


def _counterparty(graph: GraphService, order_id: str) -> str:
    """Whose commitment this is, off the edge the sweep joined the pair on.

    The edge and not the order's `company_name`, which is denormalized for
    cheap reads and carries the ERP's own spelling. The join that produced this
    finding ran on `sold_to` — naming the party by anything else would let the
    screen and the sweep disagree about who it is about, in exactly the
    situation where two spellings of one company is the whole problem.
    """
    for _link, company in graph.neighbors(order_id, SCAN.to_edge, "out"):
        return company.label or company.natural_key
    return ""


@router.get("/disagreements", response_model=DisagreementScreen)
def disagreements(
    principal: Principal = Depends(get_disagreement_principal),
) -> DisagreementScreen:
    """Every finding the two books have produced, standing and settled.

    One call, for `identity`'s reason: the finding is an edge, the evidence is
    on the edge, the two rows are the objects it joins, the write is a table
    entry, and whether you may answer it is a property of a Task somewhere
    else. Fetched separately the client would be assembling the argument — and
    a controller would be reading a before/after stitched from five instants.
    """
    graph = deps.get_graph_service()
    # The sync service and not the knowledge one: `read_write_policy` walks the
    # finding's edge to the order it names, which is a graph read.
    service = deps.get_sync_service()
    tasks_by_case = {
        task.case_ref: task
        for task in deps.get_task_service().list(kind="state_disagreement")
        if task.case_ref
    }

    standing: list[DisagreementCase] = []
    decided: list[DisagreementCase] = []
    for link in graph.store.links():
        if link.type != SCAN.link_type:
            continue
        found_in = dict(link.props.get("state_when_found") or {})
        crm = _deal_side(
            graph, graph.store.get_object(link.from_id), link.from_id,
            str(found_in.get("opportunity_stage") or ""),
        )
        erp = _order_side(
            graph, graph.store.get_object(link.to_id), link.to_id,
            str(found_in.get("order_status") or ""),
        )
        status = _finding_status(link)
        case_ref = f"link:{link.type}/{link.id}"
        case = DisagreementCase(
            link_id=link.id,
            link_type=link.type,
            case_ref=case_ref,
            finding_class=str(link.props.get("class") or ""),
            status=status,
            confidence=link.confidence,
            method=link.method,
            # The sweep records its evidence as `identity_sweep.Signal`, which
            # `disagreement_sweep` imports rather than copies — so the console
            # reads both passes back through the one model.
            signals=[
                IdentitySignal(
                    name=str(s.get("name") or ""),
                    weight=float(s.get("weight") or 0),
                    detail=str(s.get("detail") or ""),
                )
                for s in (link.props.get("signals") or [])
                if isinstance(s, dict)
            ],
            policy_ref=str(link.props.get("policy_ref") or ""),
            detected_at=str(link.props.get("detected_at") or link.valid_from_iso),
            run_id=str(link.props.get("run_id") or link.run_id),
            counterparty=_counterparty(graph, link.to_id),
            crm=crm, erp=erp,
            change=_change(service, case_ref, erp),
            moved_since_found=(
                (bool(crm.state_when_found) and crm.state != crm.state_when_found)
                or (bool(erp.state_when_found) and erp.state != erp.state_when_found)
            ),
            resolved_how=str(link.props.get("resolved_how") or ""),
            # A standing finding names no decider. The sweep stamps itself on
            # the edge so it always says who stands behind it, and reporting
            # that as a decision would put a machine's name where a person's
            # belongs — `IdentityProposal`'s rule, for its reason.
            decided_at=(
                "" if status == STANDING
                else str(
                    link.props.get("resolved_at")
                    or link.props.get("dismissed_at") or ""
                )
            ),
            decided_by=(
                "" if status == STANDING
                else str(
                    link.props.get("resolved_by")
                    or link.props.get("dismissed_by") or ""
                )
            ),
            resolution=(
                "" if status == STANDING
                else str(link.props.get("resolution") or "")
            ),
            gate=_disagreement_gate(tasks_by_case.get(case_ref), principal),
        )
        (standing if status == STANDING else decided).append(case)

    standing.sort(key=lambda c: (-c.confidence, c.erp.label))
    decided.sort(key=lambda c: c.decided_at, reverse=True)
    return DisagreementScreen(
        standing=standing,
        decided=decided[:DECIDED_WINDOW],
        decided_total=len(decided),
        last_sweep=_state_sweep(),
        yours=principal.owns_queue(ORDER_DESK_QUEUE),
        desk=ORDER_DESK_QUEUE,
    )


# ---------------------------------------------------------------------------
# System state — the whole deployment, read off the surfaces that already hold it
# ---------------------------------------------------------------------------
#
# Four questions in the order an operator asks them: **what does the map hold**,
# **is the outside world arriving**, **is judgment flowing**, **is governance
# holding**. Nothing here is a new record. Every figure is composed from a
# surface that already owns it — the graph's own counts, the Sources rows, the
# task queue, the ledger, the flow projections — so a number on this screen is
# always answerable by a list one rail stop away, and the two can never drift
# apart because there is only one of them.
#
# What that rules out is the thing a dashboard is normally for. There is no
# second bookkeeping table behind this route, no history it keeps of itself, and
# no figure derived from an assumption: a quantity that cannot be read out of
# stored state is a quantity this screen does not show. The places where it
# would like to say more and cannot — spend nobody priced, a conformance report
# a tenant may not read — say *that* instead, which is the honest shape of the
# same tile.


#: The window every rate on this screen is taken over. One window for the whole
#: screen rather than one per tile: two panels quoting different windows read as
#: one panel contradicting another, and the reader has no way to tell.
DASHBOARD_WINDOW_DAYS = 30

#: Ledger entries read for the window count. The store is newest-first, so this
#: is a window onto the newest entries and `truncated` says when the window was
#: too small to hold the days asked for — the `scanned_runs` idiom, applied to
#: the one list here that grows without bound.
LEDGER_SCAN = 1000

#: How a governed write can have ended, which is what the window counts. Named
#: once rather than spelled out at each bucket: the shape this replaced wrote
#: `{"committed": 0, "refused": 0}` at every use and counted `entry.outcome in
#: bucket`, so an outcome the Gateway had begun writing was dropped by all of
#: them without a word. `pending` is the stub state and is never stored.
LEDGER_OUTCOMES: tuple[str, ...] = ("committed", "refused", "failed")


def _age_seconds(iso: str) -> int | None:
    """How long ago, or `None` when there is no timestamp to subtract.

    `None` rather than a large number, because "never" and "a long time ago"
    are opposite readings of a feed and only one of them is about staleness.
    Naive stamps are read as UTC, which is what every writer in this process
    produces; a stamp that will not parse is reported as absent rather than as
    a wrong age.
    """
    if not iso:
        return None
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, int((utcnow() - when).total_seconds()))


# ---- 1 · what the map holds ------------------------------------------------


class TypeState(BaseModel):
    """One object type, as the map holds it now."""

    name: str
    label: str
    instances: int
    #: Instances where two sources assert the same field differently. The
    #: precedence order picks a winner to read, and this is how many rows are
    #: standing on one — outstanding disagreement, not resolved history.
    conflicted: int


class CollectionState(BaseModel):
    """A Collection, and what is inside it.

    Grouped by Collection because that is the unit governance hangs off: a
    retention horizon, a custody decision and an authoritativeness claim are
    properties of the set, so a count grouped any other way is a count of
    nothing anybody governs.
    """

    id: str
    label: str
    role: str
    origin: str
    custody: str
    retention: str
    authoritative: bool
    types: list[TypeState]
    objects: int
    conflicted: int


class MapState(BaseModel):
    objects: int
    links: int
    conflicted: int
    collections: list[CollectionState]
    #: TBox violations over the whole graph, and their kinds. `None` — never a
    #: reassuring zero — for a tenant-scoped persona, on the same reading the
    #: Ontology screen's `conformance` uses: a violation is a Naviant bug, and
    #: an alarm the reader can take no action on is worse than no alarm.
    invariants: int | None
    invariant_kinds: dict[str, int] | None


def _map_state(principal: Principal) -> MapState:
    """The book, by Collection, from one pass over the store.

    `_graph_counts` is the same pass the Ontology screen counts from, so a type
    reading 135 here reads 135 there. That is the point of borrowing it rather
    than counting again.
    """
    graph = deps.get_graph_service()
    by_type, conflicted, by_link = _graph_counts(graph)

    members: dict[str, list[TypeState]] = {}
    for declared in ONTOLOGY.objects:
        governing = data_collections.for_object_type(declared.name)
        members.setdefault(
            governing.id if governing is not None else UNGOVERNED, []
        ).append(TypeState(
            name=declared.name, label=declared.label,
            instances=by_type.get(declared.name, 0),
            conflicted=conflicted.get(declared.name, 0),
        ))

    collections: list[CollectionState] = []
    for collection in data_collections.COLLECTIONS:
        types = members.get(collection.id)
        if not types:
            # A Collection claiming no object type governs a container that is
            # not the graph — the corpus, the task ledger, the run journals'
            # neighbours. Counting graph objects for it would be a zero about
            # the wrong store.
            continue
        collections.append(CollectionState(
            id=collection.id, label=collection.name, role=collection.role,
            origin=collection.origin, custody=collection.custody,
            retention=collection.retention,
            authoritative=collection.authoritative,
            types=sorted(types, key=lambda t: -t.instances),
            objects=sum(t.instances for t in types),
            conflicted=sum(t.conflicted for t in types),
        ))
    if UNGOVERNED in members:
        types = members[UNGOVERNED]
        log.warning(
            "Object types governed by no Collection",
            extra={"types": [t.name for t in types]},
        )
        collections.append(CollectionState(
            id=UNGOVERNED, label="Ungoverned — no Collection claims these",
            role="", origin="", custody="", retention="",
            authoritative=False,
            types=sorted(types, key=lambda t: -t.instances),
            objects=sum(t.instances for t in types),
            conflicted=sum(t.conflicted for t in types),
        ))

    violations = graph.check_invariants() if principal.sees_everything else None
    kinds: dict[str, int] | None = None
    if violations is not None:
        kinds = {}
        for violation in violations:
            kinds[violation.kind] = kinds.get(violation.kind, 0) + 1
    return MapState(
        objects=sum(by_type.values()),
        links=sum(by_link.values()),
        conflicted=sum(conflicted.values()),
        collections=collections,
        invariants=None if violations is None else len(violations),
        invariant_kinds=kinds,
    )


# ---- 2 · is the outside world arriving -------------------------------------


class EntityCount(BaseModel):
    """A type one feed resolves to, and what the book holds of it now.

    Beside `records_landed` and `created` this is the number that explains the
    screen's most misreadable gap: a feed that minted 14 vendor rows against a
    book holding 13 has not dropped one. The name is the natural key, two ERP
    rows carry the same one, and they are one Vendor by construction.

    The comparison survives a shared book, which is why it is worth showing at
    all: another feed writing the same type can only make `instances` *larger*,
    so minting more rows than the book holds proves a collapse however many
    feeds resolve against it.
    """

    type: str
    instances: int
    #: How many Mappings write into this type — resolving against it on
    #: `entities`, landing into it on `landed_type`. More than one and the book
    #: is shared, so `instances` is not this feed's output: the mail and
    #: calendar feeds resolve *against* Employee without ever minting one, the
    #: ERP and the CRM both write the counterparty book, and four contracts
    #: land their rows as `SourceRecord`.
    feeds: int


class FeedState(BaseModel):
    """One inbound feed's health, composed from the Sources row.

    Two different facts share the word *unchanged* and this shape keeps them
    apart, because conflating them turns a healthy quiet feed into a broken
    one and back. `last_pull_outcome == "unchanged"` is the **connector**
    saying the estate reported no delta since the watermark — nothing was
    fetched and no run started. `unchanged_rows` counts **rows** that arrived
    byte-identical to what the map already held, which only happens on a pull
    that *did* deliver. A feed can have either without the other.
    """

    source: str
    kind: str
    flow_id: str
    #: `""` when no Mapping claims this feed — rows landing under no declared
    #: contract, which is the finding, not a missing field.
    contract_id: str
    transport: str
    #: The type a reified row becomes, and `""` for a **bind** mapping — whose
    #: rows become nothing of their own and go straight to the types in
    #: `entities`. Carried because that distinction is what makes
    #: `records_landed` comparable to what the book holds: a feed that lands
    #: `SourceRecord`s is not short of entities when it holds fewer of them.
    lands_as: str
    #: What the book holds of `lands_as`, on the same terms as `entities`. A
    #: reify feed's rows *become* this type, so the destination line can say
    #: "lands as PurchaseOrder 31 · resolves against Vendor 13" rather than
    #: printing the resolution targets under a heading about landing. `None`
    #: for a bind mapping, which lands nothing of its own.
    landed_type: EntityCount | None
    #: `None` for a feed that pushes; it has no app for discovery to confirm.
    active: bool | None
    last_run_at_iso: str
    last_run_age_seconds: int | None
    last_status: str
    last_error: str
    deliveries: int
    #: Rows the delivery carried, against rows the landing step kept. See
    #: `SourceRow`: on a reify feed these diverge by two thirds.
    records: int = 0
    records_landed: int
    #: `None` — never `0` — when this feed's contract does not report the
    #: disposition. See `sync.SourceStatus`.
    created: int | None = None
    linked: int | None = None
    unchanged_rows: int | None = None
    awaiting_steward: int
    entities: list[EntityCount]
    #: Whether Cumulus pulls this feed. A pushed feed keeps its own cadence and
    #: has no cursor here, so every pull field below stays empty as a fact
    #: about the arrangement rather than as missing data.
    pulls: bool
    last_pull_at: str
    last_pull_age_seconds: int | None
    last_pull_outcome: str
    last_pull_error: str
    #: The furthest cursor across the feed's resources — what the next pull
    #: asks from. Its age is how far back the estate has been read up to, which
    #: a delivery count cannot tell you.
    watermark: str
    watermark_age_seconds: int | None


class ArrivalsState(BaseModel):
    estate: EstateCard
    feeds: list[FeedState]
    #: Feeds arriving under no Mapping, named. The same rows as
    #: `contract_id == ""` above, counted once so the governance panel and this
    #: one cannot disagree about how many there are.
    without_contract: list[str]
    failing: list[str]


def _feed_states(rows: list[SourceRow]) -> list[FeedState]:
    """The Sources rows, with the ages and entity counts a dashboard reads by.

    Assembled from `_source_rows` rather than from a second walk of the runs:
    there is one answer to "what has this feed delivered", and the Sources
    screen owns it.
    """
    graph = deps.get_graph_service()
    by_type, _conflicted, _by_link = _graph_counts(graph)
    fed_by: dict[str, int] = {}
    lands_by: dict[str, int] = {}
    for mapping in MAPPINGS:
        for type_name in mapping.resolves_to:
            fed_by[type_name] = fed_by.get(type_name, 0) + 1
        if mapping.lands_as:
            lands_by[mapping.lands_as] = lands_by.get(mapping.lands_as, 0) + 1

    out: list[FeedState] = []
    for row in rows:
        pull = row.pull
        watermark = (
            max(pull.watermarks.values()) if pull and pull.watermarks else ""
        )
        contract = row.contract
        lands_as = contract.lands_as if contract else ""
        out.append(FeedState(
            source=row.source, kind=row.kind, flow_id=row.flow_id,
            contract_id=contract.id if contract else "",
            transport=contract.transport if contract else "",
            lands_as=lands_as,
            landed_type=(
                EntityCount(
                    type=lands_as,
                    instances=by_type.get(lands_as, 0),
                    feeds=lands_by.get(lands_as, 0),
                )
                if lands_as else None
            ),
            active=pull.active if pull else None,
            last_run_at_iso=row.last_run_at_iso,
            last_run_age_seconds=_age_seconds(row.last_run_at_iso),
            last_status=row.last_status, last_error=row.last_error,
            deliveries=row.deliveries, records=row.records,
            records_landed=row.records_landed,
            created=row.created, linked=row.linked,
            unchanged_rows=row.unchanged,
            awaiting_steward=row.awaiting_steward,
            entities=[
                EntityCount(
                    type=type_name,
                    instances=by_type.get(type_name, 0),
                    feeds=fed_by.get(type_name, 0),
                )
                for type_name in (contract.resolves_to if contract else ())
            ],
            pulls=pull is not None,
            last_pull_at=pull.last_pull_at if pull else "",
            last_pull_age_seconds=_age_seconds(pull.last_pull_at if pull else ""),
            last_pull_outcome=pull.last_outcome if pull else "",
            last_pull_error=pull.last_error if pull else "",
            watermark=watermark,
            watermark_age_seconds=_age_seconds(watermark),
        ))
    return out


# ---- 3 · is judgment flowing -----------------------------------------------


class QueueState(BaseModel):
    """One desk: what is on it, how long the oldest has waited, and who holds it.

    A queue with work and nobody bound to it is the finding this shape exists
    for. Tasks route to a queue and never to a person precisely so work
    survives absence — which also means an unbound queue accumulates silently,
    and no per-person inbox anywhere would ever show it.
    """

    queue: str
    open: int
    high_priority: int
    by_kind: dict[str, int]
    oldest_open_at_iso: str
    oldest_open_age_seconds: int | None
    #: Personas bound to this queue, by label. Read off the roster, so a
    #: rebinding moves the queue and this together.
    bound: list[str]


class RegimeCount(BaseModel):
    """Writes in the window under one regime, split by how they ended.

    Declared, never inferred: an observation, a decision and a resolution are
    indistinguishable by their effect on the graph, so the Action carries its
    regime and this counts what the Actions said. A refusal is counted beside
    a commit because a write the Gateway stopped is the spine doing its job —
    reporting only the commits would make a refusing deployment look idle.

    The third bucket is the one nobody wants and everybody needs: `refused` is
    the Gateway saying no and nothing happened, `failed` is a write it allowed
    that then broke. Summing them into one "not committed" would put the
    spine's successes and its outages behind the same number.
    """

    regime: str
    committed: int
    refused: int
    failed: int = 0


class LedgerWindow(BaseModel):
    days: int
    #: Entries read, newest first, and whether the read hit its limit. A count
    #: over a window that could not be fully scanned says so rather than
    #: quietly under-reporting.
    scanned: int
    truncated: bool
    in_window: int
    by_regime: list[RegimeCount]
    refused: int
    failed: int = 0
    newest_at_iso: str


class IdentityState(BaseModel):
    """What the counterparty books are waiting on, taken from the workbench.

    Composed by calling the identity route's own function rather than walking
    the links again: the workbench is the list this panel's numbers are
    answerable by, and a second derivation would be a second way to be wrong.
    """

    standing: int
    #: Standing proposals with an open Task carrying them — the ones a steward
    #: can actually answer.
    awaiting_decision: int
    #: Standing proposals with no Task at all. Every proposal the sweep writes
    #: raises one, so this is an anomaly rather than a policy: a proposal
    #: nobody was paged about and nowhere to decide it.
    without_task: int
    confirmed: int
    withdrawn: int
    books: list[IdentityBook]
    last_sweep: SweepRun | None


class DisagreementOutcomes(BaseModel):
    """What the order desk settled in the window, and how each one ended.

    Two numbers off one status rather than two statuses, which is the shape
    `order_release.RESOLVED` argues for: a finding that is over is `resolved`,
    and *how* it ended is a property of the resolution. `by_cumulus` is a write
    this system made and verified; `externally` is the ERP already holding the
    released value with nothing in the write journal asking for it — somebody
    else did it, and the desk's decision closed a finding rather than causing
    anything. Reporting them as one number would make a system that changed
    nothing indistinguishable from one that changed a customer's order.

    `dismissed` is beside them and not among them: it is the desk saying the
    two systems may disagree here, which settles the finding without either
    system moving.
    """

    days: int
    resolved: int
    by_cumulus: int
    externally: int
    dismissed: int


class DisagreementState(BaseModel):
    """What the two commercial books are waiting on. `IdentityState`'s sibling.

    Composed by calling the disagreement route's own function, for
    `IdentityState`'s reason: the screen is the list these numbers are
    answerable by, and a second derivation would be a second way to be wrong.

    The one number that is **not** derived from that list is
    `blocked_on_identity`, and the reason is worth reading. It counts evidence
    the sweep declined to admit because a merge nobody has confirmed was
    hiding it — a decision *not* to read, which leaves nothing on the graph to
    walk afterwards. The sweep publishes it; this reads it. The list it is
    answerable by is the identity workbench, one rail stop away, which is also
    the only place anybody can do anything about it.
    """

    standing: int
    #: Standing findings with an open Task carrying them — the ones the desk
    #: can actually answer.
    awaiting_decision: int
    #: Off the last sweep's own arithmetic. 0 with `last_sweep` set means the
    #: pass read everything it wanted; 0 with `last_sweep` None means nobody
    #: has asked.
    blocked_on_identity: int
    outcomes: DisagreementOutcomes
    last_sweep: StateSweepRun | None


class JudgmentState(BaseModel):
    open_tasks: int
    high_priority: int
    queues: list[QueueState]
    #: Queues holding open work that no persona is bound to.
    unowned_with_work: list[str]
    ledger: LedgerWindow
    identity: IdentityState
    #: The next question after identity, and only askable once identity has
    #: been answered: given that we know who the customer is, are the systems
    #: saying compatible things about what was committed to?
    disagreement: DisagreementState


def _known_queues(open_tasks: list) -> set[str]:
    """Every desk that exists, not only the ones with something on them.

    Four declarations name a queue and all four are queues an operator can be
    asked about: a persona's binding, a guarded Action's desk, a Mapping's
    escalation target, and a Task that actually landed. A list built from open
    Tasks alone would make a desk that has gone quiet indistinguishable from a
    desk that was never declared.
    """
    return (
        {q for p in personas.ALL_PERSONAS for q in p.queues}
        | {a.queue for a in ACTIONS.values() if a.queue}
        | {m.steward_queue for m in MAPPINGS if m.steward_queue}
        | {t.queue for t in open_tasks if t.queue}
    )


def _queue_states() -> list[QueueState]:
    tasks = deps.get_task_service().list(status="open")
    bound: dict[str, list[str]] = {}
    for persona in personas.ALL_PERSONAS:
        for queue in persona.queues:
            bound.setdefault(queue, []).append(persona.label)

    states: list[QueueState] = []
    for queue in sorted(_known_queues(tasks)):
        mine = [t for t in tasks if t.queue == queue]
        by_kind: dict[str, int] = {}
        for task in mine:
            by_kind[task.kind] = by_kind.get(task.kind, 0) + 1
        oldest = min(
            (t.created_at_iso for t in mine if t.created_at_iso), default=""
        )
        states.append(QueueState(
            queue=queue,
            open=len(mine),
            high_priority=sum(1 for t in mine if t.priority == "high"),
            by_kind=by_kind,
            oldest_open_at_iso=oldest,
            oldest_open_age_seconds=_age_seconds(oldest),
            bound=sorted(bound.get(queue, [])),
        ))
    return states


def _ledger_window(days: int) -> LedgerWindow:
    """Governed writes in the window, by regime, off the ledger itself.

    Read unscoped because this route is already refused to anyone who reads
    only their own work — the narrowing `ledger_api` applies per entry exists
    for a principal that reaches the ledger at all, and there is no such
    principal here.
    """
    entries = deps.get_action_gateway().ledger.list(limit=LEDGER_SCAN)
    cutoff = (utcnow() - timedelta(days=days)).isoformat()
    in_window = [e for e in entries if e.at_iso >= cutoff]
    counts = {
        regime: dict.fromkeys(LEDGER_OUTCOMES, 0)
        for regime in ("observation", "decision", "resolution")
    }
    for entry in in_window:
        bucket = counts.setdefault(entry.regime, dict.fromkeys(LEDGER_OUTCOMES, 0))
        if entry.outcome in bucket:
            bucket[entry.outcome] += 1
    return LedgerWindow(
        days=days,
        scanned=len(entries),
        truncated=len(entries) >= LEDGER_SCAN and len(in_window) == len(entries),
        in_window=len(in_window),
        by_regime=[
            RegimeCount(
                regime=regime, committed=c["committed"],
                refused=c["refused"], failed=c["failed"],
            )
            for regime, c in counts.items()
        ],
        refused=sum(c["refused"] for c in counts.values()),
        failed=sum(c["failed"] for c in counts.values()),
        newest_at_iso=entries[0].at_iso if entries else "",
    )


def _identity_state(principal: Principal) -> IdentityState:
    """The workbench's own composition, counted. See `IdentityState`.

    Safe to call directly: this route is gated on read reach, and read reach is
    one of the two ways `may_open_identity` lets a principal in — so anyone who
    reaches here would have been admitted to the workbench anyway.
    """
    screen = identity(principal=principal)
    return IdentityState(
        standing=len(screen.standing),
        awaiting_decision=sum(
            1 for p in screen.standing if p.gate.task_status == "open"
        ),
        without_task=sum(1 for p in screen.standing if not p.gate.task_id),
        confirmed=sum(1 for p in screen.decided if p.status == "confirmed"),
        withdrawn=sum(1 for p in screen.decided if p.status == "withdrawn"),
        books=screen.books,
        last_sweep=screen.last_sweep,
    )


def _disagreement_state(principal: Principal) -> DisagreementState:
    """The order desk's screen, counted. See `DisagreementState`.

    Safe to call directly for `_identity_state`'s reason: this route is gated
    on read reach, and read reach is one of the two ways
    `may_open_disagreements` lets a principal in.

    The window is counted off the rows the screen returned rather than by
    re-reading the graph with a date filter — `decided_total` says whether that
    list was truncated, which is the honest way to report a count over a window
    onto a window.
    """
    screen = disagreements(principal=principal)
    cutoff = (utcnow() - timedelta(days=DASHBOARD_WINDOW_DAYS)).isoformat()
    recent = [c for c in screen.decided if c.decided_at >= cutoff]
    sweep = screen.last_sweep
    return DisagreementState(
        standing=len(screen.standing),
        awaiting_decision=sum(
            1 for c in screen.standing if c.gate.task_status == "open"
        ),
        blocked_on_identity=sweep.blocked_on_identity if sweep else 0,
        outcomes=DisagreementOutcomes(
            days=DASHBOARD_WINDOW_DAYS,
            resolved=sum(1 for c in recent if c.resolved_how),
            by_cumulus=sum(1 for c in recent if c.resolved_how == BY_CUMULUS),
            externally=sum(1 for c in recent if c.resolved_how == EXTERNALLY),
            dismissed=sum(1 for c in recent if c.status == DISMISSED),
        ),
        last_sweep=sweep,
    )


# ---- 4 · is governance holding ---------------------------------------------


class FlowGap(BaseModel):
    """One flow whose declared footprint and its observed one disagree.

    Both directions are gaps and they are opposite problems. `undeclared_*` is
    a flow reading or writing something no Collection says it may — governance
    that is not covering what is happening. `unobserved_*` is a declaration no
    run has ever exercised — either a stale promise or a path nothing has
    tested. `state` is carried because a flow that has never run produces the
    second on every declaration it has, which is not the same finding.
    """

    flow_id: str
    label: str
    state: ProjectionState
    total_runs: int
    undeclared_writes: list[str]
    undeclared_reads: list[str]
    unobserved_writes: list[str]
    unobserved_reads: list[str]


class SpendState(BaseModel):
    """What the ModelCalls in the window cost, and whether that is a measurement.

    `basis` is the whole tile. A $0.00 has two readings pointing opposite ways
    — nothing called a model, or models were called and no call site recorded a
    price — and only the first is a fact about spending. `unrecorded` states
    itself rather than letting a confident zero pass as good news.
    """

    days: int
    runs: int
    model_calls: int
    priced_model_calls: int
    cost_usd: float
    tokens: int
    basis: flows.CostBasis


class GovernanceState(BaseModel):
    flows_total: int
    gaps: list[FlowGap]
    #: Named again from the arrivals panel's own list, so the two counts are
    #: one count.
    feeds_without_contract: list[str]
    #: Mappings naming a type or Collection that does not exist. Withheld from
    #: a tenant on the same reading as `MapState.invariants`.
    registry_problems: list[str] | None
    spend: SpendState


def _governance_state(
    principal: Principal, without_contract: list[str], days: int
) -> GovernanceState:
    engine = deps.get_run_engine()
    projections = project_flows(engine)
    names = {f.id: f.name for f in deps.get_flow_service().list() if f.name}
    gaps = [
        FlowGap(
            flow_id=p.flow_id, label=_flow_label(p.flow_id, names),
            state=p.state, total_runs=p.total_runs,
            undeclared_writes=p.undeclared_writes,
            undeclared_reads=p.undeclared_reads,
            unobserved_writes=p.unobserved_writes,
            unobserved_reads=p.unobserved_reads,
        )
        for p in projections
        if p.undeclared_writes or p.undeclared_reads
        or p.unobserved_writes or p.unobserved_reads
    ]

    # One rollup over every run in the deployment, through the same aggregation
    # a single flow's usage chart is drawn from. A second summation here would
    # be a second definition of what a priced ModelCall is.
    series = flows.usage_series(engine.store.list(), days)
    model_calls = sum(d.model_calls for d in series)
    priced = sum(d.priced_model_calls for d in series)
    return GovernanceState(
        flows_total=len(projections),
        gaps=sorted(gaps, key=lambda g: g.flow_id),
        feeds_without_contract=without_contract,
        registry_problems=(
            validate_mappings() if principal.sees_everything else None
        ),
        spend=SpendState(
            days=days,
            runs=sum(d.runs for d in series),
            model_calls=model_calls,
            priced_model_calls=priced,
            cost_usd=round(sum(d.cost_usd for d in series), 6),
            tokens=sum(d.tokens for d in series),
            basis=flows.cost_basis(model_calls, priced),
        ),
    )


# ---- the screen ------------------------------------------------------------


class Dashboard(BaseModel):
    """The whole system, as of now.

    `at_iso` is on the payload because every figure below is a reading taken at
    one instant and none of them is a stored total. A screen that renders a
    count without saying when it was true invites the reader to treat it as a
    standing fact.
    """

    at_iso: str
    window_days: int
    map: MapState
    arrivals: ArrivalsState
    judgment: JudgmentState
    governance: GovernanceState


@router.get("/dashboard", response_model=Dashboard)
def dashboard(
    principal: Principal = Depends(get_dashboard_principal),
) -> Dashboard:
    """The current state of the whole system, in one call.

    Gated exactly as the Knowledge layer is, and for the same reason: none of
    this is scoped to one person's work. It is the shape of the map, the health
    of every feed, the depth of every desk and the spine's own record — so an
    own-scoped persona is refused outright rather than handed a screen of zeros
    it would read as "everything is fine".

    One call because the four questions are one question. Fetching them
    separately would put the join in the client, where the panels could each
    show a different instant and disagree about a number they were both
    reading off the same store.
    """
    estate, pulls = _estate_view(principal)
    rows = _source_rows(pulls)
    feeds = _feed_states(rows)
    without_contract = sorted(f.source for f in feeds if not f.contract_id)
    queues = _queue_states()
    stats = deps.get_task_service().stats()

    return Dashboard(
        at_iso=utcnow().isoformat(),
        window_days=DASHBOARD_WINDOW_DAYS,
        map=_map_state(principal),
        arrivals=ArrivalsState(
            estate=estate,
            feeds=feeds,
            without_contract=without_contract,
            failing=sorted(
                f.source for f in feeds
                if f.last_status == "failed" or f.last_pull_outcome == "failed"
            ),
        ),
        judgment=JudgmentState(
            open_tasks=stats.open,
            high_priority=stats.high_priority,
            queues=queues,
            unowned_with_work=[
                q.queue for q in queues if q.open > 0 and not q.bound
            ],
            ledger=_ledger_window(DASHBOARD_WINDOW_DAYS),
            identity=_identity_state(principal),
            disagreement=_disagreement_state(principal),
        ),
        governance=_governance_state(
            principal, without_contract, DASHBOARD_WINDOW_DAYS
        ),
    )


# ---------------------------------------------------------------------------
# Accounts Payable — the workspace, its composer, and one submission
# ---------------------------------------------------------------------------
#
# The screen used to ask a clerk to type `vendor=acme-industrial total=1840
# invoice=ACME-INV-20881 po=PO-7741` into a free-text box, which is developer
# scaffolding wearing a business screen's clothes. The string was never the
# problem — it is the case-authoring language of the flow, and it drives the
# seeded world and the eval triggers as well as a manual submission. What was
# wrong was where it came from.
#
# So the composer is served from here: every control is a real row out of the
# graph — the vendor master, that vendor's purchase orders, that order's lines
# — and the string is *generated* from the choices. You learn the vocabulary by
# watching it assemble, which is the one thing reading `payables.py` gives you
# and a text field never could.


class ComposerSource(BaseModel):
    """One external system's id for a vendor. The reason a vendor is an entity
    rather than a slug, shown where choosing one is the reason you care."""

    source: str
    external_id: str
    live: bool


class ComposerLine(BaseModel):
    """A line a composed invoice can bill, as the graph holds it."""

    sku: str
    description: str
    unit_price_usd: float
    #: What the purchase order ordered. The quantity stepper defaults here, so
    #: the un-touched composition is the invoice that matches exactly.
    quantity_ordered: float = 1.0
    #: What the receiving reports booked in, summed over this order's receipts.
    #: Carried so "bill more than was received" is visible rather than a
    #: promise: `-1` means nothing has been received against this line yet.
    quantity_received: float = -1.0


class ComposerOrder(BaseModel):
    po_number: str
    status: str
    ref: str
    lines: list[ComposerLine]
    receipts: list[str]


#: A purchase order nothing more can be billed against. The composer offers the
#: complement and the vendor card counts it, so "N open PO(s)" is a count of
#: orders that are open — it used to include the closed and the fully-billed,
#: which made the number and the word disagree on every vendor.
#:
#: Named as the closed set rather than the open one because the ERP's
#: vocabulary is the source's and grows: a status this deployment has not seen
#: yet is more safely read as still-open than silently dropped from the picker.
CLOSED_PO_STATUSES = frozenset({"cancelled", "closed", "fully-billed"})


class ComposerVendor(BaseModel):
    label: str
    slug: str
    status: str
    tax_id: str
    payment_terms: str
    invoices: int
    ref: str
    sources: list[ComposerSource]
    orders: list[ComposerOrder]


class Composer(BaseModel):
    """Everything the compose path picks from, and the values a run will read.

    `catalog` is the line source for an invoice with no purchase order behind
    it — a vendor we do not know, billing us for the first time. It is the same
    catalog the seeded orders were built from, which is why a local three-way
    match is a real match rather than a coincidence.
    """

    vendors: list[ComposerVendor]
    catalog: list[ComposerLine]
    twists: list[payables.ScenarioTwist]
    policy: list[payables.PolicyClause]
    #: The extractor wired behind the ModelCall. The upload path says what
    #: `stub` means — it reads the filename, not the page — rather than
    #: letting a person believe a document was understood.
    extractor: str


class OutcomeGate(BaseModel):
    """Where the run stopped, and whether this persona may release it.

    `may_decide` is `require_queue` answered *before* the 403 rather than
    after it. Tomas is bound to `ap-clerk` and can decide; Kim is bound to
    nothing and cannot, and the screen says which queue and which binding
    rather than offering a button that fails.
    """

    step_id: str
    step_name: str
    task_id: str
    task_title: str
    queue: str
    may_decide: bool
    why_not: str


class PayableOutcome(BaseModel):
    """One invoice as a screen rather than a sentence.

    The same shape for a row you just submitted and a row from last week, so
    the console renders both with one widget. A submission used to answer with
    a single line of text and no way to reach anything it named.
    """

    invoice: payables.InvoiceRecord
    gate: OutcomeGate | None
    #: The governed clauses this run read, with the deciding one marked.
    policy: list[payables.PolicyClause]
    #: The hint string the run was composed from, when this console composed
    #: it. Empty for a historical row: the string lives on the run's inputs and
    #: fetching every run to fill a column nobody reads is a poor trade.
    hint: str = ""


class PayablesScreen(BaseModel):
    invoices: list[PayableOutcome]
    stats: payables.PayableStats
    composer: Composer


def _composer() -> Composer:
    """The vendor master and its orders, walked once.

    Walked here rather than reused from `_gather_match_context`: that reads
    the legs of *one* match into the matcher's shape, and this reads every
    vendor's orders into a picker's. Sharing them would make one of the two
    lie about what it is for.
    """
    graph = deps.get_graph_service()
    vendors: list[ComposerVendor] = []
    for vendor in graph.find("Vendor"):
        if vendor.status == "superseded":
            continue
        orders: list[ComposerOrder] = []
        for _, order in graph.neighbors(vendor.id, "ordered_from", "in"):
            if str(order.props.get("status") or "") in CLOSED_PO_STATUSES:
                continue
            received: dict[str, float] = {}
            receipts: list[str] = []
            for _, report in graph.neighbors(order.id, "receives_against", "in"):
                receipts.append(
                    str(report.props.get("receipt_number") or report.label)
                )
                for _, line in graph.neighbors(
                    report.id, "has_receiving_line", "out"
                ):
                    sku = str(line.props.get("sku") or "")
                    received[sku] = received.get(sku, 0.0) + float(
                        line.props.get("quantity_received") or 0
                    )
            # Kept in the order's own ordinal order, not sorted. The twists
            # are worded "the first line", and the first line of a purchase
            # order is line 1 of that document — not whichever SKU sorts first.
            lines = []
            for link, line in graph.neighbors(order.id, "has_po_line", "out"):
                sku = str(line.props.get("sku") or "")
                lines.append((
                    int(link.props.get("ordinal", 0) or 0),
                    ComposerLine(
                        sku=sku,
                        description=str(line.props.get("description") or ""),
                        unit_price_usd=float(line.props.get("unit_price_usd") or 0),
                        quantity_ordered=float(line.props.get("quantity_ordered") or 0),
                        quantity_received=received.get(sku, -1.0),
                    ),
                ))
            orders.append(ComposerOrder(
                po_number=str(order.props.get("po_number") or order.label),
                status=str(order.props.get("status") or ""),
                ref=order.ref(),
                lines=[line for _, line in sorted(lines, key=lambda p: p[0])],
                receipts=sorted(set(receipts)),
            ))
        vendors.append(ComposerVendor(
            label=vendor.label,
            slug=str(vendor.props.get("slug") or ""),
            status=str(vendor.props.get("status") or vendor.status),
            tax_id=str(vendor.props.get("tax_id") or ""),
            payment_terms=str(vendor.props.get("payment_terms") or ""),
            invoices=len(graph.neighbors(vendor.id, "issued", "out")),
            ref=vendor.ref(),
            sources=[
                ComposerSource(
                    source=str(other.props.get("source") or ""),
                    external_id=str(other.props.get("external_id") or ""),
                    live=link.live,
                )
                for link, other in graph.neighbors(
                    vendor.id, "identified_as", "out", include_reversed=True
                )
            ],
            orders=sorted(orders, key=lambda o: o.po_number),
        ))
    return Composer(
        vendors=sorted(vendors, key=lambda v: v.label.lower()),
        catalog=[
            ComposerLine(sku=sku, description=description, unit_price_usd=price)
            for sku, description, price in payables.CATALOG
        ],
        twists=list(payables.SCENARIO_TWISTS),
        policy=payables.read_policy_clauses(deps.get_service()),
        extractor=getattr(deps.get_payable_service().reader, "name", ""),
    )


def _outcome(
    record: payables.InvoiceRecord,
    principal: Principal,
    clauses: list[payables.PolicyClause],
    hint: str = "",
) -> PayableOutcome:
    return PayableOutcome(
        invoice=record,
        gate=_gate(record, principal),
        policy=payables.clauses_cited_by(clauses, record.verdict_reason),
        hint=hint,
    )


def _gate(record: payables.InvoiceRecord, principal: Principal) -> OutcomeGate | None:
    """The open gate, or nothing.

    Only an *open* Task is a gate: a decided one is history, and history is
    already in the journal. That also keeps the run lookup — the only thing
    that knows which step suspended — off the rows that do not need it.
    """
    if not record.task_id:
        return None
    task = deps.get_task_service().get(record.task_id)
    if task is None or task.status != "open":
        return None
    run = deps.get_run_engine().get(record.run_id) if record.run_id else None
    step = next(
        (s for s in (run.steps if run else []) if s.task_id == task.id), None
    )
    bound = ", ".join(principal.queues) or "no queue"
    return OutcomeGate(
        step_id=step.step_id if step else "",
        step_name=step.name if step else "",
        task_id=task.id,
        task_title=task.title,
        queue=task.queue,
        may_decide=principal.owns_queue(task.queue),
        why_not=(
            ""
            if principal.owns_queue(task.queue)
            else f"this is in {task.queue}; you are bound to {bound}"
        ),
    )


def get_payables_principal(
    principal: Principal = Depends(get_ui_principal),
) -> Principal:
    """The AP workspace's door: the clerk queue, or read reach past yourself.

    The rows were already scoped — `PayableService.list` narrows an own-scoped
    reader to their own — but the screen is more than its rows. The composer is
    the vendor master, every open purchase order, the posting policy and a
    control that runs the flow, none of which is anybody's *own* work, and all
    of which an AE with no queue could open. So the same rule the rail draws
    with gates the route behind it: own the desk, or read past yourself.
    """
    if principal.reads_beyond_self or principal.owns_queue(payables.CLERK_QUEUE):
        return principal
    raise HTTPException(
        status_code=403,
        detail=(
            "The Accounts Payable workspace is the "
            f"`{payables.CLERK_QUEUE}` desk — the vendor master, its open "
            "orders and the posting policy. You are not bound to it, and your "
            "access is to your own work."
        ),
    )


@router.get("/payables", response_model=PayablesScreen)
def payables_screen(
    principal: Principal = Depends(get_payables_principal),
) -> PayablesScreen:
    """The AP workspace: the rows this reader is accountable for, and the
    composer that starts a new run against real graph rows."""
    service = deps.get_payable_service()
    clauses = payables.read_policy_clauses(deps.get_service())
    return PayablesScreen(
        invoices=[
            _outcome(record, principal, clauses)
            for record in service.list(principal=principal)
        ],
        stats=service.stats(principal=principal),
        composer=_composer(),
    )


class PayableSubmission(BaseModel):
    """Two ways in, and they are not variations of each other.

    An **upload** is a document: bytes, a filename, and no hints — with no
    model configured the extractor reads the filename rather than the page,
    and the screen says so instead of implying otherwise.

    A **composition** is a case: structured choices made against the vendor
    master and its orders, which serialize to the hint string on the way out.
    The string is built by `InvoiceScenario.to_hint` on this side, so the one
    the console previews and the one the run receives cannot diverge.
    """

    image_base64: str = ""
    filename: str = ""
    scenario: payables.InvoiceScenario | None = None


@router.post("/payables/submissions", response_model=PayableOutcome, status_code=201)
def submit_payable(
    body: PayableSubmission,
    principal: Principal = Depends(get_payables_principal),
) -> PayableOutcome:
    """Run the accounts-payable flow now, and answer with a screen.

    Gated harder than the read beside it. Reading the workspace is a
    Seeing-Space capability — a platform principal diagnosing the deployment
    belongs in it — but *submitting* posts to the AP book and may auto-post
    money under the governed limit, so it needs the desk itself. Naviant
    Support reads every invoice and files none, which is exactly the line
    `require_queue` draws for a Task decision.
    """
    require_queue(principal, payables.CLERK_QUEUE)
    hint = body.scenario.to_hint() if body.scenario is not None else ""
    if not hint and not body.image_base64 and not body.filename:
        raise HTTPException(
            status_code=400,
            detail="Upload a document or compose an invoice.",
        )
    record = deps.get_payable_service().submit(
        submitted_by=principal.email,
        image_b64=body.image_base64,
        filename=body.filename,
        hint=hint,
    )
    log.info(
        "Invoice submitted from the console",
        extra={"invoiceId": record.id, "state": record.state, "actor": principal.uid},
    )
    return _outcome(
        record, principal, payables.read_policy_clauses(deps.get_service()), hint
    )
