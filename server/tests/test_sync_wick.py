"""The plant floor: three feeds, three joins, and one edge that gets withdrawn.

These are the feeds a manufacturer's namespace runs, and what is worth pinning
about each of them is a place it declines to decide something.

The jobs *connector* **renames and never judges**: `"Yes"` and `"No"` are the
ERP's own picklist values and they land verbatim, because mapping them onto a
release state is a governed decision a customer can re-map. What it does
decide is *addressing* — which column belongs to which department, and which
customer row a `customerId` points at — and the tests below hold it to
exactly that line. The **flow** is where the words become facts: `job-sync`
reads the `## Source vocabulary` table off the plant's own governed page in
the same layer that already reads the resolution thresholds, so the
eligibility rule reads declared fields instead of raw strings out of a landed
payload, and every run names the revision it read.

The crews join on a **cost centre**: an HRIS department has an id (`DEPT-1022`)
and an accounting code (`12`), the ERP writes the code in `jobs.crewId`, and
`Team` declares both because the graph should hold the fact it joins on. A job
the ERP has not crewed yet says nothing rather than saying nobody, and is
asserted to.

The credential feed **fans one row into four and mints nobody**: a driver file
holds four unrelated expiries, a null column is the HRIS saying there is
nothing on file, and a holder the workforce does not contain is counted rather
than invented.

The equipment feed's whole point is a **null**: a skid steer with no next-due
date is not tracked, which is a fact about the machine, and saying so once on
the way in is what stops a compliance sweep reporting it as overdue forever.

The estate is faked (`estate_fixtures.wick_estate`), the door is not:
deliveries go through `POST /api/v1/sources/deliver`, so the wire shapes, the
Dispatcher and the three flows are exercised for real.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.connectors.compliance import _site_slug
from app.connectors.sync import (
    CREDENTIAL_FLOW_ID,
    EQUIPMENT_FLOW_ID,
    JOB_FLOW_ID,
)
from app.dispatch import TRIGGERS
from app.estate import (
    DMS_LIBRARY,
    ERP_EQUIPMENT,
    ERP_JOBS,
    ERP_SALES,
    ERP_VENDORS,
    ERP_DEPARTMENT_COLUMNS,
    HRIS_CREDENTIALS,
    HRIS_WORKFORCE,
    EstateConnector,
    EstateError,
    InMemoryConnectorStateStore,
    team_slug,
)
from app.graph import object_id
from app.ontology import ONTOLOGY
from tests import estate_fixtures as fx
from tests import world as test_world

PROJECT = "cumulus-demo-mitchell-suter"


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


def door(client):
    """The real HTTP door as the connector's `deliver`."""

    def deliver(payload: dict) -> dict:
        response = client.post("/api/v1/sources/deliver", json=payload)
        if response.status_code != 202:
            raise EstateError(f"door → {response.status_code}: {response.text}")
        return response.json()

    return deliver


def build(client, estate: fx.FakeEstate | None = None, **over) -> EstateConnector:
    """A connector on the `wick` namespace, delivering through the door."""
    estate = estate or fx.wick_estate()
    connector = EstateConnector(
        InMemoryConnectorStateStore(),
        project=PROJECT,
        namespace="wick",
        fetch=estate.fetch,
        deliver=door(client),
        **over,
    )
    connector.estate = estate  # type: ignore[attr-defined] — test handle
    return connector


def job(world, job_number: str):
    return world.graph.get(object_id("Job", f"{ERP_JOBS}:{job_number}"))


def landed(world, source: str, external_id: str):
    return world.graph.get(object_id("SourceRecord", f"{source}:{external_id}"))


@pytest.fixture()
def plant(client, world):
    """The whole namespace pulled in dependency order, once.

    The order is the one the FEEDS registry declares and the one the flows
    need: the workforce first, because a crew is a department and a job binds
    to it; the sell side next, because a job's customer is a counterparty the
    ERP already holds; the library, because a job's folders are filed before
    anybody asks for a packet. Then the three plant feeds.
    """
    connector = build(client)
    for feed in (HRIS_WORKFORCE, ERP_SALES, DMS_LIBRARY,
                 ERP_JOBS, HRIS_CREDENTIALS, ERP_EQUIPMENT):
        out = connector.pull(feed)
        assert out["outcome"] == "delivered", (feed, out)
    return connector


# ---------------------------------------------------------------------------
# The jobs feed
# ---------------------------------------------------------------------------


def test_the_jobs_feed_lands_the_plant_through_the_http_door(world, plant):
    """Every job in the delta, with the fields the source really carries."""
    counts = world.graph.stats()["by_type"]
    assert counts["Job"] == len(fx.WICK_JOBS)

    machine_shed = job(world, "250553")
    assert machine_shed is not None
    assert machine_shed.props["job_number"] == "250553"
    assert machine_shed.props["name"] == "40x72x14 machine storage"
    # The join, not a column: the estate serves `customerId`.
    assert machine_shed.props["customer_name"] == "Evermore Ridge Estates"
    assert machine_shed.props["plant_site"] == "Mazo"
    assert machine_shed.props["status"] == "In Progress"
    assert machine_shed.props["trailer_number"] == "TRL-07"
    assert machine_shed.props["width"] == 40
    assert machine_shed.props["length"] == 72
    assert machine_shed.props["height"] == 14
    # A dealer sold it, and `builder` is the typed string that says so.
    assert machine_shed.props["builder_name"] == "Wick Buildings — Direct"
    # The two wings measurements, kept as measured rather than added up —
    # `0 x 0` on a building with no wings, and the real pair on one with them.
    assert machine_shed.props["secondary_dimensions"] == "0 x 0"
    assert job(world, "260402").props["secondary_dimensions"] == "12 x 60"
    # Everything the *feed* lands is the ERP's. The two fields beside them are
    # not the feed's to claim and never were: `documents_complete` and
    # `packet_label` are `flow` claims on a non-authoritative type — what the
    # field-documents window made of the same job, which the delivery wakes —
    # exactly as the ontology declares them.
    ours = ("documents_complete", "packet_label")
    assert {
        c.source for c in machine_shed.claims if c.field not in ours
    } == {f"erp:{ERP_JOBS}"}
    assert {
        c.source for c in machine_shed.claims if c.field in ours
    } <= {"flow:field-documents"}
    assert world.graph.check_invariants() == []


def test_the_customer_reaches_the_job_by_a_join_memoized_per_pull(world, plant):
    """The declared-key dereference, and the case it exists for.

    One job names a customer the customers page did not carry. The row is
    fetched once for the whole pull — not once per chunk and not once per job
    — and the name lands on the job as if it had been a column all along.
    """
    machine_shed = job(world, "260449")
    assert machine_shed.props["customer_name"] == "Lone Rock Ag Services LLC"

    fetches = [
        u for u in plant.estate.calls_for("customers")
        if u.endswith("/customers/1009")
    ]
    assert len(fetches) == 1
    # And the customers the page *did* carry cost nothing: the join reads the
    # pulled rows first, which is the whole reason `customers` is a resource
    # on this feed rather than a round trip per job.
    assert not [
        u for u in plant.estate.calls_for("customers")
        if u.endswith("/customers/1000")
    ]


def test_the_job_binds_to_the_customer_the_sell_side_landed(world, plant):
    """`job_for` is one edge into the counterparty book the ERP already wrote.

    The customer names on these jobs are the ERP's own, so they resolve to the
    Companies `erp-sales` minted from the same customer list — an exact
    normalized-name match, no steward, and no second company for one farm.
    """
    machine_shed = job(world, "250553")
    customers = [
        o.label for _, o in
        world.graph.neighbors(machine_shed.id, "job_for", "out")
    ]
    assert customers == ["Evermore Ridge Estates"]
    # Every job whose customer the sell side holds is bound; the one naming an
    # off-page customer minted its own Company from the name it joined.
    bound = [
        o for o in world.graph.find("Job")
        if world.graph.neighbors(o.id, "job_for", "out")
    ]
    assert len(bound) == len(fx.WICK_JOBS)


def test_the_crew_id_resolves_through_the_department_the_hris_landed(
    world, plant
):
    """S1, end to end: the ERP's `crewId` is the crew department's own id.

    A lookup and not a match. The foreman's name is drift on purpose — the
    ERP prints `D. Prellwitz` where the HRIS holds `Dale Prellwitz` — and
    nothing here joins on it, which is why the crew resolves at all.
    """
    machine_shed = job(world, "250553")
    crews = [
        o.label for _, o in
        world.graph.neighbors(machine_shed.id, "assigned_to", "out")
    ]
    assert crews == ["Crew 12"]
    assert machine_shed.props["crew_id"] == "12"
    assert machine_shed.props["foreman_name"] == "D. Prellwitz"

    crew = world.graph.get(object_id("Team", "crew-12"))
    # **The join runs on the cost centre, and both facts are on the Team.**
    # The HRIS addresses this department `DEPT-1022` like every other row it
    # holds; the code the rest of the business writes for it is `12`, and that
    # is what the ERP puts in `jobs.crewId`. Binding on the code is what keeps
    # the seam off a seeding accident — a department whose primary id was the
    # bare string `12` is not what an HRIS looks like — and it is why `Team`
    # declares the field rather than a flow keeping a lookup.
    #
    # Every other department the estate seeds carries an accounting code
    # spelled `CC-####`, so the index has exactly three keys the ERP can name
    # and no blank one for an uncrewed job to fall into.
    assert crew.props["cost_center"] == "12"
    assert crew.props["external_id"] == "DEPT-1022"
    assert {
        o.props["cost_center"] for o in world.graph.find("Team")
        if not o.props["cost_center"].startswith("CC-")
    } == {"12", "14", "18"}
    assert [c.source for c in crew.claims if c.field == "cost_center"] == [
        f"hris:{HRIS_WORKFORCE}"
    ]


def test_a_department_with_no_lead_is_perfectly_ordinary(world, plant):
    """Nine of Wick's twenty-four departments have no named head, and that is
    not a gap.

    The plant floor's own departments report to a superintendent and the HRIS
    holds no lead for any of them. Each has to land as an unremarkable team —
    nesting under the plant, carrying its accounting code, and drawing no
    `led_by` edge at all rather than one to nobody.

    Nothing here is about the *cost centre*, and the reason is worth writing
    down: this estate gives **every** department one (`CC-####` above the
    floor, the bare crew id on the three crews), so `Team.cost_center` is
    never absent in this namespace and a fixture asserting otherwise would
    have been asserting a row the estate does not seed.
    """
    floor = world.graph.get(object_id("Team", "truss-fabrication"))
    assert floor is not None
    assert floor.props["cost_center"] == "CC-4020"
    assert world.graph.neighbors(floor.id, "led_by", "out") == []
    assert [o.label for _, o in
            world.graph.neighbors(floor.id, "subteam_of", "out")] == [
        "Plant — Mazomanie",
    ]
    # And the ones that do have a head draw the edge, so the absence above is
    # a fact about those nine rows and not about the feed.
    crew = world.graph.get(object_id("Team", "crew-12"))
    assert [o.label for _, o in
            world.graph.neighbors(crew.id, "led_by", "out")] == [
        "Dale Prellwitz",
    ]
    assert world.graph.check_invariants() == []


def test_a_job_the_erp_has_not_crewed_is_a_silence_and_not_a_nobody(
    world, plant
):
    """An empty `crewId` draws no edge and costs the binding nothing.

    Three of the twelve jobs in the recording are like this — the ERP enters a
    building before anybody decides who erects it — and one more arrives as a
    one-row delta below. None of them may resolve to whichever department
    happens to sort first, none may be counted against the crew seam, and none
    may end an assignment: `crew_unresolved` is for an id that *named* a crew
    no department holds, and the subcontractor is the only one of those.
    """
    uncrewed = [o for o in world.graph.find("Job") if "crew_id" not in o.props]
    assert sorted(o.props["job_number"] for o in uncrewed) == [
        "260431", "260457", "260470",
    ]
    for row in uncrewed:
        assert world.graph.neighbors(row.id, "assigned_to", "out") == []

    plant.estate.resources[("erp", "jobs")] = [
        *fx.WICK_JOBS, fx.WICK_JOB_NO_CREW,
    ]
    assert plant.pull(ERP_JOBS)["pulled"]["jobs"] == 1
    barn = job(world, "260478")
    assert "crew_id" not in barn.props
    assert "foreman_name" not in barn.props
    assert world.graph.neighbors(barn.id, "assigned_to", "out") == []

    runs = [r for r in world.engine.store.list() if r.flow_id == JOB_FLOW_ID]
    assert sum(r.outputs["crew_unresolved"] for r in runs) == 1
    assert sum(r.outputs["withdrawn"] for r in runs) == 0
    assert world.graph.check_invariants() == []


def test_an_unresolvable_crew_is_an_aggregate_and_the_job_still_lands(
    client, world, plant
):
    """The subcontractor case (S2), and the designed shape of the answer.

    `SUB-KVB` has no HRIS department and never will. The job lands, keeps both
    strings, draws no crew edge, and the run *counts* it — no Task, no
    failure. A per-row escalation here would page somebody every week about a
    subcontractor everybody in the building knows about.
    """
    equipment_shed = job(world, "260411")
    assert equipment_shed is not None
    assert equipment_shed.props["crew_id"] == "SUB-KVB"
    assert equipment_shed.props["foreman_name"] == "KICKAPOO VALLEY BUILDERS"
    assert world.graph.neighbors(equipment_shed.id, "assigned_to", "out") == []

    runs = [r for r in world.engine.store.list() if r.flow_id == JOB_FLOW_ID]
    assert len(runs) == 1
    assert runs[0].outputs["crew_unresolved"] == 1
    # Counted off the recording rather than written down, because the crewed
    # jobs, the subcontracted one and the three nobody has crewed yet are
    # three different readings of the same column.
    assert runs[0].outputs["assigned_to"] == sum(
        1 for row in fx.WICK_JOBS if row["crewId"] in ("12", "14", "18")
    )
    # Nothing was escalated: this feed's only per-row Task is the customer
    # match, and every customer here resolved or minted.
    assert [t for t in world.tasks.list() if t.flow_id == JOB_FLOW_ID] == []


def test_a_job_that_moves_crews_withdraws_the_old_edge(client, world, plant):
    """An observation, not a deletion — and the reconciliation bot's epitaph.

    The second delivery carries one row: the same job, a week later, with a
    different crew. The edge to Crew 12 has its validity ended and stays on
    the record beside the new one, so *which crew has it now* and *who held it
    before* are one traversal over one object. Nothing has to go looking for
    the difference between last week's schedule and this one, which is exactly
    what the weekly folder reconciliation existed to do.
    """
    moved = fx.wick_estate(resources={
        ("erp", "jobs"): [*fx.WICK_JOBS, fx.WICK_JOB_REASSIGNED],
    })
    # A fresh cursor would re-deliver everything; this connector carries the
    # first pull's watermarks, so the delta really is one row.
    plant.estate.resources[("erp", "jobs")] = moved.resources[("erp", "jobs")]
    out = plant.pull(ERP_JOBS)
    assert out["pulled"]["jobs"] == 1

    machine_shed = job(world, "250553")
    live = [
        o.label for _, o in
        world.graph.neighbors(machine_shed.id, "assigned_to", "out")
    ]
    assert live == ["Crew 14"]
    every = {
        o.label: link for link, o in world.graph.neighbors(
            machine_shed.id, "assigned_to", "out", include_reversed=True,
        )
    }
    assert set(every) == {"Crew 12", "Crew 14"}
    withdrawn = every["Crew 12"]
    assert not withdrawn.live
    assert withdrawn.valid_to_iso and withdrawn.reversed_at_iso
    assert withdrawn.reversed_by == f"flow:{JOB_FLOW_ID}"
    assert "Crew 12" in withdrawn.reversal_reason
    # And the job now says the new crew, both ways it can be asked.
    assert machine_shed.props["crew_id"] == "14"
    assert world.graph.check_invariants() == []

    runs = [r for r in world.engine.store.list() if r.flow_id == JOB_FLOW_ID]
    assert sorted(r.outputs["withdrawn"] for r in runs) == [0, 1]


def test_an_ambiguous_customer_raises_a_steward_task_and_lands_the_job(
    world, plant
):
    """The per-row escalation the contract declares, and its one shape.

    A job names a customer the sell side never carried, and the name the join
    filled in sits between the governed floor and the auto-link threshold. So
    nothing is asserted about the counterparty — no link, no second dairy
    minted — a Task goes to the desk the Mapping names, and **the job lands
    anyway**, because a feed that stopped on row 47 would hold up rows 48
    through 200 for no reason.
    """
    plant.estate.resources[("erp", "jobs")] = [
        *fx.WICK_JOBS, fx.WICK_JOB_NEAR_MISS,
    ]
    plant.estate.off_page[("erp", "customers")] = fx.WICK_CUSTOMER_NEAR_MISS
    out = plant.pull(ERP_JOBS)
    assert out["pulled"]["jobs"] == 1

    shed = job(world, "260474")
    assert shed is not None
    assert shed.props["customer_name"] == "Hoffman Ridge Farms"
    assert world.graph.neighbors(shed.id, "job_for", "out") == []
    # No second Hoffman farm: the row is parked, not resolved in either
    # direction. The one the sell side landed is the *stale* half of the ERP's
    # own duplicate, which is what makes this the estate's designed trap
    # rather than an invented one.
    assert sorted(
        o.label for o in world.graph.find("Company")
        if "Hoffman" in o.label
    ) == ["Hoffmann Ridge Farms"]

    task = next(
        t for t in world.tasks.list()
        if t.kind == "match_review" and "Hoffman Ridge Farms" in t.title
    )
    assert task.queue == "data-stewardship"
    assert task.flow_id == JOB_FLOW_ID
    assert task.proposal.target == "company-master"
    runs = [r for r in world.engine.store.list() if r.flow_id == JOB_FLOW_ID]
    assert sum(r.outputs["awaiting_steward"] for r in runs) == 1
    assert sum(r.outputs["customer_unresolved"] for r in runs) == 1


def test_a_stewards_answer_is_appliable_because_the_reference_is_a_row(
    world, plant
):
    """Why the customer reference lands as a `SourceRecord` of its own.

    A Task nobody can answer is worse than no Task, and a decision is applied
    against *the row that escalated* — `record_kind` on the landed record is
    what says which book it writes into. So the job feed lands the reference it
    joined, keyed by the normalized name, and the same steward path the CRM and
    the sell side use answers it: no second machinery, and no per-feed
    exception in `apply_match_decision`.
    """
    from app.task_queue import TaskDecision

    plant.estate.resources[("erp", "jobs")] = [
        *fx.WICK_JOBS, fx.WICK_JOB_NEAR_MISS,
    ]
    plant.estate.off_page[("erp", "customers")] = fx.WICK_CUSTOMER_NEAR_MISS
    plant.pull(ERP_JOBS)

    task = next(
        t for t in world.tasks.list()
        if t.kind == "match_review" and "Hoffman Ridge Farms" in t.title
    )
    farm = world.graph.get(object_id("Company", "Hoffmann Ridge Farms"))
    decided = task.model_copy(update={
        "decision": TaskDecision(
            verdict="corrected", decided_by="steward@wickbuildings.com",
            note="one farm — the sell side keeps the old spelling",
            payload={"object_id": farm.id},
        ),
    })
    result = world.sync.apply_match_decision(decided)

    assert result["action"] == "linked"
    assert result["object_type"] == "Company"
    assert result["object_id"] == farm.id
    row = landed(world, ERP_JOBS, "hoffman-ridge-farms")
    assert row is not None
    link = next(
        link for link, other in world.graph.neighbors(
            farm.id, "company_identified_as", "out",
        )
        if other.id == row.id
    )
    assert link.method == "human"


def test_the_yes_no_flags_land_verbatim_on_the_row(world, plant):
    """The connector renames; the flow judges. This is the connector's half.

    The landed row holds the ERP's own words, so a change to the governed
    vocabulary table can be re-applied without re-pulling the estate. A
    connector that shipped `released=True` would have made a governed decision
    invisible and un-re-runnable — and would have left nothing to re-derive
    from when a plant edited the table.
    """
    import json

    # `8017` is the ERP's internal id for job `260470` — the estate's own
    # sequential integer, which is not the job number and never has been.
    row = landed(world, ERP_JOBS, "8017")
    assert row is not None
    payload = json.loads(row.props["payload"])
    assert payload["do_not_fab"] == "Yes"
    assert payload["out_to_yard"] == "Yes"
    # The production window arrives as a phrase for the same reason.
    assert payload["week_position"] == "Week 3"
    # `doNotFab` is `No` on another job and just as verbatim there: the point
    # is the absence of an opinion in the puller, not the value. **Every one
    # of the estate's twenty-two jobs is `outToYard: "Yes"`**, so the other
    # branch of that column is not something this estate can be asked for —
    # recorded as it is rather than bent to give this line a second case.
    shed = json.loads(landed(world, ERP_JOBS, "8000").props["payload"])
    assert shed["do_not_fab"] == "No"
    assert shed["out_to_yard"] == "Yes"
    # A job already in the building has no window at all, and the column
    # arrives empty rather than as a guess.
    assert shed["week_position"] == ""


def test_the_governed_vocabulary_turns_the_sources_words_into_facts(
    world, plant
):
    """The flow's half, and the seam it closes.

    Lane S left `release_state`, `hold_reason` and `week_position` empty on
    purpose: translating in the estate connector would have put a governed
    decision below the Mapping. Correct, and it left the eligibility rule with
    nothing to read but raw strings out of a landed payload — which is exactly
    what the ontology exists to stop. So `job-sync` reads the `## Source
    vocabulary` table itself, in the same layer that already reads the
    resolution thresholds, and claims the three fields in the guarded write.
    """
    held = job(world, "260470")
    assert held.props["release_state"] == "released"
    assert held.props["hold_reason"] == "DNF"
    assert held.props["week_position"] == 3
    # Every claim still comes from the ERP's authority: the *words* are the
    # plant's governed page, and what asserted them is the system that holds
    # the flags they were read off.
    assert [c.source for c in held.claims if c.field == "release_state"] == [
        f"erp:{ERP_JOBS}"
    ]

    shed = job(world, "260431")
    assert shed.props["release_state"] == "released"
    assert shed.props["week_position"] == 2
    # `doNotFab: No` has a row in the table and the row says *claim nothing*.
    # A job nobody held reads as unheld rather than as held for a reason
    # spelled "none" — which is why the parser keeps "no row" and "a row
    # mapping to nothing" apart.
    assert "hold_reason" not in shed.props

    # And a job already in the building: the ERP clears `weekPosition` once
    # the work starts, and the governed page says a job the screen puts
    # outside its three windows *is* a zero rather than an error.
    machine_shed = job(world, "250553")
    assert machine_shed.props["week_position"] == 0

    runs = [r for r in world.engine.store.list() if r.flow_id == JOB_FLOW_ID]
    assert len(runs) == 1
    # A release state and a week position on every job, plus the one hold —
    # counted as facts rather than as jobs, because "every job got at least
    # one" is a number that can never say anything.
    assert runs[0].outputs["vocabulary_translated"] == (
        2 * len(fx.WICK_JOBS) + 1
    )
    assert runs[0].outputs["vocabulary_unmapped"] == 0


def test_the_step_names_the_revision_the_words_came_from(world, plant):
    """A claim derived from a governed word is auditable only by its revision.

    Same contract `x1 read-resolution-policy` has carried since the vendor
    feed: the step says which page at which revision, the run carries the ref,
    and a supervisor disputing a release state is disputing a line on a page
    with a date on it.
    """
    run = next(
        r for r in world.engine.store.list() if r.flow_id == JOB_FLOW_ID
    )
    step = run.step("x2")
    assert step is not None
    assert step.name == "read-source-vocabulary"
    assert "knowledge:plant-scheduling-parameters@rev1" in step.summary
    assert "2 release word(s)" in step.summary
    assert "3 week position(s)" in step.summary
    # `anything else, or absent` is a row of the table like any other, and the
    # step reports what it read rather than what the flow shipped with.
    assert "anything else is week 0" in step.summary
    assert "knowledge:plant-scheduling-parameters@rev1" in run.refs
    # And the write that used it cites it too, so the claim and the revision
    # travel together rather than one step away from each other.
    assert "knowledge:plant-scheduling-parameters@rev1" in run.step("a2").refs


def test_a_missing_governed_page_falls_back_and_the_run_says_so(client, world):
    """The one thing tolerance may never do is stay quiet.

    A deployment whose corpus is missing the page still lands translated jobs
    — three dead fields would be worse — but the words are the flow's and not
    the plant's, and a run that did not say which of the two it used would be
    indistinguishable from one that read the customer's own table.
    """
    world.knowledge.repo.delete("plant-scheduling-parameters")
    connector = build(client)
    for feed in (HRIS_WORKFORCE, ERP_SALES, ERP_JOBS):
        assert connector.pull(feed)["outcome"] == "delivered"

    run = next(
        r for r in world.engine.store.list() if r.flow_id == JOB_FLOW_ID
    )
    step = run.step("x2")
    assert "not in the corpus" in step.summary
    assert "the words the flow shipped with" in step.summary
    # No `@rev`: there was no revision to name, which is the honest ref.
    assert step.summary.startswith("knowledge:plant-scheduling-parameters:")
    assert "@rev" not in step.summary

    # And the jobs are translated anyway, by the built-in copy of the table.
    held = job(world, "260470")
    assert held.props["release_state"] == "released"
    assert held.props["hold_reason"] == "DNF"
    assert held.props["week_position"] == 3


def test_the_source_vocabulary_parser_reads_the_governed_page_as_written():
    """Held against the asset itself, and against the trap on the same page.

    The document carries nine other tables and a quoted `- goal max: —` under
    its `## Open` heading. Section-scoping is what keeps that out of the
    vocabulary, and this is the assertion that says the scoping works rather
    than that it exists.
    """
    from app.connectors.production import (
        SourceVocabulary,
        parse_source_vocabulary,
    )

    content = (
        pathlib.Path(__file__).parent.parent / "seed"
        / "plant-scheduling-parameters.md"
    ).read_text()
    vocabulary = parse_source_vocabulary(content)
    assert vocabulary.found_asset and vocabulary.found_table
    assert vocabulary.release_state == {"yes": "released",
                                        "no": "not released"}
    assert vocabulary.hold_reason == {"yes": "DNF", "no": ""}
    assert vocabulary.week_position == {"week-1": 1, "week-2": 2, "week-3": 3}
    assert vocabulary.week_position_default == 0
    # `word` answers three ways, and the difference is the whole design: a
    # fact, an explicit nothing, and "this table has never heard that word".
    assert vocabulary.word("release_state", "Yes") == "released"
    assert vocabulary.word("hold_reason", "No") == ""
    assert vocabulary.word("release_state", "Pending") is None
    assert vocabulary.number("Week 4") == 0

    # A page with no such section leaves the built-in words standing and says
    # which of the two happened — a supervisor mid-edit must not be able to
    # make a run raise, and must not be able to make one lie either.
    partial = parse_source_vocabulary("# A page\n\n## Open\n\n- goal max: —\n")
    assert partial.found_asset and not partial.found_table
    assert partial.release_state == SourceVocabulary().release_state
    assert partial.week_position_default == 0


def test_upstream_done_lists_the_department_slugs_the_source_marked(
    world, plant
):
    """The one vocabulary the connector does spell, and why it is allowed to.

    Which of ten columns belongs to which department is the ERP's schema; the
    slug is the third column of the governed `## Departments` table, so
    neither system learns the other's spelling.
    """
    # One department has finished with it and nine have not.
    assert job(world, "260457").props["upstream_done"] == ["truss-cut"]
    # Everything done: the floor has finished with it and it is on the truck.
    assert job(world, "250553").props["upstream_done"] == [
        slug for _column, slug in ERP_DEPARTMENT_COLUMNS
    ]
    # And a job nobody has started says so with an empty list rather than by
    # having no answer: the ten columns ride on every row.
    assert job(world, "260470").props["upstream_done"] == []


def test_the_department_rename_table_agrees_with_the_governed_asset():
    """Two copies of one table, held to each other.

    The pairing is declared for a person in `plant-scheduling-parameters`'
    `## Departments` table and for the machine in `ERP_DEPARTMENT_COLUMNS` —
    the connector may not read the corpus, which is why the copy exists at
    all. What it may not do is drift, so this is the assertion that says so.
    """
    asset = (
        pathlib.Path(__file__).parent.parent / "seed"
        / "plant-scheduling-parameters.md"
    ).read_text()
    pairs = re.findall(r"^\|\s*[^|]+\|\s*(\w+Done)\s*\|\s*([a-z-]+)\s*\|$",
                       asset, re.MULTILINE)
    assert pairs, "the governed asset no longer carries a Departments table"
    assert tuple(pairs) == ERP_DEPARTMENT_COLUMNS


# ---------------------------------------------------------------------------
# The credential feed
# ---------------------------------------------------------------------------


def credential(world, external_id: str):
    return world.graph.get(
        object_id("Credential", f"{HRIS_CREDENTIALS}:{external_id}")
    )


def test_the_credential_feed_joins_employee_ids_to_work_emails(world, plant):
    """The second join, for the second reason: both resources carry an id only.

    The holder is bound by an address the workforce feed already keyed on, so
    the edge is drawn rather than matched — and the one driver whose row the
    employees page did not carry costs exactly one GET for the whole pull.
    """
    forklift = credential(world, "TRN-1001")
    assert forklift is not None
    assert forklift.props["holder_email"] == (
        "doug.zwettler@wickbuildings.com"
    )
    assert forklift.props["result"] == "pass"
    assert forklift.props["provider"] == "Wick Safety"
    holders = [
        o.label for _, o in
        world.graph.neighbors(forklift.id, "holds_credential", "in")
    ]
    assert holders == ["Doug Zwettler"]

    fetches = [
        u for u in plant.estate.calls_for("employees")
        if u.endswith("/employees/EMP-1026")
    ]
    assert len(fetches) == 1
    assert credential(world, "DQ-1004:cdl").props["holder_email"] == (
        "cody.statz@wickbuildings.com"
    )
    assert world.graph.check_invariants() == []


def test_one_driver_row_becomes_one_credential_per_dated_column(world, plant):
    """Four unrelated expiries on one row, fanned out — and a null is a null.

    An absent CDL date is the HRIS saying this driver has no CDL on file, so
    it yields no credential at all rather than an expiry of nothing. Each
    record gets its own key suffix, because the natural key is the source's
    row id and four permissions cannot share one.
    """
    terry = {
        "DQ-1001:cdl": "licence",
        "DQ-1001:medical-card": "medical",
        "DQ-1001:psp": "clearance",
        "DQ-1001:clearinghouse": "clearance",
    }
    for external_id, category in terry.items():
        row = credential(world, external_id)
        assert row is not None, external_id
        assert row.props["category"] == category
        assert row.props["holder_email"] == "terry.nachreiner@wickbuildings.com"
        # A driver file carries a date and nothing else: no result, no
        # provider, no completion. Left absent rather than defaulted.
        assert "result" not in row.props
        assert "provider" not in row.props

    # Vern Oimoen has no PSP on file: three permissions, not four.
    assert credential(world, "DQ-1002:cdl") is not None
    assert credential(world, "DQ-1002:psp") is None
    assert credential(world, "DQ-1002:clearinghouse") is not None

    # And a person really does hold both kinds: one training record and four
    # dated permissions off one driver file, on one roster.
    # Asked with the object's own id and not its alias: a natural key is how
    # records find an entity, never what it is.
    terry = world.graph.get(
        object_id("Employee", "terry.nachreiner@wickbuildings.com")
    )
    held = [
        (o.props.get("category"), o.props["credential_type"]) for _, o in
        world.graph.neighbors(terry.id, "holds_credential", "out")
    ]
    assert sorted(held, key=str) == [
        ("clearance", "Clearinghouse"),
        ("clearance", "PSP"),
        ("licence", "CDL"),
        ("medical", "Medical card"),
        # The course he sat, beside the three permissions he holds. The HRIS
        # files it under `Safety` — its own taxonomy is the department that
        # runs a course — and `CREDENTIAL_CATEGORIES` renames that to
        # `training`, which is what a credential *is* rather than who taught
        # it.
        ("training", "First Aid / CPR / AED"),
    ]


def test_a_category_the_vocabulary_does_not_hold_is_counted_not_rounded(
    world, plant
):
    """An orientation is not a certification, and guessing would say it was.

    **This estate makes the point about its whole training matrix.** Wick's
    HRIS files every course by the department that runs it — `Safety`,
    `Equipment` — while the declared vocabulary files a credential by what it
    *is*. The two are reconciled in one named place, `CREDENTIAL_CATEGORIES`,
    because that is a renaming and the connector plane is where this estate's
    word becomes ours. An orientation lands as `training` and an equipment
    ticket as `certification`; neither is guessed from the course name, and
    the orientation is emphatically not promoted to a certification for
    sounding like one.

    What the aggregate is still for is the word the table has *not* been
    taught. That is why the count is asserted at zero here rather than
    deleted: it is the thing that would notice a sixth course category
    arriving, which is exactly how this defect was found — every training row
    in the namespace landed categoryless, and the compliance window, which
    selects on the governed `categories tracked` list, could not see one.
    """
    orientation = credential(world, "TRN-1019")
    assert orientation is not None
    assert orientation.props["category"] == "training"
    assert orientation.props["credential_type"] == (
        "New Employee Safety Orientation"
    )

    runs = [
        r for r in world.engine.store.list() if r.flow_id == CREDENTIAL_FLOW_ID
    ]
    assert len(runs) == 1
    # Every course this estate files is now a word the table holds. The
    # counter stays asserted, at zero, because a number nobody checks is a
    # number that stops meaning anything.
    assert runs[0].outputs["category_unmapped"] == 0
    # And the other aggregate on the same run, for the same reason: two of
    # Cody Statz's dated permissions name a driver the *workforce* feed has
    # never delivered — the credential feed's join found his address, and no
    # `Employee` holds it. Counted, never minted: a driver file is not
    # evidence of employment.
    assert runs[0].outputs["holder_unresolved"] == 5
    assert credential(world, "DQ-1004:cdl").props["holder_email"] == (
        "cody.statz@wickbuildings.com"
    )


def test_the_issuing_systems_status_is_not_the_derived_one(world, plant):
    """`Completed` is a fact about an assignment; `expiring` is a verdict.

    Nothing here claims `Credential.status`: it is derived from the dates
    against the governed notice stages, and a feed that wrote its own input
    into that field would make the compliance sweep read its own output.
    """
    for external_id in ("TRN-1001", "TRN-1013", "DQ-1001:cdl"):
        row = credential(world, external_id)
        assert "status" not in row.props, external_id
        assert "last_notified_on" not in row.props
        assert "retain_until" not in row.props
    # A sitting that was failed is on file as failed. A training record that
    # exists is not a training record that was passed.
    assert credential(world, "TRN-1015").props["result"] == "fail"


def test_the_expiry_ladder_reaches_every_governed_notice_stage(world, plant):
    """The fixture's own contract, asserted where a later lane will read it.

    A compliance sweep is only testable if the world contains work at each
    stage, so the recorded estate puts something 61, 45, 30, 21, 15 and 7 days
    out and one thing ten days past due.
    """
    expiries = {
        o.props.get("expires_on") for o in world.graph.find("Credential")
    }
    for offset in (61, 45, 30, 21, 15, 7, -10):
        assert fx.wick_date(offset) in expiries, offset


# ---------------------------------------------------------------------------
# The equipment feed
# ---------------------------------------------------------------------------


def unit(world, tag: str):
    return world.graph.get(object_id("Equipment", f"{ERP_EQUIPMENT}:{tag}"))


def test_a_null_next_due_date_lands_untracked_and_writes_no_inspection(
    world, plant
):
    """The feed's whole reason for existing, and the SDD's skid-steer rule.

    Not tracked is a fact about the machine, derived once on the way in — so a
    sweep reads a claim instead of re-deciding what a null means, and no
    `Inspection` part exists to be reported as overdue forever.
    """
    skid = unit(world, "SKD-01")
    assert skid is not None
    assert skid.props["inspection_tracked"] == "no"
    assert skid.props["category"] == "machine"
    assert world.graph.neighbors(skid.id, "has_inspection", "out") == []

    bare = unit(world, "SKD-03")
    assert bare.props["inspection_tracked"] == "no"
    assert world.graph.neighbors(bare.id, "has_inspection", "out") == []
    # Both of them, and only them: the estate's two skid steers are the only
    # units in the register with neither an inspection nor a due date.
    assert sorted(
        row["assetTag"] for row in fx.WICK_ASSETS
        if row["nextInspectionDueDate"] is None
    ) == ["SKD-01", "SKD-03"]

    truck = unit(world, "TRK-11")
    assert truck.props["inspection_tracked"] == "yes"
    assert truck.props["category"] == "vehicle"
    assert truck.props["identifier"] == "1NP4L70X8KD"
    # One column on the shape, two kinds of unit: a serial where the source
    # has one, the plate where it does not.
    assert unit(world, "TRK-17").props["identifier"] == "WI 45330"
    # A telehandler is a `Lift` here, because the ERP's `assetType` is a
    # five-value picklist and the model is in the description.
    assert unit(world, "LFT-02").props["category"] == "lift"


def test_the_examination_is_written_from_the_two_dates_on_the_asset(
    world, plant
):
    """One `Inspection` per landed date, and nothing invented around it."""
    truck = unit(world, "TRK-11")
    inspections = [
        o for _, o in world.graph.neighbors(truck.id, "has_inspection", "out")
    ]
    assert len(inspections) == 1
    found = inspections[0]
    assert found.props["inspected_on"] == fx.wick_date(-350)
    assert found.props["next_due_on"] == fx.wick_date(15)
    # No result, no inspector, no notes: the source holds two dates and says
    # nothing about what was found, and a `pass` inferred from a due date
    # would be inventing the finding a certificate carries.
    assert "result" not in found.props
    assert "inspector" not in found.props
    # A part is identified through its owner — the weak-entity rule.
    assert truck.id in found.natural_key
    assert world.graph.check_invariants() == []

    # Twelve of the fourteen units are on a cycle, and every one of them
    # carries the date of its last examination — so the part is written for
    # all twelve and `due_without_history` is **zero**. The counter is for a
    # unit delivered new, or bought from somebody who kept the paperwork:
    # counted rather than written, because a part keyed by an examination date
    # cannot be written without one. Wick's register has no such unit, which
    # is a fact about the estate and is asserted here so that it stays one.
    runs = [
        r for r in world.engine.store.list() if r.flow_id == EQUIPMENT_FLOW_ID
    ]
    tracked = [row for row in fx.WICK_ASSETS
               if row["nextInspectionDueDate"] is not None]
    assert runs[0].outputs["tracked"] == len(tracked) == 12
    assert runs[0].outputs["inspections"] == len(tracked)
    assert runs[0].outputs["due_without_history"] == 0
    assert all(row["lastInspectionDate"] for row in tracked)


def test_the_erps_site_word_resolves_to_the_department_it_names(world, plant):
    """The rename that makes `operated_by` an edge instead of a count.

    `assets.homeSite` is the ERP's site picklist and holds **exactly two
    words** — `Mazo` and `Fork West` — while the HRIS departments are `Plant —
    Mazomanie` and `Plant — Fork West`. So before the pairing every asset in
    the register resolved to nothing. That was not a reconciliation anybody
    made; it was the count standing in for one. The table pairs the words, on
    the same footing as every other place one estate's word becomes ours.

    There is no third case to assert and there cannot be: because `homeSite`
    is picklisted, an asset filed under a department name is not something
    this estate can serve, and neither is a site the pairing has never heard
    of — see the aggregate test below.
    """
    def sites_of(tag: str) -> list[str]:
        return [
            o.label for _, o in
            world.graph.neighbors(unit(world, tag).id, "operated_by", "out")
        ]

    # Renamed: the ERP's two site words, onto the two plants.
    stranded = unit(world, "TRK-11")
    assert stranded.props["home_site"] == "Mazo"
    assert sites_of("TRK-11") == ["Plant — Mazomanie"]
    assert sites_of("SKD-01") == ["Plant — Mazomanie"]
    assert unit(world, "LFT-05").props["home_site"] == "Fork West"
    assert sites_of("LFT-05") == ["Plant — Fork West"]
    assert sites_of("SKD-03") == ["Plant — Fork West"]
    # The `home_site` claim is still the source's own word. The rename decides
    # which team the edge goes to; it does not overwrite what the ERP said.
    assert stranded.props["home_site"] == "Mazo"

    runs = [
        r for r in world.engine.store.list() if r.flow_id == EQUIPMENT_FLOW_ID
    ]
    assert runs[0].outputs["operated_by"] == len(fx.WICK_ASSETS)
    assert runs[0].outputs["site_unresolved"] == 0


def test_a_site_the_pairing_has_never_heard_of_cannot_arrive_from_this_estate(
    world, plant
):
    """The aggregate is right to exist and has nothing to count here.

    A third plant must arrive as a number somebody can act on rather than as a
    silent absence — still an aggregate and still not a Task, because pairing
    two picklists is one governed decision and not a question to ask per
    truck. But `erp.ENTITIES["assets"]` **picklists `homeSite` to
    `PLANT_SITES`**, and `SITE_DEPARTMENTS` pairs both of those words, so no
    route in the estate can put a third site on an asset: not the seed, which
    goes through `_clean`, and not a PATCH, which goes through the same check.
    `site_unresolved` is therefore structurally zero for this namespace.

    Asserted rather than left implicit, and with a fixture row rather than an
    invented one: a recording that made up an unpaired site to exercise the
    counter would have been testing a shape nothing serves — which is the one
    thing this file exists not to do. The day the ERP's picklist grows a third
    plant, this test is the one that should fail.
    """
    from app.connectors.compliance import SITE_DEPARTMENTS

    sites = {row["homeSite"] for row in fx.WICK_ASSETS}
    assert sites == {"Mazo", "Fork West"}
    assert {_site_slug(site) for site in sites} == set(SITE_DEPARTMENTS)

    runs = [
        r for r in world.engine.store.list() if r.flow_id == EQUIPMENT_FLOW_ID
    ]
    assert [r.outputs["site_unresolved"] for r in runs] == [0]
    assert [r.outputs["category_unmapped"] for r in runs] == [0]
    # Nothing was escalated: this feed raises no per-row Task at all.
    assert [t for t in world.tasks.list()
            if t.flow_id == EQUIPMENT_FLOW_ID] == []


def test_the_site_rename_table_agrees_with_the_governed_asset():
    """Two copies of one pairing, held to each other.

    The plant sites are named for a person in `plant-scheduling-parameters` —
    the `plant sites:` line under `## Source documents`, and the plant rows of
    the `## Departments` table — and for the machine in `SITE_DEPARTMENTS`.
    The connector plane may not read the corpus, which is why the copy exists;
    what it may not do is drift, so this is the assertion that says so.
    """
    from app.connectors.compliance import SITE_DEPARTMENTS

    asset = (
        pathlib.Path(__file__).parent.parent / "seed"
        / "plant-scheduling-parameters.md"
    ).read_text()
    listed = re.search(r"^- plant sites:\s*(.+)$", asset, re.MULTILINE)
    assert listed, "the governed asset no longer names the plant sites"
    sites = [s.strip() for s in listed.group(1).split(",")]
    assert {_site_slug(s) for s in sites} == set(SITE_DEPARTMENTS)
    # And each department the table pairs a site with is a department the
    # governed table really holds, spelled the HRIS's way.
    hris_names = re.findall(r"^\|\s*([^|]+?)\s*\|\s*\w+Done\s*\|", asset,
                            re.MULTILINE)
    for site, department in SITE_DEPARTMENTS.items():
        assert site in {_site_slug(s) for s in sites}, site
        # The plants are not `<dept>Done` rows — they are the two locations
        # those departments sit at — so what is pinned is that the pairing
        # names a real HRIS department name and not a slug or a guess.
        assert department.startswith("Plant — ")
        assert department not in hris_names


def test_the_site_slug_agrees_with_the_one_the_hris_feed_keyed_on():
    """Two copies of one derivation, held to each other.

    `estate.team_slug` is where it is canonically written; the connector plane
    cannot import the module that reaches the network, so it repeats it. This
    is the assertion that keeps the repetition honest — a drift here would
    file every truck under nothing.
    """
    for name in ("Transportation", "Plant — Mazomanie", "Plant — Fork West",
                 "Hardware & Trim Packaging", "Crew 12", "Mazo", ""):
        assert _site_slug(name) == team_slug(name), name


def test_the_jobs_folders_are_edges_to_both_trees_that_hold_them(world, plant):
    """A packet is a query over edges, not a folder somebody has to create.

    `250553` is filed twice — the office's copy of the portal scans and the
    floor's cut sheets, two trees with two retention rules — so it draws two
    edges, which is why the link is many-to-many.
    """
    machine_storage = job(world, "260431")
    paths = sorted(
        o.props["path"] for _, o in
        world.graph.neighbors(machine_storage.id, "job_folder", "out")
    )
    assert paths == ["Job Documents/260431", "PlantCutSheets/Mazo/260431"]
    # And a job nobody has filed anything for draws none, which is the case
    # the field-documents flow counts rather than treats as an error.
    assert world.graph.neighbors(
        job(world, "260449").id, "job_folder", "out",
    ) == []
    # The trap on the same tree: somebody once filed `250553` under a packet
    # label instead of a job number, and the folder is still there holding a
    # superseded set of plans. Its leaf is `250553 Evermore`, so the index by
    # leaf draws nothing from it — which is the right answer, and the reason
    # the folder is in the recording at all.
    assert sorted(
        o.props["path"] for _, o in
        world.graph.neighbors(job(world, "250553").id, "job_folder", "out")
    ) == ["Job Documents/250553"]
    assert any(row["name"] == "250553 Evermore" for row in fx.WICK_FOLDERS)


# ---------------------------------------------------------------------------
# The contracts, and the boundaries
# ---------------------------------------------------------------------------


def test_the_three_mappings_declare_what_the_flows_do():
    from app.mappings import MAPPINGS, validate_mappings

    assert validate_mappings() == []
    by_source = {m.source: m for m in MAPPINGS}

    jobs = by_source[ERP_JOBS]
    assert jobs.strategy == "reify" and jobs.lands_as == "Job"
    assert jobs.resolves_to == ("Company", "Team")
    assert jobs.precedence_source == "erp"
    assert jobs.collection == "production-records"
    assert jobs.escalation == "per-row task"
    assert jobs.steward_queue == "data-stewardship"
    by_column = {b.column: b for b in jobs.identity}
    assert set(by_column) == {"customer_name", "job_number", "crew_id"}
    # The binding names the field it joins on, and the field is on the type.
    # `external_id` would have been the HRIS's department id (`DEPT-1022`),
    # which is a different string about the same department — and would have
    # made the seam depend on an estate keying its departments by crew number.
    assert by_column["crew_id"].object_type == "Team"
    assert "cost_center" in by_column["crew_id"].key
    assert "cost_center" in {
        f.name for f in ONTOLOGY.object_type("Team").fields
    }
    # The three decisions the contract has to carry, or the export lies about
    # what the flow does.
    assert "cost centre" in jobs.notes
    assert "withdraws the old edge" in jobs.notes
    assert "countable aggregate" in jobs.notes
    assert "customerId" in jobs.notes

    creds = by_source[HRIS_CREDENTIALS]
    assert creds.strategy == "bind" and creds.lands_as == ""
    assert creds.resolves_to == ("Employee", "Credential")
    assert creds.precedence_source == "hris"
    assert creds.collection == "compliance-records"
    assert creds.escalation == "windowed aggregate"
    assert "employeeId" in creds.notes

    kit = by_source[ERP_EQUIPMENT]
    assert kit.strategy == "reify" and kit.lands_as == "Equipment"
    assert kit.resolves_to == ("Team",)
    assert kit.collection == "equipment-register"
    assert kit.escalation == "windowed aggregate"
    assert "nextInspectionDueDate" in kit.notes
    # The site pairing is a named rename table and the contract says where —
    # an export that still described it as an open reconciliation would be
    # telling an operator to make a decision somebody already made.
    assert "SITE_DEPARTMENTS" in kit.notes
    assert "counted on the run" in kit.notes


def test_the_three_triggers_start_the_three_flows():
    """Declared with the bodies they name, in one commit.

    `Dispatcher.emit` starts every matched Trigger without guarding
    `RunEngine.start`, which raises for an unregistered flow — so a row landed
    ahead of its package would not fail in one run, it would take the delivery
    door down for every feed.
    """
    from app import tasks as packages

    for trigger_id, event, flow_id in (
        ("jobs-delivered", "JobRecordsDelivered", JOB_FLOW_ID),
        ("credentials-delivered", "CredentialRecordsDelivered",
         CREDENTIAL_FLOW_ID),
        ("equipment-delivered", "EquipmentRecordsDelivered",
         EQUIPMENT_FLOW_ID),
    ):
        trigger = TRIGGERS[trigger_id]
        assert trigger.event == event
        assert trigger.starts == flow_id
        assert trigger.wakes == "window"
        assert packages.shipped_version_numbers(flow_id) == {1}
        assert packages.default_version(flow_id) == 1


def test_every_step_the_package_declares_is_a_step_the_body_runs(world, plant):
    """The fleet page audits the map against the journal, so this does too."""
    from app import tasks as packages

    for flow_id in (JOB_FLOW_ID, CREDENTIAL_FLOW_ID, EQUIPMENT_FLOW_ID):
        run = next(
            r for r in world.engine.store.list() if r.flow_id == flow_id
        )
        declared = [s.id for s in packages.shipped_versions(flow_id)[1].STEPS]
        assert [s.step_id for s in run.steps] == declared, flow_id


def test_a_feed_whose_delta_is_empty_is_unchanged_not_a_fault(client, world):
    """A healthy feed with zero rows, and the Sources screen reads it as one.

    Not a resource nobody serves — Wick's ERP really does hold a vendor book,
    and the recording carries it because the fleet's `vendorId`s point into
    it. The empty delta is the **second** pull: the first moves the cursor
    past everything the estate has, and the one after it carries nothing at
    all, which is what almost every pull of a healthy feed looks like. The
    cursor does not move, no run starts, and the outcome is `unchanged`. That
    is a real reading rather than a failure, and it is different in kind from
    a 404.
    """
    connector = build(client)
    first = connector.pull(ERP_VENDORS)
    assert first["outcome"] == "delivered"
    assert first["pulled"] == {"vendors": len(fx.WICK_VENDORS)}
    watermarks = dict(connector.state(ERP_VENDORS).watermarks)
    assert watermarks

    out = connector.pull(ERP_VENDORS)
    assert out["outcome"] == "unchanged"
    assert out["pulled"] == {"vendors": 0}
    assert dict(connector.state(ERP_VENDORS).watermarks) == watermarks
    assert connector.state(ERP_VENDORS).last_error == ""
    assert world.graph.stats()["by_type"]["Vendor"] == len(fx.WICK_VENDORS)


def test_the_namespace_activates_only_the_apps_it_runs(client):
    """No CRM and no PSA on this one, and the FEEDS registry is not consulted
    about it: the namespace's own `apps` list is the filter."""
    connector = build(client)
    assert connector.discover()["id"] == "wick"
    active = {f.feed for f in connector.active_feeds()}
    assert {ERP_JOBS, HRIS_CREDENTIALS, ERP_EQUIPMENT} <= active
    assert "crm-book" not in active and "psa-book" not in active
    with pytest.raises(EstateError):
        connector.pull("crm-book")


#: The three modules `estate.py`'s own docstring says it may not reach. Held
#: here as well as in `test_estate.py`, because three new feeds are three new
#: chances for a build function to start scoring something.
OFF_LIMITS = {"graph", "resolution", "gateway"}


def test_the_new_feeds_did_not_give_the_connector_an_opinion():
    source = pathlib.Path(__file__).parent.parent / "app" / "estate.py"
    tree = ast.parse(source.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = (node.module or "").strip(".")
            if module:
                imported.add(module.rsplit(".", 1)[-1])
            else:
                imported.update(a.name.rsplit(".", 1)[-1] for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name.rsplit(".", 1)[-1] for a in node.names)
    assert imported & OFF_LIMITS == set()
    # And it may not reach the corpus either, which is why the department
    # rename table is a module constant with a pointer to the governed asset.
    assert "knowledge" not in imported
