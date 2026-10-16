"""Mail sync, v1 — the shape this flow has run since it landed.

The body lives beside the sync machinery it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....connectors.office import mail_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Lands office mailboxes and messages, previews capped at the governed length."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="MailDelivered",
             summary="one run per delivery, woken by a window",
             config={"event": "MailDelivered"}, next=["a1"]),
    FlowStep(id="a1", type="action", name="apply-mailboxes",
             summary="guarded write of each mailbox, keyed by address — the directory's job title and department land as `office` claims, which the HRIS outranks wherever the two disagree",
             config={"target": "correspondence", "gateway": "action-gateway"},
             next=["a2"]),
    FlowStep(id="a2", type="action", name="apply-messages",
             summary="guarded write of each message as a part of the mailbox that holds it, truncated to the governed preview length; a message whose mailbox this graph does not hold is dropped and counted",
             config={"target": "correspondence", "gateway": "action-gateway",
                     "custody": "headers, addressing and body_preview only"},
             next=["a3"]),
    FlowStep(id="a3", type="action", name="wire-correspondents",
             summary="guarded write of the sender and recipient edges, by address — separately typed for employees and outside people, because whether a commitment came from inside or outside the company is the first question anybody asks of a thread",
             config={"target": "correspondence", "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="MailSynced",
             summary="emitted on commit", config={"event": "MailSynced"}),
]


def build_body(service):
    return lambda ctx, inputs: mail_body(service, ctx, inputs)
