"""Accounts payable v9: one gate, on the submitter's desk, and a map that runs.

What this file is for, claim by claim:

- **The declared map is the executed map.** A run that exercises every branch
  writes exactly the step families `STEPS` declares — no `x0` nobody declared,
  no declared step nothing runs. That assertion is the one this version exists
  to make possible, and it is written as a set comparison over a real journal
  rather than as a reading of the source.
- **The review goes to the person who submitted the invoice**, on their own
  desk, with their address on the Task — and an invoice nobody handed over
  falls back to the AP desk and says so.
- **A $0.00 total can never auto-post**, whatever the governed limit is.
- **The vendor question is folded into the one review**: the scored candidates
  ride the proposal, the decision may name one, and applying it is a guarded
  Action committed under the desk the review was routed to.
- **The second model call is absent, not empty**, on an invoice with nothing in
  doubt.
- **The graph writes are v6's**, unchanged: Invoice, InvoiceLine, MatchSet and
  the links between them.
- **The flywheel still turns**: a corrected field emits the correction Event and
  the pair lands in the dataset.
"""

import base64

import pytest

from app.config import settings
from app.gateway import ACTIONS
from app.ontology import names as onto
from app.tasks.accounts_payable import domain as payables
from app.tasks.accounts_payable.v9 import flow as v9
from tests import demo_world
from tests import world as test_world

PDF = b"%PDF-1.4\n% an invoice\n%%EOF\n"

SUBMITTER = "connie.alberts@naviant.com"
INTAKE = "flow:ap-intake"

#: A vendor in the master, an order to match against, and lines that pair with
#: it — so what a case is *about* is the one twist it adds.
CLEAN = "vendor=acme-industrial po=PO-7741 lines=STL-0250:2,FAS-M12:3"
#: A printed name the resolver scores inside the steward band: close enough to
#: "Acme Industrial Inc." to be a candidate, not close enough to assert. This
#: is what puts the vendor question in front of a person.
UNSETTLED = "vendor=acme-industrial-supply po=PO-7741 lines=STL-0250:2,FAS-M12:3"


@pytest.fixture()
def world():
    """A seeded world running v9 — the package's declared default."""
    world = test_world.fresh()
    demo_world.seed_payables(world.payables, sync=world.sync)
    return world


def _stored(world, case: str, uploaded_by: str = INTAKE) -> str:
    """One artifact whose *filename* names the case.

    v9 passes no hint to the reader, so a stub-steering case travels on the
    page or on the name — which is what a stub extractor reads. Under a real
    extractor both strings are inert and the document is what steers it.
    """
    record = world.documents.put_bytes(
        base64.b64encode(PDF + case.encode()).decode(),
        filename=f"{case}.pdf", kind="invoice", uploaded_by=uploaded_by,
    )
    return record.id


def _run(world, case: str, submitted_by: str = SUBMITTER):
    record = world.payables.submit(
        submitted_by, document_id=_stored(world, case, submitted_by)
    )
    return record, world.engine.get(record.run_id)


def _families(run) -> set[str]:
    """The step families a journal holds — loop suffixes trimmed.

    `r1_1` and `r1_2` are one declared step run twice, which is what the
    console shows and what a map can promise. A comparison against the raw ids
    would fail the moment a reviewer corrected something.
    """
    out = set()
    for step in run.steps:
        head, _, tail = step.step_id.rpartition("_")
        out.add(head if head and tail.isdigit() else step.step_id)
    return out


# ---- the map is the run ------------------------------------------------------


def test_the_declared_steps_are_exactly_the_steps_a_full_run_executes(world):
    """The claim v9 is for, over a run that reaches every branch.

    The case is built to exercise the conditional steps together: the vendor
    is unsettled (so the review carries the entity question and `a0` applies
    the answer), one line's arithmetic disagrees (so `m2` is asked for a box),
    and the first decision is a correction (so `x3` applies it and the match
    and rule run again). What is left over in either direction is the bug: a
    step in the journal that no map declares, or a map promising a step the
    engine never runs.
    """
    record, run = _run(world, f"{UNSETTLED} badmath invoice=ACME-INV-9100")
    task = world.tasks.get(run.open_task_id)
    chosen = task.proposal.vendor["candidates"][0]["id"]

    world.payables.resume_for_task(world.tasks.decide(
        task.id, "corrected", SUBMITTER,
        note="Read the line off the page again; and it is Acme Industrial.",
        payload={
            "fields": {"line_items[0].amount_usd": "972.00"},
            "vendor_id": chosen,
        },
    ))
    run = world.engine.get(record.run_id)
    second = world.tasks.get(run.open_task_id)
    posted = world.payables.resume_for_task(
        world.tasks.decide(second.id, "approved", SUBMITTER)
    )
    run = world.engine.get(record.run_id)

    assert run.status == "succeeded" and posted.state == "posted"
    assert _families(run) == {step.id for step in v9.STEPS}
    # And the order, because a map is a sequence and not a set: the checks sit
    # between the extraction and the vendor, and the gate sits between the rule
    # and the commit.
    assert [s.step_id for s in run.steps] == [
        "t1", "s1", "x0", "m1", "c1", "x1", "m2", "z1", "x2", "c2",
        "r1_1", "k1_1", "a0", "x3_1", "c2_1", "r1_2", "k1_2", "a1", "e1",
    ]


def test_x0_is_declared_and_holds_every_governed_value_the_run_reads(world):
    """The read that used to be invisible.

    v6 and v8 both ran an `x0` that no `STEPS` list mentioned, and read the
    posting policy, the arithmetic tolerances and the match tolerances from
    three other places besides. One step now, with every revision on its
    read-set — which is what lets somebody answer "what did this run consult"
    off the journal instead of off the source.
    """
    _record, run = _run(world, f"{CLEAN} invoice=ACME-INV-9101")
    step = run.step("x0")

    assert step is not None and step.name == "read-knowledge"
    reads = v9.GovernedReads.model_validate(step.output)
    assert reads.posting.limit_usd == 0.0            # the governed decision
    assert reads.arithmetic.min_field_confidence == 0.70
    assert reads.match.price_variance_pct == 2.0
    assert reads.routing.table.found_asset

    # Every one of them names the asset revision it was read at, and the step
    # declares those refs rather than leaving them inside a helper.
    assert "knowledge:invoice-posting-policy@rev1" in step.refs
    assert "knowledge:invoice-extraction-prompt@rev1" in step.refs
    assert "knowledge:entity-resolution-policy@rev1" in step.refs
    # And they reach the run's read-set, which is what the projection reads.
    assert {"knowledge:invoice-posting-policy@rev1",
            "knowledge:entity-resolution-policy@rev1"} <= set(run.refs)


def test_the_declared_extraction_model_is_the_configured_one():
    """A map that named a model the deployment does not run would be a lie in
    the one place a reader trusts. Both calls, because they are deliberately
    different models: reading a page and locating a field on it are two jobs,
    priced and switchable apart."""
    by_id = {step.id: step for step in v9.STEPS}
    assert by_id["m1"].config["model"] == settings.model
    assert by_id["m2"].config["model"] == settings.locate_model
    assert by_id["m1"].config["model"] != by_id["m2"].config["model"]


# ---- where the review goes ---------------------------------------------------


def test_the_review_lands_on_the_uploaders_own_desk(world):
    """The brief, in one assertion: the person who submitted the invoice is
    the person asked about it — on their own desk, with their address on the
    Task rather than inside an evidence row built for a human to read."""
    _record, run = _run(world, f"{CLEAN} invoice=ACME-INV-9102")
    task = world.tasks.get(run.open_task_id)
    desk = world.queues.ensure_personal(SUBMITTER)

    assert task.queue == desk.slug and desk.personal
    assert task.subject_email == SUBMITTER
    assert task.flow_version == 9
    # The desk is a claim somebody can check, so the Task says why it is here.
    why = next(e.value for e in task.evidence
               if e.label == "Routed to this desk because")
    assert "own desk" in why and "submitted" in why


def test_an_invoice_nobody_handed_over_falls_back_to_the_ap_desk(world):
    """`ap-intake` fetches invoices out of a folder, so there is no submitter
    to route back to. The governed table's fallback desk is the honest answer,
    and the run says it fell back rather than looking like a routing success.
    """
    _record, run = _run(world, f"{CLEAN} invoice=ACME-INV-9103", INTAKE)
    task = world.tasks.get(run.open_task_id)

    assert task.queue == payables.CLERK_QUEUE
    assert task.subject_email is None
    reads = v9.GovernedReads.model_validate(run.step("x0").output)
    assert reads.routing.fell_back and reads.routing.submitter_desk == ""
    assert "names no submitter" in reads.routing.explanation


def test_editing_the_approval_table_moves_the_next_review(world):
    """Routing is governed knowledge, so a finance owner moves it by editing a
    page. Nothing is recompiled and no code path changes."""
    from app.models import AssetUpdate

    asset = world.knowledge.repo.get(payables.POLICY_ASSET_ID)
    world.knowledge.update(payables.POLICY_ASSET_ID, AssetUpdate(
        content=asset.content.replace("| Over $0.00 | submitter |",
                                      "| Over $0.00 | vendor-stewardship |"),
        updated_by="finance-ops",
    ))
    _record, run = _run(world, f"{CLEAN} invoice=ACME-INV-9104")
    task = world.tasks.get(run.open_task_id)
    assert task.queue == "vendor-stewardship"
    # Still the submitter's case, even when somebody else reviews it.
    assert task.subject_email == SUBMITTER


def test_the_submitter_commits_the_post_from_their_own_desk(world):
    """The other half of routing to a personal desk: the Gateway checks the
    queue that raised the work, so `post-invoice` has to accept the Task's own
    desk. Pinned to `ap-clerk` alone, the submitter would answer their own
    review and have the write refused."""
    from app.auth import principal_for
    from app import personas

    record, run = _run(world, f"{CLEAN} invoice=ACME-INV-9105")
    task = world.tasks.get(run.open_task_id)
    decided = world.tasks.decide(task.id, "approved", SUBMITTER)
    posted = world.payables.resume_for_task(
        decided, principal_for(personas.get("customer-user:ap"))
    )

    assert posted.state == "posted"
    [entry] = [
        e for e in world.gateway.ledger.list()
        if e.action == "post-invoice" and e.run_id == record.run_id
    ]
    assert entry.outcome == "committed"
    assert entry.task_id == task.id and entry.decided_by == SUBMITTER
    # Both flow commits declare a standing desk *and* a per-case one; this is
    # the per-case half being exercised.
    assert ACTIONS["post-invoice"].queue_from_task


# ---- the rule ----------------------------------------------------------------


def test_a_zero_total_can_never_auto_post_whatever_the_limit_says():
    """The live estate posted a $0.00 invoice as "matched".

    Nothing was wrong with the machinery: the limit comparison passed, because
    nothing is *above* zero either, and a match with no amounts to compare had
    nothing to disagree with. So the refusal is structural and sits ahead of
    the limit — asserted here as a unit, against a policy whose limit is
    generous, because the point is that no limit can clear it.
    """
    from app.matching import MatchResult

    generous = payables.PostingPolicy(limit_usd=2500.0, min_confidence=0.0)
    matched = MatchResult(verdict="matched")
    unread = payables.InvoiceExtraction(
        vendor_name="Acme Industrial Inc.", invoice_number="ACME-0",
        invoice_date="2026-08-30", total_usd=0.0, confidence=0.99,
    )
    reasons = payables._posting_check(unread, matched, {}, generous)["reasons"]
    assert reasons and "reads $0.00" in reasons[0]

    # And a real total under the same limit posts, so the clause above is about
    # zero rather than about being strict.
    read = unread.model_copy(update={"total_usd": 1200.0})
    assert payables._posting_check(read, matched, {}, generous)["reasons"] == []


def test_a_run_whose_extraction_reads_no_total_stops_for_a_person(world):
    """The same refusal through the flow, because a unit test of a pure rule
    cannot say whether the flow calls it."""

    class BlankReader:
        name = "stub"

        def read(self, image, filename, hint, *, prompt="", media_type=""):
            return payables.InvoiceExtraction(
                vendor_name="Acme Industrial Inc.",
                invoice_number="ACME-INV-9106",
                invoice_date="2026-08-30", total_usd=0.0, confidence=0.99,
                model="stub-extractor",
            )

    world.payables.reader = BlankReader()
    record, run = _run(world, "invoice=ACME-INV-9106")

    assert run.status == "suspended" and record.state == "pending"
    held = run.step("r1_1").output["reasons"]
    assert any("reads $0.00" in reason for reason in held)
    # Nothing was written: no Invoice object exists for it.
    assert not world.graph.find(onto.INVOICE, limit=50) or not [
        obj for obj in world.graph.find(onto.INVOICE, limit=50)
        if obj.props.get("invoice_number") == "ACME-INV-9106"
    ]


# ---- the second call, and its absence ----------------------------------------


def test_a_clean_invoice_makes_one_model_call_and_the_second_step_is_absent(world):
    """No boxes needed, so no call and no cost — and the journal shows `m2`
    *missing* rather than present-and-empty, which is the honest record of a
    call nobody made."""
    _record, run = _run(world, f"{CLEAN} invoice=ACME-INV-9107")

    assert [s.step_id for s in run.steps if s.kind == "modelcall"] == ["m1"]
    assert run.step("m2") is None
    assert run.model_calls == 1
    verdicts = v9.VerdictSet.model_validate(run.step("x1").output)
    assert verdicts.clean
    # The Task still carries a field list, and it is empty rather than absent:
    # this version looked and found nothing, which is a different message to
    # the screen from "this version computes no verdicts".
    task = world.tasks.get(run.open_task_id)
    assert task.proposal.fields == []


def test_a_disputed_field_is_located_and_the_box_reaches_the_task(world):
    """The other branch: one number in doubt, one narrow call, one box."""
    _record, run = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9108")

    located = run.step("m2")
    assert located is not None and located.name == "locate-fields"
    task = world.tasks.get(run.open_task_id)
    [field] = task.proposal.fields
    assert field["path"] == "line_items[0].amount_usd"
    assert field["status"] == "inconsistent"
    assert field["region"] is not None
    # The check's own sentence reaches the reviewer whole.
    assert "but this line reads" in field["reason"]


# ---- the vendor, folded in ----------------------------------------------------


def test_an_unsettled_vendor_travels_into_the_one_review(world):
    """v6 stopped the run and asked. v9 carries the question to the same person
    who is about to read the page anyway — and asserts nothing about the
    identity until they answer."""
    record, run = _run(world, f"{UNSETTLED} invoice=ACME-INV-9109")

    assert run.step("z1").output["disposition"] == "review"
    # Nothing was gated on its own: one task step in the whole journal.
    assert [s.step_id for s in run.steps if s.kind == "task"] == ["k1_1"]
    assert run.step("a0") is None

    task = world.tasks.get(run.open_task_id)
    vendor = task.proposal.vendor
    assert vendor["resolved"] is None
    assert vendor["printed"] == "Acme Industrial Supply"
    assert vendor["candidates"][0]["name"] == "Acme Industrial Inc."
    assert 0.7 <= vendor["candidates"][0]["score"] < 0.92
    assert all({"id", "name", "score"} == set(c) for c in vendor["candidates"])

    # The entity question is said out loud on the Task rather than left to be
    # inferred from the presence of a block. It is the flow's reason and not
    # the Rule's, so it is on the question a person reads and not in `r1`'s
    # output — which stays exactly what the governed check returned.
    assert "could not settle which vendor" in task.summary
    assert "name it here" in task.summary
    assert not any(
        "vendor" in reason for reason in run.step("r1_1").output["reasons"]
    )
    assert record.state == "pending"


def test_the_reviewers_vendor_choice_is_applied_through_the_guarded_action(world):
    """`vendor_id` on the decision, `confirm-vendor` at the write, and the
    desk the review was routed to on the ledger entry.

    The Action derives its desk from the Task for exactly this reason: the
    review is on the submitter's own queue, and a pin on `ap-clerk` alone would
    refuse them the answer they were asked for.
    """
    record, run = _run(world, f"{UNSETTLED} invoice=ACME-INV-9110")
    task = world.tasks.get(run.open_task_id)
    chosen = task.proposal.vendor["candidates"][0]["id"]

    posted = world.payables.resume_for_task(world.tasks.decide(
        task.id, "approved", SUBMITTER,
        payload={"vendor_id": chosen},
        note="It is Acme Industrial — same company, different letterhead.",
    ))

    run = world.engine.get(record.run_id)
    applied = run.step("a0")
    assert applied is not None and applied.output["vendor_id"] == chosen
    assert applied.output["action"] == "linked"
    assert posted.state == "posted" and posted.vendor_id == chosen

    [entry] = [
        e for e in world.gateway.ledger.list()
        if e.action == "confirm-vendor" and e.run_id == record.run_id
    ]
    assert entry.outcome == "committed"
    assert entry.decided_by == SUBMITTER
    assert entry.task_id == task.id
    assert ACTIONS["confirm-vendor"].queue_from_task

    # The reviewer's answer is an assertion on the vendor, sourced to them.
    vendor = world.graph.get(chosen)
    assert "Acme Industrial Supply" in (vendor.props.get("aliases") or [])


# ---- the graph, unchanged -----------------------------------------------------


def test_the_commit_writes_the_same_objects_and_links_v6_wrote(world):
    """Simplifying the map must not simplify the record. Invoice, its lines,
    the MatchSet and the links between them are what a person can open two
    years from now, and they are v6's shapes with v6's names."""
    record, run = _run(world, f"{CLEAN} invoice=ACME-INV-9111")
    posted = world.payables.resume_for_task(world.tasks.decide(
        world.tasks.get(run.open_task_id).id, "approved", SUBMITTER,
    ))

    assert posted.state == "posted" and posted.match_verdict == "matched"
    invoice = world.graph.get(posted.graph_ref.split("/")[1].split("@")[0])
    assert invoice.props["invoice_number"] == "ACME-INV-9111"
    assert invoice.props["state"] == "posted"

    lines = world.graph.neighbors(invoice.id, "has_invoice_line", "out")
    assert len(lines) == len(posted.lines)
    assert world.graph.neighbors(invoice.id, "evidenced_by_document", "out")
    assert world.graph.neighbors(invoice.id, "billed_against", "out")
    [(_, vendor)] = world.graph.neighbors(invoice.id, "issued", "in")
    assert vendor.id == posted.vendor_id

    match = world.graph.get(posted.match_ref.split("/")[1].split("@")[0])
    assert match.props["verdict"] == "matched"
    assert match.props["matched_lines"] == len(posted.lines)
    assert len(world.graph.neighbors(match.id, "matches_invoice_line", "out")) == (
        len(posted.lines)
    )
    assert world.graph.check_invariants() == []


# ---- the flywheel -------------------------------------------------------------


def test_a_correction_under_v9_still_feeds_the_correction_set(world):
    """The route doctrine asks for, unchanged by the new map: the decision
    emits `ExtractionCorrected`, `ap-corrections` answers it in its own run,
    and the invoice below posts whether or not that knowledge write lands."""
    before = len(world.corrections.list())
    record, run = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9112")
    document_id = run.step("s1").output["document_id"]

    world.payables.resume_for_task(world.tasks.decide(
        run.open_task_id, "corrected", SUBMITTER,
        payload={"fields": {"line_items[0].amount_usd": "972.00"}},
    ))

    appended = [
        pair for pair in world.corrections.list()
        if pair.document_id == document_id
    ]
    assert len(world.corrections.list()) == before + len(appended)
    [pair] = appended
    assert pair.path == "line_items[0].amount_usd"
    assert (pair.raw_value, pair.corrected_value) == ("990.00", "972.00")
    assert pair.status == "inconsistent"
    assert pair.decided_by == SUBMITTER
    assert pair.source_flow_version == 9

    # By Event, in its own run, which is what keeps a correction-set outage
    # from being able to stop a payment.
    correction_runs = [
        r for r in world.engine.list(flow_id="ap-corrections")
        if r.case_ref == world.engine.get(record.run_id).case_ref
    ]
    assert len(correction_runs) == 1
    assert correction_runs[0].status == "succeeded"


# ---- what v9 cut --------------------------------------------------------------


def test_the_cut_steps_are_gone_from_the_map_and_the_run(world):
    """Three deletions, asserted so they cannot creep back: the vendor gate,
    the line-projection step that did nothing, and the anomaly screen — which
    is not lost, only not on this path. v8 still ships it."""
    from app.tasks.accounts_payable.v8 import flow as v8

    declared = {step.id for step in v9.STEPS}
    assert not declared & {"k0", "x3_project_lines", "x5", "r2", "k2"}
    assert "project-lines" not in {step.name for step in v9.STEPS}
    assert "anomaly-screen" not in {step.name for step in v9.STEPS}
    assert "anomaly-screen" in {step.name for step in v8.STEPS}

    _record, run = _run(world, f"{CLEAN} invoice=ACME-INV-9113")
    record = world.payables.get(run.case_ref or run.id)
    # The row still renders: the screen was not evaluated, and the projection
    # says that rather than reporting zero findings as a clean bill.
    assert record.anomaly_findings == []
    assert record.fraud_screen == "not evaluated"


def test_the_clause_registry_is_read_off_the_version_that_runs(world):
    """The workspace's governed-value list must match the version it renders
    beside: v9 consults no anomaly thresholds and does resolve review routing,
    v6 is the reverse, and an unshipped version overstates rather than hides."""
    nine = {c.id for c in payables.read_policy_clauses(world.knowledge, version=9)}
    assert "review-routing" in nine
    assert not nine & {"amount-outlier", "duplicate-window"}
    routing_clause = next(
        c for c in payables.read_policy_clauses(world.knowledge, version=9)
        if c.id == "review-routing"
    )
    assert routing_clause.value == "submitter (fallback ap-clerk)"

    six = {c.id for c in payables.read_policy_clauses(world.knowledge, version=6)}
    assert {"amount-outlier", "duplicate-window"} <= six
    assert "review-routing" not in six

    unshipped = {c.id for c in payables.read_policy_clauses(world.knowledge, version=99)}
    assert {"amount-outlier", "duplicate-window", "review-routing"} <= unshipped
