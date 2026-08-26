"""The live expense-reimbursement runner and its table.

The doctrinal assertions: the rule's values come from the governed policy
document (editing it changes behavior with no code change), an elevated run
suspends into a real Task, deciding that Task resumes the run and commits
the state, and submission is a human act (keys refused).
"""

import pytest

import app.deps as deps
from app.config import settings
from app.reimbursements import (
    InMemoryReimbursementDirectory,
    ReimbursementService,
    StubReceiptReader,
    seed_reimbursements_if_empty,
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
    deps._reimbursement_service = ReimbursementService(
        InMemoryReimbursementDirectory(),
        StubReceiptReader(),
        deps._service,
        deps._task_service,
    )
    seed_reimbursements_if_empty(deps._reimbursement_service)
    return _client


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


# ---- the table ----


def test_seeded_table_and_stats_agree(client):
    rows = client.get("/api/v1/reimbursements").json()
    stats = client.get("/api/v1/reimbursements/stats").json()
    assert stats["total"] == len(rows) == 9
    assert stats["approved"] == sum(1 for r in rows if r["state"] == "approved")
    assert stats["pending"] == 2 and stats["denied"] == 1
    # Newest first.
    submitted = [r["submitted_at_iso"] for r in rows]
    assert submitted == sorted(submitted, reverse=True)


def test_state_filter_and_missing_row(client):
    pending = client.get("/api/v1/reimbursements", params={"state": "pending"}).json()
    assert {r["id"] for r in pending} == {"reimb_seed_08", "reimb_seed_09"}
    assert client.get("/api/v1/reimbursements/reimb_nope").status_code == 404


# ---- the run: auto-approval within governed policy ----


def test_in_policy_receipt_auto_approves(client):
    resp = client.post(
        "/api/v1/reimbursements",
        json={"filename": "lunch.png", "note": "total=42.10 category=meals vendor=corner-bistro"},
    )
    assert resp.status_code == 201
    row = resp.json()
    assert row["state"] == "approved"
    assert row["total_usd"] == 42.10 and row["category"] == "meals"
    assert row["task_id"] == ""
    # The journal traces the declared steps, ending in the gateway commit.
    steps = [e["step_id"] for e in row["journal"]]
    assert steps == ["t1", "m1", "x1", "r1", "a1", "e1"]
    assert "knowledge:expense-approval-policy" in row["journal"][2]["summary"]


def test_over_limit_suspends_into_a_task_and_decision_resumes(client):
    resp = client.post(
        "/api/v1/reimbursements",
        json={"note": "total=480.00 category=lodging vendor=harbor-hotel"},
    )
    row = resp.json()
    assert row["state"] == "pending"
    assert "exceeds the $200.00 auto-approval limit" in row["verdict_reason"]
    steps = [e["step_id"] for e in row["journal"]]
    assert steps == ["t1", "m1", "x1", "r1", "k1"]  # suspended at the Task

    # The Task is real, in the declared queue, carrying the policy ref.
    task = client.get(f"/api/v1/tasks/{row['task_id']}").json()
    assert task["queue"] == "expense-review" and task["case_ref"] == row["id"]
    # Revision-stamped: the ref pins the policy revision the run actually read.
    assert any(
        r.startswith("knowledge:expense-approval-policy@rev") for r in task["refs"]
    )
    # And the resolver dereferences it.
    resolved = client.get(f"/api/v1/refs/{task['refs'][0]}").json()
    assert resolved["resolved"] and resolved["asset"]["id"] == "expense-approval-policy"
    assert resolved["pinned_available"] is True

    # Deciding the task resumes the run: state commits, journal completes.
    decided = client.post(
        f"/api/v1/tasks/{row['task_id']}/decide",
        json={"verdict": "approved", "note": "Conference rate confirmed."},
    )
    assert decided.status_code == 200
    resumed = client.get(f"/api/v1/reimbursements/{row['id']}").json()
    assert resumed["state"] == "approved"
    assert resumed["decided_by"]
    assert [e["step_id"] for e in resumed["journal"]][-3:] == ["k1", "a1", "e1"]


def test_rejection_denies(client):
    row = client.post(
        "/api/v1/reimbursements",
        json={"note": "total=333.00 category=travel vendor=airline"},
    ).json()
    client.post(
        f"/api/v1/tasks/{row['task_id']}/decide",
        json={"verdict": "rejected", "note": "Personal leg of the trip."},
    )
    assert (
        client.get(f"/api/v1/reimbursements/{row['id']}").json()["state"] == "denied"
    )


def test_low_confidence_and_restricted_category_elevate(client):
    blurry = client.post(
        "/api/v1/reimbursements",
        json={"filename": "blurry-receipt.jpg", "note": "total=20.00 category=meals"},
    ).json()
    assert blurry["state"] == "pending"
    assert "confidence" in blurry["verdict_reason"]

    wine = client.post(
        "/api/v1/reimbursements",
        json={"note": "total=30.00 category=alcohol vendor=vintners-cellar"},
    ).json()
    assert wine["state"] == "pending"
    assert "always reviewed" in wine["verdict_reason"]


def test_editing_the_policy_changes_the_flow_without_code(client, owner):
    """The governed-knowledge demo: the rule reads the document at run time."""
    asset = client.get(
        "/api/v1/assets/expense-approval-policy", headers=owner
    ).json()
    tightened = asset["content"].replace(
        "Auto-approval limit: $200.00", "Auto-approval limit: $50.00"
    )
    assert tightened != asset["content"]
    resp = client.put(
        "/api/v1/assets/expense-approval-policy",
        json={"content": tightened},
        headers=owner,
    )
    assert resp.status_code == 200

    row = client.post(
        "/api/v1/reimbursements",
        json={"note": "total=64.20 category=meals vendor=corner-bistro"},
        headers=owner,
    ).json()
    assert row["state"] == "pending"
    assert "$50.00 auto-approval limit" in row["verdict_reason"]
    assert row["policy_limit_usd"] == 50.0


# ---- gates ----


def test_key_may_read_but_not_submit(client, owner):
    from app.api_keys import InMemoryApiKeyDirectory
    from app.identities import IdentityService, InMemoryMachineIdentityDirectory
    from app.users import InMemoryUserDirectory

    deps._user_directory = InMemoryUserDirectory()
    deps._api_key_directory = InMemoryApiKeyDirectory()
    deps._identity_service = IdentityService(
        deps._user_directory, InMemoryMachineIdentityDirectory()
    )
    identity = client.post(
        "/api/v1/identities",
        json={"display_name": "Reimb bot", "role": "admin"},
        headers=owner,
    ).json()
    minted = client.post(
        "/api/v1/api-keys",
        json={"name": "CI", "identity_uid": identity["uid"]},
        headers=owner,
    ).json()
    key = {"X-API-Key": minted["token"]}
    assert client.get("/api/v1/reimbursements", headers=key).status_code == 200
    resp = client.post(
        "/api/v1/reimbursements", json={"note": "total=10"}, headers=key
    )
    assert resp.status_code == 403


def test_unauthenticated_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    assert client.get("/api/v1/reimbursements").status_code == 401
