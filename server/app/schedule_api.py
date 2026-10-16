"""The plant schedule, handed to the customer as a file they own.

`GET /api/v1/schedule/export?week=YYYY-Www&department=<slug>` → `text/csv`.

**A Tier-1 REST route and deliberately not a console BFF endpoint.** The
console's plant-schedule screen is one way to read a day; this is the other,
and it exists because a portable export is a platform capability rather than a
screen's concern. A customer must be able to pull their own approved schedule
with a script they wrote and keep the file — which is the honest replacement
for the workbook this whole flow retired, and the answer to "what happens if
the deployment goes dark".

**A derived Dataset, regenerated on request and stored nowhere.** *Sources are
synced; derivatives are regenerated.* The header names the derivation the way
the doctrine asks a derived Dataset to name it — where it came from, the
recipe, the run that produced it, who approved it and when — so the file can be
traced back to a `ScheduleDay` revision and a run journal months later. Keeping
a copy would be a snapshot of a graph we already hold, going stale the moment a
supervisor adjusts a row, and nothing here writes: a GET must not, and there is
no declared Action for it if it wanted to.

**Only an `approved` day exports.** A day at `pending_approval` answers **409**
naming the open Task, because an export a supervisor has not signed reads to
whoever opens it as a schedule. That refusal is the enforcement half of the
scheduling flow's central claim — nothing leaves the graph before approval —
and it is why the flow writes the day *before* it asks: the record exists, and
the door onto it stays shut.

**`user`+, and deliberately not queue-gated.** Reading is not deciding, and
handing a customer their own approved schedule is a read. The decision happened
at the approval Task, through `require_queue`, and is already on the ledger.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from . import deps
from .auth import Principal, get_principal, require_reader
from .graph import ObjectRecord, object_id
from .models import utcnow
from .ontology import names as onto
from .tasks.department_scheduling.domain import FLOW_ID, day_key

log = logging.getLogger("cumulus.schedule")

router = APIRouter(prefix="/api/v1/schedule", tags=["schedule"])

#: The recipe name on every export's header. A derived Dataset names the recipe
#: that produced it, and a reader holding a file from six months ago needs to
#: know which one — a column set that changes is a new recipe, not a silent
#: edit of this one.
RECIPE = "schedule-export-v1"

#: The columns, in order. One row per `ScheduleEntry`, plus the date, because a
#: week holds several days and a row that could not say which day it belonged
#: to would be unusable in exactly the file this replaces.
COLUMNS = (
    "date", "job_number", "job_label", "labor_hours", "basis", "sequence",
    "row_kind", "vehicle_ref", "placed_by",
)

APPROVED = "approved"


@router.get("/export")
def export_schedule(
    week: str = Query(
        description="ISO week, `YYYY-Www` — the week the days fall in",
    ),
    department: str = Query(
        description="The department's Cumulus slug, e.g. `truss-fab`",
    ),
    principal: Principal = Depends(get_principal),
) -> Response:
    """One week of one department's approved days, as CSV.

    404 when the week holds no day for this department at all — which is a
    different fact from a day that exists and is not signed, and the caller
    should be able to tell them apart. 409 when any day in the week is not
    approved, naming the Task that is holding it.
    """
    require_reader(principal)
    dates = week_dates(week)
    graph = deps.get_graph_service()

    days = [
        record for record in (
            graph.get(
                object_id(onto.SCHEDULE_DAY, day_key(department, day)),
                follow=False,
            )
            for day in dates
        )
        if record is not None
    ]
    if not days:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No schedule day exists for `{department}` in {week}. "
                "Either no window has opened a day for this department that "
                "week, or every candidate share was deferred — the run "
                "journal says which."
            ),
        )

    unapproved = [
        record for record in days
        if str(record.props.get("status") or "") != APPROVED
    ]
    if unapproved:
        raise HTTPException(status_code=409, detail=_refusal(
            week, department, unapproved
        ))

    body = _csv(department, days, graph)
    log.info(
        "Schedule exported",
        extra={
            "uid": principal.uid, "department": department, "week": week,
            "days": len(days),
        },
    )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="schedule-{department}-{week}.csv"'
            ),
            # Regenerated on request and stored nowhere — including in
            # somebody's cache, where it would go stale the moment a
            # supervisor adjusts a row.
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------


def _refusal(
    week: str, department: str, unapproved: list[ObjectRecord]
) -> dict:
    """409, naming the Task. The enforcement half of the flow's own claim."""
    service = deps.get_department_scheduling_service()
    tasks = deps.get_task_service()
    rows = []
    for record in unapproved:
        day = str(record.props.get("schedule_date") or "")
        task_id = service.open_approval_task_id(day_key(department, day))
        task = tasks.get(task_id) if task_id else None
        rows.append({
            "date": day,
            "status": str(record.props.get("status") or "unset"),
            "task_id": task_id,
            # The desk off the Task rather than a constant here: the governed
            # approval table decides where a day's review lands, and a refusal
            # that named a desk of its own would be a second answer to it.
            "queue": task.queue if task is not None else "",
            "ref": record.ref(),
        })
    named = ", ".join(
        f"{row['date']} ({row['status']}"
        + (f", task {row['task_id']}" if row["task_id"] else ", no open task")
        + ")"
        for row in rows
    )
    return {
        "error": "not-approved",
        "week": week,
        "department": department,
        "days": rows,
        "message": (
            f"{len(rows)} day(s) in {week} for `{department}` have not been "
            f"approved: {named}. A plant schedule is exported only once a "
            "supervisor has signed it — an export nobody signed reads to "
            "whoever opens it as a schedule. Approve the Task named above and "
            "this answers."
        ),
    }


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def _csv(department: str, days: list[ObjectRecord], graph) -> str:
    """The header naming the derivation, then one row per `ScheduleEntry`."""
    out = io.StringIO()
    generated = utcnow().isoformat()
    out.write(f"# cumulus schedule export · recipe: {RECIPE}\n")
    out.write(f"# department: {department}\n")
    out.write(f"# generated_at: {generated}\n")
    out.write(
        "# stored: nowhere — this is a derived Dataset, regenerated on "
        "request from the graph that holds the schedule\n"
    )
    for record in sorted(
        days, key=lambda item: str(item.props.get("schedule_date") or "")
    ):
        props = record.props
        out.write(
            f"# day: {props.get('schedule_date', '')}"
            f" · from: {record.ref()}"
            f" · produced_by: {_produced_by(record) or 'unknown'}"
            f" · approved_by: {props.get('approved_by', '') or 'unknown'}"
            f" · approved_at: {props.get('approved_at', '') or 'unknown'}"
            f" · total_hours: {props.get('total_hours', '')}"
            f" · incentive_per_hour: {props.get('incentive_per_hour', '')}\n"
        )
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for record in sorted(
        days, key=lambda item: str(item.props.get("schedule_date") or "")
    ):
        day = str(record.props.get("schedule_date") or "")
        for entry in _entries(graph, record.id):
            props = entry.props
            writer.writerow([
                day,
                props.get("job_number", ""),
                props.get("job_label", ""),
                _number(props.get("labor_hours")),
                props.get("basis", ""),
                _number(props.get("sequence")),
                props.get("row_kind", ""),
                props.get("vehicle_ref", ""),
                props.get("placed_by", ""),
            ])
    return out.getvalue()


def _entries(graph, day_id: str) -> list[ObjectRecord]:
    """The day's live rows, in sequence.

    Live, so a row a later fill withdrew is not exported: the edge ended its
    validity and the row keeps its record, which is the graph's own answer to
    "what was this department told to run last Tuesday" without putting a
    retracted row in front of a crew this Tuesday.
    """
    rows = [
        entry for _link, entry in graph.neighbors(
            day_id, "has_schedule_entry", "out"
        )
    ]
    rows.sort(key=lambda entry: (
        float(entry.props.get("sequence") or 0.0), entry.natural_key
    ))
    return rows


def _produced_by(record: ObjectRecord) -> str:
    """The run that wrote this day, off the claims it wrote.

    Read from the record rather than kept in a second field, for the reason
    every derivation here is: a Dataset's `producedBy` has to point at
    something an auditor can open, and the claim already carries the run and
    the step that made it.
    """
    for claim in reversed(record.claims):
        if claim.run_id:
            return claim.run_id
    return ""


def _number(value) -> str:
    """A figure as the file should carry it: plain, and never `None`."""
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def week_dates(week: str) -> list[date]:
    """The seven dates of an ISO week, or a 422 saying what the shape is."""
    raw = (week or "").strip().upper()
    try:
        year, _, number = raw.partition("-W")
        return [
            date.fromisocalendar(int(year), int(number), day)
            for day in range(1, 8)
        ]
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=422,
            detail=(
                f"`week` must be an ISO week of the form `YYYY-Www` "
                f"(for example `2026-W37`); got `{week}`."
            ),
        ) from None


#: Public because the console's plant-schedule BFF parses the same `week`
#: parameter and must refuse a malformed one with the same 422 sentence. A
#: second parser there would be a second answer to "what shape is a week".
_week_dates = week_dates

__all__ = ["COLUMNS", "FLOW_ID", "RECIPE", "router", "week_dates"]
