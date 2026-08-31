"""What a ModelCall cost, and who is allowed to say so.

Three failures used to live between a model call and the console tile that
reported it, and they compounded:

1. **The model column held the backend.** Both extraction call sites passed
   `getattr(self.reader, "name", "")` — "vertex" — into the field an auditor
   reads as *which model made this claim*. The real id was already on the
   extraction output, one attribute away.
2. **Nobody read the token counts.** Vertex returns `usage_metadata` on every
   response and all three readers dropped it, so every priced field in every
   journal was zero.
3. **Three model calls had no journal at all** — `/ask`, the A2A door and the
   Employee Hub's HR agent each reached a planner directly.

The tests here are about the properties that fix leaves behind, and the
sharpest of them is a negative: a $0.00 that means *nobody recorded a price*
must never render as a $0.00 that means *this was cheap*. `cost_basis` is the
field that keeps those apart, and half this module exists to keep it honest.

No network. A fake reader shaped exactly like the Vertex one — `name` is the
backend, `model` is the model, and it reports tokens — is what proves the fix
on the real flow path.
"""

import pytest

import app.deps as deps
from app.answering import ANSWER_FLOW_ID, AnswerService, Toolbox
from app.flows import (
    MODEL_PRICING_ASSET_ID,
    ModelRates,
    cost_basis,
    parse_model_pricing,
    read_model_rates,
    usage_series,
)
from app.models import AskStep, AssetUpdate
from app.payables import InvoiceExtraction
from app.reimbursements import ReceiptExtraction, SubmittedDocument
from app.runs import ModelUsage
from tests import world as test_world


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


# ---------------------------------------------------------------------------
# Readers that report a model and its tokens, the way the Vertex ones do
# ---------------------------------------------------------------------------


class MeteredReceiptReader:
    """The exact shape of the bug: a backend called "vertex" running Gemini.

    `name` and `model` differ on purpose. Every assertion below that the
    journal holds `gemini-3.5-flash-lite` is simultaneously an assertion that
    it does not hold `vertex`, which is the value the old call site passed.
    """

    name = "vertex"

    def __init__(self, model: str = "gemini-3.5-flash-lite") -> None:
        self.model = model

    def read(self, image, filename, hint, *, prompt: str = "") -> ReceiptExtraction:
        return ReceiptExtraction(
            vendor="corner-bistro", date="2026-08-30", total_usd=42.00,
            category="meals", confidence=0.94, model=self.model,
            input_tokens=100_000, output_tokens=10_000,
        )


class MeteredInvoiceReader:
    name = "vertex"

    def __init__(self, model: str = "gemini-3.5-flash-lite") -> None:
        self.model = model

    def read(self, image, filename, hint, *, prompt: str = "") -> InvoiceExtraction:
        return InvoiceExtraction(
            vendor_name="Acme Industrial Inc.", vendor_slug="acme-industrial",
            invoice_number="ACME-INV-20001", invoice_date="2026-08-30",
            total_usd=1200.0, confidence=0.93, model=self.model,
            input_tokens=200_000, output_tokens=20_000,
        )


def submit(world, hint: str = "total=42.00 category=meals"):
    return world.expenses.submit(
        "felicia.marks@naviant.com",
        documents=[SubmittedDocument(filename="r.jpg", hint=hint)],
        commentary="Team lunch.",
    )


def model_step(world, record):
    """The one ModelCall row of the run a submission produced.

    An expense request is keyed on its run id; an invoice record names it.
    """
    run = world.engine.get(getattr(record, "run_id", "") or record.id)
    return next(s for s in run.steps if s.kind == "modelcall")


# ---------------------------------------------------------------------------
# 1 · The model column holds a model
# ---------------------------------------------------------------------------


def test_the_step_records_the_model_that_ran_not_the_backend_it_ran_on(world):
    """The lie, named. "vertex" is a backend; it is not a thing with a price.

    Filed in the model column it survives every downstream consumer — the
    recommender looks for a downgrade rung and finds none, the price sheet has
    no row for it, and the console prints a vendor's product name where a
    model id belongs. The id was always one attribute away on the extraction.
    """
    world.expenses.reader = MeteredReceiptReader()
    request = submit(world)

    step = model_step(world, request)
    assert step.model == "gemini-3.5-flash-lite"
    assert step.model != "vertex"
    # And the reader still calls itself vertex, so the assertion above is a
    # statement about where the step looked rather than about what was there.
    assert world.expenses.reader.name == "vertex"


def test_the_ap_step_records_its_model_too(world):
    """The same deletion at the second call site. Both or neither: an estate
    where one flow reports a model and the other reports a backend cannot roll
    spend up across them at all."""
    world.payables.reader = MeteredInvoiceReader()
    record = world.payables.submit(
        submitted_by="ap.clerk@naviant.com", filename="inv.pdf", hint="",
    )
    step = model_step(world, record)
    assert step.model == "gemini-3.5-flash-lite"
    assert step.model != "vertex"


def test_the_tokens_the_reader_reported_reach_the_journal(world):
    """`usage_metadata` used to be read off the response and dropped."""
    world.expenses.reader = MeteredReceiptReader()
    step = model_step(world, submit(world))
    assert step.tokens == 110_000


def test_a_vertex_response_is_read_for_its_usage(monkeypatch):
    """The adapter, without a network: `ModelUsage.from_vertex` is the one
    place that knows what those two fields are called."""

    class Response:
        class usage_metadata:  # noqa: N801 — mirrors the SDK's attribute
            prompt_token_count = 1234
            candidates_token_count = 56

    usage = ModelUsage.from_vertex("gemini-3.5-flash-lite", Response())
    assert (usage.input_tokens, usage.output_tokens) == (1234, 56)
    assert usage.tokens == 1290

    # A response with no usage block reports zero rather than raising, and
    # zero is `unrecorded` — not a claim that the call was free.
    assert ModelUsage.from_vertex("m", object()).tokens == 0


# ---------------------------------------------------------------------------
# 2 · The stub records nothing, and says so
# ---------------------------------------------------------------------------


def test_the_stub_records_zero_and_the_basis_says_unrecorded(world, client):
    """The property that matters more than any number on the page.

    The local stub calls nothing. It records the model it "used" and zero
    tokens, which prices to $0.00 — and that zero has to reach the console
    labelled `unrecorded`, because the other reading of $0.00 is "we ran this
    for free" and a reader shown an unlabelled zero will take the good news.
    """
    submit(world)
    step = model_step(world, submit(world))
    assert step.model == "stub-extractor"
    assert step.tokens == 0
    assert step.cost_usd == 0.0

    usage = client.get(
        "/api/v1/flows/expense-reimbursement/usage", params={"days": 30}
    ).json()
    assert usage["cost_usd"] == 0.0
    assert usage["model_calls"] == 2
    assert usage["priced_model_calls"] == 0
    assert usage["cost_basis"] == "unrecorded"


def test_unrecorded_is_not_no_model_calls(world, client):
    """The two zeroes, kept apart. Nothing has run at all in accounts payable,
    and that is a genuinely zero bill; the expense flow ran twice against a
    reader that priced nothing, and that is a hole."""
    submit(world)
    ap = client.get(
        "/api/v1/flows/accounts-payable/usage", params={"days": 30}
    ).json()
    assert ap["cost_basis"] == "no-model-calls"
    expenses = client.get(
        "/api/v1/flows/expense-reimbursement/usage", params={"days": 30}
    ).json()
    assert expenses["cost_basis"] == "unrecorded"
    assert ap["cost_usd"] == expenses["cost_usd"] == 0.0


def test_the_basis_vocabulary_is_exhaustive():
    """Four inputs, four answers, no fifth state where a caller guesses."""
    assert cost_basis(0, 0) == "no-model-calls"
    assert cost_basis(3, 0) == "unrecorded"
    assert cost_basis(3, 1) == "partial"
    assert cost_basis(3, 3) == "measured"


# ---------------------------------------------------------------------------
# 3 · The rates are governed knowledge
# ---------------------------------------------------------------------------


def test_the_rates_come_from_the_asset(world):
    rates = read_model_rates(world.knowledge)
    assert rates.found_asset is True
    assert rates.source_ref.startswith(f"knowledge:{MODEL_PRICING_ASSET_ID}@rev")
    # The two rungs of the Gemini ladder the recommender proposes across, kept
    # when the table moved out of code.
    assert rates.rates["gemini-3.5-flash-lite"] == (0.10, 0.40)
    assert rates.rates["gemini-3.7-flash"] == (0.30, 2.50)


def test_a_priced_call_costs_what_the_asset_says(world):
    """100k in and 10k out at $0.10/$0.40 per million: $0.01 + $0.004."""
    world.expenses.reader = MeteredReceiptReader()
    step = model_step(world, submit(world))
    assert step.cost_usd == pytest.approx(0.014)
    # And the read-set names the sheet it was priced against, so the number on
    # the journal has something behind it to check the arithmetic against.
    run = world.engine.list(flow_id="expense-reimbursement")[0]
    assert any(r.startswith(f"knowledge:{MODEL_PRICING_ASSET_ID}@rev") for r in run.refs)


def test_editing_the_asset_moves_the_cost(world):
    """Mutation-proof: the rate is read, not remembered.

    A built-in default table still exists for a deployment whose corpus does
    not hold the asset, and a bug that ignored the document would be invisible
    against it — the defaults are a copy of the same rows. Moving the document
    and watching the dollars follow is the only assertion that can tell the
    two apart.
    """
    world.expenses.reader = MeteredReceiptReader()
    before = model_step(world, submit(world)).cost_usd

    asset = world.knowledge.get(MODEL_PRICING_ASSET_ID)
    world.knowledge.update(MODEL_PRICING_ASSET_ID, AssetUpdate(
        content=asset.content.replace(
            "| gemini-3.5-flash-lite | 0.10 | 0.40 |",
            "| gemini-3.5-flash-lite | 1.00 | 4.00 |",
        )
    ))
    after = model_step(world, submit(world, hint="total=43.00 category=meals"))

    assert after.cost_usd == pytest.approx(before * 10)
    assert after.cost_usd == pytest.approx(0.14)
    # The new revision is what the second run pinned against, so two runs
    # priced differently can be told apart by reading their journals.
    newest = world.engine.list(flow_id="expense-reimbursement")[0]
    assert f"knowledge:{MODEL_PRICING_ASSET_ID}@rev{asset.revision + 1}" in newest.refs


def test_a_model_the_sheet_does_not_price_is_unpriced_never_cheap(world):
    """The one decision in `ModelRates` worth arguing about.

    A fallback rate would put a plausible dollar figure on a call nobody knows
    the price of — the same confident-number-over-a-hole failure `cost_basis`
    exists to prevent, and harder to spot because it looks like a measurement.
    """
    world.expenses.reader = MeteredReceiptReader(model="gemini-9-imaginary")
    step = model_step(world, submit(world))
    assert step.model == "gemini-9-imaginary"
    assert step.tokens == 110_000  # the call was measured
    assert step.cost_usd == 0.0    # and it was not priced
    assert cost_basis(1, 0) == "unrecorded"


def test_a_corpus_without_the_asset_falls_back_and_says_so(world):
    """A missing price sheet degrades to the built-in copy rather than to no
    price at all — the same posture as a missing extraction prompt."""
    world.knowledge.repo.delete(MODEL_PRICING_ASSET_ID)
    rates = read_model_rates(world.knowledge)
    assert rates.found_asset is False
    assert rates.source_ref == f"knowledge:{MODEL_PRICING_ASSET_ID}"
    assert rates.rates["gemini-3.5-flash-lite"] == (0.10, 0.40)


def test_the_table_parser_ignores_everything_that_is_not_a_rate_row():
    parsed = parse_model_pricing(
        "| Model | Input $/M tokens | Output $/M tokens |\n"
        "|---|---|---|\n"
        "| gemini-3.5-flash-lite | 0.10 | 0.40 |\n"
        "\nSome prose about | pipes | that is not a row.\n"
    )
    assert parsed.rates == {"gemini-3.5-flash-lite": (0.10, 0.40)}


def test_the_blend_is_only_ever_a_ratio():
    """Both rungs priced, so a downgrade card can size itself; an unpriced
    model blends to zero, which is how the recommender knows to withhold."""
    rates = ModelRates()
    assert rates.blended("gemini-3.7-flash") == pytest.approx(0.30 * 0.6 + 2.50 * 0.4)
    assert rates.blended("nothing-here") == 0.0


# ---------------------------------------------------------------------------
# 4 & 5 · The ungoverned calls became a flow
# ---------------------------------------------------------------------------


class ScriptedBackend:
    """A planner that answers in one turn and reports what it consumed.

    Two turns, in fact, when `turns` says so — which is the case the flow's
    central claim is about: several model turns, one ModelCall step.
    """

    name = "vertex"

    def __init__(self, model: str = "gemini-3.5-flash-lite", turns: int = 1) -> None:
        self._model = model
        self._turns = turns

    def answer(self, question, context, tools):
        for _ in range(self._turns):
            tools.record_model_turn(ModelUsage(
                model=self._model, input_tokens=1000, output_tokens=100,
            ))
        tools.steps.append(AskStep(tool="search_knowledge", summary="one hit"))
        return (
            "The auto-approval limit is stated in [expense-approval-policy].",
            tools.steps,
        )


@pytest.fixture()
def answering(world):
    """The answering stack over this world, with a planner that meters."""
    service = AnswerService(
        world.knowledge, None, ScriptedBackend(),
        graph=world.graph, dispatcher=world.dispatcher, engine=world.engine,
    )
    return service


def answer_runs(world):
    return world.engine.list(flow_id=ANSWER_FLOW_ID)


def test_asking_a_question_produces_a_run_with_one_modelcall_step(world, answering):
    """The last ungoverned ModelCall, brought inside.

    Before this the console's /ask reached a planner directly: a bill accrued,
    documents were read, and no run recorded that any of it happened.
    """
    reply = answering.ask("What is the expense approval limit?", asked_by="ae")

    runs = answer_runs(world)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "succeeded"
    assert run.actor == "ae"
    assert run.model_calls == 1
    steps = [s.kind for s in run.steps]
    assert steps == ["trigger", "modelcall", "event"]
    assert reply.answer.startswith("The auto-approval limit")
    # The answer the caller got is the answer the run recorded — one record of
    # one event, not a reply beside a journal that could disagree with it.
    assert run.outputs["answer"]["answer"] == reply.answer


def test_the_ask_endpoint_produces_the_run_too(world, client, monkeypatch):
    """The console's own door, through the real dependency wiring.

    The service-level test above proves the flow; this proves the endpoint
    reaches it, which is the half that would silently regress if
    `get_answer_service` ever stopped handing over the Dispatcher.
    """
    monkeypatch.setattr(deps, "_answer_backend", ScriptedBackend())
    resp = client.post("/api/v1/ask", json={"question": "What is the VPN standard?"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["backend"] == "vertex"

    run = answer_runs(world)[0]
    assert run.model_calls == 1
    assert sum(1 for s in run.steps if s.kind == "modelcall") == 1
    # The signed-in principal, not a blank column: a journal that cannot say
    # who asked is most of the way back to no journal at all.
    assert run.actor


def test_a_planner_that_takes_four_turns_still_records_one_call(world):
    """One ModelCall step per *question*, never per turn.

    A row per turn would make `model_calls` a measure of how chatty the
    planner is rather than of how often somebody asked something — and the
    fleet's busiest flow would be whichever one had the least decisive model.
    """
    service = AnswerService(
        world.knowledge, None, ScriptedBackend(turns=4),
        graph=world.graph, dispatcher=world.dispatcher, engine=world.engine,
    )
    service.ask("Which team owns the VPN standard?")

    run = answer_runs(world)[0]
    assert run.model_calls == 1
    step = next(s for s in run.steps if s.kind == "modelcall")
    # Summed, not sampled: four turns at 1,100 tokens each.
    assert step.tokens == 4400
    assert step.model == "gemini-3.5-flash-lite"
    assert step.cost_usd == pytest.approx((4000 * 0.10 + 400 * 0.40) / 1_000_000)


def test_with_no_backend_the_run_still_happens_and_records_no_cost(world):
    """`MODEL_BACKEND=none`, honestly.

    The question was still asked, so the run exists and the journal says who
    asked and what came back. What it does not say is that a model ran — the
    step is a transform, and the fleet reports `no-model-calls` rather than one
    unpriced call. Writing a `modelcall` row for a call nobody made would
    inflate the exact count this flow exists to make truthful.
    """
    service = AnswerService(
        world.knowledge, None, None,
        graph=world.graph, dispatcher=world.dispatcher, engine=world.engine,
    )
    reply = service.ask("What is the expense approval limit?")

    run = answer_runs(world)[0]
    assert run.status == "succeeded"
    assert run.model_calls == 0
    assert run.cost_usd == 0.0
    assert [s.kind for s in run.steps] == ["trigger", "transform", "event"]
    assert reply.backend == "retrieval"

    series = usage_series(world.engine.list(flow_id=ANSWER_FLOW_ID), days=30)
    assert sum(d.model_calls for d in series) == 0
    assert cost_basis(
        sum(d.model_calls for d in series),
        sum(d.priced_model_calls for d in series),
    ) == "no-model-calls"


def test_the_a2a_door_produces_the_same_run(world, client, answering, monkeypatch):
    """It comes free, and that is the point of routing it through `ask`.

    An external agent's model spend was the least visible in the system: the
    A2A endpoint answered questions for a caller nobody in the console could
    see. It shares one answer path with /ask, so it shares the journal.
    """
    import app.a2a as a2a

    monkeypatch.setattr(a2a, "get_answer_service", lambda: answering)
    resp = client.post("/a2a", json={
        "jsonrpc": "2.0", "id": "1", "method": "message/send",
        "params": {"message": {
            "role": "user",
            "parts": [{"kind": "text", "text": "What is the VPN standard?"}],
        }},
    })

    assert resp.status_code == 200, resp.text
    run = answer_runs(world)[0]
    assert run.model_calls == 1
    assert sum(1 for s in run.steps if s.kind == "modelcall") == 1


def test_the_hr_agent_emits_into_the_same_flow(world, answering):
    """One flow, two doors. The HR agent's context is an employee's own facts,
    which is the whole of what makes it a different question — so it is a
    different Event name bound to the same response."""
    from app.employee import EmployeeService, InMemoryEmployeeDirectory
    from tests import demo_world

    demo_world.seed_workforce(world.sync, world.graph)
    hub = EmployeeService(
        InMemoryEmployeeDirectory(), world.knowledge, world.tasks, world.graph,
        dispatcher=world.dispatcher,
    )
    reply = hub.chat("john.smith@naviant.com", "How much PTO do I have left?")

    runs = answer_runs(world)
    assert len(runs) == 1
    run = runs[0]
    assert run.actor == "john.smith@naviant.com"
    assert run.model_calls == 1
    assert reply.backend == "vertex"
    # The employee's own facts reached the planner, which is what the separate
    # event name exists to carry.
    assert any("PTO balance" in fact for fact in reply.grounded_on)
    assert run.inputs["context"].startswith("You are the HR Agent")
    # And the fact that started it says which desk was asked.
    names = [e.name for e in world.dispatcher.events.list()]
    assert "HrQuestionAsked" in names
    assert "QuestionAsked" not in names


def test_the_hr_agent_still_degrades_without_a_backend(world):
    """No planner, so the hub composes from the employee's own facts — and the
    run exists anyway, saying a question was asked and nothing was spent."""
    from app.employee import EmployeeService, InMemoryEmployeeDirectory
    from tests import demo_world

    demo_world.seed_workforce(world.sync, world.graph)
    hub = EmployeeService(
        InMemoryEmployeeDirectory(), world.knowledge, world.tasks, world.graph,
        dispatcher=world.dispatcher,
    )
    reply = hub.chat("john.smith@naviant.com", "How much PTO do I have left?")

    assert reply.backend == "retrieval"
    assert "PTO balance" in reply.reply
    run = answer_runs(world)[0]
    assert run.model_calls == 0 and run.cost_usd == 0.0


def test_a_toolbox_sums_turns_and_refuses_to_blend_two_models():
    """The accumulator's one rule. Two models summed into one id would give a
    token count attributable to neither, so the id degrades to `mixed` rather
    than silently naming the first."""
    tools = Toolbox(None, None)
    tools.record_model_turn(ModelUsage(model="a", input_tokens=10, output_tokens=1))
    tools.record_model_turn(ModelUsage(model="a", input_tokens=20, output_tokens=2))
    assert (tools.usage.model, tools.usage.tokens) == ("a", 33)

    tools.record_model_turn(ModelUsage(model="b", input_tokens=5, output_tokens=0))
    assert tools.usage.model == "mixed"
    assert tools.usage.tokens == 38


# ---------------------------------------------------------------------------
# 6 · The fleet rollup sums the new steps
# ---------------------------------------------------------------------------


def test_the_fleet_spend_sums_answering_and_extraction_together(
    world, client, answering
):
    """One rollup over every governed model call, whatever made it.

    This is what the whole stage is for: before it, an extraction's cost was
    zero because nobody read the tokens and a question's cost did not exist
    because nobody recorded the call. Both now land in the same sum, and the
    basis says `measured` because every call in the window carried a price.
    """
    world.expenses.reader = MeteredReceiptReader()
    submit(world)
    answering.ask("What is the expense approval limit?")

    expenses = client.get(
        "/api/v1/flows/expense-reimbursement/usage", params={"days": 30}
    ).json()
    answers = client.get(
        f"/api/v1/flows/{ANSWER_FLOW_ID}/usage", params={"days": 30}
    ).json()
    assert expenses["cost_basis"] == answers["cost_basis"] == "measured"

    stats = client.get("/api/v1/flows/stats", params={"days": 30}).json()
    assert stats["model_calls"] == 2
    assert stats["priced_model_calls"] == 2
    assert stats["cost_basis"] == "measured"
    assert stats["model_spend_usd"] == pytest.approx(
        expenses["cost_usd"] + answers["cost_usd"]
    )
    assert stats["model_spend_usd"] > 0
    assert stats["tokens"] == 110_000 + 1_100
