"""Graph-shaped evals: the spec/case split, checkpoints, and isolation.

What is worth asserting about an eval harness is mostly that it *fails* when it
should. A grader that only ever passes is indistinguishable from no grader, so
most of these break something on purpose and check the failure is legible.
"""

import pytest

from app.eval_specs import AP_SPEC, EXPENSE_SPEC, DeclaredSpecStore
from app.evals import (
    Checkpoint,
    CheckpointExpectation,
    EvalCase,
    EvalService,
    EvalSpec,
    ExpectedObject,
    FieldRule,
    FixtureObject,
    GraphFixture,
    LinkRule,
    ObjectRule,
    ScriptedDecision,
    TrajectoryRule,
    compare,
    snapshot_as_of,
    steps_upto,
)
from tests import world as test_world


@pytest.fixture()
def service():
    return EvalService(DeclaredSpecStore())


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


@pytest.fixture()
def owner(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


# ---- the declared specs pass ----------------------------------------------


@pytest.mark.parametrize("spec_id", [EXPENSE_SPEC.id, AP_SPEC.id])
def test_every_declared_case_passes(service, spec_id):
    result = service.run(spec_id)
    assert result.cases, "the spec has no cases"
    failures = [
        f"{c.case_id}: {c.error or '; '.join(f'{a.target}.{a.field} {a.detail}' for a in c.failures)}"
        for c in result.cases
        if not c.passed
    ]
    assert not failures, "\n".join(failures)


def test_a_case_carries_the_knowledge_revisions_it_read(service):
    result = service.run(EXPENSE_SPEC.id, case_ids=["expense-in-policy-auto-approves"])
    revisions = result.cases[0].knowledge_revisions
    # Comparing two pass rates only means something when they read the same
    # governed values, so the result records them.
    assert revisions["expense-approval-policy"] >= 1


def test_every_case_references_a_declared_checkpoint(service):
    for spec in service.specs():
        declared = {c.id for c in spec.checkpoints}
        for case in service.cases(spec.id):
            for expectation in case.expectations:
                assert expectation.checkpoint in declared, (
                    f"{case.id} expects '{expectation.checkpoint}', "
                    f"spec declares {sorted(declared)}"
                )


# ---- the match rules ------------------------------------------------------


@pytest.mark.parametrize(
    "rule,expected,actual,passes",
    [
        (FieldRule(field="f"), "posted", "posted", True),
        (FieldRule(field="f"), "posted", "pending", False),
        # Numbers compare as numbers, so 1425.25 and "1425.25" agree.
        (FieldRule(field="f"), 1425.25, "1425.25", True),
        (FieldRule(field="f", match="approx", tolerance=0.01), 100.0, 100.005, True),
        (FieldRule(field="f", match="approx", tolerance=0.01), 100.0, 100.5, False),
        (FieldRule(field="f", match="fuzzy", tolerance=0.90),
         "Acme Industrial Inc.", "ACME INDUSTRIAL, INCORPORATED", True),
        (FieldRule(field="f", match="fuzzy", tolerance=0.90),
         "Acme Industrial Inc.", "Northwind Traders LLC", False),
        (FieldRule(field="f", match="one_of", options=["a", "b"]), "", "b", True),
        (FieldRule(field="f", match="one_of", options=["a", "b"]), "", "c", False),
        (FieldRule(field="f", match="present"), "", "anything", True),
        (FieldRule(field="f", match="present"), "", "", False),
        (FieldRule(field="f", match="absent"), "", "", True),
        (FieldRule(field="f", match="absent"), "", "something", False),
        # `ignore` is a decision, not an omission: it passes whatever happens.
        (FieldRule(field="f", match="ignore"), "a", "z", True),
    ],
)
def test_match_rules(rule, expected, actual, passes):
    assert compare(rule, expected, actual)[0] is passes


def test_a_missing_value_fails_rather_than_passing_vacuously():
    passed, why = compare(FieldRule(field="f"), "posted", None)
    assert not passed and "not set" in why


# ---- checkpoints ----------------------------------------------------------


def test_a_checkpoint_sees_only_what_had_been_written(service, world):
    """The point of recording the step on every claim."""
    from app.reimbursements import SubmittedDocument

    request = world.expenses.submit(
        "pat@customer.example",
        documents=[SubmittedDocument(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    run = world.engine.get(request.run_id)

    # The expense flow writes the graph at a1, so before it there is nothing.
    early = snapshot_as_of(world.graph, run, "x2")
    assert not [o for o in early.objects if o.type == "ExpenseLine"]
    late = snapshot_as_of(world.graph, run, "final")
    assert [o for o in late.objects if o.type == "ExpenseLine"]


def test_steps_upto_reports_a_step_the_run_never_reached(service, world):
    from app.reimbursements import SubmittedDocument

    request = world.expenses.submit(
        "sam@customer.example",
        documents=[SubmittedDocument(hint="total=612.00 category=lodging")],
        commentary="Conference block rate for the finance summit.",
    )
    run = world.engine.get(request.run_id)
    prefix, reached = steps_upto(run, "a1")
    assert not reached and prefix == []
    prefix, reached = steps_upto(run, "r1")
    assert reached and prefix[-1] == "r1"


def test_a_case_declaring_an_unreached_checkpoint_fails_legibly(service):
    """A held run has not committed, and saying so is the assertion."""
    spec = EvalSpec(
        id="probe", flow_id="expense-reimbursement",
        checkpoints=[Checkpoint(id="committed", after_step="a1")],
    )
    case = EvalCase(
        id="held", spec_id="probe", name="over the limit, nobody decided",
        fixture=GraphFixture(objects=[FixtureObject(
            type="Employee", key="sam@customer.example",
            props={"email": "sam@customer.example"},
        )]),
        trigger={
            "employee": "sam@customer.example",
            "hint": "total=612.00 category=lodging",
        },
        expectations=[CheckpointExpectation(
            checkpoint="committed",
            objects=[ExpectedObject(type="ExpenseRequest", key="$run")],
        )],
    )
    outcome = service.run_case(spec, case)
    assert not outcome.passed
    assert outcome.run_status == "suspended"
    failure = outcome.failures[0]
    assert failure.rule == "reached"
    assert "never reached a1" in failure.detail


def test_trajectory_is_scoped_to_the_checkpoint(service):
    """`step_absent` at a mid-run checkpoint means "not yet", not "never"."""
    spec = EvalSpec(
        id="probe", flow_id="accounts-payable",
        checkpoints=[
            Checkpoint(
                id="early", after_step="z1",
                trajectory=[TrajectoryRule(kind="step_absent", expected="m2")],
            ),
            Checkpoint(
                id="late", after_step="final",
                trajectory=[TrajectoryRule(kind="step_ran", expected="m2")],
            ),
        ],
    )
    case = EvalCase(
        id="one-shot", spec_id="probe", name="known vendor",
        fixture=GraphFixture(deliveries=[{
            "source": "erp-vendors",
            "vendors": [{"external_id": "V-1", "name": "Acme Industrial Inc."}],
        }]),
        trigger={
            "submitted_by": "ap@customer.example",
            "hint": "vendor=acme-industrial nopo invoice=A-1 lines=PPE-GLV:1 total=80.00",
        },
        decisions=[ScriptedDecision(verdict="approved")],
        expectations=[
            CheckpointExpectation(checkpoint="early"),
            CheckpointExpectation(checkpoint="late"),
        ],
    )
    outcome = service.run_case(spec, case)
    assert outcome.passed, [
        (a.checkpoint, a.field, a.detail) for a in outcome.failures
    ]


# ---- the grader fails when it should -------------------------------------


def test_a_wrong_expected_value_fails_with_both_values(service):
    spec = EvalSpec(
        id="probe", flow_id="expense-reimbursement",
        checkpoints=[Checkpoint(
            id="final", after_step="final",
            objects=[ObjectRule(
                type="ExpenseRequest",
                fields=[FieldRule(field="requested_usd", match="approx",
                                  tolerance=0.01)],
            )],
        )],
    )
    case = EvalCase(
        id="wrong", spec_id="probe", name="deliberately wrong",
        fixture=GraphFixture(objects=[FixtureObject(
            type="Employee", key="pat@customer.example",
            props={"email": "pat@customer.example"},
        )]),
        trigger={
            "employee": "pat@customer.example",
            "commentary": "Team lunch after the Acme review.",
            "hint": "total=64.20 category=meals",
        },
        expectations=[CheckpointExpectation(
            checkpoint="final",
            objects=[ExpectedObject(
                type="ExpenseRequest", key="$run",
                fields={"requested_usd": 999.99},
            )],
        )],
    )
    outcome = service.run_case(spec, case)
    assert not outcome.passed
    failure = next(a for a in outcome.failures if a.field == "requested_usd")
    assert failure.expected == "999.99" and failure.actual == "64.2"
    assert "off by" in failure.detail


def test_an_ambiguous_empty_key_is_refused_rather_than_guessed(service):
    spec = EvalSpec(
        id="probe", flow_id="expense-reimbursement",
        checkpoints=[Checkpoint(id="final", after_step="final")],
    )
    case = EvalCase(
        id="ambiguous", spec_id="probe", name="two lines, no key",
        fixture=GraphFixture(objects=[FixtureObject(
            type="Employee", key="kim@customer.example",
            props={"email": "kim@customer.example"},
        )]),
        trigger={
            "employee": "kim@customer.example",
            "commentary": "Two nights at the Acme onsite.",
            "hint": "compound total=140.00 category=lodging",
        },
        expectations=[CheckpointExpectation(
            checkpoint="final",
            objects=[ExpectedObject(type="ExpenseLine", key="")],
        )],
    )
    outcome = service.run_case(spec, case)
    assert not outcome.passed
    assert "ambiguous" in outcome.failures[0].detail


def test_unlisted_fields_can_be_required(service):
    """Drift detection: the flow claimed something the spec never named."""
    spec = EvalSpec(
        id="probe", flow_id="expense-reimbursement",
        checkpoints=[Checkpoint(
            id="final", after_step="final",
            objects=[ObjectRule(
                type="ExpenseRequest",
                unlisted_fields="require",
                fields=[FieldRule(field="state", match="exact")],
            )],
        )],
    )
    case = EvalCase(
        id="drift", spec_id="probe", name="spec behind the flow",
        fixture=GraphFixture(objects=[FixtureObject(
            type="Employee", key="pat@customer.example",
            props={"email": "pat@customer.example"},
        )]),
        trigger={
            "employee": "pat@customer.example",
            "commentary": "Team lunch after the Acme review.",
            "hint": "total=64.20 category=meals",
        },
        expectations=[CheckpointExpectation(
            checkpoint="final",
            objects=[ExpectedObject(
                type="ExpenseRequest", key="$run", fields={"state": "approved"},
            )],
        )],
    )
    outcome = service.run_case(spec, case)
    drift = next(a for a in outcome.assertions if a.rule == "no-drift")
    assert not drift.passed
    assert "requested_usd" in drift.actual


def test_a_tbox_violation_fails_a_case_nobody_wrote_an_assertion_for(service):
    """The universal tier, applied per case at no authoring cost."""
    result = service.run(AP_SPEC.id, case_ids=["ap-known-vendor-clean"])
    assert result.cases[0].invariants == []
    assert result.cases[0].passed


# ---- isolation ------------------------------------------------------------


def test_an_eval_cannot_touch_the_live_world(world):
    """The reason separate collections was the call.

    Object ids derive from natural keys, so an eval fixture named "Acme
    Industrial Inc." *is* the production vendor id. If a run were not isolated
    it would append claims to a real vendor and — worse — feed eval invoices
    into the anomaly baseline real invoices are judged against.
    """
    service = EvalService(DeclaredSpecStore(), knowledge=world.knowledge)
    before_objects = world.graph.stats()
    before_runs = len(world.engine.list())
    before_tasks = len(world.tasks.directory.list())
    before_shelf = {a.id for a in world.knowledge.repo.all() if "exemplar" in a.tags}

    result = service.run(AP_SPEC.id)
    assert result.passed == len(result.cases)

    assert world.graph.stats() == before_objects
    assert len(world.engine.list()) == before_runs
    assert len(world.tasks.directory.list()) == before_tasks
    # The AP flow writes an exemplar when a clerk verifies one; the eval's
    # copy-on-write corpus is why that did not land on the real shelf.
    after_shelf = {a.id for a in world.knowledge.repo.all() if "exemplar" in a.tags}
    assert after_shelf == before_shelf


def test_each_case_gets_a_fresh_world(service):
    """Two cases minting the same vendor must not see each other's invoices."""
    result = service.run(
        AP_SPEC.id,
        case_ids=["ap-known-vendor-clean", "ap-price-variance-corrected"],
    )
    assert result.passed == 2
    assert len({c.run_id for c in result.cases}) == 2


# ---- the API -------------------------------------------------------------


def test_specs_endpoint_summarizes_the_contract(client, world):
    rows = client.get("/api/v1/evals").json()
    by_id = {r["id"]: r for r in rows}
    ap = by_id["accounts-payable-core"]
    assert ap["flow_id"] == "accounts-payable"
    assert ap["cases"] >= 5 and ap["checkpoints"] >= 3
    # Ignored fields are counted separately: a spec that ignores half the model
    # should look different from one that grades it.
    assert ap["graded_fields"] > 0 and ap["ignored_fields"] > 0
    assert ap["graded_links"] > 0


def test_spec_and_cases_are_readable(client, world):
    spec = client.get("/api/v1/evals/accounts-payable-core").json()
    assert [c["id"] for c in spec["checkpoints"]][0] == "vendor-resolved"
    cases = client.get("/api/v1/evals/accounts-payable-core/cases").json()
    assert any(c["fixture"]["deliveries"] for c in cases)
    assert client.get("/api/v1/evals/nope").status_code == 404


def test_running_needs_admin_but_a_key_may_do_it(client, owner, world):
    """CI launching an eval is the point of having keys; deciding a Task is not."""
    resp = client.post(
        "/api/v1/evals/expense-reimbursement-core/run", headers=owner
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["failed"] == 0 and body["passed"] > 0
    assert body["assertions"] > 0 and body["failed_assertions"] == 0
    assert body["result"]["cases"][0]["assertions"]

    again = client.get(
        "/api/v1/evals/expense-reimbursement-core/result", headers=owner
    ).json()
    assert again["at_iso"] == body["at_iso"]


def test_the_spec_is_exported_as_governed_knowledge(world):
    asset = world.knowledge.repo.get("flow-eval-specs")
    assert asset is not None and asset.status == "published"
    assert "accounts-payable-core" in asset.content
    # The ignore rules are the ones worth being able to review.
    assert "ignore" in asset.content
    assert "due_date" in asset.content
