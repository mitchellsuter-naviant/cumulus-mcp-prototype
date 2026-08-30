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

This module holds three things: the mock delivery (what the provider would
push), the seeding pass that runs it through the `calendar-sync` Flow, and the
read side the console calls. Only the first is fiction — the events land in
the real graph, through the real sync flow, journalled like every other feed,
and the read side traverses links rather than consulting a fixture.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field

from . import workforce
from .graph import GraphService, ObjectRecord, object_id
from .workforce import OPERATOR_EMAIL

log = logging.getLogger("cumulus.calendars")

CALENDAR_SOURCE = "calendar-m365"
PROVIDER = "microsoft-365"

# Whose calendar the console shows when the signed-in principal is not in the
# workforce — which is every local-dev session, where the principal is
# `dev@localhost`. Named rather than "the first employee we find", so the demo
# is stable, and surfaced in the response rather than hidden, so nobody
# mistakes it for their own schedule.
DEMO_PERSONA = "dana@customer.example"

# The firm works out of Boston; the two remote people keep their calendars in
# the firm's zone, as consultants do.
DEFAULT_TZ = "America/New_York"


# ---------------------------------------------------------------------------
# The mock delivery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockSpec:
    """One block in the weekly template.

    `attendees` is what makes a spec a meeting rather than a personal block: a
    copy of the event lands on every attendee's calendar, exactly as the
    provider would deliver it.
    """

    slug: str
    title: str
    kind: str  # meeting | focus | task-block | deadline | out-of-office
    day: int  # 0 = Monday … 6 = Sunday
    start: str  # "HH:MM" wall clock
    minutes: int
    organizer: str
    attendees: tuple[str, ...] = ()
    location: str = ""
    agenda_ref: str = ""
    notes: str = ""
    all_day: bool = False
    optional: tuple[str, ...] = ()
    # Which weeks this appears in, relative to the current one. Recurring
    # blocks appear in all three; a one-off is pinned to this week.
    weeks: tuple[int, ...] = (-1, 0, 1)

    @property
    def invitees(self) -> tuple[str, ...]:
        return self.attendees or (self.organizer,)


# The console's operator — declared with the rest of the firm so his week is
# built the same way theirs is. See `workforce.OPERATOR_EMAIL`.
MITCHELL = OPERATOR_EMAIL
ROWAN = "rowan@customer.example"
DANA = "dana@customer.example"
PRIYA = "priya@customer.example"
KIM = "kim@customer.example"
SAM = "sam@customer.example"
PAT = "pat@customer.example"
MARCUS = "marcus@customer.example"
ELENA = "elena@customer.example"
NADIA = "nadia@customer.example"
TOMAS = "ap@customer.example"

# A client sponsor: a real attendee who is not our employee. The sync counts
# them as unresolved and mints nothing, which is the point of including one.
CLIENT_SPONSOR = "raj.patel@acme.example"

# The recurring week. Every one of these lands on several calendars, so the
# same meeting is readable from either end: what is on my Wednesday, and who
# have I committed to.
MEETINGS: tuple[BlockSpec, ...] = (
    BlockSpec(
        slug="acme-standup", title="Acme Phase 1 — daily standup",
        kind="meeting", day=0, start="08:45", minutes=15, organizer=DANA,
        attendees=(DANA, PRIYA, KIM, SAM), location="Teams",
        agenda_ref="engagement:acme-phase-1",
        notes="Blockers only; anything longer becomes a working session.",
    ),
    BlockSpec(
        slug="all-hands", title="Firm all-hands", kind="meeting", day=0,
        start="09:15", minutes=30, organizer=ROWAN,
        attendees=(ROWAN, MITCHELL, DANA, PRIYA, KIM, SAM, PAT, MARCUS, ELENA,
                   NADIA, TOMAS),
        location="Boardroom · Teams",
    ),
    BlockSpec(
        slug="portfolio-review", title="Delivery portfolio review",
        kind="meeting", day=0, start="13:00", minutes=60, organizer=DANA,
        attendees=(DANA, PRIYA, SAM, ROWAN, MITCHELL), location="Boardroom",
        notes="Scope, staffing and margin across every live engagement.",
    ),
    BlockSpec(
        slug="de-standup", title="Data engineering standup", kind="meeting",
        day=1, start="10:00", minutes=45, organizer=PRIYA,
        attendees=(PRIYA, KIM), location="Teams",
    ),
    BlockSpec(
        slug="platform-session", title="Platform working session",
        kind="meeting", day=1, start="15:00", minutes=60, organizer=SAM,
        attendees=(SAM, PAT), location="Teams",
    ),
    BlockSpec(
        slug="advisory-sync", title="Advisory practice sync", kind="meeting",
        day=2, start="09:30", minutes=45, organizer=MARCUS,
        attendees=(MARCUS, ELENA, DANA), location="Teams",
    ),
    BlockSpec(
        slug="finance-review", title="Finance & operations review",
        kind="meeting", day=2, start="14:00", minutes=60, organizer=NADIA,
        attendees=(NADIA, TOMAS, ROWAN, MITCHELL), location="Boardroom",
        agenda_ref="workspace:accounts-payable",
        notes="Payables position, revenue recognition, and the week's holds.",
    ),
    BlockSpec(
        slug="ap-huddle", title="AP exceptions huddle", kind="meeting",
        day=3, start="11:00", minutes=45, organizer=TOMAS,
        attendees=(TOMAS, NADIA, DANA), location="Teams",
        agenda_ref="queue:expense-review",
        notes="Invoices the three-way match blocked, and what unblocks them.",
    ),
    BlockSpec(
        slug="weekly-close", title="Weekly close — pipeline & staffing",
        kind="meeting", day=4, start="16:30", minutes=30, organizer=ROWAN,
        attendees=(ROWAN, DANA, NADIA, MITCHELL), location="Boardroom",
    ),
)

# The same standup, the other four mornings. Written out rather than looped
# so each day is addressable — a Wednesday standup that moved is a different
# event, not an index into a recurrence.
MEETINGS += tuple(
    BlockSpec(
        slug=f"acme-standup-d{day}", title="Acme Phase 1 — daily standup",
        kind="meeting", day=day, start="08:45", minutes=15, organizer=DANA,
        attendees=(DANA, PRIYA, KIM, SAM), location="Teams",
        agenda_ref="engagement:acme-phase-1",
    )
    for day in (1, 2, 3, 4)
)

# Personal blocks: focus time, the task blocks people hold for queue work, and
# the deadlines that are not meetings at all.
PERSONAL: tuple[BlockSpec, ...] = (
    BlockSpec(
        slug="dana-invoice-block", title="Task block — held invoices",
        kind="task-block", day=4, start="09:30", minutes=30, organizer=DANA,
        agenda_ref="queue:expense-review",
        notes="Clear whatever the AP flow escalated this week.",
    ),
    BlockSpec(
        slug="dana-blueprint", title="Focus — Blueprint v13 review",
        kind="focus", day=4, start="14:30", minutes=90, organizer=DANA,
    ),
    BlockSpec(
        slug="dana-qbr-dry-run", title="QBR dry run — Acme Phase 2",
        kind="meeting", day=4, start="11:00", minutes=45, organizer=DANA,
        attendees=(DANA, MARCUS, PRIYA), location="Boardroom",
        agenda_ref="engagement:acme-phase-2", weeks=(0,),
    ),
    BlockSpec(
        slug="dana-change-order", title="Decide: Acme change order",
        kind="task-block", day=4, start="13:30", minutes=30, organizer=DANA,
        agenda_ref="task:task_cur_0214", weeks=(0,),
        notes="Scope band exceeded; the policy override is waiting on a call.",
    ),
    BlockSpec(
        slug="dana-expense", title="Confirm dinner receipt — expense claim",
        kind="task-block", day=4, start="17:00", minutes=15, organizer=DANA,
        agenda_ref="workspace:expense-reimbursement", weeks=(0,),
    ),
    BlockSpec(
        slug="acme-qbr", title="Acme QBR — Raj Patel", kind="meeting",
        day=1, start="10:00", minutes=90, organizer=DANA,
        attendees=(DANA, ROWAN, MARCUS, CLIENT_SPONSOR),
        location="Acme · Cambridge", agenda_ref="engagement:acme-phase-2",
        weeks=(0, 1),
        notes="The client sponsor is not in the workforce — an attendee, "
              "never an employee.",
    ),
    BlockSpec(
        slug="dana-1-1-priya", title="1:1 — Priya Raghavan", kind="meeting",
        day=3, start="09:15", minutes=30, organizer=DANA,
        attendees=(DANA, PRIYA), location="Teams",
    ),
    BlockSpec(
        slug="rowan-partner-time", title="Focus — partner reading",
        kind="focus", day=2, start="07:30", minutes=60, organizer=ROWAN,
    ),
    BlockSpec(
        slug="rowan-board-prep", title="Board pack — final read",
        kind="deadline", day=4, start="12:00", minutes=0, organizer=ROWAN,
        all_day=True,
    ),
    BlockSpec(
        slug="priya-model-review", title="Focus — claims mart model review",
        kind="focus", day=2, start="13:00", minutes=120, organizer=PRIYA,
    ),
    BlockSpec(
        slug="priya-exemplar", title="Task block — curate vendor exemplar",
        kind="task-block", day=3, start="15:00", minutes=45, organizer=PRIYA,
        agenda_ref="queue:knowledge-curation",
    ),
    BlockSpec(
        slug="kim-pipeline", title="Focus — ingestion framework",
        kind="focus", day=1, start="13:00", minutes=180, organizer=KIM,
    ),
    BlockSpec(
        slug="kim-review", title="Code review sweep", kind="task-block",
        day=3, start="16:00", minutes=60, organizer=KIM,
    ),
    BlockSpec(
        slug="sam-landing-zone", title="Focus — landing zone hardening",
        kind="focus", day=2, start="10:30", minutes=120, organizer=SAM,
    ),
    BlockSpec(
        slug="sam-oncall", title="On call — platform", kind="out-of-office",
        day=4, start="09:00", minutes=0, organizer=SAM, all_day=True,
    ),
    BlockSpec(
        slug="pat-ci", title="Focus — CI module library", kind="focus",
        day=0, start="10:00", minutes=120, organizer=PAT,
    ),
    BlockSpec(
        slug="pat-cert", title="GCP certification study", kind="focus",
        day=3, start="08:00", minutes=60, organizer=PAT,
    ),
    BlockSpec(
        slug="marcus-workshop", title="Client workshop — operating model",
        kind="meeting", day=1, start="09:00", minutes=180, organizer=MARCUS,
        attendees=(MARCUS, ELENA), location="Northwind · Waltham",
    ),
    BlockSpec(
        slug="marcus-facilitation", title="Facilitation training",
        kind="meeting", day=3, start="13:00", minutes=120, organizer=MARCUS,
        attendees=(MARCUS, ELENA, DANA), location="Boardroom", weeks=(1,),
    ),
    BlockSpec(
        slug="elena-security-review", title="Security review — rollout plan",
        kind="meeting", day=3, start="15:00", minutes=45, organizer=ELENA,
        attendees=(ELENA, SAM), location="Teams",
    ),
    BlockSpec(
        slug="elena-writeup", title="Focus — change plan write-up",
        kind="focus", day=4, start="10:00", minutes=120, organizer=ELENA,
    ),
    BlockSpec(
        slug="nadia-close", title="Month-end close", kind="deadline",
        day=4, start="09:00", minutes=0, organizer=NADIA, all_day=True,
    ),
    BlockSpec(
        slug="nadia-margin", title="Focus — engagement margin review",
        kind="focus", day=2, start="10:00", minutes=90, organizer=NADIA,
    ),
    BlockSpec(
        slug="tomas-run", title="Task block — AP posting run",
        kind="task-block", day=0, start="15:00", minutes=90, organizer=TOMAS,
        agenda_ref="workspace:accounts-payable",
    ),
    BlockSpec(
        slug="tomas-statements", title="Task block — vendor statements",
        kind="task-block", day=2, start="11:00", minutes=60, organizer=TOMAS,
        agenda_ref="workspace:accounts-payable",
    ),
    # The operator's week. Platform work is mostly queue work and reviews, so
    # his blocks name the queues they are held for — which is the join the
    # provider cannot make and the console can.
    BlockSpec(
        slug="ms-1-1-rowan", title="1:1 — Rowan Ellis", kind="meeting",
        day=1, start="11:00", minutes=30, organizer=ROWAN,
        attendees=(ROWAN, MITCHELL), location="Teams",
    ),
    BlockSpec(
        slug="ms-knowledge-review", title="Focus — knowledge layer review",
        kind="focus", day=1, start="14:00", minutes=90, organizer=MITCHELL,
        agenda_ref="knowledge:enterprise-ontology",
    ),
    BlockSpec(
        slug="ms-curation-block", title="Task block — curation queue",
        kind="task-block", day=2, start="09:00", minutes=60,
        organizer=MITCHELL, agenda_ref="queue:knowledge-curation",
        notes="Promotions into canonical knowledge; never an inline side "
              "effect.",
    ),
    BlockSpec(
        slug="ms-steward-block", title="Task block — vendor stewardship",
        kind="task-block", day=3, start="15:30", minutes=45,
        organizer=MITCHELL, agenda_ref="queue:vendor-stewardship",
    ),
    BlockSpec(
        slug="ms-fleet-review", title="Focus — flow fleet: cost and ROI",
        kind="focus", day=4, start="10:00", minutes=60, organizer=MITCHELL,
        agenda_ref="workspace:flows",
    ),
    BlockSpec(
        slug="ms-eval-review", title="Eval review — expense golden set",
        kind="task-block", day=4, start="13:30", minutes=45,
        organizer=MITCHELL, agenda_ref="queue:eval-review", weeks=(0,),
    ),
    BlockSpec(
        slug="benefits-close", title="Benefits enrollment closes",
        kind="deadline", day=6, start="00:00", minutes=0, organizer=NADIA,
        attendees=(ROWAN, MITCHELL, DANA, PRIYA, KIM, SAM, PAT, MARCUS, ELENA,
                   NADIA, TOMAS),
        all_day=True, agenda_ref="knowledge:benefits-overview", weeks=(0,),
    ),
)

ALL_BLOCKS: tuple[BlockSpec, ...] = MEETINGS + PERSONAL


def calendar_address(email: str) -> str:
    """One calendar per person in the mock feed, at their work address."""
    return email


def _week_start(today: date | None = None) -> date:
    day = today or datetime.now().date()
    return day - timedelta(days=day.weekday())


def build_delivery(
    today: date | None = None, only: set[str] | None = None
) -> dict:
    """The payload the provider would push: three weeks either side of now.

    Returned as a plain dict so the seed and the tests both hand it to
    `SyncRequest.model_validate` — the same door an external caller uses.

    `only` narrows it to certain owners, which is what a late-arriving person
    needs: their calendar mirrors without re-delivering everyone else's.
    """
    monday = _week_start(today)
    people = [
        person for person in workforce.EMPLOYEES
        if only is None or person["email"] in only
    ]
    calendars = [
        {
            "external_id": f"AAMk-{person['external_id']}",
            "address": calendar_address(person["email"]),
            "name": f"{person['name']} — calendar",
            "provider": PROVIDER,
            "timezone": DEFAULT_TZ,
            "owner_email": person["email"],
        }
        for person in people
    ]
    known = {person["email"] for person in people}

    events: list[dict] = []
    for spec in ALL_BLOCKS:
        for week in spec.weeks:
            day = monday + timedelta(days=spec.day + 7 * week)
            hour, minute = (int(part) for part in spec.start.split(":"))
            starts = datetime(day.year, day.month, day.day, hour, minute)
            ends = starts + timedelta(minutes=spec.minutes)
            for attendee in spec.invitees:
                # An external guest gets no calendar here: we mirror our own
                # people's schedules, not the client's.
                if attendee not in known:
                    continue
                events.append({
                    "external_id": f"{spec.slug}-w{week}",
                    "calendar_address": calendar_address(attendee),
                    "title": spec.title,
                    "kind": spec.kind,
                    "starts_at": starts.isoformat(timespec="seconds"),
                    "ends_at": ends.isoformat(timespec="seconds"),
                    "all_day": spec.all_day,
                    "location": spec.location,
                    "status": "confirmed",
                    "organizer_email": spec.organizer,
                    "attendee_emails": list(spec.invitees),
                    "optional_emails": list(spec.optional),
                    "agenda_ref": spec.agenda_ref,
                    "notes": spec.notes,
                })
    return {
        "source": CALENDAR_SOURCE,
        "watermark": f"{monday.isoformat()}T00:00:00Z",
        "idempotency_key": f"calendar-seed-{monday.isoformat()}",
        "calendars": calendars,
        "calendar_events": events,
    }


def seed_calendars_if_empty(sync, graph: GraphService) -> int:
    """Deliver the mock week through the real `calendar-sync` Flow.

    Idempotent on the presence of any Calendar, and dependent on the workforce
    seed having run: a calendar with no owner in the graph is a mirror of
    nobody. Employees that do not exist yet simply get no calendar, which the
    run reports rather than papers over.
    """
    if graph.find("Calendar", limit=1):
        return 0
    if not graph.find("Employee", limit=1):
        log.info("No workforce yet; calendars not seeded")
        return 0

    from .sync import SyncRequest

    delivery = build_delivery()
    sync.deliver(SyncRequest.model_validate(delivery), actor="system:seed")
    log.info(
        "Seeded calendars",
        extra={
            "calendars": len(delivery["calendars"]),
            "events": len(delivery["calendar_events"]),
        },
    )
    return len(delivery["calendars"])


def sync_calendar_additions(sync, graph: GraphService) -> int:
    """Mirror the calendars of people who have one on the roster but not in
    the graph — the late-arrival counterpart to the seed.

    Same reason as `workforce.sync_workforce_additions`: a person added after
    a store was first seeded would otherwise have no calendar at all, and the
    console would show them an empty week or somebody else's. Scoped to their
    own calendars, so nobody else's mirror is re-delivered.

    One honest limit: other attendees' copies of a shared meeting were written
    before this person existed, so their copies do not link to them until the
    next full delivery. The person's own copies carry the full attendee list,
    which is what every personal surface reads.
    """
    if not graph.find("Calendar", limit=1):
        return 0  # nothing seeded yet; the seed pass owns that case
    missing = {
        person["email"] for person in workforce.EMPLOYEES
        if graph.get(object_id("Employee", person["email"])) is not None
        and graph.get(object_id("Calendar", calendar_address(person["email"])))
        is None
    }
    if not missing:
        return 0

    from .sync import SyncRequest

    delivery = build_delivery(only=missing)
    delivery["idempotency_key"] = (
        f"calendar-additions-{'-'.join(sorted(missing))}"
    )
    sync.deliver(SyncRequest.model_validate(delivery), actor="system:roster")
    log.info(
        "Mirrored calendars for roster additions",
        extra={"calendars": len(missing), "events": len(delivery["calendar_events"])},
    )
    return len(missing)


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
    workforce and a demo persona is being shown instead — said out loud rather
    than silently substituted.
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
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class CalendarService:
    """Reads schedules out of the graph. Holds no state of its own."""

    def __init__(self, graph: GraphService) -> None:
        self.graph = graph

    # -- lookups ------------------------------------------------------------

    def employee(self, email: str) -> ObjectRecord | None:
        if not email:
            return None
        return self.graph.get(object_id("Employee", email))

    def resolve_person(self, email: str) -> tuple[ObjectRecord | None, str]:
        """The employee to show, and why, if it is not who asked.

        A principal outside the workforce — every local-dev session — gets the
        demo persona with a note saying so. Substituting silently would make
        the home screen quietly lie about whose day it is.
        """
        person = self.employee(email)
        if person is not None:
            return person, ""
        persona = self.employee(DEMO_PERSONA)
        if persona is None:
            return None, ""
        note = (
            f"Signed in as {email or 'an unidentified principal'}, who is not "
            f"in the workforce. Showing {persona.label}'s calendar so the "
            "demo has a day to render."
        )
        return persona, note

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
            return Agenda(email=email, start_date=start, days=days)
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
            (self._summarize(calendar) for calendar in self.graph.find("Calendar")),
            key=lambda c: c.owner_name or c.address,
        )


def build_calendar_service(graph: GraphService) -> CalendarService:
    return CalendarService(graph)
