"""Who you are acting as.

The doctrine's Role primitive confers two things at once: **permissions**
(what you may touch) and **queue-bindings** (what work is yours). This module
is the whole of that, for the platform kind of Role.

The roster, and the shape of each entry is two facts plus its queues:

    persona            authority   scope     acts as
    naviant-engineer   owner       platform  Mitchell Suter
    naviant-support    user        platform  nobody — not in the org chart
    customer-admin     admin       tenant    Sam Okafor
    customer-user      user        own       Tomas · Nadia · Kim · Rowan

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

#: Every queue Tasks actually route to. Declared here because a persona binds
#: to queues and nothing else in the system enumerates them — `tasks.py` only
#: ever sees the one name a caller hands it.
ALL_QUEUES: tuple[str, ...] = (
    "expense-review",       # reimbursements.REVIEW_QUEUE
    "ap-clerk",             # payables.CLERK_QUEUE
    "vendor-stewardship",   # sync.STEWARD_QUEUE
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
# Every persona is a real person wherever one exists, because a persona with
# no `Employee` behind it opens onto an empty hub, an empty calendar and no
# expense history — which teaches you nothing about the screen you are
# testing. Naviant Support is the one exception, and honestly so: support
# staff are not in the customer's org chart, and their hub should say that
# rather than invent a record.
#
# The customer-user personas differ from each other in the *queues* they bind
# to, which is the whole point. "Can Tomas post this invoice" and "can Kim post
# this invoice" are different questions with different answers, and the write
# ladder cannot tell them apart — they sit on the same rung and own different
# work.

ENGINEER = Persona(
    id="naviant-engineer",
    label="Naviant Engineer",
    authority="owner",
    scope="platform",
    queues=ALL_QUEUES,
    email="masuter@naviant.com",
    uid="persona_naviant_engineer",
    person="Mitchell Suter",
    title="Director, Platform & Operations",
    blurb="Builds and operates the deployment. Sees every plane, decides "
          "anything, and is the only persona that may act as another.",
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
    queues=("vendor-stewardship", "knowledge-curation", "eval-review"),
    email="sam@customer.example",
    uid="persona_customer_admin",
    person="Sam Okafor",
    title="Cloud Platform Lead",
    blurb="Governs the customer's own map — knowledge, flows, access — and "
          "sees none of Naviant's platform internals.",
)

_USERS: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    (
        "ap@customer.example", "Tomas Berg", "Accounts Payable Specialist",
        ("ap-clerk",),
        "Works the AP queue. Test the posting gate, a vendor that will not "
        "resolve, and a three-way match that misses.",
    ),
    (
        "nadia@customer.example", "Nadia Farrell", "Finance & Operations Lead",
        ("expense-review", "ap-clerk"),
        "Approves expenses and owns the spend picture. Test the management "
        "dashboard and a reimbursement above the policy limit.",
    ),
    (
        "kim@customer.example", "Kim Reyes", "Senior Data Engineer",
        (),
        "Submits expenses and owns no queue at all. The persona that proves "
        "scoping works: everything an approver can do is refused here.",
    ),
    (
        "rowan@customer.example", "Rowan Ellis", "Managing Partner",
        ("expense-review",),
        "The org-wide read. Every team rolls up here, so it is the persona "
        "for a view that should span the whole company.",
    ),
)

CUSTOMER_USERS: tuple[Persona, ...] = tuple(
    Persona(
        id=f"customer-user:{email.split('@')[0]}",
        label=name,
        authority="user",
        scope="own",
        queues=queues,
        email=email,
        uid=f"persona_{email.split('@')[0]}",
        person=name,
        title=title,
        blurb=blurb,
    )
    for email, name, title, queues, blurb in _USERS
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
#: job is to be developed against — and because he is a real employee, so the
#: personal surfaces have something in them from the first render.
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
