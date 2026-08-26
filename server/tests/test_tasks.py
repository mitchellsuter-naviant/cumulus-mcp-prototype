"""The Task Queue: seeding, listing order, stats, and the decide gate.

The doctrinal assertions live at the API boundary: deciding is a durable,
attributed, once-only write, and a minted API key can read the queue but
never decide — a key deciding a Task would be an agent approving its own
proposal.
"""

import pytest

import app.deps as deps
from app.api_keys import InMemoryApiKeyDirectory
from app.config import settings
from app.identities import IdentityService, InMemoryMachineIdentityDirectory
from app.tasks import InMemoryTaskDirectory, TaskService, seed_tasks_if_empty
from app.users import InMemoryUserDirectory


@pytest.fixture()
def client(_client):
    deps._task_service = TaskService(InMemoryTaskDirectory())
    seed_tasks_if_empty(deps._task_service)
    return _client


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


# ---- seeding and listing ----


def test_seed_is_idempotent(client):
    assert seed_tasks_if_empty(deps._task_service) == 0


def test_seeded_queue_lists_open_first_high_priority_first(client):
    rows = client.get("/api/v1/tasks").json()
    assert len(rows) >= 10
    statuses = [r["status"] for r in rows]
    # Open before done, no interleaving.
    assert statuses == sorted(statuses, key=lambda s: 0 if s == "open" else 1)
    open_rows = [r for r in rows if r["status"] == "open"]
    priorities = [r["priority"] for r in open_rows]
    assert priorities == sorted(priorities, key=lambda p: 0 if p == "high" else 1)
    # Within a priority band, oldest first — the fair order for shared work.
    normal = [r["created_at_iso"] for r in open_rows if r["priority"] == "normal"]
    assert normal == sorted(normal)


def test_filters(client):
    for params, check in [
        ({"queue": "data-steward"}, lambda r: r["queue"] == "data-steward"),
        ({"kind": "curation"}, lambda r: r["kind"] == "curation"),
        ({"status": "done"}, lambda r: r["status"] == "done"),
    ]:
        rows = client.get("/api/v1/tasks", params=params).json()
        assert rows and all(check(r) for r in rows)


def test_stats_count_only_open(client):
    stats = client.get("/api/v1/tasks/stats").json()
    open_rows = client.get("/api/v1/tasks", params={"status": "open"}).json()
    assert stats["open"] == len(open_rows)
    assert sum(stats["by_queue"].values()) == stats["open"]
    assert stats["high_priority"] == sum(
        1 for r in open_rows if r["priority"] == "high"
    )


def test_get_task_and_missing(client):
    task = client.get("/api/v1/tasks/task_mdm_0071").json()
    assert task["kind"] == "match_review"
    # The task carries its world: evidence, refs, and the proposed action.
    assert task["evidence"] and task["refs"] and task["proposal"]["target"] == "graph"
    assert client.get("/api/v1/tasks/task_nope").status_code == 404


# ---- deciding ----


def test_decide_records_verdict_actor_and_is_once_only(client):
    resp = client.post(
        "/api/v1/tasks/task_reimb_seed_08/decide",
        json={"verdict": "approved", "note": "Matches the PO."},
    )
    assert resp.status_code == 200
    decided = resp.json()
    assert decided["status"] == "done"
    assert decided["decision"]["verdict"] == "approved"
    assert decided["decision"]["decided_by"]  # attributed, always
    assert decided["decision"]["decided_at_iso"]
    # The decision is durable state, not a response-only echo.
    again = client.get("/api/v1/tasks/task_reimb_seed_08").json()
    assert again["decision"]["note"] == "Matches the PO."
    # A second decider learns the judgment already happened.
    resp = client.post(
        "/api/v1/tasks/task_reimb_seed_08/decide", json={"verdict": "rejected"}
    )
    assert resp.status_code == 409


def test_decide_reduces_open_count(client):
    before = client.get("/api/v1/tasks/stats").json()["open"]
    client.post(
        "/api/v1/tasks/task_cur_0210/decide",
        json={"verdict": "corrected", "correction": "Promote 4 of 5; hold one."},
    )
    after = client.get("/api/v1/tasks/stats").json()["open"]
    assert after == before - 1


def test_decide_missing_task_is_404(client):
    resp = client.post("/api/v1/tasks/task_nope/decide", json={"verdict": "approved"})
    assert resp.status_code == 404


# ---- the human-only gate ----


def _minted_key(client, owner, role="admin"):
    identity = client.post(
        "/api/v1/identities",
        json={"display_name": f"Task bot {role}", "role": role},
        headers=owner,
    ).json()
    minted = client.post(
        "/api/v1/api-keys",
        json={"name": "CI", "identity_uid": identity["uid"]},
        headers=owner,
    ).json()
    return {"X-API-Key": minted["token"]}


def test_minted_key_may_read_but_never_decide(client, owner):
    deps._user_directory = InMemoryUserDirectory()
    deps._api_key_directory = InMemoryApiKeyDirectory()
    deps._identity_service = IdentityService(
        deps._user_directory, InMemoryMachineIdentityDirectory()
    )
    key = _minted_key(client, owner)
    assert client.get("/api/v1/tasks", headers=key).status_code == 200
    assert client.get("/api/v1/tasks/stats", headers=key).status_code == 200
    resp = client.post(
        "/api/v1/tasks/task_payout_3307/decide",
        json={"verdict": "approved"},
        headers=key,
    )
    assert resp.status_code == 403
    # The refusal changed nothing.
    assert client.get("/api/v1/tasks/task_payout_3307", headers=key).json()[
        "status"
    ] == "open"


def test_unauthenticated_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    assert client.get("/api/v1/tasks").status_code == 401
    resp = client.post(
        "/api/v1/tasks/task_payout_3307/decide", json={"verdict": "approved"}
    )
    assert resp.status_code == 401
