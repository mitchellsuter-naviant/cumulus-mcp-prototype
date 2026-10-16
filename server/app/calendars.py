"""Calendars: the schedule, mirrored from the provider and read as a graph.

The console needed a home screen and a week view, and the honest way to build
one was not a table of rows beside the graph — it was to ask what a calendar
*is* in the ontology. Two types earned their place:

- **Calendar** — one person's schedule at a provider. A root, but deliberately
  not authoritative: Microsoft 365 owns it, we mirror it. Its competency
  questions are ones nothing else here can answer — what is on somebody's
  Friday, and when are they free.
- **CalendarEvent** — one block on one calendar, and a *part* of it. A meeting
  with four attendees is four events, one per calendar, because that is what
  every provider actually stores: each copy has its own id, its own reminders
  and its own response. What the copies agree on is expressed as
  `attended_by` links to the same people, so "who is Dana committed to this
  week" is a traversal rather than a string match on a title.

This module is now only the read side. It used to hold a mock delivery too — a
fabricated three-week schedule for an invented firm, run through the real
`calendar-sync` Flow at startup — and that went when the estate became the only
world: calendars arrive from the `office-calendar` feed like every other fact,
so a schedule Cumulus shows is one somebody actually keeps. The read side never
consulted the fixture anyway; it traverses links, which is why deleting the
fixture changed nothing here but the imports.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field

from .graph import GraphService, ObjectRecord, object_id
from .ontology import names as onto

log = logging.getLogger("cumulus.calendars")

#: The feed calendars arrive under — the same declared contract the
#: estate's own office calendars pull through (`mappings.py`, `office-calendar`).
#: Named here as well as there because the read side below joins on it: a
#: Calendar object's provenance has to be checkable against the contract the
#: rows arrived under.
CALENDAR_SOURCE = "office-calendar"
PROVIDER = "microsoft-365"


def _week_start(today: date | None = None) -> date:
    day = today or datetime.now().date()
    return day - timedelta(days=day.weekday())


# ---------------------------------------------------------------------------
# The read side
# ---------------------------------------------------------------------------


class Attendee(BaseModel):
    email: str
    name: str = ""
    required: bool = True
    response: str = "accepted"
    # True when this person's own copy of the event is the one being read.
    is_owner: bool = False


class AgendaEvent(BaseModel):
    id: str
    ref: str
    title: str
    kind: str = "meeting"
    starts_at: str = ""
    ends_at: str = ""
    all_day: bool = False
    location: str = ""
    status: str = "confirmed"
    organizer_email: str = ""
    organizer_name: str = ""
    agenda_ref: str = ""
    notes: str = ""
    attendees: list[Attendee] = Field(default_factory=list)


class CalendarSummary(BaseModel):
    id: str
    ref: str
    address: str
    name: str = ""
    provider: str = ""
    timezone: str = ""
    owner_email: str = ""
    owner_name: str = ""
    owner_title: str = ""
    events: int = 0
    next_event_at: str = ""
    next_event_title: str = ""


class Agenda(BaseModel):
    """One person's window of time, and who they are.

    `persona_note` is non-empty when the signed-in principal is not in the
    workforce, and says so. It never names a substitute: a week that is not
    yours is worse than no week at all, because it reads as yours.
    """

    email: str
    name: str = ""
    title: str = ""
    calendar: CalendarSummary | None = None
    start_date: str = ""
    days: int = 7
    events: list[AgendaEvent] = Field(default_factory=list)
    persona_note: str = ""


def _parse(value: str) -> datetime | None:
    """One block's start, as a wall-clock time with no offset attached.

    Providers are inconsistent about this and both spellings are legitimate:
    the estate's Office feed stamps `2026-08-28T09:00:00+00:00`, and a naive
    `2026-08-28T09:00:00` is what a mirror of a floating recurrence looks
    like. A window is a *day range on somebody's calendar*, so the comparison
    that matters is the wall clock; mixing the two spellings in one `<` is
    what raised `can't compare offset-naive and offset-aware datetimes` on the
    first real pull, once the mock feed that only ever produced naive stamps
    was gone.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed


class CalendarService:
    """Reads schedules out of the graph. Holds no state of its own."""

    def __init__(self, graph: GraphService) -> None:
        self.graph = graph

    # -- lookups ------------------------------------------------------------

    def employee(self, email: str) -> ObjectRecord | None:
        if not email:
            return None
        return self.graph.get(object_id(onto.EMPLOYEE, email))

    def resolve_person(self, email: str) -> tuple[ObjectRecord | None, str]:
        """The employee whose week this is, or a sentence saying there is none.

        A principal outside the workforce — the Naviant engineer operating the
        deployment, a support engineer, a machine credential — gets an empty
        week and a note. It used to get a named demo persona's week instead,
        which was a lie the moment anybody read it as their own; and once the
        mock world went there was no such persona to borrow from anyway. An
        empty world is a statement, not a broken screen.
        """
        person = self.employee(email)
        if person is not None:
            return person, ""
        note = (
            f"{email or 'This principal'} is not in the workforce, so there "
            "is no calendar to mirror. Switch to somebody on the org chart to "
            "read a week."
        )
        return None, note

    def calendar_of(self, person: ObjectRecord) -> ObjectRecord | None:
        for _, calendar in self.graph.neighbors(person.id, "has_calendar", "out"):
            return calendar
        return None

    # -- reads --------------------------------------------------------------

    def agenda(
        self, email: str, start: str = "", days: int = 7
    ) -> Agenda:
        person, note = self.resolve_person(email)
        if person is None:
            # An empty week that says why, rather than a blank grid: the note
            # is the whole difference between "nothing on" and "not you".
            return Agenda(
                email=email, start_date=start, days=days, persona_note=note
            )
        first = _parse(f"{start}T00:00:00") if start else None
        if first is None:
            first = datetime.combine(_week_start(), datetime.min.time())
        calendar = self.calendar_of(person)
        events = (
            self._events(calendar, first, days, owner_email=person.props.get("email", ""))
            if calendar is not None
            else []
        )
        return Agenda(
            email=person.props.get("email", email),
            name=person.label,
            title=person.props.get("title", ""),
            calendar=self._summarize(calendar, person) if calendar else None,
            start_date=first.date().isoformat(),
            days=days,
            events=events,
            persona_note=note,
        )

    def _events(
        self,
        calendar: ObjectRecord,
        first: datetime,
        days: int,
        owner_email: str = "",
    ) -> list[AgendaEvent]:
        last = first + timedelta(days=days)
        out: list[AgendaEvent] = []
        for _, event in self.graph.neighbors(calendar.id, "has_event", "out"):
            starts = _parse(str(event.props.get("starts_at", "")))
            if starts is None or not (first <= starts < last):
                continue
            out.append(self._view(event, owner_email))
        out.sort(key=lambda e: (e.starts_at, e.title))
        return out

    def _view(self, event: ObjectRecord, owner_email: str = "") -> AgendaEvent:
        props = event.props
        attendees: list[Attendee] = []
        for link, person in self.graph.neighbors(event.id, "attended_by", "out"):
            email = str(person.props.get("email", ""))
            attendees.append(Attendee(
                email=email,
                name=person.label,
                required=bool(link.props.get("required", True)),
                response=str(link.props.get("response", "accepted")),
                is_owner=bool(owner_email) and email == owner_email,
            ))
        attendees.sort(key=lambda a: (not a.is_owner, a.name))
        organizer = ""
        for _, person in self.graph.neighbors(event.id, "organized_by", "out"):
            organizer = person.label
            break
        return AgendaEvent(
            id=event.id,
            ref=event.ref(),
            title=props.get("title", event.label),
            kind=str(props.get("kind", "meeting")),
            starts_at=str(props.get("starts_at", "")),
            ends_at=str(props.get("ends_at", "")),
            all_day=str(props.get("all_day", "false")) == "true",
            location=str(props.get("location", "")),
            status=str(props.get("status", "confirmed")),
            organizer_email=str(props.get("organizer_email", "")),
            organizer_name=organizer,
            agenda_ref=str(props.get("agenda_ref", "")),
            notes=str(props.get("notes", "")),
            attendees=attendees,
        )

    def _summarize(
        self, calendar: ObjectRecord, owner: ObjectRecord | None = None
    ) -> CalendarSummary:
        now = datetime.now()
        events = [
            event for _, event in
            self.graph.neighbors(calendar.id, "has_event", "out")
        ]
        upcoming = sorted(
            (
                (starts, event)
                for event in events
                if (starts := _parse(str(event.props.get("starts_at", ""))))
                and starts >= now
            ),
            key=lambda pair: pair[0],
        )
        if owner is None:
            owner = next(
                (
                    person for _, person in
                    self.graph.neighbors(calendar.id, "has_calendar", "in")
                ),
                None,
            )
        return CalendarSummary(
            id=calendar.id,
            ref=calendar.ref(),
            address=str(calendar.props.get("address", calendar.natural_key)),
            name=str(calendar.props.get("name", calendar.label)),
            provider=str(calendar.props.get("provider", "")),
            timezone=str(calendar.props.get("timezone", "")),
            owner_email=str(calendar.props.get("owner_email", "")),
            owner_name=owner.label if owner else "",
            owner_title=str(owner.props.get("title", "")) if owner else "",
            events=len(events),
            next_event_at=upcoming[0][0].isoformat() if upcoming else "",
            next_event_title=(
                str(upcoming[0][1].props.get("title", "")) if upcoming else ""
            ),
        )

    def calendars(self) -> list[CalendarSummary]:
        """Every calendar in the system — the proof that everyone has one."""
        return sorted(
            (self._summarize(calendar) for calendar in self.graph.find(onto.CALENDAR)),
            key=lambda c: c.owner_name or c.address,
        )


def build_calendar_service(graph: GraphService) -> CalendarService:
    return CalendarService(graph)
