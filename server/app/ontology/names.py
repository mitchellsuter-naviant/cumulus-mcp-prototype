"""Type names as constants, so a name the TBox does not declare cannot be said.

A write already fails loudly on an undeclared type — `GraphService` refuses it
with `unknown-object-type`. A **read** does not: `graph.find("Vendors")` and
`graph.get(object_id("Employe", email))` return nothing at all, indistinguishable
from a deployment where the feed has not landed yet. That silence is the bug
this module exists to remove: every constant here is checked against the TBox at
import, so a rename in `tbox.py` that a caller did not follow is an ImportError
at startup rather than an empty screen a week later.

Deliberately not a rewrite of every literal in the server. Two kinds of literal
are worth spending a name on:

- a **lookup key** — the argument to `find`, `get`, `object_id`, `neighbors` —
  because a typo there is silent;
- a **declaration** — a Mapping's `resolves_to`, a Collection's `object_types` —
  because those are read as data and nothing would ever try the name.

Declarations are cheaper to protect in bulk, so the registries validate their
own tuples against the ontology (`validate_mappings`, `validate_collections`,
`validate_specs`) rather than importing thirty constants each. Everywhere else
the string stays a string: a `Region.field` path, a claim's field name, and a
source's own vocabulary are not ontology type names and would be worse for
pretending to be.
"""

from __future__ import annotations

from .tbox import ONTOLOGY, Ontology


def require_type(name: str, ontology: Ontology = ONTOLOGY) -> str:
    """The name, if the TBox declares it. Raises at import time if it does not."""
    if ontology.object_type(name) is None:
        raise ValueError(
            f"`{name}` is not an Object type in the ontology. A read keyed on "
            "it would match nothing and report an empty graph rather than a "
            "mistake"
        )
    return name


def require_link(name: str, ontology: Ontology = ONTOLOGY) -> str:
    """The link name, if the TBox declares it. Same reasoning as `require_type`.

    A traversal is the read that hides a typo best: `neighbors(id, "member_off")`
    is an empty list, which is also what a person on no team looks like.
    """
    if ontology.link_type(name) is None:
        raise ValueError(
            f"`{name}` is not a Link type in the ontology. A traversal over it "
            "would return nothing and read as an entity with no edges"
        )
    return name


# -- object types -----------------------------------------------------------

EMPLOYEE = require_type("Employee")
TEAM = require_type("Team")
CAPABILITY = require_type("Capability")
CALENDAR = require_type("Calendar")
CALENDAR_EVENT = require_type("CalendarEvent")
EXPENSE_REQUEST = require_type("ExpenseRequest")
EXPENSE_LINE = require_type("ExpenseLine")
DOCUMENT = require_type("Document")
FOLDER = require_type("Folder")
VENDOR = require_type("Vendor")
COMPANY = require_type("Company")
PERSON = require_type("Person")
SOURCE_RECORD = require_type("SourceRecord")
INVOICE = require_type("Invoice")
INVOICE_LINE = require_type("InvoiceLine")
PURCHASE_ORDER = require_type("PurchaseOrder")
PURCHASE_ORDER_LINE = require_type("PurchaseOrderLine")
RECEIVING_REPORT = require_type("ReceivingReport")
RECEIVING_REPORT_LINE = require_type("ReceivingReportLine")
MATCH_SET = require_type("MatchSet")
OPPORTUNITY = require_type("Opportunity")
SALES_ORDER = require_type("SalesOrder")
SALES_ORDER_LINE = require_type("SalesOrderLine")
ITEM = require_type("Item")
MAILBOX = require_type("Mailbox")
MAIL_MESSAGE = require_type("MailMessage")
PROJECT = require_type("Project")
PROJECT_PHASE = require_type("ProjectPhase")
JOB = require_type("Job")
JOB_TASK = require_type("JobTask")
SCHEDULE_DAY = require_type("ScheduleDay")
SCHEDULE_ENTRY = require_type("ScheduleEntry")
CREDENTIAL = require_type("Credential")
EQUIPMENT = require_type("Equipment")
INSPECTION = require_type("Inspection")

# No link constants for the plant-floor edges, and the omission is the
# convention rather than an oversight: this module protects lookup keys and
# declarations, and today every traversal passes its link name as a string
# (`ap_intake` walks `"filed_under"`). `require_link` is here for the day
# that changes, and a name it would guard is worth adding then — one caller
# at a time, with the read it protects.
