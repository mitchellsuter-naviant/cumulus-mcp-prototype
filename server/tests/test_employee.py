"""The Employee Hub: profile, governed PTO, and the grounded HR agent."""

import pytest

import app.deps as deps
from app.config import settings
from app.employee import (
    EmployeeService,
    InMemoryEmployeeDirectory,
    seed_employee_if_empty,
)
from app.service import KnowledgeService, seed_if_empty
from app.storage import InMemoryRepository
from app.tasks import InMemoryTaskDirectory, TaskService, seed_tasks_if_empty


@pytest.fixture()
def client(_client):
    deps._service = KnowledgeService(InMemoryRepository())
    seed_if_empty(deps._service, settings.seed_dir)
    deps._task_service = TaskService(InMemoryTaskDirectory())
    seed_tasks_if_empty(deps._task_service)
    deps._employee_service = EmployeeService(
        InMemoryEmployeeDirectory(),
        deps._service,
        deps._task_service,
        answer_backend=None,
    )
    seed_employee_if_empty(deps._employee_service)
    return _client


def test_profile_is_created_on_first_read(client):
    me = client.get("/api/v1/employee/me").json()
    assert me["uid"] == "dev"
    assert me["pto_balance_days"] == 17.5
    assert len(me["benefits"]) == 4
    kinds = {b["kind"] for b in me["benefits"]}
    assert "medical" in kinds and "retirement" in kinds


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
    mine = client.get("/api/v1/employee/pto").json()
    seeded = next(r for r in mine if r["id"] == "pto_seed_01")
    assert seeded["state"] == "pending"
    assert seeded["task_id"] == "task_pto_seed_01"
    assert client.get("/api/v1/tasks/task_pto_seed_01").json()["queue"] == "hr"


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
