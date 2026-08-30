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
    # Where an ambiguous row goes for a human answer. Declared per feed
    # because the person who can say whether two Northwinds are one company is
    # not the person who decides whether an invoice gets paid; the default
    # keeps every feed that predates the counterparty book exactly where it
    # was, and C4 rebinds it without touching a flow.
    steward_queue: str = "vendor-stewardship"
    notes: str = ""


MAPPINGS: tuple[Mapping, ...] = (
    Mapping(
        id="erp-vendors",
        source="erp-vendors",
        system="ERP vendor master (the estate's finance system)",
        summary=(
            "The ERP's vendor list, pulled on a delta. The ERP remains the "
            "system of record for its own rows; Cumulus mints the canonical "
            "Vendor and owns the identity mapping between the two."
        ),
        transport="pull",
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
        freshness="pull, delta by updatedAt watermark",
        custody="copy",
        collection="source-landing",
        escalation="per-row task",
        notes=(
            "Byte-identical redeliveries are suppressed by payload hash; a "
            "re-landed row is an update, not a conflict. The ERP carries no "
            "tax id on a vendor, so the decisive identity signal this "
            "contract could use is simply absent and matching falls to the "
            "name — stated because it is why a vendor here can reach a "
            "steward that an ERP with tax ids would have resolved outright."
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
        id="erp-purchase-orders",
        source="erp-purchase-orders",
        system="ERP purchasing (the estate's finance system)",
        summary=(
            "Purchase orders with their lines, pulled on a delta. Each PO "
            "binds to the Vendor it was cut against; the three-way match "
            "reads these legs out of the graph rather than re-fetching."
        ),
        transport="pull",
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
        freshness="pull, delta by updatedAt watermark",
        custody="copy",
        collection="source-landing",
        escalation="windowed aggregate",
        notes=(
            "A batch feed: unresolved vendors escalate as one aggregate Task "
            "per delivery, never per row. Declared-key dereferences, named "
            "because the connector is otherwise mechanical: a PO row carries "
            "`vendorId` and its lines carry an internal `itemId`, while this "
            "contract keys a vendor by legal name and a line by SKU — so the "
            "vendor row is read for `companyName` and the item row for its "
            "SKU and display name. Both are memoized for the whole pull, so a "
            "delta of forty orders against four vendors costs four GETs."
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
        id="office-calendar",
        source="office-calendar",
        system="Office suite calendars (the estate's mail and calendar system)",
        summary=(
            "Every mailbox's schedule, mirrored per calendar and pulled on a "
            "delta. A **bind** contract like the HRIS: a calendar address and "
            "a provider event id are exact, and attendees are named by work "
            "email, so nothing is reified and nothing needs a steward. "
            "Attendees the workforce does not contain — a client sponsor, an "
            "external recruiter — are counted as unresolved rather than "
            "minted: an invitation is not evidence of employment."
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
        freshness="pull, delta by updatedAt watermark per resource",
        custody="copy",
        collection="calendars",
        escalation="windowed aggregate",
        notes=(
            "Times land as wall-clock local in the calendar's own zone, not "
            "as UTC instants: a recurring 9am has to stay 9am across a "
            "daylight-saving change, and normalizing it away would move "
            "every recurring meeting twice a year. One declared-key "
            "dereference, named because the connector is otherwise mechanical: "
            "an event names its mailbox by internal id and this contract keys "
            "a calendar by address, so the mailbox row is read for `address` "
            "and memoized for the whole pull. The source models a mailbox as "
            "one person's, so that address is also the owner's work email. Two "
            "facts it does not carry are left absent rather than inferred: it "
            "has no required/optional split on attendees, and its `showAs` is "
            "availability rather than an event kind, so `kind` keeps the wire "
            "shape's default instead of having availability promoted into it."
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
    Mapping(
        id="crm-book",
        source="crm-book",
        system="CRM (the estate's sales system)",
        summary=(
            "The sales book: accounts, the people in them, and the deals "
            "being pursued. Accounts and contacts **reify**, because this is "
            "the feed the whole identity story runs through — the CRM spells "
            "a counterparty the way a rep typed it (`Northwind Traders`) "
            "while the ERP spells it the way a contract does (`Northwind "
            "Traders Inc.`), and a steward has to be able to rule on *that "
            "row*. Opportunities bind instead: a CRM opportunity id is exact "
            "and nobody adjudicates a deal's identity, only its stage."
        ),
        transport="pull",
        payload="AccountRecord + ContactRecord + OpportunityRecord",
        strategy="reify",
        lands_as="SourceRecord",
        resolves_to=("Company", "Person", "Opportunity"),
        identity=(
            IdentityBinding(column="name", object_type="Company",
                            key="normalized legal name"),
            IdentityBinding(column="email", object_type="Person",
                            key="lowercased email"),
            IdentityBinding(column="account_external_id", object_type="Company",
                            key="the CRM's own account id, via its landed row"),
        ),
        precedence_source="crm",
        match_policy="knowledge:entity-resolution-policy",
        freshness="pull, delta by updatedAt watermark",
        custody="copy",
        collection="source-landing",
        escalation="per-row task",
        steward_queue="data-stewardship",
        notes=(
            "No dereference: every reference in this family is an id the "
            "flow resolves against a row the same feed landed, so the "
            "connector never leaves the page it pulled. A contact with no "
            "address mints under `source:external id` rather than matching on "
            "a name — the duplicate that produces is the point, and B5 "
            "proposes the merge with evidence a human can read."
        ),
    ),
    Mapping(
        id="erp-sales",
        source="erp-sales",
        system="ERP order-to-cash (the estate's finance system)",
        summary=(
            "The sell side of the ERP: customers, the catalogue, and sales "
            "orders with their lines. Customers **reify** onto the same "
            "counterparty book the CRM writes into — this is the second voice "
            "on the same companies, and where the two disagree about a "
            "commercial fact both claims are kept and the ERP's precedence "
            "resolves it. Items and orders bind: a SKU and an order number "
            "are exact, and nothing about them needs a steward."
        ),
        transport="pull",
        payload="CustomerRecord + ItemRecord + SalesOrderRecord",
        strategy="reify",
        lands_as="SourceRecord",
        resolves_to=("Company", "Item", "SalesOrder", "SalesOrderLine"),
        identity=(
            IdentityBinding(column="name", object_type="Company",
                            key="normalized legal name"),
            IdentityBinding(column="customer_external_id", object_type="Company",
                            key="the ERP's own customer id, via its landed row"),
            IdentityBinding(column="sku", object_type="Item", key="SKU"),
            IdentityBinding(column="item_external_id", object_type="Item",
                            key="the ERP's internal item id"),
            IdentityBinding(column="order_number", object_type="SalesOrder",
                            key="order number (tranId)"),
        ),
        precedence_source="erp",
        match_policy="knowledge:entity-resolution-policy",
        freshness="pull, delta by updatedAt watermark",
        custody="copy",
        collection="source-landing",
        escalation="windowed aggregate",
        steward_queue="data-stewardship",
        notes=(
            "No dereference: an order line names an item by the ERP's "
            "internal id, and the item row this feed carries is the only "
            "thing that translates it to a SKU — so items travel with the "
            "orders and the flow resolves in-batch first, falling back to the "
            "`external_id` already claimed on a known Item. A line whose item "
            "neither answers is counted, never minted: inventing a catalogue "
            "entry out of an id would put a SKU-less Item in the book that "
            "the next real one could never merge with."
        ),
    ),
    Mapping(
        id="office-mail",
        source="office-mail",
        system="Office suite mail (the estate's mail and calendar system)",
        summary=(
            "Mail as evidence that a conversation happened and who was in it "
            "— not a mail store. A **bind** contract on the same terms as the "
            "calendar beside it: a mailbox address and a provider message id "
            "are exact, so nothing is reified and no steward is asked. What "
            "makes this feed worth having is that a commitment is usually "
            "made in a thread before any system has a row for it; what keeps "
            "it defensible is that custody stops at an opening preview."
        ),
        transport="pull",
        payload="MailboxRecord + MailMessageRecord",
        strategy="bind",
        resolves_to=("Mailbox", "MailMessage", "Person", "Employee"),
        identity=(
            IdentityBinding(column="address", object_type="Mailbox",
                            key="mailbox address"),
            IdentityBinding(column="mailbox_address", object_type="Mailbox",
                            key="mailbox address"),
            IdentityBinding(column="from_email", object_type="Employee",
                            key="work email"),
            IdentityBinding(column="from_email", object_type="Person",
                            key="lowercased email"),
            IdentityBinding(column="to_emails", object_type="Employee",
                            key="work email"),
            IdentityBinding(column="to_emails", object_type="Person",
                            key="lowercased email"),
            IdentityBinding(column="cc_emails", object_type="Employee",
                            key="work email"),
            IdentityBinding(column="cc_emails", object_type="Person",
                            key="lowercased email"),
        ),
        # Deliberately the lowest-ranked system authority. A job title read off
        # a mail directory is a hint the HRIS outranks, and a fact asserted in
        # a thread is a fact somebody typed.
        precedence_source="office",
        freshness="pull, delta by updatedAt watermark per resource",
        custody="copy",
        collection="correspondence",
        escalation="windowed aggregate",
        notes=(
            "**Custody stops at `body_preview`, capped at 240 characters "
            "(`sync_office.PREVIEW_CHARS`).** The source serves the whole body "
            "on every row; the connector never puts it on the wire — the shape "
            "has no `body` field — and the flow truncates the preview again on "
            "the way in, so a provider that called a whole body a preview "
            "cannot smuggle one through. An address that belongs to nobody in "
            "the graph is counted and left on the row as `from_email`, never "
            "minted: a counterparty book that grew a Person per unrecognized "
            "sender would fill with no-reply robots. That is also where the "
            "estate's vendor drift sits — `GSF Logistics` writing from "
            "`billing@granitefreight.example.com` — as `from_name` evidence on "
            "a landed message, for a later flow with a steward and a threshold "
            "to propose, rather than a batch feed to guess. One declared-key "
            "dereference, the same one the calendar makes: a message names its "
            "mailbox by internal id and this contract keys by address."
        ),
    ),
)


def steward_queue_for(source: str) -> str:
    """Which queue this feed's ambiguous rows go to for a human answer.

    A lookup rather than a constant in the flow, because the queue is part of
    the contract: a Mapping that changes hands changes where its judgment
    lands, and a flow that hardcoded a queue name would quietly ignore that.
    A feed with no declared Mapping falls back to the field's own default,
    which is where every such row went before this existed.
    """
    return next(
        (m.steward_queue for m in MAPPINGS if m.source == source),
        Mapping.__dataclass_fields__["steward_queue"].default,
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
        lines.append(
            f"- Escalation: {m.escalation}, to the `{m.steward_queue}` queue"
        )
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
