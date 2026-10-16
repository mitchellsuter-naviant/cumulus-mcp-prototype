"""Mappings: the declared seams between external systems and the graph.

The doctrine names Mapping as a primitive — "how an external source's fields
and identifiers bind to the ontology, plus identity keys and freshness policy.
Config, not logic." Until now ours was real but smeared: payload schemas in
`connectors/sync.py`, match policy in `resolution.py`, natural keys in
`ontology.py`, the precedence table in `graph.py`. Each piece was right;
nowhere could you read *one feed's whole contract*. This module is that
contract, declared in Python and exported to the corpus like the ontology and
the Collection registry — one artifact the person reads and the system runs.

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

**Every Mapping here describes a system the customer actually runs.** Three
once did not — `procurement-coupa`, `legacy-ap-extract` and `wms-receiving`
were contracts for systems invented to give a seeded demo world something to
arrive from — and they retired with that world. `receiving-sync` was for a
long time the standing example of the other half of that rule: it kept its
Flow, its wire shape and its Tier-2 definition, because the goods-received leg
of a three-way match is real machinery that evals exercise, and it kept no
Mapping, because the estate had no receiving system to declare a contract
with. The Sources screen rendered that gap as a feed under no declared
contract, which is the finding it exists to make.

The estate grew one. `erp-receiving` below is that contract, and the gap it
used to demonstrate is closed — the three-way match now reads a real third leg
rather than an absent one. The reading the screen gives an unannounced feed is
unchanged; there is simply no longer a feed relying on it to tell the truth.

**A third case, now that more than one kind of business is modelled: a
contract whose feed lands nothing.** A namespace activates a feed by running
the app it names, not by having rows in it — so a manufacturer's ERP serves
the job and asset routes this registry's plant-floor contracts read, and can
answer `erp-vendors`, `erp-purchase-orders` and `erp-receiving` with empty
lists because its purchasing lives elsewhere. That is a **healthy feed with
zero rows**: the route answered, the cursor did not move, no run started, and
the Sources screen says `unchanged`. It is a real reading and not a fault, and
it is a different fact in kind from a route that 404s or a feed under no
declared contract. The three plant-floor contracts run the same way in
reverse: on the office estate they are declared, their apps are present, and
their resources are absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from . import desks
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
    payload: str  # the payload schema, by name (a model in connectors/sync.py)
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
    # `per-row task` means this feed's flow raises a Task per ambiguous row —
    # the only escalation that reaches `steward_queue`. `windowed aggregate`
    # means the flow counts what it could not resolve and reports it on the
    # run; nobody is paged, and the export says so by omitting the queue.
    escalation: Literal["per-row task", "windowed aggregate"] = "per-row task"
    # Where an ambiguous row goes for a human answer, when one is asked for.
    # Declared per feed because the person who can say whether two Northwinds
    # are one company is not the person who decides whether an invoice gets
    # paid; the default keeps every feed that predates the counterparty book
    # exactly where it was, and C4 rebinds it without touching a flow.
    steward_queue: str = desks.VENDOR_STEWARDSHIP
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
            "A batch feed: an unresolved vendor is counted on the run's "
            "summary and the order lands unlinked — no Task is raised, and "
            "the count is the aggregate a later window reads. Declared-key "
            "dereferences, named "
            "because the connector is otherwise mechanical: a PO row carries "
            "`vendorId` and its lines carry an internal `itemId`, while this "
            "contract keys a vendor by legal name and a line by SKU — so the "
            "vendor row is read for `companyName` and the item row for its "
            "SKU and display name. Both are memoized for the whole pull, so a "
            "delta of forty orders against four vendors costs four GETs."
        ),
    ),
    Mapping(
        id="erp-receiving",
        source="erp-receiving",
        system="ERP receiving (the estate's finance system)",
        summary=(
            "What the dock signed for, pulled on a delta. Each report books "
            "against the purchase order it names, which is the third leg the "
            "three-way match reads out of the graph — and the one that was "
            "structurally absent until this feed existed."
        ),
        transport="pull",
        payload="ReceivingRecord",
        strategy="reify",
        lands_as="ReceivingReport",
        resolves_to=("PurchaseOrder",),
        identity=(
            IdentityBinding(column="po_number", object_type="PurchaseOrder",
                            key="po number"),
            IdentityBinding(column="receipt_number",
                            object_type="ReceivingReport",
                            key="receipt number"),
        ),
        precedence_source="erp",
        freshness="pull, delta by updatedAt watermark",
        custody="copy",
        collection="source-landing",
        escalation="windowed aggregate",
        notes=(
            "A batch feed with no fuzzy leg: a report binds to its order on an "
            "exact key, and an order that has not landed yet is counted as "
            "`po_missing` on the run while the report lands anyway — no Task is "
            "raised and the edge is made when the order arrives. Declared-key "
            "dereferences, named because the connector is otherwise mechanical: "
            "a receipt row names its order by internal id while a "
            "PurchaseOrder's natural key is the ERP's `tranId`, and its lines "
            "name an item by internal id while this contract keys a line by "
            "SKU — so the order row is read for `tranId` and the item row for "
            "its SKU and display name. Both memoized for the whole pull, so a "
            "split delivery of two receipts against one order costs one GET. "
            "Two fields this source cannot fill: a receipt carries no status "
            "(completeness is the order's property, which the ERP restates "
            "from the sum of its receipts) and a line carries no condition, so "
            "both land empty rather than defaulted. There is no money on a "
            "receipt at all."
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
            "The estate's departments, jobs and workers — the one HRIS "
            "contract in this registry, pulled on a delta. A **bind** "
            "mapping: a work email is exact and a department slug is a key, "
            "so there is nothing for a steward to adjudicate and no landing "
            "record worth keeping — the columns bind straight to entities and "
            "the org chart falls out. It is against a real system, so its "
            "identity drift is real too: the HRIS can hold two worker records "
            "under one work email, and this feed delivers both exactly as it "
            "found them."
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
            IdentityBinding(column="manager_external_id",
                            object_type="Employee",
                            key="the HRIS's own worker id"),
            IdentityBinding(column="team_slug", object_type="Team",
                            key="team slug"),
            IdentityBinding(column="department_external_id",
                            object_type="Team",
                            key="the HRIS's own department id"),
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
            "Two references bind twice on purpose — a manager and a "
            "department arrive as both the derived key and the source's own "
            "id — which is what retired the dereference that used to fetch "
            "them. The connector reads the pulled page and stops; where the "
            "page does not answer, the raw id travels and the flow resolves "
            "it against a person or team already in the graph. The "
            "dereferences that remain, named because the connector is "
            "otherwise mechanical: `jobId` is read as the job title, and "
            "`parentDepartmentId` and `headEmployeeId` as a department's slug "
            "and a work email, because no column on the wire carries either "
            "by id. Capabilities are deliberately absent: a skill is asserted "
            "by people, not observed by a system, so nothing this contract "
            "carries may write one — the `Capability` type stands declared "
            "and unwritten until somebody asserts against it."
        ),
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
        steward_queue=desks.DATA_STEWARDSHIP,
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
        # Per row, like the CRM feed it shares a book with: an ambiguous
        # customer raises a `match_review` Task and asserts nothing. Only the
        # *item* side of this contract escalates on the aggregate, and it
        # reaches no desk — see the note below.
        escalation="per-row task",
        steward_queue=desks.DATA_STEWARDSHIP,
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
            "(`connectors.office.PREVIEW_CHARS`).** The source serves the "
            "whole body on every row; the connector never puts it on the wire "
            "— the shape has no `body` field — and the flow truncates the "
            "preview again on the way in, so a provider that called a whole "
            "body a preview cannot smuggle one through. An address that "
            "belongs to nobody in "
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
    Mapping(
        id="hris-employment-events",
        source="hris-employment-events",
        system="HRIS employment ledger (the estate's people system)",
        summary=(
            "The append-only record of what changed about a worker and when: "
            "hires, promotions, transfers, leaves, terminations. It "
            "**reifies** and then stops — the rows land as SourceRecords and "
            "nothing else is asserted. Not a half-built feed: no type in the "
            "ontology holds a before/after pair, each ledger row carries both, "
            "and modelling one to fit would have been modelling the feed "
            "rather than the business. The workforce feed says who someone is "
            "now; this one is the only thing that says who they were."
        ),
        transport="pull",
        payload="EmploymentEventRecord",
        strategy="reify",
        lands_as="SourceRecord",
        # Nothing. Stated by its absence, and true: this contract resolves no
        # entity, which is exactly the claim its landing-only flow makes.
        resolves_to=(),
        identity=(),
        precedence_source="hris",
        freshness="pull, delta by updatedAt watermark",
        custody="copy",
        collection="source-landing",
        escalation="windowed aggregate",
        notes=(
            "The row is landed whole, including the `previous*` columns — "
            "which is the point, and also why this feed carries salary "
            "figures that no other contract here does. The ledger only ever "
            "grows, so its watermark never has to reconsider a row it has "
            "already seen, and a redelivery is suppressed by payload hash "
            "like any other."
        ),
    ),
    Mapping(
        id="dms-library",
        source="dms-library",
        system="Document management (the estate's filing system)",
        summary=(
            "The customer's filing tree, mirrored as knowledge about *where "
            "things are* rather than as a copy of what is in them. A **bind** "
            "contract: a folder id and a document id are exact, so nothing is "
            "adjudicated and no landing record is kept — folders become Folder "
            "objects keyed by their path, and each file becomes a Document row "
            "stamped with the document library's Collection id and a pointer "
            "back to the original."
        ),
        transport="pull",
        payload="FolderRecord + DmsDocumentRecord",
        strategy="bind",
        resolves_to=("Folder", "Document"),
        identity=(
            IdentityBinding(column="external_id", object_type="Folder",
                            key="source + folder path from the tree's root"),
            IdentityBinding(column="parent_external_id", object_type="Folder",
                            key="source + folder path from the tree's root"),
            IdentityBinding(column="folder_external_id", object_type="Folder",
                            key="source + folder path from the tree's root"),
            IdentityBinding(column="external_id", object_type="Document",
                            key="source + external id (referenced in place)"),
        ),
        precedence_source="dms",
        freshness="pull, delta by updatedAt watermark per resource",
        # The whole contract in one field. The estate serves the bytes at
        # `/documents/<id>/content`; this feed never calls it.
        custody="reference",
        collection="document-library",
        escalation="windowed aggregate",
        notes=(
            "Bytes are never fetched. `storage_ref` is the customer's own "
            "content URL, built from the ids the list route gave us and "
            "pinned to the revision it called current — a pointer without a "
            "version would silently change meaning on the next upload. A "
            "Document landed here therefore has no `sha256`, deliberately: a "
            "content hash is what a *read* file has, and minting one from "
            "metadata would make an unread original indistinguishable from a "
            "document we hold. No dereference: `parentId` and `folderId` are "
            "resolved against rows this same feed landed, and a folder whose "
            "parent neither the delta nor the graph holds is counted on the "
            "aggregate rather than filed at the root."
        ),
    ),
    Mapping(
        id="erp-jobs",
        source="erp-jobs",
        system="ERP production (the estate's manufacturing system)",
        summary=(
            "What the plant is building: the job, its dimensions, the date it "
            "has to leave the yard, the crew it is assigned to, and ten "
            "departments' worth of completion flags. Jobs **reify**, and the "
            "reason is the customer: a job names the party it is being built "
            "for by an internal id, this feed's own pull turns that into a "
            "name, and a name is exactly the evidence that fails — `Hoffman "
            "Ridge Farms` and `Hoffmann Ridge Farms` are one farm and two "
            "rows, and nothing in the source says so. So the reference lands "
            "as a row a steward can rule on, in the same book the CRM, the "
            "sell side and the PSA write into. The crew is the opposite case "
            "and is bound below."
        ),
        transport="pull",
        payload="JobRecord",
        strategy="reify",
        lands_as="Job",
        resolves_to=("Company", "Team"),
        identity=(
            IdentityBinding(column="customer_name", object_type="Company",
                            key="normalized legal name"),
            IdentityBinding(column="job_number", object_type="Job",
                            key="job number"),
            IdentityBinding(column="crew_id", object_type="Team",
                            key="cost_center — the HRIS crew department's "
                                "accounting code"),
        ),
        precedence_source="erp",
        match_policy="knowledge:entity-resolution-policy",
        freshness="pull, delta by updatedAt watermark per resource",
        custody="copy",
        collection="production-records",
        # Per row, like the three feeds it shares the counterparty book with:
        # an ambiguous customer raises a `match_review` Task and asserts
        # nothing about it, while the job lands anyway — a batch feed never
        # suspends for one row. Only the *crew* side of this contract
        # escalates on the aggregate, and it reaches no desk: see the note.
        escalation="per-row task",
        steward_queue=desks.DATA_STEWARDSHIP,
        notes=(
            "**`crew_id → Team` is declared here rather than decided in a "
            "flow, and that is the whole point of the binding.** The HRIS "
            "lands a crew as a department whose head is the foreman and whose "
            "**cost centre** is the bare crew id (`12`, `14`, `18`); the "
            "ERP's job names the crew by that same string. The key is "
            "`Team.cost_center` and not `Team.external_id`: a department's id "
            "is the HRIS's own addressing and a cost centre is the code the "
            "rest of the business writes, so binding on the code keeps the "
            "join off a seeding accident. Declaring it makes \"this "
            "crew id is that team\" a portable statement the customer owns — "
            "and makes a crew id that resolves to **nothing** a countable "
            "aggregate rather than a per-row failure. That case is designed, "
            "not exceptional: jobs erected by the subcontractor carry "
            "`crewId: SUB-KVB` and there is no HRIS department for it, so the "
            "`Job` keeps `crew_id` and `foreman_name` as landed strings and a "
            "later flow raises the identity question with a human at the end "
            "of it. A Task per row would page somebody weekly about a "
            "subcontractor everybody knows about. "
            "**A job that changes crews withdraws the old edge rather than "
            "deleting it** — the link's validity is ended and both stay on the "
            "record — which is what lets a workspace show *former crew* beside "
            "*current crew*, and what retires the weekly folder "
            "reconciliation the RPA design needed: the difference is a "
            "property of the record, so nothing has to go looking for it. "
            "One declared-key dereference, named because the connector is "
            "otherwise mechanical: a job row carries `customerId` while this "
            "contract keys a counterparty by name, so the customer row is "
            "read for `companyName` — the pulled page first, one memoized GET "
            "for what it does not hold, so forty jobs against six customers "
            "cost six. Those customer rows are joined and **never delivered**: "
            "the sell side's list is `erp-sales`' contract, and filling it "
            "from here would start two flows on one delivery. "
            "**Three columns arrive as the estate's own words and are "
            "translated by the flow, not here**: `out_to_yard` and "
            "`do_not_fab` are the ERP's literal `Yes`/`No` picklist values "
            "(it has no boolean type) and `week_position` is its own phrase "
            "for a production window — all three are mapped onto "
            "`release_state`, `hold_reason` and the numeric week against the "
            "`## Source vocabulary` table in `plant-scheduling-parameters`, "
            "so a customer can re-map them without a deploy. The landed row "
            "keeps the strings verbatim, which is what lets the derivation be "
            "re-run when the table changes. `upstream_done` is the one "
            "vocabulary the connector does spell: the ten `<dept>Done` "
            "columns become the department slugs of the governed `## "
            "Departments` table, so neither system learns the other's "
            "spelling. No `site_address` in v1: the field exists and nothing "
            "reads it, and a column delivered for nobody gets relied on "
            "before anyone decides what it means."
        ),
    ),
    Mapping(
        id="hris-credentials",
        source="hris-credentials",
        system="HRIS training and driver files (the estate's people system)",
        summary=(
            "Everything somebody holds that expires: completed training, "
            "certifications, a CDL, a medical card, a PSP query, a "
            "clearinghouse check. A **bind** contract on the HRIS feed's own "
            "argument — a work email is exact and nobody adjudicates it — so "
            "nothing is reified and no steward is asked. One shape for two "
            "resources, because a thing that expires and gets renewed "
            "behaves identically whichever department tracks it: both land as "
            "`Credential` rows distinguished only by `category`, and a second "
            "record model for driver files would have produced two compliance "
            "sweeps that had to agree with each other."
        ),
        transport="pull",
        payload="CredentialRecord",
        strategy="bind",
        resolves_to=("Employee", "Credential"),
        identity=(
            IdentityBinding(column="holder_email", object_type="Employee",
                            key="work email"),
            IdentityBinding(column="holder_external_id", object_type="Employee",
                            key="the HRIS's own worker id"),
            IdentityBinding(column="external_id", object_type="Credential",
                            key="source + the issuing system's row id"),
        ),
        precedence_source="hris",
        freshness="pull, delta by updatedAt watermark per resource",
        custody="copy",
        collection="compliance-records",
        escalation="windowed aggregate",
        notes=(
            "One declared-key dereference, and it is why the feed pulls a "
            "third resource: both credential resources name a person by "
            "`employeeId` and nothing else, while this contract binds a "
            "holder by work email — so `employees` is pulled first and the "
            "row is read for `workEmail`, the pulled page first and one "
            "memoized GET for what it does not hold. `holder_external_id` "
            "travels beside the address for `manager_external_id`'s reason: a "
            "delta of one renewal should not have to buy a translation the "
            "graph already holds, and it is the fallback when the join "
            "missed. A holder the workforce does not contain is counted, "
            "never minted — a training record is not evidence of employment. "
            "**One driver-qualification row becomes up to four credentials**, "
            "one per date it carries, each under its own `external_id` suffix "
            "because the natural key is the source's row id and four "
            "permissions cannot share one; a null date yields no row at all, "
            "because an absent CDL date is the HRIS saying this driver has no "
            "CDL on file rather than an expiry of nothing. **Two fields the "
            "source could fill and this contract refuses.** `status` is the "
            "issuing system's word for how an *assignment* stands, and "
            "`Credential.status` is `valid | expiring | expired | withdrawn` "
            "derived from the dates against the governed notice stages — "
            "writing one into the other would make the compliance sweep read "
            "its own input as its own output. And `retain_until` is a "
            "regulator's horizon, which is why the holder edge is an "
            "association: a credential outlives the employment it was earned "
            "under."
        ),
    ),
    Mapping(
        id="erp-equipment",
        source="erp-equipment",
        system="ERP fixed assets (the estate's finance system)",
        summary=(
            "The fleet the business operates and is answerable for — trucks, "
            "trailers, lifts, machines — and the two dates that say whether "
            "each unit is legal to run. It **reifies** on the purchase-order "
            "feed's terms: an asset tag is exact and nothing needs a steward, "
            "but the row is the only place the pair of dates exists, so "
            "keeping it as delivered is what lets a change to the notice "
            "stages re-read the fleet without re-pulling it."
        ),
        transport="pull",
        payload="EquipmentRecord",
        strategy="reify",
        lands_as="Equipment",
        resolves_to=("Team",),
        identity=(
            IdentityBinding(column="equipment_id", object_type="Equipment",
                            key="source + equipment id (the asset tag)"),
            IdentityBinding(column="home_site", object_type="Team",
                            key="team slug"),
        ),
        precedence_source="erp",
        freshness="pull, delta by updatedAt watermark",
        custody="copy",
        # The unit's own home. Its `Inspection` parts are governed by
        # `compliance-records` instead — declared on the type, because what
        # governs a dated examination of a truck is the same thing that
        # governs a dated certification of a driver, and neither is a fixed
        # asset. One contract names the collection the rows it lands belong
        # to; the part's is the ontology's to declare.
        collection="equipment-register",
        escalation="windowed aggregate",
        notes=(
            "**A null `nextInspectionDueDate` is the source saying this unit "
            "carries no tracked inspection**, and `inspection_tracked` is "
            "derived from it once, on the way in. The skid steers really are "
            "like this. Saying so on the row is what stops a compliance sweep "
            "reporting the same machine as overdue forever, and it is a fact "
            "about the asset rather than a gap in the feed — a screen that had "
            "to explain the null would teach people to ignore it. There is no "
            "inspection resource to pull and no `InspectionRecord` on the "
            "wire: the two dates on the asset are the whole of what the source "
            "holds, so the examination is written as a **part of the unit** "
            "from them — one per landed inspection date, with no `result`, no "
            "`inspector` and no `notes`, because a `pass` inferred from the "
            "existence of a next due date would be inventing the finding a "
            "certificate carries. `make`, `model` and `year` are declared on "
            "the wire and left empty, because this estate's asset rows do not "
            "carry them; parsing them out of the description would put a guess "
            "in three fields that read as facts. No dereference: the site is a "
            "string and the vendor id is a reference this contract does not "
            "bind. The site is where the two systems' vocabularies really do "
            "differ — an asset says `Mazo` where the HRIS department is `Plant "
            "— Mazomanie` — and the flow closes that with a **named rename "
            "table** (`connectors.compliance.SITE_DEPARTMENTS`), on the same "
            "footing as every other place one estate's word becomes ours. A "
            "rename is a rename and not a judgment; the pairing is also "
            "declared for a person in the `## Departments` / site material of "
            "`plant-scheduling-parameters`, so two copies exist and the "
            "governed one is the authority. A site the table does not hold is "
            "still **counted on the run** — a third plant should arrive as a "
            "number rather than as a silent absence — and never as a question "
            "to ask per truck."
        ),
    ),
    Mapping(
        id="psa-book",
        source="psa-book",
        system="PSA (the estate's project delivery system)",
        summary=(
            "What the delivery organization is doing for the customers the "
            "sell side booked: engagements, their phases, and the hours logged "
            "against them. Clients **reify** onto the same counterparty book "
            "the CRM and the ERP write into — the third voice on those "
            "companies and by far the most informal, because a PSA client name "
            "is what a coordinator typed after a phone call (`Apex Mfg`, "
            "`Kestrel CU`) rather than anything a system issued. Projects and "
            "phases bind: an `ENG-` code is exact and nobody adjudicates a "
            "project's identity. Time entries are neither: they are summed "
            "onto the phase and the project and never stored as rows."
        ),
        transport="pull",
        payload=(
            "PsaClientRecord + ProjectRecord + ProjectPhaseRecord + "
            "ProjectTaskRecord + TimeEntryRecord"
        ),
        strategy="reify",
        lands_as="SourceRecord",
        resolves_to=("Company", "Project", "ProjectPhase"),
        identity=(
            IdentityBinding(column="name", object_type="Company",
                            key="normalized legal name"),
            IdentityBinding(column="client_external_id", object_type="Company",
                            key="the PSA's own client id, via its landed row"),
            IdentityBinding(column="project_code", object_type="Project",
                            key="source + project code (ENG-0100)"),
            IdentityBinding(column="project_external_id", object_type="Project",
                            key="the PSA's internal project id"),
            IdentityBinding(column="phase_external_id",
                            object_type="ProjectPhase",
                            key="project id + the PSA's phase id"),
        ),
        # Last among the systems in the precedence order, and the contract is
        # where that is visible: this feed's name claims are retained and lose
        # to every other system that holds a record for the same company.
        precedence_source="psa",
        match_policy="knowledge:entity-resolution-policy",
        freshness="pull, delta by updatedAt watermark per resource",
        custody="copy",
        collection="source-landing",
        # Per row, like the two feeds it shares the counterparty book with: an
        # ambiguous client raises a `match_review` Task and asserts nothing.
        escalation="per-row task",
        steward_queue=desks.DATA_STEWARDSHIP,
        notes=(
            "**Two whole column sets are refused at the wire.** A task's name, "
            "assignee, status, due date and estimate never leave the estate: "
            "no type holds a task, the rows travel as a three-column lookup so "
            "an hour booked against one can be attributed to its phase, and "
            "nothing about them survives the run. A time entry's `notes` never "
            "leaves either — the same custody line the mail feed draws at "
            "`bodyPreview`, and for the same reason: a column that never "
            "reaches the wire cannot be landed by a flow written later. "
            "**People are display names all the way in.** `S. Hoffmann` and "
            "`Curt Boyd` are what a coordinator typed; the PSA holds no "
            "employee id and neither does anything here, so those strings land "
            "as `project_manager` and `contributors` claims — observations "
            "awaiting promotion — and the join to the HRIS is a fuzzy match "
            "with a steward at the end of it, never a batch feed's to assert. "
            "**Hours have a window.** `actual_hours` is the PSA's own running "
            "total, served whole on every project read and claimed as it "
            "arrived; the billable and per-phase sums are computed from the "
            "entries a delivery carried, and are written only for the projects "
            "it carried any for — so a delta about one renamed project cannot "
            "overwrite a total with the zero it happened to see. On a full "
            "pull they are the totals; on a delta they are that window. No "
            "dereference: every reference in the family is an id the flow "
            "resolves against a row this same feed landed."
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


def steward_queue_for_type(object_type: str) -> str:
    """Which desk judges identity for this *book*, read off the Mappings.

    `steward_queue_for` answers per feed, which is right where a row is
    escalating: the row came from somewhere. Two callers have no feed to ask
    with — the fallback on a steward decision whose Task recorded no queue, and
    the cross-source sweep, which is looking at entities rather than at rows —
    and both used to answer from a constant sitting next to the Mapping that
    already knew. One rebind should move both, so both read it here.

    The feeds that resolve into a book agree about its desk by construction:
    `crm-book` and `erp-sales` both write Company and both name
    `data-stewardship`; `erp-vendors` writes Vendor and names
    `vendor-stewardship`. `validate_mappings` fails a disagreement at
    declaration time rather than letting the first match win quietly.
    """
    return next(
        (
            m.steward_queue for m in MAPPINGS
            if object_type in m.resolves_to and m.escalation == "per-row task"
        ),
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
        if m.steward_queue and desks.get(m.steward_queue) is None:
            problems.append(
                f"{where}: steward queue `{m.steward_queue}` is not a "
                "declared desk — an escalated row would land where nobody "
                "is looking"
            )
    # One book, one desk. `steward_queue_for_type` answers "who judges identity
    # for a Company" by finding the first feed that writes one, which is only
    # honest while every feed that writes one agrees — so a disagreement is a
    # declaration-time failure rather than a coin toss resolved by tuple order.
    by_type: dict[str, dict[str, list[str]]] = {}
    for m in mappings:
        if m.escalation != "per-row task":
            continue
        for t in m.resolves_to:
            by_type.setdefault(t, {}).setdefault(m.steward_queue, []).append(m.id)
    for object_type, by_queue in by_type.items():
        if len(by_queue) > 1:
            named = "; ".join(
                f"`{queue}` ({', '.join(ids)})" for queue, ids in sorted(by_queue.items())
            )
            problems.append(
                f"type `{object_type}`: feeds disagree about its steward "
                f"queue — {named}. One book, one desk"
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
        # The queue is only named where a Task is actually raised. `per-row
        # task` is the escalation that reaches a desk; a windowed aggregate is
        # counted on the run and reported on a screen, and printing a steward
        # queue beside it would have this published contract promise a human
        # who is never asked.
        queue = (
            f", to the `{m.steward_queue}` queue"
            if m.escalation == "per-row task" else ""
        )
        lines.append(f"- Escalation: {m.escalation}{queue}")
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
        "owner": "platform-eng",
        "tags": ["mappings", "sync", "governed-knowledge", "mdm"],
        "status": "published",
        "content": mappings_markdown(),
    }
