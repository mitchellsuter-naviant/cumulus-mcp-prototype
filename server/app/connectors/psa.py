"""The delivery feed: engagements, their phases, and where the hours went.

The sixth app in the estate and the first one that is not a system of record
for anything the other five hold. A PSA is where a delivery organization keeps
its own week: projects, the phases they are broken into, the tasks a
coordinator writes down, and the hours people book. Its conventions are
deliberately unlike the rest of the estate — opaque integer ids, a
user-facing `ENG-0100` code, **and people as free-typed display names**. Nobody
in that app knows `S. Hoffmann` is Stacy Hoffmann in the HRIS, and nothing here
pretends otherwise: those strings land as strings, on the type's declared
"observation awaiting promotion" terms, and the join — if anybody ever wants it
— is a fuzzy match with a steward at the end of it, not something a batch feed
may assert.

**Three cuts, and the reasons they are where they are.**

*Clients reify onto the counterparty book*, through the very same
`_apply_counterparty` the CRM and the ERP feeds go through. That sharing is not
tidiness: three systems that disagree about what a company is *called* must not
also disagree about what counts as the same company. What this feed adds is a
third voice, and the most informal one in the estate — `Apex Mfg`, `Kestrel
CU`, whatever the coordinator said out loud — which is why `psa` ranks last
among the systems in `PRECEDENCE` and why the interesting outcome here is not a
link but a *proposal*: a nickname usually scores below the resolver's floor,
mints its own Company, and is caught by the identity sweep on the domain its
contact's address carries.

*Projects and phases are mirrored; tasks are not.* A phase is a part of its
project in the strict sense and is the smallest unit any question Cumulus has
of Delivery is asked at — is this engagement on track, where are the hours
going. A task is how a coordinator organizes a week: the estate's twelve
finished projects carry none at all, because nobody archived a task list, they
just stopped opening it. So task rows travel on the wire as a **lookup table
and nothing else** — three columns, no name, no assignee, no status — so that
an hour booked against a task can be attributed to the phase that task belongs
to. Nothing about a task survives the run.

*Time entries are summed, never landed.* Two hundred and sixty-six rows for
eighteen projects at demo scale is tens of thousands at real scale, and the
questions they answer — budget against actual, who is on what — are sums. This
is the case `mappings.py` names for `bind`: a million readings must not become
a million objects. The custody line is drawn on the wire itself, where it
cannot be crossed later: `TimeEntryRecord` has no `notes` column, so what a
technician wrote about a customer's site never arrives at all.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ..graph import Claim, GraphWriter, normalize_key
from ..identity_sweep import _domain
from ..ontology import names as onto
from ..resolution import (
    ResolutionOutcome,
    ResolutionThresholds,
    read_resolution_thresholds,
    resolve,
)
from ..runs import RunContext
from .sync import (
    PSA_EVENT,
    PSA_FLOW_ID,
    ProjectPhaseRecord,
    ProjectRecord,
    ProjectTaskRecord,
    PsaClientRecord,
    SyncService,
    TimeEntryRecord,
    _authority,
    _slug,
)
from .commerce import _CompanyIndex, _apply_counterparty, _count

log = logging.getLogger("cumulus.sync.psa")


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


#: The host out of a contact address, or "". A mapping decision and therefore
#: the flow's: the connector delivers the address the PSA holds and has no
#: opinion about what can be read off it. Borrowed from the sweep rather than
#: reimplemented because public mail hosts are not counterparty domains — a
#: client whose only contact is on gmail tells us nothing about the firm — and
#: two copies of that list would be two things able to disagree.
_domain_of = _domain


def _project_key(source: str, record: ProjectRecord) -> str:
    """`Project`'s declared natural key: source + the PSA's project code.

    Source-scoped for the reason an Opportunity id is: two delivery systems can
    both issue `ENG-0100` and mean different engagements. The internal record
    id is the fallback rather than the key, because the code is what everybody
    in the customer's building actually says.
    """
    return f"{source}:{record.project_code or record.external_id}"


# ---------------------------------------------------------------------------
# psa-sync
# ---------------------------------------------------------------------------


def psa_body(service: SyncService, ctx: RunContext, inputs: dict) -> dict:
    """Clients reify onto the counterparty book; the delivery book binds."""
    source = inputs["source"]
    watermark = inputs.get("watermark", "")
    clients = [
        PsaClientRecord.model_validate(c) for c in inputs.get("clients", [])
    ]
    projects = [
        ProjectRecord.model_validate(p) for p in inputs.get("projects", [])
    ]
    phases = [
        ProjectPhaseRecord.model_validate(p) for p in inputs.get("phases", [])
    ]
    tasks = [
        ProjectTaskRecord.model_validate(t) for t in inputs.get("tasks", [])
    ]
    entries = [
        TimeEntryRecord.model_validate(t) for t in inputs.get("time_entries", [])
    ]

    ctx.event(
        "t1", PSA_EVENT,
        f"{len(clients)} client(s), {len(projects)} project(s), "
        f"{len(phases)} phase(s) and {len(entries)} time entr(y/ies) from "
        f"`{source}`" + (f" · watermark {watermark}" if watermark else ""),
        kind="trigger",
    )

    thresholds_raw = ctx.step(
        "x1", "read-resolution-policy", "transform",
        fn=lambda: read_resolution_thresholds(service.knowledge).model_dump(),
        summary=lambda out: (
            f"{out['source_ref']}: auto-link ≥ {out['auto_link_at']:.2f} · "
            f"steward review ≥ {out['review_from']:.2f}"
        ),
    )
    thresholds = ResolutionThresholds.model_validate(thresholds_raw)
    ctx.note_refs(thresholds.source_ref)

    landing = service._act(ctx, "land-source-records", "s1")
    landed = ctx.step(
        "s1", "land-records", "sync",
        # Only the clients land. The projects, phases and hours below bind —
        # an `ENG-` code is exact and nobody adjudicates a project's identity —
        # so there is no landing record worth keeping for them and no steward
        # to ask.
        fn=lambda: landing.commit(lambda w: service._land(
            source, "company", [c.model_dump() for c in clients], w,
        )),
        writes=lambda _out: landing.writes,
        summary=lambda out: (
            f"{out['landed']} landed, {out['unchanged']} unchanged "
            f"(byte-identical redelivery) in `source-landing`"
        ),
    )

    resolutions = ctx.step(
        "z1", "resolve-records", "resolve",
        fn=lambda: [
            resolve(
                service.graph, onto.COMPANY, [c.name], thresholds,
                source=source, source_external_id=c.external_id,
            ).model_dump()
            for c in clients
        ],
        summary=lambda out: (
            f"{_count(out, 'linked')} client(s) linked to a known "
            f"counterparty · {_count(out, 'new')} new · "
            f"{_count(out, 'review')} to a steward"
        ),
        refs=[thresholds.source_ref],
    )

    index = _CompanyIndex(service.graph, source)
    apply = service._act(ctx, "resolve-identities", "a1")
    applied = ctx.step(
        "a1", "apply-resolutions", "action",
        fn=lambda: apply.commit(lambda w: _apply_clients(
            service, w, source, clients,
            [ResolutionOutcome.model_validate(r) for r in resolutions],
            ctx.record.id, index,
        )),
        writes=lambda _out: apply.writes,
        summary=lambda out: (
            f"{out['linked']} claim(s) onto known counterparties · "
            f"{out['created']} minted from a nickname · "
            f"{out['awaiting_steward']} Task(s) raised"
        ),
    )

    book = service._act(ctx, "apply-source-records", "a2")
    delivery = ctx.step(
        "a2", "apply-delivery-book", "action",
        fn=lambda: book.commit(lambda w: _apply_delivery_book(
            service, w, source, projects, phases, tasks, entries,
            ctx.record.id, index,
        )),
        writes=lambda _out: book.writes,
        summary=lambda out: (
            f"{out['projects']} project(s) and {out['phases']} phase(s) · "
            f"{out['delivered_for']} bound to a counterparty · "
            f"{out['hours_logged']:.1f}h from {out['time_entries']} entr"
            f"(y/ies)"
            + (
                f" · {out['hours_unattributed']:.1f}h booked past every phase"
                if out["hours_unattributed"] else ""
            )
            + (
                f" · {out['client_unresolved']} project(s) naming a client "
                "this delta could not place"
                if out["client_unresolved"] else ""
            )
        ),
    )

    ctx.event(
        "e1", "PsaBookSynced",
        f"{len(clients)} client(s), {len(projects)} project(s), "
        f"{len(phases)} phase(s) from {source}",
    )
    return {
        "source": source, "watermark": watermark, "kind": "psa-book",
        "records": len(clients) + len(projects) + len(phases) + len(entries),
        **landed, **applied, **delivery,
    }


def _apply_clients(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    records: list[PsaClientRecord],
    outcomes: list[ResolutionOutcome],
    run_id: str,
    index: _CompanyIndex,
) -> dict:
    """The delivery org's client list, onto the book the sell side wrote.

    The claims are thin because the source is: a name, its normalization, and —
    where somebody filled the field in — the domain out of the contact's
    address. That last one is the whole reason this feed is worth anything to
    identity: `Apex Mfg` against `Apex Manufacturing` is a name a resolver will
    not join, and `apexmfg.example.com` on both sides is what lets the sweep
    say so with evidence.
    """
    counts = {"linked": 0, "created": 0, "awaiting_steward": 0, "skipped": 0}
    claim_source = f"{_authority(source)}:{source}"
    for record, outcome in zip(records, outcomes):
        claims = [
            Claim(field="name", value=record.name, source=claim_source),
            Claim(field="slug", value=normalize_key(record.name),
                  source=claim_source),
        ]
        domain = _domain_of(record.contact_email)
        if domain:
            claims.append(
                Claim(field="domain", value=domain, source=claim_source)
            )
        _apply_counterparty(
            service, graph, source=source, flow_id=PSA_FLOW_ID,
            external_id=record.external_id, name=record.name, claims=claims,
            outcome=outcome, run_id=run_id, index=index, counts=counts,
        )
    return counts


def _apply_delivery_book(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    projects: list[ProjectRecord],
    phases: list[ProjectPhaseRecord],
    tasks: list[ProjectTaskRecord],
    entries: list[TimeEntryRecord],
    run_id: str,
    index: _CompanyIndex,
) -> dict:
    """Every project, its phases, its counterparty, and its hours.

    One step rather than three, because the three are one write: a phase cannot
    exist without the project it is part of, and an hour has nowhere to go
    until both do. Splitting them would produce a journal that reads as more
    work than happened and a half-applied batch on a refusal.
    """
    counts = {
        "projects": 0, "phases": 0, "delivered_for": 0, "has_phase": 0,
        "client_unresolved": 0, "phase_unplaced": 0, "project_unkeyed": 0,
        "time_entries": len(entries), "hours_logged": 0.0,
        "hours_billable": 0.0, "hours_unattributed": 0.0,
    }
    claim_source = f"{_authority(source)}:{source}"

    # The bridge, and the only thing a task row is here for: an hour names a
    # task, a task names a phase, and this is the translation. In-batch only —
    # unlike a folder's parent there is nothing worth walking back to the
    # graph for, because a task is never stored and a delta that carried the
    # hours carries the tasks beside them.
    phase_of_task = {
        t.external_id: t.phase_external_id
        for t in tasks if t.external_id and t.phase_external_id
    }

    project_hours: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    phase_hours: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    people: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        booked = project_hours[entry.project_external_id]
        booked[0] += entry.hours
        if entry.billable:
            booked[1] += entry.hours
        if entry.person:
            people[entry.project_external_id].add(entry.person)
        phase_id = phase_of_task.get(entry.task_external_id, "")
        if not phase_id:
            # Booked straight to the project, or against a task this delivery
            # did not carry. Counted rather than spread evenly over the phases,
            # which would invent an attribution nobody made: the live estate
            # has a whole finished engagement whose hours were logged before
            # anybody wrote a task list.
            counts["hours_unattributed"] += entry.hours
            continue
        stage = phase_hours[phase_id]
        stage[0] += entry.hours
        if entry.billable:
            stage[1] += entry.hours
    counts["hours_logged"] = round(
        sum(booked[0] for booked in project_hours.values()), 2
    )
    counts["hours_billable"] = round(
        sum(booked[1] for booked in project_hours.values()), 2
    )
    counts["hours_unattributed"] = round(counts["hours_unattributed"], 2)

    landed: dict[str, str] = {}
    for record in projects:
        if not (record.project_code or record.external_id):
            # The key is the code, or the id where a source issues none. A row
            # with neither names no engagement, and landing it under a bare
            # `psa-book:` would collide every such row into one ghost project.
            # `project` and not `name`: `extra={"name": ...}` collides with
            # `LogRecord.name` and raises inside the logging call, which turns
            # a skipped row into a failed run.
            log.info("PSA project row has no code and no id",
                     extra={"source": source, "project": record.name})
            counts["project_unkeyed"] += 1
            continue
        company_id = index.lookup(record.client_external_id)
        company = service.graph.get(company_id) if company_id else None
        booked = project_hours.get(record.external_id)
        claims = [
            Claim(field="project_code", value=record.project_code,
                  source=claim_source),
            Claim(field="external_id", value=record.external_id,
                  source=claim_source),
            # Carried for the same reason `Folder` carries one: the key is
            # source-scoped, so the lookup that places a phase against a
            # project an earlier delta landed has to be too.
            Claim(field="source", value=source, source=claim_source),
            Claim(field="name", value=record.name, source=claim_source),
            Claim(field="status", value=_slug(record.status),
                  source=claim_source),
            Claim(field="project_manager", value=record.project_manager,
                  source=claim_source),
            Claim(field="start_date", value=record.start_date,
                  source=claim_source),
            Claim(field="target_end_date", value=record.target_end_date,
                  source=claim_source),
            Claim(field="budget_hours", value=record.budget_hours,
                  source=claim_source),
            # The source's own running total, not ours. The PSA sums every
            # time entry it holds on each read, so this number is whole even
            # when the delivery that carried it is a one-project delta — which
            # is exactly when our own sum below would understate it.
            Claim(field="actual_hours", value=record.actual_hours,
                  source=claim_source),
        ]
        if company is not None:
            claims.append(Claim(
                field="company_name",
                value=str(company.props.get("name") or company.label),
                source=claim_source,
            ))
        if booked is not None:
            # Only where this delivery carried hours for this project. A delta
            # about one renamed project must not overwrite a billable total
            # with the zero it happens to have seen.
            claims.append(Claim(field="billable_hours", value=round(booked[1], 2),
                                source=claim_source))
        contributors = sorted(people.get(record.external_id, ()))
        if contributors:
            claims.append(Claim(field="contributors", value=contributors,
                                source=claim_source))

        project = graph.upsert(
            onto.PROJECT, _project_key(source, record), claims,
            label=record.name or record.project_code or record.external_id,
            provenance=f"synced by {run_id}",
        )
        landed[record.external_id] = project.id
        counts["projects"] += 1

        if company is not None:
            graph.link(
                "delivered_for", project.id, company.id,
                method="declared", provenance="psa clientId",
            )
            counts["delivered_for"] += 1
        elif record.client_external_id:
            # A client this delta did not carry and no earlier one landed —
            # or one still sitting with a steward, which is the common case
            # here and the right one: nothing was asserted about it, so there
            # is nothing for the project to point at yet.
            counts["client_unresolved"] += 1

    for record in phases:
        project_id = landed.get(record.project_external_id) or _known_project(
            service, source, record.project_external_id
        )
        if not project_id:
            # A phase whose project neither this delta nor the graph holds.
            # Landing it would produce a part with no owner, which the TBox
            # sweep refuses anyway — so it is counted instead.
            counts["phase_unplaced"] += 1
            continue
        project = service.graph.get(project_id)
        stage = phase_hours.get(record.external_id)
        claims = [
            Claim(field="external_id", value=record.external_id,
                  source=claim_source),
            Claim(field="name", value=record.name, source=claim_source),
            Claim(field="sequence", value=record.sequence, source=claim_source),
            Claim(field="status", value=_slug(record.status),
                  source=claim_source),
            Claim(field="target_date", value=record.target_date,
                  source=claim_source),
            Claim(
                field="project_code",
                value=str(project.props.get("project_code") or "")
                if project is not None else "",
                source=claim_source,
            ),
        ]
        if stage is not None:
            claims.append(Claim(field="logged_hours", value=round(stage[0], 2),
                                source=claim_source))
            claims.append(Claim(field="billable_hours",
                                value=round(stage[1], 2), source=claim_source))
        phase = graph.upsert(
            onto.PROJECT_PHASE, _phase_key(project_id, record.external_id), claims,
            label=record.name or record.external_id,
            provenance=f"synced by {run_id}",
        )
        graph.link(
            "has_phase", project_id, phase.id,
            props={"sequence": record.sequence}, provenance=run_id,
        )
        counts["phases"] += 1
        counts["has_phase"] += 1
    return counts


def _phase_key(project_id: str, external_id: str) -> str:
    """A part's key embeds its owner's — the weak-entity rule, enforced by
    `GraphService.check_invariants`. The PSA's own phase id is the
    discriminator rather than the sequence, because a plan that gets reordered
    should move a phase, not rename it into a different one."""
    return f"{project_id}#{external_id}"


def _known_project(service: SyncService, source: str, external_id: str) -> str:
    """A project this delta did not carry but an earlier one landed.

    Asked of the store by the property the way `connectors.commerce` asks for an
    item, rather than reading the whole delivery book to place one phase — and
    scoped by source, because a PSA id is only an id inside the PSA that
    minted it.
    """
    if not external_id:
        return ""
    found = service.graph.find(
        onto.PROJECT,
        prop_equals={"external_id": external_id, "source": source},
        limit=1,
    )
    return found[0].id if found else ""
