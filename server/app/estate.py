"""The estate connector: pull the customer's systems, deliver through the door.

Everything else in `server/app/` waits to be pushed to. This is the one module
that reaches out — and it is deliberately the dumbest one in the system. It
holds a cursor per feed, asks each source what changed since, renames the
fields the wire shape names, and POSTs the result to
`POST /api/v1/sources/deliver` like any other external system would. It does
not resolve, score, mint, or rank. A connector that started deciding which
record *is* which entity would be a second, undeclared copy of the resolution
policy, sitting below the Mapping where nobody could read it — so the import
boundary is a test (`test_estate.py`), not a promise: this module may not
reach `graph.py`, `resolution.py`, or `gateway.py`.

**Delivery goes through the HTTP door, never in-process.** Calling
`SyncService.deliver` directly would work and would be a lie: the door is where
authority is checked and where `Dispatcher.emit` appends the Event *before* the
run exists. Going around it would produce a run with no fact behind it, which
is the exact inversion `dispatch.py` was written to fix. So the connector is a
client of its own server, and "a completed pull is an Event" is satisfied at
the chokepoint rather than asserted here.

**Three things the estate's shape decides for us.** Its list routes filter
`updatedAt` strictly greater than `?updatedSince=` and order ascending, so the
next cursor is simply the largest `updatedAt` observed — no overlap window, and
a re-pull of the same window is safe anyway (the wire is idempotent and the
upserts are keyed). They are unpaged, so a delta arrives whole and *chunking*
is ours to do: a delivery is capped and split on `updatedAt` boundaries, so a
cursor that advances per acknowledged chunk never skips the tail of a
timestamp.

**Two gaps the delta model leaves, accepted for this phase and stated rather
than papered over.** Deletes are invisible to `updatedSince`: a row the estate
removes is a row this connector never hears about again, so the graph keeps it.
And a row whose `updatedAt` is *null* is delivered by the first, cursorless
pull and then invisible to every delta after it — the live DMS has three such
folders, hand-made before the namespace was seeded — so renaming one of them
would never sync. Both are properties of a watermark cursor over a source that
does not journal its changes; closing either needs a change log or a periodic
full re-pull, and neither is engineered here.

**Scheduling is a sourced capability** (DOCTRINE.md: no continuously-warm
component of our own). The deployed form is a managed scheduler POSTing
`/api/v1/estate/pull`; the endpoint is the port. The interval thread below is
the *dev adapter* for that port — off unless `ESTATE_PULL_INTERVAL_SECONDS`
is set, and never on in tests.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Protocol
from urllib.parse import quote, urlencode

import httpx
from pydantic import BaseModel, Field

from .models import utcnow
from .connectors.sync import (
    AccountRecord,
    CalendarEventRecord,
    CalendarRecord,
    ContactRecord,
    CredentialRecord,
    CustomerRecord,
    DmsDocumentRecord,
    EmployeeRecord,
    EmploymentEventRecord,
    EquipmentRecord,
    FolderRecord,
    ItemRecord,
    JobRecord,
    MailboxRecord,
    MailMessageRecord,
    OpportunityRecord,
    ProjectPhaseRecord,
    ProjectRecord,
    ProjectTaskRecord,
    PsaClientRecord,
    PurchaseOrderLineRecord,
    PurchaseOrderRecord,
    ReceivingLineRecord,
    ReceivingRecord,
    SalesOrderLineRecord,
    SalesOrderRecord,
    SyncRequest,
    TeamRecord,
    TimeEntryRecord,
    VendorRecord,
)

log = logging.getLogger("cumulus.estate")

#: `https://{project}-{app}.web.app` — the app is a *host*, not a path segment.
DEFAULT_URL_TEMPLATE = "https://{project}-{app}.web.app"
#: Discovery lives on the estate's admin site, under the same template.
DISCOVERY_APP = "admin"
DISCOVERY_PATH = "/admin/namespaces"

HRIS_WORKFORCE = "hris-workforce"
CRM_BOOK = "crm-book"
ERP_SALES = "erp-sales"
ERP_VENDORS = "erp-vendors"
ERP_PURCHASE_ORDERS = "erp-purchase-orders"
ERP_RECEIVING = "erp-receiving"
OFFICE_CALENDAR = "office-calendar"
OFFICE_MAIL = "office-mail"
#: The plant floor: what is being built, who holds a dated permission to work
#: on it, and what the business is answerable for operating. Three feeds and
#: not one, for the reason `erp-receiving` states below its own row — they move
#: on different clocks and a shared cursor would let one failure hold back rows
#: that were already safe to keep.
ERP_JOBS = "erp-jobs"
HRIS_CREDENTIALS = "hris-credentials"
ERP_EQUIPMENT = "erp-equipment"

#: The suite behind the estate's Office app, named once. Not an inference: the
#: app serves Microsoft 365's own shapes — opaque `AAMkA…` message ids,
#: `toRecipients`, `showAs`, `Sent Items` — and which system a feed comes from
#: is precisely the kind of fact a FeedSpec is the right place to declare. The
#: mirror types carry it so a reader can see whose copy they are looking at.
OFFICE_PROVIDER = "microsoft-365"
HRIS_EMPLOYMENT_EVENTS = "hris-employment-events"
DMS_LIBRARY = "dms-library"
#: The delivery system. `psa`, and not `pas` — the estate ships an insurance
#: policy-administration app one transposition away, and the two never appear
#: in one namespace, which is exactly the condition under which a typo goes
#: unnoticed.
PSA_BOOK = "psa-book"

#: Records per delivery. The estate does not page, so this is the only bound on
#: how large one POST gets; a chunk is cut on an `updatedAt` boundary, never
#: mid-timestamp.
DEFAULT_CHUNK_SIZE = 500

#: This process's self-mark, stamped on every delivery the connector makes.
#: `CONNECTOR_DELIVER_URL` defaults to `public_base_url`, and on a shared dev
#: box that URL can belong to a *different* Cumulus process — which then
#: accepts the delivery, reports success, and leaves the initiating server
#: with an advanced cursor and no data. It happened twice in one afternoon
#: before this existed. The door refuses a mismatched mark when its world is
#: process-local (memory backend); under a shared store, any instance of the
#: same deployment is the same world and the mark is informational.
PROCESS_TOKEN = secrets.token_hex(8)
PROCESS_HEADER = "X-Cumulus-Connector-Process"


class EstateError(RuntimeError):
    """The estate, or the door, said no.

    Raised rather than returned for `gateway.Refused`'s reason: a caller that
    forgot to look must not read a failed pull as an empty delta. A feed that
    raises has advanced no cursor and recorded why in its `ConnectorState`.
    """


# ---------------------------------------------------------------------------
# Cursor state
# ---------------------------------------------------------------------------


class ConnectorState(BaseModel):
    """Where one feed's cursor stands, and how the last pull went.

    Machinery, not a Dataset (DOCTRINE.md: "machinery/operational state — flow
    run journals, the trigger index, queues — is not a Dataset"), so it lives
    in its own collection and stays out of `data_collections.COLLECTIONS`.

    It cannot be derived from the runs the way `SyncService.sources()` is,
    which is the whole reason it exists: a pull returning an empty delta starts
    no run, and a pull that *failed* has no run at all. The cursor and the
    failure both have to survive the case where nothing ran.
    """

    feed: str
    # Per resource, because they move independently — an employee change must
    # not re-pull the jobs catalogue.
    watermarks: dict[str, str] = Field(default_factory=dict)
    last_pull_at: str = ""
    last_outcome: str = ""  # delivered · unchanged · failed
    last_error: str = ""
    next_pull_at: str = ""


class ConnectorStateStore(Protocol):
    def get(self, feed: str) -> ConnectorState | None: ...
    def put(self, state: ConnectorState) -> ConnectorState: ...
    def list(self) -> list[ConnectorState]: ...


class InMemoryConnectorStateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, ConnectorState] = {}

    def get(self, feed: str) -> ConnectorState | None:
        with self._lock:
            found = self._items.get(feed)
        # A copy, so a caller holding a cursor cannot advance it by accident —
        # the Firestore store has no choice but to behave this way, and two
        # backends that disagree about aliasing is a bug that only shows up in
        # production.
        return found.model_copy(deep=True) if found is not None else None

    def put(self, state: ConnectorState) -> ConnectorState:
        with self._lock:
            self._items[state.feed] = state.model_copy(deep=True)
        return state

    def list(self) -> list[ConnectorState]:
        with self._lock:
            return [s.model_copy(deep=True) for s in self._items.values()]


class FirestoreConnectorStateStore:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed in dev mode

        self._col = firestore.Client(project=project).collection(collection)

    def get(self, feed: str) -> ConnectorState | None:
        snap = self._col.document(feed).get()
        if not snap.exists:
            return None
        try:
            return ConnectorState.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable connector state", extra={"feed": feed})
            return None

    def put(self, state: ConnectorState) -> ConnectorState:
        self._col.document(state.feed).set(state.model_dump(mode="json"))
        return state

    def list(self) -> list[ConnectorState]:
        out: list[ConnectorState] = []
        for doc in self._col.stream():
            try:
                out.append(ConnectorState.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping state row", extra={"docId": doc.id})
        return out


def build_connector_state_store(
    backend: str, project: str | None, collection: str
) -> ConnectorStateStore:
    return (
        FirestoreConnectorStateStore(project, collection)
        if backend == "firestore"
        else InMemoryConnectorStateStore()
    )


# ---------------------------------------------------------------------------
# The feed registry
# ---------------------------------------------------------------------------

class EstateAccess(Protocol):
    """What a `build` may ask the estate for beyond the rows it was handed.

    Called as `access(resource, id)` it resolves one referenced record to the
    row the estate holds for it, empty when there is no such row — the
    sanctioned dereference. `access.url(...)` builds an address *in* the estate
    without fetching anything, which is what a reference-custody feed needs: a
    `storage_ref` is the customer's own content URL, and only the connector
    knows the project and namespace it is spelled with. Both are mechanical.
    Neither reads a byte more than the mapping declares.
    """

    def __call__(self, resource: str, record_id: str) -> dict: ...

    def url(self, resource: str, record_id: str, suffix: str = "") -> str: ...


#: The historical name for the callable half, kept because it is what the
#: FeedSpec docstring and every existing build signature call it.
Deref = EstateAccess


@dataclass(frozen=True)
class FeedSpec:
    """One (app, resource-family) pulled together and delivered as one shape.

    `build` receives the rows of *this chunk*, keyed by resource, plus the
    dereference callable — and does mechanical field renames only. The second
    argument is the one sanctioned exception (risk decision 2): when a delta
    row references a record the pulled page does not contain, the connector may
    fetch that one record to read the Mapping's *declared natural key* off it
    (an HRIS `managerId` is an internal id; the key an EmployeeRecord binds on
    is the work email). Every such dereference is named in the feed's Mapping
    notes. Anything beyond reading a declared key belongs in the flow.
    """

    feed: str  # the SyncRequest.source deliveries arrive under
    app: str  # estate app id from /admin/namespaces: crm|erp|office|hris|dms|psa…
    resources: tuple[str, ...]  # list routes pulled together, in delivery order
    build: Callable[[dict[str, list[dict]], Deref], SyncRequest]


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def team_slug(name: str) -> str:
    """A department name as the Team natural key the wire shape declares.

    Deterministic, and one of the three derivations this module allows itself,
    all of them addressing rather than meaning: **a key** (this, and the
    `parent_slug` on the other end of the same org edge — both ends have to
    spell it the same way or the flow wires nothing), **a name composed from
    the parts a source hands separately** (`legalFirstName` + `legalLastName`
    where there is no `fullName`), and **a URL** (`_storage_ref`, and
    `_Access.url` under it, because only the connector knows the project and
    namespace an address is spelled with).

    What is *not* here is any decision about what a value means: no vocabulary
    mapped onto a declared enum, no score, no precedence, no minting. Those are
    the flow's, and `connectors.sync._event_status` and
    `connectors.office._importance` are where the two enums this estate
    implies actually get decided.
    """
    return _SLUG_STRIP.sub("-", (name or "").lower()).strip("-")


def _text(row: dict, field: str) -> str:
    """One estate column as a string. It sends JSON nulls generously."""
    value = row.get(field)
    return "" if value is None else str(value)


def _number(row: dict, field: str) -> float:
    """One estate column as a float, and 0.0 for anything that is not one.

    Not a judgment about the missing value — a wire shape declares a number
    and something has to be put there. What the *absence* means is a question
    for the flow, which can see the whole row.
    """
    try:
        return float(row.get(field))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _build_hris_workforce(
    rows: dict[str, list[dict]], deref: Deref
) -> SyncRequest:
    """HRIS departments/jobs/employees → the existing TeamRecord/EmployeeRecord.

    Renames, plus id→key lookups that read the pulled page first. Two of them
    still fall back to a GET when the page does not answer — `jobId` for a
    title, and `parentDepartmentId`/`headEmployeeId`, because no column on
    `TeamRecord` carries a department's parent or lead by id, so the key is the
    only thing that can travel.

    **The manager and department dereferences are gone**, and that is the point
    of the columns that replaced them. `managerId` and `departmentId` now ride
    on the row as themselves, so a delta of one promoted employee no longer
    costs a round trip to re-read a work email the flow can look up against a
    person it already holds. The page is still read first — a full pull answers
    both without leaving — and where it does not, the raw id is delivered and
    the flow resolves it. A connector that fetched a key it had already been
    given was doing the flow's job badly and its own job twice.

    Nothing here decides anything: a department becomes a Team because the
    Mapping says departments are Teams, and a worker whose email another row
    already holds is delivered as it arrived. The duplicate is real HRIS drift
    and resolving it is B5's job, not a connector's.
    """
    departments = {d["id"]: d for d in rows.get("departments", [])}
    jobs = {j["id"]: j for j in rows.get("jobs", [])}
    workers = {e["id"]: e for e in rows.get("employees", [])}

    def off_page(page: dict[str, dict], resource: str, record_id: str) -> dict:
        """The pulled page first, then one GET for what it does not hold.

        No cache of its own: `_deref` memoizes every fetch for the whole pull,
        and a second cache in front of it would only make two places able to
        disagree about what was already read. The page dicts stay page-only, so
        `on_page_*` below means what it says.
        """
        return page.get(record_id) or deref(resource, record_id)

    def slug_of(dept_id: str) -> str:
        return team_slug(
            _text(off_page(departments, "departments", dept_id), "name")
        )

    def email_of(worker_id: str) -> str:
        return _text(off_page(workers, "employees", worker_id), "workEmail")

    def on_page_slug(dept_id: str) -> str:
        """The department's slug, but only if the pull already carried it."""
        return team_slug(_text(departments.get(dept_id) or {}, "name"))

    def on_page_email(worker_id: str) -> str:
        """The worker's email, but only if the pull already carried them."""
        return _text(workers.get(worker_id) or {}, "workEmail")

    teams = [
        TeamRecord(
            external_id=_text(d, "id"),
            slug=team_slug(_text(d, "name")),
            name=_text(d, "name"),
            # A rename, and the plainest kind: the HRIS spells the accounting
            # code `costCenter` and the wire shape spells it `cost_center`.
            # It rides beside the id rather than replacing it because they are
            # two different strings about the same department, and the ERP's
            # job rows name a crew by the *code* — so a feed that dropped it
            # would leave the `crew_id → Team` binding nothing to resolve
            # against. Null for most departments, which `_text` turns into the
            # empty string the flow then declines to claim.
            cost_center=_text(d, "costCenter"),
            parent_slug=slug_of(_text(d, "parentDepartmentId")),
            lead_email=email_of(_text(d, "headEmployeeId")),
        )
        for d in rows.get("departments", [])
    ]

    people: list[EmployeeRecord] = []
    for e in rows.get("employees", []):
        email = _text(e, "workEmail").strip()
        if not email:
            # The natural key is the work email, so a row without one names no
            # entity. Skipped rather than landed under an empty key — which is
            # bookkeeping, not judgment: there is nothing to resolve.
            log.info("HRIS row has no work email", extra={"record": e.get("id")})
            continue
        people.append(EmployeeRecord(
            external_id=_text(e, "id"),
            email=email,
            name=_text(e, "fullName") or " ".join(filter(None, (
                _text(e, "legalFirstName"), _text(e, "legalLastName"),
            ))),
            title=_text(off_page(jobs, "jobs", _text(e, "jobId")), "title"),
            location=_text(e, "workLocation"),
            start_date=_text(e, "hireDate"),
            manager_email=on_page_email(_text(e, "managerId")),
            team_slug=on_page_slug(_text(e, "departmentId")),
            manager_external_id=_text(e, "managerId"),
            department_external_id=_text(e, "departmentId"),
            # `workerStatus`, not `status`: the estate spells it its own way
            # and this is the rename the wire shape exists for. A worker the
            # HRIS calls Terminated arrives saying so instead of arriving
            # indistinguishable from everyone still here.
            status=_text(e, "workerStatus"),
            employment_type=_text(e, "employmentType"),
        ))

    return SyncRequest(source=HRIS_WORKFORCE, teams=teams, employees=people)


def _build_crm_book(rows: dict[str, list[dict]], deref: Deref) -> SyncRequest:
    """CRM accounts/contacts/opportunities → the counterparty wire shapes.

    Renames only, and no dereference at all: every reference in this family
    (`accountId`) is an id the flow resolves against a row this same feed
    landed, so the connector never leaves the page it pulled. The account
    spellings arrive exactly as the CRM holds them — `Northwind Traders`,
    without the legal suffix its ERP record carries — because deciding those
    are one company is the flow's job and this module's whole discipline is
    not to have an opinion about it.
    """
    return SyncRequest(
        source=CRM_BOOK,
        accounts=[
            AccountRecord(
                external_id=_text(a, "id"),
                name=_text(a, "name"),
                industry=_text(a, "industry"),
                website=_text(a, "website"),
                phone=_text(a, "phone"),
            )
            for a in rows.get("accounts", [])
        ],
        contacts=[
            ContactRecord(
                external_id=_text(c, "id"),
                first_name=_text(c, "firstName"),
                last_name=_text(c, "lastName"),
                title=_text(c, "title"),
                email=_text(c, "email"),
                phone=_text(c, "phone"),
                account_external_id=_text(c, "accountId"),
            )
            for c in rows.get("contacts", [])
        ],
        opportunities=[
            OpportunityRecord(
                external_id=_text(o, "id"),
                name=_text(o, "name"),
                account_external_id=_text(o, "accountId"),
                stage=_text(o, "stage"),
                amount=_number(o, "amount"),
                close_date=_text(o, "closeDate"),
            )
            for o in rows.get("opportunities", [])
        ],
    )


def _build_erp_sales(rows: dict[str, list[dict]], deref: Deref) -> SyncRequest:
    """ERP customers/items/salesOrders → the sell-side wire shapes.

    No dereference either, and for a reason worth naming: an order line names
    its item by the ERP's *internal* id (`"itemId": "3000"`), which is not the
    SKU the ontology keys on (`SCN-4200`). The item rows travel with the
    orders precisely so the translation is a fact in the delivery rather than
    a round trip — `ItemRecord` carries both ids, and the flow reads the
    bridge off the row.
    """
    return SyncRequest(
        source=ERP_SALES,
        customers=[
            CustomerRecord(
                external_id=_text(c, "id"),
                name=_text(c, "companyName"),
                email=_text(c, "email"),
                phone=_text(c, "phone"),
                payment_terms=_text(c, "terms"),
            )
            for c in rows.get("customers", [])
        ],
        items=[
            ItemRecord(
                external_id=_text(i, "id"),
                # The estate's `itemId` is the SKU; its `id` is the record.
                sku=_text(i, "itemId"),
                display_name=_text(i, "displayName"),
                item_type=_text(i, "itemType"),
                base_price=_number(i, "basePrice"),
            )
            for i in rows.get("items", [])
        ],
        sales_orders=[
            SalesOrderRecord(
                external_id=_text(o, "id"),
                order_number=_text(o, "tranId"),
                customer_external_id=_text(o, "customerId"),
                ordered_date=_text(o, "tranDate"),
                status=_text(o, "status"),
                memo=_text(o, "memo"),
                total=_number(o, "total"),
                lines=[
                    SalesOrderLineRecord(
                        item_external_id=_text(line, "itemId"),
                        quantity=_number(line, "quantity"),
                        rate=_number(line, "rate"),
                        amount=_number(line, "amount"),
                    )
                    for line in (o.get("lines") or [])
                ],
            )
            for o in rows.get("salesOrders", [])
        ],
    )


def _build_erp_vendors(rows: dict[str, list[dict]], deref: Deref) -> SyncRequest:
    """ERP vendors → the existing `VendorRecord`, onto the existing flow.

    Renames and nothing else. The estate carries no tax id on a vendor, so
    the field stays empty rather than being filled from something adjacent —
    a fabricated hard identifier is worse than an absent one, because the
    resolver treats a tax-id match as decisive.

    `email` and `phone` are carried now and were not before. They are not
    commercial facts the AP flow reads; they are *identity evidence*, and the
    two the estate's vendor rows have always held. A vendor with no domain and
    no number is a vendor the cross-source sweep can only judge on its name,
    which is the one signal a trading name defeats.
    """
    return SyncRequest(
        source=ERP_VENDORS,
        vendors=[
            VendorRecord(
                external_id=_text(v, "id"),
                name=_text(v, "companyName"),
                payment_terms=_text(v, "terms"),
                email=_text(v, "email"),
                phone=_text(v, "phone"),
            )
            for v in rows.get("vendors", [])
        ],
    )


def _build_erp_purchase_orders(
    rows: dict[str, list[dict]], deref: Deref
) -> SyncRequest:
    """ERP purchaseOrders → the existing `PurchaseOrderRecord`.

    The one feed here that dereferences, because the wire shape it has to fill
    was declared against a system that sends names: a PO row carries only
    `vendorId`, and `PurchaseOrderRecord.vendor_name` is what the flow matches
    a Vendor on. Same for a line's internal `itemId`, where the shape wants a
    SKU and a description. Both are declared keys read off one record each, and
    both are named in the Mapping's notes — which is the rule this exception
    exists under. Neither is cached here: `_deref` memoizes for the whole pull,
    so a delta of forty orders against four vendors is four GETs and the same
    vendor reappearing in the next chunk is none.
    """
    orders: list[PurchaseOrderRecord] = []
    for record in rows.get("purchaseOrders", []):
        vendor_id = _text(record, "vendorId")
        lines: list[PurchaseOrderLineRecord] = []
        for line in record.get("lines") or []:
            item = deref("items", _text(line, "itemId"))
            lines.append(PurchaseOrderLineRecord(
                sku=_text(item, "itemId"),
                description=_text(item, "displayName"),
                quantity_ordered=_number(line, "quantity"),
                unit_price_usd=_number(line, "rate"),
            ))
        orders.append(PurchaseOrderRecord(
            external_id=_text(record, "id"),
            po_number=_text(record, "tranId"),
            vendor_external_id=vendor_id,
            vendor_name=_text(deref("vendors", vendor_id), "companyName"),
            ordered_date=_text(record, "tranDate"),
            status=_text(record, "status"),
            lines=lines,
        ))
    return SyncRequest(source=ERP_PURCHASE_ORDERS, purchase_orders=orders)


def _build_erp_receiving(
    rows: dict[str, list[dict]], deref: Deref
) -> SyncRequest:
    """ERP receivingReports → the existing `ReceivingRecord`. The third leg.

    Everything downstream of this function was already built and unreachable:
    `receiving-sync`, the wire shape, the `ReceivingReport`/`ReceivingReportLine`
    types and the matcher's `receives_against` traversal have all been in place
    since the AP flow landed, and nothing had ever delivered them — which made a
    three-way match structurally two-way in production. This feed is the whole
    fix. No flow, no wire field and no matcher rule changed to accommodate it.

    It dereferences for the reason the purchase-order feed does, and here it is
    load-bearing rather than cosmetic: `sync._apply_receipts` binds a report to
    its order by looking `object_id("PurchaseOrder", po_number)` up, a
    PurchaseOrder's natural key is the ERP's `tranId`, and a receipt row names
    its order by internal id. The estate does spell one from the other (`PO` +
    id) and that rule is deliberately not used — a key rebuilt from a naming
    convention breaks silently on the day the convention does, and the failure
    it produces is the quietest kind: a receipt that lands looking perfectly
    correct with no edge to the order it books against, and an invoice that
    matches on two legs and reports the third as missing. So the order row is
    read for the key it actually carries. Same declared-key lookup for a line's
    internal `itemId`, where the shape wants a SKU and a description. `_deref`
    memoizes both for the whole pull, so a split delivery — two receipts against
    one order, which this estate really produces — costs one GET and not two.

    **Two fields the ERP cannot source, left empty rather than defaulted.** A
    receipt carries no status: completeness is a property of the *order*, which
    the ERP restates to `Partially Received` or `Pending Billing` from the sum
    of everything received against it, so the wire shape's `complete` default
    would assert of every partial delivery exactly the thing that is untrue
    about it. And a line carries no `condition` — `good | damaged | short` is a
    judgment about a shipment, and a dock that only counts has not made one;
    deriving `short` from received-under-ordered here would be this connector
    inventing vocabulary, which is the flow's job and not its own.

    Two source fields go nowhere, which is the honest half of a rename: a line's
    `quantityOrdered` (the shape holds only what turned up — the ordered figure
    is the PO's, and the matcher reads it from that leg) and the receipt's
    `receivedBy`/`memo`, which the shape has no room for. Dropped rather than
    packed into a field that means something else. There is also no money on a
    receipt anywhere in this source, and none is invented: what it answers is
    what turned up, never what it was worth.
    """
    reports: list[ReceivingRecord] = []
    for record in rows.get("receivingReports", []):
        order = deref("purchaseOrders", _text(record, "purchaseOrderId"))
        lines: list[ReceivingLineRecord] = []
        for line in record.get("lines") or []:
            item = deref("items", _text(line, "itemId"))
            lines.append(ReceivingLineRecord(
                sku=_text(item, "itemId"),
                description=_text(item, "displayName"),
                quantity_received=_number(line, "quantityReceived"),
                condition="",
            ))
        reports.append(ReceivingRecord(
            external_id=_text(record, "id"),
            receipt_number=_text(record, "tranId"),
            po_number=_text(order, "tranId"),
            received_date=_text(record, "tranDate"),
            status="",
            lines=lines,
        ))
    return SyncRequest(source=ERP_RECEIVING, receiving_reports=reports)


def _mailbox_addresses(rows: dict[str, list[dict]], deref: Deref):
    """`mailboxId` → the mailbox address, the way both office feeds need it.

    The estate references a mailbox by its internal uuid while both office wire
    shapes key on the address, so this is the same declared-key dereference the
    purchase-order feed makes for `vendorId` — the pulled page first, one GET
    for anything it does not hold, and `_deref` memoizing that GET for the whole
    pull so six events across three mailboxes cost three. Shared by the two
    feeds because a mailbox is a mailbox: two copies of this lookup that drifted
    would put the calendar and the mail of one person under two different keys,
    which is the one mistake that would make the mirror useless.
    """
    mailboxes = {m["id"]: m for m in rows.get("mailboxes", [])}

    def address_of(mailbox_id: str) -> str:
        page = mailboxes.get(mailbox_id) if mailbox_id else None
        return _text(page or deref("mailboxes", mailbox_id), "address")

    return address_of


def _build_office_calendar(
    rows: dict[str, list[dict]], deref: Deref
) -> SyncRequest:
    """Office mailboxes/events → the existing Calendar wire shapes.

    A rename onto a contract that already existed, which is why there is no new
    flow here: `calendar-sync` was written against a provider mirror and the
    estate's Office app *is* one. Two things it does not carry, stated rather
    than filled in. It has no required/optional split on attendees, so
    `optional_emails` stays empty and every guest lands required — a claim about
    the addressing we do not have, left absent instead of guessed. And it has no
    event *kind*: `showAs` is availability (`Busy`, `Out of Office`), which is a
    different fact, so `kind` keeps the wire shape's default rather than having
    availability quietly promoted into it. Mapping one onto the other would be
    an invention, and inventions in a connector are invisible.
    """
    address_of = _mailbox_addresses(rows, deref)
    calendars = [
        CalendarRecord(
            external_id=_text(m, "id"),
            address=_text(m, "address"),
            name=_text(m, "displayName"),
            provider=OFFICE_PROVIDER,
            timezone=_text(m, "timeZone"),
            # The app models a mailbox as one person's, so its address is both
            # the calendar's own and its owner's work email. A shared mailbox
            # would need a directory this app does not serve — and would land
            # with an owner nobody can resolve, which the flow already counts.
            owner_email=_text(m, "address"),
        )
        for m in rows.get("mailboxes", [])
    ]
    events = [
        CalendarEventRecord(
            external_id=_text(e, "id"),
            calendar_address=address_of(_text(e, "mailboxId")),
            title=_text(e, "subject"),
            starts_at=_text(e, "start"),
            ends_at=_text(e, "end"),
            all_day=bool(e.get("isAllDay")),
            location=_text(e, "location"),
            # The flag as the source spells it, not the enum it implies:
            # `CalendarEvent.status` is `confirmed | tentative | cancelled` and
            # deciding which of those a boolean means is a vocabulary question
            # the flow answers (`sync._event_status`).
            cancelled=bool(e.get("isCancelled")),
            organizer_email=_text(e, "organizer"),
            attendee_emails=[str(a) for a in (e.get("attendees") or [])],
            notes=_text(e, "body"),
        )
        for e in rows.get("events", [])
    ]
    return SyncRequest(
        source=OFFICE_CALENDAR, calendars=calendars, calendar_events=events,
    )


def _build_office_mail(rows: dict[str, list[dict]], deref: Deref) -> SyncRequest:
    """Office mailboxes/messages → the mail mirror. `bodyPreview`, never `body`.

    The estate serves the whole message body on every row and this function
    reads the preview beside it and drops the body on the floor. That is the
    custody line held at the earliest possible point: a field that is never put
    on the wire cannot be landed by mistake, cannot be logged by an
    intermediary, and cannot be quietly relied on by a flow written later. The
    flow caps the preview again — see `connectors.office.PREVIEW_CHARS` —
    because
    this is a boundary worth defending twice.
    """
    address_of = _mailbox_addresses(rows, deref)
    return SyncRequest(
        source=OFFICE_MAIL,
        mailboxes=[
            MailboxRecord(
                external_id=_text(m, "id"),
                address=_text(m, "address"),
                display_name=_text(m, "displayName"),
                provider=OFFICE_PROVIDER,
                job_title=_text(m, "jobTitle"),
                department=_text(m, "department"),
                timezone=_text(m, "timeZone"),
            )
            for m in rows.get("mailboxes", [])
        ],
        mail_messages=[
            MailMessageRecord(
                external_id=_text(m, "id"),
                mailbox_address=address_of(_text(m, "mailboxId")),
                subject=_text(m, "subject"),
                folder=_text(m, "folder"),
                from_email=_text(m, "from"),
                from_name=_text(m, "fromName"),
                to_emails=[str(a) for a in (m.get("toRecipients") or [])],
                cc_emails=[str(a) for a in (m.get("ccRecipients") or [])],
                # `bodyPreview`, and deliberately not `body`.
                body_preview=_text(m, "bodyPreview"),
                importance=_text(m, "importance"),
                conversation_id=_text(m, "conversationId"),
                received_at=_text(m, "receivedDateTime"),
                sent_at=_text(m, "sentDateTime"),
            )
            for m in rows.get("messages", [])
        ],
    )


def _build_hris_employment_events(
    rows: dict[str, list[dict]], deref: Deref
) -> SyncRequest:
    """HRIS employmentEvents → the ledger shape, carried whole.

    The only build here that does not rename anything down to a chosen set of
    columns, because the row *is* the payload. A promotion carries what the
    job, department, manager and salary became and what each of them was; a
    connector that picked four of those seventeen columns would have decided
    which thirteen a later reader is allowed to ask about. Four are named on
    the shape for addressing — an id to land under, a person to reference, a
    type and a date to read without parsing — and the whole row travels beside
    them.
    """
    return SyncRequest(
        source=HRIS_EMPLOYMENT_EVENTS,
        employment_events=[
            EmploymentEventRecord(
                external_id=_text(row, "id"),
                employee_external_id=_text(row, "employeeId"),
                event_type=_text(row, "eventType"),
                effective_date=_text(row, "effectiveDate"),
                payload=dict(row),
            )
            for row in rows.get("employmentEvents", [])
        ],
    )


def _build_dms_library(
    rows: dict[str, list[dict]], deref: Deref
) -> SyncRequest:
    """DMS folders/documents → the filing tree and pointers into it.

    No dereference and, more to the point, **no content fetch**. The estate
    serves the bytes at `/documents/<id>/content` and this build deliberately
    only ever writes that address down: `storage_ref` is built from the ids the
    list route already gave us, pinned to the revision it said was current.
    That is the `reference` custody the Mapping declares, spelled out in the
    one place it could quietly have been broken.

    The parent pointer travels as the source's own id rather than as a path,
    because a path is something the tree decides and the tree is the flow's to
    walk.
    """
    return SyncRequest(
        source=DMS_LIBRARY,
        folders=[
            FolderRecord(
                external_id=_text(f, "id"),
                name=_text(f, "name"),
                parent_external_id=_text(f, "parentId"),
            )
            for f in rows.get("folders", [])
        ],
        documents=[
            DmsDocumentRecord(
                external_id=_text(d, "id"),
                name=_text(d, "name"),
                folder_external_id=_text(d, "folderId"),
                content_type=_text(d, "contentType"),
                size=int(_number(d, "size")),
                latest_version=int(_number(d, "latestVersion")),
                storage_ref=_storage_ref(deref, d),
            )
            for d in rows.get("documents", [])
        ],
    )


def _build_psa_book(rows: dict[str, list[dict]], deref: Deref) -> SyncRequest:
    """PSA clients/projects/phases/tasks/timeEntries → the delivery shapes.

    Renames, and no dereference: every reference in this family (`clientId`,
    `projectId`, `phaseId`, `taskId`) is an id the flow resolves against a row
    this same feed landed, so the connector never leaves the page it pulled.

    Two columns are read and two whole sets of columns are not, and both are
    decisions rather than omissions. A **task** arrives as three ids and
    nothing else — its name, assignee, status, due date and estimate are served
    on every row and dropped here — because no type holds a task and the row
    is only here to say which phase an hour belongs to. A **time entry**
    arrives without its `notes`, the same custody line `_build_office_mail`
    draws at `bodyPreview`: a field that never reaches the wire cannot be
    landed by a flow written later.

    `actualHours` is the one server-computed number the estate sends, and it
    travels as it arrived. The connector has no opinion about whether it agrees
    with the entries beside it; whether they can agree at all is a question
    about the delivery window, and the flow is where that is answered.
    """
    return SyncRequest(
        source=PSA_BOOK,
        psa_clients=[
            PsaClientRecord(
                external_id=_text(c, "id"),
                name=_text(c, "name"),
                contact_name=_text(c, "contactName"),
                contact_email=_text(c, "contactEmail"),
            )
            for c in rows.get("clients", [])
        ],
        projects=[
            ProjectRecord(
                external_id=_text(p, "id"),
                project_code=_text(p, "projectCode"),
                client_external_id=_text(p, "clientId"),
                name=_text(p, "name"),
                status=_text(p, "status"),
                project_manager=_text(p, "projectManager"),
                start_date=_text(p, "startDate"),
                target_end_date=_text(p, "targetEndDate"),
                budget_hours=_number(p, "budgetHours"),
                actual_hours=_number(p, "actualHours"),
            )
            for p in rows.get("projects", [])
        ],
        project_phases=[
            ProjectPhaseRecord(
                external_id=_text(p, "id"),
                project_external_id=_text(p, "projectId"),
                name=_text(p, "name"),
                sequence=int(_number(p, "sequence")),
                status=_text(p, "status"),
                target_date=_text(p, "targetDate"),
            )
            for p in rows.get("phases", [])
        ],
        project_tasks=[
            ProjectTaskRecord(
                external_id=_text(t, "id"),
                project_external_id=_text(t, "projectId"),
                phase_external_id=_text(t, "phaseId"),
            )
            for t in rows.get("tasks", [])
        ],
        time_entries=[
            TimeEntryRecord(
                external_id=_text(t, "id"),
                project_external_id=_text(t, "projectId"),
                task_external_id=_text(t, "taskId"),
                person=_text(t, "person"),
                work_date=_text(t, "workDate"),
                hours=_number(t, "hours"),
                # The estate defaults it to true and sends it as a real
                # boolean, so the flag is read rather than assumed — a
                # non-billable day is the fact a utilization question turns on.
                billable=bool(t.get("billable")),
            )
            for t in rows.get("timeEntries", [])
        ],
    )


#: The estate's ten per-department completion columns, and the slug each one
#: names — **a rename table, which is what this module is for.** The
#: precedents are `connectors.sync._event_status` and
#: `connectors.office._importance`: one estate's word for a thing, spelled the
#: way everything downstream spells it. Which column belongs to which
#: department is a fact about the ERP's schema, the same kind of fact
#: `_build_erp_sales` states when it says `itemId` is the SKU.
#:
#: **The pairing is declared for a person in the `## Departments` table of
#: `plant-scheduling-parameters`** — HRIS department name, ERP Done field,
#: Cumulus slug — and this is the machine's copy of its middle and right
#: columns. Two copies of one table can drift, so the governed one is the
#: authority and this comment is the pointer to it; the corpus is deliberately
#: not read here, because a connector that reached the knowledge layer would
#: be as wrong as one that reached the graph (see the module docstring).
#:
#: The slugs are the whole point of the third column: neither repo learns the
#: other's spelling, because both spell the department the same third way
#: (S3). What a set flag *means* to the department after it — that a missing
#: upstream stage blocks a share — stays the flow's.
ERP_DEPARTMENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("trussCutDone", "truss-cut"),
    ("trussFabDone", "truss-fab"),
    ("steelFabDone", "steel-fab"),
    ("trimFabDone", "trim-fab"),
    # `hardwarePackDone`, not `hardwareTrimPackDone`: the ERP's column name is
    # shorter than the department it belongs to, which is exactly the kind of
    # thing a rename table exists to absorb.
    ("hardwarePackDone", "hardware-trim-pack"),
    ("verticalsDone", "verticals"),
    ("componentsDone", "components"),
    ("laminatesDone", "laminates"),
    ("stackOutDone", "stack-out"),
    ("loadingDone", "loading"),
)

#: The one value the ERP's picklists use for a set flag. Named because it is
#: read in one place and delivered verbatim everywhere else: this connector
#: tests the flag to decide *which slug to list*, which is addressing, and it
#: never translates the word into a state.
ERP_FLAG_SET = "Yes"


def _build_erp_jobs(rows: dict[str, list[dict]], deref: Deref) -> SyncRequest:
    """ERP customers/jobs → the `JobRecord` wire shape. One join, no judgment.

    The join is the reason `customers` is pulled first and the reason this
    feed has two resources at all: an AXIS job names its customer by the ERP's
    internal `customerId`, and `JobRecord.customer_name` is what the flow
    matches a `Company` on (S4). The customer row is read for `companyName`
    off the pulled page where the delta carried it and with one GET where it
    did not — the same declared-key dereference `_build_erp_purchase_orders`
    makes for `vendorId`, memoized by `_deref` for the whole pull, so forty
    jobs against six customers costs six GETs and the same customer
    reappearing in the next chunk costs none.

    **The customer rows are joined and never delivered.** They travel in this
    feed's chunk and stop here: `SyncRequest.customers` is the *sell side's*
    list, and filling it would make `deliver` fire `SalesRecordsDelivered`
    beside `JobRecordsDelivered` — one delivery starting two flows, the second
    of them landing an ERP customer book under the name of a jobs feed. The
    counterparty book is `erp-sales`' to write; this feed only needs the name.

    Three renames worth naming, because each of them is the connector
    declining to decide something:

    - **`outToYard` and `doNotFab` arrive as the estate's own `"Yes"`/`"No"`
      strings.** The ERP has no boolean type and these are picklists; mapping
      them onto `release_state` / `hold_reason` is the flow's job against the
      `## Source vocabulary` table in `plant-scheduling-parameters`, the same
      way `connectors.sync._event_status` decides what a provider's
      cancellation flag means. A connector that shipped `released=True` would
      have made a governed decision invisible.
    - **`weekPosition` travels as the source's own word too**, for exactly the
      same reason: `Job.week_position` is a number, the ERP places a job in a
      window using its own vocabulary, and the table that turns one into the
      other is governed knowledge rather than a `float()` call in here.
    - **`upstream_done` lists the slugs whose column reads `"Yes"`.** Which
      column is which department is schema (above); what a set flag means to
      the department after it is the flow's.

    `site_address` is not carried: the shape has a field for it and v1 has no
    reader, and a column delivered for nobody is a column that gets relied on
    before anybody has decided what it means.
    """
    customers = {c["id"]: c for c in rows.get("customers", []) if c.get("id")}

    def customer_name(customer_id: str) -> str:
        """The pulled page first, then one GET for what it does not hold."""
        if not customer_id:
            return ""
        page = customers.get(customer_id)
        return _text(page or deref("customers", customer_id), "companyName")

    jobs: list[JobRecord] = []
    for row in rows.get("jobs", []):
        wings = " x ".join(filter(None, (
            _text(row, "wingsTotalWidthFt"), _text(row, "wingsTotalLengthFt"),
        )))
        jobs.append(JobRecord(
            external_id=_text(row, "id"),
            job_number=_text(row, "jobNumber"),
            name=_text(row, "jobName"),
            customer_name=customer_name(_text(row, "customerId")),
            builder_name=_text(row, "builder"),
            plant_site=_text(row, "plantSite"),
            status=_text(row, "status"),
            load_date=_text(row, "loadDate"),
            shipment_date=_text(row, "shipmentDate"),
            week_position=_text(row, "weekPosition"),
            out_to_yard=_text(row, "outToYard"),
            do_not_fab=_text(row, "doNotFab"),
            foreman=_text(row, "foreman"),
            crew_id=_text(row, "crewId"),
            shipment_type=_text(row, "shipmentType"),
            combined_with=_text(row, "combinedWith"),
            trailer_number=_text(row, "trailerNumber"),
            width=_number(row, "buildingWidthFt"),
            length=_number(row, "buildingLengthFt"),
            height=_number(row, "buildingHeightFt"),
            # The two wings measurements as one string, because the type holds
            # one and the source holds two. Composition of the kind
            # `team_slug`'s docstring allows — a value assembled from parts a
            # source hands separately — and deliberately not a sum: which of
            # them a labor formula adds up is a governed decision, and adding
            # them here would destroy the only evidence of what was measured.
            secondary_dimensions=wings,
            upstream_done=[
                slug for column, slug in ERP_DEPARTMENT_COLUMNS
                if _text(row, column) == ERP_FLAG_SET
            ],
        ))
    return SyncRequest(source=ERP_JOBS, jobs=jobs)


#: Which credential each dated column on a driver-qualification row is, and
#: the category the ontology declares for it. **Addressing again**: one HRIS
#: row holds four unrelated expiries, so the column a record came from is the
#: only thing that says what the record *is* — a fan-out, not a translation,
#: and the same kind of statement as "this column is the SKU". A thing that
#: expires and gets renewed behaves identically whichever department tracks
#: it, which is why all four land as `Credential` rows distinguished only by
#: category, and why there is no second record model for driver files.
HRIS_DRIVER_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("cdlExpirationDate", "cdl", "CDL", "licence"),
    ("medicalCardExpirationDate", "medical-card", "Medical card", "medical"),
    ("pspDate", "psp", "PSP", "clearance"),
    ("clearinghouseDate", "clearinghouse", "Clearinghouse", "clearance"),
)


def _build_hris_credentials(
    rows: dict[str, list[dict]], deref: Deref
) -> SyncRequest:
    """HRIS employees/trainingRecords/driverQualifications → `CredentialRecord`.

    The second feed with a join, and it exists for the reason the first one
    does: both credential resources reference a person by the HRIS's own
    `employeeId` and nothing else, while this contract binds a holder by work
    email — so the employee row is read for `workEmail`, off the pulled page
    where the delta carried it and with one memoized GET where it did not.
    `holder_external_id` travels beside the address, the way
    `EmployeeRecord.manager_external_id` does: a delta of one renewed
    certification should not have to buy a translation the graph already
    holds.

    A **bind** feed, and this is where that shows: nothing is reified because
    a work email is exact and nobody adjudicates it, so a holder the workforce
    does not contain is counted rather than minted.

    One row of training becomes one record; one driver-qualification row
    becomes up to four, one per date it carries (`HRIS_DRIVER_COLUMNS`), each
    under its own `external_id` suffix because `Credential`'s natural key is
    the source's record id and four permissions cannot share one. A column the
    source left null yields no record at all — an absent CDL date is the HRIS
    saying this driver has no CDL on file, not an expiry of nothing.

    Two columns are carried as the HRIS's own words and translated nowhere
    here: `category` on a training row (the flow slugs it against the declared
    vocabulary) and `status`, which is the HRIS's word for how the assignment
    stands — `Credential.status` is `valid | expiring | expired | withdrawn`
    derived from the dates against the governed notice stages, and letting an
    assignment state land in it would make the compliance sweep read its own
    input as its own output.
    """
    workers = {e["id"]: e for e in rows.get("employees", []) if e.get("id")}

    def work_email(employee_id: str) -> str:
        """The pulled page first, then one GET for what it does not hold."""
        if not employee_id:
            return ""
        page = workers.get(employee_id)
        return _text(page or deref("employees", employee_id), "workEmail")

    credentials: list[CredentialRecord] = []
    for row in rows.get("trainingRecords", []):
        employee_id = _text(row, "employeeId")
        credentials.append(CredentialRecord(
            external_id=_text(row, "id"),
            holder_email=work_email(employee_id),
            holder_external_id=employee_id,
            credential_type=_text(row, "courseName"),
            category=_text(row, "category"),
            assigned_date=_text(row, "assignedDate"),
            completion_date=_text(row, "completionDate"),
            expiration_date=_text(row, "expirationDate"),
            status=_text(row, "status"),
            result=_text(row, "result"),
            provider=_text(row, "provider"),
        ))
    for row in rows.get("driverQualifications", []):
        employee_id = _text(row, "employeeId")
        email = work_email(employee_id)
        record_id = _text(row, "id")
        for column, suffix, label, category in HRIS_DRIVER_COLUMNS:
            expires = _text(row, column)
            if not expires:
                continue
            credentials.append(CredentialRecord(
                external_id=f"{record_id}:{suffix}",
                holder_email=email,
                holder_external_id=employee_id,
                credential_type=label,
                category=category,
                # A driver file holds the date the permission lapses and
                # nothing else: no assignment, no completion, no result, no
                # provider. Left empty rather than filled from the expiry,
                # which would claim a renewal happened on the day the last one
                # ran out.
                expiration_date=expires,
            ))
    return SyncRequest(source=HRIS_CREDENTIALS, credentials=credentials)


def _build_erp_equipment(
    rows: dict[str, list[dict]], deref: Deref
) -> SyncRequest:
    """ERP assets → `EquipmentRecord`. Renames, and one absence carried whole.

    No dereference: an asset row names its site as a string and its vendor by
    an id this contract does not bind, so the connector never leaves the page
    it pulled.

    **A null `nextInspectionDueDate` is delivered as an absent date, not as a
    zero and not as a flag.** It is the source saying this unit carries no
    tracked inspection — the skid steers really are like this — and the flow
    derives `inspection_tracked` from it once, on the way in. Deciding here
    that "no due date" means "not tracked" would be the connector reading a
    meaning off a missing value, which is the one thing `_number`'s docstring
    says it will not do.

    `make`, `model` and `year` are declared on the wire and left empty,
    because this estate's asset rows do not carry them. Stated rather than
    filled from the description, which would put a guess in three fields that
    read as facts.
    """
    return SyncRequest(
        source=ERP_EQUIPMENT,
        equipment=[
            EquipmentRecord(
                external_id=_text(row, "id"),
                # The estate's `assetTag` is the tag painted on the unit; its
                # `id` is the record. Both travel, for `ItemRecord`'s reason.
                equipment_id=_text(row, "assetTag"),
                name=_text(row, "description"),
                category=_text(row, "assetType"),
                # A serial where the source has one, a plate where it does
                # not: one column on the shape, because what a trailer is
                # keyed by and what a truck is keyed by are the same question
                # asked of two kinds of unit.
                identifier=_text(row, "serialNumber") or _text(row, "plateNumber"),
                home_site=_text(row, "homeSite"),
                last_inspection_date=_text(row, "lastInspectionDate"),
                next_inspection_due_date=_text(row, "nextInspectionDueDate"),
            )
            for row in rows.get("assets", [])
        ],
    )

def _storage_ref(deref: Deref, row: dict) -> str:
    """The customer-resolvable address of one file's current revision.

    Version-pinned on purpose. `/content` without a version answers with
    whatever is latest *at read time*, so a reference recorded today would
    quietly change meaning when somebody uploads a revision — and a Document
    row that claims `latest_version: 2` while its pointer resolves to v4 is
    worse than either fact alone.
    """
    record_id = _text(row, "id")
    if not record_id:
        return ""
    version = int(_number(row, "latestVersion"))
    suffix = f"/content?version={version}" if version else "/content"
    return deref.url("documents", record_id, suffix)


#: The closed table of feeds, and the extension point every later app lands in
#: — same idiom as `dispatch.TRIGGERS` and `gateway.ACTIONS`. A sixth app is
#: one FeedSpec, one Mapping, and (if it needs one) one flow; the connector
#: does not change, and discovery decides which rows are live.
FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec(
        feed=HRIS_WORKFORCE,
        app="hris",
        # Order matters on the wire, not in the pull: the flow needs teams to
        # exist before it can wire an employee into one.
        resources=("departments", "jobs", "employees"),
        build=_build_hris_workforce,
    ),
    FeedSpec(
        feed=CRM_BOOK,
        app="crm",
        # Accounts first for the same reason teams precede employees: a
        # contact and a deal both name an account, and the flow can only bind
        # them once the counterparty they name has landed.
        resources=("accounts", "contacts", "opportunities"),
        build=_build_crm_book,
    ),
    FeedSpec(
        feed=ERP_SALES,
        app="erp",
        # Items before orders: a line names an item by an internal id, and the
        # item row is the only thing that translates it to a SKU.
        resources=("customers", "items", "salesOrders"),
        build=_build_erp_sales,
    ),
    FeedSpec(
        feed=ERP_VENDORS,
        app="erp",
        resources=("vendors",),
        build=_build_erp_vendors,
    ),
    FeedSpec(
        feed=ERP_PURCHASE_ORDERS,
        app="erp",
        resources=("purchaseOrders",),
        build=_build_erp_purchase_orders,
    ),
    FeedSpec(
        feed=ERP_RECEIVING,
        app="erp",
        # Below the orders and not above them, which is the one thing this
        # row's position decides: `_apply_receipts` can only draw the
        # `receives_against` edge if the PurchaseOrder is already in the graph,
        # and counts `po_missing` when it is not. Ordering the registry this way
        # makes the edge on the first pull rather than leaving it to whichever
        # later window happens to touch the order again.
        #
        # Still its own feed rather than a second resource on the orders, for
        # the reason the two office halves are two feeds: the dock moves on a
        # different clock from purchasing, they are different wire shapes on
        # different flows, and a shared cursor would let one order that failed
        # to deliver hold back a week of receipts that were already safe to
        # keep. That the estate bumps a PO's `updatedAt` when a receipt is
        # written against it is expected and needs nothing here — the orders
        # feed re-delivers that row, and the upsert is keyed.
        resources=("receivingReports",),
        build=_build_erp_receiving,
    ),
    FeedSpec(
        feed=OFFICE_CALENDAR,
        app="office",
        # Mailboxes first, for the reason teams precede employees: an event is
        # a part of a calendar and cannot land before the calendar does.
        resources=("mailboxes", "events"),
        build=_build_office_calendar,
    ),
    FeedSpec(
        feed=OFFICE_MAIL,
        app="office",
        # The same mailboxes, pulled again under their own cursor. Two feeds
        # over one resource rather than one feed over three, because the two
        # halves of an office suite are different contracts — different
        # authority, different custody, different flow — and a shared cursor
        # would make a quiet calendar hold back the mail.
        resources=("mailboxes", "messages"),
        build=_build_office_mail,
    ),
    FeedSpec(
        feed=HRIS_EMPLOYMENT_EVENTS,
        app="hris",
        # Its own feed and not a fourth resource on `hris-workforce`: the
        # ledger only grows, so its cursor moves on a different clock from the
        # worker list's, and a workforce pull that failed must not hold back
        # rows that were already safe to keep.
        resources=("employmentEvents",),
        build=_build_hris_employment_events,
    ),
    FeedSpec(
        feed=DMS_LIBRARY,
        app="dms",
        # Folders before documents, for the reason teams precede employees: a
        # file names the folder it is filed in, and the flow can only place it
        # once that folder has landed.
        resources=("folders", "documents"),
        build=_build_dms_library,
    ),
    FeedSpec(
        feed=ERP_JOBS,
        app="erp",
        # Customers before jobs, for the reason items precede orders: a job
        # names its customer by an internal id, and the customer row is the
        # only thing that translates it to the name this contract matches a
        # Company on (S4). Above `erp-equipment` because that is the order the
        # plant floor is read in — what is being built, then what builds it —
        # and because a reader looking for the production feeds should find
        # them in the order the flows depend on them.
        #
        # Its own feed rather than two more resources on `erp-sales`, for the
        # reason `erp-receiving` gives at length above: the shop floor moves on
        # a different clock from order-to-cash, the wire shapes and the flows
        # are different, and a shared cursor would let one failed order
        # delivery hold back a week of job changes that were already safe to
        # keep.
        resources=("customers", "jobs"),
        build=_build_erp_jobs,
    ),
    FeedSpec(
        feed=HRIS_CREDENTIALS,
        app="hris",
        # Employees first, for the same reason customers precede jobs: both
        # credential resources name a person by the HRIS's own id and nothing
        # else, and the employee row is what turns that into the work email
        # this contract binds a holder by.
        #
        # Its own feed and not a fourth resource on `hris-workforce`: a
        # certification expiring is not a worker changing, the two cursors move
        # independently, and a workforce pull that failed must not hold back a
        # renewal that had already landed.
        resources=("employees", "trainingRecords", "driverQualifications"),
        build=_build_hris_credentials,
    ),
    FeedSpec(
        feed=ERP_EQUIPMENT,
        app="erp",
        # One resource, no join, and nothing above it it depends on: an asset
        # row names its site as a string. Its own feed for the reason the two
        # above are: a fleet's inspection dates and a plant's job list have
        # nothing to say to each other, and sharing a cursor would only mean
        # sharing a failure.
        resources=("assets",),
        build=_build_erp_equipment,
    ),
    # **There is no schedule feed, and its absence is the design.** A reader
    # looking for a fourth row here is looking for the plant's daily
    # department schedule, which Cumulus is the system of record for
    # (`ScheduleDay`/`ScheduleEntry` are the only `authoritative=True` types
    # on the plant floor) because nothing in the estate holds one. A schedule
    # that lives in a spreadsheet cannot be asked what it deferred or who
    # signed it. So there is nothing to pull, and a feed declared here would
    # be a contract with a system that does not exist.
    FeedSpec(
        feed=PSA_BOOK,
        app="psa",
        # The longest family in the registry, and every step of it is a
        # reference the next one needs: a project names a client, a phase names
        # a project, a task names a phase, an hour names a task. One feed
        # rather than five, for the reason teams and employees are one.
        resources=("clients", "projects", "phases", "tasks", "timeEntries"),
        build=_build_psa_book,
    ),
)


# ---------------------------------------------------------------------------
# The connector
# ---------------------------------------------------------------------------


class EstateConnector:
    """One registered estate, and a cursor per feed.

    `fetch` and `deliver` are injectable so the tests can drive a recorded
    estate without a network while still posting through the real HTTP door —
    the only thing worth faking here is the wire.
    """

    def __init__(
        self,
        store: ConnectorStateStore,
        *,
        project: str = "",
        url_template: str = DEFAULT_URL_TEMPLATE,
        namespace: str = "",
        deliver_url: str = "",
        api_key: str = "",
        feeds: tuple[FeedSpec, ...] = FEEDS,
        fetch: Callable[[str], dict] | None = None,
        #: The bytes half of `fetch`, separate because it answers a different
        #: shape: a JSON row versus content plus the type the server declared
        #: for it. Injectable for the same reason — a recorded estate serves
        #: documents too.
        fetch_bytes: Callable[[str], tuple[bytes, str]] | None = None,
        deliver: Callable[[dict], dict] | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        interval_seconds: int = 0,
        timeout: float = 30.0,
    ) -> None:
        self.store = store
        self.project = project
        self.url_template = url_template or DEFAULT_URL_TEMPLATE
        self.namespace = namespace
        self.deliver_url = deliver_url
        self.api_key = api_key
        self.feeds = feeds
        self.chunk_size = max(1, chunk_size)
        self.interval_seconds = interval_seconds
        self.timeout = timeout
        self._fetch = fetch or self._http_fetch
        self._fetch_bytes = fetch_bytes or self._http_fetch_bytes
        self._deliver = deliver or self._http_deliver
        self._discovery: dict | None = None
        self._loop: threading.Thread | None = None

    # -- registration -------------------------------------------------------

    @property
    def registered(self) -> bool:
        """No estate configured is the default outside a dev box, and it reads
        as "nothing to do" rather than as a misconfiguration."""
        return bool(self.project)

    def base_url(self, app: str) -> str:
        return self.url_template.format(project=self.project, app=app)

    def discovery_url(self) -> str:
        return f"{self.base_url(DISCOVERY_APP)}{DISCOVERY_PATH}"

    def resource_url(self, app: str, resource: str, since: str = "") -> str:
        url = f"{self.base_url(app)}/api/{self.namespace}/v1/{resource}"
        return f"{url}?{urlencode({'updatedSince': since})}" if since else url

    def record_url(self, app: str, resource: str, record_id: str) -> str:
        return (
            f"{self.base_url(app)}/api/{self.namespace}/v1/{resource}/"
            f"{quote(record_id, safe='')}"
        )

    def discover(self, *, refresh: bool = False) -> dict:
        """The namespace the estate says it runs, and the apps it runs it with.

        Nothing hardcodes the app list: the FEEDS registry proposes and this
        disposes, which is what makes a sixth app a data change.
        """
        if not self.registered:
            raise EstateError("no estate registered (set ESTATE_PROJECT)")
        if self._discovery is not None and not refresh:
            return self._discovery
        payload = self._get(self.discovery_url())
        spaces = payload.get("namespaces") or []
        wanted = self.namespace or str(payload.get("default") or "")
        found = next((n for n in spaces if n.get("id") == wanted), None)
        if found is None:
            raise EstateError(
                f"namespace `{wanted or '(none)'}` is not one of "
                f"{[n.get('id') for n in spaces]}"
            )
        self.namespace = str(found.get("id") or wanted)
        self._discovery = found
        return found

    def active_feeds(self) -> list[FeedSpec]:
        apps = set(self.discover().get("apps") or [])
        return [f for f in self.feeds if f.app in apps]

    def spec(self, feed: str) -> FeedSpec | None:
        return next((f for f in self.feeds if f.feed == feed), None)

    def state(self, feed: str) -> ConnectorState:
        return self.store.get(feed) or ConnectorState(feed=feed)

    # -- pulling ------------------------------------------------------------

    def pull(self, feed: str) -> dict:
        """One feed, start to finish: delta, chunks, deliveries, cursor.

        All-or-nothing across the family. A feed whose employees route failed
        while its departments route answered has learned nothing it can safely
        write down — advancing the departments cursor would strand the two
        halves of an org edge on opposite sides of a window.
        """
        spec = self.spec(feed)
        if spec is None:
            raise EstateError(f"no such feed: {feed}")
        if not self.registered:
            raise EstateError("no estate registered (set ESTATE_PROJECT)")
        if spec.feed not in {f.feed for f in self.active_feeds()}:
            raise EstateError(
                f"feed `{feed}` needs app `{spec.app}`, which namespace "
                f"`{self.namespace}` does not run"
            )

        state = self.state(feed)
        started = utcnow()
        try:
            rows = {
                resource: self._list(
                    spec.app, resource, state.watermarks.get(resource, "")
                )
                for resource in spec.resources
            }
        except EstateError as exc:
            self._stamp(state, started, "failed", str(exc))
            raise

        chunks = _chunks(rows, self.chunk_size)
        deref = self._deref(spec.app)
        delivered: list[dict] = []
        try:
            for ordinal, chunk in enumerate(chunks):
                delivered.append(self._deliver_chunk(spec, chunk, ordinal, deref))
                # Per acknowledged chunk, never per pull: a door that refuses
                # chunk three leaves chunks one and two banked.
                for resource, items in chunk.items():
                    if items:
                        state.watermarks[resource] = max(
                            _updated_at(r) for r in items
                        )
                self.store.put(state)
        except EstateError as exc:
            self._stamp(state, started, "failed", str(exc))
            raise

        outcome = "delivered" if chunks else "unchanged"
        self._stamp(state, started, outcome, "")
        log.info(
            "Estate feed pulled",
            extra={
                "feed": feed, "app": spec.app, "namespace": self.namespace,
                "records": sum(len(v) for v in rows.values()),
                "chunks": len(chunks), "outcome": outcome,
            },
        )
        return {
            "feed": feed,
            "app": spec.app,
            "namespace": self.namespace,
            "outcome": outcome,
            "pulled": {resource: len(items) for resource, items in rows.items()},
            "chunks": delivered,
            "watermarks": dict(state.watermarks),
            "error": "",
        }

    def pull_all(self) -> list[dict]:
        """Every active feed. The scheduler's target, and the loop's body.

        One red feed does not stop the rest: a failure is that feed's recorded
        state, and a dashboard fact (C3), not an exception that strands four
        healthy sources behind a fifth.
        """
        if not self.registered:
            return []
        outcomes: list[dict] = []
        for spec in self.active_feeds():
            try:
                outcomes.append(self.pull(spec.feed))
            except EstateError as exc:
                log.warning(
                    "Estate feed failed",
                    extra={"feed": spec.feed, "error": str(exc)},
                )
                outcomes.append({
                    "feed": spec.feed, "app": spec.app,
                    "namespace": self.namespace, "outcome": "failed",
                    "pulled": {}, "chunks": [], "watermarks": {},
                    "error": str(exc),
                })
        return outcomes

    def registration(self) -> dict:
        """What is registered, what the estate says, and where each feed stands.

        Never raises: an estate that is down is a thing the console has to be
        able to render, and a screen that 500s is a worse answer than a screen
        that says the discovery call failed.
        """
        detail: dict | None = None
        error = ""
        if self.registered:
            try:
                detail = self.discover()
            except EstateError as exc:
                error = str(exc)
        apps = set((detail or {}).get("apps") or ())
        return {
            "registered": self.registered,
            "project": self.project,
            "namespace": self.namespace,
            "url_template": self.url_template,
            "discovery_url": self.discovery_url() if self.registered else "",
            # No `deliver_url`. This view is read at `require_reader`, and the
            # address of our own write door is machinery config rather than
            # anything about the customer's estate — the same reason the API
            # key is reported as a boolean below. The console's estate card
            # curates it away too; reporting one shape to the screen and a
            # wider one to the route it does not use would be the leak.
            #
            # Whether, not what — a credential never leaves this process.
            "authenticated": bool(self.api_key),
            "pull_interval_seconds": self.interval_seconds,
            "namespace_detail": detail,
            "discovery_error": error,
            "feeds": [
                {
                    "feed": spec.feed,
                    "app": spec.app,
                    "resources": list(spec.resources),
                    "base_url": self.base_url(spec.app) if self.registered else "",
                    "active": spec.app in apps,
                    "state": self.state(spec.feed).model_dump(mode="json"),
                }
                for spec in self.feeds
            ],
        }

    # -- the dev scheduling adapter ----------------------------------------

    def start_interval_loop(self) -> bool:
        """The dev adapter for the Scheduling port. Off unless asked.

        Deployed, a managed scheduler POSTs `/api/v1/estate/pull` and nothing
        here runs — Cumulus keeps no warm component of its own. This exists so
        a laptop can watch the estate drift without a second terminal.
        """
        if self.interval_seconds <= 0 or not self.registered:
            return False
        if self._loop is not None and self._loop.is_alive():
            return True
        self._loop = threading.Thread(
            target=self._tick, name="estate-pull", daemon=True
        )
        self._loop.start()
        log.info(
            "Estate pull loop started (dev adapter)",
            extra={"intervalSeconds": self.interval_seconds},
        )
        return True

    def _tick(self) -> None:
        while True:
            time.sleep(self.interval_seconds)
            try:
                self.pull_all()
            except Exception:
                # A loop that dies on a transient outage is worse than no loop:
                # it stops silently and the estate looks quiet.
                log.exception("Estate pull tick failed")

    # -- internals ----------------------------------------------------------

    def _stamp(
        self, state: ConnectorState, started, outcome: str, error: str
    ) -> None:
        state.last_pull_at = started.isoformat()
        state.last_outcome = outcome
        state.last_error = error
        # Only the dev loop knows when the next tick is; under a managed
        # scheduler the schedule lives in the scheduler, so this stays empty
        # rather than claiming a cadence nothing here controls.
        state.next_pull_at = (
            (started + timedelta(seconds=self.interval_seconds)).isoformat()
            if self.interval_seconds > 0 else ""
        )
        self.store.put(state)

    def _list(self, app: str, resource: str, since: str) -> list[dict]:
        payload = self._get(self.resource_url(app, resource, since))
        rows = payload.get(resource)
        if not isinstance(rows, list):
            raise EstateError(
                f"{app}/{resource}: response has no `{resource}` list"
            )
        return rows

    def _deref(self, app: str) -> EstateAccess:
        """One GET for a referenced record the pulled page does not hold.

        **The only cache in the connector.** Memoized here for the whole pull,
        not per chunk and not again inside a `build`: a hundred people reporting
        to one manager is one GET, so is that manager reappearing in the next
        chunk, and a second cache in front of this one would only be two places
        able to disagree about what has already been read. A miss is empty
        rather than fatal — a manager who left between the pull and the
        dereference is a dangling reference, and the flow already counts those
        on the aggregate instead of failing the batch. So is an empty id: a row
        that names nothing is answered without a round trip.
        """
        seen: dict[tuple[str, str], dict] = {}
        connector = self

        class _Access:
            def __call__(self, resource: str, record_id: str) -> dict:
                if not record_id:
                    return {}
                if (resource, record_id) in seen:
                    return seen[(resource, record_id)]
                try:
                    found = connector._get(
                        connector.record_url(app, resource, record_id)
                    )
                except EstateError as exc:
                    log.info(
                        "Dereference missed",
                        extra={"resource": resource, "id": record_id,
                               "error": str(exc)},
                    )
                    found = {}
                seen[(resource, record_id)] = (
                    found if isinstance(found, dict) else {}
                )
                return seen[(resource, record_id)]

            def url(
                self, resource: str, record_id: str, suffix: str = ""
            ) -> str:
                """An address in the estate, built and not called.

                The one thing a build cannot work out for itself: the project
                and namespace a URL is spelled with are the connector's
                machinery config, deliberately below the portability line, so a
                Mapping never names them and a `build` never sees them.
                """
                return connector.record_url(app, resource, record_id) + suffix

        return _Access()

    def _deliver_chunk(
        self, spec: FeedSpec, chunk: dict[str, list[dict]], ordinal: int,
        deref: Deref,
    ) -> dict:
        watermark = max(
            _updated_at(row) for items in chunk.values() for row in items
        )
        try:
            built = spec.build(chunk, deref)
        except Exception as exc:
            # A row the wire shape cannot hold is this feed's failure, recorded
            # against its state — not an unhandled 500 from a sync-now.
            raise EstateError(
                f"building `{spec.feed}` chunk {ordinal} failed: {exc}"
            ) from exc
        request = built.model_copy(update={
            "source": spec.feed,
            "watermark": watermark,
            # The same pull retried is the same delivery: `deliver` re-enters
            # the run this key already named and replays it.
            "idempotency_key": f"{spec.feed}:{watermark}:{ordinal}",
        })
        payload = request.model_dump(mode="json")
        try:
            response = self._deliver(payload)
        except Exception as exc:
            raise EstateError(
                f"delivery of `{spec.feed}` chunk {ordinal} failed: {exc}"
            ) from exc
        return {
            "idempotency_key": request.idempotency_key,
            "watermark": watermark,
            "records": sum(len(items) for items in chunk.values()),
            "runs": (response or {}).get("runs", []),
        }

    # -- content ------------------------------------------------------------

    def fetch_content(self, url: str) -> tuple[bytes, str]:
        """One file's bytes, from an address the customer's own system minted.

        The reference-custody feeds write `storage_ref` down and never call it;
        this is where a flow that genuinely needs the content — `ap-intake`,
        reading a supplier invoice — calls it, and it is here rather than in
        the flow because reaching outward is this module's job and nothing
        else's.

        The address is **checked against the estate's own hosts before it is
        called**, and that is not belt-and-braces. A `storage_ref` is a string
        that arrived from an external system and was stored in the graph; a
        flow that GETs whatever it finds there has handed the customer's
        document system the ability to point this service's credentials at any
        address it likes. The check is what makes that a refusal instead.
        """
        if not self.registered:
            raise EstateError("no estate registered (set ESTATE_PROJECT)")
        if not self._addresses_this_estate(url):
            raise EstateError(
                f"refusing to fetch `{url[:120]}`: not an address in the "
                f"`{self.project}` estate"
            )
        return self._fetch_bytes(url)

    def _addresses_this_estate(self, url: str) -> bool:
        """Is this URL one of the registered estate's app hosts?

        Built from the same template every other address in this module is,
        with the app left open: an estate is a set of sibling hosts and a
        reference may name any of them, but nothing outside the set.
        """
        prefix, _, suffix = self.url_template.partition("{app}")
        prefix = prefix.format(project=self.project)
        if not url.startswith(prefix):
            return False
        rest = url[len(prefix):]
        host, _, _ = rest.partition("/")
        return host.endswith(suffix) and len(host) > len(suffix)

    def _http_fetch_bytes(self, url: str) -> tuple[bytes, str]:
        response = httpx.get(url, timeout=self.timeout, follow_redirects=False)
        if response.status_code >= 400:
            raise EstateError(f"GET {url} → {response.status_code}")
        return response.content, response.headers.get("content-type", "")

    def _get(self, url: str) -> dict:
        try:
            payload = self._fetch(url)
        except EstateError:
            raise
        except Exception as exc:
            raise EstateError(f"GET {url} failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise EstateError(f"GET {url}: expected a JSON object")
        return payload

    def _http_fetch(self, url: str) -> dict:
        response = httpx.get(url, timeout=self.timeout)
        if response.status_code >= 400:
            raise EstateError(f"GET {url} → {response.status_code}")
        return response.json()

    def _http_deliver(self, payload: dict) -> dict:
        """The HTTP door, called the way any external feed would call it."""
        base = (self.deliver_url or "").rstrip("/")
        if not base:
            raise EstateError("no deliver URL configured")
        url = f"{base}/api/v1/sources/deliver"
        headers = {PROCESS_HEADER: PROCESS_TOKEN}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        response = httpx.post(
            url, json=payload, headers=headers, timeout=self.timeout
        )
        if response.status_code != 202:
            raise EstateError(
                f"POST {url} → {response.status_code}: {response.text[:200]}"
            )
        return response.json()


def _updated_at(row: dict) -> str:
    value = row.get("updatedAt")
    return "" if value is None else str(value)


def _chunks(
    rows: dict[str, list[dict]], size: int
) -> list[dict[str, list[dict]]]:
    """Split a delta into deliveries, cutting only where `updatedAt` changes.

    The rows are merged across resources and re-sorted ascending first, because
    the cut has to be a point in *time*: every row before it, in every
    resource, is delivered before every row after it, so a cursor advanced to
    the boundary skips nothing. Sorting also frees the connector from the
    estate's default ordering, which is by name until `?updatedSince=` is
    passed and by `updatedAt` after — a difference that would otherwise make
    the first pull behave unlike every pull after it.

    Rows sharing an `updatedAt` are never split, or the second one would fall
    behind a strictly-greater-than cursor and never be seen again.
    """
    merged = sorted(
        (
            (_updated_at(row), resource, index, row)
            for resource, items in rows.items()
            for index, row in enumerate(items)
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    out: list[dict[str, list[dict]]] = []
    current: dict[str, list[dict]] = {}
    held = 0
    previous = None
    for stamp, resource, _index, row in merged:
        if held >= size and stamp != previous:
            out.append(current)
            current, held = {}, 0
        current.setdefault(resource, []).append(row)
        held += 1
        previous = stamp
    if current:
        out.append(current)
    return out


def build_estate_connector(
    backend: str,
    project: str | None,
    collection: str,
    *,
    estate_project: str,
    url_template: str,
    namespace: str,
    deliver_url: str,
    api_key: str,
    interval_seconds: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> EstateConnector:
    return EstateConnector(
        build_connector_state_store(backend, project, collection),
        project=estate_project,
        url_template=url_template,
        namespace=namespace,
        deliver_url=deliver_url,
        api_key=api_key,
        interval_seconds=interval_seconds,
        chunk_size=chunk_size,
    )
