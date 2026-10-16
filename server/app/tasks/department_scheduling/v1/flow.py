"""Department scheduling, v1 — a day filled, signed, and only then exportable.

Ten steps, and the order of three of them is the whole design. `a1` writes the
day at `pending_approval` **before** `k1` asks, because the thing a supervisor
is approving has to be a record they can open rather than a proposal in a run's
memory; `k1` suspends; `a2` moves the day to `approved`, and the export answers
at that instant and not before. A run that stops anywhere in between leaves a
day nobody can pull — which is the point.

Two rules and both are pure. `r1` judges each share against the governed
clauses in the page's own order and defers what fails, with the reason;
nothing waits, because the next day's eligibility sweep re-raises the day. `r2`
adds shares in load-date order until the governed goal max — or, for the two
departments the page names, until the computed incentive crosses zero — with
the absolute max plus the fudge as the hard stop.

The one thing this flow may never do is overwrite a supervisor. Rows carrying
`placed_by: human` are kept, their hours counted against the day's capacity
first, and the fill works around them.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from ....flows import FlowStep
from ....runs import RunContext
from ....task_screens import TaskScreen, TaskUi
from ..parameters import POLICY_ASSET_ID, PlantParameters
from ..domain import (
    DAY_SCHEDULED_EVENT,
    DEPARTMENT_DAY_EVENT,
    MAX_REVIEW_ROUNDS,
    Candidate,
    DayState,
    Fill,
    Limits,
    Row,
    Verdict,
    choose_limits,
    consolidate_loading,
    deferrals,
    fill,
    judge,
    plant_flow_principal,
)

VERSION = 1
LABEL = "v1"
NOTES = (
    "One department's day, once per (department, business day). It reads the "
    "governed capacity table, the headcount ladder, the lead times and "
    "farthest-out windows, the sequencing dependencies, the row-shaping "
    "conditions, the label convention, the incentive's two figures and the "
    "approval table — all in one step, at one revision. Then it reads the day "
    "out of **our own graph**: any `ScheduleDay` a previous window "
    "opened, its rows, and specifically the `placed_by: human` rows a "
    "supervisor placed, "
    "which it must never overwrite; plus the headcount, which is the count of "
    "active employees `member_of` the department's team rather than a number "
    "somebody typed into a workbook. It gathers this department's demand rows "
    "with each job's upstream state read live off the job's own Done flags, "
    "judges every share against the governed clauses — eligible, upstream "
    "done, inside both windows, on a day the plant is open — and defers what "
    "fails **with its reason, never as a failure**. The fill then adds shares "
    "in load-date order until the governed goal max, or until the computed "
    "incentive crosses zero for the two departments the page names, with the "
    "absolute max plus the fudge as the hard stop; below capacity is reported "
    "and never forced. One guarded write puts the day at `pending_approval` "
    "with its rows, their basis and the computed incentive; one Task per "
    "department-day carries the rows, the total against the limits, the "
    "incentive stated as its own arithmetic, what was deferred and why, and a "
    "headcount staleness warning that warns and proceeds. The run suspends "
    "there — the permanent control point. Approving marks the day approved, "
    "and that is the instant it becomes exportable. **Nothing leaves the "
    "graph before then.**"
)

#: One screen, because a supervisor opening this is doing one thing: reading a
#: day against three governed numbers and signing it or changing it. The rows
#: on the proposal are what the screen edits, and a correction re-fills what is
#: left around whatever they touched.
UI = TaskUi(
    otherwise=TaskScreen(
        screen_id="plant-schedule/day-approval",
        label="Open the day for approval",
        hint=(
            "Opens the department's day: every row with its hours and the "
            "governed formula that produced them, the total against the goal "
            "max and the absolute max with the fudge band, the incentive "
            "written out as its own arithmetic so the out-of-the-hole "
            "indicator can be argued with, and everything the fill deferred "
            "with the clause that deferred it. Editing or removing a row "
            "stamps it as yours and re-fills the rest around it; approving is "
            "what makes the day exportable."
        ),
    ),
)


STEPS: list[FlowStep] = [
    FlowStep(
        id="t1", type="trigger", name=DEPARTMENT_DAY_EVENT,
        summary=(
            "one run per (department, business day) — the fact the plant "
            "window raised once its extractions had produced demand rows for "
            "that department. Windowed, because capacity, headcount and the "
            "incentive are all properties of the day rather than of a job"
        ),
        config={"event": DEPARTMENT_DAY_EVENT, "wakes": "window"},
        next=["x0"],
    ),
    FlowStep(
        id="x0", type="transform", name="read-knowledge",
        summary=(
            "every governed value this run consumes, read once and recorded "
            "at its revision: this department's capacity row and its min, "
            "goal max, absolute max and fudge; the headcount ladder and its "
            "freshness window; the lead time and farthest-out window; the "
            "sequencing table and its dependency clause; the row-shaping "
            "conditions; the job label abbreviations; the incentive's "
            "starting figure and per-hour rate and which departments fill by "
            "it; the eligibility clauses and the source vocabulary they "
            "resolve against; the business-day calendar; and the approval "
            "table this day's sign-off is routed by"
        ),
        config={"source": f"knowledge:{POLICY_ASSET_ID}"},
        next=["x1"],
    ),
    FlowStep(
        id="x1", type="transform", name="read-day-state",
        summary=(
            "the day as **our own graph** already holds it — never a "
            "spreadsheet and never the estate: the `ScheduleDay` a previous "
            "window opened, its rows, and specifically the rows a "
            "supervisor's correction stamped `placed_by: human`, which this "
            "flow must not overwrite. Plus the headcount, which is the count "
            "of active employees `member_of` the department's team, and how "
            "old the workforce feed's last landing is — because there is no "
            "headcount typed into a sheet, since there is no sheet"
        ),
        config={"source": "graph:ScheduleDay,graph:Team,graph:Employee"},
        next=["x2"],
    ),
    FlowStep(
        id="x2", type="transform", name="gather-candidate-work",
        summary=(
            "this department's `JobTask` rows, in load-date order as the "
            "governed page asks, each carrying its job's upstream state read "
            "**live** off the job's own Done flags rather than off a snapshot "
            "taken when the job became eligible — a job whose truss cutting "
            "finished this morning is fabricable this afternoon"
        ),
        config={"source": "graph:JobTask,graph:Job"},
        next=["r1"],
    ),
    FlowStep(
        id="r1", type="rule", name="dependency-and-lead-time",
        summary=(
            "pure, per share, in the page's own order: is the job still "
            "eligible at all (re-asked, because a job held after it queued "
            "must not be built), is the upstream department done, is this day "
            "inside the lead-time and farthest-out windows, and is it a day "
            "the plant is open on. **Not met is `deferred` with its reason, "
            "never a failure** — the run finishes and the next day's "
            "eligibility sweep re-raises the day"
        ),
        config={"rule_type": "cross-reference",
                "source": f"knowledge:{POLICY_ASSET_ID}"},
        next=["r2"],
    ),
    FlowStep(
        id="r2", type="rule", name="fill-to-capacity",
        summary=(
            "pure: adds shares in load-date order until the governed goal "
            "max — or, for the two departments under `incentive decides the "
            "fill`, until the **computed** incentive crosses zero — inside "
            "the fudge band, with the absolute max as the hard stop. "
            "Exceeding the absolute max by within the fudge is explicitly not "
            "an exception, and the page says so. Rows a supervisor placed are "
            "counted first and worked around. Below capacity is reported and "
            "never forced"
        ),
        config={"rule_type": "accumulation",
                "source": f"knowledge:{POLICY_ASSET_ID}"},
        next=["a1"],
    ),
    FlowStep(
        id="a1", type="action", name="apply-schedule-day",
        summary=(
            "guarded: the `ScheduleDay` at `pending_approval` and its "
            "`ScheduleEntry` parts — each with its basis, its row kind, its "
            "governed label and `placed_by: flow` — plus the total, the "
            "computed incentive per hour, the headcount and its age, and how "
            "much was deferred. **This is the write. There is no second one, "
            "and nothing leaves the graph here**"
        ),
        config={"action": "apply-schedule-day",
                "target": "production-records"},
        next=["k1"],
    ),
    FlowStep(
        id="k1", type="task", name="approve-department-day",
        summary=(
            "the permanent control point: one Task per department-day, "
            "carrying the rows with their hours and basis, the total against "
            "the goal and the absolute max with the fudge band, the incentive "
            "stated as its own arithmetic so the out-of-the-hole indicator is "
            "auditable, what was deferred and why, a headcount staleness "
            "warning where the workforce feed is behind the governed window "
            "(warn and proceed, never fail), and what approving unlocks. The "
            "run suspends. A correction re-fills what is left around the rows "
            "the supervisor touched and asks again, capped so it stops asking "
            "rather than looping"
        ),
        config={"queue": "plant-supervisor",
                "rounds": str(MAX_REVIEW_ROUNDS),
                "source": f"knowledge:{POLICY_ASSET_ID}"},
        next=["a2"],
    ),
    FlowStep(
        id="a2", type="action", name="mark-day-approved",
        summary=(
            "guarded: `status → approved`, with who signed it and when, "
            "claimed under the approver's own authority so no later re-fill "
            "can un-approve it. **The day becomes exportable at this instant "
            "and not before**"
        ),
        config={"action": "mark-day-approved",
                "target": "production-records"},
        next=["e1"],
    ),
    FlowStep(
        id="e1", type="event", name=DAY_SCHEDULED_EVENT,
        summary=(
            "the day is scheduled, with its totals. Nothing is bound to this "
            "fact: what reads an approved day is the schedule export, on "
            "request, and a Trigger here would be a response looking for a "
            "question"
        ),
        config={"event": DAY_SCHEDULED_EVENT},
    ),
]


# ---------------------------------------------------------------------------
# x0 — one governed read
# ---------------------------------------------------------------------------


class GovernedReads(BaseModel):
    """Everything `x0` read, in one step output.

    One step rather than nine, for `accounts_payable/v9`'s reason: a run that
    read the same page from nine places could name two revisions of it if
    somebody republished mid-window, and a reader of the map could not tell
    what the run consults.
    """

    plant: PlantParameters = Field(default_factory=PlantParameters)


def _plant_reads(plant: PlantParameters) -> PlantParameters:
    """The sections this flow consumes, and only those.

    The step's output *is* the read-set, so a step that dumped the page
    families and confidence floors it never consults would claim a read that
    did not happen. Those belong to the extraction's own step.
    """
    return PlantParameters(
        eligibility=plant.eligibility,
        calendar=plant.calendar,
        departments=plant.departments,
        vocabulary=plant.vocabulary,
        capacity=plant.capacity,
        headcount=plant.headcount,
        lead_times=plant.lead_times,
        sequencing=plant.sequencing,
        row_shaping=plant.row_shaping,
        labels=plant.labels,
        incentives=plant.incentives,
        incentive_model=plant.incentive_model,
        incentive_decides_the_fill=plant.incentive_decides_the_fill,
        approval=plant.approval,
        source_ref=plant.source_ref,
        found_asset=plant.found_asset,
    )


def _reads_summary(params: PlantParameters, department: str) -> str:
    """What the page says about *this* department, at this revision.

    The capacity rows are named rather than reduced to one, because most
    departments carry more than one and which of them answers is a question
    the fill settles with the day's crew count in hand — a summary that picked
    one here would be guessing at a step that has not read the headcount yet.
    """
    rows = [
        row for row in params.capacity.rows if row.department == department
    ]
    incentive = params.incentive_for(department)
    return (
        f"{params.source_ref}: {len(params.capacity.rows)} capacity row(s), "
        f"{len(params.headcount.ladder)} headcount rung(s), "
        f"{len(params.lead_times)} lead-time row(s), "
        f"{len(params.sequencing.rows)} sequencing row(s) · "
        + (
            f"`{department}` has "
            + ", ".join(
                f"`{row.row}` (goal "
                + (f"{row.goal_max:g} h" if row.goal_max is not None else "—")
                + ", absolute max "
                + (f"{row.absolute_max:g} h"
                   if row.absolute_max is not None else "none")
                + f", fudge {row.fudge:g} h)"
                for row in rows
            )
            if rows else
            f"the page carries no capacity row for `{department}`"
        )
        + (
            f" · incentive ${incentive.start_usd:,.2f} + "
            f"${incentive.rate_usd_per_hour:,.2f}/h"
            if incentive.found_asset else " · no incentive figures on the page"
        )
        + (
            ", which decides the fill"
            if department in params.incentive_decides_the_fill else ""
        )
        + f" · approvals to `{params.approval.fallback}`"
        + ("" if params.found_asset
           else " — the page is not in the corpus, so this day ran on "
                "built-in behaviour and every limit is unset")
    )


# ---------------------------------------------------------------------------
# The body
# ---------------------------------------------------------------------------


def build_body(service):
    """Bind the scheduling service and return the engine-runnable body."""

    def body(ctx: RunContext, inputs: dict) -> dict:
        department = str(inputs.get("department") or "")
        schedule_date = str(inputs.get("schedule_date") or "")
        as_of = _as_of(inputs)
        ctx.event(
            "t1", DEPARTMENT_DAY_EVENT,
            f"a day opened for `{department}` on {schedule_date}"
            + (
                f", {inputs['lead_time_days']} business day(s) before the "
                "load date" if inputs.get("lead_time_days") else ""
            )
            + (" — late, so it opened on the first day available"
               if inputs.get("late") else ""),
            kind="trigger",
        )

        # -- x0: the governed read -----------------------------------------
        #
        # Read once, outside the step, and used for both the output and the
        # read-set: two reads could name two revisions of one page in the run
        # where somebody republishes mid-window.
        governed = service.read_parameters()
        params = PlantParameters.model_validate(ctx.step(
            "x0", "read-knowledge", "transform",
            fn=lambda: _plant_reads(governed).model_dump(mode="json"),
            summary=lambda out: _reads_summary(
                PlantParameters.model_validate(out), department
            ),
            refs=[governed.source_ref, governed.vocabulary.source_ref],
        ))
        ctx.note_refs(params.source_ref, params.vocabulary.source_ref)

        # -- x1: our own graph, and nothing else ---------------------------
        found = service.day_state(department, schedule_date, params)
        state = DayState.model_validate(ctx.step(
            "x1", "read-day-state", "transform",
            fn=lambda: found.model_dump(mode="json"),
            summary=lambda out: _day_summary(DayState.model_validate(out)),
            refs=[ref for ref in (found.day_ref,) if ref],
        ))

        # A day somebody has already signed is settled, and a day with an
        # approval Task open under somebody's hand is theirs until they answer
        # it. Re-filling either would be this flow proposing underneath a
        # person — the one thing the control point exists to prevent.
        if state.status == "approved":
            return _settled(
                state, params,
                "the day is already approved; nothing re-fills a signed day",
            )
        if state.open_task_id:
            return _settled(
                state, params,
                f"an approval Task ({state.open_task_id}) for this day is "
                "still open, so the day was left exactly as the supervisor "
                "will find it",
            )

        # -- x2: this department's share of the work -----------------------
        gathered = service.candidates(department, params)
        rows = ctx.step(
            "x2", "gather-candidate-work", "transform",
            fn=lambda: {
                "candidates": [
                    row.model_dump(mode="json") for row in gathered
                ],
            },
            summary=lambda out: _candidates_summary(out, params, department),
            refs=sorted({row.task_ref for row in gathered if row.task_ref}),
        )
        candidates = {
            row.job_key: row
            for row in (
                Candidate.model_validate(item) for item in rows["candidates"]
            )
        }

        # -- r1: the Rule --------------------------------------------------
        judged = ctx.step(
            "r1", "dependency-and-lead-time", "rule",
            fn=lambda: [
                verdict.model_dump(mode="json")
                for verdict in judge(
                    list(candidates.values()), params,
                    department=department,
                    schedule_date=_iso(schedule_date, as_of),
                    as_of=as_of,
                )
            ],
            summary=lambda out: _rule_summary(
                [Verdict.model_validate(row) for row in out]
            ),
            refs=[params.source_ref],
        )
        verdicts = [Verdict.model_validate(row) for row in judged]
        placeable = [verdict for verdict in verdicts if verdict.placeable]
        blocked = [verdict for verdict in verdicts if not verdict.placeable]

        limits = choose_limits(params, department, state.headcount)

        # -- r2: the fill --------------------------------------------------
        filled = _fill_step(
            ctx, "", params, department, candidates,
            placeable, blocked, limits, state.human_rows,
        )

        # A day the plant cannot fill gets no schedule and nobody is asked:
        # the design's own sentence, and the honest end for a department whose
        # saw has not finished. Nothing was written, so there is nothing to
        # approve and nothing to export.
        if not filled.rows:
            return _deferred(state, params, filled, verdicts)

        # -- a1: the write, before anybody is asked ------------------------
        written = _write_step(
            ctx, service, "", state, filled, params, decided_by="",
        )

        rounds = 0
        decided_by = ""
        decision_note = ""
        commit_queue = ""
        approved = False
        dropped: set[str] = set()
        human_rows = list(state.human_rows)
        outcome = "unsettled"

        for round_number in range(1, MAX_REVIEW_ROUNDS + 1):
            rounds = round_number
            desk = params.desk_for(filled.total_hours)
            commit_queue = desk.queue
            decision = ctx.gate(
                f"k1_{round_number}", "approve-department-day",
                build_task=lambda s=state, f=filled, rn=round_number,
                q=desk.queue, note=desk.explanation:
                    service.build_approval_task(
                        ctx, s, f, params,
                        round_number=rn, queue=q, routing_note=note,
                    ),
            )
            decided_by = decision.decided_by
            decision_note = decision.note or decision.correction

            if decision.verdict == "approved":
                approved = True
                outcome = "approved"
                break
            if decision.verdict == "rejected":
                # Nothing more is written. The day keeps its rows and its
                # `pending_approval` status, the export keeps refusing, and
                # the next window re-fills it — which is the whole point of a
                # gate that is a control point rather than a formality.
                outcome = "rejected"
                break

            # `corrected`: the supervisor moved rows. Their rows are stamped
            # as theirs and kept; the fill runs again over what is left.
            human_rows, dropped, changed, refused = _apply_correction(
                filled, decision.payload, decided_by, dropped,
            )
            remaining = [
                verdict for verdict in placeable
                if verdict.job_key not in dropped
            ]
            removed = [
                verdict.model_copy(update={
                    "placeable": False, "clause": "supervisor-removed",
                    "reason": (
                        f"{decided_by or 'a supervisor'} took this row off "
                        "the day, and the governed page says supervisor-"
                        "placed rows are never overwritten"
                    ),
                })
                for verdict in placeable if verdict.job_key in dropped
            ]
            filled = _fill_step(
                ctx, f"_{round_number}", params, department, candidates,
                remaining, blocked + removed, limits, human_rows,
                correction=(changed, refused),
            )
            written = _write_step(
                ctx, service, f"_{round_number}", state, filled, params,
                decided_by=decided_by, queue=commit_queue,
                task_id=f"task_{ctx.record.id}_k1_{round_number}",
            )

        # -- a2: the instant the day becomes exportable --------------------
        approval = {}
        if approved:
            principal = ctx.principal or plant_flow_principal(commit_queue)
            commit = service.gateway.action(
                "mark-day-approved", principal,
                run=ctx.record, step_id="a2",
                task_id=f"task_{ctx.record.id}_k1_{rounds}",
                decided_by=decided_by,
                queue=commit_queue,
            )
            approval = ctx.step(
                "a2", "mark-day-approved", "action",
                fn=lambda: commit.commit(
                    lambda writer: service.mark_day_approved(
                        state, decided_by, ctx.record.id, writer,
                    )
                ),
                writes=lambda _out: commit.writes,
                summary=lambda out: (
                    f"{state.department} {state.schedule_date} is approved by "
                    f"{out['approved_by'] or 'nobody named'} at "
                    f"{out['approved_at']} — the day is exportable from this "
                    "instant, and was not before"
                ),
                refs=[params.approval.source_ref],
            )
            ctx.event(
                "e1", DAY_SCHEDULED_EVENT,
                f"{state.department} {state.schedule_date}: "
                f"{len(filled.rows)} row(s), {filled.total_hours:,.1f} h, "
                f"incentive {filled.incentive_sentence}"
                + (f", {len(filled.deferred)} deferred"
                   if filled.deferred else ""),
            )

        return _outputs(
            state, params, filled, verdicts, written,
            outcome=outcome, rounds=rounds, decided_by=decided_by,
            decision_note=decision_note, approval=approval,
        )

    return body


# ---------------------------------------------------------------------------
# r2 and a1 — run once, and again after every correction
# ---------------------------------------------------------------------------


def _fill_step(
    ctx: RunContext,
    suffix: str,
    params: PlantParameters,
    department: str,
    candidates: dict[str, Candidate],
    placeable: list[Verdict],
    blocked: list[Verdict],
    limits: Limits,
    human_rows: list[Row],
    correction: tuple[list[str], list[str]] | None = None,
) -> Fill:
    """`r2`, over whatever is left. Pure, and re-run rather than patched.

    Re-run in full after a correction and not adjusted, for the extraction's
    reason: a supervisor who removes a job hands the day back *different*, and
    carrying the first fill's stopping point forward would hold a share for a
    limit the correction had just released.
    """

    def run() -> dict:
        result = fill(
            placeable, candidates, params,
            department=department, limits=limits, human_rows=human_rows,
        )
        if department == "loading":
            result = result.model_copy(update={
                "rows": consolidate_loading(
                    result.rows, candidates, params, department
                ),
            })
        return result.model_copy(update={
            "deferred": result.deferred + blocked,
        }).model_dump(mode="json")

    return Fill.model_validate(ctx.step(
        f"r2{suffix}", "fill-to-capacity", "rule",
        fn=run,
        summary=lambda out: _fill_summary(
            Fill.model_validate(out), correction
        ),
        refs=[params.source_ref],
    ))


def _write_step(
    ctx: RunContext,
    service,
    suffix: str,
    state: DayState,
    filled: Fill,
    params: PlantParameters,
    *,
    decided_by: str,
    queue: str = "",
    task_id: str = "",
) -> dict:
    """`a1`, the one write — and the day is `pending_approval` after it.

    The desk is empty on the first pass and is the Task's on every re-fill: no
    Task authorized the first proposal, so the Action's standing pin answers
    for it, and after a correction the queue that raised the work is the queue
    that commits it — which is what `queue_from_task` is for.
    """
    principal = ctx.principal or plant_flow_principal(queue)
    commit = service.gateway.action(
        "apply-schedule-day", principal,
        run=ctx.record, step_id=f"a1{suffix}",
        task_id=task_id,
        decided_by=decided_by,
        queue=queue,
    )
    return ctx.step(
        f"a1{suffix}", "apply-schedule-day", "action",
        fn=lambda: commit.commit(lambda writer: service.apply_schedule_day(
            state, filled, params, ctx.record.id, writer,
            decided_by=decided_by,
        )),
        writes=lambda _out: commit.writes,
        summary=lambda out: (
            f"{state.department} {state.schedule_date} written at "
            f"`pending_approval`: {len(out['rows'])} row(s), "
            f"{out['total_hours']:,.1f} h"
            + (
                f" · {len(out['withdrawn'])} row(s) a previous fill placed "
                "were withdrawn, not deleted"
                if out["withdrawn"] else ""
            )
            + " — nothing has left the graph, and the export refuses this "
            "day until it is approved"
        ),
        refs=[params.source_ref],
    )


def _apply_correction(
    filled: Fill, payload: dict, decided_by: str, dropped: set[str],
) -> tuple[list[Row], set[str], list[str], list[str]]:
    """The supervisor's edits, stamped as theirs. Everything else re-fills.

    Three shapes a correction can take, and each is the same act from a
    different angle: a row edited, a row removed, a row placed by hand. The
    first and third come back stamped `placed_by: human`, which is what the
    fill works around and what no later run may overwrite; the second takes
    the job out of the running, so the next fill does not simply put it back.

    A key this day does not carry is **refused and counted**, not created: a
    correction keyed on a row that is not on this day is a correction about a
    different day, and inventing the row would put work in a plant's day that
    nothing asked for.
    """
    by_key = {row.key: row for row in filled.rows}
    kept: dict[str, Row] = {
        row.key: row for row in filled.rows if row.placed_by == "human"
    }
    changed: list[str] = []
    refused: list[str] = []
    dropped = set(dropped)

    for key, patch in (payload.get("rows") or {}).items():
        row = by_key.get(key)
        if row is None:
            refused.append(key)
            continue
        update: dict = {"placed_by": "human"}
        for field in ("labor_hours", "job_label", "vehicle_ref", "row_kind"):
            if field in (patch or {}):
                update[field] = (
                    float(patch[field]) if field == "labor_hours"
                    else str(patch[field])
                )
        kept[key] = row.model_copy(update=update)
        changed.append(key)

    for key in (payload.get("removed") or []):
        row = by_key.get(key)
        if row is None:
            refused.append(key)
            continue
        kept.pop(key, None)
        if row.job_key:
            dropped.add(row.job_key)
        changed.append(key)

    for raw in (payload.get("added") or []):
        number = str((raw or {}).get("job_number") or "")
        if not number:
            refused.append("added:<no job number>")
            continue
        key = str(raw.get("key") or number)
        kept[key] = Row(
            key=key,
            job_key=str(raw.get("job_key") or ""),
            job_id=str(raw.get("job_id") or ""),
            job_number=number,
            job_label=str(raw.get("job_label") or number),
            labor_hours=float(raw.get("labor_hours") or 0.0),
            basis=str(
                raw.get("basis")
                or f"placed by {decided_by or 'a supervisor'} on review"
            ),
            row_kind=str(raw.get("row_kind") or "standard"),
            vehicle_ref=str(raw.get("vehicle_ref") or ""),
            placed_by="human",
        )
        if raw.get("job_key"):
            dropped.add(str(raw["job_key"]))
        changed.append(f"added:{number}")

    return list(kept.values()), dropped, changed, refused


# ---------------------------------------------------------------------------
# The endings that are not an approval
# ---------------------------------------------------------------------------


def _settled(state: DayState, params: PlantParameters, reason: str) -> dict:
    """A day this run must not touch. No fill, no write, no Task."""
    return {
        "kind": "department-scheduling",
        "department": state.department,
        "schedule_date": state.schedule_date,
        "day_key": state.day_key,
        "state": "settled",
        "status": state.status,
        "reason": reason,
        "wrote": False,
        "rows": 0,
        "total_hours": 0.0,
        "deferred": 0,
        "parameters_ref": params.source_ref,
        "open_task_id": state.open_task_id,
    }


def _deferred(
    state: DayState,
    params: PlantParameters,
    filled: Fill,
    verdicts: list[Verdict],
) -> dict:
    """Nothing could be placed. Deferred with reasons, and nobody is asked."""
    return {
        "kind": "department-scheduling",
        "department": state.department,
        "schedule_date": state.schedule_date,
        "day_key": state.day_key,
        "state": "deferred",
        "status": state.status,
        "reason": (
            "no share of this department's work could be placed on this day, "
            "so no schedule was generated and nobody was asked to approve "
            "one — the next eligibility sweep re-raises the day"
        ),
        "wrote": False,
        "rows": 0,
        "total_hours": 0.0,
        "deferred": len(filled.deferred),
        "deferrals": deferrals(filled.deferred),
        "reasons": [
            {"job_number": row.job_number, "clause": row.clause,
             "reason": row.reason}
            for row in filled.deferred
        ],
        "considered": len(verdicts),
        "capacity_row": filled.limits.row,
        "parameters_ref": params.source_ref,
    }


def _outputs(
    state: DayState,
    params: PlantParameters,
    filled: Fill,
    verdicts: list[Verdict],
    written: dict,
    *,
    outcome: str,
    rounds: int,
    decided_by: str,
    decision_note: str,
    approval: dict,
) -> dict:
    return {
        "kind": "department-scheduling",
        "department": state.department,
        "schedule_date": state.schedule_date,
        "day_key": state.day_key,
        "day_ref": written.get("day_ref", ""),
        "state": outcome,
        "status": approval.get("status") or "pending_approval",
        "wrote": True,
        "rows": len(filled.rows),
        "row_kinds": sorted({row.row_kind for row in filled.rows}),
        "human_rows": len([
            row for row in filled.rows if row.placed_by == "human"
        ]),
        "total_hours": filled.total_hours,
        "capacity_row": filled.limits.row,
        "capacity_row_note": filled.limits.row_note,
        "goal_max_hours": filled.limits.goal_max,
        "abs_max_hours": filled.limits.absolute_max,
        "fudge_hours": filled.limits.fudge,
        "stopped_at": filled.stopped_at,
        "incentive_per_hour": filled.incentive_per_hour,
        "incentive_sentence": filled.incentive_sentence,
        "out_of_the_hole": filled.out_of_the_hole,
        "below_capacity": filled.below_capacity,
        "headcount": state.headcount,
        "headcount_as_of": state.headcount_as_of,
        "headcount_stale": state.headcount_stale,
        "considered": len(verdicts),
        "deferred": len(filled.deferred),
        "deferrals": deferrals(filled.deferred),
        "review_rounds": rounds,
        "decided_by": decided_by,
        "decision_note": decision_note,
        "approved_by": approval.get("approved_by", ""),
        "approved_at": approval.get("approved_at", ""),
        "parameters_ref": params.source_ref,
    }


# ---------------------------------------------------------------------------
# Summaries — what a reader of the journal actually sees
# ---------------------------------------------------------------------------


def _as_of(inputs: dict) -> date:
    """The run's own day, pinnable by a fixture.

    `date.today()` is the production answer; a Rule whose verdict moved because
    the suite ran past midnight would be a Rule nobody could grade.
    """
    raw = str(inputs.get("as_of") or "")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return date.today()


def _iso(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return fallback


def _day_summary(state: DayState) -> str:
    return (
        (
            f"the graph already holds this day at `{state.status}` with "
            f"{len(state.rows)} row(s), {len(state.human_rows)} of them "
            "placed by a supervisor and never overwritten"
            if state.exists else
            "no day in the graph for this department and date yet"
        )
        + f" · headcount {state.headcount:g}"
        + (" (stale)" if state.headcount_stale else "")
        + f" — {state.headcount_note}"
        + (
            f" · an approval Task ({state.open_task_id}) is already open"
            if state.open_task_id else ""
        )
    )


def _candidates_summary(
    out: dict, params: PlantParameters, department: str
) -> str:
    rows = out["candidates"]
    if not rows:
        return (
            f"no `JobTask` row in the graph owes `{department}` anything — "
            "either no extraction has produced demand for it, or it has none"
        )
    hours = sum(float(row.get("labor_hours") or 0.0) for row in rows)
    return (
        f"{len(rows)} share(s) totalling {hours:,.1f} h of demand for "
        f"`{department}`, {params.eligibility.sort_by or 'unsorted'}"
    )


def _rule_summary(verdicts: list[Verdict]) -> str:
    placeable = [verdict for verdict in verdicts if verdict.placeable]
    counts = deferrals(verdicts)
    if not verdicts:
        return "no share to judge"
    return (
        f"{len(placeable)} of {len(verdicts)} share(s) clear every clause"
        + (
            " · deferred: " + ", ".join(
                f"{clause} ×{count}" for clause, count in counts.items()
            ) if counts else ""
        )
    )


def _fill_summary(
    filled: Fill, correction: tuple[list[str], list[str]] | None
) -> str:
    limits = filled.limits
    parts = [
        f"{len(filled.rows)} row(s), {filled.total_hours:,.1f} h against "
        f"{limits.row or 'no governed capacity row'}"
        + (f" (goal {limits.goal_max:g} h)"
           if limits.goal_max is not None else "")
        + (f", absolute max {limits.absolute_max:g} h"
           if limits.absolute_max is not None else "")
        + (f", fudge {limits.fudge:g} h" if limits.fudge else ""),
        limits.row_note,
        filled.stopped_at,
        f"incentive {filled.incentive_sentence}"
        + (" — out of the hole" if filled.out_of_the_hole
           else " — still in the hole"),
    ]
    if filled.human_hours:
        parts.append(
            f"{filled.human_hours:,.1f} h of that was already on the day, "
            "placed by a supervisor and worked around"
        )
    if filled.below_capacity:
        parts.append(
            "below the governed minimum — reported, never forced"
        )
    if filled.deferred:
        parts.append(f"{len(filled.deferred)} share(s) deferred")
    if correction is not None:
        changed, refused = correction
        parts.append(
            "re-filled after a correction to "
            + (', '.join(changed) or 'nothing')
            + (f" · refused {', '.join(refused)} — no such row on this day"
               if refused else "")
        )
    return " · ".join(part for part in parts if part)
