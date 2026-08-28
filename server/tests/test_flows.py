"""Flows: the registry, its telemetry contract, and the test harness.

Telemetry is synthesized deterministically from each definition's seed
profile, so the interesting assertions are about the *contract*: rollups agree
with their series, journals agree with their summaries, and the governance
gates (admin writes, human-only management, no deleting active definitions)
hold.

The seeded fleet is two flows — expense-reimbursement (the workhorse most
behavioural tests exercise) and accounts-payable (a branching, match-and-screen
definition that keeps the fleet-level rollups honest). Shape-derived
behaviours neither seed exhibits (windowed triggering, canonical-graph writes)
are covered by synthetic definitions constructed inline, because the behaviour
belongs to the shape, not to any particular seed.
"""

import time
from datetime import timedelta

import pytest

import app.deps as deps
from app.flows import (
    FlowStep,
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
    assert {r["id"] for r in rows} == {"expense-reimbursement", "accounts-payable"}
    # One kind of authored logic: nothing in the payload types a flow.
    assert not any("kind" in r for r in rows)
    # Sorted by 30-day spend, descending — the list doubles as the cost report.
    costs = [r["cost_30d_usd"] for r in rows]
    assert costs == sorted(costs, reverse=True)
    assert all(c > 0 for c in costs)


def test_list_filters(client):
    hits = client.get("/api/v1/flows", params={"q": "receipt"}).json()
    assert {r["id"] for r in hits} == {"expense-reimbursement"}
    invoices = client.get("/api/v1/flows", params={"q": "invoice"}).json()
    assert {r["id"] for r in invoices} == {"accounts-payable"}
    active = client.get("/api/v1/flows", params={"status": "active"}).json()
    assert active and all(r["status"] == "active" for r in active)
    # Both seeds belong to finance-ops, so the team filter returns the fleet.
    team = client.get("/api/v1/flows", params={"team": "finance-ops"}).json()
    assert {r["id"] for r in team} == {"expense-reimbursement", "accounts-payable"}
    none = client.get("/api/v1/flows", params={"team": "no-such-team"}).json()
    assert none == []


def test_behaviour_is_derived_from_shape_not_a_type(client):
    """The retired Workflow/DataFlow split is replaced by derived signals.

    The seeded flow is woken by a business event and writes to an operational
    table, so it is per-case work: not windowed, no canonical-graph commits.
    A flow woken by a timer that commits into the graph is bulk work — and the
    code decides that by reading its trigger and its Action target, not a
    label, so a synthetic definition is enough to prove the signal.
    """
    from app.flows import FlowDefinition, FlowStep, is_windowed, writes_canonical

    service = deps._flow_service
    per_case = service.require("expense-reimbursement")
    assert not is_windowed(per_case)
    assert not writes_canonical(per_case)

    windowed = FlowDefinition(
        id="nightly-graph-sync",
        name="Nightly graph sync",
        steps=[
            FlowStep(
                id="t1", type="trigger", name="every-night",
                summary="windowing timer event", config={"event": "timer:24h"},
                next=["a1"],
            ),
            FlowStep(
                id="a1", type="action", name="upsert-canonical",
                summary="resolution writes into the canonical graph",
                config={"target": "graph"},
            ),
        ],
    )
    assert is_windowed(windowed) and writes_canonical(windowed)

    # And the audit trail follows the derived signal, not a type: a per-case
    # flow ledgers decisions and gateway commits, never claim resolutions.
    # Enough history that one busy morning of automatic commits cannot crowd
    # every human decision out of the window — the assertion is about which
    # kinds the ledger carries, not about the newest page.
    entries = client.get(
        "/api/v1/flows/expense-reimbursement/audit?limit=200"
    ).json()
    kinds = {e["kind"] for e in entries}
    assert "decision" in kinds and "action" in kinds
    assert "resolution" not in kinds


def test_detail_carries_declarative_steps(client):
    detail = client.get("/api/v1/flows/expense-reimbursement").json()
    types = [s["type"] for s in detail["steps"]]
    assert types[0] == "trigger" and "modelcall" in types and "action" in types
    # The transparency bar: every step carries a plain-language summary.
    assert all(s["summary"] for s in detail["steps"])
    assert detail["golden_cases"] > 0
    assert client.get("/api/v1/flows/nope").status_code == 404


def test_accounts_payable_export_declares_its_shape(client):
    """The Tier-2 export states the flow's real shape: one governed extraction
    pass, a resolve step that can branch to a human, and the retained
    three-way match — no step in the map that the engine does not run."""
    from app.flows import is_windowed, writes_canonical

    detail = client.get("/api/v1/flows/accounts-payable").json()
    by_id = {s["id"]: s for s in detail["steps"]}

    # One ModelCall reads the document. If a second ever appears here, the
    # simplification has quietly been undone.
    model_steps = [s for s in detail["steps"] if s["type"] == "modelcall"]
    assert [s["id"] for s in model_steps] == ["m1"]
    assert by_id["m1"]["name"] == "extract-invoice"

    # The resolver is its own step kind and its own branch: it either settles
    # the vendor or stops the run for a human, and nothing is asserted first.
    resolve = by_id["z1"]
    assert resolve["type"] == "resolve"
    assert resolve["next"] == ["x3", "k0"]
    assert resolve["branch_labels"] == ["resolved", "ambiguous"]
    assert by_id["k0"]["type"] == "task" and by_id["k0"]["config"]["queue"] == "ap-clerk"

    # The match and the screen are real checks now, not declared placeholders.
    assert by_id["c1"]["type"] == "check" and by_id["c2"]["type"] == "check"
    assert not [
        s for s in detail["steps"] if s["config"].get("status") == "placeholder"
    ]

    # Shape-derived signals: woken per invoice, writes to the AP ledger —
    # never the canonical graph, never on a window.
    defn = deps._flow_service.require("accounts-payable")
    assert not is_windowed(defn)
    assert not writes_canonical(defn)


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
            "/api/v1/flows/expense-reimbursement", json={"status": "paused"}
        ).status_code == 403
    finally:
        app.dependency_overrides.pop(get_principal, None)


# ---- telemetry contract ----


def test_fleet_stats_agree_with_series(client):
    stats = client.get("/api/v1/flows/stats", params={"days": 30}).json()
    assert stats["definitions"] == 2
    # The rollup sums across the whole fleet, and its series agrees with it.
    assert stats["runs"] == sum(day["runs"] for day in stats["series"])
    assert len(stats["series"]) == 30
    assert stats["model_spend_usd"] > 0
    assert 0 < stats["success_rate"] <= 1
    # ROI is derived from governed per-definition assumptions.
    assert stats["hours_returned"] > 0 and stats["roi_usd"] > 0
    # The fleet number is the sum of the per-flow rollups, nothing more.
    per_flow = [
        client.get(f"/api/v1/flows/{fid}/usage", params={"days": 30}).json()
        for fid in ("expense-reimbursement", "accounts-payable")
    ]
    assert stats["runs"] == sum(u["runs"] for u in per_flow)


def test_usage_rollup_matches_series_and_is_deterministic(client):
    first = client.get(
        "/api/v1/flows/expense-reimbursement/usage", params={"days": 14}
    ).json()
    again = client.get(
        "/api/v1/flows/expense-reimbursement/usage", params={"days": 14}
    ).json()
    assert first == again  # synthesized, but stable
    assert first["runs"] == sum(d["runs"] for d in first["series"])
    assert first["completed"] <= first["runs"]
    assert first["tokens"] > 0


def test_run_journal_matches_summary(client):
    runs = client.get(
        "/api/v1/flows/expense-reimbursement/runs", params={"limit": 10}
    ).json()
    assert runs
    run = runs[0]
    detail = client.get(
        f"/api/v1/flows/expense-reimbursement/runs/{run['id']}"
    ).json()
    assert detail["id"] == run["id"]
    assert detail["status"] == run["status"]
    # Every ModelCall in the journal is an audited record with model + tokens.
    assert len(detail["calls"]) >= 1
    for call in detail["calls"]:
        assert call["model"] and call["input_tokens"] > 0 and call["cost_usd"] > 0
    step_types = {s["type"] for s in detail["steps"]}
    assert "trigger" in step_types and "modelcall" in step_types
    assert (
        client.get(
            "/api/v1/flows/expense-reimbursement/runs/run_000000"
        ).status_code
        == 404
    )


def test_audit_trail_names_actors_and_ledger(client):
    entries = client.get("/api/v1/flows/expense-reimbursement/audit").json()
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
        "/api/v1/flows/expense-reimbursement/golden-set", headers=owner
    ).json()
    # The set names the repository file its seeded half came from, so the
    # console never implies it is the only source of golden cases.
    assert before["synced_from"].endswith("expense-reimbursement.json")
    assert before["synced_at"]

    added = client.post(
        "/api/v1/flows/expense-reimbursement/golden-set",
        json={"name": "Zero-total receipt", "expected": "rejected · no Action"},
        headers=owner,
    )
    assert added.status_code == 201
    cases = added.json()["cases"]
    assert len(cases) == len(before["cases"]) + 1
    new_case = cases[-1]
    assert new_case["source"] == "human-authored"

    removed = client.delete(
        f"/api/v1/flows/expense-reimbursement/golden-set/{new_case['id']}",
        headers=owner,
    )
    assert removed.status_code == 200
    assert len(removed.json()["cases"]) == len(before["cases"])
    assert (
        client.delete(
            "/api/v1/flows/expense-reimbursement/golden-set/gc-does-not-exist",
            headers=owner,
        ).status_code
        == 404
    )


def test_accounts_payable_golden_set_is_seeded_and_synced(client, owner):
    golden = client.get(
        "/api/v1/flows/accounts-payable/golden-set", headers=owner
    ).json()
    assert golden["synced_from"].endswith("accounts-payable.json")
    assert golden["synced_at"]
    # Six cases — deliberately under the coverage bar, like the expense seed.
    assert len(golden["cases"]) == 6
    by_ref = {c["input_ref"]: c for c in golden["cases"]}
    # The clean-scan fixture carries structured labels: facts on invoice.*
    # paths, a trajectory naming the terminal commit, and a pinned world.
    clean = by_ref["ap-fixture-001"]
    paths = {a["path"] for a in clean["assertions"]}
    assert {"invoice.total_usd", "invoice.match_verdict", "invoice.verdict"} <= paths
    assert all(a["path"].startswith("invoice.") for a in clean["assertions"])
    committed = next(
        t for t in clean["trajectory"] if t["kind"] == "action_committed"
    )
    assert committed["expected"] == "post-to-ap"
    refs = {r["ref"] for r in clean["read_set"]}
    assert "knowledge:entity-resolution-policy@rev1" in refs
    assert "knowledge:invoice-posting-policy@rev1" in refs
    # First contact: the resolver mints rather than matches, and a human
    # vouches for the vendor at the posting gate.
    first_contact = by_ref["ap-fixture-007"]
    assert {r["ref"] for r in first_contact["read_set"]} == {
        "knowledge:entity-resolution-policy@rev1",
        "knowledge:invoice-posting-policy@rev1",
    }
    assert any(
        a["path"] == "invoice.vendor_id" and a["expected"] == "vendor:meridian-supply"
        for a in first_contact["assertions"]
    )


def test_promoting_a_run_tags_the_case_and_keeps_the_pointer(client, owner):
    run = client.get(
        "/api/v1/flows/expense-reimbursement/runs",
        params={"limit": 1},
        headers=owner,
    ).json()[0]
    added = client.post(
        "/api/v1/flows/expense-reimbursement/golden-set",
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
        "/api/v1/flows/expense-reimbursement/versions", headers=owner
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

    detail = client.get(
        "/api/v1/flows/expense-reimbursement", headers=owner
    ).json()
    assert detail["active_version_label"] == "v9"
    assert detail["draft_version_label"] == "v10-draft"
    assert detail["version_count"] == len(versions)

    assert (
        client.get(
            "/api/v1/flows/expense-reimbursement/versions/999", headers=owner
        ).status_code
        == 404
    )


def test_a_newer_definition_in_code_replaces_a_stored_one(client):
    """The bug this closes: a definition's steps live only in the seed, and
    the seed used to run once. An environment that already held a flow kept
    serving its old canvas forever — the console drawing a map the engine had
    stopped running. A higher declared version now republishes."""
    from app.flows import seed_flows_if_empty

    service = deps._flow_service
    stored = service.require("accounts-payable")
    # Wind the stored definition back to a plausible earlier revision.
    service.update(stored.model_copy(update={
        "version": stored.version - 1,
        "steps": [
            FlowStep(id="t1", type="trigger", name="InvoiceReceived",
                     summary="one run per inbound invoice", next=["m0"]),
            FlowStep(id="m0", type="modelcall", name="classify-vendor",
                     summary="the retired classifier pass"),
        ],
        "versions": [],
    }))
    assert [s.id for s in service.require("accounts-payable").steps] == ["t1", "m0"]

    seed_flows_if_empty(service)

    upgraded = service.require("accounts-payable")
    assert upgraded.version == stored.version
    assert "m0" not in {s.id for s in upgraded.steps}
    assert [s.id for s in upgraded.steps if s.type == "modelcall"] == ["m1"]
    # The revision that was live is archived rather than dropped, so the
    # version picker still opens it and the diff shows what changed.
    history = {v.version: v.status for v in upgraded.version_history()}
    assert history[stored.version] == "active"
    assert history[stored.version - 1] == "archived"
    old_version = upgraded.find_version(stored.version - 1)
    assert {s.id for s in old_version.steps} == {"t1", "m0"}


def test_an_equal_or_older_definition_leaves_the_stored_one_alone(client):
    """The version bump is the opt-in. Re-running the seed against a registry
    already at that version must not touch it — otherwise every restart would
    quietly rewrite history and a console edit would vanish."""
    from app.flows import seed_flows_if_empty

    service = deps._flow_service
    before = service.require("accounts-payable")
    seed_flows_if_empty(service)
    seed_flows_if_empty(service)
    after = service.require("accounts-payable")
    assert after.version == before.version
    assert after.updated_at == before.updated_at
    assert [v.version for v in after.version_history()] == [
        v.version for v in before.version_history()
    ]


def test_an_upgrade_keeps_a_golden_set_a_human_has_edited(client, owner):
    """A re-sync must not discard curation. An untouched set is republished
    from code; one someone has added a case to is left exactly as it is."""
    from app.flows import seed_flows_if_empty

    service = deps._flow_service
    created = client.post(
        "/api/v1/flows/accounts-payable/golden-set",
        json={"name": "A case a human wrote", "expected": "posts"},
        headers=owner,
    )
    assert created.status_code == 201, created.text

    stored = service.require("accounts-payable")
    service.update(stored.model_copy(update={"version": stored.version - 1}))
    seed_flows_if_empty(service)

    golden = service.golden_set("accounts-payable")
    assert any(c.name == "A case a human wrote" for c in golden.cases)


def test_accounts_payable_history_has_no_open_draft(client, owner):
    """A draft is an open piece of work, not furniture every flow ships with:
    accounts-payable seeds two archived revisions and the active one, only."""
    versions = client.get(
        "/api/v1/flows/accounts-payable/versions", headers=owner
    ).json()
    assert [(v["version"], v["status"]) for v in versions] == [
        (6, "active"), (5, "archived"), (4, "archived")
    ]
    # Full snapshots here too — every revision carries the whole map.
    assert all(v["steps"] for v in versions)

    detail = client.get("/api/v1/flows/accounts-payable", headers=owner).json()
    assert detail["active_version_label"] == "v6"
    assert detail["draft_version_label"] == ""
    assert detail["version_count"] == 3


def test_version_diff_marks_added_removed_and_modified(client, owner):
    # The seeded draft inserts a duplicate guard ahead of the model call.
    diff = client.get(
        "/api/v1/flows/expense-reimbursement/versions/10/diff", headers=owner
    ).json()
    by_id = {d["step"]["id"]: d for d in diff}
    assert by_id["x0"]["change"] == "added"
    # The trigger now points at the guard rather than the model call.
    assert by_id["t1"]["change"] == "modified"
    assert "next" in by_id["t1"]["changed_fields"]
    assert by_id["a1"]["change"] == "unchanged"

    # An archived version differs from the live one only in governed knowledge.
    older = client.get(
        "/api/v1/flows/expense-reimbursement/versions/8/diff", headers=owner
    ).json()
    changed = [d for d in older if d["change"] == "modified"]
    assert changed and all(d["changed_fields"] == ["config"] for d in changed)


def test_diff_keeps_removed_steps_as_ghosts():
    """A draft that deletes a step must still show it — "what did this draft
    delete" is exactly the question a reviewer asks of the canvas."""
    from app.flows import FlowStep, FlowVersion, diff_versions

    trigger = FlowStep(
        id="t1", type="trigger", name="ReceiptSubmitted",
        config={"event": "ReceiptSubmitted"}, next=["x1"],
    )
    normalizer = FlowStep(
        id="x1", type="transform", name="normalize", next=["a1"],
    )
    action = FlowStep(id="a1", type="action", name="record")
    baseline = FlowVersion(version=1, steps=[trigger, normalizer, action])
    candidate = FlowVersion(
        version=2,
        steps=[trigger.model_copy(update={"next": ["a1"]}), action],
    )
    diff = diff_versions(candidate, baseline)
    removed = [d for d in diff if d.change == "removed"]
    # The ghost carries the *baseline* step, appended after the live ones.
    assert [d.step.id for d in removed] == ["x1"]
    assert diff[-1].change == "removed"


# ---- recommendations ----


def test_recommendations_carry_evidence_and_apply_to_a_draft(client, owner):
    recs = client.get(
        "/api/v1/flows/expense-reimbursement/recommendations", headers=owner
    ).json()
    assert recs
    for rec in recs:
        assert rec["title"] and rec["detail"] and rec["evidence"]
    # Sorted by the money at stake.
    savings = [r["estimated_monthly_savings_usd"] for r in recs]
    assert savings == sorted(savings, reverse=True)

    # With ~1,400 runs/30d at ~$0.14 on one claude-sonnet-5 call and a 99%+
    # success rate, the telemetry earns exactly these three cards: a one-rung
    # downgrade, a standing-prompt cache, and thin golden coverage (6 < 8).
    assert {r["kind"] for r in recs} == {
        "model-downgrade", "prompt-cache", "coverage"
    }

    downgrade = next(r for r in recs if r["kind"] == "model-downgrade")
    assert downgrade["proposed_change"]["from"] == "claude-sonnet-5"
    assert downgrade["proposed_change"]["to"] == "claude-haiku-4-5"

    applied = client.post(
        f"/api/v1/flows/expense-reimbursement/recommendations/{downgrade['id']}/apply",
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
    live = client.get("/api/v1/flows/expense-reimbursement", headers=owner).json()
    active_step = next(
        s for s in live["steps"] if s["id"] == downgrade["proposed_change"]["step_id"]
    )
    assert active_step["config"]["model"] == "claude-sonnet-5"


def test_accounts_payable_recommendations_follow_its_own_telemetry(client, owner):
    """Cards are derived per flow: accounts-payable has one ModelCall, so it
    earns a prompt cache on it, a one-rung downgrade on it (claude-sonnet-5
    has a cheaper rung), and the thin-coverage advisory (6 < 8). Spend per run
    sits under its $0.60 budget and the trigger is per-invoice, so neither the
    budget nor the batching card fires."""
    recs = client.get(
        "/api/v1/flows/accounts-payable/recommendations", headers=owner
    ).json()
    assert {r["kind"] for r in recs} == {
        "model-downgrade", "prompt-cache", "coverage"
    }
    downgrades = [r for r in recs if r["kind"] == "model-downgrade"]
    assert {r["step_id"] for r in downgrades} == {"m1"}
    for card in downgrades:
        assert card["proposed_change"]["from"] == "claude-sonnet-5"
        assert card["proposed_change"]["to"] == "claude-haiku-4-5"
    cache = next(r for r in recs if r["kind"] == "prompt-cache")
    assert cache["step_id"] == "m1"
    # Every card still carries its evidence, and money sorts the list.
    assert all(r["evidence"] for r in recs)
    savings = [r["estimated_monthly_savings_usd"] for r in recs]
    assert savings == sorted(savings, reverse=True)


def test_downgrade_is_withheld_while_the_flow_is_failing():
    """Spending less on something that is not working is not a saving.

    A flow on an expensive model is the obvious downgrade candidate on cost
    alone — but below the success bar the right move is to fix quality, so
    the card is deliberately not offered. The seeded flow succeeds, so the
    failing shape is constructed synthetically.
    """
    from app.flows import (
        FlowDefinition,
        FlowStep,
        SeedProfile,
        recommendations,
        usage_series,
    )
    from app.models import utcnow

    failing = FlowDefinition(
        id="counterparty-paper-review",
        name="Counterparty paper review",
        status="active",
        created_at=utcnow() - timedelta(days=90),
        profile=SeedProfile(
            base_runs=7, cost_per_run=2.40, tokens_per_run=48000,
            task_rate=0.42, escalate_rate=0.045, fail_rate=0.03,
            active_seconds=210,
        ),
        steps=[
            FlowStep(
                id="t1", type="trigger", name="PaperUploaded",
                config={"event": "PaperUploaded"}, next=["m1"],
            ),
            FlowStep(
                id="m1", type="modelcall", name="risk-review",
                config={"model": "claude-sonnet-5"}, next=["a1"],
            ),
            FlowStep(id="a1", type="action", name="file-summary",
                     config={"target": "deal-room"}),
        ],
    )
    usage = usage_series(failing, 30)
    runs = sum(d.runs for d in usage)
    completed = sum(d.completed for d in usage)
    assert completed / runs < 0.97  # genuinely below the bar
    recs = recommendations(failing, usage, golden_cases=6)
    assert recs  # other insights still surface
    assert not [r for r in recs if r.kind == "model-downgrade"]


def test_advisory_recommendation_cannot_be_applied(client, owner):
    recs = client.get(
        "/api/v1/flows/expense-reimbursement/recommendations", headers=owner
    ).json()
    # The coverage card is advisory: adding golden cases is judgment work,
    # not a mechanical edit the console can stage into a draft.
    advisory = next(r for r in recs if r["kind"] == "coverage")
    assert advisory["proposed_change"] is None
    resp = client.post(
        f"/api/v1/flows/expense-reimbursement/recommendations/{advisory['id']}/apply",
        headers=owner,
    )
    assert resp.status_code == 409
    assert "advisory" in resp.json()["detail"].lower()


def test_unknown_recommendation_is_404(client, owner):
    resp = client.post(
        "/api/v1/flows/expense-reimbursement/recommendations/nope/apply",
        headers=owner,
    )
    assert resp.status_code == 404


def test_test_run_executes_against_golden_set(client, owner):
    launched = client.post(
        "/api/v1/flows/expense-reimbursement/test-runs",
        json={"candidate_version": "v10-draft"},
        headers=owner,
    )
    assert launched.status_code == 202, launched.text
    run = launched.json()
    assert run["writes_suppressed"] is True
    assert run["total_cases"] > 0

    # The simulated harness runs case-by-case in the background.
    for _ in range(100):
        run = client.get(
            f"/api/v1/flows/expense-reimbursement/test-runs/{run['id']}",
            headers=owner,
        ).json()
        if run["status"] == "completed":
            break
        time.sleep(0.05)
    assert run["status"] == "completed"
    assert len(run["results"]) == run["total_cases"]
    assert run["passed"] == sum(1 for r in run["results"] if r["passed"])
    assert run["cost_usd"] > 0

    listed = client.get(
        "/api/v1/flows/expense-reimbursement/test-runs", headers=owner
    ).json()
    assert any(r["id"] == run["id"] for r in listed)

    # Same candidate, same golden set -> same verdicts (evals must reproduce).
    rerun = client.post(
        "/api/v1/flows/expense-reimbursement/test-runs",
        json={"candidate_version": "v10-draft"},
        headers=owner,
    ).json()
    for _ in range(100):
        rerun = client.get(
            f"/api/v1/flows/expense-reimbursement/test-runs/{rerun['id']}",
            headers=owner,
        ).json()
        if rerun["status"] == "completed":
            break
        time.sleep(0.05)
    assert rerun["passed"] == run["passed"]


def test_test_run_compares_candidate_against_baseline(client, owner):
    """Every case is graded twice, so the comparison is like-for-like."""
    launched = client.post(
        "/api/v1/flows/expense-reimbursement/test-runs",
        json={"candidate_version": "v10-draft", "baseline_version": "v9"},
        headers=owner,
    ).json()
    run = launched
    for _ in range(200):
        run = client.get(
            f"/api/v1/flows/expense-reimbursement/test-runs/{run['id']}",
            headers=owner,
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
        "/api/v1/flows/expense-reimbursement/recommendations", headers=owner
    ).json()
    downgrade = next(r for r in recs if r["kind"] == "model-downgrade")
    client.post(
        f"/api/v1/flows/expense-reimbursement/recommendations/{downgrade['id']}/apply",
        headers=owner,
    )
    run = client.post(
        "/api/v1/flows/expense-reimbursement/test-runs", json={}, headers=owner
    ).json()
    # Launching with no explicit versions picks up the draft and the live one.
    assert run["candidate_version"] == "v10-draft"
    assert run["baseline_version"] == "v9"
    for _ in range(200):
        run = client.get(
            f"/api/v1/flows/expense-reimbursement/test-runs/{run['id']}",
            headers=owner,
        ).json()
        if run["status"] == "completed":
            break
        time.sleep(0.05)
    assert run["status"] == "completed"
    # The draft names a cheaper model, so the candidate must cost less.
    assert run["cost_delta_usd"] < 0
    assert run["cost_usd"] < run["baseline_cost_usd"]


def test_test_run_refused_without_golden_set(client, owner):
    # A freshly registered definition has no golden cases yet.
    created = client.post(
        "/api/v1/flows",
        json={"id": "receipt-archiver", "name": "Receipt archiver"},
        headers=owner,
    )
    assert created.status_code == 201
    resp = client.post(
        "/api/v1/flows/receipt-archiver/test-runs", json={}, headers=owner
    )
    assert resp.status_code == 409
    assert "golden set" in resp.json()["detail"].lower()


# ---- flow contract + producer map ----


def test_contract_facts_and_derived_producer_map(client):
    contract = client.get("/api/v1/flows/expense-reimbursement/contract").json()
    paths = {f["path"] for f in contract["facts"]}
    assert "receipt.total_usd" in paths
    assert "reimbursement.verdict" in paths
    # The producer map is derived per version: extraction facts land on the
    # ModelCall, verdicts on the Rule, commits on the Action.
    pmap = contract["producer_map"]
    assert pmap["receipt.total_usd"] == "m1"
    assert pmap["reimbursement.verdict"] == "r1"
    assert pmap["reimbursement.recorded"] == "a1"

    # Projecting the same contract onto the draft (which inserts a transform
    # ahead of extraction) re-derives the map — no label moved.
    draft = client.get(
        "/api/v1/flows/expense-reimbursement/contract", params={"version": 10}
    ).json()
    assert draft["version_label"] == "v10-draft"
    assert draft["producer_map"]["receipt.total_usd"] == "m1"


def test_correction_sets_per_modelcall(client):
    sets = client.get(
        "/api/v1/flows/expense-reimbursement/correction-sets"
    ).json()
    # One ModelCall in the flow, so exactly one auto-accumulated set.
    assert [s["step_id"] for s in sets] == ["m1"]
    only = sets[0]
    assert only["step_name"] == "extract-receipt"
    assert only["model"] == "claude-sonnet-5"
    assert only["pairs"] > 0 and only["sample"]
    # Correction pairs key to contract fact paths, never to step internals.
    contract_paths = {
        "receipt.vendor", "receipt.date", "receipt.total_usd", "receipt.category"
    }
    assert {p["field"] for p in only["sample"]} <= contract_paths


# ---- dataset versioning ----


def test_golden_set_is_versioned_and_mutations_bump_it(client, owner):
    before = client.get(
        "/api/v1/flows/expense-reimbursement/golden-set", headers=owner
    ).json()
    assert before["version"] == 1
    assert before["revisions"][0]["note"].startswith("seeded")

    added = client.post(
        "/api/v1/flows/expense-reimbursement/golden-set",
        json={"name": "Zero-total receipt", "expected": "rejected"},
        headers=owner,
    ).json()
    assert added["version"] == 2
    assert added["revisions"][-1]["note"].startswith("added")

    case_id = added["cases"][-1]["id"]
    removed = client.delete(
        f"/api/v1/flows/expense-reimbursement/golden-set/{case_id}",
        headers=owner,
    ).json()
    assert removed["version"] == 3


def test_bulk_import_is_one_revision(client, owner):
    lines = "\n".join(
        [
            '{"name": "Batch case A", "expected": "approved",'
            ' "assertions": [{"path": "reimbursement.verdict", "expected": "approve"}]}',
            '{"name": "Batch case B", "expected": "elevated",'
            ' "trajectory": [{"kind": "task_raised", "expected": "true"}]}',
        ]
    )
    before = client.get(
        "/api/v1/flows/expense-reimbursement/golden-set", headers=owner
    ).json()
    out = client.post(
        "/api/v1/flows/expense-reimbursement/golden-set/import",
        json={"jsonl": lines},
        headers=owner,
    )
    assert out.status_code == 201, out.text
    golden = out.json()
    assert len(golden["cases"]) == len(before["cases"]) + 2
    assert golden["version"] == before["version"] + 1
    assert "imported 2 cases" in golden["revisions"][-1]["note"]
    # A malformed line names its line number.
    bad = client.post(
        "/api/v1/flows/expense-reimbursement/golden-set/import",
        json={"jsonl": '{"name": "ok"}\n{"nope": true}'},
        headers=owner,
    )
    assert bad.status_code == 422
    assert "Line 2" in bad.json()["detail"]


# ---- review screen raw material ----


def test_case_document_grounds_the_asserted_facts(client, owner):
    golden = client.get(
        "/api/v1/flows/expense-reimbursement/golden-set", headers=owner
    ).json()
    case = next(c for c in golden["cases"] if c["input_ref"] == "exp-fixture-001")
    doc = client.get(
        f"/api/v1/flows/expense-reimbursement/golden-set/{case['id']}/document",
        headers=owner,
    ).json()
    assert doc["kind"] == "expense"
    assert doc["lines"]
    region_paths = {r["path"] for r in doc["regions"]}
    assert "receipt.total_usd" in region_paths
    assert "receipt.vendor" in region_paths
    for region in doc["regions"]:
        x, y, w, h = region["box"]
        assert 0 <= x <= 1 and 0 <= y <= 1 and w > 0 and h > 0


def test_review_corrects_labels_and_bumps_the_dataset(client, owner):
    golden = client.get(
        "/api/v1/flows/expense-reimbursement/golden-set", headers=owner
    ).json()
    case = next(c for c in golden["cases"] if c["input_ref"] == "exp-fixture-001")
    out = client.post(
        f"/api/v1/flows/expense-reimbursement/golden-set/{case['id']}/review",
        json={"corrections": {"receipt.total_usd": "65.10"}},
        headers=owner,
    ).json()
    assert out["version"] == golden["version"] + 1
    reviewed = next(c for c in out["cases"] if c["id"] == case["id"])
    assert reviewed["reviewed_by"]
    fixed = next(
        a for a in reviewed["assertions"] if a["path"] == "receipt.total_usd"
    )
    assert fixed["expected"] == "65.10"


# ---- assertion-level grading ----


def _finish(client, owner, flow_id, run):
    for _ in range(200):
        run = client.get(
            f"/api/v1/flows/{flow_id}/test-runs/{run['id']}", headers=owner
        ).json()
        if run["status"] == "completed":
            return run
        time.sleep(0.05)
    raise AssertionError("test run never completed")


def test_experiment_localizes_failures_and_states_its_gate(client, owner):
    launched = client.post(
        "/api/v1/flows/expense-reimbursement/test-runs",
        json={"candidate_version": "v10-draft"},
        headers=owner,
    ).json()
    # The experiment pins the dataset revision it measures.
    assert launched["dataset_version"] == 1
    assert launched["reads"] == "pinned"
    run = _finish(client, owner, "expense-reimbursement", launched)

    # Every case grades assertion-by-assertion, each localized to the step
    # that owns the fact in the candidate's producer map.
    result = run["results"][0]
    assert result["assertions"]
    fact_steps = {
        a["step_id"] for a in result["assertions"] if a["kind"] == "fact"
    }
    assert fact_steps <= {"m1", "r1", "a1", ""}
    # Step aggregates exist for the heatmap, and the gate is stated.
    assert run["step_stats"]
    stat = next(s for s in run["step_stats"] if s["step_id"] == "m1")
    assert stat["passed"] + stat["failed"] > 0
    assert run["gate"] is not None
    expected_gate = run["regressions"] == 0 and run["passed"] >= run["baseline_passed"]
    assert run["gate"]["passed"] == expected_gate


def test_apply_recommendation_auto_launches_the_experiment(client, owner):
    recs = client.get(
        "/api/v1/flows/expense-reimbursement/recommendations", headers=owner
    ).json()
    downgrade = next(r for r in recs if r["kind"] == "model-downgrade")
    applied = client.post(
        f"/api/v1/flows/expense-reimbursement/recommendations/{downgrade['id']}/apply",
        headers=owner,
    ).json()
    # The loop closes: apply stages the draft AND measures it.
    assert applied["test_run_id"]
    run = client.get(
        f"/api/v1/flows/expense-reimbursement/test-runs/{applied['test_run_id']}",
        headers=owner,
    ).json()
    assert run["launched_for"] == downgrade["id"]
    assert run["candidate_version"] == applied["draft"]["label"]
    run = _finish(client, owner, "expense-reimbursement", run)
    # The draft names a cheaper model, so the measured delta is a saving.
    assert run["cost_delta_usd"] < 0


def test_promoting_a_run_captures_labels_and_the_pinned_world(client, owner):
    run = client.get(
        "/api/v1/flows/expense-reimbursement/runs",
        params={"limit": 1},
        headers=owner,
    ).json()[0]
    added = client.post(
        "/api/v1/flows/expense-reimbursement/golden-set",
        json={"name": f"Promoted {run['case_ref']}", "run_id": run["id"]},
        headers=owner,
    ).json()
    case = added["cases"][-1]
    assert case["source"] == "promoted-from-run"
    # Enriched from the journal: fact assertions against the contract, a
    # trajectory, and the reads the run made at their revisions.
    paths = {a["path"] for a in case["assertions"]}
    assert "receipt.total_usd" in paths
    assert any(t["kind"] == "task_raised" for t in case["trajectory"])
    assert case["read_set"] and "@" in case["read_set"][0]["ref"]
    assert case["payload"]
