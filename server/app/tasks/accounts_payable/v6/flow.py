"""Accounts payable, v6 — the shape the fleet runs today.

The executable body and the declarative steps travel together: the map the
console draws for v6 is this module's STEPS, and the engine runs this module's
body, so the two cannot drift apart without a diff in one file saying so.

The body is written as four named phases rather than one long function, and
that is a structural decision rather than tidiness. What later versions change
is *where the document comes from and how it is read* — v8 starts from a
document the intake flow already stored, and WP-J inserts arithmetic and
field-verdict steps between the extraction and the rule. Everything from the
vendor onwards is the same work in every version, so it lives in functions the
later packages call instead of copying: a fix to the three-way match or to the
review loop reaches every shipped version, which is not true of a fork.
"""

from __future__ import annotations

from .... import routing
from ....documents import DOCUMENT_COLLECTION
from ....flows import FlowStep, read_model_rates
from ....matching import MatchLine, MatchResult, match_invoice, parse_match_tolerances
from ....models import utcnow
from ....auth import flow_principal
from ....prompts import GovernedPrompt
from ....runs import ModelUsage, RunContext
from ....task_screens import CORRECT_THE_FACTS, TaskScreen, TaskUi
from .. import domain
from ..domain import (
    AP_FLOW,
    FLOW_ID,
    MAX_REVIEW_ROUNDS,
    InvoiceExtraction,
    PayableState,
    PostingPolicy,
    _apply_invoice_patch,
    _invoice_match_lines,
)

import base64

VERSION = 6
LABEL = "v6"
NOTES = (
    "One extraction pass, vendor resolution against the master, the three-way "
    "match and the anomaly screen, posting under the governed auto-post limit "
    "with clerk review above it."
)

#: One invoice, two moments.
#:
#: A `curation` task is the extraction asking to be checked, so the document's
#: own numbers lead. Anything else — a match variance, an anomaly — is the
#: comparison asking to be answered, so what each line matched against leads.
#: Both read the same invoice and both answer with the same correction payload;
#: only the emphasis differs, which is the whole reason they are two screens
#: and not one with a flag.
#:
#: This version keeps its own declaration for as long as it can raise a task.
#: A v6 run computed no field verdicts, so answering its tasks through v8's
#: screen would tell a clerk "this run reported no field verdicts" on every
#: single one.
UI = TaskUi(
    by_kind={
        "curation": TaskScreen(
            screen_id="accounts-payable/extraction-review",
            label="Open the extraction verification",
            hint=CORRECT_THE_FACTS,
        ),
    },
    otherwise=TaskScreen(
        screen_id="accounts-payable/three-way-match",
        label="Open the invoice review",
        hint=CORRECT_THE_FACTS,
    ),
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="InvoiceReceived",
             summary="one run per inbound invoice",
             config={"event": "InvoiceReceived"}, next=["s1"]),
    FlowStep(id="s1", type="action", name="store-document",
             summary="the invoice bytes land in the document store before anything reads them, so every later claim has an artifact to point at",
             config={"target": "documents", "gateway": "action-gateway"},
             next=["m1"]),
    FlowStep(id="m1", type="modelcall", name="extract-invoice",
             summary="one pass over the document: the vendor as printed, the invoice number, dates, PO, totals and the billed lines — with a confidence",
             config={"model": "gemini-3.5-flash-lite",
                     "prompt": "knowledge:invoice-extraction-prompt"},
             next=["z1"]),
    FlowStep(id="z1", type="resolve", name="resolve-vendor",
             summary="the printed name becomes a canonical Vendor id against the vendor master; an unknown one is minted `onboarding`, an ambiguous one stops the run",
             config={"entity": "Vendor", "source": "knowledge:entity-resolution-policy"},
             next=["x3", "k0"], branch_labels=["resolved", "ambiguous"]),
    FlowStep(id="k0", type="task", name="confirm-vendor",
             summary="a clerk names the vendor from the scored candidates — nothing is asserted until they do, because posting against the wrong vendor is not recoverable",
             config={"queue": "ap-clerk"}, next=["a0"]),
    FlowStep(id="a0", type="action", name="apply-vendor-decision",
             summary="guarded write of the vendor the clerk chose: an identity link, or a newly minted vendor",
             config={"target": "vendor-master", "gateway": "action-gateway"},
             next=["x3"]),
    FlowStep(id="x3", type="transform", name="project-lines",
             summary="the extracted lines become facts against the ontology's paths — the same vocabulary the eval spec asserts on",
             config={"language": "python"}, next=["x4"]),
    FlowStep(id="x4", type="transform", name="gather-po-and-receiving",
             summary="reads the purchase order and receiving lines out of the graph — the flow gathers; the matcher and the rule never fetch",
             config={"source": "graph:PurchaseOrder,ReceivingReport"},
             next=["c1"]),
    FlowStep(id="c1", type="check", name="three-way-match",
             summary="pairs invoice ↔ PO ↔ receiving line by line within the governed tolerances, and writes the pairings as a MatchSet rather than discarding them",
             config={"source": "knowledge:invoice-posting-policy"},
             next=["c2"]),
    FlowStep(id="c2", type="check", name="anomaly-screen",
             summary="scores the invoice against this vendor's own payment history — duplicates, amount outliers, round numbers — with thresholds read at run time",
             config={"source": "knowledge:invoice-posting-policy"},
             next=["x5"]),
    FlowStep(id="x5", type="transform", name="read-posting-policy",
             summary="gathers the governed posting values (auto-post limit, confidence floor) for the rule",
             config={"source": "knowledge:invoice-posting-policy"}, next=["r2"]),
    FlowStep(id="r2", type="rule", name="post-or-review",
             summary="under the governed auto-post limit, confidently extracted, matched, and nothing unusual?",
             config={"rule_type": "threshold", "source": "knowledge:invoice-posting-policy"},
             next=["a1", "k2"], branch_labels=["post", "review"]),
    FlowStep(id="k2", type="task", name="review-invoice",
             summary="a clerk decides the flagged invoice; a correction re-runs the match and the rule rather than overriding them",
             config={"queue": "ap-clerk"}, next=["a1"]),
    FlowStep(id="a1", type="action", name="post-to-ap",
             summary="guarded write of the invoice, its lines and the match set into the graph and the AP ledger",
             config={"target": "ap-ledger", "gateway": "action-gateway"}, next=["e1"]),
    FlowStep(id="e1", type="event", name="InvoicePosted",
             summary="emitted on commit", config={"event": "InvoicePosted"}),
]


# ---------------------------------------------------------------------------
# The phases every version shares
# ---------------------------------------------------------------------------


def read_governed_prompt(service, ctx: RunContext) -> GovernedPrompt:
    """x0 — the governed read the ModelCall runs on.

    A prompt is knowledge, so it is read here at a revision rather than
    compiled in, and the revision travels onto the extraction step.
    """
    prompt = GovernedPrompt.model_validate(ctx.step(
        "x0", "read-prompt", "transform",
        fn=lambda: service.read_prompt().model_dump(),
        summary=lambda out: (
            f"{out['source_ref']}: {len(out['text'])} characters"
            + ("" if out["found_asset"]
               else " — not in the corpus, running on the built-in default")
        ),
    ))
    ctx.note_refs(prompt.source_ref)
    return prompt


def extraction_meter(service):
    """The priced `ModelUsage` for an extraction step's `meter` hook.

    Off the extraction, not off the reader's name. `getattr(reader, "name")`
    was the backend — "vertex" — filed in the column an auditor reads as the
    model that made the claim.

    The rate sheet is read once per run here, as it is in `reimbursements`: a
    ref on the metered step rather than a governed read a Rule consumes,
    because the rates change what the journal records the call cost and
    nothing about what the flow does.
    """
    rates = read_model_rates(service.knowledge)

    def meter(out) -> ModelUsage:
        return rates.priced(ModelUsage(
            model=str(out.get("model") or ""),
            input_tokens=int(out.get("input_tokens") or 0),
            output_tokens=int(out.get("output_tokens") or 0),
        ))

    return rates.source_ref, meter


def extraction_summary(out: dict) -> str:
    return (
        f"{out['model']}: {out['vendor_name'] or 'unnamed vendor'} · "
        f"{out['invoice_number']} · ${out['total_usd']:,.2f} · "
        f"{len(out['lines'])} line(s) · confidence {out['confidence']:.2f}"
    )


def resolve_vendor(
    service, ctx: RunContext, extraction: InvoiceExtraction, submitted_by: str
) -> tuple[str, str, dict]:
    """z1 (and k0/a0) — the printed name becomes one canonical vendor.

    A resolution, not a decision: the resolver picked a winner among candidates
    by the governed thresholds and nobody was asked. Automatic like an
    observation, ledgered like a decision — which is exactly what the doctrine
    asks of the third regime.
    """
    resolve_act = service.gateway.action(
        "resolve-identities", flow_principal(FLOW_ID),
        run=ctx.record, step_id="z1",
    )
    resolution_raw = ctx.step(
        "z1", "resolve-vendor", "resolve",
        fn=lambda: resolve_act.commit(
            lambda w: service._resolve_vendor(extraction, ctx.record.id, w)
        ),
        writes=lambda _out: resolve_act.writes,
        summary=lambda out: (
            f"{out['disposition']}: {out['label'] or '—'} "
            f"({out['method']} {out['score']:.2f}) — {out['reason']}"
        ),
    )
    vendor_id = resolution_raw.get("vendor_id", "")
    vendor_label = resolution_raw.get("label", "")

    if resolution_raw["disposition"] == "review":
        # A per-case flow *does* stop here: posting against the wrong vendor is
        # not a recoverable mistake, and the candidates are already scored, so
        # the question is cheap for a human to answer.
        decision = ctx.gate(
            "k0", "confirm-vendor",
            build_task=lambda: service._build_vendor_task(
                ctx.record, extraction, resolution_raw, submitted_by
            ),
        )
        # The clerk named a vendor: a decision, and a consequential one —
        # posting against the wrong company is not a recoverable mistake.
        confirm = service.gateway.action(
            "confirm-vendor", ctx.principal or AP_FLOW,
            run=ctx.record, step_id="a0",
            task_id=f"task_{ctx.record.id}_k0",
            decided_by=decision.decided_by,
        )
        chosen = ctx.step(
            "a0", "apply-vendor-decision", "action",
            fn=lambda: confirm.commit(lambda w: service._apply_vendor_decision(
                extraction, resolution_raw, decision, ctx.record.id, w,
            )),
            writes=lambda _out: confirm.writes,
            summary=lambda out: (
                f"{out['action']} {out['label']} by {out['decided_by']}"
            ),
        )
        vendor_id = chosen["vendor_id"]
        vendor_label = chosen["label"]
    return vendor_id, vendor_label, resolution_raw


def gather_and_check(
    service, ctx: RunContext, extraction: InvoiceExtraction, vendor_id: str
) -> dict:
    """x3/x4/c1/c2 — the lines become facts, then the match and the screen."""
    ctx.step(
        "x3", "project-lines", "transform",
        fn=lambda: [line.model_dump() for line in extraction.lines],
        summary=lambda out: (
            f"{len(out)} line(s) · "
            f"{sum(1 for line in out if line['kind'] == 'goods')} goods, "
            f"{sum(1 for line in out if line['kind'] != 'goods')} freight/tax"
        ),
    )

    context = ctx.step(
        "x4", "gather-po-and-receiving", "transform",
        fn=lambda: service._gather_match_context(vendor_id, extraction.po_number),
        summary=lambda out: (
            f"{len(out['po_numbers'])} PO(s) and "
            f"{len(out['receipt_numbers'])} receiving report(s) in the graph "
            "for this invoice"
            if out["po_numbers"] or out["receipt_numbers"] else
            "no purchase order or receiving report in the graph for this invoice"
        ),
    )
    policy_content, policy_ref = service._policy_asset_content()
    match_raw = ctx.step(
        "c1", "three-way-match", "check",
        fn=lambda: _match(extraction, context, policy_content, policy_ref),
        summary=lambda out: MatchResult.model_validate(out).summary(),
        refs=[policy_ref] + [
            f"graph:PurchaseOrder/{n}" for n in context["po_numbers"]
        ],
    )
    screen = ctx.step(
        "c2", "anomaly-screen", "check",
        fn=lambda: service._screen(
            vendor_id, extraction, policy_content, policy_ref
        ),
        summary=lambda out: (
            f"{len(out['findings'])} finding(s): "
            + "; ".join(f["kind"] for f in out["findings"])
            + f" · basis: {out['basis']}"
            if out["findings"] else f"nothing unusual · basis: {out['basis']}"
        ),
        refs=[policy_ref],
    )
    return {
        "context": context,
        "match": MatchResult.model_validate(match_raw),
        "screen": screen,
        "policy_content": policy_content,
        "policy_ref": policy_ref,
    }


def decide_and_post(
    service,
    ctx: RunContext,
    *,
    gathered: dict,
    extraction: InvoiceExtraction,
    vendor_id: str,
    vendor_label: str,
    resolution_raw: dict,
    submitted_by: str,
    submitted_at: str,
    filename: str,
    document_id: str,
    check,
    fields_for=None,
) -> dict:
    """x5/r2/k2/a1 — the governed rule, the review loop, and the commit.

    `fields_for` is a callable, not a list, and that is the whole of why it is
    a seam v8 could use: the verdicts belong to *an extraction*, and a clerk
    who corrects one is handed a different invoice on the next round. A list
    computed once would put round one's doubts in front of round two's reader,
    including doubts their own correction had just answered.
    """
    context = gathered["context"]
    match: MatchResult = gathered["match"]
    screen = gathered["screen"]
    policy_content = gathered["policy_content"]
    policy_ref = gathered["policy_ref"]

    policy_raw = ctx.step(
        "x5", "read-posting-policy", "transform",
        fn=lambda: service.read_policy().model_dump(),
        summary=lambda out: (
            f"{out['source_ref']}: auto-post ≤ ${out['limit_usd']:,.2f} · "
            f"min confidence {out['min_confidence']:.2f}"
        ),
    )
    policy = PostingPolicy.model_validate(policy_raw)
    ctx.note_refs(policy.source_ref)

    state: PayableState = "posted"
    reason = "auto-posted within governed policy"
    decided_by = ""
    decision_note = ""

    for round_number in range(1, MAX_REVIEW_ROUNDS + 1):
        checked = ctx.step(
            f"r2_{round_number}", "post-or-review", "rule",
            fn=lambda: check(extraction, match, screen, policy),
            summary=lambda out: (
                out.get("note")
                or ("review: " + "; ".join(out["reasons"]) if out["reasons"]
                    else "post: within limit, matched, nothing unusual")
            ),
            refs=[policy.source_ref],
        )
        reasons = checked["reasons"]
        if not reasons:
            if round_number > 1:
                reason = f"posted after review by {decided_by}"
            break
        decision = ctx.gate(
            f"k2_{round_number}", "review-invoice",
            build_task=lambda rs=reasons, rn=round_number: service._build_review_task(
                ctx.record, extraction, match, screen, policy, rs, rn,
                vendor_label, submitted_by,
                fields=fields_for(extraction) if fields_for else None,
            ),
        )
        decided_by = decision.decided_by
        decision_note = decision.note or decision.correction
        if decision.verdict == "rejected":
            state = "denied"
            reason = decision.note or "rejected on review"
            break
        if decision.verdict == "approved":
            state = "posted"
            reason = "posted on review despite: " + "; ".join(reasons)
            break
        # `corrected`: the clerk fixed the extraction, so re-run the checks
        # against the corrected values rather than trusting the override.
        patched = ctx.step(
            f"x6_{round_number}", "apply-correction", "transform",
            fn=lambda d=decision: _apply_invoice_patch(extraction, d.payload),
            summary=lambda out: (
                f"clerk corrected {', '.join(out['changed']) or 'nothing'}"
            ),
        )
        extraction = InvoiceExtraction.model_validate(patched["extraction"])
        match_raw = ctx.step(
            f"c1_{round_number}", "three-way-match", "check",
            fn=lambda: _match(extraction, context, policy_content, policy_ref),
            summary=lambda out: MatchResult.model_validate(out).summary(),
        )
        match = MatchResult.model_validate(match_raw)
    else:
        state = "pending"
        reason = (
            f"still flagged after {MAX_REVIEW_ROUNDS} review round(s) — "
            "left for the AP lead"
        )

    return service._finish(
        ctx, submitted_by, submitted_at, filename, document_id, vendor_id,
        vendor_label, resolution_raw, extraction, match, screen, policy,
        state, reason, decided_by, decision_note,
    )


def _match(
    extraction: InvoiceExtraction,
    context: dict,
    policy_content: str,
    policy_ref: str,
) -> dict:
    return match_invoice(
        [
            MatchLine.model_validate(line)
            for line in _invoice_match_lines(extraction)
        ],
        [MatchLine.model_validate(line) for line in context["po_lines"]],
        [MatchLine.model_validate(line) for line in context["receipt_lines"]],
        parse_match_tolerances(policy_content, policy_ref),
        invoice_total_usd=extraction.total_usd,
    ).model_dump()


# ---------------------------------------------------------------------------
# The body
# ---------------------------------------------------------------------------


def build_body(service, *, posting_check=None):
    """Bind the process service and return the engine-runnable body.

    `posting_check` is the one seam v7 has needed: the Rule the r2 step
    evaluates. It defaults to the domain's governed check.
    """
    check = posting_check or domain._posting_check

    def body(ctx: RunContext, inputs: dict) -> dict:
        submitted_by = inputs["submitted_by"]
        filename = inputs.get("filename", "")
        hint = inputs.get("hint", "")
        image_b64 = inputs.get("image_b64", "")
        submitted_at = inputs.get("submitted_at_iso") or utcnow().isoformat()
        image = base64.b64decode(image_b64) if image_b64 else None

        ctx.event(
            "t1", "InvoiceReceived",
            f"invoice received from {submitted_by}"
            + (f" ({filename})" if filename else ""),
            kind="trigger",
        )

        slot = ctx.step(
            "s1", "store-document", "action",
            fn=lambda: service._store(image_b64, filename, submitted_by, ctx.record.id),
            writes=lambda out: [f"document:{out['document_id']}"],
            summary=lambda out: (
                f"artifact {out['document_id']} stored in `{DOCUMENT_COLLECTION}`"
            ),
        )
        document_id = slot["document_id"]

        prompt = read_governed_prompt(service, ctx)
        rates_ref, meter = extraction_meter(service)

        # -- m1: one call reads the whole document ------------------------
        extraction = InvoiceExtraction.model_validate(ctx.step(
            "m1", "extract-invoice", "modelcall",
            fn=lambda: service._extract(
                image, filename, hint, document_id, prompt.text
            ).model_dump(),
            summary=extraction_summary,
            meter=meter,
            refs=[f"document:{document_id}", prompt.source_ref, rates_ref],
            # The operations desk, not the AP clerk's: an extraction that
            # threw is an engineering fault, and Connie has no verdict to give
            # on one. See the note at the same step in `reimbursements`.
            retries=1, escalate_queue=routing.OPS_QUEUE,
        ))

        vendor_id, vendor_label, resolution_raw = resolve_vendor(
            service, ctx, extraction, submitted_by
        )
        gathered = gather_and_check(service, ctx, extraction, vendor_id)
        return decide_and_post(
            service, ctx, gathered=gathered, extraction=extraction,
            vendor_id=vendor_id, vendor_label=vendor_label,
            resolution_raw=resolution_raw, submitted_by=submitted_by,
            submitted_at=submitted_at, filename=filename,
            document_id=document_id, check=check,
        )

    return body
