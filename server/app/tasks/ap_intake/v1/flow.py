"""AP intake, v1 — the shape this flow has run since it landed.

The executable body and the declarative steps travel together, for the reason
`accounts_payable/v6` gives: the map the console draws is this module's STEPS
and the engine runs this module's body, so the two cannot drift apart without a
diff in one file saying so.
"""

from __future__ import annotations

from ....flows import FlowStep
from ....runs import RunContext
from ..domain import (
    DOCUMENT_LIBRARY,
    INTAKE_FLOW,
    INVOICE_EVENT,
    IntakePolicy,
)

VERSION = 1
LABEL = "v1"
NOTES = (
    "Reads the supplier invoices the customer filed in their own document "
    "system: the AP folders named by governed knowledge, the files under them "
    "nobody has read yet, their bytes fetched through the pointer `dms-sync` "
    "recorded and stored under their content hash — then one InvoiceReceived "
    "per invoice, which is where the batch becomes cases."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="DocumentsDelivered",
             summary="one run per delivery, woken by a window — the same fact that wakes `dms-sync`, which the Dispatcher starts first so the tree and its files are in the graph before this reads them",
             config={"event": "DocumentsDelivered"}, next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-intake-policy",
             summary="gathers the governed values: which folders the customer files supplier invoices in, and how many invoices one window may take on",
             config={"source": "knowledge:ap-intake-policy"}, next=["x2"]),
    FlowStep(id="x2", type="transform", name="select-invoices",
             summary="walks the AP folders in the graph to the documents filed under them and keeps the ones whose pointer resolves to a revision nobody has read — a backlog longer than the window's bound is trimmed, not dropped: the rest is the next window's work",
             config={"source": "graph:Folder,Document"}, next=["s1"]),
    FlowStep(id="s1", type="sync", name="fetch-invoices",
             summary="fetches each file's bytes through the customer-resolvable `storage_ref` and stores them under their content hash, then a guarded write stamping the referenced row with what was read and which revision it came from — a file that will not fetch or carries no readable page is counted, never a gate",
             config={"target": DOCUMENT_LIBRARY, "gateway": "action-gateway",
                     "custody": "copy"},
             next=["k1"]),
    FlowStep(id="k1", type="task", name="report-intake-fault",
             summary="the aggregate escalation, and the only Task this flow raises: a window that reached for files and got nothing at all is one address that stopped answering, which is the operations desk's and nobody's business decision",
             config={"queue": "platform-operations",
                     "raised_when": "every fetch in the window failed"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name=INVOICE_EVENT,
             summary="one fact per invoice read, carrying the stored document — the Trigger table answers each with one accounts-payable run, which is the seam where a windowed batch becomes per-case work",
             config={"event": INVOICE_EVENT, "starts": "accounts-payable"}),
]


def build_body(service):
    """Bind the intake service and return the engine-runnable body."""

    def body(ctx: RunContext, inputs: dict) -> dict:
        source = inputs.get("source", "")
        watermark = inputs.get("watermark", "")
        delivered = inputs.get("documents") or []

        ctx.event(
            "t1", "DocumentsDelivered",
            f"{len(delivered)} document(s) from `{source}`"
            + (f" · watermark {watermark}" if watermark else ""),
            kind="trigger",
        )

        # -- x1: the governed read that decides what an invoice is ----------
        policy = IntakePolicy.model_validate(ctx.step(
            "x1", "read-intake-policy", "transform",
            fn=lambda: service.read_policy().model_dump(),
            summary=lambda out: (
                f"{out['source_ref']}: "
                + ", ".join(f"`{f}`" for f in out["folders"])
                + f" · up to {out['window_limit']} per window"
                + ("" if out["found_asset"]
                   else " — not in the corpus, running on the built-in default")
            ),
        ))
        ctx.note_refs(policy.source_ref)

        # -- x2: the standing question, asked of the graph -------------------
        selection = ctx.step(
            "x2", "select-invoices", "transform",
            fn=lambda: _select(service, source, policy),
            summary=lambda out: (
                f"{out['unread']} unread invoice(s) under "
                f"{out['folders']} AP folder(s) · {len(out['selected'])} taken "
                "this window"
                + (f", {out['deferred']} left for the next"
                   if out["deferred"] else "")
                if out["unread"] else
                f"nothing unread under {out['folders']} AP folder(s)"
            ),
        )

        # -- s1: the fetch, and the observation it lands ---------------------
        # One Action for the window, not one per file: a windowed flow lands
        # bulk observations, and forty guarded writes would be forty ledger
        # entries saying the same thing about one pass.
        act = service.gateway.action(
            "record-document-read", INTAKE_FLOW,
            run=ctx.record, step_id="s1",
        )
        outcome = ctx.step(
            "s1", "fetch-invoices", "sync",
            fn=lambda: act.commit(lambda w: service._fetch_and_store(
                selection["selected"], ctx.record.id, w,
            )),
            writes=lambda _out: act.writes,
            summary=lambda out: _fetch_summary(out),
        )

        # -- k1: the aggregate, when there is one ----------------------------
        ctx.step(
            "k1", "report-intake-fault", "sync",
            fn=lambda: {
                "task_id": (
                    service._raise_intake_fault(ctx, outcome)
                    if outcome["unreachable"] and not outcome["read"] else ""
                )
            },
            summary=lambda out: (
                f"operations Task {out['task_id']} — no file in this window "
                "answered"
                if out["task_id"] else "nothing to report, so nobody to ask"
            ),
        )

        # -- e1: the batch becomes cases -------------------------------------
        raised = ctx.step(
            "e1", INVOICE_EVENT, "event",
            fn=lambda: service._raise_invoices(source, outcome["stored"]),
            summary=lambda out: (
                f"{out['raised']} invoice(s) raised · "
                f"{len(out['runs'])} accounts-payable run(s) started"
                + (f", {len(out['failed_runs'])} of them failed"
                   if out["failed_runs"] else "")
                if out["raised"] else "no new invoice to raise"
            ),
        )

        return {
            "source": source, "watermark": watermark, "kind": "ap-intake",
            "delivered": len(delivered),
            "unread": selection["unread"],
            "selected": len(selection["selected"]),
            "deferred": selection["deferred"],
            "read": outcome["read"],
            "unreachable": outcome["unreachable"],
            "unreadable": outcome["unreadable"],
            "no_fetcher": outcome["no_fetcher"],
            "raised": raised["raised"],
            "runs": [r["run_id"] for r in raised["runs"]],
            "failed_runs": [r["run_id"] for r in raised["failed_runs"]],
        }

    return body


def _select(service, source: str, policy: IntakePolicy) -> dict:
    """The window's work list, and what it left behind.

    The overflow is reported rather than silently trimmed, because a count
    nobody states is a backlog nobody knows about.
    """
    candidates = service._candidates(source, policy)
    limit = max(0, policy.window_limit)
    return {
        "unread": len(candidates),
        "folders": len(policy.folders),
        "selected": candidates[:limit],
        "deferred": max(0, len(candidates) - limit),
    }


def _fetch_summary(out: dict) -> str:
    parts = [f"{out['read']} invoice(s) read into the map"]
    if out["unreachable"]:
        parts.append(f"{out['unreachable']} whose pointer did not answer")
    if out["unreadable"]:
        parts.append(f"{out['unreadable']} carrying no page a model can read")
    if out["no_fetcher"]:
        parts.append(
            f"{out['no_fetcher']} left unread — no estate is registered to "
            "fetch them from"
        )
    return " · ".join(parts)
