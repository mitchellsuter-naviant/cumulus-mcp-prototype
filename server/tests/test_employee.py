"""The Employee Hub: profile, governed PTO, and the grounded HR agent.

The hub is a personal surface, so the question underneath every test here is
"who is me". The answer is the signed-in email, because that is the natural
key the graph holds an `Employee` under; anything else resolves to nobody.
"""

import pytest

import app.deps as deps
from app import personas
from app.config import settings
from app.employee import EmployeeService, InMemoryEmployeeDirectory
from tests import demo_world
from tests import world as test_world


@pytest.fixture()
def client(_client):
    world = test_world.fresh()
    demo_world.seed_workforce(world.sync, world.graph)
    demo_world.seed_tasks(world.tasks)
    # The hub holds a Dispatcher rather than an answer backend now: the HR
    # agent emits `HrQuestionAsked` and the `answer-question` Flow owns the
    # call. This world's flow has no generative backend behind it, so chat
    # still degrades to facts-plus-retrieval — and now leaves a run saying so.
    deps._employee_service = EmployeeService(
        InMemoryEmployeeDirectory(),
        world.knowledge,
        world.tasks,
        world.graph,
        dispatcher=world.dispatcher,
    )
    demo_world.seed_pto(deps._employee_service)
    return _client


def _as(persona_id: str) -> dict[str, str]:
    return {"X-Cumulus-Persona": persona_id}


#: Whose hub these run in. The default persona is the Naviant engineer, who is
#: deliberately not on the customer's org chart, so a personal surface has to
#: be asked for as somebody who is.
ME = _as("customer-user:ae")


def test_the_profile_is_the_person_the_org_chart_knows(client):
    """Title, team and manager are read through from the graph, not defaulted.

    The hub used to mint a blank profile the first time anybody read it, so it
    showed a synthesized person with a balance nobody had accrued.
    """
    me = client.get("/api/v1/employee/me", headers=ME).json()
    assert me["email"] == "john.smith@naviant.com"
    assert me["name"] == "John Smith"
    assert me["title"] == "Account Executive"
    assert me["team"] == "Sales"
    assert me["manager"] == "Marcus Reyes"
    assert me["location"] == "Madison, WI"
    assert me["in_workforce"] is True

    # The hub's own state is still the hub's, and still stored.
    assert me["pto_balance_days"] == 17.5
    assert len(me["benefits"]) == 4
    kinds = {b["kind"] for b in me["benefits"]}
    assert "medical" in kinds and "retirement" in kinds


def test_the_persona_decides_whose_hub_it_is(client):
    me = client.get(
        "/api/v1/employee/me", headers=_as("customer-user:controller")
    ).json()
    assert me["email"] == "raymond.fitzgerald@naviant.com"
    assert me["name"] == "Raymond Fitzgerald"
    assert me["title"] == "Controller"
    assert me["team"] == "Finance & Administration"
    assert me["manager"] == "Diane Vogel"


@pytest.mark.parametrize(
    "persona", (personas.SUPPORT, personas.ENGINEER), ids=lambda p: p.id
)
def test_a_principal_outside_the_workforce_is_told_so(client, persona):
    """Neither Naviant persona is the customer's employee — not the support
    engineer diagnosing a ticket, and not the engineer operating the
    deployment. The hub renders and says so, rather than inventing a colleague
    to fill the screen."""
    me = client.get("/api/v1/employee/me", headers=_as(persona.id)).json()
    assert me["email"] == persona.email
    assert me["in_workforce"] is False
    assert "not in the workforce" in me["workforce_note"]
    assert me["name"] == "" and me["benefits"] == []
    assert me["pto_balance_days"] == 0


def test_the_hub_is_served_the_window_it_calls_governed(client):
    """The console printed "requests of 3 working days or fewer auto-approve"
    from a literal `3` in Dart, beside the claim that the number was governed.

    Editing the policy asset moved the rule and not the sentence — the exact
    drift the whole governed-knowledge argument exists to prevent — so the
    parsed value and the revision-stamped ref it came from travel with the
    profile, and the rule the screen states is the rule the flow applies.
    """
    me = client.get("/api/v1/employee/me", headers=ME).json()
    assert me["pto_auto_approve_days"] == 3.0
    assert me["pto_policy_ref"].startswith("knowledge:pto-policy@rev")

    published = client.get("/api/v1/assets/pto-policy").json()
    edited = client.put(
        "/api/v1/assets/pto-policy",
        json={
            "content": published["content"].replace(
                "3 working days or fewer", "5 working days or fewer"
            ),
        },
    )
    assert edited.status_code == 200

    moved = client.get("/api/v1/employee/me", headers=ME).json()
    assert moved["pto_auto_approve_days"] == 5.0
    assert moved["pto_policy_ref"] != me["pto_policy_ref"]

    # And the flow decides on the same value the hub now prints.
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-09-14", "end_date": "2026-09-18", "days": 4,
              "note": "a week off"},
        headers=ME,
    ).json()
    assert request["state"] == "approved"
    assert "governed 5-day auto-approval window" in request["reason"]


def test_a_principal_outside_the_workforce_still_reads_the_governed_rule(
    client,
):
    """The window is a fact about the deployment, not about the reader — the
    hub says what the policy is even where it has nobody to apply it to."""
    me = client.get(
        "/api/v1/employee/me", headers=_as(personas.ENGINEER.id)
    ).json()
    assert me["in_workforce"] is False
    assert me["pto_auto_approve_days"] == 3.0
    assert me["pto_policy_ref"].startswith("knowledge:pto-policy@rev")


def test_short_pto_auto_approves_within_governed_window(client):
    resp = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-09-14", "end_date": "2026-09-16", "days": 2,
              "note": "long weekend"},
        headers=ME,
    )
    assert resp.status_code == 201
    request = resp.json()
    assert request["state"] == "approved"
    assert "auto-approval window" in request["reason"]
    me = client.get("/api/v1/employee/me", headers=ME).json()
    assert me["pto_balance_days"] == 15.5  # deducted immediately


def test_long_pto_routes_to_hr_and_decision_resumes(client):
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-11-02", "end_date": "2026-11-10", "days": 6},
        headers=ME,
    ).json()
    assert request["state"] == "pending" and request["task_id"]
    task = client.get(f"/api/v1/tasks/{request['task_id']}").json()
    assert task["queue"] == "hr"
    assert any(r.startswith("knowledge:pto-policy@rev") for r in task["refs"])

    client.post(
        f"/api/v1/tasks/{task['id']}/decide",
        json={"verdict": "approved", "note": "Coverage confirmed."},
    )
    mine = client.get("/api/v1/employee/pto", headers=ME).json()
    decided = next(r for r in mine if r["id"] == request["id"])
    assert decided["state"] == "approved"
    me = client.get("/api/v1/employee/me", headers=ME).json()
    assert me["pto_balance_days"] == 11.5  # deducted on approval, not request


def test_pto_over_balance_is_declined(client):
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-12-01", "end_date": "2026-12-31", "days": 25},
        headers=ME,
    ).json()
    assert request["state"] == "denied"
    assert "balance" in request["reason"]


def test_a_pending_request_pairs_with_the_task_that_holds_it(client):
    """And belongs to whoever asked, so it is visible in their hub and in
    nobody else's."""
    mine = client.get("/api/v1/employee/pto", headers=ME).json()
    seeded = next(r for r in mine if r["id"] == "pto_seed_01")
    assert seeded["state"] == "pending"
    assert seeded["task_id"] == "task_pto_seed_01"
    assert seeded["email"] == "john.smith@naviant.com"
    assert client.get("/api/v1/tasks/task_pto_seed_01").json()["queue"] == "hr"
    assert client.get(
        "/api/v1/employee/pto", headers=_as("customer-user:controller")
    ).json() == []


def test_hr_chat_grounds_on_facts_and_cites_policy(client):
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "How much PTO do I have and how do I take time off?"},
        headers=ME,
    ).json()
    assert "17.5" in reply["reply"]
    assert any(c["id"] == "pto-policy" for c in reply["citations"])
    assert reply["backend"] == "retrieval"  # honest about the degraded mode


def test_hr_chat_answers_benefits(client):
    reply = client.post(
        "/api/v1/employee/hr-chat", json={"message": "What is the 401k match?"},
        headers=ME,
    ).json()
    assert "4%" in reply["reply"]


def test_key_cannot_request_pto(client, monkeypatch):
    from app.api_keys import InMemoryApiKeyDirectory
    from app.identities import IdentityService, InMemoryMachineIdentityDirectory
    from app.users import InMemoryUserDirectory

    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    owner = {"X-API-Key": "root-sekret"}
    deps._user_directory = InMemoryUserDirectory()
    deps._api_key_directory = InMemoryApiKeyDirectory()
    deps._identity_service = IdentityService(
        deps._user_directory, InMemoryMachineIdentityDirectory()
    )
    identity = client.post(
        "/api/v1/identities",
        json={"display_name": "HR bot", "role": "user"},
        headers=owner,
    ).json()
    minted = client.post(
        "/api/v1/api-keys",
        json={"name": "CI", "identity_uid": identity["uid"]},
        headers=owner,
    ).json()
    key = {"X-API-Key": minted["token"]}
    resp = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-09-14", "end_date": "2026-09-15", "days": 1},
        headers=key,
    )
    assert resp.status_code == 403
    # The chat read path is fine for an ambient agent.
    assert (
        client.post(
            "/api/v1/employee/hr-chat", json={"message": "pto?"}, headers=key
        ).status_code
        == 200
    )
