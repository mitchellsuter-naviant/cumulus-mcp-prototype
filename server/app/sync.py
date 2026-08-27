"""Inbound sync as Flows: vendors, purchase orders, receiving reports.

The doctrine retired the Workflow/DataFlow split, and this module is what that
decision looks like in code. A vendor feed landing 200 rows and an invoice
arriving for extraction are the same kind of thing — authored logic, run by the
engine, journalled, resumable — differing only in their Trigger and in whether
they suspend.

One rule they do *not* share, and it matters: **a batch feed never suspends for
one ambiguous row.** A per-case Flow blocks on a gate because the case cannot
proceed without the answer. A feed that stopped on row 47 would hold up rows 48
through 200 for no reason, so an ambiguous row lands, asserts nothing, and
raises a `match_review` Task; the steward's decision is applied afterwards by
`apply_match_decision`. That is why resolution and link-application are separate
steps here.

What each sync run does, in order:

  t1 SourceDelivered (trigger) → x1 read-resolution-policy (governed
  thresholds) → s1 land-records (SourceRecords, exactly as they arrived) →
  z1 resolve-records (score against what we know; assert nothing) →
  a1 apply-resolutions (mint, link, or raise a steward Task) →
  e1 …Synced (Event)

The landing step is the one people skip and regret. Keeping the raw row means a
change to the matching rules can **re-resolve without re-fetching**, which is
the difference between a sync you can reason about and one you have to trust.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from .anomaly import parse_anomaly_thresholds
from .graph import Claim, GraphService, GraphWriter, normalize_key, object_id
from .matching import parse_match_tolerances
from .models import utcnow
from .resolution import (
    ResolutionOutcome,
    ResolutionThresholds,
    read_resolution_thresholds,
    resolve,
)
from .runs import RunContext, RunEngine
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.sync")

VENDOR_FLOW_ID = "vendor-sync"
PO_FLOW_ID = "purchase-order-sync"
RECEIVING_FLOW_ID = "receiving-sync"
WORKFORCE_FLOW_ID = "workforce-sync"
STEWARD_QUEUE = "vendor-stewardship"
PEOPLE_QUEUE = "people-ops"

SyncKind = Literal[
    "vendors", "purchase-orders", "receiving-reports", "workforce",
]


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class VendorRecord(BaseModel):
    external_id: str
    name: str
    tax_id: str = ""
    status: str = "active"
    payment_terms: str = ""
    remit_country: str = ""
    aliases: list[str] = Field(default_factory=list)
    # A source saying "this one is gone". Never a delete on our side: a vendor
    # with open invoices has to stay resolvable.
    deleted: bool = False


class PurchaseOrderLineRecord(BaseModel):
    sku: str = ""
    description: str = ""
    quantity_ordered: float = 0.0
    unit_price_usd: float = 0.0


class PurchaseOrderRecord(BaseModel):
    external_id: str
    po_number: str
    vendor_external_id: str = ""
    vendor_name: str = ""
    ordered_date: str = ""
    currency: str = "USD"
    status: str = "open"
    lines: list[PurchaseOrderLineRecord] = Field(default_factory=list)


class ReceivingLineRecord(BaseModel):
    sku: str = ""
    description: str = ""
    quantity_received: float = 0.0
    condition: str = "good"


class ReceivingRecord(BaseModel):
    external_id: str
    receipt_number: str
    po_number: str = ""
    received_date: str = ""
    status: str = "complete"
    lines: list[ReceivingLineRecord] = Field(default_factory=list)


class TeamRecord(BaseModel):
    """One organizational unit as the HRIS holds it."""

    external_id: str
    slug: str
    name: str
    mission: str = ""
    # Bind columns: identifiers of other entities, resolved on the way in.
    parent_slug: str = ""
    lead_email: str = ""


class EmployeeRecord(BaseModel):
    """One person as the HRIS holds them.

    `email` is the natural key and `manager_email`/`team_slug` are bind
    columns — identifiers of other entities carried in the row. Nothing here
    needs a steward to adjudicate a match, which is exactly why the HRIS
    mapping binds rather than reifies: an exact key needs no landing record.
    """

    external_id: str
    email: str
    name: str = ""
    title: str = ""
    location: str = ""
    start_date: str = ""
    manager_email: str = ""
    team_slug: str = ""


class SyncRequest(BaseModel):
    """One delivery from one source system.

    `watermark` is the source's own cursor, echoed back on the run so a
    re-delivery is recognizable and a gap is visible. `idempotency_key` lets a
    retried push be recognized as the same delivery rather than a second one.
    """

    source: str = Field(min_length=1, max_length=64)
    watermark: str = ""
    idempotency_key: str = ""
    vendors: list[VendorRecord] = Field(default_factory=list)
    purchase_orders: list[PurchaseOrderRecord] = Field(default_factory=list)
    receiving_reports: list[ReceivingRecord] = Field(default_factory=list)
    # Teams and employees travel together: the org chart cannot be wired
    # until both ends of every edge exist, so one delivery is one run.
    teams: list[TeamRecord] = Field(default_factory=list)
    employees: list[EmployeeRecord] = Field(default_factory=list)


class SourceStatus(BaseModel):
    """What the Data → Sources lens reads for one feed."""

    source: str
    kind: SyncKind
    flow_id: str
    last_run_id: str = ""
    last_run_at_iso: str = ""
    last_status: str = ""
    watermark: str = ""
    deliveries: int = 0
    records_landed: int = 0
    linked: int = 0
    created: int = 0
    awaiting_steward: int = 0
    unchanged: int = 0
    last_error: str = ""


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SyncService:
    def __init__(
        self,
        graph: GraphService,
        knowledge,
        tasks: TaskService,
        engine: RunEngine | None = None,
    ) -> None:
        self.graph = graph
        self.knowledge = knowledge
        self.tasks = tasks
        self.engine = engine
        if engine is not None:
            engine.register(VENDOR_FLOW_ID, self._vendor_body)
            engine.register(PO_FLOW_ID, self._po_body)
            engine.register(RECEIVING_FLOW_ID, self._receiving_body)
            engine.register(WORKFORCE_FLOW_ID, self._workforce_body)

    # -- entry point --------------------------------------------------------

    def deliver(self, request: SyncRequest, actor: str = "") -> list[dict]:
        """Accept one push and run whichever sync flows it carries."""
        if self.engine is None:
            raise RuntimeError("Sync service has no run engine wired")
        outcomes: list[dict] = []
        plan: list[tuple[str, str, list[Any]]] = [
            (VENDOR_FLOW_ID, "vendors", request.vendors),
            (PO_FLOW_ID, "purchase_orders", request.purchase_orders),
            (RECEIVING_FLOW_ID, "receiving_reports", request.receiving_reports),
        ]
        if request.teams or request.employees:
            plan.append((WORKFORCE_FLOW_ID, "workforce", []))
        for flow_id, field, records in plan:
            if flow_id != WORKFORCE_FLOW_ID and not records:
                continue
            inputs = {
                "source": request.source,
                "watermark": request.watermark,
                "records": [r.model_dump() for r in records],
            }
            if flow_id == WORKFORCE_FLOW_ID:
                inputs = {
                    "source": request.source,
                    "watermark": request.watermark,
                    "teams": [t.model_dump() for t in request.teams],
                    "employees": [e.model_dump() for e in request.employees],
                }
            run_id = None
            if request.idempotency_key:
                # A retried delivery re-enters the same run, which replays its
                # completed steps and changes nothing.
                digest = hashlib.sha256(
                    f"{flow_id}:{request.source}:{request.idempotency_key}".encode()
                ).hexdigest()[:10]
                run_id = f"run_sync_{digest}"
                existing = self.engine.get(run_id)
                if existing is not None:
                    outcomes.append({
                        "flow_id": flow_id, "run_id": run_id,
                        "status": existing.status, "replayed": True,
                        **(existing.outputs or {}),
                    })
                    continue
            record = self.engine.start(
                flow_id, inputs, actor=actor or f"source:{request.source}",
                run_id=run_id,
            )
            outcomes.append({
                "flow_id": flow_id, "run_id": record.id, "status": record.status,
                "replayed": False, **(record.outputs or {}),
            })
        return outcomes

    # -- vendors -----------------------------------------------------------

    def _vendor_body(self, ctx: RunContext, inputs: dict) -> dict:
        source = inputs["source"]
        watermark = inputs.get("watermark", "")
        records = [VendorRecord.model_validate(r) for r in inputs["records"]]

        ctx.event(
            "t1", "SourceDelivered",
            f"{len(records)} vendor record(s) from `{source}`"
            + (f" · watermark {watermark}" if watermark else ""),
            kind="trigger",
        )

        thresholds_raw = ctx.step(
            "x1", "read-resolution-policy", "transform",
            fn=lambda: read_resolution_thresholds(self.knowledge).model_dump(),
            summary=lambda out: (
                f"{out['source_ref']}: auto-link ≥ {out['auto_link_at']:.2f} · "
                f"steward review ≥ {out['review_from']:.2f}"
            ),
        )
        thresholds = ResolutionThresholds.model_validate(thresholds_raw)
        ctx.note_refs(thresholds.source_ref)

        landing_writer = self.graph.writer(ctx.record.id, "s1")
        landed = ctx.step(
            "s1", "land-records", "sync",
            fn=lambda: self._land(
                source, "vendor", [r.model_dump() for r in records],
                landing_writer,
            ),
            writes=lambda _out: landing_writer.refs,
            summary=lambda out: (
                f"{out['landed']} landed, {out['unchanged']} unchanged "
                f"(byte-identical redelivery) in `source-landing`"
            ),
        )

        resolutions = ctx.step(
            "z1", "resolve-records", "resolve",
            fn=lambda: [
                self._score_vendor(record, source, thresholds).model_dump()
                for record in records
            ],
            summary=lambda out: (
                f"{sum(1 for o in out if o['disposition'] == 'linked')} linked · "
                f"{sum(1 for o in out if o['disposition'] == 'new')} new · "
                f"{sum(1 for o in out if o['disposition'] == 'review')} to a "
                "steward"
            ),
            refs=[thresholds.source_ref],
        )

        apply_writer = self.graph.writer(ctx.record.id, "a1")
        applied = ctx.step(
            "a1", "apply-resolutions", "action",
            fn=lambda: self._apply_vendors(
                source, records,
                [ResolutionOutcome.model_validate(r) for r in resolutions],
                ctx.record.id, apply_writer,
            ),
            writes=lambda _out: apply_writer.refs,
            summary=lambda out: (
                f"{out['linked']} linked to existing vendors · {out['created']} "
                f"minted · {out['deactivated']} deactivated · "
                f"{out['awaiting_steward']} Task(s) raised"
            ),
        )

        ctx.event("e1", "VendorsSynced", f"{len(records)} record(s) from {source}")
        return {
            "source": source, "watermark": watermark, "kind": "vendors",
            "records": len(records), **landed, **applied,
        }

    def _score_vendor(
        self, record: VendorRecord, source: str, thresholds
    ) -> ResolutionOutcome:
        return resolve(
            self.graph, "Vendor",
            [record.name, *record.aliases], thresholds,
            tax_id=record.tax_id, source=source,
            source_external_id=record.external_id,
        )

    def _apply_vendors(
        self,
        source: str,
        records: list[VendorRecord],
        outcomes: list[ResolutionOutcome],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        counts = {
            "linked": 0, "created": 0, "awaiting_steward": 0, "deactivated": 0,
        }
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        for record, outcome in zip(records, outcomes):
            source_record = graph.upsert(
                "SourceRecord", f"{source}:{record.external_id}",
                [
                    Claim(field="source", value=source, source=claim_source),
                    Claim(field="external_id", value=record.external_id, source=claim_source),
                    Claim(field="payload", value=record.model_dump_json(), source=claim_source),
                    Claim(field="landed_at", value=utcnow().isoformat(), source=claim_source),
                ],
                label=f"{source}:{record.external_id}",
                provenance=f"landed by {run_id}",
            )

            if outcome.disposition == "review":
                self._raise_match_task(record, outcome, source, source_record.id, run_id)
                counts["awaiting_steward"] += 1
                continue

            claims = [
                Claim(field="name", value=record.name, source=claim_source),
                Claim(field="slug", value=normalize_key(record.name),
                      source=claim_source),
                Claim(field="status",
                      value="inactive" if record.deleted else record.status,
                      source=claim_source),
            ]
            if record.tax_id:
                claims.append(Claim(field="tax_id", value=record.tax_id, source=claim_source))
            if record.payment_terms:
                claims.append(Claim(
                    field="payment_terms", value=record.payment_terms,
                    source=claim_source,
                ))
            if record.remit_country:
                claims.append(Claim(
                    field="remit_country", value=record.remit_country,
                    source=claim_source,
                ))
            if record.aliases:
                claims.append(Claim(
                    field="aliases", value=record.aliases, source=claim_source
                ))

            if outcome.disposition == "linked":
                existing = self.graph.get(outcome.object_id)
                vendor = graph.upsert(
                    "Vendor", existing.natural_key if existing else record.name,
                    claims, label=existing.label if existing else record.name,
                    provenance=f"claims from {source} via {run_id}",
                )
                counts["linked"] += 1
            else:
                # A vendor nobody has confirmed yet is `onboarding`, not
                # `active`: the AP flow can still resolve against it, and a
                # steward can see that nobody has vouched for it. The claim
                # comes from the Flow, which ranks below the source's own
                # status claim — so an ERP that says `active` wins, and an ERP
                # that says nothing leaves it onboarding.
                vendor = graph.upsert(
                    "Vendor", record.name,
                    claims + [Claim(
                        field="onboarding_state", value="unconfirmed",
                        source=f"flow:{VENDOR_FLOW_ID}", confidence=0.5,
                    )],
                    label=record.name,
                    provenance=f"minted from {source} record {record.external_id} "
                               f"by {run_id}",
                )
                counts["created"] += 1

            if record.deleted:
                counts["deactivated"] += 1

            # The method on the link is how this identity came to be asserted,
            # which is what a steward reviewing it later needs to know. A minted
            # vendor was not *matched* at all — the record declared it — so
            # calling that a fuzzy match would overstate what happened.
            method = (
                "declared" if outcome.disposition == "new"
                else "exact" if outcome.method in (
                    "tax-id", "normalized-name", "source-link"
                )
                else "fuzzy"
            )
            graph.link(
                "identified_as", vendor.id, source_record.id,
                props={
                    "source": source, "external_id": record.external_id,
                    "score": outcome.score, "run_id": run_id,
                },
                confidence=1.0 if method == "declared" else (outcome.score or 1.0),
                method=method,
                provenance=outcome.reason,
            )
        return counts

    def _raise_match_task(
        self,
        record: VendorRecord,
        outcome: ResolutionOutcome,
        source: str,
        source_record_id: str,
        run_id: str,
    ) -> Task:
        return self.tasks.create(Task(
            id=f"task_match_{object_id('Vendor', source + record.external_id)[-10:]}",
            kind="match_review",
            title=f"Vendor match needs a steward: {record.name}",
            summary=(
                f"`{source}` delivered {record.external_id} ({record.name}). "
                f"{outcome.reason}. Nothing has been asserted: approve to link "
                "it to the top candidate, reject to mint a new vendor, or "
                "correct with the id of the right one."
            ),
            queue=STEWARD_QUEUE,
            priority="normal",
            flow_id=VENDOR_FLOW_ID,
            run_id=run_id,
            case_ref=source_record_id,
            refs=[outcome.thresholds_ref] + [
                f"graph:Vendor/{c.object_id}" for c in outcome.candidates[:3]
            ],
            evidence=[
                TaskEvidence(label="Inbound name", value=record.name),
                TaskEvidence(label="Source", value=f"{source}:{record.external_id}"),
                TaskEvidence(label="Tax id", value=record.tax_id or "—"),
            ] + [
                TaskEvidence(
                    label=f"Candidate {i + 1} ({c.score:.2f})",
                    value=f"{c.label} — {c.reason}",
                )
                for i, c in enumerate(outcome.candidates[:3])
            ],
            proposal=TaskProposal(
                action="link-identity",
                target="vendor-master",
                summary=(
                    f"Link {source}:{record.external_id} to "
                    f"{outcome.candidates[0].label}"
                    if outcome.candidates else "Mint a new vendor"
                ),
            ),
            resumes="the link is asserted (or a new vendor minted) on decision",
            created_at_iso=utcnow().isoformat(),
        ))

    # -- purchase orders ----------------------------------------------------

    def _po_body(self, ctx: RunContext, inputs: dict) -> dict:
        source = inputs["source"]
        records = [PurchaseOrderRecord.model_validate(r) for r in inputs["records"]]
        ctx.event(
            "t1", "SourceDelivered",
            f"{len(records)} purchase order(s) from `{source}`", kind="trigger",
        )
        landing_writer = self.graph.writer(ctx.record.id, "s1")
        landed = ctx.step(
            "s1", "land-records", "sync",
            fn=lambda: self._land(
                source, "po", [r.model_dump() for r in records], landing_writer,
            ),
            writes=lambda _out: landing_writer.refs,
            summary=lambda out: (
                f"{out['landed']} landed, {out['unchanged']} unchanged"
            ),
        )
        apply_writer = self.graph.writer(ctx.record.id, "a1")
        applied = ctx.step(
            "a1", "apply-purchase-orders", "action",
            fn=lambda: self._apply_pos(
                source, records, ctx.record.id, apply_writer
            ),
            writes=lambda _out: apply_writer.refs,
            summary=lambda out: (
                f"{out['orders']} order(s) and {out['lines']} line(s) written · "
                f"{out['vendor_linked']} linked to a vendor · "
                f"{out['vendor_unresolved']} unresolved"
            ),
        )
        ctx.event("e1", "PurchaseOrdersSynced", f"{len(records)} record(s)")
        return {
            "source": source, "watermark": inputs.get("watermark", ""),
            "kind": "purchase-orders", "records": len(records),
            **landed, **applied,
        }

    def _apply_pos(
        self,
        source: str,
        records: list[PurchaseOrderRecord],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        counts = {"orders": 0, "lines": 0, "vendor_linked": 0, "vendor_unresolved": 0}
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        thresholds = read_resolution_thresholds(self.knowledge)
        for record in records:
            order = graph.upsert(
                "PurchaseOrder", record.po_number,
                [
                    Claim(field="po_number", value=record.po_number, source=claim_source),
                    Claim(field="ordered_date", value=record.ordered_date, source=claim_source),
                    Claim(field="currency", value=record.currency, source=claim_source),
                    Claim(field="status", value=record.status, source=claim_source),
                    Claim(
                        field="total_usd",
                        value=round(sum(
                            line.quantity_ordered * line.unit_price_usd
                            for line in record.lines
                        ), 2),
                        source=claim_source,
                    ),
                ],
                label=record.po_number,
                provenance=f"landed by {run_id}",
            )
            counts["orders"] += 1

            outcome = resolve(
                self.graph, "Vendor", [record.vendor_name], thresholds,
                source=source, source_external_id=record.vendor_external_id,
            )
            if outcome.disposition == "linked":
                graph.link(
                    "ordered_from", order.id, outcome.object_id,
                    confidence=outcome.score, method="exact",
                    provenance=outcome.reason,
                )
                counts["vendor_linked"] += 1
            else:
                counts["vendor_unresolved"] += 1

            for ordinal, line in enumerate(record.lines, start=1):
                line_obj = graph.upsert(
                    "PurchaseOrderLine", f"{order.id}#{ordinal}",
                    [
                        Claim(field="sku", value=line.sku, source=claim_source),
                        Claim(field="description", value=line.description, source=claim_source),
                        Claim(field="quantity_ordered", value=line.quantity_ordered, source=claim_source),
                        Claim(field="unit_price_usd", value=line.unit_price_usd, source=claim_source),
                        Claim(
                            field="amount_usd",
                            value=round(line.quantity_ordered * line.unit_price_usd, 2),
                            source=claim_source,
                        ),
                        Claim(field="po_number", value=record.po_number, source=claim_source),
                    ],
                    label=f"{record.po_number} line {ordinal}",
                    provenance=f"landed by {run_id}",
                )
                graph.link(
                    "has_po_line", order.id, line_obj.id,
                    props={"ordinal": ordinal}, provenance=run_id,
                )
                counts["lines"] += 1
        return counts

    # -- receiving reports --------------------------------------------------

    def _receiving_body(self, ctx: RunContext, inputs: dict) -> dict:
        source = inputs["source"]
        records = [ReceivingRecord.model_validate(r) for r in inputs["records"]]
        ctx.event(
            "t1", "SourceDelivered",
            f"{len(records)} receiving report(s) from `{source}`", kind="trigger",
        )
        landing_writer = self.graph.writer(ctx.record.id, "s1")
        landed = ctx.step(
            "s1", "land-records", "sync",
            fn=lambda: self._land(
                source, "rr", [r.model_dump() for r in records], landing_writer,
            ),
            writes=lambda _out: landing_writer.refs,
            summary=lambda out: (
                f"{out['landed']} landed, {out['unchanged']} unchanged"
            ),
        )
        apply_writer = self.graph.writer(ctx.record.id, "a1")
        applied = ctx.step(
            "a1", "apply-receiving-reports", "action",
            fn=lambda: self._apply_receipts(
                source, records, ctx.record.id, apply_writer
            ),
            writes=lambda _out: apply_writer.refs,
            summary=lambda out: (
                f"{out['reports']} report(s) and {out['lines']} line(s) written · "
                f"{out['po_linked']} booked against a PO · {out['po_missing']} "
                "with no PO in the graph yet"
            ),
        )
        ctx.event("e1", "ReceivingReportsSynced", f"{len(records)} record(s)")
        return {
            "source": source, "watermark": inputs.get("watermark", ""),
            "kind": "receiving-reports", "records": len(records),
            **landed, **applied,
        }

    # -- workforce ----------------------------------------------------------

    def _workforce_body(self, ctx: RunContext, inputs: dict) -> dict:
        """Land the org chart. A bind mapping, so nothing is reified.

        The HRIS row's key is an exact identifier — a work email, a team slug
        — so there is nothing for a steward to adjudicate and no landing
        record to keep. Claims go straight onto the entity carrying `hris:` as
        their authority, and the bind columns (`manager_email`, `team_slug`,
        `parent_slug`, `lead_email`) resolve to links in one pass once both
        ends exist. Batch feed: unresolved references escalate once, as an
        aggregate, never per row.
        """
        source = inputs["source"]
        teams = [TeamRecord.model_validate(t) for t in inputs.get("teams", [])]
        employees = [
            EmployeeRecord.model_validate(e) for e in inputs.get("employees", [])
        ]
        ctx.event(
            "t1", "SourceDelivered",
            f"{len(teams)} team(s) and {len(employees)} employee(s) from "
            f"`{source}`",
            kind="trigger",
        )

        team_writer = self.graph.writer(ctx.record.id, "a1")
        team_out = ctx.step(
            "a1", "apply-teams", "action",
            fn=lambda: self._apply_teams(source, teams, ctx.record.id, team_writer),
            writes=lambda _out: team_writer.refs,
            summary=lambda out: f"{out['teams']} team(s) written",
        )
        people_writer = self.graph.writer(ctx.record.id, "a2")
        people_out = ctx.step(
            "a2", "apply-employees", "action",
            fn=lambda: self._apply_employees(
                source, employees, ctx.record.id, people_writer
            ),
            writes=lambda _out: people_writer.refs,
            summary=lambda out: f"{out['employees']} employee(s) written",
        )
        chart_writer = self.graph.writer(ctx.record.id, "a3")
        chart_out = ctx.step(
            "a3", "wire-org-chart", "action",
            fn=lambda: self._wire_org_chart(
                teams, employees, chart_writer
            ),
            writes=lambda _out: chart_writer.refs,
            summary=lambda out: (
                f"{out['member_of']} membership(s), {out['reports_to']} "
                f"reporting line(s), {out['subteam_of']} nesting(s), "
                f"{out['led_by']} lead(s)"
                + (
                    f" · {out['unresolved']} reference(s) unresolved"
                    if out["unresolved"]
                    else ""
                )
            ),
        )
        if chart_out["unresolved"]:
            # One Task for the delivery, not one per row: a batch feed that
            # suspended on every dangling manager email would never finish.
            ctx.event(
                "e2", "WorkforceReferencesUnresolved",
                f"{chart_out['unresolved']} reference(s) named nobody in the "
                "graph",
            )
        ctx.event(
            "e1", "WorkforceSynced",
            f"{len(teams)} team(s), {len(employees)} employee(s)",
        )
        return {
            "source": source, "watermark": inputs.get("watermark", ""),
            "kind": "workforce",
            "records": len(teams) + len(employees),
            "landed": len(teams) + len(employees),
            **team_out, **people_out, **chart_out,
        }

    def _apply_teams(
        self,
        source: str,
        records: list[TeamRecord],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        for record in records:
            graph.upsert(
                "Team", record.slug,
                [
                    Claim(field="slug", value=record.slug, source=claim_source),
                    Claim(field="name", value=record.name, source=claim_source),
                    Claim(field="mission", value=record.mission,
                          source=claim_source),
                ],
                label=record.name or record.slug,
                provenance=f"synced by {run_id}",
            )
        return {"teams": len(records)}

    def _apply_employees(
        self,
        source: str,
        records: list[EmployeeRecord],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        for record in records:
            graph.upsert(
                "Employee", record.email,
                [
                    Claim(field="email", value=record.email, source=claim_source),
                    Claim(field="name", value=record.name, source=claim_source),
                    Claim(field="title", value=record.title, source=claim_source),
                    Claim(field="location", value=record.location,
                          source=claim_source),
                    Claim(field="start_date", value=record.start_date,
                          source=claim_source),
                ],
                label=record.name or record.email,
                provenance=f"synced by {run_id}",
            )
        return {"employees": len(records)}

    def _wire_org_chart(
        self,
        teams: list[TeamRecord],
        employees: list[EmployeeRecord],
        writer: GraphWriter | None = None,
    ) -> dict:
        graph = writer if writer is not None else self.graph
        counts = {
            "member_of": 0, "reports_to": 0, "subteam_of": 0, "led_by": 0,
            "unresolved": 0,
        }

        def employee(email: str):
            return self.graph.get(object_id("Employee", email)) if email else None

        def team(slug: str):
            return self.graph.get(object_id("Team", slug)) if slug else None

        for record in employees:
            person = employee(record.email)
            if person is None:
                counts["unresolved"] += 1
                continue
            if record.team_slug:
                unit = team(record.team_slug)
                if unit is None:
                    counts["unresolved"] += 1
                else:
                    graph.link("member_of", person.id, unit.id,
                               method="declared", provenance="hris team_slug")
                    counts["member_of"] += 1
            if record.manager_email:
                manager = employee(record.manager_email)
                if manager is None:
                    counts["unresolved"] += 1
                else:
                    graph.link("reports_to", person.id, manager.id,
                               method="declared",
                               provenance="hris manager_email")
                    counts["reports_to"] += 1
        for record in teams:
            unit = team(record.slug)
            if unit is None:
                counts["unresolved"] += 1
                continue
            if record.parent_slug:
                parent = team(record.parent_slug)
                if parent is None:
                    counts["unresolved"] += 1
                else:
                    graph.link("subteam_of", unit.id, parent.id,
                               method="declared", provenance="hris parent_slug")
                    counts["subteam_of"] += 1
            if record.lead_email:
                lead = employee(record.lead_email)
                if lead is None:
                    counts["unresolved"] += 1
                else:
                    graph.link("led_by", unit.id, lead.id,
                               method="declared", provenance="hris lead_email")
                    counts["led_by"] += 1
        return counts

    def _apply_receipts(
        self,
        source: str,
        records: list[ReceivingRecord],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        counts = {"reports": 0, "lines": 0, "po_linked": 0, "po_missing": 0}
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        for record in records:
            report = graph.upsert(
                "ReceivingReport", record.receipt_number,
                [
                    Claim(field="receipt_number", value=record.receipt_number, source=claim_source),
                    Claim(field="received_date", value=record.received_date, source=claim_source),
                    Claim(field="po_number", value=record.po_number, source=claim_source),
                    Claim(field="status", value=record.status, source=claim_source),
                ],
                label=record.receipt_number,
                provenance=f"landed by {run_id}",
            )
            counts["reports"] += 1

            if record.po_number:
                order = self.graph.get(object_id("PurchaseOrder", record.po_number))
                if order is not None:
                    graph.link(
                        "receives_against", report.id, order.id,
                        props={"po_number": record.po_number}, provenance=run_id,
                    )
                    counts["po_linked"] += 1
                else:
                    # Receiving can legitimately arrive before the PO feed does.
                    # The report lands anyway and the edge is made when the PO
                    # shows up — nothing is lost, and nothing is invented.
                    counts["po_missing"] += 1

            for ordinal, line in enumerate(record.lines, start=1):
                line_obj = graph.upsert(
                    "ReceivingReportLine", f"{report.id}#{ordinal}",
                    [
                        Claim(field="sku", value=line.sku, source=claim_source),
                        Claim(field="description", value=line.description, source=claim_source),
                        Claim(field="quantity_received", value=line.quantity_received, source=claim_source),
                        Claim(field="condition", value=line.condition, source=claim_source),
                        Claim(field="receipt_number", value=record.receipt_number, source=claim_source),
                    ],
                    label=f"{record.receipt_number} line {ordinal}",
                    provenance=f"landed by {run_id}",
                )
                graph.link(
                    "has_receiving_line", report.id, line_obj.id,
                    props={"ordinal": ordinal}, provenance=run_id,
                )
                counts["lines"] += 1
        return counts

    # -- landing ------------------------------------------------------------

    def _land(
        self,
        source: str,
        prefix: str,
        payloads: list[dict],
        writer: GraphWriter | None = None,
    ) -> dict:
        """Keep every row exactly as delivered, and notice re-deliveries.

        The `unchanged` count is echo suppression in its simplest honest form:
        a byte-identical row we already hold is recorded as seen and nothing
        downstream is disturbed.
        """
        landed = unchanged = 0
        graph = writer if writer is not None else self.graph
        claim_source = f"{_authority(source)}:{source}"
        for payload in payloads:
            external = str(
                payload.get("external_id")
                or payload.get("po_number")
                or payload.get("receipt_number")
                or ""
            )
            key = f"{source}:{external}"
            digest = _payload_hash(payload)
            existing = self.graph.get(object_id("SourceRecord", key))
            if existing is not None and existing.props.get("payload_hash") == digest:
                unchanged += 1
                continue
            graph.upsert(
                "SourceRecord", key,
                [
                    Claim(field="source", value=source, source=claim_source),
                    Claim(field="external_id", value=external, source=claim_source),
                    Claim(field="payload_hash", value=digest, source=claim_source),
                    Claim(
                        field="payload",
                        value=json.dumps(payload, sort_keys=True, default=str),
                        source=claim_source,
                    ),
                    Claim(field="landed_at", value=utcnow().isoformat(), source=claim_source),
                    Claim(field="record_kind", value=prefix, source=claim_source),
                ],
                label=key,
            )
            landed += 1
        return {"landed": landed, "unchanged": unchanged}

    # -- the steward's decision --------------------------------------------

    def apply_match_decision(self, task: Task) -> dict | None:
        """Assert (or refuse) the link a steward just ruled on.

        This is the resolution stream of the Task queue becoming real: nothing
        was asserted while the Task was open, so the decision is the first
        write. `approved` links to the top candidate, `corrected` links to
        whichever id the steward names, `rejected` mints a new entity.
        """
        if task.kind != "match_review" or task.decision is None:
            return None
        source_record = self.graph.get(task.case_ref)
        if source_record is None:
            return None
        payload = json.loads(str(source_record.props.get("payload") or "{}"))
        record = VendorRecord.model_validate(payload) if payload else None
        if record is None:
            return None

        verdict = task.decision.verdict
        target_id = str(task.decision.payload.get("object_id") or "")
        if verdict == "approved" and not target_id:
            target_id = next(
                (
                    ref.split("/", 1)[1] for ref in task.refs
                    if ref.startswith("graph:Vendor/")
                ),
                "",
            )
        claim_source = f"human:{task.decision.decided_by}"

        if verdict in ("approved", "corrected") and target_id:
            vendor = self.graph.get(target_id)
            if vendor is None:
                return None
            self.graph.upsert(
                "Vendor", vendor.natural_key,
                [
                    Claim(field="aliases", value=[record.name], source=claim_source),
                    Claim(field="name", value=str(vendor.props.get("name") or vendor.label),
                          source=claim_source),
                ],
                label=vendor.label,
            )
            link = self.graph.link(
                "identified_as", vendor.id, source_record.id,
                props={
                    "source": record and str(source_record.props.get("source")),
                    "external_id": record.external_id,
                    "decided_by": task.decision.decided_by,
                },
                confidence=1.0, method="human",
                provenance=f"steward decision on {task.id}: {task.decision.note}",
            )
            log.info(
                "Steward linked a source record",
                extra={
                    "taskId": task.id, "vendorId": vendor.id,
                    "linkId": link.id, "actor": task.decision.decided_by,
                },
            )
            return {"action": "linked", "vendor_id": vendor.id, "link_id": link.id}

        vendor = self.graph.upsert(
            "Vendor", record.name,
            [
                Claim(field="name", value=record.name, source=claim_source),
                Claim(field="status", value="onboarding", source=claim_source),
                Claim(field="tax_id", value=record.tax_id, source=claim_source),
            ],
            label=record.name,
            provenance=f"minted by steward decision on {task.id}",
        )
        self.graph.link(
            "identified_as", vendor.id, source_record.id,
            props={"external_id": record.external_id,
                   "decided_by": task.decision.decided_by},
            confidence=1.0, method="human",
            provenance=f"steward said this is a new vendor ({task.id})",
        )
        log.info(
            "Steward minted a new vendor",
            extra={"taskId": task.id, "vendorId": vendor.id},
        )
        return {"action": "created", "vendor_id": vendor.id}

    # -- the Sources lens ---------------------------------------------------

    def sources(self) -> list[SourceStatus]:
        """One row per (source, kind), assembled from the sync runs themselves.

        No separate sync-state store: the runs already record what was
        delivered, when, and how it went, and inventing a second place to keep
        that would guarantee the two disagree.
        """
        if self.engine is None:
            return []
        by_key: dict[tuple[str, str], SourceStatus] = {}
        flows = {
            VENDOR_FLOW_ID: "vendors",
            PO_FLOW_ID: "purchase-orders",
            RECEIVING_FLOW_ID: "receiving-reports",
            WORKFORCE_FLOW_ID: "workforce",
        }
        for flow_id, kind in flows.items():
            for run in sorted(
                self.engine.list(flow_id=flow_id), key=lambda r: r.started_at_iso
            ):
                source = str(run.inputs.get("source") or run.outputs.get("source") or "")
                if not source:
                    continue
                key = (source, kind)
                status = by_key.setdefault(key, SourceStatus(
                    source=source, kind=kind, flow_id=flow_id,
                ))
                status.deliveries += 1
                status.last_run_id = run.id
                status.last_run_at_iso = run.started_at_iso
                status.last_status = run.status
                status.watermark = str(
                    run.inputs.get("watermark") or status.watermark
                )
                outputs = run.outputs or {}
                status.records_landed += int(outputs.get("landed", 0) or 0)
                status.unchanged += int(outputs.get("unchanged", 0) or 0)
                status.linked += int(outputs.get("linked", 0) or 0)
                status.created += int(outputs.get("created", 0) or 0)
                status.awaiting_steward += int(outputs.get("awaiting_steward", 0) or 0)
                if run.error:
                    status.last_error = run.error
        return sorted(by_key.values(), key=lambda s: (s.source, s.kind))


def _authority(source: str) -> str:  # noqa: D401
    """Map a source name onto a precedence bucket from the ontology.

    Precedence is declared over authorities (`erp`, `procurement`, …), not over
    every individual feed name, so a new ERP instance inherits the ERP's
    standing without an ontology edit.
    """
    lowered = source.lower()
    if "erp" in lowered or "netsuite" in lowered or "sap" in lowered:
        return "erp"
    if "procure" in lowered or "coupa" in lowered or "ariba" in lowered:
        return "procurement"
    if "wms" in lowered or "receiv" in lowered:
        return "procurement"
    if "hris" in lowered or "workday" in lowered or "bamboo" in lowered:
        return "hris"
    return "flow"


def build_sync_service(
    graph: GraphService, knowledge, tasks: TaskService, engine: RunEngine | None
) -> SyncService:
    return SyncService(graph, knowledge, tasks, engine)
