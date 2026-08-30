"""Calendars: a mirrored schedule that is a graph, not a table.

The competency questions the two new types were admitted for, plus the
invariants that make them honest — a part identified through its owner, an
external guest counted rather than minted, and a mirror that never claims to
be the system of record.
"""

from datetime import date, datetime, timedelta

import pytest

from app.calendars import (
    DEMO_PERSONA,
    CalendarService,
    build_delivery,
    seed_calendars_if_empty,
    sync_calendar_additions,
)
from app.graph import object_id
from app.payables import seed_payables_if_empty
from app.reimbursements import seed_reimbursements_if_empty
from app.workforce import (
    EMPLOYEES,
    OPERATOR_EMAIL,
    seed_workforce_if_empty,
    sync_workforce_additions,
)
from tests import world as test_world


@pytest.fixture()
def world():
    w = test_world.fresh()
    seed_reimbursements_if_empty(w.expenses)
    seed_payables_if_empty(w.payables, sync=w.sync)
    seed_workforce_if_empty(w.sync, w.graph)
    seed_calendars_if_empty(w.sync, w.graph)
    return w


@pytest.fixture()
def client(_client, world):
    return _client


def test_every_employee_has_a_calendar(world):
    """"Populate all users" is a claim the graph can be asked to defend."""
    calendars = CalendarService(world.graph).calendars()
    assert {c.owner_email for c in calendars} == {e["email"] for e in EMPLOYEES}
    assert all(c.events for c in calendars)
    # The provider is the system of record; the mirror says so.
    assert all(c.provider == "microsoft-365" for c in calendars)


def test_seeding_twice_changes_nothing(world):
    before = world.graph.stats()
    assert seed_calendars_if_empty(world.sync, world.graph) == 0
    assert world.graph.stats() == before


def test_an_event_is_a_part_of_its_calendar(world):
    """The weak-entity rule, enforced rather than merely followed."""
    from app.ontology import ONTOLOGY

    assert ONTOLOGY.owner_of("CalendarEvent") == "Calendar"
    assert ONTOLOGY.parts_of("Calendar") == ["CalendarEvent"]
    calendar = world.graph.get(object_id("Calendar", DEMO_PERSONA))
    event = world.graph.neighbors(calendar.id, "has_event", "out")[0][1]
    assert calendar.natural_key in event.natural_key
    assert world.graph.check_invariants() == []


def test_a_meeting_is_one_event_per_calendar(world):
    """Four attendees, four rows — what the provider actually stores — and
    the copies agree by linking to the same people."""
    graph = world.graph
    dana = graph.get(object_id("Employee", DEMO_PERSONA))
    calendar = graph.neighbors(dana.id, "has_calendar", "out")[0][1]
    standups = [
        event for _, event in graph.neighbors(calendar.id, "has_event", "out")
        if event.props.get("title", "").startswith("Acme Phase 1")
    ]
    assert standups, "the persona's week has a daily standup"
    one = standups[0]
    attendees = graph.neighbors(one.id, "attended_by", "out")
    assert len(attendees) == 4
    # The same meeting, read from another attendee's calendar, is a different
    # object with the same attendance.
    priya = graph.get(object_id("Employee", "priya@customer.example"))
    hers = graph.neighbors(priya.id, "has_calendar", "out")[0][1]
    mirror = [
        event for _, event in graph.neighbors(hers.id, "has_event", "out")
        if event.props.get("external_id") == one.props.get("external_id")
    ]
    assert len(mirror) == 1 and mirror[0].id != one.id


def test_an_external_guest_is_counted_never_minted(world):
    """A client sponsor on a QBR is an attendee, not an employee."""
    assert world.graph.get(object_id("Employee", "raj.patel@acme.example")) is None
    runs = [
        r for r in world.engine.list(flow_id="calendar-sync")
    ]
    assert runs, "the seed ran through the real sync flow"
    assert runs[0].status == "succeeded"


def test_the_agenda_traverses_rather_than_matching_strings(world):
    """"What is on Dana's Friday, and who is she committed to" — links only."""
    service = CalendarService(world.graph)
    monday = date.today() - timedelta(days=date.today().weekday())
    agenda = service.agenda(DEMO_PERSONA, start=monday.isoformat(), days=7)
    assert agenda.name == "Dana Whitfield"
    assert agenda.persona_note == ""  # she is in the workforce
    assert agenda.events
    starts = [e.starts_at for e in agenda.events]
    assert starts == sorted(starts)
    friday = [
        e for e in agenda.events
        if datetime.fromisoformat(e.starts_at).date() == monday + timedelta(days=4)
    ]
    assert any(e.kind == "task-block" for e in friday)
    meeting = next(e for e in agenda.events if e.kind == "meeting" and e.attendees)
    assert meeting.attendees[0].is_owner  # your own copy puts you first


def test_an_unknown_principal_is_told_whose_day_it_is(world):
    """The local-dev principal is not an employee. Substituting silently
    would make the home screen quietly lie about whose schedule it shows."""
    agenda = CalendarService(world.graph).agenda("dev@localhost")
    assert agenda.email == DEMO_PERSONA
    assert "not in the workforce" in agenda.persona_note


def test_the_delivery_is_what_the_provider_would_push():
    """Built without a graph: the seed is a payload, not a fixture reaching
    into storage."""
    delivery = build_delivery(today=date(2026, 8, 28))
    assert delivery["source"] == "calendar-m365"
    assert len(delivery["calendars"]) == len(EMPLOYEES)
    # Wall-clock local, not a UTC instant: a recurring 9am stays 9am.
    assert all(
        "Z" not in event["starts_at"] and "+" not in event["starts_at"]
        for event in delivery["calendar_events"]
    )
    monday = date(2026, 8, 24)
    days = {
        datetime.fromisoformat(e["starts_at"]).date() for e in delivery["calendar_events"]
    }
    assert min(days) >= monday - timedelta(days=7)
    assert max(days) <= monday + timedelta(days=20)


def test_the_api_serves_my_week(client, world):
    resp = client.get("/api/v1/calendar/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == DEMO_PERSONA
    assert body["calendar"]["provider"] == "microsoft-365"
    assert body["events"]

    listed = client.get("/api/v1/calendar/calendars")
    assert listed.status_code == 200
    assert len(listed.json()) == len(EMPLOYEES)


def test_the_feed_appears_in_the_sources_lens(world):
    kinds = {(s.source, s.kind) for s in world.sync.sources()}
    assert ("calendar-m365", "calendars") in kinds


# ---- the operator: a person who arrives after the store was seeded ----------


def test_the_signed_in_operator_is_a_real_person(world):
    """The console's operator resolves to himself, not to a demo persona.

    Before he was on the roster every personal surface fell back to Dana and
    said so in `persona_note`, which is honest but is not his day.
    """
    agenda = CalendarService(world.graph).agenda(OPERATOR_EMAIL)
    assert agenda.email == OPERATOR_EMAIL
    assert agenda.name == "Mitchell Suter"
    assert agenda.persona_note == ""
    assert agenda.calendar is not None
    assert agenda.events


def test_a_late_arrival_lands_on_an_already_seeded_store(monkeypatch):
    """The seed passes only fire on an empty store, so a person added to the
    roster afterwards needs a delivery of their own — otherwise a deployment
    seeded last week would keep telling them they are not in the workforce."""
    from app import workforce

    roster = tuple(e for e in EMPLOYEES if e["email"] != OPERATOR_EMAIL)
    monkeypatch.setattr(workforce, "EMPLOYEES", roster)
    w = test_world.fresh()
    seed_reimbursements_if_empty(w.expenses)
    seed_payables_if_empty(w.payables, sync=w.sync)
    seed_workforce_if_empty(w.sync, w.graph)
    seed_calendars_if_empty(w.sync, w.graph)
    assert w.graph.get(object_id("Employee", OPERATOR_EMAIL)) is None

    # The roster gains him; the store catches up without a reset.
    monkeypatch.undo()
    assert sync_workforce_additions(w.sync, w.graph) == 1
    assert sync_calendar_additions(w.sync, w.graph) == 1

    person = w.graph.get(object_id("Employee", OPERATOR_EMAIL))
    assert person.props["title"] == "Director, Platform & Operations"
    # Delivered, not patched in: the same feed, the same authority.
    assert any(c.source == "hris:hris-workday" for c in person.claims)
    # The org chart and the skills came with him.
    assert [m.label for _, m in w.graph.neighbors(person.id, "reports_to", "out")] == [
        "Rowan Ellis"
    ]
    assert w.graph.neighbors(person.id, "member_of", "out")
    assert len(w.graph.neighbors(person.id, "has_capability", "out")) == 3

    agenda = CalendarService(w.graph).agenda(OPERATOR_EMAIL)
    assert agenda.persona_note == ""
    assert agenda.events
    # His own copy of a shared meeting carries the whole room.
    all_hands = next(e for e in agenda.events if e.title == "Firm all-hands")
    assert len(all_hands.attendees) == len(EMPLOYEES)
    assert w.graph.check_invariants() == []


def test_the_additions_pass_is_a_no_op_once_the_roster_agrees(world):
    before = world.graph.stats()
    assert sync_workforce_additions(world.sync, world.graph) == 0
    assert sync_calendar_additions(world.sync, world.graph) == 0
    assert world.graph.stats() == before
