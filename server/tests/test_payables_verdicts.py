"""Field verdicts: per-field confidence, the arithmetic check, targeted boxes.

The claim this file protects, in one sentence: **a clerk is asked about three
fields, not thirty, and the system can say which three and why.**

Four properties hold that up, and each of them is cheap to lose:

- the extractor reports a confidence *per field*, not one for the page;
- a deterministic check compares the invoice against itself and every failure
  names the exact field path it implicates;
- those two unions are the only fields sent to the locate call, so a clean
  invoice makes **one** model call and a doubtful one makes **two**, the second
  returning boxes for exactly the disputed paths;
- what the clerk then changes is kept — the correction set, appended
  automatically, promoted never.
"""

import base64

import pytest

from app.documents import Region
from app.models import AssetUpdate
from app.tasks.accounts_payable import domain as payables
from app.tasks.accounts_payable import paths
from app.tasks.accounts_payable.domain import (
    InvoiceExtraction,
    InvoiceLineExtract,
)
from app.tasks.accounts_payable.v8 import locate
from app.tasks.accounts_payable.v8.arithmetic import (
    ArithmeticTolerances,
    check_arithmetic,
    parse_arithmetic_tolerances,
)
from app.tasks.accounts_payable.v8.verdicts import verdicts_for
from tests import demo_world
from tests import world as test_world

PDF = b"%PDF-1.4\n% an invoice\n%%EOF\n"
CLERK = "dana.prentiss@naviant.com"

#: A vendor in the master, an order to match against, and lines that pair with
#: it — so what a case is *about* is the one twist it adds.
CLEAN = "vendor=acme-industrial po=PO-7741 lines=STL-0250:2,FAS-M12:3"


@pytest.fixture()
def world():
    world = test_world.fresh()
    demo_world.seed_payables(world.payables, sync=world.sync)
    # v8 is a candidate: nothing has made it live, so a test that wants it has
    # to say so — exactly as the console would.
    world.engine.live_version = lambda flow_id: (
        8 if flow_id == payables.FLOW_ID else 0
    )
    return world


def _run(world, name: str, **kwargs):
    """One v8 run over a stored artifact whose filename names the case.

    v8 takes no hint — that is the whole of WP-B — so a stub-steering case is
    carried on the *filename*, which is what a stub extractor reads. Under a
    real extractor the page is what steers it and this string is inert.
    """
    document = world.documents.put_bytes(
        base64.b64encode(PDF + name.encode()).decode(),
        filename=f"{name}.pdf", kind="invoice", uploaded_by="flow:ap-intake",
    )
    record = world.payables.submit(
        submitted_by="flow:ap-intake", document_id=document.id, **kwargs
    )
    return record, world.engine.get(record.run_id), document.id


def _verdicts(world, run) -> list[dict]:
    task = world.tasks.get(run.open_task_id)
    return list(task.proposal.fields or [])


def _model_calls(run) -> list[str]:
    return [step.step_id for step in run.steps if step.kind == "modelcall"]


# ---- the gate ----------------------------------------------------------------


def test_a_clean_invoice_raises_no_verdicts_and_makes_one_model_call(world):
    """No doubts, no boxes, no second call — and the review screen is told so.

    `fields: []` is not the same message as no `fields` at all: the empty list
    says the flow looked and found nothing, which is what lets the screen offer
    a one-click approval instead of opening thirty inputs. A version that
    computes no verdicts sends `None`, and the test below holds that apart.
    """
    record, run, document_id = _run(world, f"{CLEAN} invoice=ACME-INV-9001")

    assert run.step("x2").output["in_doubt"] == []
    assert _model_calls(run) == ["m1"]
    assert run.step("m2") is None  # not merely empty — never called
    assert world.documents.get(document_id).regions == []

    # It is still reviewed, because the governed limit is $0.00 — and the only
    # thing the clerk is being asked is that.
    assert record.state == "pending"
    task = world.tasks.get(run.open_task_id)
    assert task.proposal.fields == []
    # One reason, and it is the policy rather than anything read off the page.
    assert record.verdict_reason.endswith("exceeds the $0.00 auto-post limit")


def test_bad_line_arithmetic_names_the_line_and_locates_only_that_line(world):
    """The gate's other half: two calls, and the second about one field.

    The invoice is otherwise perfect — it matches its order, the vendor is in
    the master, the document's own totals add up — and exactly one number on it
    disagrees with the two beside it. What the clerk is handed is that number.
    """
    record, run, document_id = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9002")

    path = "line_items[0].amount_usd"
    assert run.step("c0").output[0]["path"] == path
    assert run.step("x2").output["in_doubt"] == [path]
    assert _model_calls(run) == ["m1", "m2"]

    # The box is on that field and on nothing else.
    assert [region.field for region in world.documents.get(document_id).regions] == [path]
    assert run.step("m2").writes == [f"document:{document_id}"]

    verdict = _verdicts(world, run)[0]
    assert verdict["path"] == path
    assert verdict["status"] == "inconsistent"
    assert verdict["label"] == "Line 1 — amount"
    assert "2 × $486.00 is $972.00" in verdict["reason"]
    assert verdict["region"]["field"] == path

    # And the rule held the invoice for it, naming the path rather than the
    # vague "the lines do not add up to the total" v6 would have said.
    assert path in record.verdict_reason
    assert "lines sum to" not in record.verdict_reason


def test_a_low_confidence_field_is_questioned_while_the_page_is_not(world):
    """The case a document-level score cannot reach.

    The extractor is confident about the page and unsure about one number on
    it. The posting rule's minimum *extraction* confidence never fires — the
    document reads 0.93 — and the field verdict does, which is the whole reason
    the two are separate governed values.
    """
    record, run, document_id = _run(world, f"{CLEAN} smudged invoice=ACME-INV-9003")

    extraction = InvoiceExtraction.model_validate(run.step("m1").output)
    assert extraction.confidence == 0.93
    assert extraction.field_confidence["invoice.total_usd"] == 0.40
    assert "confidence" not in record.verdict_reason

    verdict = _verdicts(world, run)[0]
    assert verdict["path"] == "invoice.total_usd"
    assert verdict["status"] == "low_confidence"
    assert verdict["confidence"] == 0.40
    assert "below the governed floor of 0.70" in verdict["reason"]
    assert [r.field for r in world.documents.get(document_id).regions] == [
        "invoice.total_usd"
    ]


def test_a_missing_required_field_is_missing_rather_than_unsure(world):
    """Absent is not low confidence, and the clerk is told which it is."""
    real = world.payables.reader.read

    def without_a_number(image, filename, hint, **kwargs):
        return real(image, filename, hint, **kwargs).model_copy(
            update={"invoice_number": ""}
        )

    world.payables.reader.read = without_a_number
    _record, run, _document_id = _run(world, f"{CLEAN} invoice=ACME-INV-9004")

    verdict = _verdicts(world, run)[0]
    assert verdict["path"] == "invoice.invoice_number"
    assert verdict["status"] == "missing"
    assert "required to post an invoice" in verdict["reason"]
    assert verdict["value"] == ""
    # A missing field still gets a box: where the number should have been is
    # exactly what a person checks first.
    assert verdict["region"]["field"] == "invoice.invoice_number"


# ---- the check itself, without a world ---------------------------------------


def _extraction(**overrides) -> InvoiceExtraction:
    base = InvoiceExtraction(
        vendor_name="Acme Industrial Inc.", invoice_number="ACME-1",
        invoice_date="2026-08-20", due_date="2026-09-19",
        subtotal_usd=1000.0, freight_usd=50.0, tax_usd=0.0, total_usd=1050.0,
        lines=[InvoiceLineExtract(
            sku="STL-0250", description="steel", quantity=2.0,
            unit_price_usd=500.0, amount_usd=1000.0,
        )],
    )
    return base.model_copy(update=overrides)


def test_the_check_agrees_with_an_invoice_that_agrees_with_itself():
    assert check_arithmetic(_extraction(), ArithmeticTolerances()) == []


def test_every_failure_names_a_path():
    """The whole point of the step, asserted as a property rather than a case.

    A finding with no path — or with one nothing can resolve — is a finding
    that cannot become a box, cannot focus an input and cannot be matched to a
    correction. It would look like a working check and teach a clerk nothing.
    """
    broken = _extraction(
        vendor_name="", due_date="2026-08-01", total_usd=2000.0,
        lines=[InvoiceLineExtract(
            sku="STL-0250", quantity=2.0, unit_price_usd=500.0, amount_usd=1234.0,
        )],
    )
    findings = check_arithmetic(broken, ArithmeticTolerances())
    assert {f.path for f in findings} == {
        "invoice.vendor_name", "invoice.due_date", "invoice.total_usd",
        "invoice.subtotal_usd", "line_items[0].amount_usd",
    }
    for finding in findings:
        assert paths.parse_path(finding.path)[1], finding.path
        assert finding.reason.strip()


def test_a_lump_sum_line_is_not_an_arithmetic_failure():
    """Freight billed as one number with no quantity and no rate is normal.

    Flagging it would put a verdict on nearly every invoice in the estate,
    which is the failure mode this whole lane exists to avoid: a review queue
    that cries wolf is read as noise within a week.
    """
    lump = _extraction(lines=[
        InvoiceLineExtract(sku="STL-0250", quantity=2.0, unit_price_usd=500.0,
                           amount_usd=1000.0),
        InvoiceLineExtract(sku="FRT-STD", description="Freight", kind="freight",
                           amount_usd=50.0),
    ])
    assert check_arithmetic(lump, ArithmeticTolerances()) == []


def test_a_total_agreeing_with_either_account_of_it_is_not_a_question():
    """Freight stated as a line and not as a field is normal, not an error.

    The document has two accounts of its own total — the summary fields and
    the billed lines — and real vendors keep one or the other. Demanding both
    would flag a large fraction of correctly-read invoices, and a check that
    fires on good invoices is one a desk stops reading.
    """
    as_a_line = _extraction(
        subtotal_usd=1000.0, freight_usd=0.0, total_usd=1050.0,
        lines=[
            InvoiceLineExtract(sku="STL-0250", quantity=2.0,
                               unit_price_usd=500.0, amount_usd=1000.0),
            InvoiceLineExtract(sku="FRT-STD", kind="freight", amount_usd=50.0),
        ],
    )
    assert check_arithmetic(as_a_line, ArithmeticTolerances()) == []

    # A total that agrees with neither account was misread, and is named.
    misread = as_a_line.model_copy(update={"total_usd": 1500.0})
    findings = check_arithmetic(misread, ArithmeticTolerances())
    assert [f.path for f in findings] == ["invoice.total_usd"]
    assert "add up to $1,050.00" in findings[0].reason


def test_a_due_date_before_the_invoice_date_is_a_misreading():
    findings = check_arithmetic(
        _extraction(due_date="2026-08-01"), ArithmeticTolerances()
    )
    assert [f.path for f in findings] == ["invoice.due_date"]
    assert "before it was issued" in findings[0].reason


def test_the_tolerances_are_governed_and_not_literals(world):
    """Widen the policy and the same invoice stops being a question.

    A cent of slack per line is a finance decision, and the point of it living
    in a document is that changing it takes an edit rather than a deploy.
    """
    _record, run, _document_id = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9005")
    assert run.step("x2").output["in_doubt"] == ["line_items[0].amount_usd"]

    content = world.knowledge.repo.get(payables.POLICY_ASSET_ID).content
    world.knowledge.update(payables.POLICY_ASSET_ID, AssetUpdate(
        content=content.replace(
            "Line arithmetic tolerance: $0.01",
            "Line arithmetic tolerance: $25.00",
        )
    ))
    _again, widened, _doc = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9006")
    assert widened.step("x2").output["in_doubt"] == []
    assert _model_calls(widened) == ["m1"]
    # And the step said which revision it read, both times.
    assert run.step("x1").refs[0].endswith("@rev1")
    assert widened.step("x1").refs[0].endswith("@rev2")


def test_the_policy_defaults_hold_when_the_corpus_has_none():
    """A deployment whose corpus has not seeded still checks arithmetic."""
    defaults = parse_arithmetic_tolerances("", "knowledge:invoice-posting-policy")
    assert not defaults.found_asset
    assert defaults.line_amount_usd == 0.01
    assert defaults.min_field_confidence == 0.70


# ---- per-field confidence ------------------------------------------------------


def test_a_models_per_field_scores_are_rekeyed_to_fact_paths():
    """The model answers in its own vocabulary; the paths are derived here.

    Asking a model to emit `line_items[1].amount_usd` is asking it to do index
    arithmetic while reading a page, and it is the index that would be wrong.
    """
    scores = payables._field_confidence({
        "field_confidence": {"total_usd": 0.91, "not_a_field": 0.4},
        "lines": [
            {"sku": "A", "field_confidence": {"amount_usd": 0.5}},
            {"sku": "B", "confidence": 0.6},
        ],
    })
    assert scores["invoice.total_usd"] == 0.91
    assert scores["line_items[0].amount_usd"] == 0.5
    # A single score for a line spreads over that line's fields: "this row was
    # hard to read" is a claim about every number on it.
    assert scores["line_items[1].quantity"] == 0.6
    assert scores["line_items[1].amount_usd"] == 0.6
    # A field nothing declares, and a score outside 0–1, are both dropped: a
    # confidence of 4 is not certainty, it is an unanswered question.
    assert "invoice.not_a_field" not in scores


def test_silence_is_not_doubt():
    """0.0 means the extractor said nothing about a field. It is not a verdict.

    Reading it as doubt would flag every field of every extraction made before
    the prompt asked for per-field scores — an entire back catalogue of
    invoices "in doubt" because nobody was asked.
    """
    quiet = _extraction()
    assert quiet.field_confidence == {}
    verdicts = verdicts_for(quiet, [], min_field_confidence=0.7)
    assert {v.status for v in verdicts} == {"ok"}


def test_the_document_confidence_still_reaches_the_posting_rule(world):
    """J1 added a field; it did not take one away.

    The rule reads the whole-document score and the stub still reports one, so
    a page nobody could read is held on the old clause even when every
    individual field happens to clear the field floor.
    """
    record, run, _document_id = _run(world, f"{CLEAN} blurry invoice=ACME-INV-9007")
    assert InvoiceExtraction.model_validate(run.step("m1").output).confidence == 0.55
    assert "extraction confidence 0.55 is below 0.75" in record.verdict_reason


# ---- the locate call's guards ---------------------------------------------------


def _box(field: str, **overrides) -> Region:
    return Region(
        field=field, page=1, x=0.1, y=0.1, width=0.2, height=0.05,
    ).model_copy(update=overrides)


def test_a_box_for_a_field_nobody_asked_about_is_refused():
    kept, refused = locate.accept(
        [_box("invoice.total_usd"), _box("invoice.vendor_name")],
        ["invoice.total_usd"],
    )
    assert [region.field for region in kept] == ["invoice.total_usd"]
    assert "did not ask about" in refused[0]


def test_a_box_off_the_page_is_refused():
    kept, refused = locate.accept(
        [
            _box("invoice.total_usd", x=0.95, width=0.2),
            _box("invoice.due_date", height=0.0),
            _box("invoice.po_number", y=-0.4),
        ],
        ["invoice.total_usd", "invoice.due_date", "invoice.po_number"],
    )
    assert kept == []
    assert len(refused) == 3
    assert all("not inside the page" in reason for reason in refused)


def test_a_box_on_a_page_the_document_does_not_have_is_refused():
    kept, refused = locate.accept(
        [_box("invoice.total_usd", page=7)], ["invoice.total_usd"], pages=2
    )
    assert kept == []
    assert "page 7 of a 2-page document" in refused[0]


def test_two_boxes_for_one_field_keep_the_more_confident_one():
    """Two rectangles on one number leave a reviewer nothing to read."""
    kept, refused = locate.accept(
        [
            _box("invoice.total_usd", x=0.10, confidence=0.4),
            _box("invoice.total_usd", x=0.70, confidence=0.9),
        ],
        ["invoice.total_usd"],
    )
    assert [(r.field, r.x) for r in kept] == [("invoice.total_usd", 0.70)]
    assert "a second box" in refused[0]


def test_boxes_come_back_in_the_order_they_were_asked_for():
    kept, _refused = locate.accept(
        [_box("invoice.due_date"), _box("invoice.total_usd")],
        ["invoice.total_usd", "invoice.due_date"],
    )
    assert [region.field for region in kept] == [
        "invoice.total_usd", "invoice.due_date"
    ]


def test_a_locator_that_answers_with_junk_stores_nothing_and_says_so(world):
    """The guard, in the flow rather than in a unit test.

    A model that returns a box for a field nobody asked about, off the page, is
    not a failed run: the invoice is still reviewed, with no highlight. What
    must not happen is a rectangle drawn over the wrong part of a page with the
    system's confidence behind it.
    """
    class Wandering:
        name = "wandering"

        def locate(self, image, filename, paths_asked, **kwargs):
            return locate.LocatedFields(
                model="wandering",
                regions=[
                    _box("invoice.vendor_name"),
                    _box(paths_asked[0], x=1.4),
                ],
            )

    world.payables.locator = Wandering()
    _record, run, document_id = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9008")

    assert _model_calls(run) == ["m1", "m2"]
    assert world.documents.get(document_id).regions == []
    assert run.step("m2").writes == []
    assert "2 box(es) refused" in run.step("m2").summary
    # The field is still in doubt and the clerk is still asked about it.
    assert _verdicts(world, run)[0]["path"] == "line_items[0].amount_usd"
    assert _verdicts(world, run)[0]["region"] is None


def test_a_deployment_with_no_locator_says_so_rather_than_looking_clean(world):
    world.payables.locator = None
    _record, run, document_id = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9009")
    assert "no field locator is wired" in run.step("m2").summary
    assert world.documents.get(document_id).regions == []


def test_an_unknown_backend_cannot_build_a_locator():
    """A typo must not produce the thing that invents rectangles."""
    from app import modelcall

    with pytest.raises(modelcall.UnusableModelBackend):
        locate.build_field_locator("vertexx", None, "", "")
    assert locate.build_field_locator("none", None, "", "").name == "stub"


# ---- the correction set --------------------------------------------------------


def test_a_correction_appends_a_raw_to_corrected_pair(world):
    """The crown-jewel asset, and the route doctrine asks for.

    The clerk's answer arrives keyed by the same fact path the verdict was
    raised under, which is what makes the pair clean: what the model read, what
    the human said instead, on which document. The append happens on the far
    side of an emitted Event — its own run, its own ledger entry — so the
    invoice below posts whether or not the knowledge write lands.
    """
    before = len(world.corrections.list())
    _record, run, document_id = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9010")

    decided = world.tasks.decide(
        run.open_task_id, "corrected", CLERK,
        note="Read the line off the page again.",
        payload={"fields": {"line_items[0].amount_usd": "972.00"}},
    )
    world.payables.resume_for_task(decided)

    appended = world.corrections.list()
    assert len(appended) == before + 1
    pair = appended[0]
    assert pair.path == "line_items[0].amount_usd"
    assert pair.label == "Line 1 — amount"
    assert (pair.raw_value, pair.corrected_value) == ("990.00", "972.00")
    assert pair.status == "inconsistent"
    assert pair.document_id == document_id
    assert pair.decided_by == CLERK
    assert pair.source_run_id == run.id
    assert pair.source_flow_version == 8

    # It travelled by Event, and the run that answered it is in the fleet.
    correction_runs = [
        r for r in world.engine.list(flow_id="ap-corrections")
        if r.case_ref == run.case_ref
    ]
    assert len(correction_runs) == 1
    assert correction_runs[0].status == "succeeded"
    assert correction_runs[0].outputs["appended"] == 1


def test_the_correction_is_also_applied_to_the_invoice(world):
    """The pair is kept *and* the number is fixed. Two effects, one decision."""
    _record, run, _document_id = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9011")
    decided = world.tasks.decide(
        run.open_task_id, "corrected", CLERK,
        payload={"fields": {"line_items[0].amount_usd": "972.00"}},
    )
    resumed = world.payables.resume_for_task(decided)
    assert resumed.lines[0].amount_usd == 972.00
    # And the arithmetic the clerk settled is gone from the next round's reason.
    assert "line_items[0].amount_usd" not in resumed.verdict_reason


def test_an_unchanged_field_is_not_a_correction(world):
    """The screen sends the whole form back; most of it is agreement.

    A correction set full of `raw == corrected` would drown the pairs that
    carry signal, so equality — numerically, not by punctuation — is dropped.
    """
    before = len(world.corrections.list())
    _record, run, _document_id = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9012")
    decided = world.tasks.decide(
        run.open_task_id, "corrected", CLERK,
        payload={"fields": {
            "line_items[0].amount_usd": "990.00",   # unchanged
            "invoice.po_number": "PO-7741",          # unchanged
        }},
    )
    world.payables.resume_for_task(decided)
    assert len(world.corrections.list()) == before


def test_nothing_promotes_a_correction_into_the_prompt(world):
    """Appending is automatic; teaching the model is not.

    Doctrine reserves promotion for a governed decision — a Rule that raises a
    curation Task — and the failure this pins is the tempting one: a system
    that quietly rewrites its own instructions because it noticed a pattern.
    """
    prompt = world.knowledge.repo.get(payables.PROMPT_ASSET_ID)
    _record, run, _document_id = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9013")
    world.payables.resume_for_task(world.tasks.decide(
        run.open_task_id, "corrected", CLERK,
        payload={"fields": {"line_items[0].amount_usd": "972.00"}},
    ))
    after = world.knowledge.repo.get(payables.PROMPT_ASSET_ID)
    assert after.revision == prompt.revision
    assert after.content == prompt.content


def test_an_approval_teaches_the_set_nothing(world):
    """A clerk agreeing is not a raw→corrected pair, and is not stored as one."""
    before = len(world.corrections.list())
    _record, run, _document_id = _run(world, f"{CLEAN} invoice=ACME-INV-9014")
    world.payables.resume_for_task(world.tasks.decide(
        run.open_task_id, "approved", CLERK,
    ))
    assert len(world.corrections.list()) == before
    # And no run at all: an approval is not a fact the flywheel responds to.
    assert not [
        r for r in world.engine.list(flow_id="ap-corrections")
        if r.case_ref == run.case_ref
    ]


def test_the_same_decision_twice_appends_once(world):
    """Two doors reach `resume_for_task`; the pair must land once.

    The API hook and a script both answering one task is a real sequence, and
    an append-only set that double-counts is a tuning set with an invented
    consensus in it.
    """
    _record, run, _document_id = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9015")
    decided = world.tasks.decide(
        run.open_task_id, "corrected", CLERK,
        payload={"fields": {"line_items[0].amount_usd": "972.00"}},
    )
    world.payables.resume_for_task(decided)
    world.payables.resume_for_task(decided)
    assert len([
        pair for pair in world.corrections.list() if pair.source_run_id == run.id
    ]) == 1


def test_the_append_is_a_governed_write_with_a_ledger_entry(world):
    """Not a raw storage write: the doctrine's route, and the record it leaves."""
    _record, run, _document_id = _run(world, f"{CLEAN} badmath invoice=ACME-INV-9016")
    world.payables.resume_for_task(world.tasks.decide(
        run.open_task_id, "corrected", CLERK,
        payload={"fields": {"line_items[0].amount_usd": "972.00"}},
    ))
    entry = next(
        e for e in world.gateway.ledger.list() if e.action == "append-correction"
    )
    assert entry.outcome == "committed"
    assert entry.regime == "observation"
    assert entry.decided_by == CLERK
    # The write-set names the dataset rows, so the entry cannot be read as a
    # no-op — which is what an empty write-set on a committed Action looks like.
    assert entry.writes and entry.writes[0].startswith("dataset:ap-field-corrections/")


def test_the_older_patch_shape_still_appends(world):
    """Eval cases and the seeded world speak the v6 patch, and keep working."""
    _record, run, _document_id = _run(
        world, f"{CLEAN} pricebump invoice=ACME-INV-9017"
    )
    world.payables.resume_for_task(world.tasks.decide(
        run.open_task_id, "corrected", CLERK,
        payload={"lines": [{"ordinal": 1, "unit_price_usd": 486.00}]},
    ))
    pair = next(
        p for p in world.corrections.list()
        if p.path == "line_items[0].unit_price_usd"
    )
    assert (pair.raw_value, pair.corrected_value) == ("524.88", "486.00")
    # Nothing flagged this field, and the pair says so rather than inventing a
    # status: a clerk correcting something nobody doubted is the most
    # interesting row in the set.
    assert pair.status == ""


# ---- the versions that came before ---------------------------------------------


def test_v6_raises_no_verdicts_and_says_nothing_about_fields():
    """`None` and `[]` are different messages to the screen.

    A version that computes no verdicts must not tell a reviewer "we looked and
    it is fine" about an invoice nobody checked, so the older versions send no
    list at all and the screen opens the whole form.
    """
    world = test_world.fresh()
    demo_world.seed_payables(world.payables, sync=world.sync)
    # Said out loud, because v6 is no longer what a fresh world runs: v9 is
    # the default, and a test about *v6's* silence has to name v6.
    world.engine.live_version = lambda flow_id: (
        6 if flow_id == payables.FLOW_ID else 0
    )
    record = world.payables.submit(
        "connie.alberts@naviant.com", filename="acme.pdf",
        hint=f"{CLEAN} invoice=ACME-V6-1",
    )
    run = world.engine.get(record.run_id)
    assert run.flow_version == 6
    task = world.tasks.get(run.open_task_id)
    assert task.proposal.fields is None
