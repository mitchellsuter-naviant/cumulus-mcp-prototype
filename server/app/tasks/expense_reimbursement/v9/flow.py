"""Expense reimbursement, v9 — the shape the fleet runs today."""

from __future__ import annotations

from ....flows import FlowStep
from ....runs import RunContext
from ....task_screens import CORRECT_THE_FACTS, TaskScreen, TaskUi
from .. import domain
from ..domain import *  # noqa: F401,F403 — the body reads its domain's vocabulary
from ..domain import _check, _clip, _event  # noqa: F401

VERSION = 9
LABEL = "v9"
NOTES = (
    "Reads the submitted receipt with one governed ModelCall, checks it "
    "against the expense policy, elevates to human review when policy or "
    "confidence demands it, and records the request with its state."
)

#: One screen for every kind this flow raises: an approver is always looking at
#: the same receipt against the same policy, and the kind only says which check
#: elevated it.
UI = TaskUi(
    otherwise=TaskScreen(
        screen_id="expense-reimbursement/expense-review",
        label="Open the expense review",
        hint=CORRECT_THE_FACTS,
    ),
)

STEPS: list[FlowStep] = [
    # The event name is the one the `expense-submitted` Trigger
    # binds, not a near-synonym: an export naming a fact nothing
    # emits is the display string this module used to carry.
    FlowStep(id="t1", type="trigger", name="ExpenseSubmitted",
             summary="one run per submitted receipt",
             config={"event": "ExpenseSubmitted"}, next=["m1"]),
    FlowStep(id="m1", type="modelcall", name="extract-receipt",
             summary="reads the receipt image: vendor, date, total, category — with a confidence per field",
             config={"model": "gemini-3.5-flash-lite",
                     "prompt": "knowledge:receipt-extraction-prompt"},
             next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-policy",
             summary="gathers the governed policy values (auto-approval limit, category caps) from the knowledge layer for the rule — rules never fetch",
             config={"source": "knowledge:expense-approval-policy"}, next=["r1"]),
    FlowStep(id="r1", type="rule", name="needs-elevation",
             summary="within the policy's auto-approval limit, an allowed category, and confidently extracted?",
             config={"rule_type": "threshold",
                     "source": "knowledge:expense-approval-policy"},
             next=["a1", "k1"], branch_labels=["approve", "elevate"]),
    FlowStep(id="k1", type="task", name="elevate-review",
             summary="a reviewer decides the elevated request with the extraction and the policy findings attached",
             config={"queue": "expense-review"}, next=["a1"]),
    FlowStep(id="a1", type="action", name="record-reimbursement",
             summary="guarded write of the request and its state into the reimbursements table",
             config={"target": "reimbursements", "gateway": "action-gateway"}, next=["e1"]),
    FlowStep(id="e1", type="event", name="ReimbursementRecorded",
             summary="emitted on commit", config={"event": "ReimbursementRecorded"}),
]


def build_body(service):
    def body(ctx: RunContext, inputs: dict) -> dict:
        employee = inputs["employee"]
        commentary = inputs.get("commentary", "")
        submitted_at = inputs.get("submitted_at_iso") or utcnow().isoformat()
        submitted = [SubmittedDocument.model_validate(d) for d in inputs["documents"]]

        ctx.event(
            "t1", "ExpenseSubmitted",
            f"{len(submitted)} document(s) submitted by {employee}"
            + (f" · commentary: \"{_clip(commentary)}\"" if commentary else ""),
            kind="trigger",
        )

        # s1 · Action — the artifacts land in the audit-documents Collection,
        # which is what stamps their two-year retention.
        slots = ctx.step(
            "s1", "store-documents", "action",
            fn=lambda: [
                slot.model_dump()
                for slot in service._store_documents(submitted, employee, ctx.record.id)
            ],
            writes=lambda out: [
                f"document:{slot['document_id']}" for slot in (out or [])
            ],
            # No "no documents attached" branch any more: the service refuses a
            # submission with no document, so an empty slot list is not a state
            # this step can be in. It used to be, and a run in it produced an
            # extraction with no artifact behind it.
            summary=lambda out: (
                f"{len(out)} document(s) stored in "
                f"`{DOCUMENT_COLLECTION}` · retention "
                f"{data_collections.get(DOCUMENT_COLLECTION).retention}"
            ),
        )
        slot_models = [DocumentSlot.model_validate(s) for s in slots]

        # x0 · transform — the governed read the ModelCall runs on. A prompt is
        # knowledge, so it is read here at a revision rather than compiled in,
        # and the revision travels onto every extraction step below.
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

        # The price sheet the extractions below are metered against. Read once
        # for the run rather than per document, and carried onto each ModelCall
        # step's read-set: the rates do not change what the flow *does*, only
        # what its journal says a call cost, so this is a ref on the metered
        # step rather than a governed read a Rule consumes.
        rates = read_model_rates(service.knowledge)

        # m{i} · ModelCall — one step per document, so a retry, a correction or
        # a cost lands on the document it belongs to.
        extractions: list[ReceiptExtraction] = []
        for index, (slot, doc) in enumerate(zip(slot_models, submitted), start=1):
            raw = ctx.step(
                f"m{index}", f"extract-receipt[{index}]", "modelcall",
                # v9's own behaviour, now stated rather than assumed. The
                # submitter's memo was concatenated into the extractor's
                # steering string and the stub reader regex-parses that string,
                # so on this version a submitter really could type
                # `total=500` into the memo box; and the stub's invented region
                # geometry really was persisted onto the document. Both are
                # doors v10 closes. They stay open *here* because a shipped
                # version has to replay as it ran — the argument is in
                # `ReimbursementService._extract`.
                fn=lambda d=doc, s=slot: service._extract(
                    d, s, prompt.text,
                    hint=f"{d.hint} {d.memo}".strip(),
                    store_regions=True,
                ).model_dump(),
                summary=lambda out: (
                    f"{out['model']}: {out['vendor']} · ${out['total_usd']:,.2f} · "
                    f"{out['category']} · {len(out['lines'])} line(s) · "
                    f"confidence {out['confidence']:.2f}"
                ),
                # The extraction already knows which model read the receipt,
                # so the step takes its accounting off the output rather than
                # off the reader's name — which is the backend ("vertex"), not
                # a model, and was being filed as one.
                meter=lambda out: rates.priced(ModelUsage(
                    model=str(out.get("model") or ""),
                    input_tokens=int(out.get("input_tokens") or 0),
                    output_tokens=int(out.get("output_tokens") or 0),
                )),
                refs=[
                    f"document:{slot.document_id}", prompt.source_ref,
                    rates.source_ref,
                ],
                retries=1,
                # A step that *threw* goes to the operations desk, not to the
                # approver's. This escalated to `expense-review` and put a
                # Vertex 400 about a file format in front of the Controller,
                # who cannot fix a mime type — while the engineer who can was
                # not looking at that queue. The business escalations below
                # still route to a reviewer, and that difference is the point:
                # one of these is somebody's judgment to give and the other is
                # a bug.
                escalate_queue=routing.OPS_QUEUE,
            )
            extractions.append(ReceiptExtraction.model_validate(raw))

        # x1 · transform — the governed read. The Rule never fetches.
        policy_raw = ctx.step(
            "x1", "read-policy", "transform",
            fn=lambda: service.read_policy().model_dump(),
            summary=lambda out: (
                f"{out['source_ref']}: limit ${out['limit_usd']:,.2f} · "
                f"min confidence {out['min_confidence']:.2f} · "
                f"purpose above ${out['purpose_required_above_usd']:,.2f} · "
                f"restricted {', '.join(out['restricted'])}"
            ),
        )
        policy = PolicyValues.model_validate(policy_raw)
        ctx.note_refs(policy.source_ref)

        # x2 · transform — documents + extractions + commentary become facts.
        lines_raw = ctx.step(
            "x2", "project-lines", "transform",
            fn=lambda: [
                line.model_dump()
                for line in project_lines(slot_models, extractions, commentary)
            ],
            summary=lambda out: (
                f"{len(out)} line(s) projected · requested "
                f"${sum(line['requested_usd'] for line in out):,.2f}"
                + (
                    " · submitter's own split honored"
                    if any(
                        line["requested_usd"] < line["document_total_usd"] - 0.001
                        for line in out
                    )
                    else ""
                )
            ),
        )
        lines = [ExpenseLine.model_validate(line) for line in lines_raw]

        # x3 · transform — which desk an elevated request would land on.
        #
        # A *transform* and not part of the Rule, because answering it means
        # walking the org chart and DOCTRINE point 199 is explicit that a Rule
        # never fetches: the caller gathers the inputs and hands them in. So
        # the graph walk happens here, once, and the Rule below is handed a
        # concrete queue.
        #
        # Resolved before the loop rather than at the gate so that the *reason*
        # is in the journal whether or not anybody was ultimately asked — "this
        # would have gone to Diane" is worth knowing about a request that
        # auto-approved, and it is what makes the routing table auditable
        # rather than merely effective.
        requested_total = round(sum(line.requested_usd for line in lines), 2)
        restricted_here = any(
            line.category in policy.restricted for line in lines
        )
        routed = ResolvedDesk.model_validate(ctx.step(
            "x2r", "resolve-approver", "transform",
            fn=lambda: service._resolve_desk(
                employee, requested_total, restricted_here
            ).model_dump(),
            summary=lambda out: out["explanation"],
            refs=[policy.source_ref],
        ))
        ctx.note_refs(f"queue:{routed.queue}")

        # The review loop. Each round is its own Rule step and its own gate, so
        # the journal shows exactly how many times a person was asked.
        state: ReimbursementState = "approved"
        reason = "auto-approved within governed policy"
        escalations: list[Escalation] = []
        override = False
        decided_by = ""
        decision_note = ""
        rounds = 0

        for round_number in range(1, MAX_REVIEW_ROUNDS + 1):
            checked = ctx.step(
                f"r{round_number}", "needs-elevation", "rule",
                fn=lambda ln=lines: _check(ln, policy),
                summary=lambda out: (
                    "elevate: " + "; ".join(e["detail"] for e in out["escalations"])
                    if out["escalations"]
                    else "approve: within limit, allowed category, confident "
                         "extraction, purpose stated"
                ),
                refs=[policy.source_ref],
            )
            escalations = [Escalation.model_validate(e) for e in checked["escalations"]]
            rounds = round_number
            if not escalations:
                if round_number > 1:
                    reason = (
                        f"approved after {round_number - 1} review round(s) — "
                        f"corrected by {decided_by}"
                    )
                break

            decision = ctx.gate(
                f"k{round_number}", "elevate-review",
                build_task=lambda rn=round_number, esc=escalations, ln=lines: (
                    service._build_review_task(
                        ctx.record, ln, esc, policy, rn, employee, routed
                    )
                ),
            )
            decided_by = decision.decided_by
            decision_note = decision.note or decision.correction

            if decision.verdict == "rejected":
                state = "denied"
                reason = decision.note or "denied on review"
                break
            if decision.verdict == "approved":
                # The reviewer looked at the flags and accepted them. Re-running
                # the Rule would raise the same flags forever, so the override
                # is recorded and the loop ends.
                override = True
                state = "approved"
                reason = (
                    "approved on review despite: "
                    + "; ".join(e.detail for e in escalations)
                )
                break

            patched = ctx.step(
                f"x{2 + round_number}", "apply-correction", "transform",
                fn=lambda ln=lines, d=decision: [
                    line.model_dump()
                    for line in apply_correction(ln, d.payload, d.decided_by)
                ],
                summary=lambda out: (
                    f"reviewer supplied {len(out)} line(s) — re-checking against "
                    "the same governed values"
                ),
            )
            lines = [ExpenseLine.model_validate(line) for line in patched]
        else:
            # Fell out of the loop still flagged: stop asking.
            state = "denied"
            reason = (
                f"unresolved after {MAX_REVIEW_ROUNDS} review rounds: "
                + "; ".join(e.detail for e in escalations)
            )

        # a1 · Action — the graph write, through the Gateway.
        request = ReimbursementRequest(
            id=ctx.record.case_ref or ctx.record.id,
            run_id=ctx.record.id,
            employee=employee,
            submitted_at_iso=submitted_at,
            commentary=commentary,
            documents=slot_models,
            lines=lines,
            state=state,
            verdict_reason=reason,
            escalations=escalations if state != "approved" or override else [],
            review_rounds=rounds - 1 if rounds else 0,
            human_override=override,
            policy_ref=policy.source_ref,
            policy_limit_usd=policy.limit_usd,
            model=extractions[0].model if extractions else "",
            task_id=next(
                (s.task_id for s in ctx.record.steps if s.task_id), ""
            ),
            decided_by=decided_by,
            decision_note=decision_note,
        ).refresh_projections()

        # Who commits it: the approver who released the last gate, or the Flow
        # itself when a clean request cleared the governed limit with no gate
        # at all. Crediting the submitter with a decision nobody made would be
        # the ledger's first lie.
        commit = service.gateway.action(
            "record-expense", ctx.principal or EXPENSE_FLOW,
            run=ctx.record, step_id="a1",
            task_id=request.task_id, decided_by=decided_by,
        )
        written = ctx.step(
            "a1", "record-expense", "action",
            fn=lambda: commit.commit(
                lambda w: service._write_graph(request, policy, decided_by, w)
            ),
            writes=lambda _out: commit.writes,
            summary=lambda out: (
                f"{request.state} · ${request.requested_usd:,.2f} · "
                f"{out['objects']} object(s) and {out['links']} link(s) written "
                f"· ledger {commit.entry.id}"
            ),
        )
        request.graph_ref = written.get("request_ref", "")
        for line, ref in zip(request.lines, written.get("line_refs", [])):
            line.graph_ref = ref
        request.journal = [JournalEvent.model_validate(e) for e in ctx.record.journal()]
        service.directory.put(request)

        ctx.event("e1", "ExpenseRecorded", f"emitted on commit · {request.state}")
        request.journal = [JournalEvent.model_validate(e) for e in ctx.record.journal()]
        service.directory.put(request)

        log.info(
            "Expense request recorded",
            extra={
                "reimbursementId": request.id, "runId": ctx.record.id,
                "state": request.state, "amount": request.requested_usd,
                "lines": len(request.lines), "actor": employee,
                "reviewRounds": request.review_rounds,
            },
        )
        return {
            "request_id": request.id,
            "state": request.state,
            "requested_usd": request.requested_usd,
            "lines": len(request.lines),
        }

    return body
