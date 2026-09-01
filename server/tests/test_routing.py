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
