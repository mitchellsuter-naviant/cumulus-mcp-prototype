"""Workflows: the registry, its telemetry contract, and the test harness.

Telemetry is synthesized deterministically from each definition's seed
profile, so the interesting assertions are about the *contract*: rollups agree
with their series, journals agree with their summaries, and the governance
gates (admin writes, human-only management, no deleting active definitions)
hold.
"""

import time

import pytest

import app.deps as deps
from app.workflows import (
    InMemoryGoldenSetDirectory,
    InMemoryTestRunDirectory,
    InMemoryWorkflowDirectory,
    WorkflowService,
    seed_workflows_if_empty,
)


@pytest.fixture()
def client(_client):
    deps._workflow_service = WorkflowService(
        InMemoryWorkflowDirectory(),
        InMemoryGoldenSetDirectory(),
        InMemoryTestRunDirectory(),
        test_seconds_per_case=0.001,
    )
    seed_workflows_if_empty(deps._workflow_service)
    return _client


@pytest.fixture()
def owner(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


# ---- registry ----


def test_seeded_fleet_lists(client):
    rows = client.get("/api/v1/workflows").json()
    ids = {r["id"] for r in rows}
    assert "invoice-intake" in ids and "vendor-master-sync" in ids
    kinds = {r["kind"] for r in rows}
    assert kinds == {"workflow", "dataflow"}
    # Sorted by 30-day spend, descending — the list doubles as the cost report.
    costs = [r["cost_30d_usd"] for r in rows]
    assert costs == sorted(costs, reverse=True)


def test_list_filters(client):
    dataflows = client.get("/api/v1/workflows", params={"kind": "dataflow"}).json()
    assert dataflows and all(r["kind"] == "dataflow" for r in dataflows)
    hits = client.get("/api/v1/workflows", params={"q": "invoice"}).json()
    assert {r["id"] for r in hits} == {"invoice-intake"}
    none = client.get("/api/v1/workflows", params={"team": "no-such-team"}).json()
    assert none == []


def test_detail_carries_declarative_steps(client):
    detail = client.get("/api/v1/workflows/invoice-intake").json()
    types = [s["type"] for s in detail["steps"]]
    assert types[0] == "trigger" and "modelcall" in types and "action" in types
    # The transparency bar: every step carries a plain-language summary.
    assert all(s["summary"] for s in detail["steps"])
    assert detail["golden_cases"] > 0
    assert client.get("/api/v1/workflows/nope").status_code == 404


def test_register_update_delete_lifecycle(client, owner):
    payload = {
        "id": "ap-batch-close",
        "name": "AP batch close",
        "kind": "workflow",
        "description": "Nightly close over approved invoices.",
        "team": "finance-ops",
        "roi_minutes_per_run": 3,
    }
    created = client.post("/api/v1/workflows", json=payload, headers=owner)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "draft"
    # Duplicate ids are refused.
    assert client.post("/api/v1/workflows", json=payload, headers=owner).status_code == 409

    updated = client.patch(
        "/api/v1/workflows/ap-batch-close",
        json={"status": "active", "roi_minutes_per_run": 4},
        headers=owner,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "active"
    assert updated.json()["roi_minutes_per_run"] == 4

    # Active definitions are map assets — deprecate, never erase.
    refused = client.delete("/api/v1/workflows/ap-batch-close", headers=owner)
    assert refused.status_code == 409
    client.patch(
        "/api/v1/workflows/ap-batch-close", json={"status": "deprecated"}, headers=owner
    )
    assert client.delete("/api/v1/workflows/ap-batch-close", headers=owner).status_code == 204
    assert (
        client.get("/api/v1/workflows/ap-batch-close", headers=owner).status_code == 404
    )


def test_writes_require_admin_role(client, monkeypatch):
    from app.auth import Principal, get_principal
    from app.main import app

    app.dependency_overrides[get_principal] = lambda: Principal(
        uid="u1", email="user@example.com", role="user"
    )
    try:
        resp = client.post("/api/v1/workflows", json={"id": "x-flow"})
        assert resp.status_code == 403
        assert client.patch(
            "/api/v1/workflows/invoice-intake", json={"status": "paused"}
        ).status_code == 403
    finally:
        app.dependency_overrides.pop(get_principal, None)


# ---- telemetry contract ----


def test_fleet_stats_agree_with_series(client):
    stats = client.get("/api/v1/workflows/stats", params={"days": 30}).json()
    assert stats["definitions"] == 6
    assert stats["runs"] == sum(day["runs"] for day in stats["series"])
    assert len(stats["series"]) == 30
    assert stats["model_spend_usd"] > 0
    assert 0 < stats["success_rate"] <= 1
    # ROI is derived from governed per-definition assumptions.
    assert stats["hours_returned"] > 0 and stats["roi_usd"] > 0


def test_usage_rollup_matches_series_and_is_deterministic(client):
    first = client.get("/api/v1/workflows/invoice-intake/usage", params={"days": 14}).json()
    again = client.get("/api/v1/workflows/invoice-intake/usage", params={"days": 14}).json()
    assert first == again  # synthesized, but stable
    assert first["runs"] == sum(d["runs"] for d in first["series"])
    assert first["completed"] <= first["runs"]
    assert first["tokens"] > 0


def test_run_journal_matches_summary(client):
    runs = client.get("/api/v1/workflows/invoice-intake/runs", params={"limit": 10}).json()
    assert runs
    run = runs[0]
    detail = client.get(f"/api/v1/workflows/invoice-intake/runs/{run['id']}").json()
    assert detail["id"] == run["id"]
    assert detail["status"] == run["status"]
    # Every ModelCall in the journal is an audited record with model + tokens.
    assert len(detail["calls"]) >= 1
    for call in detail["calls"]:
        assert call["model"] and call["input_tokens"] > 0 and call["cost_usd"] > 0
    step_types = {s["type"] for s in detail["steps"]}
    assert "trigger" in step_types and "modelcall" in step_types
    assert (
        client.get("/api/v1/workflows/invoice-intake/runs/run_000000").status_code == 404
    )


def test_audit_trail_names_actors_and_ledger(client):
    entries = client.get("/api/v1/workflows/invoice-intake/audit").json()
    assert entries
    kinds = {e["kind"] for e in entries}
    assert "action" in kinds  # gateway commits always appear
    for entry in entries:
        assert entry["actor"] and entry["summary"]
    actions = [e for e in entries if e["kind"] == "action"]
    assert all(e["ledger_ref"] for e in actions)


# ---- golden set + harness ----


def test_golden_set_add_and_remove(client, owner):
    before = client.get(
        "/api/v1/workflows/invoice-intake/golden-set", headers=owner
    ).json()
    added = client.post(
        "/api/v1/workflows/invoice-intake/golden-set",
        json={"name": "Zero-amount invoice", "expected": "rejected · no Action"},
        headers=owner,
    )
    assert added.status_code == 201
    cases = added.json()
    assert len(cases) == len(before) + 1
    new_case = cases[-1]
    assert new_case["source"] == "human-authored"

    removed = client.delete(
        f"/api/v1/workflows/invoice-intake/golden-set/{new_case['id']}", headers=owner
    )
    assert removed.status_code == 200
    assert len(removed.json()) == len(before)
    assert (
        client.delete(
            "/api/v1/workflows/invoice-intake/golden-set/gc-does-not-exist",
            headers=owner,
        ).status_code
        == 404
    )


def test_test_run_executes_against_golden_set(client, owner):
    launched = client.post(
        "/api/v1/workflows/invoice-intake/test-runs",
        json={"candidate_version": "v15-draft"},
        headers=owner,
    )
    assert launched.status_code == 202, launched.text
    run = launched.json()
    assert run["writes_suppressed"] is True
    assert run["total_cases"] > 0

    # The simulated harness runs case-by-case in the background.
    for _ in range(100):
        run = client.get(
            f"/api/v1/workflows/invoice-intake/test-runs/{run['id']}", headers=owner
        ).json()
        if run["status"] == "completed":
            break
        time.sleep(0.05)
    assert run["status"] == "completed"
    assert len(run["results"]) == run["total_cases"]
    assert run["passed"] == sum(1 for r in run["results"] if r["passed"])
    assert run["cost_usd"] > 0

    listed = client.get(
        "/api/v1/workflows/invoice-intake/test-runs", headers=owner
    ).json()
    assert any(r["id"] == run["id"] for r in listed)

    # Same candidate, same golden set -> same verdicts (evals must reproduce).
    rerun = client.post(
        "/api/v1/workflows/invoice-intake/test-runs",
        json={"candidate_version": "v15-draft"},
        headers=owner,
    ).json()
    for _ in range(100):
        rerun = client.get(
            f"/api/v1/workflows/invoice-intake/test-runs/{rerun['id']}", headers=owner
        ).json()
        if rerun["status"] == "completed":
            break
        time.sleep(0.05)
    assert rerun["passed"] == run["passed"]


def test_test_run_refused_without_golden_set(client, owner):
    # deal-brief is seeded with no golden cases.
    resp = client.post(
        "/api/v1/workflows/deal-brief/test-runs", json={}, headers=owner
    )
    assert resp.status_code == 409
    assert "golden set" in resp.json()["detail"].lower()
