"""The plant window: which jobs are in play, and why each of the rest is not.

What this file defends, claim by claim:

- **The declared map is the executed map.** A run that reaches every step
  writes exactly the step families `STEPS` declares — asserted as a set *and*
  as an ordered list, because a map is a sequence and not a set.
- **Every exclusion has its own named reason.** A weekend load, a held job, a
  past load date, an out-of-set week position and an unreleased job are five
  different answers to "why was this not scheduled", and the run counts them
  separately.
- **Nothing eligible raises nothing.** The governed page calls an empty
  eligible set informational; what raises an alarm is an empty job *book*,
  which is a feed that stopped, and that is one aggregate Task on the
  operations desk.
- **The window is bounded and says what it deferred.** The governed
  jobs-per-window figure trims the batch; the remainder is reported, not
  dropped.
- **The order of the last two steps is load-bearing.** `e1` runs every
  extraction, `e2` opens a department-day for each department those
  extractions gave demand rows to — and a job whose extraction suspended opens
  nothing, because it has no rows yet.
- **`DepartmentDayOpened` starts the flow that answers it.** Every day the
  window opens becomes a scheduling run, and each of those suspends on its own
  approval Task — which is the whole plant spine running end to end from one
  timer.
"""

from datetime import date, timedelta

import pytest

from app.dispatch import TRIGGERS
from app.graph import Claim
from app.models import AssetUpdate
from app.ontology import names as onto
from app.tasks.department_scheduling.parameters import (
    POLICY_ASSET_ID,
    read_plant_parameters,
)
from app.tasks.plant_eligibility import domain
from app.tasks.plant_eligibility.v1 import flow as v1
from app.tasks.shipping_list_extraction.readers import fixture_page
from tests import world as test_world

SOURCE = "erp-jobs"
DMS = "dms-library"
ERP = "erp:erp-jobs"


@pytest.fixture()
def world():
    return test_world.fresh()


class _Library:
    """A document system that serves content, standing in for the connector."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.calls: list[str] = []

    def fetch_content(self, url: str) -> tuple[bytes, str]:
        self.calls.append(url)
        for key, content in self.files.items():
            if key in url:
                return content, "application/pdf"
        raise RuntimeError(f"404 for {url}")


def land_job(
    world,
    number: str,
    *,
    load_date: str,
    week: float | None = 1,
    released: bool = True,
    hold: str = "",
    site: str = "Mazo",
):
    """One `Job` as `job-sync` leaves it — the words already translated.

    Written straight into the graph rather than through the feed, deliberately:
    what this flow is judged on is the *facts* the jobs feed lands, and the
    translation of `"Yes"` into `released` is that feed's own test.
    """
    claims = [
        Claim(field="job_number", value=number, source=ERP),
        Claim(field="source", value=SOURCE, source=ERP),
        Claim(field="load_date", value=load_date, source=ERP),
        Claim(field="plant_site", value=site, source=ERP),
        Claim(field="customer_name", value="Evermore Barns LLC", source=ERP),
        Claim(field="secondary_dimensions", value="12 x 24", source=ERP),
        Claim(field="release_state",
              value="released" if released else "not released", source=ERP),
    ]
    if week is not None:
        claims.append(Claim(field="week_position", value=week, source=ERP))
    if hold:
        claims.append(Claim(field="hold_reason", value=hold, source=ERP))
    return world.graph.upsert(
        onto.JOB, f"{SOURCE}:{number}", claims, label=f"{number} shed",
    )


def land_ticket(world, number: str, case: str = "", *, site: str = "Mazo") -> dict:
    """The job's shipping list, filed where the plant floor files one."""
    path = f"PlantCutSheets/{site}/{number}"
    folder = world.graph.upsert(onto.FOLDER, f"{DMS}:{path}", [
        Claim(field="path", value=path, source="dms:dms-library"),
        Claim(field="name", value=number, source="dms:dms-library"),
        Claim(field="source", value=DMS, source="dms:dms-library"),
    ], label=number)
    filename = f"{number} Shipping List A1-LL.pdf"
    document = world.graph.upsert(onto.DOCUMENT, f"{DMS}:WD-{number}", [
        Claim(field="filename", value=filename, source="dms:dms-library"),
        Claim(field="storage_ref",
              value=f"https://dms.example/{number}/content",
              source="dms:dms-library"),
        Claim(field="latest_version", value=1, source="dms:dms-library"),
    ], label=filename)
    world.graph.link("filed_under", document.id, folder.id)
    return {f"/{number}/": fixture_page(f"job={number} {case}")}


def sweep(world, as_of: date | None = None):
    """Close a window through the Dispatcher, and hand back the run."""
    day = as_of or date.today()
    return world.plant.sweep(actor="system:test", as_of=day.isoformat()).one()


def business_day(world, offset: int) -> date:
    """A working day roughly `offset` days out, per the governed calendar."""
    params = read_plant_parameters(world.knowledge)
    day = date.today() + timedelta(days=offset)
    while not params.calendar.is_business_day(day):
        day += timedelta(days=1)
    return day


def weekend(world) -> date:
    """The next day the governed calendar says the plant is shut."""
    params = read_plant_parameters(world.knowledge)
    day = date.today() + timedelta(days=1)
    while params.calendar.is_business_day(day):
        day += timedelta(days=1)
    return day


def _families(run) -> set[str]:
    """The step families a journal holds — loop suffixes trimmed."""
    out = set()
    for step in run.steps:
        head, _, tail = step.step_id.rpartition("_")
        out.add(head if head and tail.isdigit() else step.step_id)
    return out


def _exclusions(run) -> dict:
    return run.outputs["exclusions"]


# ---- the map is the run -----------------------------------------------------


def test_the_declared_steps_are_exactly_the_steps_a_full_run_executes(world):
    """The claim the version exists to make, over a run that reaches every step.

    The case is built so nothing is skipped: a job that clears every clause, a
    ticket the extraction can read, and therefore demand rows for `e2` to open
    days out of. What is left over in either direction is the bug — a step in
    the journal that no map declares, or a map promising a step the engine
    never runs.
    """
    land_job(world, "250553", load_date=business_day(world, 6).isoformat())
    world.extraction.fetcher = _Library(land_ticket(world, "250553"))

    run = sweep(world)

    assert run.status == "succeeded"
    assert _families(run) == {step.id for step in v1.STEPS}
    assert [step.step_id for step in run.steps] == [
        "t1", "x1", "x2", "r1", "k1", "e1", "e2",
    ]
    assert run.outputs["eligible"] == 1
    assert run.outputs["department_days"] == 10


def test_the_governed_parameters_are_read_once_and_named_at_their_revision(world):
    """`x1` is the read-set: one step, one revision, and the run cites it."""
    land_job(world, "250553", load_date=business_day(world, 6).isoformat())
    run = sweep(world)
    step = run.step("x1")

    assert step is not None and step.name == "read-plant-parameters"
    assert f"knowledge:{POLICY_ASSET_ID}@rev1" in step.refs
    assert f"knowledge:{POLICY_ASSET_ID}@rev1" in run.refs
    assert "week position in 1, 2, 3" in step.summary
    assert "release_state = `released`" in step.summary
    # And the clauses came off the page rather than out of this flow: the
    # release word is the one the governed vocabulary table produces.
    params = v1.PlantParameters.model_validate(step.output)
    assert params.eligibility.released_state == "released"
    assert params.eligibility.excluding_hold == "DNF"
    assert params.eligibility.jobs_per_window == 15


def test_editing_the_eligibility_clauses_changes_what_is_eligible(world):
    """The whole point of the page: a plant that widens a clause edits a line."""
    land_job(world, "260435", load_date=business_day(world, 6).isoformat(), week=3)
    assert sweep(world).outputs["eligible"] == 1

    world.knowledge.update(POLICY_ASSET_ID, AssetUpdate(
        content=world.knowledge.get(POLICY_ASSET_ID).content.replace(
            "- eligible week positions: 1, 2, 3",
            "- eligible week positions: 1, 2",
        ),
        updated_by="plant-operations",
    ))
    second = sweep(world)
    assert second.outputs["eligible"] == 0
    assert _exclusions(second) == {"week-position-out-of-set": 1}


# ---- every exclusion is a clause, not an opinion ----------------------------


def test_each_kind_of_ineligible_job_is_excluded_for_its_own_named_reason(world):
    """Five jobs, five clauses, five different sentences.

    The counts are keyed on the clause rather than on a generic "excluded",
    because *"why was 260118 not scheduled"* is the question this flow exists
    to answer and "it did not qualify" is not an answer.
    """
    land_job(world, "250553", load_date=business_day(world, 6).isoformat())
    land_job(world, "260001", load_date=weekend(world).isoformat())
    land_job(world, "260002", load_date=business_day(world, 6).isoformat(),
             hold="DNF")
    land_job(world, "260003", load_date=(date.today() - timedelta(days=3)).isoformat())
    land_job(world, "260004", load_date=business_day(world, 6).isoformat(), week=0)
    land_job(world, "260005", load_date=business_day(world, 6).isoformat(),
             released=False)
    land_job(world, "260006", load_date=business_day(world, 6).isoformat(),
             week=None)
    world.extraction.fetcher = _Library(land_ticket(world, "250553"))

    run = sweep(world)

    assert run.outputs["eligible"] == 1
    assert _exclusions(run) == {
        "held": 1,
        "load-date-past": 1,
        "no-week-position": 1,
        "not-a-business-day": 1,
        "not-released": 1,
        "week-position-out-of-set": 1,
    }
    reasons = {
        verdict["job_number"]: verdict["reason"] for verdict in run.step("r1").output
    }
    assert "business days are Monday" in reasons["260001"]
    assert "the job is held: `DNF`" in reasons["260002"]
    assert "is before" in reasons["260003"]
    assert "week position 0" in reasons["260004"]
    assert "requires `released`" in reasons["260005"]
    assert "does not place this job in a production week" in reasons["260006"]
    assert "clears every governed eligibility clause" in reasons["250553"]


def test_a_holiday_load_is_excluded_even_though_the_screen_shows_it(world):
    """*"Weekend and holiday loads are excluded even when the screen shows them."*

    The plant operations screen is a list of load dates and does not know the
    plant is shut; the governed calendar is what does.
    """
    params = read_plant_parameters(world.knowledge)
    holiday = next(
        (day for day in params.calendar.holidays
         if date.fromisoformat(day) > date.today()),
        "",
    )
    assert holiday, "the governed calendar carries no future holiday to test with"
    land_job(world, "260118", load_date=holiday)

    run = sweep(world)
    assert _exclusions(run) == {"not-a-business-day": 1}
    assert "plant holiday" in run.step("r1").output[0]["reason"]


def test_a_released_job_that_is_also_held_is_held(world):
    """Two independent flags in the source, so two clauses — and the hold wins.

    Out-to-yard and do-not-fab are separate columns and a job can carry both:
    something released and then put on hold is held, and reading the release
    alone would put it on a saw.
    """
    land_job(world, "260442", load_date=business_day(world, 6).isoformat(),
             released=True, hold="DNF")
    run = sweep(world)
    assert _exclusions(run) == {"held": 1}


def test_a_hold_the_page_does_not_name_is_still_a_hold(world):
    """A hold nobody recognizes is not a job to start cutting."""
    land_job(world, "260443", load_date=business_day(world, 6).isoformat(),
             hold="CUSTOMER FREEZE")
    run = sweep(world)
    assert _exclusions(run) == {"held": 1}
    assert "a hold the page does not name" in run.step("r1").output[0]["reason"]


# ---- what raises a Task, and what does not ----------------------------------


def test_an_empty_eligible_set_raises_nothing(world):
    """*"Nothing eligible is not a fault."* A slow week is informational."""
    land_job(world, "260002", load_date=business_day(world, 6).isoformat(),
             hold="DNF")

    run = sweep(world)

    assert run.status == "succeeded"
    assert run.outputs["eligible"] == 0
    assert not world.tasks.list()
    assert "informational" in run.step("e1").summary


def test_an_empty_job_book_raises_one_aggregate_task_to_operations(world):
    """An empty *book* is a feed that stopped, which is nobody's business call."""
    run = sweep(world)

    assert run.status == "succeeded"
    [task] = world.tasks.list()
    assert task.queue == "platform-operations"
    assert task.kind == "exception" and task.priority == "high"
    assert task.flow_id == domain.FLOW_ID
    assert "no jobs at all" in task.title
    assert "empty *eligible* set would be a slow week" in task.summary
    # Idempotent on the run: re-entering a window must not open a second copy.
    world.engine.resume(run.id)
    assert len(world.tasks.list()) == 1


# ---- the bound, and what it left behind -------------------------------------


def test_the_window_bound_is_governed_and_the_remainder_is_reported(world):
    """A backlog past the bound is the next window's work, not a loss."""
    world.knowledge.update(POLICY_ASSET_ID, AssetUpdate(
        content=world.knowledge.get(POLICY_ASSET_ID).content.replace(
            "- jobs per eligibility window: 15",
            "- jobs per eligibility window: 2",
        ),
        updated_by="plant-operations",
    ))
    files: dict[str, bytes] = {}
    for index in range(4):
        number = f"26010{index}"
        land_job(world, number,
                 load_date=business_day(world, 6 + index).isoformat())
        files.update(land_ticket(world, number))
    world.extraction.fetcher = _Library(files)

    run = sweep(world)

    assert run.outputs["eligible"] == 4
    assert run.outputs["bound"] == 2
    assert run.outputs["taken"] == 2 and run.outputs["deferred"] == 2
    assert len(world.engine.list(flow_id="shipping-list-extraction")) == 2
    assert "2 eligible job(s) left for the next window" in run.step("e1").summary


def test_candidates_are_taken_in_the_governed_order(world):
    """`- sort candidates by: load date ascending` — so the bound trims the far end."""
    for offset, number in ((20, "260300"), (6, "260100"), (12, "260200")):
        land_job(world, number, load_date=business_day(world, offset).isoformat())

    run = sweep(world)
    assert [row["job_number"] for row in run.step("x2").output["candidates"]] == [
        "260100", "260200", "260300",
    ]


# ---- the seam: one window, N cases, then the days ---------------------------


def test_every_eligible_job_becomes_one_extraction_run_before_the_window_ends(world):
    """The named cost: the window is not acknowledged until the cases have run."""
    land_job(world, "250553", load_date=business_day(world, 6).isoformat())
    world.extraction.fetcher = _Library(land_ticket(world, "250553"))

    run = sweep(world)

    [extraction] = world.engine.list(flow_id="shipping-list-extraction")
    assert extraction.status == "succeeded"
    assert extraction.case_ref == f"{SOURCE}:250553"
    assert run.outputs["extractions_succeeded"] == 1
    # The fact was logged before the run it started existed.
    raised = next(
        event for event in world.dispatcher.events.list()
        if event.name == domain.JOB_ELIGIBLE_EVENT
    )
    assert raised.starts == [extraction.id]
    assert raised.triggers == ["job-eligible"]


def test_a_job_whose_extraction_suspends_opens_no_department_day(world):
    """No demand rows yet, so no day to fill — and the window says so.

    The dependency-not-met rule in the other direction: nothing sits warm
    waiting for the supervisor, the window finishes, and the next one re-raises
    what is still eligible.
    """
    land_job(world, "250553", load_date=business_day(world, 6).isoformat())
    world.extraction.fetcher = _Library(land_ticket(
        world, "250553", "unsure=c_steel_fab.total_linear_feet",
    ))

    run = sweep(world)

    assert run.status == "succeeded"
    assert run.outputs["extractions_suspended"] == 1
    assert run.outputs["department_days"] == 0
    assert "no department has demand rows" in run.step("e2").summary
    [task] = [t for t in world.tasks.list() if t.queue == "plant-supervisor"]
    assert task.status == "open"


def test_department_days_are_dated_by_the_governed_lead_time(world):
    """One day per (department, business day) the demand rows call for.

    The date is the governed lead time counted back in business days from the
    load date — and a department whose lead time had already run out opens on
    the first business day available and **says it was late**, because a
    department starting late is a fact about the plant's week rather than
    something to hide.
    """
    load = business_day(world, 30)
    land_job(world, "250553", load_date=load.isoformat())
    world.extraction.fetcher = _Library(land_ticket(world, "250553"))

    run = sweep(world)
    days = {row["department"]: row for row in run.outputs["days"]}

    assert set(days) == {
        "components", "hardware-trim-pack", "laminates", "loading",
        "stack-out", "steel-fab", "trim-fab", "truss-cut", "truss-fab",
        "verticals",
    }
    params = read_plant_parameters(world.knowledge)
    assert days["loading"]["schedule_date"] == (
        params.calendar.business_days_before(load, 1).isoformat()
    )
    assert days["truss-cut"]["lead_time_days"] == 21
    assert not days["loading"]["late"]
    # Laminates is the department the page conditions on treated stock, and the
    # ticket does not say which — so the nearest row dates it, out loud.
    assert "the ticket does not say which" in days["laminates"]["note"]


def test_the_days_are_emitted_after_the_extractions_and_start_the_fill(world):
    """`e2` after `e1`, and every day it opens is answered.

    The order is load-bearing: `e1` runs each extraction, so by the time `e2`
    asks which departments have work the demand rows exist. Each
    `DepartmentDayOpened` then matches the `department-day-opened` Trigger and
    starts a `department-scheduling` run, which fills the day and suspends on
    the supervisor's approval — the one place in the spine a person is asked.
    """
    land_job(world, "250553", load_date=business_day(world, 6).isoformat())
    world.extraction.fetcher = _Library(land_ticket(world, "250553"))

    run = sweep(world)
    ids = [step.step_id for step in run.steps]
    assert ids.index("e1") < ids.index("e2")

    opened = [
        event for event in world.dispatcher.events.list()
        if event.name == domain.DEPARTMENT_DAY_EVENT
    ]
    assert len(opened) == run.outputs["department_days"]
    assert all(
        event.triggers == ["department-day-opened"] and len(event.starts) == 1
        for event in opened
    )
    started = run.step("e2").output["runs"]
    assert len(started) == run.outputs["department_days"]
    assert "scheduling run(s) started" in run.step("e2").summary
    # And not one of them wrote a schedule nobody signed: every day with work
    # on it stopped at its own control point, and every day whose dependencies
    # were not met finished as deferred without asking anybody.
    outcomes = {
        run_id: world.engine.get(run_id) for run_id in started
    }
    assert all(
        record.status in ("suspended", "succeeded")
        for record in outcomes.values()
    )
    assert any(record.status == "suspended" for record in outcomes.values())
    assert all(
        record.outputs.get("state") == "deferred"
        for record in outcomes.values() if record.status == "succeeded"
    )


# ---- the closed table -------------------------------------------------------


def test_the_window_and_the_case_are_both_declared_triggers(world):
    """The co-landing rule, checked from the lane that owns both rows.

    `Dispatcher.emit` starts every matched Trigger without guarding a missing
    body, so a row landed ahead of its flow would not fail quietly — it would
    fail the next thing that emitted the fact.
    """
    assert TRIGGERS["plant-window-closed"].event == domain.PLANT_WINDOW_EVENT
    assert TRIGGERS["plant-window-closed"].starts == domain.FLOW_ID
    assert TRIGGERS["plant-window-closed"].wakes == "window"
    assert TRIGGERS["job-eligible"].event == domain.JOB_ELIGIBLE_EVENT
    assert TRIGGERS["job-eligible"].starts == "shipping-list-extraction"
    assert TRIGGERS["job-eligible"].wakes == "case"
    registered = set(world.engine.registered())
    assert {domain.FLOW_ID, "shipping-list-extraction"} <= registered


def test_the_sweep_writes_nothing(world):
    """No Action, no claim, no ledger row of its own — the verdict is the journal."""
    land_job(world, "260002", load_date=business_day(world, 6).isoformat(),
             hold="DNF")
    before = len(world.gateway.ledger.list())

    run = sweep(world)

    assert len(world.gateway.ledger.list()) == before
    assert all(not step.writes for step in run.steps)
