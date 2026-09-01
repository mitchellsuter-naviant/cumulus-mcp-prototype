"""Where an elevated request goes, and why that desk.

The expense policy has carried an approval table since it was written — *under
$500, direct manager; $500–5,000, department head; over $5,000, VP of Finance*
— and the flow sent every elevated request to one hardcoded queue regardless.
These are the assertions that the document and the machinery now agree.
"""

import pytest

from app import routing
from app.queues import personal_slug
from tests import demo_world
from tests import world as test_world

AE = "john.smith@naviant.com"          # reports to the sales manager
VP_SALES = "marcus.reyes@naviant.com"  # reports to the CEO
CEO = "diane.vogel@naviant.com"        # reports to nobody


@pytest.fixture()
def world():
    w = test_world.fresh()
    demo_world.seed_workforce(w.sync, w.graph)
    return w


# ---- reading the governed table ---------------------------------------------


def test_the_table_in_the_document_is_the_table_that_runs(world):
    table = routing.parse_routing(world.expenses.read_policy_content())
    assert table.found_asset
    assert table.target_for(210.00) == routing.MANAGER_OF
    assert table.target_for(6_500.00) == "expense-review-exec"
    assert table.target_for(40.00, restricted=True) == "expense-review-exec"


def test_a_policy_with_no_routing_section_routes_everything_to_the_fallback():
    """A finance lead edits this in the console. A missing section has to
    degrade to the fallback, not raise in the middle of a reimbursement."""
    table = routing.parse_routing("# Expense Policy\n\nNo routing here.\n")
    assert not table.found_asset
    assert table.target_for(9_999.00) == "expense-review"


def test_prose_that_names_no_real_desk_is_dropped_rather_than_slugified():
    """Inventing `vp-of-finance` from "VP of Finance" would name a desk nobody
    created and route real work into it."""
    table = routing.parse_routing(
        "## Approval routing\n\n| Amount | Desk |\n| --- | --- |\n"
        "| Over $5,000 | VP of Finance |\n"
    )
    assert table.target_for(6_000.00) == "expense-review"


def test_the_document_can_spell_the_resolver_in_english():
    table = routing.parse_routing(
        "## Approval routing\n\n| Amount | Desk |\n| --- | --- |\n"
        "| Over $0 | Direct manager |\n"
    )
    assert table.target_for(10.00) == routing.MANAGER_OF


# ---- answering it against the org chart --------------------------------------


def test_the_org_chart_answers_who_approves_this_person(world):
    assert routing.manager_of(world.graph, AE)[0]
    assert routing.manager_of(world.graph, VP_SALES)[0] == CEO
    # The top of the chart reports to nobody, which is a real answer.
    assert routing.manager_of(world.graph, CEO) is None


def test_resolving_lands_on_the_managers_own_desk(world):
    resolved = routing.resolve(
        routing.MANAGER_OF, submitter_email=VP_SALES, graph=world.graph,
        queues=world.queues, fallback="expense-review",
    )
    assert resolved.queue == personal_slug(CEO)
    assert not resolved.fell_back
    assert "Diane Vogel" in resolved.explanation
    # The desk is real and its holder is the manager — not a slug pointing at
    # nothing, which would be work that exists and nobody will ever see.
    assert world.queues.get(resolved.queue).holds(CEO)


def test_a_submitter_with_no_manager_falls_back_and_says_so(world):
    """Silence here would route a VP's expense to the wrong desk and look
    exactly like success."""
    resolved = routing.resolve(
        routing.MANAGER_OF, submitter_email=CEO, graph=world.graph,
        queues=world.queues, fallback="expense-review",
    )
    assert resolved.queue == "expense-review"
    assert resolved.fell_back
    assert "no manager" in resolved.explanation


def test_somebody_outside_the_org_chart_falls_back(world):
    resolved = routing.resolve(
        routing.MANAGER_OF, submitter_email="contractor@example.com",
        graph=world.graph, queues=world.queues, fallback="expense-review",
    )
    assert resolved.fell_back and resolved.queue == "expense-review"


def test_resolving_twice_mints_one_desk(world):
    """A replayed step must pick the same queue and not a second inbox."""
    first = routing.resolve(
        routing.MANAGER_OF, submitter_email=VP_SALES, graph=world.graph,
        queues=world.queues, fallback="expense-review",
    )
    again = routing.resolve(
        routing.MANAGER_OF, submitter_email=VP_SALES, graph=world.graph,
        queues=world.queues, fallback="expense-review",
    )
    assert first.queue == again.queue
    assert len([q for q in world.queues.list() if q.personal]) == 1


# ---- through the flow ---------------------------------------------------------


def _submit(world, email: str, hint: str, commentary: str = "Client dinner."):
    from app.reimbursements import SubmittedDocument

    return world.expenses.submit(
        email,
        documents=[SubmittedDocument(filename="receipt.jpg", hint=hint)],
        commentary=commentary,
    )


def test_an_elevated_request_lands_on_the_submitters_manager(world):
    request = _submit(world, VP_SALES, "total=850.00 category=meals")
    assert request.state == "pending"
    task = world.tasks.get(request.task_id)
    assert task.queue == personal_slug(CEO)


def test_a_large_request_goes_to_the_executive_desk_instead(world):
    request = _submit(world, VP_SALES, "total=7500.00 category=travel")
    task = world.tasks.get(request.task_id)
    assert task.queue == "expense-review-exec"


def test_a_restricted_category_goes_to_the_executive_desk_at_any_amount(world):
    request = _submit(world, VP_SALES, "total=90.00 category=alcohol")
    task = world.tasks.get(request.task_id)
    assert task.queue == "expense-review-exec"


def test_the_journal_records_which_desk_and_why(world):
    """A queue slug says where work went. A reader needs to know whether that
    was the governed table or a fallback covering a gap in the org chart."""
    request = _submit(world, VP_SALES, "total=850.00 category=meals")
    step = world.engine.get(request.run_id).step("x2r")
    assert step is not None and step.status == "done"
    assert "Diane Vogel" in step.summary
    assert step.output["fell_back"] is False


def test_routing_is_recorded_even_when_nobody_was_asked(world):
    """"This would have gone to Diane" is worth knowing about a request that
    auto-approved — it is what makes the table auditable rather than merely
    effective."""
    request = _submit(world, VP_SALES, "total=40.00 category=meals",
                      commentary="Coffee for the team standup.")
    assert request.state == "approved"
    step = world.engine.get(request.run_id).step("x2r")
    assert step is not None and step.output["queue"] == personal_slug(CEO)


def test_editing_the_routing_table_moves_the_next_request(world):
    """The whole claim of the knowledge layer, tested on routing rather than on
    a threshold: no code change, and the edit is logged."""
    from app.models import AssetUpdate

    before = _submit(world, VP_SALES, "total=850.00 category=meals")
    assert world.tasks.get(before.task_id).queue == personal_slug(CEO)

    asset = world.knowledge.repo.get("expense-approval-policy")
    world.knowledge.update(
        "expense-approval-policy",
        AssetUpdate(content=asset.content.replace(
            "| Over $0 | manager-of(submitter) |",
            "| Over $0 | expense-review |",
        )),
    )
    after = _submit(world, VP_SALES, "total=851.00 category=meals")
    assert world.tasks.get(after.task_id).queue == "expense-review"


def test_a_step_that_threw_goes_to_operations_not_to_the_approver(world):
    """The Controller cannot fix a mime type, and the engineer who can was not
    looking at the finance queue."""
    from app.media import UnsupportedMediaError

    class Broken:
        name = "broken"

        def read(self, image, filename, hint, *, prompt="", media_type=""):
            raise UnsupportedMediaError("image/heic", "IMG_9.HEIC")

    world.expenses.reader = Broken()
    request = _submit(world, VP_SALES, "total=90.00 category=meals")
    run = world.engine.get(request.run_id)
    assert run.status == "suspended"
    assert world.tasks.get(run.open_task_id).queue == routing.OPS_QUEUE


def test_an_unreadable_file_is_only_attempted_once(world):
    """Retrying a deterministic refusal is just a slower way to fail."""
    from app.media import UnsupportedMediaError

    attempts = []

    class Broken:
        name = "broken"

        def read(self, image, filename, hint, *, prompt="", media_type=""):
            attempts.append(media_type)
            raise UnsupportedMediaError("image/heic", "IMG_9.HEIC")

    world.expenses.reader = Broken()
    request = _submit(world, VP_SALES, "total=90.00 category=meals")
    assert len(attempts) == 1
    run = world.engine.get(request.run_id)
    assert "resubmit as PDF or JPEG" in run.step("m1").error


# ---- recovering a run --------------------------------------------------------


def test_a_suspended_run_retries_the_failed_step_and_replays_the_rest(world):
    """The whole point of a durable journal: fix the cause, resume, and only
    the step that broke runs again."""
    from app.media import UnsupportedMediaError
    from app.reimbursements import ReceiptExtraction, ExtractedLine

    calls = {"n": 0}

    class Flaky:
        name = "flaky"

        def read(self, image, filename, hint, *, prompt="", media_type=""):
            calls["n"] += 1
            if calls["n"] == 1:
                raise UnsupportedMediaError("image/heic", "IMG_9.HEIC")
            return ReceiptExtraction(
                vendor="corner-bistro", date="2026-09-01", total_usd=90.0,
                category="meals", confidence=0.95, model="test",
                lines=[ExtractedLine(
                    vendor="corner-bistro", date="2026-09-01",
                    category="meals", amount_usd=90.0, confidence=0.95,
                )],
            )

    world.expenses.reader = Flaky()
    request = _submit(world, VP_SALES, "total=90.00 category=meals")
    run = world.engine.get(request.run_id)
    assert run.status == "suspended" and run.step("m1").status == "failed"
    replayed_before = [s.step_id for s in run.steps if s.status == "done"]

    resumed = world.engine.resume(request.run_id)
    assert resumed.step("m1").status == "done"
    assert calls["n"] == 2  # the failed step ran again; nothing else did
    for step_id in replayed_before:
        assert resumed.step(step_id).status == "done"


def test_resuming_settles_the_task_it_answered(world):
    """A `POST /runs/{id}/resume` cleared the run's pointer to the task and
    left the task itself open — a row in somebody's queue for work already
    moving."""
    from app.media import UnsupportedMediaError
    from app.reimbursements import ExtractedLine, ReceiptExtraction

    calls = {"n": 0}

    class Fixed:
        """Broken, then deployed. The point of the exception task is that the
        engineer fixes the cause and resumes; a reader that stays broken
        re-raises the same task, which is correct and tests nothing."""

        name = "fixed"

        def read(self, image, filename, hint, *, prompt="", media_type=""):
            calls["n"] += 1
            if calls["n"] == 1:
                raise UnsupportedMediaError("image/heic", "IMG_9.HEIC")
            return ReceiptExtraction(
                vendor="corner-bistro", date="2026-09-01", total_usd=90.0,
                category="meals", confidence=0.95, model="test",
                lines=[ExtractedLine(
                    vendor="corner-bistro", date="2026-09-01",
                    category="meals", amount_usd=90.0, confidence=0.95,
                )],
            )

    world.expenses.reader = Fixed()
    request = _submit(world, VP_SALES, "total=90.00 category=meals")
    task_id = world.engine.get(request.run_id).open_task_id
    assert world.tasks.get(task_id).status == "open"

    world.engine.resume(request.run_id)
    assert world.tasks.get(task_id).status == "done"
    assert "Resumed directly" in (world.tasks.get(task_id).decision.note or "")


def test_a_step_that_is_still_broken_raises_its_task_again(world):
    """Resuming without fixing the cause must leave the work visible, not
    quietly settle a task over a run that failed the same way twice."""
    from app.media import UnsupportedMediaError

    class Broken:
        name = "broken"

        def read(self, image, filename, hint, *, prompt="", media_type=""):
            raise UnsupportedMediaError("image/heic", "IMG_9.HEIC")

    world.expenses.reader = Broken()
    request = _submit(world, VP_SALES, "total=90.00 category=meals")
    world.engine.resume(request.run_id)

    run = world.engine.get(request.run_id)
    assert run.status == "suspended"
    assert world.tasks.get(run.open_task_id).status == "open"


def test_a_person_deciding_the_gate_keeps_their_own_verdict(world):
    """The ordinary path is a person deciding and *that* resuming the run.
    Settling the task on resume must not overwrite what they said."""
    request = _submit(world, VP_SALES, "total=850.00 category=meals")
    task_id = world.engine.get(request.run_id).open_task_id
    world.tasks.decide(task_id, verdict="rejected", decided_by="diane",
                       note="Out of budget this quarter.")
    world.expenses.resume_for_task(world.tasks.get(task_id))

    decision = world.tasks.get(task_id).decision
    assert decision.verdict == "rejected"
    assert decision.decided_by == "diane"
    assert "Out of budget" in decision.note


def test_a_terminally_failed_run_can_be_retried_once_the_cause_is_fixed(world):
    """Without this the journal sat there, complete and correct, with no way to
    re-enter it — and resubmitting loses the case's history and mints a second
    one."""
    from app.runs import RunContext

    attempts = {"n": 0}

    def body(ctx: RunContext, inputs: dict) -> dict:
        ctx.event("t1", "Started", "began", kind="trigger")

        def work():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("the bug")
            return {"ok": True}

        return ctx.step("s1", "do-the-thing", "action", fn=work)

    world.engine.register("retryable-flow", body)
    run = world.engine.start("retryable-flow", {})
    assert run.status == "failed" and run.error

    # The old behaviour: a failed run came back untouched, and the step never
    # ran again however many times you asked.
    assert world.engine.resume(run.id).status == "failed"
    assert attempts["n"] == 1

    retried = world.engine.resume(run.id, retry_failed=True)
    assert retried.status == "succeeded"
    assert retried.step("t1").status == "done"   # replayed, not re-run
    assert attempts["n"] == 2
