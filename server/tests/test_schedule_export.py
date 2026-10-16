"""The export: the file that replaces the workbook, and its door.

This is the enforcement half of the scheduling flow's central claim. The flow
writes a day at `pending_approval` and suspends; **the export is what makes
that status mean something**, because a schedule a supervisor has not signed
must not reach the floor. So:

- a `pending_approval` day answers **409** and names the open Task, so whoever
  asked knows exactly what has to happen before it will answer;
- an `approved` day answers **200**, one row per `ScheduleEntry`, with a header
  naming the derivation — where it came from, the recipe, the run that produced
  it, who approved it and when;
- and it **stores nothing**. Per the doctrine a derived Dataset is regenerated
  on request: a snapshot of a graph we already hold goes stale the moment a
  supervisor adjusts a row, so nothing is written, nothing is cached, and no
  ledger entry is appended by a read.

It is `user`+ and deliberately **not** queue-gated: handing a customer their
own approved schedule is a read, and the decision that authorized it already
happened at the Task and is already on the ledger.
"""

import pytest

from app import personas
from app.graph import object_id
from app.ontology import names as onto
from app.schedule_api import COLUMNS, RECIPE
from app.tasks.department_scheduling import domain
from tests import world as test_world
from tests.test_department_scheduling import (
    SUPERVISOR,
    _decide,
    open_day,
    steel_day,
)

ENGINEER = {"X-Cumulus-Persona": "naviant-engineer"}
#: An own-scoped customer persona holding no queue at all — the one that proves
#: this route is a read rather than the supervisor's desk in disguise.
NO_QUEUES = {"X-Cumulus-Persona": personas.get("customer-user:ae").id}


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


def _week(day) -> str:
    year, number, _weekday = day.isocalendar()
    return f"{year}-W{number:02d}"


def _get(client, day, headers=None, department="steel-fab"):
    return client.get(
        "/api/v1/schedule/export",
        params={"week": _week(day), "department": department},
        headers=headers or ENGINEER,
    )


def _rows(text: str) -> list[list[str]]:
    """The data rows: everything after the comment header and the columns."""
    lines = [
        line for line in text.splitlines()
        if line and not line.startswith("#")
    ]
    return [line.split(",") for line in lines[1:]]


# ---- the central claim ------------------------------------------------------


def test_a_pending_day_refuses_and_an_approved_one_answers(client, world):
    """Nothing leaves the graph before approval. The whole flow, in one test.

    At the moment the Task is raised the day exists and is complete — rows,
    hours, incentive, the lot — and the export still refuses it, naming the
    Task somebody has to answer. The signature is the only thing that changes,
    and after it the same request answers 200 with the same rows.
    """
    day = steel_day(world)
    run = open_day(world, "steel-fab", day)
    task_id = run.open_task_id

    refused = _get(client, day)
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["error"] == "not-approved"
    assert detail["department"] == "steel-fab"
    assert [row["date"] for row in detail["days"]] == [day.isoformat()]
    assert detail["days"][0]["status"] == "pending_approval"
    assert detail["days"][0]["task_id"] == task_id
    assert detail["days"][0]["queue"] == "plant-supervisor"
    assert "supervisor has signed it" in detail["message"]
    assert task_id in detail["message"]

    _decide(world, run, "approved", note="Signed.")

    answered = _get(client, day)
    assert answered.status_code == 200
    assert answered.headers["content-type"].startswith("text/csv")
    assert [row[1] for row in _rows(answered.text)] == ["250600", "250601"]


def test_a_day_the_flow_could_not_fill_never_reaches_the_export(client, world):
    """A department whose dependency is unmet writes no day, so there is
    nothing to refuse and nothing to hand out — a 404 rather than a 409, and
    the two are different facts a caller should be able to tell apart."""
    day = steel_day(world)

    answer = _get(client, day, department="truss-fab")

    assert answer.status_code == 404
    assert "No schedule day exists for `truss-fab`" in answer.json()["detail"]


# ---- the file ---------------------------------------------------------------


def _approved(world):
    day = steel_day(world)
    run = open_day(world, "steel-fab", day)
    run = _decide(world, run, "corrected", note="Six hours.",
                  payload={"rows": {"250600": {"labor_hours": 6.0}}})
    run = _decide(world, run, "approved", note="Signed.")
    return day, run


def test_the_header_names_the_derivation(client, world):
    """A derived Dataset says where it came from, or it is just a file.

    `{from, recipe, producedBy}` plus the approval, so a CSV somebody has kept
    for six months can be traced back to a `ScheduleDay` revision and a run
    journal — and so a reader can tell which recipe produced these columns.
    """
    day, run = _approved(world)

    text = _get(client, day).text
    header = [line for line in text.splitlines() if line.startswith("#")]
    blob = "\n".join(header)

    assert f"recipe: {RECIPE}" in blob
    assert "department: steel-fab" in blob
    assert f"day: {day.isoformat()}" in blob
    assert "from: graph:ScheduleDay/" in blob
    assert f"produced_by: {run.id}" in blob
    assert f"approved_by: {SUPERVISOR}" in blob
    assert "approved_at: 20" in blob
    assert "total_hours: 19.0" in blob
    assert "regenerated on request" in blob


def test_one_row_per_schedule_entry_with_the_columns_the_contract_names(
    client, world
):
    """Job number, label, hours, basis, sequence, row kind, vehicle, placed_by.

    Plus the date, because a week holds several days and a row that could not
    say which day it belonged to would be unusable in exactly the file this
    replaces. And `placed_by` travels, so a crew reading the printed day can
    see which rows a supervisor put there by hand.
    """
    day, _run = _approved(world)

    text = _get(client, day).text
    columns = [
        line for line in text.splitlines() if not line.startswith("#")
    ][0]
    rows = _rows(text)

    assert columns.split(",") == list(COLUMNS)
    assert len(rows) == 3
    assert [row[1] for row in rows] == ["250600", "250601", "250602"]
    assert [row[2] for row in rows] == [
        "250600 SF", "250601 SF", "250602 SF",
    ]
    assert [row[3] for row in rows] == ["6", "8", "5"]
    assert [row[5] for row in rows] == ["1", "2", "3"]
    assert [row[6] for row in rows] == ["standard"] * 3
    assert [row[8] for row in rows] == ["human", "flow", "flow"]
    assert all(row[0] == day.isoformat() for row in rows)


def test_the_export_stores_nothing(client, world):
    """*"Sources are synced; derivatives are regenerated."*

    A GET must not write, and there is no declared Action it could write
    through if it wanted to. So: the day's revision does not move, no object is
    minted, no ledger entry is appended, and the response asks not to be
    cached — a copy in somebody's proxy would be a snapshot going stale the
    moment a supervisor adjusts a row.
    """
    day, _run = _approved(world)
    record = world.graph.get(
        object_id(
            onto.SCHEDULE_DAY, domain.day_key("steel-fab", day.isoformat())
        ),
        follow=False,
    )
    before = (record.revision, len(world.graph.store.objects()),
              len(world.gateway.ledger.list()))

    first = _get(client, day)
    second = _get(client, day)

    after = world.graph.get(record.id, follow=False)
    assert first.status_code == second.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert "attachment" in first.headers["content-disposition"]
    assert f"schedule-steel-fab-{_week(day)}.csv" in (
        first.headers["content-disposition"]
    )
    assert (after.revision, len(world.graph.store.objects()),
            len(world.gateway.ledger.list())) == before
    assert "exported_at" not in after.props
    # Regenerated, and the same both times bar the moment it was generated.
    assert _rows(first.text) == _rows(second.text)


def test_a_row_a_later_fill_withdrew_is_not_exported(client, world):
    """A withdrawn row keeps its record and loses its place on the day.

    The supervisor took 250601 off; the edge ended its validity rather than
    being deleted, so "what was this department told to run" is still
    answerable — and the file a crew works from does not carry it.
    """
    day = steel_day(world)
    run = open_day(world, "steel-fab", day)
    run = _decide(world, run, "corrected", note="250601 is not ours.",
                  payload={"removed": ["250601"]})
    _decide(world, run, "approved", note="Signed.")

    rows = _rows(_get(client, day).text)

    assert [row[1] for row in rows] == ["250600", "250602"]
    assert world.graph.get(
        object_id(onto.SCHEDULE_ENTRY,
                  f"steel-fab:{day.isoformat()}:250601"),
        follow=False,
    ) is not None


# ---- the door ---------------------------------------------------------------


def test_it_is_a_read_and_not_the_supervisors_desk_in_disguise(client, world):
    """`user`+, and no queue. Handing a customer their own schedule is a read.

    The persona here is own-scoped and holds no desk at all — the one that
    proves scoping everywhere else in this suite — and it still gets the file,
    because the decision that authorized it happened at the Task, through
    `require_queue`, and is already on the ledger.
    """
    day, _run = _approved(world)

    answer = _get(client, day, headers=NO_QUEUES)

    assert answer.status_code == 200
    assert len(_rows(answer.text)) == 3


def test_a_malformed_week_says_what_the_shape_is(client, world):
    """A 422 naming the format: the caller is a customer's own script."""
    steel_day(world)

    answer = client.get(
        "/api/v1/schedule/export",
        params={"week": "next tuesday", "department": "steel-fab"},
        headers=ENGINEER,
    )

    assert answer.status_code == 422
    assert "`YYYY-Www`" in answer.json()["detail"]
    assert "2026-W37" in answer.json()["detail"]
