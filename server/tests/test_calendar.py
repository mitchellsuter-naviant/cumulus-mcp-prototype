"""Calendars: a mirrored schedule that is a graph, not a table.

The competency questions the two new types were admitted for, plus the
invariants that make them honest — a part identified through its owner, an
external guest counted rather than minted, and a mirror that never claims to
be the system of record.

The week these run against is a test fixture now (`tests/demo_world.py`); it
used to be a startup seed in `app/calendars.py`, which meant a deployed server
invented three weeks of meetings for people it had never synced.
"""

from datetime import date, datetime, timedelta

import pytest

from app import personas
from app.calendars import CalendarService
from app.graph import object_id
from tests import demo_world
from tests import world as test_world

CONTROLLER = demo_world.CONTROLLER


@pytest.fixture()
def world():
    w = test_world.fresh()
    demo_world.seed_expenses(w.expenses)
    demo_world.seed_payables(w.payables, sync=w.sync)
    demo_world.seed_workforce(w.sync, w.graph)
    demo_world.seed_calendars(w.sync, w.graph)
    return w


@pytest.fixture()
def client(_client, world):
    return _client


def test_every_employee_has_a_calendar(world):
    """"Populate all users" is a claim the graph can be asked to defend."""
    calendars = CalendarService(world.graph).calendars()
    assert {c.owner_email for c in calendars} == {
        e["email"] for e in demo_world.EMPLOYEES
    }
    assert all(c.events for c in calendars)
    # The provider is the system of record; the mirror says so.
    assert all(c.provider == "microsoft-365" for c in calendars)


def test_delivering_twice_changes_nothing(world):
    before = world.graph.stats()
    assert demo_world.seed_calendars(world.sync, world.graph) == 0
    assert world.graph.stats() == before


def test_an_event_is_a_part_of_its_calendar(world):
    """The weak-entity rule, enforced rather than merely followed."""
    from app.ontology import ONTOLOGY

    assert ONTOLOGY.owner_of("CalendarEvent") == "Calendar"
    assert ONTOLOGY.parts_of("Calendar") == ["CalendarEvent"]
    calendar = world.graph.get(object_id("Calendar", CONTROLLER))
    event = world.graph.neighbors(calendar.id, "has_event", "out")[0][1]
    assert calendar.natural_key in event.natural_key
    assert world.graph.check_invariants() == []


def test_a_meeting_is_one_event_per_calendar(world):
    """Four attendees, four rows — what the provider actually stores — and
    the copies agree by linking to the same people."""
    graph = world.graph
    controller = graph.get(object_id("Employee", CONTROLLER))
    calendar = graph.neighbors(controller.id, "has_calendar", "out")[0][1]
    checkpoints = [
        event for _, event in graph.neighbors(calendar.id, "has_event", "out")
        if event.props.get("title", "").startswith("Month-end close")
    ]
    assert checkpoints, "the Controller's week has a close checkpoint"
    one = checkpoints[0]
    attendees = graph.neighbors(one.id, "attended_by", "out")
    assert len(attendees) == 4
    # The same meeting, read from another attendee's calendar, is a different
    # object with the same attendance.
    it = graph.get(object_id("Employee", demo_world.IT))
    theirs = graph.neighbors(it.id, "has_calendar", "out")[0][1]
    mirror = [
        event for _, event in graph.neighbors(theirs.id, "has_event", "out")
        if event.props.get("external_id") == one.props.get("external_id")
    ]
    assert len(mirror) == 1 and mirror[0].id != one.id


def test_an_external_guest_is_counted_never_minted(world):
    """A customer sponsor on a QBR is an attendee, not an employee."""
    assert world.graph.get(
        object_id("Employee", demo_world.CUSTOMER_SPONSOR)
    ) is None
    runs = list(world.engine.list(flow_id="calendar-sync"))
    assert runs, "the fixture ran through the real sync flow"
    assert runs[0].status == "succeeded"


def test_the_agenda_traverses_rather_than_matching_strings(world):
    """"What is on the Controller's week, and who is he committed to."""
    service = CalendarService(world.graph)
    monday = date.today() - timedelta(days=date.today().weekday())
    agenda = service.agenda(CONTROLLER, start=monday.isoformat(), days=7)
    assert agenda.name == "Raymond Fitzgerald"
    assert agenda.persona_note == ""  # he is in the workforce
    assert agenda.events
    starts = [e.starts_at for e in agenda.events]
    assert starts == sorted(starts)
    friday = [
        e for e in agenda.events
        if datetime.fromisoformat(e.starts_at).date() == monday + timedelta(days=4)
    ]
    assert any(e.kind == "meeting" for e in friday)
    meeting = next(e for e in agenda.events if e.kind == "meeting" and e.attendees)
    assert meeting.attendees[0].is_owner  # your own copy puts you first


def test_a_principal_outside_the_workforce_gets_an_empty_week_and_a_reason(world):
    """No substitution. A named colleague's week reads as your own the moment
    it renders, so the honest answer is an empty grid with a sentence on it."""
    agenda = CalendarService(world.graph).agenda("dev@localhost")
    assert agenda.email == "dev@localhost"
    assert agenda.events == []
    assert agenda.calendar is None
    assert "not in the workforce" in agenda.persona_note
    # And nobody else's name appears in it.
    assert "Raymond" not in agenda.persona_note


def test_the_delivery_is_what_the_provider_would_push():
    """Built without a graph: a payload, not a fixture reaching into storage."""
    delivery = demo_world.build_calendar_delivery(today=date(2026, 8, 28))
    # Under the declared contract of the system that really serves the
    # estate's calendars.
    assert delivery["source"] == "office-calendar"
    assert len(delivery["calendars"]) == len(demo_world.EMPLOYEES)
    # Wall-clock local, not a UTC instant: a recurring 9am stays 9am.
    assert all(
        "Z" not in event["starts_at"] and "+" not in event["starts_at"]
        for event in delivery["calendar_events"]
    )
    monday = date(2026, 8, 24)
    days = {
        datetime.fromisoformat(e["starts_at"]).date()
        for e in delivery["calendar_events"]
    }
    assert min(days) >= monday - timedelta(days=7)
    assert max(days) <= monday + timedelta(days=20)


def test_the_api_serves_a_week_to_somebody_on_the_org_chart(client, world):
    resp = client.get(
        "/api/v1/calendar/me",
        headers={"X-Cumulus-Persona": "customer-user:controller"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == CONTROLLER
    assert body["persona_note"] == ""
    assert body["calendar"]["provider"] == "microsoft-365"
    assert body["events"]

    listed = client.get("/api/v1/calendar/calendars")
    assert listed.status_code == 200
    assert len(listed.json()) == len(demo_world.EMPLOYEES)


def test_the_operator_is_told_he_is_not_on_the_customers_org_chart(client, world):
    """The default persona is the Naviant engineer operating the deployment,
    and he is not one of the customer's employees. The screen says so instead
    of borrowing somebody's week."""
    body = client.get("/api/v1/calendar/me").json()
    assert body["email"] == personas.DEFAULT_PERSONA.email
    assert body["events"] == []
    assert "not in the workforce" in body["persona_note"]


def test_an_offset_stamped_event_lands_in_the_window(world):
    """The estate's Office feed stamps `+00:00`; the fixture week does not.

    Both spellings are legitimate and both arrive, so the window comparison
    has to be on the wall clock rather than on whichever one the last feed
    happened to use. Mixing them raised `can't compare offset-naive and
    offset-aware datetimes` on the first real pull — a 500 on `/calendar/me`
    that the mock feed had hidden for the life of the screen, because it only
    ever produced naive stamps.
    """
    from app.connectors.sync import SyncRequest

    monday = date.today() - timedelta(days=date.today().weekday())
    world.sync.deliver(SyncRequest.model_validate({
        "source": "office-calendar",
        "calendar_events": [{
            "external_id": "offset-stamped",
            "calendar_address": CONTROLLER,
            "title": "Board pre-read",
            "kind": "meeting",
            "starts_at": f"{monday.isoformat()}T16:00:00+00:00",
            "ends_at": f"{monday.isoformat()}T17:00:00+00:00",
            "organizer_email": CONTROLLER,
            "attendee_emails": [CONTROLLER],
        }],
    }), actor="system:test")

    service = CalendarService(world.graph)
    agenda = service.agenda(CONTROLLER, start=monday.isoformat(), days=7)
    assert "Board pre-read" in [e.title for e in agenda.events]
    # And the summary's "next block" reader survives it too.
    assert service.calendars()


def test_the_feed_appears_in_the_sources_lens(world):
    kinds = {(s.source, s.kind) for s in world.sync.sources()}
    assert ("office-calendar", "calendars") in kinds


def test_an_empty_world_serves_an_empty_week_rather_than_an_error(_client):
    """No estate configured, nothing synced: every calendar surface answers."""
    test_world.fresh()
    assert _client.get("/api/v1/calendar/calendars").json() == []
    body = _client.get("/api/v1/calendar/me").json()
    assert body["events"] == [] and body["calendar"] is None
    assert "not in the workforce" in body["persona_note"]
