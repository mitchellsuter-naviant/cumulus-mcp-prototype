"""Casts: which people are in *this* deployment.

The roster decides who may decide a Task, so "which roster" is an
authorization question rather than a presentation one. Two casts live in the
build — `naviant` and `wick` — and exactly one is selected, from the same
`ESTATE_NAMESPACE` pin that decides which estate the connector reads. What is
tested here is the selection (including every way it can be wrong), the
invariants that must hold in *every* cast, and the Wick roster itself.

A second copy of `personas` is loaded per cast rather than reloading the real
one, because the session's `app.personas` is what `auth`, `queues` and
`ui_api` already hold: swapping the roster underneath a live TestClient would
leak into every test that ran afterwards. `_personas_for` gives a throwaway
module built by the same selector, off a patched setting, and registered
nowhere.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import casts, desks, personas
from app.config import settings

SERVER_DIR = Path(__file__).resolve().parents[1]

#: Every persona the Wick cast declares, with the person behind it and the
#: desks that are theirs to decide. Written out rather than derived, so this
#: file states the roster instead of restating the module.
WICK_ROSTER = {
    "naviant-engineer": ("Mitchell Suter", desks.ALL_SLUGS),
    "naviant-support": ("", ()),
    "customer-admin": ("Jeramy Bollig", ("knowledge-curation", "eval-review")),
    "customer-user:field-ops": ("Heather Danegelis", ("field-operations",)),
    "customer-user:plan-designer": ("Marlea Knox", ("field-operations",)),
    # Two desks, and the second one is the point: `hr-safety` is where this
    # namespace's credential work lands, and `hr` is where the Employee Hub has
    # always filed a person's own requests. Bound to nobody, a Wick time-off
    # request would arrive on a desk only the operator can see.
    "customer-user:hr-safety": ("Katy Tiller", ("hr-safety", "hr")),
    "customer-user:safety-sme": ("Steve Woodbury", ("hr-safety",)),
    "customer-user:ops-coordinator": (
        "Abby Wick", ("plant-supervisor", "ap-clerk", "expense-review"),
    ),
    "customer-user:plant-manager": ("Chris Robson", ("plant-supervisor",)),
    "customer-user:foreman": ("Dale Prellwitz", ()),
}


def _personas_for(namespace: str, monkeypatch):
    """A throwaway `personas` module bound to one namespace's cast.

    The real selector, reading the real setting — just not the copy the server
    is holding. Nothing registers it in `sys.modules`, so no other test can
    end up asking this module who is on the roster.
    """
    monkeypatch.setattr(settings, "estate_namespace", namespace)
    spec = importlib.util.find_spec("app.personas")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def wick(monkeypatch):
    return _personas_for("wick", monkeypatch)


# ---- the selector -----------------------------------------------------------


def test_the_namespace_pin_selects_the_cast():
    assert casts.for_namespace("wick") is casts.CASTS["wick"]
    assert casts.for_namespace("naviant") is casts.CASTS["naviant"]


@pytest.mark.parametrize("namespace", ["", "   ", "naviant", "NAVIANT"])
def test_an_unpinned_or_naviant_deployment_runs_the_naviant_cast(namespace):
    """`""` is what local dev and the whole test suite run as, so it cannot
    mean "no roster" — it means the cast the console was built against."""
    assert casts.for_namespace(namespace) is casts.CASTS["naviant"]


def test_an_unknown_namespace_falls_back_loudly(caplog):
    """A typo in a deploy flag must not take the deployment down over a
    persona list, and must not be silent either: the personas and the estate
    would then disagree about who the customer is."""
    with caplog.at_level("WARNING", logger="cumulus.casts"):
        assert casts.for_namespace("brigadoon") is casts.CASTS["naviant"]
    assert "brigadoon" in caplog.text
    assert "no cast" in caplog.text


def test_the_active_cast_reads_the_deployment_pin(monkeypatch):
    monkeypatch.setattr(settings, "estate_namespace", "wick")
    assert casts.active() is casts.CASTS["wick"]
    monkeypatch.setattr(settings, "estate_namespace", "")
    assert casts.active() is casts.CASTS["naviant"]


def test_the_pin_reaches_the_roster_through_the_environment():
    """End to end, in a process of its own: `ship.sh -n wick` sets an
    environment variable, and what has to happen is that `personas` — the name
    every caller imports — answers with Wick's people. A patched `settings`
    attribute cannot prove that the config wiring is there."""
    code = (
        "from app import personas;"
        "print(personas.CUSTOMER_ADMIN.email);"
        "print(personas.DEFAULT_PERSONA.id);"
        "print(len(personas.ALL_PERSONAS))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SERVER_DIR),
        env={**os.environ, "ESTATE_NAMESPACE": "wick"},
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [
        "jeramy.bollig@wickbuildings.com", "naviant-engineer", "10",
    ]


def test_a_wick_deployment_boots_and_offers_wick_people():
    """The whole door, in a process pinned to `wick`.

    Out of process for two reasons: the MCP session manager refuses to start
    twice in one interpreter (see `conftest`), and the roster must not be
    swapped underneath the session-scoped TestClient every other test shares.
    What it proves is what a deploy actually does — `ship.sh -n wick` sets one
    environment variable, and the console then opens on Wick's switcher with
    the three new desks bootstrapped into the queue registry.
    """
    code = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "with TestClient(app) as c:\n"
        "    b = c.get('/api/ui/v1/session').json()\n"
        "    print(b['persona_id'], b['in_workforce'])\n"
        "    print(b['groups'][1]['label'])\n"
        "    s = c.get('/api/ui/v1/session', headers={"
        "'X-Cumulus-Persona': 'customer-user:plant-manager'}).json()\n"
        "    print(s['person'], '|', s['title'], '|', ','.join(s['queues']))\n"
        "    print(','.join(sorted(q['slug'] for q in "
        "c.get('/api/v1/queues').json())))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SERVER_DIR),
        env={**os.environ, "ESTATE_NAMESPACE": "wick"},
        capture_output=True, text=True, timeout=300,
    )
    assert out.returncode == 0, out.stderr
    lines = [
        line for line in out.stdout.splitlines()
        if not line.startswith("{")  # the JSON request log
    ]
    # The default persona is still the engineer, still honestly absent from
    # the customer's org chart.
    assert lines[0] == "naviant-engineer False"
    assert lines[1] == "Wick Buildings"
    assert lines[2] == (
        "Chris Robson | Plant Superintendent, Mazomanie | plant-supervisor"
    )
    registry = lines[3].split(",")
    for slug in ("field-operations", "hr-safety", "plant-supervisor"):
        assert slug in registry, slug


def test_the_selector_is_the_only_selection_mechanism(wick):
    """The point of putting the choice in `personas`: the module every caller
    already imports is the one that answers, so there is no second place a
    deployment can be told who is here."""
    assert wick.CUSTOMER_ADMIN is casts.CASTS["wick"].CUSTOMER_ADMIN
    assert wick.GROUPS is casts.CASTS["wick"].GROUPS
    # And the session's own copy is unaffected by having loaded another cast.
    assert personas.CUSTOMER_ADMIN.person == "Nathan Pryce"


# ---- what holds in every cast -----------------------------------------------


@pytest.mark.parametrize("namespace", casts.NAMESPACES)
def test_every_persona_binds_only_to_declared_desks(namespace):
    """No persona may hold a queue nothing routes to. The desks are declared
    once, in `desks.py`, and a cast can only spend that vocabulary."""
    cast = casts.CASTS[namespace]
    roster = tuple(p for _, group in cast.GROUPS for p in group)
    bound = {queue for p in roster for queue in p.queues}
    assert bound <= set(desks.ALL_SLUGS), namespace


@pytest.mark.parametrize("namespace", casts.NAMESPACES)
def test_every_cast_is_internally_consistent(namespace, monkeypatch):
    module = _personas_for(namespace, monkeypatch)
    ids = [p.id for p in module.ALL_PERSONAS]
    uids = [p.uid for p in module.ALL_PERSONAS]
    emails = [p.email for p in module.ALL_PERSONAS if p.email]
    assert len(set(ids)) == len(ids)
    assert len(set(uids)) == len(uids)
    assert len(set(emails)) == len(emails)
    assert all(module.get(i) is not None for i in ids)
    # A persona with no `Employee` behind it opens onto an empty hub, so every
    # cast declares a person and a fallback title for everybody but the two
    # platform personas.
    for persona in module.ALL_PERSONAS:
        assert persona.blurb, persona.id
        assert persona.title or persona.id == "naviant-support", persona.id


@pytest.mark.parametrize("namespace", casts.NAMESPACES)
def test_the_two_naviant_personas_are_in_every_cast(namespace):
    """They are the field engineer operating the deployment and the support
    engineer diagnosing it — the platform's people, not the customer's org
    chart. Declared once and shared, so a Naviant engineer cannot end up with
    a different set of desks depending on which customer he is looking at."""
    cast = casts.CASTS[namespace]
    assert cast.ENGINEER is casts.CASTS["naviant"].ENGINEER
    assert cast.SUPPORT is casts.CASTS["naviant"].SUPPORT
    assert cast.ENGINEER.id == "naviant-engineer"
    assert cast.SUPPORT.id == "naviant-support"
    assert cast.OPERATOR_EMAIL == "masuter@naviant.com"
    assert cast.GROUPS[0] == ("Naviant", (cast.ENGINEER, cast.SUPPORT))


@pytest.mark.parametrize("namespace", casts.NAMESPACES)
def test_the_default_persona_is_the_engineer_in_every_cast(
    namespace, monkeypatch
):
    """Derived in `personas`, not declared per cast, so a new customer's
    roster cannot make somebody else the persona a cold session opens in."""
    module = _personas_for(namespace, monkeypatch)
    assert module.DEFAULT_PERSONA is module.ENGINEER
    assert module.DEFAULT_PERSONA.authority == "owner"
    assert module.DEFAULT_PERSONA.queues == desks.ALL_SLUGS


@pytest.mark.parametrize("namespace", casts.NAMESPACES)
def test_no_bare_role_confers_a_queue_binding(namespace, monkeypatch):
    """Cast-independent: deciding a Task comes from being this person, and a
    credential that presents only a role is nobody in particular."""
    module = _personas_for(namespace, monkeypatch)
    assert module.reach_for("owner") == {"scope": "platform", "queues": ()}
    assert module.reach_for("admin") == {"scope": "tenant", "queues": ()}
    assert module.reach_for("user") == {"scope": "own", "queues": ()}
    assert module.reach_for("nonsense") == {"scope": "own", "queues": ()}


# ---- the Wick roster --------------------------------------------------------


def test_the_wick_roster_is_the_people_the_sdds_name(wick):
    assert {p.id: (p.person, p.queues) for p in wick.ALL_PERSONAS} == {
        pid: (person, queues) for pid, (person, queues) in WICK_ROSTER.items()
    }
    # Seven of the eight customer personas are real people from Wick's own
    # process documents, at the real domain; the eighth (the foreman) is
    # invented because the SDDs name none.
    customer = (wick.CUSTOMER_ADMIN,) + wick.CUSTOMER_USERS
    assert len(customer) == 8
    assert all(p.email.endswith("@wickbuildings.com") for p in customer)
    assert all(
        p.email == f"{p.person.lower().replace(' ', '.')}@wickbuildings.com"
        for p in customer
    )


def test_wick_holds_none_of_the_desks_that_are_inert_there(wick):
    """`order-desk` and `data-stewardship` stay declared — a desk is a
    destination — but Wick runs no `crm`, so the disagreement sweep finds
    nothing and nobody should be offered work that can never arrive."""
    bound = {q for p in wick.ALL_PERSONAS if p.scope != "platform"
             for q in p.queues}
    assert desks.ORDER_DESK not in bound
    assert desks.DATA_STEWARDSHIP not in bound
    assert desks.VENDOR_STEWARDSHIP not in bound
    # And the three desks this cast exists for do have holders.
    for slug in (desks.FIELD_OPERATIONS, desks.HR_SAFETY,
                 desks.PLANT_SUPERVISOR):
        assert slug in bound, slug


def test_two_people_hold_each_wick_desk_except_the_scoping_one(wick):
    """A desk with one holder is an inbox; a shared desk is what the claim
    lease exists for. Each of the three new desks has two."""
    holders: dict[str, list[str]] = {}
    for persona in wick.CUSTOMER_USERS:
        for slug in persona.queues:
            holders.setdefault(slug, []).append(persona.person)
    assert holders[desks.FIELD_OPERATIONS] == ["Heather Danegelis",
                                               "Marlea Knox"]
    assert holders[desks.HR_SAFETY] == ["Katy Tiller", "Steve Woodbury"]
    assert holders[desks.PLANT_SUPERVISOR] == ["Abby Wick", "Chris Robson"]
    # The foreman owns nothing at all: this cast's proof that scoping works.
    foreman = wick.get("customer-user:foreman")
    assert foreman.queues == ()
    assert foreman.scope == "own" and foreman.authority == "user"


def test_the_finance_desks_have_a_customer_holder_in_wick_too(wick):
    """Decision W14, flagged rather than hidden: the SDDs name no AP clerk,
    and the kept AP and expense flows still have to be testable as somebody
    other than the engineer, who holds every desk and so proves nothing."""
    abby = wick.get("customer-user:ops-coordinator")
    assert desks.AP_CLERK in abby.queues
    assert desks.EXPENSE_REVIEW in abby.queues
    assert abby.authority == "user" and abby.scope == "own"


def test_a_wick_email_resolves_to_its_persona_and_its_desks(wick):
    assert wick.for_email("abby.wick@wickbuildings.com") is wick.get(
        "customer-user:ops-coordinator"
    )
    assert wick.for_email("ABBY.WICK@WickBuildings.com ") is wick.get(
        "customer-user:ops-coordinator"
    )
    assert wick.queues_for_email("chris.robson@wickbuildings.com") == (
        "plant-supervisor",
    )
    assert wick.queues_for_email("dale.prellwitz@wickbuildings.com") == ()
    # The operator holds every desk in this deployment too, and support none.
    assert wick.queues_for_email("masuter@naviant.com") == desks.ALL_SLUGS
    assert wick.queues_for_email("support@naviant.com") == ()
    # Nobody from the other cast is reachable here — that is the whole point
    # of a module boundary rather than a tag on each persona.
    assert wick.for_email("raymond.fitzgerald@naviant.com") is None
    assert wick.queues_for_email("raymond.fitzgerald@naviant.com") == ()
    assert wick.for_email("") is None and wick.queues_for_email("") == ()
    assert wick.get("customer-user:controller") is None


# ---- the three new desks ----------------------------------------------------


@pytest.mark.parametrize(
    "slug, owner",
    [
        ("field-operations", "tasks.field_documents.domain"),
        ("hr-safety", "tasks.credential_tracking.domain"),
        ("plant-supervisor", "tasks.department_scheduling.domain"),
    ],
)
def test_the_wick_desks_are_declared_with_an_owner(slug, owner):
    """`owner` is documentation with teeth: it names the module that routes
    work here, so a desk nothing routes to is visibly either dead or a Flow
    somebody forgot to wire. These three name modules that later lanes land —
    which is exactly the state the admin screen should be able to report."""
    desk = desks.get(slug)
    assert desk is not None
    assert desk.owner == owner
    assert desk.description
    assert slug in desks.ALL_SLUGS
    assert slug in personas.ALL_QUEUES  # one vocabulary, every namespace


def test_the_new_desk_constants_are_importable_everywhere():
    """A flow package names its desk at import, so these must resolve in a
    naviant deployment as well — which is why `DESKS` is one global tuple and
    not a per-namespace filter."""
    assert desks.FIELD_OPERATIONS == "field-operations"
    assert desks.HR_SAFETY == "hr-safety"
    assert desks.PLANT_SUPERVISOR == "plant-supervisor"
    assert desks.ORDER_DESK in desks.ALL_SLUGS  # inert in Wick, still declared


def test_a_cast_must_answer_the_whole_contract():
    """`personas` reads six names off whichever cast it selects, so a cast
    that defines five is a deployment resolving half a roster. Checked at
    import, where the error can name the file, rather than as an
    AttributeError on somebody's first request."""
    import types

    for namespace in casts.NAMESPACES:
        assert casts.contract_gaps(casts.CASTS[namespace]) == [], namespace
    for name in casts.CONTRACT:
        assert hasattr(personas, name), name

    half = types.SimpleNamespace(
        OPERATOR_EMAIL="", ENGINEER=None, SUPPORT=None,
    )
    assert casts.contract_gaps(half) == [
        "CUSTOMER_ADMIN", "CUSTOMER_USERS", "GROUPS",
    ]
