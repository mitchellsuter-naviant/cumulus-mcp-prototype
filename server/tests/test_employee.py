"""The Employee Hub: profile, governed PTO, and the grounded HR agent.

The hub is a personal surface, so the question underneath every test here is
"who is me". The answer is the signed-in email, because that is the natural
key the graph holds an `Employee` under; anything else resolves to nobody.
"""

import pytest

import app.deps as deps
from app import personas
from app.config import settings
from app.employee import (
    EmployeeService,
    InMemoryEmployeeDirectory,
    seed_employee_if_empty,
)
from app.tasks import seed_tasks_if_empty
from app.workforce import seed_workforce_if_empty
from tests import world as test_world


@pytest.fixture()
def client(_client):
    world = test_world.fresh()
    seed_workforce_if_empty(world.sync, world.graph)
    seed_tasks_if_empty(world.tasks)
    deps._employee_service = EmployeeService(
        InMemoryEmployeeDirectory(),
        world.knowledge,
        world.tasks,
        world.graph,
        answer_backend=None,
    )
    seed_employee_if_empty(deps._employee_service)
    return _client


def _as(persona_id: str) -> dict[str, str]:
    return {"X-Cumulus-Persona": persona_id}


def test_the_profile_is_the_person_the_org_chart_knows(client):
    """Title, team and manager are read through from the graph, not defaulted.

    The hub used to mint a blank profile the first time anybody read it, so it
    showed a synthesized person with a balance nobody had accrued.
    """
    me = client.get("/api/v1/employee/me").json()
    assert me["email"] == personas.DEFAULT_PERSONA.email
    assert me["name"] == "Mitchell Suter"
    assert me["title"] == "Director, Platform & Operations"
    assert me["team"] == "Firm Leadership"
    assert me["manager"] == "Rowan Ellis"
    assert me["location"] == "Boston, MA"
    assert me["in_workforce"] is True

    # The hub's own state is still the hub's, and still stored.
    assert me["pto_balance_days"] == 17.5
    assert len(me["benefits"]) == 4
    kinds = {b["kind"] for b in me["benefits"]}
    assert "medical" in kinds and "retirement" in kinds


def test_the_persona_decides_whose_hub_it_is(client):
    me = client.get("/api/v1/employee/me", headers=_as("customer-user:kim")).json()
    assert me["email"] == "kim@customer.example"
    assert me["name"] == "Kim Reyes"
    assert me["title"] == "Senior Data Engineer"
    assert me["team"] == "Data Engineering"
    assert me["manager"] == "Priya Raghavan"


def test_a_principal_outside_the_workforce_is_told_so(client):
    """Naviant Support is not the customer's employee. The hub renders and
    says that, rather than inventing a colleague to fill the screen."""
    me = client.get("/api/v1/employee/me", headers=_as("naviant-support")).json()
    assert me["email"] == personas.SUPPORT.email
    assert me["in_workforce"] is False
    assert "not in the workforce" in me["workforce_note"]
    assert me["name"] == "" and me["benefits"] == []
    assert me["pto_balance_days"] == 0


def test_short_pto_auto_approves_within_governed_window(client):
    resp = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-09-14", "end_date": "2026-09-16", "days": 2,
              "note": "long weekend"},
    )
    assert resp.status_code == 201
    request = resp.json()
    assert request["state"] == "approved"
    assert "auto-approval window" in request["reason"]
    me = client.get("/api/v1/employee/me").json()
    assert me["pto_balance_days"] == 15.5  # deducted immediately


def test_long_pto_routes_to_hr_and_decision_resumes(client):
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-11-02", "end_date": "2026-11-10", "days": 6},
    ).json()
    assert request["state"] == "pending" and request["task_id"]
    task = client.get(f"/api/v1/tasks/{request['task_id']}").json()
    assert task["queue"] == "hr"
    assert any(r.startswith("knowledge:pto-policy@rev") for r in task["refs"])

    client.post(
        f"/api/v1/tasks/{task['id']}/decide",
        json={"verdict": "approved", "note": "Coverage confirmed."},
    )
    mine = client.get("/api/v1/employee/pto").json()
    decided = next(r for r in mine if r["id"] == request["id"])
    assert decided["state"] == "approved"
    me = client.get("/api/v1/employee/me").json()
    assert me["pto_balance_days"] == 11.5  # deducted on approval, not request


def test_pto_over_balance_is_declined(client):
    request = client.post(
        "/api/v1/employee/pto",
        json={"start_date": "2026-12-01", "end_date": "2026-12-31", "days": 25},
    ).json()
    assert request["state"] == "denied"
    assert "balance" in request["reason"]


def test_seeded_pending_request_pairs_with_seeded_task(client):
    """And belongs to the default persona, so it is visible in their hub."""
    mine = client.get("/api/v1/employee/pto").json()
    seeded = next(r for r in mine if r["id"] == "pto_seed_01")
    assert seeded["state"] == "pending"
    assert seeded["task_id"] == "task_pto_seed_01"
    assert seeded["email"] == personas.DEFAULT_PERSONA.email
    assert client.get("/api/v1/tasks/task_pto_seed_01").json()["queue"] == "hr"
    assert client.get(
        "/api/v1/employee/pto", headers=_as("customer-user:kim")
    ).json() == []


def test_hr_chat_grounds_on_facts_and_cites_policy(client):
    reply = client.post(
        "/api/v1/employee/hr-chat",
        json={"message": "How much PTO do I have and how do I take time off?"},
    ).json()
    assert "17.5" in reply["reply"]
    assert any(c["id"] == "pto-policy" for c in reply["citations"])
    assert reply["backend"] == "retrieval"  # honest about the degraded mode


def test_hr_chat_answers_benefits(client):
    reply = client.post(
        "/api/v1/employee/hr-chat", json={"message": "What is the 401k match?"}
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
