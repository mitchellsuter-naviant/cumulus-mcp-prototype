"""Who you are acting as.

The doctrine's Role primitive confers two things at once: **permissions**
(what you may touch) and **queue-bindings** (what work is yours). This module
is the whole of that, for the platform kind of Role.

The roster itself lives one level down, in `app/casts/`: one module per
customer namespace, selected here from `settings.estate_namespace`, with
`""` and any unknown value selecting `naviant`. `casts/naviant.py` carries the
roster this console was built against; `casts/wick.py` carries Wick Buildings.
Each cast declares the two Naviant personas (the field engineer operating the
deployment and the support engineer diagnosing it — the platform's people, not
the customer's org chart), its own customer admin, and its own customer users.
The shape of every entry is two facts plus its queues:

    persona            authority   scope     acts as
    naviant-engineer   owner       platform  the field engineer, in every cast
    naviant-support    user        platform  nobody — not in the org chart
    customer-admin     admin       tenant    the customer's own systems owner
    customer-user:*    user        own       the customer's people, one desk each

**The selection happens here, and not in the callers.** Every module that
needs a persona already imports `personas` — `auth` to build a Principal,
`queues` to bootstrap desk membership, `ui_api` to draw the switcher — so
making them ask a cast for a roster would add a second selection mechanism
without removing the first, and a second mechanism is the thing that drifts:
one caller reading the environment, another holding a cast it captured at
import, and a Task offered to somebody the other half of the server does not
believe exists. One selector, at the name everything already imports, means a
deployment can only have one answer to "who is here".

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

from . import casts, desks
#: Re-exported, not redefined: `personas.Persona` is the name the rest of the
#: server type-hints against (`auth.principal_for`, `ui_api._option`), and the
#: dataclass belongs beside the casts that instantiate it — otherwise a cast
#: module would have to import this one, and this one selects the casts.
from .casts import Persona  # noqa: F401  (re-export)

#: The cast this deployment runs with. Resolved once, at import, because a
#: roster that changed under a running server would mean two requests in one
#: session disagreeing about who may decide a Task. `casts.active()` is the
#: live answer for anything that legitimately wants to re-ask.
_CAST = casts.active()

#: The console's operator: whoever signs in to this deployment. It used to live
#: in `workforce.py` beside an invented org chart; that module was the mock
#: world and went with it, and the operator's identity belongs with the roster
#: anyway — `ENGINEER` below is the same fact with the rest of the answer
#: attached. The same address in every cast: the operator is the field
#: engineer who built the deployment, not one of the customer's people.
OPERATOR_EMAIL = _CAST.OPERATOR_EMAIL

#: Every queue Tasks route to, read off the desk declarations rather than
#: restated here. This list used to be the vocabulary — ten slugs, half of them
#: a second spelling of a constant in the module that routes to the desk — and
#: the registry bootstrapped from it, so the roster silently decided which
#: desks existed at all. `desks.py` decides that now; a persona binds to what
#: is declared and can no longer bind to a queue nothing files work to.
#:
#: Not per-namespace, for the reason `desks.py`'s docstring gives: a desk is a
#: work destination, and one that nothing routes to in this deployment is a
#: desk with no work rather than a desk that does not exist.
ALL_QUEUES: tuple[str, ...] = desks.ALL_SLUGS

# ---------------------------------------------------------------------------
# The roster, from the selected cast
# ---------------------------------------------------------------------------
#
# Read `app/casts/<namespace>.py` for the people and the reasoning about them.
# What is asserted here is what holds in *every* cast: the two Naviant
# personas exist, there is exactly one customer admin, the customer users are
# distinguished from each other by the desks they hold, and the default is the
# engineer.

ENGINEER = _CAST.ENGINEER
SUPPORT = _CAST.SUPPORT
CUSTOMER_ADMIN = _CAST.CUSTOMER_ADMIN
CUSTOMER_USERS: tuple[Persona, ...] = _CAST.CUSTOMER_USERS

#: Grouped the way the switcher shows them.
GROUPS: tuple[tuple[str, tuple[Persona, ...]], ...] = _CAST.GROUPS

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
#:
#: Derived from the cast rather than declared by it, so "the default is the
#: engineer" is a property of the platform and not a per-customer choice a new
#: cast could get wrong.
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

    Cast-independent on purpose. What a role reaches is a platform rule; who
    holds which desk is a customer fact, and this function is asked the first
    question by credential paths that have never heard of a persona.
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
