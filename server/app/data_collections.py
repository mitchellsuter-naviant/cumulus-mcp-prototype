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
        retention_note="superseded revisions are retained, never overwritten",
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
        written_by=("vendor-sync", "purchase-order-sync", "receiving-sync"),
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
