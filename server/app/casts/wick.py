"""The `wick` cast: Wick Buildings, a post-frame construction manufacturer.

    persona                        acts as
    naviant-engineer               Mitchell Suter — operates the deployment
    naviant-support                nobody — not in the org chart
    customer-admin                 Jeramy Bollig · Systems Administrator
    customer-user:field-ops        Heather Danegelis · Construction Services
    customer-user:plan-designer    Marlea Knox · Construction Plan Designer
    customer-user:hr-safety        Katy Tiller · HR & Safety Manager
    customer-user:safety-sme       Steve Woodbury · Safety & Training SME
    customer-user:ops-coordinator  Abby Wick · Operations Coordinator
    customer-user:plant-manager    Chris Robson · Plant Superintendent
    customer-user:foreman          Dale Prellwitz · Field Foreman, Crew 12

The authority and scope columns are the naviant cast's, unchanged: the two
Naviant personas are `owner`/`platform` and `user`/`platform`, the customer
admin is `admin`/`tenant`, and every customer user is `user`/`own`. The desks
each one holds are in the register below, which is where they can be checked
against `desks.py`.

**Seven of the eight customer personas are the people Wick's own process
documents name**, at `first.last@wickbuildings.com` — the same choice the
naviant cast makes with `naviant.com`, and the reason a demo of this
deployment reads as *your* processes rather than as a product tour. The eighth,
the Crew 12 foreman **Dale Prellwitz**, is invented: the SDDs name no foreman,
and the persona that proves scoping has to be somebody. Every one of the eight
has to resolve to an `Employee` the estate's HRIS holds at that exact address
— the invariant that makes a roster worth having, since a persona whose email
resolves to nothing opens onto an empty hub and an empty calendar, and every
screen tested with it tests the fallback rather than the screen. The estate
seed is where that is made true; this file is the half that can be read.

The two Naviant personas are imported from the naviant cast rather than
restated. They are the field engineer who operates this deployment and the
support engineer who diagnoses it — the platform's people, in every
deployment, and deliberately not in the customer's org chart. Copying the two
literals into each cast would only create the possibility of a Naviant
engineer with a different set of desks depending on which customer he is
looking at.
"""

from __future__ import annotations

from .. import desks
from . import Persona, UserRow, customer_users
from .naviant import ENGINEER, OPERATOR_EMAIL, SUPPORT

#: The contract `casts._load` checks. Worth restating here rather than left
#: implicit, because three of the six names are re-exports: the operator
#: address and the two Naviant personas are the naviant cast's, imported
#: above rather than copied.
__all__ = [
    "OPERATOR_EMAIL", "ENGINEER", "SUPPORT", "CUSTOMER_ADMIN",
    "CUSTOMER_USERS", "GROUPS",
]


# ---------------------------------------------------------------------------
# The roster
# ---------------------------------------------------------------------------
#
# Every persona below binds only to desks `app/desks.py` declares, and three
# of those desks were added for this cast: `field-operations`, `hr-safety` and
# `plant-supervisor`. Two desks the naviant cast leans on are declared and
# **inert** here — `order-desk`, and `data-stewardship` as a merge desk —
# because Wick runs no `crm`, so the disagreement sweep has nothing to find.
# Nobody in this cast binds to either: a persona holding a desk that nothing
# can ever route to is the console offering work it will never have.

CUSTOMER_ADMIN = Persona(
    id="customer-admin",
    label="Customer Admin",
    authority="admin",
    scope="tenant",
    queues=(desks.KNOWLEDGE_CURATION, desks.EVAL_REVIEW),
    email="jeramy.bollig@wickbuildings.com",
    uid="persona_customer_admin",
    person="Jeramy Bollig",
    title="Systems Administrator",
    blurb="Governs Wick's own map — knowledge, flows, connectors, access — "
          "and sees none of Naviant's platform internals. The systems owner "
          "who provisions the accounts in the process documents, not a plant "
          "or safety approver: the schedule and compliance desks belong to "
          "the people accountable for what they decide. He is also the "
          "deployment's identity trap, spelled `Jeramy` in the HRIS and "
          "`Jeremy` in Office — the SDD's own misspelling, kept as drift.",
)

#: (persona slug, work email, person, title, queues, blurb) — see
#: `casts.customer_users`, which mints the id and the uid from the slug.
#:
#: Two bindings are worth reading twice.
#:
#: 1. **Abby Wick holds `ap-clerk` and `expense-review`** (decision W14). This
#:    is the one binding in this cast not read off a process document: the
#:    SDDs name no AP clerk and no expense approver. It is here so the kept
#:    accounts-payable and expense flows are testable *as a customer persona*
#:    rather than only as the engineer, who holds every desk and therefore
#:    proves nothing about scoping. Flagged for the user to move if there is a
#:    better answer — the natural candidate is a Controller, who would be an
#:    invented eighth name.
#: 2. **Dale Prellwitz holds nothing at all**, which is the point of him. He
#:    is this cast's `ae`: everything an approver can do is refused, so the
#:    scoping tests have a subject. He is also a state worth developing
#:    against rather than smoothing over — a work email in the HRIS and no
#:    Office mailbox, because a foreman's mail leaves the building. A hub that
#:    renders him has to render "no mail" as a fact and not as an outage.
_USERS: tuple[UserRow, ...] = (
    (
        "field-ops", "heather.danegelis@wickbuildings.com",
        "Heather Danegelis", "Construction Services Coordinator",
        (desks.FIELD_OPERATIONS,),
        "Owns the field-documents process end to end and was the human on the "
        "other end of every exception mail the old automation sent. The "
        "persona for the two questions that desk exists for: a job the "
        "document system holds nothing for, and a crew id that resolves to no "
        "team.",
    ),
    (
        "plan-designer", "marlea.knox@wickbuildings.com", "Marlea Knox",
        "Construction Plan Designer",
        (desks.FIELD_OPERATIONS,),
        "The second holder of `field-operations`, which is what makes it a "
        "shared desk rather than one person's inbox. Test the claim lease "
        "here: two people open the same packet Task, and only one of them may "
        "decide it.",
    ),
    (
        "hr-safety", "katy.tiller@wickbuildings.com", "Katy Tiller",
        "HR & Safety Manager",
        # `hr` as well as `hr-safety`, and the second one is the interesting
        # choice. `hr-safety` is where this namespace's credential work lands;
        # `hr` is where the Employee Hub has always filed a person's own
        # requests (`employee.HR_QUEUE` — time off, and the rest of the hub's
        # judgment calls). Bound to nobody, those requests would arrive in a
        # Wick deployment on a desk only the engineer can see, which is a
        # working hub with an unreachable queue behind it. Wick has an HR
        # function and this is the person who runs it, so she holds both.
        (desks.HR_SAFETY, desks.HR),
        "The process owner for credentials — certifications, CDL medical "
        "dates and annual equipment inspections. Test the retention Task the "
        "SDD asks for by name: a record past seven years is reported for "
        "manual review and never deleted automatically, which is "
        "deletes-are-decisions arriving from the customer's own side. She "
        "also owns the hub's people requests, so a Wick time-off request "
        "has a decider who is not the operator.",
    ),
    (
        "safety-sme", "steve.woodbury@wickbuildings.com", "Steve Woodbury",
        "Safety & Training Supervisor, Fork West",
        (desks.HR_SAFETY,),
        "Decides pass or fail at in-person training, so he is the second "
        "holder of `hr-safety` and the persona a date that will not parse "
        "should reach. Use him for a compliance judgment made by somebody who "
        "is not in HR, and for the alert stages that run before a Task is "
        "raised at all.",
    ),
    (
        "ops-coordinator", "abby.wick@wickbuildings.com", "Abby Wick",
        "Operations Coordinator",
        (desks.PLANT_SUPERVISOR, desks.AP_CLERK, desks.EXPENSE_REVIEW),
        "Owns the plant schedule's daily sign-off, which is a permanent "
        "control point rather than a step that can be automated away: no day "
        "exports until she has approved it. She also carries the AP and "
        "expense desks, so the two kept finance flows have a customer "
        "persona and not just the engineer — see the note above, and move it "
        "if there is a better answer.",
    ),
    (
        "plant-manager", "chris.robson@wickbuildings.com", "Chris Robson",
        "Plant Superintendent, Mazomanie",
        (desks.PLANT_SUPERVISOR,),
        "The authority on a department's minimum and maximum hours, and the "
        "second holder of `plant-supervisor`. The persona for a supervisor's "
        "correction outranking a machine reading: his answer lands as a "
        "`human` claim, above both `erp` and `model` in PRECEDENCE, which is "
        "the whole argument for a governed grid.",
    ),
    (
        "foreman", "dale.prellwitz@wickbuildings.com", "Dale Prellwitz",
        "Field Foreman, Crew 12",
        (),
        "Receives job packets, decides nothing, and owns no queue at all — "
        "the persona that proves scoping works in this cast the way John "
        "Smith does in naviant's. His identity is also the designed drift: "
        "the ERP types him as `D. Prellwitz` where the HRIS holds the legal "
        "name, and he has a work email with no Office mailbox behind it.",
    ),
)

CUSTOMER_USERS: tuple[Persona, ...] = customer_users(_USERS)

#: Grouped the way the switcher shows them. The customer's own name, because a
#: Wick supervisor reading a switcher headed "Customer" is being shown
#: Naviant's word for their own company.
GROUPS: tuple[tuple[str, tuple[Persona, ...]], ...] = (
    ("Naviant", (ENGINEER, SUPPORT)),
    ("Wick Buildings", (CUSTOMER_ADMIN,) + CUSTOMER_USERS),
)
