"""AP intake: the customer's filing tree becomes accounts-payable work.

The properties worth defending here are mostly about restraint. A windowed
flow that finds forty invoices must not gate on one of them, must not lose the
forty-first to its own window bound, and must not read the same file twice
because a source redelivered it. And the recognition rule must be the
customer's own filing rather than a convention this repository invented, which
is why the folder list is a governed asset and why a folder whose *name* merely
starts with the AP root is not under it.
"""

import base64
import json

import pytest

from app.graph import object_id
from app.models import AssetUpdate
from app.ontology import names as onto
from app.tasks.ap_intake.domain import (
    DEFAULT_INVOICE_FOLDERS,
    POLICY_ASSET_ID,
    parse_intake_policy,
)
from app.connectors.sync import SyncRequest
from tests import world as test_world

DMS = "dms-library"
BASE = "https://cumulus-demo-mitchell-suter-dms.web.app/api/naviant/v1"

#: A one-page artifact with a real PDF signature. `media.sniff` reads the
#: header, so this is `application/pdf` however the DMS declared it.
def _pdf(marker: str) -> bytes:
    return b"%PDF-1.4\n% " + marker.encode() + b"\n%%EOF\n"


class _Library:
    """A document system that serves content, and counts who asked.

    Stands in for the estate connector, which is the one component allowed to
    reach outward. What a test needs from it is one call, which is exactly what
    `ContentFetcher` declares.
    """

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.calls: list[str] = []

    def fetch_content(self, url: str) -> tuple[bytes, str]:
        self.calls.append(url)
        for key, content in self.files.items():
            if key in url:
                return content, "application/pdf"
        raise RuntimeError(f"404 for {url}")


def _document(external_id: str, folder: str, name: str, version: int = 1) -> dict:
    return {
        "external_id": external_id,
        "name": name,
        "folder_external_id": folder,
        "content_type": "application/pdf",
        "size": 4096,
        "latest_version": version,
        "storage_ref": f"{BASE}/documents/{external_id}/content?version={version}",
    }


def _delivery(documents: list[dict], folders: list[dict] | None = None) -> SyncRequest:
    """The DMS feed's shape, with the AP tree the estate actually files under."""
    tree = folders if folders is not None else [
        {"external_id": "ap", "name": "Accounts Payable", "parent_external_id": ""},
        {"external_id": "ap-acme", "name": "Acme Industrial Inc.",
         "parent_external_id": "ap"},
        {"external_id": "ap-acme-26", "name": "2026",
         "parent_external_id": "ap-acme"},
        {"external_id": "cust", "name": "Customers", "parent_external_id": ""},
        {"external_id": "cust-nw", "name": "Northwind Traders",
         "parent_external_id": "cust"},
    ]
    return SyncRequest.model_validate({
        "source": DMS,
        "watermark": "2026-09-01T00:00:00Z",
        "folders": tree,
        "documents": documents,
    })


@pytest.fixture()
def world():
    return test_world.fresh()


def _deliver(world, request: SyncRequest) -> list[dict]:
    return world.sync.deliver(request, actor="system:test")


def _intake_run(world):
    runs = world.engine.list(flow_id="ap-intake")
    assert runs, "the delivery started no intake run"
    return runs[0]


def _out(world) -> dict:
    return _intake_run(world).outputs


# ---- the wake ---------------------------------------------------------------


def test_one_delivery_wakes_the_mirror_and_then_the_intake(world):
    """Three responses to one fact, in the order the Trigger table declares.

    Load-bearing rather than incidental: intake reads the filing tree out of
    the graph, so the mirror that lands it has to have run first. If the order
    ever inverts, this run selects nothing and the failure looks like a
    selection bug. `field-documents` is the third and reads the same tree from
    the other end — the job packets under the document root — which is why it
    is declared after both and not between them.
    """
    world.intake.fetcher = _Library({"doc-1": _pdf("one")})
    outcomes = _deliver(world, _delivery([
        _document("doc-1", "ap-acme-26", "Acme invoice 2026-09-01.pdf"),
    ]))
    # `deliver` reports the first response — the mirror — because that is the
    # one whose counts describe the delivery.
    assert [o["flow_id"] for o in outcomes] == ["dms-sync"]

    intake = _intake_run(world)
    assert intake.status == "succeeded"
    assert intake.outputs["read"] == 1
    # Both responses name the same fact, and the fact named both of them
    # before either existed.
    [mirror] = world.engine.list(flow_id="dms-sync")
    assert intake.caused_by == mirror.caused_by
    delivered = next(
        e for e in world.dispatcher.events.list() if e.name == "DocumentsDelivered"
    )
    assert delivered.id == intake.caused_by
    assert delivered.triggers == [
        "documents-delivered", "documents-delivered-ap",
        "documents-delivered-field",
    ]
    [packets] = world.engine.list(flow_id="field-documents")
    assert delivered.starts == [mirror.id, intake.id, packets.id]


def test_a_redelivered_document_is_not_read_twice(world):
    """The same pull, retried. The whole point of the `sha256` marker.

    Not a fetch that is thrown away afterwards: the row already says it has
    been read, so the second window does not reach for the bytes at all.
    """
    library = _Library({"doc-1": _pdf("one")})
    world.intake.fetcher = library
    documents = [_document("doc-1", "ap-acme-26", "Acme invoice.pdf")]
    _deliver(world, _delivery(documents))
    assert len(library.calls) == 1

    _deliver(world, _delivery(documents))
    assert len(library.calls) == 1
    runs = world.engine.list(flow_id="accounts-payable")
    assert len(runs) == 1


def test_a_reissued_invoice_is_read_again(world):
    """A corrected invoice comes back at v2 under the same document id.

    The hash alone would call this read. `read_version` beside it is what makes
    "has anybody read *this* revision" answerable, and a disputed invoice
    reissued after a query is exactly the case the pair exists for.
    """
    library = _Library({"doc-1": _pdf("v1")})
    world.intake.fetcher = library
    _deliver(world, _delivery([_document("doc-1", "ap-acme-26", "Acme invoice.pdf")]))
    assert len(library.calls) == 1

    library.files["doc-1"] = _pdf("v2 — corrected after the query")
    _deliver(world, _delivery([
        _document("doc-1", "ap-acme-26", "Acme invoice.pdf", version=2),
    ]))
    assert len(library.calls) == 2
    assert library.calls[-1].endswith("version=2")
    assert len(world.engine.list(flow_id="accounts-payable")) == 2


# ---- what counts as an invoice ----------------------------------------------


def test_only_documents_filed_under_the_ap_folders_are_read(world):
    """Where the customer filed it, and nothing else about the file.

    The proposal in `Customers/Northwind Traders` is a PDF with `invoice` in
    its name and it is not an invoice; the one in the AP tree has no such word
    and is.
    """
    world.intake.fetcher = _Library({
        "ap-1": _pdf("ap"), "cust-1": _pdf("cust"),
    })
    _deliver(world, _delivery([
        _document("ap-1", "ap-acme-26", "Acme 2026-09-01.pdf"),
        _document("cust-1", "cust-nw", "Northwind invoice proposal.pdf"),
    ]))
    out = _out(world)
    assert out["unread"] == 1 and out["read"] == 1
    stored = [d for d in world.documents.list() if d.kind == "invoice"]
    assert [d.filename for d in stored] == ["Acme 2026-09-01.pdf"]


def test_a_folder_that_merely_starts_with_the_ap_name_is_not_under_it(world):
    """Segment comparison, not `startswith`.

    `Accounts Payable Archive` is a sibling of the AP root and holds paid
    history somebody moved out of the way. A prefix match on the bare string
    would have read every file in it as a live invoice and started a run per
    payment the company already made.
    """
    world.intake.fetcher = _Library({"old-1": _pdf("old")})
    _deliver(world, _delivery(
        [_document("old-1", "archive", "Acme invoice 2019.pdf")],
        folders=[
            {"external_id": "ap", "name": "Accounts Payable",
             "parent_external_id": ""},
            {"external_id": "archive", "name": "Accounts Payable Archive",
             "parent_external_id": ""},
        ],
    ))
    out = _out(world)
    assert out["unread"] == 0 and out["read"] == 0
    assert world.engine.list(flow_id="accounts-payable") == []


def test_the_folders_are_governed_knowledge_and_the_run_names_the_revision(world):
    """A customer who reorganizes their filing says so in the asset."""
    world.knowledge.update(POLICY_ASSET_ID, AssetUpdate(
        content=(
            "# Intake\n\n## Where supplier invoices are filed\n\n"
            "- Invoice folder: Supplier Bills\n\n"
            "- Invoices per intake window: 25\n"
        ),
        updated_by="finance-ops",
    ))
    world.intake.fetcher = _Library({"bill-1": _pdf("bill")})
    _deliver(world, _delivery(
        [_document("bill-1", "bills", "Acme 2026-09-01.pdf")],
        folders=[
            {"external_id": "ap", "name": "Accounts Payable",
             "parent_external_id": ""},
            {"external_id": "bills", "name": "Supplier Bills",
             "parent_external_id": ""},
        ],
    ))
    assert _out(world)["read"] == 1
    run = _intake_run(world)
    assert f"knowledge:{POLICY_ASSET_ID}@rev2" in run.refs
    policy = run.step("x1").output
    assert policy["folders"] == ["Supplier Bills"]
    assert policy["found_asset"] is True


def test_a_corpus_without_the_policy_still_reads_and_says_so(world):
    """A fresh deployment must work, and must not pretend it was configured."""
    world.knowledge.repo.delete(POLICY_ASSET_ID)
    world.intake.fetcher = _Library({"doc-1": _pdf("one")})
    _deliver(world, _delivery([_document("doc-1", "ap-acme-26", "Acme.pdf")]))
    step = _intake_run(world).step("x1")
    assert step.output["found_asset"] is False
    assert step.output["folders"] == list(DEFAULT_INVOICE_FOLDERS)
    assert "built-in default" in step.summary
    assert _out(world)["read"] == 1


def test_the_policy_parses_folders_and_the_window_bound():
    values = parse_intake_policy(
        "- Invoice folder: `Accounts Payable`\n"
        "- Invoice folder: Supplier Bills/2026\n"
        "Invoices per intake window: 3\n"
    )
    assert values.folders == ["Accounts Payable", "Supplier Bills/2026"]
    assert values.window_limit == 3
    # Prose that says nothing leaves the defaults standing rather than
    # emptying the list, which would be a flow that silently does nothing.
    assert parse_intake_policy("# Intake\n\nnothing structured here").folders == list(
        DEFAULT_INVOICE_FOLDERS
    )


# ---- what a windowed flow does with trouble ---------------------------------


def test_one_unreachable_file_is_counted_and_the_others_still_run(world):
    """The doctrine's line, and the reason this flow is windowed.

    Thirty-nine invoices must not queue behind the one whose pointer 404s.
    Nothing suspends, no Task is raised for the row, and the count is on the
    run where somebody can see it.
    """
    world.intake.fetcher = _Library({"doc-1": _pdf("one"), "doc-3": _pdf("three")})
    _deliver(world, _delivery([
        _document("doc-1", "ap-acme-26", "Acme A.pdf"),
        _document("doc-2", "ap-acme-26", "Acme B.pdf"),
        _document("doc-3", "ap-acme-26", "Acme C.pdf"),
    ]))
    out = _out(world)
    assert out["read"] == 2 and out["unreachable"] == 1
    assert _intake_run(world).status == "succeeded"
    assert len(world.engine.list(flow_id="accounts-payable")) == 2
    assert [t for t in world.tasks.list() if t.flow_id == "ap-intake"] == []
    # And the one that failed is still unread, so the next window retries it.
    assert "sha256" not in _graph_document(world, "doc-2").props


def test_a_window_where_nothing_answered_raises_one_operations_task(world):
    """The aggregate escalation. Forty failures is one broken door.

    To the operations desk and not the clerk's: Connie cannot fix a document
    system that stopped serving content, and the person who can was not
    looking at her queue.
    """
    world.intake.fetcher = _Library({})
    _deliver(world, _delivery([
        _document("doc-1", "ap-acme-26", "Acme A.pdf"),
        _document("doc-2", "ap-acme-26", "Acme B.pdf"),
    ]))
    out = _out(world)
    assert out["read"] == 0 and out["unreachable"] == 2
    [task] = [t for t in world.tasks.list() if t.flow_id == "ap-intake"]
    assert task.kind == "exception"
    assert task.queue == "platform-operations"
    assert "2" in {e.value for e in task.evidence}
    # The run finished. An aggregate report is not a gate.
    assert _intake_run(world).status == "succeeded"


def test_a_file_with_no_readable_page_is_counted_not_sent_to_a_model(world):
    """A spreadsheet in the AP folder is somebody's working file.

    Counted rather than handed to an extractor that would fail at the wire and
    escalate as an engineering fault — which is a real Task on a real desk for
    a document that was never an invoice.
    """
    world.intake.fetcher = _Library({"doc-1": b"name,amount\nAcme,100\n"})
    _deliver(world, _delivery([
        _document("doc-1", "ap-acme-26", "Acme reconciliation.csv"),
    ]))
    out = _out(world)
    assert out["read"] == 0 and out["unreadable"] == 1
    assert world.engine.list(flow_id="accounts-payable") == []


def test_no_estate_registered_is_a_working_deployment(world):
    """A Cumulus with nothing to fetch from says so and finishes.

    The world's own default, which is why this is asserted rather than assumed:
    every other test in this module wires a library first.
    """
    _deliver(world, _delivery([_document("doc-1", "ap-acme-26", "Acme.pdf")]))
    out = _out(world)
    assert out["no_fetcher"] == 1 and out["read"] == 0
    assert _intake_run(world).status == "succeeded"


# ---- the window's bound ------------------------------------------------------


def test_a_backlog_longer_than_the_window_is_deferred_not_dropped(world):
    """The bound exists because the delivery waits on every case this starts.

    What it must not do is lose the remainder: the work list is a standing
    question asked of the graph, so the next window finds exactly what this
    one left.
    """
    world.knowledge.update(POLICY_ASSET_ID, AssetUpdate(
        content=(
            "## Where supplier invoices are filed\n\n"
            "- Invoice folder: Accounts Payable\n\n"
            "- Invoices per intake window: 2\n"
        ),
        updated_by="finance-ops",
    ))
    files = {f"doc-{n}": _pdf(f"invoice {n}") for n in range(5)}
    world.intake.fetcher = _Library(files)
    documents = [
        _document(f"doc-{n}", "ap-acme-26", f"Acme {n}.pdf") for n in range(5)
    ]
    _deliver(world, _delivery(documents))
    first = _out(world)
    assert first["unread"] == 5
    assert first["selected"] == 2 and first["deferred"] == 3
    assert first["read"] == 2

    _deliver(world, _delivery(documents))
    windows = sorted(
        world.engine.list(flow_id="ap-intake"), key=lambda r: r.started_at_iso
    )
    assert windows[-1].outputs["unread"] == 3
    assert windows[-1].outputs["read"] == 2
    assert len(world.engine.list(flow_id="accounts-payable")) == 4


# ---- what it lands and what it starts ---------------------------------------


def _graph_document(world, external_id: str):
    return world.graph.get(object_id(onto.DOCUMENT, f"{DMS}:{external_id}"))


def test_the_referenced_row_stops_saying_nobody_has_read_it(world):
    """The one observation this flow lands, through the Gateway, once a window.

    `dms-sync` writes a `storage_ref` and deliberately no `sha256`, which is it
    saying the file exists and nobody has read it. This is the write that makes
    that sentence stop being true, and it is guarded like every other.
    """
    content = _pdf("one")
    world.intake.fetcher = _Library({"doc-1": content})
    _deliver(world, _delivery([_document("doc-1", "ap-acme-26", "Acme.pdf")]))

    row = _graph_document(world, "doc-1")
    [case] = world.engine.list(flow_id="accounts-payable")
    stored = world.documents.get(case.inputs["document_id"])
    assert row.props["sha256"] == stored.sha256
    assert row.props["read_version"] == 1
    assert row.props["kind"] == "invoice"
    # One Action for the window, not one per file: the bulk-observation shape.
    entries = [
        e for e in world.gateway.ledger.list()
        if e.action == "record-document-read"
    ]
    assert len(entries) == 1
    assert entries[0].regime == "observation"
    assert entries[0].principal_uid == "flow:ap-intake"


def test_each_invoice_becomes_one_accounts_payable_case(world):
    """Where the batch stops being a batch.

    The event carries the stored document id, the run's case is that document,
    and the artifact the run reads is the one intake put in the store — not a
    second copy of the same bytes with a different provenance.
    """
    world.intake.fetcher = _Library({
        "doc-1": _pdf("one"), "doc-2": _pdf("two"),
    })
    _deliver(world, _delivery([
        _document("doc-1", "ap-acme-26", "Acme A.pdf"),
        _document("doc-2", "ap-acme-26", "Acme B.pdf"),
    ]))
    runs = world.engine.list(flow_id="accounts-payable")
    assert len(runs) == 2
    for run in runs:
        document_id = run.inputs["document_id"]
        assert run.case_ref == document_id
        assert run.inputs["submitted_by"] == "flow:ap-intake"
        assert "hint" not in run.inputs
        record = world.documents.get(document_id)
        assert record is not None and record.byte_size > 0
        assert base64.b64decode(record.content_b64).startswith(b"%PDF")

    # The log says what caused what, before the runs it names existed.
    raised = [
        e for e in world.dispatcher.events.list() if e.name == "InvoiceReceived"
    ]
    assert len(raised) == 2
    assert {e.case_ref for e in raised} == {r.inputs["document_id"] for r in runs}
    assert all(e.triggers == ["invoice-received"] for e in raised)


def test_a_failing_case_does_not_take_the_window_with_it(world):
    """One broken invoice is a row on the report, not the end of the pass."""
    world.intake.fetcher = _Library({
        "doc-1": _pdf("one"), "doc-2": _pdf("two"),
    })

    broken = {"count": 0}
    real = world.payables.reader.read

    def fail_once(*args, **kwargs):
        broken["count"] += 1
        if broken["count"] <= 2:  # the step retries once
            raise RuntimeError("the extractor fell over")
        return real(*args, **kwargs)

    world.payables.reader.read = fail_once
    _deliver(world, _delivery([
        _document("doc-1", "ap-acme-26", "Acme A.pdf"),
        _document("doc-2", "ap-acme-26", "Acme B.pdf"),
    ]))
    out = _out(world)
    assert out["read"] == 2 and out["raised"] == 2
    # One suspended into the operations queue, one ran. Neither stopped the
    # window, and the run says which was which.
    assert len(out["runs"]) + len(out["failed_runs"]) == 2
    assert _intake_run(world).status == "succeeded"


def test_the_window_journal_holds_ids_and_not_invoices(world):
    """A step's output *is* the run journal, and a journal is one document.

    Twenty-five base64 invoices on one step would be well past a Firestore
    document's cap, and losing the journal loses the window's whole account of
    itself — including the counts that say what it could not do.
    """
    world.intake.fetcher = _Library({
        f"doc-{n}": _pdf(f"invoice {n}" + "x" * 4000) for n in range(3)
    })
    _deliver(world, _delivery([
        _document(f"doc-{n}", "ap-acme-26", f"Acme {n}.pdf") for n in range(3)
    ]))
    fetched = _intake_run(world).step("s1").output
    assert [set(row) for row in fetched["stored"]] == [
        {"document_id", "external_id", "filename", "folder_path", "byte_size"}
    ] * 3
    assert "%PDF" not in json.dumps(_intake_run(world).model_dump(mode="json"))
    # And the bytes did reach the case that needed them.
    for run in world.engine.list(flow_id="accounts-payable"):
        assert base64.b64decode(run.inputs["image_b64"]).startswith(b"%PDF")
