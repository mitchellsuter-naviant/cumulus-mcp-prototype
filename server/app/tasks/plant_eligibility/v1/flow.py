"""Plant eligibility sweep, v1 — the window that decides what is in play.

The executable body and the declarative steps travel together, for the reason
`accounts_payable/v6` gives: the map the console draws is this module's `STEPS`
and the engine runs this module's body, so the two cannot drift apart without a
diff in one file saying so. `test_plant_eligibility.py` walks a real journal and
asserts the two are the same set, in the same order.

Seven steps, and the order of the last two is load-bearing. `e1` raises one
`JobEligible` per eligible job and each of those runs a whole extraction
synchronously; `e2` then opens a department-day for every department the
extractions gave demand rows to. Reversing them would fill a day out of a graph
the extractions had not written yet.
"""

from __future__ import annotations

from datetime import date

from ....flows import FlowStep
from ....runs import RunContext
from ...department_scheduling.parameters import PlantParameters
from ..domain import (
    DEPARTMENT_DAY_EVENT,
    JOB_ELIGIBLE_EVENT,
    PLANT_WINDOW_EVENT,
    POLICY_ASSET_ID,
    Candidate,
    Verdict,
    exclusions,
    judge,
)

VERSION = 1
LABEL = "v1"
NOTES = (
    "One pass over the job book, once per plant window. It reads the governed "
    "eligibility clauses, the business-day calendar, the department roster and "
    "the window bound; asks the graph for every job the production system "
    "holds, load date ascending; and judges each one against every clause — "
    "load date on or after today, a week position in the governed set, "
    "released, not held, and a load date the plant is actually open on. A job "
    "that fails a clause is excluded for **that** named reason and counted; "
    "an eligible set of zero raises nothing, because the governed page says a "
    "slow week is informational. The one Task it can raise is the aggregate "
    "for a job book that is **empty**, which is a feed that stopped rather "
    "than a quiet plant, and it goes to the operations desk. Then one "
    "`JobEligible` per eligible job inside the governed bound — each answered "
    "by a full extraction before the window is acknowledged, with the "
    "remainder reported as deferred rather than dropped — and, after them, one "
    "`DepartmentDayOpened` per department and business day the extractions "
    "actually produced demand for — a fact **nothing is bound to yet**, so the "
    "response to it lands with the department-scheduling flow and until then "
    "the Dispatcher logs it and starts nothing. It writes nothing itself: the "
    "verdict is the journal, and every graph write in the plant spine belongs "
    "to a later flow."
)


STEPS: list[FlowStep] = [
    FlowStep(
        id="t1", type="trigger", name=PLANT_WINDOW_EVENT,
        summary=(
            "one run per plant window — the 11:00 CST pass over the whole job "
            "book. Nothing arrived: a window closed over rows `job-sync` has "
            "already landed, which is why this is windowed and not a delivery"
        ),
        config={"event": PLANT_WINDOW_EVENT}, next=["x1"],
    ),
    FlowStep(
        id="x1", type="transform", name="read-plant-parameters",
        summary=(
            "the governed values in one step, at one revision: the eligibility "
            "clauses, the business-day calendar and its holidays, the ten "
            "departments in their three spellings, the `## Source vocabulary` "
            "table the clauses resolve against, the jobs-per-window bound, and "
            "the lead times the department-days are counted back with"
        ),
        config={"source": f"knowledge:{POLICY_ASSET_ID}"}, next=["x2"],
    ),
    FlowStep(
        id="x2", type="transform", name="select-candidate-jobs",
        summary=(
            "the standing question, asked of the graph rather than of a "
            "delivery: every `Job` the production system holds, sorted by load "
            "date ascending as the governed page asks. A job with no load date "
            "sorts last, because a date nobody wrote cannot be earlier than "
            "one somebody did"
        ),
        config={"source": "graph:Job"}, next=["r1"],
    ),
    FlowStep(
        id="r1", type="rule", name="first-pass-eligibility",
        summary=(
            "pure, per job, in the page's own clause order: load date on or "
            "after today; week position in the governed set; released, in the "
            "words `job-sync` already translated; not held; and a load date "
            "the plant is open on. The verdict and the **one** clause that "
            "excluded it are recorded per job, so an exclusion count is a "
            "count of reasons rather than of opinions"
        ),
        config={"rule_type": "match-condition",
                "source": f"knowledge:{POLICY_ASSET_ID}"},
        next=["k1"],
    ),
    FlowStep(
        id="k1", type="task", name="report-plant-fault",
        summary=(
            "the aggregate, and the only Task this flow raises: **no** `Job` "
            "rows at all, which means the feed stopped. An empty *eligible* "
            "set is informational and raises nothing — the governed page says "
            "so, and a slow week is not a fault"
        ),
        config={"queue": "platform-operations",
                "raised_when": "the job book is empty"},
        next=["e1"],
    ),
    FlowStep(
        id="e1", type="event", name=JOB_ELIGIBLE_EVENT,
        summary=(
            "one fact per eligible job, inside the governed jobs-per-window "
            "bound. Each is answered by a full extraction — a multi-page model "
            "read — synchronously, so the cost is named where it is paid: the "
            "window is not acknowledged until every case it raised has run or "
            "suspended. What the bound did not take on is still eligible and "
            "is reported as deferred"
        ),
        config={"event": JOB_ELIGIBLE_EVENT, "starts": "shipping-list-extraction"},
        next=["e2"],
    ),
    FlowStep(
        id="e2", type="event", name=DEPARTMENT_DAY_EVENT,
        summary=(
            "**after** `e1`, so every extraction has run or suspended before "
            "any day is filled: one fact per (department, business day) the "
            "demand rows in the graph now call for, dated by the governed lead "
            "time counted back from the load date. A day whose lead time had "
            "already passed opens on the first business day available and says "
            "it was late"
        ),
        config={"event": DEPARTMENT_DAY_EVENT, "starts": "department-scheduling"},
    ),
]


def build_body(service):
    """Bind the eligibility service and return the engine-runnable body."""

    def body(ctx: RunContext, inputs: dict) -> dict:
        # The window's own day, so a replayed run judges the same clauses it
        # judged the first time. `date.today()` is the production answer and
        # the input is what a fixture pins — a Rule whose verdict moved because
        # the suite ran past midnight would be a Rule nobody could grade.
        as_of = _as_of(inputs)
        ctx.event(
            "t1", PLANT_WINDOW_EVENT,
            f"a plant window closed on {as_of.isoformat()}",
            kind="trigger",
        )

        # -- x1: the governed read -----------------------------------------
        #
        # Read once, outside the step, and used for both the output and the
        # read-set: two reads could name two revisions of one page in the run
        # where somebody republishes mid-window, and a run whose clauses came
        # from one revision and whose journal cites another is a decision
        # nobody can reproduce.
        governed = service.read_parameters()
        params = PlantParameters.model_validate(ctx.step(
            "x1", "read-plant-parameters", "transform",
            fn=lambda: _reads(governed).model_dump(mode="json"),
            summary=lambda out: _reads_summary(
                PlantParameters.model_validate(out)
            ),
            refs=[governed.source_ref, governed.vocabulary.source_ref],
        ))
        ctx.note_refs(params.source_ref, params.vocabulary.source_ref)

        # -- x2: the standing question -------------------------------------
        selection = ctx.step(
            "x2", "select-candidate-jobs", "transform",
            fn=lambda: {
                "candidates": [
                    candidate.model_dump(mode="json")
                    for candidate in service.candidates(params)
                ],
            },
            summary=lambda out: (
                f"{len(out['candidates'])} job(s) in the graph, "
                f"{params.eligibility.sort_by or 'unsorted'}"
                if out["candidates"] else
                "the graph holds no jobs at all — which is a feed, not a "
                "quiet plant"
            ),
            refs=["graph:Job"],
        )
        candidates = {
            candidate.job_key: candidate
            for candidate in (
                Candidate.model_validate(row) for row in selection["candidates"]
            )
        }

        # -- r1: the Rule ---------------------------------------------------
        judged = ctx.step(
            "r1", "first-pass-eligibility", "rule",
            fn=lambda: [
                verdict.model_dump(mode="json")
                for verdict in judge(list(candidates.values()), params, as_of)
            ],
            summary=lambda out: _rule_summary(
                [Verdict.model_validate(row) for row in out], params
            ),
            refs=[params.source_ref],
        )
        verdicts = [Verdict.model_validate(row) for row in judged]
        eligible = [verdict for verdict in verdicts if verdict.eligible]
        limit = params.eligibility.jobs_per_window
        taken = eligible if limit is None else eligible[: max(limit, 0)]
        deferred = len(eligible) - len(taken)

        # -- k1: the aggregate, when there is one --------------------------
        #
        # Declared as a `task` step and recorded as a `sync` one, which is
        # `ap_intake`'s precedent and deliberate: a `task` row in a journal is
        # a *gate* — `runs_api` and the eval harness both read it as one — and
        # this flow never suspends. What the map promises is that a Task can be
        # raised here, which is exactly what happens.
        ctx.step(
            "k1", "report-plant-fault", "sync",
            fn=lambda: {
                "task_id": (
                    service.raise_plant_fault(ctx, params)
                    if not candidates else ""
                ),
            },
            summary=lambda out: (
                f"operations Task {out['task_id']} — the job book is empty"
                if out["task_id"] else
                f"{len(eligible)} job(s) eligible, "
                f"{len(verdicts) - len(eligible)} excluded — nothing to "
                "report, so nobody to ask"
            ),
        )

        # -- e1: the batch becomes cases ------------------------------------
        raised = ctx.step(
            "e1", JOB_ELIGIBLE_EVENT, "event",
            fn=lambda: {
                **service.raise_eligible(candidates, taken),
                "deferred": deferred,
            },
            summary=lambda out: (
                f"{out['raised']} job(s) raised · "
                f"{len(out['succeeded'])} extraction(s) succeeded, "
                f"{len(out['suspended'])} suspended for a supervisor, "
                f"{len(out['failed'])} failed"
                + (
                    f" · {out['deferred']} eligible job(s) left for the next "
                    "window by the governed bound" if out["deferred"] else ""
                )
                if out["raised"] else
                "nothing eligible this window, which the governed page calls "
                f"{params.eligibility.empty_set_is or 'informational'} — no "
                "Task, no alarm"
            ),
        )

        # -- e2: the days those extractions made real ----------------------
        opened = ctx.step(
            "e2", DEPARTMENT_DAY_EVENT, "event",
            fn=lambda: _open_days(service, candidates, taken, params, as_of, ctx),
            summary=lambda out: (
                f"{out['opened']} department-day(s) opened: "
                + ", ".join(
                    f"{day['department']} {day['schedule_date']}"
                    + (" (late)" if day["late"] else "")
                    for day in out["days"][:6]
                )
                + (" …" if len(out["days"]) > 6 else "")
                + (
                    f" · {len(out['runs'])} scheduling run(s) started"
                    if out["runs"] else
                    " — nothing is bound to this fact yet, so the response "
                    "lands with the department-scheduling flow"
                )
                if out["opened"] else
                "no department has demand rows to fill a day with"
            ),
        )

        return {
            "kind": "plant-eligibility",
            "as_of": as_of.isoformat(),
            "parameters_ref": params.source_ref,
            "considered": len(candidates),
            "eligible": len(eligible),
            "excluded": len(verdicts) - len(eligible),
            "exclusions": exclusions(verdicts),
            "bound": limit,
            "taken": len(taken),
            "deferred": deferred,
            "raised": raised["raised"],
            "extractions_succeeded": len(raised["succeeded"]),
            "extractions_suspended": len(raised["suspended"]),
            "extractions_failed": len(raised["failed"]),
            "runs": [row["run_id"] for row in raised["succeeded"]]
                    + [row["run_id"] for row in raised["suspended"]],
            "failed_runs": [row["run_id"] for row in raised["failed"]],
            "department_days": opened["opened"],
            "days": opened["days"],
            "days_deferred": opened["deferred"],
        }

    return body


# ---------------------------------------------------------------------------
# The pieces the body reads
# ---------------------------------------------------------------------------


def _as_of(inputs: dict) -> date:
    raw = str(inputs.get("as_of") or "")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return date.today()


def _reads(params: PlantParameters) -> PlantParameters:
    """The sections this flow consumes, and only those.

    The step's output *is* the read-set, and a step that dumped sixteen
    sections it never consulted would claim a read that did not happen — as
    well as putting the whole capacity table in every window's journal. The
    same page's capacity, headcount, sequencing and row-shaping sections are
    read by the flow that fills a day, in that flow's own step.
    """
    return PlantParameters(
        eligibility=params.eligibility,
        calendar=params.calendar,
        departments=params.departments,
        vocabulary=params.vocabulary,
        lead_times=params.lead_times,
        source_ref=params.source_ref,
        found_asset=params.found_asset,
    )


def _reads_summary(params: PlantParameters) -> str:
    eligibility = params.eligibility
    positions = ", ".join(str(value) for value in eligibility.week_positions)
    bound = (
        "no governed bound" if eligibility.jobs_per_window is None
        else f"up to {eligibility.jobs_per_window} job(s) per window"
    )
    return (
        f"{params.source_ref}: load date ≥ "
        f"{eligibility.load_date_on_or_after or 'today'} · week position in "
        f"{positions or 'any'} · release_state = "
        f"`{eligibility.released_state or 'unset'}` · no hold · "
        f"{len(params.calendar.business_weekdays)} business weekday(s) and "
        f"{len(params.calendar.holidays)} holiday(s) · "
        f"{len(params.departments)} department(s) · {bound}"
        + ("" if params.found_asset
           else " — the page is not in the corpus, so this window ran on "
                "built-in behaviour")
        + ("" if params.vocabulary.found_table
           else " — no source-vocabulary table on the page, so the clauses "
                "resolved against the built-in words")
    )


def _rule_summary(verdicts: list[Verdict], params: PlantParameters) -> str:
    eligible = [verdict for verdict in verdicts if verdict.eligible]
    counts = exclusions(verdicts)
    if not verdicts:
        return "no candidate to judge"
    return (
        f"{len(eligible)} of {len(verdicts)} job(s) clear every clause"
        + (
            " · excluded: " + ", ".join(
                f"{clause} ×{count}" for clause, count in counts.items()
            ) if counts else ""
        )
    )


def _open_days(
    service, candidates: dict, taken: list[Verdict],
    params: PlantParameters, as_of: date, ctx: RunContext,
) -> dict:
    days, deferred = service.department_days(candidates, taken, params, as_of)
    emitted = service.open_department_days(days, ctx.record.id)
    return {
        "opened": emitted["opened"],
        "runs": emitted["runs"],
        "days": [day.model_dump(mode="json") for day in days],
        "deferred": deferred,
    }
