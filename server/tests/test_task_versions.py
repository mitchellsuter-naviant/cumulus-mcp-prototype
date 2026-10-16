"""Versioned task packages: discovery, the engine's choice, and activation.

The contract under test: the versions the console can offer are exactly the
`v<N>/` packages in the shipped code; which one is live is a stored decision
the console flips; runs pin the version that started them; and the tasks a
run raises carry that version so the console can pick the matching screen.
"""

import pytest

import app.deps as deps
import app.tasks as packages
from app.flows import FlowService, InMemoryFlowDirectory, seed_flows_if_empty
from app.runs import InMemoryRunStore, RunEngine
from app.task_queue import InMemoryTaskDirectory, Task, TaskService
from tests import world as test_world


@pytest.fixture()
def world():
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


# ---- discovery -------------------------------------------------------------


def test_discovery_reads_the_shipped_directories():
    """What the console can offer is what the deployment contains — read off
    the filesystem, not off a hand-kept list. Every registered flow ships a
    package: the two authored processes, ap-intake, ap-corrections, the
    fourteen feeds, the two sweeps, order-release, answer-question, the
    compliance clock, the field-documents window and the plant spine's
    three."""
    assert packages.task_flow_ids() == [
        "accounts-payable", "answer-question", "ap-corrections", "ap-intake",
        "calendar-sync", "credential-sync", "credential-tracking",
        "crm-sync", "department-scheduling", "disagreement-sweep",
        "dms-sync", "employment-event-sync",
        "equipment-sync",
        "expense-reimbursement", "field-documents", "identity-sweep",
        "job-sync", "mail-sync",
        "order-release", "plant-eligibility-sweep", "psa-sync",
        "purchase-order-sync", "receiving-sync",
        "sales-sync", "shipping-list-extraction", "vendor-sync",
        "workforce-sync",
    ]
    assert packages.shipped_version_numbers("accounts-payable") == {6, 7, 8, 9}
    assert packages.shipped_version_numbers("ap-intake") == {1}
    assert packages.shipped_version_numbers("ap-corrections") == {1}
    assert packages.shipped_version_numbers("expense-reimbursement") == {9, 10}
    assert packages.shipped_version_numbers("order-release") == {1}
    assert packages.shipped_version_numbers("vendor-sync") == {1}
    assert packages.shipped_version_numbers("not-a-flow") == set()
    assert packages.default_version("accounts-payable") == 9
    assert packages.default_version("vendor-sync") == 1
    assert packages.default_version("not-a-flow") == 0


def test_every_shipped_version_carries_its_own_map():
    for flow_id in packages.task_flow_ids():
        for number, module in packages.shipped_versions(flow_id).items():
            assert module.VERSION == number
            assert module.STEPS, f"{flow_id} v{number} ships no steps"
            assert module.NOTES
            assert callable(module.build_body)


# ---- the engine's choice ---------------------------------------------------


def _engine() -> RunEngine:
    return RunEngine(InMemoryRunStore(), TaskService(InMemoryTaskDirectory()))


def test_the_default_version_runs_when_nothing_says_otherwise():
    engine = _engine()
    engine.register("f", lambda ctx, i: {"ran": 6}, version=6, default=True)
    engine.register("f", lambda ctx, i: {"ran": 7}, version=7)
    record = engine.start("f", {})
    assert record.outputs == {"ran": 6}
    assert record.flow_version == 6


def test_the_stored_live_pointer_outranks_the_default():
    engine = _engine()
    engine.register("f", lambda ctx, i: {"ran": 6}, version=6, default=True)
    engine.register("f", lambda ctx, i: {"ran": 7}, version=7)
    engine.live_version = lambda flow_id: 7
    record = engine.start("f", {})
    assert record.outputs == {"ran": 7}
    assert record.flow_version == 7


def test_a_live_pointer_at_unshipped_code_falls_back_to_the_default():
    engine = _engine()
    engine.register("f", lambda ctx, i: {"ran": 6}, version=6, default=True)
    engine.live_version = lambda flow_id: 12
    record = engine.start("f", {})
    assert record.outputs == {"ran": 6}
    assert record.flow_version == 6


def test_a_resumed_run_finishes_under_the_version_that_started_it():
    """v7 going live must not rewrite history: a run suspended under v6
    replays v6's journal with v6's body."""
    engine = _engine()

    def v6_body(ctx, inputs):
        decision = ctx.gate(
            "k1", "review",
            build_task=lambda: Task(
                id="", kind="decision", title="review", queue="desk",
                flow_id="f",
            ),
        )
        return {"ran": 6, "verdict": decision.verdict}

    engine.register("f", v6_body, version=6, default=True)
    engine.register("f", lambda ctx, i: {"ran": 7}, version=7)

    record = engine.start("f", {})
    assert record.status == "suspended"
    assert record.flow_version == 6

    # The gate stamped the run's version onto the task it raised.
    task = engine.tasks.get(record.open_task_id)
    assert task.flow_version == 6

    # v7 goes live while the run waits; the resume still drives v6's body.
    engine.live_version = lambda flow_id: 7
    engine.tasks.decide(task.id, verdict="approved", decided_by="clerk")
    resumed = engine.resume(record.id)
    assert resumed.status == "succeeded"
    assert resumed.outputs["ran"] == 6
    assert resumed.flow_version == 6


def test_an_unversioned_registration_is_a_deliberate_override():
    """The legacy gesture — register a bare body over a versioned fleet —
    keeps meaning what it meant: this body, not the fleet's."""
    engine = _engine()
    engine.register("f", lambda ctx, i: {"ran": 6}, version=6, default=True)
    engine.register("f", lambda ctx, i: {"ran": 0})
    record = engine.start("f", {})
    assert record.outputs == {"ran": 0}
    assert record.flow_version == 0


# ---- the world's real registrations ----------------------------------------


def test_the_world_registers_every_shipped_ap_version(world):
    record = world.engine.start(
        "accounts-payable",
        {"submitted_by": "connie.alvarez@naviant.com", "hint": "total=10.00"},
    )
    # The package's declared default, which nothing in this world has
    # overridden. v6, v7 and v8 are registered beside it and selectable.
    assert record.flow_version == 9


# ---- seeding and activation ------------------------------------------------


def _seeded_service() -> FlowService:
    service = FlowService(InMemoryFlowDirectory())
    seed_flows_if_empty(service)
    return service


def test_the_seed_offers_every_shipped_version_with_its_own_map():
    """The live one, and the ones a console can roll back to.

    Written against the *history* rather than against a particular status,
    because which version is live is a decision that moves: v9 is the default
    now and the older three are archived, where before v6 was live and the
    later packages were drafts. What must hold either way is that every
    shipped package is offered with its own steps, so the picker can show a
    map for each and an activation has something to activate.
    """
    service = _seeded_service()
    defn = service.get("accounts-payable")
    assert defn.version == packages.default_version("accounts-payable") == 9
    by_version = {v.version: v for v in defn.version_history()}
    assert set(by_version) == packages.shipped_version_numbers("accounts-payable")
    assert by_version[9].status == "active"
    assert all(v.steps for v in by_version.values())
    # Each carries its *own* map, not a copy of the live one: v7's declared
    # change is on its posting rule, and v9 has no `r2` at all.
    r2 = next(s for s in by_version[7].steps if s.id == "r2")
    assert "allowance" in r2.config
    assert not [s for s in by_version[9].steps if s.id == "r2"]


def test_activation_flips_the_live_pointer_and_the_statuses():
    service = _seeded_service()
    updated = service.activate_version("accounts-payable", 7)
    assert updated.version == 7
    statuses = {v.version: v.status for v in updated.version_history()}
    assert statuses[7] == "active"
    assert statuses[6] == "archived"
    # And back: an activated older version is a rollback, not an error.
    rolled = service.activate_version("accounts-payable", 6)
    assert rolled.version == 6
    statuses = {v.version: v.status for v in rolled.version_history()}
    assert statuses[6] == "active"
    assert statuses[7] == "archived"


def test_only_shipped_code_can_be_made_live():
    """Two ways to fail, and both must: a version nobody declared, and a
    version a stored history declares but no package backs.

    The second one used to be handed over by the seed — a fabricated
    `v10-draft` on expense reimbursement — so it is staged here instead,
    because the refusal still has to hold against a history written by an
    older deployment whose package has since been removed."""
    service = _seeded_service()
    with pytest.raises(ValueError):
        service.activate_version("accounts-payable", 5)  # never existed

    from app.flows import FlowVersion

    stored = service.require("expense-reimbursement")
    live = stored.active_version()
    service.update(stored.model_copy(update={
        "versions": stored.version_history() + [
            FlowVersion(
                version=99, label="v99", status="draft", steps=live.steps,
                notes="Declared by a deployment that shipped a package for it.",
            )
        ],
    }))
    with pytest.raises(ValueError):
        service.activate_version("expense-reimbursement", 99)


def test_a_redeploy_records_newly_shipped_versions_in_a_stored_history():
    """The store predates v7 (simulated by stripping it); the next seed pass
    puts the shipped version back without touching what is live."""
    service = _seeded_service()
    defn = service.get("accounts-payable")
    service.update(defn.model_copy(update={
        "versions": [v for v in defn.version_history() if v.version != 7],
    }))
    seed_flows_if_empty(service)
    from app.tasks import shipped_version_numbers

    refreshed = service.get("accounts-payable")
    assert {v.version for v in refreshed.version_history()} == (
        shipped_version_numbers("accounts-payable")
    )


# ---- the API face ----------------------------------------------------------


def test_versions_carry_the_shipped_flag_on_the_wire(client, owner):
    """`shipped` is read off the code on disk, never off the store.

    Every seeded version is shipped now, which is the point of the seed no
    longer inventing any — so the False case is staged: a stored history that
    names a version this deployment carries no package for.
    """
    from app.flows import FlowVersion

    service = deps._flow_service
    stored = service.require("expense-reimbursement")
    live = stored.active_version()
    service.update(stored.model_copy(update={
        "versions": stored.version_history() + [
            FlowVersion(version=99, label="v99", status="draft",
                        steps=live.steps, notes="Package no longer deployed."),
        ],
    }))

    from app.tasks import shipped_version_numbers

    versions = client.get(
        "/api/v1/flows/expense-reimbursement/versions", headers=owner
    ).json()
    flags = {v["version"]: v["shipped"] for v in versions}
    assert all(flags[n] is True for n in shipped_version_numbers(
        "expense-reimbursement"
    ))
    assert flags[99] is False


def test_activate_endpoint_flips_and_refuses(client, owner):
    activated = client.post(
        "/api/v1/flows/accounts-payable/versions/7/activate", headers=owner
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["active_version_label"] == "v7"

    # A version number no package in this deployment backs. Named rather than
    # "the next one up", because the next one up keeps getting shipped: this
    # asserts the refusal, not the current top of the range.
    refused = client.post(
        "/api/v1/flows/expense-reimbursement/versions/99/activate", headers=owner
    )
    assert refused.status_code == 409
    assert "not shipped" in refused.json()["detail"]


# ---- v7's one behavioral change --------------------------------------------


def _clean_case(total: float):
    from app.matching import MatchResult
    from app.tasks.accounts_payable.domain import (
        InvoiceExtraction, InvoiceLineExtract, PostingPolicy,
    )

    extraction = InvoiceExtraction(
        vendor_name="Acme", invoice_number="INV-1", total_usd=total,
        confidence=0.99,
        lines=[InvoiceLineExtract(
            description="widgets", quantity=1, unit_price_usd=total,
            amount_usd=total, kind="goods",
        )],
    )
    match = MatchResult(verdict="matched")
    policy = PostingPolicy(limit_usd=1000.0, min_confidence=0.8)
    return extraction, match, {"findings": []}, policy


def test_v7_posts_a_perfect_match_up_to_twice_the_limit():
    from app.tasks.accounts_payable.v7.flow import _posting_check

    extraction, match, screen, policy = _clean_case(total=1500.0)
    verdict = _posting_check(extraction, match, screen, policy)
    assert verdict["reasons"] == []
    assert "perfect-match allowance" in verdict["note"]


def test_v7_still_stops_at_twice_the_limit_or_any_other_doubt():
    from app.tasks.accounts_payable.v7.flow import _posting_check

    extraction, match, screen, policy = _clean_case(total=2500.0)
    assert _posting_check(extraction, match, screen, policy)["reasons"]

    extraction, match, screen, policy = _clean_case(total=1500.0)
    screen = {"findings": [{"kind": "duplicate", "detail": "seen before"}]}
    assert _posting_check(extraction, match, screen, policy)["reasons"]


def test_v6_is_untouched_by_the_allowance():
    from app.tasks.accounts_payable.domain import _posting_check

    extraction, match, screen, policy = _clean_case(total=1500.0)
    assert _posting_check(extraction, match, screen, policy)["reasons"]
