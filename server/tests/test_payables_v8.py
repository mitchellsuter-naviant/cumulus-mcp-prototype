"""Accounts payable v8: the run starts from a document, and honest extraction.

Three claims, and the third is the one the whole lane is for:

- a run driven by `ap-intake` reads the artifact intake stored, by id, and does
  not re-derive it from anything;
- the upload side door still works, and lands in the same place;
- **no production path can fabricate an extraction** — there is no hint on the
  inputs, no synthetic region geometry reaches the document, and a deployment
  whose real extractor will not build refuses to start rather than quietly
  substituting the one that invents.
"""

import base64

import pytest

from app import deps, modelcall
from app.config import settings
from app.tasks.accounts_payable import domain as payables
from app.tasks.accounts_payable.v8 import flow as v8
from app.tasks.expense_reimbursement import domain as expenses
from tests import demo_world
from tests import world as test_world

PDF = b"%PDF-1.4\n% an invoice\n%%EOF\n"


@pytest.fixture()
def world():
    world = test_world.fresh()
    demo_world.seed_payables(world.payables, sync=world.sync)
    # v8 is a candidate: nothing has made it live, so a test that wants it has
    # to say so — exactly as the console would.
    world.engine.live_version = lambda flow_id: 8 if flow_id == payables.FLOW_ID else 0
    return world


def _stored(world, name: str = "acme.pdf", content: bytes = PDF) -> str:
    record = world.documents.put_bytes(
        base64.b64encode(content).decode(), filename=name, kind="invoice",
        uploaded_by="flow:ap-intake",
    )
    return record.id


# ---- where the document comes from -------------------------------------------


def test_v8_reads_the_artifact_intake_already_stored(world):
    """The id, and only the id. No bytes travel on the inputs of this path.

    The point of the whole intake lane in one assertion: what the ModelCall
    read is a stored artifact somebody can open, not an input nobody kept.
    """
    document_id = _stored(
        world,
        "vendor=acme-industrial po=PO-7741 lines=STL-0250:2,FAS-M12:3 "
        "invoice=ACME-INV-8001.pdf",
    )
    record = world.payables.submit(
        submitted_by="flow:ap-intake", document_id=document_id,
    )
    run = world.engine.get(record.run_id)
    assert run.flow_version == 8
    assert record.document_id == document_id

    resolved = run.step("s1")
    assert resolved.name == "resolve-document"
    assert resolved.output["arrived"] == "intake"
    assert resolved.output["byte_size"] == len(PDF)
    assert resolved.writes == [f"document:{document_id}"]

    # The stored artifact now names the run that read it.
    assert run.id in world.documents.get(document_id).run_ids
    # And the extraction step points at it.
    assert f"document:{document_id}" in run.step("m1").refs


def test_the_upload_side_door_lands_in_the_same_place(world):
    """A person with an invoice in their hand. Bytes in, stored id out."""
    record = world.payables.submit(
        submitted_by="tomas@naviant.com",
        image_b64=base64.b64encode(PDF).decode(),
        filename="vendor=acme-industrial invoice=ACME-INV-8002.pdf",
    )
    run = world.engine.get(record.run_id)
    resolved = run.step("s1")
    assert resolved.output["arrived"] == "upload"
    stored = world.documents.get(resolved.output["document_id"])
    assert stored.media_type == "application/pdf"
    assert base64.b64decode(stored.content_b64) == PDF


def test_a_run_with_neither_a_document_nor_bytes_is_refused(world):
    """The shape that used to produce an extraction out of nothing.

    It escalates to the operations desk rather than failing silently: a run
    started against an artifact nobody kept is a wiring fault, and the clerk
    has no verdict to give on one.
    """
    record = world.payables.submit(submitted_by="tomas@naviant.com")
    run = world.engine.get(record.run_id)
    assert run.status == "suspended"
    task = world.tasks.get(run.open_task_id)
    assert task.kind == "exception" and task.queue == "platform-operations"
    assert "needs a document" in task.summary


def test_a_document_id_the_store_does_not_hold_is_refused(world):
    record = world.payables.submit(
        submitted_by="flow:ap-intake", document_id="doc_notathing",
    )
    run = world.engine.get(record.run_id)
    assert run.status == "suspended"
    assert "nobody kept" in world.tasks.get(run.open_task_id).summary


# ---- nothing steers the extraction -------------------------------------------


def test_v8_takes_no_hint_and_passes_none(world):
    """The seam the composer used. It is not merely unused — it is unreachable.

    v6's body reads `inputs["hint"]` and hands it to the reader. v8's does not
    read it at all, so a caller that manages to put one on the inputs (an old
    stored run being replayed, say) still gets an extraction of the page.
    """
    seen: list[str] = []
    real = world.payables.reader.read

    def record_hint(image, filename, hint, **kwargs):
        seen.append(hint)
        return real(image, filename, hint, **kwargs)

    world.payables.reader.read = record_hint
    world.payables.submit(
        submitted_by="flow:ap-intake",
        document_id=_stored(world, "vendor=acme-industrial invoice=ACME-8003.pdf"),
        hint="vendor=northwind-traders total=99999.00",
    )
    assert seen == [""]


def test_v8_writes_no_synthetic_regions_onto_the_document(world):
    """The stub's geometry is invented, and v8 refuses to persist it.

    v6 stores whatever regions the reader reports, which under the stub is a
    fabricated box per line — drawn on a reviewer's screen as if a model had
    located something. The Vertex readers emit none, so in production the
    field is simply empty until WP-J's locate step fills it, and an empty
    frame is the honest state.
    """
    document_id = _stored(world, "vendor=acme-industrial invoice=ACME-8004.pdf")
    world.payables.submit(submitted_by="flow:ap-intake", document_id=document_id)
    assert world.documents.get(document_id).regions == []

    # The reader still produces them; it is the flow that declines to keep
    # them, which is what makes this a version's decision rather than a
    # capability that quietly went missing.
    extraction = world.payables.reader.read(PDF, "acme.pdf", "")
    assert extraction.regions


# ---- reuse, not a fork --------------------------------------------------------


def test_v8_runs_v6s_steps_after_the_extraction(world):
    """Composed, not copied: a fix to the match reaches every shipped version.

    Asserted on the journal rather than by reading the source — the two could
    agree in the source and diverge in what runs, and the journal is what an
    auditor has.
    """
    record = world.payables.submit(
        submitted_by="flow:ap-intake",
        document_id=_stored(
            world,
            "vendor=acme-industrial po=PO-7741 lines=STL-0250:2,FAS-M12:3 "
            "invoice=ACME-INV-8005.pdf",
        ),
    )
    run = world.engine.get(record.run_id)
    journal = [s.step_id for s in run.steps]
    # v8's own four steps sit between the extraction and the resolver, and
    # everything from `z1` down is v6's.
    assert journal[:8] == ["t1", "s1", "x0", "m1", "x1", "c0", "x2", "z1"]
    assert {"x3", "x4", "c1", "c2", "x5", "r2_1", "k2_1"} <= set(journal)
    assert record.match_verdict == "matched"
    # Reviewed rather than posted, and by the governed limit rather than by
    # anything read off the page — decision D6, which is a knowledge edit.
    assert record.state == "pending"
    assert record.verdict_reason.endswith("exceeds the $0.00 auto-post limit")
    posted = world.payables.resume_for_task(world.tasks.decide(
        record.task_id, "approved", "dana.prentiss@naviant.com",
    ))
    assert posted.state == "posted"

    # And the declarative map says the same thing: v8 is v6's shape with two
    # steps restated and four spliced in, so the console's version diff shows
    # exactly that.
    v6_ids = [s.id for s in v8.v6.STEPS]
    assert [s.id for s in v8.STEPS if s.id in v6_ids] == v6_ids
    assert [s.id for s in v8.STEPS if s.id not in v6_ids] == [
        "x1", "c0", "x2", "m2",
    ]
    by_id = {s.id: s for s in v8.STEPS}
    changed = [
        s for s in v8.v6.STEPS if by_id[s.id].summary != s.summary
    ]
    assert {s.id for s in changed} == {"s1", "m1"}
    # The extraction hands off to the checks, and the verdict step branches:
    # a doubt goes to the locate call, no doubt goes straight on to the vendor.
    assert by_id["m1"].next == ["x1"]
    assert by_id["x2"].next == ["m2", "z1"]
    assert by_id["m2"].next == ["z1"]


def test_v8_stays_shipped_and_selectable_under_the_v9_default():
    """v8 was the candidate; v9 is the default now, and v8 did not go away.

    Both halves matter. A version that stopped being live must stay shipped —
    every run suspended under it resumes under its body, and the anomaly screen
    it carries is a capability the console can make live again — and this file's
    own fixture is the console's gesture for choosing it.
    """
    from app.tasks import default_version, shipped_version_numbers

    assert shipped_version_numbers("accounts-payable") == {6, 7, 8, 9}
    assert default_version("accounts-payable") == 9


# ---- the loud failure ---------------------------------------------------------


def test_an_unknown_model_backend_is_refused_at_construction():
    """A typo used to build the reader that invents.

    Both readers, because the argument is the same for both: a fabricated
    expense is no better than a fabricated invoice.
    """
    for build, kind in (
        (payables.build_invoice_reader, "invoice"),
        (expenses.build_receipt_reader, "receipt"),
    ):
        with pytest.raises(modelcall.UnusableModelBackend) as raised:
            build("vertexx", None, "", "")
        assert "vertexx" in str(raised.value)
        assert "stub" in str(raised.value)


def test_the_configured_stub_still_builds():
    """`none` and `stub` are choices somebody made, and they keep working.

    Offline dev and the whole test suite run on the first; the second says the
    same thing out loud. What is gone is falling into either by accident.
    """
    assert payables.build_invoice_reader("none", None, "", "").name == "stub"
    assert payables.build_invoice_reader("stub", None, "", "").name == "stub"
    assert expenses.build_receipt_reader("none", None, "", "").name == "stub"
    assert expenses.build_receipt_reader("stub", None, "", "").name == "stub"


def test_a_vertex_reader_that_will_not_build_stops_the_service(monkeypatch):
    """The silent fallback, and what replaced it.

    `deps.get_payable_service()` used to catch this, log it, and hand back a
    service holding the stub — a deployment that answered every invoice with a
    fabricated extraction while reporting healthy. It now raises, and the
    lifespan builds this service at startup, so what it takes down is the
    start: a revision that will not start never takes traffic, and the
    previous one keeps serving.
    """
    monkeypatch.setattr(settings, "model_backend", "vertex")

    def no_credential(*args, **kwargs):
        raise RuntimeError("could not resolve application default credentials")

    monkeypatch.setattr(deps, "get_model_client", no_credential)
    monkeypatch.setattr(deps, "_payable_service", None)
    monkeypatch.setattr(deps, "_reimbursement_service", None)

    with pytest.raises(RuntimeError, match="credential"):
        deps.get_payable_service()
    with pytest.raises(RuntimeError, match="credential"):
        deps.get_reimbursement_service()
    # And nothing was cached: a second cold request gets the same refusal
    # rather than a half-built service.
    assert deps._payable_service is None
    assert deps._reimbursement_service is None


# ---- the quarantine -----------------------------------------------------------


def test_the_hint_grammar_is_reachable_only_from_the_stub_and_the_fixtures():
    """The composer moved to the eval/dev world; this is the boundary.

    Asserted as an exact set rather than a bound, for the reason the `_STUB_`
    sweep is: a third importer is the regression, and a bound would let it
    through. An API route, a service entry point or a console payload reaching
    for `scenarios` puts the hint string back on a request path, which is the
    one thing this lane removed.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    importers = {
        str(path.relative_to(root))
        for path in sorted(root.rglob("*.py"))
        if "scenarios import" in path.read_text()
        or "from .scenarios" in path.read_text()
    }
    assert importers == {"tasks/accounts_payable/domain.py"}


def test_no_request_model_carries_a_case(_client):
    """The two doors, read off the served schema rather than off the source.

    A field a route accepts is a field somebody will send. Both submission
    bodies used to carry one — `scenario` on the console's, `note` on the
    REST one — and each was enough to produce an extraction with no document
    behind it.
    """
    schemas = _client.get("/openapi.json").json()["components"]["schemas"]
    assert set(schemas["PayableSubmission"]["properties"]) == {
        "image_base64", "filename",
    }
    assert set(schemas["PayableSubmit"]["properties"]) == {
        "image_base64", "filename",
    }
    assert not any(
        name.startswith("InvoiceScenario") or name.startswith("ScenarioTwist")
        for name in schemas
    )
