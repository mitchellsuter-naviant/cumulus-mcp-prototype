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
        ),
        authoritative=True,
        collection="workforce",
        tags=("people", "expenses", "org-chart"),
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
        ),
        authoritative=True,
        collection="workforce",
        tags=("people", "org-chart"),
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
            "extraction read. Evidence, never a fact in itself."
        ),
        natural_key="content hash",
        fields=(
            FieldSpec("filename", "string"),
            FieldSpec("media_type", "string"),
            FieldSpec("byte_size", "number"),
            FieldSpec("sha256", "string"),
            FieldSpec("kind", "enum", "receipt | invoice | purchase-order | receiving-report"),
            FieldSpec("collection_id", "string",
                      "the Collection whose retention governs the bytes"),
            FieldSpec("retention_until", "date", "stamped when the bytes landed"),
        ),
        collection="audit-documents",
        tags=("expenses", "payables", "evidence"),
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
            FieldSpec("aliases", "string", "other names this vendor arrived under"),
            FieldSpec("onboarding_state", "enum",
                      "unconfirmed until a human vouches for the vendor"),
        ),
        authoritative=True,
        collection="vendor-master",
        tags=("payables", "mdm"),
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
            FieldSpec("record_kind", "enum", "vendor | po | rr"),
            FieldSpec("landed_at", "date"),
        ),
        collection="source-landing",
        tags=("payables", "landing", "mdm"),
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
            FieldSpec("status", "enum", "open | closed | cancelled"),
        ),
        collection="ap-records",
        tags=("payables",),
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
)

# Ordered highest-authority first. A human correction outranks every system;
# among systems, the ERP is the book of record for commercial terms, and a
# Flow's own inference ranks last because it is a guess until something
# confirms it.
PRECEDENCE: tuple[str, ...] = ("human", "erp", "procurement", "flow", "model")

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
        "Governance: a new independent type must name its **competency "
        "question** — the question it answers that no existing type can — "
        "and its natural key. Types that fail that test enter as parts, "
        "fields, or not at all; the hierarchy absorbs growth so the list of "
        "independent concepts stays short.",
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
