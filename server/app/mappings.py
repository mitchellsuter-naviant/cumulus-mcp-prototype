"""Mappings: the declared seams between external systems and the graph.

The doctrine names Mapping as a primitive — "how an external source's fields
and identifiers bind to the ontology, plus identity keys and freshness policy.
Config, not logic." Until now ours was real but smeared: payload schemas in
`sync.py`, match policy in `resolution.py`, natural keys in `ontology.py`, the
precedence table in `graph.py`. Each piece was right; nowhere could you read
*one feed's whole contract*. This module is that contract, declared in Python
and exported to the corpus like the ontology and the Collection registry —
one artifact the person reads and the system runs.

The one decision a Mapping adds beyond binding is **materialization**:

- ``reify`` — every landed row becomes a `SourceRecord` object in the graph,
  so it can be the endpoint of an `identified_as` identity Link and a steward
  can adjudicate *that row*. Right for low-volume, high-identity-value feeds:
  vendor lists, purchase orders.
- ``bind`` — rows stay in their Dataset; the Mapping binds a *column* to a
  type and key ("`asset_id` contains Machine identifiers, keyed by asset
  tag") and resolution happens lazily, at query or rollup time. Unresolvable
  ids escalate on aggregates, never per row. Right for high-volume feeds:
  telemetry, GL transactions. A million readings must not become a million
  objects.

Same concept, two strategies, chosen by whether the row itself needs identity,
provenance, or human judgment. The commerce feeds reify because a vendor name
is fuzzy and a steward rules on the match; the HRIS feed binds because a work
email is exact and nobody adjudicates it. The validation refuses a bind
mapping that names a ``lands_as`` type, so the distinction cannot blur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .data_collections import COLLECTIONS
from .ontology import ONTOLOGY

Strategy = Literal["reify", "bind"]
Transport = Literal["push", "pull", "file-drop"]


@dataclass(frozen=True)
class IdentityBinding:
    """One column that carries identifiers of one ontology type."""

    column: str
    object_type: str
    # Which declared aspect of the target the column matches — usually the
    # natural key, sometimes a specific field ("asset tag", "PO number").
    key: str


@dataclass(frozen=True)
class Mapping:
    id: str
    source: str  # the feed id deliveries arrive under
    system: str  # the external system behind it
    summary: str
    transport: Transport
    payload: str  # the payload schema, by name (a model in sync.py)
    strategy: Strategy
    # reify only: the graph type a landed row becomes.
    lands_as: str = ""
    resolves_to: tuple[str, ...] = ()
    identity: tuple[IdentityBinding, ...] = ()
    # The claim-source label this feed's writes carry; its rank in the
    # ontology PRECEDENCE order is what resolves disagreements.
    precedence_source: str = ""
    # Governed values the resolution reads, by ref — thresholds live in the
    # corpus, not here, so editing the policy re-tunes the feed.
    match_policy: str = ""
    freshness: str = "on push"
    # Where the truth lives: `copy` (rows land in the map, with lineage) or
    # `reference` (the map holds a pointer; the store stays external).
    custody: Literal["copy", "reference"] = "copy"
    collection: str = ""  # the Collection landed rows belong to
    escalation: Literal["per-row task", "windowed aggregate"] = "per-row task"
    notes: str = ""


MAPPINGS: tuple[Mapping, ...] = (
    Mapping(
        id="erp-vendors",
        source="erp-vendors",
        system="ERP vendor master export",
        summary=(
            "The ERP's vendor list, pushed on change. The ERP remains the "
            "system of record for its own rows; Cumulus mints the canonical "
            "Vendor and owns the identity mapping between the two."
        ),
        transport="push",
        payload="VendorRecord",
        strategy="reify",
        lands_as="SourceRecord",
        resolves_to=("Vendor",),
        identity=(
            IdentityBinding(column="name", object_type="Vendor",
                            key="normalized legal name"),
        ),
        precedence_source="erp",
        match_policy="knowledge:entity-resolution-policy",
        collection="source-landing",
        escalation="per-row task",
        notes=(
            "Byte-identical redeliveries are suppressed by payload hash; a "
            "re-landed row is an update, not a conflict."
        ),
    ),
    Mapping(
        id="procurement-coupa",
        source="procurement-coupa",
        system="Procurement (Coupa)",
        summary=(
            "The procurement system's supplier records. Second voice on the "
            "same vendors — where it disagrees with the ERP, both claims are "
            "kept and precedence resolves."
        ),
        transport="push",
        payload="VendorRecord",
        strategy="reify",
        lands_as="SourceRecord",
        resolves_to=("Vendor",),
        identity=(
            IdentityBinding(column="name", object_type="Vendor",
                            key="normalized legal name"),
        ),
        precedence_source="procurement",
        match_policy="knowledge:entity-resolution-policy",
        collection="source-landing",
        escalation="per-row task",
    ),
    Mapping(
        id="legacy-ap-extract",
        source="legacy-ap-extract",
        system="Legacy AP system (one-time extract)",
        summary=(
            "A flat extract from the retiring AP system. Dirty on purpose — "
            "misspellings and defunct vendors are what the steward queue is "
            "for."
        ),
        transport="file-drop",
        payload="VendorRecord",
        strategy="reify",
        lands_as="SourceRecord",
        resolves_to=("Vendor",),
        identity=(
            IdentityBinding(column="name", object_type="Vendor",
                            key="normalized legal name"),
        ),
        precedence_source="erp",
        match_policy="knowledge:entity-resolution-policy",
        freshness="one-time",
        collection="source-landing",
        escalation="per-row task",
    ),
    Mapping(
        id="erp-purchasing",
        source="erp-purchasing",
        system="ERP purchasing module",
        summary=(
            "Purchase orders with their lines. Each PO binds to the Vendor it "
            "was cut against; the three-way match reads these legs out of the "
            "graph rather than re-fetching."
        ),
        transport="push",
        payload="PurchaseOrderRecord",
        strategy="reify",
        lands_as="PurchaseOrder",
        resolves_to=("Vendor",),
        identity=(
            IdentityBinding(column="vendor_name", object_type="Vendor",
                            key="normalized legal name"),
            IdentityBinding(column="po_number", object_type="PurchaseOrder",
                            key="po number"),
        ),
        precedence_source="erp",
        match_policy="knowledge:entity-resolution-policy",
        collection="source-landing",
        escalation="windowed aggregate",
        notes=(
            "A batch feed: unresolved vendors escalate as one aggregate Task "
            "per delivery, never per row."
        ),
    ),
    Mapping(
        id="hris-workday",
        source="hris-workday",
        system="HRIS (Workday)",
        summary=(
            "The firm's people and teams. The first **bind** contract in the "
            "registry: an HRIS row is keyed by work email and names its "
            "manager and team by their own keys, so there is nothing for a "
            "steward to adjudicate and no landing record worth keeping — the "
            "columns bind straight to entities and the org chart falls out."
        ),
        transport="push",
        payload="EmployeeRecord + TeamRecord",
        strategy="bind",
        resolves_to=("Employee", "Team"),
        identity=(
            IdentityBinding(column="email", object_type="Employee",
                            key="work email"),
            IdentityBinding(column="manager_email", object_type="Employee",
                            key="work email"),
            IdentityBinding(column="team_slug", object_type="Team",
                            key="team slug"),
            IdentityBinding(column="parent_slug", object_type="Team",
                            key="team slug"),
            IdentityBinding(column="lead_email", object_type="Employee",
                            key="work email"),
        ),
        precedence_source="hris",
        freshness="on push",
        collection="workforce",
        escalation="windowed aggregate",
        notes=(
            "Capabilities are deliberately not in this feed: a skill is "
            "asserted by people, not observed by a system, so the catalogue "
            "and its assignments carry `human:people-ops` authority instead."
        ),
    ),
    Mapping(
        id="calendar-m365",
        source="calendar-m365",
        system="Microsoft 365 calendars",
        summary=(
            "Every employee's schedule, mirrored per calendar. A **bind** "
            "contract like the HRIS: a calendar address and a provider event "
            "id are exact, and attendees are named by work email, so nothing "
            "is reified and nothing needs a steward. Attendees the workforce "
            "does not contain — a client sponsor, an external recruiter — are "
            "counted as unresolved rather than minted: an invitation is not "
            "evidence of employment."
        ),
        transport="pull",
        payload="CalendarRecord + CalendarEventRecord",
        strategy="bind",
        resolves_to=("Calendar", "CalendarEvent", "Employee"),
        identity=(
            IdentityBinding(column="address", object_type="Calendar",
                            key="calendar address"),
            IdentityBinding(column="owner_email", object_type="Employee",
                            key="work email"),
            IdentityBinding(column="calendar_address", object_type="Calendar",
                            key="calendar address"),
            IdentityBinding(column="organizer_email", object_type="Employee",
                            key="work email"),
            IdentityBinding(column="attendee_emails", object_type="Employee",
                            key="work email"),
        ),
        precedence_source="calendar",
        freshness="every 15 minutes, delta by watermark",
        custody="copy",
        collection="calendars",
        escalation="windowed aggregate",
        notes=(
            "Times land as wall-clock local in the calendar's own zone, not "
            "as UTC instants: a recurring 9am has to stay 9am across a "
            "daylight-saving change, and normalizing it away would move "
            "every recurring meeting twice a year."
        ),
    ),
    Mapping(
        id="hris-workforce",
        source="hris-workforce",
        system="HRIS (the estate's people system)",
        summary=(
            "The live estate's departments, jobs and workers, pulled on a "
            "delta rather than pushed. Same **bind** contract as the HRIS "
            "feed above and for the same reason — a work email is exact and a "
            "department slug is a key — but this one is against a real system "
            "rather than a seed, so its identity drift is real too: the HRIS "
            "can hold two worker records under one work email, and this feed "
            "delivers both exactly as it found them."
        ),
        transport="pull",
        payload="EmployeeRecord + TeamRecord",
        strategy="bind",
        resolves_to=("Employee", "Team"),
        identity=(
            IdentityBinding(column="email", object_type="Employee",
                            key="work email"),
            IdentityBinding(column="manager_email", object_type="Employee",
                            key="work email"),
            IdentityBinding(column="team_slug", object_type="Team",
                            key="team slug"),
            IdentityBinding(column="parent_slug", object_type="Team",
                            key="team slug"),
            IdentityBinding(column="lead_email", object_type="Employee",
                            key="work email"),
        ),
        precedence_source="hris",
        freshness="pull, delta by updatedAt watermark per resource",
        custody="copy",
        collection="workforce",
        escalation="windowed aggregate",
        notes=(
            "Declared-key dereferences, named because the connector is "
            "otherwise mechanical: the source keys by internal id and this "
            "contract keys by work email and team slug, so `managerId` and "
            "`headEmployeeId` are read as `workEmail`, `departmentId` and "
            "`parentDepartmentId` as the department's slug, and `jobId` as "
            "the job title. The pulled page answers all four wherever it can; "
            "only a reference the delta does not contain costs one GET."
        ),
    ),
    Mapping(
        id="wms-receiving",
        source="wms-receiving",
        system="Warehouse management (receiving)",
        summary=(
            "Receiving reports — the goods-actually-arrived leg of the "
            "three-way match, keyed to the PO they book in against."
        ),
        transport="push",
        payload="ReceivingRecord",
        strategy="reify",
        lands_as="ReceivingReport",
        resolves_to=("PurchaseOrder",),
        identity=(
            IdentityBinding(column="po_number", object_type="PurchaseOrder",
                            key="po number"),
        ),
        precedence_source="erp",
        match_policy="knowledge:entity-resolution-policy",
        collection="source-landing",
        escalation="windowed aggregate",
    ),
)


def validate_mappings(
    mappings: tuple[Mapping, ...] = MAPPINGS,
) -> list[str]:
    """Cheap structural conformance, same spirit as the TBox invariant sweep.

    A mapping that names a type or Collection that does not exist is wrong at
    declaration time; catching it here means the export can carry a conformance
    line and a test can pin it.
    """
    problems: list[str] = []
    types = {o.name for o in ONTOLOGY.objects}
    collections = {c.id for c in COLLECTIONS}
    precedence = set(ONTOLOGY.precedence)
    seen_sources: set[str] = set()
    for m in mappings:
        where = f"mapping `{m.id}`"
        if m.source in seen_sources:
            problems.append(f"{where}: duplicate source `{m.source}`")
        seen_sources.add(m.source)
        if m.strategy == "reify" and not m.lands_as:
            problems.append(f"{where}: reify requires `lands_as`")
        if m.strategy == "bind" and m.lands_as:
            problems.append(
                f"{where}: bind must not name `lands_as` — rows stay in "
                "their Dataset"
            )
        if m.lands_as and m.lands_as not in types:
            problems.append(f"{where}: unknown lands_as type `{m.lands_as}`")
        for t in m.resolves_to:
            if t not in types:
                problems.append(f"{where}: unknown resolves_to type `{t}`")
        for b in m.identity:
            if b.object_type not in types:
                problems.append(
                    f"{where}: identity column `{b.column}` names unknown "
                    f"type `{b.object_type}`"
                )
        if m.collection and m.collection not in collections:
            problems.append(f"{where}: unknown collection `{m.collection}`")
        if m.precedence_source and m.precedence_source not in precedence:
            problems.append(
                f"{where}: precedence source `{m.precedence_source}` is not "
                "in the ontology precedence order"
            )
    return problems


# ---------------------------------------------------------------------------
# Export to the corpus
# ---------------------------------------------------------------------------

MAPPINGS_ASSET_ID = "source-mappings"


def mappings_markdown(mappings: tuple[Mapping, ...] = MAPPINGS) -> str:
    lines: list[str] = [
        "# Source mappings",
        "",
        "The declared seams between external systems and the graph — one "
        "contract per feed: what arrives, what it binds to, under which match "
        "policy and precedence, and whether rows are **reified** into the "
        "graph (so a steward can adjudicate them) or **column-bound** and "
        "resolved lazily (so a high-volume feed never mints objects per row).",
        "",
        "Generated from `server/app/mappings.py` on every startup. Edit the "
        "Python, not this page.",
        "",
    ]
    problems = validate_mappings(mappings)
    lines.append(
        "Conformance: **clean** — every named type, collection and precedence "
        "source exists."
        if not problems
        else "Conformance: **" + f"{len(problems)} problem(s)** — " + "; ".join(problems)
    )
    lines.append("")
    for m in mappings:
        lines += [f"## {m.id}", "", m.summary, ""]
        lines.append(
            f"- System: {m.system} · transport `{m.transport}` · "
            f"freshness `{m.freshness}` · custody `{m.custody}`"
        )
        lands = f" → lands as `{m.lands_as}`" if m.lands_as else ""
        lines.append(f"- Strategy: `{m.strategy}`{lands}, payload `{m.payload}`")
        if m.identity:
            binds = "; ".join(
                f"`{b.column}` → {b.object_type} (by {b.key})" for b in m.identity
            )
            lines.append(f"- Identity: {binds}")
        if m.resolves_to:
            lines.append(f"- Resolves to: {', '.join(m.resolves_to)}")
        if m.precedence_source:
            lines.append(f"- Claims write as `{m.precedence_source}`")
        if m.match_policy:
            lines.append(f"- Match policy: {m.match_policy}")
        if m.collection:
            lines.append(f"- Landed rows governed by Collection `{m.collection}`")
        lines.append(f"- Escalation: {m.escalation}")
        if m.notes:
            lines.append(f"- {m.notes}")
        lines.append("")
    return "\n".join(lines)


def mappings_asset_fields() -> dict:
    return {
        "id": MAPPINGS_ASSET_ID,
        "path": "platform/ontology/source-mappings",
        "title": "Source mappings",
        "kind": "schema-export",
        "scope": "org",
        "owner": "platform-eng",
        "tags": ["mappings", "sync", "governed-knowledge", "mdm"],
        "status": "published",
        "content": mappings_markdown(),
    }
