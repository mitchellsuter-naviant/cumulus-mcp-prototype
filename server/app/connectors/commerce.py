"""The commerce feeds: the counterparty book, and what the business sold.

Two flow bodies — `crm-sync` and `sales-sync` — kept out of `sync.py` because
that file is already the wire shapes, the service, the landing step and five
flows, and a reader looking for the vendor sync should not have to scroll past
the whole sell side to find it. Everything structural still lives there: these
register through `SyncService.__init__`, write through `service._act` so the
Gateway records every claim, and land rows through `service._land` so a
redelivery is recognized here exactly as it is everywhere else.

**Why these two are one story.** The CRM and the ERP are both describing the
same five or six companies, and they spell them differently on purpose: a rep
types `Northwind Traders` and a contract says `Northwind Traders Inc.`. Neither
row is the company. So both feeds *reify* their counterparty rows into
SourceRecords, resolve them against one `Company` book, and attach the landed
row as a reversible `company_identified_as` link. When they disagree about a
commercial fact — payment terms, an address — nothing is overwritten: both
claims are retained and the precedence order (`erp` above `crm`) decides what
resolves. That retained disagreement *is* the demo; discarding the loser would
delete the only evidence that two systems were ever out of step.

**Where judgment is and is not applied.** A company arrives as a name, which is
fuzzy, so it goes through `resolve()` and an ambiguous one raises a Task in the
feed's declared steward queue and asserts nothing. A person arrives as an email
address, which is exact, so nothing is adjudicated — and a contact with *no*
address mints its own Person under `source:external id` rather than being
matched on a spelling. Two humans who share a name are two humans; the merge,
if there is one, is proposed later with evidence a human can read (B5).

Opportunities, items and orders bind rather than reify: an opportunity id, a
SKU and an order number are exact identifiers, so there is no landing record
worth keeping and no steward to ask. What can fail there is a *reference* — an
order for a customer this delta did not carry, a line naming an item nobody has
seen — and a batch feed never suspends for one of those. They are counted on
the aggregate and reported on the run, never minted into existence.
"""

from __future__ import annotations

import logging

from .graph import Claim, GraphService, GraphWriter, normalize_key, object_id
from .mappings import steward_queue_for
from .models import utcnow
from .resolution import (
    ResolutionOutcome,
    ResolutionThresholds,
    read_resolution_thresholds,
    resolve,
)
from .runs import RunContext
from .sync import (
    CRM_EVENT,
    CRM_FLOW_ID,
    SALES_EVENT,
    SALES_FLOW_ID,
    AccountRecord,
    ContactRecord,
    CustomerRecord,
    ItemRecord,
    OpportunityRecord,
    SalesOrderRecord,
    SyncService,
    _authority,
    _slug,
)
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.sync.commerce")


def register(service: SyncService) -> None:
    """Bind both bodies to the engine `SyncService` was handed."""
    if service.engine is None:
        return
    service.engine.register(
        CRM_FLOW_ID, lambda ctx, inputs: crm_body(service, ctx, inputs)
    )
    service.engine.register(
        SALES_FLOW_ID, lambda ctx, inputs: sales_body(service, ctx, inputs)
    )


# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------


def _item_type(value: str) -> str:
    """`Inventory Item` → `inventory`, `Service` → `service`."""
    text = (value or "").strip()
    if text.lower().endswith(" item"):
        text = text[: -len(" item")]
    return _slug(text)


def _person_key(record: ContactRecord, source: str) -> str:
    """An address, or the row's own identity when the source carried none.

    The fallback is the ontology's declared one and it is a decision, not a
    shrug: minting under `source:external id` keeps two address-less strangers
    apart, where keying on a name would silently make them one person and
    there would be nothing left to un-merge.
    """
    email = record.email.strip().lower()
    return email or f"{source}:{record.external_id}"


def _display_name(record: ContactRecord) -> str:
    return (
        " ".join(filter(None, (record.first_name, record.last_name)))
        or record.email
        or record.external_id
    )


def _identity_method(outcome: ResolutionOutcome) -> str:
    """How this identity came to be asserted — what a reviewer needs to know.

    A minted entity was not *matched*: the record declared it, and calling
    that a fuzzy match would overstate what happened.
    """
    if outcome.disposition == "new":
        return "declared"
    return (
        "exact"
        if outcome.method in ("tax-id", "normalized-name", "source-link")
        else "fuzzy"
    )


class _CompanyIndex:
    """Company ids by the id whichever source called them, within one run.

    Two lookups, in order, because a delta legitimately arrives without the
    rows it refers to: the batch we just applied, then the landed SourceRecord
    the last delivery left behind, walked back along `company_identified_as`.
    The second is what makes an order for a customer synced last week resolve
    at all — and it is a *read* of an identity somebody already asserted, not
    a second, quieter place identity gets decided.
    """

    def __init__(self, graph: GraphService, source: str) -> None:
        self._graph = graph
        self._source = source
        self._by_external: dict[str, str] = {}

    def remember(self, external_id: str, company_id: str) -> None:
        if external_id:
            self._by_external[external_id] = company_id

    def lookup(self, external_id: str) -> str:
        if not external_id:
            return ""
        found = self._by_external.get(external_id)
        if found:
            return found
        landed = self._graph.get(
            object_id("SourceRecord", f"{self._source}:{external_id}")
        )
        if landed is None:
            return ""
        for _link, company in self._graph.neighbors(
            landed.id, "company_identified_as", "in"
        ):
            self._by_external[external_id] = company.id
            return company.id
        return ""


def _raise_company_task(
    tasks: TaskService,
    *,
    source: str,
    flow_id: str,
    external_id: str,
    name: str,
    outcome: ResolutionOutcome,
    source_record_id: str,
    run_id: str,
) -> Task:
    """One ambiguous counterparty, parked for a human. Nothing is asserted.

    The mirror of the vendor steward Task, in the queue this feed's Mapping
    names — which is the whole reason `Mapping.steward_queue` exists: deciding
    whether two Northwinds are one company is master-data work, and the person
    who does it is not the person who decides whether an invoice gets paid.
    """
    return tasks.create(Task(
        id=f"task_match_{object_id('Company', source + external_id)[-10:]}",
        kind="match_review",
        title=f"Company match needs a steward: {name}",
        summary=(
            f"`{source}` delivered {external_id} ({name}). {outcome.reason}. "
            "Nothing has been asserted: approve to link it to the top "
            "candidate, reject to mint a new company, or correct with the id "
            "of the right one."
        ),
        queue=steward_queue_for(source),
        priority="normal",
        flow_id=flow_id,
        run_id=run_id,
        case_ref=source_record_id,
        refs=[outcome.thresholds_ref] + [
            f"graph:Company/{c.object_id}" for c in outcome.candidates[:3]
        ],
        evidence=[
            TaskEvidence(label="Inbound name", value=name),
            TaskEvidence(label="Source", value=f"{source}:{external_id}"),
        ] + [
            TaskEvidence(
                label=f"Candidate {i + 1} ({c.score:.2f})",
                value=f"{c.label} — {c.reason}",
            )
            for i, c in enumerate(outcome.candidates[:3])
        ],
        proposal=TaskProposal(
            action="link-identity",
            target="company-master",
            summary=(
                f"Link {source}:{external_id} to {outcome.candidates[0].label}"
                if outcome.candidates else "Mint a new company"
            ),
        ),
        resumes="the link is asserted (or a new company minted) on decision",
        created_at_iso=utcnow().isoformat(),
    ))


def _apply_counterparty(
    service: SyncService,
    graph: GraphWriter,
    *,
    source: str,
    flow_id: str,
    external_id: str,
    name: str,
    claims: list[Claim],
    outcome: ResolutionOutcome,
    run_id: str,
    index: _CompanyIndex,
    counts: dict,
) -> None:
    """One counterparty row applied: linked, minted, or handed to a steward.

    Shared by both feeds on purpose. The CRM and the ERP disagree about what a
    company is *called* and about half its fields; they must not disagree about
    what counts as the same company, and one function is the only way to be
    sure of that.
    """
    source_record = service.graph.get(
        object_id("SourceRecord", f"{source}:{external_id}")
    )
    if source_record is None:
        # `_land` ran in the same run and could only have skipped this row for
        # want of an external id, which names no entity to resolve.
        counts["skipped"] += 1
        return

    if outcome.disposition == "review":
        _raise_company_task(
            service.tasks, source=source, flow_id=flow_id,
            external_id=external_id, name=name, outcome=outcome,
            source_record_id=source_record.id, run_id=run_id,
        )
        counts["awaiting_steward"] += 1
        return

    if outcome.disposition == "linked":
        existing = service.graph.get(outcome.object_id)
        company = graph.upsert(
            "Company", existing.natural_key if existing else name,
            claims, label=existing.label if existing else name,
            provenance=f"claims from {source} via {run_id}",
        )
        counts["linked"] += 1
    else:
        company = graph.upsert(
            "Company", name, claims, label=name,
            provenance=f"minted from {source} record {external_id} by {run_id}",
        )
        counts["created"] += 1

    graph.link(
        "company_identified_as", company.id, source_record.id,
        props={
            "source": source, "external_id": external_id,
            "score": outcome.score, "run_id": run_id,
        },
        confidence=1.0 if outcome.disposition == "new" else (outcome.score or 1.0),
        method=_identity_method(outcome),
        provenance=outcome.reason,
    )
    index.remember(external_id, company.id)


# ---------------------------------------------------------------------------
# crm-sync
# ---------------------------------------------------------------------------


def crm_body(service: SyncService, ctx: RunContext, inputs: dict) -> dict:
    """Accounts and contacts reify onto the counterparty book; deals bind."""
    source = inputs["source"]
    watermark = inputs.get("watermark", "")
    accounts = [AccountRecord.model_validate(a) for a in inputs.get("accounts", [])]
    contacts = [ContactRecord.model_validate(c) for c in inputs.get("contacts", [])]
    deals = [
        OpportunityRecord.model_validate(o)
        for o in inputs.get("opportunities", [])
    ]

    ctx.event(
        "t1", CRM_EVENT,
        f"{len(accounts)} account(s), {len(contacts)} contact(s) and "
        f"{len(deals)} opportunit(y/ies) from `{source}`"
        + (f" · watermark {watermark}" if watermark else ""),
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
        # Two kinds, one commit: they arrived in one delivery, and one Action
        # is one ledger entry. `record_kind` is what tells them apart
        # afterwards — it is the field a steward's decision is routed on.
        fn=lambda: landing.commit(lambda w: _merge(
            service._land(
                source, "company", [a.model_dump() for a in accounts], w,
            ),
            service._land(
                source, "person", [c.model_dump() for c in contacts], w,
            ),
        )),
        writes=lambda _out: landing.writes,
        summary=lambda out: (
            f"{out['landed']} landed, {out['unchanged']} unchanged "
            f"(byte-identical redelivery) in `source-landing`"
        ),
    )

    resolutions = ctx.step(
        "z1", "resolve-records", "resolve",
        fn=lambda: {
            "accounts": [
                resolve(
                    service.graph, "Company", [a.name], thresholds,
                    source=source, source_external_id=a.external_id,
                ).model_dump()
                for a in accounts
            ],
            "contacts": [
                # By address, never by name. `resolve` is given the key rather
                # than the person's name on purpose: an exact address matches
                # exactly, and two different addresses at one domain score far
                # below the review floor, so the book does not quietly merge
                # two people who happen to work at the same company.
                resolve(
                    service.graph, "Person", [_person_key(c, source)], thresholds,
                    source=source, source_external_id=c.external_id,
                ).model_dump()
                for c in contacts
            ],
        },
        summary=lambda out: (
            f"{_count(out['accounts'], 'linked')} account(s) linked · "
            f"{_count(out['accounts'], 'new')} new · "
            f"{_count(out['accounts'], 'review')} to a steward · "
            f"{_count(out['contacts'], 'linked')} contact(s) linked · "
            f"{_count(out['contacts'], 'new')} new"
        ),
        refs=[thresholds.source_ref],
    )

    index = _CompanyIndex(service.graph, source)
    apply = service._act(ctx, "resolve-identities", "a1")
    applied = ctx.step(
        "a1", "apply-resolutions", "action",
        fn=lambda: apply.commit(lambda w: _apply_crm_book(
            service, w, source, accounts, contacts,
            [ResolutionOutcome.model_validate(r) for r in resolutions["accounts"]],
            [ResolutionOutcome.model_validate(r) for r in resolutions["contacts"]],
            ctx.record.id, index,
        )),
        writes=lambda _out: apply.writes,
        summary=lambda out: (
            f"{out['linked']} company claim(s) onto known counterparties · "
            f"{out['created']} minted · {out['people']} person(s) · "
            f"{out['works_at']} employment link(s) · "
            f"{out['awaiting_steward']} Task(s) raised"
        ),
    )

    deals_act = service._act(ctx, "apply-source-records", "a2")
    deals_out = ctx.step(
        "a2", "apply-opportunities", "action",
        fn=lambda: deals_act.commit(lambda w: _apply_opportunities(
            service, w, source, deals, ctx.record.id, index,
        )),
        writes=lambda _out: deals_act.writes,
        summary=lambda out: (
            f"{out['opportunities']} opportunit(y/ies) · "
            f"{out['pursued_with']} bound to a company"
            + (
                f" · {out['company_unresolved']} naming an account this "
                "delta did not carry"
                if out["company_unresolved"] else ""
            )
        ),
    )

    ctx.event(
        "e1", "CrmBookSynced",
        f"{len(accounts)} account(s), {len(contacts)} contact(s), "
        f"{len(deals)} opportunit(y/ies) from {source}",
    )
    return {
        "source": source, "watermark": watermark, "kind": "crm-book",
        "records": len(accounts) + len(contacts) + len(deals),
        **landed, **applied, **deals_out,
    }


def _apply_crm_book(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    accounts: list[AccountRecord],
    contacts: list[ContactRecord],
    account_outcomes: list[ResolutionOutcome],
    contact_outcomes: list[ResolutionOutcome],
    run_id: str,
    index: _CompanyIndex,
) -> dict:
    counts = {
        "linked": 0, "created": 0, "awaiting_steward": 0, "skipped": 0,
        "people": 0, "works_at": 0, "person_unresolved": 0,
    }
    claim_source = f"{_authority(source)}:{source}"

    for record, outcome in zip(accounts, account_outcomes):
        claims = [
            Claim(field="name", value=record.name, source=claim_source),
            Claim(field="slug", value=normalize_key(record.name),
                  source=claim_source),
        ]
        for field, value in (
            ("industry", record.industry),
            ("website", record.website),
            ("phone", record.phone),
        ):
            if value:
                claims.append(
                    Claim(field=field, value=value, source=claim_source)
                )
        _apply_counterparty(
            service, graph, source=source, flow_id=CRM_FLOW_ID,
            external_id=record.external_id, name=record.name, claims=claims,
            outcome=outcome, run_id=run_id, index=index, counts=counts,
        )

    for record, outcome in zip(contacts, contact_outcomes):
        key = _person_key(record, source)
        source_record = service.graph.get(
            object_id("SourceRecord", f"{source}:{record.external_id}")
        )
        company_id = index.lookup(record.account_external_id)
        company = service.graph.get(company_id) if company_id else None

        claims = [Claim(field="name", value=_display_name(record),
                        source=claim_source)]
        for field, value in (
            ("email", record.email.strip().lower()),
            ("first_name", record.first_name),
            ("last_name", record.last_name),
            ("title", record.title),
            ("phone", record.phone),
        ):
            if value:
                claims.append(
                    Claim(field=field, value=value, source=claim_source)
                )
        if company is not None:
            claims.append(Claim(
                field="company_name",
                value=str(company.props.get("name") or company.label),
                source=claim_source,
            ))

        # A person's identity is decided by the key, so the resolver can only
        # ever say "this exact address" or "nobody": a `review` band on two
        # near-miss addresses is a near-miss between two *people*, and the
        # type declares that those stay two rows until a human says otherwise.
        # So anything that is not an exact link mints — and the link that
        # records it says `declared`, because nothing was matched.
        minted = outcome.disposition != "linked"
        if minted:
            person = graph.upsert(
                "Person", key, claims, label=_display_name(record),
                provenance=(
                    f"minted from {source} record {record.external_id} by "
                    f"{run_id}"
                ),
            )
        else:
            existing = service.graph.get(outcome.object_id)
            person = graph.upsert(
                "Person", existing.natural_key if existing else key, claims,
                label=existing.label if existing else _display_name(record),
                provenance=f"claims from {source} via {run_id}",
            )
        counts["people"] += 1

        if source_record is not None:
            graph.link(
                "person_identified_as", person.id, source_record.id,
                props={
                    "source": source, "external_id": record.external_id,
                    "score": 0.0 if minted else outcome.score, "run_id": run_id,
                },
                confidence=1.0 if minted else (outcome.score or 1.0),
                method="declared" if minted else _identity_method(outcome),
                provenance=(
                    outcome.reason if not minted
                    else f"{source} is the only system holding this address"
                ),
            )
        if company is not None:
            graph.link(
                "works_at", person.id, company.id,
                method="declared", provenance="crm accountId",
            )
            counts["works_at"] += 1
        elif record.account_external_id:
            # An account this delta did not carry and no earlier one landed.
            # Counted on the aggregate; the person still exists.
            counts["person_unresolved"] += 1
    return counts


def _apply_opportunities(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    records: list[OpportunityRecord],
    run_id: str,
    index: _CompanyIndex,
) -> dict:
    counts = {
        "opportunities": 0, "pursued_with": 0, "company_unresolved": 0,
    }
    claim_source = f"{_authority(source)}:{source}"
    for record in records:
        company_id = index.lookup(record.account_external_id)
        company = service.graph.get(company_id) if company_id else None
        claims = [
            Claim(field="external_id", value=record.external_id,
                  source=claim_source),
            Claim(field="name", value=record.name, source=claim_source),
            Claim(field="stage", value=_slug(record.stage), source=claim_source),
            Claim(field="amount_usd", value=record.amount, source=claim_source),
            Claim(field="close_date", value=record.close_date,
                  source=claim_source),
        ]
        if company is not None:
            claims.append(Claim(
                field="company_name",
                value=str(company.props.get("name") or company.label),
                source=claim_source,
            ))
        deal = graph.upsert(
            # Source-scoped, as the type declares: two CRMs can issue the same
            # opportunity id and mean different deals.
            "Opportunity", f"{source}:{record.external_id}", claims,
            label=record.name or record.external_id,
            provenance=f"synced by {run_id}",
        )
        counts["opportunities"] += 1
        if company is not None:
            graph.link(
                "pursued_with", deal.id, company.id,
                method="declared", provenance="crm accountId",
            )
            counts["pursued_with"] += 1
        elif record.account_external_id:
            counts["company_unresolved"] += 1
    return counts


# ---------------------------------------------------------------------------
# sales-sync
# ---------------------------------------------------------------------------


def sales_body(service: SyncService, ctx: RunContext, inputs: dict) -> dict:
    """Customers reify onto the same book; the catalogue and orders bind."""
    source = inputs["source"]
    watermark = inputs.get("watermark", "")
    customers = [
        CustomerRecord.model_validate(c) for c in inputs.get("customers", [])
    ]
    items = [ItemRecord.model_validate(i) for i in inputs.get("items", [])]
    orders = [
        SalesOrderRecord.model_validate(o)
        for o in inputs.get("sales_orders", [])
    ]

    ctx.event(
        "t1", SALES_EVENT,
        f"{len(customers)} customer(s), {len(items)} item(s) and "
        f"{len(orders)} order(s) from `{source}`"
        + (f" · watermark {watermark}" if watermark else ""),
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
        fn=lambda: landing.commit(lambda w: service._land(
            source, "company", [c.model_dump() for c in customers], w,
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
                service.graph, "Company", [c.name], thresholds,
                source=source, source_external_id=c.external_id,
            ).model_dump()
            for c in customers
        ],
        summary=lambda out: (
            f"{_count(out, 'linked')} linked to a known counterparty · "
            f"{_count(out, 'new')} new · {_count(out, 'review')} to a steward"
        ),
        refs=[thresholds.source_ref],
    )

    index = _CompanyIndex(service.graph, source)
    apply = service._act(ctx, "resolve-identities", "a1")
    applied = ctx.step(
        "a1", "apply-resolutions", "action",
        fn=lambda: apply.commit(lambda w: _apply_customers(
            service, w, source, customers,
            [ResolutionOutcome.model_validate(r) for r in resolutions],
            ctx.record.id, index,
        )),
        writes=lambda _out: apply.writes,
        summary=lambda out: (
            f"{out['linked']} claim(s) onto known counterparties · "
            f"{out['created']} minted · {out['awaiting_steward']} Task(s) "
            "raised"
        ),
    )

    catalogue = service._act(ctx, "apply-source-records", "a2")
    catalogue_out = ctx.step(
        "a2", "apply-items", "action",
        fn=lambda: catalogue.commit(
            lambda w: _apply_items(w, source, items, ctx.record.id)
        ),
        writes=lambda _out: catalogue.writes,
        summary=lambda out: f"{out['items']} catalogue item(s)",
    )

    order_act = service._act(ctx, "apply-source-records", "a3")
    order_out = ctx.step(
        "a3", "apply-sales-orders", "action",
        fn=lambda: order_act.commit(lambda w: _apply_sales_orders(
            service, w, source, orders, items, ctx.record.id, index,
        )),
        writes=lambda _out: order_act.writes,
        summary=lambda out: (
            f"{out['orders']} order(s) and {out['lines']} line(s) · "
            f"{out['sold_to']} bound to a customer · {out['sells']} line(s) "
            "bound to the catalogue"
            + (
                f" · {out['customer_unresolved']} customer and "
                f"{out['item_unresolved']} item reference(s) unresolved"
                if out["customer_unresolved"] or out["item_unresolved"]
                else ""
            )
        ),
    )

    ctx.event(
        "e1", "SalesRecordsSynced",
        f"{len(customers)} customer(s), {len(items)} item(s), "
        f"{len(orders)} order(s) from {source}",
    )
    return {
        "source": source, "watermark": watermark, "kind": "sales-records",
        "records": len(customers) + len(items) + len(orders),
        **landed, **applied, **catalogue_out, **order_out,
    }


def _apply_customers(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    records: list[CustomerRecord],
    outcomes: list[ResolutionOutcome],
    run_id: str,
    index: _CompanyIndex,
) -> dict:
    counts = {"linked": 0, "created": 0, "awaiting_steward": 0, "skipped": 0}
    claim_source = f"{_authority(source)}:{source}"
    for record, outcome in zip(records, outcomes):
        claims = [
            Claim(field="name", value=record.name, source=claim_source),
            Claim(field="slug", value=normalize_key(record.name),
                  source=claim_source),
        ]
        for field, value in (
            ("email", record.email),
            ("phone", record.phone),
            # The commercial fact the ERP outranks the CRM on. Both claims are
            # kept when both systems hold one; this is simply the one that wins.
            ("payment_terms", record.payment_terms),
        ):
            if value:
                claims.append(
                    Claim(field=field, value=value, source=claim_source)
                )
        _apply_counterparty(
            service, graph, source=source, flow_id=SALES_FLOW_ID,
            external_id=record.external_id, name=record.name, claims=claims,
            outcome=outcome, run_id=run_id, index=index, counts=counts,
        )
    return counts


def _apply_items(
    graph: GraphWriter, source: str, records: list[ItemRecord], run_id: str
) -> dict:
    claim_source = f"{_authority(source)}:{source}"
    written = 0
    for record in records:
        if not record.sku:
            # The natural key is the SKU; a row without one names no catalogue
            # entry, and landing it under an empty key would collide every
            # such row into a single ghost item.
            log.info(
                "Catalogue row has no SKU",
                extra={"source": source, "record": record.external_id},
            )
            continue
        graph.upsert(
            "Item", record.sku,
            [
                Claim(field="sku", value=record.sku, source=claim_source),
                Claim(field="name", value=record.display_name,
                      source=claim_source),
                Claim(field="item_type", value=_item_type(record.item_type),
                      source=claim_source),
                Claim(field="base_price_usd", value=record.base_price,
                      source=claim_source),
                # Kept because it is what order lines reference — the only
                # bridge between the id a line names and the key we chose.
                Claim(field="external_id", value=record.external_id,
                      source=claim_source),
            ],
            label=record.display_name or record.sku,
            provenance=f"synced by {run_id}",
        )
        written += 1
    return {"items": written}


def _apply_sales_orders(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    records: list[SalesOrderRecord],
    items: list[ItemRecord],
    run_id: str,
    index: _CompanyIndex,
) -> dict:
    counts = {
        "orders": 0, "lines": 0, "sold_to": 0, "sells": 0,
        "customer_unresolved": 0, "item_unresolved": 0,
    }
    claim_source = f"{_authority(source)}:{source}"
    # In-batch first, then anything already claiming that id. Nothing here
    # mints: a line naming an item the catalogue has never carried is counted,
    # because an Item invented out of an internal id would have no SKU and the
    # real one, when it arrives, could never be reconciled with it.
    by_external = {i.external_id: i.sku for i in items if i.external_id and i.sku}

    def item_for(external_id: str):
        if not external_id:
            return None
        sku = by_external.get(external_id)
        if sku is None:
            # Asked of the store by the property, the way `sync_dms` asks for a
            # folder — a bare `find("Item")` filtered in Python here reads the
            # whole catalogue to answer one order line.
            known = service.graph.find(
                "Item", prop_equals={"external_id": external_id}, limit=1,
            )
            if not known:
                return None
            sku = str(known[0].props.get("sku") or known[0].natural_key)
            by_external[external_id] = sku
        return service.graph.get(object_id("Item", sku))

    for record in records:
        company_id = index.lookup(record.customer_external_id)
        company = service.graph.get(company_id) if company_id else None
        claims = [
            Claim(field="order_number", value=record.order_number,
                  source=claim_source),
            Claim(field="external_id", value=record.external_id,
                  source=claim_source),
            Claim(field="ordered_date", value=record.ordered_date,
                  source=claim_source),
            Claim(field="status", value=_slug(record.status),
                  source=claim_source),
            Claim(field="total_usd", value=record.total, source=claim_source),
            Claim(field="currency", value="USD", source=claim_source),
            Claim(field="memo", value=record.memo, source=claim_source),
        ]
        if company is not None:
            claims.append(Claim(
                field="company_name",
                value=str(company.props.get("name") or company.label),
                source=claim_source,
            ))
        order = graph.upsert(
            "SalesOrder", record.order_number, claims,
            label=record.order_number,
            provenance=f"synced by {run_id}",
        )
        counts["orders"] += 1

        if company is not None:
            graph.link(
                "sold_to", order.id, company.id,
                method="declared", provenance="erp customerId",
            )
            counts["sold_to"] += 1
        elif record.customer_external_id:
            counts["customer_unresolved"] += 1

        for ordinal, line in enumerate(record.lines, start=1):
            item = item_for(line.item_external_id)
            sku = str(item.props.get("sku") or item.natural_key) if item else ""
            line_obj = graph.upsert(
                "SalesOrderLine", f"{order.id}#{ordinal}",
                [
                    Claim(field="sku", value=sku, source=claim_source),
                    Claim(
                        field="description",
                        value=item.label if item is not None else "",
                        source=claim_source,
                    ),
                    Claim(field="quantity", value=line.quantity,
                          source=claim_source),
                    Claim(field="unit_price_usd", value=line.rate,
                          source=claim_source),
                    Claim(field="amount_usd", value=line.amount,
                          source=claim_source),
                    Claim(field="order_number", value=record.order_number,
                          source=claim_source),
                ],
                label=f"{record.order_number} line {ordinal}",
                provenance=f"synced by {run_id}",
            )
            graph.link(
                "has_so_line", order.id, line_obj.id,
                props={"ordinal": ordinal}, provenance=run_id,
            )
            counts["lines"] += 1
            if item is not None:
                graph.link(
                    "sells", line_obj.id, item.id,
                    props={"external_id": line.item_external_id},
                    method="declared", provenance="erp line itemId",
                )
                counts["sells"] += 1
            elif line.item_external_id:
                counts["item_unresolved"] += 1
    return counts


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _merge(*outcomes: dict) -> dict:
    """Add up the landing counts of several `_land` passes in one step."""
    total: dict[str, int] = {}
    for outcome in outcomes:
        for key, value in outcome.items():
            total[key] = total.get(key, 0) + value
    return total


def _count(outcomes: list[dict], disposition: str) -> int:
    return sum(1 for o in outcomes if o["disposition"] == disposition)
