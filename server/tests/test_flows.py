"""Flows: the registry, its telemetry contract, and the test harness.

Telemetry is synthesized deterministically from each definition's seed
profile, so the interesting assertions are about the *contract*: rollups agree
with their series, journals agree with their summaries, and the governance
gates (admin writes, human-only management, no deleting active definitions)
hold.
"""

import time

import pytest

import app.deps as deps
from app.flows import (
    InMemoryGoldenSetDirectory,
    InMemoryTestRunDirectory,
    InMemoryFlowDirectory,
    FlowService,
    seed_flows_if_empty,
)


@pytest.fixture()
def client(_client):
    deps._flow_service = FlowService(
        InMemoryFlowDirectory(),
        InMemoryGoldenSetDirectory(),
        InMemoryTestRunDirectory(),
        test_seconds_per_case=0.001,
    )
    seed_flows_if_empty(deps._flow_service)
    return _client


@pytest.fixture()
def owner(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


# ---- registry ----


def test_seeded_fleet_lists(client):
    rows = client.get("/api/v1/flows").json()
    ids = {r["id"] for r in rows}
    assert "invoice-intake" in ids and "vendor-master-sync" in ids
    # One kind of authored logic: nothing in the payload types a flow.
    assert not any("kind" in r for r in rows)
    # Sorted by 30-day spend, descending — the list doubles as the cost report.
    costs = [r["cost_30d_usd"] for r in rows]
    assert costs == sorted(costs, reverse=True)


def test_list_filters(client):
    hits = client.get("/api/v1/flows", params={"q": "invoice"}).json()
    assert {r["id"] for r in hits} == {"invoice-intake"}
    active = client.get("/api/v1/flows", params={"status": "draft"}).json()
    assert active and all(r["status"] == "draft" for r in active)
    none = client.get("/api/v1/flows", params={"team": "no-such-team"}).json()
    assert none == []


def test_behaviour_is_derived_from_shape_not_a_type(client):
    """The retired Workflow/DataFlow split is replaced by derived signals.

    `vendor-master-sync` is woken by a timer and commits into the graph, so it
    is doing bulk work and resolves competing claims — and the code decides
    that by reading its trigger and its Action target, not a label.
    """
    from app.flows import is_windowed, writes_canonical

    service = deps._flow_service
    bulk = service.require("vendor-master-sync")
    per_case = service.require("invoice-intake")

    assert is_windowed(bulk) and not is_windowed(per_case)
    assert writes_canonical(bulk) and not writes_canonical(per_case)

    # And the audit trail follows the derived signal, not a type.
    entries = client.get("/api/v1/flows/vendor-master-sync/audit").json()
    assert any(e["kind"] == "resolution" for e in entries)
    others = client.get("/api/v1/flows/invoice-intake/audit").json()
    assert not any(e["kind"] == "resolution" for e in others)


def test_detail_carries_declarative_steps(client):
    detail = client.get("/api/v1/flows/invoice-intake").json()
    types = [s["type"] for s in detail["steps"]]
    assert types[0] == "trigger" and "modelcall" in types and "action" in types
    # The transparency bar: every step carries a plain-language summary.
    assert all(s["summary"] for s in detail["steps"])
    assert detail["golden_cases"] > 0
    assert client.get("/api/v1/flows/nope").status_code == 404


def test_register_update_delete_lifecycle(client, owner):
    payload = {
        "id": "ap-batch-close",
        "name": "AP batch close",
        "kind": "flow",
        "description": "Nightly close over approved invoices.",
        "team": "finance-ops",
        "roi_minutes_per_run": 3,
    }
    created = client.post("/api/v1/flows", json=payload, headers=owner)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "draft"
    # Duplicate ids are refused.
    assert client.post("/api/v1/flows", json=payload, headers=owner).status_code == 409

    updated = client.patch(
        "/api/v1/flows/ap-batch-close",
        json={"status": "active", "roi_minutes_per_run": 4},
        headers=owner,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "active"
    assert updated.json()["roi_minutes_per_run"] == 4

    # Active definitions are map assets — deprecate, never erase.
    refused = client.delete("/api/v1/flows/ap-batch-close", headers=owner)
    assert refused.status_code == 409
    client.patch(
        "/api/v1/flows/ap-batch-close", json={"status": "deprecated"}, headers=owner
    )
    assert client.delete("/api/v1/flows/ap-batch-close", headers=owner).status_code == 204
    assert (
        client.get("/api/v1/flows/ap-batch-close", headers=owner).status_code == 404
    )


def test_writes_require_admin_role(client, monkeypatch):
    from app.auth import Principal, get_principal
    from app.main import app

    app.dependency_overrides[get_principal] = lambda: Principal(
        uid="u1", email="user@example.com", role="user"
    )
    try:
        resp = client.post("/api/v1/flows", json={"id": "x-flow"})
        assert resp.status_code == 403
        assert client.patch(
            "/api/v1/flows/invoice-intake", json={"status": "paused"}
        ).status_code == 403
    finally:
        app.dependency_overrides.pop(get_principal, None)


# ---- telemetry contract ----


def test_fleet_stats_agree_with_series(client):
    stats = client.get("/api/v1/flows/stats", params={"days": 30}).json()
    assert stats["definitions"] == 6
    assert stats["runs"] == sum(day["runs"] for day in stats["series"])
    assert len(stats["series"]) == 30
    assert stats["model_spend_usd"] > 0
    assert 0 < stats["success_rate"] <= 1
    # ROI is derived from governed per-definition assumptions.
    assert stats["hours_returned"] > 0 and stats["roi_usd"] > 0


def test_usage_rollup_matches_series_and_is_deterministic(client):
    first = client.get("/api/v1/flows/invoice-intake/usage", params={"days": 14}).json()
    again = client.get("/api/v1/flows/invoice-intake/usage", params={"days": 14}).json()
    assert first == again  # synthesized, but stable
    assert first["runs"] == sum(d["runs"] for d in first["series"])
    assert first["completed"] <= first["runs"]
    assert first["tokens"] > 0


def test_run_journal_matches_summary(client):
    runs = client.get("/api/v1/flows/invoice-intake/runs", params={"limit": 10}).json()
    assert runs
    run = runs[0]
    detail = client.get(f"/api/v1/flows/invoice-intake/runs/{run['id']}").json()
    assert detail["id"] == run["id"]
    assert detail["status"] == run["status"]
    # Every ModelCall in the journal is an audited record with model + tokens.
    assert len(detail["calls"]) >= 1
    for call in detail["calls"]:
        assert call["model"] and call["input_tokens"] > 0 and call["cost_usd"] > 0
    step_types = {s["type"] for s in detail["steps"]}
    assert "trigger" in step_types and "modelcall" in step_types
    assert (
        client.get("/api/v1/flows/invoice-intake/runs/run_000000").status_code == 404
    )


def test_audit_trail_names_actors_and_ledger(client):
    entries = client.get("/api/v1/flows/invoice-intake/audit").json()
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
        "/api/v1/flows/invoice-intake/golden-set", headers=owner
    ).json()
    # The set names the repository file its seeded half came from, so the
    # console never implies it is the only source of golden cases.
    assert before["synced_from"].endswith("invoice-intake.json")
    assert before["synced_at"]

    added = client.post(
        "/api/v1/flows/invoice-intake/golden-set",
        json={"name": "Zero-amount invoice", "expected": "rejected · no Action"},
        headers=owner,
    )
    assert added.status_code == 201
    cases = added.json()["cases"]
    assert len(cases) == len(before["cases"]) + 1
    new_case = cases[-1]
    assert new_case["source"] == "human-authored"

    removed = client.delete(
        f"/api/v1/flows/invoice-intake/golden-set/{new_case['id']}", headers=owner
    )
    assert removed.status_code == 200
    assert len(removed.json()["cases"]) == len(before["cases"])
    assert (
        client.delete(
            "/api/v1/flows/invoice-intake/golden-set/gc-does-not-exist",
            headers=owner,
        ).status_code
        == 404
    )


def test_promoting_a_run_tags_the_case_and_keeps_the_pointer(client, owner):
    run = client.get(
        "/api/v1/flows/invoice-intake/runs", params={"limit": 1}, headers=owner
    ).json()[0]
    added = client.post(
        "/api/v1/flows/invoice-intake/golden-set",
        json={
            "name": f"Promoted {run['case_ref']}",
            "expected": "matches the recorded outcome",
            "run_id": run["id"],
            "payload": '{"case_ref": "%s"}' % run["case_ref"],
        },
        headers=owner,
    )
    assert added.status_code == 201
    case = added.json()["cases"][-1]
    # A promoted case is tagged as such and keeps a pointer back to the
    # execution that motivated it, so a failing eval is traceable.
    assert case["source"] == "promoted-from-run"
    assert case["run_id"] == run["id"]
    assert case["payload"]


# ---- versions ----


def test_version_history_and_draft(client, owner):
    versions = client.get(
        "/api/v1/flows/invoice-intake/versions", headers=owner
    ).json()
    assert [v["version"] for v in versions] == sorted(
        [v["version"] for v in versions], reverse=True
    )
    statuses = {v["status"] for v in versions}
    assert "active" in statuses and "archived" in statuses and "draft" in statuses
    assert sum(1 for v in versions if v["status"] == "active") == 1
    assert sum(1 for v in versions if v["status"] == "draft") == 1
    # A version is a full snapshot, never a delta.
    assert all(v["steps"] for v in versions)

    detail = client.get("/api/v1/flows/invoice-intake", headers=owner).json()
    assert detail["active_version_label"] == "v14"
    assert detail["draft_version_label"] == "v15-draft"
    assert detail["version_count"] == len(versions)

    assert (
        client.get(
            "/api/v1/flows/invoice-intake/versions/999", headers=owner
        ).status_code
        == 404
    )


def test_version_diff_marks_added_removed_and_modified(client, owner):
    # invoice-intake's seeded draft inserts a guard ahead of the model call.
    diff = client.get(
        "/api/v1/flows/invoice-intake/versions/15/diff", headers=owner
    ).json()
    by_id = {d["step"]["id"]: d for d in diff}
    assert by_id["x0"]["change"] == "added"
    # The trigger now points at the guard rather than the model call.
    assert by_id["t1"]["change"] == "modified"
    assert "next" in by_id["t1"]["changed_fields"]
    assert by_id["a1"]["change"] == "unchanged"

    # contract-review's draft drops the normalizer, which must still appear.
    ctr = client.get(
        "/api/v1/flows/contract-review/versions/7/diff", headers=owner
    ).json()
    removed = [d for d in ctr if d["change"] == "removed"]
    assert [d["step"]["id"] for d in removed] == ["x1"]
    modified = {d["step"]["id"] for d in ctr if d["change"] == "modified"}
    assert "m2" in modified  # cheaper second-opinion model

    # An archived version differs from the live one only in governed knowledge.
    older = client.get(
        "/api/v1/flows/invoice-intake/versions/13/diff", headers=owner
    ).json()
    changed = [d for d in older if d["change"] == "modified"]
    assert changed and all(d["changed_fields"] == ["config"] for d in changed)


# ---- recommendations ----


def test_recommendations_carry_evidence_and_apply_to_a_draft(client, owner):
    recs = client.get(
        "/api/v1/flows/invoice-intake/recommendations", headers=owner
    ).json()
    assert recs
    for rec in recs:
        assert rec["title"] and rec["detail"] and rec["evidence"]
    # Sorted by the money at stake.
    savings = [r["estimated_monthly_savings_usd"] for r in recs]
    assert savings == sorted(savings, reverse=True)

    downgrade = next((r for r in recs if r["kind"] == "model-downgrade"), None)
    assert downgrade is not None, recs
    assert downgrade["proposed_change"]["from"] == "claude-sonnet-5"
    assert downgrade["proposed_change"]["to"] == "claude-haiku-4-5"

    applied = client.post(
        f"/api/v1/flows/invoice-intake/recommendations/{downgrade['id']}/apply",
        headers=owner,
    )
    assert applied.status_code == 200, applied.text
    draft = applied.json()["draft"]
    # The change lands in a DRAFT, never in the live version.
    assert draft["status"] == "draft"
    step = next(
        s for s in draft["steps"] if s["id"] == downgrade["proposed_change"]["step_id"]
    )
    assert step["config"]["model"] == "claude-haiku-4-5"
    live = client.get("/api/v1/flows/invoice-intake", headers=owner).json()
    active_step = next(
        s for s in live["steps"] if s["id"] == downgrade["proposed_change"]["step_id"]
    )
    assert active_step["config"]["model"] == "claude-sonnet-5"


def test_downgrade_is_withheld_while_the_flow_is_failing(client, owner):
    """Spending less on something that is not working is not a saving.

    contract-review runs on the most expensive model in the fleet, so it is
    the obvious downgrade candidate on cost alone — but it completes ~93% of
    runs, below the bar, so the card is deliberately not offered.
    """
    recs = client.get(
        "/api/v1/flows/contract-review/recommendations", headers=owner
    ).json()
    assert recs  # other insights still surface
    assert not [r for r in recs if r["kind"] == "model-downgrade"]


def test_advisory_recommendation_cannot_be_applied(client, owner):
    recs = client.get(
        "/api/v1/flows/document-embedder/recommendations", headers=owner
    ).json()
    advisory = next((r for r in recs if r["proposed_change"] is None), None)
    assert advisory is not None, recs
    resp = client.post(
        f"/api/v1/flows/document-embedder/recommendations/{advisory['id']}/apply",
        headers=owner,
    )
    assert resp.status_code == 409
    assert "advisory" in resp.json()["detail"].lower()


def test_unknown_recommendation_is_404(client, owner):
    resp = client.post(
        "/api/v1/flows/invoice-intake/recommendations/nope/apply", headers=owner
    )
    assert resp.status_code == 404


def test_test_run_executes_against_golden_set(client, owner):
    launched = client.post(
        "/api/v1/flows/invoice-intake/test-runs",
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
            f"/api/v1/flows/invoice-intake/test-runs/{run['id']}", headers=owner
        ).json()
        if run["status"] == "completed":
            break
        time.sleep(0.05)
    assert run["status"] == "completed"
    assert len(run["results"]) == run["total_cases"]
    assert run["passed"] == sum(1 for r in run["results"] if r["passed"])
    assert run["cost_usd"] > 0

    listed = client.get(
        "/api/v1/flows/invoice-intake/test-runs", headers=owner
    ).json()
    assert any(r["id"] == run["id"] for r in listed)

    # Same candidate, same golden set -> same verdicts (evals must reproduce).
    rerun = client.post(
        "/api/v1/flows/invoice-intake/test-runs",
        json={"candidate_version": "v15-draft"},
        headers=owner,
    ).json()
    for _ in range(100):
        rerun = client.get(
            f"/api/v1/flows/invoice-intake/test-runs/{rerun['id']}", headers=owner
        ).json()
        if rerun["status"] == "completed":
            break
        time.sleep(0.05)
    assert rerun["passed"] == run["passed"]


def test_test_run_compares_candidate_against_baseline(client, owner):
    """Every case is graded twice, so the comparison is like-for-like."""
    launched = client.post(
        "/api/v1/flows/contract-review/test-runs",
        json={"candidate_version": "v7-draft", "baseline_version": "v6"},
        headers=owner,
    ).json()
    run = launched
    for _ in range(200):
        run = client.get(
            f"/api/v1/flows/contract-review/test-runs/{run['id']}", headers=owner
        ).json()
        if run["status"] == "completed":
            break
        time.sleep(0.05)
    assert run["status"] == "completed"
    for result in run["results"]:
        assert result["baseline_passed"] is not None
        assert result["baseline_cost_usd"] > 0
    assert run["baseline_passed"] == sum(
        1 for r in run["results"] if r["baseline_passed"]
    )
    assert run["cost_delta_usd"] == round(
        run["cost_usd"] - run["baseline_cost_usd"], 3
    )
    # A regression is a case the baseline passed and the candidate did not.
    assert run["regressions"] == sum(
        1 for r in run["results"] if r["baseline_passed"] and not r["passed"]
    )


def test_applied_downgrade_shows_up_as_a_cost_saving(client, owner):
    """The recommender loop has to close: apply, run, see the delta."""
    recs = client.get(
        "/api/v1/flows/invoice-intake/recommendations", headers=owner
    ).json()
    downgrade = next(r for r in recs if r["kind"] == "model-downgrade")
    client.post(
        f"/api/v1/flows/invoice-intake/recommendations/{downgrade['id']}/apply",
        headers=owner,
    )
    run = client.post(
        "/api/v1/flows/invoice-intake/test-runs", json={}, headers=owner
    ).json()
    # Launching with no explicit versions picks up the draft and the live one.
    assert run["candidate_version"] == "v15-draft"
    assert run["baseline_version"] == "v14"
    for _ in range(200):
        run = client.get(
            f"/api/v1/flows/invoice-intake/test-runs/{run['id']}", headers=owner
        ).json()
        if run["status"] == "completed":
            break
        time.sleep(0.05)
    assert run["status"] == "completed"
    # The draft names a cheaper model, so the candidate must cost less.
    assert run["cost_delta_usd"] < 0
    assert run["cost_usd"] < run["baseline_cost_usd"]


def test_test_run_refused_without_golden_set(client, owner):
    # deal-brief is seeded with no golden cases.
    resp = client.post(
        "/api/v1/flows/deal-brief/test-runs", json={}, headers=owner
    )
    assert resp.status_code == 409
    assert "golden set" in resp.json()["detail"].lower()
