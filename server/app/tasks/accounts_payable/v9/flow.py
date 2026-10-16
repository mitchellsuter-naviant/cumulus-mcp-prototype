"""Accounts payable, v9 — one gate, on the desk of the person who submitted it.

v8 proved the machinery: bytes out of the store, one metered pass, the invoice
checked against itself, boxes on the fields in doubt, the three-way match. What
it inherited from v6 was the *shape* — two clerk gates, a theatrical projection
step, an anomaly screen, and a declared map that did not match the journal the
run wrote. This version keeps the machinery and fixes the shape. Four changes,
each of which was a specific complaint:

**One human gate.** v6 asks twice: once to name the vendor (`k0`) and again to
release the posting (`k2`). Two interruptions for one reading of one document,
and the second reviewer is usually the first. The vendor question folds into
the review: the scored candidates ride the Task's proposal, the decision may
name one, and nothing is asserted about the entity until it does — which was
the whole reason the separate gate existed.

**The declared map is the executed map.** v6 ran an `x0` nobody had declared;
so did v8. Every step this version runs is in `STEPS`, and there is a test that
walks a full run's journal and says so. The read that was invisible is now `x0`
itself: one transform, every governed value the run consumes, every revision on
the step's read-set.

**The review goes to the submitter.** Not to a shared desk somebody else opens
a document in. Where it goes is a governed decision — the `## Approval routing`
table in the posting policy — resolved against this case, and an invoice nobody
handed over (the ones `ap-intake` fetches out of a folder) falls back to
`ap-clerk` and says in the journal that it fell back.

**A zero total can never auto-post.** Not by the limit — nothing is above zero
either — but structurally, in the rule, because a $0.00 total is what an unread
page looks like. A live estate posted one as "matched".

Cut deliberately: `x3 project-lines` (a `model_dump` loop dressed as a step) and
the anomaly screen. The screen is a real capability and it stays selectable on
v8; what it is not is something every invoice needs before a person sees it.

Everything pure is imported rather than copied — the arithmetic, the verdicts,
the locator, the patch applier, the matcher — so a fix to any of them reaches
every shipped version. What v9 owns is the map and the order.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .... import routing
from ....auth import flow_principal
from ....config import settings
from ....flows import FlowStep
from ....matching import (
    MatchLine,
    MatchResult,
    MatchTolerances,
    match_invoice,
    parse_match_tolerances,
)
from ....models import utcnow
from ....prompts import GovernedPrompt
from ....resolution import read_resolution_thresholds
from ....runs import RunContext
from ....task_screens import TaskScreen, TaskUi
from .. import domain
from ..domain import (
    CLERK_QUEUE,
    FLOW_ID,
    MAX_REVIEW_ROUNDS,
    InvoiceExtraction,
    PayableState,
    PostingPolicy,
    _apply_invoice_patch,
    _invoice_match_lines,
)
from ..v6 import flow as v6
from ..v8 import flow as v8
from ..v8.arithmetic import (
    ArithmeticTolerances,
    check_arithmetic,
    parse_arithmetic_tolerances,
    summarize,
)
from ..v8.verdicts import FieldFinding, VerdictSet, in_doubt, verdicts_for

VERSION = 9
LABEL = "v9"
NOTES = (
    "One human gate, on the desk of the person who submitted the invoice. The "
    "run reads its document out of the store, gathers every governed value it "
    "needs in one declared step, makes one metered extraction pass, checks the "
    "invoice against itself, asks a second cheap model where the disputed "
    "fields are (and skips that call entirely when nothing is in doubt), "
    "resolves the vendor, matches against the purchase order and the receiving "
    "report, and then asks one person one question — with the fields in doubt, "
    "the vendor candidates if the resolver could not settle it, and the boxes "
    "on the page. Where that question goes is governed knowledge: the approval "
    "table routes it back to the submitter, and an invoice nobody handed over "
    "falls back to the AP desk. A total of $0.00 never posts unreviewed, "
    "whatever the limit says. The separate vendor gate, the line-projection "
    "step and the anomaly screen are gone; the screen stays selectable on v8."
)

#: One screen, and the fields choose the emphasis — v8's argument, unchanged.
#:
#: Declared for every kind rather than keyed by kind, because this version
#: raises exactly one kind of question about an invoice. The reading, the
#: comparison and now the vendor are all answered on the same page, in front of
#: the same document, by the same person.
UI = TaskUi(
    otherwise=TaskScreen(
        screen_id="accounts-payable/invoice-review",
        label="Open the invoice review",
        hint=(
            "Opens the invoice itself, with a box on each field the run could "
            "not settle and the check’s own sentence beside it. Everything it "
            "read cleanly is folded away; correcting a field re-runs the "
            "checks over it. When the resolver could not settle the vendor, "
            "the scored candidates are on the same page."
        ),
    ),
)


STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="InvoiceReceived",
             summary="one run per inbound invoice, however it arrived",
             config={"event": "InvoiceReceived"}, next=["s1"]),
    FlowStep(
        id="s1", type="action", name="resolve-document",
        summary=(
            "the artifact this run reads: the document `ap-intake` already "
            "stored, found by id, or the bytes an operator uploaded, stored "
            "now — either way a content-hash id, and a run that names neither "
            "is refused rather than invented"
        ),
        config={"target": "documents", "gateway": "action-gateway",
                "arrives": "ap-intake document_id | console upload"},
        next=["x0"],
    ),
    FlowStep(
        id="x0", type="transform", name="read-knowledge",
        summary=(
            "every governed value this run consumes, read once and recorded: "
            "the extraction prompt, the arithmetic tolerances, the auto-post "
            "limit and confidence floor, the match tolerances, the vendor "
            "resolution thresholds, and the approval table that says whose "
            "desk a review lands on — resolved here, against the org chart, "
            "because a Rule may not fetch"
        ),
        config={"source": f"knowledge:{domain.POLICY_ASSET_ID},"
                          f"{domain.PROMPT_ASSET_ID},entity-resolution-policy"},
        next=["m1"],
    ),
    FlowStep(
        id="m1", type="modelcall", name="extract-invoice",
        summary=(
            "one pass over the stored bytes — the vendor as printed, the "
            "invoice number, dates, PO, totals and the billed lines, with a "
            "confidence **per field**. Nothing steers it but the governed "
            "prompt and the page"
        ),
        config={"model": settings.model,
                "prompt": f"knowledge:{domain.PROMPT_ASSET_ID}",
                "reads": "document bytes"},
        next=["c1"],
    ),
    FlowStep(
        id="c1", type="check", name="check-arithmetic",
        summary=(
            "the invoice against itself — every line's amount against its "
            "quantity and price, the goods lines against the subtotal, the "
            "parts against the total, the required fields against their "
            "absence, the due date against the issue date. A pure Rule: it "
            "fetches nothing, and every failure names the field path it "
            "implicates, because those paths become the boxes"
        ),
        config={"rule_type": "arithmetic",
                "source": f"knowledge:{domain.POLICY_ASSET_ID}"},
        next=["x1"],
    ),
    FlowStep(
        id="x1", type="transform", name="field-verdicts",
        summary=(
            "the union a human is actually asked about: fields the extractor "
            "was unsure of, fields the arithmetic contradicts, and required "
            "fields that are not there — three per invoice rather than thirty"
        ),
        config={"language": "python"},
        next=["m2", "z1"],
        branch_labels=["fields in doubt", "nothing in doubt"],
    ),
    FlowStep(
        id="m2", type="modelcall", name="locate-fields",
        summary=(
            "a second, narrow look: given the document and *only* the disputed "
            "field paths, where each one is printed. The boxes are stored as "
            "Regions so the review screen can draw them. Skipped entirely when "
            "nothing is in doubt — no boxes, no call, no cost"
        ),
        config={"model": settings.locate_model,
                "prompt": "knowledge:invoice-locate-prompt",
                "skipped_when": "no field verdicts"},
        next=["z1"],
    ),
    FlowStep(
        id="z1", type="resolve", name="resolve-vendor",
        summary=(
            "the printed name becomes a canonical Vendor id against the vendor "
            "master; an unknown one is minted `onboarding`. An ambiguous one "
            "no longer stops the run: the scored candidates travel into the "
            "one review below, and nothing is asserted about the entity until "
            "a person names it"
        ),
        config={"entity": "Vendor",
                "source": "knowledge:entity-resolution-policy"},
        next=["x2"],
    ),
    FlowStep(
        id="x2", type="transform", name="gather-match-legs",
        summary=(
            "reads the purchase order and receiving lines out of the graph — "
            "the flow gathers; the matcher and the rule never fetch"
        ),
        config={"source": "graph:PurchaseOrder,ReceivingReport"},
        next=["c2"],
    ),
    FlowStep(
        id="c2", type="check", name="three-way-match",
        summary=(
            "pairs invoice ↔ PO ↔ receiving line by line within the governed "
            "tolerances, and writes the pairings as a MatchSet rather than "
            "discarding them"
        ),
        config={"rule_type": "three-way-match",
                "source": f"knowledge:{domain.POLICY_ASSET_ID}"},
        next=["r1"],
    ),
    FlowStep(
        id="r1", type="rule", name="post-or-review",
        summary=(
            "within the governed auto-post limit, confidently extracted, "
            "arithmetically consistent and matched? A total of $0.00 answers "
            "no whatever the limit says"
        ),
        config={"rule_type": "threshold",
                "source": f"knowledge:{domain.POLICY_ASSET_ID}"},
        next=["a1", "k1"], branch_labels=["post", "review"],
    ),
    FlowStep(
        id="k1", type="task", name="review-invoice",
        summary=(
            "the one gate: the fields in doubt with a box on each, the vendor "
            "candidates when the resolver could not settle it, and the "
            "invoice itself. A correction re-runs the match and the rule "
            "rather than overriding them"
        ),
        # No pinned desk, deliberately: `queue` names one where a version has
        # one, and this version's is a *resolution* — a different desk per
        # case, read from the approval table and answered against the org
        # chart. Naming `ap-clerk` here would be the map saying the thing v6
        # said and v9 stopped doing.
        config={"routes_to": routing.SUBMITTER,
                "source": f"knowledge:{domain.POLICY_ASSET_ID}",
                "fallback_queue": CLERK_QUEUE,
                "max_rounds": str(MAX_REVIEW_ROUNDS)},
        next=["a1", "x3", "a0"],
        branch_labels=["approved or rejected", "corrected", "vendor named"],
    ),
    FlowStep(
        id="x3", type="transform", name="apply-correction",
        summary=(
            "the reviewer's corrected values applied to the extraction, keyed "
            "by the same fact paths the verdicts were raised under — then the "
            "match and the rule run again over the corrected invoice"
        ),
        config={"language": "python"}, next=["c2"],
    ),
    FlowStep(
        id="a0", type="action", name="apply-vendor-decision",
        summary=(
            "guarded write of the vendor the reviewer named: an identity link, "
            "or a newly minted vendor. Runs only when the review answered the "
            "vendor question, and nothing was asserted before it"
        ),
        config={"target": "vendor-master", "gateway": "action-gateway"},
        next=["a1"],
    ),
    FlowStep(
        id="a1", type="action", name="post-invoice",
        summary=(
            "guarded write of the invoice, its lines and the match set into "
            "the graph and the AP ledger — committed under the desk of the "
            "Task that authorized it, or under the standing AP desk when the "
            "limit cleared it and nobody decided"
        ),
        config={"target": "ap-ledger", "gateway": "action-gateway"},
        next=["e1"],
    ),
    FlowStep(id="e1", type="event", name="InvoicePosted",
             summary="emitted on commit", config={"event": "InvoicePosted"}),
]


# ---------------------------------------------------------------------------
# x0 — one governed read, and the desk it resolves
# ---------------------------------------------------------------------------


class ReviewRouting(BaseModel):
    """The approval table, and the desk it resolves to for *this* case.

    Two halves, deliberately apart. `table` is governed knowledge read off the
    policy; `submitter_desk` is the answer to a question about this case,
    obtained by walking the org chart and opening a desk in the registry — the
    fetching half, which is why it happens in a transform step and not in the
    rule that consumes it.

    Which band applies depends on the amount, and the amount is not known until
    the page has been read; `desk_for` is therefore a pure function over values
    already in hand, called where the gate is raised.
    """

    table: routing.RoutingPolicy = Field(default_factory=routing.RoutingPolicy)
    #: The person the case is about, or "" when nobody handed the invoice over.
    submitter_email: str = ""
    #: Their own desk, or "" when there is no submitter or no desk to open.
    submitter_desk: str = ""
    #: The resolver's own sentence, written to be read in the Task.
    explanation: str = ""
    fell_back: bool = False

    def desk_for(self, amount_usd: float) -> routing.ResolvedQueue:
        """The desk an invoice of this amount is reviewed at, and why.

        Pure. The only outcome that must never come out of here is an empty
        queue: a Task on no desk is work that exists and that nobody will ever
        see, which is the one failure that looks exactly like success.
        """
        target = self.table.target_for(amount_usd)
        if target == routing.SUBMITTER:
            return routing.ResolvedQueue(
                queue=self.submitter_desk or self.table.fallback,
                target=target,
                explanation=self.explanation,
                fell_back=not self.submitter_desk,
            )
        desk = target or self.table.fallback
        return routing.ResolvedQueue(
            queue=desk, target=target,
            explanation=(
                f"routed to the '{desk}' desk by the governed approval table"
            ),
        )


class GovernedReads(BaseModel):
    """Everything `x0` read, in one step output.

    One step rather than five, and one journal row rather than a read nobody
    declared. v6 and v8 both ran an undeclared `x0` for the prompt and then
    read the posting policy, the arithmetic tolerances and the match
    tolerances from three more places — which meant a reader of the map could
    not tell what a run consults, and a run that read the same asset four
    times could name two revisions of it if somebody republished mid-flight.
    """

    prompt: GovernedPrompt = Field(default_factory=GovernedPrompt)
    posting: PostingPolicy = Field(default_factory=PostingPolicy)
    arithmetic: ArithmeticTolerances = Field(default_factory=ArithmeticTolerances)
    match: MatchTolerances = Field(default_factory=MatchTolerances)
    routing: ReviewRouting = Field(default_factory=ReviewRouting)
    #: The vendor-resolution thresholds' revision. The values themselves are
    #: read by the resolver; what travels is the ref, so `z1` can declare the
    #: read that decided an identity.
    resolution_ref: str = ""
    policy_ref: str = ""


def read_knowledge(
    service, ctx: RunContext, submitted_by: str
) -> GovernedReads:
    """x0 — the whole read-set, before anything is read *from* the page.

    Every value below is a decision somebody took in a document: what counts
    as close enough on a line, how confident is confident enough, how far a
    price may be off the order, and whose desk a held invoice lands on. None of
    them is a literal here, and the revision of each is on this step.
    """
    content, ref = service._policy_asset_content()
    prompt = service.read_prompt()
    posting = service.read_policy()
    table = routing.parse_routing(content, default_fallback=CLERK_QUEUE)
    table.source_ref = ref
    resolved = service._resolve_submitter_desk(submitted_by, table.fallback)
    thresholds_ref = read_resolution_thresholds(service.knowledge).source_ref

    def gather() -> dict:
        return GovernedReads(
            prompt=prompt,
            posting=posting,
            arithmetic=parse_arithmetic_tolerances(content, ref),
            match=parse_match_tolerances(content, ref),
            routing=ReviewRouting(
                table=table,
                submitter_email=domain.submitter_email(submitted_by),
                submitter_desk="" if resolved.fell_back else resolved.queue,
                explanation=resolved.explanation,
                fell_back=resolved.fell_back,
            ),
            resolution_ref=thresholds_ref,
            policy_ref=ref,
        ).model_dump(mode="json")

    reads = GovernedReads.model_validate(ctx.step(
        "x0", "read-knowledge", "transform",
        fn=gather,
        summary=lambda out: _reads_summary(GovernedReads.model_validate(out)),
        refs=[ref, prompt.source_ref, thresholds_ref],
    ))
    ctx.note_refs(reads.policy_ref, reads.prompt.source_ref, reads.resolution_ref)
    return reads


def _reads_summary(reads: GovernedReads) -> str:
    desk = reads.routing.submitter_desk or reads.routing.table.fallback
    return (
        f"{reads.policy_ref}: auto-post ≤ ${reads.posting.limit_usd:,.2f} · "
        f"document confidence ≥ {reads.posting.min_confidence:.2f} · field "
        f"confidence ≥ {reads.arithmetic.min_field_confidence:.2f} · lines "
        f"within ${reads.arithmetic.line_amount_usd:,.2f} · prices within "
        f"{reads.match.price_variance_pct:.1f}% · review to `{desk}`"
        + ("" if reads.prompt.found_asset
           else " — no extraction prompt in the corpus, running on the "
                "built-in default")
        + ("" if reads.routing.table.found_asset
           else " — no approval table in the policy, so every review falls "
                f"back to `{reads.routing.table.fallback}`")
    )


# ---------------------------------------------------------------------------
# m1 — the one pass over the page
# ---------------------------------------------------------------------------


def extract(
    service,
    ctx: RunContext,
    document_id: str,
    filename: str,
    reads: GovernedReads,
    meter,
) -> InvoiceExtraction:
    """m1 — one metered ModelCall over the bytes in the store.

    The `hint` argument the reader still takes is passed empty, deliberately
    and permanently: a caller-supplied string that steers an extraction is what
    v8 existed to remove and this version keeps removed. A composed fixture
    steers a *stub* reader through the page it wrote, which is the same
    relationship a real extractor has to a real invoice.

    `store_regions=False` for v8's reason: the only regions any reader here
    produces are the stub's synthetic geometry, and persisting those would draw
    invented boxes on a real document. v9's boxes come from `m2` or the
    document carries none and the screen says so.
    """
    rates_ref, priced = meter
    return InvoiceExtraction.model_validate(ctx.step(
        "m1", "extract-invoice", "modelcall",
        fn=lambda: service._extract(
            service._document_bytes(document_id), filename, "", document_id,
            reads.prompt.text, store_regions=False,
        ).model_dump(),
        summary=v6.extraction_summary,
        meter=priced,
        refs=[f"document:{document_id}", reads.prompt.source_ref, rates_ref],
        # The operations desk, not the reviewer's: an extraction that threw is
        # an engineering fault, and nobody at a finance desk has a verdict to
        # give on one.
        retries=1, escalate_queue=routing.OPS_QUEUE,
    ))


# ---------------------------------------------------------------------------
# c1 / x1 / m2 — what the invoice disagrees with, and where it is on the page
# ---------------------------------------------------------------------------


def field_verdicts(
    service,
    ctx: RunContext,
    extraction: InvoiceExtraction,
    tolerances: ArithmeticTolerances,
    document_id: str,
    filename: str,
    meter,
) -> VerdictSet:
    """v8's three steps under v9's numbering, calling v8's pure functions."""
    findings = ctx.step(
        "c1", "check-arithmetic", "check",
        fn=lambda: [
            finding.model_dump()
            for finding in check_arithmetic(extraction, tolerances)
        ],
        summary=lambda out: summarize([_finding(item) for item in out]),
        refs=[tolerances.source_ref],
    )

    def union() -> dict:
        fields = verdicts_for(
            extraction, [_finding(item) for item in findings],
            min_field_confidence=tolerances.min_field_confidence,
        )
        return VerdictSet(
            fields=fields,
            in_doubt=[verdict.path for verdict in in_doubt(fields)],
        ).model_dump()

    verdict_set = VerdictSet.model_validate(ctx.step(
        "x1", "field-verdicts", "transform",
        fn=union,
        summary=lambda out: VerdictSet.model_validate(out).summary(),
    ))
    if verdict_set.clean:
        # The whole point of the step above: no doubts, so no second call and
        # no boxes. The journal shows `m2` missing rather than present-and-
        # empty, which is the honest record of a call that was never made.
        return verdict_set

    # v8's own step, id and acceptor guards included: a box for a path nobody
    # asked about, one off the page, or a second box for one number is refused
    # and counted rather than drawn.
    located = v8.locate_fields(
        service, ctx, document_id, filename, verdict_set.in_doubt, meter
    )
    boxes = {region.field: region for region in located}
    return verdict_set.model_copy(update={
        "fields": [
            verdict.model_copy(update={"region": boxes.get(verdict.path)})
            if verdict.status != "ok" else verdict
            for verdict in verdict_set.fields
        ],
    })


def _finding(item: dict) -> FieldFinding:
    return FieldFinding.model_validate(item)


# ---------------------------------------------------------------------------
# z1 — the vendor, resolved and *not* gated
# ---------------------------------------------------------------------------


def resolve_vendor(
    service,
    ctx: RunContext,
    extraction: InvoiceExtraction,
    resolution_ref: str,
) -> tuple[str, str, dict]:
    """z1 — the printed name becomes one canonical vendor, and the run goes on.

    A resolution, not a decision: the resolver picked a winner among candidates
    by the governed thresholds and nobody was asked. Automatic like an
    observation, ledgered like a decision.

    Where v6 stops — `review`, meaning the top candidate scored inside the
    steward band — this version carries the question forward instead. The
    reasoning is not that the question got less important; it is that a person
    is going to open this document anyway, in a screen that shows the printed
    name, and asking them twice about one page is the interruption the doctrine
    warns about. Nothing is asserted about an unsettled identity here either
    way: a `review` disposition mints nothing and links nothing, and `a0` is
    the only step that can.
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
            + (
                " — carried into the review rather than gated on its own"
                if out["disposition"] == "review" else ""
            )
        ),
        refs=[resolution_ref],
    )
    return (
        resolution_raw.get("vendor_id", ""),
        resolution_raw.get("label", ""),
        resolution_raw,
    )


def vendor_question(
    extraction: InvoiceExtraction, resolution_raw: dict
) -> dict | None:
    """The vendor block a review carries, or `None` when there is nothing to ask.

    §4's shape, and `None` rather than an empty dict for `fields`' reason: a
    settled vendor and an unsettled one with no candidates are different facts,
    and a screen showing an empty picker would ask a question nobody raised.
    """
    if resolution_raw.get("disposition") != "review":
        return None
    return {
        "resolved": None,
        "printed": extraction.vendor_name or "no clear party",
        "candidates": [
            {
                "id": str(candidate.get("object_id", "")),
                "name": str(candidate.get("label", "")),
                "score": float(candidate.get("score", 0) or 0),
            }
            for candidate in (resolution_raw.get("candidates") or [])[:5]
        ],
    }


# ---------------------------------------------------------------------------
# x2 / c2 — the legs, and the match
# ---------------------------------------------------------------------------


def gather_and_match(
    service,
    ctx: RunContext,
    extraction: InvoiceExtraction,
    vendor_id: str,
    reads: GovernedReads,
) -> tuple[dict, MatchResult]:
    """x2/c2 — read the other two legs out of the graph, then pair them.

    v6's `x3 project-lines` is gone from between these two. It dumped the
    extraction's own line models into the journal and handed them to nobody:
    the matcher builds its own `MatchLine`s below and the graph write builds
    its own claims, so the step's entire effect was a second copy of `m1`'s
    output in the run record. A map is a promise about what happens, and a step
    that does nothing spends a reader's attention to keep no promise.
    """
    context = ctx.step(
        "x2", "gather-match-legs", "transform",
        fn=lambda: service._gather_match_context(vendor_id, extraction.po_number),
        summary=lambda out: (
            f"{len(out['po_numbers'])} PO(s) and "
            f"{len(out['receipt_numbers'])} receiving report(s) in the graph "
            "for this invoice"
            if out["po_numbers"] or out["receipt_numbers"] else
            "no purchase order or receiving report in the graph for this invoice"
        ),
        refs=[f"graph:Vendor/{vendor_id}"] if vendor_id else [],
    )
    return context, match(ctx, "c2", extraction, context, reads.match)


def match(
    ctx: RunContext,
    step_id: str,
    extraction: InvoiceExtraction,
    context: dict,
    tolerances: MatchTolerances,
) -> MatchResult:
    """The three-way match as a pure check: values in, verdict out.

    Handed parsed tolerances rather than the policy document v6's matcher
    reads, which is the difference between a Rule and a step that fetches: the
    numbers were read at `x0`, at a revision this run recorded, and re-parsing
    them here would be a second read that could disagree with the first.
    """
    return MatchResult.model_validate(ctx.step(
        step_id, "three-way-match", "check",
        fn=lambda: match_invoice(
            [
                MatchLine.model_validate(line)
                for line in _invoice_match_lines(extraction)
            ],
            [MatchLine.model_validate(line) for line in context["po_lines"]],
            [MatchLine.model_validate(line) for line in context["receipt_lines"]],
            tolerances,
            invoice_total_usd=extraction.total_usd,
        ).model_dump(),
        summary=lambda out: MatchResult.model_validate(out).summary(),
        refs=[tolerances.source_ref] + [
            f"graph:PurchaseOrder/{number}" for number in context["po_numbers"]
        ],
    ))


# ---------------------------------------------------------------------------
# r1 / k1 / x3 / a0 / a1 — the rule, the one gate, and the commit
# ---------------------------------------------------------------------------


def _rule(tolerances: ArithmeticTolerances, base):
    """The posting rule this version evaluates: the governed check plus the
    arithmetic, with the anomaly screen's argument removed.

    Recomputed from the extraction it is handed rather than closed over `x1`'s
    verdicts, and that is not a duplicate check — it is the only correct one. A
    reviewer who corrects a line is handed back to this rule with a *different*
    invoice, and a closure holding round one's findings would hold a payment
    for a disagreement the correction had just settled.

    Still pure: `check_arithmetic` reads its two arguments and nothing else,
    and `screen` arrives as `{}` because this version screens nothing.
    """

    def check(extraction, match_result, screen, policy) -> dict:
        verdict = base(
            extraction, match_result, screen, policy, check_line_sum=False
        )
        return {
            **verdict,
            "reasons": [
                f"{finding.path}: {finding.reason}"
                for finding in check_arithmetic(extraction, tolerances)
            ] + verdict["reasons"],
        }

    return check


def decide_and_post(
    service,
    ctx: RunContext,
    *,
    reads: GovernedReads,
    context: dict,
    match_result: MatchResult,
    extraction: InvoiceExtraction,
    vendor_id: str,
    vendor_label: str,
    resolution_raw: dict,
    submitted_by: str,
    submitted_at: str,
    filename: str,
    document_id: str,
    check,
    fields_for,
) -> dict:
    """r1/k1/x3/a0/a1 — the rule, the review loop, the vendor, and the commit."""
    policy = reads.posting
    state: PayableState = "posted"
    reason = "auto-posted within governed policy"
    decided_by = ""
    decision_note = ""
    commit_queue = ""
    vendor = vendor_question(extraction, resolution_raw)

    for round_number in range(1, MAX_REVIEW_ROUNDS + 1):
        checked = ctx.step(
            f"r1_{round_number}", "post-or-review", "rule",
            fn=lambda: check(extraction, match_result, {}, policy),
            summary=lambda out: (
                out.get("note")
                or ("review: " + "; ".join(out["reasons"]) if out["reasons"]
                    else "post: within limit, consistent and matched")
            ),
            refs=[policy.source_ref],
        )
        reasons = list(checked["reasons"])
        if vendor is not None:
            # The *entity* is unsettled, which is a reason to ask whatever the
            # numbers say. Posting against a vendor nobody settled is the one
            # mistake in this flow that no later correction recovers — the
            # payment went to a company — so it is spelled out as a reason
            # rather than left to be inferred from a block on the Task.
            #
            # Added by the flow rather than inside the Rule, and the split is
            # the point: the Rule judges the invoice against governed
            # thresholds and is replayable against a fixture, where "did the
            # resolver settle this name" is a fact about a step that ran. The
            # journal keeps them apart — `r1`'s output holds the rule's own
            # reasons — and the Task a person opens holds both.
            reasons.insert(
                0,
                "the resolver could not settle which vendor issued this "
                f"invoice from the name printed on it ({vendor['printed']})",
            )
        if not reasons:
            if round_number > 1:
                reason = f"posted after review by {decided_by}"
            break
        desk = reads.routing.desk_for(extraction.total_usd)
        decision = ctx.gate(
            f"k1_{round_number}", "review-invoice",
            build_task=lambda rs=reasons, rn=round_number, q=desk.queue,
            w=desk.explanation: (
                service._build_review_task(
                    ctx.record, extraction, match_result, {}, policy, rs, rn,
                    vendor_label, submitted_by,
                    fields=fields_for(extraction),
                    gate_id="k1", queue=q, routing_note=w, vendor=vendor,
                )
            ),
        )
        decided_by = decision.decided_by
        decision_note = decision.note or decision.correction
        # The desk that authorized the commit, so the Gateway checks the queue
        # that actually raised the work rather than a standing pin the reviewer
        # may not hold. A resolution that fell back is not hidden either: its
        # sentence says which desk it landed on instead of whose, and travels
        # onto the Task, because a deployment routing everything to the
        # fallback because no submitter ever reaches the flow is a broken door
        # wearing a working one.
        commit_queue = desk.queue

        if vendor is not None and decision.verdict in ("approved", "corrected"):
            vendor_id, vendor_label = apply_vendor_decision(
                service, ctx, extraction, resolution_raw, decision,
                chosen=str(decision.payload.get("vendor_id") or ""),
                queue=commit_queue,
                task_id=f"task_{ctx.record.id}_k1_{round_number}",
            )
            vendor = None

        if decision.verdict == "rejected":
            state = "denied"
            reason = decision.note or "rejected on review"
            break
        if decision.verdict == "approved":
            state = "posted"
            reason = "posted on review despite: " + "; ".join(reasons)
            break
        # `corrected`: the reviewer fixed the extraction, so re-run the checks
        # against the corrected values rather than trusting the override.
        patched = ctx.step(
            f"x3_{round_number}", "apply-correction", "transform",
            fn=lambda d=decision: _apply_invoice_patch(extraction, d.payload),
            summary=lambda out: (
                f"reviewer corrected {', '.join(out['changed']) or 'nothing'}"
            ),
        )
        extraction = InvoiceExtraction.model_validate(patched["extraction"])
        match_result = match(
            ctx, f"c2_{round_number}", extraction, context, reads.match
        )
    else:
        state = "pending"
        reason = (
            f"still flagged after {MAX_REVIEW_ROUNDS} review round(s) — "
            "left for the AP lead"
        )

    return service._finish(
        ctx, submitted_by, submitted_at, filename, document_id, vendor_id,
        vendor_label, resolution_raw, extraction, match_result, None, policy,
        state, reason, decided_by, decision_note, commit_queue,
    )


def apply_vendor_decision(
    service,
    ctx: RunContext,
    extraction: InvoiceExtraction,
    resolution_raw: dict,
    decision,
    *,
    chosen: str,
    queue: str,
    task_id: str,
) -> tuple[str, str]:
    """a0 — assert the vendor the reviewer named. Nothing was asserted before.

    A guarded Action of its own rather than a field folded into the posting
    write, because it is a different assertion by a different authority: "this
    invoice is issued by that company" is the reviewer's identity judgment, and
    the ledger has to be able to show it as one. Committed under the Task's own
    desk — which is why `confirm-vendor` derives its queue from the Task: the
    review may have been routed to the submitter, and a pin on `ap-clerk` alone
    would refuse them their own answer.

    `chosen` is the id off the decision payload; empty falls through to the
    applier's existing rule (the top candidate on an approval, a newly minted
    vendor otherwise), which is the behaviour v6's gate had.
    """
    confirm = service.gateway.action(
        "confirm-vendor", ctx.principal or domain.ap_flow_principal(queue),
        run=ctx.record, step_id="a0",
        task_id=task_id,
        decided_by=decision.decided_by,
        queue=queue,
    )
    payload = dict(decision.payload)
    if chosen:
        payload["object_id"] = chosen
    applied = ctx.step(
        "a0", "apply-vendor-decision", "action",
        fn=lambda: confirm.commit(lambda w: service._apply_vendor_decision(
            extraction, resolution_raw,
            decision.model_copy(update={"payload": payload}),
            ctx.record.id, w,
        )),
        writes=lambda _out: confirm.writes,
        summary=lambda out: (
            f"{out['action']} {out['label']} by {out['decided_by']}"
        ),
    )
    return applied["vendor_id"], applied["label"]


# ---------------------------------------------------------------------------
# The body
# ---------------------------------------------------------------------------


def build_body(service, *, posting_check=None):
    base = posting_check or domain._posting_check

    def body(ctx: RunContext, inputs: dict) -> dict:
        submitted_by = inputs["submitted_by"]
        submitted_at = inputs.get("submitted_at_iso") or utcnow().isoformat()

        ctx.event(
            "t1", "InvoiceReceived",
            f"invoice received from {submitted_by}"
            + (f" ({inputs['filename']})" if inputs.get("filename") else ""),
            kind="trigger",
        )

        stored = v8.resolve_document(service, ctx, inputs)
        document_id = stored["document_id"]
        filename = stored["filename"]

        reads = read_knowledge(service, ctx, submitted_by)
        # One read of the governed rate sheet for both ModelCalls this run can
        # make. Two reads could name two revisions in the run where somebody
        # republishes the rates mid-flight, and a run whose two calls were
        # priced against different sheets is a bill nobody can reproduce.
        meter = v6.extraction_meter(service)

        extraction = extract(
            service, ctx, document_id, filename, reads, meter
        )
        verdicts = field_verdicts(
            service, ctx, extraction, reads.arithmetic, document_id, filename,
            meter,
        )
        located = {
            verdict.path: verdict.region
            for verdict in verdicts.fields if verdict.region is not None
        }

        def fields_for(current: InvoiceExtraction) -> list[dict]:
            """The doubts about *this* reading, for the Task about to be raised.

            Recomputed rather than carried, because a correction produces a new
            extraction and the reviewer on round two must not be shown round
            one's list — including the doubts their own correction just
            settled. The boxes *are* carried: they came from the one locate
            call this run makes, and a field still in doubt is still in the
            same place on the page.
            """
            return [
                verdict.model_dump(mode="json")
                for verdict in in_doubt(verdicts_for(
                    current, check_arithmetic(current, reads.arithmetic),
                    min_field_confidence=reads.arithmetic.min_field_confidence,
                    regions=located,
                ))
            ]

        vendor_id, vendor_label, resolution_raw = resolve_vendor(
            service, ctx, extraction, reads.resolution_ref
        )
        context, match_result = gather_and_match(
            service, ctx, extraction, vendor_id, reads
        )
        return decide_and_post(
            service, ctx, reads=reads, context=context,
            match_result=match_result, extraction=extraction,
            vendor_id=vendor_id, vendor_label=vendor_label,
            resolution_raw=resolution_raw, submitted_by=submitted_by,
            submitted_at=submitted_at, filename=filename,
            document_id=document_id,
            check=_rule(reads.arithmetic, base), fields_for=fields_for,
        )

    return body
