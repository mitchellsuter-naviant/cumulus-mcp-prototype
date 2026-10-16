"""The job packet, and the promises it must not break.

Six groups of property, and the first two are what the flow exists for:

1. **The naming convention is a governed value, and both traps hold.** An
   article-leading customer name resolves through the stopword list
   (`The Willows Equine LLC` on 250661 → `250661 Willows`) and two customers
   sharing a first keyword resolve through the collision rule (`250553
   Evermore Ridge` and `250774 Evermore Farms`). Neither is a literal in code:
   editing the asset moves the label.
2. **The current revision is a comparison, not a position.** The design's
   *"the topmost one is the most recent"* is not a rule this system can keep,
   so the highest revision wins, ties break on the creation stamp at full
   precision, and the revision it replaced is superseded with its edge
   withdrawn — **and still on the record**, because a crew that already
   printed it should be able to find out what they are holding.
3. **Scope, and every exclusion named.** A job that has moved off the governed
   status is not selected at all, which is how *"once the job moves to IN
   PROGRESS, stop checking"* is kept: there is nothing to switch off. A load
   date already past, one beyond the horizon and one that will not read are
   three different facts and three different counts.
4. **Counts, then one question.** No documents at all is counted until the
   governed recurrence threshold and only then a Task; a *partial* packet is
   informational at every window and never raises one.
5. **One identity question.** A `crew_id` that resolves to no `Team` lands in
   the second aggregate and **only** the second — no document question is
   asked about a job nobody can deliver to — and eleven jobs behind one
   subcontractor are one Task.
6. **The claim that retired the reconciliation Flow.** A delivery that moves a
   job between crews leaves the old `assigned_to` edge withdrawn and still
   present with an ended validity, the new one live, and nothing deleted
   anywhere — no folder created, moved, copied or removed by any of this.

The fixture is deliveries wherever a Wick feed provides the rows — the same
push payloads `job-sync` and `dms-sync` answer in production, so both seams
are exercised on the way in — with direct `Team` writes for the crews, which
no production feed carries.
"""

from datetime import date

import pytest

from app.connectors.sync import DOCUMENTS_EVENT, JOB_EVENT, SyncRequest
from app.graph import Claim, object_id
from app.models import AssetUpdate
from app.ontology import names as onto
from app.tasks.field_documents.domain import (
    AGGREGATE_CLASSES,
    BEYOND_HORIZON,
    FLOW_ID,
    GAP_SCREEN,
    LINK_ACTION,
    LOAD_DATE_PAST,
    NO_DOCUMENTS,
    NO_LOAD_DATE,
    PACKETS_EVENT,
    POLICY_ASSET_ID,
    PRODUCTION_RECORDS,
    STATUS_OUT_OF_SCOPE,
    UNRESOLVED_CREW,
    FieldDocumentPolicy,
    _section,
    derive_labels,
    keywords_of,
    parse_field_policy,
)
from app.tasks.field_documents.v1 import flow as v1
from tests import world as test_world

#: The day every window below is answered as of. Fixed rather than relative, so
#: an assertion about a fourteen-day horizon says which fourteen days.
TODAY = date(2026, 9, 7)

JOB_SOURCE = "erp-jobs"
DMS_SOURCE = "dms-library"

WILLOWS = f"{JOB_SOURCE}:250661"
RIDGE = f"{JOB_SOURCE}:250553"
FARMS = f"{JOB_SOURCE}:250774"
SUB = f"{JOB_SOURCE}:250900"
IN_PROGRESS = f"{JOB_SOURCE}:250111"


# ---- the world -------------------------------------------------------------


def _crews(world) -> None:
    """The two crews, written directly.

    Deliberately direct: no production feed carries a department, and going
    through `workforce-sync` to land two crews would put that feed's whole
    surface between this module and what it is testing. The **cost centre** is
    the binding the `erp-jobs` Mapping declares (S1) — the HRIS writes the
    bare crew id there and the ERP's job names the crew by that same string.
    """
    for cost_center, slug, name in (
        ("12", "crew-12", "Crew 12"),
        ("14", "crew-14", "Crew 14"),
    ):
        world.graph.upsert(
            onto.TEAM, slug,
            [
                Claim(field="slug", value=slug,
                      source="hris:hris-workforce"),
                Claim(field="name", value=name,
                      source="hris:hris-workforce"),
                Claim(field="cost_center", value=cost_center,
                      source="hris:hris-workforce"),
            ],
            label=name,
        )


def _document(external_id: str, name: str, folder: str, version: int) -> dict:
    return {
        "external_id": external_id, "name": name,
        "folder_external_id": folder, "content_type": "application/pdf",
        "size": 24_000, "latest_version": version,
        "storage_ref": f"dms://{external_id}",
    }


_TREE = {
    "source": DMS_SOURCE,
    "watermark": "2026-09-07T05:00:00Z",
    "folders": [
        {"external_id": "F-root", "name": "Job Documents"},
        {"external_id": "F-661", "name": "250661",
         "parent_external_id": "F-root"},
        {"external_id": "F-553", "name": "250553",
         "parent_external_id": "F-root"},
        {"external_id": "F-900", "name": "250900",
         "parent_external_id": "F-root"},
    ],
    "documents": [
        # A complete packet, with a second revision of one type: the
        # engineering plans are on v2, and the v1 beside them is the revision
        # the crews' portal panel used to show at the top of the list.
        _document("D-661-SL", "250661 Shipping List.pdf", "F-661", 1),
        _document("D-661-EP1", "250661 Engineering Plans REV A.pdf",
                  "F-661", 1),
        _document("D-661-EP2", "250661 Engineering Plans REV B.pdf",
                  "F-661", 2),
        _document("D-661-MAP", "250661 Job Site Map.pdf", "F-661", 1),
        _document("D-661-CH", "250661 Crew Hours.pdf", "F-661", 1),
        # A file nobody named to the convention. Left where it is, counted.
        _document("D-661-X", "site photos.pdf", "F-661", 1),
        # A partial packet: one of the four. Informational at every window.
        _document("D-553-SL", "250553 Shipping List.pdf", "F-553", 1),
        # 250900's folder exists and holds nothing of the four — but its crew
        # resolves to nobody, so no document question is asked about it.
        _document("D-900-X", "notes.pdf", "F-900", 1),
    ],
}


def _job(
    external_id: str,
    number: str,
    customer: str,
    crew: str,
    foreman: str,
    load: str,
    status: str = "Scheduled",
    name: str = "",
) -> dict:
    return {
        "external_id": external_id, "job_number": number,
        "name": name or f"{number} building", "customer_name": customer,
        "status": status, "load_date": load, "crew_id": crew,
        "foreman": foreman, "plant_site": "Mazomanie",
    }


_JOBS = {
    "source": JOB_SOURCE,
    "watermark": "2026-09-07T05:00:00Z",
    "jobs": [
        # The stopword trap.
        _job("J-661", "250661", "The Willows Equine LLC", "12",
             "D. Prellwitz", "2026-09-12"),
        # The collision trap, both halves.
        _job("J-553", "250553", "Evermore Ridge Estates", "14",
             "C. Meinholz", "2026-09-14"),
        _job("J-774", "250774", "Evermore Farms LLC", "12",
             "D. Prellwitz", "2026-09-15"),
        # The subcontractor: a crew id with no HRIS department behind it (S2).
        _job("J-900", "250900", "Hoffmann Ridge Farms", "SUB-KVB",
             "KICKAPOO VALLEY BUILDERS", "2026-09-16"),
        # Out of scope, and the whole of *"stop checking once it is IN
        # PROGRESS"*: it is never selected.
        _job("J-111", "250111", "Apex Post Frame", "12", "D. Prellwitz",
             "2026-09-10", status="In Progress"),
    ],
}


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def booked(world):
    """The crews, then the document tree, then the jobs.

    The tree first, because `job-sync` draws `job_folder` by the last segment
    of a folder path and a folder that has not landed cannot be drawn to. Each
    delivery also wakes this flow, which is the point: the Trigger rows are
    exercised on the way in rather than described.
    """
    _crews(world)
    for delivery in (_TREE, _JOBS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    return world


def _window(world, event: str = JOB_EVENT, as_of: date = TODAY, **extra):
    """One window, through the Dispatcher that declares the Trigger.

    Through `emit` rather than by calling the body: the Trigger rows and this
    package land together, and `Dispatcher.emit` does not guard
    `RunEngine.start` — a row whose body nothing registered would 500 the next
    estate delivery rather than fail here. This is the test that fails loudly
    instead.
    """
    payload = {
        "source": JOB_SOURCE if event == JOB_EVENT else DMS_SOURCE,
        "watermark": "2026-09-07T06:00:00Z",
        "as_of": as_of.isoformat(),
    }
    # Exactly the shape the real feed sends, so the run can say which fact
    # woke it: the production delivery carries `records` and the document one
    # carries `folders`/`documents`.
    payload.update(
        {"records": []} if event == JOB_EVENT
        else {"folders": [], "documents": []}
    )
    payload.update(extra)
    dispatch = world.dispatcher.emit(event, payload, actor="system:test")
    started = [world.engine.get(run.id) for run in dispatch.runs]
    field = [run for run in started if run.flow_id == FLOW_ID]
    assert field, f"{event} started no field-documents run — Trigger missing?"
    return field[0]


def _deliver(world, payload: dict, as_of: date = TODAY):
    """One delivery, and the field-documents run it woke.

    Deliveries wake this flow through the same two Triggers a window does —
    that is the point of declaring them — so a test about what a *delivery*
    lands has to read the run the delivery started rather than start a second
    one on top of it.
    """
    world.sync.deliver(
        SyncRequest.model_validate(payload), actor="system:test"
    )
    return _latest(world)


def _latest(world):
    runs = [r for r in world.engine.store.list() if r.flow_id == FLOW_ID]
    runs.sort(key=lambda run: run.started_at_iso)
    assert runs, "no field-documents run has happened"
    return runs[-1]


def _windows(world) -> int:
    return len([r for r in world.engine.store.list() if r.flow_id == FLOW_ID])


def _families(run) -> set[str]:
    """The step families a journal holds — loop suffixes trimmed.

    `x3_1` and `x3_2` are one declared step run twice, which is what the
    console shows and what a map can promise.
    """
    out = set()
    for step in run.steps:
        head, _, tail = step.step_id.rpartition("_")
        out.add(head if head and tail.isdigit() else step.step_id)
    return out


def _job_row(world, natural_key: str):
    return world.graph.get(object_id(onto.JOB, natural_key))


def _doc(world, external_id: str):
    return world.graph.get(
        object_id(onto.DOCUMENT, f"{DMS_SOURCE}:{external_id}")
    )


def _label(world, natural_key: str) -> str:
    row = _job_row(world, natural_key)
    assert row is not None, natural_key
    return str(row.props.get("packet_label") or "")


def _tasks(world, name: str = "") -> list:
    rows = [t for t in world.tasks.list() if t.flow_id == FLOW_ID]
    return [t for t in rows if not name or t.case_ref.endswith(name)]


def _entries(world, action: str = LINK_ACTION) -> list:
    return [e for e in world.gateway.ledger.list() if e.action == action]


# ---- the map is the run ----------------------------------------------------


def test_the_declared_steps_are_exactly_the_steps_a_full_run_executes(booked):
    """The claim the transparency bar makes, over a window doing everything.

    The base fixture is built so one pass reaches every step with something to
    do: three jobs get packets, one is complete, one is partial, one is empty,
    a revision is superseded, a label collides, another resolves through a
    stopword, a crew resolves to nobody and a job is out of scope. What is
    left over in either direction is the bug — a step in the journal no map
    declares, or a map promising a step the engine never runs.
    """
    run = _window(booked)
    assert run.status == "succeeded", run.error

    declared = [step.id for step in v1.STEPS]
    assert _families(run) == set(declared)
    # A map is a sequence and not a set, so the order is asserted too.
    assert [step.step_id for step in run.steps] == declared


def test_the_kinds_the_journal_records_are_the_kinds_the_map_declares(booked):
    """Every step but the aggregate, which is declared `task` and executed as
    a `sync` for `ap-intake`'s reason: it raises and never waits."""
    run = _window(booked)
    declared = {step.id: step.type for step in v1.STEPS}
    for step in run.steps:
        if step.step_id == "k1":
            assert step.kind == "sync"
            continue
        assert step.kind == declared[step.step_id], step.step_id


def test_both_facts_wake_the_same_flow(booked):
    """Two Triggers on one Flow, because either half can be the new fact.

    A job becoming `Scheduled` and a revision arriving for a job already
    scheduled change the same answer, so they are two rows in the Trigger
    table and one body — not two Flows that would each have to know what the
    other had already linked.
    """
    from_jobs = _window(booked, event=JOB_EVENT)
    from_docs = _window(booked, event=DOCUMENTS_EVENT)
    assert from_jobs.id != from_docs.id
    assert from_jobs.outputs["woken_by"] == JOB_EVENT
    assert from_docs.outputs["woken_by"] == DOCUMENTS_EVENT
    assert from_jobs.status == from_docs.status == "succeeded"


def test_the_pass_emits_its_own_fact_with_the_counts(booked):
    """Nothing binds `JobPacketsLinked`; it is the run's own record."""
    run = _window(booked)
    event = run.step("e1")
    assert event.name == PACKETS_EVENT
    assert "packet(s) linked" in event.summary


# ---- the governed read -----------------------------------------------------


def test_the_parser_reads_the_shipped_policy(world):
    """Every value §D-1 names, off the document really in the tree."""
    asset = world.knowledge.repo.get(POLICY_ASSET_ID)
    assert asset is not None, "the policy asset is not in the seed corpus"
    policy = parse_field_policy(asset.content)

    assert policy.type_names == [
        "Shipping List", "Engineering Plans", "Job Site Map", "Crew Hours",
    ]
    assert [spec.order for spec in policy.document_types] == [1, 2, 3, 4]
    assert policy.document_types[0].pattern == (
        "<job number> Shipping List*.pdf"
    )
    assert policy.folder_root == "Job Documents"
    assert policy.job_folder_name == "<job number>"
    assert policy.job_folder_path("250661") == "Job Documents/250661"
    assert policy.latest_by == "latestVersion"
    assert policy.tiebreak == "createdAt"
    # Both governed words resolve to a field this deployment holds, which is
    # the property the flow reports on rather than assumes.
    assert policy.reads_declared_fields
    assert policy.version_field == "latest_version"
    assert policy.tiebreak_field == "created_at_iso"

    assert policy.statuses == ["Scheduled"]
    assert policy.horizon_days == 14
    assert policy.window_limit == 40

    assert policy.stopwords == ["the", "a", "an", "of"]
    assert policy.collision_rule == "append the next keyword"
    assert policy.label_case == "as printed"
    assert "company name" in policy.keyword_source
    assert "own name" in policy.keyword_fallback

    assert policy.missing_windows == 3
    assert policy.partial_packet == "informational"
    assert "SUBCONTRACTOR" in policy.crew_rule
    assert policy.routing.fallback == "field-operations"
    assert policy.found_asset


def test_the_parser_is_section_scoped(world):
    """The document argues about its own values in prose.

    Its filing section argues about which revision is newest, its label
    section quotes the two labels the answer keys expect, and its
    missing-documents section names its own threshold in a sentence — so an
    unscoped search would read a sentence *about* a value as the value. The
    proof is a document whose sections are gone but whose prose still mentions
    everything.
    """
    prose = (
        "# Field Documents Policy\n\n"
        "This page talks about a scheduling horizon: 999 days and a "
        "document folder root: Nowhere and jobs per document window: 3 "
        "without declaring any of them.\n"
    )
    policy = parse_field_policy(prose)
    assert policy.horizon_days == 14
    assert policy.folder_root == "Job Documents"
    assert policy.window_limit == 40


def test_the_local_section_reader_agrees_with_the_canonical_one(world):
    """Two copies of a section reader, held together by a test.

    `routing._section` is private to a module this package should not reach
    into for one regex, so there is a local copy — and the copy is only safe
    while a test says the two answer the same question the same way.
    """
    from app import routing

    asset = world.knowledge.repo.get(POLICY_ASSET_ID)
    for heading in (
        "Document types", "Where job documents are filed",
        "Which jobs are in scope", "Packet label", "Missing documents",
        "Unresolved crews", "Approval routing",
    ):
        assert _section(asset.content, heading) == routing._section(
            asset.content, heading
        ), heading
        assert _section(asset.content, heading).strip(), heading


def test_a_corpus_with_no_policy_runs_and_says_it_fell_back(world):
    """A fresh deployment still has to work, and an empty answer here is a
    flow that silently does nothing — the failure that looks like success."""
    _crews(world)
    world.knowledge.repo.delete(POLICY_ASSET_ID)
    for delivery in (_TREE, _JOBS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    run = _window(world)
    assert run.status == "succeeded", run.error
    assert run.outputs["found_asset"] is False
    assert "built-in default" in run.step("x1").summary
    # And the built-in default is the document's own values, so the traps
    # still resolve: falling back must not change the answer, only the
    # provenance.
    assert _label(world, WILLOWS) == "250661 Willows"


def test_the_run_cites_the_revision_it_read(booked):
    run = _window(booked)
    ref = run.outputs["policy_ref"]
    assert ref.startswith(f"knowledge:{POLICY_ASSET_ID}@rev")
    assert ref in run.refs


# ---- the naming convention, and its two traps ------------------------------


def test_a_leading_article_resolves_through_the_governed_stopwords(booked):
    """`The Willows Equine LLC` on 250661 is `250661 Willows`.

    The first trap the estate seeds deliberately: a leading article carries no
    information and makes two customers look alike, so the governed stopword
    list is skipped over when the keyword is chosen — and the run says *which
    rule* produced the label, because a label nobody can explain is a label
    nobody can correct.
    """
    run = _window(booked)
    assert _label(booked, WILLOWS) == "250661 Willows"
    why = next(
        row["why"] for row in run.outputs["labels"] if row["job"] == "250661"
    )
    assert "stopword" in why
    assert run.outputs["stopwords_applied"] == 1


def test_two_customers_sharing_a_keyword_resolve_through_the_collision(booked):
    """`250553 Evermore Ridge` and `250774 Evermore Farms`.

    The second trap: two customers can begin with the same keyword, and the
    governed rule is to append the next one. Both labels move, not just the
    second — the collision is a fact about the pair, and a rule that renamed
    only the loser would hand one crew a label whose derivation nobody could
    repeat.
    """
    run = _window(booked)
    assert _label(booked, RIDGE) == "250553 Evermore Ridge"
    assert _label(booked, FARMS) == "250774 Evermore Farms"
    assert run.outputs["collisions"] == 2
    for number in ("250553", "250774"):
        why = next(
            row["why"] for row in run.outputs["labels"]
            if row["job"] == number
        )
        assert "collision" in why


def test_the_label_rules_are_governed_values_and_not_literals(booked):
    """The proof: edit the page and watch the label move.

    Removing `the` from the stopword list is a one-line edit a superintendent
    can make in the console, and the label a crew reads changes with no
    deploy. If this were a literal in code the assertion below would fail.
    """
    asset = booked.knowledge.repo.get(POLICY_ASSET_ID)
    booked.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "- label stopwords: the, a, an, of",
            "- label stopwords: a, an, of",
        ),
        updated_by="test",
    ))
    run = _window(booked)
    assert run.outputs["stopwords_applied"] == 0
    assert _label(booked, WILLOWS) == "250661 The"


def test_the_collision_rule_is_a_governed_value_too(booked):
    """Two customers, one keyword each, and the rule turned off.

    With no next keyword to append the run says so rather than handing two
    crews the same half-label in silence — the honest failure, and the reason
    `exhausted` is a field rather than a shrug.
    """
    policy = FieldDocumentPolicy()
    labels = derive_labels(
        [("250553", "Evermore"), ("250774", "Evermore")], policy
    )
    # Two customers spelled identically are one keyword source: whether they
    # are one party is the counterparty book's question, not this flow's.
    assert labels["250553"].label == "250553 Evermore"
    assert labels["250774"].label == "250774 Evermore"
    assert not labels["250553"].exhausted


def test_the_keyword_list_keeps_the_case_as_printed(world):
    """`- label case: as printed`. A crew, a foreman and a scheduler all say
    `Willows`, and lowercasing to make the comparison easier would change
    what they read."""
    assert keywords_of("The Willows Equine LLC", ["the"]) == [
        "Willows", "Equine", "LLC",
    ]
    assert keywords_of("", ["the"]) == []


def test_a_job_with_no_customer_name_is_labelled_by_its_number(world):
    policy = FieldDocumentPolicy()
    labels = derive_labels([("250661", "")], policy)
    assert labels["250661"].label == "250661"
    assert "no customer name" in labels["250661"].rule


# ---- the revision comparison -----------------------------------------------


def test_the_latest_revision_is_linked_and_the_previous_superseded(booked):
    """*The topmost one is the most recent* is not a rule this system keeps.

    A document system sorted by name says nothing about age, so the current
    revision is the highest `latestVersion` — and the one it replaced is
    marked superseded with its row, its claims and its edge intact.
    """
    # The fixture's own jobs delivery is the window that did this — reading a
    # second one would assert about a pass with nothing left to do.
    run = _latest(booked)
    job = _job_row(booked, WILLOWS)
    linked = {
        other.natural_key: link
        for link, other in booked.graph.neighbors(
            job.id, "documented_by", "out"
        )
    }
    assert f"{DMS_SOURCE}:D-661-EP2" in linked
    assert f"{DMS_SOURCE}:D-661-EP1" not in linked
    assert linked[f"{DMS_SOURCE}:D-661-EP2"].props == {
        "document_type": "Engineering Plans", "revision": "2",
    }

    superseded = _doc(booked, "D-661-EP1")
    assert superseded.status == "superseded"
    # Nothing was merged: `superseded_by` is a merge pointer and two revisions
    # of one document are not the same row. The row still resolves on its own
    # address, which is what "a crew that already printed it should be able to
    # find out what they are holding" requires.
    assert superseded.superseded_by == ""
    assert superseded.props.get("storage_ref") == "dms://D-661-EP1"
    assert run.outputs["superseded"] == 1
    assert run.outputs["linked"] == 5
    assert _job_row(booked, WILLOWS).props["documents_complete"] == "yes"
    # And a second window has nothing left to supersede, which is what makes
    # this safe to wake on every delivery.
    assert _window(booked).outputs["superseded"] == 0


def test_a_new_revision_withdraws_the_previous_edge_and_deletes_nothing(
    booked,
):
    """The revision arriving is the *other* fact that wakes this flow.

    A v3 lands, the v2 edge is withdrawn — validity ended, never deleted —
    and both edges stay on the record, so "what was this crew working from
    last week" is answerable.
    """
    _window(booked)
    before = len(booked.graph.store.links())

    run = _deliver(booked, {
        "source": DMS_SOURCE, "watermark": "2026-09-08T05:00:00Z",
        "documents": [
            _document("D-661-EP3", "250661 Engineering Plans REV C.pdf",
                      "F-661", 3),
        ],
    })

    assert run.outputs["woken_by"] == DOCUMENTS_EVENT
    assert run.outputs["withdrawn"] == 1
    job = _job_row(booked, WILLOWS)
    edges = {
        other.natural_key: link
        for link, other in booked.graph.neighbors(
            job.id, "documented_by", "out", include_reversed=True,
        )
    }
    assert edges[f"{DMS_SOURCE}:D-661-EP3"].live
    withdrawn = edges[f"{DMS_SOURCE}:D-661-EP2"]
    assert not withdrawn.live
    assert withdrawn.valid_to_iso and withdrawn.reversed_at_iso
    assert "superseded by revision 3" in withdrawn.reversal_reason
    assert _doc(booked, "D-661-EP2").status == "superseded"
    # Nothing was deleted: the link count only grew.
    assert len(booked.graph.store.links()) > before


def test_a_revision_tie_breaks_on_the_creation_stamp(world):
    """`- revision tiebreak: createdAt`, at full precision.

    The estate's revisions of one document can be microseconds apart, so the
    comparison is the whole timestamp rather than a date: two files at the
    same `latestVersion` are decided by which was created later, and the
    later-landed row is the later-created one.
    """
    _crews(world)
    world.sync.deliver(SyncRequest.model_validate({
        "source": DMS_SOURCE, "watermark": "w",
        "folders": [
            {"external_id": "F-root", "name": "Job Documents"},
            {"external_id": "F-661", "name": "250661",
             "parent_external_id": "F-root"},
        ],
        "documents": [
            _document("D-early", "250661 Shipping List A.pdf", "F-661", 4),
            _document("D-late", "250661 Shipping List B.pdf", "F-661", 4),
        ],
    }), actor="system:test")
    world.sync.deliver(SyncRequest.model_validate({
        "source": JOB_SOURCE, "watermark": "w",
        "jobs": [_job("J-661", "250661", "The Willows Equine LLC", "12",
                      "D. Prellwitz", "2026-09-12")],
    }), actor="system:test")

    early, late = _doc(world, "D-early"), _doc(world, "D-late")
    assert early.created_at_iso < late.created_at_iso

    run = _window(world)
    assert run.status == "succeeded", run.error
    job = _job_row(world, WILLOWS)
    linked = {
        other.natural_key for _, other in world.graph.neighbors(
            job.id, "documented_by", "out"
        )
    }
    assert linked == {f"{DMS_SOURCE}:D-late"}
    assert _doc(world, "D-early").status == "superseded"


def test_a_file_matching_no_governed_pattern_is_counted_not_guessed(booked):
    """*It is not guessed at: a file in a job folder that nobody named to the
    convention is a filing question for the office.*"""
    run = _window(booked)
    assert run.outputs["unclassified"] == 1
    job = _job_row(booked, WILLOWS)
    linked = {
        other.props.get("filename") for _, other in booked.graph.neighbors(
            job.id, "documented_by", "out"
        )
    }
    assert "site photos.pdf" not in linked


def test_a_job_with_no_folder_at_all_is_a_count_and_not_a_failure(booked):
    """250774 has no folder in the document tree. The run says so and keeps
    going: the recurrence threshold decides when it becomes somebody's
    morning."""
    run = _window(booked)
    assert run.outputs["folders_missing"] == 1
    packet = next(
        row for row in run.outputs["packets"] if row["job"] == "250774"
    )
    assert packet["held"] == []
    assert len(packet["missing"]) == 4
    assert _job_row(booked, FARMS).props["documents_complete"] == "no"


# ---- scope, and every exclusion named --------------------------------------


def test_a_job_out_of_scope_is_not_selected(booked):
    """*Once the job moves to IN PROGRESS, stop checking for new versions* —
    kept by never starting. There is nothing to switch off, which is why it is
    not a step."""
    run = _window(booked)
    assert run.outputs["exclusions"][STATUS_OUT_OF_SCOPE] == 1
    assert IN_PROGRESS not in run.outputs["answered_jobs"]
    row = _job_row(booked, IN_PROGRESS)
    assert row.props.get("packet_label") is None
    assert not booked.graph.neighbors(row.id, "documented_by", "out")


def test_each_exclusion_is_named_and_counted_separately(world):
    """"Not scheduled yet" and "loaded last week" are different facts about a
    job, and a reader of the journal has to be able to tell them apart."""
    _crews(world)
    world.sync.deliver(SyncRequest.model_validate({
        "source": JOB_SOURCE, "watermark": "w",
        "jobs": [
            _job("J-a", "250001", "Apex", "12", "D. Prellwitz",
                 "2026-08-30"),
            _job("J-b", "250002", "Bravo", "12", "D. Prellwitz",
                 "2026-11-30"),
            _job("J-c", "250003", "Charlie", "12", "D. Prellwitz", ""),
            _job("J-d", "250004", "Delta", "12", "D. Prellwitz",
                 "2026-09-10", status="Complete"),
        ],
    }), actor="system:test")
    run = _window(world)
    assert run.outputs["exclusions"] == {
        LOAD_DATE_PAST: 1,
        BEYOND_HORIZON: 1,
        NO_LOAD_DATE: 1,
        STATUS_OUT_OF_SCOPE: 1,
    }
    assert run.outputs["in_horizon"] == 0
    assert run.outputs["gap_tasks"] == 0


def test_the_window_bound_defers_rather_than_drops(booked):
    """*Nothing is dropped — a job the window did not take on is still in
    scope, is reported as deferred, and the next window finds it.*"""
    asset = booked.knowledge.repo.get(POLICY_ASSET_ID)
    booked.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "- jobs per document window: 40",
            "- jobs per document window: 2",
        ),
        updated_by="test",
    ))
    run = _window(booked)
    assert run.outputs["window_limit"] == 2
    assert run.outputs["taken"] == 2
    assert run.outputs["deferred"] == 2
    assert "left for the next window" in run.step("x2").summary
    # Soonest to load first, so the two windows agree about the boundary.
    assert run.outputs["answered_jobs"] == [WILLOWS, RIDGE]


# ---- counts, then one question ---------------------------------------------


def test_a_partial_packet_never_raises_a_task(booked):
    """*A packet with plans and a site map but no crew hours sheet is a normal
    Tuesday.* Informational at every window, however many windows there
    are."""
    for _ in range(5):
        run = _window(booked)
    assert run.outputs["screened"]["partial"] == 1
    # Five windows, and 250553 — one of the four types filed — is in none of
    # them. The *empty* job beside it has long since crossed the threshold,
    # which is exactly the distinction: a filing failure is worth somebody's
    # morning and a missing crew hours sheet is not.
    assert _tasks(booked, NO_DOCUMENTS)
    for task in _tasks(booked):
        joined = " ".join(
            item.label + item.value for item in task.evidence
        )
        assert "250553" not in joined, task.id


def test_no_documents_is_a_count_until_the_threshold_and_a_task_after_it(
    booked,
):
    """The design's own answer, kept: *log as information and continue; notify
    if recurring.*

    250774 has no folder and nothing filed. It is counted on the first window
    and the second; on the third consecutive one it joins the aggregate. The
    count is read off this flow's own run journal rather than a counter field
    somebody could hand-edit — which is exactly the `Last Run Status` cell the
    design kept and this replaces.
    """
    threshold = 3
    # The jobs delivery in the fixture already answered one window.
    assert [
        run for run in booked.engine.store.list() if run.flow_id == FLOW_ID
    ]
    seen: list[int] = []
    for _ in range(threshold):
        run = _window(booked)
        empty = next(
            row for row in run.outputs["empty_jobs"] if row == FARMS
        )
        assert empty == FARMS
        windows = run.outputs["screened"]["empty"]
        seen.append(windows)
        if run.outputs["screened"]["past_threshold"]:
            break
    tasks = _tasks(booked, NO_DOCUMENTS)
    assert len(tasks) == 1, "one aggregate, and only past the threshold"
    task = tasks[0]
    assert task.queue == "field-operations"
    assert "no filed documents at all" in task.title
    assert "log as information and continue" in task.summary
    assert f"{threshold} consecutive window(s)" in " ".join(
        item.value for item in task.evidence
    )
    # And the jobs in it are the empty ones only — not the partial one.
    assert "250553" not in " ".join(item.label for item in task.evidence)


def test_the_task_id_is_derived_from_the_finding_so_it_stands(booked):
    """*The same finding tomorrow is the same question.*

    A daily cadence that opened a new copy of the same aggregate every morning
    would bury the desk it is trying to help, so the id is a digest of the
    finding and a second window reports it standing rather than re-raising it.
    """
    for _ in range(4):
        run = _window(booked)
    assert _tasks(booked, NO_DOCUMENTS)
    assert run.outputs["gap_tasks"] == 0
    assert run.outputs["gap_tasks_standing"] >= 1
    assert "already standing" in run.step("k1").summary


# ---- one identity question -------------------------------------------------


def test_an_unmatched_crew_lands_in_the_second_aggregate_and_only_it(booked):
    """S2: the subcontractor crew has no HRIS department and never will.

    *No document question is asked about it: it goes into a single aggregate
    task asking who this crew is.* So 250900 is in the identity aggregate, is
    **not** in the document aggregate, and has no packet claimed on it at all
    — one identity question rather than N document questions.
    """
    for _ in range(4):
        run = _window(booked)

    crew_tasks = _tasks(booked, UNRESOLVED_CREW)
    assert len(crew_tasks) == 1
    task = crew_tasks[0]
    assert task.queue == "field-operations"
    assert task.priority == "high"
    assert "SUB-KVB" in " ".join(item.value for item in task.evidence)
    assert "SUBCONTRACTOR" in task.summary

    document_tasks = _tasks(booked, NO_DOCUMENTS)
    assert document_tasks, "the document aggregate should have been raised"
    joined = " ".join(
        item.label + item.value for item in document_tasks[0].evidence
    )
    assert "250900" not in joined
    assert SUB not in run.outputs["answered_jobs"]
    assert _job_row(booked, SUB).props.get("packet_label") is None


def test_one_crew_behind_many_jobs_is_one_question(world):
    """*One identity question, once per window, rather than one document
    question per job nobody can deliver to.*"""
    _crews(world)
    world.sync.deliver(SyncRequest.model_validate({
        "source": JOB_SOURCE, "watermark": "w",
        "jobs": [
            _job(f"J-{n}", f"2509{n:02d}", "Hoffmann Ridge Farms", "SUB-KVB",
                 "KICKAPOO VALLEY BUILDERS", "2026-09-12")
            for n in range(6)
        ],
    }), actor="system:test")
    run = _window(world)
    assert run.outputs["screened"]["crewless"] == 6
    assert len(_tasks(world, UNRESOLVED_CREW)) == 1
    assert "6 job(s) name a crew" in _tasks(world, UNRESOLVED_CREW)[0].title
    # And a seventh job for the same crew is the same question.
    world.sync.deliver(SyncRequest.model_validate({
        "source": JOB_SOURCE, "watermark": "w2",
        "jobs": [_job("J-9", "250999", "Hoffmann Ridge Farms", "SUB-KVB",
                      "KICKAPOO VALLEY BUILDERS", "2026-09-13")],
    }), actor="system:test")
    _window(world)
    assert len(_tasks(world, UNRESOLVED_CREW)) == 1


def test_a_job_with_no_crew_named_at_all_is_not_an_identity_question(world):
    """An empty column is the production system saying nothing, which is a
    different fact from an id that names nobody — and only the second is
    somebody's question. The documents are still resolved."""
    _crews(world)
    for delivery in (
        _TREE,
        {
            "source": JOB_SOURCE, "watermark": "w",
            "jobs": [_job("J-661", "250661", "The Willows Equine LLC", "",
                          "", "2026-09-12")],
        },
    ):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    run = _window(world)
    assert run.outputs["crew_unstated"] == 1
    assert run.outputs["screened"]["crewless"] == 0
    assert not _tasks(world, UNRESOLVED_CREW)
    assert _label(world, WILLOWS) == "250661 Willows"


# ---- the claim that retired the reconciliation Flow ------------------------


def test_a_job_that_moves_crews_withdraws_the_old_edge_and_deletes_nothing(
    booked,
):
    """The single biggest simplification in this design.

    The retired weekly bot existed to go looking for the difference between
    last week's crew schedule and this one, and to delete a folder out of one
    foreman's tree and create it in another's. The difference is now a
    property of the record: `job-sync` **withdraws** the old `assigned_to`
    edge — validity ended, never deleted — and draws the new one, so both are
    on the job and "which crew has it now, and who held it before" is one
    traversal. There is no destructive act left to gate, and no folder is
    touched by anything.
    """
    _window(booked)
    folders_before = {
        record.natural_key for record in booked.graph.find(onto.FOLDER)
    }
    documents_before = {
        record.natural_key for record in booked.graph.find(onto.DOCUMENT)
    }

    booked.sync.deliver(SyncRequest.model_validate({
        "source": JOB_SOURCE, "watermark": "2026-09-08T05:00:00Z",
        "jobs": [_job("J-661", "250661", "The Willows Equine LLC", "14",
                      "C. Meinholz", "2026-09-12")],
    }), actor="system:test")
    run = _window(booked)

    job = _job_row(booked, WILLOWS)
    edges = booked.graph.neighbors(
        job.id, "assigned_to", "out", include_reversed=True
    )
    live = [(link, team) for link, team in edges if link.live]
    ended = [(link, team) for link, team in edges if not link.live]
    assert [team.label for _, team in live] == ["Crew 14"]
    assert [team.label for _, team in ended] == ["Crew 12"]
    # Withdrawn, not deleted: the ended edge keeps its validity, its reason
    # and the crew it named.
    assert ended[0][0].valid_to_iso
    assert ended[0][0].props["crew_id"] == "12"
    assert "moved 250661 off Crew 12" in ended[0][0].reversal_reason

    # The window reads the new crew and reports the old one.
    assert run.outputs["reassigned"] == 1
    packet = next(
        row for row in run.outputs["packets"] if row["job"] == "250661"
    )
    assert packet["crew"] == "Crew 14"

    # And nothing about the filing changed: no folder created, moved, copied
    # or deleted, and no document row removed.
    assert {
        record.natural_key for record in booked.graph.find(onto.FOLDER)
    } == folders_before
    assert {
        record.natural_key for record in booked.graph.find(onto.DOCUMENT)
    } == documents_before


def test_no_window_ever_creates_or_removes_a_folder(booked):
    """The negative claim, asserted rather than described.

    *No folder is created, copied, moved or deleted by anything here.* The
    only `Folder` rows in the graph are the ones `dms-sync` mirrored, and four
    windows leave that set exactly as it was.
    """
    before = {
        record.natural_key: record.props.get("path")
        for record in booked.graph.find(onto.FOLDER)
    }
    for _ in range(4):
        _window(booked)
    after = {
        record.natural_key: record.props.get("path")
        for record in booked.graph.find(onto.FOLDER)
    }
    assert after == before
    assert set(before) == {
        f"{DMS_SOURCE}:Job Documents",
        f"{DMS_SOURCE}:Job Documents/250661",
        f"{DMS_SOURCE}:Job Documents/250553",
        f"{DMS_SOURCE}:Job Documents/250900",
    }


def test_the_governed_folder_root_is_drawn_as_an_edge_not_a_tree(booked):
    """A packet is a query over `job_folder` edges rather than a directory
    somebody has to create, copy into and remember to delete."""
    _window(booked)
    job = _job_row(booked, WILLOWS)
    folders = [
        other.props.get("path") for _, other in booked.graph.neighbors(
            job.id, "job_folder", "out"
        )
    ]
    assert folders == ["Job Documents/250661"]


# ---- the one write ---------------------------------------------------------


def test_one_guarded_action_for_the_whole_window(booked):
    """A windowed flow lands bulk observations.

    Three packets, one ledger entry — because three guarded writes would be
    three entries saying the same thing about one pass.
    """
    run = _window(booked)
    entries = _entries(booked)
    # One entry per window, however many jobs each window answered — three
    # packets in this one, and one ledger row.
    assert len(entries) == _windows(booked)
    mine = [e for e in entries if e.run_id == run.id]
    assert len(mine) == 1
    entry = mine[0]
    assert entry.outcome == "committed"
    assert entry.regime == "decision"
    assert entry.target == PRODUCTION_RECORDS
    assert entry.flow_id == FLOW_ID
    assert entry.step_id == "a1"
    assert entry.principal_uid == f"flow:{FLOW_ID}"
    assert run.step("a1").writes == entry.writes
    # Jobs, documents, and the edges between them — and nothing else.
    assert all(
        ref.startswith(f"graph:{onto.JOB}/")
        or ref.startswith(f"graph:{onto.DOCUMENT}/")
        or ref.startswith("link:documented_by/")
        or ref.startswith("link:job_folder/")
        for ref in entry.writes
    ), entry.writes


def test_the_action_carries_no_queue_because_linking_is_nobody_s_work(booked):
    """`record-document-read`'s reasoning, from the other end of the tree.

    A queue binding says whose *work* an Action is. Linking a document nobody
    disputed is nobody's work — the Flow does it on its own authority, unasked
    — and a Flow principal owns only the queues its declaration binds it to,
    so a queue here would refuse the write outright.
    """
    from app.gateway import ACTIONS

    action = ACTIONS[LINK_ACTION]
    assert action.queue == ""
    assert action.queue_from_task is False
    assert action.regime == "decision"
    assert action.target == PRODUCTION_RECORDS
    # And the write does commit as the Flow, which is what that silence buys.
    _window(booked)
    assert _entries(booked)[0].outcome == "committed"


def test_the_two_flow_claims_are_flow_claims_on_a_mirrored_type(booked):
    """`Job` is the production system's; these two fields are ours.

    `documents_complete` and `packet_label` are `flow` claims on a
    non-authoritative type, exactly as `Document.sha256` is — and the ERP
    claims neither, so nothing is being overwritten.
    """
    _window(booked)
    job = _job_row(booked, WILLOWS)
    ours = [
        claim for claim in job.claims
        if claim.field in ("documents_complete", "packet_label")
    ]
    assert ours and all(claim.source == f"flow:{FLOW_ID}" for claim in ours)
    assert all(claim.step_id == "a1" for claim in ours)
    assert any("stopword" in claim.note for claim in ours)
    # The ERP's own fields are untouched by us.
    assert {
        claim.source for claim in job.claims if claim.field == "status"
    } == {f"erp:{JOB_SOURCE}"}


# ---- the desk --------------------------------------------------------------


def test_the_desk_comes_from_the_governed_approval_table(world):
    """Who chases a missing shipping list is an operations decision, and
    moving it is an edit to the page rather than a deploy."""
    asset = world.knowledge.repo.get(POLICY_ASSET_ID)
    world.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "| Over 0 | field-operations |", "| Over 0 | plant-supervisor |"
        ).replace(
            "- Fallback desk: field-operations",
            "- Fallback desk: plant-supervisor",
        ),
        updated_by="test",
    ))
    # Edited before the finding exists: a Task already raised keeps the desk
    # it was raised on, because `TaskService.create` is a put and re-routing
    # an answered Task would reset it.
    _crews(world)
    for delivery in (_TREE, _JOBS):
        world.sync.deliver(
            SyncRequest.model_validate(delivery), actor="system:test"
        )
    for _ in range(4):
        _window(world)
    assert _tasks(world)
    assert {task.queue for task in _tasks(world)} == {"plant-supervisor"}


def test_a_resolver_in_the_table_falls_back_and_says_why(booked):
    """*No person handed these jobs over, they arrived from a job system on a
    schedule*, so `submitter` cannot be answered and the governed fallback
    stands — stated on the Task rather than swallowed."""
    asset = booked.knowledge.repo.get(POLICY_ASSET_ID)
    booked.knowledge.update(asset.id, AssetUpdate(
        content=asset.content.replace(
            "| Over 0 | field-operations |", "| Over 0 | submitter |"
        ),
        updated_by="test",
    ))
    for _ in range(4):
        _window(booked)
    tasks = _tasks(booked)
    assert tasks and {task.queue for task in tasks} == {"field-operations"}
    assert any("no person handed these over" in t.summary for t in tasks)


def test_the_review_screen_is_declared_for_the_kind_this_flow_raises(booked):
    """Lane 6 builds the widget; the manifest degrades to the generic pane
    until then, which is why the id crosses the wire and not the screen."""
    from app.task_screens import resolve_ui

    ui = resolve_ui(FLOW_ID, 1)
    assert ui is not None
    assert ui.screen_for("exception").screen_id == GAP_SCREEN
    assert ui.screen_for("decision") is None


def test_at_most_two_aggregates_and_they_are_the_declared_two(booked):
    """*At most two aggregate Tasks per window*, and both to one desk."""
    for _ in range(4):
        _window(booked)
    tasks = _tasks(booked)
    assert len(tasks) == 2
    assert {task.case_ref for task in tasks} == {
        f"field-documents-gap:{name}" for name in AGGREGATE_CLASSES
    }
    assert {task.kind for task in tasks} == {"exception"}
    assert all(task.flow_id == FLOW_ID for task in tasks)
    # Neither proposes a mutation: the answer to each is a fact somebody
    # supplies, not a write somebody authorizes.
    assert all(not task.proposal for task in tasks)


def test_a_clean_window_writes_nothing_and_asks_nobody(world):
    """A deployment with no scheduled job in the horizon is a no-op that says
    so, which is what makes this safe to wake on every delivery."""
    _crews(world)
    run = _window(world)
    assert run.status == "succeeded", run.error
    assert run.outputs["in_horizon"] == 0
    assert run.outputs["gap_tasks"] == 0
    assert run.outputs["linked"] == 0
    assert "nothing was written" in run.step("a1").summary
    assert not _tasks(world)
