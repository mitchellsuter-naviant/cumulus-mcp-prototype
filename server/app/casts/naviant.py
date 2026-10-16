"""The `naviant` cast: the roster this console was built against.

    persona            authority   scope     acts as
    naviant-engineer   owner       platform  Mitchell Suter — operates the deployment
    naviant-support    user        platform  nobody — not in the org chart
    customer-admin     admin       tenant    Nathan Pryce, IT Manager
    customer-user      user        own       Raymond · Connie · Karen · Diane · John

**Everybody below the two Naviant rows is a real Naviant employee**, at the
work address the estate's HRIS actually holds for them. The invented cast that
used to sit here (`@customer.example`) was replaced when the estate became the
only world: a persona whose email resolves to nothing opens onto an empty hub
and an empty calendar, and every screen it is used to test then tests the
fallback rather than the screen.

Naviant is both the operator and the demo customer in this namespace, which is
why one company appears on both sides of the switcher. The two Naviant
personas are still the platform's — see `ENGINEER` and `SUPPORT` below, which
`wick.py` imports from here for exactly that reason.
"""

from __future__ import annotations

from .. import desks
from . import Persona, UserRow, customer_users

#: The whole of a cast's contract with `personas`, which reads exactly these
#: six names off the module it selects. `casts._load` checks it at import.
#: `wick.py` re-exports the first three from here.
__all__ = [
    "OPERATOR_EMAIL", "ENGINEER", "SUPPORT", "CUSTOMER_ADMIN",
    "CUSTOMER_USERS", "GROUPS",
]

#: The console's operator: whoever signs in to this deployment. It used to live
#: in `workforce.py` beside an invented org chart; that module was the mock
#: world and went with it, and the operator's identity belongs here anyway —
#: `ENGINEER` below is the same fact with the rest of the answer attached.
#:
#: The same address in every cast, because the operator of a deployment is the
#: field engineer who built it and not one of the customer's people. `wick.py`
#: imports it rather than restating it.
OPERATOR_EMAIL = "masuter@naviant.com"


# ---------------------------------------------------------------------------
# The roster
# ---------------------------------------------------------------------------
#
# Every customer persona is a real person in the estate's HRIS, because a
# persona with no `Employee` behind it opens onto an empty hub, an empty
# calendar and no expense history — which teaches you nothing about the screen
# you are testing. The two **Naviant** personas are the honest exceptions:
# neither the engineer operating the deployment nor a support engineer
# diagnosing a ticket is in the customer's org chart, and their hub should say
# so rather than invent a record.
#
# The customer-user personas differ from each other in the *queues* they bind
# to, which is the whole point. "Can Connie post this invoice" and "can John
# post this invoice" are different questions with different answers, and the
# write ladder cannot tell them apart — they sit on the same rung and own
# different work.

ENGINEER = Persona(
    id="naviant-engineer",
    label="Naviant Engineer",
    authority="owner",
    scope="platform",
    # The same tuple `personas.ALL_QUEUES` publishes: every desk that is
    # declared, so a desk added in `desks.py` is one the engineer can work
    # without a second edit here.
    queues=desks.ALL_SLUGS,
    email=OPERATOR_EMAIL,
    uid="persona_naviant_engineer",
    person="Mitchell Suter",
    title="Director, Platform & Operations",
    blurb="Builds and operates the deployment. Sees every plane, decides "
          "anything, and is the only persona that may act as another. Not in "
          "the customer's HRIS, so the personal surfaces say so.",
)

SUPPORT = Persona(
    id="naviant-support",
    label="Naviant Support",
    authority="user",
    scope="platform",
    queues=(),
    email="support@naviant.com",
    uid="persona_naviant_support",
    person="",
    title="Support Engineer",
    blurb="Reads everything to diagnose a customer's problem and decides "
          "nothing on their behalf. Use it to ask whether support could have "
          "answered a ticket from what the console shows.",
)

CUSTOMER_ADMIN = Persona(
    id="customer-admin",
    label="Customer Admin",
    authority="admin",
    scope="tenant",
    queues=(desks.KNOWLEDGE_CURATION, desks.EVAL_REVIEW),
    email="nathan.pryce@naviant.com",
    uid="persona_customer_admin",
    person="Nathan Pryce",
    title="IT Manager",
    blurb="Governs the customer's own map — knowledge, flows, connectors, "
          "access — and sees none of Naviant's platform internals. The IT "
          "owner, not a finance approver: the master-data desks belong to the "
          "Controller, who is accountable for what they decide.",
)

#: (persona slug, work email, person, title, queues, blurb) — see
#: `casts.customer_users`, which mints the id and the uid from the slug.
_USERS: tuple[UserRow, ...] = (
    (
        "controller", "raymond.fitzgerald@naviant.com", "Raymond Fitzgerald",
        "Controller",
        (desks.AP_CLERK, desks.EXPENSE_REVIEW, desks.VENDOR_STEWARDSHIP,
         desks.DATA_STEWARDSHIP, desks.ORDER_DESK),
        "The finance decision-maker: approves the spend, owns both "
        "master-data desks — who Cumulus pays, and who it thinks it is "
        "dealing with — and is the only persona who may release an order the "
        "CRM says was won. Test a reimbursement above the policy limit, a "
        "posting gate, a steward call on a vendor that will not resolve, and "
        "a disagreement between the CRM and the ERP about one commitment.",
    ),
    (
        "ap", "connie.alberts@naviant.com", "Connie Alberts",
        "Accounts Payable Specialist",
        (desks.AP_CLERK,),
        "Works the AP queue and nothing else. Test the posting gate and a "
        "three-way match that misses — then the same act as the Controller, "
        "who may also rule on the vendor behind it.",
    ),
    (
        "hr", "karen.boothe@naviant.com", "Karen Boothe", "HR Generalist",
        (desks.HR,),
        "The only persona bound to the HR queue, so a PTO request routed "
        "there has an owner. Her worker status is On Leave in the live HRIS, "
        "which is a fact the hub should show rather than smooth over.",
    ),
    (
        "ceo", "diane.vogel@naviant.com", "Diane Vogel",
        "Chief Executive Officer",
        (desks.EXPENSE_REVIEW, desks.EXPENSE_REVIEW_EXEC),
        "The org-wide read. Every department rolls up here, so it is the "
        "persona for a view that should span the whole company.",
    ),
    (
        "ae", "john.smith@naviant.com", "John Smith", "Account Executive",
        (),
        "Submits expenses and owns no queue at all. The persona that proves "
        "scoping works: everything an approver can do is refused here.",
    ),
)

CUSTOMER_USERS: tuple[Persona, ...] = customer_users(_USERS)

#: Grouped the way the switcher shows them.
GROUPS: tuple[tuple[str, tuple[Persona, ...]], ...] = (
    ("Naviant", (ENGINEER, SUPPORT)),
    ("Customer", (CUSTOMER_ADMIN,) + CUSTOMER_USERS),
)
