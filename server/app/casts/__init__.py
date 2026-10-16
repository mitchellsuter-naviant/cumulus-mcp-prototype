"""Which people are in this deployment.

One `Persona` shape, and one **cast** per customer namespace: `naviant.py` is
the roster this console was built against, `wick.py` is Wick Buildings. A
deployment runs exactly one of them, chosen from `ESTATE_NAMESPACE` — the same
pin that decides which estate the connector pulls from, because "whose systems
are we reading" and "whose people are we acting as" are one question with one
answer, and a deployment that got them from two settings could disagree with
itself about who the customer is.

**Why a module per cast, and not a `namespaces` tag on each Persona.** The
tagged version is the same code with one extra field, and it puts two
customers' real employees in the single file that decides who may decide a
Task. A wrong tag there is a Wick foreman holding a Naviant desk — a
mis-scoped authorization that reads, in the diff, as one word. A module
boundary makes *"who is in this deployment"* a whole-file answer: you read
`wick.py` top to bottom and you have the roster, with nothing to cross-check
and no filter to get right.

**Both casts are imported here, eagerly**, even though only one is selected.
Every queue a persona binds to is a `desks` constant resolved at import, so
importing both means a Wick desk that no longer exists fails on a *naviant*
deployment's startup too — the same bargain `desks._slug` already makes, and
the reason a roster typo is an ImportError rather than a Task nobody is bound
to. The cost is two small modules in memory; the alternative is a cast nobody
imports until the day it is deployed.

**An unknown or empty namespace selects naviant.** Local dev sets nothing, and
the honest default for "no estate pinned" is the roster the console was
developed against rather than an empty switcher. A non-empty value we do not
know is a typo in a deploy flag, so it is logged loudly and still falls back —
failing to import would take the whole deployment down over a persona list.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from types import ModuleType

from ..config import settings

log = logging.getLogger("cumulus.casts")

#: The cast an unpinned deployment runs with.
DEFAULT_NAMESPACE = "naviant"

#: Every cast this build carries. The name is the `ESTATE_NAMESPACE` value and
#: the module file name, deliberately the same string: a deployment names one
#: thing and the estate, the corpus and the roster all follow it.
NAMESPACES: tuple[str, ...] = ("naviant", "wick")


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
    #: What to call this persona's job **before the graph can answer**. The
    #: HRIS lands a real title on the `Employee` this persona acts as, and
    #: `ui_api` prefers it — so this is the cold-deployment fallback and the
    #: answer for the two Naviant personas, who are deliberately not in the
    #: customer's org chart. It stays declared rather than being dropped
    #: because sign-in must not depend on a populated graph: an empty
    #: deployment still has to resolve a persona and label the switcher.
    title: str = ""


#: One row of a cast's customer-user register: (slug, work email, person,
#: title, queues, blurb).
UserRow = tuple[str, str, str, str, tuple[str, ...], str]


def customer_users(rows: tuple[UserRow, ...]) -> tuple[Persona, ...]:
    """The customer-side personas of one cast, built from its register.

    Shared by both casts rather than written out in each, because the two
    things it decides — the `customer-user:<slug>` id and the `persona_<slug>`
    uid — are typed into a header by hand and recorded in the ledger
    respectively. Two spellings of that convention is how one cast ends up
    minting ids the console cannot switch to.

    The slug is authored in the register rather than sliced off the email:
    `customer-user:controller` says which desk is being tested, where
    `customer-user:raymond.fitzgerald` says only who sits at it. Hyphens are
    legal in a slug and awkward in a uid — `persona_field-ops` reads as
    arithmetic — so the uid normalises them, which is a no-op for every
    single-word naviant slug.
    """
    return tuple(
        Persona(
            id=f"customer-user:{slug}",
            label=name,
            authority="user",
            scope="own",
            queues=queues,
            email=email,
            uid=f"persona_{slug.replace('-', '_')}",
            person=name,
            title=title,
            blurb=blurb,
        )
        for slug, email, name, title, queues, blurb in rows
    )


def for_namespace(namespace: str) -> ModuleType:
    """The cast a namespace runs with — naviant for empty, or unrecognised.

    Pure, and separate from `active()`, so a test can ask what a namespace
    would select without reaching for the environment or reloading a module.
    """
    key = (namespace or "").strip().lower()
    cast = CASTS.get(key)
    if cast is not None:
        return cast
    if key:
        log.warning(
            "ESTATE_NAMESPACE=%r has no cast in app/casts — running the %s "
            "roster. Check the deploy flag: a namespace with no cast means "
            "the personas and the estate disagree about who the customer is.",
            namespace, DEFAULT_NAMESPACE,
        )
    return CASTS[DEFAULT_NAMESPACE]


def active() -> ModuleType:
    """The cast this deployment runs with.

    Reads the pin on every call rather than caching the answer, so a test can
    patch it and anything that legitimately re-asks gets the current one.
    `personas` resolves it once, at import, which is what a running server
    holds: a roster that changed between two requests in one session would
    mean the server disagreeing with itself about who may decide a Task.
    """
    return for_namespace(settings.estate_namespace)


#: Every name a cast module must define: the whole of its contract with
#: `personas`, which reads exactly these six off the module it selects.
CONTRACT: tuple[str, ...] = (
    "OPERATOR_EMAIL", "ENGINEER", "SUPPORT", "CUSTOMER_ADMIN",
    "CUSTOMER_USERS", "GROUPS",
)


def contract_gaps(cast: ModuleType) -> list[str]:
    """Which contract names a cast module does not answer."""
    return [name for name in CONTRACT if not hasattr(cast, name)]


def _load(name: str) -> ModuleType:
    """One cast, checked against the contract as it is imported.

    Checked rather than trusted, for `desks._slug`'s reason: a cast missing a
    name would otherwise fail as an AttributeError on the first request to a
    deployment pinned to it, which is both late and the wrong error. Half a
    roster is not a degraded console — it is a switcher offering personas
    whose desks nobody can resolve.
    """
    cast = importlib.import_module(f"{__name__}.{name}")
    gaps = contract_gaps(cast)
    if gaps:
        raise ImportError(
            f"app/casts/{name}.py declares no {', '.join(gaps)} — a cast has "
            "to answer every name `personas` selects over, or a deployment "
            "pinned to it resolves half a roster"
        )
    return cast


#: Imported after `Persona` and `customer_users` are defined, because that is
#: what the cast modules read back out of this package. Keep this at the
#: bottom of the file.
CASTS: dict[str, ModuleType] = {name: _load(name) for name in NAMESPACES}
