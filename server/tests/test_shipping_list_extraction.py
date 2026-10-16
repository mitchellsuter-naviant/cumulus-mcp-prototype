"""One job's ticket: read once, checked twice, asked about once, written once.

The properties worth defending here are mostly about restraint.

- **The declared map is the executed map.** A run that reaches every branch —
  something in doubt, so a locate call and a gate; a correction, so both checks
  again — writes exactly the step families `STEPS` declares, in that order.
- **The second model call is absent, not empty**, on a ticket with nothing in
  doubt. A clean ticket makes exactly one model call, and that is asserted
  rather than hoped for.
- **A department whose page family is absent is skipped, and the job is not.**
  The governed page's own sentence, and the difference between a skip and a
  zero: a zero would read as "no work" where a skip reads as "we were not
  told".
- **Out-of-band hours are a field verdict, not a second gate.** They arrive in
  the same review as the low-confidence fields, beside the grid they came from.
- **A job with no shipping list is a count, not a Task.** There is nothing for
  a supervisor to decide about a document that has not been filed.
- **`JobTask` rows are created here and nowhere else**, through one guarded
  Action, committed under the desk of the Task where somebody answered.

The world's helpers are shared with `test_plant_eligibility` rather than
copied: both files land the same plant, and two copies of a fixture is two
chances for the flow under test to be judged against different worlds.
"""

import pytest

from app.gateway import ACTIONS
from app.graph import object_id
from app.models import AssetUpdate
from app.ontology import names as onto
from app.tasks.department_scheduling.parameters import POLICY_ASSET_ID
from app.tasks.plant_eligibility.domain import JOB_ELIGIBLE_EVENT
from app.tasks.shipping_list_extraction import domain
from app.tasks.shipping_list_extraction.v1 import flow as v1
from app.tasks.shipping_list_extraction.readers import fixture_page
from tests import world as test_world
from tests.test_plant_eligibility import (
    SOURCE,
    _Library,
    business_day,
    land_job,
    land_ticket,
)

SUPERVISOR = "chris.robson@wickbuildings.com"


@pytest.fixture()
def world():
    return test_world.fresh()


def start(world, number: str = "250553", case: str = "", **job):
    """One eligible job, its ticket, and the run the fact starts.

    Through the Dispatcher, so the Trigger table and the causation ordering are
    exercised rather than assumed — and so this file tests the flow the way the
    plant window reaches it.
    """
    record = land_job(
        world, number,
        load_date=job.pop("load_date", business_day(world, 6).isoformat()),
        **job,
    )
    world.extraction.fetcher = _Library(land_ticket(world, number, case))
    dispatch = world.dispatcher.emit(
        JOB_ELIGIBLE_EVENT,
        {
            "job_key": record.natural_key, "job_number": number,
            "plant_site": "Mazo", "source": SOURCE,
        },
        actor="system:test", case_ref=record.natural_key,
    )
    return dispatch.one()


def _families(run) -> set[str]:
    """The step families a journal holds — loop suffixes trimmed."""
    out = set()
    for step in run.steps:
        head, _, tail = step.step_id.rpartition("_")
        out.add(head if head and tail.isdigit() else step.step_id)
    return out


def _demand(world) -> dict:
    return {
        row.props["department"]: row.props
        for row in world.graph.find(onto.JOB_TASK)
    }


def _open_task(world):
    return next(
        task for task in world.tasks.list()
        if task.flow_id == domain.FLOW_ID and task.status == "open"
    )


# ---- the map is the run -----------------------------------------------------


def test_the_declared_steps_are_exactly_the_steps_a_full_run_executes(world):
    """A case built to reach every branch, and nothing left over either way.

    A smudged linear-footage figure puts a field in doubt, so `m2` is asked for
    a box and `k1` asks a supervisor; the supervisor corrects the value, so
    `x3` applies it and `c1`, `c2` and `x2` run again over the corrected
    ticket; then the write and the fact.
    """
    run = start(world, case="unsure=c_steel_fab.total_linear_feet")
    assert run.status == "suspended"

    task = world.tasks.get(run.open_task_id)
    world.extraction.resume_for_task(world.tasks.decide(
        task.id, "corrected", SUPERVISOR,
        note="Read the footage off the page again.",
        payload={"fields": {"c_steel_fab.total_linear_feet": "12000"}},
    ))
    run = world.engine.get(run.id)

    assert run.status == "succeeded"
    assert _families(run) == {step.id for step in v1.STEPS}
    # And the order, because a map is a sequence and not a set: the checks sit
    # between the extraction and the question, and the gate sits between the
    # question and the write.
    assert [step.step_id for step in run.steps] == [
        "t1", "x0", "x1", "s1", "m1", "c1", "c2", "x2", "m2",
        "k1_1", "x3_1", "c1_1", "c2_1", "x2_1", "a1", "e1",
    ]


def test_x0_is_declared_and_holds_every_governed_value_the_run_reads(world):
    """One read, four assets, and every revision on the step."""
    run = start(world)
    step = run.step("x0")

    assert step is not None and step.name == "read-knowledge"
    reads = v1.GovernedReads.model_validate(step.output)
    assert reads.plant.thresholds.min_field_confidence == 0.70
    assert reads.plant.thresholds.grid_line_total_tolerance_pct == 1.0
    assert reads.plant.band_for("steel-fab").high == 25.0
    assert len(reads.labor.truss.rows) == 32
    assert reads.prompt.found_asset
    for ref in (
        f"knowledge:{POLICY_ASSET_ID}@rev1",
        "knowledge:labor-hour-formulas@rev1",
        "knowledge:truss-standards@rev1",
        f"knowledge:{domain.PROMPT_ASSET_ID}@rev1",
    ):
        assert ref in step.refs and ref in run.refs
    # The sections this flow never consults are **not** in the read-set: the
    # step's output is a claim about what the run consulted.
    assert not reads.plant.capacity.rows and not reads.plant.incentives


# ---- one model call, and only one, on a clean ticket ------------------------


def test_a_clean_ticket_makes_one_model_call_and_no_second_one(world):
    """`m2` is **missing** from the journal, not present and empty."""
    run = start(world)

    assert run.status == "succeeded"
    assert run.step("m2") is None
    assert run.model_calls == 1
    assert "no boxes needed, so no second call is made" in run.step("x2").summary
    assert "m2" not in _families(run)


def test_a_disputed_field_gets_a_box_and_a_computed_figure_never_does(world):
    """The locate call is handed only paths there is ink for.

    A computed figure is printed nowhere on the page, so asking a model to find
    one would be asking it to invent a location — and the governed locate
    prompt's own rule is to omit a path it cannot find.
    """
    run = start(
        world,
        case="steel_lf=60000 unsure=c_steel_fab.total_linear_feet",
    )
    located = run.step("m2")

    assert located is not None
    verdicts = v1.VerdictSet.model_validate(run.step("x2").output)
    assert "hours.steel-fab" in verdicts.in_doubt
    assert "hours.steel-fab" not in verdicts.locatable
    assert all(region["field"] != "hours.steel-fab"
               for region in located.output["regions"])
    # The boxes reach a person on the Task rather than in `x2`'s own output:
    # the verdicts are computed before the locate call and the regions are
    # attached to the copy that travels, which keeps the step's record what the
    # step actually decided.
    task = world.tasks.get(run.open_task_id)
    boxed = {
        field["path"] for field in task.proposal.fields if field.get("region")
    }
    assert "c_steel_fab.total_linear_feet" in boxed
    assert "hours.steel-fab" not in boxed


# ---- the two checks ---------------------------------------------------------


def test_a_grid_that_does_not_add_up_to_its_own_total_is_named(world):
    """Components' lines against `total_quantity`, inside the governed tolerance."""
    run = start(world, case="components_total=40 components_rows=60")

    findings = run.step("c1").output
    assert [row["path"] for row in findings] == ["b_components.total_quantity"]
    assert "add up to 60 item(s)" in findings[0]["reason"]
    assert "governed tolerance of 1%" in findings[0]["reason"]
    assert run.status == "suspended"


def test_the_colour_grid_is_checked_against_the_printed_linear_footage(world):
    """Its own tolerance, because the steel rate multiplies exactly that number.

    Two colours that add up to the printed total agree; one misread row 500
    feet short does not, at 4.2% against a governed 1%. And a 50-foot gap —
    0.4% — is inside the tolerance, which is what makes the tolerance a
    governed decision rather than a coincidence.
    """
    clean = start(world, case="steel_lf=12000")
    assert not clean.step("c1").output

    inside = start(world, "260418", case="steel_lf=12000 steel_grid_gap=50")
    assert not inside.step("c1").output

    outside = start(world, "260435", case="steel_lf=12000 steel_grid_gap=500")
    findings = {row["path"]: row for row in outside.step("c1").output}
    assert "c_steel_fab.total_linear_feet" in findings
    assert "the steel rate multiplies exactly that number" in (
        findings["c_steel_fab.total_linear_feet"]["reason"]
    )
    assert outside.status == "suspended"

    # Tighten the governed tolerance and the 50-foot gap becomes a question.
    world.knowledge.update(POLICY_ASSET_ID, AssetUpdate(
        content=world.knowledge.get(POLICY_ASSET_ID).content.replace(
            "- linear footage tolerance: 1.0%",
            "- linear footage tolerance: 0.1%",
        ),
        updated_by="plant-operations",
    ))
    tightened = start(world, "260442", case="steel_lf=12000 steel_grid_gap=50")
    assert [row["path"] for row in tightened.step("c1").output] == [
        "c_steel_fab.total_linear_feet",
    ]


def test_a_required_field_on_a_present_page_family_is_asked_about(world):
    """The governed required-field rule, on the fields a formula multiplies."""
    run = start(world, case="missing=c_steel_fab.total_linear_feet")

    findings = {row["path"]: row for row in run.step("c1").output}
    assert findings["c_steel_fab.total_linear_feet"]["status"] == "missing"
    assert "governed required-field rule" in (
        findings["c_steel_fab.total_linear_feet"]["reason"]
    )
    assert run.status == "suspended"


def test_out_of_band_hours_are_a_field_verdict_and_not_a_second_gate(world):
    """The design's fourth exception, in the same review as the smudges.

    One Task, not two: the supervisor confirms the reading and the hours in one
    sitting, beside the grid the figure was computed from.
    """
    run = start(world, case="steel_lf=60000")

    hours = {
        row["department"]: row for row in run.step("c2").output["demand"]
    }
    assert hours["steel-fab"]["out_of_band"] is True
    assert hours["truss-cut"]["out_of_band"] is False
    findings = {row["path"]: row for row in run.step("c2").output["findings"]}
    assert findings["hours.steel-fab"]["status"] == "out_of_band"
    assert "outside its governed expected band of 0.1 to 25 h" in (
        findings["hours.steel-fab"]["reason"]
    )

    assert run.status == "suspended"
    tasks = [task for task in world.tasks.list() if task.flow_id == domain.FLOW_ID]
    assert len(tasks) == 1
    assert tasks[0].queue == "plant-supervisor" and tasks[0].priority == "high"
    assert "Out of band: steel-fab" in tasks[0].summary


def test_a_correction_re_runs_both_checks_over_the_corrected_reading(world):
    """Not an override: the checks and the governed formulas run again.

    A supervisor who corrects a linear-footage figure is handed back a
    different ticket, and the hours that go into the plant's day are the ones
    the formulas compute from the corrected grid.
    """
    run = start(world, case="steel_lf=60000")
    before = {
        row["department"]: row["labor_hours"]
        for row in run.step("c2").output["demand"]
    }
    # 60000 lf × 0.00073 + 0.75 setup + 0.25 for the second colour + two
    # medium cribs at 0.5 — every figure off the governed page.
    assert before["steel-fab"] == pytest.approx(45.8)

    task = _open_task(world)
    world.extraction.resume_for_task(world.tasks.decide(
        task.id, "corrected", SUPERVISOR,
        # The colour rows too: correcting a printed total and leaving the grid
        # under it alone is a *different* disagreement, and the check would
        # rightly ask about that one next.
        payload={"fields": {
            "c_steel_fab.total_linear_feet": "12000",
            "c_steel_fab.colours[0].linear_feet": "6000",
            "c_steel_fab.colours[1].linear_feet": "6000",
        }},
    ))
    run = world.engine.get(run.id)

    assert run.status == "succeeded"
    assert "corrected c_steel_fab.total_linear_feet" in run.step("x3_1").summary
    after = {
        row["department"]: row for row in run.step("c2_1").output["demand"]
    }
    assert after["steel-fab"]["labor_hours"] == pytest.approx(10.76)
    assert after["steel-fab"]["out_of_band"] is False
    assert _demand(world)["steel-fab"]["labor_hours"] == pytest.approx(10.76)
    assert _demand(world)["steel-fab"]["out_of_band"] == "no"


def test_a_correction_keyed_on_a_field_this_ticket_has_no_line_for_is_refused(world):
    """A correction about a different document does not become a demand row."""
    run = start(world, case="steel_lf=60000")
    task = _open_task(world)
    world.extraction.resume_for_task(world.tasks.decide(
        task.id, "corrected", SUPERVISOR,
        payload={"fields": {
            "c_steel_fab.total_linear_feet": "12000",
            "a1_trusses.rows[9].span_ft": "48",
            "hours.steel-fab": "3",
        }},
    ))
    run = world.engine.get(run.id)
    patched = run.step("x3_1").output

    assert patched["changed"] == ["c_steel_fab.total_linear_feet"]
    assert set(patched["refused"]) == {"a1_trusses.rows[9].span_ft", "hours.steel-fab"}


def test_the_review_stops_asking_rather_than_looping(world):
    """`MAX_REVIEW_ROUNDS`, and a ticket written as read with its verdicts on it."""
    run = start(world, case="steel_lf=60000")
    for _ in range(domain.MAX_REVIEW_ROUNDS):
        task = _open_task(world)
        world.extraction.resume_for_task(world.tasks.decide(
            task.id, "corrected", SUPERVISOR,
            payload={"fields": {"c_steel_fab.total_linear_feet": "70000"}},
        ))
    run = world.engine.get(run.id)

    assert run.status == "succeeded"
    assert run.outputs["state"] == "flagged"
    assert "still in doubt after 2 review round(s)" in run.outputs["reason"]
    # Written anyway, with the verdict carried on the row: a figure a person
    # accepted still reads as unusual six months later.
    assert _demand(world)["steel-fab"]["out_of_band"] == "yes"


def test_a_rejected_reading_writes_nothing(world):
    """No demand rows, so the scheduling window does not schedule the job."""
    run = start(world, case="steel_lf=60000")
    task = _open_task(world)
    world.extraction.resume_for_task(world.tasks.decide(
        task.id, "rejected", SUPERVISOR, note="This is last year's ticket.",
    ))
    run = world.engine.get(run.id)

    assert run.status == "succeeded"
    assert run.outputs["state"] == "rejected"
    assert not world.graph.find(onto.JOB_TASK)
    assert "a1" not in _families(run)


# ---- a department is skipped; a job is not ---------------------------------


def test_a_department_whose_page_family_is_absent_is_skipped_and_the_job_is_not(world):
    """*"A building with no trusses is a building with no trusses."*

    Page E is absent, so Trim Fabrication gets no row — and Hardware & Trim
    Packaging still does, because it is fed by three page families and D and F
    are on the ticket.
    """
    run = start(world, case="pages=A1,B,C,D,F,LL")

    assert run.status == "succeeded"
    written = set(run.outputs["departments_written"])
    assert "trim-fab" not in written
    assert "hardware-trim-pack" in written
    assert run.outputs["departments_skipped"] == ["trim-fab"]
    assert "trim-fab" not in _demand(world)
    assert len(_demand(world)) == 9


def test_a_ticket_with_no_pages_at_all_writes_nothing_and_still_succeeds(world):
    """Every department skipped is still not a failed job."""
    run = start(world, case="no_pages")

    assert run.status == "succeeded"
    assert run.outputs["departments_written"] == []
    assert not world.graph.find(onto.JOB_TASK)
    assert "no demand row was written" in run.step("a1").summary


# ---- the documents ---------------------------------------------------------


def test_a_job_with_no_shipping_list_is_skipped_with_a_count_not_a_task(world):
    """The design's first business exception, and the governed page's answer."""
    record = land_job(
        world, "260435", load_date=business_day(world, 6).isoformat(),
    )
    world.extraction.fetcher = _Library({})
    run = world.dispatcher.emit(
        "JobEligible",
        {"job_key": record.natural_key, "job_number": "260435"},
        actor="system:test", case_ref=record.natural_key,
    ).one()

    assert run.status == "succeeded"
    assert run.outputs["skipped"] is True
    assert "skip the job and count it" in run.outputs["reason"]
    assert not world.tasks.list()
    assert not world.graph.find(onto.JOB_TASK)


def test_the_floors_copy_is_the_one_read_and_not_the_offices(world):
    """The same filename pattern matches both trees; the governed root decides.

    `Job Documents/<job>` is the office's copy of what the portal scanned and
    `PlantCutSheets/<site>/<job>` is the floor's — and only one of them is what
    a department's hours are computed from.
    """
    from app.graph import Claim

    number = "250553"
    office = world.graph.upsert(onto.FOLDER, f"dms-library:Job Documents/{number}", [
        Claim(field="path", value=f"Job Documents/{number}", source="dms:dms"),
        Claim(field="source", value="dms-library", source="dms:dms"),
    ], label=number)
    scanned = world.graph.upsert(onto.DOCUMENT, f"dms-library:WD-office-{number}", [
        Claim(field="filename", value=f"{number} Shipping List.pdf",
              source="dms:dms"),
        Claim(field="storage_ref", value="https://dms.example/office/content",
              source="dms:dms"),
    ], label="office copy")
    world.graph.link("filed_under", scanned.id, office.id)

    run = start(world, number)

    assert run.step("x1").output["shipping_list"] == 1
    assert run.step("x1").output["root"] == f"PlantCutSheets/Mazo/{number}"
    assert run.status == "succeeded"


def test_the_bytes_are_stored_under_their_hash_and_the_row_says_it_was_read(world):
    """`ap-intake`'s two claims: what was read, and which revision it came from."""
    run = start(world)
    fetched = run.step("s1")

    assert fetched.output["read"] == 1
    document = world.graph.get(object_id(onto.DOCUMENT, "dms-library:WD-250553"))
    assert document.props["sha256"]
    assert document.props["read_version"] == 1
    stored = world.documents.get(fetched.output["stored"][0]["document_id"])
    assert stored is not None and stored.sha256 == document.props["sha256"]
    # One guarded observation for the fetch, and the ledger says whose.
    entry = next(
        row for row in world.gateway.ledger.list()
        if row.action == "record-document-read" and row.run_id == run.id
    )
    assert entry.regime == "observation" and entry.outcome == "committed"


# ---- the one write ---------------------------------------------------------


def test_job_task_rows_are_created_here_and_carry_their_own_basis(world):
    """Each row names the formula and the asset revision that produced it."""
    run = start(world)
    rows = _demand(world)

    assert set(run.outputs["departments_written"]) == set(rows)
    truss = rows["truss-cut"]
    assert truss["labor_hours"] == pytest.approx(5.7)
    assert "truss cut" in truss["hours_basis"]
    assert "knowledge:truss-standards@rev1" in truss["hours_basis"]
    assert truss["source_page"] == "A1"
    assert truss["confidence"] == pytest.approx(0.95)
    assert truss["out_of_band"] == "no"
    assert "page A1" in truss["demand_summary"]
    # A part of the job, reachable from it.
    job = world.graph.get(object_id(onto.JOB, f"{SOURCE}:250553"))
    linked = {
        record.props["department"]
        for _link, record in world.graph.neighbors(job.id, "has_job_task", "out")
    }
    assert linked == set(rows)
    # `upstream_state` is deliberately unclaimed: the scheduling flow reads a
    # job's Done flags live, and a snapshot here would be a second answer.
    assert "upstream_state" not in truss


def test_the_demand_is_committed_through_the_declared_action(world):
    """One guarded write, filed as a decision, under the plant desk."""
    assert ACTIONS["apply-job-demand"].regime == "decision"
    assert ACTIONS["apply-job-demand"].target == "production-records"
    assert ACTIONS["apply-job-demand"].queue == "plant-supervisor"
    assert ACTIONS["apply-job-demand"].queue_from_task is True

    run = start(world)
    entry = next(
        row for row in world.gateway.ledger.list()
        if row.action == "apply-job-demand"
    )
    assert entry.outcome == "committed"
    assert entry.run_id == run.id and entry.step_id == "a1"
    assert entry.principal_uid == f"flow:{domain.FLOW_ID}"
    # Nothing was decided, so the standing desk commits it.
    assert not entry.task_id and not entry.decided_by
    assert len(entry.writes) >= len(_demand(world))


def test_a_reviewed_ticket_commits_under_the_desk_that_answered(world):
    """The queue that raised the work and the queue that commits it are one."""
    run = start(world, case="steel_lf=60000")
    task = _open_task(world)
    world.extraction.resume_for_task(world.tasks.decide(
        task.id, "approved", SUPERVISOR, note="Sixty thousand feet is right.",
    ))
    run = world.engine.get(run.id)

    entry = next(
        row for row in world.gateway.ledger.list()
        if row.action == "apply-job-demand"
    )
    assert entry.task_id == task.id
    assert entry.decided_by == SUPERVISOR
    assert run.outputs["state"] == "extracted"
    assert "written on review despite" in run.outputs["reason"]
    assert _demand(world)["steel-fab"]["out_of_band"] == "yes"


def test_the_review_task_carries_the_fields_the_hours_and_the_page(world):
    """What a supervisor is handed: three numbers, not a seven-page PDF."""
    run = start(world, case="unsure=c_steel_fab.total_linear_feet")
    task = world.tasks.get(run.open_task_id)

    assert task.kind == "decision" and task.queue == "plant-supervisor"
    assert task.proposal.action == "apply-job-demand"
    doubted = {field["path"] for field in task.proposal.fields}
    assert doubted == {"c_steel_fab.total_linear_feet"}
    [field] = task.proposal.fields
    assert field["status"] == "low_confidence"
    assert field["label"] == "C · steel fab — total linear feet"
    assert field["region"]["field"] == "c_steel_fab.total_linear_feet"
    assert "below the governed floor of 0.70" in field["reason"]
    evidence = {row.label: row.value for row in task.evidence}
    assert "truss-cut 5.7 h" in evidence["Departments with demand"]
    assert "governed approval table" in evidence["Routed to this desk because"]
    document_id = run.step("s1").output["stored"][0]["document_id"]
    assert f"document:{document_id}" in task.refs
    assert f"knowledge:{POLICY_ASSET_ID}@rev1" in task.refs


def test_the_extraction_screen_is_declared_for_this_version():
    """Lane 6 builds the widget; the manifest degrades until it does."""
    assert v1.UI.otherwise.screen_id == "plant-schedule/extraction-review"
    assert v1.UI.screen_for("decision").screen_id == (
        "plant-schedule/extraction-review"
    )


# ---- the document itself ---------------------------------------------------


def test_a_page_flag_that_disagrees_with_the_block_under_it_is_a_finding(world):
    """"Present but empty" and "absent" are different answers.

    A false absence silently removes a department's work from the plant's day,
    which is the one failure here that looks exactly like a quiet week.
    """
    world.extraction.fetcher = _Library({"/250553/": fixture_page("job=250553")})
    record = land_job(
        world, "250553", load_date=business_day(world, 6).isoformat(),
    )
    land_ticket(world, "250553")
    # A reading whose `pages` map claims a family the blocks do not carry.
    reading = domain.ShippingListExtraction(
        model="stub", confidence=0.9, pages={"C": True},
    )
    findings = v1.check_grids(
        reading.grid(),
        v1.PlantParameters.model_validate(
            _reads(world).plant.model_dump(mode="json")
        ),
    )
    assert any(row.path == "pages.C" for row in findings)
    assert record is not None


def test_a_document_that_reads_badly_overall_goes_to_review(world):
    """The whole-document score is its own question, and its own floor."""
    run = start(world, case="confidence=0.5")

    findings = {row["path"]: row for row in run.step("c1").output}
    assert findings["confidence"]["status"] == "low_confidence"
    assert "below the governed floor of 0.75" in findings["confidence"]["reason"]
    assert run.status == "suspended"


def _reads(world) -> v1.GovernedReads:
    return v1.GovernedReads(
        plant=world.extraction.read_plant(), labor=world.extraction.read_labor(),
    )
