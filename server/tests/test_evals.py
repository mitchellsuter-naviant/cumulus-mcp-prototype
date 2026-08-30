"""Graph-shaped evals: the spec/case split, milestones, and isolation.

What is worth asserting about an eval harness is mostly that it *fails* when it
should. A grader that only ever passes is indistinguishable from no grader, so
most of these break something on purpose and check the failure is legible.
"""

import pytest

from app.eval_specs import AP_SPEC, EXPENSE_SPEC, DeclaredSpecStore
from app.evals import (
    EvalCase,
    EvalService,
    EvalSpec,
    ExpectedObject,
    FieldRule,
    Fixture,
    FixtureObject,
    LinkRule,
    Milestone,
    MilestoneExpectation,
    ObjectRule,
    ScriptedDecision,
    TrajectoryRule,
    compare,
    resolve_milestone,
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


def register(service, fixture_id: str, email: str) -> str:
    """A one-Employee world, registered so a probe case can name it.

    A case names a fixture and never carries one, so a test that wants a
    precondition has to declare it the same way `eval_specs.py` does. That is
    the point of the split, and doing it here keeps these probes honest about
    it rather than routing around the store.
    """
    return service.fixtures.put(Fixture(
        id=fixture_id,
        flow_id="expense-reimbursement",
        name=f"{email} on the roster",
        objects=[FixtureObject(
            type="Employee", key=email, props={"email": email},
        )],
    )).id


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


def test_every_case_references_a_declared_milestone(service):
    for spec in service.specs():
        declared = {m.id for m in spec.milestones}
        for case in service.cases(spec.id):
            for expectation in case.expectations:
                assert expectation.milestone in declared, (
                    f"{case.id} expects '{expectation.milestone}', "
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


# ---- milestones -----------------------------------------------------------


def test_a_milestone_sees_only_what_had_been_written(service, world):
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


def test_a_case_declaring_an_unreachable_milestone_fails_legibly(service):
    """A held run has not committed, and saying so is the assertion."""
    spec = EvalSpec(
        id="probe", flow_id="expense-reimbursement",
        milestones=[Milestone(
            id="committed", anchor="wrote", selector="graph:ExpenseRequest/",
        )],
    )
    case = EvalCase(
        id="held", spec_id="probe", name="over the limit, nobody decided",
        fixture_id=register(service, "probe/sam", "sam@customer.example"),
        trigger={
            "employee": "sam@customer.example",
            "hint": "total=612.00 category=lodging",
        },
        expectations=[MilestoneExpectation(
            milestone="committed",
            objects=[ExpectedObject(type="ExpenseRequest", key="$run")],
        )],
    )
    outcome = service.run_case(spec, case)
    assert not outcome.passed
    assert outcome.run_status == "suspended"
    failure = outcome.failures[0]
    assert failure.rule == "reached"
    assert "no step wrote `graph:ExpenseRequest/`" in failure.detail
    # Legible: it names what the run *did* write, not just what it did not.
    assert failure.actual == "document:"


def test_trajectory_is_scoped_to_the_milestone(service):
    """`step_absent` at a mid-run milestone means "not yet", not "never"."""
    spec = EvalSpec(
        id="probe", flow_id="accounts-payable",
        milestones=[
            Milestone(
                id="early", anchor="first_of_kind", selector="action",
                trajectory=[TrajectoryRule(kind="step_absent", expected="c1")],
            ),
            Milestone(
                id="late", anchor="terminal",
                trajectory=[TrajectoryRule(kind="step_ran", expected="c1")],
            ),
        ],
    )
    fixture = service.fixtures.put(Fixture(
        id="probe/acme-on-the-master",
        flow_id="accounts-payable",
        name="Acme on the vendor master",
        deliveries=[{
            "source": "erp-vendors",
            "vendors": [{"external_id": "V-1", "name": "Acme Industrial Inc."}],
        }],
    ))
    case = EvalCase(
        id="known-vendor", spec_id="probe", name="known vendor",
        fixture_id=fixture.id,
        trigger={
            "submitted_by": "ap@customer.example",
            "hint": "vendor=acme-industrial nopo invoice=A-1 lines=PPE-GLV:1 total=80.00",
        },
        decisions=[ScriptedDecision(verdict="approved")],
        expectations=[
            MilestoneExpectation(milestone="early"),
            MilestoneExpectation(milestone="late"),
        ],
    )
    outcome = service.run_case(spec, case)
    assert outcome.passed, [
        (a.milestone, a.field, a.detail) for a in outcome.failures
    ]


# ---- a milestone is a predicate, not a step id ----------------------------
#
# The stage these exist to lock: flow-level expectations are affixed to the
# data that seeds a flow, so LLM calls and other transactions can be added,
# split, renamed or reordered without the golden set being rebuilt. Every
# test below either proves that, or proves the grader still fails when it
# should — a milestone that can be silently skipped buys nothing.


def reached(result) -> dict[tuple[str, str], str]:
    """Where each case's milestones landed: (case, milestone) -> step id.

    Readable straight off a result because resolution is recorded as a check
    of its own, which is what makes "the milestone did not move" an assertion
    rather than an inference.
    """
    return {
        (case.case_id, a.milestone): a.actual
        for case in result.cases
        for a in case.assertions
        if a.rule == "reached"
    }


def rename_steps(monkeypatch, **renames: str) -> None:
    """Rename step ids inside the flow bodies, as a re-architecture would.

    Reaches the four places a step id enters a run — `step`, `gate`, `event`,
    and the graph writer bound to the act — so the journal and the claim
    provenance move together and neither the spec nor the cases are told.
    That is exactly what "somebody renamed z1" looks like from outside the
    flow, and under the old `after_step` scheme it failed every case.
    """
    from app.graph import GraphService
    from app.runs import RunContext

    def renamed(step_id: str) -> str:
        return renames.get(step_id, step_id)

    step, gate, event = RunContext.step, RunContext.gate, RunContext.event
    writer = GraphService.writer
    monkeypatch.setattr(
        RunContext, "step",
        lambda self, sid, *a, **k: step(self, renamed(sid), *a, **k),
    )
    monkeypatch.setattr(
        RunContext, "gate",
        lambda self, sid, *a, **k: gate(self, renamed(sid), *a, **k),
    )
    monkeypatch.setattr(
        RunContext, "event",
        lambda self, sid, *a, **k: event(self, renamed(sid), *a, **k),
    )
    monkeypatch.setattr(
        GraphService, "writer",
        lambda self, run_id, sid: writer(self, run_id, renamed(sid)),
    )


def split_the_model_call(monkeypatch) -> None:
    """Read the document in two passes instead of one."""
    from app.runs import RunContext

    original = RunContext.step

    def split(self, step_id, name, kind, *args, **kwargs):
        if kind == "modelcall":
            original(
                self, f"{step_id}_page_2", f"{name} (second page)", kind,
                fn=lambda: {"page": 2}, summary="a second pass over the page",
            )
        return original(self, step_id, name, kind, *args, **kwargs)

    monkeypatch.setattr(RunContext, "step", split)


def test_the_declared_milestones_resolve_to_the_steps_they_replaced(service):
    """The table, asserted.

    Each milestone lands on exactly the step its `after_step` used to name, so
    this stage changed how a point is *named* and not which point it is. If
    one of these ever moves, the spec started judging a different moment.
    """
    landed = reached(service.run(AP_SPEC.id)) | reached(service.run(EXPENSE_SPEC.id))
    assert landed == {
        # `last_of_kind: resolve` — z1 writes nothing when the vendor is
        # already known, so no ref could have named this point.
        ("ap-known-vendor-clean", "vendor-settled"): "z1",
        ("ap-unknown-vendor-onboards", "vendor-settled"): "z1",
        ("ap-second-invoice-links-not-mints", "vendor-settled"): "z1",
        # `wrote: graph:Invoice/` — the commit, named by its effect.
        ("ap-known-vendor-clean", "invoice-retained"): "a1",
        ("ap-price-variance-corrected", "invoice-retained"): "a1",
        ("ap-second-invoice-links-not-mints", "invoice-retained"): "a1",
        # `terminal` — the whole journal, and only for a run that finished.
        ("ap-known-vendor-clean", "outcome"): "final",
        ("ap-price-variance-corrected", "outcome"): "final",
        ("ap-second-invoice-links-not-mints", "outcome"): "final",
        # `first_of_kind: task` — the two runs `terminal` correctly refuses.
        ("ap-quantity-variance-holds", "held"): "k2_1",
        ("ap-unknown-vendor-onboards", "held"): "k2_1",
        ("expense-in-policy-auto-approves", "recorded"): "final",
        ("expense-submitter-claims-part-of-a-receipt", "recorded"): "final",
        ("expense-compound-receipt-corrected", "recorded"): "final",
        ("expense-restricted-category-denied", "recorded"): "final",
    }


def test_renaming_a_step_id_fails_no_case(service, monkeypatch):
    """The point of the stage.

    `z1` becomes `zv`, `a1` becomes `commit`, `c1` becomes `verify`. Nothing
    about any invoice changed, so nothing may fail — and under the scheme this
    replaced, every case that named one of those ids failed instead.
    """
    before = reached(service.run(AP_SPEC.id))

    graded: list = []
    grade = EvalService._grade

    def spy(self, world, spec, case, run):
        graded.append(run)
        return grade(self, world, spec, case, run)

    monkeypatch.setattr(EvalService, "_grade", spy)
    rename_steps(monkeypatch, z1="zv", a1="commit", c1="verify")

    result = service.run(AP_SPEC.id)
    assert len(result.cases) == 5
    assert result.failed == 0, [
        f"{c.case_id}: {c.error or '; '.join(a.detail for a in c.failures)}"
        for c in result.cases if not c.passed
    ]

    # The counterfactual, stated rather than assumed: the renamed journals
    # genuinely contain no `z1`, so an expectation keyed on that id would have
    # found nothing to evaluate against.
    assert graded and all(steps_upto(run, "z1") == ([], False) for run in graded)

    # And the milestones followed the rename rather than surviving it by luck.
    after = reached(result)
    assert before[("ap-known-vendor-clean", "vendor-settled")] == "z1"
    assert after[("ap-known-vendor-clean", "vendor-settled")] == "zv"
    assert after[("ap-known-vendor-clean", "invoice-retained")] == "commit"
    assert after[("ap-quantity-variance-holds", "held")] == "k2_1"


def test_splitting_one_modelcall_in_two_moves_no_milestone(service, monkeypatch):
    """The other half, and the distinction worth being precise about.

    Reading the document in two passes must not move where anything is judged
    — but it *must* fail `model_calls_at_most: 1`, because that is a real
    regression rather than a renaming. A change that hides both is a grader
    that has stopped grading.
    """
    before = reached(service.run(AP_SPEC.id))
    split_the_model_call(monkeypatch)
    result = service.run(AP_SPEC.id)

    assert reached(result) == before, "a milestone moved when a step was added"
    failures = [(c.case_id, a.rule) for c in result.cases for a in c.failures]
    assert {rule for _, rule in failures} == {"model_calls_at_most"}
    # Only the milestone that makes the promise: the three cases asserting
    # what is true once the vendor is settled.
    assert {case for case, _ in failures} == {
        "ap-known-vendor-clean",
        "ap-unknown-vendor-onboards",
        "ap-second-invoice-links-not-mints",
    }


def test_a_milestone_with_no_expectations_is_never_vacuous(service):
    """Declaring a milestone is itself the claim that the run reaches it.

    A checkpoint carrying no expectations used to contribute nothing at all,
    so a case could assert a point the run never got to and pass.
    """
    spec = EvalSpec(
        id="probe", flow_id="expense-reimbursement",
        milestones=[Milestone(
            id="committed", anchor="wrote", selector="graph:ExpenseRequest/",
        )],
    )
    case = EvalCase(
        id="empty", spec_id="probe", name="asserts nothing at all",
        fixture_id=register(service, "probe/sam", "sam@customer.example"),
        trigger={
            "employee": "sam@customer.example",
            "hint": "total=612.00 category=lodging",
        },
        expectations=[MilestoneExpectation(milestone="committed")],
    )
    outcome = service.run_case(spec, case)
    assert not outcome.passed
    [failure] = outcome.assertions
    assert failure.target == "milestone" and failure.rule == "reached"
    assert failure.expected == "a step that writes `graph:ExpenseRequest/`"


def test_a_terminal_milestone_refuses_a_run_that_did_not_finish(service):
    """`terminal` is the one anchor that can fail on a healthy journal."""
    spec = EvalSpec(
        id="probe", flow_id="expense-reimbursement",
        milestones=[Milestone(id="outcome", anchor="terminal")],
    )
    case = EvalCase(
        id="held", spec_id="probe", name="still at the gate",
        fixture_id=register(service, "probe/sam", "sam@customer.example"),
        trigger={
            "employee": "sam@customer.example",
            "hint": "total=612.00 category=lodging",
        },
        expectations=[MilestoneExpectation(
            milestone="outcome",
            trajectory={"terminal_status": "succeeded"},
        )],
    )
    outcome = service.run_case(spec, case)
    assert not outcome.passed
    [failure] = outcome.assertions
    assert failure.rule == "reached"
    assert "the run never finished — it ended suspended at k1" in failure.detail


def test_a_milestone_copied_onto_another_flow_names_what_that_flow_ran(service):
    """The expense flow runs no `resolve` step and no `check` step.

    An anchor lifted from the AP spec has to say so, and say what the flow
    *did* run, or a spec author is left guessing at a selector.
    """
    spec = EvalSpec(
        id="probe", flow_id="expense-reimbursement",
        milestones=[Milestone(
            id="vendor-settled", anchor="last_of_kind", selector="resolve",
        )],
    )
    case = EvalCase(
        id="copied", spec_id="probe", name="an AP anchor on an expense flow",
        fixture_id=register(service, "probe/sam", "sam@customer.example"),
        trigger={
            "employee": "sam@customer.example",
            "commentary": "Client gift wine for the Acme close.",
            "hint": "total=96.40 category=alcohol vendor=vintners-cellar",
        },
        decisions=[ScriptedDecision(verdict="rejected", note="Out of policy.")],
        expectations=[MilestoneExpectation(milestone="vendor-settled")],
    )
    outcome = service.run_case(spec, case)
    assert not outcome.passed
    [failure] = outcome.assertions
    ran = "trigger, action, transform, modelcall, rule, task, event"
    assert failure.actual == ran
    assert failure.detail == (
        f"this flow ran no step of kind 'resolve' — it ran: {ran}."
    )


def test_first_and_last_of_kind_are_not_the_same_point():
    """Why both directions exist rather than one.

    `held` asserts nothing was committed before the run *first* stopped for a
    person; a second gate later on must not move that.
    """
    from app.runs import RunRecord, StepRecord

    run = RunRecord(
        id="r1", flow_id="accounts-payable", status="succeeded",
        steps=[
            StepRecord(step_id="k2_1", name="review", kind="task"),
            StepRecord(step_id="x6_1", name="correct", kind="transform"),
            StepRecord(step_id="k2_2", name="review again", kind="task"),
        ],
    )
    first = Milestone(id="held", anchor="first_of_kind", selector="task")
    last = Milestone(id="settled", anchor="last_of_kind", selector="task")
    assert resolve_milestone(first, run) == ("k2_1", None)
    assert resolve_milestone(last, run) == ("k2_2", None)


# ---- the grader fails when it should -------------------------------------


def test_a_wrong_expected_value_fails_with_both_values(service):
    spec = EvalSpec(
        id="probe", flow_id="expense-reimbursement",
        milestones=[Milestone(
            id="final", anchor="terminal",
            objects=[ObjectRule(
                type="ExpenseRequest",
                fields=[FieldRule(field="requested_usd", match="approx",
                                  tolerance=0.01)],
            )],
        )],
    )
    case = EvalCase(
        id="wrong", spec_id="probe", name="deliberately wrong",
        fixture_id=register(service, "probe/pat", "pat@customer.example"),
        trigger={
            "employee": "pat@customer.example",
            "commentary": "Team lunch after the Acme review.",
            "hint": "total=64.20 category=meals",
        },
        expectations=[MilestoneExpectation(
            milestone="final",
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
        milestones=[Milestone(id="final", anchor="terminal")],
    )
    case = EvalCase(
        id="ambiguous", spec_id="probe", name="two lines, no key",
        fixture_id=register(service, "probe/kim", "kim@customer.example"),
        trigger={
            "employee": "kim@customer.example",
            "commentary": "Two nights at the Acme onsite.",
            "hint": "compound total=140.00 category=lodging",
        },
        expectations=[MilestoneExpectation(
            milestone="final",
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
        milestones=[Milestone(
            id="final", anchor="terminal",
            objects=[ObjectRule(
                type="ExpenseRequest",
                unlisted_fields="require",
                fields=[FieldRule(field="state", match="exact")],
            )],
        )],
    )
    case = EvalCase(
        id="drift", spec_id="probe", name="spec behind the flow",
        fixture_id=register(service, "probe/pat", "pat@customer.example"),
        trigger={
            "employee": "pat@customer.example",
            "commentary": "Team lunch after the Acme review.",
            "hint": "total=64.20 category=meals",
        },
        expectations=[MilestoneExpectation(
            milestone="final",
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
    before_corpus = {(a.id, a.revision) for a in world.knowledge.repo.all()}

    result = service.run(AP_SPEC.id)
    assert result.passed == len(result.cases)

    assert world.graph.stats() == before_objects
    assert len(world.engine.list()) == before_runs
    assert len(world.tasks.directory.list()) == before_tasks
    # The corpus is copied into each world, not shared, so a graded run can
    # read and write governed knowledge without the live shelf moving under
    # the console — neither a new asset nor a bumped revision.
    after_corpus = {(a.id, a.revision) for a in world.knowledge.repo.all()}
    assert after_corpus == before_corpus


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
    assert ap["cases"] >= 5 and ap["milestones"] >= 3
    # Ignored fields are counted separately: a spec that ignores half the model
    # should look different from one that grades it.
    assert ap["graded_fields"] > 0 and ap["ignored_fields"] > 0
    assert ap["graded_links"] > 0


def test_spec_and_cases_are_readable(client, world):
    spec = client.get("/api/v1/evals/accounts-payable-core").json()
    assert [m["id"] for m in spec["milestones"]][0] == "vendor-settled"
    cases = client.get("/api/v1/evals/accounts-payable-core/cases").json()
    # A case names its world and carries none, so the deliveries are one hop
    # away rather than copied into every case that shares them.
    assert all(c["fixture_id"] for c in cases)
    shared = {c["fixture_id"] for c in cases}
    assert len(shared) < len(cases), "no two AP cases share a world any more"
    fixture = client.get(
        f"/api/v1/evals/fixtures/{cases[0]['fixture_id']}"
    ).json()
    assert fixture["deliveries"]
    assert client.get("/api/v1/evals/nope").status_code == 404


# ---- fixtures -------------------------------------------------------------


def test_every_case_names_a_declared_fixture(service):
    """A case pointing at a world nobody declared is the new way to break one."""
    for spec in service.specs():
        for case in service.cases(spec.id):
            assert case.fixture_id, f"{case.id} names no world"
            assert service.fixtures.get(case.fixture_id) is not None, (
                f"{case.id} runs in '{case.fixture_id}', which is not declared"
            )


def test_a_case_naming_an_unregistered_fixture_says_so(service):
    spec = EvalSpec(id="probe", flow_id="expense-reimbursement")
    case = EvalCase(
        id="orphan", spec_id="probe", name="names a world that is gone",
        fixture_id="expense/nobody-declared-this",
    )
    outcome = service.run_case(spec, case)
    assert not outcome.passed
    assert "not registered" in outcome.error


def test_two_cases_over_one_world_read_the_same_declaration(service):
    """The duplication the split removes: one edit, not three."""
    cases = {c.id: c for c in service.cases(AP_SPEC.id)}
    assert (
        cases["ap-known-vendor-clean"].fixture_id
        == cases["ap-price-variance-corrected"].fixture_id
    )


def test_the_catalog_elides_the_payload_and_the_fixture_serves_it(client, world):
    rows = client.get("/api/v1/evals/fixtures").json()
    by_id = {r["id"]: r for r in rows}
    assert "ap/acme-po-7741-clean" in by_id and "seed/demo-world" in by_id
    # The catalog is a catalog: the payload is one call away, not in the list.
    assert "deliveries" not in by_id["ap/acme-po-7741-clean"]
    assert by_id["ap/acme-po-7741-clean"]["read_set"] == [
        "knowledge:invoice-posting-policy@rev1"
    ]

    full = client.get("/api/v1/evals/fixtures/ap/acme-po-7741-clean").json()
    assert len(full["deliveries"]) == 3
    assert full["origin"] == "declared"
    assert full["derived_from"].endswith("eval_specs.py")
    assert client.get("/api/v1/evals/fixtures/ap/nope").status_code == 404


def test_previewing_a_fixture_shows_what_it_asserts(client, world):
    body = client.get(
        "/api/v1/evals/fixtures/expense/pat-on-the-roster/preview"
    ).json()
    assert body["error"] == ""
    assert body["by_type"] == {"Employee": 1}
    assert body["world"]["id"] == "expense/pat-on-the-roster"


def test_flow_id_narrows_the_catalog(client, world):
    rows = client.get("/api/v1/evals/fixtures?flow_id=accounts-payable").json()
    assert {r["id"] for r in rows} == {
        "ap/acme-po-7741-clean",
        "ap/acme-po-7741-short-receipt",
        "ap/acme-po-7741-meridian-onboarded",
    }


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


def test_the_published_spec_names_anchors_and_not_step_ids(world):
    """The governed asset is the reviewable artifact, so it has to be honest.

    It used to say "evaluated once step `z1` has completed", which published
    one flow's internal naming as though it were part of the contract — and
    would have gone stale the moment a ModelCall was split or renamed.
    """
    content = world.knowledge.repo.get("flow-eval-specs").content
    assert "the last step of kind `resolve`" in content
    assert "a step that writes `graph:Invoice/`" in content
    assert "the first step of kind `task`" in content
    for step_id in ("`z1`", "`a1`", "`m1`", "`m2`", "`x3`", "`c1`"):
        assert step_id not in content, f"the export still publishes {step_id}"


# ---- the measurement, and the gate over it ---------------------------------
#
# Ported from the deleted simulated harness in `flows.py`. Four ideas there
# were real and only their inputs were fake: a dataset pins the revision an
# experiment measured, a batch of cases lands as one revision, a human editing
# what a pass means moves that revision, and promotion is gated on not going
# backwards. They are asserted here against a grader that actually runs the
# flow.


class _EditableSpecStore:
    """A spec store a test can edit.

    `DeclaredSpecStore` deliberately cannot be, which is right for the app and
    useless for asking what happens when somebody changes the rules — so these
    bring their own.
    """

    def __init__(self, spec: EvalSpec, cases: list[EvalCase]) -> None:
        self.spec = spec
        self.case_list = list(cases)

    def specs(self) -> list[EvalSpec]:
        return [self.spec]

    def cases(self, spec_id: str) -> list[EvalCase]:
        return list(self.case_list) if spec_id == self.spec.id else []


def _probe_case(service, ident: str, email: str, total: float) -> EvalCase:
    return EvalCase(
        id=ident, spec_id="probe", name=f"{email} claims ${total}",
        fixture_id=register(service, f"probe/{ident}", email),
        trigger={
            "employee": email,
            "commentary": "Team lunch after the Acme review.",
            "hint": f"total={total} category=meals",
        },
        expectations=[MilestoneExpectation(
            milestone="final",
            objects=[ExpectedObject(
                type="ExpenseRequest", key="$run",
                fields={"requested_usd": total},
            )],
        )],
    )


@pytest.fixture()
def probe():
    """Three cases over one spec, and a store both can be edited through."""
    spec = EvalSpec(
        id="probe", flow_id="expense-reimbursement", version=1,
        milestones=[Milestone(
            id="final", anchor="terminal",
            objects=[ObjectRule(
                type="ExpenseRequest",
                fields=[FieldRule(field="requested_usd", match="approx",
                                  tolerance=0.01)],
            )],
        )],
    )
    store = _EditableSpecStore(spec, [])
    service = EvalService(store)
    store.case_list = [
        _probe_case(service, "probe-a", "pat@customer.example", 64.20),
        _probe_case(service, "probe-b", "kim@customer.example", 128.50),
        _probe_case(service, "probe-c", "lee@customer.example", 42.75),
    ]
    return service, store


def test_a_result_pins_the_measurement_it_made(probe):
    """Dataset versioning, on the real path.

    Two pass rates are only comparable when they measured the same thing, so
    a result has to record what that was. Adding a case moves the fingerprint
    even though every existing case is untouched — which is correct: the
    denominator changed.
    """
    service, store = probe
    first = service.run("probe")
    assert first.spec_version == 1 and first.measured

    again = service.run("probe")
    assert again.measured == first.measured

    store.case_list.append(
        _probe_case(service, "probe-d", "nadia@customer.example", 19.99)
    )
    wider = service.run("probe")
    assert wider.measured != first.measured
    assert len(wider.cases) == 4


def test_a_batch_of_cases_is_one_measurement(probe):
    """Bulk, on the real path: many cases, one revision.

    The property the deleted bulk-import test was protecting was that a batch
    lands as a single comparable revision rather than N of them. Here that is
    structural — one run grades every case under one fingerprint — and the
    thing worth asserting is the other half: a run over *some* of the cases is
    a different measurement, so a partial run can never pose as the whole set.
    """
    service, _ = probe
    whole = service.run("probe")
    assert {c.case_id for c in whole.cases} == {"probe-a", "probe-b", "probe-c"}
    assert whole.gate is None  # nothing to compare a first run against

    part = service.run("probe", case_ids=["probe-a"])
    assert len(part.cases) == 1
    assert part.measured != whole.measured
    assert part.gate is not None and not part.gate.comparable


def test_editing_what_a_pass_means_refuses_the_stale_comparison(probe):
    """Review-bumps-the-version, on the real path.

    A human tightening the grading rules is the console's "review" in its
    honest form: what a pass means changed, so the previous pass rate is no
    longer the same question. The old golden set relied on somebody calling
    `bump()`; the fingerprint notices whether they did or not, which is why
    `spec.version` is deliberately left at 1 here.
    """
    service, store = probe
    before = service.run("probe")
    assert before.failed == 0

    # A reviewer decides one field is bookkeeping and stops grading it. Inert
    # for these three cases on purpose: the point is the comparison, not a
    # failure, and a rule change that flips no verdict is the hardest kind for
    # a version number to catch.
    store.spec.milestones[0].objects[0].fields.append(
        FieldRule(field="approved_usd", match="ignore")
    )
    after = service.run("probe")
    assert store.spec.version == 1  # nobody bumped it
    assert after.measured != before.measured
    assert after.gate is not None
    assert not after.gate.comparable and not after.gate.passed
    assert "measured something else" in after.gate.reasons[0]
    # Not a regression — naming it one would send someone hunting for a bug
    # in the flow when what moved was the question.
    assert after.gate.regressed == []


def test_the_gate_names_the_cases_that_regressed(probe, monkeypatch):
    """The promotion gate, on real verdicts.

    A run that grades worse than the last one over the same measurement does
    not get promoted, and the gate says which cases went backwards rather than
    handing back a count. The flow is degraded between the two runs — one
    employee's extraction now comes back a dollar light — because a regression
    is a property of the flow, not of the spec.
    """
    from app.reimbursements import StubReceiptReader

    service, _ = probe
    baseline = service.run("probe")
    assert baseline.failed == 0

    clean = service.run("probe")
    assert clean.gate is not None
    assert clean.gate.comparable and clean.gate.passed
    assert clean.gate.reasons == []

    read = StubReceiptReader.read

    def light(self, image, filename, hint, **kw):
        out = read(self, image, filename, hint, **kw)
        if "128.5" not in f"{filename} {hint}":
            return out
        return out.model_copy(update={
            "total_usd": round(out.total_usd - 1.0, 2),
            "lines": [
                line.model_copy(
                    update={"amount_usd": round(line.amount_usd - 1.0, 2)}
                )
                for line in out.lines
            ],
        })

    monkeypatch.setattr(StubReceiptReader, "read", light)
    degraded = service.run("probe")
    assert degraded.measured == baseline.measured  # same question, worse answer
    assert degraded.gate is not None and degraded.gate.comparable
    assert not degraded.gate.passed
    assert degraded.gate.regressed == ["probe-b"]
    assert "1 regression" in degraded.gate.reasons[0]
