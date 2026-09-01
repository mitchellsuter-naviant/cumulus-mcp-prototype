"""Who you are acting as.

The doctrine's Role primitive confers two things at once: **permissions**
(what you may touch) and **queue-bindings** (what work is yours). This module
is the whole of that, for the platform kind of Role.

The roster, and the shape of each entry is two facts plus its queues:

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

`authority` is a rung on the existing write ladder, so every `require_role`
call in the server keeps working unchanged. `scope` is the orthogonal read
axis, because the ladder alone cannot say what Naviant Support is: it reads
everything and writes nothing structural, which is *above* a customer admin on
one axis and *below* it on the other. Two fields express that; a single ladder
cannot, and a full capability matrix would be machinery we have no use for.

Impersonation is how the console is developed and demonstrated. A persona
switch rewrites the `Principal` itself — uid, email, authority, scope — rather
than passing a hint alongside it, so everything downstream (the Employee Hub
resolving "me", a task queue filtering to yours, a claim recording its author)
scopes correctly with no plumbing of its own. The real operator is preserved
on `Principal.impersonated_by` so the ledger can name both parties, which is
the only thing an audit reader needs that the rewrite would otherwise lose.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The console's operator: whoever signs in to this deployment. It used to live
#: in `workforce.py` beside an invented org chart; that module was the mock
#: world and went with it, and the operator's identity belongs here anyway —
#: `ENGINEER` below is the same fact with the rest of the answer attached.
OPERATOR_EMAIL = "masuter@naviant.com"

#: Every queue Tasks actually route to. Declared here because a persona binds
#: to queues and nothing else in the system enumerates them — `tasks.py` only
#: ever sees the one name a caller hands it.
ALL_QUEUES: tuple[str, ...] = (
    "expense-review",       # reimbursements.REVIEW_QUEUE
    "ap-clerk",             # payables.CLERK_QUEUE
    "vendor-stewardship",   # sync.STEWARD_QUEUE
    # sync.DATA_STEWARD_QUEUE — the counterparty book. Separate from vendor
    # stewardship because the two answer different questions: one decides who
    # Cumulus pays, the other decides who Cumulus thinks it is dealing with,
    # and the desks are not interchangeable even where one person sits at both.
    "data-stewardship",
    # disagreement_sweep.ORDER_DESK_QUEUE — where a two-system disagreement
    # about an order lands. Deliberately not `ap-clerk` and deliberately not
    # either stewardship desk: releasing an order is a revenue decision, and
    # the authority that approves what Cumulus *pays* is not the authority
    # that decides what it *bills*. Keeping them apart is what makes "Connie
    # may not release revenue" a fact the system enforces rather than a
    # sentence in a runbook.
    "order-desk",
    "hr",                   # employee.HR_QUEUE
    "knowledge-curation",
    "eval-review",
)


@dataclass(frozen=True)
class Persona:
    """A platform Role, plus the identity to act as when impersonating it."""

    id: str
    label: str
    #: Rung on the write ladder in auth.ROLES — what `require_role` compares.
    authority: str
    #: Read reach: "platform" (the whole deployment), "tenant" (the customer's
    #: own map), or "own" (this person's work and nothing else).
    scope: str
    #: Queues whose Tasks are this persona's to decide. Empty means none.
    queues: tuple[str, ...] = ()
    #: One line describing what this persona is for, shown in the switcher.
    blurb: str = ""
    #: The graph `Employee` natural key this persona acts as.
    email: str = ""
    uid: str = ""
    #: The seeded person behind the persona, empty when there is none.
    person: str = ""
    title: str = ""


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
    queues=ALL_QUEUES,
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
    queues=("knowledge-curation", "eval-review"),
    email="nathan.pryce@naviant.com",
    uid="persona_customer_admin",
    person="Nathan Pryce",
    title="IT Manager",
    blurb="Governs the customer's own map — knowledge, flows, connectors, "
          "access — and sees none of Naviant's platform internals. The IT "
          "owner, not a finance approver: the master-data desks belong to the "
          "Controller, who is accountable for what they decide.",
)

# (persona slug, work email, person, title, queues, blurb). The slug is written
# out rather than sliced off the email because these ids are typed into a
# header by hand — `customer-user:controller` says which desk is being tested,
# where `customer-user:raymond.fitzgerald` says only who sits at it.
_USERS: tuple[tuple[str, str, str, str, tuple[str, ...], str], ...] = (
    (
        "controller", "raymond.fitzgerald@naviant.com", "Raymond Fitzgerald",
        "Controller",
        ("ap-clerk", "expense-review", "vendor-stewardship",
         "data-stewardship", "order-desk"),
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
        ("ap-clerk",),
        "Works the AP queue and nothing else. Test the posting gate and a "
        "three-way match that misses — then the same act as the Controller, "
        "who may also rule on the vendor behind it.",
    ),
    (
        "hr", "karen.boothe@naviant.com", "Karen Boothe", "HR Generalist",
        ("hr",),
        "The only persona bound to the HR queue, so a PTO request routed "
        "there has an owner. Her worker status is On Leave in the live HRIS, "
        "which is a fact the hub should show rather than smooth over.",
    ),
    (
        "ceo", "diane.vogel@naviant.com", "Diane Vogel",
        "Chief Executive Officer",
        ("expense-review",),
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

CUSTOMER_USERS: tuple[Persona, ...] = tuple(
    Persona(
        id=f"customer-user:{slug}",
        label=name,
        authority="user",
        scope="own",
        queues=queues,
        email=email,
        uid=f"persona_{slug}",
        person=name,
        title=title,
        blurb=blurb,
    )
    for slug, email, name, title, queues, blurb in _USERS
)

#: Grouped the way the switcher shows them.
GROUPS: tuple[tuple[str, tuple[Persona, ...]], ...] = (
    ("Naviant", (ENGINEER, SUPPORT)),
    ("Customer", (CUSTOMER_ADMIN,) + CUSTOMER_USERS),
)

ALL_PERSONAS: tuple[Persona, ...] = tuple(
    p for _, group in GROUPS for p in group
)

_BY_ID = {p.id: p for p in ALL_PERSONAS}

#: The persona a session starts in. The engineer, because the console's first
#: job is to be developed against and only an `owner` reaches every screen.
#: He is deliberately *not* in the customer's org chart, so the personal
#: surfaces open on their not-in-the-workforce state — which is the state a
#: deployed operator actually sees, and therefore the one worth developing
#: against. Switch to a customer persona to work a populated hub.
DEFAULT_PERSONA = ENGINEER


def reach_for(role: str) -> dict:
    """The read reach and queue bindings a bare `role` carries, with no persona.

    The credential paths that authenticate a real account — a Firebase token, a
    minted key — know a role and nothing else. They still have to say what that
    role reaches, because a `Principal` that leaves the fields unset is the
    fail-open case this exists to prevent.

    **No role confers a queue binding.** Deciding a Task is work assignment, not
    authority: it comes from being *this person*, which is what a persona is.
    An owner may read everything and still owns no queue until it acts as
    somebody — which is stricter than the console's local default and correct
    for a deployed one.
    """
    if role == "owner":
        return {"scope": "platform", "queues": ()}
    if role == "admin":
        return {"scope": "tenant", "queues": ()}
    return {"scope": "own", "queues": ()}


def get(persona_id: str) -> Persona | None:
    return _BY_ID.get(persona_id)


def for_email(email: str) -> Persona | None:
    """The persona whose work email is this one, if the roster holds it.

    The roster is keyed by `id` for switching and by `uid` for attribution,
    and neither is what a credential presents: a Firebase token carries a
    verified *email* and an opaque provider uid. So the operator signing in as
    themselves and the `naviant-engineer` persona were the same human with the
    same email and no edge between them — the persona held every desk and the
    real account held none, and the console offered work it would then refuse.
    """
    if not email:
        return None
    lowered = email.strip().lower()
    return next(
        (p for p in ALL_PERSONAS if p.email and p.email.lower() == lowered), None
    )


def queues_for_email(email: str) -> tuple[str, ...]:
    """Every desk the roster says this person holds.

    A union rather than the first match: two roster entries may legitimately
    name the same person (an operator persona and the customer-side identity
    they also hold), and holding a desk under either one is holding it.

    This is the *assignment* half of authority, and it deliberately does not
    touch the *access* half. `reach_for` still decides what a credential may
    read and write from its role; being on the roster adds work, never power.
    DOCTRINE leaves "whether access-role and assignment-role ever need to
    diverge" open at point 230 — here they plainly do: access is proven by the
    credential, assignment is declared by the org.
    """
    if not email:
        return ()
    lowered = email.strip().lower()
    found: list[str] = []
    for persona in ALL_PERSONAS:
        if persona.email and persona.email.lower() == lowered:
            found.extend(q for q in persona.queues if q not in found)
    return tuple(found)
