"""Collections: the unit governance actually hangs off.

Retention, custody, authoritativeness and read access are not properties of a
row — they are properties of the *set* a row belongs to. "Keep approved
receipts for two years" is a sentence about a Collection. So Collections are
declared here, in Python, and exported to the map as a governed-knowledge
document, the same way the ontology is: the policy and the running system read
from one artifact rather than a schema plus a wiki page.

Four properties do the work:

- **Role** — knowledge (makes flows smarter) or data (the record of what
  happened). The doctrine's Dataset Role dial.
- **Custody** — `copy` (the bytes live in the map) or `reference` (the map
  holds a pointer into a system of record). What the customer walks away with
  differs between the two, which is why it is declared rather than implied.
- **Retention** — an ISO-8601 duration, or `indefinite`. Applied when a row is
  written, so every stored document carries the date it becomes purgeable.
- **Authoritative** — this Collection is the system of record for its subject,
  so a Flow may read it as canonical. The tag that separates a vendor master
  from a scratch table.

Nothing here is enforced by magic: `retention_until` is stamped on write and
`expiring()` is what a purge pass would read. That is the honest state — the
policy is declared and computed, the sweeper is not built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from .models import utcnow

Role = Literal["knowledge", "data"]
Custody = Literal["copy", "reference"]
Origin = Literal["authored", "flywheel", "flow-written", "derived", "synced"]


@dataclass(frozen=True)
class DataCollection:
    id: str
    name: str
    summary: str
    role: Role
    kind: str
    origin: Origin
    container: str
    custody: Custody = "copy"
    # ISO-8601 duration (`P2Y`, `P90D`) or the literal "indefinite".
    retention: str = "indefinite"
    retention_note: str = ""
    authoritative: bool = False
    legal_hold: bool = False
    object_types: tuple[str, ...] = ()
    written_by: tuple[str, ...] = ()
    read_by: tuple[str, ...] = ()


COLLECTIONS: tuple[DataCollection, ...] = (
    DataCollection(
        id="knowledge-library",
        name="Knowledge library",
        summary=(
            "Policies, standards, guidelines and how-tos. Flows parse governed "
            "values out of these at run time, so an edit here changes behavior "
            "with no code change."
        ),
        role="knowledge", kind="documents", origin="authored",
        container="assets", authoritative=True, retention="indefinite",
        retention_note=(
            "an edit bumps the revision and replaces the content; superseded "
            "revisions are not yet retained"
        ),
        written_by=("human",),
        read_by=("expense-reimbursement", "accounts-payable", "hr-agent"),
    ),
    DataCollection(
        id="flow-definitions",
        name="Flow definitions",
        summary="Authored logic as declarative Tier-2 map assets.",
        role="knowledge", kind="definitions", origin="authored",
        container="flows", authoritative=True, retention="indefinite",
        written_by=("human",),
    ),
    DataCollection(
        id="eval-datasets",
        name="Golden sets & correction sets",
        summary=(
            "Eval cases keyed to a FlowContract, and the raw→corrected pairs "
            "every human decision appends to."
        ),
        role="knowledge", kind="eval datasets", origin="flywheel",
        container="flow_golden_sets", retention="indefinite",
        written_by=("expense-reimbursement", "accounts-payable", "human"),
    ),
    DataCollection(
        id="workforce",
        name="Workforce",
        summary=(
            "Employees as canonical entities — the root of the expense graph. "
            "Cumulus mints the id; an HRIS feed would attach its own."
        ),
        role="data", kind="entities", origin="synced",
        container="graph_objects", authoritative=True, retention="indefinite",
        object_types=("Employee", "Team", "Capability"),
        # Two writers on purpose: the HRIS feed syncs people and teams, and
        # people-ops asserts the capability catalogue — a skill is not a
        # system's observation.
        written_by=("workforce-sync", "human:people-ops"),
        read_by=("expense-reimbursement", "hr-agent"),
    ),
    DataCollection(
        id="calendars",
        name="Calendars",
        summary=(
            "Each person's schedule, mirrored from the calendar provider so a "
            "day can be read next to the work it is about. Copied rather than "
            "referenced — the console renders a week without a round trip — "
            "but deliberately not authoritative: the provider is the system "
            "of record, and a correction belongs there, not here."
        ),
        role="data", kind="entities", origin="synced",
        container="graph_objects", custody="copy", authoritative=False,
        retention="P1Y",
        retention_note=(
            "a working record, not a financial one; no question asked of a "
            "schedule reaches back further than a year"
        ),
        object_types=("Calendar", "CalendarEvent"),
        written_by=("calendar-sync",),
        read_by=("hr-agent",),
    ),
    DataCollection(
        id="correspondence",
        name="Correspondence",
        summary=(
            "Mailboxes and the messages in them, mirrored from the mail "
            "provider — headers, addressing, and an opening preview of the "
            "body. The cap is the governance, not an implementation shortcut: "
            "a commitment is usually made in mail before any system is told "
            "about it, so the graph needs to know a conversation happened and "
            "who was in it, and it emphatically does not need to become a "
            "second copy of the customer's inbox. Not authoritative and never "
            "will be — the provider is the system of record, and nothing here "
            "sends."
        ),
        role="data", kind="entities", origin="synced",
        container="graph_objects", custody="copy", authoritative=False,
        retention="P1Y",
        retention_note=(
            "the same horizon as calendars, and for the same reason: this is "
            "a working record read to explain something recent. A preview is "
            "not evidence worth keeping for seven years, and pretending "
            "otherwise would be storing the customer's mail under a "
            "compliance justification it cannot support"
        ),
        object_types=("Mailbox", "MailMessage"),
        written_by=("mail-sync",),
    ),
    DataCollection(
        id="vendor-master",
        name="Vendor master",
        summary=(
            "One concept of vendor, with every source system's id attached as "
            "a reversible identity Link. This is the master-data Collection: "
            "the AP flow resolves the name printed on an invoice against it, "
            "so a vendor is an entity rather than a string on a row."
        ),
        role="data", kind="entities", origin="synced",
        container="graph_objects", authoritative=True, retention="indefinite",
        retention_note="a deactivated vendor is retained while any invoice references it",
        object_types=("Vendor",),
        written_by=("vendor-sync", "accounts-payable"),
        read_by=("accounts-payable",),
    ),
    DataCollection(
        id="company-master",
        name="Company master",
        summary=(
            "The counterparty book: one concept of company, with every source "
            "system's row attached as a reversible identity Link, and the "
            "people we deal with at each. The sell side's answer to the "
            "vendor master, and governed identically because it fails "
            "identically — a counterparty that exists four times is four "
            "partial answers to every question anybody asks about them. "
            "Contacts live here rather than in a book of their own: a person "
            "at a customer is master data about that counterparty, with the "
            "same retention, the same custody and the same authority behind "
            "it, and splitting them would put one relationship under two "
            "policies."
        ),
        role="data", kind="entities", origin="synced",
        container="graph_objects", authoritative=True, retention="indefinite",
        retention_note=(
            "a dormant company is retained while any order, invoice or "
            "message still references it — the record of who we dealt with "
            "outlives the dealing"
        ),
        object_types=("Company", "Person"),
        # Declared ahead of their writers: crm-sync reifies accounts and
        # contacts, sales-sync reifies ERP customers onto the same companies.
        written_by=("crm-sync", "sales-sync"),
        # mail-sync reads it rather than writing it, which is the whole point
        # of the split: a message resolves its correspondents against the book
        # and counts the ones it cannot place, instead of minting a company
        # out of every sending domain.
        read_by=("mail-sync",),
    ),
    DataCollection(
        id="sales-records",
        name="Sales records",
        summary=(
            "The commercial cycle on the sell side: what we are pursuing, "
            "what has been ordered, and the catalogue both are written "
            "against. One Collection rather than three because it is one "
            "lifecycle — an opportunity becomes an order or it does not, and "
            "the interesting question is almost always about the seam between "
            "them. Mirrored, not owned: the ERP and the CRM are the books of "
            "record, and a stage or a total corrected here would be corrected "
            "in the wrong place."
        ),
        role="data", kind="entities", origin="synced",
        container="graph_objects", custody="copy", authoritative=False,
        retention="P7Y",
        retention_note=(
            "set by the orders, which are financial records on the same "
            "seven-year footing as AP; the pipeline rows ride the horizon "
            "because retention is a property of the set and the set is drawn "
            "around the whole cycle, not around one row's shelf life"
        ),
        object_types=("Opportunity", "SalesOrder", "SalesOrderLine", "Item"),
        written_by=("crm-sync", "sales-sync"),
    ),
    DataCollection(
        id="source-landing",
        name="Source landing",
        summary=(
            "External rows exactly as they arrived, before resolution. Kept so "
            "a rule change can re-resolve without re-fetching, which is the "
            "difference between a sync you can reason about and one you have to "
            "trust."
        ),
        role="data", kind="landing", origin="synced",
        container="graph_objects", authoritative=False, retention="P1Y",
        retention_note="raw rows age out once re-resolution is no longer plausible",
        object_types=("SourceRecord",),
        # Every feed that reifies lands here, including the four declared
        # ahead of their writers: crm-sync and sales-sync land the rows behind
        # a Company or a Person, and employment-event-sync lands the HRIS
        # ledger as-is rather than promoting it to a type — an event that only
        # ever gets read back as history has not earned one.
        written_by=(
            "vendor-sync", "purchase-order-sync", "receiving-sync",
            "crm-sync", "sales-sync", "employment-event-sync",
        ),
    ),
    DataCollection(
        id="expense-records",
        name="Expense records",
        summary=(
            "Expense requests and their lines, as decided. The record of what "
            "was reimbursed and why."
        ),
        role="data", kind="entities", origin="flow-written",
        container="graph_objects", authoritative=True, retention="P7Y",
        retention_note=(
            "financial-record default, deliberately longer than the two-year "
            "artifact retention below — the decision outlives the paper"
        ),
        object_types=("ExpenseRequest", "ExpenseLine"),
        written_by=("expense-reimbursement",),
    ),
    DataCollection(
        id="ap-records",
        name="AP records",
        summary=(
            "Invoices, their lines, the POs and receiving reports they match "
            "against, and the retained MatchSet for each."
        ),
        role="data", kind="entities", origin="flow-written",
        container="graph_objects", authoritative=True, retention="P7Y",
        object_types=(
            "Invoice", "InvoiceLine", "PurchaseOrder", "PurchaseOrderLine",
            "ReceivingReport", "ReceivingReportLine", "MatchSet",
        ),
        written_by=("accounts-payable", "purchase-order-sync", "receiving-sync"),
    ),
    DataCollection(
        id="audit-documents",
        name="Audit documents",
        summary=(
            "The submitted artifacts themselves — receipts and invoices — with "
            "the regions an extraction read, so a decision can be re-checked "
            "against the paper it was made from."
        ),
        role="data", kind="documents", origin="flow-written",
        container="documents", custody="copy", retention="P2Y",
        retention_note=(
            "two years from the run that approved it; the Flow stamps the date "
            "on write"
        ),
        authoritative=True,
        object_types=("Document",),
        written_by=("expense-reimbursement", "accounts-payable"),
    ),
    DataCollection(
        id="document-library",
        name="Document library",
        summary=(
            "The customer's own document system, mirrored as knowledge about "
            "where things are filed rather than as a copy of what is in them. "
            "The tree lands as Folder objects; the files themselves stay put "
            "and land as Document rows stamped with this Collection's id and "
            "a `storage_ref` pointing back at the original. That split is the "
            "governance: a folder path is metadata a person put there on "
            "purpose — and is where their retention rules were hung — while "
            "the bytes are the customer's, in the system they already trust "
            "with them. Copying a document library to make it queryable is "
            "how a map turns into a second system of record nobody asked for."
        ),
        role="data", kind="documents", origin="synced",
        container="graph_objects", custody="reference", authoritative=False,
        retention="indefinite",
        retention_note=(
            "there is nothing here to purge but pointers; the horizon on the "
            "documents themselves belongs to the system that holds them, and "
            "a reference that outlives its target reads as a broken link "
            "rather than as retained content"
        ),
        # Only the tree. `Document` is governed by `audit-documents` as a
        # type — the partition allows exactly one home per type — and a
        # DMS-sourced row is placed here per instance by its `collection_id`,
        # which is what that field is for.
        object_types=("Folder",),
        written_by=("dms-sync",),
    ),
    # Run journals are deliberately NOT a Collection. The doctrine is explicit
    # that machinery/operational state — "flow run journals, the trigger
    # index, queues" — is not a Dataset: journals are how the engine resumes a
    # suspended run, owned by the execution layer and read through the runs
    # API. What the customer keeps is the *decisions* (the task ledger below)
    # and the graph writes each step made, both of which carry the run/step
    # refs needed to reconstruct what happened. Declaring the journal here too
    # made it look like map content, which contradicted the LOCKED clause.
    DataCollection(
        id="task-ledger",
        name="Task ledger",
        summary=(
            "Units of human judgment and the decisions that closed them. This "
            "IS the curation ledger — map content the customer keeps, unlike "
            "the run journals (machinery state, deliberately not a Collection)."
        ),
        role="data", kind="ledger", origin="flow-written",
        container="tasks", authoritative=True, retention="P7Y",
        written_by=("expense-reimbursement", "accounts-payable", "vendor-sync"),
    ),
    DataCollection(
        id="vendor-profiles",
        name="Vendor payment profiles",
        summary=(
            "Per-vendor rolling statistics behind the anomaly screen: how this "
            "vendor's invoices normally look. Derived, so disposable — delete "
            "it and the profile Flow rebuilds it from the graph."
        ),
        role="data", kind="derived", origin="derived",
        container="vendor_profiles", authoritative=False, retention="P90D",
        retention_note="rebuildable at any time; nothing here is a source of truth",
        written_by=("vendor-profile-refresh",), read_by=("accounts-payable",),
    ),
)

BY_ID = {c.id: c for c in COLLECTIONS}


def get(collection_id: str) -> DataCollection | None:
    return BY_ID.get(collection_id)


def for_object_type(object_type: str) -> DataCollection | None:
    return next((c for c in COLLECTIONS if object_type in c.object_types), None)


def written_by(flow_id: str) -> tuple[DataCollection, ...]:
    """The Collections this flow is *declared* to write.

    Declared, which is the whole reason it is worth reading back: what a flow
    is said to write and what its runs actually wrote are two different facts,
    and `flow_projection.py` puts them side by side.
    """
    return tuple(c for c in COLLECTIONS if flow_id in c.written_by)


def read_by(flow_id: str) -> tuple[DataCollection, ...]:
    """The Collections this flow is declared to read."""
    return tuple(c for c in COLLECTIONS if flow_id in c.read_by)


# ---------------------------------------------------------------------------
# Retention arithmetic
# ---------------------------------------------------------------------------

_DURATION = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?$")


def parse_duration(duration: str) -> timedelta | None:
    """ISO-8601 durations, calendar-approximated.

    Years and months are approximated (365/30 days) on purpose: retention is a
    policy horizon, not an anniversary, and a purge that runs a day late is not
    a compliance event. If it ever needs to be exact, that is the moment to
    take a date library, not before.
    """
    if not duration or duration == "indefinite":
        return None
    match = _DURATION.match(duration.strip().upper())
    if not match:
        return None
    years, months, days = (int(g) if g else 0 for g in match.groups())
    total = years * 365 + months * 30 + days
    return timedelta(days=total) if total else None


def retention_until(collection_id: str, from_iso: str = "") -> str:
    """The date a row in this Collection becomes purgeable. `""` = indefinite."""
    collection = BY_ID.get(collection_id)
    if collection is None or collection.legal_hold:
        return ""
    delta = parse_duration(collection.retention)
    if delta is None:
        return ""
    from datetime import datetime

    start = utcnow()
    if from_iso:
        try:
            start = datetime.fromisoformat(from_iso)
        except ValueError:
            pass
    return (start + delta).isoformat()


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------

COLLECTIONS_ASSET_ID = "data-collections"


def collections_markdown() -> str:
    lines = [
        "# Data collections",
        "",
        "Every Collection the map owns, and the governed properties that apply "
        "to everything inside it. Authored in Python "
        "(`server/app/collections.py`) and exported here, so the retention a "
        "document is stamped with and the retention this document states are "
        "the same value.",
        "",
        "Custody `copy` means the bytes or rows live in the map and leave with "
        "it; `reference` means the map holds a pointer into a system of record. "
        "`authoritative` means a Flow may read the Collection as canonical.",
        "",
    ]
    for role in ("knowledge", "data"):
        lines.append(f"## {role.title()} role")
        lines.append("")
        for c in (c for c in COLLECTIONS if c.role == role):
            marks = [f"kind `{c.kind}`", f"origin `{c.origin}`", f"custody `{c.custody}`"]
            if c.authoritative:
                marks.append("**authoritative**")
            lines.append(f"### {c.name} (`{c.id}`)")
            lines.append("")
            lines.append(c.summary)
            lines.append("")
            lines.append(" · ".join(marks))
            lines.append("")
            retention = (
                "retained indefinitely" if c.retention == "indefinite"
                else f"retention `{c.retention}`"
            )
            note = f" — {c.retention_note}" if c.retention_note else ""
            lines.append(f"- Retention: {retention}{note}")
            lines.append(f"- Container: `{c.container}`")
            if c.object_types:
                lines.append(
                    "- Object types: "
                    + ", ".join(f"`{t}`" for t in c.object_types)
                )
            if c.written_by:
                lines.append("- Written by: " + ", ".join(f"`{w}`" for w in c.written_by))
            if c.read_by:
                lines.append("- Read by: " + ", ".join(f"`{r}`" for r in c.read_by))
            lines.append("")
    return "\n".join(lines)


def collections_asset_fields() -> dict:
    return {
        "id": COLLECTIONS_ASSET_ID,
        "path": "platform/governance/data-collections",
        "title": "Data collections",
        "kind": "schema-export",
        "scope": "org",
        "owner": "platform-eng",
        "tags": ["governance", "governed-knowledge", "retention", "collections"],
        "status": "published",
        "content": collections_markdown(),
    }
