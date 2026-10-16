"""Calendar sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep

VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands office calendars and their events for the people already in the graph."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="CalendarsDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "CalendarsDelivered"}, next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-calendars",
             summary="guarded write of each calendar and the `has_calendar` edge to the employee whose it is — times stay wall-clock local in the calendar's own zone, because a recurring 9am has to survive a daylight-saving change",
             config={"target": "calendars", "gateway": "action-gateway"},
             next=["a2"]),
    FlowStep(id="a2", type="action", name="apply-events",
             summary="guarded write of each block as a part of the calendar that holds it; an event whose calendar this graph does not hold is dropped and counted rather than being given an owner it does not have",
             config={"target": "calendars", "gateway": "action-gateway"},
             next=["a3"]),
    FlowStep(id="a3", type="action", name="wire-attendance",
             summary="guarded write of the organizer and attendee edges, by work email — a client sponsor or an external recruiter resolves to nobody and is reported on the aggregate",
             config={"target": "calendars", "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="CalendarsSynced",
             summary="emitted on commit",
             config={"event": "CalendarsSynced"}),
]


def build_body(service):
    return service._calendar_body
