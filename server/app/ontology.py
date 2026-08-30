"""The enterprise ontology: Object types, Link types, and claim precedence.

Declared in Python and exported to the map as a governed-knowledge document
(`ontology_markdown` renders it; the seed pass publishes it as an asset). That
is the doctrine's authoring model — Python plus decorators authors the map,
the map is what the customer keeps — and it means the ontology is one artifact
rather than a schema in code plus a wiki page that drifts from it.

Two things live here and nowhere else:

- **What kinds of thing exist, and how they connect.** Objects are canonical
  entities; Links are typed, directional, and carry confidence and reversal,
  so an identity assertion can be withdrawn without deleting history.
- **Claim precedence.** When two systems assert the same field of the same
  object, the ordered `PRECEDENCE` list decides which one resolves. Both
  claims are retained either way — reconciliation is a read-time decision, so
  changing precedence re-resolves history rather than rewriting it.

Deliberately small. Every type here is one the two live flows actually write;
nothing is declared for the shape of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """One declared field.

    `kind` is a plain scalar kind (string | number | money | date | enum) —
    or `ref:<Type>`, a field that *carries identifiers of another type*: a
    soft reference, rendered as a typed chip and queryable, but deliberately
    not a Link. The doctrine's rule stands — if two things relate, the
    relation is a Link — so a ref field is an **observation awaiting
    promotion**: the flow landed an identifier it has not resolved yet, the
    way a landed row carries `vendor_name` before resolution. The moment a
    competency question needs to traverse it, it must become a Link.
    """

    name: str
    kind: str
    note: str = ""

    @property
    def ref_target(self) -> str | None:
        return self.kind[4:] if self.kind.startswith("ref:") else None


@dataclass(frozen=True)
class ObjectType:
    name: str
    label: str
    summary: str
    # How the natural key is formed. Keys *find* entities — the same
    # real-world thing seen twice resolves to one object — but identity is a
    # minted uid, so a key can change without breaking a single reference.
    natural_key: str
    fields: tuple[FieldSpec, ...] = ()
    # True when Cumulus is the system of record for this type: its id is the
    # authoritative one and other systems' ids attach as identity Links.
    authoritative: bool = False
    # The Collection whose retention and custody policy governs its rows.
    collection: str = ""
    # Freeform facets for filtering, not structure: no registry, no
    # sections, just strings a search box can match and chips can toggle.
    # A type carries several; parts declare none and inherit their owner's
    # (enforced by validate_ontology).
    tags: tuple[str, ...] = ()
    # The questions this type exists to answer — the admission test, made
    # data. `{label}` substitutes the selected instance's label, so the ABox
    # explorer can offer them as one-tap asks. Declared on roots only; a part
    # is asked about through its owner.
    competency_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class LinkType:
    """A typed edge, and how many of it may exist.

    Cardinality is *enforced* on write (see `graph.GraphService`), so it is
    declared only where a second edge would definitely be a bug:

    - `one-to-many` — the target may have at most one live inbound edge. "An
      invoice has one issuing vendor"; "a line has one parent".
    - `many-to-one` — the source may have at most one live outbound edge. "A
      purchase order is placed with one vendor".
    - `one-to-one` — both.
    - `many-to-many` — unconstrained, and used deliberately where the second
      edge is legitimate rather than merely unobserved. A request reviewed
      twice can be decided by two people; a line can be evidenced by a receipt
      *and* a boarding pass. Declaring those tighter would buy a false
      violation the first time reality caught up.
    """

    name: str
    from_type: str
    to_type: str
    cardinality: str  # one-to-one | one-to-many | many-to-one | many-to-many
    summary: str
    # What kind of edge this is. One discriminating field rather than a pile
    # of booleans, because the three are mutually exclusive by meaning:
    #
    # - `association` — two independent things related ("issued",
    #   "ordered_from"). The default, as in UML; there is deliberately no
    #   soft "aggregation" middle tier (Fowler's "modeling placebo").
    # - `composition` — the target is a *part* of the source: identified
    #   through it (ER's weak entity — key = owner key + discriminator),
    #   cannot exist without it, dies with it. Part-ness of a *type* is
    #   derived from this, never declared, so it cannot drift.
    # - `identity` — an assertion that an external record and a canonical
    #   entity are the same real-world thing; carries confidence and can be
    #   reversed by a steward.
    nature: str = "association"  # association | composition | identity

    @property
    def identity(self) -> bool:
        return self.nature == "identity"

    @property
    def composes(self) -> bool:
        return self.nature == "composition"


@dataclass(frozen=True)
class Ontology:
    objects: tuple[ObjectType, ...]
    links: tuple[LinkType, ...]
    precedence: tuple[str, ...]

    def object_type(self, name: str) -> ObjectType | None:
        return next((o for o in self.objects if o.name == name), None)

    def link_type(self, name: str) -> LinkType | None:
        return next((linked for linked in self.links if linked.name == name), None)

    def object_names(self) -> list[str]:
        return [o.name for o in self.objects]

    # -- derived hierarchy --------------------------------------------------
    #
    # Part-ness is computed from compositional links, never declared on the
    # type, so a type cannot claim to be a part of nothing.

    def owner_of(self, type_name: str) -> str | None:
        """The type that composes this one, if any."""
        for link in self.links:
            if link.composes and link.to_type == type_name:
                return link.from_type
        return None

    def parts_of(self, type_name: str) -> list[str]:
        return [
            link.to_type for link in self.links
            if link.composes and link.from_type == type_name
        ]

    def owning_link(self, type_name: str) -> "LinkType | None":
        for link in self.links:
            if link.composes and link.to_type == type_name:
                return link
        return None

    def source_identity_link(self, type_name: str) -> "LinkType | None":
        """The identity edge that binds this type to the rows sources sent.

        Read off the declarations rather than kept in a second table beside
        them: `identified_as`, `company_identified_as` and
        `person_identified_as` are the same relationship qualified by the
        endpoint that varies (see the note above their declarations), and the
        resolver, the steward's decision and the sweep all need to name the
        right one for a type. A lookup here means adding a reified type is one
        `LinkType` and nothing else — the alternative, which this replaces, was
        a resolver that hardcoded `identified_as` and therefore silently never
        fired its source-link signal for a Company or a Person.

        Filtered to `SourceRecord` on purpose: the merge proposals B5 writes
        are identity links too, and they join two canonical objects rather than
        an object to a landed row.
        """
        return next(
            (
                link for link in self.links
                if link.nature == "identity"
                and link.from_type == type_name
                and link.to_type == "SourceRecord"
            ),
            None,
        )

    def roots(self) -> list["ObjectType"]:
        return [o for o in self.objects if self.owner_of(o.name) is None]

    def effective_tags(self, type_name: str) -> tuple[str, ...]:
        """A part is tagged as its owner is."""
        obj = self.object_type(type_name)
        if obj is None:
            return ()
        if obj.tags:
            return obj.tags
        owner = self.owner_of(type_name)
        return self.effective_tags(owner) if owner else ()


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

OBJECT_TYPES: tuple[ObjectType, ...] = (
    ObjectType(
        name="Employee",
        label="Employee",
        summary=(
            "A person employed by the enterprise. The root of the expense "
            "graph: every expense request hangs off one."
        ),
        # A key, not an identity: ids are minted, and an email change is a
        # rekey that leaves every reference intact.
        natural_key="work email (re-keyable; identity is a minted uid)",
        fields=(
            FieldSpec("email", "string"),
            FieldSpec("name", "string"),
            FieldSpec("title", "string", "job title — a label, not a role"),
            FieldSpec("location", "string"),
            FieldSpec("start_date", "date"),
            FieldSpec("external_id", "string",
                      "the HRIS's own worker id. Kept as a claim rather than "
                      "left in the connector because it is how one row's "
                      "reference to another — a `managerId` — finds a person "
                      "whose work email this delta never carried; without it "
                      "the connector has to spend a GET restating what the "
                      "row already said"),
            FieldSpec("status", "enum",
                      "the HRIS's own worker status — Active, Terminated, "
                      "On Leave. Claimed rather than inferred from a "
                      "termination date, and claimed rather than dropped: a "
                      "person who has left is still an Employee with expenses "
                      "and an org history, and a roster that quietly showed "
                      "them as current would be wrong in the one direction "
                      "that matters"),
            FieldSpec("employment_type", "enum",
                      "Full-time, Part-time, Contractor — the HRIS's word"),
        ),
        authoritative=True,
        collection="workforce",
        tags=("people", "expenses", "org-chart"),
        competency_questions=(
            "How much has {label} submitted in expenses in the last 6 months?",
            "Who reports to {label}?",
            "What capabilities does {label} hold?",
        ),
    ),
    ObjectType(
        name="Team",
        label="Team",
        summary=(
            "An organizational unit. Competency question: who is on which "
            "team, and what does a team's work cost — neither answerable by "
            "a string field on Employee. Teams nest (subteam_of), so the org "
            "chart is a traversal: members up through teams to the root."
        ),
        natural_key="team slug",
        fields=(
            FieldSpec("slug", "string"),
            FieldSpec("name", "string"),
            FieldSpec("mission", "string", "one line on what the team is for"),
            FieldSpec("external_id", "string",
                      "the HRIS's own department id, for the same reason "
                      "Employee carries one: a worker row names its "
                      "department by id, and the slug it keys on is derived "
                      "from a name the delta may not have carried"),
        ),
        authoritative=True,
        collection="workforce",
        tags=("people", "org-chart"),
        competency_questions=(
            "Who is on {label}?",
            "Which teams roll up under {label}?",
        ),
    ),
    ObjectType(
        name="Capability",
        label="Capability",
        summary=(
            "An encoded skill or competency. Competency question: who can do "
            "X — unanswerable while skills live in resume prose. The "
            "description is what makes two people's 'data engineering' the "
            "same capability instead of two strings."
        ),
        natural_key="capability slug",
        fields=(
            FieldSpec("slug", "string"),
            FieldSpec("name", "string"),
            FieldSpec("description", "string",
                      "what holding this capability actually means"),
            FieldSpec("category", "string",
                      "coarse grouping: engineering, finance, people, …"),
        ),
        authoritative=True,
        collection="workforce",
        tags=("people", "skills"),
        competency_questions=(
            "Who can do {label}, and at what proficiency?",
        ),
    ),
    ObjectType(
        name="Calendar",
        label="Calendar",
        summary=(
            "One person's schedule, as the calendar provider keeps it. "
            "Cumulus is not the system of record here — the provider is — so "
            "the calendar is a governed *mirror*: it exists in the graph "
            "because a day only means something next to the work it is "
            "about, and neither the org chart nor the queue can say when "
            "somebody is actually free."
        ),
        natural_key="calendar address at the provider",
        fields=(
            FieldSpec("address", "string",
                      "the calendar's address at the provider"),
            FieldSpec("name", "string", "what the owner calls it"),
            FieldSpec("provider", "string", "microsoft-365 | google | …"),
            FieldSpec("external_id", "string"),
            FieldSpec("timezone", "string",
                      "the zone its wall-clock times are written in"),
            FieldSpec("owner_email", "string",
                      "denormalized from has_calendar, for cheap reads"),
            FieldSpec("synced_at", "date", "when the mirror last landed"),
        ),
        # The provider owns the id and the truth; we hold a copy that a
        # steward may not overwrite.
        authoritative=False,
        collection="calendars",
        tags=("people", "calendar", "scheduling"),
        competency_questions=(
            "What is on {label} today, and which work does each block touch?",
            "When does {label} have two clear hours this week?",
            "Who is {label}'s owner committed to meeting this week?",
        ),
    ),
    ObjectType(
        name="CalendarEvent",
        label="Calendar event",
        summary=(
            "One block of time on one calendar: a meeting, a focus block, a "
            "task block, or a deadline. A **part** of the calendar that holds "
            "it — which is not a modelling flourish but how providers "
            "actually work: every attendee's copy of a meeting is a distinct "
            "row with its own id, its own reminders and its own response, "
            "identified through the calendar it sits on and deleted with it. "
            "The shared meeting is not the row; it is what the rows agree on, "
            "and `attended_by` is where that agreement lives."
        ),
        natural_key="calendar address + provider event id",
        fields=(
            FieldSpec("title", "string"),
            FieldSpec("kind", "enum",
                      "meeting | focus | task-block | deadline | "
                      "out-of-office"),
            FieldSpec("starts_at", "date",
                      "wall-clock local time, in the calendar's zone"),
            FieldSpec("ends_at", "date"),
            FieldSpec("all_day", "enum", "true | false"),
            FieldSpec("location", "string"),
            FieldSpec("status", "enum", "confirmed | tentative | cancelled"),
            FieldSpec("external_id", "string", "the provider's id for this copy"),
            FieldSpec("organizer_email", "string",
                      "denormalized from organized_by, for cheap reads"),
            FieldSpec("agenda_ref", "string",
                      "the task, run or case this block is held for — an "
                      "observation awaiting promotion, not a Link: Tasks and "
                      "runs are execution state, not graph objects, so this "
                      "stays a string until something in the graph is what "
                      "the block is about"),
            FieldSpec("notes", "string"),
        ),
        collection="calendars",
    ),
    ObjectType(
        name="ExpenseRequest",
        label="Expense request",
        summary=(
            "One submission by one employee: N documents, a commentary, and "
            "the lines projected out of them. The unit a Flow run decides."
        ),
        natural_key="run id",
        fields=(
            FieldSpec("submitted_at", "date"),
            FieldSpec("commentary", "string", "the submitter's own framing"),
            FieldSpec("requested_usd", "money"),
            FieldSpec("approved_usd", "money"),
            FieldSpec("state", "enum", "approved | pending | denied"),
            FieldSpec("employee_email", "string",
                      "denormalized from the submitted edge, for cheap reads"),
        ),
        authoritative=True,
        collection="expense-records",
        tags=("expenses",),
        competency_questions=(
            "What documents evidence {label}, and who decided it?",
        ),
    ),
    ObjectType(
        name="ExpenseLine",
        label="Expense line",
        summary=(
            "One reimbursable fact. Not the same thing as a document: a "
            "compound receipt yields several lines, and a human may add one "
            "the extraction missed."
        ),
        natural_key="run id + ordinal",
        fields=(
            FieldSpec("vendor", "string"),
            FieldSpec("date", "date"),
            FieldSpec("category", "enum"),
            FieldSpec("document_total_usd", "money", "what the receipt says"),
            FieldSpec("requested_usd", "money", "what the employee asks for"),
            FieldSpec("purpose", "string"),
            FieldSpec("origin", "enum", "model | human"),
        ),
        authoritative=True,
        collection="expense-records",
    ),
    ObjectType(
        name="Document",
        label="Document",
        summary=(
            "One artifact as submitted: bytes, pages, and the regions an "
            "extraction read. Evidence, never a fact in itself. The same type "
            "serves a receipt a person uploaded and a file that has sat in the "
            "customer's document system for six years — deliberately, because "
            "a second `DmsFile` type would split every 'what evidences this' "
            "question in two and force each reader to ask it twice. What "
            "differs between them is custody, and custody is a property of the "
            "Collection a row belongs to, which is why `collection_id` is a "
            "field and the duplicate type is not."
        ),
        natural_key=(
            "content hash; or source + external id for an original that is "
            "referenced in place rather than copied — a file we never fetched "
            "the bytes of has no hash to be keyed by, and inventing one would "
            "make it look read"
        ),
        fields=(
            FieldSpec("filename", "string"),
            FieldSpec("media_type", "string"),
            FieldSpec("byte_size", "number"),
            FieldSpec("sha256", "string"),
            FieldSpec("kind", "enum", "receipt | invoice | purchase-order | receiving-report"),
            FieldSpec("collection_id", "string",
                      "the Collection whose retention governs the bytes"),
            FieldSpec("retention_until", "date", "stamped when the bytes landed"),
            FieldSpec("storage_ref", "string",
                      "where the original lives, for a row held under "
                      "`reference` custody: a resolvable address in the "
                      "system of record, fetched on demand and never copied. "
                      "Empty for anything the map holds the bytes of. This is "
                      "the field that keeps a reference honest — without it a "
                      "referenced document is a filename with nothing behind "
                      "it"),
            FieldSpec("latest_version", "number",
                      "which revision the `storage_ref` points at, as the "
                      "source counted it. A reference is only as honest as "
                      "its version: a pointer at v2 of a document now on v4 "
                      "is not a broken link — it resolves, and answers with "
                      "content nobody has read since. Empty for a row we hold "
                      "the bytes of, which has no revisions but its own"),
        ),
        collection="audit-documents",
        tags=("expenses", "payables", "evidence"),
        competency_questions=(
            "Which lines and invoices were read out of {label}?",
        ),
    ),
    ObjectType(
        name="Folder",
        label="Folder",
        summary=(
            "One node of a document system's filing tree. It is here because "
            "where a customer filed something is itself knowledge: the same "
            "PDF under `Contracts/Northwind/Executed` and under "
            "`Scratch/inbox` means two different things, and a flat document "
            "list throws that away along with every retention rule a "
            "department hung off a folder. Modelled as a type rather than a "
            "path string on Document for the ordinary reason — a path is a "
            "spelling, and renaming a folder would silently orphan every row "
            "that had memorized the old one."
        ),
        natural_key="source + folder path from the tree's root",
        fields=(
            FieldSpec("name", "string", "the folder's own name, not its path"),
            FieldSpec("path", "string",
                      "the full path, denormalized from subfolder_of for "
                      "cheap reads and for showing a person where they are"),
            FieldSpec("external_id", "string", "the document system's id"),
            FieldSpec("source", "string", "which document system this tree is"),
            FieldSpec("depth", "number", "0 at the root"),
        ),
        # The document system owns the tree; we mirror it. A steward who wants
        # a folder moved should move it there.
        authoritative=False,
        collection="document-library",
        tags=("documents", "evidence", "filing"),
        competency_questions=(
            "What is filed under {label} — directly, and all the way down?",
            "Which folder does {label} itself sit in?",
        ),
    ),
    ObjectType(
        name="Vendor",
        label="Vendor",
        summary=(
            "One concept of vendor. Cumulus mints the authoritative id; every "
            "external system's id attaches as an identity Link, so an ERP "
            "code and a procurement code resolve to the same entity."
        ),
        natural_key="normalized legal name",
        fields=(
            FieldSpec("name", "string"),
            FieldSpec("slug", "string"),
            FieldSpec("tax_id", "string"),
            FieldSpec("status", "enum", "active | inactive | onboarding"),
            FieldSpec("payment_terms", "string"),
            FieldSpec("remit_country", "string"),
            # The two the ERP has always sent and the wire shape used to drop.
            # They are here because they are what a *cross-book* match is
            # decided on: a name says a vendor and a company might be the same
            # firm, and a domain or a switchboard number is what settles it.
            FieldSpec("email", "string",
                      "the remit-to or billing address, usually functional "
                      "(`billing@…`) rather than a person"),
            FieldSpec("phone", "string"),
            FieldSpec("aliases", "string", "other names this vendor arrived under"),
            FieldSpec("onboarding_state", "enum",
                      "unconfirmed until a human vouches for the vendor"),
        ),
        authoritative=True,
        collection="vendor-master",
        tags=("payables", "mdm"),
        competency_questions=(
            "How much has {label} invoiced us, and is that usual for them?",
            "Which systems have a record for {label}?",
        ),
    ),
    ObjectType(
        name="Company",
        label="Company",
        summary=(
            "One counterparty, as the business means it: the organization a "
            "CRM account, an ERP customer and a mail domain are each keeping "
            "their own row about. None of those rows is the company — they "
            "are three claims about one, in three spellings. Without this "
            "type, 'what is our whole relationship with Northwind' has no "
            "subject to hang off, only three answers that cannot be added "
            "together. Deliberately not merged into Vendor: buying from "
            "somebody and selling to them are different roles with different "
            "authorities behind them, and a firm that is both should read as "
            "two roles a link joins, not one row that has quietly lost track "
            "of which is which."
        ),
        natural_key=(
            "normalized legal name — the same normalization Vendor uses, so "
            "the two books collapse spellings identically and a proposed "
            "Company↔Vendor match is comparing like with like"
        ),
        fields=(
            FieldSpec("name", "string"),
            FieldSpec("slug", "string"),
            FieldSpec("industry", "string",
                      "the CRM's claim, and usually an unopposed one — no "
                      "other system holds an opinion about it"),
            FieldSpec("website", "string"),
            FieldSpec("phone", "string"),
            FieldSpec("email", "string"),
            FieldSpec("domain", "string",
                      "the mail domain the counterparty is reachable at, where "
                      "that is the only reachability a source holds. The "
                      "delivery system keeps no website and no switchboard — "
                      "it keeps the address of the person somebody talks to — "
                      "and the host out of that address is the one hard "
                      "identifier an informal client list contributes. Derived "
                      "in the flow and claimed as itself rather than filed "
                      "under `email`, because a named person's address is not "
                      "the company's"),
            FieldSpec("payment_terms", "string",
                      "a commercial fact, so the ERP outranks the CRM here — "
                      "both claims are kept and precedence picks"),
            FieldSpec("status", "enum", "prospect | active | inactive"),
            FieldSpec("aliases", "string",
                      "other names this company has arrived under"),
        ),
        authoritative=True,
        collection="company-master",
        tags=("commerce", "counterparties", "mdm"),
        competency_questions=(
            "Which systems hold a record for {label}?",
            "What have we sold {label}, and what is still open?",
            "Who at {label} do we actually deal with?",
        ),
    ),
    ObjectType(
        name="Person",
        label="Person",
        summary=(
            "A human outside the enterprise: a contact in the CRM, a name on "
            "a mail thread, the person who signs for a delivery. Deliberately "
            "not Employee. Employment is a fact the HRIS is authoritative "
            "about, and widening Employee to cover everyone we have ever "
            "emailed would put the org chart and the address book in one bag "
            "— 'who reports to whom' stops being answerable the moment half "
            "the rows are people who do not work here. Two rows for the same "
            "human should resolve to one Person; two humans who share nothing "
            "but a common name must not, which is why the key is an address "
            "and not a spelling."
        ),
        natural_key=(
            "lowercased email. Where a source carries none, source + external "
            "id — a record with no address mints its own Person rather than "
            "guessing, so two same-named strangers stay two people and the "
            "merge, if there is one, is proposed later with evidence a human "
            "can read"
        ),
        fields=(
            FieldSpec("email", "string"),
            FieldSpec("name", "string"),
            FieldSpec("first_name", "string"),
            FieldSpec("last_name", "string"),
            FieldSpec("title", "string",
                      "their job title at their own employer — a label, and "
                      "not a role we grant anything on"),
            FieldSpec("phone", "string"),
            FieldSpec("company_name", "string",
                      "denormalized from works_at, for cheap reads"),
        ),
        authoritative=True,
        collection="company-master",
        tags=("people", "commerce", "counterparties", "mdm"),
        competency_questions=(
            "Which company does {label} work at, and what have we sold them?",
            "Which correspondence has {label} been part of?",
            "Which source rows resolve to {label}?",
        ),
    ),
    ObjectType(
        name="SourceRecord",
        label="Source record",
        summary=(
            "One external system's row, kept as landed. Resolution reads "
            "these; it never overwrites them, so re-resolving after a rule "
            "change needs no re-fetch."
        ),
        natural_key="source + external id",
        fields=(
            FieldSpec("source", "string"),
            FieldSpec("external_id", "string"),
            FieldSpec("payload", "string", "the row, as JSON"),
            FieldSpec("payload_hash", "string",
                      "what makes a byte-identical redelivery recognizable"),
            FieldSpec("record_kind", "enum",
                      "vendor | po | rr | company | person | employment-event "
                      "— which book a steward's decision on this row writes "
                      "into, and the only thing that survives from the flow "
                      "that landed it. `employment-event` resolves to no book "
                      "and reaches no steward, which is the ledger feed's "
                      "whole claim: the row is kept, and nothing is asserted "
                      "from it"),
            FieldSpec("landed_at", "date"),
        ),
        collection="source-landing",
        tags=("payables", "landing", "mdm"),
        competency_questions=(
            "Which canonical entity does {label} resolve to?",
        ),
    ),
    ObjectType(
        name="Invoice",
        label="Invoice",
        summary="One inbound vendor invoice, with its lines as children.",
        natural_key="vendor slug + invoice number",
        fields=(
            FieldSpec("invoice_number", "string"),
            FieldSpec("invoice_date", "date"),
            FieldSpec("due_date", "date"),
            FieldSpec("po_number", "string"),
            FieldSpec("subtotal_usd", "money"),
            FieldSpec("freight_usd", "money"),
            FieldSpec("tax_usd", "money"),
            FieldSpec("total_usd", "money"),
            FieldSpec("currency", "string"),
            FieldSpec("state", "enum", "posted | pending | denied"),
        ),
        authoritative=True,
        collection="ap-records",
        tags=("payables",),
        competency_questions=(
            "Did {label} match its purchase order and receiving report?",
        ),
    ),
    ObjectType(
        name="InvoiceLine",
        label="Invoice line",
        summary="One billed line: description, quantity, unit price, amount.",
        natural_key="invoice key + ordinal",
        fields=(
            FieldSpec("description", "string"),
            FieldSpec("sku", "string"),
            FieldSpec("quantity", "number"),
            FieldSpec("unit_price_usd", "money"),
            FieldSpec("amount_usd", "money"),
            FieldSpec("kind", "enum", "goods | freight | tax | discount"),
        ),
        authoritative=True,
        collection="ap-records",
    ),
    ObjectType(
        name="PurchaseOrder",
        label="Purchase order",
        summary=(
            "A commitment to buy, synced from the ERP. The first leg of the "
            "three-way match."
        ),
        natural_key="po number",
        fields=(
            FieldSpec("po_number", "string"),
            FieldSpec("ordered_date", "date"),
            FieldSpec("total_usd", "money"),
            FieldSpec("currency", "string"),
            FieldSpec("status", "enum",
                      "the source's own word, slugged: open | pending-receipt "
                      "| partially-received | closed | cancelled"),
        ),
        collection="ap-records",
        tags=("payables",),
        competency_questions=(
            "What was received and invoiced against {label}?",
        ),
    ),
    ObjectType(
        name="PurchaseOrderLine",
        label="PO line",
        summary="One ordered line: what was committed to, at what price.",
        natural_key="po id + ordinal",
        fields=(
            FieldSpec("description", "string"),
            FieldSpec("sku", "string"),
            FieldSpec("quantity_ordered", "number"),
            FieldSpec("unit_price_usd", "money"),
            FieldSpec("amount_usd", "money"),
            FieldSpec("po_number", "string", "denormalized from the parent"),
        ),
        collection="ap-records",
    ),
    ObjectType(
        name="ReceivingReport",
        label="Receiving report",
        summary=(
            "What the dock actually took in. The leg most three-way matches "
            "are missing, and the reason an invoice can be right and still "
            "not payable yet."
        ),
        natural_key="receipt number",
        fields=(
            FieldSpec("receipt_number", "string"),
            FieldSpec("received_date", "date"),
            FieldSpec("po_number", "string"),
            FieldSpec("status", "enum", "partial | complete"),
        ),
        collection="ap-records",
        tags=("payables",),
        competency_questions=(
            "Which purchase order does {label} book goods in against?",
        ),
    ),
    ObjectType(
        name="ReceivingReportLine",
        label="Receiving line",
        summary="One received line: how many of what actually arrived.",
        natural_key="receiving report id + ordinal",
        fields=(
            FieldSpec("description", "string"),
            FieldSpec("sku", "string"),
            FieldSpec("quantity_received", "number"),
            FieldSpec("condition", "enum", "good | damaged | short"),
            FieldSpec("receipt_number", "string", "denormalized from the parent"),
        ),
        collection="ap-records",
    ),
    ObjectType(
        name="MatchSet",
        label="Match set",
        summary=(
            "The three-way match, kept rather than computed and discarded: "
            "each invoice line paired to a PO line and a receiving line, with "
            "the variance and the tolerance that judged it. This is what the "
            "reviewer reads and what the auditor asks for."
        ),
        natural_key="the invoice key it matches",
        fields=(
            FieldSpec("verdict", "enum", "matched | variance | unmatched | blocked"),
            FieldSpec("matched_lines", "number"),
            FieldSpec("unmatched_lines", "number"),
            FieldSpec("price_variance_usd", "money"),
            FieldSpec("quantity_variance", "number"),
            FieldSpec("tolerance_ref", "ref", "the governed values applied"),
            FieldSpec("po_numbers", "string", "the orders this match spanned"),
            FieldSpec("receipt_numbers", "string",
                      "the receiving reports this match spanned"),
        ),
        authoritative=True,
        collection="ap-records",
    ),
    ObjectType(
        name="Opportunity",
        label="Opportunity",
        summary=(
            "A deal being pursued: intent, before anything has been ordered. "
            "It earns its own type rather than a stage field on Company "
            "because one company can be pursued several times at once and "
            "each pursuit has its own answer — a single stage on the "
            "counterparty would have to pick one and lose the rest. It is "
            "also the only place the gap in deal-to-order can be seen: the "
            "window between a deal closing won and a sales order existing is "
            "a fact about two rows disagreeing, and there is no such window "
            "if they are the same row."
        ),
        natural_key="source + opportunity id at the CRM that issued it",
        fields=(
            FieldSpec("external_id", "string"),
            FieldSpec("name", "string"),
            FieldSpec("stage", "enum",
                      "prospecting | qualification | proposal | negotiation | "
                      "closed-won | closed-lost"),
            FieldSpec("amount_usd", "money", "what the CRM expects it to be worth"),
            FieldSpec("close_date", "date",
                      "expected while the deal is open, actual once it closed"),
            FieldSpec("owner_email", "string",
                      "the rep carrying it — an observation awaiting "
                      "promotion, not a Link: it becomes one the moment it "
                      "resolves to an Employee rather than a string the CRM "
                      "typed"),
            FieldSpec("company_name", "string",
                      "denormalized from pursued_with, for cheap reads"),
        ),
        # The CRM is the book of record for a pipeline; we mirror it, and a
        # correction to a stage belongs there, not here.
        authoritative=False,
        collection="sales-records",
        tags=("commerce", "pipeline"),
        competency_questions=(
            "Who is {label} being pursued with, and who else is talking to them?",
            "Did {label} close won, and did an order ever follow it?",
        ),
    ),
    ObjectType(
        name="SalesOrder",
        label="Sales order",
        summary=(
            "A commitment to sell, synced from the ERP — the mirror image of "
            "PurchaseOrder, and a type for the same reason: what was ordered, "
            "at what price, and whether it has been released are facts about "
            "the order and not about the customer who placed it. It is also "
            "the first thing Cumulus is meant to write back to, and an Action "
            "needs a subject with an identity: 'release the order' has to "
            "name an object, not a row in somebody else's table."
        ),
        natural_key="order number as the ERP issued it",
        fields=(
            FieldSpec("order_number", "string"),
            FieldSpec("external_id", "string", "the ERP's internal record id"),
            FieldSpec("ordered_date", "date"),
            FieldSpec("status", "enum",
                      "pending-approval | pending-fulfillment | "
                      "partially-fulfilled | fulfilled | billed | closed | "
                      "cancelled — the ERP's own vocabulary, slugified. It "
                      "was declared shorter than this before a real order had "
                      "been read; a mirror's enum is whatever the mirrored "
                      "system says, and pretending otherwise would make every "
                      "partially-fulfilled order unclassifiable"),
            FieldSpec("total_usd", "money"),
            FieldSpec("currency", "string"),
            FieldSpec("memo", "string"),
            FieldSpec("company_name", "string",
                      "denormalized from sold_to, for cheap reads"),
        ),
        authoritative=False,
        collection="sales-records",
        tags=("commerce", "orders"),
        competency_questions=(
            "Who did we sell {label} to, and what is on it?",
            "Which catalogue items does {label} commit us to deliver?",
        ),
    ),
    ObjectType(
        name="SalesOrderLine",
        label="Sales order line",
        summary=(
            "One sold line: what was committed to, how much of it, at what "
            "price. A part of its order in the strict sense — its ordinal "
            "only means anything inside one order, and it dies with it."
        ),
        natural_key="sales order id + ordinal",
        fields=(
            FieldSpec("description", "string"),
            FieldSpec("sku", "string"),
            FieldSpec("quantity", "number"),
            FieldSpec("unit_price_usd", "money",
                      "what this line actually charged, which is not "
                      "necessarily what the catalogue lists"),
            FieldSpec("amount_usd", "money"),
            FieldSpec("order_number", "string", "denormalized from the parent"),
        ),
        collection="sales-records",
    ),
    ObjectType(
        name="Item",
        label="Item",
        summary=(
            "A thing the business sells or buys, as the catalogue defines it. "
            "Lines on both sides of the house carry a SKU, and until that SKU "
            "is an entity 'what did we sell most of' is a count over "
            "spellings and 'is this the same part we bought' cannot be asked "
            "at all. Deliberately thin: a catalogue entry, not an inventory "
            "position. How many are on a shelf is a fact about a warehouse "
            "and a moment, and putting it here would make the type wrong the "
            "instant it was written."
        ),
        natural_key="SKU",
        fields=(
            FieldSpec("sku", "string"),
            FieldSpec("name", "string"),
            FieldSpec("item_type", "enum",
                      "inventory | non-inventory | service | assembly"),
            FieldSpec("base_price_usd", "money",
                      "the list price; what a line actually charged lives on "
                      "the line"),
            FieldSpec("external_id", "string",
                      "the source's internal id for the item — what order "
                      "lines reference, which is deliberately not the SKU, "
                      "and the reason a line needs resolving rather than "
                      "reading"),
        ),
        authoritative=False,
        collection="sales-records",
        tags=("commerce", "catalogue"),
        competency_questions=(
            "Which orders have carried {label}, and at what price each time?",
            "Where has {label} sold below its list price?",
        ),
    ),
    ObjectType(
        name="Mailbox",
        label="Mailbox",
        summary=(
            "One correspondent's mail, as the provider keeps it. A governed "
            "mirror on exactly the terms Calendar is one — the provider is "
            "the system of record and we hold a copy a steward may not "
            "overwrite — because correspondence is where a commitment is "
            "usually made before any system is told about it. What is "
            "deliberately not here is a mail store: bodies stop at a preview, "
            "attachments are not modelled, and nothing in Cumulus sends. This "
            "is evidence that a conversation happened and who was in it, not "
            "a second inbox."
        ),
        natural_key="mailbox address at the provider",
        fields=(
            FieldSpec("address", "string"),
            FieldSpec("display_name", "string", "what the provider shows"),
            FieldSpec("provider", "string", "microsoft-365 | google | …"),
            FieldSpec("external_id", "string"),
            FieldSpec("job_title", "string",
                      "as the directory behind the mailbox states it — a "
                      "claim the HRIS outranks where the two disagree"),
            FieldSpec("department", "string"),
            FieldSpec("timezone", "string"),
            FieldSpec("synced_at", "date", "when the mirror last landed"),
        ),
        authoritative=False,
        collection="correspondence",
        tags=("people", "correspondence", "evidence"),
        competency_questions=(
            "What has landed in {label} lately, and who sent it?",
            "Who does {label} correspond with outside the company?",
        ),
    ),
    ObjectType(
        name="MailMessage",
        label="Mail message",
        summary=(
            "One message as it sits in one mailbox — a **part** of that "
            "mailbox, for the same reason a calendar event is part of a "
            "calendar: a thread delivered to four people is four rows with "
            "four provider ids, four read states and four filing decisions. "
            "The shared conversation is not the row; it is what the rows "
            "agree on, and `conversation_id` is where that agreement is "
            "recorded. Custody stops at `body_preview` on purpose — enough "
            "for a person to recognize the message, far short of holding the "
            "customer's mail."
        ),
        natural_key="mailbox address + provider message id",
        fields=(
            FieldSpec("external_id", "string", "the provider's id for this copy"),
            FieldSpec("subject", "string"),
            FieldSpec("folder", "string", "where the provider filed it"),
            FieldSpec("from_email", "string",
                      "denormalized from sent_by, and the fallback when the "
                      "sender resolves to nobody we know"),
            FieldSpec("from_name", "string"),
            FieldSpec("to_emails", "string", "recipients, as addressed"),
            FieldSpec("cc_emails", "string"),
            FieldSpec("body_preview", "string",
                      "the opening of the body and nothing more — the custody "
                      "line, stated as a field so it is visible rather than "
                      "assumed"),
            FieldSpec("importance", "enum", "low | normal | high"),
            FieldSpec("conversation_id", "string",
                      "the provider's thread id — what the copies of one "
                      "exchange agree on"),
            FieldSpec("received_at", "date"),
            FieldSpec("sent_at", "date"),
        ),
        collection="correspondence",
    ),
    ObjectType(
        name="Project",
        label="Project",
        summary=(
            "One engagement the delivery organization is running for a "
            "counterparty: what was promised, how far along it is, and what it "
            "has cost in hours against what was budgeted. It earns a type "
            "because delivery is the half of the business no other feed can "
            "see — a sales order says what was sold and an invoice says what "
            "was billed, and neither says whether the work is happening. It "
            "is also where the deal-to-order disagreement is visible from the "
            "far end: a project sitting `on-hold` because its funding order "
            "is still Pending Approval is the same fact the ERP and the CRM "
            "are arguing about, seen by the people who cannot start."
        ),
        natural_key=(
            "source + the PSA's own project code (`ENG-0100`) — source-scoped "
            "for the reason an opportunity id is: two delivery systems can "
            "both issue `ENG-0100` and mean different engagements"
        ),
        fields=(
            FieldSpec("project_code", "string",
                      "the user-facing code the PSA prints on everything"),
            FieldSpec("external_id", "string", "the PSA's internal record id"),
            FieldSpec("source", "string",
                      "the feed that landed it — the key is source-scoped, so "
                      "the lookup that places a phase has to be too"),
            FieldSpec("name", "string"),
            FieldSpec("status", "enum",
                      "planned | active | on-hold | completed | cancelled — "
                      "the PSA's own vocabulary, slugified"),
            FieldSpec("project_manager", "string",
                      "the manager as the PSA holds them: a free-typed display "
                      "name (`S. Hoffmann`), never an id. An observation "
                      "awaiting promotion and deliberately not a Link — "
                      "joining that string to an HRIS Employee is a fuzzy "
                      "match with a steward at the end of it, not something a "
                      "batch feed may assert"),
            FieldSpec("contributors", "string",
                      "the display names that have logged time, on the same "
                      "terms and for the same reason — Delivery's workload, "
                      "as far as it can honestly be read"),
            FieldSpec("start_date", "date"),
            FieldSpec("target_end_date", "date"),
            FieldSpec("budget_hours", "number"),
            FieldSpec("actual_hours", "number",
                      "hours logged against the whole project, as the PSA sums "
                      "them"),
            FieldSpec("billable_hours", "number",
                      "the billable share of them, summed on the way in"),
            FieldSpec("company_name", "string",
                      "denormalized from delivered_for, for cheap reads"),
        ),
        # The PSA is the book of record for delivery; we mirror it, and a
        # status corrected here would be corrected in the wrong place.
        authoritative=False,
        collection="delivery-records",
        tags=("delivery", "projects", "commerce"),
        competency_questions=(
            "Who is {label} for, and is it burning its budget?",
            "Which order and which deal is {label} the delivery of?",
            "Who is working on {label}, and on which phase?",
        ),
    ),
    ObjectType(
        name="ProjectPhase",
        label="Project phase",
        summary=(
            "One stage of a project — Discovery, Scan, QC, Go-live — and a "
            "**part** of it in the strict sense: a phase named `QC` means "
            "nothing outside the engagement that has one, and it dies with the "
            "project. It is the smallest unit worth mirroring, and the line is "
            "drawn here deliberately: the PSA's *tasks* are not modelled. A "
            "task list is how a coordinator organizes a week, it is abandoned "
            "the moment a project closes (the estate's finished projects carry "
            "none), and every question Cumulus has of the delivery org — is "
            "this engagement on track, where are the hours going, who is on it "
            "— is answered a level up. Mirroring 48 task rows to answer none "
            "of them would be modelling the feed instead of the business."
        ),
        natural_key="project id + the PSA's own phase id",
        fields=(
            FieldSpec("external_id", "string", "the PSA's id for this phase"),
            FieldSpec("name", "string"),
            FieldSpec("sequence", "number", "where it falls in the plan"),
            FieldSpec("status", "enum",
                      "not-started | in-progress | complete"),
            FieldSpec("target_date", "date"),
            FieldSpec("logged_hours", "number",
                      "hours attributed to this phase — summed on the way in "
                      "from the time entries, through the task each one names. "
                      "Time booked straight to the project reaches no phase "
                      "and is counted on the run rather than spread"),
            FieldSpec("billable_hours", "number"),
            FieldSpec("project_code", "string", "denormalized from the parent"),
        ),
        collection="delivery-records",
    ),
)

LINK_TYPES: tuple[LinkType, ...] = (
    LinkType("submitted", "Employee", "ExpenseRequest", "one-to-many",
             "the employee who submitted the request"),
    LinkType("has_expense_line", "ExpenseRequest", "ExpenseLine", "one-to-many",
             "the lines projected out of the request's documents",
             nature="composition"),
    LinkType("evidenced_by", "ExpenseLine", "Document", "many-to-many",
             "the document (and region) a line was read from"),
    LinkType("decided_by", "ExpenseRequest", "Employee", "many-to-many",
             "the person whose Task decision resolved the request — plural, "
             "because a request reviewed twice can be decided by two people"),
    # Declared ahead of their writer: an HRIS/teams sync flow is the named
    # trigger. Until it exists these read "0 in the graph — a promise nothing
    # has kept yet", and the ref: fields on Employee carry the observations.
    LinkType("member_of", "Employee", "Team", "many-to-one",
             "the team an employee belongs to — one at a time here; matrix "
             "orgs would relax this deliberately"),
    LinkType("reports_to", "Employee", "Employee", "many-to-one",
             "the manager — what turns 'roll up a manager's spend' from "
             "unanswerable into a traversal"),
    LinkType("subteam_of", "Team", "Team", "many-to-one",
             "the parent team — nesting is what makes the org chart a tree "
             "rather than a flat list"),
    LinkType("led_by", "Team", "Employee", "many-to-one",
             "the team's lead — distinct from reports_to, because the org "
             "chart and the management chain are allowed to disagree"),
    LinkType("has_capability", "Employee", "Capability", "many-to-many",
             "an encoded skill — proficiency and how it was evidenced live "
             "on the link, not on either endpoint"),
    LinkType("has_calendar", "Employee", "Calendar", "one-to-many",
             "a calendar this person owns — one owner per calendar, several "
             "calendars per person (personal, on-call, a room they manage)"),
    LinkType("has_event", "Calendar", "CalendarEvent", "one-to-many",
             "the blocks on the calendar", nature="composition"),
    LinkType("organized_by", "CalendarEvent", "Employee", "many-to-one",
             "who called this meeting. Authorship, not attendance: an "
             "organizer can book a room they never enter, and 'what did "
             "Priya call this week' is a different question from 'where was "
             "Priya'"),
    LinkType("attended_by", "CalendarEvent", "Employee", "many-to-many",
             "who is expected in the room; the response and whether they are "
             "required live on the link, because both are facts about the "
             "invitation rather than about the person or the meeting"),
    LinkType("identified_as", "Vendor", "SourceRecord", "one-to-many",
             "an external system's id for this vendor — reversible, and "
             "carrying the confidence and method that produced it",
             nature="identity"),
    LinkType("issued", "Vendor", "Invoice", "one-to-many",
             "the vendor that issued the invoice"),
    LinkType("has_invoice_line", "Invoice", "InvoiceLine", "one-to-many",
             "the invoice's billed lines", nature="composition"),
    LinkType("evidenced_by_document", "Invoice", "Document", "many-to-many",
             "the invoice artifact the extraction read"),
    LinkType("ordered_from", "PurchaseOrder", "Vendor", "many-to-one",
             "the vendor a PO was raised against — one, and a second would be "
             "a resolution bug"),
    LinkType("has_po_line", "PurchaseOrder", "PurchaseOrderLine", "one-to-many",
             "the PO's committed lines", nature="composition"),
    LinkType("receives_against", "ReceivingReport", "PurchaseOrder", "many-to-one",
             "the PO a receiving report books against"),
    LinkType("has_receiving_line", "ReceivingReport", "ReceivingReportLine",
             "one-to-many", "the receiving report's lines",
             nature="composition"),
    LinkType("billed_against", "Invoice", "PurchaseOrder", "many-to-many",
             "the PO(s) an invoice bills against — many, on a consolidated bill"),
    # A MatchSet is a *part* of its invoice — the key test decides the
    # non-obvious case: its natural key embeds the invoice key, and it cannot
    # exist without one. A retained computation is still a dependent thing.
    LinkType("matched_by", "Invoice", "MatchSet", "one-to-one",
             "the retained three-way match for this invoice",
             nature="composition"),
    LinkType("matches_invoice_line", "MatchSet", "InvoiceLine", "one-to-many",
             "one pairing in the match, with its variance in the link props"),
    # ---- the counterparty book, and what hangs off it ---------------------
    #
    # Declared ahead of their writers, like the org-chart links above: the crm,
    # sales, mail and dms sync flows are the named triggers. Until those land
    # these read "0 in the graph", which is the honest state of a promise
    # nothing has kept yet, and the ontology overlay draws them as outlines.
    #
    # A note on the qualified names below (`company_identified_as`,
    # `sent_by_employee`). A link type is addressed by name — `link_type(name)`
    # returns one declaration, and `GraphService._validate_link` checks every
    # edge against it — so two declarations sharing a name would mean the
    # second one's edges were validated against the first one's endpoints and
    # refused. Where the same relationship holds between different types, each
    # gets its own name, qualified by the endpoint that varies. The cost is a
    # longer name; the alternative is widening one declaration's endpoints
    # until it stops constraining anything, which is how a cardinality check
    # quietly becomes decoration.
    LinkType("works_at", "Person", "Company", "many-to-one",
             "the company this person is at. One at a time, deliberately: a "
             "contact who has moved gets a new edge and the old one is "
             "withdrawn, so 'who did we know at Northwind in March' stays "
             "answerable instead of being overwritten"),
    LinkType("company_identified_as", "Company", "SourceRecord", "one-to-many",
             "an external system's row for this company — the CRM's account, "
             "the ERP's customer, reversible and carrying the confidence and "
             "method that produced it. This is the edge the whole identity "
             "story is told through: four systems, four ids, one company, and "
             "a steward who can take any one of them back",
             nature="identity"),
    LinkType("person_identified_as", "Person", "SourceRecord", "one-to-many",
             "an external system's row for this human. Kept separate from the "
             "company's so a wrong merge can be withdrawn at the person "
             "without disturbing the organization they were filed under",
             nature="identity"),
    LinkType("pursued_with", "Opportunity", "Company", "many-to-one",
             "the company a deal is being pursued with — one, and a second "
             "would mean the CRM had reparented the deal rather than that two "
             "companies were being sold the same thing"),
    LinkType("sold_to", "SalesOrder", "Company", "many-to-one",
             "the customer an order was raised for. The mirror of "
             "`ordered_from`, and the edge that makes 'what is open with this "
             "counterparty' a traversal rather than a name search"),
    LinkType("has_so_line", "SalesOrder", "SalesOrderLine", "one-to-many",
             "the order's sold lines", nature="composition"),
    LinkType("sells", "SalesOrderLine", "Item", "many-to-one",
             "the catalogue item this line commits us to. A Link and not the "
             "line's `sku` string, because the question Item exists to answer "
             "— where has this sold, and for how much — is a traversal, and a "
             "string that has to be matched by spelling on every read is not "
             "one"),
    LinkType("subfolder_of", "Folder", "Folder", "many-to-one",
             "the parent folder — what makes the filing system a tree rather "
             "than a list of paths that only look nested"),
    LinkType("filed_under", "Document", "Folder", "many-to-one",
             "the folder a document is filed in. Where a customer put "
             "something is evidence about what they think it is, and it is "
             "also where their retention rules were hung — both are lost the "
             "moment a document is only a row in a flat list"),
    LinkType("has_message", "Mailbox", "MailMessage", "one-to-many",
             "the messages in the mailbox", nature="composition"),
    LinkType("sent_by", "MailMessage", "Person", "many-to-one",
             "the outside human who sent this message, where the sender's "
             "address resolved to one we know. A sender we cannot place is "
             "counted and left as `from_email` — minting a Person per unknown "
             "address would fill the counterparty book with newsletters"),
    LinkType("sent_by_employee", "MailMessage", "Employee", "many-to-one",
             "the same relationship when the sender is one of ours. Two "
             "declarations rather than one loose edge, because 'did this "
             "commitment come from inside or outside the company' is the "
             "first question anybody asks of a thread"),
    LinkType("addressed_to", "MailMessage", "Person", "many-to-many",
             "an outside human this message was addressed to; whether they "
             "were on To or Cc lives on the link, because it is a fact about "
             "the addressing and not about either end"),
    LinkType("addressed_to_employee", "MailMessage", "Employee", "many-to-many",
             "an internal recipient, on the same terms"),
    LinkType("delivered_for", "Project", "Company", "many-to-one",
             "the counterparty an engagement is being delivered for. The "
             "third edge into the counterparty book, beside `sold_to` and "
             "`pursued_with`, and the one that completes the arc: pursued, "
             "sold, delivered. One company, because a project the PSA "
             "reparented is a correction and not two customers sharing an "
             "engagement"),
    LinkType("has_phase", "Project", "ProjectPhase", "one-to-many",
             "the project's stages, in plan order", nature="composition"),
    # ---- proposed identity: two canonical objects, one real-world party ----
    #
    # The `*_identified_as` edges above answer "which source row is this
    # entity". These four answer the harder question no single source can:
    # *these two entities we already minted are one party*. Per-source
    # resolution cannot see it — an ERP customer is scored against the book
    # before its sibling row lands, and a Vendor is never scored against a
    # Company at all — so it is proposed by a windowed sweep across sources
    # instead, with evidence attached and `status=in_review` until a steward
    # rules.
    #
    # Four declarations for the same relationship, qualified by the endpoints
    # that vary, for the reason spelled out above `company_identified_as`: one
    # widened edge would be validated against whichever pair was declared
    # first. `many-to-many`, because a stale row can plausibly belong to more
    # than one survivor and the sweep's job is to show a steward the field,
    # not to pick.
    #
    # Nothing here supersedes anything. A confirmed proposal flips its own
    # `status` and stays reversible; collapsing the two rows into one canonical
    # object is a separate, later decision made in the workbench with the
    # `reversibleWith` note this link carries.
    LinkType("company_same_as", "Company", "Company", "many-to-many",
             "a proposed merge inside the counterparty book — the ERP's 2019 "
             "`Northwind Trading Co.` and today's `Northwind Traders`, "
             "different natural keys and one company. Carries the confidence, "
             "the signals that produced it, who decided, what an un-merge "
             "restores, and a status nothing may act on until it reads "
             "`confirmed`",
             nature="identity"),
    LinkType("company_same_as_vendor", "Company", "Vendor", "many-to-many",
             "a proposed merge *across* the two books: the firm we sell to is "
             "the firm we buy from. Company and Vendor stay separate types "
             "because the roles are separate; this is the edge that says they "
             "are one party without collapsing either role",
             nature="identity"),
    LinkType("vendor_same_as", "Vendor", "Vendor", "many-to-many",
             "a proposed merge inside the vendor master, for the duplicates "
             "normalization does not catch — a trading name against a legal "
             "one, joined by a domain rather than a spelling",
             nature="identity"),
    LinkType("person_same_as", "Person", "Person", "many-to-many",
             "a proposed merge between two outside humans — `Tom Berg` and "
             "`Thomas Berg` at one account. Deliberately never asserted by "
             "the CRM feed, which keys people on an address precisely so that "
             "two spellings stay two people until somebody looks",
             nature="identity"),
)

# Ordered highest-authority first. A human correction outranks every system;
# among systems, the ERP is the book of record for commercial terms, and a
# Flow's own inference ranks last because it is a guess until something
# confirms it.
# `hris` sits with `erp`: each is the system of record for its own subject
# (people, commerce), and precedence is declared over authorities rather than
# per field, so the two never actually compete. `calendar` joins them on the
# same argument and ranks just below: the provider is the only thing that
# knows what is on somebody's Friday, but where it restates a fact the HRIS
# owns — a display name, an office — the HRIS wins.
#
# `crm` enters below the two books of record and above the mirrors. It ranks
# under `erp` on purpose: where both describe the same commercial fact —
# payment terms, an order total — the ERP is what the business is actually
# bound by, and the CRM is what somebody typed while selling. On relationship
# facts the CRM usually speaks unopposed anyway (nothing else holds an opinion
# about an industry or a website), so the ordering costs it nothing it should
# have won. `office` ranks last among the systems, just above a Flow's own
# guess: a job title read off a mail directory or a company name read off a
# sending domain is a hint worth keeping and worth losing to anything that
# actually knows. Ranking mail higher would let a signature block quietly
# outrank the HRIS.
#
# `dms` sits between `office` and `procurement` and is the narrowest authority
# in the list, deliberately. A document system knows exactly one kind of fact
# first-hand — where a file is filed, what it is called, which revision is
# current — and it is unopposed on all of them. Everything else it appears to
# say is a *filename*: `Northwind Traders proposal 2026-06-04.pdf` names a
# counterparty and a date, and reading either off it is inference, not
# observation. So it ranks below the mirrors that at least observed something
# and above `procurement`'s second-hand supplier records, and the ordering
# almost never has to be exercised — which is the point. Without a bucket of
# its own the DMS would fall through `_authority` to `flow`, and a filing
# system's own tree would have been recorded as something Cumulus guessed.
#
# `psa` sits directly below `dms` on the same argument, and it is the weakest
# system voice in the list. The delivery system is unopposed on the one kind of
# fact it owns first-hand — which engagements exist, what phase they are in,
# how many hours went into them — so its rank is almost never exercised, which
# is the point. Where it *is* opposed it should lose to everything: a PSA
# client name is not a record of a company, it is what a coordinator typed
# after a phone call (`Apex Mfg`, `Kestrel CU`), never synced from the CRM or
# the ERP and never validated against anything. That is thinner evidence than a
# filename, which is at least written down when a document is filed. It ranks
# above `procurement` only because `procurement`'s rows are second-hand
# supplier records from systems this estate does not run.
#
# Both claims are always retained, so this order is a read-time decision: the
# CRM/ERP disagreement the demo turns on is visible *because* neither claim was
# discarded, and reordering this tuple re-resolves history rather than
# rewriting it.
PRECEDENCE: tuple[str, ...] = (
    "human", "erp", "hris", "crm", "calendar", "office", "dms", "psa",
    "procurement", "flow", "model",
)

ONTOLOGY = Ontology(objects=OBJECT_TYPES, links=LINK_TYPES, precedence=PRECEDENCE)


def validate_ontology(ontology: Ontology = ONTOLOGY) -> list[str]:
    """Structural conformance of the type system itself.

    The composition rules are what keep the derived hierarchy honest: a part
    has exactly one owner by type, ownership cannot form a cycle, and a
    cardinality that would let a part have two owners is refused at
    declaration time rather than discovered as a data bug.
    """
    problems: list[str] = []
    names = {o.name for o in ontology.objects}
    for obj in ontology.objects:
        where = f"type `{obj.name}`"
        for f in obj.fields:
            target = f.ref_target
            if target is not None and target not in names:
                problems.append(
                    f"{where}: field `{f.name}` refs unknown type `{target}`"
                )
    # A link type is addressed by name and nothing else: `link_type()` returns
    # the first declaration matching, and every endpoint and cardinality check
    # in `GraphService` is made against whatever comes back. A second
    # declaration under the same name is therefore not a wider link — it is a
    # link whose edges are all validated against somebody else's endpoints and
    # refused. Caught here, at declaration time, because the failure otherwise
    # surfaces as an unexplainable `wrong-endpoint-type` on the first write.
    seen_links: set[str] = set()
    for link in ontology.links:
        if link.name in seen_links:
            problems.append(
                f"link `{link.name}`: declared twice — a link type is looked "
                "up by name, so the second declaration's edges would be "
                "checked against the first's endpoints. Qualify the name by "
                "the endpoint that varies"
            )
        seen_links.add(link.name)
    for link in ontology.links:
        where = f"link `{link.name}`"
        if link.from_type not in names:
            problems.append(f"{where}: unknown from_type `{link.from_type}`")
        if link.to_type not in names:
            problems.append(f"{where}: unknown to_type `{link.to_type}`")
        if link.nature not in ("association", "composition", "identity"):
            problems.append(f"{where}: unknown nature `{link.nature}`")
        if link.composes and link.cardinality not in ("one-to-many", "one-to-one"):
            problems.append(
                f"{where}: composition requires one-to-many or one-to-one — "
                f"`{link.cardinality}` would let a part have two owners"
            )
    # At most one compositional link may target a type: single owner.
    owners: dict[str, list[str]] = {}
    for link in ontology.links:
        if link.composes:
            owners.setdefault(link.to_type, []).append(link.name)
    for target, via in owners.items():
        if len(via) > 1:
            problems.append(
                f"type `{target}` has {len(via)} owners ({', '.join(via)}); "
                "a part has exactly one"
            )
    # Tags are facets for filtering, deliberately unregistered — but an
    # untagged root is unfindable by facet, and a part declaring its own
    # tags would drift from its owner's.
    for obj in ontology.objects:
        if ontology.owner_of(obj.name) is None and not obj.tags:
            problems.append(f"root type `{obj.name}` declares no tags")
        if ontology.owner_of(obj.name) is not None and obj.tags:
            problems.append(
                f"part type `{obj.name}` declares tags — parts inherit "
                "their owner's"
            )
        # The admission test, enforced: a root that cannot name the question
        # it answers has not earned its place in the rail.
        if ontology.owner_of(obj.name) is None and not obj.competency_questions:
            problems.append(
                f"root type `{obj.name}` names no competency question"
            )
        if ontology.owner_of(obj.name) is not None and obj.competency_questions:
            problems.append(
                f"part type `{obj.name}` declares competency questions — a "
                "part is asked about through its owner"
            )
    # Ownership must be a forest: walking owners always terminates at a root.
    for obj in ontology.objects:
        seen: set[str] = set()
        current: str | None = obj.name
        while current is not None:
            if current in seen:
                problems.append(
                    f"composition cycle through `{obj.name}` — ownership "
                    "must be a forest"
                )
                break
            seen.add(current)
            current = ontology.owner_of(current)
    return problems


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------

ONTOLOGY_ASSET_ID = "enterprise-ontology"


def ontology_markdown(ontology: Ontology = ONTOLOGY) -> str:
    """Render the ontology as the governed-knowledge document it is.

    Written for both readers: a person opening it in the console, and a
    retrieval pass answering "what does Cumulus know about a vendor".
    """
    lines: list[str] = [
        "# Enterprise ontology",
        "",
        "The declared shape of the enterprise: what kinds of thing exist, how "
        "they connect, and which system wins when two of them disagree. "
        "Authored in Python (`server/app/ontology.py`) and exported here, so "
        "this document and the running graph cannot drift apart.",
        "",
        "## Claim precedence",
        "",
        "When two sources assert the same field of the same object, both "
        "claims are retained and this order decides which one resolves:",
        "",
    ]
    for i, source in enumerate(ontology.precedence, start=1):
        lines.append(f"{i}. `{source}`")
    lines += [
        "",
        "Changing this order re-resolves history rather than rewriting it — "
        "the claims are still there.",
        "",
        "## How things are addressed (the ref grammar)",
        "",
        "Everything addressable carries a ref, and the graph is the only "
        "place refs join. Provenance is refs stamped on claims, read-sets and "
        "journals; these are the schemes:",
        "",
        "- `graph:{Type}/{id}@rev{n}` — an object, pinned to the revision "
        "that was read. Resolved by `GET /api/v1/graph/objects/{id}`.",
        "- `link:{type}/{id}` — a typed edge, including identity links.",
        "- `knowledge:{id}@rev{n}` — a corpus asset at the revision a "
        "decision actually read. Resolved by `GET /api/v1/refs/{ref}`.",
        "- `document:{id}` — a stored artifact (bytes plus extraction "
        "regions). Resolved by `GET /api/v1/documents/{id}`.",
        "- `run_{...}` / step ids — the act that wrote a claim (`run_id` + "
        "`step_id` on the claim). Resolved by `GET /api/v1/runs/{id}`.",
        "- `task_{...}` — the unit of human judgment that released a gate.",
        "",
        "A leaf no ref points at is unreachable by meaning, and should be "
        "treated as suspect.",
        "",
        "## Hierarchy",
        "",
        "Links carry a *nature*: `association` (two independent things), "
        "`composition` (the target is a **part** — identified through its "
        "owner, cannot exist without it, dies with it), or `identity` (an "
        "external record and a canonical entity are the same real-world "
        "thing). Part-ness of a type is derived from compositional links, "
        "never declared, and two invariants enforce it on every instance: a "
        "part has exactly one live owner, and its natural key embeds its "
        "owner's.",
        "",
        "The independent types, with their parts:",
        "",
    ]
    for root in ontology.roots():
        parts = ontology.parts_of(root.name)
        tags = " · ".join(root.tags)
        lines.append(
            f"- **{root.label}** ({tags})"
            + (f" ▸ {', '.join(parts)}" if parts else "")
        )
    lines += [
        "",
        "Types carry freeform *tags* — facets for filtering, deliberately "
        "unregistered; parts inherit their owner's.",
    ]
    lines += [
        "",
        "Governance: a new independent type must declare its **competency "
        "questions** — what it answers that no existing type can — and its "
        "natural key. This is enforced, not aspirational: the registry "
        "refuses a root with no questions. Types that fail the test enter "
        "as parts, fields, or not at all; the hierarchy absorbs growth so "
        "the list of independent concepts stays short.",
        "",
        "## Object types",
        "",
    ]
    for obj in ontology.objects:
        marks = []
        if obj.authoritative:
            marks.append("**authoritative**")
        if obj.collection:
            marks.append(f"collection `{obj.collection}`")
        suffix = f" — {' · '.join(marks)}" if marks else ""
        lines.append(f"### {obj.label} (`{obj.name}`){suffix}")
        lines.append("")
        lines.append(obj.summary)
        lines.append("")
        owner = ontology.owner_of(obj.name)
        if owner is not None:
            lines.append(
                f"Part of `{owner}` (via "
                f"`{ontology.owning_link(obj.name).name}`)."
            )
        lines.append(f"Natural key: {obj.natural_key}.")
        if obj.competency_questions:
            lines.append("")
            lines.append(
                "Competency questions — what this type exists to answer:"
            )
            for q in obj.competency_questions:
                lines.append(f"- {q}")
        if obj.fields:
            lines.append("")
            for f in obj.fields:
                note = f" — {f.note}" if f.note else ""
                kind = (
                    f"ref → {f.ref_target}" if f.ref_target else f.kind
                )
                lines.append(f"- `{f.name}` ({kind}){note}")
        lines.append("")
    lines += ["## Link types", ""]
    for link in ontology.links:
        nature = {
            "identity": " · identity (confidence, reversible)",
            "composition": " · composition (the target is a part)",
        }.get(link.nature, "")
        lines.append(
            f"- `{link.from_type} --{link.name}--> {link.to_type}` "
            f"({link.cardinality}){nature}: {link.summary}"
        )
    lines.append("")
    return "\n".join(lines)


def ontology_asset_fields() -> dict:
    """Frontmatter for the exported asset, matching the seed-file convention."""
    return {
        "id": ONTOLOGY_ASSET_ID,
        "path": "platform/ontology/enterprise",
        "title": "Enterprise ontology",
        "kind": "schema-export",
        "scope": "org",
        "owner": "platform-eng",
        "tags": ["ontology", "governed-knowledge", "graph"],
        "status": "published",
        "content": ontology_markdown(),
    }
