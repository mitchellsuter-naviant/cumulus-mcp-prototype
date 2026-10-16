"""Personas: who you are acting as, and what that lets you do.

The doctrine's Role confers two things at once — a rung on the write ladder
and a set of queue-bindings — so both halves are exercised here, together with
the invariant that makes the roster worth having: every persona meant to be
one of the customer's people can actually be found in the customer's graph,
and the two that are not say so.
"""

import pytest
from fastapi import HTTPException

from app import auth, personas, ui_api
from app.auth import Principal
from app.config import settings
from app.ui_api import get_ui_principal
from tests import demo_world
from tests import world as test_world

#: The personas whose authority is above `user` on the write ladder. Written
#: out rather than derived from `persona.authority`, so the test states the
#: expected answer instead of restating the implementation.
MAY_WRITE = {"naviant-engineer", "customer-admin"}


@pytest.fixture()
def world():
    w = test_world.fresh()
    demo_world.seed_workforce(w.sync, w.graph)
    demo_world.seed_calendars(w.sync, w.graph)
    return w


@pytest.fixture()
def client(_client, world):
    return _client


def _as(persona_id: str) -> dict[str, str]:
    return {"X-Cumulus-Persona": persona_id}


def _ids(roster) -> list[str]:
    return [p.id for p in roster]


# ---- the session ------------------------------------------------------------


def test_a_session_without_a_header_is_the_default_persona(client):
    body = client.get("/api/ui/v1/session").json()
    assert body["persona_id"] == personas.DEFAULT_PERSONA.id
    assert body["uid"] == personas.DEFAULT_PERSONA.uid
    assert body["email"] == personas.DEFAULT_PERSONA.email
    assert body["authority"] == "owner"
    assert body["scope"] == "platform"
    assert body["impersonated_by"] is None
    assert body["may_switch_persona"] is True

    assert body["groups"]
    assert all(group["personas"] for group in body["groups"])
    offered = [p["id"] for group in body["groups"] for p in group["personas"]]
    assert offered == _ids(personas.ALL_PERSONAS)


def test_switching_persona_rewrites_the_whole_principal(client):
    """Impersonation is not a hint travelling beside the principal. Uid, email,
    authority, scope and queues are all Kim's; only `impersonated_by` remembers
    who actually asked."""
    kim = personas.get("customer-user:ae")
    body = client.get("/api/ui/v1/session", headers=_as(kim.id)).json()
    assert body["uid"] == kim.uid
    assert body["email"] == "john.smith@naviant.com"
    assert body["person"] == "John Smith"
    assert body["authority"] == "user"
    assert body["scope"] == "own"
    assert body["queues"] == []
    assert body["impersonated_by"] == personas.DEFAULT_PERSONA.uid


def test_an_unknown_persona_is_refused(client):
    resp = client.get("/api/ui/v1/session", headers=_as("customer-user:nobody"))
    assert resp.status_code == 400
    assert "Unknown persona" in resp.json()["detail"]


# ---- the write ladder -------------------------------------------------------


@pytest.mark.parametrize(
    "persona", personas.ALL_PERSONAS, ids=_ids(personas.ALL_PERSONAS)
)
def test_the_authority_ladder_decides_who_may_write(client, persona):
    """Scope never buys a write. Naviant Support reads the whole deployment and
    is still refused here, because `authority` is the only axis the ladder
    compares."""
    asset_id = f"persona-gate-{persona.id.replace(':', '-')}"
    payload = {
        "id": asset_id,
        "path": f"z/{asset_id}",
        "title": "Persona gate",
        "content": "Written by whoever the ladder let through.",
    }
    resp = client.post("/api/v1/assets", json=payload, headers=_as(persona.id))

    if persona.id in MAY_WRITE:
        assert resp.status_code == 201
        removed = client.delete(
            f"/api/v1/assets/{asset_id}", headers=_as(persona.id)
        )
        assert removed.status_code == 204
    else:
        assert resp.status_code == 403
        assert client.get(f"/api/v1/assets/{asset_id}").status_code == 404


# ---- queue-bindings ---------------------------------------------------------


def test_queue_bindings_separate_two_people_on_the_same_rung():
    """The AP specialist and the account executive are both `user`, and only
    one of them may work AP. The ladder cannot tell them apart, which is the
    whole reason queues exist."""
    tomas = auth.principal_for(personas.get("customer-user:ap"))
    kim = auth.principal_for(personas.get("customer-user:ae"))
    assert tomas.role == kim.role == "user"

    assert auth.require_queue(tomas, "ap-clerk") is tomas

    with pytest.raises(HTTPException) as refused:
        auth.require_queue(kim, "ap-clerk")
    assert refused.value.status_code == 403
    assert "ap-clerk" in refused.value.detail


def test_the_engineer_owns_every_queue():
    engineer = auth.principal_for(personas.ENGINEER)
    for queue in personas.ALL_QUEUES:
        assert auth.require_queue(engineer, queue) is engineer, queue


def test_a_persona_also_holds_the_desks_the_registry_grants_it(world):
    """Kim owns no shared queue and still has to be able to decide her own.

    A personal desk is minted the first time something routes to it — an
    expense of hers routed back for her to confirm — so the roster cannot know
    about it, because it did not exist when the code was written. A persona
    principal therefore unions the queue registry the way a real Firebase
    credential already did; without it the console offers somebody their own
    review and then refuses it.
    """
    kim = personas.get("customer-user:ae")
    assert auth.principal_for(kim).queues == ()

    desk = world.queues.ensure_personal(kim.email, kim.person).slug
    principal = auth.principal_for(kim)
    assert desk in principal.queues
    assert auth.require_queue(principal, desk) is principal
    # It adds work and never power: still the bottom rung, still own-scope.
    assert principal.role == "user" and principal.scope == "own"


def test_sign_in_survives_an_unreadable_queue_registry(monkeypatch):
    """What an outage costs is the desks only the console knows about. It must
    not cost sign-in — the roster answers from code, so a persona resolves
    against a deployment whose registry cannot be read at all."""
    import app.deps as deps

    def broken():
        raise RuntimeError("the registry is unreachable")

    monkeypatch.setattr(deps, "get_queue_service", broken)
    clerk = personas.get("customer-user:ap")
    assert auth.principal_for(clerk).queues == clerk.queues


# ---- the person behind the persona ------------------------------------------


#: Everybody on the customer's side of the roster. Each is a real Naviant
#: employee at the work address the estate's HRIS holds, so a persona test and
#: a live session resolve the same human.
_CUSTOMER = (personas.CUSTOMER_ADMIN,) + personas.CUSTOMER_USERS


@pytest.mark.parametrize("persona", _CUSTOMER, ids=_ids(_CUSTOMER))
def test_every_customer_persona_is_really_in_the_graph(client, persona):
    """A persona with no `Employee` behind it opens onto an empty hub and an
    empty calendar, which teaches nothing about the screen under test."""
    body = client.get("/api/ui/v1/session", headers=_as(persona.id)).json()
    assert body["person"] == persona.person
    assert body["email"].endswith("@naviant.com")
    assert body["in_workforce"] is True


def test_a_title_comes_from_the_hris_and_not_from_the_roster(client, world):
    """Whose fact is a job title? The customer's HR system's.

    It was authored beside the persona, so a promotion in the HRIS left the
    switcher printing a title Naviant made up. The declared one is the
    fallback; where the graph holds an `Employee`, the graph wins.
    """
    from app.graph import Claim, object_id
    from app.tasks.accounts_payable import domain as payables  # noqa: F401

    controller = next(
        p for p in personas.CUSTOMER_USERS if p.id.endswith(":controller")
    )
    person = world.graph.get(object_id("Employee", controller.email))
    world.graph.upsert(
        "Employee", controller.email,
        [Claim(field="title", value="VP, Finance", source="hris")],
        label=person.label,
    )

    body = client.get("/api/ui/v1/session", headers=_as(controller.id)).json()
    assert body["title"] == "VP, Finance" != controller.title
    switcher = {
        option["id"]: option
        for group in body["groups"] for option in group["personas"]
    }
    assert switcher[controller.id]["title"] == "VP, Finance"


def test_a_persona_still_resolves_on_an_empty_deployment(_client):
    """Sign-in must not depend on a populated graph. A cold deployment has no
    `Employee` for anybody, and the switcher still has to name a job."""
    test_world.fresh()

    body = _client.get(
        "/api/ui/v1/session", headers=_as(personas.CUSTOMER_ADMIN.id)
    ).json()
    assert body["in_workforce"] is False
    assert body["title"] == personas.CUSTOMER_ADMIN.title


@pytest.mark.parametrize(
    "persona", (personas.SUPPORT, personas.ENGINEER), ids=_ids(
        (personas.SUPPORT, personas.ENGINEER)
    )
)
def test_the_naviant_personas_are_honestly_absent_from_the_org_chart(
    client, persona
):
    """Neither of them is one of the customer's employees — not the support
    engineer diagnosing a ticket, and not the engineer who operates the
    deployment. The console says so rather than inventing a record, which is
    also the state a real deployed operator sees on every personal surface.
    """
    body = client.get("/api/ui/v1/session", headers=_as(persona.id)).json()
    assert body["in_workforce"] is False
    assert body["scope"] == "platform"  # both still read everything


def test_a_persona_sees_its_own_personal_surfaces(client):
    """Nadia is a real employee, so `/calendar/me` is her week — no demo
    persona substituted underneath, and so no note saying one was."""
    body = client.get(
        "/api/v1/calendar/me", headers=_as("customer-user:controller")
    ).json()
    assert body["email"] == "raymond.fitzgerald@naviant.com"
    assert body["name"] == "Raymond Fitzgerald"
    assert body["persona_note"] == ""


# ---- the console door -------------------------------------------------------


def test_the_console_api_refuses_a_machine_credential():
    """A key is a machine credential and a machine has no screens; letting one
    through would make every composed convenience route public contract."""
    machine = Principal(
        uid="svc-1", email="svc@machine", role="owner", key_id="k_1"
    )
    with pytest.raises(HTTPException) as refused:
        get_ui_principal(machine)
    assert refused.value.status_code == 403
    assert "/api/v1" in refused.value.detail


# ---- the roster itself ------------------------------------------------------


def test_the_roster_is_internally_consistent():
    ids = _ids(personas.ALL_PERSONAS)
    uids = [p.uid for p in personas.ALL_PERSONAS]
    assert len(set(ids)) == len(ids)
    assert len(set(uids)) == len(uids)
    assert all(personas.get(i) is not None for i in ids)

    bound = {queue for p in personas.ALL_PERSONAS for queue in p.queues}
    assert bound <= set(personas.ALL_QUEUES)


# ---- the desk vocabulary ----------------------------------------------------


def test_a_desk_is_declared_once_and_read_everywhere():
    """The roster used to *be* the list of desks, and half of it was a second
    spelling of a constant in the module that routes there. A slug typed twice
    is a slug that can disagree with itself, and the disagreement shows up as a
    Task nobody is bound to rather than as an error."""
    from app import desks, disagreement_sweep, employee, routing
    from app.tasks.accounts_payable import domain as payables
    from app.tasks.expense_reimbursement import domain as reimbursements

    routed = {
        payables.CLERK_QUEUE,
        reimbursements.REVIEW_QUEUE,
        routing.OPS_QUEUE,
        employee.HR_QUEUE,
        disagreement_sweep.ORDER_DESK_QUEUE,
    }
    assert routed <= set(desks.ALL_SLUGS)
    assert personas.ALL_QUEUES is desks.ALL_SLUGS
    # Every declared desk carries the description the registry bootstraps with,
    # so an admin screen never opens onto an unexplained slug.
    assert all(d.description and d.owner for d in desks.DESKS)


def test_a_desk_that_is_not_declared_cannot_be_named():
    """The point of the registry: a typo is a failure at import, not a Task
    filed to a queue that does not exist."""
    from app import desks

    with pytest.raises(ValueError, match="ap-clrek"):
        desks._slug("ap-clrek")


# ---- reach: one rule, or none at all ----------------------------------------


def test_an_own_scoped_persona_cannot_read_the_graph_around_it(client):
    """The narrow gate is worthless while its neighbours stay open.

    `/graph/employees/{email}/expenses` refused a persona asking about someone
    else, and `/graph/objects?type=ExpenseRequest` handed back every
    colleague's rows with their amounts in one call. Whatever the rule is, it
    has to hold across the surface — so this walks the whole of it.
    """
    kim = {"X-Cumulus-Persona": "customer-user:ae"}
    for path in (
        "/api/v1/graph/objects?type=ExpenseRequest",
        "/api/v1/graph/objects",
        "/api/v1/graph/links",
        "/api/v1/graph/instances",
        "/api/v1/graph/search?q=expense",
        "/api/v1/graph/stats",
        "/api/v1/graph/employees/diane.vogel@naviant.com/expenses",
    ):
        assert client.get(path, headers=kim).status_code == 403, path

    # The model names types, not people, so reading it discloses nothing.
    assert client.get("/api/v1/graph/ontology", headers=kim).status_code == 200


def test_an_own_scoped_persona_may_name_a_document_and_not_browse_for_one(
    client,
):
    """The same rule the rail draws, now held by the routes behind it.

    The console drops the Knowledge layer and the identity roster for an
    own-scoped persona, and the command palette offered both anyway — which is
    how this was found. But the palette was only reporting what the server
    said: `/assets` and `/teams` enumerated the whole deployment for anybody
    who asked.

    The split is not "assets are secret". Reading *this* policy is how an
    own-scoped screen renders the clause a run cited — the posting limit on
    the AP submission panel, the PTO window on the hub — so naming one stays
    open, and
    so does naming the team an asset says owns it. Browsing the corpus, or the
    access map, is a governance act.
    """
    kim = {"X-Cumulus-Persona": "customer-user:ae"}
    for path in ("/api/v1/assets", "/api/v1/assets/tree", "/api/v1/teams"):
        refused = client.get(path, headers=kim)
        assert refused.status_code == 403, path
        assert refused.json()["detail"].endswith(
            "Your access is to your own work — a document you can name is "
            "still yours to read."
        ) or refused.json()["detail"].endswith(
            "Your access is to your own work — a team you can name is still "
            "yours to read."
        ), path

    named = client.get("/api/v1/assets/expense-approval-policy", headers=kim)
    assert named.status_code == 200
    assert client.get(
        "/api/v1/refs/knowledge:expense-approval-policy", headers=kim
    ).status_code == 200

    # And a persona that reads past itself still gets the list it is for.
    for persona in ("naviant-support", "customer-admin"):
        headers = {"X-Cumulus-Persona": persona}
        assert client.get("/api/v1/assets", headers=headers).status_code == 200
        assert client.get("/api/v1/teams", headers=headers).status_code == 200


def test_tenant_and_platform_scope_agree_across_surfaces():
    """Three scopes, read as two different binaries, put `tenant` on opposite
    sides of the same question — the customer's own admin saw every expense
    row and none of the tasks about them."""
    reach = {
        p.scope: auth.Principal(uid=p.uid, email=p.email, role=p.authority,
                                scope=p.scope, queues=p.queues)
        for p in personas.ALL_PERSONAS
    }
    assert reach["platform"].reads_beyond_self
    assert reach["tenant"].reads_beyond_self
    assert not reach["own"].reads_beyond_self


def test_a_principal_on_no_persona_is_described_by_its_own_credential():
    """The fallback for "no persona" must not be the most privileged persona.

    `personas.get(...) or DEFAULT_PERSONA` labelled a signed-in Firebase user
    "Mitchell Suter, Director of Platform & Operations" — the wrong name, and
    the one at the top of the ladder.
    """
    stranger = auth.Principal(
        uid="fb_9f2c", email="jane@acme.test", role="user",
        persona_id="", scope="own", queues=(),
    )
    out = ui_api.session(stranger)
    assert out.persona_id == ""
    assert out.person == ""
    assert out.label == "jane@acme.test"
    assert personas.DEFAULT_PERSONA.person not in (out.person, out.label)


def test_impersonation_is_not_a_one_way_trip(monkeypatch):
    """Who may switch is a question about the operator, not about whoever they
    are watching as. Asking the impersonated principal made the switcher
    vanish after the first switch, with no route back."""
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    acting_as_kim = auth.principal_for(
        personas.CUSTOMER_USERS[2], by=personas.ENGINEER.uid
    )
    assert acting_as_kim.role == "user"
    assert ui_api.session(acting_as_kim).may_switch_persona


# ---- the cast this deployment runs ------------------------------------------
#
# The roster moved into `app/casts/` and `personas` became the selector over
# it. Everything above this line is the proof that the move changed no
# behaviour — not one assertion was touched — and everything below pins the
# naviant roster itself, so a future cast cannot quietly edit it. The
# selection, the Wick cast and the three new desks are in `test_casts.py`.


def test_this_deployment_runs_the_naviant_cast():
    """Unpinned is what the suite and a dev box run as, and it has to mean the
    roster the console was built against rather than an empty switcher."""
    from app import casts

    assert not settings.estate_namespace
    assert casts.active() is casts.CASTS["naviant"]
    assert personas.GROUPS is casts.CASTS["naviant"].GROUPS
    assert personas.Persona is casts.Persona  # re-exported, not redefined
    assert personas.OPERATOR_EMAIL == "masuter@naviant.com"


def test_the_naviant_roster_is_exactly_what_it_was():
    """A snapshot, because the roster decides who may decide a Task: a cast
    refactor that dropped a desk from one persona would otherwise show up as a
    silently narrower authorization somewhere else entirely."""
    assert {
        p.id: (p.person, p.email, p.authority, p.scope, p.queues)
        for p in personas.ALL_PERSONAS
    } == {
        "naviant-engineer": (
            "Mitchell Suter", "masuter@naviant.com", "owner", "platform",
            personas.ALL_QUEUES,
        ),
        "naviant-support": (
            "", "support@naviant.com", "user", "platform", (),
        ),
        "customer-admin": (
            "Nathan Pryce", "nathan.pryce@naviant.com", "admin", "tenant",
            ("knowledge-curation", "eval-review"),
        ),
        "customer-user:controller": (
            "Raymond Fitzgerald", "raymond.fitzgerald@naviant.com", "user",
            "own",
            ("ap-clerk", "expense-review", "vendor-stewardship",
             "data-stewardship", "order-desk"),
        ),
        "customer-user:ap": (
            "Connie Alberts", "connie.alberts@naviant.com", "user", "own",
            ("ap-clerk",),
        ),
        "customer-user:hr": (
            "Karen Boothe", "karen.boothe@naviant.com", "user", "own",
            ("hr",),
        ),
        "customer-user:ceo": (
            "Diane Vogel", "diane.vogel@naviant.com", "user", "own",
            ("expense-review", "expense-review-exec"),
        ),
        "customer-user:ae": (
            "John Smith", "john.smith@naviant.com", "user", "own", (),
        ),
    }
    # The uid convention every ledger row is attributed under.
    assert [p.uid for p in personas.CUSTOMER_USERS] == [
        "persona_controller", "persona_ap", "persona_hr", "persona_ceo",
        "persona_ae",
    ]


def test_a_naviant_email_still_resolves_to_its_persona_and_desks():
    assert personas.for_email("Connie.Alberts@Naviant.com ") is personas.get(
        "customer-user:ap"
    )
    assert personas.queues_for_email("connie.alberts@naviant.com") == (
        "ap-clerk",
    )
    assert personas.queues_for_email("john.smith@naviant.com") == ()
    assert personas.for_email("nobody@naviant.com") is None
    # Nobody from another cast is reachable from this deployment.
    assert personas.for_email("abby.wick@wickbuildings.com") is None
    assert personas.queues_for_email("abby.wick@wickbuildings.com") == ()
