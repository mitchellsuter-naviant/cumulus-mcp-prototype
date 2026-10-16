"""The desks work lands on, declared once.

A queue slug used to be written down three times: as a constant in the module
whose Flow routes to it (`employee.HR_QUEUE`), as a literal in
`personas.ALL_QUEUES`, and as a key in the queue registry's blurb table. Three
copies of one word, and the roster's copy was the one the registry bootstrapped
from — so a desk the roster forgot existed for Tasks and not for the console,
and a desk the roster invented appeared in the admin screen with nothing ever
routing to it.

This module is the single declaration. It sits at the bottom of the import
graph on purpose: `personas` (the roster), `queues` (the registry) and every
module that routes to a desk read it, and it reads nothing. The alternative —
the roster importing each desk-owning module — is not available, because
`employee`, `task_queue` and `auth` all import the roster and the cycle closes
immediately.

`owner` names the module that routes work here. It is documentation with teeth:
a desk nobody routes to is either dead or a Flow somebody forgot to wire, and
the admin screen can say which.

**One global tuple, across every namespace.** The cast is per-deployment
(`app/casts/`) and the desks are not, because a desk is a work *destination*
and not a person: `plant-supervisor` is where a schedule awaiting sign-off
goes, whoever holds it, and in a deployment where nothing routes there it is
simply a desk with no work — which the Queues screen already says out loud.
Filtering this tuple by namespace would break the thing it exists for: a flow
package names its desk at **import** (`desks.PLANT_SUPERVISOR`), so a naviant
deployment importing the Wick flows would raise ImportError on a constant that
had been filtered away, and the failure would be at startup rather than at the
one line that was wrong.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Desk:
    slug: str
    #: Shown in the queue admin screen and on the registry entry bootstrapped
    #: from this declaration.
    description: str
    #: The module whose Flow or sweep routes Tasks here.
    owner: str

    @property
    def name(self) -> str:
        """The display name, derived rather than authored.

        Derived because it always was — the registry bootstrap titled the slug
        — and a deployment that has already bootstrapped keeps whatever name it
        stored. Writing them out here would have made two spellings of the same
        desk possible across two deployments for no gain.
        """
        return self.slug.replace("-", " ").title()


DESKS: tuple[Desk, ...] = (
    Desk(
        "expense-review",
        "Elevated reimbursements: over the governed limit, a restricted "
        "category, an uncertain extraction or a missing purpose.",
        "tasks.expense_reimbursement.domain",
    ),
    # The band above the routing table's top threshold. A desk rather than a
    # bigger number on the existing one, because the governed policy has always
    # said a VP signs for spend over $5,000 and the flow has always sent it to
    # the same reviewer as a $210 dinner — the document and the machinery
    # disagreeing in the customer's favour is still a disagreement.
    Desk(
        "expense-review-exec",
        "Reimbursements above the governed executive threshold, and every "
        "restricted category.",
        "routing (the governed approval table)",
    ),
    Desk(
        "ap-clerk",
        "Invoices held at the posting gate, and three-way matches that did "
        "not clear.",
        "tasks.accounts_payable.domain",
    ),
    Desk(
        "vendor-stewardship",
        "Who Cumulus pays: vendor identity that will not resolve on the "
        "evidence.",
        "mappings (erp-vendors)",
    ),
    # Separate from vendor stewardship because the two answer different
    # questions: one decides who Cumulus pays, the other decides who Cumulus
    # thinks it is dealing with, and the desks are not interchangeable even
    # where one person sits at both.
    Desk(
        "data-stewardship",
        "Who Cumulus thinks it is dealing with: counterparty matches and "
        "proposed merges.",
        "mappings (crm-book, erp-sales)",
    ),
    # Deliberately not `ap-clerk` and deliberately not either stewardship desk:
    # releasing an order is a revenue decision, and the authority that approves
    # what Cumulus *pays* is not the authority that decides what it *bills*.
    # Keeping them apart is what makes "Connie may not release revenue" a fact
    # the system enforces rather than a sentence in a runbook.
    Desk(
        "order-desk",
        "Commitments the CRM and the ERP disagree about.",
        "disagreement_sweep",
    ),
    Desk(
        "hr",
        "People requests — time off, and the rest of the hub's judgment calls.",
        "employee",
    ),
    Desk(
        "knowledge-curation",
        "Corrections proposed for promotion into canonical knowledge.",
        "gateway (the curation ledger)",
    ),
    Desk(
        "eval-review",
        "Flow evaluations awaiting a reviewer's label.",
        "evals",
    ),
    # Where a *step that threw* goes, as distinct from a business escalation. A
    # Vertex 400 about a file format used to land on the finance approver's
    # desk: the Controller cannot fix a mime type, and the person who can was
    # not looking at that queue.
    Desk(
        "platform-operations",
        "Flow steps that threw. An engineering fault, not a business "
        "judgment — nothing here is somebody's approval to give.",
        "routing (a step failure)",
    ),
    # ---- Wick Buildings ---------------------------------------------------
    #
    # Declared here with every other desk, because a desk is a destination and
    # this file is the vocabulary (see the module docstring). In a naviant
    # deployment these three are declared and inert: nothing routes to them,
    # they appear in the Queues screen with no work, and that is the honest
    # reading rather than a hidden row.
    #
    # Their `owner` modules do not exist yet — `field_documents` lands in Lane
    # 2, `credential_tracking` in Lane 3 and `department_scheduling` in Lane 5.
    # Naming them now is this file's own convention: `owner` is documentation
    # with teeth, and "a desk nobody routes to is either dead or a Flow
    # somebody forgot to wire" is precisely the state these are in until those
    # lanes land.
    Desk(
        "field-operations",
        "Job document packets that need a person: a job the document system "
        "holds nothing for, and a crew id that resolves to no team.",
        "tasks.field_documents.domain",
    ),
    Desk(
        "hr-safety",
        "Certifications, licences and inspections that need a person: a date "
        "that will not parse, a holder nobody can be found for, a record past "
        "its retention horizon.",
        "tasks.credential_tracking.domain",
    ),
    Desk(
        "plant-supervisor",
        "A day's department schedule awaiting sign-off, and an extracted grid "
        "the run could not read confidently.",
        "tasks.department_scheduling.domain",
    ),
)

# The mirror of the note above, in the other direction: two desks declared
# here are **inert in a Wick deployment**. `order-desk` has no CRM to
# disagree with an ERP about, and `data-stewardship` as a *merge* desk is fed
# by the disagreement sweep, which finds nothing for the same reason — Wick
# runs `erp`, `dms`, `office` and `hris`, and no `crm`. Both keep their
# declaration: the Sources and Queues screens read an app the namespace does
# not have as absent rather than as broken, which is what makes "nothing
# routes here" a fact a reader can act on.

#: Every desk Tasks route to, in declaration order. The roster binds personas to
#: these and the registry bootstraps from them; neither restates the list.
ALL_SLUGS: tuple[str, ...] = tuple(d.slug for d in DESKS)

_BY_SLUG = {d.slug: d for d in DESKS}


def get(slug: str) -> Desk | None:
    return _BY_SLUG.get(slug)


def _slug(name: str) -> str:
    """The one declared spelling of a desk, or a loud failure.

    Called at import by every module that names a desk, so a typo is an
    ImportError on the first request rather than a Task filed to a queue that
    does not exist and a reviewer who never sees it.
    """
    if name not in _BY_SLUG:
        raise ValueError(
            f"No desk `{name}` is declared in app/desks.py — a Task routed "
            "there would land on a queue nobody holds"
        )
    return name


EXPENSE_REVIEW = _slug("expense-review")
EXPENSE_REVIEW_EXEC = _slug("expense-review-exec")
AP_CLERK = _slug("ap-clerk")
VENDOR_STEWARDSHIP = _slug("vendor-stewardship")
DATA_STEWARDSHIP = _slug("data-stewardship")
ORDER_DESK = _slug("order-desk")
HR = _slug("hr")
KNOWLEDGE_CURATION = _slug("knowledge-curation")
EVAL_REVIEW = _slug("eval-review")
PLATFORM_OPERATIONS = _slug("platform-operations")
FIELD_OPERATIONS = _slug("field-operations")
HR_SAFETY = _slug("hr-safety")
PLANT_SUPERVISOR = _slug("plant-supervisor")
