"""Inbound sync as Flows: vendors, purchase orders, receiving reports.

The doctrine retired the Workflow/DataFlow split, and this module is what that
decision looks like in code. A vendor feed landing 200 rows and an invoice
arriving for extraction are the same kind of thing — authored logic, run by the
engine, journalled, resumable — differing only in their Trigger and in whether
they suspend. `deliver` says what arrived and `dispatch.TRIGGERS` says what
that starts; the five bindings here are the windowed ones.

One rule they do *not* share, and it matters: **a batch feed never suspends for
one ambiguous row.** A per-case Flow blocks on a gate because the case cannot
proceed without the answer. A feed that stopped on row 47 would hold up rows 48
through 200 for no reason, so an ambiguous row lands, asserts nothing, and
raises a `match_review` Task; the steward's decision is applied afterwards by
`apply_match_decision`. That is why resolution and link-application are separate
steps here.

What each sync run does, in order:

  t1 …Delivered (the trigger row: the run's record that the Dispatcher
  started it, logged before it existed) → x1 read-resolution-policy (governed
  thresholds) → s1 land-records (SourceRecords, exactly as they arrived) →
  z1 resolve-records (score against what we know; assert nothing) →
  a1 apply-resolutions (mint, link, or raise a steward Task) →
  e1 …Synced (Event)

The landing step is the one people skip and regret. Keeping the raw row means a
change to the matching rules can **re-resolve without re-fetching**, which is
the difference between a sync you can reason about and one you have to trust.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .anomaly import parse_anomaly_thresholds
from .auth import Principal, flow_principal
from .dispatch import Dispatcher, InMemoryEventLog
from .gateway import ActionGateway, InMemoryLedgerStore, PendingAction
from .graph import (
    Claim,
    GraphService,
    GraphWriter,
    InMemoryGraphStore,
    LinkRecord,
    normalize_key,
    object_id,
    parse_ref,
)
from . import disagreement_sweep, identity_sweep
from .disagreement_sweep import (
    DISAGREEMENT_SWEEP_FLOW_ID,
    STATE_WINDOW_EVENT,
)
from .identity_sweep import (
    IDENTITY_SWEEP_FLOW_ID,
    IDENTITY_WINDOW_EVENT,
    SCAN_BY_LINK,
)
from .mappings import steward_queue_for, steward_queue_for_type
from .matching import parse_match_tolerances
from .models import utcnow
from .ontology import ONTOLOGY
from .resolution import (
    ResolutionOutcome,
    ResolutionThresholds,
    read_resolution_thresholds,
    resolve,
)
from .runs import RunContext, RunEngine
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.sync")

VENDOR_FLOW_ID = "vendor-sync"
PO_FLOW_ID = "purchase-order-sync"
RECEIVING_FLOW_ID = "receiving-sync"
WORKFORCE_FLOW_ID = "workforce-sync"
CALENDAR_FLOW_ID = "calendar-sync"
CRM_FLOW_ID = "crm-sync"
SALES_FLOW_ID = "sales-sync"
MAIL_FLOW_ID = "mail-sync"
EMPLOYMENT_EVENT_FLOW_ID = "employment-event-sync"
DMS_FLOW_ID = "dms-sync"
PSA_FLOW_ID = "psa-sync"
#: Which desk an ambiguous row or a proposed merge goes to is declared per
#: Mapping (`Mapping.steward_queue`) and read back through
#: `mappings.steward_queue_for` (per feed) and `steward_queue_for_type` (per
#: book), so C4 can rebind it in one edit. The two module constants that used
#: to hold the answers here are gone: a second copy of a routing decision is a
#: second thing that can disagree with the contract, and this module's own
#: `MatchTarget` was the reader that made the drift possible.

# The fact each feed shape reports. One name per shape rather than a single
# `SourceDelivered`, because a name bound to five Triggers would start five
# flows on every delivery. `deliver` names these; `dispatch.TRIGGERS` says
# which flow each one starts, and this module no longer decides that.
VENDOR_EVENT = "VendorRecordsDelivered"
PO_EVENT = "PurchaseOrdersDelivered"
RECEIVING_EVENT = "ReceivingReportsDelivered"
WORKFORCE_EVENT = "WorkforceDelivered"
CALENDAR_EVENT = "CalendarsDelivered"
CRM_EVENT = "CrmBookDelivered"
SALES_EVENT = "SalesRecordsDelivered"
MAIL_EVENT = "MailDelivered"
EMPLOYMENT_EVENT_EVENT = "EmploymentEventsDelivered"
DOCUMENTS_EVENT = "DocumentsDelivered"
PSA_EVENT = "PsaBookDelivered"

SyncKind = Literal[
    "vendors", "purchase-orders", "receiving-reports", "workforce",
    "calendars", "crm-book", "sales-records", "mail", "employment-events",
    "documents", "psa-book",
]

#: Every sync flow, and the kind its runs report under in the Sources lens.
#: Declared here beside the event names rather than inside `sources()`, because
#: a feed that is invisible on the Sources screen until somebody remembers to
#: edit a dict a thousand lines down is a feed nobody can see is broken. One
#: place a feed registers; `sources()` reads this and nothing else.
FEED_FLOWS: dict[str, SyncKind] = {
    VENDOR_FLOW_ID: "vendors",
    PO_FLOW_ID: "purchase-orders",
    RECEIVING_FLOW_ID: "receiving-reports",
    WORKFORCE_FLOW_ID: "workforce",
    CALENDAR_FLOW_ID: "calendars",
    CRM_FLOW_ID: "crm-book",
    SALES_FLOW_ID: "sales-records",
    MAIL_FLOW_ID: "mail",
    EMPLOYMENT_EVENT_FLOW_ID: "employment-events",
    DMS_FLOW_ID: "documents",
    PSA_FLOW_ID: "psa-book",
}


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class VendorRecord(BaseModel):
    external_id: str
    name: str
    tax_id: str = ""
    status: str = "active"
    payment_terms: str = ""
    remit_country: str = ""
    # The ERP has always sent these and this shape dropped them, which cost
    # nothing while the only question asked of a vendor was "which vendor is
    # this row" — a name and a tax id answer that. It costs a great deal once
    # the question is "is this vendor the company we also sell to", because a
    # name is exactly the evidence that fails there: `Apex Mfg. Services LLC`
    # is not Apex Manufacturing, and the only thing that says so is that one
    # is on `apexmfgservices.example.com` in the 815 and the other is not.
    email: str = ""
    phone: str = ""
    aliases: list[str] = Field(default_factory=list)
    # A source saying "this one is gone". Never a delete on our side: a vendor
    # with open invoices has to stay resolvable.
    deleted: bool = False


class PurchaseOrderLineRecord(BaseModel):
    sku: str = ""
    description: str = ""
    quantity_ordered: float = 0.0
    unit_price_usd: float = 0.0


class PurchaseOrderRecord(BaseModel):
    external_id: str
    po_number: str
    vendor_external_id: str = ""
    vendor_name: str = ""
    ordered_date: str = ""
    currency: str = "USD"
    status: str = "open"
    lines: list[PurchaseOrderLineRecord] = Field(default_factory=list)


class ReceivingLineRecord(BaseModel):
    sku: str = ""
    description: str = ""
    quantity_received: float = 0.0
    condition: str = "good"


class ReceivingRecord(BaseModel):
    external_id: str
    receipt_number: str
    po_number: str = ""
    received_date: str = ""
    status: str = "complete"
    lines: list[ReceivingLineRecord] = Field(default_factory=list)


class TeamRecord(BaseModel):
    """One organizational unit as the HRIS holds it."""

    external_id: str
    slug: str
    name: str
    mission: str = ""
    # Bind columns: identifiers of other entities, resolved on the way in.
    parent_slug: str = ""
    lead_email: str = ""


class EmployeeRecord(BaseModel):
    """One person as the HRIS holds them.

    `email` is the natural key and `manager_email`/`team_slug` are bind
    columns — identifiers of other entities carried in the row. Nothing here
    needs a steward to adjudicate a match, which is exactly why the HRIS
    mapping binds rather than reifies: an exact key needs no landing record.

    `manager_external_id` and `department_external_id` are the *same* two
    references in the source's own vocabulary, and they travel beside the
    derived ones rather than instead of them. An HRIS names a manager by
    internal id; the email is a translation, and a translation is only
    available while the manager's row is on the page. Carrying both means a
    delta of one promoted employee resolves their manager against a person the
    graph already holds, where before it cost a round trip to the estate to
    re-read a key the row had already given us.

    `status` is the one column here whose absence was a *lie* rather than a
    gap: without it every worker the HRIS holds — including the ones who left
    — landed indistinguishable from the ones who did not.
    """

    external_id: str
    email: str
    name: str = ""
    title: str = ""
    location: str = ""
    start_date: str = ""
    manager_email: str = ""
    team_slug: str = ""
    # Additive and default-empty: the door stays backward compatible, and a
    # source that has none of this keeps landing exactly as it did.
    manager_external_id: str = ""
    department_external_id: str = ""
    status: str = ""
    employment_type: str = ""


class CalendarRecord(BaseModel):
    """One calendar as the provider holds it."""

    external_id: str
    address: str
    name: str = ""
    provider: str = ""
    timezone: str = "America/New_York"
    # Bind column: whose calendar this is.
    owner_email: str = ""


class CalendarEventRecord(BaseModel):
    """One block on one calendar — the provider's row, not the meeting.

    A meeting with four attendees arrives as four of these, one per calendar,
    each with its own `external_id`. That is not duplication to be normalized
    away: the copies can genuinely differ (a private note, a moved reminder,
    a declined response), and collapsing them would invent a shared object no
    provider actually has. What they agree on is expressed as links to the
    same people.

    Times are wall-clock local, in the calendar's own zone — the way every
    provider stores them. A UTC instant would be wrong for the recurring
    9am that has to stay 9am across a daylight-saving change.
    """

    external_id: str
    calendar_address: str
    title: str
    kind: str = "meeting"
    starts_at: str = ""
    ends_at: str = ""
    all_day: bool = False
    location: str = ""
    status: str = "confirmed"
    #: The provider's cancellation flag, carried raw. `None` from a feed that
    #: states `status` itself; a bool from one that only has the flag, and then
    #: the flow — not the connector — decides which declared enum value it is.
    #: Which of a source's values belong to which enum is a mapping question,
    #: and mapping questions are the flow's (`sync_office._importance` states
    #: the same rule for `importance`).
    cancelled: bool | None = None
    # Bind columns: attendance by work email, resolved on the way in.
    organizer_email: str = ""
    attendee_emails: list[str] = Field(default_factory=list)
    optional_emails: list[str] = Field(default_factory=list)
    agenda_ref: str = ""
    notes: str = ""


class AccountRecord(BaseModel):
    """One organization as the CRM holds it — the sales side of a company."""

    external_id: str
    name: str
    industry: str = ""
    website: str = ""
    phone: str = ""


class ContactRecord(BaseModel):
    """One human at a customer, as the CRM holds them.

    `email` is the natural key and is deliberately the *only* thing identity
    turns on. A contact with no address mints under `source:external_id`
    instead of being matched on a spelling: two Tom Bergs at two companies are
    two people, and a book of counterparties that quietly merged them would be
    worse than one that holds a duplicate a steward can see.
    """

    external_id: str
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    email: str = ""
    phone: str = ""
    # Bind column: the account this contact belongs to, by the CRM's own id.
    account_external_id: str = ""


class OpportunityRecord(BaseModel):
    """One deal in the pipeline, as the CRM holds it."""

    external_id: str
    name: str
    account_external_id: str = ""
    stage: str = ""
    amount: float = 0.0
    close_date: str = ""


class CustomerRecord(BaseModel):
    """One organization as the ERP holds it — the billing side of a company.

    The same firms the CRM knows, under their legal spellings: `Northwind
    Traders` in the CRM is `Northwind Traders Inc.` here. Both claims are kept
    and precedence resolves; the ERP outranks the CRM on commercial facts,
    which is why `payment_terms` arrives on this shape and not on the account.
    """

    external_id: str
    name: str
    email: str = ""
    phone: str = ""
    payment_terms: str = ""


class ItemRecord(BaseModel):
    """One catalogue entry. The SKU is the natural key.

    `external_id` is the source's *internal* record id, which is what order
    lines reference — deliberately not the SKU. Keeping both is what lets a
    line resolve to an item at all: the line names the id, the ontology keys
    on the SKU, and this row is the only place the two meet.
    """

    external_id: str
    sku: str
    display_name: str = ""
    item_type: str = ""
    base_price: float = 0.0


class SalesOrderLineRecord(BaseModel):
    """One sold line, as the ERP holds it — item by internal id, never SKU."""

    item_external_id: str = ""
    quantity: float = 0.0
    rate: float = 0.0
    amount: float = 0.0


class SalesOrderRecord(BaseModel):
    """One commitment to sell, with its lines.

    `order_number` is the ERP's human transaction id (`tranId`) and the natural
    key; `external_id` is its internal record id, kept because that is what the
    write-back Action in S2b will have to name.
    """

    external_id: str
    order_number: str
    customer_external_id: str = ""
    ordered_date: str = ""
    status: str = ""
    memo: str = ""
    total: float = 0.0
    lines: list[SalesOrderLineRecord] = Field(default_factory=list)


class MailboxRecord(BaseModel):
    """One correspondent's mailbox, as the provider holds it.

    The directory fields ride along because the provider carries them, not
    because they are authoritative: a job title read off a mail directory is a
    claim the HRIS outranks, and the precedence order is what says so.
    """

    external_id: str
    address: str
    display_name: str = ""
    provider: str = ""
    job_title: str = ""
    department: str = ""
    timezone: str = ""


class MailMessageRecord(BaseModel):
    """One message as it sits in one mailbox — headers, addressing, a preview.

    **There is no `body` field, and that absence is the design.** A thread
    delivered to four people is four of these, and the graph's reason for
    holding any of them is to know that a conversation happened and who was in
    it. Carrying the body would make Cumulus a second copy of the customer's
    inbox under a governance story it cannot support, so custody stops at the
    opening line — capped again on the way in by `sync_office.PREVIEW_CHARS`,
    because a provider that stuffed a whole body into a field named `preview`
    must not be able to smuggle one past the wire shape.

    `mailbox_address` is a bind column, and the message's own key embeds it:
    a provider message id is only unique inside the mailbox that holds it.
    """

    external_id: str
    mailbox_address: str
    subject: str = ""
    folder: str = ""
    # Bind columns: correspondents by address, resolved on the way in against
    # people we already know and counted — never minted — when they are not.
    from_email: str = ""
    from_name: str = ""
    to_emails: list[str] = Field(default_factory=list)
    cc_emails: list[str] = Field(default_factory=list)
    body_preview: str = ""
    importance: str = ""
    conversation_id: str = ""
    received_at: str = ""
    sent_at: str = ""


class EmploymentEventRecord(BaseModel):
    """One row of the HRIS's append-only employment ledger, carried whole.

    The odd one out on this wire, and deliberately: every other shape here
    names the columns a flow will read, because a flow is going to derive
    something from them. This one carries `payload` — the estate's row exactly
    as it arrived — because the ledger is not a source of entities. It is the
    *history* of the entities the workforce feed already lands, and history is
    the one thing you cannot re-derive later if you threw the row away.

    A promotion row says what the job, department, manager and salary became
    **and** what each of them was. That before/after pair is the whole value
    and no ontology type holds it, so the flow that receives these lands them
    and stops. Naming four of the seventeen columns on this shape would have
    quietly decided which thirteen a later reader is allowed to ask about.
    """

    external_id: str
    employee_external_id: str = ""
    event_type: str = ""
    effective_date: str = ""
    #: The source row, verbatim — including the previous-value columns the
    #: named fields above deliberately do not restate.
    payload: dict = Field(default_factory=dict)


class FolderRecord(BaseModel):
    """One node of the document system's tree, by its own id.

    `parent_external_id` and not a path: the source keys folders by id and
    holds the tree as parent pointers, so a path is something the *flow*
    computes by walking it. A connector that assembled paths would be deciding
    what a folder's identity is, which is exactly the line it does not cross.
    """

    external_id: str
    name: str = ""
    parent_external_id: str = ""


class DmsDocumentRecord(BaseModel):
    """One file the customer's document system holds — metadata, never bytes.

    `storage_ref` is the whole contract: a resolvable address in the system of
    record, built from the ids the source gave us and pinned to the revision it
    said was current. Nothing on this shape was read out of the file. That is
    not a limitation to be lifted later — it is the custody decision the
    `document-library` Collection declares, and the reason a DMS mirror does
    not quietly become a second copy of the customer's documents.
    """

    external_id: str
    name: str = ""
    folder_external_id: str = ""
    content_type: str = ""
    size: int = 0
    latest_version: int = 0
    storage_ref: str = ""


class PsaClientRecord(BaseModel):
    """One customer as the delivery system holds them — a nickname and a name.

    The thinnest counterparty row on this wire, and the most interesting one
    for that reason: the PSA carries no legal name, no website, no phone and no
    id anybody else would recognize. What it has is what a coordinator typed
    (`Apex Mfg`, `Kestrel CU`) and, usually, the address of the person they
    talk to — which is the only hard identifier this feed contributes and the
    reason both contact columns travel. A client with neither is a name and
    nothing else, and the sweep can only ever judge it on a spelling.
    """

    external_id: str
    name: str
    contact_name: str = ""
    contact_email: str = ""


class ProjectRecord(BaseModel):
    """One engagement, as the delivery system holds it.

    `project_code` is the PSA's human transaction id (`ENG-0100`) and the
    natural key's second half; `external_id` is the internal record id the
    phases, tasks and time entries all reference.

    `project_manager` is a *display name* and stays one all the way in. The PSA
    knows nothing about the HRIS and cheerfully stores `S. Hoffmann`; a shape
    that called this column `manager_email` would be promising a join nobody
    can make from the row.
    """

    external_id: str
    project_code: str = ""
    client_external_id: str = ""
    name: str = ""
    status: str = ""
    project_manager: str = ""
    start_date: str = ""
    target_end_date: str = ""
    budget_hours: float = 0.0
    #: The PSA's own sum over its time entries, carried as it was served. Kept
    #: beside the sum this feed computes because they can legitimately differ:
    #: the source totals the whole project, a delta carries one window of it.
    actual_hours: float = 0.0


class ProjectPhaseRecord(BaseModel):
    """One stage of one project."""

    external_id: str
    project_external_id: str = ""
    name: str = ""
    sequence: int = 0
    status: str = ""
    target_date: str = ""


class ProjectTaskRecord(BaseModel):
    """A task's *placement*, and deliberately nothing else about it.

    This is the shape that carries the least of what its source holds, and the
    absences are the design. The PSA serves a task's name, assignee, status,
    due date and estimate on every row; none of them are here, because no type
    in the ontology holds a task and inventing one to hold them would be
    modelling the feed. What survives is the two ids that let an hour booked
    against a task be attributed to the phase that task belongs to — the same
    role `ItemRecord` plays for an order line, except that nothing is kept at
    the end of it. Custody stated at the earliest point it can be: a column
    that never reaches the wire cannot be landed by accident later.
    """

    external_id: str
    project_external_id: str = ""
    phase_external_id: str = ""


class TimeEntryRecord(BaseModel):
    """One booked day, carried to be summed and not to be kept.

    No `notes` column, on the same reasoning as `MailMessageRecord`'s missing
    `body`: the estate serves a free-text note on every row ("On site — Verona
    plant"), the flow adds these up and stores totals, and a field that never
    reaches the wire cannot become a store of what a technician wrote about a
    customer's site.

    `person` is a display name like the project manager's, and travels for the
    aggregate rather than for a join: `Curt Boyd` is Curtis Boyd in the HRIS
    and nothing in either system says so.
    """

    external_id: str
    project_external_id: str = ""
    task_external_id: str = ""
    person: str = ""
    work_date: str = ""
    hours: float = 0.0
    billable: bool = True


class SyncRequest(BaseModel):
    """One delivery from one source system.

    `watermark` is the source's own cursor, echoed back on the run so a
    re-delivery is recognizable and a gap is visible. `idempotency_key` lets a
    retried push be recognized as the same delivery rather than a second one.
    """

    source: str = Field(min_length=1, max_length=64)
    watermark: str = ""
    idempotency_key: str = ""
    vendors: list[VendorRecord] = Field(default_factory=list)
    purchase_orders: list[PurchaseOrderRecord] = Field(default_factory=list)
    receiving_reports: list[ReceivingRecord] = Field(default_factory=list)
    # Teams and employees travel together: the org chart cannot be wired
    # until both ends of every edge exist, so one delivery is one run.
    teams: list[TeamRecord] = Field(default_factory=list)
    employees: list[EmployeeRecord] = Field(default_factory=list)
    # Calendars and their events travel together for the same reason teams
    # and employees do: an event cannot hang off a calendar that has not
    # landed yet.
    calendars: list[CalendarRecord] = Field(default_factory=list)
    calendar_events: list[CalendarEventRecord] = Field(default_factory=list)
    # The CRM's book travels together: a contact names its account and an
    # opportunity names the company it is being pursued with, so splitting
    # them across deliveries would strand both edges.
    accounts: list[AccountRecord] = Field(default_factory=list)
    contacts: list[ContactRecord] = Field(default_factory=list)
    opportunities: list[OpportunityRecord] = Field(default_factory=list)
    # The ERP's sell side, for the same reason: an order line names an item by
    # the id only the item row translates, and an order names its customer.
    customers: list[CustomerRecord] = Field(default_factory=list)
    items: list[ItemRecord] = Field(default_factory=list)
    sales_orders: list[SalesOrderRecord] = Field(default_factory=list)
    # Mail travels the way calendars do, and for the same reason: a message is
    # a part of the mailbox that holds it, so a delivery that split the two
    # would orphan every message whose mailbox had not moved that window.
    mailboxes: list[MailboxRecord] = Field(default_factory=list)
    mail_messages: list[MailMessageRecord] = Field(default_factory=list)
    # The HRIS ledger travels alone: it references the workforce feed's people
    # by the source's own id and derives nothing, so there is no second half
    # it has to arrive with.
    employment_events: list[EmploymentEventRecord] = Field(default_factory=list)
    # Folders and documents travel together for the reason teams and employees
    # do — a document cannot be filed under a folder that has not landed.
    folders: list[FolderRecord] = Field(default_factory=list)
    documents: list[DmsDocumentRecord] = Field(default_factory=list)
    # The delivery book travels as one thing for the reason the CRM's does, and
    # then some: a phase names its project, a task names its phase, and an hour
    # names a task. Split across deliveries, every hour would arrive with
    # nowhere to land and every phase with no project to be part of.
    psa_clients: list[PsaClientRecord] = Field(default_factory=list)
    projects: list[ProjectRecord] = Field(default_factory=list)
    project_phases: list[ProjectPhaseRecord] = Field(default_factory=list)
    project_tasks: list[ProjectTaskRecord] = Field(default_factory=list)
    time_entries: list[TimeEntryRecord] = Field(default_factory=list)


class SourceStatus(BaseModel):
    """What the Data → Sources lens reads for one feed.

    Two counts of arrival, because one word was doing both jobs. `records` is
    what the delivery *carried*; `records_landed` is what the landing step
    kept. On a bind feed they are equal — nothing is reified, so every row
    binds — but on a reify feed they diverge hard: the PSA delivery carries 374
    rows and lands 13 of them as `SourceRecord`s, and a screen that summed only
    the second reported a third of the traffic as if it were all of it.

    The three dispositions are `None` rather than `0` when the feed's flow
    never reported them. A bind mapping has no resolve step to link or mint
    with and no payload hash to suppress a redelivery by, so `0 minted · 0
    linked · 0 unchanged` over 145 landed rows was a confident answer to a
    question this contract does not ask. Absent says that; zero cannot.
    """

    source: str
    kind: SyncKind
    flow_id: str
    last_run_id: str = ""
    last_run_at_iso: str = ""
    last_status: str = ""
    watermark: str = ""
    deliveries: int = 0
    #: Rows the delivery carried, before anything decided what to keep.
    records: int = 0
    records_landed: int = 0
    linked: int | None = None
    created: int | None = None
    awaiting_steward: int = 0
    unchanged: int | None = None
    last_error: str = ""


def _accumulate(total: int | None, outputs: dict, key: str) -> int | None:
    """Sum a disposition across runs, keeping "never reported" distinct from 0.

    A run that does not carry the key leaves the total exactly as it was — so
    a feed whose flow has no resolve step stays `None` however many times it
    delivers, and one that reported a real zero stays `0`.
    """
    if key not in outputs:
        return total
    return (total or 0) + int(outputs.get(key) or 0)


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# What a steward's decision writes into
# ---------------------------------------------------------------------------


class MatchedRecord(BaseModel):
    """The part of a landed row a steward's decision needs.

    A read of the payload rather than a second declaration of the wire: extra
    fields are ignored, so a vendor row, a CRM account and an ERP customer all
    answer the same three questions — what is it called, what did the source
    call it, and is there a hard identifier on it.
    """

    external_id: str = ""
    name: str = ""
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    tax_id: str = ""

    @property
    def display(self) -> str:
        return (
            self.name
            or " ".join(filter(None, (self.first_name, self.last_name)))
            or self.email
            or self.external_id
        )


@dataclass(frozen=True)
class MatchTarget:
    """Where a decision on one landed row lands, keyed by its `record_kind`.

    Two of the five columns this used to carry were copies of facts already
    declared elsewhere, and a copy is a thing that can disagree. Both are
    derived now:

    - **`identity_link`** comes from the ontology, which is where the three
      sibling identity edges are declared and which endpoint each joins.
    - **`queue`** comes from the Mapping, which is where the routing decision
      is written down and where `mappings.py` promises C4 can rebind it in one
      edit. That promise was only half kept while a constant here held the
      answer for the fallback path.

    What is left is genuinely per-kind and declared nowhere else: which book a
    kind writes into, the governed Action it commits under (per book, because
    the Action carries the desk that may commit it), whose authority signs a
    write no human authorized, and the natural key a *rejected* match mints
    under — names for the two counterparty books, an address for a human,
    because two people who share a name are two people.
    """

    object_type: str
    action_id: str
    flow_id: str
    key: Callable[[MatchedRecord], str]

    @property
    def identity_link(self) -> str:
        declared = ONTOLOGY.source_identity_link(self.object_type)
        return declared.name if declared is not None else ""

    @property
    def queue(self) -> str:
        """The desk this book's judgments belong to, per the Mappings.

        Only ever the *fallback*: a Task carries the queue it was raised into,
        and `apply_match_decision` prefers that, because re-deriving the desk
        at decision time would let a Mapping rebound after the escalation
        commit under a desk the Task never sat in. This answers the case where
        there is no Task to ask — a seed, a test, a decision reconstructed.
        """
        return steward_queue_for_type(self.object_type)


MATCH_TARGETS: dict[str, MatchTarget] = {
    "vendor": MatchTarget(
        "Vendor", "link-source-record", VENDOR_FLOW_ID, lambda r: r.name,
    ),
    "company": MatchTarget(
        "Company", "link-company-record", CRM_FLOW_ID, lambda r: r.name,
    ),
    "person": MatchTarget(
        "Person", "link-company-record", CRM_FLOW_ID,
        lambda r: r.email.strip().lower(),
    ),
}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SyncService:
    def __init__(
        self,
        graph: GraphService,
        knowledge,
        tasks: TaskService,
        engine: RunEngine | None = None,
        gateway: ActionGateway | None = None,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self.graph = graph
        self.knowledge = knowledge
        self.tasks = tasks
        self.engine = engine
        # Never None — see the note in payables.py. Every write in this module
        # goes through the Gateway, including the ones no human authorized.
        self.gateway = gateway or ActionGateway(
            graph or GraphService(InMemoryGraphStore()), InMemoryLedgerStore()
        )
        # Never bypassed, for the same reason — see the note in payables.py.
        self.dispatcher = dispatcher or (
            Dispatcher(engine, InMemoryEventLog()) if engine is not None else None
        )
        if engine is not None:
            engine.register(VENDOR_FLOW_ID, self._vendor_body)
            engine.register(PO_FLOW_ID, self._po_body)
            engine.register(RECEIVING_FLOW_ID, self._receiving_body)
            engine.register(WORKFORCE_FLOW_ID, self._workforce_body)
            engine.register(CALENDAR_FLOW_ID, self._calendar_body)
            # The commerce bodies live in their own module — this file is
            # already long enough that a reader looking for the vendor flow
            # should not have to scroll past the counterparty book to find it
            # — and they register here so `SyncService` stays the one place
            # that knows which flows exist. Deferred because they import this
            # module for its wire shapes.
            from . import sync_commerce, sync_dms, sync_office, sync_psa

            sync_commerce.register(self)
            sync_office.register(self)
            sync_dms.register(self)
            sync_psa.register(self)
            # Not a feed: it reads the book the feeds wrote. It registers here
            # with them because this service is the one place that knows which
            # flow bodies exist, and because the steward decision it produces
            # is applied a few hundred lines below, beside its sibling.
            identity_sweep.register(self)
            # Its sibling, and downstream of it in the only way that matters:
            # what the identity sweep has confirmed is what this one is allowed
            # to join across. Registered beside it for the same reason.
            disagreement_sweep.register(self)

    def _act(self, ctx: RunContext, action_id: str, step_id: str) -> PendingAction:
        """A sync write, committed by the Flow that is running.

        Nobody *decided* a feed delivery — a source reported and Cumulus
        recorded — so the committing Principal is the Flow itself, with no
        queue binding, and the Action it names carries the regime: an
        observation for a landing or an apply, a resolution for the identity
        choice the governed thresholds made. That is the whole difference
        between the two kinds of write in this module, and it is declared
        rather than guessed from what the step touched.
        """
        return self.gateway.action(
            action_id, flow_principal(ctx.record.flow_id),
            run=ctx.record, step_id=step_id,
        )

    # -- entry point --------------------------------------------------------

    def deliver(self, request: SyncRequest, actor: str = "") -> list[dict]:
        """Accept one push and report the fact each feed shape carries.

        This used to name the flow to run. It now names what happened — "two
        hundred vendor records arrived from `erp-vendors`" — and the Dispatcher
        answers with the response, which is why the outcomes read `flow_id` off
        the run that started rather than off a variable set here. A delivery
        this module has no Trigger for raises rather than quietly landing
        nowhere.
        """
        if self.engine is None or self.dispatcher is None:
            raise RuntimeError("Sync service has no run engine wired")
        outcomes: list[dict] = []
        plan: list[tuple[str, list[Any]]] = [
            (VENDOR_EVENT, request.vendors),
            (PO_EVENT, request.purchase_orders),
            (RECEIVING_EVENT, request.receiving_reports),
            # A single-shape feed like the three above it: one list of rows,
            # landed under `records`, no second resource to arrive with.
            (EMPLOYMENT_EVENT_EVENT, request.employment_events),
        ]
        if request.teams or request.employees:
            plan.append((WORKFORCE_EVENT, []))
        if request.calendars or request.calendar_events:
            plan.append((CALENDAR_EVENT, []))
        if request.accounts or request.contacts or request.opportunities:
            plan.append((CRM_EVENT, []))
        if request.customers or request.items or request.sales_orders:
            plan.append((SALES_EVENT, []))
        if request.mailboxes or request.mail_messages:
            plan.append((MAIL_EVENT, []))
        if request.folders or request.documents:
            plan.append((DOCUMENTS_EVENT, []))
        if (
            request.psa_clients or request.projects or request.project_phases
            or request.project_tasks or request.time_entries
        ):
            plan.append((PSA_EVENT, []))
        # The multi-shape feeds carry their records in named lists rather than
        # one `records` column, so they are exempt from the emptiness check
        # and build their own inputs below.
        multi_shape = (
            WORKFORCE_EVENT, CALENDAR_EVENT, CRM_EVENT, SALES_EVENT, MAIL_EVENT,
            DOCUMENTS_EVENT, PSA_EVENT,
        )
        for event, records in plan:
            if event not in multi_shape and not records:
                continue
            inputs = {
                "source": request.source,
                "watermark": request.watermark,
                "records": [r.model_dump() for r in records],
            }
            if event == WORKFORCE_EVENT:
                inputs = {
                    "source": request.source,
                    "watermark": request.watermark,
                    "teams": [t.model_dump() for t in request.teams],
                    "employees": [e.model_dump() for e in request.employees],
                }
            elif event == CALENDAR_EVENT:
                inputs = {
                    "source": request.source,
                    "watermark": request.watermark,
                    "calendars": [c.model_dump() for c in request.calendars],
                    "events": [
                        e.model_dump() for e in request.calendar_events
                    ],
                }
            elif event == CRM_EVENT:
                inputs = {
                    "source": request.source,
                    "watermark": request.watermark,
                    "accounts": [a.model_dump() for a in request.accounts],
                    "contacts": [c.model_dump() for c in request.contacts],
                    "opportunities": [
                        o.model_dump() for o in request.opportunities
                    ],
                }
            elif event == SALES_EVENT:
                inputs = {
                    "source": request.source,
                    "watermark": request.watermark,
                    "customers": [c.model_dump() for c in request.customers],
                    "items": [i.model_dump() for i in request.items],
                    "sales_orders": [
                        o.model_dump() for o in request.sales_orders
                    ],
                }
            elif event == MAIL_EVENT:
                inputs = {
                    "source": request.source,
                    "watermark": request.watermark,
                    "mailboxes": [m.model_dump() for m in request.mailboxes],
                    "messages": [
                        m.model_dump() for m in request.mail_messages
                    ],
                }
            elif event == DOCUMENTS_EVENT:
                inputs = {
                    "source": request.source,
                    "watermark": request.watermark,
                    "folders": [f.model_dump() for f in request.folders],
                    "documents": [d.model_dump() for d in request.documents],
                }
            elif event == PSA_EVENT:
                inputs = {
                    "source": request.source,
                    "watermark": request.watermark,
                    "clients": [c.model_dump() for c in request.psa_clients],
                    "projects": [p.model_dump() for p in request.projects],
                    "phases": [p.model_dump() for p in request.project_phases],
                    "tasks": [t.model_dump() for t in request.project_tasks],
                    "time_entries": [
                        t.model_dump() for t in request.time_entries
                    ],
                }
            run_id = None
            if request.idempotency_key:
                # A retried delivery re-enters the same run, which replays its
                # completed steps and changes nothing. Keyed on the fact, not
                # on the flow: the same fact redelivered is the same delivery,
                # whatever the Trigger table decides to do with it.
                digest = hashlib.sha256(
                    f"{event}:{request.source}:{request.idempotency_key}".encode()
                ).hexdigest()[:10]
                run_id = f"run_sync_{digest}"
                existing = self.engine.get(run_id)
                if existing is not None:
                    outcomes.append({
                        "flow_id": existing.flow_id, "run_id": run_id,
                        "status": existing.status, "replayed": True,
                        **(existing.outputs or {}),
                    })
                    continue
            record = self.dispatcher.emit(
                event, inputs, actor=actor or f"source:{request.source}",
                run_id=run_id,
            ).one()
            outcomes.append({
                "flow_id": record.flow_id, "run_id": record.id,
                "status": record.status, "replayed": False,
                **(record.outputs or {}),
            })
        return outcomes

    # -- vendors -----------------------------------------------------------

    def _vendor_body(self, ctx: RunContext, inputs: dict) -> dict:
        source = inputs["source"]
        watermark = inputs.get("watermark", "")
        records = [VendorRecord.model_validate(r) for r in inputs["records"]]

        ctx.event(
            "t1", VENDOR_EVENT,
            f"{len(records)} vendor record(s) from `{source}`"
            + (f" · watermark {watermark}" if watermark else ""),
            kind="trigger",
        )

        thresholds_raw = ctx.step(
            "x1", "read-resolution-policy", "transform",
            fn=lambda: read_resolution_thresholds(self.knowledge).model_dump(),
            summary=lambda out: (
                f"{out['source_ref']}: auto-link ≥ {out['auto_link_at']:.2f} · "
                f"steward review ≥ {out['review_from']:.2f}"
            ),
        )
        thresholds = ResolutionThresholds.model_validate(thresholds_raw)
        ctx.note_refs(thresholds.source_ref)

        landing = self._act(ctx, "land-source-records", "s1")
        landed = ctx.step(
            "s1", "land-records", "sync",
            fn=lambda: landing.commit(lambda w: self._land(
                source, "vendor", [r.model_dump() for r in records], w,
            )),
            writes=lambda _out: landing.writes,
            summary=lambda out: (
                f"{out['landed']} landed, {out['unchanged']} unchanged "
                f"(byte-identical redelivery) in `source-landing`"
            ),
        )

        resolutions = ctx.step(
            "z1", "resolve-records", "resolve",
            fn=lambda: [
                self._score_vendor(record, source, thresholds).model_dump()
                for record in records
            ],
            summary=lambda out: (
                f"{sum(1 for o in out if o['disposition'] == 'linked')} linked · "
                f"{sum(1 for o in out if o['disposition'] == 'new')} new · "
                f"{sum(1 for o in out if o['disposition'] == 'review')} to a "
                "steward"
            ),
            refs=[thresholds.source_ref],
        )

        apply = self._act(ctx, "resolve-identities", "a1")
        applied = ctx.step(
            "a1", "apply-resolutions", "action",
            fn=lambda: apply.commit(lambda w: self._apply_vendors(
                source, records,
                [ResolutionOutcome.model_validate(r) for r in resolutions],
                ctx.record.id, w,
            )),
            writes=lambda _out: apply.writes,
            summary=lambda out: (
                f"{out['linked']} linked to existing vendors · {out['created']} "
                f"minted · {out['deactivated']} deactivated · "
                f"{out['awaiting_steward']} Task(s) raised"
            ),
        )

        ctx.event("e1", "VendorsSynced", f"{len(records)} record(s) from {source}")
        return {
            "source": source, "watermark": watermark, "kind": "vendors",
            "records": len(records), **landed, **applied,
        }

    def _score_vendor(
        self, record: VendorRecord, source: str, thresholds
    ) -> ResolutionOutcome:
        return resolve(
            self.graph, "Vendor",
            [record.name, *record.aliases], thresholds,
            tax_id=record.tax_id, source=source,
            source_external_id=record.external_id,
        )

    def _apply_vendors(
        self,
        source: str,
        records: list[VendorRecord],
        outcomes: list[ResolutionOutcome],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        counts = {
            "linked": 0, "created": 0, "awaiting_steward": 0, "deactivated": 0,
        }
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        for record, outcome in zip(records, outcomes):
            source_record = graph.upsert(
                "SourceRecord", f"{source}:{record.external_id}",
                [
                    Claim(field="source", value=source, source=claim_source),
                    Claim(field="external_id", value=record.external_id, source=claim_source),
                    Claim(field="payload", value=record.model_dump_json(), source=claim_source),
                    Claim(field="landed_at", value=utcnow().isoformat(), source=claim_source),
                ],
                label=f"{source}:{record.external_id}",
                provenance=f"landed by {run_id}",
            )

            if outcome.disposition == "review":
                self._raise_match_task(record, outcome, source, source_record.id, run_id)
                counts["awaiting_steward"] += 1
                continue

            claims = [
                Claim(field="name", value=record.name, source=claim_source),
                Claim(field="slug", value=normalize_key(record.name),
                      source=claim_source),
                Claim(field="status",
                      value="inactive" if record.deleted else record.status,
                      source=claim_source),
            ]
            if record.tax_id:
                claims.append(Claim(field="tax_id", value=record.tax_id, source=claim_source))
            if record.payment_terms:
                claims.append(Claim(
                    field="payment_terms", value=record.payment_terms,
                    source=claim_source,
                ))
            if record.remit_country:
                claims.append(Claim(
                    field="remit_country", value=record.remit_country,
                    source=claim_source,
                ))
            for field, value in (("email", record.email), ("phone", record.phone)):
                if value:
                    claims.append(Claim(
                        field=field, value=value, source=claim_source
                    ))
            if record.aliases:
                claims.append(Claim(
                    field="aliases", value=record.aliases, source=claim_source
                ))

            if outcome.disposition == "linked":
                existing = self.graph.get(outcome.object_id)
                vendor = graph.upsert(
                    "Vendor", existing.natural_key if existing else record.name,
                    claims, label=existing.label if existing else record.name,
                    provenance=f"claims from {source} via {run_id}",
                )
                counts["linked"] += 1
            else:
                # A vendor nobody has confirmed yet is `onboarding`, not
                # `active`: the AP flow can still resolve against it, and a
                # steward can see that nobody has vouched for it. The claim
                # comes from the Flow, which ranks below the source's own
                # status claim — so an ERP that says `active` wins, and an ERP
                # that says nothing leaves it onboarding.
                vendor = graph.upsert(
                    "Vendor", record.name,
                    claims + [Claim(
                        field="onboarding_state", value="unconfirmed",
                        source=f"flow:{VENDOR_FLOW_ID}", confidence=0.5,
                    )],
                    label=record.name,
                    provenance=f"minted from {source} record {record.external_id} "
                               f"by {run_id}",
                )
                counts["created"] += 1

            if record.deleted:
                counts["deactivated"] += 1

            # The method on the link is how this identity came to be asserted,
            # which is what a steward reviewing it later needs to know. A minted
            # vendor was not *matched* at all — the record declared it — so
            # calling that a fuzzy match would overstate what happened.
            method = (
                "declared" if outcome.disposition == "new"
                else "exact" if outcome.method in (
                    "tax-id", "normalized-name", "source-link"
                )
                else "fuzzy"
            )
            graph.link(
                "identified_as", vendor.id, source_record.id,
                props={
                    "source": source, "external_id": record.external_id,
                    "score": outcome.score, "run_id": run_id,
                },
                confidence=1.0 if method == "declared" else (outcome.score or 1.0),
                method=method,
                provenance=outcome.reason,
            )
        return counts

    def _raise_match_task(
        self,
        record: VendorRecord,
        outcome: ResolutionOutcome,
        source: str,
        source_record_id: str,
        run_id: str,
    ) -> Task:
        return self.tasks.create(Task(
            id=f"task_match_{object_id('Vendor', source + record.external_id)[-10:]}",
            kind="match_review",
            title=f"Vendor match needs a steward: {record.name}",
            summary=(
                f"`{source}` delivered {record.external_id} ({record.name}). "
                f"{outcome.reason}. Nothing has been asserted: approve to link "
                "it to the top candidate, reject to mint a new vendor, or "
                "correct with the id of the right one."
            ),
            # Declared per feed, not per flow: the same vendor body serves
            # three contracts, and which desk answers an ambiguous row is a
            # property of the contract.
            queue=steward_queue_for(source),
            priority="normal",
            flow_id=VENDOR_FLOW_ID,
            run_id=run_id,
            case_ref=source_record_id,
            refs=[outcome.thresholds_ref] + [
                f"graph:Vendor/{c.object_id}" for c in outcome.candidates[:3]
            ],
            evidence=[
                TaskEvidence(label="Inbound name", value=record.name),
                TaskEvidence(label="Source", value=f"{source}:{record.external_id}"),
                TaskEvidence(label="Tax id", value=record.tax_id or "—"),
            ] + [
                TaskEvidence(
                    label=f"Candidate {i + 1} ({c.score:.2f})",
                    value=f"{c.label} — {c.reason}",
                )
                for i, c in enumerate(outcome.candidates[:3])
            ],
            proposal=TaskProposal(
                action="link-identity",
                target="vendor-master",
                summary=(
                    f"Link {source}:{record.external_id} to "
                    f"{outcome.candidates[0].label}"
                    if outcome.candidates else "Mint a new vendor"
                ),
            ),
            resumes="the link is asserted (or a new vendor minted) on decision",
            created_at_iso=utcnow().isoformat(),
        ))

    # -- purchase orders ----------------------------------------------------

    def _po_body(self, ctx: RunContext, inputs: dict) -> dict:
        source = inputs["source"]
        records = [PurchaseOrderRecord.model_validate(r) for r in inputs["records"]]
        ctx.event(
            "t1", PO_EVENT,
            f"{len(records)} purchase order(s) from `{source}`", kind="trigger",
        )
        landing = self._act(ctx, "land-source-records", "s1")
        landed = ctx.step(
            "s1", "land-records", "sync",
            fn=lambda: landing.commit(lambda w: self._land(
                source, "po", [r.model_dump() for r in records], w,
            )),
            writes=lambda _out: landing.writes,
            summary=lambda out: (
                f"{out['landed']} landed, {out['unchanged']} unchanged"
            ),
        )
        apply = self._act(ctx, "apply-source-records", "a1")
        applied = ctx.step(
            "a1", "apply-purchase-orders", "action",
            fn=lambda: apply.commit(lambda w: self._apply_pos(
                source, records, ctx.record.id, w,
            )),
            writes=lambda _out: apply.writes,
            summary=lambda out: (
                f"{out['orders']} order(s) and {out['lines']} line(s) written · "
                f"{out['vendor_linked']} linked to a vendor · "
                f"{out['vendor_unresolved']} unresolved"
            ),
        )
        ctx.event("e1", "PurchaseOrdersSynced", f"{len(records)} record(s)")
        return {
            "source": source, "watermark": inputs.get("watermark", ""),
            "kind": "purchase-orders", "records": len(records),
            **landed, **applied,
        }

    def _apply_pos(
        self,
        source: str,
        records: list[PurchaseOrderRecord],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        counts = {"orders": 0, "lines": 0, "vendor_linked": 0, "vendor_unresolved": 0}
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        thresholds = read_resolution_thresholds(self.knowledge)
        for record in records:
            order = graph.upsert(
                "PurchaseOrder", record.po_number,
                [
                    Claim(field="po_number", value=record.po_number, source=claim_source),
                    Claim(field="ordered_date", value=record.ordered_date, source=claim_source),
                    Claim(field="currency", value=record.currency, source=claim_source),
                    # Slugged, like every other status the graph holds. The
                    # ERP says `Pending Receipt`; the ontology's enum is what
                    # queries and evals are written against, and one feed
                    # landing its own spelling meant `status == "open"` was a
                    # question you could only answer per source.
                    Claim(field="status", value=_slug(record.status),
                          source=claim_source),
                    Claim(
                        field="total_usd",
                        value=round(sum(
                            line.quantity_ordered * line.unit_price_usd
                            for line in record.lines
                        ), 2),
                        source=claim_source,
                    ),
                ],
                label=record.po_number,
                provenance=f"landed by {run_id}",
            )
            counts["orders"] += 1

            outcome = resolve(
                self.graph, "Vendor", [record.vendor_name], thresholds,
                source=source, source_external_id=record.vendor_external_id,
            )
            if outcome.disposition == "linked":
                graph.link(
                    "ordered_from", order.id, outcome.object_id,
                    confidence=outcome.score, method="exact",
                    provenance=outcome.reason,
                )
                counts["vendor_linked"] += 1
            else:
                counts["vendor_unresolved"] += 1

            for ordinal, line in enumerate(record.lines, start=1):
                line_obj = graph.upsert(
                    "PurchaseOrderLine", f"{order.id}#{ordinal}",
                    [
                        Claim(field="sku", value=line.sku, source=claim_source),
                        Claim(field="description", value=line.description, source=claim_source),
                        Claim(field="quantity_ordered", value=line.quantity_ordered, source=claim_source),
                        Claim(field="unit_price_usd", value=line.unit_price_usd, source=claim_source),
                        Claim(
                            field="amount_usd",
                            value=round(line.quantity_ordered * line.unit_price_usd, 2),
                            source=claim_source,
                        ),
                        Claim(field="po_number", value=record.po_number, source=claim_source),
                    ],
                    label=f"{record.po_number} line {ordinal}",
                    provenance=f"landed by {run_id}",
                )
                graph.link(
                    "has_po_line", order.id, line_obj.id,
                    props={"ordinal": ordinal}, provenance=run_id,
                )
                counts["lines"] += 1
        return counts

    # -- receiving reports --------------------------------------------------

    def _receiving_body(self, ctx: RunContext, inputs: dict) -> dict:
        source = inputs["source"]
        records = [ReceivingRecord.model_validate(r) for r in inputs["records"]]
        ctx.event(
            "t1", RECEIVING_EVENT,
            f"{len(records)} receiving report(s) from `{source}`", kind="trigger",
        )
        landing = self._act(ctx, "land-source-records", "s1")
        landed = ctx.step(
            "s1", "land-records", "sync",
            fn=lambda: landing.commit(lambda w: self._land(
                source, "rr", [r.model_dump() for r in records], w,
            )),
            writes=lambda _out: landing.writes,
            summary=lambda out: (
                f"{out['landed']} landed, {out['unchanged']} unchanged"
            ),
        )
        apply = self._act(ctx, "apply-source-records", "a1")
        applied = ctx.step(
            "a1", "apply-receiving-reports", "action",
            fn=lambda: apply.commit(lambda w: self._apply_receipts(
                source, records, ctx.record.id, w,
            )),
            writes=lambda _out: apply.writes,
            summary=lambda out: (
                f"{out['reports']} report(s) and {out['lines']} line(s) written · "
                f"{out['po_linked']} booked against a PO · {out['po_missing']} "
                "with no PO in the graph yet"
            ),
        )
        ctx.event("e1", "ReceivingReportsSynced", f"{len(records)} record(s)")
        return {
            "source": source, "watermark": inputs.get("watermark", ""),
            "kind": "receiving-reports", "records": len(records),
            **landed, **applied,
        }

    # -- workforce ----------------------------------------------------------

    def _workforce_body(self, ctx: RunContext, inputs: dict) -> dict:
        """Land the org chart. A bind mapping, so nothing is reified.

        The HRIS row's key is an exact identifier — a work email, a team slug
        — so there is nothing for a steward to adjudicate and no landing
        record to keep. Claims go straight onto the entity carrying `hris:` as
        their authority, and the bind columns (`manager_email`, `team_slug`,
        `parent_slug`, `lead_email`) resolve to links in one pass once both
        ends exist. Batch feed: unresolved references escalate once, as an
        aggregate, never per row.
        """
        source = inputs["source"]
        teams = [TeamRecord.model_validate(t) for t in inputs.get("teams", [])]
        employees = [
            EmployeeRecord.model_validate(e) for e in inputs.get("employees", [])
        ]
        ctx.event(
            "t1", WORKFORCE_EVENT,
            f"{len(teams)} team(s) and {len(employees)} employee(s) from "
            f"`{source}`",
            kind="trigger",
        )

        team_act = self._act(ctx, "apply-source-records", "a1")
        team_out = ctx.step(
            "a1", "apply-teams", "action",
            fn=lambda: team_act.commit(
                lambda w: self._apply_teams(source, teams, ctx.record.id, w)
            ),
            writes=lambda _out: team_act.writes,
            summary=lambda out: f"{out['teams']} team(s) written",
        )
        people_act = self._act(ctx, "apply-source-records", "a2")
        people_out = ctx.step(
            "a2", "apply-employees", "action",
            fn=lambda: people_act.commit(lambda w: self._apply_employees(
                source, employees, ctx.record.id, w,
            )),
            writes=lambda _out: people_act.writes,
            summary=lambda out: f"{out['employees']} employee(s) written",
        )
        chart_act = self._act(ctx, "apply-source-records", "a3")
        chart_out = ctx.step(
            "a3", "wire-org-chart", "action",
            fn=lambda: chart_act.commit(
                lambda w: self._wire_org_chart(teams, employees, w)
            ),
            writes=lambda _out: chart_act.writes,
            summary=lambda out: (
                f"{out['member_of']} membership(s), {out['reports_to']} "
                f"reporting line(s), {out['subteam_of']} nesting(s), "
                f"{out['led_by']} lead(s)"
                + (
                    f" · {out['unresolved']} reference(s) unresolved"
                    if out["unresolved"]
                    else ""
                )
            ),
        )
        if chart_out["unresolved"]:
            # One Task for the delivery, not one per row: a batch feed that
            # suspended on every dangling manager email would never finish.
            ctx.event(
                "e2", "WorkforceReferencesUnresolved",
                f"{chart_out['unresolved']} reference(s) named nobody in the "
                "graph",
            )
        ctx.event(
            "e1", "WorkforceSynced",
            f"{len(teams)} team(s), {len(employees)} employee(s)",
        )
        return {
            "source": source, "watermark": inputs.get("watermark", ""),
            "kind": "workforce",
            "records": len(teams) + len(employees),
            "landed": len(teams) + len(employees),
            **team_out, **people_out, **chart_out,
        }

    def _apply_teams(
        self,
        source: str,
        records: list[TeamRecord],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        for record in records:
            claims = [
                Claim(field="slug", value=record.slug, source=claim_source),
                Claim(field="name", value=record.name, source=claim_source),
                Claim(field="mission", value=record.mission,
                      source=claim_source),
            ]
            if record.external_id:
                # The source's own id, kept so a worker row naming a
                # `departmentId` can find this team without the connector
                # spending a GET to re-read the name the slug came from.
                claims.append(Claim(field="external_id",
                                    value=record.external_id,
                                    source=claim_source))
            graph.upsert(
                "Team", record.slug, claims,
                label=record.name or record.slug,
                provenance=f"synced by {run_id}",
            )
        return {"teams": len(records)}

    def _apply_employees(
        self,
        source: str,
        records: list[EmployeeRecord],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        for record in records:
            claims = [
                Claim(field="email", value=record.email, source=claim_source),
                Claim(field="name", value=record.name, source=claim_source),
                Claim(field="title", value=record.title, source=claim_source),
                Claim(field="location", value=record.location,
                      source=claim_source),
                Claim(field="start_date", value=record.start_date,
                      source=claim_source),
            ]
            # Claimed only when the source said something. An empty claim is
            # not the same fact as a missing one: a feed that predates these
            # columns would otherwise assert that everyone it delivers has no
            # status, and `status == ""` would outrank the HRIS that knows.
            for field, value in (
                ("external_id", record.external_id),
                ("status", _slug(record.status)),
                ("employment_type", _slug(record.employment_type)),
            ):
                if value:
                    claims.append(
                        Claim(field=field, value=value, source=claim_source)
                    )
            graph.upsert(
                "Employee", record.email, claims,
                label=record.name or record.email,
                provenance=f"synced by {run_id}",
            )
        return {"employees": len(records)}

    def _wire_org_chart(
        self,
        teams: list[TeamRecord],
        employees: list[EmployeeRecord],
        writer: GraphWriter | None = None,
    ) -> dict:
        graph = writer if writer is not None else self.graph
        counts = {
            "member_of": 0, "reports_to": 0, "subteam_of": 0, "led_by": 0,
            "unresolved": 0,
        }

        def by_external(object_type: str) -> dict[str, Any]:
            """Everything of one type this source has ever landed, by its id.

            One scan rather than a lookup per reference — the org chart names
            the same handful of managers a hundred times, and `find` walks the
            store. Built from the graph and not from the batch, which is the
            point: the manager of the one person in a delta is almost never in
            that delta, and before these columns existed the only way to place
            them was for the connector to go back to the estate and ask.
            """
            return {
                str(o.props.get("external_id")): o
                for o in self.graph.find(object_type)
                if o.props.get("external_id")
            }

        people_by_id = by_external("Employee")
        teams_by_id = by_external("Team")

        def employee(email: str, external_id: str = ""):
            found = (
                self.graph.get(object_id("Employee", email)) if email else None
            )
            return found or people_by_id.get(external_id)

        def team(slug: str, external_id: str = ""):
            found = self.graph.get(object_id("Team", slug)) if slug else None
            return found or teams_by_id.get(external_id)

        for record in employees:
            person = employee(record.email)
            if person is None:
                counts["unresolved"] += 1
                continue
            if record.team_slug or record.department_external_id:
                unit = team(record.team_slug, record.department_external_id)
                if unit is None:
                    counts["unresolved"] += 1
                else:
                    graph.link("member_of", person.id, unit.id,
                               method="declared", provenance="hris team_slug")
                    counts["member_of"] += 1
            if record.manager_email or record.manager_external_id:
                manager = employee(
                    record.manager_email, record.manager_external_id
                )
                if manager is None:
                    counts["unresolved"] += 1
                else:
                    graph.link("reports_to", person.id, manager.id,
                               method="declared",
                               provenance="hris manager_email")
                    counts["reports_to"] += 1
        for record in teams:
            unit = team(record.slug)
            if unit is None:
                counts["unresolved"] += 1
                continue
            if record.parent_slug:
                parent = team(record.parent_slug)
                if parent is None:
                    counts["unresolved"] += 1
                else:
                    graph.link("subteam_of", unit.id, parent.id,
                               method="declared", provenance="hris parent_slug")
                    counts["subteam_of"] += 1
            if record.lead_email:
                lead = employee(record.lead_email)
                if lead is None:
                    counts["unresolved"] += 1
                else:
                    graph.link("led_by", unit.id, lead.id,
                               method="declared", provenance="hris lead_email")
                    counts["led_by"] += 1
        return counts

    # -- calendars ----------------------------------------------------------

    def _calendar_body(self, ctx: RunContext, inputs: dict) -> dict:
        """Mirror the provider's calendars. A bind mapping, like the HRIS.

        A calendar address and a provider event id are exact identifiers, so
        there is nothing for a steward to adjudicate and no landing record
        worth keeping. Attendance is the one place judgment could creep in —
        an attendee named by an address nobody in the workforce holds — and it
        does not: an external guest is simply not a person in this graph, and
        the run reports how many references went unresolved rather than
        minting an Employee for a client's CFO.
        """
        source = inputs["source"]
        calendars = [
            CalendarRecord.model_validate(c) for c in inputs.get("calendars", [])
        ]
        events = [
            CalendarEventRecord.model_validate(e) for e in inputs.get("events", [])
        ]
        ctx.event(
            "t1", CALENDAR_EVENT,
            f"{len(calendars)} calendar(s) and {len(events)} event(s) from "
            f"`{source}`",
            kind="trigger",
        )

        calendar_act = self._act(ctx, "apply-source-records", "a1")
        calendar_out = ctx.step(
            "a1", "apply-calendars", "action",
            fn=lambda: calendar_act.commit(lambda w: self._apply_calendars(
                source, calendars, ctx.record.id, w,
            )),
            writes=lambda _out: calendar_act.writes,
            summary=lambda out: (
                f"{out['calendars']} calendar(s) written, "
                f"{out['owners']} owner link(s)"
            ),
        )
        event_act = self._act(ctx, "apply-source-records", "a2")
        event_out = ctx.step(
            "a2", "apply-events", "action",
            fn=lambda: event_act.commit(lambda w: self._apply_events(
                source, events, ctx.record.id, w,
            )),
            writes=lambda _out: event_act.writes,
            summary=lambda out: f"{out['events']} event(s) written",
        )
        attendance_act = self._act(ctx, "apply-source-records", "a3")
        attendance_out = ctx.step(
            "a3", "wire-attendance", "action",
            fn=lambda: attendance_act.commit(
                lambda w: self._wire_attendance(events, w)
            ),
            writes=lambda _out: attendance_act.writes,
            summary=lambda out: (
                f"{out['attended_by']} attendance(s), "
                f"{out['organized_by']} organizer(s)"
                + (
                    f" · {out['unresolved']} guest(s) outside the workforce"
                    if out["unresolved"]
                    else ""
                )
            ),
        )
        ctx.event(
            "e1", "CalendarsSynced",
            f"{len(calendars)} calendar(s), {len(events)} event(s)",
        )
        return {
            "source": source, "watermark": inputs.get("watermark", ""),
            "kind": "calendars",
            "records": len(calendars) + len(events),
            "landed": len(calendars) + len(events),
            **calendar_out, **event_out, **attendance_out,
        }

    def _apply_calendars(
        self,
        source: str,
        records: list[CalendarRecord],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        owners = 0
        for record in records:
            calendar = graph.upsert(
                "Calendar", record.address,
                [
                    Claim(field="address", value=record.address,
                          source=claim_source),
                    Claim(field="name", value=record.name, source=claim_source),
                    Claim(field="provider", value=record.provider,
                          source=claim_source),
                    Claim(field="external_id", value=record.external_id,
                          source=claim_source),
                    Claim(field="timezone", value=record.timezone,
                          source=claim_source),
                    Claim(field="owner_email", value=record.owner_email,
                          source=claim_source),
                    Claim(field="synced_at", value=utcnow().isoformat(),
                          source=claim_source),
                ],
                label=record.name or record.address,
                provenance=f"synced by {run_id}",
            )
            owner = (
                self.graph.get(object_id("Employee", record.owner_email))
                if record.owner_email
                else None
            )
            if owner is not None:
                graph.link(
                    "has_calendar", owner.id, calendar.id,
                    method="declared", provenance="calendar owner_email",
                )
                owners += 1
        return {"calendars": len(records), "owners": owners}

    def _apply_events(
        self,
        source: str,
        records: list[CalendarEventRecord],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        written = 0
        orphaned = 0
        for record in records:
            calendar = self.graph.get(
                object_id("Calendar", record.calendar_address)
            )
            if calendar is None:
                # No calendar, no event: a part cannot exist without its
                # owner, so the honest outcome is to drop it and say so.
                orphaned += 1
                continue
            event = graph.upsert(
                "CalendarEvent", _event_key(record),
                [
                    Claim(field="title", value=record.title, source=claim_source),
                    Claim(field="kind", value=record.kind, source=claim_source),
                    Claim(field="starts_at", value=record.starts_at,
                          source=claim_source),
                    Claim(field="ends_at", value=record.ends_at,
                          source=claim_source),
                    Claim(field="all_day", value="true" if record.all_day
                          else "false", source=claim_source),
                    Claim(field="location", value=record.location,
                          source=claim_source),
                    Claim(field="status", value=_event_status(record),
                          source=claim_source),
                    Claim(field="external_id", value=record.external_id,
                          source=claim_source),
                    Claim(field="organizer_email", value=record.organizer_email,
                          source=claim_source),
                    Claim(field="agenda_ref", value=record.agenda_ref,
                          source=claim_source),
                    Claim(field="notes", value=record.notes,
                          source=claim_source),
                ],
                label=record.title,
                provenance=f"synced by {run_id}",
            )
            graph.link(
                "has_event", calendar.id, event.id,
                method="declared", provenance="calendar_address",
            )
            written += 1
        return {"events": written, "orphaned_events": orphaned}

    def _wire_attendance(
        self,
        records: list[CalendarEventRecord],
        writer: GraphWriter | None = None,
    ) -> dict:
        graph = writer if writer is not None else self.graph
        counts = {"attended_by": 0, "organized_by": 0, "unresolved": 0}
        for record in records:
            event = self.graph.get(
                object_id("CalendarEvent", _event_key(record))
            )
            if event is None:
                continue
            if record.organizer_email:
                organizer = self.graph.get(
                    object_id("Employee", record.organizer_email)
                )
                if organizer is None:
                    counts["unresolved"] += 1
                else:
                    graph.link(
                        "organized_by", event.id, organizer.id,
                        method="declared", provenance="calendar organizer",
                    )
                    counts["organized_by"] += 1
            optional = {e.lower() for e in record.optional_emails}
            for email in record.attendee_emails:
                person = self.graph.get(object_id("Employee", email))
                if person is None:
                    # A client sponsor is a real attendee and not our
                    # employee. Counted, never minted.
                    counts["unresolved"] += 1
                    continue
                graph.link(
                    "attended_by", event.id, person.id,
                    props={
                        "required": email.lower() not in optional,
                        # The provider reports a response per invitation; the
                        # mirror carries it on the link, where it belongs.
                        "response": "accepted",
                    },
                    method="declared", provenance="calendar attendee list",
                )
                counts["attended_by"] += 1
        return counts

    def _apply_receipts(
        self,
        source: str,
        records: list[ReceivingRecord],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        counts = {"reports": 0, "lines": 0, "po_linked": 0, "po_missing": 0}
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        for record in records:
            report = graph.upsert(
                "ReceivingReport", record.receipt_number,
                [
                    Claim(field="receipt_number", value=record.receipt_number, source=claim_source),
                    Claim(field="received_date", value=record.received_date, source=claim_source),
                    Claim(field="po_number", value=record.po_number, source=claim_source),
                    Claim(field="status", value=record.status, source=claim_source),
                ],
                label=record.receipt_number,
                provenance=f"landed by {run_id}",
            )
            counts["reports"] += 1

            if record.po_number:
                order = self.graph.get(object_id("PurchaseOrder", record.po_number))
                if order is not None:
                    graph.link(
                        "receives_against", report.id, order.id,
                        props={"po_number": record.po_number}, provenance=run_id,
                    )
                    counts["po_linked"] += 1
                else:
                    # Receiving can legitimately arrive before the PO feed does.
                    # The report lands anyway and the edge is made when the PO
                    # shows up — nothing is lost, and nothing is invented.
                    counts["po_missing"] += 1

            for ordinal, line in enumerate(record.lines, start=1):
                line_obj = graph.upsert(
                    "ReceivingReportLine", f"{report.id}#{ordinal}",
                    [
                        Claim(field="sku", value=line.sku, source=claim_source),
                        Claim(field="description", value=line.description, source=claim_source),
                        Claim(field="quantity_received", value=line.quantity_received, source=claim_source),
                        Claim(field="condition", value=line.condition, source=claim_source),
                        Claim(field="receipt_number", value=record.receipt_number, source=claim_source),
                    ],
                    label=f"{record.receipt_number} line {ordinal}",
                    provenance=f"landed by {run_id}",
                )
                graph.link(
                    "has_receiving_line", report.id, line_obj.id,
                    props={"ordinal": ordinal}, provenance=run_id,
                )
                counts["lines"] += 1
        return counts

    # -- landing ------------------------------------------------------------

    def _land(
        self,
        source: str,
        prefix: str,
        payloads: list[dict],
        writer: GraphWriter | None = None,
    ) -> dict:
        """Keep every row exactly as delivered, and notice re-deliveries.

        The `unchanged` count is echo suppression in its simplest honest form:
        a byte-identical row we already hold is recorded as seen and nothing
        downstream is disturbed.
        """
        landed = unchanged = 0
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        for payload in payloads:
            external = str(
                payload.get("external_id")
                or payload.get("po_number")
                or payload.get("receipt_number")
                or ""
            )
            key = f"{source}:{external}"
            digest = _payload_hash(payload)
            existing = self.graph.get(object_id("SourceRecord", key))
            if existing is not None and existing.props.get("payload_hash") == digest:
                unchanged += 1
                continue
            graph.upsert(
                "SourceRecord", key,
                [
                    Claim(field="source", value=source, source=claim_source),
                    Claim(field="external_id", value=external, source=claim_source),
                    Claim(field="payload_hash", value=digest, source=claim_source),
                    Claim(
                        field="payload",
                        value=json.dumps(payload, sort_keys=True, default=str),
                        source=claim_source,
                    ),
                    Claim(field="landed_at", value=utcnow().isoformat(), source=claim_source),
                    Claim(field="record_kind", value=prefix, source=claim_source),
                ],
                label=key,
            )
            landed += 1
        return {"landed": landed, "unchanged": unchanged}

    # -- the steward's decision --------------------------------------------

    def apply_match_decision(
        self, task: Task, principal: Principal | None = None
    ) -> dict | None:
        """Assert (or refuse) the link a steward just ruled on.

        This is the resolution stream of the Task queue becoming real: nothing
        was asserted while the Task was open, so the decision is the first
        write. `approved` links to the top candidate, `corrected` links to
        whichever id the steward names, `rejected` mints a new entity.

        It carries no run — a batch feed does not suspend for one row — so the
        Gateway is the only thing that records it happened, which is precisely
        why it goes through the Gateway rather than straight at the graph.

        **Which book it writes into is read off the landed row**, not off the
        Task: `record_kind` is the only thing that survives from the flow that
        landed it, and inferring a type from the payload's shape would be a
        guess that gets a company filed as a vendor the first time the two
        wire shapes look alike. (`MATCH_TARGETS` covers the three kinds that
        reify identities today; B5 owns the general form, which is the same
        table read out of the Mapping.)
        """
        if task.kind != "match_review" or task.decision is None:
            return None
        source_record = self.graph.get(task.case_ref)
        if source_record is None:
            return None
        target = MATCH_TARGETS.get(
            str(source_record.props.get("record_kind") or "vendor")
        )
        if target is None:
            return None
        payload = json.loads(str(source_record.props.get("payload") or "{}"))
        record = MatchedRecord.model_validate(payload) if payload else None
        if record is None:
            return None
        act = self.gateway.action(
            target.action_id,
            # The steward, when a live caller carried one. A seed or a test
            # that did not is committing on the Flow's declared authority, and
            # `decided_by` on the entry still names whose judgment it was.
            #
            # The queue comes off the Task, because the Task is where the
            # routing decision was written down: `steward_queue_for(source)`
            # answered it per *feed* when the row escalated, and re-deriving it
            # here from `record_kind` would let a rebound Mapping commit under
            # a desk the Task never sat in.
            principal or flow_principal(
                task.flow_id or target.flow_id, task.queue or target.queue
            ),
            step_id="steward-decision", task_id=task.id,
            decided_by=task.decision.decided_by, target=source_record.ref(),
        )
        return act.commit(lambda w: self._assert_steward_decision(
            task, record, source_record, target, w
        ))

    def _assert_steward_decision(
        self,
        task: Task,
        record: MatchedRecord,
        source_record,
        target: "MatchTarget",
        graph: GraphWriter,
    ) -> dict | None:
        verdict = task.decision.verdict
        target_id = str(task.decision.payload.get("object_id") or "")
        if verdict == "approved" and not target_id:
            prefix = f"graph:{target.object_type}/"
            target_id = next(
                (
                    ref.split("/", 1)[1] for ref in task.refs
                    if ref.startswith(prefix)
                ),
                "",
            )
        claim_source = f"human:{task.decision.decided_by}"
        # Only fields the type actually declares: a Person has no `aliases`,
        # because an address is not a spelling and a second name for one is
        # evidence of a second person rather than of a nickname.
        declared = {
            f.name for f in (ONTOLOGY.object_type(target.object_type).fields)
        }

        if verdict in ("approved", "corrected") and target_id:
            entity = self.graph.get(target_id)
            if entity is None:
                return None
            claims = [Claim(
                field="name",
                value=str(entity.props.get("name") or entity.label),
                source=claim_source,
            )]
            if "aliases" in declared and record.display:
                claims.append(Claim(
                    field="aliases", value=[record.display], source=claim_source
                ))
            graph.upsert(
                target.object_type, entity.natural_key, claims,
                label=entity.label,
            )
            link = graph.link(
                target.identity_link, entity.id, source_record.id,
                props={
                    "source": str(source_record.props.get("source") or ""),
                    "external_id": record.external_id,
                    "decided_by": task.decision.decided_by,
                },
                confidence=1.0, method="human",
                provenance=f"steward decision on {task.id}: {task.decision.note}",
            )
            log.info(
                "Steward linked a source record",
                extra={
                    "taskId": task.id, "objectId": entity.id,
                    "objectType": target.object_type,
                    "linkId": link.id, "actor": task.decision.decided_by,
                },
            )
            return {
                "action": "linked", "object_type": target.object_type,
                "object_id": entity.id, "vendor_id": entity.id,
                "link_id": link.id,
            }

        # Rejected: this is not any of the candidates, so it is its own entity.
        # Keyed the way its type declares — a name for the two counterparty
        # books, an address for a human, and the landed row's own key when the
        # source carried no address at all.
        key = target.key(record) or source_record.natural_key
        claims = [Claim(field="name", value=record.display, source=claim_source)]
        if "status" in declared and target.object_type == "Vendor":
            # An entity nobody has vouched for yet, in the one book whose
            # status vocabulary has a word for that.
            claims.append(Claim(
                field="status", value="onboarding", source=claim_source
            ))
        if "tax_id" in declared and record.tax_id:
            claims.append(Claim(
                field="tax_id", value=record.tax_id, source=claim_source
            ))
        if "email" in declared and record.email:
            claims.append(Claim(
                field="email", value=record.email.lower(), source=claim_source
            ))
        entity = graph.upsert(
            target.object_type, key, claims,
            label=record.display or key,
            provenance=f"minted by steward decision on {task.id}",
        )
        graph.link(
            target.identity_link, entity.id, source_record.id,
            props={"external_id": record.external_id,
                   "decided_by": task.decision.decided_by},
            confidence=1.0, method="human",
            provenance=(
                f"steward said this is a new {target.object_type.lower()} "
                f"({task.id})"
            ),
        )
        log.info(
            "Steward minted an entity",
            extra={
                "taskId": task.id, "objectId": entity.id,
                "objectType": target.object_type,
            },
        )
        return {
            "action": "created", "object_type": target.object_type,
            "object_id": entity.id, "vendor_id": entity.id,
        }

    # -- the cross-source sweep, and the steward's answer to one -----------

    def sweep_identities(self, actor: str = "", window: str = "") -> dict:
        """Close an identity window. The Dispatcher decides what responds.

        `deliver`'s shape, for `deliver`'s reason: the caller states a fact and
        the Trigger table says what starts. Nothing here names a flow, so the
        console button, the interval loop and a graded eval case all reach the
        sweep the same way, and the run's `caused_by` points at a logged Event
        rather than at whoever happened to call.
        """
        if self.engine is None or self.dispatcher is None:
            raise RuntimeError("Sync service has no run engine wired")
        record = self.dispatcher.emit(
            IDENTITY_WINDOW_EVENT,
            {"window": window or "the whole counterparty book"},
            actor=actor or f"flow:{IDENTITY_SWEEP_FLOW_ID}",
        ).one()
        return {
            "flow_id": record.flow_id, "run_id": record.id,
            "status": record.status, **(record.outputs or {}),
        }

    def sweep_disagreements(self, actor: str = "", window: str = "") -> dict:
        """Close a state window. The Dispatcher decides what responds.

        `sweep_identities`' shape, for `sweep_identities`' reason — the caller
        states a fact, the Trigger table says what starts — and deliberately a
        second entry point rather than a flag on the first. The two passes ask
        different questions, read different policies, raise Tasks to different
        desks and will run on different schedules: identity settles once a book
        stops churning, where a disagreement is a fact about today that has to
        be re-asked every day. Folding them into one call would tie the second
        one's cadence to the first one's.
        """
        if self.engine is None or self.dispatcher is None:
            raise RuntimeError("Sync service has no run engine wired")
        record = self.dispatcher.emit(
            STATE_WINDOW_EVENT,
            {"window": window or "every held order in the book"},
            actor=actor or f"flow:{DISAGREEMENT_SWEEP_FLOW_ID}",
        ).one()
        return {
            "flow_id": record.flow_id, "run_id": record.id,
            "status": record.status, **(record.outputs or {}),
        }

    def apply_merge_decision(
        self, task: Task, principal: Principal | None = None
    ) -> dict | None:
        """Confirm or withdraw a proposed merge. The sibling of the above.

        A sibling and not a branch of `apply_match_decision`, because the two
        are opposites in the one way that matters: a `match_review` decision is
        the **first** write on a record nothing asserted, and a `merge_review`
        decision is a write on an assertion that is already in the graph at
        `in_review`. Approving flips its status; rejecting withdraws it, which
        is a reversal rather than a delete, so the fact that the machine
        proposed it and a human said no stays readable.

        **Nothing is merged on either verdict.** A confirmed proposal is an
        identity assertion two objects now carry — which is what makes them one
        party to a traversal — and collapsing them into a single canonical row
        is a further decision, made in the workbench, with the `reversible_with`
        note this link is carrying for exactly that moment.
        """
        if task.kind != "merge_review" or task.decision is None:
            return None
        ref = parse_ref(task.case_ref)
        if ref.namespace != "link" or ref.type not in SCAN_BY_LINK:
            return None
        link = self.graph.store.get_link(ref.id)
        if link is None or not link.live:
            return None
        if str(link.props.get("status") or "in_review") != "in_review":
            # Already ruled on. A second decider learns that from the Task
            # store's own 409; what this refuses is re-deciding the assertion.
            return None
        verdict = task.decision.verdict
        # The desk, once, for both halves of the authorization: the queue the
        # committing principal stands in when nobody human is on the call, and
        # the queue the Gateway checks the caller against. Off the **Task**,
        # because that is where the routing decision was written down when the
        # proposal was raised — and because the two books have two desks. A
        # `vendor_same_as` pair is `vendor-stewardship`'s work and a
        # `company_same_as` pair is `data-stewardship`'s; the Action used to
        # pin the second, which refused the vendor steward at the write on a
        # Task their own queue was holding. The fallback answers the case with
        # no Task to ask — a seed, a test, a decision reconstructed.
        queue = task.queue or steward_queue_for_type(
            SCAN_BY_LINK[ref.type].from_type
        )
        act = self.gateway.action(
            "confirm-identity-link",
            principal or flow_principal(
                task.flow_id or IDENTITY_SWEEP_FLOW_ID, queue,
            ),
            step_id="steward-decision", task_id=task.id,
            decided_by=task.decision.decided_by, target=f"link:{ref.type}/{ref.id}",
            queue=queue,
        )
        return act.commit(
            lambda w: self._assert_merge_decision(task, link, verdict, w)
        )

    def _assert_merge_decision(
        self, task: Task, link: LinkRecord, verdict: str, graph: GraphWriter
    ) -> dict:
        decided_by = task.decision.decided_by
        if verdict == "rejected":
            graph.reverse_link(
                link.id, decided_by,
                f"steward rejected the proposed merge ({task.id}): "
                f"{task.decision.note or 'not the same party'}",
            )
            log.info(
                "Proposed merge withdrawn",
                extra={"taskId": task.id, "linkId": link.id, "actor": decided_by},
            )
            return {"action": "withdrawn", "link_id": link.id}
        # Approved or corrected. Neither `confidence` nor `method` moves: this
        # is a props write on a live edge, which `GraphService.link` merges
        # into the record it already has, and that is the right behaviour
        # rather than a limitation to work around. The confidence is how good
        # the proposal was before a human looked at it — the number a later
        # tuning pass needs — and the method is *how the match was made*, which
        # a decision does not retroactively change: a fuzzy name match a
        # steward confirmed is still a fuzzy name match. What the human adds is
        # recorded where it belongs, in `status`, `decided_by`, `decided_at`,
        # the `task_id`, and the Gateway's ledger entry naming who committed
        # under whose authority. (`machine_confidence` and `machine_method`
        # are copies of the record's own fields, carried in the props so a
        # reader looking only at the proposal's decision block can see what was
        # ratified without joining back to the edge header.)
        graph.link(
            link.type, link.from_id, link.to_id,
            props={
                "status": "confirmed",
                "decided_by": decided_by,
                "decided_at": utcnow().isoformat(),
                "task_id": task.id,
                "machine_confidence": link.confidence,
                "machine_method": link.method,
            },
        )
        log.info(
            "Proposed merge confirmed",
            extra={"taskId": task.id, "linkId": link.id, "actor": decided_by},
        )
        return {"action": "confirmed", "link_id": link.id}

    # -- the Sources lens ---------------------------------------------------

    def sources(self) -> list[SourceStatus]:
        """One row per (source, kind), assembled from the sync runs themselves.

        No separate sync-state store: the runs already record what was
        delivered, when, and how it went, and inventing a second place to keep
        that would guarantee the two disagree.
        """
        if self.engine is None:
            return []
        by_key: dict[tuple[str, str], SourceStatus] = {}
        for flow_id, kind in FEED_FLOWS.items():
            for run in sorted(
                self.engine.list(flow_id=flow_id), key=lambda r: r.started_at_iso
            ):
                source = str(run.inputs.get("source") or run.outputs.get("source") or "")
                if not source:
                    continue
                key = (source, kind)
                status = by_key.setdefault(key, SourceStatus(
                    source=source, kind=kind, flow_id=flow_id,
                ))
                status.deliveries += 1
                status.last_run_id = run.id
                status.last_run_at_iso = run.started_at_iso
                status.last_status = run.status
                status.watermark = str(
                    run.inputs.get("watermark") or status.watermark
                )
                outputs = run.outputs or {}
                status.records += int(outputs.get("records", 0) or 0)
                status.records_landed += int(outputs.get("landed", 0) or 0)
                status.unchanged = _accumulate(status.unchanged, outputs, "unchanged")
                status.linked = _accumulate(status.linked, outputs, "linked")
                status.created = _accumulate(status.created, outputs, "created")
                status.awaiting_steward += int(outputs.get("awaiting_steward", 0) or 0)
                if run.error:
                    status.last_error = run.error
        return sorted(by_key.values(), key=lambda s: (s.source, s.kind))


def _event_key(record: CalendarEventRecord) -> str:
    """A part's key embeds its owner's — the weak-entity rule, enforced by
    `GraphService.check_invariants`, and true to the provider besides: an
    event id is only unique within the calendar that holds it."""
    return f"{record.calendar_address}#{record.external_id}"


def _event_status(record: CalendarEventRecord) -> str:
    """A provider's cancellation flag as the declared `status` enum.

    `CalendarEvent.status` is `confirmed | tentative | cancelled`, and a source
    that reports a boolean `isCancelled` says which of those it means without
    spelling it. Translating that is a vocabulary decision — the same kind
    `sync_office._importance` makes — so it lives in the flow: the connector
    carries the flag as it found it and has no opinion about the enum.

    A feed that states `status` itself carries no flag and is taken at its
    word, which is what keeps the mock seed's `tentative` a real third value
    rather than something a boolean could never express.
    """
    if record.cancelled is None:
        return record.status
    return "cancelled" if record.cancelled else "confirmed"


def _authority(source: str) -> str:  # noqa: D401
    """Map a source name onto a precedence bucket from the ontology.

    Precedence is declared over authorities (`erp`, `procurement`, …), not over
    every individual feed name, so a new ERP instance inherits the ERP's
    standing without an ontology edit.
    """
    lowered = source.lower()
    if "erp" in lowered or "netsuite" in lowered or "sap" in lowered:
        return "erp"
    if "crm" in lowered or "salesforce" in lowered or "hubspot" in lowered:
        return "crm"
    if "procure" in lowered or "coupa" in lowered or "ariba" in lowered:
        return "procurement"
    if "wms" in lowered or "receiv" in lowered:
        return "procurement"
    if "hris" in lowered or "workday" in lowered or "bamboo" in lowered:
        return "hris"
    if (
        "calendar" in lowered
        or "m365" in lowered
        or "exchange" in lowered
        or "outlook" in lowered
        or "gcal" in lowered
    ):
        return "calendar"
    # Below the calendar branch on purpose. One provider serves both surfaces,
    # so `office-calendar` and `office-mail` share a prefix while only the mail
    # half is a hint — matching on the suite name first would quietly demote
    # the calendar, which is the one thing that actually knows what is on
    # somebody's Friday.
    if "office" in lowered or "mail" in lowered:
        return "office"
    # A bucket of its own rather than the fall-through, because the
    # fall-through is `flow` — and a document system's own filing tree is the
    # one thing about it that is *observed*. Letting it land as `flow` would
    # have recorded the customer's folder structure as something Cumulus
    # inferred, and a later human correction would have outranked it for the
    # wrong reason.
    if "dms" in lowered or "sharepoint" in lowered or "documentum" in lowered:
        return "dms"
    # NAMING HAZARD, stated once and only here: `psa` (the delivery system) and
    # `pas` (an insurance policy administration system) are one transposition
    # apart. They never co-occur in a namespace, and this branch matches the
    # first spelling only — a feed named `pas-policies` falls through to `flow`
    # rather than being quietly filed under Delivery's authority.
    if "psa" in lowered or "kantata" in lowered or "openair" in lowered:
        return "psa"
    return "flow"


def _slug(value: str) -> str:
    """A source's display vocabulary as the ontology's declared one.

    `Pending Receipt` is the ERP's word for `pending-receipt`; the enum on the
    type is what every query and every eval is written against. Mechanical, and
    deliberately *not* in the connector: which vocabulary a source's strings
    belong to is a mapping decision, and mapping decisions live in flows.

    It lives here rather than beside its first caller because it stopped being
    one flow's habit the moment a second flow needed the same guarantee — a
    sales order slugged and a purchase order not was a difference nobody chose.
    """
    return "-".join((value or "").strip().lower().split())


def build_sync_service(
    graph: GraphService,
    knowledge,
    tasks: TaskService,
    engine: RunEngine | None,
    gateway: ActionGateway | None = None,
    dispatcher: Dispatcher | None = None,
) -> SyncService:
    return SyncService(graph, knowledge, tasks, engine, gateway, dispatcher)
