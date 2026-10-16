"""Shipping list extraction, v1 — `accounts_payable/v9`'s shape on a ticket.

Thirteen steps and every one of them is in `STEPS`: one governed read gathering
the whole read-set at its revisions, one metered pass over the pages, two pure
checks that name the field paths they implicate, a union a person is actually
asked about, a second cheap call that is **skipped entirely** when nothing is in
doubt, one human gate routed by the governed approval table, a correction that
re-runs both checks rather than overriding them, and one guarded write.

Three things this flow does not do, each because a governed page says so:

- **A job with no shipping list is skipped with a count**, not a Task. There is
  nothing for a supervisor to decide about a document that has not been filed
  yet; the office files it and the next window finds it.
- **A department whose page family is absent is skipped — that department only,
  never the job.** A building with no trusses is a building with no trusses.
- **Out-of-band hours are a field verdict, not a second gate.** They arrive in
  the same review as the low-confidence fields, beside the grid they came from,
  so a supervisor confirms the reading and the hours in one sitting.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .... import modelcall, routing
from ....config import settings
from ....flows import FlowStep, read_model_rates
from ....prompts import GovernedPrompt, read_prompt
from ....runs import ModelUsage, RunContext
from ....task_screens import TaskScreen, TaskUi
from ...accounts_payable.v8 import locate
from ...department_scheduling import formulas
from ...department_scheduling.parameters import PlantParameters
from ...plant_eligibility.domain import JOB_ELIGIBLE_EVENT
from .. import domain
from ..checks import (
    DepartmentDemand,
    check_grids,
    check_labor_hours,
    compute_demand,
    summarize_demand,
)
from ..domain import (
    DEMAND_EVENT,
    MAX_REVIEW_ROUNDS,
    JobFacts,
    ShippingListExtraction,
)
from ..verdicts import FieldFinding, VerdictSet, in_doubt, verdicts_for
from .. import paths as vocab

VERSION = 1
LABEL = "v1"
NOTES = (
    "One eligible job's shipping list, read once and checked twice. It "
    "gathers every governed value in one step — the extraction prompt, the "
    "page-family map, the confidence floors, the two arithmetic tolerances, "
    "the expected-hours bands, the labor-hour parameters, the truss standards "
    "and the approval table — then finds the job's ticket under the governed "
    "cut-sheet root, fetches the bytes under their content hash, and makes "
    "**one** metered pass over the pages with a confidence per field. The "
    "grids are checked against their own printed totals and against the "
    "page-family map; the governed formulas' hours are checked against each "
    "department's expected band. The union of what is left in doubt is what a "
    "supervisor is asked about — with a box on each field, from a second cheap "
    "call that is skipped entirely when there is nothing to locate. A "
    "correction re-runs both checks over the corrected reading. Then one "
    "guarded write: a `JobTask` per department whose page family is present, "
    "each carrying the formula and the asset revision that produced its "
    "hours. A job with no shipping list is a count, not a Task; a department "
    "with no page family is skipped, and the job is not."
)

#: One screen, and the *fields* choose the emphasis — v8's argument, unchanged.
#: A supervisor opening this is doing one thing: comparing three numbers in a
#: panel against the same three numbers on a page, with the hours those numbers
#: produced beside them.
UI = TaskUi(
    otherwise=TaskScreen(
        screen_id="plant-schedule/extraction-review",
        label="Open the shipping list review",
        hint=(
            "Opens the ticket itself, with a box on each field the run could "
            "not settle, the check’s own sentence beside it, and the hours the "
            "governed formula computed from each grid. Everything it read "
            "cleanly is folded away; correcting a value re-runs the checks and "
            "the formulas over it."
        ),
    ),
)


STEPS: list[FlowStep] = [
    FlowStep(
        id="t1", type="trigger", name=JOB_ELIGIBLE_EVENT,
        summary=(
            "one run per eligible job — the fact the plant window raised, "
            "which is where a windowed batch becomes per-case work"
        ),
        config={"event": JOB_ELIGIBLE_EVENT}, next=["x0"],
    ),
    FlowStep(
        id="x0", type="transform", name="read-knowledge",
        summary=(
            "every governed value this run consumes, read once and recorded at "
            "its revision: the extraction prompt, the page→department map, the "
            "document and field confidence floors, the two arithmetic "
            "tolerances, the per-department expected hour bands, the "
            "cut-sheet folder root and filename patterns, the labor-hour "
            "parameters, the truss standards lookup, and the approval table "
            "this case's review is routed by"
        ),
        config={"source": f"knowledge:{domain.PROMPT_ASSET_ID},"
                          "plant-scheduling-parameters,labor-hour-formulas,"
                          "truss-standards"},
        next=["x1"],
    ),
    FlowStep(
        id="x1", type="transform", name="select-source-documents",
        summary=(
            "the job row out of the graph, and its shipping list and column "
            "cut sheets out of the governed folder root — which is what tells "
            "the floor's copy from the office's, since the same filename "
            "pattern matches both. **None found** is the design's first "
            "business exception: the job is skipped with a count, not a Task"
        ),
        config={"source": "graph:Job,Folder,Document"}, next=["s1"],
    ),
    FlowStep(
        id="s1", type="sync", name="fetch-pages",
        summary=(
            "fetches each file's bytes through the customer-resolvable "
            "`storage_ref` and stores them under their content hash, then one "
            "guarded write stamping the referenced row with what was read and "
            "which revision it came from. A file that will not fetch is "
            "counted, never a gate"
        ),
        config={"target": domain.DOCUMENT_LIBRARY, "gateway": "action-gateway",
                "custody": "copy"},
        next=["m1"],
    ),
    FlowStep(
        id="m1", type="modelcall", name="extract-shipping-list",
        summary=(
            "**one** metered pass over the pages: the grids per page family, "
            "each family's own present flag, and a confidence **per field**. "
            "Nothing steers it but the governed prompt and the document — and "
            "that prompt forbids it to compute an hour, because the arithmetic "
            "is governed, versioned and checked and a number the model "
            "produced instead would be a hidden second implementation of a "
            "rule this plant can otherwise edit"
        ),
        config={"model": settings.model,
                "prompt": f"knowledge:{domain.PROMPT_ASSET_ID}",
                "purpose": domain.EXTRACT_PURPOSE,
                "reads": "document bytes"},
        next=["c1"],
    ),
    FlowStep(
        id="c1", type="check", name="check-grids",
        summary=(
            "the ticket against itself — the fields a governed formula "
            "multiplies against their absence, each grid's lines against its "
            "own printed total, the colour grid against the printed linear "
            "footage, the page flags against the blocks under them, and the "
            "document's own score against the governed floor. A pure Rule: it "
            "fetches nothing, and every failure names the field path it "
            "implicates, because those paths become the boxes"
        ),
        config={"rule_type": "arithmetic",
                "source": "knowledge:plant-scheduling-parameters"},
        next=["c2"],
    ),
    FlowStep(
        id="c2", type="check", name="check-labor-hours",
        summary=(
            "the governed formula's hours per department against that "
            "department's governed expected band. Out of band is the design's "
            "fourth business exception — *supervisor review before writing* — "
            "and it becomes a **field verdict** rather than a second gate. A "
            "governed value the formulas page does not carry is its own "
            "finding, because a plausible figure computed around a missing "
            "rate is a schedule nobody can dispute"
        ),
        config={"rule_type": "threshold",
                "source": "knowledge:labor-hour-formulas,truss-standards"},
        next=["x2"],
    ),
    FlowStep(
        id="x2", type="transform", name="field-verdicts",
        summary=(
            "the union a person is actually asked about: fields the extraction "
            "was unsure of, fields a grid contradicts, required fields that "
            "are not there, and computed figures outside their band — three "
            "per ticket rather than thirty"
        ),
        config={"language": "python"},
        next=["m2", "a1"],
        branch_labels=["fields in doubt", "nothing in doubt"],
    ),
    FlowStep(
        id="m2", type="modelcall", name="locate-fields",
        summary=(
            "a second, narrow look: given the pages and *only* the disputed "
            "field paths, where each one is printed. The boxes are stored as "
            "Regions so the review screen can draw them. Skipped entirely "
            "when nothing is in doubt — no boxes, no call, no cost — and a "
            "computed figure is never sent, because there is no ink for one"
        ),
        config={"model": settings.locate_model,
                "prompt": f"knowledge:{domain.LOCATE_PROMPT_ASSET_ID}",
                "purpose": domain.LOCATE_PURPOSE,
                "skipped_when": "no field verdicts"},
        next=["k1"],
    ),
    FlowStep(
        id="k1", type="task", name="review-extraction",
        summary=(
            "the one gate: the fields in doubt with a box on each, the hours "
            "the governed formulas computed beside each grid, and the page "
            "itself. Routed by the governed approval table — one band and a "
            "standing desk today — and capped, so a correction that does not "
            "settle the checks stops asking rather than looping"
        ),
        config={"source": "knowledge:plant-scheduling-parameters",
                "fallback_queue": domain.SUPERVISOR_QUEUE,
                "max_rounds": str(MAX_REVIEW_ROUNDS)},
        next=["a1", "x3"],
        branch_labels=["approved or rejected", "corrected"],
    ),
    FlowStep(
        id="x3", type="transform", name="apply-correction",
        summary=(
            "the supervisor's corrected values applied to the reading, keyed "
            "by the same fact paths the verdicts were raised under — then both "
            "checks and the governed formulas run again over the corrected "
            "ticket"
        ),
        config={"language": "python"}, next=["c1"],
    ),
    FlowStep(
        id="a1", type="action", name="apply-job-demand",
        summary=(
            "guarded: one `JobTask` per department whose page family is "
            "present, with the hours, the formula and asset revision that "
            "produced them, the demand sentence, the page it was read off, the "
            "per-share confidence and the out-of-band verdict. **`JobTask` "
            "rows are created here and nowhere else.** Committed under the "
            "desk of the Task where a person answered, or the standing plant "
            "desk when nothing was in doubt"
        ),
        config={"target": "production-records", "gateway": "action-gateway"},
        next=["e1"],
    ),
    FlowStep(
        id="e1", type="event", name=DEMAND_EVENT,
        summary=(
            "the run's own fact, with the departments written and the "
            "departments skipped. Nothing binds it: the scheduling window "
            "reads `JobTask` rows out of the graph instead, because a work "
            "list scoped to one delivery skews and a standing question does not"
        ),
        config={"event": DEMAND_EVENT},
    ),
]


# ---------------------------------------------------------------------------
# x0 — one governed read
# ---------------------------------------------------------------------------


class GovernedReads(BaseModel):
    """Everything `x0` read, in one step output.

    One step rather than four, and one journal row rather than a read nobody
    declared — `accounts_payable/v9`'s reason: a run that read the same asset
    from three places could name two revisions of it if somebody republished
    mid-flight, and a reader of the map could not tell what the run consults.

    `plant` is projected down to the sections this flow actually reads, because
    the step's output *is* the read-set and a step that dumped the capacity
    table it never consulted would claim a read that did not happen.
    """

    plant: PlantParameters = Field(default_factory=PlantParameters)
    labor: formulas.Parameters = Field(default_factory=formulas.Parameters)
    prompt: GovernedPrompt = Field(default_factory=GovernedPrompt)


def read_knowledge(service, ctx: RunContext) -> GovernedReads:
    """x0 — the whole read-set, before anything is read *from* the page."""
    plant = service.read_plant()
    labor = service.read_labor()
    prompt = read_prompt(
        service.knowledge, domain.PROMPT_ASSET_ID,
        _default_prompt(),
    )

    def gather() -> dict:
        return GovernedReads(
            plant=_plant_reads(plant), labor=labor, prompt=prompt,
        ).model_dump(mode="json")

    reads = GovernedReads.model_validate(ctx.step(
        "x0", "read-knowledge", "transform",
        fn=gather,
        summary=lambda out: _reads_summary(GovernedReads.model_validate(out)),
        refs=[
            plant.source_ref, labor.source_ref, labor.truss.source_ref,
            prompt.source_ref,
        ],
    ))
    ctx.note_refs(
        reads.plant.source_ref, reads.labor.source_ref,
        reads.labor.truss.source_ref, reads.prompt.source_ref,
    )
    return reads


def _default_prompt() -> str:
    from ..readers import DEFAULT_EXTRACT_PROMPT

    return DEFAULT_EXTRACT_PROMPT


def _plant_reads(plant: PlantParameters) -> PlantParameters:
    """The sections this flow consumes, and only those."""
    return PlantParameters(
        departments=plant.departments,
        page_families=plant.page_families,
        documents=plant.documents,
        thresholds=plant.thresholds,
        default_band=plant.default_band,
        bands=plant.bands,
        approval=plant.approval,
        source_ref=plant.source_ref,
        found_asset=plant.found_asset,
    )


def _reads_summary(reads: GovernedReads) -> str:
    thresholds = reads.plant.thresholds
    return (
        f"{reads.plant.source_ref}: document confidence ≥ "
        f"{thresholds.min_extraction_confidence:.2f} · field confidence ≥ "
        f"{thresholds.min_field_confidence:.2f} · grid totals within "
        f"{thresholds.grid_line_total_tolerance_pct:g}% · linear footage "
        f"within {thresholds.linear_footage_tolerance_pct:g}% · "
        f"{len(reads.plant.page_families)} page famil"
        f"{'y' if len(reads.plant.page_families) == 1 else 'ies'} feeding "
        f"{len(reads.plant.slugs)} department(s) · "
        f"{reads.labor.source_ref} · {reads.labor.truss.source_ref} "
        f"({len(reads.labor.truss.rows)} span/member row(s)) · reviews to "
        f"`{reads.plant.approval.fallback}`"
        + ("" if reads.prompt.found_asset
           else " — no extraction prompt in the corpus, running on the "
                "built-in default")
        + ("" if reads.plant.found_asset
           else " — no scheduling parameters in the corpus, so every floor "
                "and band is unset and every figure will be reported as such")
    )


# ---------------------------------------------------------------------------
# The body
# ---------------------------------------------------------------------------


def build_body(service):
    """Bind the extraction service and return the engine-runnable body."""

    def body(ctx: RunContext, inputs: dict) -> dict:
        job_key = str(inputs.get("job_key") or "")
        ctx.event(
            "t1", JOB_ELIGIBLE_EVENT,
            f"job {inputs.get('job_number') or job_key} cleared every "
            "eligibility clause"
            + (f", loading {inputs['load_date']}" if inputs.get("load_date") else ""),
            kind="trigger",
        )

        reads = read_knowledge(service, ctx)

        # -- x1: the job, and the files under the governed root ------------
        #
        # The job row is read once, outside the step, and used for both the
        # output and the read-set — `x0`'s reason, on the graph rather than on
        # the corpus.
        found = service.job_facts(job_key)
        selection = ctx.step(
            "x1", "select-source-documents", "transform",
            fn=lambda: _select(service, found, reads.plant),
            summary=lambda out: _selection_summary(out, reads.plant),
            refs=[found.job_ref] if found is not None else [],
        )
        if not selection["job"]:
            return _skipped(
                ctx, job_key, selection,
                "the graph holds no job under this key, so there is nothing "
                "to read a ticket for",
            )
        job = JobFacts.model_validate(selection["job"])
        if not selection["shipping_list"]:
            # The design's first business exception, and the governed page's
            # own answer: *skip the job and count it*. Not a Task — there is
            # nothing for a supervisor to decide about a document that has not
            # been filed.
            return _skipped(
                ctx, job_key, selection,
                f"no shipping list is filed under "
                f"`{reads.plant.documents.folder_for(job.plant_site, job.job_number)}` "
                f"— the governed page says: "
                f"{reads.plant.documents.missing_shipping_list or 'skip the job and count it'}",
            )

        # -- s1: the bytes, under their content hash -----------------------
        act = service.gateway.action(
            "record-document-read", domain.plant_flow_principal(),
            run=ctx.record, step_id="s1",
        )
        fetched = ctx.step(
            "s1", "fetch-pages", "sync",
            fn=lambda: act.commit(lambda writer: service.fetch_pages(
                selection["documents"], ctx.record.id, writer,
            )),
            writes=lambda _out: act.writes,
            summary=_fetch_summary,
        )
        page = next(
            (row for row in fetched["stored"] if row["kind"] == "shipping-list"),
            None,
        )
        if page is None:
            return _skipped(
                ctx, job_key, selection,
                "the shipping list is filed but its bytes did not answer, so "
                "there was no page to read; the row still carries its pointer "
                "and the next window will try again",
            )

        # -- m1: the one metered pass --------------------------------------
        meter = _meter(service)
        reading = _extract(service, ctx, page, reads, meter)
        # The wings are joined on *after* the step, so the m1 row in the
        # journal stays exactly what the model said. Both dimensions come off
        # the job record rather than off the shipping list — the governed
        # hardware formula reads `(wings width + wings length) × rate` — and a
        # model asked for them would be inventing a reading of a page they are
        # not printed on.
        reading = reading.model_copy(
            update={"wings_total_ft": job.wings_total_ft}
        )

        # -- c1 / c2 / x2 / m2 — and then the one gate ---------------------
        return _review_and_write(
            service, ctx, job=job, reading=reading, reads=reads,
            selection=selection, page=page, meter=meter,
        )

    return body


# ---------------------------------------------------------------------------
# x1 / s1
# ---------------------------------------------------------------------------


def _select(service, job: JobFacts | None, plant: PlantParameters) -> dict:
    if job is None:
        return {"job": None, "documents": [], "shipping_list": 0, "cut_sheets": 0}
    documents = service.source_documents(job, plant)
    return {
        "job": job.model_dump(mode="json"),
        "documents": [item.model_dump(mode="json") for item in documents],
        "shipping_list": len([
            item for item in documents if item.kind == "shipping-list"
        ]),
        "cut_sheets": len([item for item in documents if item.kind == "cut-sheet"]),
        "root": plant.documents.folder_for(job.plant_site, job.job_number),
    }


def _selection_summary(out: dict, plant: PlantParameters) -> str:
    if not out["job"]:
        return "no job in the graph under this key"
    job = JobFacts.model_validate(out["job"])
    if not out["shipping_list"]:
        return (
            f"nothing matching `{plant.documents.shipping_list_pattern}` under "
            f"`{out.get('root', '')}` — the job is skipped and counted"
        )
    return (
        f"{out['shipping_list']} shipping list(s) and {out['cut_sheets']} cut "
        f"sheet(s) under `{out.get('root', '')}` for {job.label}"
    )


def _fetch_summary(out: dict) -> str:
    parts = [f"{out['read']} file(s) read into the map"]
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


def _skipped(ctx: RunContext, job_key: str, selection: dict, reason: str) -> dict:
    """A run that ends without a demand row, and says why in one place."""
    return {
        "kind": "shipping-list-extraction",
        "job_key": job_key,
        "skipped": True,
        "reason": reason,
        "documents": len(selection.get("documents") or []),
        "departments_written": [],
        "departments_skipped": [],
    }


# ---------------------------------------------------------------------------
# m1 / m2
# ---------------------------------------------------------------------------


def _meter(service):
    """One read of the governed rate sheet for both ModelCalls this run makes.

    Two reads could name two revisions in the run where somebody republishes
    the rates mid-flight, and a run whose two calls were priced against
    different sheets is a bill nobody can reproduce.
    """
    rates = read_model_rates(service.knowledge)

    def meter(out) -> ModelUsage:
        return rates.priced(ModelUsage(
            model=str(out.get("model") or ""),
            input_tokens=int(out.get("input_tokens") or 0),
            output_tokens=int(out.get("output_tokens") or 0),
        ))

    return rates.source_ref, meter


def _extract(
    service, ctx: RunContext, page: dict, reads: GovernedReads, meter
) -> ShippingListExtraction:
    """m1 — one metered ModelCall over the bytes in the store."""
    rates_ref, priced = meter
    document_id = page["document_id"]

    def call() -> dict:
        if service.reader is None:
            # A deployment with no extractor reads no page, and it says so here
            # rather than leaving an empty grid to be read as "the model looked
            # and the ticket was blank".
            return ShippingListExtraction(
                model="", confidence=0.0,
            ).model_dump(mode="json")
        with modelcall.reading(reads.prompt.source_ref):
            found = service.reader.read(
                service.document_bytes(document_id), page["filename"],
                prompt=reads.prompt.text,
                media_type=_media_type(service, document_id),
            )
        return found.model_dump(mode="json")

    return ShippingListExtraction.model_validate(ctx.step(
        "m1", "extract-shipping-list", "modelcall",
        fn=call,
        summary=lambda out: ShippingListExtraction.model_validate(out).summary(),
        meter=priced,
        refs=[f"document:{document_id}", reads.prompt.source_ref, rates_ref],
        # The operations desk, not the supervisor's: an extraction that threw
        # is an engineering fault, and nobody on a plant floor has a verdict to
        # give on one.
        retries=1, escalate_queue=routing.OPS_QUEUE,
    ))


def _media_type(service, document_id: str) -> str:
    record = service.documents.get(document_id) if service.documents else None
    return record.media_type if record is not None else ""


def _locate(
    service,
    ctx: RunContext,
    page: dict,
    wanted: list[str],
    meter,
) -> list:
    """m2 — the second ModelCall, over the disputed paths and nothing else.

    v8's `accept` guard, imported rather than copied: a box for a path nobody
    asked about, one off the page, one on a page the document does not have, or
    a second box for one number is refused and **counted** rather than drawn.
    """
    prompt = read_prompt(
        service.knowledge, domain.LOCATE_PROMPT_ASSET_ID,
        locate.DEFAULT_LOCATE_PROMPT,
    )
    ctx.note_refs(prompt.source_ref)
    rates_ref, priced = meter
    document_id = page["document_id"]
    pages = int(page.get("pages") or 0)

    def call() -> dict:
        if service.locator is None:
            return locate.LocatedFields(
                refused=["no field locator is wired on this deployment"]
            ).model_dump()
        with modelcall.reading(prompt.source_ref):
            found = service.locator.locate(
                service.document_bytes(document_id), page["filename"], wanted,
                prompt=prompt.text,
                media_type=_media_type(service, document_id),
            )
        kept, refused = locate.accept(found.regions, wanted, pages)
        # Stored inside the step, so a resumed run replays the write rather
        # than repeating it: a suspended ticket is resumed every time somebody
        # answers the gate.
        service.store_regions(document_id, kept)
        return found.model_copy(update={
            "regions": kept, "refused": found.refused + refused,
        }).model_dump()

    out = ctx.step(
        "m2", "locate-fields", "modelcall",
        fn=call,
        summary=lambda out: (
            f"{out['model'] or 'no locator'}: {len(out['regions'])} of "
            f"{len(wanted)} disputed field(s) located"
            + (
                f" · {len(out['refused'])} box(es) refused: "
                + "; ".join(out["refused"][:3])
                if out["refused"] else ""
            )
        ),
        meter=priced,
        refs=[f"document:{document_id}", prompt.source_ref, rates_ref],
        writes=lambda out: [f"document:{document_id}"] if out["regions"] else [],
    )
    return locate.LocatedFields.model_validate(out).regions


# ---------------------------------------------------------------------------
# c1 / c2 / x2 — the checks, and what a person is asked
# ---------------------------------------------------------------------------


def _check_round(
    ctx: RunContext,
    reading: ShippingListExtraction,
    reads: GovernedReads,
    suffix: str,
    boxes: dict,
) -> tuple[list[DepartmentDemand], VerdictSet]:
    """c1, c2 and x2 over one reading. Every one of them pure.

    Re-run in full after a correction rather than patched, and that is not a
    duplicate check — it is the only correct one. A supervisor who fixes a
    linear-footage figure is handed back a *different* ticket, and carrying
    round one's findings forward would hold a department's day for a
    disagreement the correction had just settled.
    """
    grid = reading.grid()
    findings = ctx.step(
        f"c1{suffix}", "check-grids", "check",
        fn=lambda: [
            finding.model_dump() for finding in check_grids(grid, reads.plant)
        ],
        summary=lambda out: _findings_summary(out, "the ticket agrees with itself"),
        refs=[reads.plant.source_ref],
    )

    def hours_check() -> dict:
        rows = compute_demand(grid, reads.plant, reads.labor)
        return {
            "demand": [row.model_dump(mode="json") for row in rows],
            "findings": [
                finding.model_dump()
                for finding in check_labor_hours(rows, reads.plant)
            ],
        }

    hours = ctx.step(
        f"c2{suffix}", "check-labor-hours", "check",
        fn=hours_check,
        summary=lambda out: summarize_demand([
            DepartmentDemand.model_validate(row) for row in out["demand"]
        ]),
        refs=[reads.labor.source_ref, reads.labor.truss.source_ref],
    )
    demand = [DepartmentDemand.model_validate(row) for row in hours["demand"]]
    all_findings = [
        FieldFinding.model_validate(row)
        for row in list(findings) + list(hours["findings"])
    ]

    def union() -> dict:
        fields = verdicts_for(
            grid, all_findings,
            min_field_confidence=reads.plant.thresholds.min_field_confidence,
            regions=boxes,
        )
        return VerdictSet(
            fields=fields,
            in_doubt=[verdict.path for verdict in in_doubt(fields)],
        ).model_dump(mode="json")

    verdicts = VerdictSet.model_validate(ctx.step(
        f"x2{suffix}", "field-verdicts", "transform",
        fn=union,
        summary=lambda out: VerdictSet.model_validate(out).summary(),
    ))
    return demand, verdicts


def _findings_summary(rows: list, clean: str) -> str:
    if not rows:
        return clean
    return f"{len(rows)} disagreement(s): " + "; ".join(
        f"{row['path']} — {row['reason']}" for row in rows[:3]
    ) + (" …" if len(rows) > 3 else "")


# ---------------------------------------------------------------------------
# k1 / x3 / a1 / e1 — the gate, the correction, and the one write
# ---------------------------------------------------------------------------


def _review_and_write(
    service,
    ctx: RunContext,
    *,
    job: JobFacts,
    reading: ShippingListExtraction,
    reads: GovernedReads,
    selection: dict,
    page: dict,
    meter,
) -> dict:
    """The checks, the review loop, and the commit."""
    demand, verdicts = _check_round(ctx, reading, reads, "", {})
    boxes: dict = {}
    if not verdicts.clean and verdicts.locatable:
        # The whole point of `x2`: no doubts, so no second call and no boxes.
        # The journal then shows `m2` **missing** rather than present-and-empty,
        # which is the honest record of a call that was never made.
        boxes = {
            region.field: region
            for region in _locate(service, ctx, page, verdicts.locatable, meter)
        }
        verdicts = verdicts.model_copy(update={
            "fields": [
                verdict.model_copy(update={"region": boxes.get(verdict.path)})
                if verdict.status != "ok" else verdict
                for verdict in verdicts.fields
            ],
        })

    state = "extracted"
    reason = "read cleanly and written without a review"
    decided_by = ""
    decision_note = ""
    commit_queue = ""
    rounds = 0

    for round_number in range(1, MAX_REVIEW_ROUNDS + 1):
        if verdicts.clean:
            break
        rounds = round_number
        total = sum(row.labor_hours for row in demand)
        desk = reads.plant.desk_for(total)
        decision = ctx.gate(
            f"k1_{round_number}", "review-extraction",
            build_task=lambda d=demand, v=verdicts, rn=round_number,
            q=desk.queue, note=desk.explanation: service.build_review_task(
                ctx, job, d,
                [
                    verdict.model_dump(mode="json") for verdict in in_doubt(v.fields)
                ],
                round_number=rn, queue=q, routing_note=note,
                document_id=page["document_id"],
                plant_ref=reads.plant.source_ref,
                labor_ref=reads.labor.source_ref,
            ),
        )
        decided_by = decision.decided_by
        decision_note = decision.note or decision.correction
        # The desk that authorized the commit, so the Gateway checks the queue
        # that actually raised the work rather than a standing pin the
        # reviewer may not hold.
        commit_queue = desk.queue

        if decision.verdict == "rejected":
            # Nothing is written. The job then has no demand rows, the
            # scheduling window does not schedule it, and it says so as a
            # count — which is the same shape a suspended extraction leaves.
            return _finish(
                ctx, job, reading, [], demand, selection, page, reads.plant,
                state="rejected",
                reason=decision.note or "the reading was rejected on review",
                decided_by=decided_by, decision_note=decision_note,
                rounds=rounds, wrote=False,
            )
        if decision.verdict == "approved":
            state = "extracted"
            reason = (
                "written on review despite: "
                + "; ".join(
                    verdict.reason for verdict in in_doubt(verdicts.fields)[:3]
                )
            )
            break
        # `corrected`: the supervisor fixed the reading, so run both checks
        # again over the corrected values rather than trusting the override.
        patched = ctx.step(
            f"x3_{round_number}", "apply-correction", "transform",
            fn=lambda d=decision: _apply_correction(reading, d.payload),
            summary=lambda out: (
                f"the supervisor corrected {', '.join(out['changed']) or 'nothing'}"
                + (
                    f" · refused {', '.join(out['refused'])} — no such field on "
                    "this reading" if out["refused"] else ""
                )
            ),
        )
        reading = ShippingListExtraction.model_validate(patched["reading"])
        demand, verdicts = _check_round(
            ctx, reading, reads, f"_{round_number}", boxes
        )
        state = "extracted"
        reason = f"written after review by {decided_by}"
    else:
        state = "flagged"
        reason = (
            f"still in doubt after {MAX_REVIEW_ROUNDS} review round(s) — "
            "written as read, with the verdicts on the rows"
        )

    # -- a1: the one write ---------------------------------------------------
    principal = ctx.principal or domain.plant_flow_principal(commit_queue)
    commit = service.gateway.action(
        "apply-job-demand", principal,
        run=ctx.record, step_id="a1",
        task_id=(f"task_{ctx.record.id}_k1_{rounds}" if rounds else ""),
        decided_by=decided_by,
        queue=commit_queue,
    )
    written = ctx.step(
        "a1", "apply-job-demand", "action",
        fn=lambda: commit.commit(lambda writer: service.apply_demand(
            job, demand, ctx.record.id, writer,
        )),
        writes=lambda _out: commit.writes,
        summary=lambda out: (
            f"{len(out['written'])} department demand row(s) for job "
            f"{job.job_number}: "
            + ", ".join(
                f"{row['department']} {row['labor_hours']:g} h"
                + (" (out of band)" if row["out_of_band"] else "")
                for row in out["written"]
            )
            if out["written"] else
            "no department has a page family on this ticket, so no demand row "
            "was written — and the job is not skipped, its departments are"
        ),
        refs=[reads.labor.source_ref, reads.labor.truss.source_ref],
    )
    return _finish(
        ctx, job, reading, written["written"], demand, selection, page,
        reads.plant, state=state, reason=reason, decided_by=decided_by,
        decision_note=decision_note, rounds=rounds, wrote=True,
    )


def _apply_correction(reading: ShippingListExtraction, payload: dict) -> dict:
    """The corrected values applied, keyed by the paths the verdicts used.

    A path the reading does not carry is **refused and counted**, not created:
    a correction keyed on a field this ticket has no line for is a correction
    about a different document, and inventing the row would put a number in a
    plant's day that no page carries.
    """
    grid = reading.grid()
    changed: list[str] = []
    refused: list[str] = []
    for path, raw in (payload.get("fields") or {}).items():
        if vocab.is_hours(path):
            # A computed figure is not a correctable field: correcting the
            # hours would overwrite the governed formula's answer with a typed
            # one, and the way to change hours is to correct the grid they were
            # computed from.
            refused.append(path)
            continue
        if vocab.set_at(grid, path, _coerced(grid, path, raw)):
            changed.append(path)
        else:
            refused.append(path)
    # A supervisor who has read the page has resolved the extraction's
    # uncertainty — about the document as a whole, and about each field they
    # actually answered. Without the second half a field the extraction was
    # unsure of stays in doubt after the human has settled it, and the next
    # round asks them the same question about the number they just typed.
    patched = ShippingListExtraction.model_validate(grid)
    scores = dict(patched.field_confidence)
    for path in changed:
        scores[path] = 1.0
    patched = patched.model_copy(
        update={"confidence": 1.0, "field_confidence": scores}
    )
    return {
        "reading": patched.model_dump(mode="json"),
        "changed": changed,
        "refused": refused,
    }


def _coerced(grid: dict, path: str, raw):
    """A typed value out of what a screen sent, shaped by what is already there."""
    current = vocab.value_at(grid, path)
    if isinstance(current, bool):
        return str(raw).strip().lower() in ("yes", "true", "1", "on")
    if isinstance(current, str):
        return str(raw)
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return raw


def _finish(
    ctx: RunContext,
    job: JobFacts,
    reading: ShippingListExtraction,
    written: list[dict],
    demand: list[DepartmentDemand],
    selection: dict,
    page: dict,
    plant: PlantParameters,
    *,
    state: str,
    reason: str,
    decided_by: str,
    decision_note: str,
    rounds: int,
    wrote: bool,
) -> dict:
    """e1, and the run's own outputs."""
    grid = reading.grid()
    absent = [
        family for family in vocab.FAMILY_LABELS
        if isinstance(grid.get(family), dict)
        and "present" in grid[family] and not grid[family]["present"]
    ]
    skipped = sorted({
        department
        for family in absent
        for department in plant.departments_for(
            vocab.PAGE_BY_FAMILY.get(family, ""))
    } - {row.department for row in demand})
    ctx.event(
        "e1", DEMAND_EVENT,
        f"job {job.job_number}: {len(written)} department demand row(s)"
        + (f", {len(skipped)} department(s) skipped for a missing page family"
           if skipped else "")
        + (f" — {state}" if state != "extracted" else ""),
    )
    return {
        "kind": "shipping-list-extraction",
        "job_key": job.job_key,
        "job_number": job.job_number,
        "state": state,
        "reason": reason,
        "skipped": False,
        "wrote": wrote,
        "document_id": page.get("document_id", ""),
        "documents": len(selection.get("documents") or []),
        "confidence": reading.confidence,
        "departments_written": [row["department"] for row in written],
        "departments_skipped": skipped,
        "out_of_band": [row.department for row in demand if row.out_of_band],
        "review_rounds": rounds,
        "decided_by": decided_by,
        "decision_note": decision_note,
    }
