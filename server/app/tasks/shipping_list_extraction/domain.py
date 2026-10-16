"""One eligible job's shipping list becomes ten departments' worth of demand.

**Per case, and its windowed sibling is `plant-eligibility-sweep`.** One
`JobEligible` fact, one run, one job: the extraction is a multi-page model read
and the questions it raises are about *this* ticket, so it is answered per case
and committed per case. `accounts_payable/v9`'s shape, on a different document
— one governed read, one metered pass, pure checks that name field paths, a
second cheap call that is skipped when nothing is in doubt, one human gate
routed by a governed table, and a correction that re-runs the checks rather
than overriding them.

**`JobTask` rows are created here and nowhere else.** The eligibility sweep
writes nothing and the scheduling flow reads these rows out of the graph; this
is the one place a department's share of a job comes into existence, which is
what makes "why does Truss Fab owe 10.5 hours on this job" answerable by one
step's journal.

**Two exceptions the SDD names, and neither is a Task.** A job with no shipping
list is *skipped with a count* — the office files the document and the next
window finds it, and there is nothing for a supervisor to decide about a file
that has not been filed. A department whose page family is absent is *skipped —
that department only, never the job* — because a building with no trusses is a
building with no trusses.
"""

from __future__ import annotations

import base64
import logging
from typing import Protocol

from pydantic import BaseModel, Field

from ... import desks, media
from ...auth import Principal, flow_principal
from ...connectors.dms import DOCUMENT_LIBRARY
from ...documents import DocumentService, Region
from ...gateway import ActionGateway, InMemoryLedgerStore
from ...graph import Claim, GraphService, GraphWriter, InMemoryGraphStore
from ...models import utcnow
from ...ontology import names as onto
from ...runs import RunContext, RunEngine
from ...task_queue import Task, TaskEvidence, TaskProposal, TaskService
from ..department_scheduling import formulas
from ..department_scheduling.parameters import PlantParameters, read_plant_parameters
from . import DEFAULT_VERSION, FLOW_ID
from .checks import DepartmentDemand

log = logging.getLogger("cumulus.shipping_list")

#: The two governed prompts. Both are `kind: prompt` assets read at run time,
#: at the revision the run got — the doctrine's line about a prompt being
#: governed knowledge rather than a string literal.
PROMPT_ASSET_ID = "shipping-list-extract-prompt"
LOCATE_PROMPT_ASSET_ID = "shipping-list-locate-prompt"

#: What each ModelCall is *for*, on its ledger row. The column that makes a
#: bill legible without joining anything: two flows sharing one model differ
#: here and nowhere else.
EXTRACT_PURPOSE = "extract-shipping-list"
LOCATE_PURPOSE = "locate-shipping-list-fields"

#: The fact this flow emits. Nothing binds it, deliberately: the scheduling
#: window reads `JobTask` rows out of the graph instead, which is
#: `ap-intake`'s argument — a work list scoped to one delivery skews and a
#: standing question does not.
DEMAND_EVENT = "JobDemandExtracted"

SUPERVISOR_QUEUE = desks.PLANT_SUPERVISOR

#: Who commits the demand when nobody was asked. Bound to the same desk a
#: supervisor would decide from, so the Gateway asks the same question either
#: way and un-binding this stops the unreviewed commit rather than exempting it.
PLANT_FLOW = flow_principal(FLOW_ID, SUPERVISOR_QUEUE)

#: How many times one ticket may come back to a supervisor before the flow
#: stops asking. `accounts_payable`'s bound, for its reason: a correction that
#: does not settle the checks is a conversation, and a flow that keeps
#: re-raising is a loop nobody closes.
MAX_REVIEW_ROUNDS = 2


def plant_flow_principal(queue: str = "") -> Principal:
    """The Flow's own identity, bound to the desk it is committing under."""
    return flow_principal(FLOW_ID, queue or SUPERVISOR_QUEUE)


# ---------------------------------------------------------------------------
# What the page says — the governed prompt's own shape
# ---------------------------------------------------------------------------
#
# Every field name below is a key the governed extraction prompt asks for, and
# the dotted paths in `paths.py` are these names. Renaming one here without
# renaming it there makes the value arrive empty and the department it fed look
# absent, which is what that page's "editing this safely" section warns about.
#
# The scalars a governed formula **multiplies** are `float | None`, and the
# `None` is load-bearing: absent and zero are different readings of a grid, and
# a check that could not tell them apart would either flag every empty line or
# silently price a page nobody read.


class TrussRow(BaseModel):
    mark: str = ""
    quantity: float | None = None
    span_ft: float | None = None
    ply: float | None = None
    top_chord_members: float | None = None
    bottom_chord_members: float | None = None
    web_members: float | None = None


class ComponentRow(BaseModel):
    description: str = ""
    quantity: float | None = None


class LaminateRow(BaseModel):
    description: str = ""
    lumber_size: str = ""
    length_ft: float | None = None
    ply: float | None = None
    quantity: float | None = None
    perma_column: bool = False
    precut: bool = False


class ColourRow(BaseModel):
    colour: str = ""
    linear_feet: float | None = None


class CribRow(BaseModel):
    size: str = ""
    quantity: float | None = None


class HardwareRow(BaseModel):
    description: str = ""
    quantity: float | None = None


class TrimRow(BaseModel):
    description: str = ""
    length_in: float | None = None
    quantity: float | None = None
    weight_lb: float | None = None


class A1Trusses(BaseModel):
    present: bool = False
    rows: list[TrussRow] = Field(default_factory=list)


class BComponents(BaseModel):
    present: bool = False
    total_quantity: float | None = None
    rows: list[ComponentRow] = Field(default_factory=list)


class BLaminates(BaseModel):
    present: bool = False
    rows: list[LaminateRow] = Field(default_factory=list)


class BVerticals(BaseModel):
    present: bool = False
    blocks_quantity: float | None = None
    ripped_plywood_sheets: float | None = None
    ridge_cleat_quantity: float | None = None


class BTrussGrid(BaseModel):
    present: bool = False


class BNotes(BaseModel):
    #: Not a page family of its own and it has no `present`: a handwritten note
    #: is a fact about the B page, and the girts row it raises is conditioned
    #: on the note rather than on a block being there.
    girts_plated_in: bool = False
    text: str = ""


class CSteelFab(BaseModel):
    present: bool = False
    total_linear_feet: float | None = None
    condenstop: bool = False
    colours: list[ColourRow] = Field(default_factory=list)
    cribs: list[CribRow] = Field(default_factory=list)


class DHardware(BaseModel):
    present: bool = False
    rows: list[HardwareRow] = Field(default_factory=list)
    concrete_mix_bags: float = 0.0
    l_bracket_12x5_quantity: float = 0.0


class ETrim(BaseModel):
    present: bool = False
    total_weight_lb: float | None = None
    fj_trim: bool = False
    jj_trim: bool = False
    ridge_light: bool = False
    ridge_vent_quantity: float = 0.0
    house_wrap_rolls: float = 0.0
    precut_house_wrap: bool = False
    rows: list[TrimRow] = Field(default_factory=list)


class FSlideDoor(BaseModel):
    present: bool = False
    track_end_caps_quantity: float | None = None
    pull_plate: bool = False
    non_wick_trim_grid: bool = False
    framing_grid: bool = False


class LLLoaderList(BaseModel):
    present: bool = False
    board_hours: float | None = None
    lumber_grid_present: bool = False
    plywood_grid_present: bool = False
    pallet_items_quantity: float = 0.0
    largest_truss_width_ft: float | None = None
    roller_trailer: bool = False


class ShippingListExtraction(BaseModel):
    """One metered pass over one job's ticket, in the prompt's own shape."""

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    job_number: str = ""
    confidence: float = 0.0
    #: The seven printed page labels, as the document declares them. Checked
    #: against the blocks below rather than trusted: "present but empty" and
    #: "absent" are different answers and only one of them skips a department.
    pages: dict[str, bool] = Field(default_factory=dict)

    a1_trusses: A1Trusses = Field(default_factory=A1Trusses)
    b_components: BComponents = Field(default_factory=BComponents)
    b_laminates: BLaminates = Field(default_factory=BLaminates)
    b_verticals: BVerticals = Field(default_factory=BVerticals)
    b_truss_grid: BTrussGrid = Field(default_factory=BTrussGrid)
    b_notes: BNotes = Field(default_factory=BNotes)
    c_steel_fab: CSteelFab = Field(default_factory=CSteelFab)
    d_hardware: DHardware = Field(default_factory=DHardware)
    e_trim: ETrim = Field(default_factory=ETrim)
    f_slide_door: FSlideDoor = Field(default_factory=FSlideDoor)
    ll_loader_list: LLLoaderList = Field(default_factory=LLLoaderList)

    #: Confidence per dotted field path, exactly as the governed prompt asks
    #: for it. This is what turns "check this shipping list" into "check these
    #: three numbers".
    field_confidence: dict[str, float] = Field(default_factory=dict)

    #: **Not read off the page.** Both wing dimensions come off the job record
    #: — the building and wings screen — which is why the governed hardware
    #: formula still gives a job with no wings a base of the setup figure
    #: alone. The flow joins it in from the `Job` row so the pure formula can
    #: read one dictionary.
    wings_total_ft: float = 0.0

    def grid(self) -> dict:
        """The reading as a plain dictionary — what every pure function reads."""
        return self.model_dump(mode="json")

    def summary(self) -> str:
        present = [page for page, flag in sorted(self.pages.items()) if flag]
        return (
            f"{self.model or 'no extractor'}: job {self.job_number or '—'} · "
            f"page famil{'y' if len(present) == 1 else 'ies'} "
            f"{', '.join(present) or 'none'} · confidence "
            f"{self.confidence:.2f} · {len(self.field_confidence)} field "
            "score(s)"
        )


class ShippingListReader(Protocol):
    def read(
        self,
        image: bytes | None,
        filename: str,
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> ShippingListExtraction:
        """`prompt` is the governed instruction the flow read for this run.

        Passed in rather than held by the reader, for the reason
        `InvoiceReader` gives: the instruction is knowledge and the reader is
        machinery. There is no `hint` parameter and there will not be one — a
        caller-supplied string that steers an extraction is what `v8` existed
        to remove.
        """
        ...


# ---------------------------------------------------------------------------
# The documents this run reads
# ---------------------------------------------------------------------------


class SourceDocument(BaseModel):
    """One file under the governed cut-sheet root that this run will read."""

    external_id: str = ""
    object_ref: str = ""
    natural_key: str = ""
    filename: str = ""
    folder_path: str = ""
    storage_ref: str = ""
    latest_version: int = 0
    #: `shipping-list` or `cut-sheet`, by the governed filename pattern that
    #: matched it. The shipping list is what the model reads; the cut sheets
    #: are fetched and stored because the review screen opens them beside it.
    kind: str = ""


class JobFacts(BaseModel):
    """The job row this run is about, as the graph holds it.

    Read here rather than carried on the event: by the time this runs the job
    may have been re-landed, and a payload copied out of the sweep would be a
    snapshot competing with the record.
    """

    job_key: str = ""
    job_id: str = ""
    job_ref: str = ""
    job_number: str = ""
    label: str = ""
    plant_site: str = ""
    load_date: str = ""
    customer_name: str = ""
    combined_with: str = ""
    shipment_type: str = ""
    #: The two wings dimensions the ERP records separately, summed — the
    #: governed hardware formula reads `(width + length) × rate`, and the page
    #: says both come off the job record rather than off the shipping list.
    wings_total_ft: float = 0.0
    secondary_dimensions: str = ""


class ContentFetcher(Protocol):
    """Whatever can turn a customer-resolvable address into bytes.

    A Protocol rather than the `EstateConnector` type, for `ap_intake`'s
    reason: the flow needs one call, and a test that had to build a connector
    to exercise a folder filter would be testing the wrong thing.
    """

    def fetch_content(self, url: str) -> tuple[bytes, str]: ...


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class ShippingListExtractionService:
    """Reads one job's ticket, checks it, asks about it, and writes the demand."""

    def __init__(
        self,
        graph: GraphService,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        documents: DocumentService,
        tasks: TaskService,
        engine: RunEngine | None = None,
        gateway: ActionGateway | None = None,
        reader: ShippingListReader | None = None,
        locator=None,  # locate.FieldLocator — v8's, reused rather than copied
        fetcher: ContentFetcher | None = None,
    ) -> None:
        self.graph = graph
        self.knowledge = knowledge
        self.documents = documents
        self.tasks = tasks
        self.engine = engine
        #: None is a working deployment: a Cumulus with no extractor configured
        #: reads no page and says so on the run, which is honest. It is not a
        #: substitute for bytes.
        self.reader = reader
        self.locator = locator
        #: None is also a working deployment — no estate registered means
        #: nothing to fetch from, which the run reports as a count.
        self.fetcher = fetcher
        # Never None, for the reason `payables` gives at the same line: a
        # service wired without a Gateway still writes through one.
        self.gateway = gateway or ActionGateway(
            graph or GraphService(InMemoryGraphStore()), InMemoryLedgerStore()
        )
        if engine is not None:
            from .. import shipped_versions
            from ... import modelcall

            for number, module in sorted(shipped_versions(FLOW_ID).items()):
                engine.register(
                    FLOW_ID, modelcall.metered(module.build_body(self)),
                    version=number, default=(number == DEFAULT_VERSION),
                )

    # -- governed reads ------------------------------------------------------

    def read_plant(self) -> PlantParameters:
        return read_plant_parameters(self.knowledge)

    def read_labor(self) -> formulas.Parameters:
        """The labor-hour parameters and the truss standards, both at revision.

        Two assets, one call, because the formulas cannot run without both and
        a run that read them at two moments could name two revisions of a pair
        that has to agree.
        """
        truss = self.knowledge.repo.get(formulas.TRUSS_ASSET_ID) if self.knowledge else None
        standards = formulas.parse_truss_standards(
            truss.content if truss is not None else "",
            f"knowledge:{formulas.TRUSS_ASSET_ID}"
            + (f"@rev{getattr(truss, 'revision', 1)}" if truss is not None else ""),
        )
        labor = self.knowledge.repo.get(formulas.LABOR_ASSET_ID) if self.knowledge else None
        if labor is None:
            log.warning(
                "Labor hour formulas missing; every department reports the "
                "governed values it could not find",
                extra={"assetId": formulas.LABOR_ASSET_ID},
            )
        return formulas.parse_labor_parameters(
            labor.content if labor is not None else "",
            f"knowledge:{formulas.LABOR_ASSET_ID}"
            + (f"@rev{getattr(labor, 'revision', 1)}" if labor is not None else ""),
            standards,
        )

    # -- x1: the job, and the files under the governed root ----------------

    def job_facts(self, job_key: str) -> JobFacts | None:
        """The `Job` row this run is about, or None when the graph has none."""
        from ...graph import object_id

        record = self.graph.get(object_id(onto.JOB, job_key))
        if record is None:
            return None
        props = record.props
        return JobFacts(
            job_key=record.natural_key,
            job_id=record.id,
            job_ref=record.ref(),
            job_number=str(props.get("job_number") or ""),
            label=record.label or record.natural_key,
            plant_site=str(props.get("plant_site") or ""),
            load_date=str(props.get("load_date") or ""),
            customer_name=str(props.get("customer_name") or ""),
            combined_with=str(props.get("combined_with") or ""),
            shipment_type=str(props.get("shipment_type") or ""),
            secondary_dimensions=str(props.get("secondary_dimensions") or ""),
            wings_total_ft=_wings(props.get("secondary_dimensions")),
        )

    def source_documents(
        self, job: JobFacts, plant: PlantParameters
    ) -> list[SourceDocument]:
        """The job's shipping list and cut sheets, out of the governed folder.

        Walked from the folder rather than from the document library, for
        `ap_intake`'s arithmetic: the plant's tree is one folder per job, so
        this costs one neighbour read instead of a scan of every file the
        customer has ever filed.

        The **root** is what makes this correct rather than merely cheap. The
        same job's shipping list is also filed under `Job Documents/<job>` for
        the office, and the governed filename pattern matches both; only the
        cut-sheet root says which one the plant floor works from.
        """
        root = plant.documents.folder_for(job.plant_site, job.job_number)
        if not root:
            return []
        found: list[SourceDocument] = []
        for folder in self.graph.find(onto.FOLDER):
            if str(folder.props.get("path") or "") != root:
                continue
            for _link, document in self.graph.neighbors(
                folder.id, "filed_under", "in"
            ):
                if document.status == "superseded":
                    continue
                props = document.props
                filename = str(props.get("filename") or document.label)
                kind = (
                    "shipping-list"
                    if plant.documents.matches(
                        plant.documents.shipping_list_pattern, filename,
                        job.job_number)
                    else "cut-sheet"
                    if plant.documents.matches(
                        plant.documents.cut_sheet_pattern, filename,
                        job.job_number)
                    else ""
                )
                if not kind:
                    continue
                found.append(SourceDocument(
                    external_id=str(props.get("external_id") or ""),
                    object_ref=document.ref(),
                    natural_key=document.natural_key,
                    filename=filename,
                    folder_path=root,
                    storage_ref=str(props.get("storage_ref") or ""),
                    latest_version=int(props.get("latest_version") or 0),
                    kind=kind,
                ))
        # The shipping list first, then the cut sheets, then by name: the
        # model reads the first shipping list and a stable order is what makes
        # "which page did this number come off" reproducible.
        found.sort(key=lambda item: (item.kind != "shipping-list", item.filename))
        return found

    # -- s1: the bytes, under their content hash ---------------------------

    def fetch_pages(
        self,
        candidates: list[dict],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        """Read each file, put the bytes in the map, and mark the row read.

        `ap_intake._fetch_and_store`'s shape, and its two claims: the hash says
        what was read and the version says which revision it was read from,
        because a reissued ticket is exactly the case where a hash alone would
        report a stale reading as a current one.

        A file that will not fetch is a **count**, never a gate. The run then
        has no page to read and says so; the next window tries again.
        """
        graph = writer if writer is not None else self.graph
        counts = {"read": 0, "unreachable": 0, "unreadable": 0, "no_fetcher": 0}
        stored: list[dict] = []
        notes: list[str] = []
        for raw in candidates:
            candidate = SourceDocument.model_validate(raw)
            if self.fetcher is None or not candidate.storage_ref:
                counts["no_fetcher"] += 1
                continue
            try:
                content, declared = self.fetcher.fetch_content(candidate.storage_ref)
            except Exception as exc:  # noqa: BLE001 — counted, never fatal
                counts["unreachable"] += 1
                notes.append(f"{candidate.filename}: {type(exc).__name__}: {exc}")
                log.warning(
                    "Shipping list bytes did not fetch",
                    extra={
                        "runId": run_id, "documentId": candidate.external_id,
                        "storageRef": candidate.storage_ref, "error": str(exc),
                    },
                )
                continue
            media_type = media.sniff(content, candidate.filename, declared)
            if not media.is_model_readable(media_type):
                counts["unreadable"] += 1
                notes.append(
                    f"{candidate.filename}: {media_type} carries no page to read"
                )
                continue
            record = self.documents.put_bytes(
                base64.b64encode(content).decode(),
                filename=candidate.filename, kind="other",
                uploaded_by=f"flow:{FLOW_ID}", run_id=run_id,
                media_type=media_type,
            )
            counts["read"] += 1
            stored.append({
                "document_id": record.id,
                "external_id": candidate.external_id,
                "filename": candidate.filename,
                "kind": candidate.kind,
                "pages": record.pages,
                "byte_size": record.byte_size,
            })
            graph.upsert(
                onto.DOCUMENT, candidate.natural_key,
                [
                    Claim(field="sha256", value=record.sha256,
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="read_version", value=candidate.latest_version,
                          source=f"flow:{FLOW_ID}"),
                ],
                label=candidate.filename or candidate.natural_key,
                provenance=f"read by {run_id}",
            )
        return {**counts, "stored": stored, "notes": notes[:10]}

    def document_bytes(self, document_id: str) -> bytes | None:
        record = self.documents.get(document_id) if self.documents else None
        if record is None or not record.content_b64:
            return None
        return base64.b64decode(record.content_b64)

    def store_regions(self, document_id: str, regions: list[Region]) -> None:
        if regions and self.documents is not None:
            self.documents.set_regions(document_id, regions)

    # -- k1: the one gate --------------------------------------------------

    def build_review_task(
        self,
        ctx: RunContext,
        job: JobFacts,
        demand: list[DepartmentDemand],
        fields: list[dict],
        *,
        round_number: int,
        queue: str,
        routing_note: str,
        document_id: str,
        plant_ref: str,
        labor_ref: str,
    ) -> Task:
        """The fields in doubt, the computed hours beside each grid, and the page.

        One question, not a seven-page PDF. `fields` is the serialized verdict
        set — carried and not read here, so this method stays the shape of the
        Task and the verdicts stay the shape of the review.
        """
        doubted = len(fields)
        out_of_band = [row.department for row in demand if row.out_of_band]
        return Task(
            id=f"task_{ctx.record.id}_k1_{round_number}",
            kind="decision",
            title=(
                f"Check {doubted} field(s) on job {job.job_number}'s shipping list"
                if doubted else
                f"Confirm job {job.job_number}'s shipping list"
            ),
            summary=(
                f"The extraction read {job.job_number}"
                + (f" for {job.customer_name}" if job.customer_name else "")
                + " and these values did not settle. Correcting one re-runs "
                "the grid checks and the governed labor formulas over it; the "
                "hours beside each grid are what the plant's own formulas "
                "compute, at the revision named on the row."
                + (
                    " Out of band: " + ", ".join(out_of_band)
                    + " — the governed page puts a figure outside its "
                    "department's expected range in front of a supervisor "
                    "before it is written."
                    if out_of_band else ""
                )
            ),
            queue=queue,
            priority="high" if out_of_band else "normal",
            flow_id=FLOW_ID,
            run_id=ctx.record.id,
            case_ref=ctx.record.case_ref or job.job_key,
            refs=(
                [plant_ref, labor_ref, job.job_ref]
                + ([f"document:{document_id}"] if document_id else [])
            ),
            evidence=[
                TaskEvidence(label="Job", value=job.label or job.job_number),
                TaskEvidence(label="Load date", value=job.load_date or "—"),
                TaskEvidence(label="Plant site", value=job.plant_site or "—"),
                TaskEvidence(
                    label="Departments with demand",
                    value=", ".join(
                        f"{row.department} {row.labor_hours:g} h" for row in demand
                    ) or "none",
                ),
                TaskEvidence(label="Fields in doubt", value=str(doubted)),
                TaskEvidence(label="Review round", value=str(round_number)),
                TaskEvidence(label="Routed to this desk because",
                             value=routing_note),
            ],
            proposal=TaskProposal(
                action="apply-job-demand",
                target="production-records",
                summary=(
                    f"Write {len(demand)} department demand row(s) for job "
                    f"{job.job_number}, each with the formula and asset "
                    "revision that produced its hours."
                ),
                fields=fields,
            ),
            resumes=(
                "approving writes the demand rows; correcting a value re-runs "
                "the checks and the formulas over the corrected reading"
            ),
            created_at_iso=utcnow().isoformat(),
        )

    def resume_for_task(self, task: Task, principal: Principal | None = None):
        """`principal` is the human who decided, carried to the commit.

        The Gateway checks the committer at the write, so the deciding human
        has to reach it — a uid recovered from `decided_by` afterwards would
        name the right person and prove nothing about their authority.
        """
        if task.flow_id != FLOW_ID or self.engine is None:
            return None
        return self.engine.resume_for_task(task.id, principal=principal)

    def apply_task_decision(self, task: Task, principal: Principal | None = None):
        """The console's door, shaped like every other flow's."""
        return self.resume_for_task(task, principal)

    # -- a1: the one write -------------------------------------------------

    def apply_demand(
        self,
        job: JobFacts,
        demand: list[DepartmentDemand],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        """One `JobTask` per department whose grid is present, and the edge to it.

        **`JobTask` rows are created here and nowhere else.** A department
        whose page family is absent never reaches this list, which is the skip
        the governed page asks for: that department only, never the job.

        `upstream_state` is deliberately **not** claimed. The scheduling flow
        reads a job's own Done flags live at the moment a department is
        considered — a job whose truss cutting finished this morning is
        fabricable this afternoon — so a snapshot written here would be a
        second answer to a question the graph can always re-ask.
        """
        graph = writer if writer is not None else self.graph
        written: list[dict] = []
        for row in demand:
            key = f"{job.job_key}:{row.department}"
            record = graph.upsert(
                onto.JOB_TASK, key,
                [
                    Claim(field="department", value=row.department,
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="labor_hours", value=row.labor_hours,
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="hours_basis", value=row.hours_basis,
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="demand_summary", value=row.demand_summary,
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="source_page", value=row.source_page,
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="confidence", value=row.confidence,
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="out_of_band",
                          value="yes" if row.out_of_band else "no",
                          source=f"flow:{FLOW_ID}"),
                ],
                label=f"{job.job_number} {row.department}",
                provenance=f"extracted by {run_id}",
            )
            # No `run_id`/`step_id` here: a `GraphWriter` stamps both from
            # the step it was opened for, and naming them again is a
            # `TypeError` rather than a duplicate.
            graph.link(
                "has_job_task", job.job_id, record.id,
                method="declared", provenance=f"extracted by {run_id}",
            )
            written.append({
                "department": row.department,
                "labor_hours": row.labor_hours,
                "out_of_band": row.out_of_band,
                "ref": record.ref(),
            })
        return {"written": written}


def _wings(raw) -> float:
    """`"12 x 24"` → 36 — the two wings dimensions the hardware base multiplies.

    Kept verbatim on the job by the feed, because *which* of a building's
    dimensions a labor formula sums is a governed decision and not the
    connector's. This is that decision, read where the formula's input is
    assembled: the governed page says the base is
    `(wings total width + wings total length) × rate`.
    """
    numbers = [
        float(part) for part in str(raw or "").replace("×", "x").split("x")
        if _is_number(part)
    ]
    return sum(numbers)


def _is_number(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def build_extraction_service(
    graph: GraphService,
    knowledge,
    documents: DocumentService,
    tasks: TaskService,
    engine: RunEngine | None = None,
    gateway: ActionGateway | None = None,
    reader: ShippingListReader | None = None,
    locator=None,
    fetcher: ContentFetcher | None = None,
) -> ShippingListExtractionService:
    return ShippingListExtractionService(
        graph, knowledge, documents, tasks, engine, gateway, reader, locator,
        fetcher,
    )


__all__ = [
    "DEMAND_EVENT",
    "DOCUMENT_LIBRARY",
    "EXTRACT_PURPOSE",
    "LOCATE_PROMPT_ASSET_ID",
    "LOCATE_PURPOSE",
    "MAX_REVIEW_ROUNDS",
    "PROMPT_ASSET_ID",
    "PLANT_FLOW",
    "SUPERVISOR_QUEUE",
    "JobFacts",
    "ShippingListExtraction",
    "ShippingListExtractionService",
    "ShippingListReader",
    "SourceDocument",
    "build_extraction_service",
    "plant_flow_principal",
]
