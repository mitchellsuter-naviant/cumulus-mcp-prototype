"""The production feed: what the plant is building, and who has it now.

One body, and three decisions worth reading before the code.

**A customer name is fuzzy, so the row reifies.** The ERP names a job's
customer by an internal id and this feed's own pull translates it to a name
(S4) — but a name is exactly the evidence that fails: `Hoffman Ridge Farms` and
`Hoffmann Ridge Farms` are one farm and two rows, and nothing in the source
says so. So the customer reference each job carries is landed as its own
`SourceRecord`, scored against the counterparty book through the same
`_apply_counterparty` the CRM, the ERP's sell side and the PSA go through, and
an ambiguous one raises a `match_review` Task in the desk the Mapping names.
Three systems that disagree about what a company is *called* must not also
disagree about what counts as the same company, and one function is the only
way to be sure of that. The `Job` lands either way — a batch feed never
suspends for one ambiguous row — keeping `customer_name` as the answer there
is until a steward gives a better one.

**A crew id resolving to nothing is a count, not a failure.** The HRIS lands a
crew as a department whose head is the foreman and whose **cost centre** is
the bare crew id; the ERP's job names the crew by that same string, and the
Mapping declares the binding on `Team.cost_center`, which is what makes "this
crew id is that team" a portable statement a customer owns rather than a
lookup buried in a flow. The cost centre and not the department id, because
they are two different strings: an HRIS addresses a department by `DEPT-1022`
and the rest of the business writes `12`, and a seed that made the crew id the
department's primary id to make this join work would collide with the HRIS's
own conventions and leave the join depending on an accident. An id that
matches no `Team` is deliberately ordinary: the subcontractor crew
(`SUB-KVB`) has no HRIS department and never will, so the `Job` keeps
`crew_id` and `foreman_name` as landed strings, the run reports how many
looked like that, and a later flow raises the identity question with a human
at the end of it. A per-row Task here would page somebody every week about a
subcontractor everybody knows about.

**A job that moves crews withdraws the old edge; it does not delete it.** The
previous `assigned_to` link's validity is ended and the new one is drawn, and
both stay on the record — which is what lets a workspace show *former crew*
beside *current crew*, and what retires the SDD's weekly folder-reconciliation
bot. That bot existed to go looking for the difference between last week's
schedule and this one; the difference is now a property of the record, so
there is nothing to go looking for.

**The ERP's `"Yes"`, `"No"` and `"Week 3"` become facts here, and nowhere
before here.** The *estate connector* delivers them verbatim, because
translating in a puller would put a governed decision below the Mapping where
nobody can read it — and that is right, but on its own it left
`Job.release_state`, `hold_reason` and `week_position` empty, which would have
forced the eligibility Rule to read raw strings out of a `SourceRecord`
payload. That is precisely what the ontology exists to stop. So this body — a
*flow* body, which runs inside the engine and may read governed knowledge, the
way its `x1 read-resolution-policy` step already does — reads the `## Source
vocabulary` table off `plant-scheduling-parameters` in `x2` and claims the
three fields in the same guarded write as everything else. The landed
`SourceRecord` still holds the strings verbatim, so a change to the table
re-derives the fields without re-pulling the estate.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from ..graph import Claim, GraphWriter, normalize_key
from ..ontology import names as onto
from ..resolution import (
    ResolutionOutcome,
    ResolutionThresholds,
    read_resolution_thresholds,
    resolve,
)
# The house grammar for reading a governed document: scope to one `##`
# heading, then match inside it. Imported rather than re-typed — the two
# copies of `team_slug` below `compliance._site_slug` exist because a
# connector may not import the module that reaches the network, and no such
# prohibition applies here. One reader, one definition of "this section".
from ..routing import _section
from ..runs import RunContext
from .sync import (
    JOB_EVENT,
    JOB_FLOW_ID,
    JobRecord,
    SyncService,
    _authority,
    _slug,
)
from .commerce import _CompanyIndex, _apply_counterparty, _count

log = logging.getLogger("cumulus.sync.production")

#: The governed page the plant's own words are declared on. Only its `##
#: Source vocabulary` table is read here; the eligibility clauses, the
#: capacity bands and the calendar on the same page belong to the flows that
#: decide with them.
SCHEDULING_ASSET_ID = "plant-scheduling-parameters"

#: The built-in vocabulary — the words this flow shipped with, copied from the
#: governed table itself. It is a **fallback and not a default**: a deployment
#: whose corpus is missing the page lands translated jobs rather than three
#: dead fields, and the run *says* it fell back, because a flow quietly using
#: its own words for a customer's is the exact failure the governed table
#: exists to prevent.
_BUILT_IN_RELEASE: dict[str, str] = {"yes": "released", "no": "not released"}
#: `no` maps to nothing on purpose: the table's `(none)` means *claim nothing*,
#: because a job with no hold should read as unheld rather than as held for a
#: reason spelled "none".
_BUILT_IN_HOLD: dict[str, str] = {"yes": "DNF", "no": ""}
_BUILT_IN_WEEK: dict[str, float] = {"week-1": 1.0, "week-2": 2.0, "week-3": 3.0}


class SourceVocabulary(BaseModel):
    """The job system's own words, and the facts the governed table makes them.

    One map per fact rather than one map keyed by both columns, because the
    table's `Fact` column *is* the ontology's field name and that is what a
    caller asks for. Which delivered column each fact is read from stays in
    code: a governed table declares vocabulary, not addressing, and
    `JobRecord.out_to_yard` already records which ERP column it renamed.

    Keys are slugged source values, so `Yes`, `yes` and ` Yes ` are one row.
    `week_position_default` is the table's own catch-all — the page says a job
    the screen puts beyond Week 3 is a `0` and that this is a fact rather than
    an error — so it is a value, not a `None`.
    """

    release_state: dict[str, str] = Field(
        default_factory=lambda: dict(_BUILT_IN_RELEASE))
    hold_reason: dict[str, str] = Field(
        default_factory=lambda: dict(_BUILT_IN_HOLD))
    week_position: dict[str, float] = Field(
        default_factory=lambda: dict(_BUILT_IN_WEEK))
    week_position_default: float = 0.0
    #: The revision the run read, named so a claim can be checked against the
    #: page that produced it.
    source_ref: str = f"knowledge:{SCHEDULING_ASSET_ID}"
    #: Whether the page was in the corpus at all, and whether it carried the
    #: table. Two flags because they are two different things to tell an
    #: operator, and both mean the built-in words stood in.
    found_asset: bool = False
    found_table: bool = False

    def word(self, fact: str, said: str) -> str | None:
        """The fact a source's word becomes — `None` when the table has no row.

        Three answers and not two, which is the whole point of the `None`: a
        word the table maps to *nothing* (`doNotFab: No` → `(none)`) is the
        table saying claim nothing, and a word the table has never heard of is
        a vocabulary gap the run has to count. Collapsing the two would hide a
        renamed picklist behind an empty claim.
        """
        return getattr(self, fact).get(_slug(said))

    def number(self, said: str) -> float:
        """The week position, with the table's own catch-all behind it."""
        return self.week_position.get(_slug(said), self.week_position_default)


#: One row of a four-column governed table. Anchored to the whole line so the
#: three-column tables on the same page cannot half-match.
_VOCAB_ROW = re.compile(
    r"^[ \t]*\|([^|\n]*)\|([^|\n]*)\|([^|\n]*)\|([^|\n]*)\|[ \t]*$",
    re.MULTILINE,
)

#: The source-value cell the table uses for everything the rows above did not
#: name — `weekPosition | anything else, or absent | week_position | 0`.
_VOCAB_CATCH_ALL = re.compile(r"anything else|any other|absent|otherwise",
                              re.IGNORECASE)

#: How the page writes "no value": the `(none)` in the hold-reason row, plus
#: the dashes the rest of the document uses for an unset figure.
_VOCAB_EMPTY = {"", "(none)", "none", "-", "--", "—", "–", "n/a"}


def _cell(raw: str) -> str:
    """One table cell, without the markdown a person wrote it with."""
    return (raw or "").strip().strip("`*_ ").strip()


def parse_source_vocabulary(content: str) -> SourceVocabulary:
    """Read the `## Source vocabulary` table off the governed page.

    Section-scoped, and not for tidiness: the same document carries nine other
    tables and a quoted `- goal max: —` under its `## Open` heading, so a
    whole-document match would read the wrong rows as vocabulary.
    `routing.parse_routing` scopes the same way for the same reason.

    Tolerant in one direction only. Rows merge onto the built-in words rather
    than replacing them wholesale, so a re-ordered table, a renamed picklist
    or a supervisor mid-edit leaves a working vocabulary standing — this is a
    page a plant edits in the console, and it must not be able to make a run
    raise. What tolerance may never do is stay quiet: `found_asset` and
    `found_table` are what the step reports, so a run reading its own words
    instead of the plant's says so in the journal.
    """
    section = _section(content, "source vocabulary")
    if not section:
        return SourceVocabulary(found_asset=True, found_table=False)

    vocabulary = SourceVocabulary(found_asset=True, found_table=True)
    for _source_field, raw_said, raw_fact, raw_value in _VOCAB_ROW.findall(
        section
    ):
        fact = _cell(raw_fact)
        if fact not in ("release_state", "hold_reason", "week_position"):
            # The header row, the `---` separator, and any fact this flow does
            # not claim. Skipped rather than rejected: the table is allowed to
            # grow a fourth row for a field somebody adds later, and a parser
            # that failed on one would make adding it a code change.
            continue
        said = _cell(raw_said)
        value = _cell(raw_value)
        if fact == "week_position":
            try:
                number = float(value)
            except ValueError:
                # A week the page did not write as a number. Skipped, because
                # `Job.week_position` is declared numeric and inventing an
                # ordinal from prose would put a guess in a field a capacity
                # fill reads as a fact.
                continue
            if _VOCAB_CATCH_ALL.search(said):
                vocabulary.week_position_default = number
            else:
                vocabulary.week_position[_slug(said)] = number
            continue
        if not said:
            continue
        getattr(vocabulary, fact)[_slug(said)] = (
            "" if value.lower() in _VOCAB_EMPTY else value
        )
    return vocabulary


def read_source_vocabulary(knowledge) -> SourceVocabulary:
    """The governed vocabulary, or the built-in one with the reason recorded.

    Shaped exactly like `resolution.read_resolution_thresholds`, including the
    `@rev` on the ref: a claim derived from a governed word is only auditable
    if the run names the revision it read.
    """
    asset = knowledge.repo.get(SCHEDULING_ASSET_ID) if knowledge else None
    if asset is None:
        log.warning("Plant scheduling parameters missing; using built-in "
                    "source vocabulary")
        return SourceVocabulary(found_asset=False, found_table=False)
    vocabulary = parse_source_vocabulary(asset.content)
    vocabulary.source_ref = (
        f"knowledge:{SCHEDULING_ASSET_ID}@rev{getattr(asset, 'revision', 1)}"
    )
    return vocabulary


def _job_key(source: str, record: JobRecord) -> str:
    """`Job`'s declared natural key: source + the job number.

    Source-scoped for the reason a project code is: two plants' systems can
    both issue `250661` and mean different buildings. The internal record id
    is the fallback rather than the key, because the number is what everybody
    in the building actually says.
    """
    return f"{source}:{record.job_number or record.external_id}"


def _customer_payloads(records: list[JobRecord]) -> list[dict]:
    """One landing row per distinct customer name the join produced.

    The jobs feed does not deliver the ERP's customer list — that is
    `erp-sales`' contract, and one delivery must not start two flows — but the
    *reference* on each job is a claim about a counterparty, and this is the
    row a steward rules on when the name is ambiguous. Keyed by the normalized
    name rather than by the ERP's customer id, because the id is not on this
    wire: the shape carries the name the join filled in, which is also the
    only thing identity here can turn on.

    De-duplicated within the delivery, because forty jobs for one builder are
    one identity question and forty Tasks would be forty copies of it.
    """
    seen: dict[str, dict] = {}
    for record in records:
        name = record.customer_name.strip()
        key = normalize_key(name)
        if not key or key in seen:
            continue
        seen[key] = {"external_id": key, "name": name}
    return list(seen.values())


def job_body(service: SyncService, ctx: RunContext, inputs: dict) -> dict:
    """Land the jobs, resolve their customers, wire the crew and the folders."""
    source = inputs["source"]
    watermark = inputs.get("watermark", "")
    records = [JobRecord.model_validate(r) for r in inputs["records"]]
    customers = _customer_payloads(records)

    ctx.event(
        "t1", JOB_EVENT,
        f"{len(records)} job record(s) from `{source}`"
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

    # The second governed read, and the seam it closes. The estate connector
    # delivers `"Yes"`, `"No"` and `"Week 3"` verbatim — correctly, because a
    # puller that translated them would put the decision below the Mapping —
    # and this is the layer that is allowed to know what they mean. Read
    # before the write and never inside it, exactly like the thresholds above:
    # a step that fetched while it committed would make a replay depend on
    # what the corpus said the second time.
    vocabulary_raw = ctx.step(
        "x2", "read-source-vocabulary", "transform",
        fn=lambda: read_source_vocabulary(service.knowledge).model_dump(),
        summary=lambda out: (
            f"{out['source_ref']}: "
            + (
                f"{len(out['release_state'])} release word(s), "
                f"{len(out['hold_reason'])} hold word(s), "
                f"{len(out['week_position'])} week position(s) · "
                f"anything else is week "
                f"{out['week_position_default']:.0f}"
                if out["found_table"] else
                (
                    "the page carries no `## Source vocabulary` table, so "
                    "this run translated with the words the flow shipped "
                    "with"
                )
                if out["found_asset"] else
                (
                    "not in the corpus, so this run translated with the "
                    "words the flow shipped with — the jobs are not "
                    "untranslated, and the vocabulary is not the plant's"
                )
            )
        ),
    )
    vocabulary = SourceVocabulary.model_validate(vocabulary_raw)
    ctx.note_refs(vocabulary.source_ref)

    landing = service._act(ctx, "land-source-records", "s1")
    landed = ctx.step(
        "s1", "land-records", "sync",
        # Both kinds in one commit, because they are one landing: the job row
        # as delivered — including the `"Yes"`/`"No"` flags in the *source's*
        # words, kept verbatim even though `a2` now translates them, so the
        # derivation can be re-run against an edited vocabulary table without
        # re-pulling the estate — and the customer reference read off it,
        # which is the row a steward adjudicates.
        fn=lambda: landing.commit(lambda w: _land_both(
            service, source, [r.model_dump() for r in records], customers, w,
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
                service.graph, onto.COMPANY, [c["name"]], thresholds,
                source=source, source_external_id=c["external_id"],
            ).model_dump()
            for c in customers
        ],
        summary=lambda out: (
            f"{_count(out, 'linked')} customer(s) linked to a known "
            f"counterparty · {_count(out, 'new')} new · "
            f"{_count(out, 'review')} to a steward"
        ),
        refs=[thresholds.source_ref],
    )

    index = _CompanyIndex(service.graph, source)
    identities = service._act(ctx, "resolve-identities", "a1")
    applied = ctx.step(
        "a1", "apply-customer-identities", "action",
        fn=lambda: identities.commit(lambda w: _apply_customers(
            service, w, source, customers,
            [ResolutionOutcome.model_validate(r) for r in resolutions],
            ctx.record.id, index,
        )),
        writes=lambda _out: identities.writes,
        summary=lambda out: (
            f"{out['linked']} claim(s) onto known counterparties · "
            f"{out['created']} minted · {out['awaiting_steward']} Task(s) "
            "raised"
        ),
    )

    book = service._act(ctx, "apply-source-records", "a2")
    plant = ctx.step(
        "a2", "apply-jobs", "action",
        fn=lambda: book.commit(lambda w: _apply_jobs(
            service, w, source, records, ctx.record.id, index, vocabulary,
        )),
        writes=lambda _out: book.writes,
        refs=[vocabulary.source_ref],
        summary=lambda out: (
            f"{out['jobs']} job(s) · {out['job_for']} bound to a customer · "
            f"{out['assigned_to']} crew edge(s)"
            + (
                f" · {out['vocabulary_translated']} fact(s) claimed from "
                "the governed vocabulary"
                if out["vocabulary_translated"] else ""
            )
            + (
                f" · {out['vocabulary_unmapped']} source word(s) that table "
                "does not hold"
                if out["vocabulary_unmapped"] else ""
            )
            + (
                f", {out['withdrawn']} withdrawn from a crew that no longer "
                "has it"
                if out["withdrawn"] else ""
            )
            + (f" · {out['job_folder']} folder edge(s)" if out["job_folder"]
               else "")
            + (
                f" · {out['crew_unresolved']} crew id(s) matching no team"
                if out["crew_unresolved"] else ""
            )
            + (
                f" · {out['customer_unresolved']} job(s) whose customer this "
                "delta could not place"
                if out["customer_unresolved"] else ""
            )
        ),
    )

    ctx.event(
        "e1", "JobsSynced",
        f"{len(records)} job record(s) from {source}",
    )
    return {
        "source": source, "watermark": watermark, "kind": "jobs",
        "records": len(records), **landed, **applied, **plant,
    }


def _land_both(
    service: SyncService,
    source: str,
    jobs: list[dict],
    customers: list[dict],
    writer: GraphWriter,
) -> dict:
    """The job rows and the customer references they carry, in one pass."""
    counts = service._land(source, "job", jobs, writer)
    refs = service._land(source, "company", customers, writer)
    return {
        "landed": counts["landed"] + refs["landed"],
        "unchanged": counts["unchanged"] + refs["unchanged"],
    }


def _apply_customers(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    payloads: list[dict],
    outcomes: list[ResolutionOutcome],
    run_id: str,
    index: _CompanyIndex,
) -> dict:
    """The builders and owners the jobs name, onto the counterparty book.

    The thinnest claim set in the registry — a name and its normalization —
    because that is all a job row carries about the party it is being built
    for. The ERP's own customer feed carries the terms and the addresses and
    outranks nothing here, since both write as `erp`: two voices from one
    system agreeing on a name is the ordinary case, and the upsert dedupes it.
    """
    counts = {"linked": 0, "created": 0, "awaiting_steward": 0, "skipped": 0}
    claim_source = f"{_authority(source)}:{source}"
    for payload, outcome in zip(payloads, outcomes):
        name = str(payload["name"])
        _apply_counterparty(
            service, graph, source=source, flow_id=JOB_FLOW_ID,
            external_id=str(payload["external_id"]), name=name,
            claims=[
                Claim(field="name", value=name, source=claim_source),
                Claim(field="slug", value=normalize_key(name),
                      source=claim_source),
            ],
            outcome=outcome, run_id=run_id, index=index, counts=counts,
        )
    return counts


def _teams_by_cost_center(service: SyncService) -> dict[str, Any]:
    """Every `Team` the graph holds, by the cost centre its source carries.

    One scan per run rather than a lookup per job, the way
    `_wire_org_chart.by_external` does it: a delivery names the same three
    crews forty times, and `find` walks the store.

    The cost centre is the whole binding (S1, as the `erp-jobs` Mapping
    declares it): the HRIS's crew department carries the bare crew id — `"12"`,
    `"14"`, `"18"` — as its accounting code, and the ERP's job names the crew
    by that same string, so this is a lookup and not a match. Deliberately
    **not** `external_id`: that is the HRIS's own department id (`DEPT-1022`),
    a different string about the same department, and joining on it would
    require an estate to key its departments by crew number to make the join
    work at all.

    **A crew whose cost centre is spelled some other way resolves to
    nothing**, which is the aggregate below and not an error; the two systems
    agreeing on that string is a seam the estate's own seed has to honour, and
    the count is how a disagreement becomes visible instead of silent.

    Departments with no cost centre are simply absent from the index, which is
    the ordinary case — most of an org chart has none — and never a defect.
    """
    return {
        str(o.props.get("cost_center")): o
        for o in service.graph.find(onto.TEAM)
        if o.props.get("cost_center")
    }


def _folders_by_leaf(service: SyncService) -> dict[str, list[Any]]:
    """Every `Folder` the graph holds, by the last segment of its path.

    A job's artifacts live under more than one root — `Job Documents/<job>` in
    the office's tree and `PlantCutSheets/<site>/<job>` on the floor, two
    trees with two retention rules — and both end in the job number. So the
    index is by leaf, the edge is many, and a packet is a query over the edges
    rather than a folder somebody has to create, copy into and remember to
    delete.

    Read once per run for `_teams_by_cost_center`'s reason, and scoped to
    nothing: a folder path is already source-scoped in its own key, and a
    second source filing the same job is a second real place it is filed.
    """
    by_leaf: dict[str, list[Any]] = {}
    for record in service.graph.find(onto.FOLDER):
        path = str(record.props.get("path") or "")
        if not path:
            continue
        by_leaf.setdefault(path.rsplit("/", 1)[-1], []).append(record)
    return by_leaf


def _apply_jobs(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    records: list[JobRecord],
    run_id: str,
    index: _CompanyIndex,
    vocabulary: SourceVocabulary,
) -> dict:
    """Each job as a root, the three edges the source lets us draw, and the
    three fields the governed vocabulary turns its own words into."""
    counts = {
        "jobs": 0, "job_for": 0, "assigned_to": 0, "withdrawn": 0,
        "job_folder": 0, "crew_unresolved": 0, "customer_unresolved": 0,
        "job_unkeyed": 0, "vocabulary_translated": 0,
        "vocabulary_unmapped": 0,
    }
    claim_source = f"{_authority(source)}:{source}"
    crews = _teams_by_cost_center(service)
    folders = _folders_by_leaf(service)

    for record in records:
        if not (record.job_number or record.external_id):
            # A row with neither names no building, and landing it under a
            # bare `erp-jobs:` would collide every such row into one ghost.
            log.info("Job row has no number and no id",
                     extra={"source": source, "job": record.name})
            counts["job_unkeyed"] += 1
            continue

        claims = [
            Claim(field="job_number", value=record.job_number,
                  source=claim_source),
            Claim(field="external_id", value=record.external_id,
                  source=claim_source),
            # Carried for the reason a Project carries one: the key is
            # source-scoped, so every lookup against it has to be too.
            Claim(field="source", value=source, source=claim_source),
            # The department slugs the source marks done, claimed whole and
            # every window: the ten columns ride on every row, so an empty
            # list is the source saying nothing is finished rather than a
            # delta that failed to mention it.
            Claim(field="upstream_done", value=record.upstream_done,
                  source=claim_source),
        ]
        # Claimed only where the source said something, the way the workforce
        # feed does it: an empty claim is not the same fact as a missing one,
        # and `status == ""` would outrank the system that knows.
        for field, value in (
            ("name", record.name),
            ("customer_name", record.customer_name),
            ("builder_name", record.builder_name),
            ("plant_site", record.plant_site),
            ("status", record.status),
            ("load_date", record.load_date),
            ("shipment_date", record.shipment_date),
            ("foreman_name", record.foreman),
            ("crew_id", record.crew_id),
            ("shipment_type", record.shipment_type),
            ("combined_with", record.combined_with),
            ("trailer_number", record.trailer_number),
            ("secondary_dimensions", record.secondary_dimensions),
        ):
            if value:
                claims.append(
                    Claim(field=field, value=value, source=claim_source)
                )
        # Dimensions: a zero is the source not having measured it, and a
        # claimed 0.0 would read as a building with no width.
        for field, number in (
            ("width", record.width),
            ("length", record.length),
            ("height", record.height),
        ):
            if number:
                claims.append(
                    Claim(field=field, value=number, source=claim_source)
                )

        # The governed translation, and the three fields it exists for. The
        # source's picklist word goes in, the plant's own declared fact comes
        # out, and nothing between here and the page decides anything.
        for field, said in (
            ("release_state", record.out_to_yard),
            ("hold_reason", record.do_not_fab),
        ):
            if not said:
                # The column was empty. An absent flag is not a state: a
                # claimed `release_state == ""` would outrank the system that
                # knows, exactly as it would for `status` above.
                continue
            fact = vocabulary.word(field, said)
            if fact is None:
                # A word the table has never heard of — a renamed picklist,
                # most likely. Counted, never guessed: `release_state` gates
                # whether a job may be built, and rounding an unknown word to
                # the nearest declared one would release something on the
                # strength of a spelling.
                counts["vocabulary_unmapped"] += 1
                continue
            if not fact:
                # The table's own `(none)`: it *has* a row for this word and
                # the row says claim nothing. A job nobody held reads as
                # unheld rather than as held for a reason spelled "none".
                continue
            claims.append(Claim(field=field, value=fact, source=claim_source))
            counts["vocabulary_translated"] += 1
        # Claimed unconditionally, and `0` is the reason: the governed page
        # says a job the screen puts beyond Week 3 *is* a zero and that this
        # is a fact rather than an error, so this is the one number on the row
        # where a falsy value is worth writing down. The dimensions above are
        # the opposite case and stay that way.
        claims.append(Claim(
            field="week_position",
            value=vocabulary.number(record.week_position),
            source=claim_source,
        ))
        counts["vocabulary_translated"] += 1

        job = graph.upsert(
            onto.JOB, _job_key(source, record), claims,
            label=" ".join(filter(None, (record.job_number, record.name)))
                  or record.external_id,
            provenance=f"synced by {run_id}",
        )
        counts["jobs"] += 1

        company_id = index.lookup(normalize_key(record.customer_name))
        if company_id:
            graph.link(
                "job_for", job.id, company_id,
                method="declared", provenance="erp customerId, joined to name",
            )
            counts["job_for"] += 1
        elif record.customer_name:
            # A customer still sitting with a steward, or one this delivery
            # scored below the floor. Nothing was asserted about it, so there
            # is nothing for the job to point at yet — and `customer_name` is
            # on the row, which is the answer there is until somebody rules.
            counts["customer_unresolved"] += 1

        crew = crews.get(record.crew_id) if record.crew_id else None
        # Withdraw before drawing, and withdraw whatever this crew is not:
        # a job can be reassigned twice in a week, and the record should read
        # as one live edge and two ended ones rather than as three live crews.
        # **Only when the source named a crew at all**, which is the same rule
        # the claims above follow: an empty column is the ERP saying nothing,
        # and ending a live assignment on the strength of a silence would take
        # a job off a crew that still has it. A crew id that names *no* team is
        # a different matter and does withdraw — the source did say, and what
        # it said is that this is no longer Crew 12's job.
        for link, team in (
            service.graph.neighbors(job.id, "assigned_to", "out")
            if record.crew_id else ()
        ):
            if crew is not None and team.id == crew.id:
                continue
            graph.reverse_link(
                link.id, f"flow:{JOB_FLOW_ID}",
                f"{source} moved {record.job_number} off {team.label}"
                if crew is not None
                else f"{source} names crew `{record.crew_id}`, which is not "
                     f"{team.label}",
            )
            counts["withdrawn"] += 1
        if crew is not None:
            graph.link(
                "assigned_to", job.id, crew.id,
                props={"crew_id": record.crew_id, "foreman": record.foreman},
                method="declared", provenance="erp crewId",
            )
            counts["assigned_to"] += 1
        elif record.crew_id:
            # The designed case, and an aggregate on purpose: the
            # subcontractor crew has no HRIS department and never will. The
            # strings stay on the job and a later flow asks the question.
            counts["crew_unresolved"] += 1

        for folder in folders.get(record.job_number, ()):
            graph.link(
                "job_folder", job.id, folder.id,
                method="declared",
                provenance=f"dms path ends in {record.job_number}",
            )
            counts["job_folder"] += 1
    return counts
