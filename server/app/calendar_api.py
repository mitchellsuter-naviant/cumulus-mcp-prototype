"""REST surface for calendars: your week, and who else has one.

Reads only. The provider is the system of record for a schedule, so there is
deliberately no write path here — a block created in Cumulus would be a fact
the mirror could not defend, and the next sync would delete it. Booking time
is a Flow that calls the provider, and it does not exist yet; until it does,
this surface says what it knows and nothing more.
"""

import logging

from fastapi import APIRouter, Depends, Query

from .auth import Principal, get_principal, require_reader
from .calendars import Agenda, CalendarSummary
from .deps import get_calendar_service

log = logging.getLogger("cumulus.calendar.api")

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


@router.get("/me", response_model=Agenda)
def my_agenda(
    start: str = Query(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$"),
    days: int = Query(default=7, ge=1, le=31),
    principal: Principal = Depends(get_principal),
) -> Agenda:
    """The signed-in person's window. Defaults to the current week, Monday-first.

    A principal with no Employee record — the Naviant engineer, Naviant
    Support, a machine credential — gets an empty week with `persona_note`
    saying they are not in the workforce. No substitute is shown: somebody
    else's week reads as your own.
    """
    require_reader(principal)
    return get_calendar_service().agenda(principal.email, start=start, days=days)


@router.get("/calendars", response_model=list[CalendarSummary])
def list_calendars(
    principal: Principal = Depends(get_principal),
) -> list[CalendarSummary]:
    """Every mirrored calendar, with its owner and next block.

    The org-wide view: it is what shows how much of the workforce the mirror
    actually covers — and, before any office feed has pulled, that it covers
    none of it yet.
    """
    require_reader(principal)
    return get_calendar_service().calendars()
