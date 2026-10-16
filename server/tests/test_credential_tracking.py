"""The compliance clock, and the promises it must not break.

Five groups of property, and the second is the one this module exists for:

1. **It reads the shipped policy** — the headings and the `- key: value` lines
   of `certification-alert-policy`, section-scoped, and a corpus with no such
   document falls back to a built-in default the run *says* it fell back to.
2. **A stage fires once per cycle.** 60 · 30 · 15, each exactly once as one
   record walks down to its expiry, and a second window on the same day
   re-sends nothing. That guard is what makes a daily cadence safe rather than
   a supervisor's inbox filling every morning, and it is asserted here over
   four real windows rather than described.
3. **Counts, not gates.** An asset whose register carries no due date never
   alerts and is *counted*; a category the policy does not track is counted;
   and a unit whose due date is dropped by a later delivery stops alerting
   while its examination history stays exactly where it was.
4. **Four classes, four Tasks.** Five exception rows across four classes are
   four aggregate Tasks on `hr-safety` and never five, and the retention class
   is a report: nothing is deleted, and nothing in this system can delete a
   compliance record.
5. **The map is the run.** The declared `STEPS` are exactly the journal, as a
   set and as a sequence.

The fixture is deliveries wherever a Wick feed provides the rows — the same
push payloads `credential-sync` and `equipment-sync` answer in production, so
the seam is exercised on the way in — with direct `Employee`/`Team` writes for
the org chart, which no compliance feed carries.
"""

from datetime import date

import pytest

from app.connectors.sync import SyncRequest
from app.graph import Claim, object_id
from app.models import AssetUpdate
from app.ontology import names as onto
from app.tasks.credential_tracking.domain import (
    ALERT_ACTION,
    ALERT_EVENT,
    COMPLIANCE_RECORDS,
    COMPLIANCE_WINDOW_EVENT,
    EXCEPTION_CLASSES,
    EXCEPTION_SCREEN,
    FLOW_ID,
    MISSING_DATE,
    POLICY_ASSET_ID,
    RETAINED_PAST_HORIZON,
    TERMINATED_HOLDER,
    UNRESOLVED_HOLDER,
    AlertPolicy,
    _section,
    parse_alert_policy,
    stage_due,
)
from app.tasks.credential_tracking.v1 import flow as v1
from tests import world as test_world

#: The day the base fixture is answered as of. Fixed rather than relative, so
#: an assertion about a 60-day stage says which sixty days.
TODAY = date(2026, 9, 7)

TRAINING_SOURCE = "hris-training"
DRIVER_SOURCE = "hris-driver-files"
ASSET_SOURCE = "erp-assets"

CARL = "carl.ostrander@wickbuildings.com"
NINA = "nina.vance@wickbuildings.com"
HEATHER = "heather.lane@wickbuildings.com"
DANA = "dana.prentiss@wickbuildings.com"
GHOST = "ghost.contractor@wickbuildings.com"
PLANT = "plant-mazomanie"

#: The row that walks the stages. Nothing else in the fixture expires in
#: November, so the three windows below move exactly one record.
WALKER = f"{TRAINING_SOURCE}:TR-1001"


# ---- the world -------------------------------------------------------------


def _person(world, email: str, name: str, status: str = "Active") -> None:
    world.graph.upsert(
        onto.EMPLOYEE, email,
        [
            Claim(field="email", value=email, source="hris:hris-workforce"),
            Claim(field="name", value=name, source="hris:hris-workforce"),
            Claim(field="status", value=status, source="hris:hris-workforce"),
        ],
        label=name,
    )


def _org(world) -> None:
    """The org chart the notices are addressed through.

    Direct writes, deliberately: no compliance feed carries an employee or a
    department, and going through `workforce-sync` to land four people would
    put that feed's whole surface between this module and what it is testing.
    """
    for email, name, status in (
        (HEATHER, "Heather Lane", "Active"),
        (DANA, "Dana Prentiss", "Active"),
        (CARL, "Carl Ostrander", "Active"),
        (NINA, "Nina Vance", "Terminated"),
    ):
        _person(world, email, name, status)
    world.graph.upsert(
        onto.TEAM, PLANT,
        [
            Claim(field="slug", value=PLANT, source="hris:hris-workforce"),
            Claim(field="name", value="Plant — Mazomanie",
                  source="hris:hris-workforce"),
        ],
        label="Plant — Mazomanie",
    )
    ids = {
        email: world.graph.get(object_id(onto.EMPLOYEE, email))
        for email in (HEATHER, DANA, CARL, NINA)
    }
    team = world.graph.get(object_id(onto.TEAM, PLANT))
    for email in (CARL, NINA):
        world.graph.link(
            "reports_to", ids[email].id, ids[HEATHER].id, method="declared",
        )
    world.graph.link(
        "led_by", team.id, ids[DANA].id, method="declared",
    )


_TRAINING = {
    "source": TRAINING_SOURCE,
    "watermark": "2026-09-07T05:00:00Z",
    "credentials": [
        # The row the stage tests walk: 55 days out on the base day.
        {"external_id": "TR-1001", "holder_email": CARL,
         "credential_type": "Fall Protection Refresher",
         "category": "training",
         "completion_date": "2025-11-01", "expiration_date": "2026-11-01",
         "result": "Pass", "provider": "Wisconsin Safety Council"},
        # No expiry at all, and one that is prose rather than a date. Two rows,
        # one aggregate — the trackers this replaces were colour-coded, and the
        # original design was explicit that a bot must never read colour.
        {"external_id": "TR-1002", "holder_email": CARL,
         "credential_type": "Lift Operator", "category": "training",
         "completion_date": "2026-01-14", "result": "Pass"},
        # A holder the workforce does not contain.
        {"external_id": "TR-1003", "holder_email": GHOST,
         "credential_type": "Confined Space", "category": "clearance",
         "expiration_date": "2026-10-01"},
        # A holder who has left, still on the active roster.
        {"external_id": "TR-1004", "holder_email": NINA,
         "credential_type": "Forklift Certification",
         "category": "certification", "expiration_date": "2026-10-15"},
        # Nine years expired: past the seven-year horizon.
        {"external_id": "TR-1005", "holder_email": CARL,
         "credential_type": "Rigging Certification",
         "category": "certification", "expiration_date": "2017-06-30"},
        # A category the policy does not track. Carried in the graph, never
        # alerted on, counted.
        {"external_id": "TR-1006", "holder_email": CARL,
         "credential_type": "Safety Orientation",
         "category": "safety-orientation", "expiration_date": "2026-09-30"},
    ],
}

_DRIVER = {
    "source": DRIVER_SOURCE,
    "watermark": "2026-09-07T05:00:00Z",
    "credentials": [
        # A driver-qualification row and a training row are one type: the only
        # thing that differs is `category`.
        {"external_id": "DQ-2001", "holder_email": CARL,
         "credential_type": "Commercial Driver Licence — Class A",
         "category": "licence", "expiration_date": "2026-09-20"},
        {"external_id": "DQ-2002", "holder_email": CARL,
         "credential_type": "DOT Medical Card", "category": "medical",
         "expiration_date": "see certificate"},
    ],
}

_ASSETS = {
    "source": ASSET_SOURCE,
    "watermark": "2026-09-07T05:00:00Z",
    "equipment": [
        {"external_id": "A-14", "equipment_id": "TRK-14", "name": "Truck 14",
         "category": "truck", "home_site": "Mazo",
         "last_inspection_date": "2025-10-01",
         "next_inspection_due_date": "2026-10-01"},
        # The skid steer. No due date, so the register is saying this unit
        # carries no tracked inspection — a fact about the asset, not a gap.
        {"external_id": "A-31", "equipment_id": "SKID-3",
         "name": "Skid Steer 3",
         "category": "skid-steer", "home_site": "Mazo",
         "last_inspection_date": "2025-04-02"},
    ],
}


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def booked(world):
    """The org chart, then the three compliance deliveries."""
    _org(world)
    for delivery in (_TRAINING, _DRIVER, _ASSETS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    return world


def _close_window(world, label: str, as_of: date):
    """A compliance window closing, through the Dispatcher that declares it.

    Through `emit` rather than by calling the body: the Trigger row and this
    package land together, and `Dispatcher.emit` does not guard
    `RunEngine.start` — a row whose body nothing registered would 500 the next
    delivery rather than fail here. This is the test that fails loudly instead.
    """
    dispatch = world.dispatcher.emit(
        COMPLIANCE_WINDOW_EVENT,
        {"window": label, "as_of": as_of.isoformat()},
        actor="system:test",
    )
    assert dispatch.runs, "no run started — is the Trigger declared?"
    record = dispatch.runs[0]
    return record, world.engine.get(record.id)


def _families(run) -> set[str]:
    """The step families a journal holds — loop suffixes trimmed.

    `r1_1` and `r1_2` are one declared step run twice, which is what the
    console shows and what a map can promise.
    """
    out = set()
    for step in run.steps:
        head, _, tail = step.step_id.rpartition("_")
        out.add(head if head and tail.isdigit() else step.step_id)
    return out


def _cred(world, natural_key: str):
    return world.graph.get(object_id(onto.CREDENTIAL, natural_key))


def _stage(world, natural_key: str):
    record = _cred(world, natural_key)
    assert record is not None, natural_key
    return record.props.get("last_notice_stage"), record.props.get(
        "last_notified_on"
    )


def _tasks(world) -> list:
    return [t for t in world.tasks.list() if t.flow_id == FLOW_ID]


# ---- the map is the run ----------------------------------------------------


def test_the_declared_steps_are_exactly_the_steps_a_full_run_executes(booked):
    """The claim the transparency bar makes, over a window doing everything.

    The base fixture is built so one pass reaches every step with something to
    do: six rows are due, five are exceptions across four classes, an asset is
    excluded and a category is untracked. What is left over in either direction
    is the bug — a step in the journal no map declares, or a map promising a
    step the engine never runs.
    """
    _, run = _close_window(booked, "the whole book", TODAY)
    assert run.status == "succeeded", run.error

    declared = [step.id for step in v1.STEPS]
    assert _families(run) == set(declared)
    # A map is a sequence and not a set, so the order is asserted too.
    assert [step.step_id for step in run.steps] == declared


def test_the_kinds_the_journal_records_are_the_kinds_the_map_declares(booked):
    """Every step but the aggregate, which is declared `task` and executed as
    a `sync` for `ap-intake`'s reason: it raises and never waits."""
    _, run = _close_window(booked, "kinds", TODAY)
    declared = {step.id: step.type for step in v1.STEPS}
    for step in run.steps:
        expected = declared[step.step_id]
        if step.step_id == "k1":
            assert step.kind == "sync"
            continue
        assert step.kind == expected, step.step_id


# ---- the governed read -----------------------------------------------------


def test_the_parser_reads_the_shipped_policy(world):
    """Every value §D-2 names, off the document really in the tree."""
    asset = world.knowledge.repo.get(POLICY_ASSET_ID)
    assert asset is not None, "the policy asset is not in the seed corpus"
    policy = parse_alert_policy(asset.content)

    assert policy.stages == [60, 30, 15]
    assert policy.sla_floor_days == 30
    assert policy.honors_sla_floor
    assert policy.widest_stage == 60
    assert set(policy.categories) == {
        "certification", "training", "licence", "medical", "clearance",
    }
    assert policy.cadence == "daily"
    assert policy.retention_years == 7
    assert "manual review" in policy.past_horizon
    assert "next-due date" in policy.untracked_when
    assert policy.routing.fallback == "hr-safety"
    assert policy.found_asset


def test_the_parser_is_section_scoped(world):
    """The document argues about its own numbers in prose.

    Its cadence section discusses stages and its exception table names the
    retention horizon, so an unscoped search would read a sentence *about* a
    number as the number. The proof is a document whose sections are gone but
    whose prose still mentions everything.
    """
    prose = (
        "# Certification Alert Policy\n\n"
        "This page talks about notice stages: 999 and a retention horizon: "
        "42 years without declaring either.\n"
    )
    policy = parse_alert_policy(prose)
    assert policy.stages == [60, 30, 15]
    assert policy.retention_years == 7


def test_the_local_section_reader_agrees_with_the_canonical_one(world):
    """Two copies of a section reader, held together by a test.

    `routing._section` is the canonical one and is private to a module this
    package should not reach into for one regex — the same arrangement
    `connectors.compliance._site_slug` is in, and the same honest way of
    keeping two copies from drifting.
    """
    from app import routing

    content = world.knowledge.repo.get(POLICY_ASSET_ID).content
    for heading in (
        "run cadence", "notice stages", "what is tracked", "retention",
        "approval routing", "not a heading in this document",
    ):
        assert _section(content, heading) == routing._section(content, heading)


def test_a_missing_policy_document_makes_the_run_fall_back_and_say_so(booked):
    """A fresh deployment still works, and the journal never pretends.

    An empty answer here would be a flow that silently does nothing — the
    failure that looks exactly like success.
    """
    booked.knowledge.repo.delete(POLICY_ASSET_ID)
    _, run = _close_window(booked, "no policy", TODAY)

    assert run.status == "succeeded", run.error
    assert run.outputs["found_asset"] is False
    read = run.step("x1")
    assert "built-in default" in read.summary
    # And it still did the work, on the declared defaults.
    assert run.outputs["stages"] == [60, 30, 15]
    assert run.outputs["alerted"] == 6


def test_an_sla_floor_breach_is_said_out_loud_rather_than_corrected(booked):
    """A stage list whose widest entry is under the floor breaks the promise
    the document states. The run reports it; nothing substitutes a stage
    nobody wrote down."""
    asset = booked.knowledge.repo.get(POLICY_ASSET_ID)
    booked.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "- notice stages: 60, 30, 15", "- notice stages: 20, 10"
        ),
        updated_by="test",
    ))
    _, run = _close_window(booked, "narrowed", TODAY)

    assert run.outputs["stages"] == [20, 10]
    assert run.outputs["honors_sla_floor"] is False
    assert "service-level floor" in run.step("x1").summary


# ---- the stages ------------------------------------------------------------


def test_the_three_stages_each_fire_once_per_cycle(booked):
    """60 · 30 · 15, once each, as one record walks down to its expiry.

    Four windows over `TR-1001`, which expires 2026-11-01. The fourth is the
    day after the third and moves nothing, which is the whole point of a daily
    cadence: the stages are what happen, and the other 362 windows are no-ops.
    """
    sent: list[tuple[str, int]] = []
    for label, as_of in (
        ("w1", date(2026, 9, 7)),    # 55 days — the 60-day stage
        ("w2", date(2026, 10, 10)),  # 22 days — the 30-day stage
        ("w3", date(2026, 10, 25)),  # 7 days  — the 15-day stage
        ("w4", date(2026, 10, 26)),  # 6 days  — nothing left to send
    ):
        _, run = _close_window(booked, label, as_of)
        assert run.status == "succeeded", run.error
        moved = [m for m in run.outputs["moves"] if m["row"].startswith(
            "Fall Protection Refresher"
        )]
        for move in moved:
            sent.append((label, move["to_stage"]))
        stage, notified = _stage(booked, WALKER)
        if label != "w4":
            assert notified == as_of.isoformat()
    assert sent == [("w1", 60), ("w2", 30), ("w3", 15)]
    # The last window found the row at 15 and left it there.
    assert _stage(booked, WALKER) == ("15", "2026-10-25")


def test_a_second_window_in_the_same_cycle_re_sends_nothing(booked):
    """The guard, stated as the two runs a daily cadence really makes.

    The second pass is a whole window that writes nothing, sends nothing and
    names the rows it skipped — which is what an empty compliance day has to
    look like if the cadence is going to be trusted.
    """
    _, first = _close_window(booked, "morning", TODAY)
    assert first.outputs["alerted"] == 6

    before = _cred(booked, WALKER).revision
    _, second = _close_window(booked, "afternoon", TODAY)

    assert second.status == "succeeded", second.error
    assert second.outputs["alerted"] == 0
    assert second.outputs["already_sent"] == 6
    assert second.outputs["due"] == 0
    # Nothing was written, so nothing moved a revision.
    assert _cred(booked, WALKER).revision == before
    # And the journal names a skipped row as skipped, with the stage it is at.
    skipped = {row["row"]: row for row in second.outputs["skipped"]}
    walker = next(
        row for label, row in skipped.items()
        if label.startswith("Fall Protection Refresher")
    )
    assert walker["at_stage"] == 60
    assert "already sent this cycle" in walker["why"]
    assert "already sent" in second.step("r1").summary


@pytest.mark.parametrize(
    "days,last,expected",
    [
        # The narrowest stage reached wins: a row 25 days out is owed the
        # 30-day notice, not the 60-day one it should have had a month ago.
        (25, None, 30),
        (55, None, 60),
        (10, None, 15),
        # Beyond every stage: nothing is due.
        (61, None, None),
        (400, None, None),
        # Already sent, and `<=` rather than `<` — 60 was sent, the row is
        # still 55 days out, and the due stage is 60 again. Same notice.
        (55, 60, None),
        (25, 60, 30),
        (10, 30, 15),
        (10, 15, None),
        # Long expired, never notified: one notice at the narrowest stage and
        # then silence, rather than a walk down from 60 forever.
        (-3000, None, 15),
        (-3000, 15, None),
        # No date to measure from.
        (None, None, None),
    ],
)
def test_the_rule_is_a_pure_function_of_three_arguments(days, last, expected):
    """`stage_due` reads its arguments and nothing else: no clock, no
    graph."""
    due, reason = stage_due(days, last, [60, 30, 15])
    assert due == expected
    assert (reason == "") is (due is not None)


def test_a_training_record_and_a_driver_row_are_one_type(booked):
    """One `Credential` type, distinguished only by `category`.

    A second record model for driver files would have produced two compliance
    sweeps that had to agree with each other; instead the training refresher
    and the CDL differ in one field and are handled by the same Rule, the same
    Action and the same stamps.
    """
    _close_window(booked, "one type", TODAY)

    training = _cred(booked, f"{TRAINING_SOURCE}:TR-1001")
    licence = _cred(booked, f"{DRIVER_SOURCE}:DQ-2001")
    assert training.type == licence.type == onto.CREDENTIAL
    assert training.props["category"] == "training"
    assert licence.props["category"] == "licence"
    # Both stamped by the same pass, each at the stage its own date earned.
    assert training.props["last_notice_stage"] == "60"
    assert licence.props["last_notice_stage"] == "15"
    assert (
        training.props["last_notified_on"]
        == licence.props["last_notified_on"]
        == TODAY.isoformat()
    )
    assert training.props["status"] == "expiring"


# ---- counts, not gates -----------------------------------------------------


def test_an_untracked_asset_never_alerts_and_is_counted(booked):
    """The skid-steer exclusion, as a property of the row rather than a list.

    Excluded where the rows are gathered and **counted** — never carried to
    the Rule to be rejected — because a register that quietly stopped carrying
    due dates has to show up as a rising number rather than as a silent
    absence of alerts.
    """
    _, run = _close_window(booked, "the fleet", TODAY)

    skid = booked.graph.get(
        object_id(onto.EQUIPMENT, f"{ASSET_SOURCE}:SKID-3")
    )
    assert skid.props["inspection_tracked"] == "no"
    assert run.outputs["equipment"] == 2
    assert run.outputs["untracked_assets"] == 1
    assert run.outputs["inspections"] == 1
    assert "excluded and counted" in run.step("x2").summary
    # It reached neither the Rule nor the exception screen.
    assert not any(
        "Skid Steer" in move["subject"] for move in run.outputs["moves"]
    )
    for rows in _screened(run).values():
        assert not any("SKID-3" in row for row in rows)


def test_a_unit_whose_due_date_is_dropped_stops_alerting_and_its_history_stays(
    booked,
):
    """The exclusion is about the unit, and that is why it is walked from one.

    A register that stops carrying a due date is the source saying this unit is
    no longer on a cycle. The examination that was already recorded is not a
    lie and is not deleted — it simply stops being something anybody is
    notified about, which walking the `Inspection` rows instead of the units
    could not have expressed.
    """
    _, first = _close_window(booked, "tracked", TODAY)
    assert first.outputs["inspections"] == 1
    inspections_before = len(booked.graph.find(onto.INSPECTION))

    dropped = {
        "source": ASSET_SOURCE, "watermark": "2026-09-08T05:00:00Z",
        "equipment": [
            {"external_id": "A-14", "equipment_id": "TRK-14",
             "name": "Truck 14", "category": "truck", "home_site": "Mazo",
             "last_inspection_date": "2025-10-01"},
        ],
    }
    booked.sync.deliver(
        SyncRequest.model_validate(dropped), actor="system:test"
    )

    _, second = _close_window(booked, "untracked now", date(2026, 9, 8))
    assert second.outputs["untracked_assets"] == 2
    assert second.outputs["inspections"] == 0
    # Nothing was deleted: the examination is still on the graph.
    assert len(booked.graph.find(onto.INSPECTION)) == inspections_before


def test_a_category_the_policy_does_not_track_is_carried_and_counted(booked):
    """*A category not on the list is carried in the graph and never alerted
    on. Adding one is a line here.* — the document's own words."""
    _, run = _close_window(booked, "vocabulary", TODAY)

    orientation = _cred(booked, f"{TRAINING_SOURCE}:TR-1006")
    assert orientation is not None
    assert "last_notice_stage" not in orientation.props
    assert run.outputs["category_untracked"] == 1
    assert "does not track" in run.step("x2").summary


def test_the_tracked_category_list_is_a_line_in_the_document(world):
    """*Adding one is a line here.* — the other way round, proved by an edit.

    Drop `clearance` from the governed list and the confined-space row stops
    being alerted on and starts being counted, with no code change and no
    deploy. Its exception class empties too, which is the whole point: a
    category the policy does not track is *carried in the graph and never
    alerted on*, not carried into somebody's queue.
    """
    _org(world)
    asset = world.knowledge.repo.get(POLICY_ASSET_ID)
    world.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "- categories tracked: certification, training, licence, "
            "medical, clearance",
            "- categories tracked: certification, training, licence, medical",
        ),
        updated_by="test",
    ))
    for delivery in (_TRAINING, _DRIVER, _ASSETS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )

    _, run = _close_window(world, "narrowed", TODAY)
    assert "clearance" not in run.outputs["stages"]
    assert run.outputs["category_untracked"] == 2  # orientation + clearance
    assert run.outputs["alerted"] == 5             # six in the base fixture
    # The unresolved-holder row was the clearance row, so that class is empty
    # and its Task is not raised.
    assert _screened(run)[UNRESOLVED_HOLDER] == []
    assert run.outputs["exception_tasks"] == 3


def test_a_word_the_issuing_vocabulary_does_not_hold_stays_counted(world):
    """The honest limit of "adding one is a line here", stated as a test.

    `connectors.compliance.CREDENTIAL_CATEGORIES` is where an issuing system's
    word becomes our enum, and a word it does not hold lands with **no**
    `category` at all — the feed counts that as `category_unmapped` on the way
    in. So widening the policy alone cannot rescue such a row: the second line
    is the connector's table, and this flow's count is what makes the gap
    visible rather than silent.
    """
    _org(world)
    asset = world.knowledge.repo.get(POLICY_ASSET_ID)
    world.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "medical, clearance", "medical, clearance, safety-orientation"
        ),
        updated_by="test",
    ))
    for delivery in (_TRAINING, _DRIVER, _ASSETS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )

    orientation = _cred(world, f"{TRAINING_SOURCE}:TR-1006")
    assert "category" not in orientation.props
    _, run = _close_window(world, "half-widened", TODAY)
    assert run.outputs["category_untracked"] == 1
    assert run.outputs["alerted"] == 6
    assert "last_notice_stage" not in world.graph.get(orientation.id).props


def test_a_window_with_nothing_due_writes_nothing_and_raises_nothing(world):
    """An empty book, and the honest shape of an empty compliance day."""
    _org(world)
    _, run = _close_window(world, "an empty book", TODAY)

    assert run.status == "succeeded", run.error
    assert run.outputs["considered"] == 0
    assert run.outputs["alerted"] == 0
    assert run.outputs["exception_tasks"] == 0
    assert _tasks(world) == []
    assert run.writes() == []
    assert "nothing tracked" in run.step("x2").summary
    assert "nobody to ask" in run.step("k1").summary


# ---- the four classes ------------------------------------------------------


def _screened(run) -> dict:
    return {
        name: [
            row["natural_key"] for row in
            run.step("c1").output["classes"][name]
        ]
        for name in EXCEPTION_CLASSES
    }


def test_the_four_exception_classes_are_four_tasks_and_not_n(booked):
    """Five rows, four classes, four Tasks — and one class carries two rows.

    *A window that finds eleven unreadable dates asks one question eleven
    times over, which is one question.*
    """
    _, run = _close_window(booked, "exceptions", TODAY)

    assert _screened(run) == {
        MISSING_DATE: [
            f"{DRIVER_SOURCE}:DQ-2002", f"{TRAINING_SOURCE}:TR-1002",
        ],
        UNRESOLVED_HOLDER: [f"{TRAINING_SOURCE}:TR-1003"],
        RETAINED_PAST_HORIZON: [f"{TRAINING_SOURCE}:TR-1005"],
        TERMINATED_HOLDER: [f"{TRAINING_SOURCE}:TR-1004"],
    }
    assert run.outputs["exception_tasks"] == 4

    tasks = _tasks(booked)
    assert len(tasks) == 4
    assert {t.queue for t in tasks} == {"hr-safety"}
    assert {t.kind for t in tasks} == {"exception"}
    assert {t.case_ref for t in tasks} == {
        f"compliance-exception:{name}" for name in EXCEPTION_CLASSES
    }
    # The two-row class is one Task carrying both rows, not two Tasks.
    dates = next(
        t for t in tasks if t.case_ref.endswith(MISSING_DATE)
    )
    assert "2 row(s)" in dates.summary
    assert len([e for e in dates.evidence if e.label not in
                ("Class", "Rows")]) == 2


def test_one_row_belongs_to_exactly_one_class(booked):
    """Four aggregates that each carried the same row would be four people
    asking about it. The precedence order is what makes the assignment
    defensible, and it is asserted as a partition."""
    _, run = _close_window(booked, "partition", TODAY)
    screened = _screened(run)
    everything = [key for rows in screened.values() for key in rows]
    assert len(everything) == len(set(everything)) == 5


def test_a_row_past_the_retention_horizon_is_reported_and_nothing_is_deleted(
    booked,
):
    """The customer's own rule, and the one that is easy to get wrong.

    *Flag records older than 7 years for deletion — no automatic deletion,
    reported for manual review.* So the Task is a report with no proposal on
    it: there is no Action in this system that deletes a compliance record.
    """
    before = len(booked.graph.find())
    _, run = _close_window(booked, "retention", TODAY)

    task = next(
        t for t in _tasks(booked)
        if t.case_ref.endswith(RETAINED_PAST_HORIZON)
    )
    assert task.proposal is None
    assert "no automatic deletion" in task.summary
    assert "Nothing is deleted" in " ".join(
        e.value for e in task.evidence
    )
    assert task.priority == "low"
    assert run.step("c1").output["horizon"] == "2019-09-07"

    # Structurally: nothing was removed and the row is still active.
    assert len(booked.graph.find()) == before
    row = _cred(booked, f"{TRAINING_SOURCE}:TR-1005")
    assert row.status == "active"
    # And no Action this flow can name deletes anything.
    from app.gateway import ACTIONS

    assert ACTIONS[ALERT_ACTION].target == COMPLIANCE_RECORDS
    assert "delete" not in ALERT_ACTION


def test_the_same_rows_found_again_are_the_same_question(booked):
    """A daily cadence that opened a new copy of one question every morning
    would bury the desk it is trying to help. A changed row set is a different
    question and gets its own Task."""
    _close_window(booked, "day one", TODAY)
    assert len(_tasks(booked)) == 4

    _, again = _close_window(booked, "day two", date(2026, 9, 8))
    assert again.outputs["exception_tasks"] == 0
    assert again.outputs["exception_tasks_standing"] == 4
    assert len(_tasks(booked)) == 4
    assert "already standing" in again.step("k1").summary


def test_the_aggregates_route_through_the_governed_table(booked):
    """The desk is read, not hardcoded — proved by moving it."""
    asset = booked.knowledge.repo.get(POLICY_ASSET_ID)
    booked.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "| Over 0 | hr-safety |", "| Over 0 | field-operations |"
        ).replace("- Fallback desk: hr-safety",
                  "- Fallback desk: field-operations"),
        updated_by="test",
    ))
    _close_window(booked, "rerouted", TODAY)
    assert {t.queue for t in _tasks(booked)} == {"field-operations"}


def test_a_resolver_in_the_table_falls_back_and_says_why(booked):
    """*There is no submitter to route back to and no amount to band on.*

    So `submitter` cannot be answered by an aggregate about records, and the
    governed fallback stands — stated on the Task rather than swallowed,
    because a deployment quietly routing everything to the fallback is a broken
    table wearing a working flow.
    """
    asset = booked.knowledge.repo.get(POLICY_ASSET_ID)
    booked.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "| Over 0 | hr-safety |", "| Over 0 | submitter |"
        ),
        updated_by="test",
    ))
    _close_window(booked, "resolver", TODAY)
    tasks = _tasks(booked)
    assert {t.queue for t in tasks} == {"hr-safety"}
    assert "no submitter to route back to" in tasks[0].summary


def test_the_review_screen_is_declared_for_the_kind_this_flow_raises(booked):
    """Lane 6 builds the widget; the manifest degrades to the generic pane
    until then, which is why the id crosses the wire and not the screen."""
    from app.task_screens import resolve_ui

    ui = resolve_ui(FLOW_ID, 1)
    assert ui is not None
    screen = ui.screen_for("exception")
    assert screen.screen_id == EXCEPTION_SCREEN
    assert ui.screen_for("decision") is None


# ---- the one write ---------------------------------------------------------


def _entries(world, action: str) -> list:
    return [e for e in world.gateway.ledger.list() if e.action == action]


def test_one_guarded_action_for_the_whole_window(booked):
    """A windowed flow lands bulk observations.

    Six rows, one ledger entry — because six guarded writes would be six
    entries saying the same thing about one pass.
    """
    _, run = _close_window(booked, "one write", TODAY)

    entries = _entries(booked, ALERT_ACTION)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.outcome == "committed"
    assert entry.regime == "decision"
    assert entry.target == COMPLIANCE_RECORDS
    assert entry.flow_id == FLOW_ID
    assert entry.step_id == "a1"
    assert entry.principal_uid == f"flow:{FLOW_ID}"
    assert entry.write_count == 6
    # Every ref is a compliance row, and nothing else was touched.
    assert all(
        ref.startswith(f"graph:{onto.CREDENTIAL}/")
        or ref.startswith(f"graph:{onto.INSPECTION}/")
        for ref in entry.writes
    ), entry.writes
    assert run.step("a1").writes == entry.writes


def test_the_action_carries_no_queue_because_noticing_is_nobody_s_work(booked):
    """`record-disagreements`' reasoning exactly, and the structural proof.

    A queue binding says whose *work* an Action is. Noticing that a date is
    close is nobody's work — the Flow does it on its own authority, unasked —
    and a queue here would refuse the write outright, since a Flow principal
    owns only the queues its declaration binds it to.
    """
    from app.gateway import ACTIONS

    action = ACTIONS[ALERT_ACTION]
    assert action.queue == ""
    assert action.queue_from_task is False
    assert action.regime == "decision"

    _close_window(booked, "no queue", TODAY)
    assert _entries(booked, ALERT_ACTION)[0].outcome == "committed"


def test_the_inspection_stamp_stays_inside_what_the_ontology_declares(booked):
    """Two types, two write-sets, because the ontology says so.

    A `Credential` carries `status` and `retain_until`; an `Inspection` carries
    neither. Claiming an undeclared field would be refused at the Gateway, so
    this is the ontology doing its job rather than a caller remembering.
    """
    _close_window(booked, "two types", TODAY)

    inspection = next(iter(booked.graph.find(onto.INSPECTION)))
    assert inspection.props["last_notice_stage"] == "30"
    assert inspection.props["last_notified_on"] == TODAY.isoformat()
    assert "status" not in inspection.props
    assert "retain_until" not in inspection.props

    credential = _cred(booked, WALKER)
    assert credential.props["status"] == "expiring"
    assert credential.props["retain_until"] == "2033-11-01"


def test_the_notice_names_the_supervisor_it_would_go_to(booked):
    """The fetching half did the joining, so the Rule did not have to.

    A credential's notice is addressed through `reports_to`; an inspection's
    through `Equipment —operated_by→ Team —led_by→ Employee`, which is the
    traversal that edge exists for.
    """
    _, run = _close_window(booked, "addresses", TODAY)
    notify = {
        move["kind"]: move["notify"] for move in run.outputs["moves"]
    }
    assert notify["credential"] == HEATHER
    assert notify["inspection"] == DANA
    assert run.outputs["supervisor_unresolved"] == 0
    assert run.outputs["holder_unresolved"] == 1


def test_the_pass_makes_no_model_call(booked):
    """Every judgment here is a date comparison against a governed number."""
    _, run = _close_window(booked, "no models", TODAY)
    assert run.model_calls == 0
    assert run.cost_usd == 0.0
    assert not any(step.kind == "modelcall" for step in run.steps)


def test_the_emitted_fact_carries_the_per_stage_counts(booked):
    """The run's own record of the pass, and what the fleet charts."""
    _, run = _close_window(booked, "the fact", TODAY)
    emitted = run.step("e1")
    assert emitted.name == ALERT_EVENT
    assert emitted.kind == "event"
    assert "2 at 60 days" in emitted.summary
    assert "2 at 30 days" in emitted.summary
    assert "2 at 15 days" in emitted.summary
    assert run.outputs["by_stage"] == {"60": 2, "30": 2, "15": 2}


def test_the_window_bound_defers_rather_than_drops(booked):
    """A declared bound whose remainder is the next window's work.

    Stated on the run, because a count nobody states is a backlog nobody knows
    about.
    """
    asset = booked.knowledge.repo.get(POLICY_ASSET_ID)
    booked.knowledge.update(asset.id, AssetUpdate(
        content=asset.content + "\n- records per compliance window: 2\n",
        updated_by="test",
    ))
    _, run = _close_window(booked, "bounded", TODAY)

    assert run.outputs["considered"] == 8
    assert run.outputs["taken"] == 2
    assert run.outputs["deferred"] == 6
    assert run.outputs["alerted"] == 2
    assert "left for the next window" in run.step("x2").summary


def test_the_run_reads_one_revision_of_one_asset(booked):
    """One governed read for the whole pass, so two steps cannot be looking at
    two revisions of the same document."""
    _, run = _close_window(booked, "one revision", TODAY)
    refs = [ref for ref in run.refs if ref.startswith("knowledge:")]
    assert refs == [f"knowledge:{POLICY_ASSET_ID}@rev1"]
    assert run.outputs["policy_ref"] == refs[0]
    assert run.outputs["cadence"] == "daily"


def test_the_policy_is_what_decides_whether_work_happens(world):
    """The window opens on a timer; the asset decides what an open window
    means. A policy with no stages at all is a pass that alerts on nothing and
    says so, rather than a pass that invents a stage."""
    _org(world)
    for delivery in (_TRAINING, _DRIVER, _ASSETS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    assert AlertPolicy(stages=[]).widest_stage == 0

    asset = world.knowledge.repo.get(POLICY_ASSET_ID)
    world.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "- notice stages: 60, 30, 15", "- notice stages: 1"
        ),
        updated_by="test",
    ))
    _, run = _close_window(world, "one day out", TODAY)
    assert run.outputs["stages"] == [1]
    assert run.outputs["in_window"] == 1  # only the row expired years ago
    assert run.outputs["alerted"] == 1
