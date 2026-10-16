"""Accounts payable, v8 — the run starts from a document, and asks about fields.

The one change, and everything it implies. v6's body accepts `image_b64`, a
`filename` and a **hint**, and hands all three to the extractor; with no model
configured the hint *is* the extraction, which is how a demo could produce a
posted invoice nobody had written. v8 accepts a `document_id` — the artifact
`ap-intake` fetched out of the customer's document system and stored under its
content hash — reads the bytes back out of the store, and runs the ModelCall
over those. There is no hint parameter to pass.

The upload door still works, and that is not a compromise: a person holding an
invoice hands over bytes, the store gives them an id, and from the second step
onwards the two arrivals are indistinguishable. What is gone is the third
arrival, where nothing was handed over at all.

**The second change is what happens between reading the page and trusting it.**
Four steps sit between the extraction and the vendor, and they exist to turn
"this invoice needs a human" into "these three fields need a human":

- `x1` reads the governed arithmetic tolerances;
- `c0` checks the invoice against itself — a pure Rule, no fetching — and every
  failure names the **field path** it implicates;
- `x2` unions those with the fields the extractor said it was unsure of and the
  required fields that are simply absent;
- `m2` asks a second, cheap model where exactly those fields are on the page,
  and stores the answer as Regions.

`m2` is **skipped entirely when nothing is in doubt**: a clean invoice makes one
model call, and there is a test that says so. The rest of the run — vendor
resolution, the three-way match, the anomaly screen, the posting rule, the
review loop and the commit — is v6's, called rather than copied, so a fix to
the match reaches v6, v7 and v8 together.

Shipped as a candidate: v6 stays the default until the console makes this one
live, exactly as v7 did.
"""

from __future__ import annotations

from .... import modelcall, routing
from ....documents import DOCUMENT_COLLECTION, Region
from ....flows import FlowStep
from ....models import utcnow
from ....prompts import read_prompt
from ....runs import RunContext
from ....task_screens import TaskScreen, TaskUi
from .. import domain
from ..domain import InvoiceExtraction
from ..v6 import flow as v6
from . import locate
from .arithmetic import (
    ArithmeticTolerances,
    check_arithmetic,
    parse_arithmetic_tolerances,
    summarize,
)
from .verdicts import FieldFinding, VerdictSet, in_doubt, verdicts_for

VERSION = 8
LABEL = "v8"
NOTES = (
    "The run starts from a stored document rather than from a description of "
    "one: `ap-intake` fetches the vendor's file out of the customer's document "
    "system and this reads those bytes. No hint, no composed case, and no "
    "synthetic region geometry. What the extraction reports is then checked "
    "against itself — line arithmetic, the totals, the required fields, the "
    "dates — and the fields that fail, plus the ones the extractor said it was "
    "unsure of, are the only fields a clerk is asked about. A second cheap "
    "model call puts a box on the page around exactly those, and is skipped "
    "when there are none. Every step after that is v6's."
)

#: One screen, and the *fields* choose the emphasis.
#:
#: v6 needs two because a person opening it has no idea which of thirty inputs
#: the run was unsure of, so the flow has to say through the task's kind whether
#: the reading or the comparison is the question. This run answers that per
#: field, in the task itself — so a kind-keyed split here would be the older,
#: coarser signal arguing with the finer one.
UI = TaskUi(
    otherwise=TaskScreen(
        screen_id="accounts-payable/invoice-review",
        label="Open the invoice review",
        hint=(
            "Opens the invoice itself, with a box on each field the run could "
            "not settle and the check’s own sentence beside it. Everything "
            "it read cleanly is folded away; correcting a field re-runs the "
            "checks over it."
        ),
    ),
)


def _steps() -> list[FlowStep]:
    """v6's map with s1 restated and the field-verdict steps spliced in.

    A copy rather than a reference, because a `FlowStep` is the Tier-2 export
    of *this* version and two versions sharing one mutable list would be one
    version editing the other's map.
    """
    steps = [step.model_copy(deep=True) for step in v6.STEPS]
    for index, step in enumerate(steps):
        if step.id == "s1":
            steps[index] = step.model_copy(update={
                "name": "resolve-document",
                "summary": (
                    "the artifact this run reads: the document `ap-intake` "
                    "already stored, found by id, or the bytes an operator "
                    "uploaded, stored now — either way a content-hash id, and "
                    "a run that names neither is refused rather than invented"
                ),
                "config": {
                    "target": "documents", "gateway": "action-gateway",
                    "arrives": "ap-intake document_id | console upload",
                },
            })
        if step.id == "m1":
            steps[index] = step.model_copy(update={
                "summary": (
                    "one pass over the stored bytes — the vendor as printed, "
                    "the invoice number, dates, PO, totals and the billed "
                    "lines, with a confidence **per field**. Nothing steers it "
                    "but the governed prompt and the page"
                ),
                "config": {**step.config, "reads": "document bytes"},
                # The extraction now hands off to the checks rather than
                # straight to the resolver.
                "next": ["x1"],
            })
    spliced = [
        FlowStep(
            id="x1", type="transform", name="read-arithmetic-tolerances",
            summary=(
                "gathers the governed values the arithmetic check runs on: "
                "how far a line may be from quantity × price, how far a "
                "document may be from its own parts, and the confidence floor "
                "below which a field is worth a human's glance"
            ),
            config={"source": f"knowledge:{domain.POLICY_ASSET_ID}"},
            next=["c0"],
        ),
        FlowStep(
            id="c0", type="check", name="check-arithmetic",
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
            next=["x2"],
        ),
        FlowStep(
            id="x2", type="transform", name="field-verdicts",
            summary=(
                "the union a human is actually asked about: fields the "
                "extractor was unsure of, fields the arithmetic contradicts, "
                "and required fields that are not there — three per invoice "
                "rather than thirty"
            ),
            config={"language": "python"},
            next=["m2", "z1"],
            branch_labels=["fields in doubt", "nothing in doubt"],
        ),
        FlowStep(
            id="m2", type="modelcall", name="locate-fields",
            summary=(
                "a second, narrow look: given the document and *only* the "
                "disputed field paths, where each one is printed. The boxes "
                "are stored as Regions so the review screen can draw them. "
                "Skipped entirely when nothing is in doubt — no boxes, no "
                "call, no cost"
            ),
            config={"model": "gemini-3.7-flash",
                    "prompt": f"knowledge:{locate.LOCATE_PROMPT_ASSET_ID}",
                    "skipped_when": "no field verdicts"},
            next=["z1"],
        ),
    ]
    at = next(i for i, step in enumerate(steps) if step.id == "m1") + 1
    return steps[:at] + spliced + steps[at:]


STEPS: list[FlowStep] = _steps()


# ---------------------------------------------------------------------------
# The phases this version owns
# ---------------------------------------------------------------------------


def resolve_document(service, ctx: RunContext, inputs: dict) -> dict:
    """s1 — the artifact, by the id intake stored or the bytes an operator sent."""
    return ctx.step(
        "s1", "resolve-document", "action",
        fn=lambda: service._resolve_document(
            inputs.get("document_id", ""),
            inputs.get("image_b64", ""),
            inputs.get("filename", ""),
            inputs["submitted_by"],
            ctx.record.id,
        ),
        writes=lambda out: [f"document:{out['document_id']}"],
        summary=lambda out: (
            f"artifact {out['document_id']} ({out['byte_size']:,} bytes) "
            f"in `{DOCUMENT_COLLECTION}`, arrived by {out['arrived']}"
        ),
        # An operations fault, not a clerk's: a run started against an artifact
        # the store does not hold is a wiring problem, and Connie has no
        # verdict to give on one.
        escalate_queue=routing.OPS_QUEUE,
    )


def extract(service, ctx: RunContext, document_id: str, filename: str, meter):
    """m1 — one metered ModelCall over the bytes in the store.

    The `hint` argument the reader still takes is passed empty, deliberately
    and permanently: a caller-supplied string that steers an extraction is the
    thing this version exists to remove. Under an explicitly configured stub
    reader the filename still steers it, which the console says out loud —
    that is what a stub *is*, and it is a choice somebody made in config
    rather than a request field anybody can post.
    """
    prompt = v6.read_governed_prompt(service, ctx)
    rates_ref, priced = meter
    return InvoiceExtraction.model_validate(ctx.step(
        "m1", "extract-invoice", "modelcall",
        fn=lambda: service._extract(
            service._document_bytes(document_id), filename, "", document_id,
            prompt.text, store_regions=False,
        ).model_dump(),
        summary=v6.extraction_summary,
        meter=priced,
        refs=[f"document:{document_id}", prompt.source_ref, rates_ref],
        retries=1, escalate_queue=routing.OPS_QUEUE,
    ))


def read_tolerances(service, ctx: RunContext) -> ArithmeticTolerances:
    """x1 — the governed values the arithmetic check runs on.

    Read here rather than inside the check for the reason `x5` is read outside
    the posting rule: a Rule that fetched its own thresholds could not be
    replayed, could not be graded against a fixture, and would hide the read
    from the step's read-set.
    """
    content, ref = service._policy_asset_content()
    tolerances = ArithmeticTolerances.model_validate(ctx.step(
        "x1", "read-arithmetic-tolerances", "transform",
        fn=lambda: parse_arithmetic_tolerances(content, ref).model_dump(),
        summary=lambda out: (
            f"{out['source_ref']}: lines within ${out['line_amount_usd']:,.2f}, "
            f"documents within ${out['document_usd']:,.2f}, field confidence "
            f"floor {out['min_field_confidence']:.2f}"
            + ("" if out["found_asset"]
               else " — no policy in the corpus, running on the defaults")
        ),
        refs=[ref],
    ))
    ctx.note_refs(tolerances.source_ref)
    return tolerances


def field_verdicts(
    service,
    ctx: RunContext,
    extraction: InvoiceExtraction,
    tolerances: ArithmeticTolerances,
    document_id: str,
    filename: str,
    meter,
) -> VerdictSet:
    """c0/x2/m2 — what the invoice disagrees with, and where it is on the page."""
    findings = ctx.step(
        "c0", "check-arithmetic", "check",
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

    verdicts = ctx.step(
        "x2", "field-verdicts", "transform",
        fn=union,
        summary=lambda out: VerdictSet.model_validate(out).summary(),
    )
    verdict_set = VerdictSet.model_validate(verdicts)
    if verdict_set.clean:
        # The whole point of the step above: no doubts, so no second call and
        # no boxes. The journal shows `m2` missing rather than present-and-
        # empty, which is the honest record of a call that was never made.
        return verdict_set

    located = locate_fields(
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


def locate_fields(
    service,
    ctx: RunContext,
    document_id: str,
    filename: str,
    paths: list[str],
    meter,
) -> list[Region]:
    """m2 — the second ModelCall, over the disputed paths and nothing else."""
    prompt = read_prompt(
        service.knowledge, locate.LOCATE_PROMPT_ASSET_ID,
        locate.DEFAULT_LOCATE_PROMPT,
    )
    ctx.note_refs(prompt.source_ref)
    rates_ref, priced = meter
    document = service.documents.get(document_id) if service.documents else None
    pages = document.pages if document is not None else 0

    def call() -> dict:
        if service.locator is None:
            # A deployment with no locator is a deployment that shows no boxes,
            # and it says so here rather than leaving an empty region list to
            # be read as "the model looked and found nothing".
            return locate.LocatedFields(
                refused=["no field locator is wired on this deployment"]
            ).model_dump()
        with modelcall.reading(prompt.source_ref):
            found = service.locator.locate(
                service._document_bytes(document_id), filename, paths,
                prompt=prompt.text,
                media_type=document.media_type if document is not None else "",
            )
        kept, refused = locate.accept(found.regions, paths, pages)
        # Stored inside the step, so a resumed run replays the write rather
        # than repeating it — a suspended invoice is resumed every time
        # somebody answers a gate, and re-writing the document each time would
        # be a store write per click for a value that cannot have changed.
        if kept and service.documents is not None:
            service.documents.set_regions(document_id, kept)
        return found.model_copy(update={
            "regions": kept, "refused": found.refused + refused
        }).model_dump()

    out = ctx.step(
        "m2", "locate-fields", "modelcall",
        fn=call,
        summary=lambda out: (
            f"{out['model'] or 'no locator'}: {len(out['regions'])} of "
            f"{len(paths)} disputed field(s) located"
            + (
                f" · {len(out['refused'])} box(es) refused: "
                + "; ".join(out["refused"][:3])
                if out["refused"] else ""
            )
        ),
        meter=priced,
        refs=[f"document:{document_id}", prompt.source_ref, rates_ref],
        writes=lambda out: (
            [f"document:{document_id}"] if out["regions"] else []
        ),
        # No retry and no escalation: boxes are an aid to a reviewer, and an
        # invoice that cannot be located is an invoice reviewed without
        # highlights. Failing the run over a missing rectangle would trade a
        # payment for a convenience.
        retries=0,
    )
    return [Region.model_validate(item) for item in out["regions"]]


def _finding(item: dict) -> FieldFinding:
    return FieldFinding.model_validate(item)


def _rule(tolerances: ArithmeticTolerances, base):
    """The posting rule this version evaluates: v6's, with the arithmetic.

    Recomputed from the extraction it is handed rather than closed over the
    verdicts `x2` produced, and that is not a duplicate check — it is the only
    correct one. A clerk who corrects a line is handed back to this rule with a
    *different* extraction, and a closure holding round one's findings would
    hold an invoice for a disagreement the correction had just settled.

    Still pure: `check_arithmetic` reads its two arguments and nothing else.
    """

    def check(extraction, match, screen, policy) -> dict:
        verdict = base(extraction, match, screen, policy, check_line_sum=False)
        return {
            **verdict,
            "reasons": [
                f"{finding.path}: {finding.reason}"
                for finding in check_arithmetic(extraction, tolerances)
            ] + verdict["reasons"],
        }

    return check


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

        stored = resolve_document(service, ctx, inputs)
        document_id = stored["document_id"]
        filename = stored["filename"]

        # One read of the governed rate sheet for both ModelCalls this run can
        # make. Two reads could name two revisions in the run where somebody
        # republishes the rates mid-flight, and a run whose two calls were
        # priced against different sheets is a bill nobody can reproduce.
        meter = v6.extraction_meter(service)

        extraction = extract(service, ctx, document_id, filename, meter)
        tolerances = read_tolerances(service, ctx)
        verdicts = field_verdicts(
            service, ctx, extraction, tolerances, document_id, filename, meter
        )
        located = {
            verdict.path: verdict.region
            for verdict in verdicts.fields if verdict.region is not None
        }

        def fields_for(current: InvoiceExtraction) -> list[dict]:
            """The doubts about *this* reading, for the Task about to be raised.

            Recomputed rather than carried, because a correction produces a new
            extraction and the clerk on round two must not be shown round one's
            list — including the doubts their own correction just settled. The
            boxes *are* carried: they came from the one locate call this run
            makes, and a field still in doubt is still in the same place on the
            page.

            Only the fields in doubt travel. The screen renders the rest of the
            extraction as collapsed context out of the invoice itself, and a
            Task carrying thirty `ok` verdicts would be paying storage for the
            twenty-seven nobody is being asked about.
            """
            return [
                verdict.model_dump(mode="json")
                for verdict in in_doubt(verdicts_for(
                    current, check_arithmetic(current, tolerances),
                    min_field_confidence=tolerances.min_field_confidence,
                    regions=located,
                ))
            ]

        vendor_id, vendor_label, resolution_raw = v6.resolve_vendor(
            service, ctx, extraction, submitted_by
        )
        gathered = v6.gather_and_check(service, ctx, extraction, vendor_id)
        return v6.decide_and_post(
            service, ctx, gathered=gathered, extraction=extraction,
            vendor_id=vendor_id, vendor_label=vendor_label,
            resolution_raw=resolution_raw, submitted_by=submitted_by,
            submitted_at=submitted_at, filename=filename,
            document_id=document_id, check=_rule(tolerances, base),
            fields_for=fields_for,
        )

    return body
