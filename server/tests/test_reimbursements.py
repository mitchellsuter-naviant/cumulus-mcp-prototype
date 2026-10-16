"""The expense-reimbursement Flow: documents, facts, gates, and the graph.

The doctrinal assertions, in one place:

- the Rule's values come from the governed policy document, so editing it
  changes behaviour with no code change;
- a document is not a fact — a compound receipt yields two lines, and the
  submitter can add one the extraction missed;
- the submitter's own commentary is a real input, not decoration;
- review is not one round: a correction re-runs the checks;
- the run writes entities, so "how much has this employee submitted" is a
  traversal rather than a table scan;
- submission is a human act (keys refused).

**v10 is the live version, and it changed the shape of most of these.** Every
run now stops for its submitter to confirm what was read off their receipt, so
a test that used to assert `state == "approved"` immediately after `submit`
asserts it after a confirmation instead — `_confirm` below. The doors v10 shut
have tests of their own further down: no evidence, no run; the memo never
reaches the extractor; no invented boxes on a real photograph.
"""

import pytest

from app.models import AssetUpdate
from app.queues import personal_slug
from app.tasks.expense_reimbursement.domain import (
    SubmittedDocument,
    apply_correction,
    parse_requested_amount,
)
from tests import demo_world
from tests import world as test_world


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


def _doc(hint: str = "", memo: str = "", filename: str = "receipt.jpg",
         content_b64: str = ""):
    """One submitted receipt.

    `hint` is the stub reader's steering seam — reachable from a test, a
    fixture or the seed, and from no REST body. `memo` is the submitter's own
    words, which the run reads for a stated claim and a purpose and never gives
    to the extractor. Keeping them apart in the fixture is the point: that is
    the distinction v10 exists to make.
    """
    return SubmittedDocument(
        filename=filename, memo=memo, hint=hint, content_b64=content_b64,
    )


def _confirm(world, request, decided_by: str = "", **kwargs):
    """The submitter confirming their own values: v10's ordinary path.

    Defaults to the person who submitted, because that is who the governed
    routing table sends it to.
    """
    decided = world.tasks.decide(
        request.task_id, kwargs.pop("verdict", "approved"),
        decided_by or request.employee, **kwargs,
    )
    return world.expenses.resume_for_task(decided)


def _submit(world, employee: str, **kwargs):
    return world.expenses.submit(employee, **kwargs)


def _through(world, employee: str, **kwargs):
    """Submit and confirm in one move, for tests about something else."""
    return _confirm(world, _submit(world, employee, **kwargs))


# ---- the map is the run ----------------------------------------------------
#
# v9 declared seven steps and executed twelve. Four executed families — `s1`
# store-documents, `x0` read-prompt, `x2` project-lines, `x2r` resolve-approver
# — appeared nowhere in the Tier-2 export, and the Event the export named
# (`ReimbursementRecorded`) was not the one the body emitted
# (`ExpenseRecorded`). A map that omits a third of the run and misnames its
# output is the transparency bar drawn over a different flow.


def _family(step_id: str) -> str:
    """One executed step id, reduced to the family the map declares.

    A repeated step is `base_{n}` — the extraction per document, the check and
    the gate per round — which is the convention every flow package uses, so
    the reduction is one rule and not a table.
    """
    import re

    return re.sub(r"_\d+$", "", step_id)


def test_the_declared_steps_are_the_steps_that_run(world):
    from app.tasks.expense_reimbursement import v10

    declared = [step.id for step in v10.STEPS]
    assert len(declared) == len(set(declared))

    # A run that exercises every family: two documents (so the extraction
    # fans out), a finding, a correction round, and a commit.
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[
            _doc(hint="total=612.00 category=lodging", filename="hotel.pdf"),
            _doc(hint="total=38.50 category=meals", filename="lunch.jpg"),
        ],
        commentary="Conference block rate and dinner at the finance summit.",
    )
    corrected = _confirm(
        world, request, verdict="corrected",
        payload={"lines": [{"ordinal": 1, "requested_usd": 590.0}]},
    )
    final = _confirm(world, corrected)
    assert final.state == "approved"

    run = world.engine.get(request.run_id)
    executed = [_family(s.step_id) for s in run.steps]
    assert set(executed) == set(declared)
    # And in the declared order, families collapsed: an export whose arrows
    # point somewhere the run does not go is the same defect one level down.
    assert list(dict.fromkeys(executed)) == declared


def test_the_declared_event_is_the_event_the_run_emits(world):
    from app.tasks.expense_reimbursement import v10

    [declared] = [s for s in v10.STEPS if s.type == "event"]
    request = _through(
        world, "felicia.marks@naviant.com",
        documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    emitted = world.engine.get(request.run_id).step("e1")
    assert emitted.name == declared.name == declared.config["event"]
    assert declared.name == "ExpenseRecorded"


def test_the_declared_model_is_the_configured_one(world):
    """A map naming a model the deployment does not call is a lie in the one
    place a reader has no way to check it.

    So the step config is read from `settings.expense_model` rather than typed,
    and this pins the two together — including the default, because
    `gemini-3.7-flash` is a decision about how a photographed receipt gets read
    and not an implementation detail.
    """
    from app.config import settings
    from app.tasks.expense_reimbursement import v10

    [model_step] = [s for s in v10.STEPS if s.type == "modelcall"]
    assert model_step.config["model"] == settings.expense_model
    assert settings.expense_model == "gemini-3.7-flash"


def test_the_reader_the_deployment_builds_runs_the_declared_model(monkeypatch):
    """The other half of the pin: the wiring reads the same setting.

    Asserted against the constructed reader rather than by reading `deps.py`,
    because the failure this guards is somebody changing one of the two.
    """
    from app.config import settings
    from app.tasks.expense_reimbursement.domain import build_receipt_reader

    monkeypatch.setattr(settings, "expense_model", "gemini-3.7-flash")
    reader = build_receipt_reader(
        "vertex", "a-project", "global", settings.expense_model,
        client=object(),
    )
    assert reader._model == settings.expense_model


def test_v10_is_the_default_and_v9_is_still_selectable():
    from app.tasks import default_version, shipped_version_numbers
    from app.tasks.expense_reimbursement import domain

    assert domain.DEFAULT_VERSION == 10
    assert default_version("expense-reimbursement") == 10
    assert shipped_version_numbers("expense-reimbursement") == {9, 10}


def test_a_v9_run_still_replays_its_own_shape(world):
    """The reason v10 renumbers instead of editing v9 in place.

    A step id is part of a journal's shape, so a run recorded under v9 has to
    keep resolving against v9's ids — the projection reads both, and this is
    the assertion that says so rather than the comment claiming it.
    """
    from app.tasks.expense_reimbursement.domain import FLOW_ID

    previous = world.engine.live_version
    world.engine.live_version = lambda flow_id: 9 if flow_id == FLOW_ID else 0
    try:
        request = world.expenses.submit(
            "felicia.marks@naviant.com",
            documents=[_doc(hint="total=64.20 category=meals")],
            commentary="Team lunch after the Acme review.",
        )
    finally:
        world.engine.live_version = previous

    record = world.engine.get(request.run_id)
    assert record.flow_version == 9
    ids = {s.step_id for s in record.steps}
    assert "x2r" in ids and "m1" in ids   # v9's numbering, not v10's
    # v9 auto-approved a clean receipt under the limit, and still does: the
    # version is the shape, and a shipped shape does not change under it.
    assert request.state == "approved"
    # The projection read v9's `x1` policy step and v9's `x2` lines.
    assert request.lines and request.policy_limit_usd == 200.0


# ---- the governed read -----------------------------------------------------


def test_policy_values_come_from_the_document(world):
    policy = world.expenses.read_policy()
    assert policy.found_asset
    assert policy.limit_usd == 200.0
    assert policy.purpose_required_above_usd == 75.0
    # Pinned to the revision actually read, so a decision stays explainable
    # against the document as it was.
    assert policy.source_ref.endswith("@rev1")


def test_the_category_vocabulary_is_written_down_once(world):
    """It was written down three times, and a fourth in the prompt text.

    A category the extractor may return and the policy does not screen is
    extracted and then never reviewed — the failure the receipt-extraction
    prompt warns its editor about in prose, and one nothing enforced. The
    governed policy is the source at run time; the declaration is what answers
    before the corpus has seeded, and everything in code derives from it.
    """
    from app.tasks.expense_reimbursement import domain as reimbursements

    seeded = world.expenses.read_policy()
    assert seeded.found_asset
    assert seeded.restricted == list(reimbursements.RESTRICTED_BY_DEFAULT)
    assert set(seeded.restricted) <= set(reimbursements.ALL_CATEGORIES)

    # The fallback prompt asks for exactly the vocabulary the policy screens
    # against, because it is interpolated from it rather than retyped.
    for category in reimbursements.ALL_CATEGORIES:
        assert category in reimbursements.DEFAULT_RECEIPT_PROMPT
    # And the stub can only return words that vocabulary holds, so a local run
    # exercises the escalation rather than inventing a category nothing screens.
    assert set(reimbursements.ORDINARY_CATEGORIES) <= set(
        reimbursements.ALL_CATEGORIES
    )


def test_the_governed_policy_beats_the_declared_restricted_list(world):
    """The declaration is a fallback, never the answer where an asset exists."""
    content = world.knowledge.repo.get("expense-approval-policy").content
    world.knowledge.update(
        "expense-approval-policy",
        AssetUpdate(content=content.replace(
            "Restricted categories: alcohol, gifts, entertainment",
            "Restricted categories: alcohol, gifts, entertainment, software",
        )),
    )
    request = world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[_doc(hint="total=40.00 category=software")],
        commentary="Editor licence renewal.",
    )
    assert any(e.kind == "restricted-category" for e in request.escalations)


def test_editing_the_policy_changes_the_flow_without_code(world):
    """The claim, restated for v10.

    v9 proved it by flipping a run from auto-approved to elevated. v10 asks a
    person either way, so what the edit moves is what the check *says* — which
    is the thing the document actually governs, and the thing that travels to
    the submitter's desk as the reason to look twice.
    """
    request = world.expenses.submit(
        "felicia.marks@naviant.com", documents=[_doc(hint="total=150.00 category=meals")],
        commentary="Team lunch with the Acme delivery crew.",
    )
    assert not any(e.kind == "over-limit" for e in request.escalations)

    content = world.knowledge.repo.get("expense-approval-policy").content
    world.knowledge.update(
        "expense-approval-policy",
        AssetUpdate(content=content.replace(
            "Auto-approval limit: $200.00", "Auto-approval limit: $100.00"
        )),
    )
    again = world.expenses.submit(
        "felicia.marks@naviant.com", documents=[_doc(hint="total=150.00 category=meals")],
        commentary="Second lunch, same crew.",
    )
    assert again.state == "pending"
    assert any(e.kind == "over-limit" for e in again.escalations)
    # The new revision is the one the run recorded.
    assert again.policy_ref.endswith("@rev2")
    # And the finding annotates the task rather than moving it: both went to
    # the same desk, because who confirms an expense is not what the limit is
    # about.
    assert (
        world.tasks.get(request.task_id).queue
        == world.tasks.get(again.task_id).queue
    )


# ---- the prompt is governed knowledge too ---------------------------------


def test_the_extraction_prompt_is_an_asset_read_at_a_revision(world):
    """The ModelCall's instruction lives in the corpus, not in a string literal.

    Doctrine's claim is specific: a customer editing a prompt in the shell is
    editing the map, "not application code — which is what makes it safe." That
    is only true if the flow reads the asset, and only auditable if the run says
    which revision it read. Both halves are checked here.
    """
    asset = world.knowledge.repo.get("receipt-extraction-prompt")
    assert asset is not None
    assert asset.kind == "prompt"
    # A prompt never argues with a policy, so it carries no authority.
    assert asset.authority_level is None

    prompt = world.expenses.read_prompt()
    assert prompt.found_asset
    assert prompt.source_ref == "knowledge:receipt-extraction-prompt@rev1"
    # The instruction, not the page. The asset also explains itself to whoever
    # is editing it, and the model has no use for that half.
    assert prompt.text.startswith("Extract from this receipt:")
    assert "governed knowledge" not in prompt.text


def test_the_modelcall_records_the_prompt_revision_it_ran_on(world):
    """The point of the exercise: a decision explainable against the prompt.

    The policy's revision has always been in the read-set of the Rule that
    consumed it. The prompt's is now in the read-set of the ModelCall it
    steered, which is the same guarantee one step earlier.
    """
    request = world.expenses.submit(
        "felicia.marks@naviant.com", documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme Phase 1 review.",
    )
    run = world.engine.get(request.run_id)
    [model_step] = [s for s in run.steps if s.kind == "modelcall"]
    assert "knowledge:receipt-extraction-prompt@rev1" in model_step.refs
    # And it is on the run's roll-up, so the flow's projection onto the
    # ontology counts the prompt among the knowledge this flow reads.
    assert "knowledge:receipt-extraction-prompt@rev1" in run.refs


def test_editing_the_prompt_changes_what_the_modelcall_runs_on(world):
    """The same claim the policy test makes, for the prompt: an edit in the
    shell is a behaviour change with no deploy, and the new revision is what
    the next run records."""
    world.knowledge.update(
        "receipt-extraction-prompt",
        AssetUpdate(content=(
            "# Receipt prompt\n\n```text\nRead the tip as its own charge.\n```\n"
        )),
    )
    prompt = world.expenses.read_prompt()
    assert prompt.text == "Read the tip as its own charge."
    assert prompt.source_ref == "knowledge:receipt-extraction-prompt@rev2"

    request = world.expenses.submit(
        "felicia.marks@naviant.com", documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme Phase 1 review.",
    )
    run = world.engine.get(request.run_id)
    [model_step] = [s for s in run.steps if s.kind == "modelcall"]
    assert "knowledge:receipt-extraction-prompt@rev2" in model_step.refs


def test_a_corpus_without_the_prompt_still_extracts():
    """A deployment whose corpus has not seeded yet must still run.

    Same degradation as the policy read, and for the same reason: the built-in
    default carries the run, and the *unpinned* ref left in the journal is how
    the run says it never got a revision.
    """
    world = test_world.fresh(seed_corpus=False)
    prompt = world.expenses.read_prompt()
    assert not prompt.found_asset
    assert prompt.source_ref == "knowledge:receipt-extraction-prompt"

    request = _through(
        world, "felicia.marks@naviant.com",
        documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme Phase 1 review.",
    )
    assert request.state == "approved"
    assert len(request.lines) == 1
    run = world.engine.get(request.run_id)
    [model_step] = [s for s in run.steps if s.kind == "modelcall"]
    assert "knowledge:receipt-extraction-prompt" in model_step.refs


# ---- documents are not facts ----------------------------------------------


def test_a_compound_receipt_becomes_two_lines(world):
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[_doc(hint="compound total=210.00 category=lodging")],
        commentary="Room and parking on one folio at the Acme onsite.",
    )
    assert len(request.documents) == 1
    assert len(request.lines) == 2
    assert {line.document_id for line in request.lines} == {
        request.documents[0].document_id
    }
    assert sum(line.document_total_usd for line in request.lines) == pytest.approx(210.0)


def test_several_documents_in_one_submission(world):
    request = _through(
        world, "nathan.pryce@naviant.com",
        documents=[
            _doc(hint="total=42.00 category=travel", filename="cab.jpg"),
            _doc(hint="total=38.50 category=meals", filename="lunch.jpg"),
        ],
        commentary="Client visit to Acme — cab and lunch.",
    )
    assert len(request.documents) == 2
    assert len(request.lines) == 2
    assert request.requested_usd == pytest.approx(80.50)
    assert request.state == "approved"
    # One ModelCall step per document, so a retry or a cost lands on the
    # document it belongs to.
    run = world.engine.get(request.run_id)
    assert {s.step_id for s in run.steps} >= {"m1_1", "m1_2"}


# ---- the submitter's own words --------------------------------------------


@pytest.mark.parametrize(
    "note,expected",
    [
        ("I am only requesting $75 of the $110 — the rest was mine.", 75.0),
        ("only requesting 75", 75.0),
        ("claiming $40.50 for this one", 40.50),
        ("receipt attached", None),
        ("", None),
    ],
)
def test_a_stated_claim_is_read_out_of_the_note(note, expected):
    assert parse_requested_amount(note) == expected


def test_the_submitter_can_claim_less_than_the_receipt(world):
    request = _through(
        world, "felicia.marks@naviant.com",
        documents=[_doc(
            hint="total=110.00 category=meals",
            memo=(
                "Dinner with the Acme team after the Phase 2 review. I am only "
                "requesting $75 of the $110 — I ordered wine on my own tab."
            ),
        )],
        commentary="Client dinner, Acme Phase 2.",
    )
    line = request.lines[0]
    assert line.document_total_usd == pytest.approx(110.0)
    assert line.requested_usd == pytest.approx(75.0)
    # Claiming less is legitimate: nothing is flagged about it.
    assert request.escalations == []
    assert request.state == "approved"
    # And the memo lands on the fact, which is the only place the submitter's
    # own words appear — never in the extractor's instruction.
    assert line.description.startswith("Dinner with the Acme team")


def test_claiming_more_than_the_document_is_always_elevated(world):
    request = world.expenses.submit(
        "felicia.marks@naviant.com", documents=[_doc(hint="total=40.00 category=meals")],
    )
    # Patch the line upward the way a mis-parse would, then re-check.
    from app.tasks.expense_reimbursement.domain import PolicyValues, _check

    lines = apply_correction(
        request.lines, {"lines": [{"ordinal": 1, "requested_usd": 400.0}]},
        "someone@naviant.com",
    )
    result = _check(lines, PolicyValues())
    assert any(e["kind"] == "amount-mismatch" for e in result["escalations"])


def test_a_purpose_is_required_above_the_governed_amount(world):
    bare = world.expenses.submit(
        "nathan.pryce@naviant.com", documents=[_doc(hint="total=120.00 category=meals")],
    )
    assert bare.state == "pending"
    assert any(e.kind == "purpose-missing" for e in bare.escalations)

    stated = world.expenses.submit(
        "nathan.pryce@naviant.com",
        documents=[_doc(hint="total=120.00 category=meals")],
        commentary="Dinner with the Acme team after the Phase 2 review.",
    )
    assert stated.escalations == []
    assert _confirm(world, stated).state == "approved"


# ---- gates -----------------------------------------------------------------


def test_every_run_stops_for_its_submitter_to_confirm(world):
    """v10's shape, in one test. A clean receipt still asks.

    v9 auto-approved anything under the governed limit that raised no flag, and
    what it was auto-approving was a *model's reading of a photograph* with
    nobody in the loop. The limit still means something — it is on the Task as
    a finding — but the confirmation is unconditional, because the person
    holding the receipt is the only one who can say what it says.
    """
    request = world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[_doc(hint="total=12.40 category=meals")],
        commentary="Coffee for the Acme standup.",
    )
    assert request.escalations == []           # nothing flagged …
    assert request.state == "pending"          # … and still somebody's to say
    task = world.tasks.get(request.task_id)
    assert task.status == "open"
    assert task.queue == personal_slug("felicia.marks@naviant.com")
    assert "Nothing was flagged" in task.summary


def test_the_gate_goes_to_the_submitters_own_desk_and_names_them(world):
    request = world.expenses.submit(
        "nathan.pryce@naviant.com",
        documents=[_doc(hint="total=612.00 category=lodging")],
        commentary="Conference block rate for the finance summit.",
    )
    assert request.state == "pending" and request.task_id
    task = world.tasks.get(request.task_id)
    assert task.queue == personal_slug("nathan.pryce@naviant.com")
    assert task.status == "open" and task.run_id == request.run_id
    # Who the case is *about*, first-class rather than display text in an
    # evidence row. Here it is also whose desk it went to, and that coincidence
    # is the point of the version.
    assert task.subject_email == "nathan.pryce@naviant.com"
    # The desk is real and its holder is the submitter — not a slug pointing at
    # nothing, which would be work that exists and nobody will ever see.
    assert world.queues.get(task.queue).holds("nathan.pryce@naviant.com")
    run = world.engine.get(request.run_id)
    assert run.status == "suspended" and run.suspended_at_step == "k1_1"

    resumed = _confirm(
        world, request, note="Block rate confirmed against the event contract."
    )
    assert resumed.state == "approved"
    assert resumed.decided_by == "nathan.pryce@naviant.com"
    assert world.engine.get(request.run_id).status == "succeeded"
    # The over-limit finding is on the record rather than lost: it did not stop
    # the expense, and it did not choose a different desk either.
    assert any(e.kind == "over-limit" for e in resumed.escalations)
    assert resumed.human_override is True


def test_an_unreachable_desk_registry_falls_back_honestly(world):
    """A registry outage is a routing answer the policy already names.

    The fallback desk is what the document says a case gets when a resolver
    cannot answer, and the alternative — an exception in the middle of somebody's
    reimbursement — is not an improvement on it.
    """

    class Unreachable:
        def ensure_personal(self, email, label=""):
            raise RuntimeError("the registry is down")

    world.expenses.queues = Unreachable()
    request = world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[_doc(hint="total=40.00 category=meals")],
        commentary="Coffee for the Acme standup.",
    )
    task = world.tasks.get(request.task_id)
    assert task.queue == "expense-review"
    step = world.engine.get(request.run_id).step("x2")
    assert step.output["fell_back"] is True
    assert "no desk could be opened" in step.summary


def test_rejection_denies(world):
    """The submitter withdrawing their own request is the honest reading of a
    rejected confirmation: they looked at what was read and said no."""
    request = world.expenses.submit(
        "nathan.pryce@naviant.com",
        documents=[_doc(hint="total=96.40 category=alcohol")],
        commentary="Client gift wine for the Acme close.",
    )
    assert any(e.kind == "restricted-category" for e in request.escalations)
    resumed = _confirm(
        world, request, verdict="rejected",
        note="Out of policy §4.2 — use the gifting desk.",
    )
    assert resumed.state == "denied"
    assert "§4.2" in resumed.verdict_reason


def test_a_correction_re_runs_the_checks_and_asks_again(world):
    """Review is not one round — and in v10 a correction is always followed by
    a confirmation, because a corrected set of facts is a new set of facts and
    nobody has vouched for it yet."""
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[_doc(hint="compound total=210.00 category=lodging")],
        commentary="Two nights at the Acme onsite.",
    )
    assert request.state == "pending"
    corrected = _confirm(
        world, request, verdict="corrected",
        note="Trimmed the parking line to the reimbursable rate.",
        payload={"lines": [
            {"ordinal": 1, "purpose": "Two nights at the Acme onsite."},
            {"ordinal": 2, "requested_usd": 60.0, "category": "travel",
             "purpose": "Parking, capped rate."},
        ]},
    )
    assert corrected.state == "pending"
    assert corrected.requested_usd == pytest.approx(186.0)
    assert all(line.origin == "human" for line in corrected.lines)
    assert corrected.task_id != request.task_id

    final = _confirm(world, corrected)
    assert final.state == "approved"
    assert final.review_rounds == 1
    assert final.requested_usd == pytest.approx(186.0)
    # The Rule ran twice — once before the correction and once after.
    run = world.engine.get(request.run_id)
    assert [s.step_id for s in run.steps if s.kind == "rule"] == ["r1_1", "r1_2"]


def test_a_submitter_can_add_a_line_the_extraction_missed(world):
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[_doc(hint="total=180.00 category=meals")],
    )
    assert request.state == "pending"
    corrected = _confirm(
        world, request, verdict="corrected",
        note="Second receipt was stapled behind the first.",
        payload={
            "lines": [{
                "ordinal": 1, "requested_usd": 90.0,
                "purpose": "Dinner for the Acme workshop group.",
            }],
            "add_lines": [{
                "vendor": "corner-bistro", "category": "meals",
                "document_total_usd": 45.0, "requested_usd": 45.0,
                "purpose": "Second table at the Acme workshop dinner.",
                "document_id": request.documents[0].document_id,
            }],
        },
    )
    assert len(corrected.lines) == 2
    added = corrected.lines[1]
    assert added.origin == "human" and added.corrected_by == "john.smith@naviant.com"
    # A human-entered line is certain by definition: there is no model to be
    # uncertain about it.
    assert added.confidence == 1.0
    assert _confirm(world, corrected).state == "approved"


def test_confidence_correction_stops_the_uncertainty_flag_repeating(world):
    request = world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[_doc(hint="blurry total=58.75 category=travel")],
        commentary="Airport transfer for the customer visit.",
    )
    assert any(e.kind == "extraction-uncertain" for e in request.escalations)
    corrected = _confirm(
        world, request, verdict="corrected",
        note="Amount matches the card feed.",
        payload={"lines": [{"ordinal": 1, "requested_usd": 58.75}]},
    )
    # The flag is gone on the re-check: a person who looked at the line has
    # resolved the model's uncertainty about it.
    assert corrected.escalations == []
    final = _confirm(world, corrected)
    assert final.state == "approved"
    assert final.review_rounds == 1


def test_correcting_every_round_stops_asking_and_denies(world):
    """Somebody who has rewritten the same submission three times is telling us
    the submission is wrong, not that the checks are."""
    request = world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[_doc(hint="total=40.00 category=meals")],
        commentary="Coffee for the Acme standup.",
    )
    current = request
    for round_number in range(3):
        current = _confirm(
            world, current, verdict="corrected",
            payload={"lines": [{
                "ordinal": 1, "requested_usd": 39.0 - round_number,
            }]},
        )
    assert current.state == "denied"
    assert "never confirmed after 3 correction round(s)" in current.verdict_reason


# ---- documents and the graph ----------------------------------------------


def test_documents_land_in_the_audit_collection_with_a_retention_date(world):
    request = world.expenses.submit(
        "felicia.marks@naviant.com", documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    document = world.documents.get(request.documents[0].document_id)
    assert document.collection_id == "audit-documents"
    assert document.retention_until_iso  # two years, stamped on write
    assert document.kind == "receipt"
    assert request.run_id in document.run_ids


def test_no_regions_are_stored_because_nothing_located_anything(world):
    """The door v9 left open, closed.

    The stub reader invents plausible box geometry — it says so in its own
    note — and v9 persisted it onto the stored document, so a review screen
    drew boxes on a real photograph as if a model had found something at those
    coordinates. There is no field locator for receipts, so v10 stores no
    regions rather than convincing ones. The reader still returns them; nothing
    keeps them.
    """
    request = world.expenses.submit(
        "felicia.marks@naviant.com", documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    document = world.documents.get(request.documents[0].document_id)
    assert document.regions == []
    # And the reading itself is unchanged — this is about what was *kept*.
    step = world.engine.get(request.run_id).step("m1_1")
    assert step.output["regions"]


def test_the_run_writes_the_expense_graph(world):
    request = _through(
        world, "felicia.marks@naviant.com",
        documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    from app.graph import object_id

    employee = world.graph.get(object_id("Employee", "felicia.marks@naviant.com"))
    assert employee is not None
    requests = world.graph.neighbors(employee.id, "submitted", "out")
    assert len(requests) == 1
    lines = world.graph.traverse(
        employee.id, [("submitted", "out"), ("has_expense_line", "out")]
    )
    assert len(lines) == len(request.lines)
    # Every line points back at the document it was read from.
    assert world.graph.traverse(
        employee.id,
        [("submitted", "out"), ("has_expense_line", "out"), ("evidenced_by", "out")],
    )


def test_the_graph_write_is_as_rich_as_it_ever_was(world):
    """v10 removed steps. It removed nothing from the commit.

    Every object and every edge the older shape wrote is written here, named
    rather than counted, because "the graph write regressed" is the kind of
    loss a passing test suite hides: the run still succeeds, the row still
    reads correctly, and the traversal that made the whole thing worth building
    quietly returns less.
    """
    from app.graph import object_id

    request = _through(
        world, "felicia.marks@naviant.com",
        documents=[
            _doc(hint="compound total=210.00 category=lodging", filename="folio.pdf"),
            _doc(hint="total=38.50 category=meals", filename="lunch.jpg"),
        ],
        commentary="Two nights and dinner at the Acme onsite.",
    )
    assert request.state == "approved"
    run = world.engine.get(request.run_id)

    employee = world.graph.get(
        object_id("Employee", "felicia.marks@naviant.com")
    )
    assert employee is not None
    [(_, requested)] = world.graph.neighbors(employee.id, "submitted", "out")
    assert requested.props["state"] == "approved"
    assert requested.props["employee_email"] == "felicia.marks@naviant.com"
    assert requested.props["requested_usd"] == pytest.approx(request.requested_usd)
    assert requested.props["commentary"].startswith("Two nights")

    lines = world.graph.neighbors(requested.id, "has_expense_line", "out")
    assert len(lines) == 3  # two off the folio, one off the lunch receipt
    for _, line in lines:
        for field in (
            "vendor", "date", "category", "document_total_usd",
            "requested_usd", "origin",
        ):
            assert field in line.props, field
        # Every line points back at the document it was read from.
        [(edge, document)] = world.graph.neighbors(line.id, "evidenced_by", "out")
        assert document.props["kind"] == "receipt"
        assert document.props["filename"]
        assert edge.method == "derived"

    # The decider, because somebody confirmed this — and it is the submitter.
    [(edge, decider)] = world.graph.neighbors(requested.id, "decided_by", "out")
    assert decider.props["email"] == "felicia.marks@naviant.com"
    assert edge.method == "human" and edge.props["verdict"] == "approved"

    # And the commit is one step's work, with its write-set on the journal.
    committed = run.step("a1")
    assert any(r.startswith("graph:ExpenseRequest/") for r in committed.writes)
    assert sum(
        1 for r in committed.writes if r.startswith("graph:ExpenseLine/")
    ) == 3
    assert any(r.startswith("graph:Document/") for r in committed.writes)
    assert any(r.startswith("link:evidenced_by/") for r in committed.writes)
    assert any(r.startswith("link:decided_by/") for r in committed.writes)


def test_a_human_correction_outranks_the_model_in_the_claims(world):
    request = world.expenses.submit(
        "john.smith@naviant.com", documents=[_doc(hint="blurry total=90.00 category=meals")],
        commentary="Dinner with the Acme team.",
    )
    corrected = _confirm(
        world, request, verdict="corrected",
        payload={"lines": [{"ordinal": 1, "requested_usd": 72.0}]},
    )
    resumed = _confirm(world, corrected)
    line = world.graph.get(resumed.lines[0].graph_ref.split("/")[1].split("@")[0])
    # Both readings are retained; the human's resolves.
    sources = {c.source.split(":")[0] for c in line.claims if c.field == "requested_usd"}
    assert "human" in sources
    assert line.props["requested_usd"] == pytest.approx(72.0)


def test_replaying_a_finished_run_does_not_double_write_the_graph(world):
    request = _through(
        world, "felicia.marks@naviant.com",
        documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    before = world.graph.stats()
    world.engine.resume(request.run_id)
    assert world.graph.stats() == before


# ---- the flywheel ----------------------------------------------------------
#
# Doctrine calls the correction set a crown-jewel asset, and its clause about
# the route is specific: a human correction flows back **decoupled, via an
# Event, not inline**. Accounts payable already emits `ExtractionCorrected`
# when a clerk's edit is accepted, and the `ap-corrections` flow answers it by
# appending raw→corrected pairs to the `eval-datasets` Collection. This side
# emits the *same* fact rather than growing a second flow: every expense
# correction the submitter makes is a miss the confidence score did not catch,
# which is exactly the signal that set exists to hold.


def test_a_correction_appends_raw_corrected_pairs(world):
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[_doc(hint="total=90.00 category=meals", filename="dinner.jpg")],
        commentary="Dinner with the Acme team after the Phase 2 review.",
    )
    read_total = request.lines[0].requested_usd
    corrected = _confirm(
        world, request, verdict="corrected",
        note="The total is 72.00 — the printed line is the pre-tip subtotal.",
        payload={"lines": [{"ordinal": 1, "requested_usd": 72.0}]},
    )
    assert corrected.lines[0].requested_usd == pytest.approx(72.0)

    [row] = world.corrections.list()
    assert row.path == "expense.lines[0].requested_usd"
    assert row.raw_value == f"{read_total:.2f}"
    assert row.corrected_value == "72.00"
    assert row.decided_by == "john.smith@naviant.com"
    assert row.source_flow_id == "expense-reimbursement"
    assert row.source_run_id == request.run_id
    assert row.task_id == request.task_id
    # The receipt the pair is about, so a later tuning pass can fetch the page.
    assert row.document_id == request.documents[0].document_id
    assert row.collection_id == "eval-datasets"

    # Its own run in the fleet, on the far side of an Event — so "corrections
    # are being captured" is something an operator can see rather than infer.
    appended = [
        r for r in world.engine.store.list() if r.flow_id == "ap-corrections"
    ]
    assert len(appended) == 1 and appended[0].status == "succeeded"
    assert appended[0].caused_by


def test_a_clean_confirmation_appends_nothing(world):
    """Most of what a confirm screen sends back is the person agreeing.

    A correction set full of `raw == corrected` would drown the pairs that
    carry the signal, so agreement is not a correction and neither is a
    reformatting of the same number.
    """
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[_doc(hint="total=90.00 category=meals")],
        commentary="Dinner with the Acme team after the Phase 2 review.",
    )
    total = request.lines[0].requested_usd
    _confirm(world, request)
    assert world.corrections.list() == []

    # And a `corrected` verdict that changed nothing is still nothing.
    again = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[_doc(hint="total=90.00 category=meals", filename="b.jpg")],
        commentary="Dinner with the Acme team after the Phase 2 review.",
    )
    _confirm(
        world, again, verdict="corrected",
        payload={"lines": [{"ordinal": 1, "requested_usd": total}]},
    )
    assert world.corrections.list() == []


def test_a_correction_names_the_receipt_it_is_about(world):
    """One expense can carry three receipts, and a pair filed against the
    wrong one would send a tuning run to the wrong page."""
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[
            _doc(hint="total=42.00 category=travel", filename="cab.jpg"),
            _doc(hint="total=38.50 category=meals", filename="lunch.jpg"),
        ],
        commentary="Client visit to Acme — cab and lunch.",
    )
    _confirm(
        world, request, verdict="corrected",
        payload={"lines": [{"ordinal": 2, "requested_usd": 41.25}]},
    )
    [row] = world.corrections.list()
    assert row.path == "expense.lines[1].requested_usd"
    assert row.document_id == request.documents[1].document_id


def test_a_pair_records_whether_the_check_had_doubted_the_line(world):
    """The question the correction set exists to answer later: were the lines
    we flagged the lines that were actually wrong?

    So a pair carries what the check said about its line beforehand — and the
    empty status is the interesting one, because a value nobody doubted and
    the submitter fixed anyway is a miss the confidence score did not catch.
    """
    doubted = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[_doc(hint="blurry total=58.75 category=travel")],
        commentary="Airport transfer for the Acme customer visit.",
    )
    assert any(e.kind == "extraction-uncertain" for e in doubted.escalations)
    _confirm(
        world, doubted, verdict="corrected",
        payload={"lines": [{"ordinal": 1, "requested_usd": 61.0}]},
    )
    [flagged] = world.corrections.list()
    assert flagged.status == "flagged"
    assert flagged.confidence and flagged.confidence < 0.8

    quiet = world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[_doc(hint="total=40.00 category=meals", filename="b.jpg")],
        commentary="Coffee for the Acme standup.",
    )
    assert quiet.escalations == []
    _confirm(
        world, quiet, verdict="corrected",
        payload={"lines": [{"ordinal": 1, "requested_usd": 44.0}]},
    )
    unflagged = next(
        r for r in world.corrections.list() if r.id != flagged.id
    )
    assert unflagged.status == ""


def test_nothing_is_promoted_into_the_prompt(world):
    """Appending is automatic; teaching is not.

    Doctrine reserves promotion for a declarative Rule raising a curation
    Task, and a promotion path invented in the same lane as the capture path
    would be a knowledge edit nobody governed. So a correction moves no asset
    revision — least of all the extraction prompt it is a comment on.
    """
    before = {
        asset.id: asset.revision for asset in world.knowledge.repo.all()
    }
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[_doc(hint="total=90.00 category=meals")],
        commentary="Dinner with the Acme team after the Phase 2 review.",
    )
    _confirm(
        world, request, verdict="corrected",
        payload={"lines": [{"ordinal": 1, "requested_usd": 72.0}]},
    )
    assert world.corrections.list()  # the pair landed …
    after = {asset.id: asset.revision for asset in world.knowledge.repo.all()}
    assert after == before          # … and taught nothing on its own


def test_the_same_correction_is_not_appended_twice(world):
    """Two callers reach for the same decided Task — the API hook and a
    seeding script — and "is this run still waiting" is the fact that tells
    them apart."""
    request = world.expenses.submit(
        "john.smith@naviant.com",
        documents=[_doc(hint="total=90.00 category=meals")],
        commentary="Dinner with the Acme team after the Phase 2 review.",
    )
    decided = world.tasks.decide(
        request.task_id, "corrected", "john.smith@naviant.com",
        payload={"lines": [{"ordinal": 1, "requested_usd": 72.0}]},
    )
    world.expenses.resume_for_task(decided)
    world.expenses.resume_for_task(world.tasks.get(decided.id))
    assert len(world.corrections.list()) == 1


# ---- seeding and the API -------------------------------------------------


def test_seed_drives_real_runs(world):
    seeded = demo_world.seed_expenses(world.expenses)
    assert seeded > 0
    assert demo_world.seed_expenses(world.expenses) == 0  # idempotent
    rows = world.expenses.list()
    assert len(rows) == seeded
    assert all(row.run_id for row in rows)
    # Every seeded row has a real run behind it with a real journal.
    for row in rows:
        run = world.engine.get(row.run_id)
        assert run is not None and run.steps
    stats = world.expenses.stats()
    assert stats.approved and stats.pending and stats.denied
    assert stats.human_corrected > 0


#: A one-pixel JPEG, so a REST submission carries actual bytes. The stub
#: reader derives its numbers from a hash of these plus the filename, which is
#: exactly the point of the tests below: over the wire, nobody chooses what the
#: extraction says.
_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


def test_api_submit_accepts_documents_and_commentary(client, world):
    resp = client.post(
        "/api/v1/reimbursements",
        json={
            "commentary": "Client visit to Acme — cab and lunch.",
            "documents": [
                {"filename": "cab.jpg", "content_base64": _JPEG_B64},
                {"filename": "lunch.jpg", "content_base64": _JPEG_B64,
                 "note": "Lunch with the Acme delivery crew."},
            ],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["lines"]) == 2
    assert body["commentary"].startswith("Client visit")
    # Waiting on its submitter, which is every run's shape now.
    assert body["state"] == "pending" and body["task_id"]
    assert body["run_id"]
    # The note travelled as the document's memo.
    assert body["documents"][1]["memo"].startswith("Lunch with the Acme")


def test_a_submission_with_no_evidence_is_refused(client, world):
    """The door v9 left open, closed at the wire.

    A caller could describe a receipt in prose and get back an extraction, a
    set of lines and a graph write. `payables_api` refuses the same shape on
    the invoice side; this is it here.
    """
    prose = client.post(
        "/api/v1/reimbursements",
        json={"commentary": "I spent about ninety dollars on dinner, trust me."},
    )
    assert prose.status_code == 400
    assert "carries bytes" in prose.json()["detail"]

    # A document entry with a filename and no bytes is the same submission
    # wearing a list.
    named = client.post(
        "/api/v1/reimbursements",
        json={"documents": [{"filename": "dinner.jpg"}]},
    )
    assert named.status_code == 400
    # Nothing ran: no row, no run, no document.
    assert world.expenses.list() == []


def test_the_service_refuses_an_empty_submission_too(world):
    """The floor under every caller, not only the REST one."""
    with pytest.raises(ValueError, match="at least one document"):
        world.expenses.submit(
            "felicia.marks@naviant.com", commentary="Dinner, about $90."
        )


def test_the_note_reaches_the_memo_and_never_the_extractor(client, world):
    """The steering channel, closed — the quarantine test.

    `StubReceiptReader` regex-parses its hint, so v9's habit of concatenating
    the submitter's note into it meant `total=500 vendor=corner-bistro` typed
    into a note box was an extraction the submitter had authored. The wire has
    no hint field any more, and the note is not made into one: the reader is
    handed an empty steering string and the submitter's words land on the
    document as its memo.
    """
    seen: list[str] = []
    real = world.expenses.reader

    class Watched:
        name = "watched"

        def read(self, image, filename, hint, *, prompt="", media_type=""):
            seen.append(hint)
            return real.read(
                image, filename, hint, prompt=prompt, media_type=media_type
            )

    world.expenses.reader = Watched()
    body = client.post(
        "/api/v1/reimbursements",
        json={
            "documents": [{
                "filename": "dinner.jpg",
                "content_base64": _JPEG_B64,
                "note": "total=500.00 vendor=corner-bistro blurry compound",
            }],
        },
    ).json()

    # The reader saw nothing the submitter wrote.
    assert seen == [""]
    # The words are kept, on the document, where a person will read them back.
    assert body["documents"][0]["memo"].startswith("total=500.00")
    # And none of that prose became the extraction: not the amount, not the
    # confidence, not the split.
    assert body["total_usd"] != pytest.approx(500.0)
    assert len(body["lines"]) == 1
    assert not any(
        e["kind"] == "extraction-uncertain" for e in body["escalations"]
    )


def test_the_stub_can_still_be_steered_from_the_fixture_seam(world):
    """The knob survives where it belongs: a test, a fixture, the seed.

    Deleting it would make local development a guessing game — the escalations
    this flow exists to demonstrate could not be produced on demand. What
    matters is that it is not on the wire, which the test above pins.
    """
    from app.reimbursements_api import SubmittedDocumentBody

    assert "hint" not in SubmittedDocumentBody.model_fields
    request = world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[_doc(hint="total=500.00 category=alcohol")],
    )
    assert request.total_usd == pytest.approx(500.0)
    assert any(e.kind == "restricted-category" for e in request.escalations)


def test_a_submission_is_filed_under_the_submitter_s_email(client, world):
    """The row's `employee` is the graph's natural key for a person, so the
    row and the `Employee` object it writes are the same person."""
    body = client.post(
        "/api/v1/reimbursements",
        json={"documents": [{"content_base64": _JPEG_B64}]},
        headers={"X-Cumulus-Persona": "customer-user:ae"},
    ).json()
    assert body["employee"] == "john.smith@naviant.com"


def test_an_own_scoped_persona_only_ever_sees_its_own(client, world):
    """`mine=true` and the bare list are one answer for somebody whose reach
    is their own work. Unasked, this endpoint used to hand every submitter's
    receipts to anybody who was signed in."""
    demo_world.seed_expenses(world.expenses)
    kim = {"X-Cumulus-Persona": "customer-user:ae"}
    mine = client.get(
        "/api/v1/reimbursements", params={"mine": True}, headers=kim
    ).json()
    assert mine and {r["employee"] for r in mine} == {"john.smith@naviant.com"}
    assert client.get("/api/v1/reimbursements", headers=kim).json() == mine

    everyone = client.get("/api/v1/reimbursements").json()
    assert len({r["employee"] for r in everyone}) > 1


def test_a_minted_key_may_read_but_not_submit(client, monkeypatch, world):
    """Asking to be paid back is a human act; a credential cannot do it."""
    import app.deps as deps
    from app.api_keys import InMemoryApiKeyDirectory
    from app.config import settings
    from app.identities import IdentityService, InMemoryMachineIdentityDirectory
    from app.users import InMemoryUserDirectory

    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    owner = {"X-API-Key": "root-sekret"}
    deps._user_directory = InMemoryUserDirectory()
    deps._api_key_directory = InMemoryApiKeyDirectory()
    deps._identity_service = IdentityService(
        deps._user_directory, InMemoryMachineIdentityDirectory()
    )
    identity = client.post(
        "/api/v1/identities",
        json={"display_name": "Expense bot", "role": "admin"},
        headers=owner,
    ).json()
    minted = client.post(
        "/api/v1/api-keys",
        json={"name": "CI", "identity_uid": identity["uid"]},
        headers=owner,
    ).json()
    key = {"X-API-Key": minted["token"]}

    assert client.get("/api/v1/reimbursements", headers=key).status_code == 200
    refused = client.post(
        "/api/v1/reimbursements",
        json={"documents": [{"content_base64": _JPEG_B64}]},
        headers=key,
    )
    assert refused.status_code == 403


def test_a_suspended_run_shows_the_newest_facts(world):
    """A reviewer's edit must not look like it vanished.

    After a correction the facts live in that round's `apply-correction` step,
    and the run may stop again for a *different* reason. The row has to show
    the corrected lines and the reason that is still standing, not the original
    extraction and the reason already dealt with.
    """
    request = world.expenses.submit(
        "nathan.pryce@naviant.com",
        documents=[_doc(hint="total=487.90 category=lodging")],
    )
    assert {e.kind for e in request.escalations} == {
        "over-limit", "purpose-missing"
    }
    resumed = _confirm(
        world, request, verdict="corrected",
        note="Purpose confirmed against the event contract.",
        payload={"lines": [{
            "ordinal": 1,
            "purpose": "Conference block rate for the finance summit.",
        }]},
    )
    # Asked again on the corrected facts — and only the standing finding is
    # still standing.
    assert resumed.state == "pending"
    assert {e.kind for e in resumed.escalations} == {"over-limit"}
    assert resumed.review_rounds == 1
    assert resumed.decided_by == "nathan.pryce@naviant.com"
    assert resumed.lines[0].purpose.startswith("Conference block rate")
    assert resumed.lines[0].origin == "human"
    # And the row points at the *new* gate, not the decided one.
    assert resumed.task_id != request.task_id
    assert world.tasks.get(resumed.task_id).status == "open"

    final = _confirm(
        world, resumed, note="Rate is contractual; confirming over the limit."
    )
    assert final.state == "approved"
    assert final.human_override is True


# ---- who the expense table belongs to --------------------------------------
#
# Nadia is the whole point of this block. She owns the `expense-review` queue,
# has three open tasks sitting in it, and — because the old rule narrowed an
# own-scoped principal to `principal.email` and she has submitted nothing
# herself — the table those tasks are about came back empty. The approver of a
# queue could not see the queue.
#
# One sentence, shared with the AP workspace: your own rows, plus the rows of
# any run whose Task sits in a queue you own.

#: Rows each persona reaches over the seeded expense world, and why.
EXPENSE_VISIBLE = {
    # Platform scope: the deployment is theirs to diagnose.
    "naviant-engineer": 12,
    "naviant-support": 12,
    # Tenant scope: the customer's own map, whole, by design.
    "customer-admin": 12,
    # Bound to `expense-review`, the queue this flow routes to, so the whole
    # workspace is theirs — the six that stopped for a decision and the six
    # that auto-approved. Lens 4 calls a Workspace impersonal: you watch how
    # a workstream runs, not only the parts that stopped for you.
    "customer-user:controller": 12,
    "customer-user:ceo": 12,
    # Submits and owns no queue: her own four, and this is what she saw
    # before too — she is the persona that was already scoped correctly.
    "customer-user:ae": 4,
    # Bound to `ap-clerk` only, and has never filed an expense.
    "customer-user:ap": 0,
}


@pytest.mark.parametrize("persona,expected", sorted(EXPENSE_VISIBLE.items()))
def test_the_expense_table_shows_the_work_you_are_accountable_for(
    client, world, persona, expected
):
    demo_world.seed_expenses(world.expenses)
    rows = client.get(
        "/api/v1/reimbursements", headers={"X-Cumulus-Persona": persona}
    ).json()
    assert len(rows) == expected


@pytest.mark.parametrize("persona,expected", sorted(EXPENSE_VISIBLE.items()))
def test_the_expense_strip_counts_the_table_under_it(
    client, world, persona, expected
):
    """The same agreement the AP strip owes its table, for the same reason."""
    demo_world.seed_expenses(world.expenses)
    headers = {"X-Cumulus-Persona": persona}
    rows = client.get("/api/v1/reimbursements", headers=headers).json()
    stats = client.get("/api/v1/reimbursements/stats", headers=headers).json()
    assert stats["total"] == len(rows) == expected
    assert stats["approved"] + stats["pending"] + stats["denied"] == len(rows)


def test_the_approver_of_a_queue_can_open_the_queue_s_rows(client, world):
    """The defect, named. Nadia owns `expense-review` and has filed nothing
    herself, so narrowing her to `principal.email` emptied the very table her
    open tasks are about."""
    demo_world.seed_expenses(world.expenses)
    nadia = {"X-Cumulus-Persona": "customer-user:controller"}
    rows = client.get("/api/v1/reimbursements", headers=nadia).json()
    assert rows and "raymond.fitzgerald@naviant.com" not in {r["employee"] for r in rows}

    # Owning the queue this flow routes to makes the whole workstream hers —
    # the requests that stopped for her decision *and* the ones that never
    # needed one. An approver shown only the exceptions has no baseline to
    # judge them against, and Lens 4 calls a Workspace impersonal for exactly
    # this reason: you watch how the work runs, not only the parts aimed at you.
    assert {r["id"] for r in rows} == {r.id for r in world.expenses.list()}
    # Including the ones still waiting on their submitter.
    assert any(r["state"] == "pending" for r in rows)
    # And the ones already confirmed and recorded, which she was never asked
    # about — under v10 the confirmation is the submitter's, and the queue
    # binding is what makes the workstream hers to watch.
    assert any(r["state"] == "approved" for r in rows)


def test_mine_stays_a_real_filter_for_an_approver(client, world):
    """"My expenses" and "the queue I approve" are two questions, and an
    approver asks both. Collapsing them would hide Nadia's own receipts inside
    the queue she reviews."""
    demo_world.seed_expenses(world.expenses)
    nadia = {"X-Cumulus-Persona": "customer-user:controller"}
    assert client.get(
        "/api/v1/reimbursements", params={"mine": True}, headers=nadia
    ).json() == []
    # For a submitter who owns no queue the two answers coincide, as before.
    kim = {"X-Cumulus-Persona": "customer-user:ae"}
    assert (
        client.get("/api/v1/reimbursements", params={"mine": True}, headers=kim).json()
        == client.get("/api/v1/reimbursements", headers=kim).json()
    )


def test_a_request_outside_your_slice_is_missing_not_forbidden(client, world):
    """404 rather than 403: a 403 confirms the id exists, which is the one bit
    an unauthorized caller should not get back."""
    demo_world.seed_expenses(world.expenses)
    request_id = "run_er_seed11"  # Sam's, pending in expense-review
    assert client.get(
        f"/api/v1/reimbursements/{request_id}",
        headers={"X-Cumulus-Persona": "customer-user:controller"},
    ).status_code == 200
    refused = client.get(
        f"/api/v1/reimbursements/{request_id}",
        headers={"X-Cumulus-Persona": "customer-user:ae"},
    )
    assert refused.status_code == 404
