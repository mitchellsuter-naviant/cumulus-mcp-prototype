"""The three plant workspaces' doors, and the honest empty state behind them.

`/api/ui/v1/field-documents`, `/compliance` and `/plant-schedule` are
`payables_screen`'s shape three more times, so they inherit its door: **own the
desk, or read past yourself.** The rows are scoped either way; what the gate
protects is everything else on the page. A workspace carries the governed pages
that judged its rows — the naming convention, the notice stages, the capacity
table — and those are nobody's *own* work, so an own-scoped persona bound to
none of the three desks is refused outright rather than handed a screen of
zeros it would read as "everything is fine".

Four properties, and the last one is the one this file exists for:

1. **The desk, or read reach.** Both ways in, tested separately, because they
   are independent axes: the plant supervisor decides, and Naviant Support
   watches without deciding anything.
2. **The refusal names the desk.** A person told "not yours" with no desk named
   has nothing to do next; the sentence has to be the one they can act on.
3. **The screens read what the flows wrote.** The packet is the
   `documented_by` edges, the former crew is a withdrawn edge *still on the
   record*, an untracked asset is named untracked rather than shown as overdue,
   and a day is exportable exactly when it is approved — which is the same
   answer `/api/v1/schedule/export` gives, so the console cannot offer a
   control the server will refuse.
4. **A deployment that does not run the app says so in a sentence.** The
   session answers availability, in `_sync_now_refusal`'s own words, rather
   than the console compiling a third copy of the rule and drawing an empty
   table where the honest answer is "this estate has no such system".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import deps, desks, ui_api
from app.auth import Principal
from app.connectors.sync import SyncRequest
from app.estate import EstateConnector, EstateError, InMemoryConnectorStateStore
from app.graph import Claim, object_id
from app.models import utcnow
from app.ontology import names as onto
from app.ui_api import (
    get_compliance_principal,
    get_field_documents_principal,
    get_plant_schedule_principal,
)
from tests import estate_fixtures as fx
from tests import test_credential_tracking as ct
from tests import world as test_world

FIELD_DOCUMENTS = "/api/ui/v1/field-documents"
COMPLIANCE = "/api/ui/v1/compliance"
PLANT_SCHEDULE = "/api/ui/v1/plant-schedule"
SESSION = "/api/ui/v1/session"

PROJECT = "cumulus-demo-mitchell-suter"

#: Reads every plane in order to diagnose a deployment and holds no queue at
#: all — the persona that separates "may see this" from "may answer this". The
#: engineer is deliberately not used for that: it owns every desk, so it would
#: pass a gate test no binding was actually being checked by.
OBSERVER = "naviant-support"
#: Owns no queue and reads only his own work. The persona that proves scoping.
OUTSIDE = "customer-user:ae"

#: The three doors, each with the desk it names and the route behind it.
DOORS = (
    (get_field_documents_principal, desks.FIELD_OPERATIONS, FIELD_DOCUMENTS,
     "Field Documents"),
    (get_compliance_principal, desks.HR_SAFETY, COMPLIANCE, "Compliance"),
    (get_plant_schedule_principal, desks.PLANT_SUPERVISOR, PLANT_SCHEDULE,
     "Plant Schedule"),
)

JOB_SOURCE = "erp-jobs"
DMS_SOURCE = "dms-library"
TODAY = "2026-09-08"


def _as(persona_id: str) -> dict[str, str]:
    return {"X-Cumulus-Persona": persona_id}


def _on_desk(desk: str) -> Principal:
    """An own-scoped persona bound to one desk and nothing else."""
    return Principal(
        uid=f"persona_{desk.replace('-', '_')}",
        email=f"{desk}@wickbuildings.com",
        role="user", scope="own", persona_id=desk, queues=(desk,),
    )


def _no_desk() -> Principal:
    return Principal(
        uid="persona_outside", email="dale.prellwitz@wickbuildings.com",
        role="user", scope="own", persona_id="foreman", queues=(),
    )


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------


_TREE = {
    "source": DMS_SOURCE,
    "watermark": "2026-09-08T05:00:00Z",
    "folders": [
        {"external_id": "F-root", "name": "Job Documents"},
        {"external_id": "F-661", "name": "250661",
         "parent_external_id": "F-root"},
    ],
    "documents": [
        {"external_id": "D-SL", "name": "250661 Shipping List.pdf",
         "folder_external_id": "F-661", "content_type": "application/pdf",
         "size": 2048, "latest_version": 1, "storage_ref": "dms://D-SL"},
        {"external_id": "D-EP1", "name": "250661 Engineering Plans REV A.pdf",
         "folder_external_id": "F-661", "content_type": "application/pdf",
         "size": 2048, "latest_version": 1, "storage_ref": "dms://D-EP1"},
        {"external_id": "D-MAP", "name": "250661 Job Site Map.pdf",
         "folder_external_id": "F-661", "content_type": "application/pdf",
         "size": 2048, "latest_version": 1, "storage_ref": "dms://D-MAP"},
        {"external_id": "D-CH", "name": "250661 Crew Hours.pdf",
         "folder_external_id": "F-661", "content_type": "application/pdf",
         "size": 2048, "latest_version": 1, "storage_ref": "dms://D-CH"},
    ],
}


#: The revision that arrives second, and the whole of the supersession case.
#: Delivered as its own window because that is how it happens: a pass links REV
#: A, a later pass finds REV B at a higher revision, withdraws the first edge
#: and marks the row superseded — **keeping both**, because a crew that already
#: printed REV A should be able to find out what they are holding. Landing both
#: revisions in one delivery would only ever exercise the tie-break, never the
#: withdrawal.
_REVISION = {
    "source": DMS_SOURCE,
    "watermark": "2026-09-08T08:00:00Z",
    "folders": [],
    "documents": [
        {"external_id": "D-EP2", "name": "250661 Engineering Plans REV B.pdf",
         "folder_external_id": "F-661", "content_type": "application/pdf",
         "size": 2048, "latest_version": 2, "storage_ref": "dms://D-EP2"},
    ],
}


def _job(crew: str) -> dict:
    return {
        "source": JOB_SOURCE,
        "watermark": f"2026-09-08T0{6 if crew == '12' else 7}:00:00Z",
        "jobs": [{
            "external_id": "J-661", "job_number": "250661",
            "name": "250661 building",
            "customer_name": "The Willows Equine LLC",
            "builder_name": "Hoffmann Builders",
            "status": "Scheduled", "load_date": "2026-09-12",
            "crew_id": crew, "foreman": "D. Prellwitz",
            "plant_site": "Mazomanie",
        }],
    }


def _crews(world) -> None:
    for cost_center, slug, name in (
        ("12", "crew-12", "Crew 12"), ("14", "crew-14", "Crew 14"),
    ):
        world.graph.upsert(
            onto.TEAM, slug,
            [
                Claim(field="slug", value=slug, source="hris:hris-workforce"),
                Claim(field="name", value=name, source="hris:hris-workforce"),
                Claim(field="cost_center", value=cost_center,
                      source="hris:hris-workforce"),
            ],
            label=name,
        )


def _day(world, status: str, department: str = "truss-fab") -> None:
    """One `ScheduleDay` and one row on it, written straight into the graph.

    Direct rather than through the flow, because what is under test here is the
    *screen* over a written day: driving a whole scheduling window to produce
    one would put that flow's entire surface between this module and the shape
    it is checking, and `test_department_scheduling.py` already owns that.
    """
    key = f"{department}:{TODAY}"
    claims = [
        Claim(field="department", value=department,
              source="flow:department-scheduling"),
        Claim(field="schedule_date", value=TODAY,
              source="flow:department-scheduling"),
        Claim(field="status", value=status,
              source="flow:department-scheduling"),
        Claim(field="total_hours", value=28.5,
              source="flow:department-scheduling"),
        Claim(field="goal_max_hours", value=30.0,
              source="flow:department-scheduling"),
        Claim(field="abs_max_hours", value=36.0,
              source="flow:department-scheduling"),
        Claim(field="fudge_hours", value=2.0,
              source="flow:department-scheduling"),
        Claim(field="headcount", value=4.0,
              source="flow:department-scheduling"),
        Claim(field="headcount_as_of", value="2026-09-07",
              source="flow:department-scheduling"),
        Claim(field="incentive_per_hour", value=0.8,
              source="flow:department-scheduling"),
        Claim(field="deferred_count", value=1.0,
              source="flow:department-scheduling"),
    ]
    if status == "approved":
        claims += [
            Claim(field="approved_by", value="abby.wick@wickbuildings.com",
                  source="human:abby.wick@wickbuildings.com"),
            Claim(field="approved_at", value=utcnow().isoformat(),
                  source="human:abby.wick@wickbuildings.com"),
        ]
    day = world.graph.upsert(
        onto.SCHEDULE_DAY, key, claims, label=f"{department} {TODAY}",
    )
    entry = world.graph.upsert(
        onto.SCHEDULE_ENTRY, f"{key}:250661",
        [
            Claim(field="job_number", value="250661",
                  source="flow:department-scheduling"),
            Claim(field="job_label", value="250661 Willows",
                  source="flow:department-scheduling"),
            Claim(field="labor_hours", value=28.5,
                  source="flow:department-scheduling"),
            Claim(field="sequence", value=1.0,
                  source="flow:department-scheduling"),
            Claim(field="basis", value="truss formula at rev3",
                  source="flow:department-scheduling"),
            Claim(field="row_kind", value="job",
                  source="flow:department-scheduling"),
            Claim(field="placed_by", value="flow",
                  source="flow:department-scheduling"),
        ],
        label="250661 Willows",
    )
    world.graph.link(
        "has_schedule_entry", day.id, entry.id, method="declared",
    )


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


@pytest.fixture()
def plant(client, world):
    """A packet, a crew move, a fleet with one untracked unit, and a day."""
    _crews(world)
    for delivery in (_TREE, _job("12"), _REVISION, _job("14")):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    # The compliance book is `test_credential_tracking`'s own, imported rather
    # than restated: the screen and the sweep have to be describing one world,
    # and a second fixture here would be a second opinion about which rows are
    # in scope — which is the first thing this screen could get wrong.
    ct._org(world)
    for delivery in (ct._TRAINING, ct._DRIVER, ct._ASSETS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    _day(world, "pending_approval")
    return client


def _screen(client, route: str, persona: str = "naviant-engineer") -> dict:
    resp = client.get(route, headers=_as(persona))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# The doors
# ---------------------------------------------------------------------------


def test_the_desk_gets_in(plant):
    """Own the desk — the first of the two independent ways in."""
    for door, desk, _route, _label in DOORS:
        principal = _on_desk(desk)
        assert door(principal) is principal, desk


def test_reading_past_yourself_gets_in(plant):
    """The second way in, and it has to be a second way: a Naviant persona
    diagnosing a deployment holds none of these desks and still belongs on
    every one of these screens."""
    for _door, _desk, route, _label in DOORS:
        assert plant.get(route, headers=_as(OBSERVER)).status_code == 200, route


def test_neither_is_refused_and_the_desk_is_named(plant):
    """The refusal has to be one a person can act on.

    Told only "not yours", somebody has nowhere to go next. The sentence names
    the desk, what the workspace holds, and that their access is to their own
    work — `get_payables_principal`'s exact three clauses.
    """
    for door, desk, route, label in DOORS:
        with pytest.raises(HTTPException) as refused:
            door(_no_desk())
        assert refused.value.status_code == 403
        detail = str(refused.value.detail)
        assert f"`{desk}`" in detail, detail
        assert label in detail, detail
        assert "your own work" in detail, detail

        over_http = plant.get(route, headers=_as(OUTSIDE))
        assert over_http.status_code == 403, route
        assert desk in over_http.text, route


def test_a_minted_key_never_reaches_a_console_screen(plant):
    """`get_ui_principal`'s rule, inherited rather than restated — the three
    doors sit on it, so a machine credential is refused before the desk
    question is even asked."""
    for _door, _desk, route, _label in DOORS:
        resp = plant.get(route, headers={"X-API-Key": "not-a-real-key"})
        assert resp.status_code in (401, 403), route


# ---------------------------------------------------------------------------
# What the screens say
# ---------------------------------------------------------------------------


def test_the_packet_is_edges_and_the_former_crew_is_still_on_the_record(plant):
    """Packets, not folders — and a crew move deletes nothing.

    The job moved from Crew 12 to Crew 14, so the live `assigned_to` edge names
    the new crew and the withdrawn one is *still here* with the moment its
    validity ended. Nothing anywhere in this screen names a folder that was
    created, moved or removed, because nothing creates, moves or removes one.
    """
    screen = _screen(plant, FIELD_DOCUMENTS)
    row = next(r for r in screen["jobs"] if r["job_number"] == "250661")

    assert row["current_crew"]["name"] == "Crew 14"
    former = [c["name"] for c in row["former_crews"]]
    assert former == ["Crew 12"], row["former_crews"]
    assert row["former_crews"][0]["until_iso"], "an ended validity has a date"

    kinds = {d["kind"] for d in row["documents"] if not d["superseded"]}
    assert len(kinds) == 4, sorted(kinds)
    assert row["missing"] == []
    assert row["complete"] is True

    # The second revision won and the first kept its row, its filename and its
    # date — which is what a crew holding the printed REV A needs to find out.
    superseded = [d for d in row["documents"] if d["superseded"]]
    assert len(superseded) == 1, row["documents"]
    assert "REV A" in superseded[0]["filename"]

    assert row["packet_label"] == "250661 Willows", row["packet_label"]
    assert row["label_rule"], "the convention behind the label travels with it"
    assert screen["policy_clauses"], "the governed page is on the screen"


def test_an_untracked_unit_is_named_untracked_and_not_shown_as_overdue(plant):
    """The distinction the compliance screen exists to keep.

    A unit the register holds no next-due date for is excluded at the *unit*
    and listed as untracked. It must never appear in the expiry table with an
    empty date, because that reads as a record that lapsed.
    """
    screen = _screen(plant, COMPLIANCE)
    untracked = {a["equipment_id"] for a in screen["untracked_assets"]}
    assert untracked == {"SKID-3"}, screen["untracked_assets"]
    assert screen["stats"]["untracked"] == 1
    assert "SKID-3" not in {row["subject"] for row in screen["rows"]}
    # The truck *is* tracked, so it belongs in the table — the exclusion is
    # about the register's silence, not about equipment.
    assert any(row["kind"] == "inspection" for row in screen["rows"])
    assert screen["untracked_assets"][0]["why"], "the page's own sentence"
    assert screen["retention_horizon"], "nothing is deleted, and this says when"


def test_days_to_expiry_ascending_with_unreadable_dates_last(plant):
    """The order the sweep itself works in, preserved on the wire.

    Asserted as a property of the list rather than by re-sorting it: a screen
    that sorted for itself would be a second opinion about urgency.
    """
    rows = _screen(plant, COMPLIANCE)["rows"]
    dated = [r["days_to_expiry"] for r in rows if r["days_to_expiry"] is not None]
    assert dated == sorted(dated), dated
    seen_unreadable = False
    for row in rows:
        if row["days_to_expiry"] is None:
            seen_unreadable = True
        else:
            assert not seen_unreadable, "an unreadable date sorted before a date"


def test_a_day_is_exportable_exactly_when_it_is_approved(plant, world):
    """The console must not offer what the server will refuse.

    `exportable` is `status == approved` and nothing else, which is the same
    answer `/api/v1/schedule/export` gives — so the two cannot disagree, and
    the 409 the export answers on a pending day is checked here against the
    control the screen would draw.
    """
    week = "2026-W37"
    cells = _screen(plant, f"{PLANT_SCHEDULE}?week={week}")["cells"]
    cell = next(c for c in cells if c["department"] == "truss-fab" and c["exists"])
    assert cell["status"] == "pending_approval"
    assert cell["exportable"] is False
    assert cell["export_url"].endswith("week=2026-W37&department=truss-fab")

    refused = plant.get(cell["export_url"], headers=_as("naviant-engineer"))
    assert refused.status_code == 409, refused.text

    _day(world, "approved")
    cells = _screen(plant, f"{PLANT_SCHEDULE}?week={week}")["cells"]
    cell = next(c for c in cells if c["department"] == "truss-fab" and c["exists"])
    assert cell["exportable"] is True
    assert cell["approved_by"]
    assert plant.get(
        cell["export_url"], headers=_as("naviant-engineer")
    ).status_code == 200


def test_an_absent_day_is_absent_rather_than_zero(plant):
    """A department with no schedule and a department scheduled for nothing are
    opposite facts, and a blank cell reads as the second."""
    screen = _screen(plant, f"{PLANT_SCHEDULE}?week=2026-W37")
    absent = [c for c in screen["cells"] if not c["exists"]]
    assert absent, "the grid draws every department × day, not only the written"
    assert all(cell["status"] == "" for cell in absent)
    assert screen["stats"]["days"] == 1, screen["stats"]
    # Every cell still carries where its export would be, so the grid never has
    # to compose a URL of its own.
    assert all(cell["export_url"] for cell in screen["cells"])


def test_a_malformed_week_is_refused_in_the_export_s_own_words(plant):
    """One parser, one sentence. The BFF and the Tier-1 export read the same
    `week`, and a second parse here would be a second answer to what shape a
    week is."""
    resp = plant.get(f"{PLANT_SCHEDULE}?week=septemberish",
                     headers=_as("naviant-engineer"))
    assert resp.status_code == 422
    assert "YYYY-Www" in resp.text


# ---------------------------------------------------------------------------
# The honest empty state
# ---------------------------------------------------------------------------


def _install_estate(namespaces: dict | None = None, fetch=None) -> None:
    deps._estate_connector = EstateConnector(
        InMemoryConnectorStateStore(),
        project=PROJECT,
        namespace="wick",
        fetch=fetch or fx.FakeEstate(namespaces=namespaces).fetch,
    )


@pytest.fixture()
def estate():
    """Swap the estate connector out, and put it back.

    A module-level singleton, so a test that left a fake in place would change
    what every later module's session route says about the deployment.
    """
    held = deps._estate_connector
    yield _install_estate
    deps._estate_connector = held


def _availability(client, persona: str = "naviant-engineer") -> dict[str, dict]:
    resp = client.get(SESSION, headers=_as(persona))
    assert resp.status_code == 200, resp.text
    return {row["slug"]: row for row in resp.json()["workspaces"]}


def test_a_namespace_without_the_app_says_so_in_the_estate_s_own_sentence(
    client, estate,
):
    """The finding, in `_sync_now_refusal`'s words rather than a second set.

    An estate running only `office` has no document system, no HRIS and no
    production system, so the three plant workspaces are not screens with
    nothing in them — they are screens about rows that cannot exist. The
    console draws the reason instead of an empty table.
    """
    estate({"namespaces": [{"id": "wick", "name": "Wick Buildings",
                            "apps": ["office"], "processes": []}],
            "default": "wick"})
    rows = _availability(client)

    for slug, app in (("field-documents", "dms"), ("compliance", "hris"),
                      ("plant-schedule", "erp")):
        assert rows[slug]["available"] is False, slug
        assert rows[slug]["reason"] == (
            f"The `wick` namespace does not run the `{app}` app, so this feed "
            "has nothing to pull."
        ), rows[slug]

    # The two finance workspaces rest on no app — a document a person uploads
    # is an invoice book — so they are unaffected.
    assert rows["payables"]["available"] is True
    assert rows["expenses"]["available"] is True


def test_a_namespace_that_runs_the_apps_has_the_workspaces(client, estate):
    estate(fx.WICK_NAMESPACES)
    rows = _availability(client)
    for slug in ("field-documents", "compliance", "plant-schedule"):
        assert rows[slug]["available"] is True, rows[slug]
        assert rows[slug]["reason"] == ""


def test_an_unreachable_estate_is_reported_as_itself(client, estate):
    """Discovery failing and discovery answering "no such app" are opposite
    findings, and only the second is about the customer's estate. Collapsing
    them would tell an operator their namespace lacks a system when what
    actually happened is that nothing could be reached."""

    def _down(url: str) -> dict:
        raise EstateError("connection refused")

    estate(fetch=_down)
    rows = _availability(client)
    assert rows["plant-schedule"]["available"] is False
    assert rows["plant-schedule"]["reason"].startswith(
        "The estate could not be reached:"
    )


def test_no_estate_at_all_is_not_a_missing_app(client, estate, monkeypatch):
    """A deployment running against no estate still has every workspace its
    cast staffs. There is nothing to ask, so the app half is skipped — the
    alternative reports the absence of a connector as a fact about a customer's
    systems."""
    deps._estate_connector = EstateConnector(
        InMemoryConnectorStateStore(), project="",
    )
    rows = _availability(client)
    assert all(row["available"] for row in rows.values()), rows


def test_a_desk_nobody_holds_is_a_workspace_nobody_would_answer(
    client, estate, monkeypatch,
):
    """The other half of availability, and the half that is about *this cast*.

    A workspace whose desk no persona holds routes its Tasks to a queue with no
    reader. That is a fact about the deployment and belongs in the answer,
    ahead of the estate question: there is no point asking whether the ERP is
    reachable for a screen nobody could act on.
    """
    estate(fx.WICK_NAMESPACES)
    # A whole cast, stood in for rather than filtered. Filtering the real one
    # takes the engineer out — it holds every desk — and with it every other
    # desk this test wants left alone, so the assertion below could not tell
    # "nobody holds plant-supervisor" from "nobody holds anything".
    monkeypatch.setattr(ui_api.personas, "ALL_PERSONAS", (
        SimpleNamespace(queues=(
            desks.FIELD_OPERATIONS, desks.HR_SAFETY, desks.AP_CLERK,
            desks.EXPENSE_REVIEW,
        )),
    ))
    rows = _availability(client)
    assert rows["plant-schedule"]["available"] is False
    assert desks.PLANT_SUPERVISOR in rows["plant-schedule"]["reason"]
    assert "Plant Schedule" in rows["plant-schedule"]["reason"]
    assert rows["compliance"]["available"] is True


def test_the_slugs_are_the_console_s_own_section_slugs(client, estate):
    """Availability and navigation are keyed on one string.

    Two vocabularies here — a workspace id and a section slug — would be a
    deployment able to say a workspace is unavailable and a rail unable to
    find out which stop that was.
    """
    estate(fx.WICK_NAMESPACES)
    assert set(_availability(client)) == {
        "expenses", "payables", "field-documents", "compliance",
        "plant-schedule",
    }


# ---------------------------------------------------------------------------
# The resume hooks — without these the screens are decorative
# ---------------------------------------------------------------------------
#
# A review screen answers with a verdict and a payload; `POST
# /api/v1/tasks/{id}/decide` records it and then walks `RESUME_HOOKS` to reach
# the flow that raised the Task. None of the four plant flows was in that
# table, so every decision taken on one of these screens was a judgment the
# queue recorded and no flow ever heard.
#
# The four are not the same shape and the difference is worth pinning. **Two
# gate**: the shipping-list review and the day approval each suspend a run on
# their Task, and answering one drives it forward — tested here through the
# real route rather than by calling the service, because what is under test is
# the routing table and not the service's own method. **Two do not**:
# `field-documents` and `credential-tracking` raise aggregate Tasks and walk on
# to their closing event, so there is no run to resume and the hook answers
# `None`. Tested anyway, and for the property that matters most about a hook
# that does nothing: it must not raise, because a hook that raises takes the
# decision back off the Task.


def _decide_over_http(client, task_id: str, verdict: str = "approved",
                      persona: str = "naviant-engineer", **body) -> dict:
    resp = client.post(
        f"/api/v1/tasks/{task_id}/decide",
        json={"verdict": verdict, "note": "", "correction": "", **body},
        headers=_as(persona),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_approving_a_day_over_the_api_resumes_its_run_and_signs_the_day(
    client, world,
):
    """The whole point of the table, end to end.

    Nothing here calls `DepartmentSchedulingService` directly: the decision
    goes through the same route the console posts to, so what is being proved
    is that a supervisor pressing Approve reaches the flow — and that the day
    it was holding open is signed as a result.
    """
    from tests.test_department_scheduling import open_day, steel_day

    day = steel_day(world)
    run = open_day(world, "steel-fab", day)
    suspended = world.engine.get(run.id)
    assert suspended.status == "suspended", suspended.status
    task_id = suspended.open_task_id
    assert task_id, "the fill must have raised an approval Task"

    _decide_over_http(client, task_id)

    resumed = world.engine.get(run.id)
    assert resumed.status == "succeeded", resumed.error
    assert resumed.resume_count == 1

    record = world.graph.get(
        object_id(onto.SCHEDULE_DAY, f"steel-fab:{day.isoformat()}"),
        follow=False,
    )
    assert record.props["status"] == "approved"
    assert record.props["approved_by"], "the signature names who signed it"


def test_answering_a_shipping_list_review_over_the_api_resumes_its_run(
    client, world,
):
    """The second gate, and the one whose screen reuses the v8 field-verdict
    contract. A correction there is worth nothing if the decision reaches no
    run to re-check it."""
    from tests.test_shipping_list_extraction import start

    # A field the extraction could not settle, so the run gates: a ticket
    # that reads cleanly is approved without asking anybody, which is the
    # design and would test nothing about a decision reaching a run.
    run = start(world, case="unsure=c_steel_fab.total_linear_feet")
    suspended = world.engine.get(run.id)
    assert suspended.status == "suspended", suspended.status

    _decide_over_http(client, suspended.open_task_id)

    resumed = world.engine.get(run.id)
    assert resumed.status == "succeeded", resumed.error
    assert resumed.resume_count == 1
    assert world.graph.find(onto.JOB_TASK), "approving writes the demand rows"


def test_a_packet_gap_decision_settles_and_never_takes_itself_back(
    client, world,
):
    """A hook with no run to resume must still be safe to be in the table.

    `field-documents` raises at most two aggregate Tasks per window and walks
    on to `e1`, so nothing is suspended on them. What matters is that answering
    one records the judgment and leaves it recorded: a hook that raised would
    have `decide_task` withdraw the decision and answer 500, which is how a
    Task that was answered comes back open with nobody told.
    """
    _crews(world)
    for delivery in (_TREE, _job("SUB-KVB")):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    task = next(
        t for t in world.tasks.list(status="open")
        if t.case_ref.startswith("field-documents-gap:")
    )
    decided = _decide_over_http(
        client, task.id, verdict="corrected",
        payload={"crew": {"250661": "crew-14"}},
    )
    assert decided["status"] == "done"
    assert decided["decision"]["verdict"] == "corrected"
    # And it stays decided — the withdraw path only runs on a hook that raised.
    assert world.tasks.get(task.id).status == "done"


def test_a_compliance_exception_decision_settles_the_same_way(client, world):
    """The fourth of them, over the exception class the review screen edits."""
    ct._org(world)
    for delivery in (ct._TRAINING, ct._DRIVER, ct._ASSETS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    ct._close_window(world, "the compliance book", ct.TODAY)
    task = next(
        t for t in world.tasks.list(status="open")
        if t.case_ref.startswith("compliance-exception:")
    )
    decided = _decide_over_http(
        client, task.id, verdict="corrected",
        payload={"dates": {task.evidence[-1].label: "2026-12-01"}},
    )
    assert decided["status"] == "done"
    assert world.tasks.get(task.id).status == "done"


def test_every_flow_that_raises_a_task_has_a_hook_that_reaches_it(world):
    """The table is the routing, so a flow missing from it is a silence.

    Asserted over the *services* rather than over the lambdas, because what a
    reader wants to know is that a decision on one of these Tasks arrives
    somewhere that can act on it — and every one of the four exposes the same
    two-method door precisely so this can be checked in one line each.
    """
    for service in (
        world.packets, world.credentials, world.extraction, world.scheduling,
    ):
        assert callable(getattr(service, "apply_task_decision", None)), service
        assert callable(getattr(service, "resume_for_task", None)), service


def test_the_cell_carries_the_fill_s_own_arithmetic_and_its_deferrals(
    client, world,
):
    """The grid reads the run, not a second derivation of it.

    A `ScheduleDay` carries the numbers but not the *reasoning*: the capacity
    row that was chosen, the incentive written out as its own arithmetic, the
    clause the fill stopped against and everything it deferred all live in the
    `r2` step's output. Reading them there is what makes the out-of-the-hole
    indicator auditable rather than a cell nobody can check — and re-deriving
    them here would be a second implementation of a governed Rule, free to
    disagree with the day it is describing.

    The **last** `r2` is the one read, because a corrected day re-fills: `r2_2`
    is what the day currently is and `r2` is what it was before somebody edited
    it, and a screen showing the first beside the second's rows would be
    listing deferrals that are no longer deferred.
    """
    from tests.test_department_scheduling import open_day, steel_day

    day = steel_day(world)
    open_day(world, "steel-fab", day)
    week = f"{day.isocalendar()[0]}-W{day.isocalendar()[1]:02d}"

    cells = _screen(client, f"{PLANT_SCHEDULE}?week={week}")["cells"]
    cell = next(
        c for c in cells
        if c["department"] == "steel-fab" and c["date"] == day.isoformat()
    )

    assert cell["exists"] is True
    assert cell["run_id"], "the cell names the run that filled it"
    assert cell["capacity_row"], "which capacity row was chosen"
    assert "=" in cell["incentive_basis"], cell["incentive_basis"]
    assert cell["incentive_start_usd"] is not None
    assert cell["incentive_rate_usd_per_hour"] is not None
    assert cell["stopped_at"], "the governed clause the fill stopped against"
    assert cell["entries"], "the rows, in sequence"
    assert all(row["basis"] for row in cell["entries"])
    assert cell["headcount"] == 3.0
    assert cell["headcount_as_of"] or not cell["headcount_as_of"]
    assert cell["pending_task_id"], "the open approval Task the day is holding"

    # The arithmetic on the cell reproduces the figure on the record, which is
    # the whole of what "auditable" means here: a reader with the three numbers
    # can check the fourth.
    computed = (
        cell["incentive_start_usd"]
        + cell["incentive_rate_usd_per_hour"] * cell["total_hours"]
    )
    assert abs(computed - cell["incentive_per_hour"]) < 0.005, cell
    assert cell["out_of_the_hole"] is (cell["incentive_per_hour"] >= 0)
