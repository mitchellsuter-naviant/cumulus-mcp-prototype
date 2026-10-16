"""A department's day: filled from the graph, signed by a person, then a file.

The central claim first, because everything else in this flow is in service of
it: **nothing leaves the graph before approval, and the day is exportable only
once approved.** The write happens *before* the question — the supervisor has
to have a record to open — so the day exists at `pending_approval` while the
Task is open, and `a2` is the instant it becomes exportable. The export's own
refusal is asserted in `test_schedule_export.py`; what this file defends is
everything on this side of it.

- **The declared map is the executed map.** A run that reaches every branch —
  a correction, so the fill and the write again, and then an approval — writes
  exactly the step families `STEPS` declares, in that order.
- **The goal max stops the fill**, the fudge is the latitude for the row that
  crosses it, and the absolute max plus the same fudge is the hard stop. Going
  past the absolute max by within the fudge is explicitly not an exception —
  the governed page's own sentence, asserted as a number.
- **The incentive crossing zero stops the two departments the page names**,
  and the figure is computed and stated as its own arithmetic so a supervisor
  can dispute it. Trim Fab's ceiling binds before its incentive ever crosses,
  which is a finding about the governed numbers and is pinned here.
- **A `placed_by: human` row survives a re-run untouched.** Structurally, not
  by an `if`: the claim is written under a `human:` source, which outranks
  `flow:` in the ontology's precedence.
- **A dependency not met ends `deferred` with its reason and raises nothing.**
  No Task, no `ScheduleDay`, no waiting — the next sweep re-raises the day.
- **The headcount is the `member_of` count**, and a stale one warns and
  proceeds rather than failing.
- **Every number is governed.** Editing the capacity table moves where the
  fill stops; editing the incentive figures moves it somewhere else again.
"""

from datetime import timedelta

import pytest

from app.dispatch import TRIGGERS
from app.gateway import ACTIONS
from app.graph import Claim, object_id
from app.models import AssetUpdate, utcnow
from app.ontology import names as onto
from app.tasks.department_scheduling import domain, formulas
from app.tasks.department_scheduling.parameters import (
    POLICY_ASSET_ID,
    read_plant_parameters,
)
from app.tasks.department_scheduling.v1 import flow as v1
from tests import world as test_world
from tests.test_plant_eligibility import business_day, land_job

SUPERVISOR = "chris.robson@wickbuildings.com"
HRIS = "hris:hris-workforce"
ERP = "erp:erp-jobs"
WORKFORCE = "hris-workforce"


@pytest.fixture()
def world():
    return test_world.fresh()


# ---- the world --------------------------------------------------------------


def land_team(world, hris_name: str, slug: str, people: int = 3):
    """One department, with a crew.

    Written directly rather than through `workforce-sync`, for the reason
    `test_field_documents` gives about crews: no production feed carries a
    plant department, and putting that feed's whole surface between this module
    and the fill would be testing the wrong thing. The **name** is what
    matters: the governed `## Departments` table is the only place the
    workforce system's spelling and the platform's slug are tied together, and
    this flow resolves the team through it.
    """
    team = world.graph.upsert(onto.TEAM, slug, [
        Claim(field="slug", value=slug, source=HRIS),
        Claim(field="name", value=hris_name, source=HRIS),
    ], label=hris_name)
    for index in range(people):
        email = f"{slug}-{index}@wickbuildings.com"
        person = world.graph.upsert(onto.EMPLOYEE, email, [
            Claim(field="email", value=email, source=HRIS),
            Claim(field="name", value=f"Crew {index}", source=HRIS),
            Claim(field="status", value="Active", source=HRIS),
        ], label=f"Crew {index}")
        world.graph.link("member_of", person.id, team.id)
    return team


def land_workforce_feed(world, hours_ago: float = 1.0):
    """A landing time for the crew list, as the workforce feed leaves one."""
    return world.graph.upsert(onto.SOURCE_RECORD, f"{WORKFORCE}:roster", [
        Claim(field="source", value=WORKFORCE, source=HRIS),
        Claim(field="external_id", value="roster", source=HRIS),
        Claim(
            field="landed_at",
            value=(utcnow() - timedelta(hours=hours_ago)).isoformat(),
            source=HRIS,
        ),
    ], label="roster")


def land_demand(
    world,
    number: str,
    department: str,
    hours: float,
    load_date: str,
    *,
    summary: str = "",
    done: tuple[str, ...] = (),
    combined_with: str = "",
):
    """One `JobTask` as the extraction leaves it, and the job under it.

    Written straight into the graph rather than by running the extraction: what
    this flow is judged on is what it does with a department's hours, and how
    those hours were read off a shipping list is the extraction's own test.
    """
    job = land_job(world, number, load_date=load_date)
    extra = []
    if done:
        extra.append(Claim(
            field="upstream_done", value=list(done), source=ERP,
        ))
    if combined_with:
        extra.append(
            Claim(field="combined_with", value=combined_with, source=ERP)
        )
    if extra:
        world.graph.upsert(onto.JOB, job.natural_key, extra)
    task = world.graph.upsert(
        onto.JOB_TASK, f"{job.natural_key}:{department}", [
            Claim(field="department", value=department, source="flow:test"),
            Claim(field="labor_hours", value=hours, source="flow:test"),
            Claim(field="hours_basis",
                  value="the governed formula · knowledge:labor-hour-formulas",
                  source="flow:test"),
            Claim(field="demand_summary",
                  value=summary or f"page C: {hours:g} h from a governed rate",
                  source="flow:test"),
            Claim(field="out_of_band", value="no", source="flow:test"),
        ], label=f"{number} {department}")
    world.graph.link("has_job_task", job.id, task.id)
    return job


def open_day(world, department: str, schedule_date):
    """Open a department-day through the Dispatcher, as the sweep does."""
    return world.scheduling.open_day(
        department, schedule_date.isoformat(), actor="system:test",
    ).one()


def _families(run) -> set[str]:
    """The step families a journal holds — loop suffixes trimmed."""
    out = set()
    for step in run.steps:
        head, _, tail = step.step_id.rpartition("_")
        out.add(head if head and tail.isdigit() else step.step_id)
    return out


def _day(world, department: str, schedule_date):
    return world.graph.get(
        object_id(
            onto.SCHEDULE_DAY,
            domain.day_key(department, schedule_date.isoformat()),
        ),
        follow=False,
    )


def _rows(world, department: str, schedule_date):
    record = _day(world, department, schedule_date)
    if record is None:
        return []
    rows = [
        entry for _link, entry in world.graph.neighbors(
            record.id, "has_schedule_entry", "out"
        )
    ]
    rows.sort(key=lambda entry: entry.props.get("sequence") or 0)
    return rows


def _decide(world, run, verdict: str, **kwargs):
    """Answer the run's open Task the way the console would."""
    task = world.tasks.get(world.engine.get(run.id).open_task_id)
    world.scheduling.resume_for_task(
        world.tasks.decide(task.id, verdict, SUPERVISOR, **kwargs)
    )
    return world.engine.get(run.id)


def steel_day(world, hours=(12.0, 8.0, 5.0), people: int = 3, stale=1.0):
    """Three shares of steel fab work on a day inside every window.

    Steel Fab is the fixture department because its governed row is the
    complete one: a minimum in hours, a goal max, an absolute max and a fudge,
    and no dependency in the sequencing table to arrange around.
    """
    land_team(world, "Steel Fabrication", "steel-fab", people)
    land_workforce_feed(world, hours_ago=stale)
    load = business_day(world, 30)
    day = business_day(world, 2)
    for index, figure in enumerate(hours):
        land_demand(world, f"25060{index}", "steel-fab", figure,
                    load.isoformat())
    return day


# ---- the map is the run -----------------------------------------------------


def test_the_declared_steps_are_exactly_the_steps_a_full_run_executes(world):
    """A case built to reach every step, and nothing left over either way.

    The supervisor corrects a row, so `r2` and `a1` run again over what is
    left; then they approve, so `a2` marks the day and `e1` says what was
    scheduled. What is left over in either direction is the bug — a step in the
    journal that no map declares, or a map promising a step nothing runs.
    """
    day = steel_day(world)

    run = open_day(world, "steel-fab", day)
    assert run.status == "suspended"
    run = _decide(world, run, "corrected",
                  note="250600 is six hours, not twelve.",
                  payload={"rows": {"250600": {"labor_hours": 6.0}}})
    run = _decide(world, run, "approved", note="Signed.")

    assert run.status == "succeeded"
    assert _families(run) == {step.id for step in v1.STEPS}
    # And the order, because a map is a sequence and not a set: the write sits
    # between the fill and the question, and the approval sits after both.
    assert [step.step_id for step in run.steps] == [
        "t1", "x0", "x1", "x2", "r1", "r2", "a1",
        "k1_1", "r2_1", "a1_1", "k1_2", "a2", "e1",
    ]


def test_x0_is_the_read_set_and_names_the_revision_it_read(world):
    """One read, one revision, and only the sections this flow consumes."""
    day = steel_day(world)
    run = open_day(world, "steel-fab", day)
    step = run.step("x0")

    assert step is not None and step.name == "read-knowledge"
    params = v1.PlantParameters.model_validate(step.output)
    assert params.capacity.rows and params.headcount.ladder
    assert params.incentives["steel-fab"].rate_usd_per_hour == 6.0
    assert f"knowledge:{POLICY_ASSET_ID}@rev1" in step.refs
    assert f"knowledge:{POLICY_ASSET_ID}@rev1" in run.refs
    # The sections this flow never consults are **not** in the read-set: the
    # step's output is a claim about what the run consulted, and the page
    # families and confidence floors belong to the extraction's own step.
    assert not params.page_families
    assert not params.thresholds.required_field_rule


# ---- the central claim, on this side of the export --------------------------


def test_the_day_is_pending_approval_while_the_task_is_open(world):
    """The write happens before the question, and stops there.

    `a1` is what gives a supervisor a record to open; `pending_approval` is
    what keeps it inside the building until they sign. Both halves matter: a
    flow that asked first would be asking about something that does not exist,
    and one that approved on its own would have no control point at all.
    """
    day = steel_day(world)
    run = open_day(world, "steel-fab", day)

    assert run.status == "suspended"
    record = _day(world, "steel-fab", day)
    assert record is not None
    assert record.props["status"] == "pending_approval"
    assert not record.props.get("approved_by")
    assert "exported_at" not in record.props

    task = world.tasks.get(run.open_task_id)
    assert task.queue == "plant-supervisor"
    assert task.kind == "decision"
    assert task.case_ref == "steel-fab:" + day.isoformat()
    assert task.proposal.action == "mark-day-approved"
    assert "the schedule export refuses to answer for it" in task.summary

    run = _decide(world, run, "approved", note="Signed.")
    record = _day(world, "steel-fab", day)
    assert run.status == "succeeded"
    assert record.props["status"] == "approved"
    assert record.props["approved_by"] == SUPERVISOR
    assert record.props["approved_at"]
    # And the approval is claimed under the approver's own authority, which is
    # what stops a later re-fill outranking it: `human` beats `flow`.
    approval = [
        claim for claim in record.claims
        if claim.field == "status" and claim.value == "approved"
    ]
    assert [claim.source for claim in approval] == [f"human:{SUPERVISOR}"]


# ---- the fill ---------------------------------------------------------------


def test_the_goal_max_stops_the_fill(world):
    """Twelve and eight reach the governed goal of twenty; five does not fit.

    `no-condenstop` is the row every Steel Fab day falls to, because both of
    that department's rows are conditioned on a property of a ticket rather
    than of a crew and the most restrictive one stands.
    """
    day = steel_day(world, hours=(12.0, 8.0, 5.0))
    run = open_day(world, "steel-fab", day)

    assert run.step("r2").output["total_hours"] == 20.0
    assert run.step("r2").output["limits"]["row"] == "no-condenstop"
    assert run.step("r2").output["limits"]["goal_max"] == 20.0
    assert run.step("r2").output["limits"]["absolute_max"] == 22.0
    assert run.step("r2").output["limits"]["fudge"] == 1.0
    deferred = run.step("r2").output["deferred"]
    assert [row["job_number"] for row in deferred] == ["250602"]
    assert deferred[0]["clause"] == "day-full"
    assert "governed goal max for `no-condenstop` is 20.0 h" in (
        deferred[0]["reason"]
    )
    placed = [
        row.props["job_number"] for row in _rows(world, "steel-fab", day)
    ]
    assert placed == ["250600", "250601"]


def test_the_fudge_carries_the_row_that_crosses_the_goal(world):
    """20 + 1 is the ceiling, so 20.5 is a day and 22 is not.

    The page's own reading: the fudge is the latitude either side of the goal,
    not a second target above it. A row that lands the day inside it is placed;
    the next one is deferred and the fill stops.
    """
    day = steel_day(world, hours=(12.0, 8.5, 0.4))
    run = open_day(world, "steel-fab", day)

    assert run.step("r2").output["total_hours"] == 20.5
    assert len(run.step("r2").output["rows"]) == 2
    # And the fudge does not extend past the ceiling it computed: a share that
    # would take the day to 22 h — the absolute max itself — is refused,
    # because the goal plus the fudge is 21 and that is the smaller bound.
    world.graph.upsert(onto.JOB_TASK, "erp-jobs:250602:steel-fab", [
        Claim(field="labor_hours", value=1.6, source="human:test"),
    ])
    again = open_day(world, "steel-fab", business_day(world, 3))
    assert again.step("r2").output["total_hours"] == 20.5


def test_the_absolute_max_plus_the_fudge_is_the_hard_stop(world):
    """Truss Fab's three-man row has no goal at all, only a ceiling.

    35 + 5, and the design's own sentence is asserted as a number: 38 hours is
    past the absolute max and is **accepted, not an exception**, while 43 is
    refused. A day with no goal max is the `—` the governed page carries for
    four departments Chris Robson still owes figures for.
    """
    land_team(world, "Truss Fabrication", "truss-fab", 3)
    land_workforce_feed(world)
    params = read_plant_parameters(world.knowledge)
    load = business_day(world, 25)
    day = params.calendar.business_days_before(load, 21)
    for index, hours in enumerate((20.0, 18.0, 5.0)):
        land_demand(world, f"25070{index}", "truss-fab", hours,
                    load.isoformat(), done=("truss-cut",))

    run = open_day(world, "truss-fab", day)
    fill = run.step("r2").output

    assert fill["limits"]["row"] == "table-3-man"
    assert fill["limits"]["goal_max"] is None
    assert fill["limits"]["absolute_max"] == 35.0
    assert fill["limits"]["fudge"] == 5.0
    assert fill["total_hours"] == 38.0
    assert "3 employee(s) on `table-3-man`" in fill["limits"]["row_note"]
    assert fill["deferred"][0]["clause"] == "over-capacity"
    assert "past the governed ceiling of 40.0 h" in (
        fill["deferred"][0]["reason"]
    )


def test_the_incentive_crossing_stops_the_two_departments_named(world):
    """Hardware & Trim Packaging fills by money, not by a ceiling.

    `- incentive decides the fill: trim-fab, hardware-trim-pack`, and the
    figure is **computed** from two governed numbers rather than read out of
    the table's fourth column. −150 ÷ 6 is twenty-five hours; the day stops at
    the first total past it.
    """
    land_team(world, "Hardware & Trim Packaging", "hardware-trim-pack", 4)
    land_workforce_feed(world)
    load = business_day(world, 20)
    day = business_day(world, 2)
    for index in range(4):
        land_demand(world, f"25080{index}", "hardware-trim-pack", 10.0,
                    load.isoformat(), done=("trim-fab",))

    run = open_day(world, "hardware-trim-pack", day)
    fill = run.step("r2").output

    assert fill["limits"]["fills_by_incentive"] is True
    assert fill["limits"]["crossing"] == 25.0
    assert fill["limits"]["absolute_max"] is None
    assert fill["total_hours"] == 30.0
    assert fill["out_of_the_hole"] is True
    assert fill["incentive_per_hour"] == 30.0
    assert fill["incentive_sentence"] == (
        "$-150.00 + $6.00/h × 30.0 h = $+30.00"
    )
    assert "crosses zero at 25.00 h" in fill["deferred"][0]["reason"]


def test_trim_fabs_ceiling_binds_before_its_incentive_ever_crosses(world):
    """A finding about the governed numbers, pinned so it cannot drift.

    Trim Fab is the second department the page says fills by the incentive, and
    its own figures make that unreachable: −180 ÷ 6.20 is 29.03 hours, and its
    capacity row stops at 25 + 1. So the ceiling always binds first and a Trim
    Fab day is always in the hole — which is the page's own worked example,
    `−$180.00 + $6.20/h × 29.0 h = −$0.20`, arrived at from the other side.
    """
    land_team(world, "Trim Fabrication", "trim-fab", 4)
    land_workforce_feed(world)
    load = business_day(world, 20)
    day = business_day(world, 2)
    for index, hours in enumerate((12.0, 12.0, 2.0, 1.0)):
        land_demand(world, f"25090{index}", "trim-fab", hours,
                    load.isoformat())

    run = open_day(world, "trim-fab", day)
    fill = run.step("r2").output
    params = read_plant_parameters(world.knowledge)
    incentive = params.incentive_for("trim-fab")

    assert round(formulas.crosses_zero_at(incentive), 2) == 29.03
    assert fill["limits"]["row"] == "few-specials"
    assert fill["total_hours"] == 26.0  # 25 goal + 1 fudge, and no further
    assert fill["out_of_the_hole"] is False
    assert fill["below_capacity"] is True
    assert fill["incentive_sentence"] == (
        "$-180.00 + $6.20/h × 26.0 h = $-18.80"
    )
    # The page's own worked example, computed rather than quoted.
    assert formulas.incentive_sentence(29.0, incentive) == (
        "$-180.00 + $6.20/h × 29.0 h = $-0.20"
    )
    # Below the governed minimum is **reported, never forced**: the day is
    # still written and still asked about, with the shortfall on the Task.
    task = world.tasks.get(run.open_task_id)
    assert "below its governed minimum" in task.summary
    assert task.priority == "high"


def test_the_incentive_is_stated_as_its_own_arithmetic_on_the_task(world):
    """The out-of-the-hole indicator is a sentence, not a cell."""
    day = steel_day(world)
    run = open_day(world, "steel-fab", day)
    task = world.tasks.get(run.open_task_id)
    evidence = {row.label: row.value for row in task.evidence}

    assert evidence["Incentive"] == "$-72.00 + $6.00/h × 20.0 h = $+48.00"
    assert evidence["Out of the hole"].startswith("yes")
    assert "cross zero at 12.00 h" in evidence["Out of the hole"]
    assert "the most restrictive row (`no-condenstop`) stands" in (
        evidence["Capacity row chosen"]
    )
    assert "250602" in evidence["Deferred"]


# ---- what the flow may never touch -----------------------------------------


def test_a_supervisor_placed_row_survives_a_re_run_untouched(world):
    """The one thing this flow may never do, enforced by precedence.

    A corrected row is claimed under a `human:` source, which outranks `flow:`
    in the ontology — so a later run re-asserting `placed_by: flow` on the same
    row loses, and the supervisor's hours stand. The row is also kept on the
    day and its hours counted first, so the fill works around it rather than
    over it.
    """
    day = steel_day(world)
    run = open_day(world, "steel-fab", day)
    run = _decide(world, run, "corrected", note="Six hours.",
                  payload={"rows": {"250600": {"labor_hours": 6.0}}})
    _decide(world, run, "rejected", note="Not yet — leave it with me.")

    first = {
        row.props["job_number"]: row
        for row in _rows(world, "steel-fab", day)
    }
    assert first["250600"].props["placed_by"] == "human"
    assert first["250600"].props["labor_hours"] == 6.0
    assert _day(world, "steel-fab", day).props["status"] == "pending_approval"

    # The day is unapproved and no Task is open, so the next window re-fills
    # it — and it must come back with the supervisor's row exactly as they
    # left it.
    again = open_day(world, "steel-fab", day)
    rows = {
        row.props["job_number"]: row
        for row in _rows(world, "steel-fab", day)
    }

    assert again.status == "suspended"
    assert rows["250600"].props["placed_by"] == "human"
    assert rows["250600"].props["labor_hours"] == 6.0
    assert again.step("x1").output["human_rows"][0]["job_number"] == "250600"
    assert "placed by a supervisor and worked around" in (
        again.step("r2").summary
    )
    # 6 already on the day, and the fill puts the other two around it.
    assert again.step("r2").output["human_hours"] == 6.0
    assert again.step("r2").output["total_hours"] == 19.0


def test_a_day_already_approved_is_not_refilled(world):
    """A signed day is settled. Nothing re-proposes underneath a signature."""
    day = steel_day(world)
    run = open_day(world, "steel-fab", day)
    _decide(world, run, "approved", note="Signed.")
    revision = _day(world, "steel-fab", day).revision

    again = open_day(world, "steel-fab", day)

    assert again.status == "succeeded"
    assert again.outputs["state"] == "settled"
    assert "nothing re-fills a signed day" in again.outputs["reason"]
    assert [step.step_id for step in again.steps] == ["t1", "x0", "x1"]
    assert _day(world, "steel-fab", day).revision == revision


def test_a_day_with_an_open_approval_task_is_left_alone(world):
    """One Task per department-day, by construction rather than by counting."""
    day = steel_day(world)
    first = open_day(world, "steel-fab", day)

    again = open_day(world, "steel-fab", day)

    assert again.outputs["state"] == "settled"
    assert again.outputs["open_task_id"] == first.open_task_id
    assert len([
        task for task in world.tasks.list()
        if task.flow_id == domain.FLOW_ID and task.status == "open"
    ]) == 1


def test_the_correction_loop_is_capped_and_leaves_the_day_unapproved(world):
    """It stops asking rather than looping, and stops on the safe side.

    Past `MAX_REVIEW_ROUNDS` the flow writes what the last correction left and
    finishes. The day stays `pending_approval`, which means unexportable —
    nobody signed it, so nothing may leave.
    """
    day = steel_day(world)
    run = open_day(world, "steel-fab", day)
    for number in range(domain.MAX_REVIEW_ROUNDS):
        run = _decide(world, run, "corrected", note=f"round {number}",
                      payload={"rows": {"250601": {"labor_hours": 7.0}}})

    assert run.status == "succeeded"
    assert run.outputs["state"] == "unsettled"
    assert run.outputs["review_rounds"] == domain.MAX_REVIEW_ROUNDS
    assert _day(world, "steel-fab", day).props["status"] == "pending_approval"
    assert not [task for task in world.tasks.list() if task.status == "open"]


# ---- the Rule ---------------------------------------------------------------


def test_a_dependency_not_met_ends_deferred_and_raises_nothing(world):
    """*"Unmet upstream dependency: deferred to the next window."*

    Truss Fabrication waits on Truss Cutting, and the job system does not have
    it done. Nothing waits in place: the run records the reason and finishes,
    no `ScheduleDay` is written, and nobody is asked — because there is nothing
    to decide about a saw that has not finished.
    """
    land_team(world, "Truss Fabrication", "truss-fab", 3)
    land_workforce_feed(world)
    params = read_plant_parameters(world.knowledge)
    load = business_day(world, 25)
    day = params.calendar.business_days_before(load, 21)
    land_demand(world, "250553", "truss-fab", 20.0, load.isoformat())

    run = open_day(world, "truss-fab", day)

    assert run.status == "succeeded"
    assert run.outputs["state"] == "deferred"
    assert run.outputs["deferrals"] == {"upstream-not-done": 1}
    reason = run.outputs["reasons"][0]["reason"]
    assert "puts `truss-cut` before `truss-fab`" in reason
    assert "deferred to the next window" in reason
    assert [step.step_id for step in run.steps] == [
        "t1", "x0", "x1", "x2", "r1", "r2",
    ]
    assert _day(world, "truss-fab", day) is None
    assert not world.tasks.list()


def test_a_job_held_after_it_queued_is_no_longer_eligible(world):
    """The eligibility clauses are re-asked here, through the sweep's own Rule.

    A job somebody put a hold on between the window opening the day and the
    fill running must not be built this afternoon — and the clause that
    excludes it is the same clause, with the same wording, the eligibility
    sweep uses.
    """
    day = steel_day(world)
    world.graph.upsert(onto.JOB, "erp-jobs:250600", [
        Claim(field="hold_reason", value="DNF", source=ERP),
    ])

    run = open_day(world, "steel-fab", day)
    verdicts = {row["job_number"]: row for row in run.step("r1").output}

    assert verdicts["250600"]["placeable"] is False
    assert verdicts["250600"]["clause"] == "not-eligible:held"
    assert "the job is held: `DNF`" in verdicts["250600"]["reason"]
    assert run.step("r2").output["total_hours"] == 13.0


def test_a_day_outside_the_farthest_out_window_defers_with_that_clause(world):
    """Truss work may not be placed earlier than the governed window allows."""
    land_team(world, "Truss Fabrication", "truss-fab", 3)
    land_workforce_feed(world)
    params = read_plant_parameters(world.knowledge)
    load = business_day(world, 40)
    too_early = params.calendar.business_days_before(load, 22)
    land_demand(world, "250553", "truss-fab", 20.0, load.isoformat(),
                done=("truss-cut",))

    run = open_day(world, "truss-fab", too_early)

    assert run.outputs["state"] == "deferred"
    assert run.outputs["deferrals"] == {"too-far-out": 1}
    assert "21 business day(s) before the load date" in (
        run.outputs["reasons"][0]["reason"]
    )


# ---- the headcount ----------------------------------------------------------


def test_the_headcount_is_the_member_of_count(world):
    """No sheet, so no typed number. The org chart is the crew list.

    And the count is what chooses the capacity row for the truss tables, which
    is why the ladder and the count have to agree: four people is a four-man
    table, and a fifth moves the day to a hundred-hour row.
    """
    land_team(world, "Truss Fabrication", "truss-fab", 4)
    land_workforce_feed(world)
    params = read_plant_parameters(world.knowledge)
    load = business_day(world, 25)
    day = params.calendar.business_days_before(load, 21)
    land_demand(world, "250553", "truss-fab", 20.0, load.isoformat(),
                done=("truss-cut",))

    run = open_day(world, "truss-fab", day)
    state = run.step("x1").output

    assert state["headcount"] == 4.0
    assert state["headcount_stale"] is False
    assert "4 active employee(s) `member_of` truss-fab" in (
        state["headcount_note"]
    )
    assert run.step("r2").output["limits"]["row"] == "table-4-man"
    assert run.step("r2").output["limits"]["absolute_max"] == 45.0
    assert _day(world, "truss-fab", day).props["headcount"] == 4.0


def test_a_terminated_employee_is_not_on_the_crew(world):
    """*"Count of **active** employees member_of the department team."*"""
    day = steel_day(world, people=3)
    world.graph.upsert(onto.EMPLOYEE, "steel-fab-0@wickbuildings.com", [
        Claim(field="status", value="Terminated", source=HRIS),
    ])

    run = open_day(world, "steel-fab", day)

    assert run.step("x1").output["headcount"] == 2.0


def test_a_stale_headcount_warns_and_proceeds(world):
    """Exception 7: warn, proceed, supervisor to confirm — never fail.

    The governed freshness window is 24 hours. A feed that last landed three
    days ago is a warning printed on the approval Task with the number and its
    age, and the day is scheduled all the same.
    """
    day = steel_day(world, stale=72.0)

    run = open_day(world, "steel-fab", day)
    task = world.tasks.get(run.open_task_id)
    evidence = {row.label: row.value for row in task.evidence}

    assert run.status == "suspended"
    assert run.step("x1").output["headcount_stale"] is True
    assert "Headcount warning" in task.summary
    assert "warn and proceed" in task.summary
    assert task.priority == "high"
    assert evidence["Headcount"].startswith("3 (stale)")
    assert "72." in evidence["Headcount"]
    # And the day was written regardless — a stale crew count is not a reason
    # to leave a department without a schedule.
    assert _day(world, "steel-fab", day).props["status"] == "pending_approval"


# ---- row shaping ------------------------------------------------------------


def test_a_multi_ply_truss_gets_its_own_nailing_row(world):
    """A job is not always one row, and the total is preserved exactly.

    The governed page raises a NAILING row where any truss is multi-ply and a
    GIRTS row where the B page carries the handwritten note. The extraction
    folds all three into one `JobTask`, and the per-formula split it wrote into
    `demand_summary` is what the day is shaped from — so 16.5 hours becomes
    12 + 3 + 1.5 and not 16.5 twice.
    """
    land_team(world, "Truss Fabrication", "truss-fab", 3)
    land_workforce_feed(world)
    params = read_plant_parameters(world.knowledge)
    load = business_day(world, 25)
    day = params.calendar.business_days_before(load, 21)
    land_demand(
        world, "250553", "truss-fab", 16.5, load.isoformat(),
        done=("truss-cut",),
        summary=(
            "page A1: 12 h from truss fab: the standards' fabrication hours, "
            "summed over the trusses; 3 h from nailing: the standards' "
            "nailing hours over the multi-ply trusses; 1.5 h from girts "
            "plated in: the standards' per-truss girts figure"
        ),
    )

    run = open_day(world, "truss-fab", day)
    rows = _rows(world, "truss-fab", day)

    assert [row.props["row_kind"] for row in rows] == [
        "standard", "nailing", "girts",
    ]
    assert [row.props["labor_hours"] for row in rows] == [12.0, 3.0, 1.5]
    # The governed label convention's `Extra` column, per row kind.
    assert [row.props["job_label"] for row in rows] == [
        "250553 TF", "250553 TF-NAIL", "250553 TF-GIRTS",
    ]
    assert run.step("r2").output["total_hours"] == 16.5
    assert _day(world, "truss-fab", day).props["total_hours"] == 16.5


def test_loading_consolidates_tickets_that_share_a_truck(world):
    """Loading is the opposite shape: two tickets on one truck are one row.

    And the trailer number is left blank on purpose — which trailer goes out is
    the yard's call on the day, and writing a guess into that column would be
    this system making an operational decision.
    """
    land_team(world, "Loading & Yard", "loading", 2)
    land_workforce_feed(world)
    params = read_plant_parameters(world.knowledge)
    load = business_day(world, 10)
    day = params.calendar.business_days_before(load, 1)
    land_demand(world, "250801", "loading", 2.0, load.isoformat(),
                combined_with="250802")
    land_demand(world, "250802", "loading", 1.5, load.isoformat(),
                combined_with="250801")
    land_demand(world, "250803", "loading", 1.0, load.isoformat())

    open_day(world, "loading", day)
    rows = _rows(world, "loading", day)

    assert [row.props["row_kind"] for row in rows] == [
        "consolidated", "standard",
    ]
    assert rows[0].props["labor_hours"] == 3.5
    assert not rows[0].props.get("vehicle_ref")
    assert rows[1].props["job_number"] == "250803"
    assert "tickets sharing a truck become one row" in rows[0].props["basis"]


# ---- the numbers are governed -----------------------------------------------


def test_editing_the_governed_capacity_moves_where_the_fill_stops(world):
    """One line on a page, no deploy, and the day is a different day."""
    day = steel_day(world)
    before = open_day(world, "steel-fab", day)
    assert before.step("r2").output["total_hours"] == 20.0

    world.knowledge.update(POLICY_ASSET_ID, AssetUpdate(
        content=world.knowledge.get(POLICY_ASSET_ID).content.replace(
            "| steel-fab | no-condenstop | 12 | 20 | 22 | 1 | |",
            "| steel-fab | no-condenstop | 12 | 12 | 14 | 1 | |",
        ),
        updated_by="plant-operations",
    ))

    after = open_day(world, "steel-fab", business_day(world, 3))

    assert after.step("r2").output["limits"]["goal_max"] == 12.0
    assert after.step("r2").output["total_hours"] == 12.0
    assert len(after.step("r2").output["rows"]) == 1
    assert f"knowledge:{POLICY_ASSET_ID}@rev2" in after.step("x0").refs


def test_editing_the_governed_incentive_moves_where_the_fill_stops(world):
    """The bonus cell is two numbers on a page, and the fill reads them.

    `formulas.py` holds no literal, so halving the starting figure halves the
    hours a Hardware & Trim Packaging day needs to climb out of the hole — and
    the day stops there instead.
    """
    land_team(world, "Hardware & Trim Packaging", "hardware-trim-pack", 4)
    land_workforce_feed(world)
    load = business_day(world, 20)
    for index in range(4):
        land_demand(world, f"25080{index}", "hardware-trim-pack", 10.0,
                    load.isoformat(), done=("trim-fab",))

    before = open_day(world, "hardware-trim-pack", business_day(world, 2))
    assert before.step("r2").output["total_hours"] == 30.0

    world.knowledge.update(POLICY_ASSET_ID, AssetUpdate(
        content=world.knowledge.get(POLICY_ASSET_ID).content.replace(
            "| hardware-trim-pack | -150.00 | 6.00 | 25.0 |",
            "| hardware-trim-pack | -60.00 | 6.00 | 10.0 |",
        ),
        updated_by="plant-operations",
    ))

    after = open_day(world, "hardware-trim-pack", business_day(world, 3))

    assert after.step("r2").output["limits"]["crossing"] == 10.0
    assert after.step("r2").output["total_hours"] == 10.0
    assert after.step("r2").output["incentive_sentence"] == (
        "$-60.00 + $6.00/h × 10.0 h = $+0.00"
    )


# ---- the closed tables ------------------------------------------------------


def test_the_two_actions_and_the_trigger_are_declared(world):
    """The co-landing rule, checked from the lane that owns all three rows.

    `Dispatcher.emit` starts every matched Trigger without guarding a missing
    body, and this fact is emitted from *inside* the eligibility sweep — so a
    Trigger landing before the body would fail the window that raised it.
    """
    trigger = TRIGGERS["department-day-opened"]
    assert trigger.event == domain.DEPARTMENT_DAY_EVENT
    assert trigger.starts == domain.FLOW_ID
    assert trigger.wakes == "window"

    for action_id in ("apply-schedule-day", "mark-day-approved"):
        action = ACTIONS[action_id]
        assert action.target == "production-records"
        assert action.regime == "decision"
        assert action.queue == "plant-supervisor"
        assert action.queue_from_task is True


def test_both_writes_reach_the_ledger_under_the_desk_that_signed(world):
    """Two Actions, two ledger rows, and the approval names its Task.

    The day's fill commits under the standing desk on the first pass, because
    no Task authorized it — the flow writes it precisely so a person has
    something to open. The signature commits under the desk of the Task where
    that person answered.
    """
    day = steel_day(world)
    run = open_day(world, "steel-fab", day)
    task_id = run.open_task_id
    _decide(world, run, "approved", note="Signed.")

    entries = [
        entry for entry in world.gateway.ledger.list()
        if entry.flow_id == domain.FLOW_ID
    ]
    by_action = {entry.action: entry for entry in entries}

    assert set(by_action) == {"apply-schedule-day", "mark-day-approved"}
    assert all(entry.outcome == "committed" for entry in entries)
    assert all(entry.regime == "decision" for entry in entries)
    assert by_action["mark-day-approved"].task_id == task_id
    assert by_action["mark-day-approved"].decided_by == SUPERVISOR
    assert by_action["apply-schedule-day"].writes
