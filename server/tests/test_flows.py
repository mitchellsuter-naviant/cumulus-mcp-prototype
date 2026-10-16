"""Flows: the registry, its versions, and its telemetry contract.

Grading is not here — `test_evals.py` owns that. What is left is the registry:
definitions, the version history a candidate is staged into, and the rollups
the console reads.

Telemetry used to be synthesized from a per-definition seed profile, and these
tests asserted against those manufactured magnitudes — thousands of runs, a
99.5% success rate, dollars of spend. All of it is now read off stored runs,
so the assertions are about *agreement with the journals*: the fleet count is
the run count, a flow with no runs reports zeroes rather than a distribution,
and a $0.00 spend tile can say whether it means "nothing ran" or "nobody
recorded what it cost".

The fleet is every flow the engine holds and a definition for each of them:
two authored, fourteen inbound feeds that every one of them now carries a
declarative export for — down from the five with no map the workplan opened
with, and no undeclared feed left — the identity sweep, the one windowed flow
no source pushes, and `answer-question`, which brought the last ungoverned
ModelCall inside. The
registry still cannot be the list of what exists, and the tests below read the
fleet off the engine either way: a definition is a *map*, a run is the
territory, and the day the two disagree is the day this module has to be able
to say so.
"""

from datetime import timedelta

import pytest

import app.deps as deps
from app.flows import (
    FlowStep,
    InMemoryFlowDirectory,
    FlowService,
    seed_flows_if_empty,
)
from app.models import utcnow
from app.tasks.expense_reimbursement.domain import SubmittedDocument
from app.runs import RunRecord
from tests import demo_world
from tests import world as test_world

# The fourteen inbound feeds that write the graph — the eleven the estate's
# office apps and books arrive through, plus the three plant-floor feeds a
# manufacturer's namespace runs. With the two authored flows, ap-intake, and
# the sweeps and per-case flows below they are the twenty-one the fleet holds.
SYNC_FLOWS = {
    "vendor-sync", "purchase-order-sync", "receiving-sync",
    "workforce-sync", "calendar-sync", "crm-sync", "sales-sync",
    "mail-sync",
    "employment-event-sync", "dms-sync", "psa-sync",
    "job-sync", "credential-sync", "equipment-sync",
}
#: Every feed now carries its Tier-2 declarative export — the carried debt
#: reached zero, so the UNDECLARED_FEEDS set that tracked it is gone.
#: `identity-sweep` and `disagreement-sweep` are neither feeds nor authored
#: per-case flows: both are windowed like the feeds and read the book they
#: wrote — the first asking whether two rows are one party, the second asking
#: whether two systems agree about what was committed to — and both carry their
#: export like everything else.
#: `answer-question` is the odd one out in every direction:
#: it is the only flow here that writes nothing, and the only one whose runs
#: are started by a person typing a question rather than by a document
#: arriving. It is in the fleet because a ModelCall nobody counted is exactly
#: what the fleet exists to stop.
#: `order-release` is neither: it is the one per-case flow that writes to a
#: system Cumulus does not own, started by the approval of what the
#: disagreement sweep found.
#: `ap-intake` is a third shape again: windowed like a feed and woken by the
#: same delivery `dms-sync` is, but it reads the graph rather than a payload
#: and its output is per-case runs of another flow.
#: `ap-corrections` is the fifth shape: per-case like the AP flow, started by
#: a fact that flow emits when a clerk corrects an extraction, and writing
#: neither the graph nor a customer's system but a knowledge Dataset — the
#: correction set. It is in the fleet because the flywheel turning is
#: something an operator should be able to watch rather than infer.
#: `credential-tracking` is a sixth shape, and the only one a *clock* starts:
#: windowed like the sweeps, reading the book the feeds wrote, but woken by a
#: timer over a governed cadence rather than by anything arriving or by a pass
#: over the whole estate. Its export is here like everything else.
DECLARED_FLOWS = SYNC_FLOWS | {
    "expense-reimbursement", "accounts-payable", "ap-intake", "identity-sweep",
    "disagreement-sweep", "answer-question", "order-release",
    "ap-corrections", "credential-tracking", "field-documents",
    "plant-eligibility-sweep", "shipping-list-extraction",
    "department-scheduling",
}


@pytest.fixture()
def world():
    """A fresh world: nineteen registered flow bodies, no runs, no tasks."""
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    deps._flow_service = FlowService(InMemoryFlowDirectory())
    seed_flows_if_empty(deps._flow_service)
    return _client


@pytest.fixture()
def owner(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


def submit_expenses(
    world, count: int, hint: str = "total=42.00 category=meals",
    *, confirm: bool = True,
):
    """Real runs of the real flow — the only kind this module measures now.

    Confirmed by default, because a measure of *completed* runs needs runs that
    completed and every expense run now stops for its submitter. `confirm=False`
    is how this module makes an in-flight run on purpose.
    """
    for index in range(count):
        request = world.expenses.submit(
            "felicia.marks@naviant.com",
            documents=[SubmittedDocument(filename=f"r{index}.jpg", hint=hint)],
            commentary="Team lunch with the Acme delivery crew.",
        )
        if confirm:
            demo_world.confirm_expense(world.expenses, request)


def priced_runs(
    world,
    flow_id: str,
    count: int,
    *,
    cost_usd: float = 0.10,
    tokens: int = 5200,
    fail_every: int = 0,
) -> None:
    """Runs whose ModelCall step actually carries a price.

    The two real ModelCall sites pass a model and no cost, so nothing in the
    live world exercises the measured-spend path. Registering a body that
    records one is how these tests reach the branches that only fire when
    somebody wrote the number down.
    """

    def body(ctx, inputs):
        if fail_every and inputs["i"] % fail_every == 0:
            ctx.step("m1", "extract-receipt", "modelcall",
                     fn=lambda: (_ for _ in ()).throw(RuntimeError("reader down")),
                     model="gemini-3.7-flash", cost_usd=cost_usd, tokens=tokens)
        ctx.step("m1", "extract-receipt", "modelcall", fn=lambda: {"ok": True},
                 model="gemini-3.7-flash", cost_usd=cost_usd, tokens=tokens)
        return {}

    world.engine.register(flow_id, body)
    for index in range(count):
        world.engine.start(flow_id, {"i": index}, run_id=f"run_{flow_id}_{index}")


def probe_flow(client, owner, flow_id: str) -> None:
    """Register a one-ModelCall flow declared on the upper Gemini rung.

    The seeded flows run the cheapest rung, so the downgrade ladder has
    nothing to propose against them — which is correct, and useless for
    testing the ladder. A probe declared one rung up is how the machinery
    stays exercised without falsifying a seeded export.
    """
    payload = {
        "id": flow_id,
        "name": "Downgrade probe",
        "kind": "flow",
        "description": "A one-step flow on the upper Gemini rung.",
        "team": "finance-ops",
        "steps": [
            {"id": "t1", "type": "trigger", "name": "ProbeSubmitted",
             "summary": "one run per probe",
             "config": {"event": "ProbeSubmitted"}, "next": ["m1"]},
            {"id": "m1", "type": "modelcall", "name": "extract-receipt",
             "summary": "the priced step",
             "config": {"model": "gemini-3.7-flash"}, "next": []},
        ],
    }
    created = client.post("/api/v1/flows", json=payload, headers=owner)
    assert created.status_code == 201, created.text
    activated = client.patch(
        f"/api/v1/flows/{flow_id}", json={"status": "active"}, headers=owner
    )
    assert activated.status_code == 200, activated.text


# ---- registry ----


def test_the_window_is_the_callers_to_choose_and_the_row_says_which(client, world):
    """Every figure on a row is over a window, and the row states it.

    The fields used to be named `runs_30d`. The console can now ask for sixty
    or ninety days, and a field called `runs_30d` carrying ninety days of runs
    is worse than no field at all — so the window travels with the numbers.
    """
    month = client.get("/api/v1/flows").json()
    quarter = client.get("/api/v1/flows", params={"days": 90}).json()

    assert all(r["window_days"] == 30 for r in month)
    assert all(r["window_days"] == 90 for r in quarter)

    # A wider window can only find more runs, never fewer.
    by_id = {r["id"]: r for r in month}
    for row in quarter:
        assert row["runs_window"] >= by_id[row["id"]]["runs_window"]

    # And the window is bounded, so nobody can ask for a decade by accident.
    assert client.get("/api/v1/flows", params={"days": 0}).status_code == 422
    assert client.get("/api/v1/flows", params={"days": 900}).status_code == 422


def test_health_is_withheld_until_a_run_has_actually_finished(client, world):
    """`no_runs` is a real verdict, not a gap.

    A flow nobody has run has no success rate, and calling it HEALTHY would be
    a claim over an empty sample — the one thing a fleet dashboard must never
    do, because it is indistinguishable from a flow that is genuinely fine.
    """
    rows = {r["id"]: r for r in client.get("/api/v1/flows").json()}

    for row in rows.values():
        finished = row["success_window"] is not None
        if row["status"] == "draft":
            assert row["health"] == "draft"
        elif not finished:
            assert row["health"] == "no_runs"
        else:
            assert row["health"] in {"healthy", "degraded", "failed"}



def test_the_health_ladder_reads_off_finished_runs_only():
    """The thresholds themselves, stated once.

    Exercised directly rather than through a fixture: whether any seeded flow
    happens to be failing this week is not what this is checking.
    """
    from app.flows_api import _health

    # Lifecycle beats measurement.
    assert _health("draft", 100, 0) == "draft"
    # Nothing finished is not the same as nothing wrong.
    assert _health("active", 0, 0) == "no_runs"
    # The bar is 0.95, and the floor below it is 0.5.
    assert _health("active", 100, 0) == "healthy"
    assert _health("active", 95, 5) == "healthy"
    assert _health("active", 94, 6) == "degraded"
    assert _health("active", 6, 4) == "degraded"
    assert _health("active", 4, 6) == "failed"
    assert _health("active", 0, 3) == "failed"



def test_the_fleet_is_every_flow_that_runs_not_every_definition(client, world):
    """The list used to be the registry, so it showed two flows in a system
    that runs eighteen. Eleven inbound sync feeds write the graph, two
    windowed flows sweep the book, `ap-intake` turns a filing tree into cases,
    answering and the order release are the per-case pair, and all of them now
    have a `FlowDefinition` — but the list
    is still read off the engine, because the two agreeing today is a fact
    about today. A body registered with no map still has to appear, so one is
    registered here to prove the list is not quietly back to being the
    registry."""
    submit_expenses(world, 2)
    priced_runs(world, "probe-unmapped", 1)
    rows = client.get("/api/v1/flows").json()
    by_id = {r["id"]: r for r in rows}
    assert SYNC_FLOWS < set(by_id)
    assert {"expense-reimbursement", "accounts-payable"} < set(by_id)
    assert len(rows) == 28  # twenty-seven declared, plus the unmapped probe

    # A definition-less row says so, rather than borrowing a lifecycle state
    # it has no definition to hold.
    probe = by_id["probe-unmapped"]
    assert probe["has_definition"] is False
    assert probe["status"] == "undeclared"
    assert probe["version"] == 0
    # And the feed that used to be this test's example now has a map.
    assert by_id["receiving-sync"]["has_definition"] is True
    assert by_id["expense-reimbursement"]["has_definition"] is True

    # One kind of authored logic: nothing in the payload types a flow.
    assert not any("kind" in r for r in rows)
    # Busiest first — the flow that actually ran leads the list.
    assert rows[0]["id"] == "expense-reimbursement"
    assert rows[0]["runs_window"] == 2
    assert rows[0]["window_days"] == 30


def test_list_filters(client):
    hits = client.get("/api/v1/flows", params={"q": "receipt"}).json()
    assert {r["id"] for r in hits} == {"expense-reimbursement"}
    invoices = client.get("/api/v1/flows", params={"q": "invoice"}).json()
    # Three flows are about invoices now: the one that reads them, the one
    # that finds them, and the one that keeps what a clerk changed about one.
    assert {r["id"] for r in invoices} == {
        "accounts-payable", "ap-intake", "ap-corrections",
    }
    # Filtering by a definition's lifecycle state excludes what has no
    # definition to be in a state — an empty answer now, and the filter must
    # say so rather than 404 or fall back to everything.
    active = client.get("/api/v1/flows", params={"status": "active"}).json()
    assert {r["id"] for r in active} == DECLARED_FLOWS
    undeclared = client.get("/api/v1/flows", params={"status": "undeclared"}).json()
    assert undeclared == []
    team = client.get("/api/v1/flows", params={"team": "finance-ops"}).json()
    assert {r["id"] for r in team} == {"expense-reimbursement", "accounts-payable"}
    none = client.get("/api/v1/flows", params={"team": "no-such-team"}).json()
    assert none == []


def test_a_definition_less_flow_can_still_be_opened(client, world):
    """A row the fleet list shows must open. The flow has runs and no map,
    and saying that is better than a 404 or an invented definition.

    The example used to be `receiving-sync`, which has an export now. That the
    case no longer occurs in the shipped fleet is the point of the last four
    definitions; that the *behaviour* still has to hold is why this registers
    its own unmapped body rather than being deleted along with the debt.
    """
    priced_runs(world, "probe-unmapped", 1)
    detail = client.get("/api/v1/flows/probe-unmapped").json()
    assert detail["has_definition"] is False
    assert detail["steps"] == []
    assert detail["status"] == "undeclared"
    usage = client.get("/api/v1/flows/probe-unmapped/usage").json()
    assert usage["has_definition"] is False
    # Editing and versioning still need a definition — there is nothing there
    # to version, and inventing one would be the same lie in a new place.
    assert client.get("/api/v1/flows/probe-unmapped/versions").status_code == 404
    assert client.get("/api/v1/flows/no-such-flow").status_code == 404


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
    pass over the page, a second cheap call only for the fields in doubt, the
    retained three-way match, and exactly one place a person is asked — no step
    in the map that the engine does not run, and none it runs that is not in
    the map."""
    from app.flows import is_windowed, writes_canonical

    detail = client.get("/api/v1/flows/accounts-payable").json()
    by_id = {s["id"]: s for s in detail["steps"]}

    # Two ModelCalls, and the second is the narrow one: given the disputed
    # field paths and nothing else, where they are printed. It is skipped
    # entirely on an invoice with nothing in doubt, which its config says.
    model_steps = [s for s in detail["steps"] if s["type"] == "modelcall"]
    assert [s["id"] for s in model_steps] == ["m1", "m2"]
    assert by_id["m1"]["name"] == "extract-invoice"
    assert by_id["m2"]["config"]["skipped_when"] == "no field verdicts"

    # The resolver is its own step kind, and it no longer stops the run: an
    # unsettled vendor is carried into the single review, where the scored
    # candidates are put in front of the person already reading the page.
    resolve = by_id["z1"]
    assert resolve["type"] == "resolve"
    assert resolve["next"] == ["x2"]
    assert "k0" not in by_id

    # One human gate, and where it goes is governed rather than pinned.
    gates = [s for s in detail["steps"] if s["type"] == "task"]
    assert [s["id"] for s in gates] == ["k1"]
    assert gates[0]["config"]["routes_to"] == "submitter"
    assert gates[0]["config"]["fallback_queue"] == "ap-clerk"

    # The arithmetic and the match are real checks, not declared placeholders.
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


def test_the_fleet_run_count_is_the_real_run_count(client, world):
    """The number this whole stage exists for. The header said 2,748 with 27
    runs stored, because the count was drawn from a seed profile rather than
    counted. It is now `len(engine.list())` and nothing else."""
    submit_expenses(world, 5)
    stored = world.engine.list()
    stats = client.get("/api/v1/flows/stats", params={"days": 30}).json()

    assert stats["runs"] == len(stored) == 5
    assert stats["runs"] == sum(day["runs"] for day in stats["series"])
    assert len(stats["series"]) == 30
    # And the fleet is exactly the sum of its flows, feeds included.
    per_flow = [
        client.get(f"/api/v1/flows/{row['id']}/usage", params={"days": 30}).json()
        for row in client.get("/api/v1/flows").json()
    ]
    assert stats["runs"] == sum(u["runs"] for u in per_flow)
    assert stats["flows"] == 27
    assert stats["definitions"] == 27
    # The carried debt reached zero: every flow that runs carries its export.
    assert stats["without_definition"] == 0


def test_a_flow_with_no_runs_reports_zero_rather_than_a_series(client):
    """Nothing has run. The old rollup answered with a month of plausible
    daily traffic anyway; the honest answer is thirty zeroes."""
    usage = client.get(
        "/api/v1/flows/accounts-payable/usage", params={"days": 30}
    ).json()
    assert usage["runs"] == 0
    assert len(usage["series"]) == 30
    assert all(d["runs"] == 0 for d in usage["series"])
    assert usage["cost_usd"] == 0.0 and usage["tokens"] == 0
    # No finished runs is not a 0% success rate — it is no evidence.
    assert usage["success_rate"] is None
    assert usage["cost_basis"] == "no-model-calls"
    assert usage["roi_usd"] == 0.0 and usage["minutes_saved"] == 0.0

    stats = client.get("/api/v1/flows/stats").json()
    assert stats["runs"] == 0 and stats["success_rate"] is None
    assert stats["hours_returned"] == 0.0 and stats["roi_usd"] == 0.0


def test_runs_outside_the_window_are_not_folded_into_it(client, world):
    """A forty-day-old run is not this month's traffic. It drops out of a
    thirty-day window rather than piling onto the edge day, so the series
    always sums to the headline over the same window."""
    submit_expenses(world, 2)
    world.engine.store.put(RunRecord(
        id="run_old", flow_id="expense-reimbursement", status="succeeded",
        started_at_iso=(utcnow() - timedelta(days=40)).isoformat(),
    ))
    month = client.get(
        "/api/v1/flows/expense-reimbursement/usage", params={"days": 30}
    ).json()
    assert month["runs"] == 2
    assert month["runs"] == sum(d["runs"] for d in month["series"])
    year = client.get(
        "/api/v1/flows/expense-reimbursement/usage", params={"days": 60}
    ).json()
    assert year["runs"] == 3


def test_usage_rollup_matches_its_own_series(client, world):
    submit_expenses(world, 4)
    usage = client.get(
        "/api/v1/flows/expense-reimbursement/usage", params={"days": 14}
    ).json()
    assert usage["runs"] == sum(d["runs"] for d in usage["series"])
    assert usage["completed"] == sum(d["completed"] for d in usage["series"])
    assert usage["model_calls"] == sum(d["model_calls"] for d in usage["series"])
    # Every run is accounted for exactly once.
    assert usage["completed"] + usage["failed"] + usage["in_flight"] == usage["runs"]


def test_cost_distinguishes_unrecorded_spend_from_zero_spend(client, world):
    """The field this stage adds. A $0.00 tile has two readings that point in
    opposite directions, and the console has to be able to tell them apart."""
    submit_expenses(world, 3)
    expense = client.get(
        "/api/v1/flows/expense-reimbursement/usage", params={"days": 30}
    ).json()
    # Models ran. Nobody wrote down what they cost, so the zero is a hole.
    assert expense["model_calls"] > 0
    assert expense["priced_model_calls"] == 0
    assert expense["cost_usd"] == 0.0
    assert expense["cost_basis"] == "unrecorded"

    # A feed calls no model at all, so *its* zero is a fact about spending.
    feed = client.get("/api/v1/flows/vendor-sync/usage", params={"days": 30}).json()
    assert feed["model_calls"] == 0
    assert feed["cost_basis"] == "no-model-calls"

    # And a priced call reads as measured — the same field, a third answer.
    priced_runs(world, "accounts-payable", 3, cost_usd=0.25, tokens=900)
    measured = client.get(
        "/api/v1/flows/accounts-payable/usage", params={"days": 30}
    ).json()
    assert measured["cost_basis"] == "measured"
    assert measured["priced_model_calls"] == measured["model_calls"] == 3
    assert measured["cost_usd"] == 0.75 and measured["tokens"] == 2700


def test_roi_says_it_is_an_assumption_times_a_count(client, world):
    """The least honest thing on the page was a dollar ROI figure with no
    marking. The count under it is now real; the minutes over it never were,
    and the derivation travels with the number."""
    submit_expenses(world, 4)
    stats = client.get("/api/v1/flows/stats", params={"days": 30}).json()
    basis = stats["roi_basis"]

    completed = stats["completed"]
    assert basis["completed_runs"] == completed
    assert basis["modelled_runs"] == completed  # all from a defined flow
    assert basis["assumed_minutes_per_run"] == 6.0  # the definition's own value
    assert basis["hourly_rate_usd"] == 30.0
    assert "assumption, not a measurement" in basis["statement"]
    # And the figure is exactly that arithmetic, not a separate meter.
    minutes = completed * 6.0
    assert stats["hours_returned"] == round(minutes / 60.0, 1)
    assert stats["roi_usd"] == round(minutes / 60.0 * 30.0, 2)


def test_runs_of_a_flow_with_no_roi_assumption_return_no_hours(client, world):
    """A sync feed has no definition and so no `roi_minutes_per_run`. Its runs
    are real and its modelled hours are zero — counting them at some default
    would put invented value back into the figure by the side door."""
    priced_runs(world, "receiving-sync", 4)
    stats = client.get("/api/v1/flows/stats", params={"days": 30}).json()
    assert stats["runs"] == 4 and stats["completed"] == 4
    assert stats["hours_returned"] == 0.0 and stats["roi_usd"] == 0.0
    assert stats["roi_basis"]["modelled_runs"] == 0
    assert "no modelled time returned" in stats["roi_basis"]["statement"]


def test_success_rate_is_taken_over_finished_runs(client, world):
    """A run suspended on a human decision is not a failure. Counting it as
    one is the easiest way to make this number move without anything
    changing."""
    submit_expenses(world, 2)                      # confirmed and recorded
    submit_expenses(world, 1, confirm=False)       # still on its submitter's desk
    usage = client.get(
        "/api/v1/flows/expense-reimbursement/usage", params={"days": 30}
    ).json()
    assert usage["runs"] == 3
    assert usage["in_flight"] == 1 and usage["failed"] == 0
    assert usage["success_rate"] == 1.0
    # One per run: every expense asks its submitter to confirm, so "tasks
    # raised" is now a count of runs rather than a count of exceptions.
    assert usage["tasks_raised"] == 3


def test_awaiting_judgment_is_the_slice_my_tasks_would_show(client, world):
    """One queue, two screens, one number.

    The Flows header counted every open Task in the deployment while My Tasks
    counted the reader's own bindings, so an AE bound to nothing read
    "Awaiting judgment 21" next to "Queue is clear". Both surfaces now take
    the narrowing `TaskService.stats` takes, and the fleet total is bounded by
    the queue total on every persona — the header can never claim work the
    queue behind it would not show.
    """
    # Submitted by John, so the confirm-values Task lands on John's own desk —
    # which is what makes the persona split below interesting.
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[SubmittedDocument(filename="r.jpg", hint="total=900.00 category=meals")],
        commentary="Team dinner with the Acme delivery crew.",
    )
    assert world.tasks.get(request.task_id).queue.startswith("person-")

    for persona, visible in (
        # Platform reach: the deployment is theirs to diagnose.
        ("naviant-engineer", True),
        # John's own desk. He owns no shared queue at all, and under v10 that
        # no longer means he sees nothing: the registry desk minted for him is
        # a binding like any other, which is what lets him decide his own case.
        ("customer-user:ae", True),
        # Bound to `expense-review` and the exec desk, neither of which this
        # confirmation went to — the checks annotate, they do not re-route.
        ("customer-user:ceo", False),
        # Tenant reach.
        ("customer-admin", True),
    ):
        headers = {"X-Cumulus-Persona": persona}
        stats = client.get("/api/v1/flows/stats", headers=headers).json()
        queue = client.get("/api/v1/tasks/stats", headers=headers).json()
        rows = client.get("/api/v1/flows", headers=headers).json()
        row = next(r for r in rows if r["id"] == "expense-reimbursement")

        assert (stats["tasks_open"] > 0) is visible, persona
        assert row["tasks_open"] == stats["tasks_open"], persona
        # The header is a subset of the queue: a Task with no flow behind it
        # is real work and simply has no row here to be counted on.
        assert stats["tasks_open"] <= queue["open"], persona


# ---- versions ----


def test_a_history_is_the_shipped_packages_and_nothing_else(client, owner):
    """The seed used to mint two back-dated `archived` revisions per flow, and
    a fabricated `add-duplicate-check` draft for this one — so the picker
    offered five revisions of a flow that had shipped one or two, and the
    canvas diffed a draft no engineer had proposed.

    Asked of the code on disk rather than pinned to a number, because which
    version is live is a decision the console makes.
    """
    from app.tasks import shipped_version_numbers

    flow_id = "expense-reimbursement"
    versions = client.get(
        f"/api/v1/flows/{flow_id}/versions", headers=owner
    ).json()
    assert [v["version"] for v in versions] == sorted(
        [v["version"] for v in versions], reverse=True
    )
    assert {v["version"] for v in versions} == shipped_version_numbers(flow_id)
    assert sum(1 for v in versions if v["status"] == "active") == 1
    # A version is a full snapshot, never a delta.
    assert all(v["steps"] for v in versions)

    detail = client.get(f"/api/v1/flows/{flow_id}", headers=owner).json()
    live = next(v for v in versions if v["status"] == "active")
    assert detail["active_version_label"] == live["label"]
    assert detail["version_count"] == len(versions)

    assert (
        client.get(
            f"/api/v1/flows/{flow_id}/versions/999", headers=owner
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
    assert [s.id for s in upgraded.steps if s.type == "modelcall"] == ["m1", "m2"]
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


def test_accounts_payable_history_carries_every_shipped_candidate(client, owner):
    """AP ships several versions: the history shows exactly those, one of them
    live, the ones above it as drafts the console can activate and the ones
    below as archived. The newest candidate is the one the detail page offers.

    Read off the shipped packages rather than pinned to version numbers — the
    two back-dated archives that used to sit under them were invented by the
    seed and could never have been run.
    """
    from app.tasks import shipped_version_numbers

    versions = client.get(
        "/api/v1/flows/accounts-payable/versions", headers=owner
    ).json()
    assert {v["version"] for v in versions} == shipped_version_numbers(
        "accounts-payable"
    )
    assert all(v["shipped"] for v in versions)
    # Full snapshots here too — every revision carries the whole map.
    assert all(v["steps"] for v in versions)

    live = next(v for v in versions if v["status"] == "active")
    assert [v["status"] for v in versions if v["version"] > live["version"]] == [
        "draft"
    ] * sum(1 for v in versions if v["version"] > live["version"])
    assert all(
        v["status"] == "archived"
        for v in versions if v["version"] < live["version"]
    )

    detail = client.get("/api/v1/flows/accounts-payable", headers=owner).json()
    assert detail["active_version_label"] == live["label"]
    assert detail["version_count"] == len(versions)
    drafts = [v for v in versions if v["status"] == "draft"]
    assert detail["draft_version_label"] == (drafts[0]["label"] if drafts else "")


def test_version_diff_marks_added_and_modified(client, owner):
    """Diffed between two real shipped packages, since those are the only
    versions a history holds now — v8 against v6, named explicitly so the diff
    is a fact about two packages rather than about whichever is live. v8 adds
    the arithmetic pass ahead of the vendor resolve; v7 changes one rule's
    config and nothing else."""
    diff = client.get(
        "/api/v1/flows/accounts-payable/versions/8/diff",
        params={"against": 6}, headers=owner,
    ).json()
    by_id = {d["step"]["id"]: d for d in diff}
    # v8's added steps: tolerances, the arithmetic check, the field verdicts
    # and the locate call they feed.
    assert {"x1", "c0", "x2", "m2"} <= {
        i for i, d in by_id.items() if d["change"] == "added"
    }
    # The step before them points at the new pass rather than the resolve.
    assert by_id["m1"]["change"] == "modified"
    assert "next" in by_id["m1"]["changed_fields"]
    assert by_id["e1"]["change"] == "unchanged"

    # v7 differs from the live version in one rule's governed config.
    older = client.get(
        "/api/v1/flows/accounts-payable/versions/7/diff",
        params={"against": 6}, headers=owner,
    ).json()
    changed = {d["step"]["id"]: d for d in older if d["change"] == "modified"}
    assert set(changed) == {"r2"}
    assert changed["r2"]["changed_fields"] == ["summary", "config"]


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


def test_the_recommender_states_its_basis_when_evidence_is_thin(client, world, owner):
    """The consequence of re-pointing at real runs, made deliberate.

    Every cost card was priced off synthesized spend. With the real journals
    recording no cost, those cards have no basis — and a recommender with no
    basis has exactly one acceptable behaviour. Not carrying on (a downgrade
    proposed on zero measured spend is fabricated confidence). Not vanishing
    either, because an empty list renders as a clean bill of health.
    """
    submit_expenses(world, 3)
    recs = client.get(
        "/api/v1/flows/expense-reimbursement/recommendations", headers=owner
    ).json()

    # The basis leads, always.
    assert recs[0]["kind"] == "evidence"
    basis = recs[0]
    assert "3 run(s) / 30d" in basis["evidence"]
    assert "none carrying a cost" in basis["evidence"]
    # The extract step already runs the cheapest rung, so a downgrade is not
    # even considered — the withheld list names only checks that wanted a
    # measurement and did not get one.
    assert "budget check (no measured cost per run)" in basis["detail"]

    # And nothing priced off spend that was never recorded survives.
    kinds = {r["kind"] for r in recs}
    assert "model-downgrade" not in kinds and "budget" not in kinds
    assert "prompt-cache" not in kinds and "batching" not in kinds
    # Coverage needs no spend — both halves are counted, not modelled.
    assert "coverage" in kinds
    for rec in recs:
        assert rec["title"] and rec["detail"] and rec["evidence"]


def test_the_recommender_does_not_go_silent_with_no_runs_at_all(client, owner):
    recs = client.get(
        "/api/v1/flows/accounts-payable/recommendations", headers=owner
    ).json()
    assert [r["kind"] for r in recs] == ["evidence"]
    assert "0 run(s) / 30d" in recs[0]["evidence"]


def test_measured_spend_earns_the_cards_it_used_to_be_given(client, world, owner):
    """The gates are about evidence, not about withholding on principle.

    Record real costs on real runs and the cost cards come back — which is
    what makes their absence above a statement rather than a policy. The
    seeded flows already run the cheapest rung, so the ladder is exercised
    on a probe flow declared one rung up.
    """
    probe_flow(client, owner, "probe-downgrade")
    priced_runs(world, "probe-downgrade", 250, cost_usd=0.14, tokens=5200)
    recs = client.get(
        "/api/v1/flows/probe-downgrade/recommendations", headers=owner
    ).json()
    kinds = {r["kind"] for r in recs}
    assert {"evidence", "model-downgrade", "prompt-cache", "coverage"} == kinds
    assert recs[0]["kind"] == "evidence"
    assert "all priced" in recs[0]["evidence"]
    assert "Nothing was withheld" in recs[0]["detail"]
    # Money sorts what follows the basis card.
    savings = [r["estimated_monthly_savings_usd"] for r in recs[1:]]
    assert savings == sorted(savings, reverse=True)

    downgrade = next(r for r in recs if r["kind"] == "model-downgrade")
    assert downgrade["proposed_change"]["from"] == "gemini-3.7-flash"
    assert downgrade["proposed_change"]["to"] == "gemini-3.5-flash-lite"
    # Its evidence is the arithmetic it was derived from, not a magnitude.
    assert "$35.00 attributed to this step" in downgrade["evidence"]

    applied = client.post(
        f"/api/v1/flows/probe-downgrade/recommendations/{downgrade['id']}/apply",
        headers=owner,
    )
    assert applied.status_code == 200, applied.text
    draft = applied.json()["draft"]
    # The change lands in a DRAFT, never in the live version.
    assert draft["status"] == "draft"
    step = next(
        s for s in draft["steps"] if s["id"] == downgrade["proposed_change"]["step_id"]
    )
    assert step["config"]["model"] == "gemini-3.5-flash-lite"
    live = client.get("/api/v1/flows/probe-downgrade", headers=owner).json()
    active_step = next(
        s for s in live["steps"] if s["id"] == downgrade["proposed_change"]["step_id"]
    )
    assert active_step["config"]["model"] == "gemini-3.7-flash"


def test_downgrade_is_withheld_while_the_flow_is_failing(client, world, owner):
    """Spending less on something that is not working is not a saving.

    A flow on an expensive model with measured spend is the obvious downgrade
    candidate on cost alone — but below the success bar the right move is to
    fix quality. The card is withheld, and the basis card says which of the
    two reasons withheld it, because "no evidence" and "the evidence says no"
    are different answers.
    """
    probe_flow(client, owner, "probe-failing")
    priced_runs(
        world, "probe-failing", 200, cost_usd=0.30, fail_every=5,
    )
    recs = client.get(
        "/api/v1/flows/probe-failing/recommendations", headers=owner
    ).json()
    assert not [r for r in recs if r["kind"] == "model-downgrade"]
    assert "below the bar to spend less" in recs[0]["detail"]
    # Other insights still surface — the spend was measured, after all.
    assert len(recs) > 1


def test_a_flow_with_no_definition_says_so_rather_than_recommending(client, world, owner):
    priced_runs(world, "probe-unmapped", 3)
    recs = client.get(
        "/api/v1/flows/probe-unmapped/recommendations", headers=owner
    ).json()
    assert recs[0]["kind"] == "evidence"
    assert "no declarative export" in recs[0]["detail"]
    assert not [r for r in recs if r["proposed_change"]]


def test_advisory_recommendation_cannot_be_applied(client, world, owner):
    submit_expenses(world, 2)
    recs = client.get(
        "/api/v1/flows/expense-reimbursement/recommendations", headers=owner
    ).json()
    # The coverage card is advisory: adding eval cases is judgment work,
    # not a mechanical edit the console can stage into a draft.
    advisory = next(r for r in recs if r["kind"] == "coverage")
    assert advisory["proposed_change"] is None
    resp = client.post(
        f"/api/v1/flows/expense-reimbursement/recommendations/{advisory['id']}/apply",
        headers=owner,
    )
    assert resp.status_code == 409
    assert "advisory" in resp.json()["detail"].lower()


def test_the_basis_card_is_a_statement_not_a_proposal(client, world, owner):
    submit_expenses(world, 1)
    recs = client.get(
        "/api/v1/flows/expense-reimbursement/recommendations", headers=owner
    ).json()
    resp = client.post(
        f"/api/v1/flows/expense-reimbursement/recommendations/{recs[0]['id']}/apply",
        headers=owner,
    )
    assert resp.status_code == 409


def test_unknown_recommendation_is_404(client, owner):
    resp = client.post(
        "/api/v1/flows/expense-reimbursement/recommendations/nope/apply",
        headers=owner,
    )
    assert resp.status_code == 404


# ---- inventory -------------------------------------------------------------
#
# What a flow is *wired to*, which is a different question from what it did.
# The two halves — declared in the export, observed in the journal — are
# reported side by side and never reconciled into one, because "declared and
# never exercised" and "exercised and never declared" are the two governance
# faults this page exists to surface, and a single merged list hides both.


def test_inventory_reports_a_model_declared_but_never_called(client, world, owner):
    """A declaration with no calls behind it is still the wiring.

    The step names the model; nothing has run. Reporting an empty inventory
    would say this flow calls no model, which is the opposite of what its
    export says.
    """
    probe_flow(client, owner, "probe-inv-idle")
    inv = client.get(
        "/api/v1/flows/probe-inv-idle/inventory", headers=owner
    ).json()
    assert inv["state"] == "never_ran"
    model = next(m for m in inv["models"] if m["model"] == "gemini-3.7-flash")
    assert model["declared"] is True
    assert model["steps"] == ["m1"]
    assert model["calls"] == 0


def test_inventory_counts_the_calls_the_journal_recorded(client, world, owner):
    """Observed model use comes off the run journal, priced calls included.

    `StepRecord` is the only record of what a model call cost, so this is the
    one place the console can answer "what did this flow spend, on what".
    """
    probe_flow(client, owner, "probe-inv-busy")
    priced_runs(world, "probe-inv-busy", 4, cost_usd=0.25, tokens=1000)
    inv = client.get(
        "/api/v1/flows/probe-inv-busy/inventory", headers=owner
    ).json()
    model = next(m for m in inv["models"] if m["model"] == "gemini-3.7-flash")
    assert model["declared"] is True
    assert model["calls"] == 4
    assert model["priced_calls"] == 4
    assert model["tokens"] == 4000
    assert model["cost_usd"] == pytest.approx(1.0)


def test_an_unpriced_call_is_counted_but_not_priced(client, world, owner):
    """A cost of zero over unpriced calls is a hole, not a bargain.

    `priced_calls` is what lets the console say which — the same distinction
    the fleet's cost basis draws.
    """

    def body(ctx, inputs):
        ctx.step("m1", "extract-receipt", "modelcall", fn=lambda: {"ok": True},
                 model="gemini-3.7-flash", tokens=900)
        return {}

    probe_flow(client, owner, "probe-inv-unpriced")
    world.engine.register("probe-inv-unpriced", body)
    world.engine.start("probe-inv-unpriced", {}, run_id="run_unpriced_1")
    inv = client.get(
        "/api/v1/flows/probe-inv-unpriced/inventory", headers=owner
    ).json()
    model = next(m for m in inv["models"] if m["model"] == "gemini-3.7-flash")
    assert model["calls"] == 1
    assert model["priced_calls"] == 0
    assert model["cost_usd"] == 0.0


def test_every_link_is_a_verb_a_kind_and_an_address(client, owner):
    """One shape for every relationship, and each one addressable.

    The console renders these as a single table and routes a click on `ref`;
    deriving an address back out of a display label is how the two drift.
    """
    inv = client.get(
        "/api/v1/flows/accounts-payable/inventory", headers=owner
    ).json()
    verbs = {"reads", "mutates", "establishes", "writes-to", "routes-to", "reaches"}
    kinds = {"entity", "knowledge", "collection", "link", "queue", "system"}
    assert inv["links"], "a mapped flow touches something"
    for link in inv["links"]:
        assert link["verb"] in verbs, link
        assert link["kind"] in kinds, link
        assert ":" in link["ref"], link
        # A row that is neither declared nor observed came from nowhere.
        assert link["declared"] or link["observed"], link
    # The vendor master is declared for this flow in the Collection registry.
    assert any(
        link["ref"] == "collection:vendor-master" and link["verb"] == "reads"
        for link in inv["links"]
    )


def test_reading_and_mutating_an_entity_are_two_rows_not_one(client, owner):
    """The verb is what distinguishes them, so both survive.

    A flow that reads Vendor and writes Document must not collapse into one
    "touches" row: which direction the data went is the whole question.
    """
    inv = client.get(
        "/api/v1/flows/accounts-payable/inventory", headers=owner
    ).json()
    by_verb: dict[str, set[str]] = {}
    for link in inv["links"]:
        by_verb.setdefault(link["verb"], set()).add(link["label"])
    assert "Vendor" in by_verb["reads"]
    assert by_verb.get("mutates"), "this flow writes entities"
    assert by_verb["reads"] != by_verb.get("mutates")


def test_links_are_ordered_for_reading(client, owner):
    """Grouped by verb, in the order the console lists them.

    The table has no group headers — the verb column is the grouping — so the
    order has to come off the wire already correct.
    """
    order = ["reads", "mutates", "establishes", "writes-to", "routes-to", "reaches"]
    inv = client.get(
        "/api/v1/flows/accounts-payable/inventory", headers=owner
    ).json()
    seen = [order.index(link["verb"]) for link in inv["links"]]
    assert seen == sorted(seen), "verbs are not grouped"


def test_a_desk_is_declared_wiring_not_an_observation(client, owner):
    """A queue nobody has been routed to yet is still a queue.

    Connections come off the export alone: a run that never suspended is not
    evidence the desk does not exist, and scoping the desk to observed traffic
    would empty these rows on every quiet flow.
    """
    inv = client.get(
        "/api/v1/flows/disagreement-sweep/inventory", headers=owner
    ).json()
    desk = next(
        link for link in inv["links"] if link["verb"] == "routes-to"
    )
    assert desk["ref"] == "queue:order-desk"
    assert desk["kind"] == "queue"
    assert desk["declared"] is True
    # The export calls it a gateway; the table calls it a system.
    system = next(
        link for link in inv["links"] if link["verb"] == "reaches"
    )
    assert system["kind"] == "system"
    assert system["ref"].startswith("system:")


def test_inventory_is_not_windowed(client, world, owner):
    """The wiring does not change because the last 30 days were quiet.

    The endpoint takes no `days`, and a run older than any window the console
    offers still counts toward what the flow is wired to.
    """
    probe_flow(client, owner, "probe-inv-old")
    priced_runs(world, "probe-inv-old", 2)
    stale = (utcnow() - timedelta(days=400)).isoformat()
    for run in world.engine.list(flow_id="probe-inv-old"):
        run.started_at_iso = stale
    inv = client.get(
        "/api/v1/flows/probe-inv-old/inventory", headers=owner
    ).json()
    assert next(
        m for m in inv["models"] if m["model"] == "gemini-3.7-flash"
    )["calls"] == 2


def test_inventory_needs_a_reader_and_a_real_flow(client, owner):
    assert client.get("/api/v1/flows/nope-not-a-flow/inventory",
                      headers=owner).status_code == 404
    assert client.get("/api/v1/flows/accounts-payable/inventory").status_code == 401
