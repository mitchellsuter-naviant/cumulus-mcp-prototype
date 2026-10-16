"""Expense reimbursement, v10 — submit, extract, the submitter confirms, record.

The whole version is one sentence: **a person submits a receipt, one governed
ModelCall reads it, that person confirms what it read, and the confirmed facts
go into the graph.** Everything else v9 did between those four moments was
either a door that should never have been open or a step the map did not admit
to running.

What changed, and why each one is a change rather than a preference:

- **Evidence is required.** v9 accepted a submission with no document at all —
  the service minted an empty `SubmittedDocument` from the commentary — and
  then extracted, projected and *wrote the graph* from it. Facts with no
  artifact behind them are indistinguishable afterwards from facts with one.
  The REST door answers 400 and `ReimbursementService.submit` refuses.

- **The submitter's words stop steering the extractor.** v9 concatenated the
  document's note into the reader's hint, and `StubReceiptReader` regex-parses
  that hint: `total=500 vendor=corner-bistro` typed into a note box was an
  extraction the submitter had authored. The note is now the document's `memo`
  — read for a stated claim, read for a purpose, shown back on the confirm
  screen, and never handed to the reader. Steering survives only on
  `SubmittedDocument.hint`, which no REST body can set.

- **No synthetic regions.** v9 persisted the stub's invented box geometry onto
  the stored document, and a review screen drew those boxes on a real
  photograph. There is no field locator for receipts, so v10 stores no regions
  at all rather than plausible ones. Accounts payable's v8 refused the same
  thing for the same reason.

- **The gate is always raised, and it goes to the submitter.** v9's Rule chose
  between auto-approving and elevating to somebody else's desk. Confirming an
  extraction is not somebody else's judgment: nobody but the person holding the
  receipt can say whether the number on it is the number on it. So the Rule
  still runs and still says what it found — over the limit, restricted
  category, no stated purpose — and those findings *annotate* the Task instead
  of choosing a desk. Which desk is still governed knowledge: the policy
  asset's `## Approval routing` table says `submitter`, and editing it there
  moves the next submission with no code change.

- **The map matches the run.** v9 declared seven steps and executed twelve;
  four executed families (`s1`, `x0`, `x2`, `x2r`) appeared nowhere in the
  export, and the Event it declared (`ReimbursementRecorded`) was not the one
  it emitted (`ExpenseRecorded`). Every family below is declared, and
  `test_reimbursements.py` pins the two sets equal.

- **The extraction model is `gemini-3.7-flash`, from config.** A photographed
  receipt is a harder read than a clean vendor PDF, so it gets the stronger
  model — as a setting (`EXPENSE_MODEL`), declared in the step config below,
  with a test pinning the declaration to the configured value. A map naming a
  model the deployment does not call is the transparency bar lying.

- **Corrections feed the flywheel.** When the confirm decision moves a value
  the model read, `ReimbursementService._emit_correction` emits the same
  `ExtractionCorrected` fact accounts payable emits, and the existing
  `ap-corrections` flow appends the raw→corrected pairs to the `eval-datasets`
  Collection. Decoupled via an Event, per doctrine; nothing is promoted into
  the prompt.

Step ids follow accounts payable's convention: a repeated family member is
`base_{n}`, so the declared `m1`, `r1`, `k1` and `x3` below are the families,
and `m1_2`, `r1_2`, `k1_2`, `x3_2` are their second instances. A step id is
part of the journal's shape and is never renumbered — which is why this is a
new version package rather than an edit to v9's.
"""

from __future__ import annotations

from ....config import settings
from ....flows import FlowStep
from ....runs import RunContext
from ....task_screens import CORRECT_THE_FACTS, TaskScreen, TaskUi
from ..domain import *  # noqa: F401,F403 — the body reads its domain's vocabulary
from ..domain import _check, _clip  # noqa: F401

VERSION = 10
LABEL = "v10"
NOTES = (
    "Submit, extract, confirm, record. Evidence is required; the submitter's "
    "memo is shown to them and never given to the extractor; no synthetic "
    "regions are stored; the receipt is read once on gemini-3.7-flash. The "
    "review is always raised and the governed routing table sends it back to "
    "the person who submitted it, because nobody else can say whether the "
    "number on the receipt is the number on the receipt. The policy checks "
    "still run and annotate that review rather than choosing a desk."
)

#: One screen, because there is one question. v9 declared a screen per kind for
#: an approver ruling on somebody else's receipt; this asks the submitter to
#: confirm their own, and the checks only say what is worth looking at twice.
CONFIRM_SCREEN = "expense-reimbursement/confirm-values"

UI = TaskUi(
    otherwise=TaskScreen(
        screen_id=CONFIRM_SCREEN,
        label="Confirm the values",
        hint=CORRECT_THE_FACTS,
    ),
)

#: The extraction model, off the deployment's setting rather than typed here.
#: Declared so the map names it; read from config so the map cannot drift from
#: what `deps.get_reimbursement_service` actually builds.
EXTRACT_MODEL = settings.expense_model

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="ExpenseSubmitted",
             summary="one run per submitted receipt — the fact, not the run: which response an expense submission starts is the Trigger table's answer",
             config={"event": "ExpenseSubmitted"}, next=["s1"]),
    FlowStep(id="s1", type="action", name="store-documents",
             summary="guarded store of every submitted artifact into the audit-documents Collection, which is what stamps their two-year retention — a submission carrying no bytes is refused before it gets here",
             config={"target": DOCUMENT_COLLECTION,  # noqa: F405
                     "gateway": "action-gateway"},
             next=["x0"]),
    FlowStep(id="x0", type="transform", name="read-knowledge",
             summary="gathers the governed knowledge this run needs — the extraction prompt, the policy values the Rule consumes, and the approval-routing table — each at the revision actually read, and records every ref: rules never fetch",
             config={"prompt": f"knowledge:{PROMPT_ASSET_ID}",  # noqa: F405
                     "policy": f"knowledge:{POLICY_ASSET_ID}",  # noqa: F405
                     "routing": f"knowledge:{POLICY_ASSET_ID}#approval-routing"},  # noqa: F405
             next=["m1"]),
    FlowStep(id="m1", type="modelcall", name="extract-receipt",
             summary="reads one receipt: vendor, date, total, category and the distinct charges on it, with a confidence per field — one metered call per document, so a retry or a cost lands on the document it belongs to. The submitter's memo is not part of the instruction.",
             config={"model": EXTRACT_MODEL,
                     "prompt": f"knowledge:{PROMPT_ASSET_ID}",  # noqa: F405
                     "on_failure": f"escalate to {routing.OPS_QUEUE}"},  # noqa: F405
             next=["x1"]),
    FlowStep(id="x1", type="transform", name="project-lines",
             summary="documents plus extractions plus the submitter's own words become facts: a compound receipt yields two lines, and a stated claim (\"only requesting $75 of the $110\") lowers what a line asks for — never raises it",
             next=["x2"]),
    FlowStep(id="x2", type="transform", name="resolve-reviewer",
             summary="which desk confirms this, per the governed routing table: `submitter` resolves to the submitter's own desk, and every way of failing to resolve lands on the policy's fallback desk saying so",
             config={"source": f"knowledge:{POLICY_ASSET_ID}#approval-routing",  # noqa: F405
                     "fallback": REVIEW_QUEUE},  # noqa: F405
             next=["r1"]),
    FlowStep(id="r1", type="rule", name="policy-check",
             summary="a pure check over what the flow gathered: within the governed limit, an allowed category, a confident reading, a stated purpose where one is required, and nothing claiming more than its document supports",
             config={"rule_type": "threshold",
                     "source": f"knowledge:{POLICY_ASSET_ID}"},  # noqa: F405
             next=["k1"]),
    FlowStep(id="k1", type="task", name="confirm-values",
             summary="the submitter confirms what was read off their own receipt, with the policy findings attached as context — they may correct an amount, split a document into several lines, add a line the extraction missed, or supply a purpose",
             config={"queue": "resolved per the governed routing table",
                     "screen": CONFIRM_SCREEN,
                     "max_rounds": str(MAX_REVIEW_ROUNDS)},  # noqa: F405
             next=["a1", "x3"], branch_labels=["confirm", "correct"]),
    FlowStep(id="x3", type="transform", name="apply-correction",
             summary="applies the submitter's patch — edited values, added or removed lines — marking every touched line as human-authored, then hands the corrected facts back to the same check",
             next=["r1"]),
    FlowStep(id="a1", type="action", name="record-expense",
             summary="guarded write of the request, its lines and the documents behind them into the graph, committed under the desk of the Task that authorized it",
             config={"target": "expense-records", "gateway": "action-gateway"},
             next=["e1"]),
    FlowStep(id="e1", type="event", name="ExpenseRecorded",
             summary="emitted on commit — the name the flow actually emits, which v9's export got wrong",
             config={"event": "ExpenseRecorded"}),
]


def build_body(service):
    def body(ctx: RunContext, inputs: dict) -> dict:
        employee = inputs["employee"]
        commentary = inputs.get("commentary", "")
        submitted_at = inputs.get("submitted_at_iso") or utcnow().isoformat()  # noqa: F405
        submitted = [
            SubmittedDocument.model_validate(d) for d in inputs["documents"]  # noqa: F405
        ]
        if not submitted:
            # The service refuses this and so does the REST door; this is the
            # floor under a run started some third way. An extraction with no
            # artifact behind it is a claim nobody can check.
            raise ValueError(
                "an expense-reimbursement run needs at least one document"
            )

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
            summary=lambda out: (
                f"{len(out)} document(s) stored in "
                f"`{DOCUMENT_COLLECTION}` · retention "  # noqa: F405
                f"{data_collections.get(DOCUMENT_COLLECTION).retention}"  # noqa: F405
            ),
        )
        slot_models = [DocumentSlot.model_validate(s) for s in slots]  # noqa: F405

        # x0 · transform — one read of the knowledge layer for the whole run.
        #
        # One step and not three. The prompt the ModelCall runs on, the values
        # the Rule consumes and the table the routing transform answers are all
        # knowledge, all read at a revision, and all read *for this run*: three
        # steps would let a console edit land between two of them, so a receipt
        # could be checked against rev 3's limit and routed by rev 4's table.
        reads = GovernedReads.model_validate(ctx.step(  # noqa: F405
            "x0", "read-knowledge", "transform",
            fn=lambda: service._read_knowledge().model_dump(),
            summary=lambda out: (
                f"prompt {out['prompt']['source_ref']}"
                + ("" if out["prompt"]["found_asset"]
                   else " (not in the corpus — running on the built-in default)")
                + f" · policy {out['policy']['source_ref']}: limit "
                f"${out['policy']['limit_usd']:,.2f}, min confidence "
                f"{out['policy']['min_confidence']:.2f}, purpose above "
                f"${out['policy']['purpose_required_above_usd']:,.2f}, "
                f"restricted {', '.join(out['policy']['restricted'])}"
                + " · routing "
                + (
                    f"{len(out['routing']['rules'])} band(s), fallback "
                    f"'{out['routing']['fallback']}'"
                    if out["routing"]["found_asset"]
                    else "no routing section — everything falls back to "
                         f"'{out['routing']['fallback']}'"
                )
            ),
        ))
        policy = reads.policy
        ctx.note_refs(*reads.refs())

        # The price sheet the extractions below are metered against. Read once
        # for the run rather than per document: the rates do not change what the
        # flow *does*, only what its journal says a call cost, so this is a ref
        # on the metered step rather than a governed read a Rule consumes.
        rates = read_model_rates(service.knowledge)  # noqa: F405

        # m1_{i} · ModelCall — one step per document.
        extractions: list[ReceiptExtraction] = []  # noqa: F405
        for index, (slot, doc) in enumerate(zip(slot_models, submitted), start=1):
            raw = ctx.step(
                f"m1_{index}", f"extract-receipt[{index}]", "modelcall",
                # `hint=doc.hint` and nothing else, and that is the whole point
                # of the parameter: the submitter's memo is not part of the
                # extractor's instruction. `hint` reaches this model from a
                # test, a fixture or the seed — never from a REST body.
                fn=lambda d=doc, s=slot: service._extract(
                    d, s, reads.prompt.text,
                    hint=d.hint, store_regions=False,
                ).model_dump(),
                summary=lambda out: (
                    f"{out['model']}: {out['vendor']} · ${out['total_usd']:,.2f} · "
                    f"{out['category']} · {len(out['lines'])} line(s) · "
                    f"confidence {out['confidence']:.2f}"
                ),
                # The extraction knows which model read the receipt, so the
                # step takes its accounting off the output rather than off the
                # reader's name — which is the backend ("vertex"), not a model.
                meter=lambda out: rates.priced(ModelUsage(  # noqa: F405
                    model=str(out.get("model") or ""),
                    input_tokens=int(out.get("input_tokens") or 0),
                    output_tokens=int(out.get("output_tokens") or 0),
                )),
                refs=[
                    f"document:{slot.document_id}", reads.prompt.source_ref,
                    rates.source_ref,
                ],
                retries=1,
                # A step that *threw* goes to the operations desk, not to the
                # submitter's. A Vertex 400 about a file format is a bug, and
                # the person who can fix a mime type is not the person holding
                # the receipt. The business findings below stay with the
                # submitter, and that difference is the point.
                escalate_queue=routing.OPS_QUEUE,  # noqa: F405
            )
            extractions.append(ReceiptExtraction.model_validate(raw))  # noqa: F405

        # x1 · transform — documents + extractions + commentary become facts.
        lines_raw = ctx.step(
            "x1", "project-lines", "transform",
            fn=lambda: [
                line.model_dump()
                for line in project_lines(slot_models, extractions, commentary)  # noqa: F405
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
        lines = [ExpenseLine.model_validate(line) for line in lines_raw]  # noqa: F405

        # x2 · transform — which desk confirms this.
        #
        # A transform and not part of the Rule, because answering it means
        # reading a governed table and possibly minting a desk, and DOCTRINE
        # point 199 is explicit that a Rule never fetches: the caller gathers
        # the inputs and hands them in. Resolved before the loop so the desk
        # and the sentence explaining it are in the journal once, and every
        # round of the loop raises its Task to the same place.
        requested_total = round(sum(line.requested_usd for line in lines), 2)
        restricted_here = any(
            line.category in policy.restricted for line in lines
        )
        routed = ResolvedDesk.model_validate(ctx.step(  # noqa: F405
            "x2", "resolve-reviewer", "transform",
            fn=lambda: service._resolve_desk(
                employee, requested_total, restricted_here
            ).model_dump(),
            summary=lambda out: out["explanation"],
            refs=[policy.source_ref],
        ))
        ctx.note_refs(f"queue:{routed.queue}")

        # The confirm loop. Each round is its own Rule step and its own gate,
        # so the journal shows exactly how many times the person was asked.
        state: ReimbursementState = "approved"  # noqa: F405
        reason = ""
        escalations: list[Escalation] = []  # noqa: F405
        override = False
        decided_by = ""
        decision_note = ""
        rounds = 0

        for round_number in range(1, MAX_REVIEW_ROUNDS + 1):  # noqa: F405
            checked = ctx.step(
                f"r1_{round_number}", "policy-check", "rule",
                fn=lambda ln=lines: _check(ln, policy),
                summary=lambda out: (
                    "worth a second look: "
                    + "; ".join(e["detail"] for e in out["escalations"])
                    if out["escalations"]
                    else "nothing flagged: within limit, allowed category, "
                         "confident reading, purpose stated"
                ),
                refs=[policy.source_ref],
            )
            escalations = [
                Escalation.model_validate(e) for e in checked["escalations"]  # noqa: F405
            ]
            rounds = round_number

            # The gate is unconditional, and that is this version's shape. The
            # Rule's findings are context on the question, not the reason for
            # asking it: a receipt nothing flagged still has a total somebody
            # has to vouch for, and the only person who can is the one who was
            # there.
            decision = ctx.gate(
                f"k1_{round_number}", "confirm-values",
                build_task=lambda rn=round_number, esc=escalations, ln=lines: (
                    service._build_confirm_task(
                        ctx.record, ln, slot_models, extractions, esc, policy,
                        rn, employee, routed,
                    )
                ),
            )
            decided_by = decision.decided_by
            decision_note = decision.note or decision.correction

            if decision.verdict == "rejected":
                state = "denied"
                reason = decision.note or "withdrawn on review"
                break
            if decision.verdict == "approved":
                state = "approved"
                override = bool(escalations)
                reason = (
                    "confirmed by " + (decided_by or "the submitter")
                    + (
                        " despite: " + "; ".join(e.detail for e in escalations)
                        if escalations else ""
                    )
                    + (
                        f" after {round_number - 1} correction round(s)"
                        if round_number > 1 else ""
                    )
                )
                break

            patched = ctx.step(
                f"x3_{round_number}", "apply-correction", "transform",
                fn=lambda ln=lines, d=decision: [
                    line.model_dump()
                    for line in apply_correction(ln, d.payload, d.decided_by)  # noqa: F405
                ],
                summary=lambda out: (
                    f"{len(out)} line(s) as the submitter left them — "
                    "re-checking against the same governed values"
                ),
            )
            lines = [ExpenseLine.model_validate(line) for line in patched]  # noqa: F405
        else:
            # Corrected every round and never confirmed. Somebody who has
            # rewritten the same submission three times is telling us the
            # submission is wrong, not that the checks are.
            state = "denied"
            reason = (
                f"never confirmed after {MAX_REVIEW_ROUNDS} correction "  # noqa: F405
                "round(s)"
                + (
                    ": " + "; ".join(e.detail for e in escalations)
                    if escalations else ""
                )
            )

        # a1 · Action — the graph write, through the Gateway.
        request = ReimbursementRequest(  # noqa: F405
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
            # The gate that authorized this, and specifically not "the first
            # step carrying a task id". A run whose extraction threw escalated
            # to the operations desk and put *that* exception task's id on the
            # row and on the ledger entry — so the write appeared to have been
            # authorized by an engineer looking at a mime type.
            task_id=next(
                (
                    s.task_id for s in reversed(ctx.record.steps)
                    if s.kind == "task" and s.task_id
                ),
                "",
            ),
            decided_by=decided_by,
            decision_note=decision_note,
        ).refresh_projections()

        # Who commits it: the human who released the gate, when a live caller
        # drove this pass. A resume with no principal — a seed, an operator's
        # `POST /runs/{id}/resume` — commits on the Flow's own authority, bound
        # to the desk this case was routed to as well as the standing one.
        # `queue=routed.queue` is the desk of the Task that authorized this
        # write: without it the queue that raised the work and the queue that
        # may commit it are different queues, and a submitter answering their
        # own Task would have their own answer refused.
        commit = service.gateway.action(
            "record-expense",
            ctx.principal or expense_flow_principal(routed.queue),  # noqa: F405
            run=ctx.record, step_id="a1",
            task_id=request.task_id, decided_by=decided_by,
            queue=routed.queue,
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
        request.journal = [
            JournalEvent.model_validate(e) for e in ctx.record.journal()  # noqa: F405
        ]
        service.directory.put(request)

        ctx.event("e1", "ExpenseRecorded", f"emitted on commit · {request.state}")
        request.journal = [
            JournalEvent.model_validate(e) for e in ctx.record.journal()  # noqa: F405
        ]
        service.directory.put(request)

        log.info(  # noqa: F405
            "Expense request recorded",
            extra={
                "reimbursementId": request.id, "runId": ctx.record.id,
                "state": request.state, "amount": request.requested_usd,
                "lines": len(request.lines), "actor": employee,
                "queue": routed.queue, "reviewRounds": request.review_rounds,
            },
        )
        return {
            "request_id": request.id,
            "state": request.state,
            "requested_usd": request.requested_usd,
            "lines": len(request.lines),
        }

    return body
