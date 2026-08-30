"""The expense-reimbursement Flow: documents, facts, gates, and the graph.

The doctrinal assertions, in one place:

- the Rule's values come from the governed policy document, so editing it
  changes behaviour with no code change;
- a document is not a fact — a compound receipt yields two lines, and a
  reviewer can add one the extraction missed;
- the submitter's own commentary is a real input, not decoration;
- review is not one round: a correction re-runs the checks;
- the run writes entities, so "how much has this employee submitted" is a
  traversal rather than a table scan;
- submission is a human act (keys refused).
"""

import pytest

from app.models import AssetUpdate
from app.reimbursements import (
    SubmittedDocument,
    apply_correction,
    parse_requested_amount,
    seed_reimbursements_if_empty,
)
from tests import world as test_world


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


def _doc(hint: str = "", note: str = "", filename: str = "receipt.jpg"):
    return SubmittedDocument(filename=filename, note=note, hint=hint)


# ---- the governed read -----------------------------------------------------


def test_policy_values_come_from_the_document(world):
    policy = world.expenses.read_policy()
    assert policy.found_asset
    assert policy.limit_usd == 200.0
    assert policy.purpose_required_above_usd == 75.0
    # Pinned to the revision actually read, so a decision stays explainable
    # against the document as it was.
    assert policy.source_ref.endswith("@rev1")


def test_editing_the_policy_changes_the_flow_without_code(world):
    request = world.expenses.submit(
        "pat@customer.example", documents=[_doc(hint="total=150.00 category=meals")],
        commentary="Team lunch with the Acme delivery crew.",
    )
    assert request.state == "approved"

    content = world.knowledge.repo.get("expense-approval-policy").content
    world.knowledge.update(
        "expense-approval-policy",
        AssetUpdate(content=content.replace(
            "Auto-approval limit: $200.00", "Auto-approval limit: $100.00"
        )),
    )
    again = world.expenses.submit(
        "pat@customer.example", documents=[_doc(hint="total=150.00 category=meals")],
        commentary="Second lunch, same crew.",
    )
    assert again.state == "pending"
    assert any(e.kind == "over-limit" for e in again.escalations)
    # The new revision is the one the run recorded.
    assert again.policy_ref.endswith("@rev2")


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
        "pat@customer.example", documents=[_doc(hint="total=64.20 category=meals")],
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
        "pat@customer.example", documents=[_doc(hint="total=64.20 category=meals")],
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

    request = world.expenses.submit(
        "pat@customer.example", documents=[_doc(hint="total=64.20 category=meals")],
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
        "kim@customer.example",
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
    request = world.expenses.submit(
        "sam@customer.example",
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
    assert {s.step_id for s in run.steps} >= {"m1", "m2"}


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
    request = world.expenses.submit(
        "pat@customer.example",
        documents=[_doc(
            hint="total=110.00 category=meals",
            note=(
                "Dinner with the Acme team after the Phase 2 review. I am only "
                "requesting $75 of the $110 — I ordered wine on my own tab."
            ),
        )],
        commentary="Client dinner, Acme Phase 2.",
    )
    line = request.lines[0]
    assert line.document_total_usd == pytest.approx(110.0)
    assert line.requested_usd == pytest.approx(75.0)
    # Claiming less is legitimate and does not need a human.
    assert request.state == "approved"


def test_claiming_more_than_the_document_is_always_elevated(world):
    request = world.expenses.submit(
        "pat@customer.example", documents=[_doc(hint="total=40.00 category=meals")],
    )
    # Patch the line upward the way a mis-parse would, then re-check.
    from app.reimbursements import PolicyValues, _check

    lines = apply_correction(
        request.lines, {"lines": [{"ordinal": 1, "requested_usd": 400.0}]},
        "someone@customer.example",
    )
    result = _check(lines, PolicyValues())
    assert any(e["kind"] == "amount-mismatch" for e in result["escalations"])


def test_a_purpose_is_required_above_the_governed_amount(world):
    bare = world.expenses.submit(
        "sam@customer.example", documents=[_doc(hint="total=120.00 category=meals")],
    )
    assert bare.state == "pending"
    assert any(e.kind == "purpose-missing" for e in bare.escalations)

    stated = world.expenses.submit(
        "sam@customer.example",
        documents=[_doc(hint="total=120.00 category=meals")],
        commentary="Dinner with the Acme team after the Phase 2 review.",
    )
    assert stated.state == "approved"


# ---- gates -----------------------------------------------------------------


def test_over_limit_suspends_into_a_task_and_a_decision_resumes(world):
    request = world.expenses.submit(
        "sam@customer.example",
        documents=[_doc(hint="total=612.00 category=lodging")],
        commentary="Conference block rate for the finance summit.",
    )
    assert request.state == "pending" and request.task_id
    task = world.tasks.get(request.task_id)
    assert task.queue == "expense-review" and task.status == "open"
    assert task.run_id == request.run_id
    run = world.engine.get(request.run_id)
    assert run.status == "suspended" and run.suspended_at_step == "k1"

    decided = world.tasks.decide(
        request.task_id, "approved", "dana@customer.example",
        note="Block rate confirmed against the event contract.",
    )
    resumed = world.expenses.resume_for_task(decided)
    assert resumed.state == "approved"
    assert resumed.decided_by == "dana@customer.example"
    assert world.engine.get(request.run_id).status == "succeeded"


def test_rejection_denies(world):
    request = world.expenses.submit(
        "sam@customer.example",
        documents=[_doc(hint="total=96.40 category=alcohol")],
        commentary="Client gift wine for the Acme close.",
    )
    assert any(e.kind == "restricted-category" for e in request.escalations)
    decided = world.tasks.decide(
        request.task_id, "rejected", "dana@customer.example",
        note="Out of policy §4.2 — use the gifting desk.",
    )
    resumed = world.expenses.resume_for_task(decided)
    assert resumed.state == "denied"
    assert "§4.2" in resumed.verdict_reason


def test_a_correction_re_runs_the_checks_and_can_finish_the_run(world):
    request = world.expenses.submit(
        "kim@customer.example",
        documents=[_doc(hint="compound total=210.00 category=lodging")],
        commentary="Two nights at the Acme onsite.",
    )
    assert request.state == "pending"
    decided = world.tasks.decide(
        request.task_id, "corrected", "dana@customer.example",
        note="Trimmed the parking line to the reimbursable rate.",
        payload={"lines": [
            {"ordinal": 1, "purpose": "Two nights at the Acme onsite."},
            {"ordinal": 2, "requested_usd": 60.0, "category": "travel",
             "purpose": "Parking, capped rate."},
        ]},
    )
    resumed = world.expenses.resume_for_task(decided)
    assert resumed.state == "approved"
    assert resumed.review_rounds == 1
    assert resumed.requested_usd == pytest.approx(186.0)
    assert all(line.origin == "human" for line in resumed.lines)
    # The Rule ran twice — once before the correction and once after.
    run = world.engine.get(request.run_id)
    assert [s.step_id for s in run.steps if s.kind == "rule"] == ["r1", "r2"]


def test_a_reviewer_can_add_a_line_the_extraction_missed(world):
    # No commentary, so the purpose check holds it: this is the escalation a
    # reviewer answers by editing the facts rather than by overriding a rule.
    request = world.expenses.submit(
        "kim@customer.example",
        documents=[_doc(hint="total=180.00 category=meals")],
    )
    assert request.state == "pending"
    decided = world.tasks.decide(
        request.task_id, "corrected", "dana@customer.example",
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
    resumed = world.expenses.resume_for_task(decided)
    assert len(resumed.lines) == 2
    added = resumed.lines[1]
    assert added.origin == "human" and added.corrected_by == "dana@customer.example"
    # A human-entered line is certain by definition: there is no model to be
    # uncertain about it.
    assert added.confidence == 1.0
    assert resumed.state == "approved"


def test_confidence_correction_stops_the_uncertainty_flag_repeating(world):
    request = world.expenses.submit(
        "pat@customer.example",
        documents=[_doc(hint="blurry total=58.75 category=travel")],
        commentary="Airport transfer for the customer visit.",
    )
    assert any(e.kind == "extraction-uncertain" for e in request.escalations)
    decided = world.tasks.decide(
        request.task_id, "corrected", "dana@customer.example",
        note="Amount matches the card feed.",
        payload={"lines": [{"ordinal": 1, "requested_usd": 58.75}]},
    )
    resumed = world.expenses.resume_for_task(decided)
    assert resumed.state == "approved"
    assert resumed.review_rounds == 1


# ---- documents and the graph ----------------------------------------------


def test_documents_land_in_the_audit_collection_with_a_retention_date(world):
    request = world.expenses.submit(
        "pat@customer.example", documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    document = world.documents.get(request.documents[0].document_id)
    assert document.collection_id == "audit-documents"
    assert document.retention_until_iso  # two years, stamped on write
    assert document.kind == "receipt"
    assert request.run_id in document.run_ids
    # The extraction's regions are attached, which is what a review screen
    # draws boxes from.
    assert any(r.field == "receipt.total_usd" for r in document.regions)


def test_the_run_writes_the_expense_graph(world):
    request = world.expenses.submit(
        "pat@customer.example", documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    from app.graph import object_id

    employee = world.graph.get(object_id("Employee", "pat@customer.example"))
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


def test_a_human_correction_outranks_the_model_in_the_claims(world):
    request = world.expenses.submit(
        "kim@customer.example", documents=[_doc(hint="blurry total=90.00 category=meals")],
        commentary="Dinner with the Acme team.",
    )
    decided = world.tasks.decide(
        request.task_id, "corrected", "dana@customer.example",
        payload={"lines": [{"ordinal": 1, "requested_usd": 72.0}]},
    )
    resumed = world.expenses.resume_for_task(decided)
    line = world.graph.get(resumed.lines[0].graph_ref.split("/")[1].split("@")[0])
    # Both readings are retained; the human's resolves.
    sources = {c.source.split(":")[0] for c in line.claims if c.field == "requested_usd"}
    assert "human" in sources
    assert line.props["requested_usd"] == pytest.approx(72.0)


def test_replaying_a_finished_run_does_not_double_write_the_graph(world):
    request = world.expenses.submit(
        "pat@customer.example", documents=[_doc(hint="total=64.20 category=meals")],
        commentary="Team lunch after the Acme review.",
    )
    before = world.graph.stats()
    world.engine.resume(request.run_id)
    assert world.graph.stats() == before


# ---- seeding and the API -------------------------------------------------


def test_seed_drives_real_runs(world):
    seeded = seed_reimbursements_if_empty(world.expenses)
    assert seeded > 0
    assert seed_reimbursements_if_empty(world.expenses) == 0  # idempotent
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


def test_api_submit_accepts_documents_and_commentary(client, world):
    resp = client.post(
        "/api/v1/reimbursements",
        json={
            "commentary": "Client visit to Acme — cab and lunch.",
            "documents": [
                {"filename": "cab.jpg", "hint": "total=42.00 category=travel"},
                {"filename": "lunch.jpg", "hint": "total=38.50 category=meals"},
            ],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["lines"]) == 2
    assert body["commentary"].startswith("Client visit")
    assert body["state"] == "approved"
    assert body["run_id"]


def test_a_submission_is_filed_under_the_submitter_s_email(client, world):
    """The row's `employee` is the graph's natural key for a person, so the
    row and the `Employee` object it writes are the same person."""
    body = client.post(
        "/api/v1/reimbursements",
        json={"documents": [{"hint": "total=21.00 category=meals"}]},
        headers={"X-Cumulus-Persona": "customer-user:kim"},
    ).json()
    assert body["employee"] == "kim@customer.example"


def test_an_own_scoped_persona_only_ever_sees_its_own(client, world):
    """`mine=true` and the bare list are one answer for somebody whose reach
    is their own work. Unasked, this endpoint used to hand every submitter's
    receipts to anybody who was signed in."""
    seed_reimbursements_if_empty(world.expenses)
    kim = {"X-Cumulus-Persona": "customer-user:kim"}
    mine = client.get(
        "/api/v1/reimbursements", params={"mine": True}, headers=kim
    ).json()
    assert mine and {r["employee"] for r in mine} == {"kim@customer.example"}
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
        json={"documents": [{"hint": "total=10.00"}]},
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
        "sam@customer.example",
        documents=[_doc(hint="total=487.90 category=lodging")],
    )
    assert {e.kind for e in request.escalations} == {
        "over-limit", "purpose-missing"
    }
    decided = world.tasks.decide(
        request.task_id, "corrected", "dana@customer.example",
        note="Purpose confirmed against the event contract.",
        payload={"lines": [{
            "ordinal": 1,
            "purpose": "Conference block rate for the finance summit.",
        }]},
    )
    resumed = world.expenses.resume_for_task(decided)
    # Still over the limit, so it asks again — but only about that.
    assert resumed.state == "pending"
    assert {e.kind for e in resumed.escalations} == {"over-limit"}
    assert resumed.review_rounds == 1
    assert resumed.decided_by == "dana@customer.example"
    assert resumed.lines[0].purpose.startswith("Conference block rate")
    assert resumed.lines[0].origin == "human"
    # And the row points at the *new* gate, not the decided one.
    assert resumed.task_id != decided.id
    assert world.tasks.get(resumed.task_id).status == "open"

    final = world.expenses.resume_for_task(
        world.tasks.decide(
            resumed.task_id, "approved", "dana@customer.example",
            note="Rate is contractual; approving over the limit.",
        )
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
    "customer-user:nadia": 12,
    "customer-user:rowan": 12,
    # Submits and owns no queue: her own four, and this is what she saw
    # before too — she is the persona that was already scoped correctly.
    "customer-user:kim": 4,
    # Bound to `ap-clerk` only, and has never filed an expense.
    "customer-user:ap": 0,
}


@pytest.mark.parametrize("persona,expected", sorted(EXPENSE_VISIBLE.items()))
def test_the_expense_table_shows_the_work_you_are_accountable_for(
    client, world, persona, expected
):
    seed_reimbursements_if_empty(world.expenses)
    rows = client.get(
        "/api/v1/reimbursements", headers={"X-Cumulus-Persona": persona}
    ).json()
    assert len(rows) == expected


@pytest.mark.parametrize("persona,expected", sorted(EXPENSE_VISIBLE.items()))
def test_the_expense_strip_counts_the_table_under_it(
    client, world, persona, expected
):
    """The same agreement the AP strip owes its table, for the same reason."""
    seed_reimbursements_if_empty(world.expenses)
    headers = {"X-Cumulus-Persona": persona}
    rows = client.get("/api/v1/reimbursements", headers=headers).json()
    stats = client.get("/api/v1/reimbursements/stats", headers=headers).json()
    assert stats["total"] == len(rows) == expected
    assert stats["approved"] + stats["pending"] + stats["denied"] == len(rows)


def test_the_approver_of_a_queue_can_open_the_queue_s_rows(client, world):
    """The defect, named. Nadia owns `expense-review` and has filed nothing
    herself, so narrowing her to `principal.email` emptied the very table her
    open tasks are about."""
    seed_reimbursements_if_empty(world.expenses)
    nadia = {"X-Cumulus-Persona": "customer-user:nadia"}
    rows = client.get("/api/v1/reimbursements", headers=nadia).json()
    assert rows and "nadia@customer.example" not in {r["employee"] for r in rows}

    # Owning the queue this flow routes to makes the whole workstream hers —
    # the requests that stopped for her decision *and* the ones that never
    # needed one. An approver shown only the exceptions has no baseline to
    # judge them against, and Lens 4 calls a Workspace impersonal for exactly
    # this reason: you watch how the work runs, not only the parts aimed at you.
    assert {r["id"] for r in rows} == {r.id for r in world.expenses.list()}
    # Including the ones still waiting on a decision from her.
    assert any(r["state"] == "pending" for r in rows)
    # And the ones that auto-approved and asked her nothing.
    assert any(r["state"] == "approved" and not r.get("task_id") for r in rows)


def test_mine_stays_a_real_filter_for_an_approver(client, world):
    """"My expenses" and "the queue I approve" are two questions, and an
    approver asks both. Collapsing them would hide Nadia's own receipts inside
    the queue she reviews."""
    seed_reimbursements_if_empty(world.expenses)
    nadia = {"X-Cumulus-Persona": "customer-user:nadia"}
    assert client.get(
        "/api/v1/reimbursements", params={"mine": True}, headers=nadia
    ).json() == []
    # For a submitter who owns no queue the two answers coincide, as before.
    kim = {"X-Cumulus-Persona": "customer-user:kim"}
    assert (
        client.get("/api/v1/reimbursements", params={"mine": True}, headers=kim).json()
        == client.get("/api/v1/reimbursements", headers=kim).json()
    )


def test_a_request_outside_your_slice_is_missing_not_forbidden(client, world):
    """404 rather than 403: a 403 confirms the id exists, which is the one bit
    an unauthorized caller should not get back."""
    seed_reimbursements_if_empty(world.expenses)
    request_id = "run_er_seed11"  # Sam's, pending in expense-review
    assert client.get(
        f"/api/v1/reimbursements/{request_id}",
        headers={"X-Cumulus-Persona": "customer-user:nadia"},
    ).status_code == 200
    refused = client.get(
        f"/api/v1/reimbursements/{request_id}",
        headers={"X-Cumulus-Persona": "customer-user:kim"},
    )
    assert refused.status_code == 404
