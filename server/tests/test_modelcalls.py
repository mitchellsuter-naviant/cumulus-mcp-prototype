"""The ModelCall ledger: every invocation on the record, and reconcilable.

Four hand-rolled Vertex clients used to sit behind four call sites. Three of
them metered their token counts onto a `StepRecord` and the fourth — the
embedder — metered nothing at all, so a corpus backfill cost real money that
appeared on no page anywhere. The journal was also the *only* record: a run
whose planner took four turns recorded one number, a call that raised recorded
nothing, and a call the model refused recorded a step that looked like every
other step.

What the ledger adds is per-invocation truth beside the per-step rollup, and
the property that matters most is that the two **agree**: the rows for one step
add up to what the step recorded, because the same governed rate sheet priced
both. The tests here are mostly that agreement, plus the three rows the journal
could never hold — a failure, a refusal, and an embedding.

No network anywhere. A fake wire shaped exactly like `genai.Client` —
`models.generate_content`, `models.embed_content` — is what proves this on the
real flow path, which is the only place worth proving it.
"""

from __future__ import annotations

import base64
import json

import pytest

import app.deps as deps
from app.answering import (
    ANSWER_FLOW_ID,
    PLAN_PURPOSE,
    AnswerService,
    VertexAnswerBackend,
)
from app.embeddings import DOCUMENT_PURPOSE, QUERY_PURPOSE, VertexEmbedder
from app.flows import cost_basis, fleet_model_rates
from app.modelcall import (
    GovernedModelClient,
    InMemoryModelCallStore,
    ModelCallContext,
    current,
    governed_ref,
    summarize,
    under,
)
from app.tasks.accounts_payable.domain import (
    EXTRACT_PURPOSE as INVOICE_PURPOSE,
    PROMPT_ASSET_ID as INVOICE_PROMPT_ASSET,
    VertexInvoiceReader,
)
from app.tasks.expense_reimbursement.domain import (
    EXTRACT_PURPOSE as RECEIPT_PURPOSE,
    SubmittedDocument,
    VertexReceiptReader,
)
from tests import world as test_world

MODEL = "gemini-3.5-flash-lite"
#: The rates the seeded `model-pricing` asset carries for the model above:
#: $0.10 / M input, $0.40 / M output.
RATE_IN, RATE_OUT = 0.10, 0.40


def priced(input_tokens: int, output_tokens: int) -> float:
    return round((input_tokens * RATE_IN + output_tokens * RATE_OUT) / 1_000_000, 6)


# ---------------------------------------------------------------------------
# A wire, not a network
# ---------------------------------------------------------------------------


class Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.prompt_token_count = input_tokens
        self.candidates_token_count = output_tokens


class Reply:
    """A `generate_content` response, in the shape the SDK returns one.

    `finish_reason` is here because a refusal is a *response*, not an error: the
    call succeeded, the tokens were spent, and the model declined. A ledger that
    filed that as a success would lose the one row somebody is looking for.
    """

    def __init__(
        self,
        text: str = "{}",
        *,
        input_tokens: int = 1_000,
        output_tokens: int = 100,
        finish_reason: str = "STOP",
        parts: list | None = None,
    ) -> None:
        self.text = text
        self.usage_metadata = Usage(input_tokens, output_tokens)
        self.prompt_feedback = None
        content = type("Content", (), {"parts": parts or []})()
        self.candidates = [
            type("Candidate", (), {
                "content": content, "finish_reason": finish_reason,
            })()
        ]


class Wire:
    """Stands in for `genai.Client`: replies in order, or raises."""

    def __init__(self, *replies) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model})
        reply = self._replies[min(len(self.calls) - 1, len(self._replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def embed_content(self, *, model, contents, config):
        self.calls.append({"model": model, "texts": len(contents)})
        reply = self._replies[min(len(self.calls) - 1, len(self._replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


class Vector:
    def __init__(self, values: list[float], token_count: int | None) -> None:
        self.values = values
        self.statistics = (
            None if token_count is None
            else type("Stats", (), {"token_count": token_count, "truncated": False})()
        )


class Vectors:
    def __init__(self, *vectors: Vector) -> None:
        self.embeddings = list(vectors)


#: An invoice that agrees with itself, which is what makes these cost tests
#: about cost. v9 asks a second, cheap model where a *disputed* field is
#: printed, so a fixture with no billed lines would make one call here and two
#: there — and a test of what one extraction costs would be measuring two.
INVOICE_JSON = json.dumps({
    "vendor_name": "Acme Industrial Inc.",
    "invoice_number": "ACME-INV-77001",
    "invoice_date": "2026-08-30",
    "due_date": "2026-09-29",
    "subtotal_usd": 1200.0,
    "total_usd": 1200.0,
    "confidence": 0.93,
    "lines": [{
        "sku": "STL-0250", "description": "Hot-rolled steel plate, 1/4 in",
        "quantity": 2, "unit_price_usd": 600.0, "amount_usd": 1200.0,
        "kind": "goods",
    }],
})

RECEIPT_JSON = json.dumps({
    "vendor": "corner-bistro",
    "date": "2026-08-30",
    "total_usd": 42.0,
    "category": "meals",
    "confidence": 0.94,
})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def ledger(world):
    """This test's own ledger, wired where the deployment's would be."""
    store = InMemoryModelCallStore()
    deps._modelcall_store = store
    return store


@pytest.fixture()
def client(_client, world, ledger):
    return _client


def governed(ledger, wire, **kwargs) -> GovernedModelClient:
    """A governed client over a fake wire, priced from this world's corpus."""
    return GovernedModelClient(wire, ledger, fleet_model_rates, **kwargs)


#: A page for the run to read. Not decoration: from v9 an accounts-payable run
#: with no artifact behind it is refused at `s1`, so a test about what the
#: extraction *cost* has to hand over something to extract from.
PDF = b"%PDF-1.4\n% an invoice\n%%EOF\n"


def submit_invoice(world, ledger, *replies):
    world.payables.reader = VertexInvoiceReader(
        MODEL, governed(ledger, Wire(*replies))
    )
    return world.payables.submit(
        submitted_by="ap.clerk@naviant.com", filename="inv.pdf", hint="",
        image_b64=base64.b64encode(PDF).decode(),
    )


def ap_run(world, record):
    return world.engine.get(getattr(record, "run_id", "") or record.id)


# ---------------------------------------------------------------------------
# 1 · A call that worked
# ---------------------------------------------------------------------------


def test_a_successful_call_lands_on_the_ledger_with_its_coordinates(world, ledger):
    """One extraction, one row, and every column an auditor joins on.

    The run, the step and the case are the keying Q3 asked for; the prompt is
    the *governed* one at the revision the flow read, so the row can answer
    "which instruction produced this claim" without anybody keeping a copy of
    the instruction here.
    """
    record = submit_invoice(world, ledger, Reply(INVOICE_JSON))
    run = ap_run(world, record)

    rows = ledger.list()
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "succeeded"
    assert row.model == MODEL
    assert row.purpose == INVOICE_PURPOSE
    assert (row.flow_id, row.run_id, row.step_id) == ("accounts-payable", run.id, "m1")
    assert row.flow_version == run.flow_version
    assert row.case_ref == run.case_ref
    assert row.prompt_ref.startswith(f"knowledge:{INVOICE_PROMPT_ASSET}@rev")
    assert (row.input_tokens, row.output_tokens) == (1_000, 100)
    assert row.cost_usd == priced(1_000, 100)
    assert row.error == ""


def test_the_row_holds_references_and_never_the_document(world, ledger):
    """The refusal to copy inputs, asserted rather than trusted.

    A ledger that kept the prompt text and the bytes would be a second,
    ungoverned copy of every invoice a customer ever submitted, under a
    retention policy nobody wrote. The row names things; it does not hold them.
    """
    submit_invoice(world, ledger, Reply(INVOICE_JSON))
    row = ledger.list()[0]

    dumped = row.model_dump()
    assert "Acme Industrial" not in json.dumps(dumped)
    assert set(dumped) == {
        "id", "at_iso", "flow_id", "flow_version", "run_id", "step_id",
        "case_ref", "model", "purpose", "prompt_ref", "input_tokens",
        "output_tokens", "cost_usd", "latency_ms", "outcome", "error",
    }


# ---------------------------------------------------------------------------
# 2 · A call that did not
# ---------------------------------------------------------------------------


def test_a_failed_call_is_still_a_call(world, ledger):
    """The row the journal could never hold.

    A step that raised records a failure and no accounting at all, which reads
    as "nothing was spent here". Vertex was called, it consumed whatever it
    consumed before it broke, and the retry spent it again — so the ledger
    carries two rows and the step's own failure carries none.
    """
    record = submit_invoice(world, ledger, RuntimeError("upstream is down"))
    run = ap_run(world, record)

    rows = ledger.list()
    # Two, because the step retries once before escalating. A ledger that
    # collapsed them would under-report exactly the calls that cost twice.
    assert len(rows) == 2
    assert {r.outcome for r in rows} == {"failed"}
    assert all("upstream is down" in r.error for r in rows)
    assert all(r.run_id == run.id and r.step_id == "m1" for r in rows)
    # The exception still reached the engine: the append happens on the way
    # past, it does not swallow the failure.
    assert next(s for s in run.steps if s.step_id == "m1").status == "failed"


def test_a_refusal_is_not_a_failure_and_not_a_success(ledger, world):
    """Three outcomes, kept apart, because they mean three different things.

    A model declining is governance information — it is the row somebody goes
    looking for — and it is invisible if it is filed as a plain success. It is
    equally not a failure: nothing broke, and an operator triaging an outage
    should not find it in the list of things that did.
    """
    client = governed(ledger, Wire(Reply("", finish_reason="SAFETY")))
    response, usage = client.generate(
        model=MODEL, contents=[], config=None, purpose="probe",
    )

    row = ledger.list()[0]
    assert row.outcome == "refused"
    assert "SAFETY" in row.error
    # It still cost what it cost.
    assert row.input_tokens == 1_000
    assert row.cost_usd == priced(1_000, 100)
    assert usage.tokens == 1_100
    assert response is not None


def test_a_blocked_prompt_is_a_refusal_too(ledger, world):
    """The other end of the same fact: the request never reached the model."""
    reply = Reply("")
    reply.prompt_feedback = type("Feedback", (), {"block_reason": "SAFETY"})()
    client = governed(ledger, Wire(reply))
    client.generate(model=MODEL, contents=[], config=None, purpose="probe")

    row = ledger.list()[0]
    assert row.outcome == "refused"
    assert "prompt blocked" in row.error


# ---------------------------------------------------------------------------
# 3 · The ledger and the journal agree
# ---------------------------------------------------------------------------


def test_the_ledger_and_the_step_agree_on_one_call(world, ledger):
    """The whole point of adding a second record: it must not be a second story.

    Both are priced from the same governed sheet by construction — the client
    prices its row, hands the priced usage back, and `meter=` files that exact
    object on the step — so a divergence here would mean two rate sheets in one
    process, which is the failure this arrangement exists to make impossible.
    """
    record = submit_invoice(world, ledger, Reply(INVOICE_JSON))
    run = ap_run(world, record)
    step = next(s for s in run.steps if s.kind == "modelcall")
    rows = [r for r in ledger.list() if r.run_id == run.id and r.step_id == step.step_id]

    assert sum(r.input_tokens + r.output_tokens for r in rows) == step.tokens
    assert round(sum(r.cost_usd for r in rows), 6) == step.cost_usd
    assert {r.model for r in rows} == {step.model}
    # And the run-level rollup the console reads is the same money again.
    assert run.cost_usd == step.cost_usd
    assert run.model_calls == 1


def test_one_step_per_document_means_one_row_per_document(world, ledger):
    """Why the step id is on the row at all.

    The expense flow files a ModelCall per receipt. Without the step id a
    three-receipt request is three rows nobody can tell apart — the cost of
    reading a particular document, which is the question somebody actually
    asks, would be unanswerable.
    """
    world.expenses.reader = VertexReceiptReader(
        MODEL, governed(ledger, Wire(
            Reply(RECEIPT_JSON, input_tokens=800, output_tokens=60),
            Reply(RECEIPT_JSON, input_tokens=1_600, output_tokens=90),
        ))
    )
    request = world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[
            SubmittedDocument(filename="a.jpg", hint="total=42.00 category=meals"),
            SubmittedDocument(filename="b.jpg", hint="total=42.00 category=meals"),
        ],
        commentary="Two receipts.",
    )
    run = world.engine.get(request.run_id)

    rows = {r.step_id: r for r in ledger.list()}
    # One row per receipt, keyed by the step that read it: `m1_{index}` is the
    # extraction family's second and third instances, the same suffix
    # convention every repeated step in a flow package uses.
    assert set(rows) == {"m1_1", "m1_2"}
    assert all(r.purpose == RECEIPT_PURPOSE for r in rows.values())
    assert (rows["m1_1"].input_tokens, rows["m1_2"].input_tokens) == (800, 1_600)
    for step in (s for s in run.steps if s.kind == "modelcall"):
        assert rows[step.step_id].input_tokens + rows[step.step_id].output_tokens == step.tokens
        assert rows[step.step_id].cost_usd == step.cost_usd


def test_several_planner_turns_are_several_rows_under_one_step(world, ledger):
    """One question is one journal row and however many ledger rows it took.

    The fleet counts questions, so the step stays one — a row per turn would
    make `model_calls` a measure of the planner's verbosity. The ledger keeps
    the turns, because "this answer took three calls" is the fact an operator
    needs when a bill moves and it is exactly what a sum hides. The two are
    reconciled by addition, which is the contract.
    """
    part = type("Part", (), {
        "function_call": type("Call", (), {
            "name": "search_knowledge", "args": {"query": "vpn"},
        })(),
    })()
    backend = VertexAnswerBackend(
        MODEL,
        governed(ledger, Wire(
            Reply("", input_tokens=900, output_tokens=40, parts=[part]),
            Reply("", input_tokens=1_500, output_tokens=60, parts=[part]),
            Reply("The VPN standard is owned by platform-eng.",
                  input_tokens=2_000, output_tokens=120),
        )),
        max_steps=6,
        max_output_tokens=2048,
    )
    service = AnswerService(
        world.knowledge, None, backend,
        graph=world.graph, dispatcher=world.dispatcher, engine=world.engine,
    )
    service.ask("Which team owns the VPN standard?")

    run = world.engine.list(flow_id=ANSWER_FLOW_ID)[0]
    step = next(s for s in run.steps if s.kind == "modelcall")
    rows = ledger.list()

    assert run.model_calls == 1
    assert len(rows) == 3
    assert {r.purpose for r in rows} == {PLAN_PURPOSE}
    assert {r.step_id for r in rows} == {"m1"}
    assert {r.run_id for r in rows} == {run.id}
    assert sum(r.input_tokens + r.output_tokens for r in rows) == step.tokens
    assert round(sum(r.cost_usd for r in rows), 6) == step.cost_usd


def test_a_replayed_step_does_not_call_the_model_or_the_ledger_twice(world, ledger):
    """Replay is what makes a resumable run cheap; a double row would undo it.

    The extraction is memoized on the journal, so a resumed run replays it and
    re-executes nothing. If the ledger grew a row on the way past, a run
    resumed three times would report three extractions that never happened —
    and the ledger would be the one record disagreeing with the journal.
    """
    record = submit_invoice(world, ledger, Reply(INVOICE_JSON))
    run = ap_run(world, record)
    assert run.status == "suspended", "this invoice should have raised a task"
    before = len(ledger.list())

    world.tasks.decide(run.open_task_id, "approved", "ap.clerk@naviant.com")
    world.payables.resume_for_task(world.tasks.get(run.open_task_id))

    assert len(ledger.list()) == before


# ---------------------------------------------------------------------------
# 4 · Embeddings, metered for the first time
# ---------------------------------------------------------------------------


def embedder(ledger, *replies) -> VertexEmbedder:
    return VertexEmbedder(
        "gemini-embedding-2", governed(ledger, Wire(*replies)),
        dimensions=0, max_batch=250, max_input_chars=8_000,
    )


def test_an_embedding_call_is_metered(world, ledger):
    """The call site that reported nothing at all.

    Three readers took their counts off `usage_metadata`; this one took none,
    so a backfill of a few thousand documents cost a real number of dollars
    that appeared on no page. The count is not in `usage_metadata` — an embed
    response has none — it is per vector, in `statistics.token_count`.
    """
    embedder(ledger, Vectors(Vector([0.0, 1.0], 512))).embed_documents(["one"])

    row = ledger.list()[0]
    assert row.outcome == "succeeded"
    assert row.model == "gemini-embedding-2"
    assert row.purpose == DOCUMENT_PURPOSE
    assert row.input_tokens == 512
    # A vector is not tokens, so there is no output side to report.
    assert row.output_tokens == 0


def test_an_embedding_has_no_run_even_when_one_is_on_the_stack(world, ledger):
    """Empty run coordinates, on purpose and by construction.

    Embedding is corpus work — a backfill thread, a query encoder — and when it
    does happen underneath a running step it is retrieval, not that step's own
    ModelCall: the step's `meter=` does not count it. Borrowing the step's id
    would leave the journal and the ledger disagreeing about what that step
    cost, which is the one thing this ledger must never do.
    """
    with under(ModelCallContext(
        flow_id="answer-question", run_id="run_x", step_id="m1", case_ref="q1",
    )):
        embedder(ledger, Vectors(Vector([1.0, 0.0], 12))).embed_query("vpn")

    row = ledger.list()[0]
    assert row.purpose == QUERY_PURPOSE
    assert (row.flow_id, row.run_id, row.step_id, row.case_ref) == ("", "", "", "")


def test_an_encoder_the_sheet_does_not_price_is_unpriced_never_cheap(world, ledger):
    """The sheet has no embedding row, so the cost is a hole and says so.

    Zero here means *nobody knows the rate*. Inventing one — even a plausible
    one — would put a confident number over that hole, which is the same class
    of dishonesty as a confident invented answer and the one that survives
    longer because it looks like good news.
    """
    embedder(ledger, Vectors(Vector([0.0, 1.0], 4_000))).embed_documents(["one"])

    row = ledger.list()[0]
    assert row.input_tokens == 4_000
    assert row.cost_usd == 0.0
    assert summarize(ledger.list()).cost_basis == "unrecorded"


def test_an_encoder_that_reports_no_statistics_records_zero_not_a_guess(world, ledger):
    """A missing count is `unrecorded`, not an estimate from character length."""
    embedder(ledger, Vectors(Vector([0.0, 1.0], None))).embed_documents(["one"])

    assert ledger.list()[0].input_tokens == 0


def test_a_failed_embedding_is_recorded_and_re_raised(world, ledger):
    """Same posture as generation: the row is written on the way past."""
    with pytest.raises(RuntimeError):
        embedder(ledger, RuntimeError("quota")).embed_documents(["one"])

    row = ledger.list()[0]
    assert row.outcome == "failed"
    assert "quota" in row.error


# ---------------------------------------------------------------------------
# 5 · The scope, and what it refuses to guess
# ---------------------------------------------------------------------------


def test_the_scope_is_reset_so_a_finished_run_cannot_lend_its_id(world, ledger):
    """A row naming the wrong run is worse than one naming none.

    The second is visibly a gap; the first is an audit fact with a plausible
    shape and nothing on the page to say it is wrong.
    """
    submit_invoice(world, ledger, Reply(INVOICE_JSON))
    assert current() == ModelCallContext()

    governed(ledger, Wire(Reply(INVOICE_JSON))).generate(
        model=MODEL, contents=[], config=None, purpose="probe",
    )
    assert ledger.list()[0].run_id == ""


def test_the_prompt_revision_comes_off_the_step_that_declared_it(world, ledger):
    """Not a second read of the asset, which could name a different revision.

    The flow reads the prompt in its own step and names that revision on the
    metered one; taking it from anywhere else would answer "which instruction
    produced this claim" twice, and the two answers would differ exactly in the
    run where somebody published a new prompt mid-flight.
    """
    with under(ModelCallContext(read_set=[
        "document:doc_1", "knowledge:invoice-extraction-prompt@rev7",
        "knowledge:model-pricing@rev2",
    ])):
        assert governed_ref("invoice-extraction-prompt") == (
            "knowledge:invoice-extraction-prompt@rev7"
        )
        # A prompt the step never declared reading is not invented from the id.
        assert governed_ref("receipt-extraction-prompt") == ""


def test_the_stub_backend_builds_no_vertex_client_at_all(world, ledger):
    """`MODEL_BACKEND=none` still means *nothing reaches google-genai*.

    A shared client is easy to build eagerly and hand to everyone, and doing
    that would quietly resolve credentials and open a pool on every local box
    and in every test run — turning the one setting that guarantees an offline
    deployment into a setting that only guarantees an unused one.
    """
    from app import modelcall

    assert deps.get_model_client() is None
    assert deps.get_embedding_client() is None
    assert modelcall._WIRES == {}


def test_the_stub_readers_make_no_calls_and_write_no_rows(world, ledger):
    """`MODEL_BACKEND=none` runs a whole flow and bills nothing, truthfully.

    The counterpart of `cost_basis`'s `no-model-calls`: an empty ledger here is
    the honest record of a local deployment that called nothing, and it must
    not be confused with a ledger that lost its rows.
    """
    world.payables.submit(
        submitted_by="ap.clerk@naviant.com", filename="inv.pdf", hint="",
    )
    assert ledger.list() == []
    assert summarize(ledger.list()).cost_basis == "no-model-calls"


# ---------------------------------------------------------------------------
# 6 · The REST surface
# ---------------------------------------------------------------------------


def seed_rows(world, ledger):
    """One AP extraction and one planner question, over two models."""
    submit_invoice(world, ledger, Reply(INVOICE_JSON))
    embedder(ledger, Vectors(Vector([0.0, 1.0], 300))).embed_query("vpn")
    return ledger.list()


def test_the_endpoint_lists_the_ledger_newest_first(world, ledger, client):
    seed_rows(world, ledger)
    rows = client.get("/api/v1/modelcalls").json()
    assert len(rows) == 2
    assert rows[0]["at_iso"] >= rows[1]["at_iso"]
    assert {r["purpose"] for r in rows} == {INVOICE_PURPOSE, QUERY_PURPOSE}


def test_the_endpoint_filters(world, ledger, client):
    """Five filters, each answering a different operator's question."""
    seed_rows(world, ledger)
    run_id = ledger.list()[-1].run_id

    by_run = client.get("/api/v1/modelcalls", params={"run_id": run_id}).json()
    assert [r["purpose"] for r in by_run] == [INVOICE_PURPOSE]

    by_flow = client.get(
        "/api/v1/modelcalls", params={"flow_id": "accounts-payable"}
    ).json()
    assert len(by_flow) == 1

    by_model = client.get("/api/v1/modelcalls", params={"model": MODEL}).json()
    assert len(by_model) == 1

    by_purpose = client.get(
        "/api/v1/modelcalls", params={"purpose": QUERY_PURPOSE}
    ).json()
    assert len(by_purpose) == 1

    # `since` is a text comparison over ISO instants, which is exact.
    assert client.get(
        "/api/v1/modelcalls", params={"since": "2999-01-01T00:00:00+00:00"}
    ).json() == []
    assert len(client.get(
        "/api/v1/modelcalls", params={"since": "2000-01-01T00:00:00+00:00"}
    ).json()) == 2


def test_the_stats_endpoint_says_how_much_of_the_bill_is_known(world, ledger, client):
    """`partial`, which is the state a real deployment is actually in.

    The extraction is priced and the embedding is not, and a console handed
    only the sum would render a confident total over a hole.
    """
    seed_rows(world, ledger)
    stats = client.get("/api/v1/modelcalls/stats").json()

    assert stats["calls"] == 2
    assert stats["succeeded"] == 2
    assert stats["priced_calls"] == 1
    assert stats["cost_basis"] == "partial"
    assert stats["cost_usd"] == priced(1_000, 100)
    assert stats["by_model"][MODEL]["calls"] == 1
    assert stats["by_model"]["gemini-embedding-2"]["cost_usd"] == 0.0
    assert stats["by_purpose"] == {INVOICE_PURPOSE: 1, QUERY_PURPOSE: 1}
    # Rolled up over exactly the rows the list route would return.
    scoped = client.get(
        "/api/v1/modelcalls/stats", params={"flow_id": "accounts-payable"}
    ).json()
    assert scoped["calls"] == 1
    assert scoped["cost_basis"] == "measured"


def test_one_call_is_readable_by_id_and_a_missing_one_is_a_404(world, ledger, client):
    seed_rows(world, ledger)
    call_id = ledger.list()[0].id

    assert client.get(f"/api/v1/modelcalls/{call_id}").json()["id"] == call_id
    assert client.get("/api/v1/modelcalls/mc_nope").status_code == 404


def test_the_ledger_has_no_write_route(world, ledger, client):
    """A route that could write a row could write a false one.

    The whole value of this collection is that every number on it was recorded
    by the code that made the call.
    """
    assert client.post("/api/v1/modelcalls", json={}).status_code == 405


def test_the_basis_vocabulary_is_the_journal_s(world, ledger):
    """One vocabulary for "why is this number what it is", not two.

    The rollup here reuses `flows.cost_basis` rather than restating it: a
    second implementation would drift, and the drift would be a console where
    the run page and the spend page disagree about the same zero.
    """
    stats = summarize(seed_rows(world, ledger))
    assert stats.cost_basis == cost_basis(stats.calls, stats.priced_calls)
