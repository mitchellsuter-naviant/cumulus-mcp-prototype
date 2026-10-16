"""Answer question, v1 — the shape this flow has run since it landed.

The body lives beside the answering service it is made of; this package is where the
version ships: its declarative steps, and the binding the engine registers.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....answering import answer_body


VERSION = 1
LABEL = "v1"
NOTES = (
    "Answers a question over the graph and the knowledge layer, citing what it read."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="QuestionAsked",
             summary="one run per question, from any surface — the HR agent's `HrQuestionAsked` binds the same flow and differs only in the context it carries",
             config={"event": "QuestionAsked"}, next=["m1"]),
    FlowStep(id="m1", type="modelcall", name="answer-question",
             summary="the planner loop over the two retrieval planes: document text and corpus metadata, every claim cited to an asset id it actually read — one step, however many turns it took",
             config={"model": "gemini-3.5-flash-lite",
                     "prompt": "answering.SYSTEM_INSTRUCTION",
                     "budget": "answer_max_steps turns"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="QuestionAnswered",
             summary="emitted with the backend that answered — `retrieval` when nothing generated, which the console renders differently on purpose",
             config={"event": "QuestionAnswered"}),
]


def build_body(service):
    return lambda ctx, inputs: answer_body(service, ctx, inputs)
