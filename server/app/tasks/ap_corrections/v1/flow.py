"""AP corrections, v1 — one fact in, one append, nothing promoted.

Short on purpose. The value of this flow is not in what it computes; it is in
*where it sits*: on the far side of an Event from the invoice that produced it,
so a correction reaches the eval/tuning set without the posting path ever
knowing whether it landed.

The step that is deliberately absent is the interesting one. Doctrine reserves
promotion — teaching the extraction prompt what the clerk keeps saying — for a
declarative Rule that raises a curation Task, and this flow stops one step
short of it. Adding an auto-promote branch here would be a knowledge edit
nobody governed, made by the same lane that wrote the capture.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....runs import RunContext
from ..domain import (
    APPEND_ACTION,
    CORRECTION_COLLECTION,
    CORRECTION_EVENT,
    CORRECTIONS_FLOW,
)

VERSION = 1
LABEL = "v1"
NOTES = (
    "Captures what a clerk changed. One run per accepted correction: the "
    "raw→corrected pairs are appended to the correction set through a guarded "
    "Action, keyed by the same canonical field paths the review screen and the "
    "golden sets use. Nothing is promoted into the extraction prompt or any "
    "other governed asset — that stays a human decision, and this flow does "
    "not make it."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name=CORRECTION_EVENT,
             summary="one run per accepted correction — the fact the accounts-payable run emits when a clerk's edit is applied, answered here rather than inline so the invoice posts whether or not this lands",
             config={"event": CORRECTION_EVENT}, next=["a1"]),
    FlowStep(id="a1", type="action", name=APPEND_ACTION,
             summary="guarded append of one raw→corrected pair per field the clerk moved, each carrying the document it was read from, the run that read it and who decided — a pair whose two halves are equal is not a correction and is dropped",
             config={"target": CORRECTION_COLLECTION,
                     "gateway": "action-gateway",
                     "dataset": "ap-field-corrections"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="CorrectionSetAppended",
             summary="emitted on commit, so the flywheel's own turn is a fact something else could respond to — the promotion Rule the doctrine reserves would start here, and deliberately does not exist yet",
             config={"event": "CorrectionSetAppended"}),
]


def build_body(service):
    """Bind the correction service and return the engine-runnable body."""

    def body(ctx: RunContext, inputs: dict) -> dict:
        pairs = list(inputs.get("pairs") or [])
        context = {
            key: inputs.get(key)
            for key in (
                "document_id", "invoice_number", "vendor_id", "flow_id",
                "flow_version", "run_id", "case_ref", "task_id", "decided_by",
            )
        }
        ctx.event(
            "t1", CORRECTION_EVENT,
            f"{len(pairs)} field(s) corrected by "
            f"{context.get('decided_by') or 'a clerk'}"
            + (
                f" on {context['invoice_number']}"
                if context.get("invoice_number") else ""
            ),
            kind="trigger",
        )

        if service.gateway is None:
            # No Gateway is a wiring fault, not a business outcome: an
            # ungoverned append would put customer-owned knowledge in a store
            # with nothing saying who put it there.
            raise RuntimeError("the corrections service has no Action Gateway")

        act = service.gateway.action(
            APPEND_ACTION, ctx.principal or CORRECTIONS_FLOW,
            run=ctx.record, step_id="a1",
            task_id=str(context.get("task_id") or ""),
            decided_by=str(context.get("decided_by") or ""),
        )
        appended = ctx.step(
            "a1", APPEND_ACTION, "action",
            fn=lambda: act.commit(
                lambda writer: service.append(pairs, context, writer)
            ),
            writes=lambda _out: act.writes,
            summary=lambda out: (
                f"{out['appended']} pair(s) appended: "
                + ", ".join(out["paths"][:4])
                + (" …" if len(out["paths"]) > 4 else "")
                if out["appended"] else
                "nothing appended — every field came back as it was read"
            ),
            refs=(
                [f"document:{context['document_id']}"]
                if context.get("document_id") else []
            ),
        )

        ctx.event(
            "e1", "CorrectionSetAppended",
            f"{appended['appended']} pair(s) in the correction set",
        )
        return {
            "appended": appended["appended"],
            "paths": appended["paths"],
            "document_id": context.get("document_id") or "",
        }

    return body
